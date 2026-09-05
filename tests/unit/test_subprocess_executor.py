import asyncio
import subprocess
from datetime import datetime, timezone
from unittest.mock import patch

from google_meridian_mcp_server.domain.optimization import (
    OptimizationConfig,
    OptimizationRun,
    OptimizationRunState,
    RunStatus,
)
from google_meridian_mcp_server.execution.base_executor import BaseExecutor
from google_meridian_mcp_server.execution.subprocess_executor import (
    AsyncSubprocessExecutor,
)
from google_meridian_mcp_server.persistence.optimization_run_registry import (
    LocalOptimizationRunRegistry,
)


def _run(run_id):
    cfg = OptimizationConfig.model_validate({"scenario": {"type": "fixed_budget"}})
    return OptimizationRun(
        run_id=run_id,
        label="l",
        model_id="m",
        config=cfg,
        config_fingerprint="fp",
        compute_tier_requested="auto",
        compute_tier_resolved="local",
        size_score=1,
        created_at="2026-06-29T00:00:00+00:00",
        meridian_version="1.7.0",
        server_version="0.1.0",
    )


class _Handle:
    def __init__(self):
        self.alive = True


class _FakeExecutor(BaseExecutor):
    def __init__(self, registry, **kw):
        super().__init__(registry, **kw)
        self.launched: list[str] = []

    def _launch(self, run):
        self.launched.append(run.run_id)
        return _Handle()

    def _is_alive(self, handle):
        return handle.alive

    def _terminate(self, handle) -> None:
        pass  # no-op for test doubles


def test_gate_limits_concurrent_launches(tmp_path):
    reg = LocalOptimizationRunRegistry(str(tmp_path))
    ex = _FakeExecutor(reg, max_parallel=1, heartbeat_stale_seconds=60)
    reg.create(_run("a"))
    ex.submit(_run("a"))
    reg.create(_run("b"))
    ex.submit(_run("b"))
    assert ex.launched == ["a"]  # b is gated
    assert reg.get_state("b").status == RunStatus.QUEUED
    # finish a -> next pump launches b
    ex._handles["a"].alive = False
    ex.pump()
    assert ex.launched == ["a", "b"]


def test_alive_handle_with_stale_heartbeat_not_failed(tmp_path):
    """FIX 1: alive subprocess must never be stale-failed.

    Even with heartbeat_stale_seconds=0 and a heartbeat_at set to epoch (very
    stale), a handle that _is_alive() returns True for must remain RUNNING and
    stay in _handles so the concurrency gate is not prematurely freed.
    """
    from google_meridian_mcp_server.domain.optimization import OptimizationRunState

    reg = LocalOptimizationRunRegistry(str(tmp_path))
    ex = _FakeExecutor(reg, max_parallel=1, heartbeat_stale_seconds=0)

    reg.create(_run("a"))
    ex.submit(_run("a"))

    # Simulate an ancient heartbeat so stale detection would fire if _reconcile_stale
    # were incorrectly called for alive handles.
    reg.write_state(
        OptimizationRunState(
            run_id="a",
            status=RunStatus.RUNNING,
            heartbeat_at="1970-01-01T00:00:00+00:00",  # very stale
        )
    )

    # Keep the handle alive and pump
    assert ex._handles["a"].alive is True
    ex.pump()

    # Run must remain RUNNING — not FAILED
    assert reg.get_state("a").status == RunStatus.RUNNING
    # Handle must still be tracked (slot not leaked)
    assert "a" in ex._handles

    # A second submitted run must stay QUEUED (gate still honored)
    reg.create(_run("b"))
    ex.submit(_run("b"))
    assert reg.get_state("b").status == RunStatus.QUEUED
    assert "b" not in ex._handles  # not launched yet — slot still held by "a"


class _TrackingHandle:
    def __init__(self):
        self.alive = True
        self.terminated = False


class _TrackingExecutor(BaseExecutor):
    def __init__(self, registry, **kw):
        super().__init__(registry, **kw)
        self.launched: list[str] = []

    def _launch(self, run):
        self.launched.append(run.run_id)
        return _TrackingHandle()

    def _is_alive(self, handle):
        return handle.alive

    def _terminate(self, handle) -> None:
        handle.terminated = True


def test_cancel_terminates_tracked_handle(tmp_path):
    """FIX 4: cancel() terminates a tracked handle and writes CANCELED."""
    reg = LocalOptimizationRunRegistry(str(tmp_path))
    ex = _TrackingExecutor(reg, max_parallel=2, heartbeat_stale_seconds=60)

    reg.create(_run("a"))
    ex.submit(_run("a"))
    handle = ex._handles["a"]

    ex.cancel("a")

    assert handle.terminated
    assert "a" not in ex._handles
    assert reg.get_state("a").status == RunStatus.CANCELED


def test_cancel_removes_queued_run(tmp_path):
    """FIX 4: cancel() removes a queued (not yet launched) run from the queue."""
    reg = LocalOptimizationRunRegistry(str(tmp_path))
    ex = _TrackingExecutor(reg, max_parallel=1, heartbeat_stale_seconds=60)

    # Submit two runs; max_parallel=1 means "b" stays queued.
    reg.create(_run("a"))
    ex.submit(_run("a"))
    reg.create(_run("b"))
    ex.submit(_run("b"))

    assert "a" in ex._handles
    assert "b" not in ex._handles  # still queued
    assert reg.get_state("b").status == RunStatus.QUEUED

    ex.cancel("b")

    assert "b" not in ex._handles
    assert reg.get_state("b").status == RunStatus.CANCELED


def test_cancel_does_not_overwrite_completed_state(tmp_path):
    """FIX 4: cancel() does NOT overwrite a terminal (COMPLETED/FAILED) state."""
    reg = LocalOptimizationRunRegistry(str(tmp_path))
    ex = _TrackingExecutor(reg, max_parallel=2, heartbeat_stale_seconds=60)

    reg.create(_run("a"))
    ex.submit(_run("a"))

    # Worker finished before cancel arrived.
    reg.write_state(OptimizationRunState(run_id="a", status=RunStatus.COMPLETED))
    ex.cancel("a")

    assert reg.get_state("a").status == RunStatus.COMPLETED


def test_reconcile_stale_precondition_guards_against_race():
    """FIX 4: _reconcile_stale does NOT pop the handle when a competing write
    bumps the generation before the guarded FAILED write (precondition rejected).
    A second call with a fresh generation DOES fail the stale run."""
    from google_meridian_mcp_server.persistence.optimization_run_registry import (
        GcsOptimizationRunRegistry,
    )
    from tests.fakes.fake_gcs import FakeGcsClient

    client = FakeGcsClient()
    registry = GcsOptimizationRunRegistry("bkt", "opts/", client_factory=lambda: client)

    run_id = "m-1"
    cfg = OptimizationConfig.model_validate({"scenario": {"type": "fixed_budget"}})
    run_obj = OptimizationRun(
        run_id=run_id,
        label="l",
        model_id="m",
        config=cfg,
        config_fingerprint="fp",
        compute_tier_requested="auto",
        compute_tier_resolved="cloud_cpu",
        size_score=1,
        created_at="2026-06-29T00:00:00+00:00",
        meridian_version="1.7.0",
        server_version="0.1.0",
    )
    registry.create(run_obj)

    stale_ts = "1970-01-01T00:00:00+00:00"
    registry.write_state(
        OptimizationRunState(
            run_id=run_id, status=RunStatus.RUNNING, heartbeat_at=stale_ts
        )
    )

    # Capture generation BEFORE the competing write.
    gen_stale = registry.get_state_generation(run_id)

    # Competing write bumps the generation (simulates a live heartbeat from worker).
    registry.write_state(
        OptimizationRunState(
            run_id=run_id, status=RunStatus.RUNNING, heartbeat_at=stale_ts
        )
    )

    ex = _FakeExecutor(registry, max_parallel=2, heartbeat_stale_seconds=0)
    ex._handles[run_id] = _Handle()

    # First call: patch get_state_generation to return the PRE-COMPETITION (stale)
    # generation so the guarded write is rejected by the precondition check.
    with patch.object(registry, "get_state_generation", return_value=gen_stale):
        ex._reconcile_stale(run_id)

    # Precondition write was rejected: run stays RUNNING, handle NOT popped.
    assert registry.get_state(run_id).status == RunStatus.RUNNING
    assert run_id in ex._handles

    # Second call with the real (fresh) generation -> write succeeds.
    ex._reconcile_stale(run_id)

    assert registry.get_state(run_id).status == RunStatus.FAILED
    assert run_id not in ex._handles


def test_reap_after_deleted_run_does_not_raise(tmp_path):
    """A completed run's handle may be reaped after the run is deleted (delete
    races the poll() lag). _fail_if_unfinished must treat a missing run as a
    no-op, not propagate RunNotFoundError into a later pump()."""
    reg = LocalOptimizationRunRegistry(str(tmp_path))
    ex = _FakeExecutor(reg, max_parallel=1, heartbeat_stale_seconds=60)
    reg.create(_run("a"))
    ex.submit(_run("a"))  # launched; handle in _handles
    ex._handles["a"].alive = False  # subprocess exited
    reg.delete("a")  # run deleted before its handle is reaped
    ex.pump()  # _reap -> _fail_if_unfinished("a") must not raise
    assert "a" not in ex._handles


def test_subprocess_executor_builds_worker_command(tmp_path, monkeypatch):
    reg = LocalOptimizationRunRegistry(str(tmp_path))
    captured = {}

    class _Popen:
        def __init__(self, cmd, env=None, **kwargs):
            captured["cmd"] = cmd
            captured["env"] = env
            captured.update(kwargs)

        def poll(self):
            return None

    monkeypatch.setattr(subprocess, "Popen", _Popen)
    ex = AsyncSubprocessExecutor(
        reg,
        max_parallel=2,
        heartbeat_stale_seconds=60,
        log_root=tmp_path / "logs",
    )
    reg.create(_run("a"))
    ex.submit(_run("a"))
    assert "google_meridian_mcp_server.execution.worker" in captured["cmd"]
    assert captured["env"]["OPTIMIZATION_RUN_ID"] == "a"
    # MERIDIAN_BACKEND comes from the module constant in base_subprocess.py,
    # not a per-executor parameter.
    assert captured["env"]["MERIDIAN_BACKEND"] == "jax"


def test_launch_redirects_and_new_session(monkeypatch, tmp_path):
    reg = LocalOptimizationRunRegistry(str(tmp_path))
    captured = {}

    def fake_popen(argv, **kwargs):
        captured.update(kwargs)

        class _P:
            pid = 4321

        return _P()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    ex = AsyncSubprocessExecutor(
        reg,
        max_parallel=1,
        heartbeat_stale_seconds=60,
        log_root=tmp_path,
    )
    ex._launch(_run("r1"))
    assert captured["start_new_session"] is True
    assert captured["stdout"] is not None


def test_reconcile_orphans_fails_running_and_queued_unconditionally(tmp_path):
    """F1: a server restart must not strand a local-tier RUNNING/QUEUED run.

    `_handles`/`_queue` are in-memory, so a fresh server starts with neither;
    the PID-1 parent-death guard (worker.py) guarantees a local worker cannot
    survive its parent server, so AsyncSubprocessExecutor.reconcile_orphans
    must fail BOTH RUNNING and QUEUED unconditionally -- no heartbeat-staleness
    grace period, even with a perfectly fresh heartbeat."""
    reg = LocalOptimizationRunRegistry(str(tmp_path))
    reg.create(_run("running-run"))
    reg.write_state(
        OptimizationRunState(
            run_id="running-run",
            status=RunStatus.RUNNING,
            heartbeat_at=datetime.now(timezone.utc).isoformat(),  # FRESH heartbeat
        )
    )
    reg.create(_run("queued-run"))
    reg.write_state(OptimizationRunState(run_id="queued-run", status=RunStatus.QUEUED))

    ex = AsyncSubprocessExecutor(reg, max_parallel=2, heartbeat_stale_seconds=60)
    ex.reconcile_orphans()

    for run_id in ("running-run", "queued-run"):
        state = reg.get_state(run_id)
        assert state.status == RunStatus.FAILED
        assert state.error["code"] == "worker_lost"


def test_reconcile_orphans_does_not_touch_terminal_runs(tmp_path):
    """F1 regression: reconcile_orphans must not clobber a run that already
    reached a terminal state before the restart."""
    reg = LocalOptimizationRunRegistry(str(tmp_path))
    reg.create(_run("done-run"))
    reg.write_state(OptimizationRunState(run_id="done-run", status=RunStatus.COMPLETED))

    ex = AsyncSubprocessExecutor(reg, max_parallel=2, heartbeat_stale_seconds=60)
    ex.reconcile_orphans()

    assert reg.get_state("done-run").status == RunStatus.COMPLETED


def test_pump_skips_run_deleted_from_registry_while_queued(tmp_path):
    """F2(a): a run_id popped off the internal queue whose registry record is
    gone (e.g. deleted out-of-band, bypassing the executor.cancel() dequeue
    OptimizationService.delete now performs) must be skipped by pump(), not
    raise RunNotFoundError into whatever unrelated call triggered the pump."""
    reg = LocalOptimizationRunRegistry(str(tmp_path))
    ex = _FakeExecutor(reg, max_parallel=1, heartbeat_stale_seconds=60)
    reg.create(_run("a"))
    ex.submit(_run("a"))  # launched (max_parallel=1)
    reg.create(_run("b"))
    ex.submit(_run("b"))  # queued behind "a"

    reg.delete("b")  # registry record gone; "b" is still sitting in ex._queue

    ex._handles["a"].alive = False  # free the concurrency slot
    ex.pump()  # must not raise RunNotFoundError

    assert "b" not in ex._handles
    assert "b" not in ex._queue


def test_pump_fails_run_when_launch_raises(tmp_path):
    """F2(b): a _launch failure (EMFILE, unwritable log_root, fork failure,
    ...) must fail the run FAILED/worker_lost -- not leave it wedged QUEUED
    forever -- and the raw exception must not escape pump()/submit()."""
    reg = LocalOptimizationRunRegistry(str(tmp_path))

    class _BoomExecutor(_FakeExecutor):
        def _launch(self, run):
            raise OSError("EMFILE: too many open files")

    ex = _BoomExecutor(reg, max_parallel=1, heartbeat_stale_seconds=60)
    reg.create(_run("a"))
    ex.submit(_run("a"))  # submit() calls pump() internally; must not raise

    assert "a" not in ex._handles
    state = reg.get_state("a")
    assert state.status == RunStatus.FAILED
    assert state.error["code"] == "worker_lost"
    assert "EMFILE" in state.error["message"]


def test_terminate_reaps_child_after_kill(monkeypatch, tmp_path):
    """F9: _terminate reaps the child after SIGKILL so it doesn't linger as
    a zombie."""
    reg = LocalOptimizationRunRegistry(str(tmp_path))
    ex = AsyncSubprocessExecutor(reg, max_parallel=1, heartbeat_stale_seconds=60)
    monkeypatch.setattr(ex, "kill_group", lambda pid: None)

    waited = {}

    class _Handle:
        pid = 4321

        def wait(self, timeout=None):
            waited["timeout"] = timeout
            return 0

    ex._terminate(_Handle())
    assert "timeout" in waited  # wait() was called to reap the zombie


def test_terminate_suppresses_wait_exceptions(monkeypatch, tmp_path):
    """F9 regression: a wait() failure (e.g. TimeoutExpired) must not escape
    _terminate -- it's best-effort zombie reaping, not a hard requirement."""
    reg = LocalOptimizationRunRegistry(str(tmp_path))
    ex = AsyncSubprocessExecutor(reg, max_parallel=1, heartbeat_stale_seconds=60)
    monkeypatch.setattr(ex, "kill_group", lambda pid: None)

    class _Handle:
        pid = 4321

        def wait(self, timeout=None):
            raise subprocess.TimeoutExpired(cmd="worker", timeout=timeout)

    ex._terminate(_Handle())  # must not raise


async def test_concurrent_submits_do_not_corrupt_executor_state(tmp_path):
    """F6(b): once the MCP tool handlers offload their sync service calls onto
    worker threads (asyncio.to_thread), submit()/pump()/cancel() can run
    concurrently from multiple threads against the same executor. The RLock
    added to BaseExecutor must serialize access to `_handles`/`_queue` so N
    concurrent submits neither raise nor lose/duplicate an entry."""
    reg = LocalOptimizationRunRegistry(str(tmp_path))
    ex = _FakeExecutor(reg, max_parallel=3, heartbeat_stale_seconds=60)

    n = 20
    runs = [_run(f"r{i}") for i in range(n)]
    for run in runs:
        reg.create(run)

    await asyncio.gather(*(asyncio.to_thread(ex.submit, run) for run in runs))

    # Nothing lost or duplicated across the two structures the lock protects.
    tracked = set(ex._handles) | set(ex._queue)
    assert len(ex._handles) + len(ex._queue) == n
    assert tracked == {run.run_id for run in runs}
    assert len(ex._handles) == 3  # concurrency gate still honored
    assert len(ex._queue) == n - 3


async def test_concurrent_pump_calls_do_not_raise_or_duplicate_launches(tmp_path):
    """F6(b) regression: concurrent pump() calls (e.g. several get_optimization_status
    polls landing at once, each offloaded to its own thread) must not double-launch
    a queued run or raise."""
    reg = LocalOptimizationRunRegistry(str(tmp_path))
    ex = _FakeExecutor(reg, max_parallel=2, heartbeat_stale_seconds=60)

    runs = [_run(f"p{i}") for i in range(6)]
    for run in runs:
        reg.create(run)
        ex.submit(run)  # sequential seed: 2 launched, 4 queued

    assert len(ex._handles) == 2
    assert len(ex._queue) == 4

    # Free one concurrency slot, then hammer pump() from many threads at once.
    first_handle = next(iter(ex._handles.values()))
    first_handle.alive = False

    await asyncio.gather(*(asyncio.to_thread(ex.pump) for _ in range(10)))

    # Exactly one run_id was ever launched per call to _launch -- no duplicates.
    assert len(ex.launched) == len(set(ex.launched))
    assert len(ex._handles) == 2  # gate still honored after the free slot backfilled

import subprocess
from unittest.mock import patch

from google_meridian_mcp_server.domain.optimization import (
    OptimizationConfig,
    OptimizationRun,
    OptimizationRunState,
    RunStatus,
)
from google_meridian_mcp_server.execution.base_executor import BaseExecutor
from google_meridian_mcp_server.execution.subprocess_executor import SubprocessExecutor
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
        backend="tensorflow",
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
        backend="jax",
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
        def __init__(self, cmd, env=None):
            captured["cmd"] = cmd
            captured["env"] = env

        def poll(self):
            return None

    monkeypatch.setattr(subprocess, "Popen", _Popen)
    ex = SubprocessExecutor(
        reg, max_parallel=2, heartbeat_stale_seconds=60, backend="jax"
    )
    reg.create(_run("a"))
    ex.submit(_run("a"))
    assert "google_meridian_mcp_server.execution.worker" in captured["cmd"]
    assert captured["env"]["OPTIMIZATION_RUN_ID"] == "a"
    assert captured["env"]["MERIDIAN_BACKEND"] == "jax"

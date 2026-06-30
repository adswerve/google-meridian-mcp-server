import subprocess

from google_meridian_mcp_server.domain.optimization import (
    OptimizationConfig,
    OptimizationRun,
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

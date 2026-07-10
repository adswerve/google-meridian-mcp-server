from types import SimpleNamespace

from google_meridian_mcp_server.domain.optimization import (
    OptimizationConfig,
    OptimizationRun,
    OptimizationRunState,
    RunStatus,
)
from google_meridian_mcp_server.execution.cloud_run_executor import CloudRunJobExecutor


def _run(tier="cloud_cpu"):
    return OptimizationRun(
        run_id="m-1",
        label="l",
        model_id="m",
        config=OptimizationConfig.model_validate(
            {"scenario": {"type": "fixed_budget"}}
        ),
        config_fingerprint="fp",
        compute_tier_requested="auto",
        compute_tier_resolved=tier,
        backend="jax",
        size_score=1,
        created_at="2026-06-30T00:00:00+00:00",
        meridian_version="1.7.0",
        server_version="0.1.0",
    )


class _FakeJobs:
    def __init__(self):
        self.calls = []

    def run_job(self, request):
        self.calls.append(request)
        return SimpleNamespace(metadata=SimpleNamespace(name="exec-123"))


class _FakeExecutions:
    def __init__(self, alive=True):
        self.alive = alive

    def get_execution(self, name):
        # completion_time empty -> alive
        return SimpleNamespace(
            completion_time=None if self.alive else "2026-06-30T00:01:00Z"
        )


class _Registry:
    def __init__(self):
        self.states = {}

    def write_state(self, state, *, expected_generation=None):
        self.states[state.run_id] = state

    def get_record(self, run_id):
        return _run()

    def get_state(self, run_id):
        return self.states.get(
            run_id, OptimizationRunState(run_id=run_id, status=RunStatus.QUEUED)
        )

    def get_state_generation(self, run_id):
        return 1


def _cfg():
    from google_meridian_mcp_server.domain.models import RuntimeConfig

    return RuntimeConfig(
        persistence_backend="gcs",
        gcs_bucket="b",
        gcs_models_prefix="m/",
        registry_backend="gcs",
        optimization_allowed_tiers=("cloud_cpu", "cloud_gpu"),
        cloud_run_project="example-dev-project",
        cloud_run_region="us-central1",
        cloud_run_job_cpu="opt-cpu",
        cloud_run_job_gpu="opt-gpu",
    )


def test_launch_calls_run_job_with_env_overrides():
    jobs = _FakeJobs()
    ex = CloudRunJobExecutor(
        _Registry(),
        cfg=_cfg(),
        max_parallel=2,
        heartbeat_stale_seconds=60,
        jobs_client=jobs,
        executions_client=_FakeExecutions(),
    )
    ex.submit(_run("cloud_cpu"))
    assert len(jobs.calls) == 1
    req = jobs.calls[0]
    assert "opt-cpu" in req.name  # cpu job selected by tier
    env_names = {e.name for e in req.overrides.container_overrides[0].env}
    assert {"OPTIMIZATION_RUN_ID", "MERIDIAN_BACKEND"} <= env_names


def test_is_alive_reflects_execution_completion():
    ex = CloudRunJobExecutor(
        _Registry(),
        cfg=_cfg(),
        max_parallel=2,
        heartbeat_stale_seconds=60,
        jobs_client=_FakeJobs(),
        executions_client=_FakeExecutions(alive=False),
    )
    assert ex._is_alive("exec-123") is False


def test_reconcile_orphans_leaves_fresh_heartbeat_running_untouched(tmp_path):
    """F1: cloud tier keeps heartbeat-staleness reconciliation via the
    default BaseExecutor.reconcile_orphans (CloudRunJobExecutor does not
    override it, unlike AsyncSubprocessExecutor) -- a RUNNING run with a
    FRESH heartbeat must be left alone, since a cloud worker CAN outlive the
    server process, unlike a local subprocess worker."""
    from datetime import datetime, timezone

    from google_meridian_mcp_server.domain.optimization import OptimizationRunState
    from google_meridian_mcp_server.persistence.optimization_run_registry import (
        LocalOptimizationRunRegistry,
    )

    reg = LocalOptimizationRunRegistry(str(tmp_path))
    reg.create(_run("cloud_cpu"))  # run_id is always "m-1" (see _run() above)
    reg.write_state(
        OptimizationRunState(
            run_id="m-1",
            status=RunStatus.RUNNING,
            heartbeat_at=datetime.now(timezone.utc).isoformat(),
        )
    )

    ex = CloudRunJobExecutor(
        reg,
        cfg=_cfg(),
        max_parallel=2,
        heartbeat_stale_seconds=60,
        jobs_client=_FakeJobs(),
        executions_client=_FakeExecutions(),
    )
    ex.reconcile_orphans()

    assert reg.get_state("m-1").status == RunStatus.RUNNING

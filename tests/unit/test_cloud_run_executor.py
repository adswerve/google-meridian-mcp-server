from datetime import datetime, timezone
from types import SimpleNamespace

from google_meridian_mcp_server.domain.optimization import (
    OptimizationConfig,
    OptimizationRun,
    OptimizationRunDispatch,
    OptimizationRunState,
    RunStatus,
)
from google_meridian_mcp_server.execution.cloud_run_executor import CloudRunJobExecutor
from google_meridian_mcp_server.persistence.optimization_run_registry import (
    ResultNotReadyError,
)


def _now():
    return datetime.now(timezone.utc).isoformat()


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
        self.dispatches = {}

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

    def claim_dispatch(self, dispatch):
        if dispatch.run_id in self.dispatches:
            return False
        self.dispatches[dispatch.run_id] = dispatch
        return True

    def write_dispatch(self, dispatch):
        self.dispatches[dispatch.run_id] = dispatch

    def get_dispatch(self, run_id):
        return self.dispatches.get(run_id)

    def get_result(self, run_id):
        raise ResultNotReadyError(run_id, self.get_state(run_id).status.value)


def _cfg():
    from google_meridian_mcp_server.domain.models import RuntimeConfig

    return RuntimeConfig(
        persistence_backend="gcs",
        gcs_bucket="b",
        gcs_models_prefix="m/",
        optimization_tier="cloud_auto",
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
    assert {
        "OPTIMIZATION_RUN_ID",
        "MERIDIAN_BACKEND",
        "MERIDIAN_ENABLE_JAX_X64",
    } <= env_names
    env_by_name = {e.name: e.value for e in req.overrides.container_overrides[0].env}
    assert env_by_name["MERIDIAN_BACKEND"] == "jax"
    assert env_by_name["MERIDIAN_ENABLE_JAX_X64"] == "true"


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


def test_missing_cloud_run_job_reports_configuration_error_not_worker_lost():
    """A 404 from run_job is a permanent misconfiguration, not a lost worker.

    Asserting only that the run failed would pass under the old blanket
    handler, which is the whole reason this test exists.
    """
    from google.api_core.exceptions import NotFound

    class _NotFoundJobs:
        def run_job(self, request):
            raise NotFound(
                "Resource 'projects/p/locations/r/jobs/opt-gpu' was not found"
            )

    reg = _Registry()
    ex = CloudRunJobExecutor(
        reg,
        cfg=_cfg(),
        max_parallel=2,
        jobs_client=_NotFoundJobs(),
        executions_client=_FakeExecutions(),
    )
    run = _run("cloud_gpu")
    ex.submit(run)

    state = reg.get_state(run.run_id)
    assert state.status is RunStatus.FAILED
    assert state.error["code"] == "cloud_job_not_found"
    assert "was not found" in state.error["message"]


def test_permission_denied_on_launch_is_classified_separately():
    from google.api_core.exceptions import PermissionDenied

    class _DeniedJobs:
        def run_job(self, request):
            raise PermissionDenied("caller lacks run.jobs.run")

    reg = _Registry()
    ex = CloudRunJobExecutor(
        reg,
        cfg=_cfg(),
        max_parallel=2,
        jobs_client=_DeniedJobs(),
        executions_client=_FakeExecutions(),
    )
    run = _run("cloud_cpu")
    ex.submit(run)
    assert reg.get_state(run.run_id).error["code"] == "cloud_job_permission_denied"


def test_unrecognised_launch_failure_still_reports_worker_lost():
    """The classification must not swallow the generic case."""

    class _BrokenJobs:
        def run_job(self, request):
            raise OSError("EMFILE")

    reg = _Registry()
    ex = CloudRunJobExecutor(
        reg,
        cfg=_cfg(),
        max_parallel=2,
        jobs_client=_BrokenJobs(),
        executions_client=_FakeExecutions(),
    )
    run = _run("cloud_cpu")
    ex.submit(run)
    assert reg.get_state(run.run_id).error["code"] == "worker_lost"


def test_two_executors_sharing_a_registry_launch_the_run_once(tmp_path):
    """The claim is the only thing preventing double GPU spend.

    Asserting a sum of 1 would also pass if the claim were inverted and the
    loser won, so this pins which executor launched.
    """
    from google_meridian_mcp_server.persistence.optimization_run_registry import (
        LocalOptimizationRunRegistry,
    )

    reg = LocalOptimizationRunRegistry(str(tmp_path))
    run = _run("cloud_cpu")
    reg.create(run)

    jobs_a, jobs_b = _FakeJobs(), _FakeJobs()
    ex_a = CloudRunJobExecutor(
        reg,
        cfg=_cfg(),
        max_parallel=2,
        jobs_client=jobs_a,
        executions_client=_FakeExecutions(),
    )
    ex_b = CloudRunJobExecutor(
        reg,
        cfg=_cfg(),
        max_parallel=2,
        jobs_client=jobs_b,
        executions_client=_FakeExecutions(),
    )

    ex_a.submit(run)
    # Instance B rebuilt the same queue. Appending directly is the only way to
    # reach pump()'s claim-loss path: after ex_a.submit the dispatch document
    # already carries an execution_name, so reconcile_orphans would take the
    # adopt branch instead.
    ex_b._queue.append(run.run_id)
    ex_b.pump()

    assert len(jobs_a.calls) == 1
    assert jobs_b.calls == []
    assert reg.get_dispatch(run.run_id).execution_name == "exec-123"
    assert reg.get_state(run.run_id).status is RunStatus.QUEUED  # loser wrote no FAILED
    assert reg.get_state(run.run_id).error is None


def test_dispatch_records_the_execution_name(tmp_path):
    from google_meridian_mcp_server.persistence.optimization_run_registry import (
        LocalOptimizationRunRegistry,
    )

    reg = LocalOptimizationRunRegistry(str(tmp_path))
    run = _run("cloud_cpu")
    reg.create(run)
    ex = CloudRunJobExecutor(
        reg,
        cfg=_cfg(),
        max_parallel=2,
        jobs_client=_FakeJobs(),
        executions_client=_FakeExecutions(),
    )

    ex.submit(run)

    assert reg.get_dispatch(run.run_id).execution_name == "exec-123"


def test_losing_the_claim_does_not_fail_or_launch(tmp_path):
    """A lost race must neither write FAILED over the winner's run nor launch."""
    from google_meridian_mcp_server.persistence.optimization_run_registry import (
        LocalOptimizationRunRegistry,
    )

    reg = LocalOptimizationRunRegistry(str(tmp_path))
    run = _run("cloud_cpu")
    reg.create(run)
    reg.claim_dispatch(OptimizationRunDispatch(run_id=run.run_id, claimed_at=_now()))

    jobs = _FakeJobs()
    ex = CloudRunJobExecutor(
        reg,
        cfg=_cfg(),
        max_parallel=2,
        jobs_client=jobs,
        executions_client=_FakeExecutions(),
    )

    ex.submit(run)

    assert jobs.calls == []  # bound and asserted: an unbound fake hid this
    assert reg.get_state(run.run_id).status is RunStatus.QUEUED
    assert reg.get_state(run.run_id).error is None


def test_a_failed_dispatch_record_does_not_fail_a_live_run(tmp_path):
    """run_job() already succeeded, so the execution is live and billing.
    Losing the name costs cancel-by-name, never the run."""
    from google_meridian_mcp_server.persistence.optimization_run_registry import (
        LocalOptimizationRunRegistry,
    )

    reg = LocalOptimizationRunRegistry(str(tmp_path))
    run = _run("cloud_cpu")
    reg.create(run)

    def _boom(dispatch):
        raise OSError("transient")

    reg.write_dispatch = _boom
    jobs = _FakeJobs()
    ex = CloudRunJobExecutor(
        reg,
        cfg=_cfg(),
        max_parallel=2,
        jobs_client=jobs,
        executions_client=_FakeExecutions(),
    )

    ex.submit(run)

    assert len(jobs.calls) == 1  # it did launch
    assert reg.get_state(run.run_id).status is RunStatus.QUEUED  # and was NOT failed
    assert ex._handles[run.run_id] == "exec-123"  # handle kept regardless


def test_is_alive_treats_a_collected_execution_as_finished():
    """GCP garbage-collects old Executions; get_execution then 404s.

    Adoption is the first path that can call _is_alive on an execution from a
    previous process, so this exception previously escaped _reap.
    """
    from google.api_core.exceptions import NotFound

    class _GoneExecutions:
        def get_execution(self, name):
            raise NotFound(name)

    ex = CloudRunJobExecutor(
        _Registry(),
        cfg=_cfg(),
        max_parallel=2,
        jobs_client=_FakeJobs(),
        executions_client=_GoneExecutions(),
    )
    assert ex._is_alive("projects/p/locations/r/jobs/j/executions/gone") is False


def test_reap_does_not_fail_a_run_that_already_wrote_a_result(tmp_path):
    """worker.py writes result.json (:231) BEFORE its terminal write_state
    (:243). A container killed in between leaves a valid result under a RUNNING
    state, and failing it destroys a completed run.

    Reverting the get_result guard makes this FAILED/worker_lost with a
    complete result.json sitting in the bucket -- verified by probe.
    """
    from google_meridian_mcp_server.persistence.optimization_run_registry import (
        LocalOptimizationRunRegistry,
    )

    reg = LocalOptimizationRunRegistry(str(tmp_path))
    run = _run("cloud_cpu")
    reg.create(run)
    reg.write_state(
        OptimizationRunState(
            run_id=run.run_id, status=RunStatus.RUNNING, heartbeat_at=_now()
        )
    )
    reg.write_result(run.run_id, {"summary": {"ok": True}})

    ex = CloudRunJobExecutor(
        reg,
        cfg=_cfg(),
        max_parallel=2,
        jobs_client=_FakeJobs(),
        executions_client=_FakeExecutions(alive=False),
    )
    ex._handles[run.run_id] = "exec-done"
    ex.pump()  # drives _reap -> _is_alive(False) -> _fail_if_unfinished

    assert reg.get_state(run.run_id).status is not RunStatus.FAILED
    assert reg.get_result(run.run_id) == {"summary": {"ok": True}}

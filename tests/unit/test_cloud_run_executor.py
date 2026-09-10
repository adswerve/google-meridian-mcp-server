from datetime import datetime, timedelta, timezone
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
        self.asked = []

    def get_execution(self, name):
        self.asked.append(name)
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
    """F1: cloud tier keeps heartbeat-staleness reconciliation, now via its
    own reconcile_orphans override (which calls super() for this check) --
    a RUNNING run with a FRESH heartbeat must be left alone, since a cloud
    worker CAN outlive the server process, unlike a local subprocess worker."""
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


def _fresh_executor(reg, jobs=None, execs=None, max_parallel=1):
    """A brand-new executor over an existing registry: the restart."""
    return CloudRunJobExecutor(
        reg,
        cfg=_cfg(),
        max_parallel=max_parallel,
        jobs_client=jobs or _FakeJobs(),
        executions_client=execs or _FakeExecutions(),
    )


def _queued(reg, run_id, created="2026-09-10T00:00:00+00:00"):
    run = _run("cloud_cpu").model_copy(
        update={"run_id": run_id, "created_at": created, "config_fingerprint": run_id}
    )
    reg.create(run)
    reg.write_state(OptimizationRunState(run_id=run_id, status=RunStatus.QUEUED))
    return run


def test_queued_run_is_dispatched_after_a_restart(tmp_path):
    from google_meridian_mcp_server.persistence.optimization_run_registry import (
        LocalOptimizationRunRegistry,
    )

    reg = LocalOptimizationRunRegistry(str(tmp_path))
    run = _queued(reg, "m-1")

    jobs = _FakeJobs()
    _fresh_executor(reg, jobs).reconcile_orphans()

    assert len(jobs.calls) == 1  # actually dispatched, not merely dequeued
    assert reg.get_dispatch(run.run_id).execution_name == "exec-123"


def test_restart_adopts_a_running_run_and_restores_liveness(tmp_path):
    """The correction: in-flight runs are RUNNING by the time a restart
    happens, so gating adoption on QUEUED would never adopt anything and
    max_parallel would go unenforced against the survivors."""
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
    reg.claim_dispatch(OptimizationRunDispatch(run_id=run.run_id, claimed_at=_now()))
    reg.write_dispatch(
        OptimizationRunDispatch(
            run_id=run.run_id, claimed_at=_now(), execution_name="exec-live"
        )
    )

    jobs, execs = _FakeJobs(), _FakeExecutions()
    ex = _fresh_executor(reg, jobs, execs)
    ex.reconcile_orphans()

    assert jobs.calls == []  # not relaunched
    assert ex._handles[run.run_id] == "exec-live"  # adopted, by exact name
    assert execs.asked == ["exec-live"]  # and liveness actually probed


def test_restart_does_not_adopt_a_running_run_with_no_dispatch_document(tmp_path):
    """Migration guard: on the first boot after this ships, every in-flight run
    has no dispatch document. Adopting or re-dispatching those would double-launch
    every live GPU execution."""
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

    jobs = _FakeJobs()
    ex = _fresh_executor(reg, jobs)
    ex.reconcile_orphans()

    assert jobs.calls == []
    assert ex._handles == {}
    assert reg.get_state(run.run_id).status is RunStatus.RUNNING


def test_adopted_handles_occupy_slots_so_a_queued_run_waits(tmp_path):
    """The cost brake, restored. With both slots adopted the recovered run must
    NOT dispatch -- an earlier draft of the live gate asserted the opposite."""
    from google_meridian_mcp_server.persistence.optimization_run_registry import (
        LocalOptimizationRunRegistry,
    )

    reg = LocalOptimizationRunRegistry(str(tmp_path))
    for run_id in ("m-live-1", "m-live-2"):
        run = _run("cloud_cpu").model_copy(
            update={"run_id": run_id, "config_fingerprint": run_id}
        )
        reg.create(run)
        reg.write_state(
            OptimizationRunState(
                run_id=run_id, status=RunStatus.RUNNING, heartbeat_at=_now()
            )
        )
        reg.claim_dispatch(OptimizationRunDispatch(run_id=run_id, claimed_at=_now()))
        reg.write_dispatch(
            OptimizationRunDispatch(
                run_id=run_id, claimed_at=_now(), execution_name=f"exec-{run_id}"
            )
        )
    _queued(reg, "m-waiting")

    jobs = _FakeJobs()
    ex = _fresh_executor(reg, jobs, max_parallel=2)
    ex.reconcile_orphans()

    assert jobs.calls == []  # cap full: nothing new launched
    assert len(ex._handles) == 2
    assert list(ex._queue) == ["m-waiting"]  # queued, and still queued
    assert reg.get_state("m-waiting").status is RunStatus.QUEUED


def test_stale_heartbeat_running_run_is_still_failed_by_the_override(tmp_path):
    """The override must call super(). Dropping the RUNNING branch would leave a
    fresh-heartbeat run untouched too, so test_cloud_run_executor.py:121 cannot
    catch its loss."""
    from google_meridian_mcp_server.persistence.optimization_run_registry import (
        LocalOptimizationRunRegistry,
    )

    reg = LocalOptimizationRunRegistry(str(tmp_path))
    run = _run("cloud_cpu")
    reg.create(run)
    reg.write_state(
        OptimizationRunState(
            run_id=run.run_id,
            status=RunStatus.RUNNING,
            heartbeat_at=(
                datetime.now(timezone.utc) - timedelta(seconds=600)
            ).isoformat(),
        )
    )

    _fresh_executor(reg).reconcile_orphans()

    assert reg.get_state(run.run_id).status is RunStatus.FAILED


def test_a_young_claim_over_a_queued_run_is_left_alone(tmp_path):
    """Branch 3. A peer may be inside run_job() right now.

    Without this test the whole DEFAULT_DISPATCH_STALE_SECONDS check can be
    deleted and every other Task 7 test still passes.
    """
    from google_meridian_mcp_server.persistence.optimization_run_registry import (
        LocalOptimizationRunRegistry,
    )

    reg = LocalOptimizationRunRegistry(str(tmp_path))
    run = _queued(reg, "m-young")
    reg.claim_dispatch(OptimizationRunDispatch(run_id=run.run_id, claimed_at=_now()))

    jobs = _FakeJobs()
    ex = _fresh_executor(reg, jobs)
    ex.reconcile_orphans()

    assert jobs.calls == []
    assert list(ex._queue) == []  # not re-enqueued
    assert reg.get_state(run.run_id).status is RunStatus.QUEUED  # and not failed
    assert reg.get_state(run.run_id).error is None
    assert run.run_id not in ex._handles


def test_a_stale_claim_over_a_live_run_is_not_failed(tmp_path):
    """A crash after run_job() but before write_dispatch leaves a claim with no
    execution name while the execution is live and billing."""
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
    old = (datetime.now(timezone.utc) - timedelta(seconds=7200)).isoformat()
    reg.claim_dispatch(OptimizationRunDispatch(run_id=run.run_id, claimed_at=old))

    _fresh_executor(reg).reconcile_orphans()

    assert reg.get_state(run.run_id).status is RunStatus.RUNNING


def test_a_stale_claim_over_a_queued_run_is_abandoned(tmp_path):
    from google_meridian_mcp_server.persistence.optimization_run_registry import (
        LocalOptimizationRunRegistry,
    )

    reg = LocalOptimizationRunRegistry(str(tmp_path))
    run = _queued(reg, "m-stale")
    old = (datetime.now(timezone.utc) - timedelta(seconds=7200)).isoformat()
    reg.claim_dispatch(OptimizationRunDispatch(run_id=run.run_id, claimed_at=old))

    jobs = _FakeJobs()
    _fresh_executor(reg, jobs).reconcile_orphans()

    assert jobs.calls == []
    state = reg.get_state(run.run_id)
    assert state.status is RunStatus.FAILED
    # NOT worker_lost: no worker ever started, and Task 4 exists precisely
    # because worker_lost is a catch-all rather than a diagnosis.
    assert state.error["code"] == "dispatch_abandoned"
    assert state.error["message"] == "dispatch claimed but never launched"


def test_abandoning_a_stale_dispatch_writes_with_the_read_generation(tmp_path):
    """expected_generation is the protection against clobbering a state another
    writer advanced. Every other test here runs on the local provider, which
    ignores it -- so without this the kwarg can be deleted silently.
    """
    from google_meridian_mcp_server.persistence.optimization_run_registry import (
        LocalOptimizationRunRegistry,
    )

    reg = LocalOptimizationRunRegistry(str(tmp_path))
    run = _queued(reg, "m-gen")
    old = (datetime.now(timezone.utc) - timedelta(seconds=7200)).isoformat()
    reg.claim_dispatch(OptimizationRunDispatch(run_id=run.run_id, claimed_at=old))

    seen = []
    real_write = reg.write_state
    reg.get_state_generation = lambda run_id: 7
    reg.write_state = lambda s, *, expected_generation=None: (
        seen.append(expected_generation) or real_write(s)
    )

    _fresh_executor(reg).reconcile_orphans()

    assert seen[-1] == 7  # not None: an unconditional write would clobber


def test_the_executor_writes_no_state_on_the_claim_dispatch_and_adopt_paths(tmp_path):
    """The plan's own load-bearing constraint, pinned at the executor level.

    A registry-level test cannot fail here: it is the EXECUTOR that must not
    write state.json to record dispatch.
    """
    from google_meridian_mcp_server.persistence.optimization_run_registry import (
        LocalOptimizationRunRegistry,
    )

    reg = LocalOptimizationRunRegistry(str(tmp_path))
    _queued(reg, "m-fresh")
    adopted = _run("cloud_cpu").model_copy(
        update={"run_id": "m-adopted", "config_fingerprint": "m-adopted"}
    )
    reg.create(adopted)
    reg.write_state(
        OptimizationRunState(
            run_id="m-adopted", status=RunStatus.RUNNING, heartbeat_at=_now()
        )
    )
    reg.claim_dispatch(OptimizationRunDispatch(run_id="m-adopted", claimed_at=_now()))
    reg.write_dispatch(
        OptimizationRunDispatch(
            run_id="m-adopted", claimed_at=_now(), execution_name="exec-live"
        )
    )

    calls = []
    real_write = reg.write_state
    reg.write_state = lambda s, *, expected_generation=None: (
        calls.append(s.status) or real_write(s)
    )

    _fresh_executor(reg, max_parallel=2).reconcile_orphans()

    assert calls == []  # claim, dispatch and adoption are all state-free


def test_recovered_runs_are_dispatched_oldest_first(tmp_path):
    """registry.list() sorts created_at DESCENDING, so using it unreversed
    would dispatch newest-first."""
    from google_meridian_mcp_server.persistence.optimization_run_registry import (
        LocalOptimizationRunRegistry,
    )

    reg = LocalOptimizationRunRegistry(str(tmp_path))
    _queued(reg, "m-old", created="2026-09-10T00:00:00+00:00")
    _queued(reg, "m-new", created="2026-09-10T01:00:00+00:00")

    jobs = _FakeJobs()
    ex = _fresh_executor(reg, jobs, max_parallel=1)
    ex.reconcile_orphans()

    assert len(jobs.calls) == 1
    env = {e.name: e.value for e in jobs.calls[0].overrides.container_overrides[0].env}
    assert env["OPTIMIZATION_RUN_ID"] == "m-old"  # which, not just how many
    assert reg.get_dispatch("m-new") is None


def test_cancel_terminates_an_execution_this_process_never_launched(tmp_path):
    """The billing leak. Before this, cancel wrote CANCELED and returned while
    the Cloud Run execution kept running -- so a test that checked only the
    tool response would confirm the bug rather than the fix."""
    from google_meridian_mcp_server.persistence.optimization_run_registry import (
        LocalOptimizationRunRegistry,
    )

    class _RecordingExecutions:
        def __init__(self):
            self.canceled = []

        def get_execution(self, name):
            return SimpleNamespace(completion_time=None)

        def cancel_execution(self, name):
            self.canceled.append(name)

    reg = LocalOptimizationRunRegistry(str(tmp_path))
    run = _run("cloud_cpu")
    reg.create(run)
    reg.write_state(
        OptimizationRunState(
            run_id=run.run_id, status=RunStatus.RUNNING, heartbeat_at=_now()
        )
    )
    reg.claim_dispatch(OptimizationRunDispatch(run_id=run.run_id, claimed_at=_now()))
    reg.write_dispatch(
        OptimizationRunDispatch(
            run_id=run.run_id, claimed_at=_now(), execution_name="exec-live"
        )
    )

    executions = _RecordingExecutions()
    ex = CloudRunJobExecutor(
        reg,
        cfg=_cfg(),
        max_parallel=1,
        jobs_client=_FakeJobs(),
        executions_client=executions,
    )  # fresh process: _handles is empty

    ex.cancel(run.run_id)

    assert executions.canceled == ["exec-live"]
    assert reg.get_state(run.run_id).status is RunStatus.CANCELED


def test_cancel_of_a_completed_run_does_not_touch_the_execution(tmp_path):
    """OptimizationService.delete calls cancel() first (optimization_service.py:332),
    so without a state check every delete of a finished cloud run would fire a
    pointless cancel_execution against a terminal execution."""
    from google_meridian_mcp_server.persistence.optimization_run_registry import (
        LocalOptimizationRunRegistry,
    )

    class _RecordingExecutions:
        def __init__(self):
            self.canceled = []

        def get_execution(self, name):
            return SimpleNamespace(completion_time="2026-09-10T01:00:00Z")

        def cancel_execution(self, name):
            self.canceled.append(name)

    reg = LocalOptimizationRunRegistry(str(tmp_path))
    run = _run("cloud_cpu")
    reg.create(run)
    reg.write_state(OptimizationRunState(run_id=run.run_id, status=RunStatus.COMPLETED))
    reg.claim_dispatch(OptimizationRunDispatch(run_id=run.run_id, claimed_at=_now()))
    reg.write_dispatch(
        OptimizationRunDispatch(
            run_id=run.run_id, claimed_at=_now(), execution_name="exec-done"
        )
    )

    executions = _RecordingExecutions()
    ex = CloudRunJobExecutor(
        reg,
        cfg=_cfg(),
        max_parallel=1,
        jobs_client=_FakeJobs(),
        executions_client=executions,
    )

    ex.cancel(run.run_id)

    assert executions.canceled == []
    assert reg.get_state(run.run_id).status is RunStatus.COMPLETED

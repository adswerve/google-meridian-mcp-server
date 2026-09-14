"""Executor that runs the worker as a Cloud Run Job execution."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from google_meridian_mcp_server.domain.models import RuntimeConfig
from google_meridian_mcp_server.domain.optimization import (
    OptimizationRun,
    OptimizationRunDispatch,
    OptimizationRunState,
    RunStatus,
)
from google_meridian_mcp_server.execution.base_executor import (
    DEFAULT_DISPATCH_STALE_SECONDS,
    DEFAULT_HEARTBEAT_STALE_SECONDS,
    BaseExecutor,
)
from google_meridian_mcp_server.execution.base_subprocess import MERIDIAN_BACKEND
from google_meridian_mcp_server.persistence.optimization_run_registry import (
    OptimizationRunRegistry,
)

log = logging.getLogger(__name__)


class CloudRunJobExecutor(BaseExecutor):
    def __init__(
        self,
        registry: OptimizationRunRegistry,
        *,
        cfg: RuntimeConfig,
        max_parallel: int,
        heartbeat_stale_seconds: int = DEFAULT_HEARTBEAT_STALE_SECONDS,
        jobs_client: Any | None = None,
        executions_client: Any | None = None,
    ) -> None:
        super().__init__(
            registry,
            max_parallel=max_parallel,
            heartbeat_stale_seconds=heartbeat_stale_seconds,
        )
        self._cfg = cfg
        self._jobs = jobs_client or self._default_jobs_client()
        self._executions = executions_client or self._default_executions_client()

    @staticmethod
    def _default_jobs_client():
        from google.cloud import run_v2

        return run_v2.JobsClient()

    @staticmethod
    def _default_executions_client():
        from google.cloud import run_v2

        return run_v2.ExecutionsClient()

    def reconcile_orphans(self) -> None:
        """Rebuild the in-memory queue and re-adopt in-flight executions.

        The base implementation only reconciles RUNNING runs by heartbeat
        staleness, so before this a QUEUED cloud run survived a restart with no
        in-memory counterpart and nothing that would ever look at it again.

        Adoption is keyed on a recorded execution_name, NOT on status: the
        worker writes RUNNING as soon as it starts, so by restart time the
        in-flight runs are RUNNING. An execution_name can only exist because
        write_dispatch recorded it, so a pre-migration in-flight run has no
        document and is left entirely alone -- that is the migration guard.
        """
        super().reconcile_orphans()
        with self._lock:
            # Adopt anything this design dispatched, whatever its status, so
            # liveness and cancel work again and max_parallel is enforced
            # against the survivors.
            for summary in self._registry.list(status=RunStatus.RUNNING):
                dispatch = self._registry.get_dispatch(summary.run_id)
                if dispatch and dispatch.execution_name:
                    self._handles[summary.run_id] = dispatch.execution_name

            now = datetime.now(timezone.utc)
            # list() sorts created_at descending; dispatch oldest-first.
            for summary in sorted(
                self._registry.list(status=RunStatus.QUEUED),
                key=lambda s: s.created_at,
            ):
                run_id = summary.run_id
                dispatch = self._registry.get_dispatch(run_id)
                if dispatch is None:
                    self._queue.append(run_id)
                    continue
                if dispatch.execution_name:
                    self._handles[run_id] = dispatch.execution_name
                    continue
                age = (
                    now - datetime.fromisoformat(dispatch.claimed_at)
                ).total_seconds()
                if age <= DEFAULT_DISPATCH_STALE_SECONDS:
                    # A peer instance may be inside run_job() right now. Leave
                    # it: re-enqueuing would only lose the claim (the document
                    # already exists) and skip the run anyway.
                    continue
                self._fail_stale_dispatch(run_id)
            self.pump()

    def _fail_stale_dispatch(self, run_id: str) -> None:
        """Fail a run whose claimer died before it managed to call run_job.

        Reads state first: a crash AFTER run_job() succeeded but before the
        execution name was recorded produces an identical document while the
        execution is live and billing. Cannot reuse _reconcile_stale, which
        gates on RUNNING and a non-null heartbeat and is a no-op on a QUEUED
        run.
        """
        from google.api_core.exceptions import PreconditionFailed

        gen = self._registry.get_state_generation(run_id)
        if self._registry.get_state(run_id).status is not RunStatus.QUEUED:
            return
        try:
            self._registry.write_state(
                OptimizationRunState(
                    run_id=run_id,
                    status=RunStatus.FAILED,
                    error={
                        "code": "dispatch_abandoned",
                        "message": "dispatch claimed but never launched",
                    },
                ),
                expected_generation=gen,
            )
        except PreconditionFailed:
            # Someone advanced the state between our read and our write.
            return

    def _job_name(self, tier: str) -> str:
        job = self._cfg.cloud_run_job_for_tier(tier)
        return (
            f"projects/{self._cfg.cloud_run_project}"
            f"/locations/{self._cfg.cloud_run_region}/jobs/{job}"
        )

    def _launch(self, run: OptimizationRun) -> Any:
        from google.cloud import run_v2

        tier = run.compute_tier_resolved
        # child_env() cannot help here: the Cloud Run Job container does not
        # inherit this process's environment, so the constants are injected as
        # explicit container overrides.
        env = [
            run_v2.EnvVar(name="OPTIMIZATION_RUN_ID", value=run.run_id),
            run_v2.EnvVar(name="MERIDIAN_BACKEND", value=MERIDIAN_BACKEND),
            run_v2.EnvVar(name="MERIDIAN_ENABLE_JAX_X64", value="true"),
        ]
        request = run_v2.RunJobRequest(
            name=self._job_name(tier),
            overrides=run_v2.RunJobRequest.Overrides(
                container_overrides=[
                    run_v2.RunJobRequest.Overrides.ContainerOverride(env=env)
                ]
            ),
        )
        operation = self._jobs.run_job(request)
        # Do NOT block on operation.result(); the worker drives the registry.
        # NOTE: operation.metadata.name is the Execution resource name per the
        # run_v2 client docs at time of authoring; verified live in Task 9/10.
        return operation.metadata.name  # the Execution resource name

    def _is_alive(self, handle: Any) -> bool:
        from google.api_core.exceptions import NotFound

        try:
            execution = self._executions.get_execution(name=handle)
        except NotFound:
            # GCP collected the Execution. Not alive; _fail_if_unfinished will
            # respect a terminal state or an existing result.
            return False
        return not getattr(execution, "completion_time", None)

    def _claim(self, run_id: str) -> Any | None:
        dispatch = OptimizationRunDispatch(
            run_id=run_id, claimed_at=datetime.now(timezone.utc).isoformat()
        )
        return dispatch if self._registry.claim_dispatch(dispatch) else None

    def _record_dispatch(self, claim: Any, handle: Any) -> None:
        try:
            # model_copy does not validate, which is fine: handle is always
            # operation.metadata.name, a str (cloud_run_executor.py:82).
            self._registry.write_dispatch(
                claim.model_copy(update={"execution_name": handle})
            )
        except Exception:  # noqa: BLE001 - the execution is already running
            # Must never fail the run: run_job() succeeded, so the execution is
            # live and billing. Losing the name costs cancel-by-name, not the run.
            log.warning(
                "dispatched %s as %s but could not record it",
                claim.run_id,
                handle,
                exc_info=True,
            )

    def _on_alive(self, run_id: str) -> None:
        # Remote liveness is coarse; stale heartbeat is the authoritative crash signal.
        self._reconcile_stale(run_id)

    def _launch_error_code(self, exc: Exception) -> str:
        # A dangling CLOUD_RUN_JOB_* name is a permanent misconfiguration, not a
        # lost worker, and must not read as retryable. There is no boot-time
        # probe (see the design doc), so this is the only guard that catches a
        # job deleted after terraform apply.
        from google.api_core.exceptions import NotFound, PermissionDenied

        if isinstance(exc, NotFound):
            return "cloud_job_not_found"
        if isinstance(exc, PermissionDenied):
            return "cloud_job_permission_denied"
        return super()._launch_error_code(exc)

    def _terminate(self, handle: Any) -> None:
        # Best-effort cancel of the running execution (used by cancel_optimization).
        try:
            self._executions.cancel_execution(name=handle)
        except Exception:  # noqa: BLE001 - best effort
            pass

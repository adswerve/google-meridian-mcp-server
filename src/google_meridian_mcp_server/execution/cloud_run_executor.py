"""Executor that runs the worker as a Cloud Run Job execution."""

from __future__ import annotations

from typing import Any

from google_meridian_mcp_server.domain.models import RuntimeConfig
from google_meridian_mcp_server.domain.optimization import OptimizationRun
from google_meridian_mcp_server.execution.base_executor import BaseExecutor
from google_meridian_mcp_server.persistence.optimization_run_registry import (
    OptimizationRunRegistry,
)


class CloudRunJobExecutor(BaseExecutor):
    def __init__(
        self,
        registry: OptimizationRunRegistry,
        *,
        cfg: RuntimeConfig,
        max_parallel: int,
        heartbeat_stale_seconds: int,
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

    def _job_name(self, tier: str) -> str:
        job = self._cfg.cloud_run_job_for_tier(tier)
        return (
            f"projects/{self._cfg.cloud_run_project}"
            f"/locations/{self._cfg.cloud_run_region}/jobs/{job}"
        )

    def _launch(self, run: OptimizationRun) -> Any:
        from google.cloud import run_v2

        tier = run.compute_tier_resolved
        backend = self._cfg.backend_for_tier(tier)
        env = [
            run_v2.EnvVar(name="OPTIMIZATION_RUN_ID", value=run.run_id),
            run_v2.EnvVar(name="MERIDIAN_BACKEND", value=backend),
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
        execution = self._executions.get_execution(handle)
        return not getattr(execution, "completion_time", None)

    def _on_alive(self, run_id: str) -> None:
        # Remote liveness is coarse; stale heartbeat is the authoritative crash signal.
        self._reconcile_stale(run_id)

    def _terminate(self, handle: Any) -> None:
        # Best-effort cancel of the running execution (used by cancel_optimization).
        try:
            self._executions.cancel_execution(name=handle)
        except Exception:  # noqa: BLE001 - best effort
            pass

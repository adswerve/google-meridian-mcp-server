"""Shared construction of runtime objects (used by the server lifespan and worker)."""

from __future__ import annotations

from google_meridian_mcp_server.domain.models import PersistenceBackend, RuntimeConfig
from google_meridian_mcp_server.persistence.cache import DiscoveryCache
from google_meridian_mcp_server.persistence.gcs_provider import GcsModelProvider
from google_meridian_mcp_server.persistence.local_provider import LocalModelProvider
from google_meridian_mcp_server.persistence.optimization_run_registry import (
    LocalOptimizationRunRegistry,
    OptimizationRunRegistry,
)


def build_provider(cfg: RuntimeConfig):
    """Shared provider construction (server + worker); no Meridian dependency."""
    if cfg.persistence_backend == PersistenceBackend.GCS.value:
        return GcsModelProvider(cfg.gcs_bucket, cfg.gcs_models_prefix)
    return LocalModelProvider(cfg.local_models_root)


def build_discovery_cache(cfg: RuntimeConfig) -> DiscoveryCache:
    """Server-side: discovery only, no facades/materialization (never pulls Meridian)."""
    provider = build_provider(cfg)
    return DiscoveryCache(provider, cfg.discovery_ttl_seconds)


# NOTE: build_worker_catalog (full ModelCatalog with materialization + facades)
# lives in execution/worker.py, not here. bootstrap.py is imported by the
# server lifespan and must stay provably free of the meridian subpackage
# (enforced by ruff TID251); worker.py is the worker-only import boundary.


def build_registry(cfg: RuntimeConfig) -> OptimizationRunRegistry:
    if cfg.persistence_backend == PersistenceBackend.GCS.value:
        from google_meridian_mcp_server.persistence.optimization_run_registry import (
            GcsOptimizationRunRegistry,
        )

        return GcsOptimizationRunRegistry(cfg.gcs_bucket, cfg.optimization_gcs_prefix)
    return LocalOptimizationRunRegistry(cfg.optimization_runs_root)


def build_executor(
    cfg: RuntimeConfig,
    registry: OptimizationRunRegistry,
    *,
    jobs_client=None,
    executions_client=None,
):
    from google_meridian_mcp_server.domain.models import ComputeTier

    allowed = set(cfg.optimization_allowed_tiers)
    if ComputeTier.LOCAL.value in allowed:
        from google_meridian_mcp_server.execution.subprocess_executor import (
            AsyncSubprocessExecutor,
        )

        return AsyncSubprocessExecutor(
            registry,
            max_parallel=cfg.optimization_max_parallel,
            heartbeat_stale_seconds=cfg.optimization_heartbeat_stale_seconds,
        )
    from google_meridian_mcp_server.execution.cloud_run_executor import (
        CloudRunJobExecutor,
    )

    return CloudRunJobExecutor(
        registry,
        cfg=cfg,
        max_parallel=cfg.optimization_max_parallel,
        heartbeat_stale_seconds=cfg.optimization_heartbeat_stale_seconds,
        jobs_client=jobs_client,
        executions_client=executions_client,
    )


def reconcile_orphans(registry: OptimizationRunRegistry, executor) -> None:
    """On startup, reconcile in-flight runs left over by a stopped server.

    Delegates to the executor: the local subprocess tier unconditionally
    fails any run still RUNNING or QUEUED (the PID-1 parent-death guard in
    worker.py guarantees a local worker cannot survive its parent server, so
    such a run is provably dead); the cloud tier only fails RUNNING runs
    whose heartbeat has gone stale, since a cloud worker CAN outlive the
    server process. See BaseExecutor.reconcile_orphans /
    AsyncSubprocessExecutor.reconcile_orphans.
    """
    # `registry` is unused here (kept for call-site symmetry/back-compat): the
    # executor already holds its own reference to the same registry instance.
    executor.reconcile_orphans()

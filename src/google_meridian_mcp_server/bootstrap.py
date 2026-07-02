"""Shared construction of runtime objects (used by the server lifespan and worker)."""

from __future__ import annotations

from google_meridian_mcp_server.domain.models import PersistenceBackend, RuntimeConfig
from google_meridian_mcp_server.meridian.catalog import ModelCatalog
from google_meridian_mcp_server.persistence.cache import (
    DiscoveryCache,
    MaterializationCache,
)
from google_meridian_mcp_server.persistence.gcs_provider import GcsModelProvider
from google_meridian_mcp_server.persistence.local_provider import LocalModelProvider
from google_meridian_mcp_server.persistence.optimization_run_registry import (
    LocalOptimizationRunRegistry,
    OptimizationRunRegistry,
)


def build_model_catalog(cfg: RuntimeConfig) -> ModelCatalog:
    if cfg.persistence_backend == PersistenceBackend.GCS.value:
        provider = GcsModelProvider(cfg.gcs_bucket, cfg.gcs_models_prefix)
    else:
        provider = LocalModelProvider(cfg.local_models_root)
    discovery = DiscoveryCache(provider, cfg.discovery_ttl_seconds)
    materialization = MaterializationCache(provider, cfg.model_cache_root)
    return ModelCatalog(discovery, materialization)


def build_registry(cfg: RuntimeConfig) -> OptimizationRunRegistry:
    if cfg.resolved_registry_backend == PersistenceBackend.GCS.value:
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
            SubprocessExecutor,
        )

        return SubprocessExecutor(
            registry,
            max_parallel=cfg.optimization_max_parallel,
            heartbeat_stale_seconds=cfg.optimization_heartbeat_stale_seconds,
            backend=cfg.optimization_backend_local,
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
    """On startup, fail runs left RUNNING with a stale heartbeat (crash during downtime)."""
    from google_meridian_mcp_server.domain.optimization import RunStatus

    for summary in registry.list(status=RunStatus.RUNNING):
        executor._reconcile_stale(summary.run_id)

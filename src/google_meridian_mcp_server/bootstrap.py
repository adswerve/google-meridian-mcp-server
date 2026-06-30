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

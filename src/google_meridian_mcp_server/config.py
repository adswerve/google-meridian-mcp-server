"""Dotenv-backed runtime configuration."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

from google_meridian_mcp_server.domain.models import RuntimeConfig

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = PROJECT_ROOT / ".env"

load_dotenv(ENV_FILE)


def _read_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _read_csv(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    value = os.getenv(name)
    if not value:
        return default
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _read_thresholds(name: str, default: tuple[int, int]) -> tuple[int, int]:
    value = os.getenv(name)
    if not value:
        return default
    parts = [int(item.strip()) for item in value.split(",")]
    if len(parts) != 2:
        raise ValueError(f"{name} must be 'T_local,T_gpu'")
    return (parts[0], parts[1])


def load_config() -> RuntimeConfig:
    """Build a RuntimeConfig from environment variables."""
    result_cache_ttl = os.getenv("RESULT_CACHE_TTL_SECONDS")

    return RuntimeConfig(
        transport=os.getenv("MCP_TRANSPORT", "streamable-http"),
        persistence_backend=os.getenv("PERSISTENCE_BACKEND", "local"),
        local_models_root=os.getenv("LOCAL_MODELS_ROOT"),
        gcs_bucket=os.getenv("GCS_BUCKET"),
        gcs_models_prefix=os.getenv("GCS_MODELS_PREFIX"),
        discovery_ttl_seconds=int(os.getenv("DISCOVERY_TTL_SECONDS", "7200")),
        model_cache_root=os.getenv("MODEL_CACHE_ROOT", "/tmp/mmm-models"),
        result_cache_enabled=_read_bool("RESULT_CACHE_ENABLED", True),
        result_cache_ttl_seconds=int(result_cache_ttl) if result_cache_ttl else None,
        registry_backend=os.getenv("REGISTRY_BACKEND"),
        optimization_runs_root=os.getenv("OPTIMIZATION_RUNS_ROOT", "./optimizations"),
        optimization_gcs_prefix=os.getenv("OPTIMIZATION_GCS_PREFIX", "optimizations/"),
        optimization_allowed_tiers=_read_csv("OPTIMIZATION_ALLOWED_TIERS", ("local",)),
        optimization_default_tier=os.getenv("OPTIMIZATION_DEFAULT_TIER", "auto"),
        optimization_max_parallel=int(os.getenv("OPTIMIZATION_MAX_PARALLEL", "2")),
        optimization_size_thresholds=_read_thresholds(
            "OPTIMIZATION_SIZE_THRESHOLDS", (1_000_000, 100_000_000)
        ),
        optimization_heartbeat_stale_seconds=int(
            os.getenv("OPTIMIZATION_HEARTBEAT_STALE_SECONDS", "60")
        ),
        optimization_backend_local=os.getenv(
            "OPTIMIZATION_BACKEND_LOCAL", "tensorflow"
        ),
    )

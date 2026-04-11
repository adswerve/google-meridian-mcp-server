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
    )

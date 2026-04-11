"""Shared test fixtures."""

from __future__ import annotations

import pytest


@pytest.fixture()
def sample_runtime_config():
    """Return a minimal RuntimeConfig for local-backend tests."""
    from google_meridian_mcp_server.domain.models import RuntimeConfig

    return RuntimeConfig(
        transport="streamable-http",
        persistence_backend="local",
        local_models_root="./test-models",
        model_cache_root="/tmp/mmm-models-test",
        discovery_ttl_seconds=60,
        result_cache_enabled=False,
    )

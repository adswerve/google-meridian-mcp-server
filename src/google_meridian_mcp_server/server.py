"""FastMCP server factory with default streamable-http transport."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastmcp import FastMCP

from google_meridian_mcp_server.config import load_config
from google_meridian_mcp_server.domain.models import PersistenceBackend, Transport
from google_meridian_mcp_server.meridian.catalog import ModelCatalog
from google_meridian_mcp_server.persistence.cache import (
    DiscoveryCache,
    MaterializationCache,
    ResultCache,
)
from google_meridian_mcp_server.persistence.gcs_provider import GcsModelProvider
from google_meridian_mcp_server.persistence.local_provider import LocalModelProvider
from google_meridian_mcp_server.transport.tools import register_tools

log = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(server: FastMCP):
    """Initialize shared runtime state available to all tools."""
    cfg = load_config()
    log.info(
        "Starting server: transport=%s backend=%s",
        cfg.transport,
        cfg.persistence_backend,
    )

    # Build the provider
    if cfg.persistence_backend == PersistenceBackend.GCS.value:
        provider = GcsModelProvider(cfg.gcs_bucket, cfg.gcs_models_prefix)
    else:
        provider = LocalModelProvider(cfg.local_models_root)

    discovery_cache = DiscoveryCache(provider, cfg.discovery_ttl_seconds)
    materialization_cache = MaterializationCache(provider, cfg.model_cache_root)
    model_catalog = ModelCatalog(discovery_cache, materialization_cache)
    result_cache = ResultCache(
        enabled=cfg.result_cache_enabled,
        ttl_seconds=cfg.result_cache_ttl_seconds,
    )
    log.info(
        "Result cache: enabled=%s ttl=%s",
        cfg.result_cache_enabled,
        cfg.result_cache_ttl_seconds,
    )

    yield {
        "config": cfg,
        "model_catalog": model_catalog,
        "result_cache": result_cache,
    }


def create_server() -> FastMCP:
    """Build and return a configured FastMCP server instance."""
    mcp = FastMCP(
        "Google Meridian MCP Server",
        lifespan=_lifespan,
    )

    register_tools(mcp)
    return mcp


mcp = create_server()
server = mcp


def run_server() -> None:
    """Run the configured server using the selected transport."""
    cfg = load_config()

    if cfg.transport == Transport.STDIO.value:
        mcp.run(transport="stdio")
        return

    host = os.getenv("MCP_HOST", "0.0.0.0")
    port = int(os.getenv("PORT", os.getenv("MCP_PORT", "8000")))
    mcp.run(transport="http", host=host, port=port)


if __name__ == "__main__":
    run_server()

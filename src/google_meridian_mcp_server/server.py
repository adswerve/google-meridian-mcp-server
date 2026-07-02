"""FastMCP server factory with default streamable-http transport."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastmcp import FastMCP
from fastmcp.server.providers.skills import SkillsDirectoryProvider

from google_meridian_mcp_server.bootstrap import build_model_catalog
from google_meridian_mcp_server.config import load_config
from google_meridian_mcp_server.domain.models import Transport
from google_meridian_mcp_server.persistence.cache import ResultCache
from google_meridian_mcp_server.transport.tools import register_tools

log = logging.getLogger(__name__)

_SKILLS_ROOT = Path(__file__).resolve().parents[2] / "skills"

_SERVER_INSTRUCTIONS = (
    "Google Meridian marketing-mix-model analysis and budget optimization tools. "
    "This server bundles a 'meridian-analyst' skill with orchestration, domain, and "
    "scenario guidance. Read skill://meridian-analyst/SKILL.md before analysis — "
    "especially for budget optimization, reallocation, or channel-performance "
    "questions."
)


@asynccontextmanager
async def _lifespan(server: FastMCP):
    """Initialize shared runtime state available to all tools."""
    cfg = load_config()
    log.info(
        "Starting server: transport=%s backend=%s",
        cfg.transport,
        cfg.persistence_backend,
    )

    model_catalog = build_model_catalog(cfg)
    result_cache = ResultCache(
        enabled=cfg.result_cache_enabled,
        ttl_seconds=cfg.result_cache_ttl_seconds,
    )
    log.info(
        "Result cache: enabled=%s ttl=%s",
        cfg.result_cache_enabled,
        cfg.result_cache_ttl_seconds,
    )

    from google_meridian_mcp_server.bootstrap import (
        build_executor,
        build_registry,
        reconcile_orphans,
    )

    optimization_registry = build_registry(cfg)
    optimization_executor = build_executor(cfg, optimization_registry)
    try:
        reconcile_orphans(optimization_registry, optimization_executor)
    except Exception:  # noqa: BLE001 - reconcile is best-effort startup hygiene
        log.warning("startup orphan reconcile failed", exc_info=True)

    yield {
        "config": cfg,
        "model_catalog": model_catalog,
        "result_cache": result_cache,
        "optimization_registry": optimization_registry,
        "optimization_executor": optimization_executor,
    }


def create_server() -> FastMCP:
    """Build and return a configured FastMCP server instance."""
    mcp = FastMCP(
        "Google Meridian MCP Server",
        instructions=_SERVER_INSTRUCTIONS,
        lifespan=_lifespan,
    )

    register_tools(mcp)
    mcp.add_provider(SkillsDirectoryProvider(roots=_SKILLS_ROOT))
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

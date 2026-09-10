"""FastMCP server factory with default streamable-http transport."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastmcp import FastMCP
from fastmcp.server.providers.skills import SkillsDirectoryProvider

from google_meridian_mcp_server.bootstrap import build_discovery_cache
from google_meridian_mcp_server.config import load_config
from google_meridian_mcp_server.domain.models import Transport
from google_meridian_mcp_server.execution.subprocess_executor import DEFAULT_LOG_ROOT
from google_meridian_mcp_server.execution.sync_subprocess_executor import (
    SyncSubprocessExecutor,
    sweep_stale_entries,
)
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

    discovery_cache = build_discovery_cache(cfg)
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

    analysis_runner = SyncSubprocessExecutor(
        semaphore=asyncio.Semaphore(cfg.analysis_max_parallel),
        run_timeout=cfg.analysis_worker_timeout,
        queue_wait_timeout=cfg.analysis_queue_wait_timeout,
        max_response_bytes=cfg.analysis_max_response_bytes,
        workdir_root=cfg.analysis_workdir_root,
        env_base={
            "PERSISTENCE_BACKEND": cfg.persistence_backend,
            **(
                {"LOCAL_MODELS_ROOT": cfg.local_models_root}
                if cfg.local_models_root
                else {}
            ),
            **({"GCS_BUCKET": cfg.gcs_bucket} if cfg.gcs_bucket else {}),
            **(
                {"GCS_MODELS_PREFIX": cfg.gcs_models_prefix}
                if cfg.gcs_models_prefix
                else {}
            ),
            "MODEL_CACHE_ROOT": cfg.model_cache_root,
        },
    )

    # F10b: age-based sweep of retained analysis workdirs + optimization worker
    # log files, run once at startup (not on every spawn). Best-effort startup
    # hygiene, same posture as reconcile_orphans above.
    try:
        sweep_stale_entries(cfg.analysis_workdir_root, cfg.analysis_workdir_ttl_seconds)
        sweep_stale_entries(DEFAULT_LOG_ROOT, cfg.analysis_workdir_ttl_seconds)
    except Exception:  # noqa: BLE001 - sweep is best-effort startup hygiene
        log.warning("startup workdir/log sweep failed", exc_info=True)

    try:
        yield {
            "config": cfg,
            "discovery_cache": discovery_cache,
            "result_cache": result_cache,
            "optimization_registry": optimization_registry,
            "optimization_executor": optimization_executor,
            "analysis_runner": analysis_runner,
        }
    finally:
        await analysis_runner.shutdown()


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
    # json_response=True keeps every tools/call reply on the uncapped
    # application/json branch. Without it, any handler running longer than
    # mcp's 15s mode-switch window commits the reply to a single SSE frame,
    # which httpx2 clients reject above 1 MiB with the misleading error
    # "SSE stream ended without a response". See
    # tests/integration/test_json_response_mode.py.
    mcp.run(transport="http", host=host, port=port, json_response=True)


if __name__ == "__main__":
    run_server()

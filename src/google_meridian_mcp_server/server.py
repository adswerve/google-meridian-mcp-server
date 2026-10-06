"""FastMCP server factory with default streamable-http transport."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastmcp import FastMCP
from fastmcp.server.providers.skills import SkillsDirectoryProvider
from fastmcp.settings import Settings as FastMCPSettings

from google_meridian_mcp_server.bootstrap import (
    build_discovery_cache,
    start_queue_poller,
    stop_queue_poller,
)
from google_meridian_mcp_server.config import load_config
from google_meridian_mcp_server.domain.models import Transport
from google_meridian_mcp_server.execution.subprocess_executor import DEFAULT_LOG_ROOT
from google_meridian_mcp_server.execution.sync_subprocess_executor import (
    DEFAULT_WORKDIR_ROOT,
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
    " For full-funnel models (get_model_overview shows funnel='full_funnel'), also "
    "read skill://meridian-analyst/references/full-funnel.md."
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
        run_timeout=cfg.analysis_worker_timeout,
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
        sweep_stale_entries(DEFAULT_WORKDIR_ROOT)
        sweep_stale_entries(DEFAULT_LOG_ROOT)
    except Exception:  # noqa: BLE001 - sweep is best-effort startup hygiene
        log.warning("startup workdir/log sweep failed", exc_info=True)

    # Without this the optimization queue advances only when a tool handler
    # happens to call pump(), so a run queued past OPTIMIZATION_MAX_PARALLEL
    # sits QUEUED for as long as the instance is idle. Started after
    # reconcile_orphans, so the first tick sees the recovered queue, and
    # immediately before the try/finally that is guaranteed to cancel it.
    queue_poller = start_queue_poller(
        optimization_executor,
        interval_seconds=cfg.optimization_poll_interval_seconds,
    )
    log.info(
        "Optimization queue poller: interval=%ss max_parallel=%s",
        cfg.optimization_poll_interval_seconds,
        cfg.optimization_max_parallel,
    )

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
        await stop_queue_poller(queue_poller)
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


def stateless_http_enabled() -> bool:
    """Whether streamable HTTP runs stateless. True unless the operator says no.

    Stateful mode keeps each client's session id in this process's memory, so
    after any restart (a deploy, a crash, Cloud Run moving the instance) a client
    still holding its old id is answered 404 "Session not found" on every call
    and never recovers by itself. No tool keeps per-session state -- everything
    shared lives in the lifespan context -- so stateless costs nothing here.

    fastmcp's own default is stateful, and its setting cannot tell "unset" from
    "false", so the default is flipped only when FASTMCP_STATELESS_HTTP was not
    given. ``FASTMCP_STATELESS_HTTP=false`` still turns it off. The settings are
    re-read here rather than taken from ``fastmcp.settings``, which was built at
    import, before ``config.py`` loaded the project ``.env``.
    """
    settings = FastMCPSettings()
    if "stateless_http" in settings.model_fields_set:
        return settings.stateless_http
    return True


def http_app_options() -> dict[str, bool]:
    """Options for the streamable HTTP app, shared by run_server and its tests."""
    # json_response=True keeps every tools/call reply on the uncapped
    # application/json branch. Without it, any handler running longer than
    # mcp's 15s mode-switch window commits the reply to a single SSE frame,
    # which httpx2 clients reject above 1 MiB with the misleading error
    # "SSE stream ended without a response". See
    # tests/integration/test_json_response_mode.py.
    return {"json_response": True, "stateless_http": stateless_http_enabled()}


def run_server() -> None:
    """Run the configured server using the selected transport."""
    cfg = load_config()

    if cfg.transport == Transport.STDIO.value:
        mcp.run(transport="stdio")
        return

    host = os.getenv("MCP_HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    mcp.run(transport="http", host=host, port=port, **http_app_options())


if __name__ == "__main__":
    run_server()

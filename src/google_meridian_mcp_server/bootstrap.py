"""Shared construction of runtime objects (used by the server lifespan and worker)."""

from __future__ import annotations

import asyncio
import contextlib
import logging

from google_meridian_mcp_server.domain.models import PersistenceBackend, RuntimeConfig
from google_meridian_mcp_server.persistence.cache import DiscoveryCache
from google_meridian_mcp_server.persistence.gcs_provider import GcsModelProvider
from google_meridian_mcp_server.persistence.local_provider import LocalModelProvider
from google_meridian_mcp_server.persistence.optimization_run_registry import (
    LocalOptimizationRunRegistry,
    OptimizationRunRegistry,
    RunNotFoundError,
)

log = logging.getLogger(__name__)


def build_provider(cfg: RuntimeConfig):
    """Shared provider construction (server + worker); no Meridian dependency."""
    if cfg.persistence_backend == PersistenceBackend.GCS.value:
        return GcsModelProvider(cfg.gcs_bucket, cfg.gcs_models_prefix)
    return LocalModelProvider(cfg.local_models_root)


def build_discovery_cache(cfg: RuntimeConfig) -> DiscoveryCache:
    """Server-side: discovery only, no facades/materialization (never pulls Meridian)."""
    provider = build_provider(cfg)
    return DiscoveryCache(provider)


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
    from google_meridian_mcp_server.domain.models import OptimizationMode

    if cfg.optimization_tier == OptimizationMode.LOCAL.value:
        from google_meridian_mcp_server.execution.subprocess_executor import (
            AsyncSubprocessExecutor,
        )

        return AsyncSubprocessExecutor(
            registry,
            max_parallel=cfg.optimization_max_parallel,
        )
    from google_meridian_mcp_server.execution.cloud_run_executor import (
        CloudRunJobExecutor,
    )

    return CloudRunJobExecutor(
        registry,
        cfg=cfg,
        max_parallel=cfg.optimization_max_parallel,
        jobs_client=jobs_client,
        executions_client=executions_client,
    )


def _pump_error_types() -> tuple[type[BaseException], ...]:
    """Return the exception families ``pump()`` can actually surface.

    Resolved lazily because every google.* import in this package is deferred:
    bootstrap is imported by the worker too, and a local-tier deployment should
    not pay for the cloud client packages at import time.

    The families, and where each escapes ``pump()``:

    * ``RunNotFoundError`` -- ``_fail_if_unfinished`` writes state for a run a
      peer may have deleted since the read. The one ``MeridianMcpError`` subtype
      ``pump()`` can raise today; the base class is deliberately NOT caught, so
      any other domain error reaching here ends the loop loudly (below) instead
      of being logged past.
    * ``OSError`` -- the local registry's atomic file writes.
    * ``GoogleAPIError`` -- the cloud tier reaches the Cloud Run Executions
      client in ``_reap``/``_is_alive`` and GCS in ``_claim``.
    * ``GoogleAuthError`` -- credential refresh on either of those clients.

    ``_launch`` failures are already handled inside ``pump()`` and never reach
    here.
    """
    from google.api_core.exceptions import GoogleAPIError
    from google.auth.exceptions import GoogleAuthError

    return (RunNotFoundError, OSError, GoogleAPIError, GoogleAuthError)


async def pump_queue_forever(executor, *, interval_seconds: float) -> None:
    """Pump *executor*'s optimization queue every *interval_seconds* until cancelled.

    The queue only advances inside ``BaseExecutor.pump()``, which is otherwise
    called from tool handlers alone -- so a run queued past
    ``OPTIMIZATION_MAX_PARALLEL`` made no progress on an instance nothing was
    calling. Guarantees: it never returns on its own; a pump failure in one of
    the families above is logged and the next tick still happens; and it
    duplicates none of pump()'s locking, reaping or claiming -- two instances
    running this are as safe as two instances serving requests.

    ``pump()`` is synchronous and does blocking registry I/O, so it runs on a
    worker thread rather than on the event loop. A cancellation landing inside
    that call leaves the thread to finish its pump; nothing is left half-done,
    because pump() holds the executor lock for its whole body.
    """
    pump_errors = _pump_error_types()
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            await asyncio.to_thread(executor.pump)
        except pump_errors as exc:
            log.warning(
                "queue poller: pump() failed, continuing: %s", exc, exc_info=True
            )


def _log_poller_exit(task: asyncio.Task) -> None:
    """Report a poller that ended on its own -- it never should."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        log.error(
            "queue poller stopped: queued optimization runs now advance only "
            "when a request arrives",
            exc_info=exc,
        )


def start_queue_poller(executor, *, interval_seconds: float) -> asyncio.Task:
    """Start the background queue pump and return its task.

    The caller owns the task and MUST keep a reference to it (an unreferenced
    task can be garbage-collected mid-flight) and stop it with
    ``stop_queue_poller``.
    """
    task = asyncio.create_task(
        pump_queue_forever(executor, interval_seconds=interval_seconds),
        name="optimization-queue-poller",
    )
    task.add_done_callback(_log_poller_exit)
    return task


async def stop_queue_poller(task: asyncio.Task) -> None:
    """Cancel the queue poller and wait for it to finish.

    Suppresses only the CancelledError this function itself caused; any other
    outcome has already been reported by the task's done-callback.
    """
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


def reconcile_orphans(registry: OptimizationRunRegistry, executor) -> None:
    """On startup, reconcile in-flight runs left over by a stopped server.

    Delegates to the executor: the local subprocess tier unconditionally
    fails any run still RUNNING or QUEUED (the PID-1 parent-death guard in
    worker.py guarantees a local worker cannot survive its parent server, so
    such a run is provably dead); the cloud tier fails RUNNING runs whose
    heartbeat has gone stale, since a cloud worker CAN outlive the server
    process, and additionally rebuilds its in-memory queue from QUEUED runs
    and re-adopts in-flight executions by their recorded execution_name, so
    a restart or scale-to-zero no longer strands a queued or running cloud
    run. See BaseExecutor.reconcile_orphans /
    CloudRunJobExecutor.reconcile_orphans /
    AsyncSubprocessExecutor.reconcile_orphans.
    """
    # `registry` is unused here (kept for call-site symmetry/back-compat): the
    # executor already holds its own reference to the same registry instance.
    executor.reconcile_orphans()

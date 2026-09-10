"""Shared optimization worker: runs one optimization and writes it to the registry."""

from __future__ import annotations

import os
import sys
import threading
import traceback
from datetime import datetime, timezone
from typing import Any

from google_meridian_mcp_server.bootstrap import build_provider
from google_meridian_mcp_server.domain.models import RuntimeConfig
from google_meridian_mcp_server.domain.optimization import (
    OptimizationRunState,
    RunPhase,
    RunStatus,
)
from google_meridian_mcp_server.execution.base_subprocess import MERIDIAN_BACKEND
from google_meridian_mcp_server.meridian.catalog import ModelCatalog
from google_meridian_mcp_server.persistence.cache import (
    DiscoveryCache,
    MaterializationCache,
)
from google_meridian_mcp_server.persistence.optimization_run_registry import (
    OptimizationRunRegistry,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_worker_catalog(cfg: RuntimeConfig) -> ModelCatalog:
    """Worker-side: full catalog with materialization + facades.

    Lives here (not in bootstrap.py) so the server import graph -- which
    imports bootstrap.py for the lifespan -- stays provably free of the
    meridian subpackage; this module is the worker-only import boundary
    (see TID251 per-file-ignores in pyproject.toml).
    """
    provider = build_provider(cfg)
    discovery = DiscoveryCache(provider)
    materialization = MaterializationCache(provider, cfg.model_cache_root)
    return ModelCatalog(discovery, materialization)


def _is_orphaned(original_ppid: int, current_ppid: int) -> bool:
    """Pure predicate: has our parent changed since the guard started?

    A changed ppid means our original parent is gone and we were reparented
    (to init/PID 1 on most systems, or to a subreaper on Linux -- either way,
    a pid that is not our original parent). Comparing against a fixed "== 1"
    is wrong when the server itself runs as PID 1 (e.g. the shipped container,
    which uses exec-form CMD with no init): every worker would see
    getppid() == 1 from birth and exit immediately, before doing any work.
    """
    return current_ppid != original_ppid


def _expected_parent_pid() -> int:
    """Resolve the pid the orphan guard should treat as "our real parent".

    Prefers MERIDIAN_PARENT_PID (set by the PARENT at spawn time, in
    BaseSubprocessExecutor.child_env) over self-capturing os.getppid() here:
    capturing our own getppid() only happens after the full module-import
    chain (0.5-2s after exec), so a parent death DURING that window would
    already have reparented us before we ever recorded an "original" ppid --
    silently masking the orphan. The env var is fixed by the parent before
    fork+exec, so even the guard's very first check (before any poll wait)
    can catch a parent that died during our own import window. Falls back to
    self-captured getppid() when the env var is absent (e.g. the worker
    entrypoint invoked standalone, or in tests).
    """
    env_ppid = os.environ.get("MERIDIAN_PARENT_PID")
    return int(env_ppid) if env_ppid is not None else os.getppid()


def _start_parent_death_guard(poll_interval: float = 2.0) -> threading.Thread:
    """Exit immediately if our parent process dies (reparented away).

    Guards against orphaned worker subprocesses lingering after the parent
    server process crashes or is killed without a chance to clean up children.
    Exits as soon as our parent pid CHANGES from the expected one -- correct
    whether the parent is PID 1 (container) or not. See _expected_parent_pid
    for why the expected pid comes from MERIDIAN_PARENT_PID when available.
    """
    original_ppid = _expected_parent_pid()

    def _watch() -> None:
        if _is_orphaned(original_ppid, os.getppid()):
            os._exit(0)
        while True:
            threading.Event().wait(poll_interval)
            if _is_orphaned(original_ppid, os.getppid()):
                os._exit(0)

    thread = threading.Thread(target=_watch, daemon=True)
    thread.start()
    return thread


def run_analysis(
    request_path: str, response_path: str, *, catalog: Any, limit_bytes: int
) -> int:
    import json
    import traceback

    from google_meridian_mcp_server.domain.errors import (
        MeridianMcpError,
        ResponseTooLargeError,
    )
    from google_meridian_mcp_server.execution import analysis_ops

    with open(request_path) as f:
        req = json.load(f)

    rc = 0
    try:
        result = analysis_ops.run_operation(
            catalog, req["operation"], req["model_id"], req["params"]
        )
        payload: dict[str, Any] = {"ok": True, "result": result}
    except MeridianMcpError as err:
        payload = {"ok": False, "error": err.to_payload()}  # normal outcome -> rc 0
    except Exception as exc:  # noqa: BLE001 - worker boundary
        traceback.print_exc()  # child log only
        payload = {
            "ok": False,
            "error": {
                "error_code": "internal_error",
                "message": type(exc).__name__,
                "details": {},
            },
        }
        rc = 1

    tmp = response_path + ".tmp"
    try:
        payload = analysis_ops.sanitize_nan(
            payload
        )  # whole payload, incl. error details
        # GUARD 1 measures len(body) in characters; GUARD 2 (the executor
        # backstop) measures the written file's st_size in bytes. The two
        # only agree because ensure_ascii defaults to True here, so every
        # character in `body` is one ASCII byte. Do NOT pass
        # ensure_ascii=False: it would let non-ASCII geo/channel names
        # serialize as multi-byte UTF-8, making GUARD 1 under-count relative
        # to the actual byte size and admit an over-limit payload.
        body = json.dumps(payload, separators=(",", ":"), allow_nan=False)
        if payload.get("ok") and len(body) > limit_bytes:
            # GUARD 1. Replace, do NOT raise: a raise lands in the except
            # below and degrades to internal_error/rc 1. This is a normal,
            # user-correctable domain outcome, so it stays rc 0.
            result = payload["result"]
            cols = result.get("columns") if isinstance(result, dict) else None
            err = ResponseTooLargeError(
                nbytes=len(body),
                limit_bytes=limit_bytes,
                total_rows=result.get("row_count")
                if isinstance(result, dict)
                else None,
                total_columns=len(cols) if cols is not None else None,
            )
            payload = {"ok": False, "error": err.to_payload()}
            body = json.dumps(payload, separators=(",", ":"), allow_nan=False)
        with open(tmp, "w") as f:
            f.write(body)
    except Exception as exc:  # noqa: BLE001 - serialization must never leave "no response"
        # sanitize_nan/json.dump raised (e.g. an object type sanitize_nan
        # doesn't know about yet): fall back to a minimal, ALWAYS-serializable
        # payload so the caller sees a clean internal_error, not a misleading
        # "worker_failed: no response" (which would look like the worker never
        # ran at all, rather than that it ran and failed to report back).
        traceback.print_exc()  # child log only
        fallback = {
            "ok": False,
            "error": {
                "error_code": "internal_error",
                "message": type(exc).__name__,
                "details": {},
            },
        }
        with open(tmp, "w") as f:
            json.dump(fallback, f, allow_nan=False)
        rc = 1
    os.replace(tmp, response_path)
    return rc


def _headline(result: dict[str, Any]) -> str:
    summary = result.get("summary", {})
    mode = result.get("outcome_mode", "revenue")
    label = "ROAS" if mode == "revenue" else "CPIK"
    non_opt = summary.get("non_optimized_efficiency")
    opt = summary.get("optimized_efficiency")
    budget = summary.get("optimized_budget")
    return f"{label} {non_opt} -> {opt} at budget {budget}"


def run_worker(
    run_id: str,
    *,
    registry: OptimizationRunRegistry,
    catalog: Any,
    heartbeat_interval: float = 8.0,
) -> int:
    # The backend is no longer a parameter: main() pins MERIDIAN_BACKEND to the
    # module constant before any meridian import (D2).
    record = registry.get_record(run_id)
    started = _now()
    registry.write_state(
        OptimizationRunState(
            run_id=run_id,
            status=RunStatus.RUNNING,
            phase=RunPhase.LOADING_MODEL,
            progress_fraction=0.05,
            started_at=started,
            heartbeat_at=started,
        )
    )

    stop = threading.Event()
    # phase_box dict assignments are GIL-safe (immutable values), no lock needed.
    phase_box = {"phase": RunPhase.LOADING_MODEL, "progress": 0.05}

    def _beat() -> None:
        while not stop.wait(heartbeat_interval):
            registry.write_state(
                OptimizationRunState(
                    run_id=run_id,
                    status=RunStatus.RUNNING,
                    phase=phase_box["phase"],
                    progress_fraction=phase_box["progress"],
                    started_at=started,
                    heartbeat_at=_now(),
                )
            )

    beat = threading.Thread(target=_beat, daemon=True)
    beat.start()
    try:
        facade = catalog.get_optimizer_facade(record.model_id)
        phase_box["phase"] = RunPhase.OPTIMIZING
        phase_box["progress"] = 0.3
        registry.write_state(
            OptimizationRunState(
                run_id=run_id,
                status=RunStatus.RUNNING,
                phase=RunPhase.OPTIMIZING,
                progress_fraction=0.3,
                started_at=started,
                heartbeat_at=_now(),
            )
        )
        result = facade.execute(record.config)
        phase_box["phase"] = RunPhase.UPLOADING
        phase_box["progress"] = 0.95
        registry.write_result(run_id, result)
        # Stop heartbeat before writing terminal state to prevent a post-terminal
        # heartbeat. F8: join FULLY (no timeout) -- with a GCS registry a
        # write_state(RUNNING) already in flight can take longer than a short
        # timeout; giving up early let the terminal write land first and the
        # stale in-flight RUNNING write land AFTER it, showing RUNNING forever.
        # The beat loop exits promptly once `stop` is set (its only wait is
        # `stop.wait(heartbeat_interval)`, which returns immediately once set),
        # so at most one in-flight write is ever outstanding -- the join is
        # bounded by that single write's duration, not by heartbeat_interval.
        stop.set()
        beat.join()
        registry.write_state(
            OptimizationRunState(
                run_id=run_id,
                status=RunStatus.COMPLETED,
                progress_fraction=1.0,
                started_at=started,
                finished_at=_now(),
                headline=_headline(result),
            )
        )
        return 0
    except Exception as exc:  # noqa: BLE001 - worker boundary: record then exit non-zero
        # Stop heartbeat before writing terminal state to prevent a post-terminal
        # heartbeat. F8: full join (see the success-path comment above).
        stop.set()
        beat.join()
        registry.write_state(
            OptimizationRunState(
                run_id=run_id,
                status=RunStatus.FAILED,
                started_at=started,
                finished_at=_now(),
                error={
                    "code": "optimization_failed",
                    "message": str(exc),
                    "traceback": traceback.format_exc(),
                },
            )
        )
        return 1
    finally:
        # Idempotent: a second stop.set()/beat.join() is harmless (join() on an
        # already-finished thread returns immediately); ensures cleanup even on
        # unexpected control flow. F8: full join here too, for the same reason.
        stop.set()
        beat.join()


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv
    if argv[1:2] == ["analysis"]:
        _start_parent_death_guard()
        os.environ["MERIDIAN_BACKEND"] = MERIDIAN_BACKEND  # before any meridian import
        os.environ["MERIDIAN_ENABLE_JAX_X64"] = "true"
        from google_meridian_mcp_server.config import load_config

        cfg = load_config()
        return run_analysis(
            argv[2],
            argv[3],
            catalog=build_worker_catalog(cfg),
            limit_bytes=cfg.analysis_max_response_bytes,
        )

    _start_parent_death_guard()
    run_id = os.environ["OPTIMIZATION_RUN_ID"]
    # Set before importing meridian (build_worker_catalog does).
    os.environ["MERIDIAN_BACKEND"] = MERIDIAN_BACKEND
    os.environ["MERIDIAN_ENABLE_JAX_X64"] = "true"

    from google_meridian_mcp_server.bootstrap import build_registry
    from google_meridian_mcp_server.config import load_config

    cfg = load_config()
    return run_worker(
        run_id,
        registry=build_registry(cfg),
        catalog=build_worker_catalog(cfg),
    )


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

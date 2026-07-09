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
    discovery = DiscoveryCache(provider, cfg.discovery_ttl_seconds)
    materialization = MaterializationCache(provider, cfg.model_cache_root)
    return ModelCatalog(discovery, materialization)


def _start_parent_death_guard(poll_interval: float = 2.0) -> threading.Thread:
    """Exit immediately if our parent process dies (reparented to init/PID 1).

    Guards against orphaned worker subprocesses lingering after the parent
    server process crashes or is killed without a chance to clean up children.
    """

    def _watch() -> None:
        while True:
            threading.Event().wait(poll_interval)
            if os.getppid() == 1:
                os._exit(0)

    thread = threading.Thread(target=_watch, daemon=True)
    thread.start()
    return thread


def run_analysis(request_path: str, response_path: str, *, catalog: Any) -> int:
    import json
    import traceback

    from google_meridian_mcp_server.domain.errors import MeridianMcpError
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

    payload = analysis_ops.sanitize_nan(payload)  # whole payload, incl. error details
    tmp = response_path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f, allow_nan=False)
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
    backend: str,
    heartbeat_interval: float = 8.0,
) -> int:
    # NOTE: `backend` is applied via MERIDIAN_BACKEND before the meridian import
    # in main(); kept in the signature for provenance/symmetry.
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
        # Stop heartbeat before writing terminal state to prevent a post-terminal heartbeat.
        stop.set()
        beat.join(timeout=1.0)
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
        # Stop heartbeat before writing terminal state to prevent a post-terminal heartbeat.
        stop.set()
        beat.join(timeout=1.0)
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
        # Idempotent: a second stop.set()/beat.join() is harmless; ensures cleanup
        # even on unexpected control flow.
        stop.set()
        beat.join(timeout=1.0)


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv
    if argv[1:2] == ["analysis"]:
        _start_parent_death_guard()
        os.environ.setdefault(
            "MERIDIAN_BACKEND", "tensorflow"
        )  # not self-referential
        from google_meridian_mcp_server.config import load_config

        return run_analysis(
            argv[2], argv[3], catalog=build_worker_catalog(load_config())
        )

    _start_parent_death_guard()
    run_id = os.environ["OPTIMIZATION_RUN_ID"]
    backend = os.environ.get("MERIDIAN_BACKEND", "tensorflow")
    os.environ["MERIDIAN_BACKEND"] = (
        backend  # set before importing meridian (catalog does)
    )

    from google_meridian_mcp_server.bootstrap import build_registry
    from google_meridian_mcp_server.config import load_config

    cfg = load_config()
    return run_worker(
        run_id,
        registry=build_registry(cfg),
        catalog=build_worker_catalog(cfg),
        backend=backend,
    )


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

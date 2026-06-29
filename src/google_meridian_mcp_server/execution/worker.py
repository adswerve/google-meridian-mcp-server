"""Shared optimization worker: runs one optimization and writes it to the registry."""

from __future__ import annotations

import os
import sys
import traceback
from datetime import datetime, timezone
from typing import Any

from google_meridian_mcp_server.domain.optimization import (
    OptimizationRunState,
    RunPhase,
    RunStatus,
)
from google_meridian_mcp_server.persistence.optimization_run_registry import (
    OptimizationRunRegistry,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _headline(result: dict[str, Any]) -> str:
    summary = result.get("summary", {})
    mode = result.get("outcome_mode", "revenue")
    label = "ROAS" if mode == "revenue" else "CPIK"
    non_opt = summary.get("non_optimized_efficiency")
    opt = summary.get("optimized_efficiency")
    budget = summary.get("optimized_budget")
    return f"{label} {non_opt} -> {opt} at budget {budget}"


def run_worker(
    run_id: str, *, registry: OptimizationRunRegistry, catalog: Any, backend: str
) -> int:
    # NOTE: `backend` is intentionally unused here — it is applied via the
    # MERIDIAN_BACKEND env var before the catalog/meridian import in main().
    record = registry.get_record(run_id)
    started = _now()
    registry.write_state(
        OptimizationRunState(
            run_id=run_id,
            status=RunStatus.RUNNING,
            phase=RunPhase.LOADING_MODEL,
            started_at=started,
            heartbeat_at=started,
        )
    )
    try:
        facade = catalog.get_optimizer_facade(record.model_id)
        registry.write_state(
            OptimizationRunState(
                run_id=run_id,
                status=RunStatus.RUNNING,
                phase=RunPhase.OPTIMIZING,
                started_at=started,
                heartbeat_at=_now(),
            )
        )
        result = facade.run(record.config)
        registry.write_result(run_id, result)
        registry.write_state(
            OptimizationRunState(
                run_id=run_id,
                status=RunStatus.COMPLETED,
                started_at=started,
                finished_at=_now(),
                headline=_headline(result),
            )
        )
        return 0
    except Exception as exc:  # noqa: BLE001 - worker boundary: record then exit non-zero
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


def main(argv: list[str] | None = None) -> int:
    run_id = os.environ["OPTIMIZATION_RUN_ID"]
    backend = os.environ.get("MERIDIAN_BACKEND", "tensorflow")
    os.environ["MERIDIAN_BACKEND"] = (
        backend  # set before importing meridian (catalog does)
    )

    from google_meridian_mcp_server.bootstrap import build_model_catalog, build_registry
    from google_meridian_mcp_server.config import load_config

    cfg = load_config()
    return run_worker(
        run_id,
        registry=build_registry(cfg),
        catalog=build_model_catalog(cfg),
        backend=backend,
    )


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

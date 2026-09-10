"""Opt-in real Cloud Run smoke test against a deployed Cloud Run stack.

Run:
  CLOUD_SMOKE=1 CLOUD_RUN_PROJECT=your-gcp-project CLOUD_RUN_REGION=us-central1 \\
  CLOUD_RUN_JOB_CPU=meridian-opt-cpu GCS_BUCKET=<bucket> GCS_MODELS_PREFIX=<prefix> \\
  PERSISTENCE_BACKEND=gcs OPTIMIZATION_TIER=cloud_cpu \\
  MODEL_ID=<model_id> \\
  uv run python -m scripts.validation.cloud_smoke

To smoke-test cloud_gpu instead:
  COMPUTE_TIER=cloud_gpu CLOUD_RUN_JOB_GPU=meridian-opt-gpu OPTIMIZATION_TIER=cloud_gpu \\
  ... (same other vars) ...
  uv run python -m scripts.validation.cloud_smoke
"""

from __future__ import annotations

import asyncio
import os
import sys
import time


class _InProcessCatalogRunner:
    """Adapts a full in-process ModelCatalog to the runner.run(...) interface
    OptimizationService now expects (Task 11), bypassing the subprocess
    boundary -- this script already runs with full Meridian access
    in-process, unlike the real server."""

    def __init__(self, catalog):
        self._catalog = catalog

    async def run(self, operation, model_id, params):
        from google_meridian_mcp_server.execution import analysis_ops

        return analysis_ops.run_operation(self._catalog, operation, model_id, params)


def main() -> int:
    if os.getenv("CLOUD_SMOKE") != "1":
        print(
            "SKIP: set CLOUD_SMOKE=1 (and ADC creds) to run the real Cloud Run smoke test"
        )
        return 0

    from google_meridian_mcp_server.bootstrap import build_executor, build_registry
    from google_meridian_mcp_server.config import load_config
    from google_meridian_mcp_server.execution.worker import build_worker_catalog
    from google_meridian_mcp_server.services.optimization_service import (
        OptimizationService,
    )

    compute_tier = os.getenv("COMPUTE_TIER", "cloud_cpu")
    model_id = os.getenv("MODEL_ID") or sys.exit("ERROR: MODEL_ID env var is required")
    timeout = float(os.getenv("OPTIMIZATION_SMOKE_TIMEOUT", "1800"))
    # Same model+config fingerprints to one run regardless of tier, so a repeat
    # smoke (e.g. cloud_gpu after cloud_cpu) would otherwise reuse the prior run.
    force_rerun = os.getenv("FORCE_RERUN") == "1"

    cfg = load_config()
    registry = build_registry(cfg)
    executor = build_executor(cfg, registry)
    catalog = build_worker_catalog(cfg)
    runner = _InProcessCatalogRunner(catalog)
    service = OptimizationService(
        runner=runner, registry=registry, executor=executor, cfg=cfg
    )

    config = {
        "scenario": {"type": "fixed_budget"},
        "constraint": {"mode": "global", "pct": 0.2},
    }
    submit = asyncio.run(
        service.run_optimization(
            model_id, config, compute_tier=compute_tier, force_rerun=force_rerun
        )
    )
    run_id = submit["run_id"]
    assert submit["compute_tier_resolved"] == compute_tier, (
        f"tier mismatch: expected {compute_tier!r}, got {submit!r}"
    )
    print(f"submitted {run_id} -> Cloud Run ({compute_tier}); polling...")

    deadline = time.time() + timeout
    status_dict: dict | None = None
    terminal = {"completed", "failed", "canceled"}

    while time.time() < deadline:
        status_dict = service.get_status(run_id)
        elapsed = status_dict.get("elapsed_seconds")
        elapsed_str = f" elapsed={elapsed:.1f}s" if elapsed is not None else ""
        print(
            f"  status={status_dict['status']} phase={status_dict['phase']}{elapsed_str}"
        )
        if status_dict["status"] in terminal:
            break
        time.sleep(15)

    if status_dict is None or status_dict["status"] not in terminal:
        print(f"TIMEOUT: run {run_id} did not complete within {timeout}s")
        return 1

    if status_dict["status"] != "completed":
        error = status_dict.get("error") or "(no error detail)"
        print(
            f"FAILED: run {run_id} ended with status={status_dict['status']}: {error}"
        )
        return 1

    result = service.get_result(run_id)
    required_keys = (
        "summary",
        "channel_tables",
        "allocation",
        "spend_delta",
        "outcome_mode",
    )
    for key in required_keys:
        assert key in result, f"missing key {key!r} in result: {list(result)}"

    print(f"REAL CLOUD RUN SMOKE PASSED ({compute_tier})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

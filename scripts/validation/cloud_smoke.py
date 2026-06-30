"""Opt-in real Cloud Run smoke test against as-dev-anze.

Run:
  CLOUD_SMOKE=1 CLOUD_RUN_PROJECT=as-dev-anze CLOUD_RUN_REGION=us-central1 \\
  CLOUD_RUN_JOB_CPU=meridian-opt-cpu GCS_BUCKET=<bucket> GCS_MODELS_PREFIX=<prefix> \\
  PERSISTENCE_BACKEND=gcs REGISTRY_BACKEND=gcs OPTIMIZATION_ALLOWED_TIERS=cloud_cpu \\
  OPTIMIZATION_DEFAULT_TIER=cloud_cpu MODEL_ID=<model_id> \\
  uv run python -m scripts.validation.cloud_smoke

To smoke-test cloud_gpu instead:
  COMPUTE_TIER=cloud_gpu CLOUD_RUN_JOB_GPU=meridian-opt-gpu OPTIMIZATION_ALLOWED_TIERS=cloud_gpu \\
  ... (same other vars) ...
  uv run python -m scripts.validation.cloud_smoke
"""

from __future__ import annotations

import os
import sys
import time


def main() -> int:
    if os.getenv("CLOUD_SMOKE") != "1":
        print(
            "SKIP: set CLOUD_SMOKE=1 (and ADC creds) to run the real Cloud Run smoke test"
        )
        return 0

    from google_meridian_mcp_server.bootstrap import (
        build_executor,
        build_model_catalog,
        build_registry,
    )
    from google_meridian_mcp_server.config import load_config
    from google_meridian_mcp_server.services.optimization_service import (
        OptimizationService,
    )

    compute_tier = os.getenv("COMPUTE_TIER", "cloud_cpu")
    model_id = os.environ["MODEL_ID"]
    timeout = float(os.getenv("OPTIMIZATION_SMOKE_TIMEOUT", "1800"))

    cfg = load_config()
    registry = build_registry(cfg)
    executor = build_executor(cfg, registry)
    catalog = build_model_catalog(cfg)
    service = OptimizationService(
        catalog=catalog, registry=registry, executor=executor, cfg=cfg
    )

    config = {
        "scenario": {"type": "fixed_budget"},
        "constraint": {"mode": "global", "pct": 0.2},
    }
    submit = service.run_optimization(model_id, config, compute_tier=compute_tier)
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

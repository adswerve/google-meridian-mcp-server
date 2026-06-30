"""Live validation: build fixtures if missing, run the matrix, exit non-zero on failure.

Usage:
  uv run python -m scripts.validation.live_validate
  uv run python -m scripts.validation.live_validate --force   # rebuild fixtures
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from scripts.generate_validation_models import DEFAULT_OUT_ROOT, build_all
from scripts.validation.runner import assert_cloud_live_optimization, run_matrix


def _ensure_fixtures(force: bool) -> None:
    build_all(DEFAULT_OUT_ROOT, force=force)


def _build_cloud_service(*, backend: str, shared_dir):
    """Wire an OptimizationService backed by a CloudRunJobExecutor whose jobs.run
    is faked to launch the REAL worker locally.

    The server-side cfg must be gcs-valid (RuntimeConfig requires gcs registry +
    Cloud Run coords for any cloud tier), but we hand the executor/service a
    LocalOptimizationRunRegistry pointing at a shared dir. The worker subprocess
    loads its OWN local config (local backend + local registry on the same dir),
    so an in-memory fake never has to cross the process boundary.
    """
    from google_meridian_mcp_server.bootstrap import build_model_catalog
    from google_meridian_mcp_server.domain.models import RuntimeConfig
    from google_meridian_mcp_server.execution.cloud_run_executor import (
        CloudRunJobExecutor,
    )
    from google_meridian_mcp_server.persistence.optimization_run_registry import (
        LocalOptimizationRunRegistry,
    )
    from google_meridian_mcp_server.services.optimization_service import (
        OptimizationService,
    )
    from scripts.validation.cloud_fake import FakeExecutionsClient, FakeJobsClient

    worker_base_env = {
        "PERSISTENCE_BACKEND": "local",
        "LOCAL_MODELS_ROOT": str(DEFAULT_OUT_ROOT),
        "REGISTRY_BACKEND": "local",
        "OPTIMIZATION_RUNS_ROOT": str(shared_dir),
        "RESULT_CACHE_ENABLED": "false",
        "MODEL_CACHE_ROOT": "/tmp/mmm-models-cloudgate",
    }
    cloud_cfg = RuntimeConfig(
        persistence_backend="gcs",
        gcs_bucket="fake",
        gcs_models_prefix="m/",
        registry_backend="gcs",
        optimization_allowed_tiers=("cloud_cpu",),
        optimization_backend_cloud_cpu=backend,
        cloud_run_project="fake",
        cloud_run_region="fake",
        cloud_run_job_cpu="opt-cpu",
        local_models_root=str(DEFAULT_OUT_ROOT),
    )
    # Catalog reads fixtures from the local dir; cloud_cfg's gcs provider would not.
    local_cfg = RuntimeConfig(
        persistence_backend="local",
        local_models_root=str(DEFAULT_OUT_ROOT),
        model_cache_root="/tmp/mmm-models-cloudgate",
        result_cache_enabled=False,
    )
    catalog = build_model_catalog(local_cfg)
    registry = LocalOptimizationRunRegistry(str(shared_dir))
    jobs = FakeJobsClient(base_env=worker_base_env)
    execs = FakeExecutionsClient(jobs)
    executor = CloudRunJobExecutor(
        registry,
        cfg=cloud_cfg,
        max_parallel=2,
        heartbeat_stale_seconds=60,
        jobs_client=jobs,
        executions_client=execs,
    )
    return OptimizationService(catalog, registry, executor, cloud_cfg)


def _run_cloud_gate() -> list[str]:
    """Run the local cloud-executor live gate + cross-backend JAX gate.

    Returns a list of failure strings (empty == all green/skipped).
    """
    failures: list[str] = []
    print("\n=== Cloud executor live gate (faked jobs.run, real worker) ===")
    print(
        "NOTE: cloud gate runs the worker against a local-dir registry "
        "(in-memory FakeGcs can't cross the subprocess boundary); the real GCS "
        "blob path is covered by the opt-in Cloud Run smoke (Task 10)."
    )

    shared_dir = DEFAULT_OUT_ROOT / "_cloud_runs"
    shared_dir.mkdir(parents=True, exist_ok=True)
    tf_service = _build_cloud_service(backend="tensorflow", shared_dir=shared_dir)
    for model_id in ("national-revenue", "geo-revenue"):
        label = f"cloud/{model_id}/run_optimization[cloud_cpu,tensorflow]"
        try:
            assert_cloud_live_optimization(tf_service, model_id)
            print(f"  PASS {label}")
        except AssertionError as exc:
            failures.append(f"{label}: {exc}")
            print(f"  FAIL {label}: {exc}")

    # Cross-backend gate: a TF-fit model must optimize under JAX. Skip if jax
    # is not importable in this environment.
    jax_label = "cloud/national-revenue/run_optimization[cloud_cpu,jax]"
    try:
        import jax  # noqa: F401
    except Exception:
        print("  SKIP: jax not installed (cross-backend gate)")
    else:
        jax_dir = DEFAULT_OUT_ROOT / "_cloud_runs_jax"
        jax_dir.mkdir(parents=True, exist_ok=True)
        jax_service = _build_cloud_service(backend="jax", shared_dir=jax_dir)
        try:
            assert_cloud_live_optimization(jax_service, "national-revenue")
            print(f"  PASS {jax_label}")
        except AssertionError as exc:
            failures.append(f"{jax_label}: {exc}")
            print(f"  FAIL {jax_label}: {exc}")

    return failures


async def _run() -> int:
    os.environ["PERSISTENCE_BACKEND"] = "local"
    os.environ["LOCAL_MODELS_ROOT"] = str(DEFAULT_OUT_ROOT)
    os.environ.setdefault("RESULT_CACHE_ENABLED", "false")
    os.environ.setdefault("OPTIMIZATION_RUNS_ROOT", str(DEFAULT_OUT_ROOT / "_runs"))

    from fastmcp import Client

    from google_meridian_mcp_server.server import mcp

    async with Client(mcp) as client:
        report = await run_matrix(client)

    cloud_failures = _run_cloud_gate()

    print(
        f"\n{len(report.passed)} passed, "
        f"{len(report.failed) + len(cloud_failures)} failed"
    )
    failures = report.failed + cloud_failures
    if failures:
        print("FAILURES:")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("LIVE VALIDATION PASSED")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="Rebuild fixtures first")
    args = parser.parse_args()
    if not (DEFAULT_OUT_ROOT.exists() and any(DEFAULT_OUT_ROOT.iterdir())) or args.force:
        _ensure_fixtures(args.force)
    sys.exit(asyncio.run(_run()))


if __name__ == "__main__":
    main()

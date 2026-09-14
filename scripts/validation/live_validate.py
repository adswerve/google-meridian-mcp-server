"""Live validation: build fixtures if missing, run the matrix, exit non-zero on failure.

Usage:
  uv run python -m scripts.validation.live_validate
  uv run python -m scripts.validation.live_validate --force   # rebuild fixtures
"""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import sys

from scripts.generate_validation_models import DEFAULT_OUT_ROOT, build_all
from scripts.validation.runner import assert_cloud_live_optimization, run_matrix


def _ensure_fixtures(force: bool) -> None:
    build_all(DEFAULT_OUT_ROOT, force=force)


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


def _build_cloud_service(*, shared_dir, max_parallel: int = 2):
    """Wire an OptimizationService backed by a CloudRunJobExecutor whose jobs.run
    is faked to launch the REAL worker locally.

    The server-side cfg must be gcs-valid (RuntimeConfig requires gcs registry +
    Cloud Run coords for any cloud tier), but we hand the executor/service a
    LocalOptimizationRunRegistry pointing at a shared dir. The worker subprocess
    loads its OWN local config (local backend + local registry on the same dir),
    so an in-memory fake never has to cross the process boundary.

    Returns (service, registry, jobs, execs, cloud_cfg): the restart step
    needs the registry, the fakes, and cloud_cfg to rebuild a second
    CloudRunJobExecutor over the same in-flight state.
    """
    from google_meridian_mcp_server.domain.models import RuntimeConfig
    from google_meridian_mcp_server.execution.cloud_run_executor import (
        CloudRunJobExecutor,
    )
    from google_meridian_mcp_server.execution.worker import build_worker_catalog
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
        "OPTIMIZATION_RUNS_ROOT": str(shared_dir),
        "RESULT_CACHE_ENABLED": "false",
        "MODEL_CACHE_ROOT": "/tmp/mmm-models-cloudgate",
    }
    cloud_cfg = RuntimeConfig(
        persistence_backend="gcs",
        gcs_bucket="fake",
        gcs_models_prefix="m/",
        optimization_tier="cloud_cpu",
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
    catalog = build_worker_catalog(local_cfg)
    runner = _InProcessCatalogRunner(catalog)
    registry = LocalOptimizationRunRegistry(str(shared_dir))
    jobs = FakeJobsClient(base_env=worker_base_env)
    execs = FakeExecutionsClient(jobs)
    executor = CloudRunJobExecutor(
        registry,
        cfg=cloud_cfg,
        max_parallel=max_parallel,
        heartbeat_stale_seconds=60,
        jobs_client=jobs,
        executions_client=execs,
    )
    service = OptimizationService(runner, registry, executor, cloud_cfg)
    return service, registry, jobs, execs, cloud_cfg


async def _assert_cloud_restart_recovery() -> None:
    """The actual defect, exercised for free on every machine.

    Submits a run (dispatches, filling the only slot) and a second run over
    the cap (queues), waits for the first to reach RUNNING, then simulates a
    server restart: discards the executor and rebuilds a fresh
    CloudRunJobExecutor over the SAME registry and the SAME fake jobs/executions
    clients, and calls reconcile_orphans(). Confirms the in-flight run is
    adopted rather than relaunched (FakeJobsClient's launch count is
    unchanged) and that the queued run does NOT dispatch while the adopted
    run occupies the only slot -- only once it finishes and a later pump()
    runs does the queued run get its turn.
    """
    import time

    from google_meridian_mcp_server.execution.cloud_run_executor import (
        CloudRunJobExecutor,
    )

    model_id = "national-revenue"
    shared_dir = DEFAULT_OUT_ROOT / "_cloud_restart"
    shutil.rmtree(shared_dir, ignore_errors=True)
    shared_dir.mkdir(parents=True, exist_ok=True)

    service, registry, jobs, execs, cloud_cfg = _build_cloud_service(
        shared_dir=shared_dir, max_parallel=1
    )

    config_a = {
        "scenario": {"type": "fixed_budget"},
        "constraint": {"mode": "global", "pct": 0.2},
    }
    config_b = {
        "scenario": {"type": "fixed_budget"},
        "constraint": {"mode": "global", "pct": 0.35},
    }
    dispatched = await service.run_optimization(
        model_id, config_a, compute_tier="cloud_cpu"
    )
    dispatched_id = dispatched["run_id"]
    waiting = await service.run_optimization(
        model_id, config_b, compute_tier="cloud_cpu"
    )
    waiting_id = waiting["run_id"]
    assert waiting_id != dispatched_id, f"expected two distinct runs: {waiting}"

    status = None
    for _ in range(240):  # ~120s cap
        status = service.get_status(dispatched_id)
        if status["status"] in ("running", "completed", "failed"):
            break
        time.sleep(0.5)
    assert status and status["status"] == "running", (
        f"expected the dispatched run to reach RUNNING before the restart: {status}"
    )
    assert service.get_status(waiting_id)["status"] == "queued", (
        f"expected the second run to queue over the cap: {service.get_status(waiting_id)}"
    )

    launches_before = len(jobs.procs)

    # The restart: a fresh executor over the same registry and fake clients.
    new_executor = CloudRunJobExecutor(
        registry,
        cfg=cloud_cfg,
        max_parallel=1,
        heartbeat_stale_seconds=60,
        jobs_client=jobs,
        executions_client=execs,
    )
    new_executor.reconcile_orphans()

    assert len(jobs.procs) == launches_before, (
        "adoption must not relaunch the in-flight execution"
    )
    assert dispatched_id in new_executor._handles, (
        "the in-flight run must be adopted by the rebuilt executor"
    )
    assert service.get_status(waiting_id)["status"] == "queued", (
        "the cap is still full with the adopted run: the queued run must not "
        "dispatch at reconcile time"
    )

    # Let the adopted run finish; a later pump() must free the slot and
    # dispatch the run that was waiting.
    status = None
    for _ in range(240):
        status = service.get_status(dispatched_id)
        if status["status"] in ("completed", "failed"):
            break
        new_executor.pump()
        time.sleep(0.5)
    assert status and status["status"] == "completed", (
        f"adopted run did not complete: {status}"
    )

    status = None
    for _ in range(240):
        status = service.get_status(waiting_id)
        if status["status"] in ("running", "completed", "failed"):
            break
        time.sleep(0.5)
    assert status and status["status"] in ("running", "completed"), (
        f"queued run never dispatched once the adopted run freed its slot: {status}"
    )


async def _run_cloud_gate() -> list[str]:
    """Run the local cloud-executor live gate.

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
    shutil.rmtree(shared_dir, ignore_errors=True)
    shared_dir.mkdir(parents=True, exist_ok=True)
    service, _registry, _jobs, _execs, _cfg = _build_cloud_service(
        shared_dir=shared_dir
    )
    checks = 0
    for model_id in ("national-revenue", "geo-revenue"):
        label = f"cloud/{model_id}/run_optimization[cloud_cpu]"
        checks += 1
        try:
            await assert_cloud_live_optimization(service, model_id)
            print(f"  PASS {label}")
        except AssertionError as exc:
            failures.append(f"{label}: {exc}")
            print(f"  FAIL {label}: {exc}")

    # The cross-backend JAX gate that used to live here (a TF-fit model must
    # optimize under JAX) was deleted with the backend knob: with one backend
    # there is nothing to cross. This is a REAL coverage loss and is recorded
    # in AGENTS.md rather than quietly dropped.

    # The actual defect (Task 7): a restart must adopt an in-flight run rather
    # than strand it, and must not relaunch it. This is the only restart
    # coverage that runs on every machine for free -- the opt-in Cloud Run
    # smoke (Task 10) costs real money and needs a deployed stack.
    restart_label = "cloud/restart-recovery"
    checks += 1
    try:
        await _assert_cloud_restart_recovery()
        print(f"  PASS {restart_label}")
    except AssertionError as exc:
        failures.append(f"{restart_label}: {exc}")
        print(f"  FAIL {restart_label}: {exc}")

    print(f"  cloud gate: {checks - len(failures)}/{checks} checks passed")
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

    cloud_failures = await _run_cloud_gate()

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
    if (
        not (DEFAULT_OUT_ROOT.exists() and any(DEFAULT_OUT_ROOT.iterdir()))
        or args.force
    ):
        _ensure_fixtures(args.force)
    sys.exit(asyncio.run(_run()))


if __name__ == "__main__":
    main()

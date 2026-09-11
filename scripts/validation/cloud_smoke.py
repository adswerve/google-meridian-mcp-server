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

QUEUE_SMOKE=1 is a SEPARATE, more expensive, opt-in mode: the durable-queue /
restart / cancel acceptance gate against a real deployed stack (Task 10 of the
2026-09-10 optimization-durability-and-encoding plan). It costs real money --
13 real optimizer executions (3 each for Phases 0, 1, 2, 2b, plus 1 cancelled
in Phase 3), roughly DOUBLE the 7 executions a naive reading of the plan's
budget note suggests, because Phases 0, 1, 2, and 2b are each independent
scenarios with their own fresh triplet of submissions rather than one triplet
shared across all of them. That is deliberate: sharing would couple Phase 1's
completion timing to Phase 2's viability, and Phase 2 voids itself if both
runs finish before the restart -- so a slow Phase 1 would silently void Phase
2 and the operator would pay for a run proving nothing. Independent phases
cost more but are honest about what each one actually measured. Budget
roughly 4-6 hours wall-clock (up to 2 executions run concurrently at a time)
and set OPTIMIZATION_SMOKE_TIMEOUT accordingly. It also MUTATES a shared
Cloud Run service's --max-instances, so it is never run unless QUEUE_SMOKE=1
is set explicitly -- CLOUD_SMOKE=1 alone does not trigger it, and neither
flag implies the other. See the docstrings on _queue_smoke_main and each
_phaseN function below for what each phase proves and which commit it
exercises. Run:

  QUEUE_SMOKE=1 CLOUD_RUN_PROJECT=your-gcp-project CLOUD_RUN_REGION=us-central1 \\
  CLOUD_RUN_SERVICE=meridian-mcp-server CLOUD_RUN_JOB_CPU=meridian-opt-cpu \\
  GCS_BUCKET=<bucket> GCS_MODELS_PREFIX=<prefix> PERSISTENCE_BACKEND=gcs \\
  OPTIMIZATION_TIER=cloud_cpu OPTIMIZATION_MAX_PARALLEL=2 MODEL_ID=national-revenue \\
  OPTIMIZATION_SMOKE_TIMEOUT=1800 \\
  uv run python -m scripts.validation.cloud_smoke

Prerequisites (the script checks and refuses to proceed if any is missing,
rather than running and producing a misleading pass -- see Step 1 of the
task brief):
  - CLOUD_RUN_SERVICE pinned to --max-instances=1 for the duration. With
    max_instance_count=2 and no session affinity, three submits can spread
    across two instances, each admitting two, and NOTHING QUEUES AT ALL.
  - ADC credentials (`gcloud auth application-default login`) for a principal
    with roles/run.developer and bucket read/write.
  - A `.binpb` model already uploaded to gs://$GCS_BUCKET/$GCS_MODELS_PREFIX/.
  - MCP_AUTH_TOKEN set to an identity token (`gcloud auth print-identity-token`)
    -- this project's org policy refused the allUsers invoker binding, so the
    deployed service requires one even with allow_unauthenticated=true. The
    token itself lives only an hour, far short of this gate; build_client
    re-mints it via MCP_AUTH_TOKEN_REFRESH_CMD (default `gcloud auth
    print-identity-token`) before it expires, so that command must keep
    working for the whole run.
  - No pre-existing QUEUED or RUNNING runs in the target bucket --
    reconcile_orphans() scans the whole bucket prefix, so leftovers from a
    prior (or still-broken) deployment would be re-enqueued and launched too.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Any


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
    if os.getenv("QUEUE_SMOKE") == "1":
        return _queue_smoke_main()

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


# ---------------------------------------------------------------------------
# QUEUE_SMOKE=1: durable queue / restart / cancel acceptance on Cloud Run.
#
# Every mechanism below is verified in-process with fakes elsewhere in this
# repo. The defect this whole plan fixes is specifically about state
# surviving a process boundary -- exactly what a fake cannot prove. This mode
# drives a real deployed stack to prove it. Nothing here runs unless
# QUEUE_SMOKE=1 is set.
# ---------------------------------------------------------------------------

_QUEUE_POLL_INTERVAL = 10.0


def _queue_phase_timeout() -> float:
    return float(os.getenv("OPTIMIZATION_SMOKE_TIMEOUT", "1800"))


def _executions_for_job(cfg, job_name: str) -> set[str]:
    """Live execution names, so assertions cannot be satisfied by our own
    bookkeeping. Cloud Run retains prior executions, so every phase diffs
    against a baseline snapshot taken before it runs -- never counts all
    executions."""
    from google.cloud import run_v2

    client = run_v2.ExecutionsClient()
    parent = (
        f"projects/{cfg.cloud_run_project}/locations/{cfg.cloud_run_region}"
        f"/jobs/{job_name}"
    )
    return {e.name for e in client.list_executions(parent=parent)}


def _live_executions_for_job(cfg, job_name: str) -> set[str]:
    """Names of executions that are ACTUALLY still running right now, unlike
    _executions_for_job above (which is deliberately cumulative -- see its
    docstring -- and correct for the assertions that diff it against a
    baseline). Cloud Run retains completed executions forever, so a bare
    membership count only grows and can never be read as a live concurrency
    level. This is for Phase 1's max_parallel invariant only, which polls
    until all three runs are terminal: by the time the third dispatches, the
    cumulative set already contains all three names even though at most two
    are ever concurrently live, and treating that as a live count produces a
    false "max_parallel breached" failure."""
    from google.cloud import run_v2

    client = run_v2.ExecutionsClient()
    parent = (
        f"projects/{cfg.cloud_run_project}/locations/{cfg.cloud_run_region}"
        f"/jobs/{job_name}"
    )
    return {
        e.name
        for e in client.list_executions(parent=parent)
        if not getattr(e, "completion_time", None)
    }


def _get_service(cfg, service_name: str):
    from google.cloud import run_v2

    client = run_v2.ServicesClient()
    name = (
        f"projects/{cfg.cloud_run_project}/locations/{cfg.cloud_run_region}"
        f"/services/{service_name}"
    )
    return client.get_service(name=name)


def _read_max_instances(cfg, service_name: str) -> int:
    return _get_service(cfg, service_name).template.scaling.max_instance_count


def _check_adc() -> None:
    import google.auth
    from google.auth.exceptions import DefaultCredentialsError

    try:
        google.auth.default()
    except DefaultCredentialsError as exc:
        sys.exit(
            "PREREQUISITE MISSING: no Application Default Credentials found.\n"
            "Run `gcloud auth application-default login` for a principal with "
            "roles/run.developer (run.jobs.run, run.executions.{get,list,cancel}) "
            f"and read/write on the model bucket, then re-run. ({exc})"
        )


def _check_mcp_auth_token() -> None:
    """Phases 0 and 2b go through build_client("http", ...), which sends an
    Authorization header only when MCP_AUTH_TOKEN is set. A domain-restricted
    -sharing org policy on this project refused the allUsers invoker binding
    (see capture_baseline.py's build_client), so the deployed service
    requires an identity token even though Terraform sets
    allow_unauthenticated=true. Failing here, before any submit, is cheap;
    failing inside Phase 0 after the queue is already filled is not."""
    if not os.environ.get("MCP_AUTH_TOKEN"):
        sys.exit(
            "PREREQUISITE MISSING: MCP_AUTH_TOKEN is not set. The deployed "
            "service requires an identity token (an org policy on this "
            "project refused the allUsers invoker binding, so "
            "allow_unauthenticated=true alone is not enough). Set "
            "MCP_AUTH_TOKEN=$(gcloud auth print-identity-token) and re-run."
        )


def _check_no_preexisting_queue_activity(cfg) -> None:
    """CloudRunJobExecutor.reconcile_orphans scans the WHOLE bucket prefix,
    not just this run's ids. Phase 2 calls it against the operator's real
    bucket, and any pre-existing QUEUED or RUNNING run there gets re-enqueued
    and (once a slot is free) actually launched -- unbudgeted GPU spend, and
    a bucket that has been running the broken pre-fix code is *guaranteed* to
    have stranded QUEUED runs, since that is the exact defect this branch
    fixes. It also breaks Phase 2's `== {d1_name, d2_name}` and Phase 2b's
    `len(all_new) == 3` assertions for a reason that reads as a code failure
    rather than a dirty bucket."""
    from google_meridian_mcp_server.bootstrap import build_registry
    from google_meridian_mcp_server.domain.optimization import RunStatus

    registry = build_registry(cfg)
    queued = registry.list(status=RunStatus.QUEUED)
    running = registry.list(status=RunStatus.RUNNING)
    if queued or running:
        found = ", ".join(f"{s.run_id} ({s.status.value})" for s in (*queued, *running))
        sys.exit(
            "PREREQUISITE MISSING: this bucket already has QUEUED or RUNNING "
            f"optimization runs: {found}. reconcile_orphans() scans the whole "
            "bucket prefix, not just this gate's own runs, so Phase 2's "
            "restart would re-enqueue and launch these too -- unbudgeted "
            "spend, and a false failure that reads as a code bug. Resolve or "
            "cancel them (or point this gate at a clean bucket/prefix) "
            "before running QUEUE_SMOKE."
        )
    print("prereq OK: no pre-existing QUEUED or RUNNING runs in this bucket")


def _check_model_in_bucket(cfg, model_id: str) -> None:
    from google.cloud import storage

    prefix = f"{cfg.gcs_models_prefix.rstrip('/')}/{model_id}/"
    client = storage.Client()
    blobs = list(client.list_blobs(cfg.gcs_bucket, prefix=prefix))
    binpb = [b for b in blobs if b.name.endswith(".binpb")]
    if not binpb:
        sys.exit(
            f"PREREQUISITE MISSING: no .binpb model at "
            f"gs://{cfg.gcs_bucket}/{prefix} .pkl is rejected. Build one with "
            "`uv run python scripts/generate_validation_models.py`, then upload "
            f"it: `gsutil -m cp -r models/_validation/{model_id} "
            f"gs://{cfg.gcs_bucket}/{cfg.gcs_models_prefix}/` and set "
            f"MODEL_ID={model_id}."
        )
    print(f"prereq OK: {model_id} has a .binpb model at gs://{cfg.gcs_bucket}/{prefix}")


def _get_service_or_exit(cfg, service_name: str):
    try:
        return _get_service(cfg, service_name)
    except Exception as exc:  # noqa: BLE001 - surfaced as an actionable prerequisite failure
        sys.exit(
            f"PREREQUISITE MISSING: could not read Cloud Run service "
            f"{service_name!r} in project={cfg.cloud_run_project} "
            f"region={cfg.cloud_run_region}: {exc}. Check CLOUD_RUN_SERVICE / "
            "CLOUD_RUN_PROJECT / CLOUD_RUN_REGION and ADC."
        )


def _check_pinned_single_instance(cfg, service_name: str, service) -> int:
    max_instances = service.template.scaling.max_instance_count
    if max_instances != 1:
        sys.exit(
            f"PREREQUISITE MISSING: {service_name} has max_instance_count="
            f"{max_instances}, not 1. With 2+ instances and no session "
            "affinity, three submits can spread across two instances, each "
            "admitting two, and NOTHING QUEUES AT ALL -- this gate would fail "
            "for a configuration reason and be misread as a code failure.\n"
            f"Pin it first: `gcloud run services update {service_name} "
            f"--max-instances=1 --project {cfg.cloud_run_project} --region "
            f"{cfg.cloud_run_region}` (or the terraform max_instance_count "
            f"var), run this gate, then restore it to {max_instances} "
            "afterwards -- record both values in the evidence."
        )
    print(f"prereq OK: {service_name} max_instance_count=1 (pinned)")
    return max_instances


def _force_new_revision(cfg, service_name: str, *, tag: str) -> None:
    """Force an ACTUAL new Cloud Run revision and VERIFY it, rather than
    assume it.

    Per Cloud Run's revision model, a service-level LABEL update alone is
    metadata-only and does not reliably create a new revision or replace the
    running container instance -- only a change to the revision TEMPLATE
    (image, env vars, resources, concurrency, ...) does. Using a label update
    here (the plan brief's literal Step 3 sketch) risks a gate that polls the
    *same still-running process* and never exercises the cross-process
    durability it exists to prove -- a PASS that measured nothing. So this
    bumps an env var (a template change) instead, and then reads the
    service's serving revision back to confirm it actually changed, failing
    loudly rather than discovering "it measured nothing" only after a live,
    paid run.
    """
    before = _get_service(cfg, service_name).latest_ready_revision
    value = str(int(time.time()))
    cmd = [
        "gcloud",
        "run",
        "services",
        "update",
        service_name,
        f"--update-env-vars=QUEUE_SMOKE_{tag}={value}",
        f"--project={cfg.cloud_run_project}",
        f"--region={cfg.cloud_run_region}",
        "--quiet",
    ]
    print(f"forcing a real revision swap (env-var/template change): {' '.join(cmd)}")
    subprocess.run(cmd, check=True)

    deadline = time.time() + 300
    after = before
    while time.time() < deadline:
        after = _get_service(cfg, service_name).latest_ready_revision
        if after and after != before:
            break
        time.sleep(5)
    if not after or after == before:
        raise AssertionError(
            f"revision did not change after the swap (still {before!r}) -- "
            "this phase would have measured the same still-running process, "
            "never the cross-process durability it exists to prove. Aborting "
            "rather than reporting a false PASS."
        )
    print(f"revision confirmed swapped: {before!r} -> {after!r}")


async def _phase0(cfg, service_url: str, service_name: str, model_id: str, job: str):
    """Phase 0: the queue on the deployed service, over HTTP, through a
    forced (and VERIFIED) revision swap. Answers the requirement literally:
    a live deployment, real Cloud Run Job runs, the queue filled (two
    running, one waiting), surviving an instance replacement.

    The swap goes through _force_new_revision's env-var/template change,
    not a service-level label update: a label update is metadata-only and
    does not reliably replace the running container, which would make this
    phase pass without ever exercising cross-process durability -- this
    deviates from the plan brief's literal Step 3 sketch deliberately,
    because the spec's requirement (prove durability across a real instance
    replacement) is the binding authority, not the sketch's exact command.

    Exercises 6b28b6e + eab662a (dispatch.json / atomic claim / recorded
    execution name) and 54ea6fd (startup reconciliation on the deployed
    service's own restart path). Also folds in the free check of 371a831 +
    fa06339: a non-ASCII label survives the round trip.

    Drives the deployed endpoint over HTTP (capture_baseline.py's
    --transport http --url pattern), never through the in-process executor.
    """
    from scripts.validation.capture_baseline import build_client
    from scripts.validation.payloads import extract

    baseline = _executions_for_job(cfg, job)
    non_ascii_label = "予算最適化 \U0001f3af"  # "budget optimization" + dart target

    async with build_client("http", service_url) as client:
        run_ids: list[str] = []
        for i, pct in enumerate((0.1, 0.2, 0.3)):
            args: dict[str, Any] = {
                "model_id": model_id,
                "config": {
                    "scenario": {"type": "fixed_budget"},
                    "constraint": {"mode": "global", "pct": pct},
                },
                "compute_tier": "cloud_cpu",
                "force_rerun": True,
            }
            if i == 0:
                args["label"] = non_ascii_label
            submit = extract(await client.call_tool("run_optimization", args))
            run_ids.append(submit["run_id"])
        print(f"phase0: submitted {run_ids}")

        deadline = time.time() + _queue_phase_timeout()
        statuses: dict[str, dict] = {}
        while True:
            statuses = {
                rid: extract(
                    await client.call_tool("get_optimization_status", {"run_id": rid})
                )
                for rid in run_ids
            }
            running = [r for r, s in statuses.items() if s.get("status") == "running"]
            queued = [r for r, s in statuses.items() if s.get("status") == "queued"]
            print(f"phase0: running={len(running)} queued={len(queued)}")
            if len(running) == 2 and len(queued) == 1:
                break
            if time.time() > deadline:
                raise AssertionError(
                    f"phase0: never reached 2 running / 1 queued; statuses={statuses}"
                )
            await asyncio.sleep(_QUEUE_POLL_INTERVAL)

        new_executions = _executions_for_job(cfg, job) - baseline
        assert len(new_executions) == 2, (
            f"phase0: expected exactly 2 new executions while 2 run and 1 "
            f"waits, got {new_executions}"
        )

        # Free check of Tasks 1/2: non-ASCII label round trip. get_status does
        # not echo `label` (optimization_service.get_status's fixed field
        # set), so the only live tool-surface check is list_optimizations.
        listing = extract(
            await client.call_tool("list_optimizations", {"model_id": model_id})
        )
        entry = next((r for r in listing["runs"] if r["run_id"] == run_ids[0]), None)
        assert entry is not None, (
            f"phase0: run {run_ids[0]} missing from list_optimizations: {listing}"
        )
        assert entry["label"] == non_ascii_label, (
            f"non-ASCII label did not round-trip byte-identical: "
            f"{entry['label']!r} != {non_ascii_label!r}"
        )
        print(
            f"phase0: non-ASCII label round-tripped byte-identical: {entry['label']!r}"
        )

        queued_run_id = queued[0]

    # The MCP streamable-http session lives in the serving container's memory,
    # so replacing the instance destroys it and every later call on this client
    # returns "Session not found" -- which is the POINT of the phase, not a
    # failure of it: the run's durability is in GCS, the session's is not.
    # Reconnect afterwards exactly as a real client would, or the gate reports
    # a session-lifetime artifact as a durability failure.
    _force_new_revision(cfg, service_name, tag="PHASE0_SWAP")

    async with build_client("http", service_url) as client:
        deadline = time.time() + _queue_phase_timeout()
        while True:
            status = extract(
                await client.call_tool(
                    "get_optimization_status", {"run_id": queued_run_id}
                )
            )
            print(f"phase0: post-swap {queued_run_id} status={status.get('status')}")
            state = status.get("status")
            if state == "completed":
                break
            if state in ("failed", "canceled"):
                raise AssertionError(f"phase0: queued run ended {state}: {status}")
            if time.time() > deadline:
                raise AssertionError(
                    f"phase0: {queued_run_id} never completed after the revision swap"
                )
            await asyncio.sleep(_QUEUE_POLL_INTERVAL)

    return {
        "submitted": 3,
        "running_at_cap": 2,
        "queued": 1,
        "new_executions": len(new_executions),
        "non_ascii_label_roundtrip": True,
        "survived_revision_swap": True,
    }


def _phase1(cfg, model_id: str, job: str) -> dict:
    """Phase 1: the cap holds and the third really waits (34e4a56, eab662a).

    Accumulates an invariant across the poll loop -- never more than two
    concurrent, and the third dispatched only once a slot freed -- rather
    than asserting a point in time, because get_status calls pump()
    (optimization_service.py's get_status) and a point-in-time read taken via
    it could dispatch the very run being measured. So this reads state
    through the registry only, and advances the executor with direct pump()
    calls instead of a get_status poll.
    """
    from google_meridian_mcp_server.bootstrap import build_executor, build_registry
    from google_meridian_mcp_server.execution.worker import build_worker_catalog
    from google_meridian_mcp_server.services.optimization_service import (
        OptimizationService,
    )

    registry = build_registry(cfg)
    executor = build_executor(cfg, registry)
    catalog = build_worker_catalog(cfg)
    service = OptimizationService(
        runner=_InProcessCatalogRunner(catalog),
        registry=registry,
        executor=executor,
        cfg=cfg,
    )

    baseline = _executions_for_job(cfg, job)
    run_ids = []
    for pct in (0.1, 0.2, 0.3):
        config = {
            "scenario": {"type": "fixed_budget"},
            "constraint": {"mode": "global", "pct": pct},
        }
        submit = asyncio.run(
            service.run_optimization(
                model_id, config, compute_tier="cloud_cpu", force_rerun=True
            )
        )
        run_ids.append(submit["run_id"])
    print(f"phase1: submitted {run_ids}")

    terminal = {"completed", "failed", "canceled"}
    peak = 0
    third_dispatched_while: dict[str, str] | None = None
    deadline = time.time() + _queue_phase_timeout()
    while True:
        states = {r: registry.get_state(r).status.value for r in run_ids}
        live = _live_executions_for_job(cfg, job) - baseline
        peak = max(peak, len(live))
        assert peak <= 2, f"max_parallel=2 breached: {len(live)} executions live"
        dispatch = registry.get_dispatch(run_ids[2])
        if dispatch and dispatch.execution_name and third_dispatched_while is None:
            third_dispatched_while = states.copy()
        print(f"phase1: states={states} live={len(live)} peak={peak}")
        if all(s in terminal for s in states.values()):
            break
        if time.time() > deadline:
            raise AssertionError(f"phase1: timed out; last states={states}")
        time.sleep(_QUEUE_POLL_INTERVAL)
        executor.pump()

    assert peak == 2, f"the queue never filled to the cap (peak={peak})"
    assert third_dispatched_while is not None, "the third run never dispatched"
    assert [third_dispatched_while[r] for r in run_ids[:2]].count("running") < 2, (
        "the third run dispatched while both others were still running -- "
        "no queueing occurred"
    )
    print(f"phase1: PASSED peak={peak} third_dispatched_while={third_dispatched_while}")
    return {"submitted": 3, "peak_concurrent": peak, "run_ids": run_ids}


def _phase2(cfg, model_id: str, job: str) -> dict:
    """Phase 2: the restart. With two runs still in flight, discard the
    executor and registry objects, rebuild both over the same bucket, and
    reconcile exactly as server.py's lifespan does on startup.

    Exercises 54ea6fd (startup reconciliation: queue rebuild + adoption
    keyed on execution_name). The queued run stays QUEUED at reconcile time
    -- both slots are occupied by adopted handles -- and only dispatches once
    an adopted run completes and a later pump() runs.
    """
    from google_meridian_mcp_server.bootstrap import build_executor, build_registry
    from google_meridian_mcp_server.execution.worker import build_worker_catalog
    from google_meridian_mcp_server.services.optimization_service import (
        OptimizationService,
    )

    registry = build_registry(cfg)
    executor = build_executor(cfg, registry)
    catalog = build_worker_catalog(cfg)
    service = OptimizationService(
        runner=_InProcessCatalogRunner(catalog),
        registry=registry,
        executor=executor,
        cfg=cfg,
    )

    baseline = _executions_for_job(cfg, job)
    run_ids = []
    for pct in (0.15, 0.25, 0.35):
        config = {
            "scenario": {"type": "fixed_budget"},
            "constraint": {"mode": "global", "pct": pct},
        }
        submit = asyncio.run(
            service.run_optimization(
                model_id, config, compute_tier="cloud_cpu", force_rerun=True
            )
        )
        run_ids.append(submit["run_id"])
    print(f"phase2: submitted {run_ids}")

    deadline = time.time() + _queue_phase_timeout()
    while True:
        live = _executions_for_job(cfg, job) - baseline
        if len(live) >= 2:
            break
        if time.time() > deadline:
            raise AssertionError("phase2: never reached 2 concurrent executions")
        time.sleep(_QUEUE_POLL_INTERVAL)
        executor.pump()

    # Abort rather than pass if the first two complete before the restart --
    # the phase is void, not green.
    if all(registry.get_state(r).status.value != "running" for r in run_ids[:2]):
        sys.exit(
            "VOID: both runs completed before the restart; use a larger model "
            "or a longer constraint sweep"
        )

    d1 = registry.get_dispatch(run_ids[0])
    d2 = registry.get_dispatch(run_ids[1])
    assert d1 and d1.execution_name and d2 and d2.execution_name, (
        f"expected both handles dispatched by restart time: {d1} {d2}"
    )
    d1_name, d2_name = d1.execution_name, d2.execution_name

    # Discard the executor and registry objects -- the process boundary this
    # whole task exists to prove state survives.
    del registry, executor, service

    new_registry = build_registry(cfg)
    new_executor = build_executor(cfg, new_registry)
    new_executor.reconcile_orphans()

    assert set(new_executor._handles.values()) == {d1_name, d2_name}, (
        "the two in-flight executions were not adopted; max_parallel is not "
        f"enforced after a restart: {new_executor._handles}"
    )
    assert _executions_for_job(cfg, job) - baseline == {d1_name, d2_name}, (
        "a new execution was launched during reconciliation instead of adopting"
    )
    assert new_registry.get_state(run_ids[2]).status.value == "queued", (
        "the third run must stay QUEUED right after reconcile: both slots are "
        "occupied by adopted handles"
    )
    print("phase2: adoption confirmed by exact execution name; third run still queued")

    deadline = time.time() + _queue_phase_timeout()
    while new_registry.get_state(run_ids[2]).status.value == "queued":
        if time.time() > deadline:
            raise AssertionError(
                "phase2: third run never dispatched after a slot freed"
            )
        time.sleep(_QUEUE_POLL_INTERVAL)
        new_executor.pump()

    all_new = _executions_for_job(cfg, job) - baseline
    assert len(all_new) == 3, f"expected exactly 3 new executions total, got {all_new}"
    print(
        f"phase2: third run dispatched once a slot freed; new executions total={len(all_new)}"
    )

    deadline = time.time() + _queue_phase_timeout()
    while True:
        states = {r: new_registry.get_state(r).status.value for r in run_ids}
        print(f"phase2: states={states}")
        if all(s == "completed" for s in states.values()):
            break
        if any(s in ("failed", "canceled") for s in states.values()):
            raise AssertionError(f"phase2: a run ended non-completed: {states}")
        if time.time() > deadline:
            raise AssertionError(f"phase2: not all runs completed: {states}")
        time.sleep(_QUEUE_POLL_INTERVAL)
        new_executor.pump()

    required_keys = (
        "summary",
        "channel_tables",
        "allocation",
        "spend_delta",
        "outcome_mode",
    )
    for r in run_ids:
        result = new_registry.get_result(r)
        for key in required_keys:
            assert key in result, f"missing {key!r} in result for {r}: {list(result)}"
    print("phase2: all three runs completed with valid results")

    return {"submitted": 3, "adopted": 2, "new_executions_total": len(all_new)}


async def _phase2b(cfg, service_url: str, service_name: str, model_id: str, job: str):
    """Phase 2b: repeat Phase 2's adoption proof against an ACTUALLY
    redeployed revision rather than a rebuilt object graph. The in-process
    rebuild in Phase 2 proves the reconciliation logic; only a real revision
    swap proves nothing else in the deployment depends on process memory.
    Uses the same verified _force_new_revision as Phase 0.
    """
    from google_meridian_mcp_server.bootstrap import build_registry
    from scripts.validation.capture_baseline import build_client
    from scripts.validation.payloads import extract

    registry = build_registry(cfg)  # read-only observation of the shared GCS state
    baseline = _executions_for_job(cfg, job)

    async with build_client("http", service_url) as client:
        run_ids: list[str] = []
        for pct in (0.12, 0.22, 0.32):
            args = {
                "model_id": model_id,
                "config": {
                    "scenario": {"type": "fixed_budget"},
                    "constraint": {"mode": "global", "pct": pct},
                },
                "compute_tier": "cloud_cpu",
                "force_rerun": True,
            }
            submit = extract(await client.call_tool("run_optimization", args))
            run_ids.append(submit["run_id"])
        print(f"phase2b: submitted {run_ids}")

        deadline = time.time() + _queue_phase_timeout()
        while True:
            live = _executions_for_job(cfg, job) - baseline
            if len(live) >= 2:
                break
            if time.time() > deadline:
                raise AssertionError("phase2b: never reached 2 concurrent executions")
            await asyncio.sleep(_QUEUE_POLL_INTERVAL)

        states_before = {r: registry.get_state(r).status.value for r in run_ids}
        if (
            states_before[run_ids[0]] != "running"
            or states_before[run_ids[1]] != "running"
        ):
            sys.exit(
                "VOID: expected the first two runs still running before the "
                f"revision swap, got {states_before}"
            )
        queued_run_id = run_ids[2]
        assert registry.get_state(queued_run_id).status.value == "queued"

        d1 = registry.get_dispatch(run_ids[0])
        d2 = registry.get_dispatch(run_ids[1])
        assert d1 and d1.execution_name and d2 and d2.execution_name, (
            f"expected both handles dispatched before the swap: {d1} {d2}"
        )
        d1_name, d2_name = d1.execution_name, d2.execution_name

    # Same session-lifetime caveat as Phase 0: the swap kills the server-side
    # MCP session, so the post-swap assertions need a fresh connection.
    _force_new_revision(cfg, service_name, tag="PHASE2B_SWAP")

    async with build_client("http", service_url) as client:
        deadline = time.time() + _queue_phase_timeout()
        while True:
            status = extract(
                await client.call_tool(
                    "get_optimization_status", {"run_id": queued_run_id}
                )
            )
            print(f"phase2b: post-swap {queued_run_id} status={status.get('status')}")
            state = status.get("status")
            if state == "completed":
                break
            if state in ("failed", "canceled"):
                raise AssertionError(f"phase2b: queued run ended {state}: {status}")
            if time.time() > deadline:
                raise AssertionError(
                    f"phase2b: {queued_run_id} never completed after the real "
                    "revision swap"
                )
            await asyncio.sleep(_QUEUE_POLL_INTERVAL)

        for rid in run_ids[:2]:
            status = extract(
                await client.call_tool("get_optimization_status", {"run_id": rid})
            )
            assert status.get("status") == "completed", (
                f"{rid} did not complete: {status}"
            )

    all_new = _executions_for_job(cfg, job) - baseline
    assert {d1_name, d2_name} <= all_new and len(all_new) == 3, (
        f"unexpected execution set after the real revision swap: {all_new} "
        f"(expected exactly {{{d1_name}, {d2_name}, <the third>}})"
    )
    print(f"phase2b: real revision swap survived; new executions={all_new}")
    return {"submitted": 3, "new_executions_total": len(all_new)}


def _phase3(cfg, model_id: str, job: str) -> dict:
    """Phase 3: cancel actually cancels (3528ba8). Submit a fourth run, let
    it reach RUNNING, rebuild the executor and registry so the handle is
    NOT in memory, then cancel by recorded name and confirm the execution
    itself goes terminal -- not just the registry write.
    """
    from google.api_core.exceptions import NotFound
    from google.cloud import run_v2

    from google_meridian_mcp_server.bootstrap import build_executor, build_registry
    from google_meridian_mcp_server.execution.worker import build_worker_catalog
    from google_meridian_mcp_server.services.optimization_service import (
        OptimizationService,
    )

    registry = build_registry(cfg)
    executor = build_executor(cfg, registry)
    catalog = build_worker_catalog(cfg)
    service = OptimizationService(
        runner=_InProcessCatalogRunner(catalog),
        registry=registry,
        executor=executor,
        cfg=cfg,
    )

    config = {
        "scenario": {"type": "fixed_budget"},
        "constraint": {"mode": "global", "pct": 0.4},
    }
    submit = asyncio.run(
        service.run_optimization(
            model_id, config, compute_tier="cloud_cpu", force_rerun=True
        )
    )
    run_id = submit["run_id"]
    print(f"phase3: submitted {run_id}")

    deadline = time.time() + _queue_phase_timeout()
    while True:
        state = registry.get_state(run_id).status.value
        print(f"phase3: state={state}")
        if state == "running":
            break
        if state in ("completed", "failed", "canceled"):
            raise AssertionError(
                f"phase3: run reached {state} before it could be canceled"
            )
        if time.time() > deadline:
            raise AssertionError("phase3: run never reached running")
        time.sleep(_QUEUE_POLL_INTERVAL)
        executor.pump()

    dispatch = registry.get_dispatch(run_id)
    recorded_name = dispatch.execution_name if dispatch else None
    assert recorded_name, f"phase3: no execution_name recorded for {run_id}"

    # Rebuild so the handle is genuinely not in memory -- cancel must work by
    # recorded name alone, not by luck of an in-memory handle (base_executor's
    # cancel() falls back to get_dispatch exactly for this case).
    del registry, executor, service
    new_registry = build_registry(cfg)
    new_executor = build_executor(cfg, new_registry)

    new_executor.cancel(run_id)
    assert new_registry.get_state(run_id).status.value == "canceled", (
        "cancel() did not write CANCELED"
    )

    # _terminate swallows every exception (cloud_run_executor.py's _terminate),
    # so a permission failure would otherwise yield "canceled" with the
    # execution still billing. cancel_execution is async: poll for terminal.
    deadline = time.time() + 180
    executions_client = run_v2.ExecutionsClient()
    while time.time() < deadline:
        try:
            execution = executions_client.get_execution(name=recorded_name)
        except NotFound:
            break
        if execution.completion_time:
            break
        time.sleep(10)
    else:
        raise AssertionError(
            f"registry says canceled but {recorded_name} is still running -- "
            "the billing leak is NOT fixed"
        )
    print(f"phase3: {recorded_name} confirmed terminal after cancel-by-name")
    return {"submitted": 1, "canceled": 1}


def _check_dispatch_json_deletion(cfg, run_id: str) -> bool:
    """Near-free addition: dispatch.json deletion against a REAL bucket.
    Task 8's only other proof is FakeGcsClient, and the local-vs-GCS delete
    asymmetry is the trap.

    "Near-free" means it must cost NO extra execution: this REUSES a run_id
    an earlier phase already submitted and paid for (Phase 1's, which runs
    every submission to completion), rather than submitting a fresh one --
    submitting here would be a full extra paid Cloud Run Job execution and
    contradict the whole point of calling this addition free.
    """
    from google.cloud import storage

    from google_meridian_mcp_server.bootstrap import build_executor, build_registry
    from google_meridian_mcp_server.services.optimization_service import (
        OptimizationService,
    )

    registry = build_registry(cfg)
    executor = build_executor(cfg, registry)
    # No runner/catalog needed: delete() only touches the registry/executor.
    service = OptimizationService(
        runner=None, registry=registry, executor=executor, cfg=cfg
    )

    terminal = {"completed", "failed", "canceled"}
    state = registry.get_state(run_id).status.value
    assert state in terminal, (
        f"near-free: expected {run_id} to already be terminal (reused from an "
        f"earlier phase), got {state}"
    )

    service.delete(run_id)

    client = storage.Client()
    prefix = f"{cfg.optimization_gcs_prefix.rstrip('/')}/runs/{run_id}"
    remaining = list(client.list_blobs(cfg.gcs_bucket, prefix=prefix))
    assert not remaining, (
        f"delete_optimization left blobs behind (dispatch.json among them): "
        f"{[b.name for b in remaining]}"
    )
    print(
        f"near-free: every blob for reused run {run_id} (incl. dispatch.json) "
        "confirmed deleted from GCS"
    )
    return True


def _check_cloud_job_not_found(cfg, model_id: str) -> bool:
    """Near-free addition: cloud_job_not_found classification (34e4a56),
    live. A nonexistent CLOUD_RUN_JOB_CPU means run_job() raises NotFound
    immediately -- no execution starts, so this costs nothing."""
    from google_meridian_mcp_server.bootstrap import build_executor, build_registry
    from google_meridian_mcp_server.execution.worker import build_worker_catalog
    from google_meridian_mcp_server.services.optimization_service import (
        OptimizationService,
    )

    bogus_cfg = cfg.model_copy(
        update={"cloud_run_job_cpu": "queue-smoke-deliberately-nonexistent-job"}
    )
    registry = build_registry(cfg)  # same bucket/prefix; only the job pointer is bad
    executor = build_executor(bogus_cfg, registry)
    catalog = build_worker_catalog(cfg)
    service = OptimizationService(
        runner=_InProcessCatalogRunner(catalog),
        registry=registry,
        executor=executor,
        cfg=bogus_cfg,
    )

    config = {
        "scenario": {"type": "fixed_budget"},
        "constraint": {"mode": "global", "pct": 0.06},
    }
    submit = asyncio.run(
        service.run_optimization(
            model_id, config, compute_tier="cloud_cpu", force_rerun=True
        )
    )
    run_id = submit["run_id"]

    status = service.get_status(run_id)
    error = status.get("error") or {}
    assert error.get("code") == "cloud_job_not_found", (
        f"expected cloud_job_not_found, got status={status}"
    )
    print(f"near-free: {run_id} correctly classified as cloud_job_not_found: {error}")
    return True


def _queue_smoke_main() -> int:
    """Entry point for QUEUE_SMOKE=1: prerequisite checks, then Phases 0-3
    plus the two near-free additions, printing everything a verification
    report needs (project, region, timestamps, per-phase execution counts,
    both max_instance_count readings) so an operator can paste real console
    output into reports/durable-queue-live-verification.md.
    """
    from google_meridian_mcp_server.config import load_config
    from google_meridian_mcp_server.domain.models import OptimizationMode

    cfg = load_config()

    assert cfg.optimization_max_parallel == 2, (
        f"queue smoke needs max_parallel=2, got {cfg.optimization_max_parallel}"
    )
    if cfg.persistence_backend != "gcs":
        sys.exit("ERROR: PERSISTENCE_BACKEND=gcs is required for QUEUE_SMOKE")
    if cfg.optimization_tier not in (
        OptimizationMode.CLOUD_CPU.value,
        OptimizationMode.CLOUD_AUTO.value,
    ):
        sys.exit(
            f"ERROR: OPTIMIZATION_TIER={cfg.optimization_tier!r} does not permit "
            "compute_tier=cloud_cpu submissions; set OPTIMIZATION_TIER=cloud_cpu"
        )

    model_id = os.getenv("MODEL_ID") or sys.exit("ERROR: MODEL_ID env var is required")
    service_name = os.getenv("CLOUD_RUN_SERVICE") or sys.exit(
        "ERROR: CLOUD_RUN_SERVICE env var is required (the deployed MCP "
        "server's Cloud Run service name, e.g. meridian-mcp-server)"
    )
    job = cfg.cloud_run_job_cpu or sys.exit(
        "ERROR: CLOUD_RUN_JOB_CPU env var is required"
    )

    started_at = datetime.now(timezone.utc).isoformat()
    print("=" * 78)
    print("QUEUE_SMOKE: durable queue / restart / cancel acceptance gate")
    print(
        f"project={cfg.cloud_run_project} region={cfg.cloud_run_region} "
        f"service={service_name} job={job} model_id={model_id}"
    )
    print(f"started_at={started_at}")
    print(
        "budget: 13 real optimizer executions across 5 independent phases "
        "(Phases 0/1/2/2b submit their own fresh triplet each, Phase 3 "
        "submits and cancels 1); expect roughly 4-6 hours wall-clock -- see "
        "the module docstring."
    )
    print("=" * 78)

    _check_adc()
    _check_mcp_auth_token()
    _check_model_in_bucket(cfg, model_id)
    _check_no_preexisting_queue_activity(cfg)
    # One read serves both the pin check and the service URL -- avoid a
    # second round trip for what the very next line needs anyway.
    service = _get_service_or_exit(cfg, service_name)
    max_instances_before = _check_pinned_single_instance(cfg, service_name, service)
    service_url = service.uri
    print(f"service_url={service_url}")

    counts: dict[str, Any] = {}
    try:
        counts["phase0"] = asyncio.run(
            _phase0(cfg, service_url, service_name, model_id, job)
        )
        phase1_result = _phase1(cfg, model_id, job)
        counts["phase1"] = phase1_result
        counts["phase2"] = _phase2(cfg, model_id, job)
        counts["phase2b"] = asyncio.run(
            _phase2b(cfg, service_url, service_name, model_id, job)
        )
        counts["phase3"] = _phase3(cfg, model_id, job)
        # Reuse a run Phase 1 already paid for and completed -- see
        # _check_dispatch_json_deletion's docstring for why this must not
        # submit a fresh execution.
        reused_run_id = phase1_result["run_ids"][-1]
        counts["dispatch_json_deletion"] = _check_dispatch_json_deletion(
            cfg, reused_run_id
        )
        counts["cloud_job_not_found"] = _check_cloud_job_not_found(cfg, model_id)
    finally:
        finished_at = datetime.now(timezone.utc).isoformat()
        try:
            # A live GCP call inside `finally` must never replace a genuine
            # phase exception already propagating (e.g. expired credentials
            # partway through a 4-6 hour gate) with one of its own.
            max_instances_after = _read_max_instances(cfg, service_name)
        except Exception as exc:  # noqa: BLE001 - must not mask the real failure
            max_instances_after = f"UNKNOWN (read failed: {exc})"
        print("=" * 78)
        print(
            f"max_instance_count: before={max_instances_before} after={max_instances_after}"
        )
        if max_instances_after != max_instances_before:
            print(
                f"NOTE: {service_name}'s max_instance_count changed during this "
                "run -- restore it to its original operating value now."
            )
        print(f"started_at={started_at} finished_at={finished_at}")
        print("Execution counts per phase:")
        for phase, c in counts.items():
            print(f"  {phase}: {c}")
        print("=" * 78)

    print("QUEUE SMOKE PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())

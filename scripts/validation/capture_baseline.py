"""Capture a labelled behavioural baseline of every tool case.

The harness is deliberately VERSION-AGNOSTIC: the same code captures every
label in spec section 4, so anything it reports as different really is
different in the system under test.

Usage:
  uv run python -m scripts.validation.capture_baseline --label v1.7-engine
  uv run python -m scripts.validation.capture_baseline --label v2.0-refit --force
  uv run python -m scripts.validation.capture_baseline --label v2.0-cloud-cpu \
      --transport http --url https://<service>.run.app
  uv run python -m scripts.validation.capture_baseline --label v2.0-cloud-gpu \
      --transport http --url ... --compute-tier cloud_gpu \
      --variants national-revenue geo-revenue \
      --tools run_optimization run_future_optimization lifecycle
  uv run python -m scripts.validation.capture_baseline --label v2.0-tf \
      --tools get_model_fit --cases geo_filter          # re-run one case

Snapshots land in the gitignored tree
``models/_validation/_baseline/<label>/<variant>/<tool>__<case>.json``,
with ``manifest.json`` beside them -- written LAST, so a label directory
without one is known-incomplete.

Expected wall-clock: 27 real optimizer subprocess runs per label (13 for
national-revenue, 14 for geo-revenue) plus ~250 analysis calls across the
eight fixtures. Tens of minutes per label, six labels.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from scripts.generate_validation_models import DEFAULT_OUT_ROOT
from scripts.validation import matrix
from scripts.validation.manifest import build_manifest, write_manifest
from scripts.validation.matrix import ToolCase
from scripts.validation.normalize import normalize
from scripts.validation.payloads import extract
from scripts.validation.remote_smoke import normalize_mcp_url

BASELINE_ROOT = DEFAULT_OUT_ROOT / "_baseline"

_POLL_ATTEMPTS = 240  # ~120s; real Meridian optimize takes seconds on fixtures
_POLL_INTERVAL = 0.5
_TERMINAL = {"completed", "failed", "canceled"}


class CaptureFailure(Exception):
    """A tool call raised instead of returning an envelope.

    Deliberately NOT snapshotted. Writing the exception to disk would make a
    transport blip indistinguishable from a real result, and -- because the
    file would then exist -- the resumed rerun would skip the case forever.
    """


def case_path(root: Path, label: str, variant_key: str, case: ToolCase) -> Path:
    return Path(root) / label / variant_key / f"{case.tool}__{case.name}.json"


def write_snapshot(path: Path, payload: Any) -> None:
    """Normalize, then write atomically.

    Resumability (spec section 7.2) depends on this: a case whose output file
    exists is skipped, so a half-written file from a killed capture would be
    silently accepted as a finished one. Temp file plus ``os.replace`` makes
    a partial file impossible.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(normalize(payload), indent=2, sort_keys=True))
    os.replace(tmp, path)


def prepare_env(runs_root: str) -> None:
    """Spec section 4.1 -- the trap that would have voided every measurement.

    ``OptimizationService._submit`` short-circuits on ``find_by_fingerprint``
    whenever an existing run for the same fingerprint is COMPLETED, RUNNING
    or QUEUED. Between captures the model_id, the config and (until Phase 4)
    the Meridian version are all identical while only the fixture bytes
    differ -- so a shared registry would serve every optimization and
    lifecycle case in a later capture from an EARLIER capture's run, and the
    diff would report a spurious clean result on exactly the runs it exists
    to measure.

    Copied from ``scripts/qa/future_optimization_qa.py`` lines 559-564.

    IN-PROCESS ONLY. Over HTTP the server is Cloud Run and ignores both
    OPTIMIZATION_RUNS_ROOT and RESULT_CACHE_ENABLED; only ``force_rerun=True``
    (see ``run_optimization_case``) crosses the wire.
    """
    os.environ["PERSISTENCE_BACKEND"] = "local"
    os.environ["LOCAL_MODELS_ROOT"] = str(DEFAULT_OUT_ROOT)
    os.environ["REGISTRY_BACKEND"] = "local"
    os.environ["OPTIMIZATION_ALLOWED_TIERS"] = "local"
    os.environ["RESULT_CACHE_ENABLED"] = "false"
    os.environ["OPTIMIZATION_RUNS_ROOT"] = runs_root


def worker_env() -> dict[str, str]:
    """The environment worker subprocesses actually run under.

    The manifest records the backend and precision the TOOLS ran on, not the
    ones this driver happens to have. ``child_env`` is the single place that
    decides both.
    """
    from google_meridian_mcp_server.execution.base_subprocess import (
        BaseSubprocessExecutor,
    )

    return BaseSubprocessExecutor().child_env()


def selected_cases(
    cases: list[ToolCase], *, tools: list[str] | None, cases_filter: list[str] | None
) -> list[ToolCase]:
    """Apply the --tools / --cases selectors.

    Without them a single failed case forces a variant-wide rerun, which is
    painful locally and expensive on Cloud Run.
    """
    result = cases
    if tools:
        result = [case for case in result if case.tool in tools]
    if cases_filter:
        result = [case for case in result if case.name in cases_filter]
    return result


async def call_tool(client, tool: str, args: dict) -> Any:
    """Call one tool and return its payload.

    Error envelopes are DATA here: this server converts domain errors into a
    flat ``{"error_code": ...}`` payload, and ``metric_not_supported``
    silently becoming a different code is a breaking change for every
    consuming agent -- invisible to a harness that records only successes.

    An EXCEPTION is not data. A protocol-level raise, a transport blip or a
    poll timeout aborts the case rather than being written to disk.
    """
    try:
        result = await client.call_tool(tool, args)
    except Exception as exc:  # noqa: BLE001 - re-raised as a typed capture failure
        raise CaptureFailure(f"{tool}: {type(exc).__name__}: {exc}") from exc
    return extract(result)


def _submit_args(args: dict, compute_tier: str | None) -> dict:
    """force_rerun is mandatory (spec 4.1); compute_tier only when requested.

    OPTIMIZATION_DEFAULT_TIER does not influence routing -- it feeds a startup
    validation check only (domain/models.py:135-139). resolve_tier honours the
    TOOL ARGUMENT and returns it directly when it is not "auto"
    (routing.py:52-58), so this is the only way to reach the GPU job.
    """
    submit = {**args, "force_rerun": True}
    if compute_tier:
        submit["compute_tier"] = compute_tier
    return submit


async def _poll(client, run_id: str) -> dict:
    status: dict = {}
    for _ in range(_POLL_ATTEMPTS):
        status = await call_tool(client, "get_optimization_status", {"run_id": run_id})
        if isinstance(status, dict) and status.get("status") in _TERMINAL:
            return status
        await asyncio.sleep(_POLL_INTERVAL)
    raise CaptureFailure(f"run {run_id} did not reach a terminal status in time")


async def run_optimization_case(
    client, tool: str, args: dict, *, compute_tier: str | None
) -> dict:
    """Submit -> poll -> read result, snapshotted as one composite payload."""
    submit = await call_tool(client, tool, _submit_args(args, compute_tier))
    run_id = submit.get("run_id") if isinstance(submit, dict) else None
    if not run_id:
        # A typed rejection at submit time is a legitimate result.
        return {"submit": submit, "status": None, "result": None}
    try:
        status = await _poll(client, run_id)
        result = await call_tool(client, "get_optimization_result", {"run_id": run_id})
        return {"submit": submit, "status": status, "result": result}
    finally:
        # Always reap: the run's manifest/state/result files and its
        # fingerprint pointer would otherwise leak into the next case.
        await call_tool(client, "delete_optimization", {"run_id": run_id})


_LIFECYCLE_CONFIG = {
    "scenario": {"type": "fixed_budget"},
    "constraint": {"mode": "global", "pct": 0.25},
}
_CANCEL_CONFIG = {
    "scenario": {"type": "fixed_budget"},
    "constraint": {"mode": "global", "pct": 0.35},
}


async def run_lifecycle_case(
    client, model_id: str, name: str, *, compute_tier: str | None
) -> dict:
    """The five lifecycle tools, as two scenarios.

    ``status_result_reuse_delete``: submit -> poll -> result -> identical
    resubmit (must be reused) -> list -> delete -> status (must be a typed
    not-found envelope).

    ``cancel``: submit a different config -> cancel -> status -> delete.
    """
    if name == "cancel":
        submit = await call_tool(
            client,
            "run_optimization",
            _submit_args(
                {"model_id": model_id, "config": _CANCEL_CONFIG}, compute_tier
            ),
        )
        run_id = submit.get("run_id") if isinstance(submit, dict) else None
        if not run_id:
            return {"submit": submit}
        canceled = await call_tool(client, "cancel_optimization", {"run_id": run_id})
        status = await call_tool(client, "get_optimization_status", {"run_id": run_id})
        deleted = await call_tool(client, "delete_optimization", {"run_id": run_id})
        return {
            "submit": submit,
            "canceled": canceled,
            "status": status,
            "deleted": deleted,
        }

    submit = await call_tool(
        client,
        "run_optimization",
        _submit_args({"model_id": model_id, "config": _LIFECYCLE_CONFIG}, compute_tier),
    )
    run_id = submit.get("run_id") if isinstance(submit, dict) else None
    if not run_id:
        return {"submit": submit}
    status = await _poll(client, run_id)
    result = await call_tool(client, "get_optimization_result", {"run_id": run_id})
    # No force_rerun: this submit MUST be served from the fingerprint index.
    reuse_args = {"model_id": model_id, "config": _LIFECYCLE_CONFIG}
    if compute_tier:
        reuse_args["compute_tier"] = compute_tier
    reused = await call_tool(client, "run_optimization", reuse_args)
    listing = await call_tool(client, "list_optimizations", {"model_id": model_id})
    deleted = await call_tool(client, "delete_optimization", {"run_id": run_id})
    gone = await call_tool(client, "get_optimization_status", {"run_id": run_id})
    return {
        "submit": submit,
        "status": status,
        "result": result,
        "reused": reused,
        "listing_count": listing.get("count") if isinstance(listing, dict) else listing,
        "deleted": deleted,
        "status_after_delete": gone,
    }


async def execute_case(client, variant, case: ToolCase, *, compute_tier: str | None):
    if case.tool == "lifecycle":
        return await run_lifecycle_case(
            client, variant.key, case.name, compute_tier=compute_tier
        )
    if case.tool in ("run_optimization", "run_future_optimization"):
        return await run_optimization_case(
            client, case.tool, case.args, compute_tier=compute_tier
        )
    return await call_tool(client, case.tool, case.args)


def build_client(transport: str, url: str | None):
    from fastmcp import Client

    if transport == "http":
        if not url:
            raise SystemExit("--transport http requires --url")
        return Client(normalize_mcp_url(url))
    from google_meridian_mcp_server.server import mcp

    return Client(mcp)


async def capture(
    *,
    label: str,
    transport: str,
    url: str | None,
    variant_keys: list[str],
    tools: list[str] | None,
    cases_filter: list[str] | None,
    compute_tier: str | None,
    force: bool,
    out_root: Path,
) -> int:
    specs = [v for v in matrix.fixture_specs() if v.key in variant_keys]
    if not specs:
        raise SystemExit(f"no known fixtures in {variant_keys}")

    written: list[str] = []
    skipped: list[str] = []
    failures: list[str] = []

    async with build_client(transport, url) as client:
        for variant in specs:
            try:
                overview = await call_tool(
                    client, "get_model_overview", {"model_id": variant.key}
                )
            except CaptureFailure as exc:
                failures.append(f"{variant.key}: {exc}")
                continue
            if not isinstance(overview, dict) or "time" not in overview:
                failures.append(f"{variant.key}: get_model_overview failed: {overview}")
                continue
            all_cases = matrix.tool_cases(
                variant, overview
            ) + matrix.adversarial_tool_cases(variant)
            for case in selected_cases(
                all_cases, tools=tools, cases_filter=cases_filter
            ):
                path = case_path(out_root, label, variant.key, case)
                if path.exists() and not force:
                    skipped.append(str(path))
                    continue
                try:
                    payload = await execute_case(
                        client, variant, case, compute_tier=compute_tier
                    )
                except CaptureFailure as exc:
                    # NOT written to disk: an existing file would make the
                    # resumed rerun skip this case permanently.
                    failures.append(f"{variant.key}/{case.tool}__{case.name}: {exc}")
                    print(f"  FAIL {variant.key}/{case.tool}__{case.name}: {exc}")
                    continue
                write_snapshot(path, payload)
                written.append(str(path))
                print(f"  captured {variant.key}/{case.tool}__{case.name}")

    print(f"\n{len(written)} captured, {len(skipped)} skipped, {len(failures)} failed")
    for failure in failures:
        print(f"  FAIL {failure}")
    if failures:
        print(
            "\nNo manifest written. Fix the failures and re-run; already-captured "
            "cases are skipped, so only the failures are retried."
        )
        return 1

    if tools or cases_filter:
        print(
            "NOTE: a selector was used, so manifest.json was NOT written. "
            "Re-run without --tools/--cases to finish this label."
        )
        return 0

    manifest = build_manifest(
        label=label,
        transport=transport,
        fixture_root=DEFAULT_OUT_ROOT,
        fixture_names=[v.key for v in specs],
        worker_env=worker_env(),
    )
    # Over HTTP the tools ran in a container we cannot inspect from here, so
    # the recorded worker_env and provenance describe THIS machine, not the
    # measurement. Say so, rather than letting a reader assume otherwise.
    manifest["worker_env_authoritative"] = transport == "inprocess"
    manifest_path = write_manifest(Path(out_root) / label, manifest)
    print(f"manifest -> {manifest_path}")
    return 0


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", required=True, help="Capture label, e.g. v2.0-jax")
    parser.add_argument(
        "--transport", choices=("inprocess", "http"), default="inprocess"
    )
    parser.add_argument("--url", default=os.getenv("MERIDIAN_MCP_URL"))
    parser.add_argument("--variants", nargs="*", default=None)
    parser.add_argument("--tools", nargs="*", default=None)
    parser.add_argument("--cases", nargs="*", default=None)
    parser.add_argument(
        "--compute-tier",
        default=None,
        choices=("local", "cloud_cpu", "cloud_gpu"),
        help=(
            "Passed through as the run_optimization/run_future_optimization "
            "`compute_tier` argument. The ONLY way to reach a specific tier: "
            "OPTIMIZATION_DEFAULT_TIER does not influence routing, and `auto` "
            "always resolves to the cheapest allowed tier on these fixtures."
        ),
    )
    parser.add_argument(
        "--force", action="store_true", help="Re-capture existing cases"
    )
    parser.add_argument("--out-root", default=str(BASELINE_ROOT))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    if args.transport == "inprocess":
        runs_root = tempfile.mkdtemp(prefix=f"baseline-{args.label}-runs-")
        prepare_env(runs_root)
        print(f"isolated optimization registry: {runs_root}")
    else:
        print(
            "NOTE: --transport http -- registry isolation is client-side only. "
            "force_rerun=true still defeats the optimization fingerprint "
            "short-circuit, but the deployed service's analysis ResultCache is "
            "whatever the deployment configured."
        )
    variant_keys = args.variants or [v.key for v in matrix.fixture_specs()]
    return asyncio.run(
        capture(
            label=args.label,
            transport=args.transport,
            url=args.url,
            variant_keys=variant_keys,
            tools=args.tools,
            cases_filter=args.cases,
            compute_tier=args.compute_tier,
            force=args.force,
            out_root=Path(args.out_root),
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())

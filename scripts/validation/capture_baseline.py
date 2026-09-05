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

Snapshot marker contract, for the (separate) diff tool: some case payloads
carry a top-level ``known_racy_fields`` key -- a list of dot-separated
object-key paths (e.g. ``"status.status"``) into that SAME snapshot, naming
fields whose value may legitimately differ between labels because of an
inherent race (see ``lifecycle__cancel``). The diff tool MUST (1) classify a
change at any listed path as REVIEW rather than FAIL, and (2) EXCLUDE the
``known_racy_fields`` key itself from the payload comparison -- otherwise a
later change to the marker list (one more racy field added or removed) would
misread as a payload diff on a key that was never data. Path syntax is
deliberately minimal: only dot-separated object-key paths are supported.
There is no escaping for a key that itself contains a literal dot, and no
syntax for indexing through a list (e.g. a racy field inside a ``runs[]``
entry) -- neither is needed by any case today. A future case that needs
either must extend this contract explicitly; do not assume it works.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import platform
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from scripts.generate_validation_models import DEFAULT_OUT_ROOT
from scripts.validation import matrix
from scripts.validation.manifest import (
    _RECORDED_ENV_KEYS,
    build_manifest,
    package_versions,
    write_manifest,
)
from scripts.validation.matrix import ToolCase
from scripts.validation.normalize import normalize
from scripts.validation.payloads import extract
from scripts.validation.remote_smoke import normalize_mcp_url

BASELINE_ROOT = DEFAULT_OUT_ROOT / "_baseline"

_POLL_INTERVAL = 0.5
_DEFAULT_POLL_TIMEOUT = 120.0  # was hard-coded as 240 attempts * 0.5s
_TERMINAL = {"completed", "failed", "canceled"}

# Sidecar recording the environment a label was FIRST captured under (spec
# finding IMPORTANT 3). Deliberately separate from manifest.json: the
# manifest is written last and only for a complete label, while this must be
# written on the very first snapshot so a later resume under a different
# MERIDIAN_BACKEND/package set can be refused instead of silently mixing two
# environments into one "label".
_ENV_SIDECAR_NAME = "capture_env.json"

# IMPORTANT 5: after cancel_optimization the snapshotted get_optimization_status
# can legitimately land on either "canceled" or "completed" depending on which
# side won the race. This key, alongside the snapshot, tells the (separate)
# diff tool which dotted paths inside THIS snapshot are known-racy so it can
# classify a change there as REVIEW instead of FAIL, without hiding the value
# and without adding it to normalize.VOLATILE_FIELDS.
KNOWN_RACY_FIELDS_KEY = "known_racy_fields"


class CaptureFailure(Exception):
    """A tool call raised instead of returning an envelope.

    Deliberately NOT snapshotted. Writing the exception to disk would make a
    transport blip indistinguishable from a real result, and -- because the
    file would then exist -- the resumed rerun would skip the case forever.
    """


def case_path(root: Path, label: str, variant_key: str, case: ToolCase) -> Path:
    return Path(root) / label / variant_key / f"{case.tool}__{case.name}.json"


def is_valid_snapshot(path: Path) -> bool:
    """A file is only a legitimate skip if it is a non-empty, parseable JSON
    snapshot (spec finding CRITICAL 1).

    ``path.exists()`` alone is not enough: a truncated/killed write (or a
    zero-byte file from any other cause) previously read as "already
    captured", the label read as complete, and the diff tool hit a JSON
    decode error against data that was never really there.
    """
    if not path.exists():
        return False
    try:
        text = path.read_text()
        if not text.strip():
            return False
        json.loads(text)
    except (OSError, json.JSONDecodeError):
        return False
    return True


# MINOR New-4: 5 minutes is far longer than any single write_snapshot call
# takes today (a case payload is at most a few MB of JSON; writing and
# renaming it is a sub-second operation), so a .tmp file older than this
# cannot be a live in-flight write from a concurrently running capture --
# only a killed process could leave one this old. That keeps the sweep safe
# for the module's own documented "re-run one case" workflow, where a second
# `capture()` call can legitimately be writing into the SAME label directory
# while this one starts up.
_STALE_TMP_AGE_SECONDS = 300.0


def sweep_stale_tmp_files(
    label_dir: Path, *, max_age_seconds: float = _STALE_TMP_AGE_SECONDS
) -> None:
    """Remove genuinely stale ``*.tmp`` orphans left by a killed
    ``write_snapshot`` (MINOR 8).

    NOT a blanket sweep of every ``.tmp`` under the label: two ``capture()``
    calls can legitimately share a label directory at the same time (e.g. one
    finishing a full run while another re-captures a single ``--cases`` case
    started via the module's own documented resume workflow), and an
    unconditional ``rglob`` sweep would delete the other's in-flight ``.tmp``
    out from under it, making its ``os.replace`` raise ``FileNotFoundError``.
    Only files older than ``max_age_seconds`` are removed.
    """
    if not label_dir.exists():
        return
    now = time.time()
    for tmp_path in label_dir.rglob("*.tmp"):
        try:
            age = now - tmp_path.stat().st_mtime
        except OSError:
            continue  # raced with another process removing/renaming it
        if age >= max_age_seconds:
            tmp_path.unlink(missing_ok=True)


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


def capture_environment(
    transport: str, worker_environment: dict[str, str]
) -> dict[str, Any]:
    """The environment-identifying material for IMPORTANT 3's sidecar.

    Deliberately the SAME material ``manifest.build_manifest`` already
    records (package versions + the recorded worker-env keys), reusing
    ``manifest``'s own helpers rather than inventing a parallel notion of
    "environment". Excludes ``manifest.probe_fixtures``: that is per-fixture
    provenance, not per-label environment, and re-running it on every
    ``capture()`` call (including single-case reruns) would be needlessly
    expensive.
    """
    return {
        "transport": transport,
        "python": platform.python_version(),
        "packages": package_versions(),
        "worker_env": {key: worker_environment.get(key) for key in _RECORDED_ENV_KEYS},
    }


def _describe_env_diff(previous: dict[str, Any], current: dict[str, Any]) -> list[str]:
    diffs = []
    for key in sorted(set(previous) | set(current)):
        if previous.get(key) != current.get(key):
            diffs.append(f"{key}: {previous.get(key)!r} -> {current.get(key)!r}")
    return diffs


def _read_capture_environment(path: Path) -> dict[str, Any] | None:
    """Like ``is_valid_snapshot``, applied to the env sidecar (MINOR New-2):
    ``None`` means "unreadable/corrupt", not "matches nothing in particular"
    -- callers must treat that as an unverifiable environment, not a match.
    """
    try:
        text = path.read_text()
        if not text.strip():
            return None
        data = json.loads(text)
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


_FULL_RECAPTURE_HOWTO = (
    "Re-run with --force and NO --tools/--cases/--variants to fully "
    "recapture this label under the new environment (this rewrites the "
    "sidecar), or choose a fresh --label."
)


def check_capture_environment(
    label_dir: Path, current: dict[str, Any], *, force: bool, has_selector: bool
) -> str | None:
    """Refuse to resume a label under a different (or unverifiable)
    environment than it was captured under (IMPORTANT 3, MINOR New-2).

    ``has_selector`` must be True whenever this run is narrowed by
    ``--tools``/``--cases``/``--variants`` -- i.e. it is NOT a full-label
    recapture. Three situations, per IMPORTANT New-1:

      1. No ``--force``: always refuse on a real mismatch or an unreadable
         sidecar.
      2. ``--force`` WITH a selector: still refuse. A selective recapture
         under a different environment would leave two environments mixed
         into one label -- exactly the failure this guard exists to
         prevent -- so ``--force`` does not override it. Only a full,
         unfiltered ``--force`` may.
      3. ``--force`` with NO selector: a legitimate whole-label recapture.
         The label truly becomes the new environment: return ``None`` (the
         caller rewrites the sidecar on the first snapshot actually
         written).

    Returns ``None`` when there is nothing to refuse: a fresh label (no
    sidecar yet), a sidecar whose recorded environment matches ``current``,
    or situation 3 above.
    """
    path = label_dir / _ENV_SIDECAR_NAME
    if not path.exists():
        return None
    previous = _read_capture_environment(path)
    if previous is None:
        if force and not has_selector:
            return None
        if force:
            return (
                f"capture environment sidecar for label {label_dir.name!r} is "
                f"corrupt or unreadable ({path}), so its environment cannot be "
                "verified -- and --force was passed WITH a selector "
                "(--tools/--cases/--variants), which only recaptures specific "
                "cases and cannot safely override an unverifiable environment. "
                f"{_FULL_RECAPTURE_HOWTO}"
            )
        return (
            f"capture environment sidecar for label {label_dir.name!r} is corrupt "
            f"or unreadable ({path}), so its environment cannot be verified. "
            f"Refusing to resume into an unknown-environment label. "
            f"{_FULL_RECAPTURE_HOWTO}"
        )
    if previous == current:
        return None
    if force and not has_selector:
        return None
    diffs = "; ".join(_describe_env_diff(previous, current))
    if force:
        return (
            f"capture environment changed for label {label_dir.name!r} since it "
            f"was first captured: {diffs}. --force was passed WITH a selector "
            "(--tools/--cases/--variants), which only recaptures specific cases "
            "-- doing that under a different environment would leave two "
            f"environments mixed into one label. {_FULL_RECAPTURE_HOWTO}"
        )
    return (
        f"capture environment changed for label {label_dir.name!r} since it was "
        f"first captured: {diffs}. Refusing to resume into a mixed-environment "
        f"label -- that would silently break the one-variable-per-diff "
        f"guarantee. {_FULL_RECAPTURE_HOWTO}"
    )


def record_capture_environment(label_dir: Path, current: dict[str, Any]) -> None:
    path = label_dir / _ENV_SIDECAR_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(current, indent=2, sort_keys=True))


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


async def _poll(client, run_id: str, *, timeout: float = _DEFAULT_POLL_TIMEOUT) -> dict:
    attempts = max(1, math.ceil(timeout / _POLL_INTERVAL))
    status: dict = {}
    for _ in range(attempts):
        status = await call_tool(client, "get_optimization_status", {"run_id": run_id})
        if isinstance(status, dict) and status.get("status") in _TERMINAL:
            return status
        await asyncio.sleep(_POLL_INTERVAL)
    raise CaptureFailure(
        f"run {run_id} did not reach a terminal status within {timeout}s"
    )


async def run_optimization_case(
    client,
    tool: str,
    args: dict,
    *,
    compute_tier: str | None,
    poll_timeout: float = _DEFAULT_POLL_TIMEOUT,
) -> dict:
    """Submit -> poll -> read result, snapshotted as one composite payload.

    MINOR 7: the reap (``delete_optimization``) is always attempted, but its
    failure must never mask a successful outcome, nor replace the original
    error when the primary path ALSO failed -- both of those would misattribute
    the real cause. So the primary outcome/error is captured first, the reap
    is attempted unconditionally afterwards and only logged on failure, and
    whatever the primary path produced (return value or raised error) wins.
    """
    submit = await call_tool(client, tool, _submit_args(args, compute_tier))
    run_id = submit.get("run_id") if isinstance(submit, dict) else None
    if not run_id:
        # A typed rejection at submit time is a legitimate result.
        return {"submit": submit, "status": None, "result": None}

    outcome: dict | None = None
    primary_error: CaptureFailure | None = None
    try:
        status = await _poll(client, run_id, timeout=poll_timeout)
        result = await call_tool(client, "get_optimization_result", {"run_id": run_id})
        outcome = {"submit": submit, "status": status, "result": result}
    except CaptureFailure as exc:
        primary_error = exc

    # Always reap: the run's manifest/state/result files and its fingerprint
    # pointer would otherwise leak into the next case.
    try:
        await call_tool(client, "delete_optimization", {"run_id": run_id})
    except CaptureFailure as reap_exc:
        print(f"  WARN: failed to reap run {run_id} after capture: {reap_exc}")

    if primary_error is not None:
        raise primary_error
    assert outcome is not None
    return outcome


_LIFECYCLE_CONFIG = {
    "scenario": {"type": "fixed_budget"},
    "constraint": {"mode": "global", "pct": 0.25},
}
_CANCEL_CONFIG = {
    "scenario": {"type": "fixed_budget"},
    "constraint": {"mode": "global", "pct": 0.35},
}


def _scoped_listing(listing: Any, run_id: str) -> Any:
    """IMPORTANT 4: snapshot the ``list_optimizations`` envelope, not a raw
    ``count``, scoped to the run THIS case created.

    Locally the registry is a fresh tempdir (one run -> count 1); against a
    shared Cloud Run registry ``count`` is however many unrelated runs live
    there, which is a permanent false FAIL in the cloud-vs-local comparison.
    Collapsing to an int also hides any change to the envelope's own shape.

    Chosen representation: keep the envelope's own top-level keys (so a
    shape change -- a renamed/added/removed key -- is still visible), but
    restrict ``runs`` to entries whose ``run_id`` matches this case's run and
    recompute ``count`` from that filtered list. That is invariant to
    unrelated runs sharing the registry while still catching a change to
    this run's own listed entry (label/config_summary/status/headline) or to
    the envelope shape itself.
    """
    if not isinstance(listing, dict):
        return listing
    runs = listing.get("runs")
    if not isinstance(runs, list):
        return listing
    matched = [r for r in runs if isinstance(r, dict) and r.get("run_id") == run_id]
    scoped = dict(listing)
    scoped["runs"] = matched
    if "count" in scoped:
        scoped["count"] = len(matched)
    return scoped


async def run_lifecycle_case(
    client,
    model_id: str,
    name: str,
    *,
    compute_tier: str | None,
    poll_timeout: float = _DEFAULT_POLL_TIMEOUT,
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
        # IMPORTANT 5: service.cancel() returns "canceled" unconditionally,
        # but the run itself may still be mid-flight -- an immediate status
        # read can catch queued/running/canceled/completed depending on which
        # side of the race won. Poll to a TERMINAL state (bounded, same
        # machinery as everywhere else) so we snapshot a settled outcome
        # rather than an arbitrary intermediate one.
        #
        # MINOR New-3: the reap must happen regardless of whether that poll
        # succeeds -- otherwise a run that never reaches a terminal state
        # fails the case AND leaks the run (delete_optimization used to sit
        # after _poll unconditionally succeeding). Same non-masking pattern
        # as MINOR 7's run_optimization_case: capture the primary outcome or
        # error first, always attempt the reap afterwards, only log its
        # failure, and let the primary result/error win.
        status = None
        primary_error: CaptureFailure | None = None
        try:
            status = await _poll(client, run_id, timeout=poll_timeout)
        except CaptureFailure as exc:
            primary_error = exc
        try:
            deleted = await call_tool(client, "delete_optimization", {"run_id": run_id})
        except CaptureFailure as reap_exc:
            print(f"  WARN: failed to reap run {run_id} after cancel: {reap_exc}")
            deleted = None
        if primary_error is not None:
            raise primary_error
        return {
            "submit": submit,
            "canceled": canceled,
            "status": status,
            "deleted": deleted,
            # A genuine cancel-vs-complete race can still resolve either way
            # even after polling to a terminal state. Flag the exact field so
            # the diff tool downgrades a change there to REVIEW, not FAIL.
            KNOWN_RACY_FIELDS_KEY: ["status.status"],
        }

    submit = await call_tool(
        client,
        "run_optimization",
        _submit_args({"model_id": model_id, "config": _LIFECYCLE_CONFIG}, compute_tier),
    )
    run_id = submit.get("run_id") if isinstance(submit, dict) else None
    if not run_id:
        return {"submit": submit}
    status = await _poll(client, run_id, timeout=poll_timeout)
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
        "listing": _scoped_listing(listing, run_id),
        "deleted": deleted,
        "status_after_delete": gone,
    }


async def execute_case(
    client,
    variant,
    case: ToolCase,
    *,
    compute_tier: str | None,
    poll_timeout: float = _DEFAULT_POLL_TIMEOUT,
):
    if case.tool == "lifecycle":
        return await run_lifecycle_case(
            client,
            variant.key,
            case.name,
            compute_tier=compute_tier,
            poll_timeout=poll_timeout,
        )
    if case.tool in ("run_optimization", "run_future_optimization"):
        return await run_optimization_case(
            client,
            case.tool,
            case.args,
            compute_tier=compute_tier,
            poll_timeout=poll_timeout,
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
    variants_selected: bool = False,
    poll_timeout: float = _DEFAULT_POLL_TIMEOUT,
) -> int:
    """``variants_selected`` must be True whenever ``variant_keys`` came from
    an explicit ``--variants`` (as opposed to defaulting to every known
    fixture) -- see CRITICAL 2."""
    known_specs = matrix.fixture_specs()
    known_keys = {v.key for v in known_specs}
    unknown = [key for key in variant_keys if key not in known_keys]
    if unknown:
        # CRITICAL 2(b): a typo must fail loudly, naming it -- not silently
        # narrow the run to whatever DID match.
        raise SystemExit(
            f"unknown variant(s) requested: {unknown}. Known variants: "
            f"{sorted(known_keys)}"
        )
    specs = [v for v in known_specs if v.key in variant_keys]
    if not specs:
        raise SystemExit("no variants requested")

    # IMPORTANT New-1: --tools/--cases/--variants all narrow this run to less
    # than the whole label; whether one of them was used determines what
    # --force is allowed to override (see check_capture_environment).
    has_selector = bool(tools or cases_filter or variants_selected)

    label_dir = Path(out_root) / label
    sweep_stale_tmp_files(label_dir)  # MINOR 8: clear orphans from a killed run

    # IMPORTANT 3: refuse to resume this label under a different capture
    # environment than it was started with -- checked BEFORE touching any
    # case, using the worker environment the tools will actually run under.
    current_env = capture_environment(transport, worker_env())
    env_conflict = check_capture_environment(
        label_dir, current_env, force=force, has_selector=has_selector
    )
    if env_conflict:
        print(env_conflict)
        return 1
    env_recorded = False

    written: list[str] = []
    skipped: list[str] = []
    recaptured: list[str] = []
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
                if not force:
                    if is_valid_snapshot(path):
                        skipped.append(str(path))
                        continue
                    if path.exists():
                        # CRITICAL 1: corrupt/empty is not a valid skip --
                        # re-capture it instead of trusting a truncated file.
                        recaptured.append(str(path))
                        print(
                            f"  RECAPTURE (corrupt/empty snapshot) "
                            f"{variant.key}/{case.tool}__{case.name}"
                        )
                try:
                    payload = await execute_case(
                        client,
                        variant,
                        case,
                        compute_tier=compute_tier,
                        poll_timeout=poll_timeout,
                    )
                except CaptureFailure as exc:
                    # NOT written to disk: an existing file would make the
                    # resumed rerun skip this case permanently.
                    failures.append(f"{variant.key}/{case.tool}__{case.name}: {exc}")
                    print(f"  FAIL {variant.key}/{case.tool}__{case.name}: {exc}")
                    continue
                if not env_recorded:
                    record_capture_environment(label_dir, current_env)
                    env_recorded = True
                write_snapshot(path, payload)
                written.append(str(path))
                print(f"  captured {variant.key}/{case.tool}__{case.name}")

    print(
        f"\n{len(written)} captured, {len(skipped)} skipped, "
        f"{len(recaptured)} recaptured, {len(failures)} failed"
    )
    for failure in failures:
        print(f"  FAIL {failure}")
    if failures:
        print(
            "\nNo manifest written. Fix the failures and re-run; already-captured "
            "cases are skipped, so only the failures are retried."
        )
        return 1

    if tools or cases_filter or variants_selected:
        print(
            "NOTE: a selector was used, so manifest.json was NOT written. "
            "Re-run without --tools/--cases/--variants to finish this label."
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
    manifest_path = write_manifest(label_dir, manifest)
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
    parser.add_argument(
        "--poll-timeout",
        type=float,
        default=_DEFAULT_POLL_TIMEOUT,
        help=(
            "Seconds to poll get_optimization_status for a terminal state "
            f"before failing the case (default {_DEFAULT_POLL_TIMEOUT:.0f}s, "
            "today's effective ceiling). Cloud Run Job scheduling plus GPU "
            "allocation routinely exceeds that -- raise this for "
            "--transport http --compute-tier cloud_gpu."
        ),
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
            variants_selected=args.variants is not None,
            poll_timeout=args.poll_timeout,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())

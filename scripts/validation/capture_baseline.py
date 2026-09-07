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
  uv run python -m scripts.validation.capture_baseline --label <existing-label> \
      --allow-stale-recapture   # required once the recorded env is stale;
                                # see CRITICAL C1 below -- refused otherwise

Snapshots land in the gitignored tree
``models/_validation/_baseline/<label>/<variant>/<tool>__<case>.json``,
with ``manifest.json`` beside them -- written LAST, so a label directory
without one is known-incomplete.

Expected wall-clock: 27 real optimizer subprocess runs per label (13 for
national-revenue, 14 for geo-revenue) plus ~250 analysis calls across the
eight fixtures. Tens of minutes per label, six labels.

Snapshot envelope (Fix wave 4): each snapshot file on disk is an explicit
envelope, NOT a bare tool payload:

    {"capture_env": <env fingerprint>, "known_racy_fields": [...]?, "payload": <normalized tool payload>}

``known_racy_fields`` is present only for cases that need it (today, only
``lifecycle__cancel``) and is a list of dot-separated object-key paths INTO
``payload`` naming fields whose value may legitimately differ between
labels because of an inherent race (e.g. ``"status.status"``). The diff
tool compares ONLY ``payload`` between two labels' same-named snapshot;
``capture_env`` and ``known_racy_fields`` are metadata, read but never
diffed as data -- a change confined to either is not a payload difference.
For any path listed in ``known_racy_fields``, the diff tool must classify a
change there as REVIEW rather than FAIL. Path syntax is deliberately
minimal: only dot-separated object-key paths are supported. There is no
escaping for a key that itself contains a literal dot, and no syntax for
indexing through a list (e.g. a racy field inside a ``runs[]`` entry) --
neither is needed by any case today. A future case that needs either must
extend this contract explicitly; do not assume it works.

``capture_env`` makes environment identity a PER-SNAPSHOT property rather
than a per-label one (an earlier revision of this module tracked it in a
single label-wide sidecar, which required a three-branch ``--force`` rule
and a destructive ``rmtree`` to stay correct -- both are gone). A snapshot
is only ever skipped as "already captured" when it is a valid envelope
(Minor 1: which requires a ``payload`` key, not just a JSON object) AND its
own ``capture_env`` matches what THIS run would capture under; a
stale-environment snapshot is recaptured individually, exactly like a
corrupt one, and no other file in the label is touched. ``--force`` is back
to its plain original meaning: recapture regardless of whether an existing
snapshot is valid or current. No destructive/whole-directory code path
remains in this file.

``capture_env`` also carries (Minor 3) a content hash of the fixture the
case ran against and a hash of the ``src/`` tree the server ran from, so
that rebuilding a fixture or editing ``src/`` MID-LABEL -- a realistic
sequence when a resumed capture straddles a source change -- invalidates
exactly the snapshots affected, which the once-at-the-end manifest cannot
see. Before executing anything, ``capture()`` prints a one-line pre-flight
summary (Minor 4: current/stale/missing/will-execute counts) so that cost
is visible before it is incurred, without gating or prompting on it.

CRITICAL C1 (whole-branch review): a label whose EXISTING snapshots have
gone stale because the environment moved on (the exact situation
``v1.7-engine`` is in today: its 330 snapshots record ``google-meridian
1.7.0`` and this machine now runs ``2.0.0``, so every one of them reads as
``stale_env``) is a fundamentally different situation from a label that
merely has missing or corrupt cases. Recapturing a stale-env snapshot does
not "catch it up" -- it PERMANENTLY DESTROYS the only record of how the OLD
environment behaved, and for a label like ``v1.7-engine`` that record
cannot be regenerated (the old Meridian version is no longer installed).
Before Fix wave 6 this recapture happened unconditionally and silently: no
prompt, and ``--force`` was not even required, because a stale-env snapshot
was already treated as "not a valid skip" on the plain default path.

``--force`` keeps its plain, orthogonal meaning from Fix wave 4 (recapture
regardless of validity/currency -- including snapshots that are already
CURRENT); it is a "redo everything" instrument, not an acknowledgement that
irreplaceable old-environment evidence is about to be overwritten, and a
label can accumulate stale-env snapshots with nobody ever having typed
``--force`` at all. So the stale-env guard is a SEPARATE flag,
``--allow-stale-recapture``, and it gates BOTH the default path and
``--force``: recapturing any ``stale_env`` snapshot requires this flag
regardless of ``--force``. If a plan contains one or more ``stale_env``
snapshots and this flag is absent, ``capture()`` refuses the entire run
before touching anything -- naming the label, the count of stale-env
snapshots, and which recorded packages differ from what this run would
capture -- and returns 1. A ``corrupt`` snapshot (empty/malformed/no
``payload``) is not gated: there is no valid old-environment data in it to
lose, so it is recaptured exactly as before.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
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
from scripts.validation.fixture_probe import file_fingerprint
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

# Fix wave 5 (MINOR 3): the server code the payloads actually depend on, so
# that editing src/ mid-label invalidates exactly the snapshots affected.
# Resolved from this file's own location (matches the pattern already used
# by scripts/qa/future_optimization_qa.py's _REPO_ROOT), NOT from cwd --
# unlike DEFAULT_OUT_ROOT, which the rest of this module already assumes is
# cwd-relative; this one is cheap to make robust, so it is.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _REPO_ROOT / "src" / "google_meridian_mcp_server"

_POLL_INTERVAL = 0.5
_DEFAULT_POLL_TIMEOUT = 120.0  # was hard-coded as 240 attempts * 0.5s
_TERMINAL = {"completed", "failed", "canceled"}

# IMPORTANT 5: after cancel_optimization the snapshotted get_optimization_status
# can legitimately land on either "canceled" or "completed" depending on which
# side won the race. This key, in the snapshot ENVELOPE (not the payload --
# Fix wave 4), tells the (separate) diff tool which dotted paths into
# `payload` are known-racy so it can classify a change there as REVIEW
# instead of FAIL, without hiding the value and without adding it to
# normalize.VOLATILE_FIELDS.
KNOWN_RACY_FIELDS_KEY = "known_racy_fields"


class CaptureFailure(Exception):
    """A tool call raised instead of returning an envelope.

    Deliberately NOT snapshotted. Writing the exception to disk would make a
    transport blip indistinguishable from a real result, and -- because the
    file would then exist -- the resumed rerun would skip the case forever.
    """


def case_path(root: Path, label: str, variant_key: str, case: ToolCase) -> Path:
    return Path(root) / label / variant_key / f"{case.tool}__{case.name}.json"


def validate_label(label: str, out_root: Path) -> Path:
    """CRITICAL, defence in depth: ``--label`` is operator-supplied and is
    joined straight into a filesystem path.

    Reproduced without this check: ``--label ""`` collapses ``label_dir`` to
    ``out_root`` itself; ``--label ".."`` targets the parent; ``--label
    "/abs/path"`` escapes entirely, because ``Path('/a/b') / '/c/d'`` is
    ``/c/d`` in pathlib, not a joined path; ``--label "."`` slips past the
    substring filter below and collapses to ``out_root`` itself exactly like
    ``""`` does. Reject a label that is empty or contains ``/``, ``\\`` or
    ``..`` up front, THEN independently check that the resulting directory
    really does resolve to a direct child of ``out_root`` -- a second,
    structural check that does not rely on having enumerated every bad
    substring (it is what actually catches ``"."``).

    MINOR 2: that second check used to be a bare ``assert``, which (a) dies
    with an ugly ``AssertionError`` traceback instead of a clean exit 1, and
    (b) VANISHES ENTIRELY under ``python -O``, silently letting
    ``label_dir`` fall back to ``out_root``. It is a real ``if``/``raise``
    now, so it fires regardless of how Python was invoked.
    """
    if not label or "/" in label or "\\" in label or ".." in label:
        raise SystemExit(
            f"invalid --label {label!r}: must be non-empty and must not "
            "contain '/', '\\', or '..' -- it is joined directly into a "
            "filesystem path."
        )
    label_dir = Path(out_root) / label
    resolved_root = Path(out_root).resolve()
    if label_dir.resolve().parent != resolved_root:
        raise SystemExit(
            f"invalid --label {label!r}: resolved outside {resolved_root} "
            f"(got {label_dir.resolve()})"
        )
    return label_dir


def read_snapshot(path: Path) -> dict[str, Any] | None:
    """Load a snapshot ENVELOPE. Returns ``None`` if the file is missing,
    empty, not valid JSON, not a JSON object, or (MINOR 1) a JSON object
    with no ``"payload"`` key -- CRITICAL 1, generalized to the envelope
    shape: none of those is a legitimate skip candidate, and
    ``path.exists()`` alone was never enough to tell. MINOR 1 matters
    because a same-environment, ``payload``-less envelope would otherwise
    read as a valid, current snapshot and be skipped forever with no data
    in it -- the exact failure class CRITICAL 1 closed for the bare-payload
    file, reopened by the envelope's extra structure.
    """
    if not path.exists():
        return None
    try:
        text = path.read_text()
        if not text.strip():
            return None
        data = json.loads(text)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or "payload" not in data:
        return None
    return data


def _snapshot_status(path: Path, current_env: dict[str, Any]) -> str:
    """Classify one case's on-disk state against ``current_env``, shared by
    the MINOR 4 pre-flight summary and the real skip decision so the two
    can never disagree:

      'missing'   -- no file at all.
      'corrupt'   -- exists but ``read_snapshot`` rejects it (empty, not
                     JSON, not an object, or missing ``payload`` -- CRITICAL
                     1 / MINOR 1).
      'stale_env' -- a valid envelope, but its own ``capture_env`` no
                     longer matches (environment, fixture content, or src/
                     content changed -- IMPORTANT 3 / MINOR 3).
      'current'   -- a valid envelope whose ``capture_env`` matches: the
                     only legitimate skip.
    """
    if not path.exists():
        return "missing"
    envelope = read_snapshot(path)
    if envelope is None:
        return "corrupt"
    if envelope.get("capture_env") == current_env:
        return "current"
    return "stale_env"


def _package_diff(
    old_packages: dict[str, Any], new_packages: dict[str, Any]
) -> dict[str, tuple[Any, Any]]:
    """Which package versions differ between an existing snapshot's
    ``capture_env["packages"]`` and what this run would record -- the detail
    the CRITICAL C1 refusal message names so an operator can tell "the
    environment genuinely moved on" from "I nearly destroyed something for
    no reason". Keyed on the union of both sides so an added/removed package
    shows up too, not just a changed version string.
    """
    diffs: dict[str, tuple[Any, Any]] = {}
    for key in sorted(set(old_packages) | set(new_packages)):
        old_value = old_packages.get(key)
        new_value = new_packages.get(key)
        if old_value != new_value:
            diffs[key] = (old_value, new_value)
    return diffs


def describe_stale_env(old_env: dict[str, Any], new_env: dict[str, Any]) -> str:
    """Human-readable summary of why one snapshot's ``capture_env`` is
    stale, for the CRITICAL C1 refusal message. Leads with package
    differences (the ``v1.7-engine`` scenario this guard exists for --
    ``google-meridian``/``jax`` version drift) and falls back to naming any
    other differing field so the message stays honest when packages did NOT
    change (e.g. only ``src_hash`` moved because ``src/`` was edited).
    """
    parts: list[str] = []
    pkg_diffs = _package_diff(
        old_env.get("packages") or {}, new_env.get("packages") or {}
    )
    if pkg_diffs:
        rendered = ", ".join(
            f"{name} {old!r} -> {new!r}" for name, (old, new) in pkg_diffs.items()
        )
        parts.append(f"packages differ: {rendered}")
    for key in ("python", "transport", "fixture_hash", "src_hash", "worker_env"):
        if old_env.get(key) != new_env.get(key):
            parts.append(
                f"{key} changed ({old_env.get(key)!r} -> {new_env.get(key)!r})"
            )
    return (
        "; ".join(parts)
        if parts
        else "environment changed (no field-level detail available)"
    )


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


def write_snapshot(
    path: Path,
    payload: Any,
    *,
    capture_env: dict[str, Any],
    racy_fields: list[str] | None = None,
) -> None:
    """Normalize the payload, wrap it in a self-describing envelope, and
    write atomically.

    Fix wave 4: the envelope's ``capture_env`` is what makes environment
    identity a per-SNAPSHOT property -- ``capture_env`` and
    ``known_racy_fields`` are metadata about the payload, so only ``payload``
    itself is passed through ``normalize`` (they are not tool output and
    must not be tokenized as if they were).

    Resumability (spec section 7.2) depends on the atomic write: a case
    whose output file exists is (conditionally) skipped, so a half-written
    file from a killed capture would be silently accepted as a finished one.
    Temp file plus ``os.replace`` makes a partial file impossible.
    """
    envelope: dict[str, Any] = {"capture_env": capture_env}
    if racy_fields:
        envelope[KNOWN_RACY_FIELDS_KEY] = list(racy_fields)
    envelope["payload"] = normalize(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(envelope, indent=2, sort_keys=True))
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

    FROZEN, deliberately: this return value (specifically the
    ``manifest._RECORDED_ENV_KEYS`` slice of it) feeds every snapshot's
    ``capture_env`` via ``capture_environment`` below, which is what makes an
    existing snapshot's on-disk envelope match or not match a fresh run's
    environment. Built with NO ``env_base``, this reports whatever this
    driver process happened to inherit -- e.g. no ``MERIDIAN_BACKEND`` at all
    on a machine that never exported one -- which is why it must NOT be used
    to decide what backend the fixture PROBE runs under (see
    ``probe_backend`` below): changing this function's return value, or
    ``manifest._RECORDED_ENV_KEYS``, would mark every existing snapshot in
    every existing label stale, including ``v1.7-engine``'s 330 snapshots,
    which cannot be regenerated (Meridian 1.7 is no longer installed on this
    machine) and would be silently overwritten with Meridian 2.0 output if
    anything ever forced their recapture.
    """
    from google_meridian_mcp_server.execution.base_subprocess import (
        BaseSubprocessExecutor,
    )

    return BaseSubprocessExecutor().child_env()


def probe_backend() -> str:
    """The backend the fixture PROBE must run under, so its reading of
    ``meridian.backend.computation_backend()`` describes what the real
    workers computed instead of Meridian's own library default (JAX, as of
    Meridian 2.0 -- see ``meridian.backend.config._DEFAULT_BACKEND``).

    NOT derived from ``worker_env()``: that function is frozen (see its own
    docstring) and, on a machine that never exported ``MERIDIAN_BACKEND``,
    reports no backend at all. This function instead mirrors the SAME
    resolution the real analysis/optimization workers use -- both now pin
    ``base_subprocess.MERIDIAN_BACKEND`` unconditionally (Phase 4 removed the
    ``MERIDIAN_BACKEND`` env-var knob entirely and fixed the backend as a
    module constant in ``src/`` instead) -- so a reader sees what the tools
    genuinely ran on.

    This imports that constant directly rather than reading an environment
    variable or guessing a default, so the probe follows the code instead of
    guessing. Importing a name from ``src/`` here does not touch
    ``src_tree_hash()`` (see below) -- that hash is computed from file
    content on disk, not from what this module happens to import -- and this
    value is NOT part of ``worker_env()``/``capture_env`` (see above), so
    this change cannot mark any existing snapshot stale.
    """
    from google_meridian_mcp_server.execution.base_subprocess import (
        MERIDIAN_BACKEND,
    )

    return MERIDIAN_BACKEND


def _tree_hash(root: Path, *, glob: str = "*") -> str:
    """Deterministic content hash of every ``glob``-matching file under
    ``root``, combining ``fixture_probe.file_fingerprint`` per file (Minor
    3: reusing that hashing, not inventing a second one) by relative path.

    ``__pycache__`` is always excluded: bytecode caches churn on ordinary
    interpreter runs, independent of any real source or fixture change, and
    hashing them would manufacture false staleness. A missing ``root``
    degrades to a fixed sentinel string rather than raising -- this feeds a
    staleness COMPARISON (do two hashes match), not a hard dependency, so a
    missing tree just becomes its own consistent, comparable value.
    """
    root = Path(root)
    if not root.exists():
        return f"<missing:{root}>"
    files = sorted(
        p for p in root.rglob(glob) if p.is_file() and "__pycache__" not in p.parts
    )
    combined = hashlib.sha256()
    for path in files:
        combined.update(str(path.relative_to(root)).encode())
        combined.update(file_fingerprint(path).encode())
    return combined.hexdigest()


def fixture_content_hash(fixture_dir: Path) -> str:
    """Content hash of one fixture directory (Minor 3), so rebuilding a
    fixture mid-label invalidates exactly the snapshots captured against it
    -- every file in the directory, not just the model file, since a
    variant's args/expectations can depend on sidecar fixture data too.
    """
    return _tree_hash(fixture_dir)


def src_tree_hash() -> str:
    """Content hash of the server package the tool payloads actually run
    (Minor 3) -- ``*.py`` files under ``src/google_meridian_mcp_server``
    only.

    Deliberately NOT ``git rev-parse HEAD``: any commit at all -- docs,
    tests, this harness itself -- would invalidate every snapshot in a
    label and force a full recapture, which for a Cloud Run label costs
    real money. Hashing the tree instead means only an actual change to the
    server code invalidates anything. Excludes ``*.egg-info`` (build/install
    metadata, not code) by construction (the glob is ``*.py`` and egg-info
    holds none) and ``__pycache__`` (see ``_tree_hash``).
    """
    return _tree_hash(_SRC_ROOT, glob="*.py")


def capture_environment(
    transport: str,
    worker_environment: dict[str, str],
    *,
    fixture_hash: str,
    src_hash: str,
) -> dict[str, Any]:
    """The environment-identifying material recorded in every snapshot's
    ``capture_env`` (Fix wave 4; formerly a label-wide sidecar, IMPORTANT 3).

    The package-version/worker-env material is the SAME ``manifest.build_manifest``
    already records, reusing ``manifest``'s own helpers rather than inventing
    a parallel notion of "environment". Excludes ``manifest.probe_fixtures``:
    that is per-fixture PROVENANCE (backend/precision the model was trained
    under), not a content hash, and re-running it on every ``capture()`` call
    (including single-case reruns) would be needlessly expensive -- it
    imports Meridian in a subprocess.

    ``fixture_hash`` and ``src_hash`` (Minor 3) close a gap the manifest
    cannot: the manifest is written once, at the end, so it records the
    POST-change state for the whole label -- it cannot see that a fixture
    was rebuilt or ``src/`` was edited midway through a label that started
    earlier and is now being resumed. Per-snapshot hashes catch that at the
    only granularity that matters: the individual case.
    """
    return {
        "transport": transport,
        "python": platform.python_version(),
        "packages": package_versions(),
        "worker_env": {key: worker_environment.get(key) for key in _RECORDED_ENV_KEYS},
        "fixture_hash": fixture_hash,
        "src_hash": src_hash,
    }


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

    NOTE the deliberate asymmetry with ``run_lifecycle_case``'s ``cancel``
    branch (IMPORTANT B): here the reap is pure cleanup with NO
    representation in the returned payload, so a WARN-and-continue on
    failure is correct and must stay that way. There, ``deleted`` is itself
    part of the snapshotted payload, so a failed reap must fail the case
    instead of being logged and recorded as ``null``. Do not "fix" one to
    match the other.
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

    ``cancel``: submit a different config -> cancel -> status -> delete. Its
    returned dict carries a ``KNOWN_RACY_FIELDS_KEY`` entry; ``capture()``
    pops that out of the payload and into the snapshot envelope before
    writing (Fix wave 4) -- callers of THIS function still see it embedded,
    exactly as before.
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
        # MINOR New-3 / IMPORTANT B: the reap must be attempted regardless of
        # whether that poll succeeds -- otherwise a run that never reaches a
        # terminal state leaks (New-3). BUT this is NOT the same shape as
        # run_optimization_case's reap: there, deletion is pure cleanup with
        # no representation in the payload, so warn-and-continue on failure
        # is correct and MUST stay that way. HERE, `deleted` is itself part
        # of this case's snapshotted payload -- a failed delete_optimization
        # must FAIL the case (the cardinal "a failed tool call is a failure,
        # never a snapshot" rule), not be recorded as `deleted: null`. If the
        # poll already failed, that original error remains the reported
        # cause; the reap failure is only logged, never allowed to replace
        # it (the Minor 7 non-masking property, preserved).
        status = None
        primary_error: CaptureFailure | None = None
        try:
            status = await _poll(client, run_id, timeout=poll_timeout)
        except CaptureFailure as exc:
            primary_error = exc
        deleted = None
        try:
            deleted = await call_tool(client, "delete_optimization", {"run_id": run_id})
        except CaptureFailure as reap_exc:
            if primary_error is None:
                primary_error = reap_exc
            else:
                print(
                    f"  WARN: failed to reap run {run_id} after cancel (case "
                    f"already failing on: {primary_error}): {reap_exc}"
                )
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
        endpoint = normalize_mcp_url(url)
        # Task 24: example-dev-project's domain-restricted-sharing org policy refused
        # the allUsers invoker binding, so the deployed service requires an
        # identity token (`gcloud auth print-identity-token`) even though
        # allow_unauthenticated=true in Terraform. MCP_AUTH_TOKEN carries it
        # here rather than adding a CLI flag that would leak into shell
        # history; unset (the local/inprocess-adjacent case) is unaffected.
        token = os.environ.get("MCP_AUTH_TOKEN")
        if token:
            from fastmcp.client.transports import StreamableHttpTransport

            return Client(
                StreamableHttpTransport(
                    endpoint, headers={"Authorization": f"Bearer {token}"}
                )
            )
        return Client(endpoint)
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
    allow_stale_recapture: bool = False,
) -> int:
    """``variants_selected`` must be True whenever ``variant_keys`` came from
    an explicit ``--variants`` (as opposed to defaulting to every known
    fixture) -- see CRITICAL 2 / manifest suppression, below."""
    label_dir = validate_label(label, out_root)  # CRITICAL: defence in depth

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

    # CRITICAL 2(a) / MINOR C: --tools/--cases always narrow this run below
    # the whole label; --variants only narrows it if the requested set is a
    # PROPER subset of every known fixture -- naming all of them (e.g. an
    # explicit Phase 7 cloud spelling) is a full-label run in substance.
    # This is the ONLY remaining notion of "a selector was used" now that
    # environment tracking is per-snapshot: it governs manifest suppression
    # alone.
    variants_narrow = variants_selected and set(variant_keys) != known_keys
    is_partial_run = bool(tools or cases_filter or variants_narrow)

    sweep_stale_tmp_files(label_dir)  # MINOR 8: clear orphans from a killed run

    # Fix wave 4: environment identity is a PER-SNAPSHOT property (each
    # envelope's own `capture_env`), not a per-label sidecar. `--force` is
    # back to its plain meaning: recapture regardless of validity/currency.
    # Fix wave 5 (MINOR 3): capture_env also carries a fixture content hash
    # (per variant, computed once each below) and a src/ tree hash (once
    # here -- it does not vary by variant).
    worker_environment = worker_env()
    src_hash = src_tree_hash()

    written: list[str] = []
    skipped: list[str] = []
    recaptured: list[str] = []
    failures: list[str] = []

    async with build_client(transport, url) as client:
        # Phase 1: resolve every variant's overview and case list ONCE.
        # Needed before MINOR 4's pre-flight summary can be computed (the
        # case list depends on the overview), and reused for phase 2 so
        # get_model_overview is still called exactly once per variant.
        plan: list[tuple[Any, list[ToolCase], dict[str, Any]]] = []
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
            variant_cases = selected_cases(
                all_cases, tools=tools, cases_filter=cases_filter
            )
            variant_env = capture_environment(
                transport,
                worker_environment,
                fixture_hash=fixture_content_hash(DEFAULT_OUT_ROOT / variant.key),
                src_hash=src_hash,
            )
            plan.append((variant, variant_cases, variant_env))

        # MINOR 4: visibility before cost, no gate/prompt -- the same
        # command is used against Cloud Run, where execution is not free.
        #
        # CRITICAL C1: 'stale_env' and 'corrupt' are counted separately here
        # (both still roll up into the printed "stale" total, unchanged from
        # Fix wave 4/5) because only 'stale_env' overwrites data that
        # recorded a DIFFERENT, possibly irreplaceable environment. A
        # 'corrupt' file has no valid payload to lose, so it stays ungated.
        n_current = n_missing = n_stale_env = n_corrupt = 0
        stale_env_examples: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for _variant, variant_cases, variant_env in plan:
            for case in variant_cases:
                path = case_path(out_root, label, _variant.key, case)
                status = _snapshot_status(path, variant_env)
                if status == "current":
                    n_current += 1
                elif status == "missing":
                    n_missing += 1
                elif status == "corrupt":
                    n_corrupt += 1
                else:
                    n_stale_env += 1
                    envelope = read_snapshot(path)
                    if envelope is not None:
                        stale_env_examples.append(
                            (envelope.get("capture_env") or {}, variant_env)
                        )
        n_stale = n_stale_env + n_corrupt
        n_will_execute = (
            (n_current + n_stale + n_missing) if force else (n_stale + n_missing)
        )
        print(
            f"Pre-flight: {n_current} current, {n_stale} stale, {n_missing} missing "
            f"-> {n_will_execute} case(s) will be executed"
            + (" (--force: every case, regardless of currency)" if force else "")
        )

        # CRITICAL C1: stale-environment recapture requires explicit opt-in,
        # independent of --force (see the module docstring and
        # describe_stale_env). Refuse the ENTIRE run before executing
        # anything -- naming the label, how many snapshots are stale, and
        # what changed -- rather than silently overwriting the only record
        # of the old environment's behaviour.
        if n_stale_env and not allow_stale_recapture:
            diffs = "; ".join(
                dict.fromkeys(
                    describe_stale_env(old_env, new_env)
                    for old_env, new_env in stale_env_examples
                )
            )
            print(
                f"REFUSED: label {label!r} has {n_stale_env} snapshot(s) whose "
                "recorded environment no longer matches this run. Recapturing "
                "them would permanently overwrite the only record of how the "
                "OLD environment behaved.\n"
                f"What changed: {diffs}\n"
                "Re-run with --allow-stale-recapture once you have confirmed "
                "this is intentional -- --force alone does not bypass this "
                "guard, and this guard does not require --force either."
            )
            return 1

        # Phase 2: execute.
        for variant, variant_cases, variant_env in plan:
            for case in variant_cases:
                path = case_path(out_root, label, variant.key, case)
                if not force:
                    status = _snapshot_status(path, variant_env)
                    if status == "current":
                        skipped.append(str(path))
                        continue
                    if status == "stale_env":
                        # Fix wave 4/5: a valid envelope whose capture_env
                        # (environment OR fixture/src content, Minor 3) no
                        # longer matches is not a valid skip either --
                        # recapture it individually. Nothing else in the
                        # label is touched: a per-file decision, never a
                        # per-label one.
                        recaptured.append(str(path))
                        print(
                            f"  RECAPTURE (stale environment) "
                            f"{variant.key}/{case.tool}__{case.name}"
                        )
                    elif status == "corrupt":
                        # CRITICAL 1 / MINOR 1: corrupt, empty, or
                        # payload-less is not a valid skip -- re-capture it
                        # instead of trusting a truncated/malformed file.
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
                racy_fields = None
                if isinstance(payload, dict) and KNOWN_RACY_FIELDS_KEY in payload:
                    # Fix wave 4: this marker is envelope metadata, not
                    # payload -- move it out before writing.
                    racy_fields = payload.pop(KNOWN_RACY_FIELDS_KEY)
                write_snapshot(
                    path, payload, capture_env=variant_env, racy_fields=racy_fields
                )
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

    if is_partial_run:
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
        probe_backend=probe_backend(),
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
        "--allow-stale-recapture",
        action="store_true",
        help=(
            "CRITICAL C1: required to recapture any snapshot whose recorded "
            "environment no longer matches this run (e.g. a package version "
            "changed since the label was captured). Independent of --force: "
            "--force alone does not bypass this guard. Without it, a run "
            "that would touch a stale-environment snapshot refuses entirely "
            "before executing anything, naming the label and what changed."
        ),
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
            allow_stale_recapture=args.allow_stale_recapture,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())

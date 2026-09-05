"""Diff two labelled baselines and classify every leaf (spec section 7.3).

  diff_baseline.py <label_a> <label_b> [--allow-fixture-change] [--report out.md]

Manifests are compared BEFORE data. Differing fixture fingerprints abort
unless --allow-fixture-change, which makes it structurally impossible to
attribute posterior resampling to engine drift. Only the Phase 5 diff passes
that flag.

Snapshot envelope: each snapshot file on disk is
``{"capture_env": ..., "known_racy_fields": [...]?, "payload": ...}``
(see the module docstring of ``capture_baseline.py`` for the authoritative
contract). Only ``payload`` is compared leaf-by-leaf below; ``capture_env``
and ``known_racy_fields`` are metadata, never diffed as data. A file that is
not a valid envelope (no top-level ``payload`` key -- a pre-Task-6
bare-payload snapshot, or a corrupt file) is a loud error, never a silent
empty payload: ``compare(None, None)`` would otherwise report a clean diff
for two files that were never actually compared.

``known_racy_fields`` lists dot-separated OBJECT paths into ``payload`` (no
dot-escaping, no list-index form) whose value may legitimately differ
between labels because of an inherent race -- today, only
``lifecycle__cancel``'s ``status.status``. A difference at any such path is
downgraded from FAIL to REVIEW, never silently dropped: it is still a
change, just not a defect. A malformed declaration (not a list of strings)
is a loud error too, not a silent no-op or a bare traceback.

``headline`` (``OptimizationRunState.headline``, built by
``execution/worker.py``'s ``_headline``) is
``f"{label} {non_opt} -> {opt} at budget {budget}"`` -- three real computed
floats baked into an opaque string. As a plain string it would bypass the
numeric tolerance entirely and turn into a guaranteed FAIL under any
low-order-digit float drift (e.g. the Phase 4 JAX + x64 switch), even though
the same numbers are separately captured raw, under full tolerance, in
``get_optimization_result``'s ``summary``. It is therefore parsed and
compared structurally: the label and literal separators exactly, and each
embedded number through the SAME tolerance as everything else -- never
waived wholesale, and never added to ``normalize.VOLATILE_FIELDS``.

NaN and infinity are handled explicitly in the numeric tolerance (see
``_compare_numbers``): an unchanged NaN is not a finding, a value becoming or
ceasing to be NaN is a structural FAIL (not float noise), and the same is
true for infinity.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import math
import re
import sys
from pathlib import Path
from typing import Any

from scripts.validation import acknowledged
from scripts.validation.manifest import (
    MANIFEST_NAME,
    fixture_fingerprints,
    read_manifest,
)

# Relative tolerance WITH an absolute floor, so 1e-12 -> 2e-12 does not read
# as 100% drift. Measures are already rounded to six significant figures in
# services/analysis_service.py, which damps sub-threshold noise at source.
REL_TOLERANCE = 1e-3
ABS_FLOOR = 1e-9

# Identities, not measurements. A relative float tolerance would let
# row_count 2185 -> 2186 (4.6e-4) PASS, while spec 7.3 classifies a changed
# row_count as structural. `count` covers both list_optimizations.count and
# get_model_overview.time.count.
IDENTITY_FIELDS = frozenset(
    {"row_count", "size_score", "count", "geo_count", "total_channels"}
)

_CI_KEYS = ("ci_lo", "mean", "ci_hi")

# f"{label} {non_opt} -> {opt} at budget {budget}" from execution/worker.py's
# _headline. label/non_opt/opt/budget are all single Python reprs (a float, or
# the literal "None") and therefore never contain internal whitespace, so a
# plain \S+ token per slot is exact -- not an approximation.
_HEADLINE_RE = re.compile(
    r"^(?P<label>\S+) (?P<non_opt>\S+) -> (?P<opt>\S+) at budget (?P<budget>\S+)$"
)


@dataclasses.dataclass(frozen=True)
class Finding:
    verdict: str  # "FAIL" | "REVIEW" | "ACKNOWLEDGED" | "PASS"
    pointer: str
    detail: str


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _leaf(pointer: str) -> str:
    return pointer.rsplit("/", 1)[-1]


def _pointer_for(pointer: str, key: str) -> str:
    """Append one object key to a JSON-pointer-style path.

    The scheme used throughout this module is deliberately minimal (plain
    ``/``-joined keys, no escaping -- matching ``known_racy_fields``'s own
    dot-path contract). A key containing a literal ``/`` would corrupt every
    pointer built beneath it: e.g. a key ``"a/count"`` one level down
    produces the exact same trailing segment as the identity field
    ``"count"`` to ``_leaf``, silently mis-classifying an ordinary field as
    an identity. Rather than risk that, fail loudly and name the key.
    """
    if "/" in key:
        raise ValueError(
            f"key {key!r} under pointer {pointer or '/'} contains a literal "
            "'/', which this module's dot-free pointer scheme cannot "
            "represent without ambiguity. Extend the pointer contract "
            "explicitly before diffing a payload with this key."
        )
    return f"{pointer}/{key}"


def _classify_key_change(pointer: str, change: str, detail: str) -> Finding:
    entry = acknowledged.match(pointer, change)
    if entry is not None:
        return Finding("ACKNOWLEDGED", pointer, f"{detail} -- {entry.reason}")
    return Finding("FAIL", pointer, detail)


def _compare_numbers(a: float, b: float, pointer: str) -> list[Finding]:
    # NaN and infinity break ordinary arithmetic comparison before the
    # absolute floor or the sign-flip check below ever run: NaN compares
    # unequal to everything, including itself, so an UNCHANGED NaN leaf
    # would otherwise fall through to `relative = nan`, producing an
    # unactionable "relative delta nan" REVIEW on a value that did not even
    # change -- exactly the "hundreds of false findings nobody reads"
    # failure mode this module exists to avoid. Handled explicitly, before
    # any arithmetic:
    #   - NaN vs NaN is the SAME value here (by construction, not IEEE
    #     equality) -- no finding.
    #   - a value becoming or ceasing to be NaN, in either direction, is a
    #     structural break, not float noise -- FAIL.
    #   - the same infinity (`inf == inf` and `-inf == -inf` in Python) is
    #     unchanged -- no finding; `inf` vs a finite number, or `inf` vs
    #     `-inf`, is FAIL.
    nan_a, nan_b = math.isnan(a), math.isnan(b)
    if nan_a or nan_b:
        if nan_a and nan_b:
            return []
        return [
            Finding(
                "FAIL",
                pointer,
                f"value {'became' if nan_b else 'stopped being'} NaN: {a} -> {b}",
            )
        ]
    if math.isinf(a) or math.isinf(b):
        if a == b:
            return []
        return [Finding("FAIL", pointer, f"value changed at infinity: {a} -> {b}")]
    if a == b:
        return []
    scale = max(abs(a), abs(b))
    # The floor comes FIRST, including before the sign check: get_model_fit
    # residuals hover at zero and flip sign as pure float noise.
    if scale <= ABS_FLOOR:
        return []
    if (a < 0) != (b < 0) and a != 0 and b != 0:
        return [Finding("REVIEW", pointer, f"unexpected sign flip: {a} -> {b}")]
    relative = abs(b - a) / scale
    if relative <= REL_TOLERANCE:
        return []
    return [
        Finding(
            "REVIEW",
            pointer,
            f"relative delta {relative:.3e} > {REL_TOLERANCE:.0e}: {a} -> {b}",
        )
    ]


def _try_float(token: str) -> float | None:
    try:
        return float(token)
    except ValueError:
        return None


def _compare_headline(a: str, b: str, pointer: str) -> list[Finding]:
    """Structural comparison of an ``OptimizationRunState.headline`` string.

    Splits ``"{label} {non_opt} -> {opt} at budget {budget}"``, compares
    ``label`` (and the literal separators, implicitly, via the regex match)
    exactly, and runs each embedded number through the same tolerance as
    every other float in the payload.
    """
    if a == b:
        return []
    match_a = _HEADLINE_RE.match(a)
    match_b = _HEADLINE_RE.match(b)
    if match_a is None or match_b is None:
        # Doesn't fit the known shape (or the shape itself changed): fall
        # back to plain string equality rather than guessing.
        return [Finding("FAIL", pointer, f"headline value changed: {a!r} -> {b!r}")]
    if match_a["label"] != match_b["label"]:
        return [
            Finding(
                "FAIL",
                pointer,
                f"headline label changed: {match_a['label']!r} -> {match_b['label']!r}",
            )
        ]
    findings: list[Finding] = []
    for part in ("non_opt", "opt", "budget"):
        raw_a, raw_b = match_a[part], match_b[part]
        if raw_a == raw_b:
            continue
        num_a, num_b = _try_float(raw_a), _try_float(raw_b)
        if num_a is not None and num_b is not None:
            for finding in _compare_numbers(num_a, num_b, pointer):
                findings.append(
                    Finding(
                        finding.verdict,
                        finding.pointer,
                        f"headline {part} {finding.detail}",
                    )
                )
        else:
            findings.append(
                Finding(
                    "FAIL",
                    pointer,
                    f"headline {part} changed: {raw_a!r} -> {raw_b!r}",
                )
            )
    return findings


def compare(a: Any, b: Any, pointer: str = "") -> list[Finding]:
    """Classify every leaf difference between two normalized payloads."""
    if isinstance(a, dict) and isinstance(b, dict):
        findings: list[Finding] = []
        for key in sorted(set(a) - set(b)):
            findings.append(
                _classify_key_change(
                    _pointer_for(pointer, key), "removed", f"key removed: {key!r}"
                )
            )
        for key in sorted(set(b) - set(a)):
            findings.append(
                _classify_key_change(
                    _pointer_for(pointer, key), "added", f"key added: {key!r}"
                )
            )
        for key in sorted(set(a) & set(b)):
            findings.extend(compare(a[key], b[key], _pointer_for(pointer, key)))
        return findings

    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return [Finding("FAIL", pointer, f"list length {len(a)} -> {len(b)}")]
        findings = []
        for index, (item_a, item_b) in enumerate(zip(a, b)):
            findings.extend(compare(item_a, item_b, f"{pointer}/{index}"))
        return findings

    if _leaf(pointer) == "headline" and isinstance(a, str) and isinstance(b, str):
        return _compare_headline(a, b, pointer)

    if _is_number(a) and _is_number(b):
        if type(a) is not type(b):
            return [
                Finding(
                    "FAIL",
                    pointer,
                    f"numeric type changed: {type(a).__name__} -> "
                    f"{type(b).__name__} ({a!r} -> {b!r})",
                )
            ]
        if _leaf(pointer) in IDENTITY_FIELDS:
            if a != b:
                return [Finding("FAIL", pointer, f"identity value changed: {a} -> {b}")]
            return []
        return _compare_numbers(float(a), float(b), pointer)

    if type(a) is not type(b):
        return [
            Finding(
                "FAIL",
                pointer,
                f"type changed: {type(a).__name__} -> {type(b).__name__} "
                f"({a!r} -> {b!r})",
            )
        ]

    if a != b:
        return [Finding("FAIL", pointer, f"value changed: {a!r} -> {b!r}")]
    return []


def _ci_row_finding(values: dict, pointer: str) -> list[Finding]:
    lo, mean, hi = values["ci_lo"], values["mean"], values["ci_hi"]
    if not all(_is_number(value) for value in (lo, mean, hi)):
        return []
    if lo <= mean <= hi:
        return []
    return [
        Finding(
            "REVIEW", pointer, f"CI ordering broken: {lo} <= {mean} <= {hi} is false"
        )
    ]


def ci_findings(payload: Any, pointer: str = "") -> list[Finding]:
    """Scan a payload for broken confidence intervals.

    Handles both shapes this server emits: the columnar envelope
    (``columns`` + positional ``rows``) and object rows such as
    ``allocation`` / ``channel_tables``. ``marginal_cpik`` is the one that
    matters most: it is derived by inverting posterior ``mroi`` values and
    must keep its bounds ordered after inversion.
    """
    findings: list[Finding] = []
    if isinstance(payload, dict):
        columns = payload.get("columns")
        rows = payload.get("rows")
        if isinstance(columns, list) and isinstance(rows, list):
            if set(_CI_KEYS) <= set(columns):
                index = {name: columns.index(name) for name in _CI_KEYS}
                for row_number, row in enumerate(rows):
                    if not isinstance(row, list) or len(row) != len(columns):
                        continue
                    findings.extend(
                        _ci_row_finding(
                            {name: row[position] for name, position in index.items()},
                            f"{pointer}/rows/{row_number}",
                        )
                    )
        if set(_CI_KEYS) <= set(payload):
            findings.extend(_ci_row_finding(payload, pointer))
        for key, value in payload.items():
            if key in ("columns", "rows"):
                continue
            findings.extend(ci_findings(value, _pointer_for(pointer, key)))
    elif isinstance(payload, list):
        for index, item in enumerate(payload):
            findings.extend(ci_findings(item, f"{pointer}/{index}"))
    return findings


def _racy_pointer_set(racy_fields: frozenset[str] | set[str]) -> frozenset[str]:
    """Translate dot-separated object paths into the ``/``-joined pointers
    ``compare`` produces. Deliberately minimal, matching the capture
    contract: no dot-escaping, no list-index form."""
    return frozenset("/" + path.replace(".", "/") for path in racy_fields)


def diff_case(
    a: dict,
    b: dict,
    pointer: str,
    *,
    racy_fields: frozenset[str] | set[str] = frozenset(),
) -> list[Finding]:
    """All findings for one case: structural/numeric drift plus CI ordering on
    B, with any ``known_racy_fields`` path downgraded from FAIL to REVIEW.

    ``racy_fields`` is metadata carried outside ``payload`` in the snapshot
    envelope (present only for ``lifecycle__cancel`` today) -- a change at
    one of these paths is an acknowledged race, not a defect, but it is
    still surfaced as REVIEW rather than silently dropped. Keyword-only so a
    caller can never accidentally pass it positionally where ``pointer`` was
    meant.
    """
    findings = compare(a, b, pointer) + ci_findings(b, pointer)
    racy_pointers = _racy_pointer_set(racy_fields)
    if not racy_pointers:
        return findings
    result: list[Finding] = []
    for finding in findings:
        if finding.verdict == "FAIL" and finding.pointer in racy_pointers:
            result.append(
                Finding(
                    "REVIEW",
                    finding.pointer,
                    f"{finding.detail} -- known racy field (declared in "
                    "known_racy_fields; inherent race, not a defect)",
                )
            )
        else:
            result.append(finding)
    return result


def exit_code(findings: list[Finding]) -> int:
    """Non-zero on any FAIL or un-acknowledged REVIEW."""
    return 1 if any(f.verdict in ("FAIL", "REVIEW") for f in findings) else 0


def check_manifests(
    manifest_a: dict, manifest_b: dict, *, allow_fixture_change: bool
) -> None:
    """Compare manifests BEFORE any data.

    Differing fixture fingerprints mean the two captures measured different
    models. Diffing them anyway would attribute posterior resampling to
    engine drift -- the exact confusion the four-label design exists to
    prevent. Only the Phase 5 (v2.0-jax -> v2.0-refit) diff may pass the flag.
    """
    fingerprints_a = fixture_fingerprints(manifest_a)
    fingerprints_b = fixture_fingerprints(manifest_b)
    if fingerprints_a == fingerprints_b:
        return
    changed = sorted(
        name
        for name in set(fingerprints_a) | set(fingerprints_b)
        if fingerprints_a.get(name) != fingerprints_b.get(name)
    )
    if not allow_fixture_change:
        raise SystemExit(
            "fixture fingerprints differ for: "
            + ", ".join(changed)
            + "\nRefusing to diff: this would attribute posterior resampling to "
            "engine drift.\nPass --allow-fixture-change ONLY for the Phase 5 "
            "resampling diff."
        )
    print(f"WARNING: fixtures changed ({', '.join(changed)}); allowed by flag.")


def _load_snapshot(path: Path) -> tuple[Any, dict, list[str]]:
    """Read one snapshot envelope and split it into (payload, capture_env,
    known_racy_fields).

    The envelope is the on-disk contract written by ``capture_baseline.py``:
    a dict with a mandatory ``payload`` key (``capture_baseline``'s own skip
    logic requires the same key to treat a file as a valid snapshot). A bare
    pre-Task-6 payload file -- or anything else that is not a valid envelope
    -- has NO ``payload`` key, and must never be silently treated as an
    absent/empty payload: ``compare(None, None)`` would then report a clean
    diff for two files that were never actually compared, i.e. a silent
    PASS on exactly the drift the harness exists to catch. Fail loudly,
    naming the file, instead.
    """
    envelope = json.loads(path.read_text())
    if not isinstance(envelope, dict) or "payload" not in envelope:
        raise SystemExit(
            f"{path}: not a valid snapshot envelope (missing a top-level "
            "'payload' key). This is either a pre-Task-6 bare-payload "
            "snapshot or a corrupt file -- either way it must not be "
            "silently diffed as an empty payload. Recapture it with "
            "capture_baseline.py."
        )
    known_racy_fields = envelope.get("known_racy_fields", [])
    if not isinstance(known_racy_fields, list) or not all(
        isinstance(item, str) for item in known_racy_fields
    ):
        raise SystemExit(
            f"{path}: 'known_racy_fields' must be a list of strings, got "
            f"{known_racy_fields!r}. Fix the snapshot rather than silently "
            "ignoring (or crashing on) a malformed declaration."
        )
    return (
        envelope["payload"],
        envelope.get("capture_env", {}),
        known_racy_fields,
    )


def _load_case_files(root: Path, label: str) -> dict[str, tuple[Any, dict, list[str]]]:
    base = root / label
    cases: dict[str, tuple[Any, dict, list[str]]] = {}
    for path in sorted(base.rglob("*.json")):
        if path.name == MANIFEST_NAME:
            continue
        cases[str(path.relative_to(base))] = _load_snapshot(path)
    return cases


def _provenance_rows(label: str, manifest: dict) -> list[str]:
    lines = [f"**{label}**", ""]
    if manifest.get("worker_env_authoritative") is False:
        lines.append(
            "- NOTE: captured over HTTP. The worker environment and provenance "
            "below describe the machine that ran the capture client, NOT the "
            "deployed container the tools actually executed in."
        )
    for name, probe in sorted(manifest.get("fixtures", {}).items()):
        trained = probe.get("trained_backend")
        if trained is None:
            continue
        lines.append(
            f"- `{name}`: trained {trained}/{probe.get('trained_precision')} "
            f"(v{probe.get('trained_model_version')}) vs current "
            f"{probe.get('current_backend')}/{probe.get('current_precision')} "
            f"-- backend mismatch: {probe.get('backend_mismatch')}, "
            f"precision mismatch: {probe.get('precision_mismatch')}"
        )
    lines.append("")
    return lines


_ENV_PROVENANCE_FIELDS = ("transport", "python", "fixture_hash", "src_hash")


def _env_provenance_section(
    label_a: str,
    label_b: str,
    findings_by_case: dict[str, list[Finding]],
    case_envs: dict[str, tuple[dict, dict]],
) -> list[str]:
    """Per-case ``capture_env`` provenance for cases with findings, with any
    field that differs THE SAME WAY across every such case hoisted to one
    label-level line instead of being repeated once per case.

    A field like ``src_hash`` is typically constant per label -- if it
    differs between the two labels, it differs identically for every case
    that has a finding, and repeating the identical line once per case (six
    times in an early draft of this report) pushed the actual findings below
    the fold without adding any information. Only a field that genuinely
    VARIES from case to case is worth a per-case line.
    """
    cases_with_findings = [
        case for case in sorted(findings_by_case) if findings_by_case[case]
    ]
    if not cases_with_findings:
        return []

    per_field_pairs: dict[str, set[tuple[Any, Any]]] = {
        field: set() for field in _ENV_PROVENANCE_FIELDS
    }
    for case in cases_with_findings:
        env_a, env_b = case_envs.get(case, ({}, {}))
        for field in _ENV_PROVENANCE_FIELDS:
            value_a, value_b = env_a.get(field), env_b.get(field)
            if value_a != value_b:
                per_field_pairs[field].add((value_a, value_b))

    constant_fields = {
        field: next(iter(pairs))
        for field, pairs in per_field_pairs.items()
        if len(pairs) == 1
    }
    varying_fields = [
        field
        for field in _ENV_PROVENANCE_FIELDS
        if field not in constant_fields and per_field_pairs[field]
    ]

    lines: list[str] = []
    if constant_fields:
        hoisted = "; ".join(
            f"{field}: `{value_a}` vs `{value_b}`"
            for field, (value_a, value_b) in constant_fields.items()
        )
        lines.append(
            f"- {label_a} vs {label_b} capture_env differs the same way for "
            f"every case below: {hoisted}"
        )

    for case in cases_with_findings:
        env_a, env_b = case_envs.get(case, ({}, {}))
        diffs = [
            f"{field}: `{env_a.get(field)}` vs `{env_b.get(field)}`"
            for field in varying_fields
            if env_a.get(field) != env_b.get(field)
        ]
        if diffs:
            lines.append(f"- `{case}` -- " + "; ".join(diffs))

    if not lines:
        return []
    return (
        ["## Per-case environment provenance (for cases with findings)", ""]
        + lines
        + [""]
    )


def _review_reason(detail: str) -> str:
    """Categorize a REVIEW finding's reason for the report's Reason column.

    Needed because "REVIEW" covers several unrelated situations (a numeric
    drift over tolerance, a sign flip, a known racy field, broken CI
    ordering) that a reader cannot tell apart from the verdict alone -- the
    racy-field row for ``lifecycle__cancel`` is a STRING change
    (``'canceled' -> 'completed'``), not a number, and filing it under a
    heading that says "numeric" is actively misleading.
    """
    if "known racy field" in detail:
        return "known racy field"
    if "sign flip" in detail:
        return "sign flip"
    if "CI ordering broken" in detail:
        return "ordering"
    if "relative delta" in detail:
        return "over tolerance"
    return "other"


def _verdict_line(counts: dict[str, int], missing_count: int, code: int) -> str:
    parts = []
    if counts["FAIL"]:
        parts.append(f"{counts['FAIL']} FAIL")
    if counts["REVIEW"]:
        parts.append(f"{counts['REVIEW']} REVIEW")
    if missing_count:
        parts.append(f"{missing_count} case(s) present in only one label")
    if counts["ACKNOWLEDGED"]:
        parts.append(f"{counts['ACKNOWLEDGED']} ACKNOWLEDGED")
    breakdown = ", ".join(parts) if parts else "nothing to report"
    verdict = "FAIL" if code else "PASS"
    return f"**Verdict: {verdict} -- exit code {code}** ({breakdown})"


def render_report(
    label_a: str,
    label_b: str,
    findings_by_case: dict[str, list[Finding]],
    manifest_a: dict,
    manifest_b: dict,
    missing: dict[str, list[str]],
    case_envs: dict[str, tuple[dict, dict]] | None = None,
) -> str:
    all_findings = [f for findings in findings_by_case.values() for f in findings]
    counts = {
        verdict: sum(1 for f in all_findings if f.verdict == verdict)
        for verdict in ("FAIL", "REVIEW", "ACKNOWLEDGED")
    }
    missing_count = len(missing.get("only_in_a", [])) + len(
        missing.get("only_in_b", [])
    )
    # A case-set difference is exactly as blocking as a structural FAIL (see
    # main()'s own exit-code rule) -- folded into the same count here so the
    # Summary can never show "FAIL: 0" next to a nonzero exit code.
    total_fail = counts["FAIL"] + missing_count
    code = 1 if (total_fail or counts["REVIEW"]) else 0

    lines: list[str] = [
        f"# Drift report: `{label_a}` -> `{label_b}`",
        "",
        _verdict_line(counts, missing_count, code),
        "",
        "## Legend",
        "",
        "- **FAIL** and **REVIEW** both block (exit code 1): FAIL is a structural "
        "break (added/removed/type-changed field, changed identity, or a case "
        "missing from one label) with no ambiguity; REVIEW is a numeric or "
        "ordering finding that needs a human to judge (over tolerance, sign "
        "flip, known racy field, broken CI ordering) and MIGHT be legitimate.",
        "- **ACKNOWLEDGED** does not block: pre-registered in `acknowledged.py` "
        "with a mandatory reason (spec 7.4). Reported in its own section, "
        "never folded into a clean PASS.",
        "",
        "## Environment",
        "",
        f"| Item | {label_a} | {label_b} |",
        "| --- | --- | --- |",
        f"| transport | {manifest_a.get('transport')} | {manifest_b.get('transport')} |",
        f"| Python | {manifest_a.get('python')} | {manifest_b.get('python')} |",
    ]
    packages = sorted(
        set(manifest_a.get("packages", {})) | set(manifest_b.get("packages", {}))
    )
    for name in packages:
        lines.append(
            f"| {name} | {manifest_a.get('packages', {}).get(name)} "
            f"| {manifest_b.get('packages', {}).get(name)} |"
        )
    for key in ("MERIDIAN_BACKEND", "MERIDIAN_ENABLE_JAX_X64", "TF_CPP_MIN_LOG_LEVEL"):
        lines.append(
            f"| worker {key} | {manifest_a.get('worker_env', {}).get(key)} "
            f"| {manifest_b.get('worker_env', {}).get(key)} |"
        )
    lines.append(
        f"| relative tolerance (REL_TOLERANCE) | {REL_TOLERANCE:.0e} | {REL_TOLERANCE:.0e} |"
    )
    lines.append(f"| absolute floor (ABS_FLOOR) | {ABS_FLOOR:.0e} | {ABS_FLOOR:.0e} |")
    lines += ["", "## Fixture provenance", ""]
    lines += _provenance_rows(label_a, manifest_a)
    lines += _provenance_rows(label_b, manifest_b)

    if missing["only_in_a"] or missing["only_in_b"]:
        lines += ["## Case-set differences", ""]
        for name in missing["only_in_a"]:
            lines.append(f"- **FAIL** case present only in `{label_a}`: `{name}`")
        for name in missing["only_in_b"]:
            lines.append(f"- **FAIL** case present only in `{label_b}`: `{name}`")
        lines.append("")

    lines += [
        "## Summary",
        "",
        f"- Cases compared: {len(findings_by_case)}",
        f"- Case-set differences (case present in only one label): {missing_count}",
        f"- FAIL (including case-set differences above): {total_fail}",
        f"- REVIEW: {counts['REVIEW']}",
        f"- ACKNOWLEDGED: {counts['ACKNOWLEDGED']}",
        "",
    ]

    if case_envs:
        lines += _env_provenance_section(label_a, label_b, findings_by_case, case_envs)

    for verdict, heading, columns in (
        ("FAIL", "## FAIL - structural", ("Case", "Pointer", "Detail")),
        (
            "REVIEW",
            "## REVIEW -- needs a human",
            ("Case", "Pointer", "Reason", "Detail"),
        ),
        (
            "ACKNOWLEDGED",
            "## ACKNOWLEDGED - pre-registered in acknowledged.py",
            ("Case", "Pointer", "Detail"),
        ),
    ):
        lines += [heading, ""]
        rows = [
            (case, finding)
            for case, findings in sorted(findings_by_case.items())
            for finding in findings
            if finding.verdict == verdict
        ]
        if not rows:
            lines += ["_none_", ""]
            continue
        lines += [
            "| " + " | ".join(columns) + " |",
            "| " + " | ".join("---" for _ in columns) + " |",
        ]
        for case, finding in rows:
            detail = finding.detail.replace("|", "\\|")
            pointer = finding.pointer or "/"
            if verdict == "REVIEW":
                lines.append(
                    f"| `{case}` | `{pointer}` | {_review_reason(finding.detail)} "
                    f"| {detail} |"
                )
            else:
                lines.append(f"| `{case}` | `{pointer}` | {detail} |")
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("label_a")
    parser.add_argument("label_b")
    parser.add_argument("--allow-fixture-change", action="store_true")
    parser.add_argument("--report", default=None, help="Write the Markdown report here")
    parser.add_argument(
        "--root", default="models/_validation/_baseline", help="Baseline snapshot root"
    )
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    root = Path(args.root)
    manifest_a = read_manifest(root / args.label_a)
    manifest_b = read_manifest(root / args.label_b)
    check_manifests(
        manifest_a, manifest_b, allow_fixture_change=args.allow_fixture_change
    )

    cases_a = _load_case_files(root, args.label_a)
    cases_b = _load_case_files(root, args.label_b)
    missing = {
        "only_in_a": sorted(set(cases_a) - set(cases_b)),
        "only_in_b": sorted(set(cases_b) - set(cases_a)),
    }
    common = sorted(set(cases_a) & set(cases_b))
    findings_by_case: dict[str, list[Finding]] = {}
    case_envs: dict[str, tuple[dict, dict]] = {}
    for name in common:
        payload_a, env_a, racy_a = cases_a[name]
        payload_b, env_b, racy_b = cases_b[name]
        racy_fields = frozenset(racy_a) | frozenset(racy_b)
        findings_by_case[name] = diff_case(
            payload_a, payload_b, "", racy_fields=racy_fields
        )
        case_envs[name] = (env_a, env_b)

    report = render_report(
        args.label_a,
        args.label_b,
        findings_by_case,
        manifest_a,
        manifest_b,
        missing,
        case_envs,
    )
    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(report)
        print(f"report -> {args.report}")
    else:
        print(report)

    all_findings = [f for findings in findings_by_case.values() for f in findings]
    if missing["only_in_a"] or missing["only_in_b"]:
        return 1
    return exit_code(all_findings)


if __name__ == "__main__":
    raise SystemExit(main())

"""Every classification rule in the drift differ."""

import json
import math

import pytest

from scripts.validation import diff_baseline as db


def verdicts(a, b):
    return sorted(finding.verdict for finding in db.compare(a, b))


def test_identical_payloads_produce_no_findings():
    payload = {"columns": ["a"], "rows": [[1.0]], "row_count": 1}
    assert db.compare(payload, payload) == []


def test_added_key_is_structural_fail():
    findings = db.compare({"a": 1}, {"a": 1, "b": 2})
    assert [f.verdict for f in findings] == ["FAIL"]
    assert findings[0].pointer == "/b"


def test_removed_key_is_structural_fail():
    findings = db.compare({"a": 1, "b": 2}, {"a": 1})
    assert [f.verdict for f in findings] == ["FAIL"]


def test_reordered_columns_are_structural_fail():
    a = {"columns": ["channel", "mean"]}
    b = {"columns": ["mean", "channel"]}
    assert verdicts(a, b) == ["FAIL", "FAIL"]


def test_row_list_length_change_is_structural_fail():
    findings = db.compare({"rows": [[1.0]]}, {"rows": [[1.0], [2.0]]})
    assert findings[0].verdict == "FAIL"
    assert "length" in findings[0].detail


def test_row_count_off_by_one_is_FAIL_not_a_passing_rounding_error():
    """Spec 7.3 classifies a changed row_count as structural. Through a
    relative float tolerance, 2185 -> 2186 is 4.6e-4 and would PASS."""
    findings = db.compare({"row_count": 2185}, {"row_count": 2186})
    assert [f.verdict for f in findings] == ["FAIL"]
    assert "identity" in findings[0].detail


@pytest.mark.parametrize(
    "field", ["row_count", "size_score", "count", "geo_count", "total_channels"]
)
def test_every_identity_field_compares_exactly(field):
    assert db.compare({field: 100}, {field: 100}) == []
    assert [f.verdict for f in db.compare({field: 100}, {field: 101})] == ["FAIL"]


def test_time_count_is_an_identity_even_though_it_is_nested():
    findings = db.compare({"time": {"count": 52}}, {"time": {"count": 53}})
    assert [f.verdict for f in findings] == ["FAIL"]
    assert findings[0].pointer == "/time/count"


def test_int_to_float_is_a_type_change():
    findings = db.compare({"mean": 4}, {"mean": 4.0})
    assert [f.verdict for f in findings] == ["FAIL"]
    assert "type changed" in findings[0].detail


def test_number_to_null_is_structural_fail():
    assert verdicts({"mean": 1.0}, {"mean": None}) == ["FAIL"]


def test_number_to_string_is_structural_fail():
    assert verdicts({"mean": 1.0}, {"mean": "1.0"}) == ["FAIL"]


def test_error_code_change_is_structural_fail():
    findings = db.compare(
        {"error_code": "metric_not_supported"}, {"error_code": "missing_model_data"}
    )
    assert findings[0].verdict == "FAIL"


def test_valid_becoming_an_error_is_structural_fail():
    assert verdicts({"rows": []}, {"error_code": "boom"}) == ["FAIL", "FAIL"]


def test_numeric_drift_within_tolerance_passes():
    assert db.compare({"mean": 1.0}, {"mean": 1.0 + 5e-4}) == []


def test_numeric_drift_over_tolerance_is_review():
    findings = db.compare({"mean": 1.0}, {"mean": 1.01})
    assert [f.verdict for f in findings] == ["REVIEW"]


def test_absolute_floor_suppresses_tiny_magnitude_noise():
    """1e-12 -> 2e-12 is 100% relative drift and must NOT be reported."""
    assert db.compare({"mean": 1e-12}, {"mean": 2e-12}) == []


def test_sign_flip_above_the_floor_is_review():
    findings = db.compare({"mean": 1e-6}, {"mean": -1e-6})
    assert findings and findings[0].verdict == "REVIEW"
    assert "sign" in findings[0].detail


def test_sign_flip_below_the_floor_is_suppressed():
    """get_model_fit residuals hover at zero and flip sign as float noise; the
    floor exists precisely to stop that blocking a gate."""
    assert db.compare({"residual": 1e-15}, {"residual": -1e-15}) == []


def test_broken_ci_ordering_in_columnar_rows_is_review():
    payload = {
        "columns": ["channel", "ci_lo", "mean", "ci_hi"],
        "rows": [["tv", 3.0, 2.0, 1.0]],
    }
    findings = db.ci_findings(payload)
    assert findings and findings[0].verdict == "REVIEW"


def test_well_ordered_ci_produces_nothing():
    payload = {
        "columns": ["channel", "ci_lo", "mean", "ci_hi"],
        "rows": [["tv", 1.0, 2.0, 3.0]],
    }
    assert db.ci_findings(payload) == []


def test_broken_ci_ordering_in_object_rows_is_review():
    payload = {"allocation": [{"ci_lo": 5.0, "mean": 1.0, "ci_hi": 2.0}]}
    assert db.ci_findings(payload)[0].verdict == "REVIEW"


# The four case-dict keys where backend/meridian_version are structurally
# reachable (see acknowledged.py's "Fix wave 3" docstring section for the
# call-site derivation of each): `submit`/`status` from the first
# submit/status calls, `reused` from a SECOND submit call, and
# `status_after_delete` from a SECOND status call. `None` stands for the
# case root itself, which match() also accepts (per its own documented
# design) even though no real case is ever diffed as a bare root object.
_REAL_ENVELOPE_KEYS = (None, "submit", "status", "reused", "status_after_delete")

# Every OTHER top-level key a capture_baseline.py case dict actually uses,
# where backend/meridian_version never structurally occur: `result` is
# get_result's {run_id, **result} (analysis payload, no backend);
# `listing` is list_optimizations' OptimizationRunSummary entries (no
# backend); `deleted` is delete_optimization's {run_id, deleted} (no
# backend); `canceled` is cancel_optimization's {run_id, status} (no
# backend). A field appearing under any of these is a real, unrelated
# drift and must FAIL -- Fix wave 2 wrongly included all of these by
# enumerating every case-dict key rather than deriving where the field
# actually occurs.
_ENVELOPE_KEYS_WITHOUT_BACKEND = ("result", "listing", "deleted", "canceled")


@pytest.mark.parametrize("envelope_key", _REAL_ENVELOPE_KEYS)
def test_removed_backend_is_acknowledged_at_every_real_pointer_shape(envelope_key):
    """`backend` is a direct key of the dict returned by both
    optimization_service.py's `_submit_envelope` (~line 258) and
    `get_status` (~line 283) -- and those dicts land UNCHANGED one level
    inside the composite case dict capture_baseline.py actually snapshots
    (see _REAL_ENVELOPE_KEYS above), never at a bare payload root. This
    parametrization is what pins the anchor: it FAILS if the match is too
    narrow (e.g. root-only, the fix-wave-1 regression) OR too wide (e.g. the
    original depth-crossing glob)."""
    if envelope_key is None:
        a, b = {"backend": "jax", "run_id": "x"}, {"run_id": "x"}
        pointer = "/backend"
    else:
        a = {envelope_key: {"backend": "jax", "run_id": "x"}}
        b = {envelope_key: {"run_id": "x"}}
        pointer = f"/{envelope_key}/backend"
    findings = db.compare(a, b)
    assert [f.verdict for f in findings] == ["ACKNOWLEDGED"], (envelope_key, findings)
    assert "backend" in findings[0].detail
    assert findings[0].pointer == pointer


@pytest.mark.parametrize("envelope_key", _REAL_ENVELOPE_KEYS)
def test_added_meridian_version_is_acknowledged_at_every_real_pointer_shape(
    envelope_key,
):
    if envelope_key is None:
        a, b = {"run_id": "x"}, {"run_id": "x", "meridian_version": "2.0.0"}
        pointer = "/meridian_version"
    else:
        a = {envelope_key: {"run_id": "x"}}
        b = {envelope_key: {"run_id": "x", "meridian_version": "2.0.0"}}
        pointer = f"/{envelope_key}/meridian_version"
    findings = db.compare(a, b)
    assert [f.verdict for f in findings] == ["ACKNOWLEDGED"], (envelope_key, findings)
    assert findings[0].pointer == pointer


@pytest.mark.parametrize("envelope_key", _ENVELOPE_KEYS_WITHOUT_BACKEND)
def test_backend_removed_under_an_envelope_key_that_never_carries_it_is_not_acknowledged(
    envelope_key,
):
    """Minor 2 (fix wave 3): result/listing/deleted/canceled never
    structurally carry backend -- a `backend` key appearing there anyway is
    a real, unrelated drift (or a hypothetical future field) and must not
    be silently waved through just because it sits under a key some OTHER
    case dict happens to use."""
    a = {envelope_key: {"backend": "jax", "run_id": "x"}}
    b = {envelope_key: {"run_id": "x"}}
    findings = db.compare(a, b)
    assert [f.verdict for f in findings] == ["FAIL"], (envelope_key, findings)
    assert findings[0].pointer == f"/{envelope_key}/backend"


@pytest.mark.parametrize("envelope_key", _ENVELOPE_KEYS_WITHOUT_BACKEND)
def test_meridian_version_added_under_an_envelope_key_that_never_carries_it_is_not_acknowledged(
    envelope_key,
):
    a = {envelope_key: {"run_id": "x"}}
    b = {envelope_key: {"run_id": "x", "meridian_version": "2.0.0"}}
    findings = db.compare(a, b)
    assert [f.verdict for f in findings] == ["FAIL"], (envelope_key, findings)
    assert findings[0].pointer == f"/{envelope_key}/meridian_version"


def test_an_unrelated_added_key_is_still_a_fail():
    findings = db.compare({"a": 1}, {"a": 1, "surprise": 2})
    assert [f.verdict for f in findings] == ["FAIL"]


def test_an_unrelated_key_under_a_known_envelope_key_is_still_a_fail():
    """Being nested under a known envelope key (e.g. `submit`) acknowledges
    ONLY backend/meridian_version -- not anything else that happens to sit
    next to them."""
    findings = db.compare({"submit": {}}, {"submit": {"surprise": 1}})
    assert [f.verdict for f in findings] == ["FAIL"]


def test_backend_key_nested_in_an_error_envelope_is_not_acknowledged():
    """IMPORTANT 3 regression: the original glob (`*/backend`, `fnmatch`'s
    `*` crosses `/`) matched `backend` at ANY depth, including
    domain/errors.py's unrelated `backend` key inside an error envelope's
    `details` (e.g. pointer /details/backend for ModelNotFoundError/
    BackendUnavailableError/AuthenticationFailedError) -- a silent-PASS
    vector for a real drift in a completely different, agent-visible
    contract. `details` is not one of capture_baseline.py's known envelope
    keys, so this must still FAIL."""
    findings = db.compare({"details": {"backend": "jax"}}, {"details": {}})
    assert [f.verdict for f in findings] == ["FAIL"]
    assert findings[0].pointer == "/details/backend"


def test_backend_two_levels_under_a_known_envelope_key_is_not_acknowledged():
    """The match is depth-limited to exactly one level under a known
    envelope key (or the root) -- not an arbitrary-depth walk. This is what
    makes '/error/details/backend'-shaped pointers safe in general, not
    just the one case above."""
    findings = db.compare(
        {"submit": {"nested": {"backend": "jax"}}}, {"submit": {"nested": {}}}
    )
    assert [f.verdict for f in findings] == ["FAIL"]


def test_exit_code_is_one_on_any_fail_or_review():
    assert db.exit_code([db.Finding("PASS", "/a", "")]) == 0
    assert db.exit_code([db.Finding("ACKNOWLEDGED", "/a", "")]) == 0
    assert db.exit_code([db.Finding("REVIEW", "/a", "")]) == 1
    assert db.exit_code([db.Finding("FAIL", "/a", "")]) == 1


def test_manifest_gate_blocks_a_fixture_change_by_default():
    a = {"fixtures": {"m": {"files": {"model.binpb": "aaa"}}}}
    b = {"fixtures": {"m": {"files": {"model.binpb": "bbb"}}}}
    with pytest.raises(SystemExit, match="--allow-fixture-change"):
        db.check_manifests(a, b, allow_fixture_change=False)


def test_manifest_gate_passes_when_explicitly_allowed():
    a = {"fixtures": {"m": {"files": {"model.binpb": "aaa"}}}}
    b = {"fixtures": {"m": {"files": {"model.binpb": "bbb"}}}}
    db.check_manifests(a, b, allow_fixture_change=True)  # must not raise


# --- Context items 4 and 5: known_racy_fields and headline structural
# comparison. These are contracts settled AFTER the plan/brief were written
# and are not covered by the brief's own test list above. ---


def test_known_racy_field_is_review_not_fail():
    findings = db.diff_case(
        {"status": {"status": "canceled"}},
        {"status": {"status": "completed"}},
        "",
        racy_fields=frozenset({"status.status"}),
    )
    assert [f.verdict for f in findings] == ["REVIEW"]
    assert "racy" in findings[0].detail.lower()


def test_known_racy_field_detail_is_not_duplicated_with_the_report_reason_column():
    """The report's REVIEW table has a Reason column that already spells out
    'known racy field' in full (see _review_reason) -- the Finding's own
    detail should carry only a short marker, not repeat the whole
    explanation a second time in the adjacent column."""
    findings = db.diff_case(
        {"status": {"status": "canceled"}},
        {"status": {"status": "completed"}},
        "",
        racy_fields=frozenset({"status.status"}),
    )
    assert findings[0].detail == (
        "value changed: 'canceled' -> 'completed' (known racy field)"
    )


def test_racy_field_declaration_does_not_shield_unrelated_paths():
    findings = db.diff_case(
        {"status": {"status": "canceled"}, "other": "a"},
        {"status": {"status": "canceled"}, "other": "b"},
        "",
        racy_fields=frozenset({"status.status"}),
    )
    assert [f.verdict for f in findings] == ["FAIL"]
    assert findings[0].pointer == "/other"


def test_diff_case_without_racy_fields_behaves_like_compare_plus_ci():
    findings = db.diff_case(
        {"status": {"status": "canceled"}}, {"status": {"status": "completed"}}, ""
    )
    assert [f.verdict for f in findings] == ["FAIL"]


def test_headline_numeric_drift_within_tolerance_passes():
    a = {"headline": "ROAS 1.0 -> 2.0 at budget 100000.0"}
    b = {"headline": "ROAS 1.0000005 -> 2.0 at budget 100000.0"}
    assert db.compare(a, b) == []


def test_headline_numeric_drift_over_tolerance_is_review():
    a = {"headline": "ROAS 1.0 -> 2.0 at budget 100000.0"}
    b = {"headline": "ROAS 1.5 -> 2.0 at budget 100000.0"}
    findings = db.compare(a, b)
    assert [f.verdict for f in findings] == ["REVIEW"]
    assert findings[0].pointer == "/headline"


def test_headline_label_change_is_structural_fail():
    a = {"headline": "ROAS 1.0 -> 2.0 at budget 100000.0"}
    b = {"headline": "CPIK 1.0 -> 2.0 at budget 100000.0"}
    findings = db.compare(a, b)
    assert [f.verdict for f in findings] == ["FAIL"]


def test_headline_is_not_waived_wholesale():
    """headline must never be skipped outright -- it bakes real computed
    floats into a string, and a huge drift there must still surface."""
    a = {"headline": "ROAS 1.0 -> 2.0 at budget 100000.0"}
    b = {"headline": "ROAS 999.0 -> 2.0 at budget 100000.0"}
    findings = db.compare(a, b)
    assert findings and findings[0].verdict == "REVIEW"


def test_headline_unparseable_change_is_structural_fail():
    """Must go through the headline-specific fallback, not generic string
    comparison -- both would report FAIL here, so the discriminating
    assertion pins the EXACT detail text _compare_headline's fallback
    produces (verified by actually deleting the headline dispatch and
    confirming this test then fails, which the generic 'value changed: ...'
    message alone would not catch on the verdict check above)."""
    findings = db.compare({"headline": "weird"}, {"headline": "also weird"})
    assert [f.verdict for f in findings] == ["FAIL"]
    assert findings[0].detail == "headline value changed: 'weird' -> 'also weird'"


def test_headline_differently_formatted_but_numerically_equal_produces_no_findings():
    """Proves headline is actually PARSED and compared numerically, not just
    string-equality-checked: two byte-different reprs of the same float
    (as could arise from a formatting change between library versions) must
    produce no finding. A generic string-equality compare (i.e. headline
    logic removed) would see two different strings and FAIL here, so this
    discriminates -- unlike comparing two literally identical strings, which
    passes trivially with or without any headline-specific code at all."""
    a = {"headline": "ROAS 1.5 -> 2.0 at budget 100000.0"}
    b = {"headline": "ROAS 1.5000000 -> 2.0 at budget 100000.0"}
    assert db.compare(a, b) == []


def test_headline_none_component_becoming_numeric_is_structural_fail():
    a = {"headline": "ROAS None -> None at budget None"}
    b = {"headline": "ROAS 1.0 -> None at budget None"}
    findings = db.compare(a, b)
    assert [f.verdict for f in findings] == ["FAIL"]


def test_headline_sign_flip_at_noise_floor_is_suppressed():
    a = {"headline": "ROAS 1e-15 -> 2.0 at budget 100000.0"}
    b = {"headline": "ROAS -1e-15 -> 2.0 at budget 100000.0"}
    assert db.compare(a, b) == []


# Fix wave 3, Minor 1: headline dispatches on `_leaf(pointer) == "headline"`,
# so it fires at ANY depth, including inside list elements -- the real shape
# is /listing/runs/<N>/headline (see the acknowledged.py-adjacent discovery
# that `headline` is never at get_status's own root, only inside a scoped
# `list_optimizations` listing). Nothing previously asserted this: a future
# refactor that anchored headline handling to a fixed depth (e.g. only
# `/headline` or only index 0) would pass all other tests. N > 0 is
# included specifically so a hard-coded index cannot pass either.


def _listing_with_headline(value: str, *, index: int) -> dict:
    runs = [{"run_id": f"r{i}"} for i in range(index)]
    runs.append({"run_id": f"r{index}", "headline": value})
    return {"listing": {"runs": runs, "count": index + 1}}


@pytest.mark.parametrize("index", [0, 1, 3])
def test_headline_over_tolerance_at_any_list_index_in_a_real_listing(index):
    a = _listing_with_headline("ROAS 1.5 -> 2.0 at budget 100000.0", index=index)
    b = _listing_with_headline("ROAS 1.9 -> 2.0 at budget 100000.0", index=index)
    findings = db.compare(a, b)
    assert [f.verdict for f in findings] == ["REVIEW"], (index, findings)
    assert findings[0].pointer == f"/listing/runs/{index}/headline"


@pytest.mark.parametrize("index", [0, 2])
def test_headline_label_change_at_any_list_index_in_a_real_listing(index):
    a = _listing_with_headline("ROAS 1.5 -> 2.0 at budget 100000.0", index=index)
    b = _listing_with_headline("CPIK 1.5 -> 2.0 at budget 100000.0", index=index)
    findings = db.compare(a, b)
    assert [f.verdict for f in findings] == ["FAIL"], (index, findings)
    assert findings[0].pointer == f"/listing/runs/{index}/headline"
    assert "label changed" in findings[0].detail


# --- Fix wave: adversarial review findings. ---
#
# CRITICAL 1: _load_snapshot silently returned None for a non-envelope file,
# so compare(None, None) == [] and two bare-payload (pre-Task-6) snapshots
# with a real difference diffed clean at exit 0.
#
# CRITICAL 2: NaN broke _compare_numbers in both directions (an unchanged
# NaN read as "relative delta nan" REVIEW; a value becoming NaN read as
# ordinary float noise), and infinity was unhandled entirely.


def test_nan_vs_nan_is_unchanged():
    assert db.compare({"residual": math.nan}, {"residual": math.nan}) == []


def test_identity_field_holding_nan_on_both_sides_is_unchanged():
    """Fix wave 2 Minor: the identity-field exact-compare branch ran BEFORE
    NaN handling, so `nan != nan` (True, by IEEE unordered comparison)
    reported a nonsensical 'identity value changed: nan -> nan' for a value
    that, by this module's own NaN-equality convention, did not change.
    An identity should never legitimately be NaN, but the report must not
    lie about it either way."""
    assert db.compare({"row_count": math.nan}, {"row_count": math.nan}) == []


def test_identity_field_becoming_nan_is_still_a_fail():
    # Both sides float so the numeric-type-change branch doesn't fire first
    # and this actually exercises the identity-field NaN check itself.
    findings = db.compare({"row_count": 5.0}, {"row_count": math.nan})
    assert [f.verdict for f in findings] == ["FAIL"]
    assert "identity" in findings[0].detail


def test_number_becoming_nan_is_structural_fail():
    findings = db.compare({"residual": 1.0}, {"residual": math.nan})
    assert [f.verdict for f in findings] == ["FAIL"]
    assert "nan" in findings[0].detail.lower()


def test_nan_becoming_a_number_is_structural_fail():
    findings = db.compare({"residual": math.nan}, {"residual": 1.0})
    assert [f.verdict for f in findings] == ["FAIL"]
    assert "nan" in findings[0].detail.lower()


def test_same_infinity_is_unchanged():
    assert db.compare({"mean": math.inf}, {"mean": math.inf}) == []
    assert db.compare({"mean": -math.inf}, {"mean": -math.inf}) == []


def test_infinity_vs_finite_is_structural_fail():
    findings = db.compare({"mean": math.inf}, {"mean": 1.0})
    assert [f.verdict for f in findings] == ["FAIL"]


def test_infinity_flips_sign_is_structural_fail():
    findings = db.compare({"mean": math.inf}, {"mean": -math.inf})
    assert [f.verdict for f in findings] == ["FAIL"]


def test_no_finding_ever_reports_a_nan_relative_delta():
    """Regression for the 'hundreds of false findings nobody reads' failure
    mode: an unchanged NaN must never surface as 'relative delta nan'."""
    findings = db.compare({"residual": math.nan}, {"residual": math.nan})
    findings += db.compare({"a": math.nan}, {"a": 1.0})
    findings += db.compare({"a": 1.0}, {"a": math.nan})
    assert all("relative delta nan" not in f.detail for f in findings)


# --- IMPORTANT 4: malformed known_racy_fields must fail loudly, never be
# silently ignored (a string degrading to per-character pointers) or crash
# with a bare traceback (None/int). CRITICAL 1: a non-envelope snapshot file
# must fail loudly too, never silently diff as an empty payload. ---


def _write_envelope(path, payload, *, known_racy_fields=None, capture_env=None):
    envelope = {
        "capture_env": capture_env if capture_env is not None else {},
        "payload": payload,
    }
    if known_racy_fields is not None:
        envelope["known_racy_fields"] = known_racy_fields
    path.write_text(json.dumps(envelope))


def test_load_snapshot_round_trips_a_valid_envelope(tmp_path):
    path = tmp_path / "case.json"
    _write_envelope(
        path, {"mean": 1.0}, known_racy_fields=["a.b"], capture_env={"python": "3.12.8"}
    )
    payload, capture_env, racy = db._load_snapshot(path)
    assert payload == {"mean": 1.0}
    assert racy == ["a.b"]
    assert capture_env == {"python": "3.12.8"}


def test_load_snapshot_defaults_racy_fields_and_capture_env_when_absent(tmp_path):
    path = tmp_path / "case.json"
    path.write_text(json.dumps({"payload": {"mean": 1.0}}))
    payload, capture_env, racy = db._load_snapshot(path)
    assert payload == {"mean": 1.0}
    assert racy == []
    assert capture_env == {}


def test_load_snapshot_rejects_a_bare_payload_file(tmp_path):
    """CRITICAL 1 regression: a pre-Task-6 bare-payload file (no envelope)
    must fail loudly, not silently diff as an empty payload."""
    path = tmp_path / "case.json"
    path.write_text(json.dumps({"row_count": 2185}))
    with pytest.raises(SystemExit, match="payload"):
        db._load_snapshot(path)


def test_load_snapshot_rejects_a_non_dict_json_file(tmp_path):
    path = tmp_path / "case.json"
    path.write_text(json.dumps([1, 2, 3]))
    with pytest.raises(SystemExit, match="payload"):
        db._load_snapshot(path)


@pytest.mark.parametrize(
    "bad_racy", ["status.status", {"status.status": True}, 5, None]
)
def test_load_snapshot_rejects_malformed_known_racy_fields(tmp_path, bad_racy):
    path = tmp_path / "case.json"
    path.write_text(json.dumps({"payload": {"a": 1}, "known_racy_fields": bad_racy}))
    with pytest.raises(SystemExit, match="known_racy_fields"):
        db._load_snapshot(path)


def test_load_snapshot_rejects_a_list_containing_a_non_string(tmp_path):
    path = tmp_path / "case.json"
    path.write_text(json.dumps({"payload": {"a": 1}, "known_racy_fields": ["ok", 5]}))
    with pytest.raises(SystemExit, match="known_racy_fields"):
        db._load_snapshot(path)


def test_load_case_files_reads_the_envelope_and_skips_the_manifest(tmp_path):
    variant_dir = tmp_path / "label" / "variant"
    variant_dir.mkdir(parents=True)
    _write_envelope(variant_dir / "case_a.json", {"mean": 1.0})
    (tmp_path / "label" / "manifest.json").write_text(json.dumps({"label": "label"}))
    cases = db._load_case_files(tmp_path, "label")
    assert set(cases) == {"variant/case_a.json"}
    payload, _capture_env, racy = cases["variant/case_a.json"]
    assert payload == {"mean": 1.0}
    assert racy == []


# --- IMPORTANT 5: no test touched the loader or the CLI -- exactly the code
# rewritten against the Task 7 context items, and exactly why both Criticals
# shipped. These are end-to-end, through main(), against real files on disk.


def _write_manifest(label_dir, label, *, fixture_hash="same-hash"):
    manifest = {
        "label": label,
        "transport": "stdio",
        "python": "3.12.8",
        "packages": {},
        "worker_env": {},
        "fixtures": {"m": {"files": {"model.binpb": fixture_hash}}},
    }
    label_dir.mkdir(parents=True, exist_ok=True)
    (label_dir / "manifest.json").write_text(json.dumps(manifest))


def test_main_end_to_end_rejects_bare_payload_snapshots_loudly(tmp_path):
    """THE Critical 1 probe: two bare-payload (non-envelope) files with a
    real difference (row_count 2185 -> 9999) must not exit 0 -- they must
    fail loudly instead of silently diffing clean."""
    root = tmp_path
    for label in ("v-a", "v-b"):
        (root / label / "v").mkdir(parents=True)
        _write_manifest(root / label, label)
    (root / "v-a" / "v" / "case.json").write_text(json.dumps({"row_count": 2185}))
    (root / "v-b" / "v" / "case.json").write_text(json.dumps({"row_count": 9999}))
    with pytest.raises(SystemExit, match="payload"):
        db.main(["v-a", "v-b", "--root", str(root)])


def test_main_end_to_end_real_difference_exits_nonzero(tmp_path, capsys):
    root = tmp_path
    for label in ("v-a", "v-b"):
        (root / label / "v").mkdir(parents=True)
        _write_manifest(root / label, label)
    _write_envelope(root / "v-a" / "v" / "case.json", {"row_count": 2185})
    _write_envelope(root / "v-b" / "v" / "case.json", {"row_count": 9999})
    code = db.main(["v-a", "v-b", "--root", str(root)])
    assert code == 1
    out = capsys.readouterr().out
    assert "identity value changed" in out


def test_main_end_to_end_clean_diff_exits_zero(tmp_path):
    root = tmp_path
    for label in ("v-a", "v-b"):
        (root / label / "v").mkdir(parents=True)
        _write_manifest(root / label, label)
    _write_envelope(root / "v-a" / "v" / "case.json", {"row_count": 2185})
    _write_envelope(root / "v-b" / "v" / "case.json", {"row_count": 2185})
    assert db.main(["v-a", "v-b", "--root", str(root)]) == 0


# --- MINORS ---


def test_key_containing_a_slash_fails_loudly_instead_of_corrupting_pointers():
    """A channel named e.g. 'Search/Brand' is plausible input, not a
    programming error -- SystemExit with a clean message, not a bare
    ValueError/traceback, matching every other user-facing abort here."""
    with pytest.raises(SystemExit, match="/"):
        db.compare({"a/count": 1}, {"a/count": 2})


def test_racy_fields_is_keyword_only_matching_its_documentation():
    with pytest.raises(TypeError):
        db.diff_case({}, {}, "", frozenset())  # type: ignore[misc]


# --- Report legibility ---


def _base_manifest():
    return {
        "transport": "stdio",
        "python": "3.12.8",
        "packages": {},
        "worker_env": {},
        "fixtures": {},
    }


def test_report_top_line_shows_verdict_and_exit_code():
    findings_by_case = {"case_fail": [db.Finding("FAIL", "/mean", "value changed")]}
    manifest = _base_manifest()
    missing = {"only_in_a": [], "only_in_b": []}
    report = db.render_report("a", "b", findings_by_case, manifest, manifest, missing)
    content_lines = [line for line in report.splitlines() if line.strip()]
    assert "Verdict" in content_lines[1]
    assert "exit code 1" in content_lines[1]


def test_report_summary_counts_include_case_set_differences():
    """A case present in only one label must be counted in the Summary's
    FAIL total, so the numbers can never contradict a nonzero exit code."""
    findings_by_case = {"clean": []}
    manifest = _base_manifest()
    missing = {"only_in_a": ["only_a_case"], "only_in_b": []}
    report = db.render_report("a", "b", findings_by_case, manifest, manifest, missing)
    assert "Verdict: FAIL -- exit code 1" in report
    summary = report.split("## Summary")[1].split("##")[0]
    assert "FAIL (including case-set differences above): 1" in summary


def test_report_summary_shows_how_many_compared_cases_were_clean():
    """A reader must be able to tell which compared cases were clean versus
    merely absent from every FAIL/REVIEW/ACKNOWLEDGED section -- '9
    compared, 2 clean' must be visible, not just inferable by counting."""
    findings_by_case = {
        "clean_one": [],
        "clean_two": [],
        "has_a_finding": [db.Finding("FAIL", "/x", "value changed")],
    }
    manifest = _base_manifest()
    missing = {"only_in_a": [], "only_in_b": []}
    report = db.render_report("a", "b", findings_by_case, manifest, manifest, missing)
    summary = report.split("## Summary")[1].split("##")[0]
    assert "3" in summary and "2 clean" in summary


def test_report_review_section_has_a_reason_column_and_is_not_called_numeric():
    findings_by_case = {
        "lifecycle__cancel": [
            db.Finding(
                "REVIEW",
                "/status/status",
                "value changed: 'canceled' -> 'completed' -- known racy field "
                "(declared in known_racy_fields; inherent race, not a defect)",
            )
        ]
    }
    manifest = _base_manifest()
    missing = {"only_in_a": [], "only_in_b": []}
    report = db.render_report("a", "b", findings_by_case, manifest, manifest, missing)
    assert "REVIEW -- needs a human" in report
    assert "REVIEW - numeric" not in report
    assert "known racy field" in report


def test_report_prints_the_tolerance_constants():
    manifest = _base_manifest()
    missing = {"only_in_a": [], "only_in_b": []}
    report = db.render_report("a", "b", {}, manifest, manifest, missing)
    assert f"{db.REL_TOLERANCE:.0e}" in report
    assert f"{db.ABS_FLOOR:.0e}" in report


def test_report_has_a_legend_explaining_what_blocks():
    manifest = _base_manifest()
    missing = {"only_in_a": [], "only_in_b": []}
    report = db.render_report("a", "b", {}, manifest, manifest, missing)
    assert "## Legend" in report
    assert "block" in report.lower()


def test_report_environment_table_prints_the_corrected_probe_backend():
    """Final-branch-review Important #4: manifest.py writes `probe_backend`
    (the CORRECTED backend the fixture probe actually ran under) but nothing
    ever read it back out, so `02-backend-precision.md` printed the frozen,
    possibly-wrong `worker MERIDIAN_BACKEND` row (`None`) right next to a
    Fixture-provenance section reporting `TENSORFLOW` for the same label --
    two contradictory, unlabelled answers in one report.

    Reproduces that exact shape: label `a` never exported MERIDIAN_BACKEND
    (worker_env value None) but its probe was still forced onto tensorflow;
    label `b`'s worker_env genuinely says jax. The report must print BOTH
    manifests' `probe_backend` values distinguishably, not silently drop the
    corrected one.
    """
    manifest_a = _base_manifest()
    manifest_a["worker_env"] = {"MERIDIAN_BACKEND": None}
    manifest_a["probe_backend"] = "tensorflow"
    manifest_b = _base_manifest()
    manifest_b["worker_env"] = {"MERIDIAN_BACKEND": "jax"}
    manifest_b["probe_backend"] = "jax"
    missing = {"only_in_a": [], "only_in_b": []}
    report = db.render_report("a", "b", {}, manifest_a, manifest_b, missing)
    env_section = report.split("## Environment")[1].split("## Fixture provenance")[0]
    assert "| probe backend" in env_section
    assert "| tensorflow | jax |" in env_section
    assert "frozen" in env_section.lower()
    assert "corrected" in env_section.lower()


def test_report_collapses_identical_per_case_provenance_lines_to_one():
    findings_by_case = {
        "case1": [db.Finding("FAIL", "/x", "value changed")],
        "case2": [db.Finding("FAIL", "/y", "value changed")],
    }
    manifest = _base_manifest()
    missing = {"only_in_a": [], "only_in_b": []}
    case_envs = {
        "case1": ({"src_hash": "aaa"}, {"src_hash": "bbb"}),
        "case2": ({"src_hash": "aaa"}, {"src_hash": "bbb"}),
    }
    report = db.render_report(
        "a", "b", findings_by_case, manifest, manifest, missing, case_envs
    )
    assert report.count("src_hash") == 1


def test_report_shows_per_case_provenance_when_it_actually_varies():
    findings_by_case = {
        "case1": [db.Finding("FAIL", "/x", "value changed")],
        "case2": [db.Finding("FAIL", "/y", "value changed")],
    }
    manifest = _base_manifest()
    missing = {"only_in_a": [], "only_in_b": []}
    case_envs = {
        "case1": ({"src_hash": "aaa"}, {"src_hash": "bbb"}),
        "case2": ({"src_hash": "aaa"}, {"src_hash": "ccc"}),
    }
    report = db.render_report(
        "a", "b", findings_by_case, manifest, manifest, missing, case_envs
    )
    assert report.count("src_hash") >= 2

"""Every classification rule in the drift differ."""

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


def test_removed_backend_is_acknowledged_not_failed():
    findings = db.compare({"submit": {"backend": "jax"}}, {"submit": {}})
    assert [f.verdict for f in findings] == ["ACKNOWLEDGED"]
    assert "backend" in findings[0].detail


def test_added_meridian_version_is_acknowledged_not_failed():
    findings = db.compare({"submit": {}}, {"submit": {"meridian_version": "2.0.0"}})
    assert [f.verdict for f in findings] == ["ACKNOWLEDGED"]


def test_an_unrelated_added_key_is_still_a_fail():
    findings = db.compare({"submit": {}}, {"submit": {"surprise": 1}})
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
    findings = db.compare({"headline": "weird"}, {"headline": "also weird"})
    assert [f.verdict for f in findings] == ["FAIL"]


def test_headline_identical_strings_produce_no_findings():
    a = {"headline": "ROAS 1.0 -> 2.0 at budget 100000.0"}
    assert db.compare(a, dict(a)) == []


def test_headline_none_component_becoming_numeric_is_structural_fail():
    a = {"headline": "ROAS None -> None at budget None"}
    b = {"headline": "ROAS 1.0 -> None at budget None"}
    findings = db.compare(a, b)
    assert [f.verdict for f in findings] == ["FAIL"]


def test_headline_sign_flip_at_noise_floor_is_suppressed():
    a = {"headline": "ROAS 1e-15 -> 2.0 at budget 100000.0"}
    b = {"headline": "ROAS -1e-15 -> 2.0 at budget 100000.0"}
    assert db.compare(a, b) == []

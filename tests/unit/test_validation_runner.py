import pytest

from scripts.validation import runner


def test_assert_columnar_accepts_valid_payload():
    payload = {"model_id": "m", "columns": ["a"], "rows": [[1]], "row_count": 1}
    runner.assert_columnar(payload, "ok")  # no raise


def test_assert_columnar_rejects_legacy_keys():
    payload = {"model_id": "m", "columns": [], "rows": [], "row_count": 0, "data": []}
    with pytest.raises(AssertionError):
        runner.assert_columnar(payload, "legacy")


def test_assert_columnar_rejects_ragged_rows():
    payload = {"model_id": "m", "columns": ["a", "b"], "rows": [[1]], "row_count": 1}
    with pytest.raises(AssertionError):
        runner.assert_columnar(payload, "ragged")


def test_assert_error_matches_code():
    runner.assert_error({"error_code": "metric_not_supported"}, "metric_not_supported", "e")
    with pytest.raises(AssertionError):
        runner.assert_error({"error_code": "other"}, "metric_not_supported", "e")

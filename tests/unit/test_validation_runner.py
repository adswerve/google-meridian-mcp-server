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
    runner.assert_error(
        {"error_code": "metric_not_supported"}, "metric_not_supported", "e"
    )
    with pytest.raises(AssertionError):
        runner.assert_error({"error_code": "other"}, "metric_not_supported", "e")


def test_runner_close_and_rows_helpers():
    payload = {"columns": ["channel", "v"], "rows": [["A", 1.0], ["B", 2.0]]}
    assert runner._rows(payload) == [
        {"channel": "A", "v": 1.0},
        {"channel": "B", "v": 2.0},
    ]
    assert runner._close(100.0, 100.0005)  # within 6-sig-fig rounding
    assert not runner._close(100.0, 100.1)


def test_direct_plus_indirect_check_rejects_a_split_that_does_not_sum():
    good = {
        "channel_tables": {
            "optimized": [
                {
                    "incremental_outcome": 10.0,
                    "incremental_outcome_direct": 6.0,
                    "incremental_outcome_indirect": 4.0,
                }
            ]
        }
    }
    runner._assert_direct_plus_indirect(good, "good")
    bad = {
        "channel_tables": {
            "optimized": [
                {
                    "incremental_outcome": 10.0,
                    "incremental_outcome_direct": 6.0,
                    "incremental_outcome_indirect": 5.0,
                }
            ]
        }
    }
    with pytest.raises(AssertionError):
        runner._assert_direct_plus_indirect(bad, "bad")
    with pytest.raises(AssertionError):
        runner._assert_direct_plus_indirect({"channel_tables": {"optimized": []}}, "e")

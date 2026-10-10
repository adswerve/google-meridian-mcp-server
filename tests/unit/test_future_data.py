from datetime import date, timedelta

import numpy as np
import pytest

from google_meridian_mcp_server.meridian import future_data as fd

WEEKLY = [
    f"2025-{m:02d}-{d:02d}"
    for (m, d) in [(1, 6), (1, 13), (1, 20), (1, 27), (2, 3), (2, 10)]
]  # 7-day cadence


def test_infer_cadence_days_weekly():
    assert fd.infer_cadence_days(WEEKLY) == 7


def test_future_time_labels_steps_cadence():
    labels = fd.future_time_labels(date(2026, 10, 1), 3, 7)
    assert labels == ["2026-10-01", "2026-10-08", "2026-10-15"]


def test_reference_indices_trailing_takes_last_horizon():
    times = [f"2025-01-{d:02d}" for d in range(1, 11)]  # 10 daily points
    assert fd.reference_indices("trailing", 3, date(2025, 2, 1), times, 1) == [7, 8, 9]


def test_reference_indices_full_history_returns_all():
    times = [f"2025-01-{d:02d}" for d in range(1, 6)]
    assert fd.reference_indices(
        "full_history_average", 2, date(2025, 2, 1), times, 1
    ) == [0, 1, 2, 3, 4]


def test_reference_indices_same_period_last_year_cadence_aware():
    # weekly, exactly 52 periods ending just before start; cadence-aware year = 52 wk back
    times = [
        np.datetime_as_string(
            np.datetime64("2025-10-06") + np.timedelta64(7 * i, "D"), unit="D"
        )
        for i in range(52)
    ]
    start = date.fromisoformat(times[-1]) + timedelta(days=7)
    idx = fd.reference_indices("same_period_last_year", 3, start, times, 7)
    assert len(idx) == 3
    assert idx[0] == 0  # 52 weeks back from the next period == first label


def _weekly_104() -> list[str]:
    # 2024-01-01 .. 2025-12-22 weekly; the data covers through 2025-12-28.
    return [
        str(np.datetime64("2024-01-01") + np.timedelta64(7 * i, "D"))
        for i in range(104)
    ]


def test_reference_indices_same_period_last_year_insufficient_history():
    times = [f"2026-09-{d:02d}" for d in range(1, 8)]  # < 1 year before start
    with pytest.raises(ValueError) as exc:
        fd.reference_indices("same_period_last_year", 3, date(2026, 10, 1), times, 1)
    msg = str(exc.value)
    assert "needs data for 2025-10-01 to 2025-10-03" in msg
    assert "the planned 2026-10-01 to 2026-10-03" in msg
    assert "data covers 2026-09-01 to 2026-09-07" in msg


def test_reference_indices_same_period_last_year_past_data_end_horizon_1():
    # One year before 2027-03-01 is 2026-03-02, ten weeks after the data ends.
    # Before the fix this silently returned [103], the model's final week.
    with pytest.raises(ValueError) as exc:
        fd.reference_indices(
            "same_period_last_year", 1, date(2027, 3, 1), _weekly_104(), 7
        )
    msg = str(exc.value)
    assert "needs data for 2026-03-02 to 2026-03-08" in msg
    assert "data covers 2024-01-01 to 2025-12-28" in msg


def test_reference_indices_same_period_last_year_past_data_end_names_window():
    with pytest.raises(ValueError) as exc:
        fd.reference_indices(
            "same_period_last_year", 4, date(2027, 3, 1), _weekly_104(), 7
        )
    msg = str(exc.value)
    assert "needs data for 2026-03-02 to 2026-03-29" in msg
    assert "the planned 2027-03-01 to 2027-03-28" in msg
    assert "data covers 2024-01-01 to 2025-12-28" in msg


def test_reference_indices_same_period_last_year_target_inside_last_period():
    # Target 2025-12-25 is off-grid but inside the last period (2025-12-22..28),
    # so it snaps to that period. Guards against a `target > times[-1]` off-by-one.
    assert fd.reference_indices(
        "same_period_last_year", 1, date(2026, 12, 24), _weekly_104(), 7
    ) == [103]


def test_reference_indices_same_period_last_year_target_one_period_past_end():
    # Target 2025-12-29 is the first day after the data ends.
    with pytest.raises(ValueError) as exc:
        fd.reference_indices(
            "same_period_last_year", 1, date(2026, 12, 28), _weekly_104(), 7
        )
    assert "needs data for 2025-12-29 to 2026-01-04" in str(exc.value)


def test_reference_indices_same_period_last_year_window_runs_past_end():
    # Starts inside the last period, but a 2-period window spills past the data.
    with pytest.raises(ValueError) as exc:
        fd.reference_indices(
            "same_period_last_year", 2, date(2026, 12, 24), _weekly_104(), 7
        )
    msg = str(exc.value)
    assert "needs data for 2025-12-25 to 2026-01-07" in msg
    assert "data covers 2024-01-01 to 2025-12-28" in msg


def test_normalize_planned_allocation_keeps_shares_and_splits_rest_by_spend():
    # carried is reference-window SPEND in currency, as the facade passes it.
    carried = {"tv": 500_000.0, "search": 300_000.0, "social": 100_000.0}
    out = fd.normalize_planned_allocation(
        {"tv": 0.4}, carried, ["tv", "search", "social"]
    )
    assert out == pytest.approx([0.4, 0.6 * 3 / 4, 0.6 * 1 / 4])


def test_normalize_planned_allocation_none_passthrough():
    assert fd.normalize_planned_allocation(None, {"tv": 1.0}, ["tv"]) is None


def test_normalize_planned_allocation_unknown_channel_raises():
    with pytest.raises(ValueError):
        fd.normalize_planned_allocation({"nope": 1.0}, {"tv": 1.0}, ["tv"])


def test_apply_cost_multipliers_scales_named_channel_ignores_others():
    # Called per-subset with the full dict: RF-keyed entries are ignored for the media list.
    cpmu = np.array([2.0, 4.0])
    out = fd.apply_cost_multipliers(
        cpmu, {"search": 1.5, "yt_rf": 2.0}, ["tv", "search"]
    )
    assert out.tolist() == [2.0, 6.0]


def test_validate_channel_keys_raises_on_unknown():
    with pytest.raises(ValueError):
        fd.validate_channel_keys({"nope": 2.0}, ["tv", "search"])


def test_validate_channel_keys_accepts_union_membership():
    fd.validate_channel_keys(
        {"tv": 1.2, "yt_rf": 0.9}, ["tv", "search", "yt_rf"]
    )  # no raise


def test_resolve_budget_prefers_explicit_then_seeded_then_none():
    assert fd.resolve_budget(1000.0, True, 800.0) == 1000.0
    assert fd.resolve_budget(None, True, 800.0) == 800.0
    assert fd.resolve_budget(None, False, 800.0) is None


ORDER = ["ch_0", "ch_1", "ch_2", "rf_ch_0", "rf_ch_1"]


def test_validate_excluded_channels_ok():
    fd.validate_excluded_channels(["ch_0"], None, None, ORDER)  # no raise


def test_validate_excluded_channels_none_is_noop():
    fd.validate_excluded_channels(None, {"ch_0": 0.5}, {"ch_0": 1.2}, ORDER)


def test_validate_excluded_channels_unknown():
    with pytest.raises(ValueError, match="unknown"):
        fd.validate_excluded_channels(["nope"], None, None, ORDER)


def test_validate_excluded_channels_overlap_planned():
    with pytest.raises(ValueError, match="planned_allocation"):
        fd.validate_excluded_channels(["ch_0"], {"ch_0": 0.4}, None, ORDER)


def test_validate_excluded_channels_overlap_cost():
    with pytest.raises(ValueError, match="cost_multipliers"):
        fd.validate_excluded_channels(["ch_1"], None, {"ch_1": 1.5}, ORDER)


def test_validate_excluded_channels_all_excluded():
    with pytest.raises(ValueError, match="every channel"):
        fd.validate_excluded_channels(list(ORDER), None, None, ORDER)


def test_apply_exclusions_none_returns_inputs_as_lists():
    pct = [0.2, 0.2, 0.2, 0.2, 0.2]
    new_pct, lo, hi = fd.apply_exclusions(pct, 0.3, 0.3, None, ORDER)
    assert new_pct == pct
    assert lo == [0.3] * 5 and hi == [0.3] * 5


def test_apply_exclusions_zeroes_and_renormalizes_global_bounds():
    pct = [0.2, 0.2, 0.2, 0.2, 0.2]
    new_pct, lo, hi = fd.apply_exclusions(pct, 0.3, 0.3, ["ch_0"], ORDER)
    assert new_pct[0] == 0.0
    assert lo[0] == 0.0 and hi[0] == 0.0
    assert abs(sum(new_pct) - 1.0) < 1e-9
    assert all(abs(w - 0.25) < 1e-9 for w in new_pct[1:])  # 0.2/0.8
    assert lo[1:] == [0.3] * 4 and hi[1:] == [0.3] * 4


def test_apply_exclusions_per_channel_bounds_list():
    pct = [0.2, 0.2, 0.2, 0.2, 0.2]
    lower = [0.1, 0.2, 0.3, 0.4, 0.5]
    upper = [0.5, 0.4, 0.3, 0.2, 0.1]
    new_pct, lo, hi = fd.apply_exclusions(pct, lower, upper, ["ch_2"], ORDER)
    assert new_pct[2] == 0.0 and lo[2] == 0.0 and hi[2] == 0.0
    assert lo[0] == 0.1 and hi[0] == 0.5  # untouched


def test_apply_exclusions_all_zero_raises():
    with pytest.raises(ValueError, match="zero baseline"):
        fd.apply_exclusions([1.0, 0.0], 0.3, 0.3, ["a"], ["a", "b"])

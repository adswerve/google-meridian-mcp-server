"""get_funnel_breakdown row builders on the real two-mediator fixture."""

from __future__ import annotations

from datetime import date

import pytest

from google_meridian_mcp_server.domain.filters import AnalysisFilters

pytestmark = pytest.mark.integration

M1_LABEL = "M1 (brand equity, rest)"
M2_LABEL = "M2 (brand equity, rest)"


def _keys(rows):
    return [(r["channel"], r["component"], r["mediator"]) for r in rows]


def test_channel_breakdown_lists_every_channel_component_in_order(ff_facade):
    rows = ff_facade.get_funnel_channel_breakdown(AnalysisFilters())
    assert _keys(rows) == [
        ("A", "direct", None),
        ("A", "indirect", "M1"),
        ("A", "indirect", "M2"),
        ("B", "direct", None),
        ("B", "indirect", "M1"),
        ("B", "indirect", "M2"),
        ("C", "direct", None),
        ("C", "indirect", "M1"),
        ("C", "indirect", "M2"),
        (M1_LABEL, "brand_equity_rest", "M1"),
        (M2_LABEL, "brand_equity_rest", "M2"),
    ]


def test_channel_breakdown_components_sum_to_channel_total(ff_facade):
    rows = ff_facade.get_funnel_channel_breakdown(AnalysisFilters())
    total = ff_facade._funnel_split(AnalysisFilters(), aggregate_times=True).total
    for channel in ("A", "B", "C"):
        own = [r for r in rows if r["channel"] == channel]
        assert sum(r["incremental_outcome"] for r in own) == pytest.approx(
            float(total[channel]), rel=1e-6
        )
        if float(total[channel]):
            assert sum(r["share_of_channel_total"] for r in own) == pytest.approx(
                1.0, rel=1e-6
            )
    rest = [r for r in rows if r["component"] == "brand_equity_rest"]
    assert all(r["share_of_channel_total"] is None for r in rest)


def test_non_driving_channel_has_zero_indirect_rows(ff_facade):
    rows = ff_facade.get_funnel_channel_breakdown(AnalysisFilters())
    # C drives neither mediator; B drives M2 only.
    for channel, mediator in (("C", "M1"), ("C", "M2"), ("B", "M1")):
        (row,) = [
            r
            for r in rows
            if (r["channel"], r["component"], r["mediator"])
            == (channel, "indirect", mediator)
        ]
        assert row["incremental_outcome"] == 0.0
        assert row["share_of_channel_total"] == 0.0
    (driving,) = [
        r
        for r in rows
        if (r["channel"], r["component"], r["mediator"]) == ("A", "indirect", "M1")
    ]
    assert driving["incremental_outcome"] != 0.0


def test_channel_filter_by_mediator_keeps_its_rows_only(ff_facade):
    rows = ff_facade.get_funnel_channel_breakdown(AnalysisFilters(channels=["M2"]))
    assert _keys(rows) == [
        ("A", "indirect", "M2"),
        ("B", "indirect", "M2"),
        ("C", "indirect", "M2"),
        (M2_LABEL, "brand_equity_rest", "M2"),
    ]


def test_channel_filter_by_paid_channel_keeps_its_rows_only(ff_facade):
    rows = ff_facade.get_funnel_channel_breakdown(AnalysisFilters(channels=["B"]))
    assert _keys(rows) == [
        ("B", "direct", None),
        ("B", "indirect", "M1"),
        ("B", "indirect", "M2"),
    ]


def test_mixed_channel_filter_is_the_union(ff_facade):
    rows = ff_facade.get_funnel_channel_breakdown(AnalysisFilters(channels=["C", "M1"]))
    assert _keys(rows) == [
        ("A", "indirect", "M1"),
        ("B", "indirect", "M1"),
        ("C", "direct", None),
        ("C", "indirect", "M1"),
        ("C", "indirect", "M2"),
        (M1_LABEL, "brand_equity_rest", "M1"),
    ]


def test_channel_breakdown_honours_window_and_use_kpi(ff_facade):
    times = ff_facade.get_time_values()
    windowed = AnalysisFilters(
        start_date=date.fromisoformat(times[-4][:10]),
        end_date=date.fromisoformat(times[-1][:10]),
        use_kpi=True,
    )
    full = ff_facade.get_funnel_channel_breakdown(AnalysisFilters(use_kpi=True))
    rows = ff_facade.get_funnel_channel_breakdown(windowed)
    assert _keys(rows) == _keys(full)
    split = ff_facade._funnel_split(windowed, aggregate_times=True)
    direct_a = next(
        r for r in rows if (r["channel"], r["component"]) == ("A", "direct")
    )
    assert direct_a["incremental_outcome"] == pytest.approx(
        float(split.direct["A"]), rel=1e-9
    )
    full_a = next(r for r in full if (r["channel"], r["component"]) == ("A", "direct"))
    assert direct_a["incremental_outcome"] != pytest.approx(
        full_a["incremental_outcome"], rel=1e-6
    )


def test_mediator_lift_is_in_native_units_with_intervals(ff_facade):
    rows = ff_facade.get_funnel_mediator_lift(AnalysisFilters())
    assert [(r["mediator"], r["channel"]) for r in rows] == [
        ("M1", "A"),
        ("M2", "A"),
        ("M2", "B"),
    ]
    for r in rows:
        assert (
            r["incremental_units_ci_lo"]
            <= r["incremental_units"]
            <= r["incremental_units_ci_hi"]
        )
        assert r["spend"] > 0 and r["cost_per_incremental_unit"] > 0


def test_mediator_lift_channel_filters(ff_facade):
    by_mediator = ff_facade.get_funnel_mediator_lift(AnalysisFilters(channels=["M2"]))
    assert [(r["mediator"], r["channel"]) for r in by_mediator] == [
        ("M2", "A"),
        ("M2", "B"),
    ]
    by_channel = ff_facade.get_funnel_mediator_lift(AnalysisFilters(channels=["B"]))
    assert [(r["mediator"], r["channel"]) for r in by_channel] == [("M2", "B")]
    mixed = ff_facade.get_funnel_mediator_lift(AnalysisFilters(channels=["B", "M1"]))
    assert [(r["mediator"], r["channel"]) for r in mixed] == [
        ("M1", "A"),
        ("M2", "B"),
    ]
    assert ff_facade.get_funnel_mediator_lift(AnalysisFilters(channels=["C"])) == []

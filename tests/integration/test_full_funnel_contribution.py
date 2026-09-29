"""get_contribution on the real two-mediator fixture reconciles with Meridian."""

from __future__ import annotations

from collections import defaultdict
from datetime import date

import numpy as np
import pytest

from google_meridian_mcp_server.domain.filters import AnalysisFilters
from google_meridian_mcp_server.meridian.full_funnel.decomposition import ALL_CHANNELS

pytestmark = pytest.mark.integration

M1_LABEL = "M1 (brand equity, rest)"
M2_LABEL = "M2 (brand equity, rest)"


def _by_channel(rows):
    return {r["channel"]: r for r in rows}


def _short_window_filters(facade, geos=None) -> AnalysisFilters:
    times = facade.get_time_values()
    return AnalysisFilters(
        start_date=date.fromisoformat(times[-4][:10]),
        end_date=date.fromisoformat(times[-1][:10]),
        geos=geos if geos is not None else [facade.geo_names()[0]],
    )


def test_contribution_rows_plus_baseline_equal_full_funnel_expected(
    ff_facade, ff_expected
):
    rows = ff_facade.get_contribution_metrics(AnalysisFilters())
    total = sum(r["incremental_outcome"] for r in rows)
    assert total == pytest.approx(ff_expected(AnalysisFilters()), rel=1e-6)
    assert sum(r["pct_of_contribution"] for r in rows) == pytest.approx(1.0, rel=1e-6)


def test_contribution_reconciles_for_use_kpi_and_a_window(ff_facade, ff_expected):
    for filters in (
        AnalysisFilters(use_kpi=True),
        _short_window_filters(ff_facade, geos=[]),
        _short_window_filters(ff_facade),
    ):
        rows = ff_facade.get_contribution_metrics(filters)
        assert sum(r["incremental_outcome"] for r in rows) == pytest.approx(
            ff_expected(filters), rel=1e-6
        )


def test_contribution_relabels_mediators_and_splits_paid_rows(ff_facade):
    rows = _by_channel(ff_facade.get_contribution_metrics(AnalysisFilters()))
    assert {M1_LABEL, M2_LABEL} <= rows.keys()
    assert "M1" not in rows and "M2" not in rows
    a, c = rows["A"], rows["C"]
    assert a["incremental_outcome_direct"] + a["incremental_outcome_indirect"] == (
        pytest.approx(a["incremental_outcome"], rel=1e-6)
    )
    assert a["incremental_outcome_indirect"] > 0  # A builds both mediators
    assert abs(c["incremental_outcome_indirect"]) <= 1e-6 * abs(
        c["incremental_outcome"]
    )
    assert rows[M1_LABEL]["incremental_outcome_direct"] is None
    assert rows["baseline"]["incremental_outcome_indirect"] is None
    # Pin the relabelled VALUE (rows + baseline == expected holds by construction).
    split = ff_facade._funnel_split(AnalysisFilters(), aggregate_times=True)
    for name, label in (("M1", M1_LABEL), ("M2", M2_LABEL)):
        assert rows[label]["incremental_outcome"] == pytest.approx(
            float(split.mediators[name].rest), rel=1e-6
        )


def test_contribution_channel_filter_by_mediator_name(ff_facade):
    rows = ff_facade.get_contribution_metrics(AnalysisFilters(channels=["M1"]))
    channels = {r["channel"] for r in rows} - {"baseline"}
    assert channels == {M1_LABEL}


def test_mediator_filter_without_non_paid_matches_single_model_organic_filter(
    ff_facade, plain_facade
):
    """A mediator is organic, so it is absent from the paid-only table, as on a single model."""
    with pytest.raises(KeyError) as single:
        plain_facade.get_contribution_metrics(
            AnalysisFilters(channels=["organic_media_0"], include_non_paid=False)
        )
    with pytest.raises(KeyError) as funnel:
        ff_facade.get_contribution_metrics(
            AnalysisFilters(channels=["M1"], include_non_paid=False)
        )
    assert type(funnel.value) is type(single.value)
    assert funnel.value.args == single.value.args


def test_by_time_single_geo_short_window_reconciles(ff_facade, ff_expected):
    filters = _short_window_filters(ff_facade)
    rows = ff_facade.get_contribution_metrics_by_time(filters)
    per_time: dict[str, float] = defaultdict(float)
    for r in rows:
        per_time[str(r["time"])[:10]] += r["incremental_outcome"]
    got = np.array([per_time[t] for t in sorted(per_time)])
    assert len(got) == 4
    np.testing.assert_allclose(
        got, ff_expected(filters, aggregate_times=False), rtol=1e-6
    )
    assert all(np.isfinite(r["incremental_outcome"]) for r in rows)  # rest may be < 0

    # Each period's mediator row IS the split's per-period rest: no clamping at zero.
    split = ff_facade._funnel_split(filters, aggregate_times=False)
    times = [t[:10] for t in ff_facade._split_times(filters)]
    assert sorted(per_time) == sorted(times)
    for name, label in (("M1", M1_LABEL), ("M2", M2_LABEL)):
        by_time = {
            str(r["time"])[:10]: r["incremental_outcome"]
            for r in rows
            if r["channel"] == label
        }
        expected = np.asarray(split.mediators[name].rest, dtype=float)
        assert len(expected) == 4
        np.testing.assert_allclose(
            [by_time[t] for t in times], expected, rtol=1e-9, atol=1e-12
        )


def test_by_time_split_columns_follow_each_period(ff_facade):
    filters = _short_window_filters(ff_facade)
    rows = ff_facade.get_contribution_metrics_by_time(filters)
    split = ff_facade._funnel_split(filters, aggregate_times=False)
    times = [t[:10] for t in ff_facade._split_times(filters)]
    a_rows = {str(r["time"])[:10]: r for r in rows if r["channel"] == "A"}
    np.testing.assert_allclose(
        [a_rows[t]["incremental_outcome_direct"] for t in times],
        split.direct["A"],
        rtol=1e-9,
    )
    np.testing.assert_allclose(
        [a_rows[t]["incremental_outcome_indirect"] for t in times],
        split.indirect["A"],
        rtol=1e-9,
    )


def test_two_mediator_additivity_on_real_posteriors(ff_facade):
    split = ff_facade._funnel_split(AnalysisFilters(), aggregate_times=True)
    for c in ("A", "B", "C"):
        via = sum(m.indirect_by_channel[c] for m in split.mediators.values())
        assert split.total[c] == pytest.approx(split.direct[c] + via, rel=1e-6)


def test_geo_filter_is_honoured(ff_facade):
    all_geos = ff_facade._funnel_split(AnalysisFilters(), aggregate_times=True)
    one = ff_facade._funnel_split(
        AnalysisFilters(geos=[ff_facade.geo_names()[0]]), aggregate_times=True
    )
    assert one.total["A"] < all_geos.total["A"]


def test_funnel_split_is_memoized_per_slice(ff_facade):
    filters = AnalysisFilters(geos=[ff_facade.geo_names()[0]])
    first = ff_facade._funnel_split(filters, aggregate_times=True)
    assert ff_facade._funnel_split(filters, aggregate_times=True) is first
    assert ff_facade._funnel_split(filters, aggregate_times=False) is not first
    assert ff_facade._funnel_split(AnalysisFilters(), aggregate_times=True) is not first


def test_channel_lists_are_in_meridian_column_order(ff_facade):
    """`all_channels` must match include_non_paid_channels=True's column order."""
    analyzer = ff_facade._get_direct_analyzer()
    summary = analyzer.summary_metrics(include_non_paid_channels=True)
    coord = [str(c) for c in summary.channel.values if str(c) != ALL_CHANNELS]
    assert ff_facade.all_channels() == coord
    assert ff_facade.all_channels()[: len(ff_facade.paid_channels())] == (
        ff_facade.paid_channels()
    )

    # Names alone could still be permuted against the columns; compare values too.
    cols = np.asarray(
        analyzer.incremental_outcome(
            use_posterior=True, include_non_paid_channels=True, aggregate_times=True
        ),
        dtype=float,
    ).mean(axis=(0, 1))
    posterior_mean = summary["incremental_outcome"].sel(
        distribution="posterior", metric="mean"
    )
    for i, name in enumerate(ff_facade.all_channels()):
        assert cols[i] == pytest.approx(
            float(posterior_mean.sel(channel=name)), rel=1e-6
        )


@pytest.mark.parametrize("window", [False, True])
def test_full_analyzer_mediator_row_equals_direct_organic_total(ff_facade, window):
    """The relabel's All Channels arithmetic assumes O_m is the same in both analyzers."""
    filters = _short_window_filters(ff_facade, geos=[]) if window else AnalysisFilters()
    full = ff_facade._get_analyzer().summary_metrics(
        selected_times=ff_facade._expand_selected_times(filters),
        include_non_paid_channels=True,
        use_kpi=ff_facade.resolve_use_kpi(filters),
    )
    split = ff_facade._funnel_split(filters, aggregate_times=True)
    for name in ff_facade.mediator_names:
        row = float(
            full["incremental_outcome"].sel(
                channel=name, distribution="posterior", metric="mean"
            )
        )
        assert row == pytest.approx(
            float(split.mediators[name].organic_total), rel=1e-9
        )


def test_single_model_contribution_has_no_split_columns(plain_facade):
    rows = plain_facade.get_contribution_metrics(AnalysisFilters())
    assert all("incremental_outcome_direct" not in r for r in rows)

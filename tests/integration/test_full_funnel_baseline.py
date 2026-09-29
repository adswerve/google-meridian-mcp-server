"""One baseline: baseline_summary_metrics == model-fit baseline == contribution baseline."""

from __future__ import annotations

from datetime import date

import numpy as np
import pytest

from google_meridian_mcp_server.domain.filters import AnalysisFilters

pytestmark = pytest.mark.integration


def _contribution_baseline(facade, filters):
    rows = facade.get_contribution_metrics(filters)
    return next(r["incremental_outcome"] for r in rows if r["channel"] == "baseline")


def _summary_baseline(facade, filters):
    rows = facade.get_baseline_summary_metrics(filters)
    return next(r for r in rows if r["metric"] == "mean")


def _check(facade, filters):
    contribution = _contribution_baseline(facade, filters)
    summary = _summary_baseline(facade, filters)
    assert summary["baseline_outcome"] == pytest.approx(contribution, rel=1e-6)
    fit = facade.get_model_fit(filters)
    assert sum(r["baseline"] for r in fit) == pytest.approx(contribution, rel=1e-6)
    assert all(r["baseline_ci_lo"] is None and r["baseline_ci_hi"] is None for r in fit)


def test_one_baseline_full_window(ff_facade):
    _check(ff_facade, AnalysisFilters())


def test_one_baseline_single_geo(ff_facade):
    _check(ff_facade, AnalysisFilters(geos=[ff_facade.geo_names()[0]]))


def test_one_baseline_use_kpi(ff_facade):
    _check(ff_facade, AnalysisFilters(use_kpi=True))


def test_one_baseline_short_window_single_geo(ff_facade):
    times = ff_facade.get_time_values()
    _check(
        ff_facade,
        AnalysisFilters(
            geos=[ff_facade.geo_names()[0]],
            start_date=date.fromisoformat(times[-4][:10]),
            end_date=date.fromisoformat(times[-1][:10]),
        ),
    )


def test_adjusted_summary_ci_cells_are_null(ff_facade):
    rows = ff_facade.get_baseline_summary_metrics(AnalysisFilters())
    assert all(r["baseline_outcome"] is None for r in rows if r["metric"] != "mean")


def test_model_fit_expected_is_full_funnel(ff_facade, ff_expected):
    fit = ff_facade.get_model_fit(AnalysisFilters())
    assert sum(r["expected"] for r in fit) == pytest.approx(
        ff_expected(AnalysisFilters()), rel=1e-6
    )


def test_model_fit_per_period_baseline_aligns_with_rest_by_time_label(
    ff_facade, ff_expected
):
    """The positional per-period adjustment must land on the right period.

    Per time label: adjusted baseline + sum(paid) + sum(rest) == expected outcome,
    with paid and rest taken from the non-aggregated split indexed by time label.
    """
    times = ff_facade.get_time_values()
    for filters in (
        AnalysisFilters(),
        AnalysisFilters(
            geos=[ff_facade.geo_names()[0]],
            start_date=date.fromisoformat(times[-6][:10]),
            end_date=date.fromisoformat(times[-2][:10]),
        ),
    ):
        fit = ff_facade.get_model_fit(filters)
        split = ff_facade._funnel_split(filters, aggregate_times=False)
        labels = [t[:10] for t in ff_facade._split_times(filters)]
        assert [str(r["time"])[:10] for r in fit] == labels
        paid = sum(np.asarray(v, dtype=float) for v in split.total.values())
        rest = np.asarray(split.rest_total, dtype=float)
        assert not np.allclose(rest, rest[0])  # rest varies, so misordering would show
        expected = np.asarray(ff_expected(filters, aggregate_times=False))
        for i, row in enumerate(fit):
            assert row["baseline"] + paid[i] + rest[i] == pytest.approx(
                expected[i], rel=1e-6
            )
            assert row["expected"] == pytest.approx(expected[i], rel=1e-6)


def test_single_model_baseline_untouched(plain_facade):
    rows = plain_facade.get_baseline_summary_metrics(AnalysisFilters())
    ci = [r for r in rows if r["metric"] == "ci_lo"]
    assert ci and all(r["baseline_outcome"] is not None for r in ci)

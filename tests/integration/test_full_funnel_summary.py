"""paid_summary_metrics / roi split columns on the real fixture."""

from __future__ import annotations

import warnings

import pytest

from google_meridian_mcp_server.domain.filters import AnalysisFilters
from google_meridian_mcp_server.meridian.analyzer_facade import AnalyzerFacade
from scripts.validation.fixtures import ensure_full_funnel_fixture

pytestmark = pytest.mark.integration


def _mean_rows(rows):
    return {r["channel"]: r for r in rows if r["metric"] == "mean"}


def test_paid_summary_split_sums_to_total(ff_facade):
    rows = _mean_rows(ff_facade.get_paid_summary_metrics(AnalysisFilters()))
    for c in ("A", "B", "C", "All Channels"):
        r = rows[c]
        assert r["incremental_outcome_direct"] + r["incremental_outcome_indirect"] == (
            pytest.approx(r["incremental_outcome"], rel=1e-6)
        ), c
        assert r["roi_direct"] + r["roi_indirect"] == pytest.approx(
            r["roi"], rel=1e-6
        ), c
    assert rows["A"]["roi"] > rows["A"]["roi_direct"]
    assert rows["C"]["roi"] == pytest.approx(rows["C"]["roi_direct"], rel=1e-6)


def test_split_columns_are_null_on_non_mean_rows(ff_facade):
    rows = ff_facade.get_paid_summary_metrics(AnalysisFilters())
    others = [r for r in rows if r["metric"] != "mean"]
    assert others and all(r["incremental_outcome_direct"] is None for r in others)


def test_roi_output_gains_split_columns(ff_facade):
    rows = _mean_rows(ff_facade.get_roi(AnalysisFilters()))
    a = rows["A"]
    assert set(a) >= {"channel", "metric", "roi", "roi_direct", "roi_indirect"}
    assert a["roi_direct"] + a["roi_indirect"] == pytest.approx(a["roi"], rel=1e-6)


def test_paid_breakdown_use_kpi_consistent(ff_facade):
    kpi = _mean_rows(ff_facade.get_paid_summary_metrics(AnalysisFilters(use_kpi=True)))
    rev = _mean_rows(ff_facade.get_paid_summary_metrics(AnalysisFilters(use_kpi=False)))
    a_kpi, a_rev = kpi["A"], rev["A"]
    assert a_kpi["incremental_outcome_direct"] + a_kpi[
        "incremental_outcome_indirect"
    ] == (pytest.approx(a_kpi["incremental_outcome"], rel=1e-6))
    # Fixture revenue_per_kpi is a constant 2.0: KPI and revenue split scale together.
    assert a_rev["incremental_outcome_indirect"] / a_kpi[
        "incremental_outcome_indirect"
    ] == (pytest.approx(2.0, rel=1e-3))


def test_non_aggregated_summary_has_split_but_no_roi_split(ff_facade):
    rows = ff_facade.get_paid_summary_metrics(AnalysisFilters(aggregate_times=False))
    assert rows and "incremental_outcome_direct" in rows[0]
    assert "roi_direct" not in rows[0]


def test_single_model_summary_has_no_split_columns(plain_facade):
    rows = plain_facade.get_paid_summary_metrics(AnalysisFilters())
    assert all("roi_direct" not in r for r in rows)


def test_direct_and_stage1_analyzers_use_non_deprecated_constructor():
    """Meridian 2.1 warns on Analyzer(meridian); the full-funnel sites must not."""
    stage2, mediators = ensure_full_funnel_fixture("geo-full-funnel")
    facade = AnalyzerFacade(stage2, mediators)
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        direct = facade._get_direct_analyzer()
        stage1 = facade._get_stage1_analyzer(facade.mediator_names[0])
    assert direct is not facade._get_analyzer()
    assert stage1 is not None

"""Every analysis tool that takes ``use_kpi`` resolves it by the one rule.

On a model with no revenue per KPI, ``use_kpi=false`` used to run Meridian's KPI analysis
anyway (the analyzer ignores the request) and label the answer ``revenue``: KPI figures
presented as revenue. It is now refused, as for an optimization, and an omitted ``use_kpi``
answers the KPI. Real fitted ``national-kpi-only`` fixture (built on first use).

The tools that take ``use_kpi`` are the ones ``domain/applicability.py`` lists with it:
get_channel_summary, get_contribution, get_response_curves, get_reach_frequency,
get_model_fit, get_spend_scenario and get_funnel_breakdown (channel_breakdown). All but the
last are driven here; the funnel breakdown only exists on a full-funnel model, which has
revenue, and shares the same ``resolve_use_kpi`` through ``_funnel_split``.
"""

from __future__ import annotations

import pytest

from google_meridian_mcp_server.domain.errors import ObjectiveNotAvailableError
from google_meridian_mcp_server.execution import analysis_ops

pytestmark = pytest.mark.integration

MODEL = "national-kpi-only"


class _Catalog:
    def __init__(self, facade):
        self._facade = facade

    def get_facade(self, model_id):
        return self._facade

    def get_interrogator(self, model_id):
        return self._facade


@pytest.fixture(scope="module")
def catalog():
    from google_meridian_mcp_server.meridian.analyzer_facade import AnalyzerFacade
    from scripts.validation.fixtures import ensure_fixture_model

    facade = AnalyzerFacade(ensure_fixture_model(MODEL))
    assert not facade.has_revenue_per_kpi()
    return _Catalog(facade)


def _calls(channel: str):
    return [
        ("get_channel_summary", {"output_type": "paid_summary_metrics"}),
        ("get_channel_summary", {"output_type": "baseline_summary_metrics"}),
        ("get_channel_summary", {"output_type": "cpik"}),
        ("get_contribution", {"output_type": "contribution_metrics"}),
        ("get_response_curves", {"output_type": "response_curve_summary"}),
        ("get_reach_frequency", {}),
        ("get_model_fit", {}),
        ("get_spend_scenario", {"channel": channel, "spend_increase": 1000.0}),
    ]


def _run(catalog, tool, params, use_kpi):
    filters = {} if use_kpi is None else {"use_kpi": use_kpi}
    return analysis_ops.run_operation(
        catalog, tool, MODEL, {**params, "filters": filters}
    )


def test_a_revenue_objective_is_refused_by_every_analysis_tool(catalog):
    """Break the source: restore the interrogator's own rule (an explicit value wins) --
    the tools answer KPI figures and this goes red."""
    channel = catalog._facade.get_data_inputs()["media"][0]
    calls = _calls(channel)
    assert calls
    for tool, params in calls:
        with pytest.raises(ObjectiveNotAvailableError) as refused:
            _run(catalog, tool, params, use_kpi=False)
        assert refused.value.error_code == "objective_not_available", tool


def test_the_default_objective_answers_the_kpi_and_says_so(catalog):
    channel = catalog._facade.get_data_inputs()["media"][0]

    scenario = _run(
        catalog,
        "get_spend_scenario",
        {"channel": channel, "spend_increase": 1000.0},
        use_kpi=None,
    )
    explicit = _run(
        catalog,
        "get_spend_scenario",
        {"channel": channel, "spend_increase": 1000.0},
        use_kpi=True,
    )

    assert scenario["outcome_mode"] == "kpi"
    assert explicit["outcome_mode"] == "kpi"
    for tool, params in _calls(channel):
        assert _run(catalog, tool, params, use_kpi=None), tool

"""The objective is resolved once, before the target is read for it.

A model without revenue per KPI cannot give a revenue objective. Meridian's analyzer
runs the KPI analysis anyway (``Analyzer._use_kpi``), but a target is inverted only
when the request said ``use_kpi=true``, so ``use_kpi=false`` there used a ROAS figure
as KPI-per-dollar. It is now refused, before the fingerprint, the reuse lookup and
the queue; ``use_kpi`` omitted still resolves to the KPI and inverts.
"""

from __future__ import annotations

import pytest
from test_analysis_ops import FakeOptimizerCatalog, FakeOptimizerFacade

from google_meridian_mcp_server.domain.errors import ObjectiveNotAvailableError
from google_meridian_mcp_server.domain.optimization import (
    FutureOptimizationConfig,
    OptimizationConfig,
    resolve_use_kpi,
    to_optimize_kwargs,
)
from google_meridian_mcp_server.execution import analysis_ops
from google_meridian_mcp_server.meridian.optimizer_facade import OptimizerFacade

CHANNELS = ["tv", "search"]
FUTURE = {"start_date": "2099-01-01", "horizon": 4}
SCENARIOS = {
    "fixed_budget": {"type": "fixed_budget", "budget": 100.0},
    "target_roas": {"type": "target_roas", "target_value": 4.0},
    "target_mroas": {"type": "target_mroas", "target_value": 2.0},
}


def _config(kind: str, scenario: str, use_kpi: bool | None):
    body = {"scenario": SCENARIOS[scenario], "use_kpi": use_kpi}
    if kind == "future":
        return FutureOptimizationConfig.model_validate(
            {**body, "kind": "future", "future": FUTURE}
        )
    return OptimizationConfig.model_validate(body)


def _facade(has_revenue: bool) -> OptimizerFacade:
    facade = OptimizerFacade.__new__(OptimizerFacade)
    facade.has_revenue_per_kpi = lambda: has_revenue
    return facade


def _kwargs(facade: OptimizerFacade, config):
    return to_optimize_kwargs(
        config,
        channel_order=CHANNELS,
        use_kpi=facade.resolve_use_kpi(config),
    )


@pytest.mark.parametrize("kind", ["historical", "future"])
@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_a_revenue_objective_on_a_model_with_no_revenue_is_refused(kind, scenario):
    """Break the source: drop the refusal from ``resolve_use_kpi`` -- this goes red."""
    with pytest.raises(ObjectiveNotAvailableError) as refused:
        _facade(has_revenue=False).resolve_use_kpi(_config(kind, scenario, False))

    assert refused.value.error_code == "objective_not_available"
    assert str(refused.value) == (
        "This model has no revenue per KPI, so it cannot be optimized for a revenue "
        "objective (use_kpi=false). Omit use_kpi, or set it to true: on this model both "
        "optimize the KPI, and a target_roas or target_mroas figure is read as a cost per "
        "incremental KPI (CPIK)."
    )


@pytest.mark.parametrize("kind", ["historical", "future"])
def test_the_default_objective_on_a_model_with_no_revenue_is_the_kpi_and_inverts(kind):
    facade = _facade(has_revenue=False)

    roas = _kwargs(facade, _config(kind, "target_roas", None))
    mroas = _kwargs(facade, _config(kind, "target_mroas", None))

    assert roas["use_kpi"] is True
    assert roas["target_roi"] == pytest.approx(0.25)
    assert mroas["target_mroi"] == pytest.approx(0.5)


@pytest.mark.parametrize("kind", ["historical", "future"])
def test_the_kpi_objective_on_a_model_with_revenue_inverts(kind):
    kwargs = _kwargs(_facade(has_revenue=True), _config(kind, "target_roas", True))

    assert kwargs["use_kpi"] is True
    assert kwargs["target_roi"] == pytest.approx(0.25)


@pytest.mark.parametrize("kind", ["historical", "future"])
@pytest.mark.parametrize("use_kpi", [False, None])
def test_a_revenue_or_default_objective_on_a_model_with_revenue_does_not_invert(
    kind, use_kpi
):
    facade = _facade(has_revenue=True)

    roas = _kwargs(facade, _config(kind, "target_roas", use_kpi))
    mroas = _kwargs(facade, _config(kind, "target_mroas", use_kpi))

    assert roas["use_kpi"] is False and roas["target_roi"] == 4.0
    assert mroas["target_mroi"] == 2.0


@pytest.mark.parametrize(
    ("requested", "has_revenue", "resolved"),
    [(None, True, False), (None, False, True), (True, True, True), (True, False, True)],
)
def test_resolve_use_kpi_table(requested, has_revenue, resolved):
    assert resolve_use_kpi(requested, has_revenue_per_kpi=has_revenue) is resolved


class _NoRevenueFacade(FakeOptimizerFacade):
    """The preflight's fake facade, with the real resolution over a no-revenue model."""

    def has_revenue_per_kpi(self):
        return False

    objective_purpose = OptimizerFacade.objective_purpose
    resolve_use_kpi = OptimizerFacade.resolve_use_kpi


@pytest.mark.parametrize("kind", ["historical", "future"])
@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_preflight_refuses_a_revenue_objective_before_anything_is_queued(
    kind, scenario
):
    """The submit path raises whatever preflight reports, ahead of the fingerprint's reuse
    lookup and the queue, so a refused request is never stored as, or served from, a run.

    Break the source: resolve ``use_kpi`` from the raw request in preflight again -- the
    refusal is gone for the future path and this goes red."""
    config = {"scenario": SCENARIOS[scenario], "use_kpi": False}
    if kind == "future":
        config = {**config, "kind": "future", "future": FUTURE}

    out = analysis_ops.run_operation(
        FakeOptimizerCatalog(_NoRevenueFacade()),
        "preflight_optimization",
        "m1",
        {"config": config},
    )

    assert out["validation_error"]["error_code"] == "objective_not_available"
    assert out["use_kpi"] is None


def test_preflight_resolves_the_default_to_the_kpi_on_a_model_with_no_revenue():
    out = analysis_ops.run_operation(
        FakeOptimizerCatalog(_NoRevenueFacade()),
        "preflight_optimization",
        "m1",
        {"config": {"scenario": SCENARIOS["target_roas"]}},
    )

    assert out["validation_error"] is None
    assert out["use_kpi"] is True

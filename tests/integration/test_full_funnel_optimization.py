"""Full-funnel optimization end to end on the real fixture (in-process facade)."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pytest

from google_meridian_mcp_server.domain.optimization import (
    FutureOptimizationConfig,
    OptimizationConfig,
)

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def ff_opt():
    from google_meridian_mcp_server.meridian.optimizer_facade import OptimizerFacade
    from scripts.validation.fixtures import ensure_full_funnel_fixture

    stage2, mediators = ensure_full_funnel_fixture()
    return OptimizerFacade(stage2, mediators)


def _historical():
    return OptimizationConfig.model_validate({"scenario": {"type": "fixed_budget"}})


def _future(facade):
    last = date.fromisoformat(facade.get_time_values()[-1][:10])
    return FutureOptimizationConfig.model_validate(
        {
            "scenario": {"type": "fixed_budget"},
            "future": {
                "start_date": (last + timedelta(days=7)).isoformat(),
                "horizon": 4,
            },
        }
    )


def _check_rows(result):
    for table in ("initial", "optimized"):
        for row in result["channel_tables"][table]:
            got = (
                row["incremental_outcome_direct"] + row["incremental_outcome_indirect"]
            )
            assert got == pytest.approx(row["incremental_outcome"], rel=1e-5), row


def test_historical_run_has_split_rows(ff_opt):
    result = ff_opt.run(_historical())
    _check_rows(result)
    optimized = {r["channel"]: r for r in result["channel_tables"]["optimized"]}
    assert optimized["A"]["incremental_outcome_indirect"] > 0
    assert abs(optimized["C"]["incremental_outcome_indirect"]) <= 1e-5 * abs(
        optimized["C"]["incremental_outcome"]
    )


def test_future_run_has_split_rows_and_full_funnel_assumptions(ff_opt):
    result = ff_opt.run_future(_future(ff_opt))
    _check_rows(result)
    assert result["assumptions"]["full_funnel"] == {
        "mediators": ["M1", "M2"],
        "mediator_treatment": "predicted_from_planned_spend",
    }


@pytest.mark.parametrize("kind", ["historical", "future"])
def test_direct_split_of_initial_allocation_matches_plain_optimizer(ff_opt, kind):
    from meridian.analysis import optimizer as optimizer_mod

    config = _historical() if kind == "historical" else _future(ff_opt)
    use_kpi = ff_opt.resolve_use_kpi(config)
    build = ff_opt._historical_kwargs if kind == "historical" else ff_opt._future_kwargs
    ff_bo = optimizer_mod.BudgetOptimizer(ff_opt._mmm, analyzer=ff_opt._get_analyzer())
    kwargs = build(config, ff_bo, use_kpi)
    kwargs.pop("_assumptions", None)
    ff_results = ff_bo.optimize(**kwargs)
    got = ff_opt._direct_incremental(
        ff_results, ff_results.nonoptimized_data, kwargs, use_kpi
    )

    plain_bo = optimizer_mod.BudgetOptimizer(ff_opt._mmm)
    plain_kwargs = build(config, plain_bo, use_kpi)
    plain_kwargs.pop("_assumptions", None)
    plain = plain_bo.optimize(**plain_kwargs).nonoptimized_data
    expected = plain["incremental_outcome"].sel(metric="mean").values
    np.testing.assert_allclose(
        [got[c] for c in ff_opt.channel_order()], np.asarray(expected, float), rtol=1e-6
    )


def test_future_kwargs_select_the_whole_future_window(ff_opt):
    from meridian.analysis import optimizer as optimizer_mod

    from google_meridian_mcp_server.meridian import future_data as fd

    config = _future(ff_opt)
    opt = optimizer_mod.BudgetOptimizer(ff_opt._mmm, analyzer=ff_opt._get_analyzer())
    kwargs = ff_opt._future_kwargs(config, opt, ff_opt.resolve_use_kpi(config))
    labels = fd.future_time_labels(
        config.future.start_date,
        config.future.horizon,
        fd.infer_cadence_days(ff_opt.get_time_values()),
    )
    # AnalyzerFullFunnel supports only the whole future window, never a sub-window.
    assert (kwargs["start_date"], kwargs["end_date"]) == (labels[0], labels[-1])


def test_size_features_count_every_model(ff_opt):
    from google_meridian_mcp_server.execution.routing import model_size_features

    assert model_size_features(ff_opt)["n_models"] == 3

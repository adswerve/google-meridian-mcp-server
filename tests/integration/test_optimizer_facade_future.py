"""Integration: run_future against a real tiny fitted model (built-if-missing)."""

import pytest

from google_meridian_mcp_server.domain.optimization import FutureOptimizationConfig

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def national_revenue_facade():
    # Reuse the validation fixtures + loader; build-if-missing.
    from google_meridian_mcp_server.meridian.optimizer_facade import OptimizerFacade
    from scripts.validation.fixtures import ensure_fixture_model

    mmm = ensure_fixture_model("national-revenue")
    return OptimizerFacade(mmm)


def _future_cfg(facade, **future_over):
    times = facade.get_time_values()
    from datetime import date, timedelta

    last = date.fromisoformat(times[-1][:10])
    start = (last + timedelta(days=7)).isoformat()
    future = {"start_date": start, "horizon": 4, **future_over}
    return FutureOptimizationConfig.model_validate(
        {"scenario": {"type": "fixed_budget"}, "future": future}
    )


def test_run_future_trailing_default_wellformed(national_revenue_facade):
    facade = national_revenue_facade
    result = facade.run_future(_future_cfg(facade))
    assert result["outcome_mode"] in ("revenue", "kpi")
    assert {"summary", "channel_tables", "allocation", "spend_delta"} <= result.keys()
    assert result["channel_tables"]["optimized"]  # non-empty rows


def test_run_future_omits_response_curves(national_revenue_facade):
    """FIX I1: Meridian's get_response_curves ignores new_data's future
    media/media_spend and would silently return curves reflecting historical
    flighting/cost, contradicting the future run's own assumptions -- so a
    future run must omit `response_curves` entirely rather than ship a
    misleading enrichment. The historical `run()` path is unchanged and still
    enriches with response curves."""
    facade = national_revenue_facade
    future_result = facade.run_future(_future_cfg(facade))
    assert "response_curves" not in future_result

    from google_meridian_mcp_server.domain.optimization import OptimizationConfig

    historical_result = facade.run(
        OptimizationConfig.model_validate({"scenario": {"type": "fixed_budget"}})
    )
    assert "response_curves" in historical_result


def test_run_future_cost_multiplier_shifts_away(national_revenue_facade):
    facade = national_revenue_facade
    # Use a media channel, not RF: RF channels can sit at their spend-constraint
    # floor, so a *cost increase* can't move them lower and the test would pass
    # trivially even if the cost path were broken.
    channel = facade.get_data_inputs()["media"][0]
    base = facade.run_future(_future_cfg(facade))
    # A large multiplier makes the effect unambiguous.
    bumped = facade.run_future(_future_cfg(facade, cost_multipliers={channel: 100.0}))

    def opt_spend(res, ch):
        rows = {r["channel"]: r["spend"] for r in res["channel_tables"]["optimized"]}
        return rows[ch]

    def opt_allocation(res):
        return {r["channel"]: r["spend"] for r in res["channel_tables"]["optimized"]}

    # (a) The full optimized allocation must differ from baseline. This is the
    # teeth of the test: if the cost multiplier were ignored, `bumped` would be
    # identical to `base` and this assertion would catch it.
    assert opt_allocation(bumped) != opt_allocation(base), (
        "cost multiplier had no effect on the optimized allocation — the "
        "future cost assumption is being ignored"
    )

    # (b) Making one channel 100x more expensive should not increase its own
    # optimized spend.
    assert opt_spend(bumped, channel) <= opt_spend(base, channel) + 1e-6, (
        f"channel {channel!r} optimized spend increased despite a 100x cost "
        "multiplier — the future cost assumption is being ignored"
    )


def _spend_map(rows):
    return {r["channel"]: r["spend"] for r in rows}


@pytest.mark.filterwarnings("ignore::RuntimeWarning")
def test_run_future_excludes_channel(national_revenue_facade):
    facade = national_revenue_facade
    result = facade.run_future(_future_cfg(facade, excluded_channels=["ch_0"]))
    initial = _spend_map(result["channel_tables"]["initial"])
    optimized = _spend_map(result["channel_tables"]["optimized"])
    # Excluded channel present in BOTH tables, pinned to 0.
    assert "ch_0" in initial and "ch_0" in optimized
    assert initial["ch_0"] == 0 or initial["ch_0"] is None or initial["ch_0"] == 0.0
    assert optimized["ch_0"] == 0 or optimized["ch_0"] == 0.0
    # Remaining channels carry all the spend.
    assert sum(v for k, v in optimized.items() if k != "ch_0") > 0
    # Exact budget conservation: optimized spend across ALL channels equals the
    # optimized total budget, and the excluded channel contributes exactly 0.
    total_opt = sum(v for v in optimized.values() if v is not None)
    budget = result["summary"]["optimized_budget"]
    assert abs(total_opt - budget) <= 1e-6 * max(budget, 1.0), (
        f"excluded budget leaked: channels sum {total_opt} != budget {budget}"
    )


def test_run_future_exclude_unknown_channel_raises(national_revenue_facade):
    facade = national_revenue_facade
    with pytest.raises(ValueError, match="unknown"):
        facade.validate_future(_future_cfg(facade, excluded_channels=["nope"]))


def test_run_future_exclude_all_raises(national_revenue_facade):
    facade = national_revenue_facade
    every = facade.channel_order()
    with pytest.raises(ValueError, match="every channel"):
        facade.validate_future(_future_cfg(facade, excluded_channels=every))

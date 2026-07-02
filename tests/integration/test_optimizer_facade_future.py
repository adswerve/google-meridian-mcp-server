"""Integration: run_future against a real tiny fitted model (built-if-missing)."""

import pytest

from google_meridian_mcp_server.domain.optimization import FutureOptimizationConfig

pytestmark = pytest.mark.integration  # register in pyproject (see Step 0)


@pytest.fixture(scope="module")
def national_revenue_facade():
    # Reuse the validation fixtures + loader; build-if-missing.
    from google_meridian_mcp_server.meridian.optimizer_facade import OptimizerFacade
    from scripts.validation.fixtures import ensure_fixture_model  # helper (Task 11)

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


def test_run_future_cost_multiplier_shifts_away(national_revenue_facade):
    facade = national_revenue_facade
    channel = facade.channel_order()[0]
    base = facade.run_future(_future_cfg(facade))
    bumped = facade.run_future(_future_cfg(facade, cost_multipliers={channel: 3.0}))

    def opt_spend(res, ch):
        rows = {r["channel"]: r["spend"] for r in res["channel_tables"]["optimized"]}
        return rows[ch]

    # Tripling one channel's cost should not increase its optimized spend.
    assert opt_spend(bumped, channel) <= opt_spend(base, channel) + 1e-6

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
    #
    # NOTE: the media channel itself can *also* land on its floor under a
    # refit posterior (e.g. the global spend-constraint pct pins it at
    # 0.7x its non-optimized spend), which makes allocation identity an
    # unreliable proxy for "the multiplier had an effect" -- assertion (a)
    # used to compare the full optimized allocation and is fragile to
    # exactly this. We instead assert on the summary economics
    # (optimized_efficiency / optimized_incremental_outcome), which must
    # move if the multiplier is applied at all, regardless of whether any
    # channel's spend is floor-pinned.
    channel = facade.get_data_inputs()["media"][0]
    base = facade.run_future(_future_cfg(facade))
    # A large multiplier makes the effect unambiguous.
    bumped = facade.run_future(_future_cfg(facade, cost_multipliers={channel: 100.0}))

    def opt_spend(res, ch):
        rows = {r["channel"]: r["spend"] for r in res["channel_tables"]["optimized"]}
        return rows[ch]

    # (a) The optimized summary economics must differ from baseline. This is
    # the teeth of the test: a 100x cost multiplier on one channel changes
    # what it costs to buy the same incremental outcome, so
    # optimized_efficiency and optimized_incremental_outcome cannot be
    # unchanged even when the optimizer keeps every channel's spend pinned
    # at its floor (e.g. under a tight global spend constraint). If the
    # cost multiplier were ignored, both would be identical to baseline and
    # this assertion would catch it.
    base_summary = base["summary"]
    bumped_summary = bumped["summary"]
    assert (
        bumped_summary["optimized_efficiency"] != base_summary["optimized_efficiency"]
        or bumped_summary["optimized_incremental_outcome"]
        != base_summary["optimized_incremental_outcome"]
    ), (
        "cost multiplier had no effect on optimized_efficiency or "
        "optimized_incremental_outcome — the future cost assumption is "
        "being ignored"
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
    assert initial["ch_0"] in (0, 0.0)
    assert optimized["ch_0"] in (0, 0.0)
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


@pytest.mark.filterwarnings("ignore::RuntimeWarning")
def test_run_future_excluded_dark_channel_succeeds_and_reports_assumptions():
    """A channel that is dark (zero spend AND zero units) over the reference
    window makes an un-excluded future run fail fast, but excluding it succeeds
    end to end — and the result echoes the assumed budget/reference/exclusion.
    Covers both budget_source values and a target scenario."""
    from datetime import date, timedelta

    from google_meridian_mcp_server.domain.optimization import (
        FutureOptimizationConfig,
    )
    from google_meridian_mcp_server.meridian.optimizer_facade import OptimizerFacade
    from scripts.validation.fixtures import ensure_fixture_model

    mmm = ensure_fixture_model("national-revenue")
    facade = OptimizerFacade(mmm)
    media_channels = facade.get_data_inputs()["media"]
    dark = media_channels[-1]
    dark_idx = media_channels.index(dark)

    # Faithful dark channel: zero units AND zero spend across all periods. This
    # drives cpmu through the 0/1 -> 1.0 bump and _carried_allocation with a
    # genuinely zero-weight channel. NOTE: we mutate input_data only; the model's
    # already-fitted internal tensors are untouched. That's sound here —
    # validate_future reads input_data, and exclusion pins the channel to 0 spend
    # via bounds regardless of the fit.
    facade._mmm.input_data.media.values[..., dark_idx] = 0.0
    facade._mmm.input_data.media_spend.values[..., dark_idx] = 0.0

    times = facade.get_time_values()
    start = (date.fromisoformat(times[-1][:10]) + timedelta(days=7)).isoformat()

    def cfg(scenario=None, **future_over):
        return FutureOptimizationConfig.model_validate(
            {
                "scenario": scenario or {"type": "fixed_budget"},
                "future": {"start_date": start, "horizon": 4, **future_over},
            }
        )

    # Un-excluded -> fail fast at validate, naming the channel.
    with pytest.raises(ValueError, match=dark):
        facade.validate_future(cfg())

    # Excluded, budget omitted -> succeeds; budget derived from the reference.
    result = facade.run_future(cfg(excluded_channels=[dark]))
    optimized = {
        r["channel"]: r["spend"] for r in result["channel_tables"]["optimized"]
    }
    assert optimized[dark] in (0, 0.0)
    a = result["assumptions"]
    assert a["excluded_channels"] == [dark]
    assert a["reference_mode"] == "trailing"
    assert a["budget_source"] == "derived_from_reference"
    # Meridian rounds the fixed-budget total to whole currency units — a large
    # relative swing at this toy fixture's ~25-unit scale (negligible at real scale).
    assert a["budget"] == pytest.approx(result["summary"]["optimized_budget"], rel=0.05)

    # Excluded, budget explicit -> budget_source flips to "explicit" and echoes it.
    explicit = facade.run_future(
        cfg(
            scenario={"type": "fixed_budget", "budget": 1_000_000.0},
            excluded_channels=[dark],
        )
    )
    assert explicit["assumptions"]["budget_source"] == "explicit"
    assert explicit["assumptions"]["budget"] == pytest.approx(1_000_000.0, rel=1e-6)

    # Target scenario (flexible budget) -> determined_by_target, budget None.
    target = facade.run_future(
        cfg(
            scenario={"type": "target_roas", "target_value": 2.0},
            excluded_channels=[dark],
        )
    )
    assert target["assumptions"]["budget_source"] == "determined_by_target"
    assert target["assumptions"]["budget"] is None

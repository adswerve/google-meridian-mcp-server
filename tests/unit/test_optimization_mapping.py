import pytest

from google_meridian_mcp_server.domain.optimization import (
    FutureOptimizationConfig,
    OptimizationConfig,
    to_optimize_kwargs,
)

CHANNELS = ["tv", "search"]


def _cfg(d):
    return OptimizationConfig.model_validate(d)


def test_fixed_budget_global_revenue():
    kw = to_optimize_kwargs(
        _cfg(
            {
                "scenario": {"type": "fixed_budget", "budget": 500.0},
                "constraint": {"mode": "global", "pct": 0.2},
            }
        ),
        channel_order=CHANNELS,
        use_kpi=False,
    )
    assert kw["fixed_budget"] is True
    assert kw["budget"] == 500.0
    assert kw["target_roi"] is None and kw["target_mroi"] is None
    assert kw["spend_constraint_lower"] == 0.2 and kw["spend_constraint_upper"] == 0.2
    assert kw["use_kpi"] is False


def test_target_roas_revenue_not_inverted():
    kw = to_optimize_kwargs(
        _cfg({"scenario": {"type": "target_roas", "target_value": 4.0}}),
        channel_order=CHANNELS,
        use_kpi=False,
    )
    assert kw["fixed_budget"] is False
    assert kw["target_roi"] == 4.0


def test_target_roas_kpi_inverted():
    kw = to_optimize_kwargs(
        _cfg({"scenario": {"type": "target_roas", "target_value": 4.0}}),
        channel_order=CHANNELS,
        use_kpi=True,
    )
    assert kw["target_roi"] == pytest.approx(0.25)  # 1/4 CPIK target


def test_per_channel_constraint_orders_to_channels():
    kw = to_optimize_kwargs(
        _cfg(
            {
                "scenario": {"type": "fixed_budget"},
                "constraint": {
                    "mode": "per_channel",
                    "bounds": {
                        "search": {"lower_pct": 0.1, "upper_pct": 0.5},
                        "tv": {"lower_pct": 0.2, "upper_pct": 0.4},
                    },
                },
            }
        ),
        channel_order=CHANNELS,
        use_kpi=False,
    )
    assert kw["spend_constraint_lower"] == [0.2, 0.1]  # tv, search order
    assert kw["spend_constraint_upper"] == [0.4, 0.5]


def test_per_channel_missing_channel_raises():
    with pytest.raises(ValueError, match="search"):
        to_optimize_kwargs(
            _cfg(
                {
                    "scenario": {"type": "fixed_budget"},
                    "constraint": {
                        "mode": "per_channel",
                        "bounds": {"tv": {"lower_pct": 0.2, "upper_pct": 0.4}},
                    },
                }
            ),
            channel_order=CHANNELS,
            use_kpi=False,
        )


def test_to_optimize_kwargs_on_future_config_omits_historical_dates():
    cfg = FutureOptimizationConfig.model_validate(
        {
            "scenario": {"type": "fixed_budget", "budget": 1000.0},
            "constraint": {"mode": "global", "pct": 0.25},
            "future": {"start_date": "2026-10-01", "horizon": 13},
        }
    )
    kw = to_optimize_kwargs(cfg, channel_order=CHANNELS, use_kpi=False)
    # base translation still works…
    assert kw["fixed_budget"] is True and kw["budget"] == 1000.0
    assert kw["spend_constraint_lower"] == 0.25
    # …and no historical window is emitted for a future config.
    assert kw["start_date"] is None and kw["end_date"] is None

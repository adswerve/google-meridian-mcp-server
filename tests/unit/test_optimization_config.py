import pytest
from pydantic import ValidationError

from google_meridian_mcp_server.domain.optimization import (
    GlobalConstraint,
    OptimizationConfig,
    PerChannelConstraint,
    config_fingerprint,
)


def test_fixed_budget_scenario_parses_from_dict():
    cfg = OptimizationConfig.model_validate(
        {"scenario": {"type": "fixed_budget", "budget": 1_200_000}}
    )
    assert cfg.scenario.type == "fixed_budget"
    assert cfg.scenario.budget == 1_200_000
    assert isinstance(cfg.constraint, GlobalConstraint)
    assert cfg.constraint.pct == 0.3


def test_target_roas_scenario_parses():
    cfg = OptimizationConfig.model_validate(
        {"scenario": {"type": "target_roas", "target_value": 2.0}}
    )
    assert cfg.scenario.type == "target_roas"
    assert cfg.scenario.target_value == 2.0


def test_per_channel_constraint_parses():
    cfg = OptimizationConfig.model_validate(
        {
            "scenario": {"type": "fixed_budget"},
            "constraint": {
                "mode": "per_channel",
                "bounds": {"tv": {"lower_pct": 0.1, "upper_pct": 0.3}},
            },
        }
    )
    assert isinstance(cfg.constraint, PerChannelConstraint)
    assert cfg.constraint.bounds["tv"].upper_pct == 0.3


def test_target_value_must_be_positive():
    with pytest.raises(ValidationError):
        OptimizationConfig.model_validate(
            {"scenario": {"type": "target_roas", "target_value": 0}}
        )


def test_fingerprint_is_stable_and_order_insensitive():
    a = OptimizationConfig.model_validate(
        {
            "scenario": {"type": "fixed_budget", "budget": 100.0},
            "selected_geos": ["b", "a"],
        }
    )
    b = OptimizationConfig.model_validate(
        {
            "scenario": {"type": "fixed_budget", "budget": 100.0},
            "selected_geos": ["a", "b"],
        }
    )
    assert config_fingerprint("m", a) == config_fingerprint("m", b)
    assert config_fingerprint("m", a) != config_fingerprint("other", a)

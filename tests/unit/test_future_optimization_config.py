import json

import pytest
from pydantic import TypeAdapter, ValidationError

from google_meridian_mcp_server.domain.optimization import (
    AnyOptimizationConfig,
    FutureOptimizationConfig,
    OptimizationConfig,
    config_fingerprint,
)


def test_historical_kind_defaults_and_backward_compat():
    # Old persisted config without `kind` still parses as historical.
    cfg = OptimizationConfig.model_validate(
        {"scenario": {"type": "fixed_budget", "budget": 500.0}}
    )
    assert cfg.kind == "historical"


def test_future_config_parses_with_defaults():
    cfg = FutureOptimizationConfig.model_validate(
        {
            "scenario": {"type": "fixed_budget"},
            "future": {"start_date": "2026-10-01", "horizon": 13},
        }
    )
    assert cfg.kind == "future"
    assert cfg.future.horizon == 13
    assert cfg.future.reference.mode == "trailing"
    assert cfg.future.revenue_per_kpi_multiplier == 1.0
    assert cfg.future.cost_multipliers is None


def test_future_config_rejects_nonpositive_horizon():
    with pytest.raises(ValidationError):
        FutureOptimizationConfig.model_validate(
            {
                "scenario": {"type": "fixed_budget"},
                "future": {"start_date": "2026-10-01", "horizon": 0},
            }
        )


def test_reference_same_period_last_year_and_full_history():
    for mode in ("same_period_last_year", "full_history_average"):
        cfg = FutureOptimizationConfig.model_validate(
            {
                "scenario": {"type": "fixed_budget"},
                "future": {
                    "start_date": "2026-10-01",
                    "horizon": 4,
                    "reference": {"mode": mode},
                },
            }
        )
        assert cfg.future.reference.mode == mode


def test_any_config_discriminates_by_kind():
    adapter = TypeAdapter(AnyOptimizationConfig)
    hist = adapter.validate_python(
        {"kind": "historical", "scenario": {"type": "fixed_budget"}}
    )
    fut = adapter.validate_python(
        {
            "kind": "future",
            "scenario": {"type": "fixed_budget"},
            "future": {"start_date": "2026-10-01", "horizon": 5},
        }
    )
    assert isinstance(hist, OptimizationConfig)
    assert isinstance(fut, FutureOptimizationConfig)


def test_any_config_defaults_missing_kind_to_historical():
    # Legacy persisted config JSON has no `kind`; the union must still parse it.
    adapter = TypeAdapter(AnyOptimizationConfig)
    parsed = adapter.validate_python({"scenario": {"type": "fixed_budget"}})
    assert isinstance(parsed, OptimizationConfig)


def test_optimization_run_roundtrips_legacy_json_without_kind():
    # The real regression surface: OptimizationRun.model_validate_json on old records.
    from google_meridian_mcp_server.domain.optimization import OptimizationRun

    legacy = {
        "run_id": "r1",
        "label": "l",
        "model_id": "m",
        "config": {"scenario": {"type": "fixed_budget"}},
        "config_fingerprint": "f",
        "compute_tier_requested": "auto",
        "compute_tier_resolved": "local",
        "backend": "tensorflow",
        "size_score": 1,
        "created_at": "2026-01-01T00:00:00+00:00",
        "meridian_version": "1.7.0",
        "server_version": "0.1.0",
    }
    run = OptimizationRun.model_validate_json(json.dumps(legacy))
    assert run.config.kind == "historical"
    # The `backend` key above is deliberately left in place: pydantic's default
    # extra="ignore" is what lets run JSON written before spec 6.3 removed the
    # field still deserialize off disk.
    assert not hasattr(run, "backend")


def test_future_block_rejects_nonpositive_multiplier():
    with pytest.raises(ValidationError):
        FutureOptimizationConfig.model_validate(
            {
                "scenario": {"type": "fixed_budget"},
                "future": {
                    "start_date": "2026-10-01",
                    "horizon": 4,
                    "cost_multipliers": {"tv": 0.0},
                },
            }
        )


def test_future_block_accepts_excluded_channels():
    from google_meridian_mcp_server.domain.optimization import FutureOptimizationConfig

    cfg = FutureOptimizationConfig.model_validate(
        {
            "scenario": {"type": "fixed_budget"},
            "future": {
                "start_date": "2099-01-01",
                "horizon": 4,
                "excluded_channels": ["TV"],
            },
        }
    )
    assert cfg.future.excluded_channels == ["TV"]


def test_future_block_excluded_channels_defaults_none():
    from google_meridian_mcp_server.domain.optimization import FutureBlock

    fb = FutureBlock.model_validate({"start_date": "2099-01-01", "horizon": 4})
    assert fb.excluded_channels is None


# --- the run fingerprint is salted when planned_allocation's meaning changed ---

# Fingerprints computed on 1fbd3d5, before the planned-mix rule changed, for
# model "m", Meridian "2.1.0", model_version "v1".
_PRE_RULE_FINGERPRINTS = {
    "partial": "b4f886e91dac24ab8a01ade7b9515d180b253814a7874115cb6f90a9e9f441f7",
    "full": "65b529d4c461af85064c6c1d84eb860b7f292ae1a4e7359113e57b34db58f03c",
    "future_none": "e4cf496f65615c4fc04acff651ceb4896f645d49574a6ea6ebcf33be0ecff963",
    "historical": "8e56ccb1ca28be062d3a366c53485727ddc7499cd2ebd8c1a8b92baf9eb78c23",
}


def _fingerprint_case(name):
    def future(**f):
        return FutureOptimizationConfig.model_validate(
            {
                "scenario": {"type": "fixed_budget"},
                "future": {"start_date": "2026-02-02", "horizon": 3, **f},
            }
        )

    config = {
        "partial": lambda: future(planned_allocation={"Search": 0.6, "TV": 0.3}),
        "full": lambda: future(planned_allocation={"Search": 0.5, "TV": 0.5}),
        "future_none": lambda: future(),
        "historical": lambda: OptimizationConfig.model_validate(
            {"scenario": {"type": "fixed_budget"}}
        ),
    }[name]()
    return config_fingerprint("m", config, meridian_version="2.1.0", model_version="v1")


@pytest.mark.parametrize("name", ["partial", "full"])
def test_a_planned_allocation_run_from_before_the_rule_change_is_not_reused(name):
    """A run stored under the old planned-mix rule must miss reuse, partial or
    full mix alike."""
    assert _fingerprint_case(name) != _PRE_RULE_FINGERPRINTS[name]


@pytest.mark.parametrize("name", ["future_none", "historical"])
def test_configs_without_planned_allocation_keep_their_fingerprint(name):
    assert _fingerprint_case(name) == _PRE_RULE_FINGERPRINTS[name]


def test_planned_allocation_description_states_the_zero_spend_cases():
    from google_meridian_mcp_server.domain.optimization import FutureBlock

    text = FutureBlock.model_fields["planned_allocation"].description
    assert "a left-out channel with no spend there gets 0" in text
    assert "every channel left out had no spend in the reference window" in text

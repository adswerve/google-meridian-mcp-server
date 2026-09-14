import json

import pytest
from pydantic import TypeAdapter, ValidationError

from google_meridian_mcp_server.domain.optimization import (
    AnyOptimizationConfig,
    FutureOptimizationConfig,
    OptimizationConfig,
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

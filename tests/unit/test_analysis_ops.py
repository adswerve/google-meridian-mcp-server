"""Unit tests for the worker-side analysis op dispatch (execution/analysis_ops.py).

These tests use fakes only -- no real Meridian model is loaded. Op bodies are
ported verbatim from AnalysisService; these tests check dispatch, filter
rehydration, model-validation guards, and NaN sanitization.
"""

import pytest

from google_meridian_mcp_server.domain.errors import (
    MeridianMcpError,
    MetricNotSupportedError,
    MissingModelDataError,
)
from google_meridian_mcp_server.domain.filters import AnalysisFilters
from google_meridian_mcp_server.execution import analysis_ops


class FakeFacade:
    def __init__(self, **overrides):
        self._overrides = overrides

    def get_contribution_metrics(self, filters):
        assert isinstance(filters, AnalysisFilters)  # op must rehydrate
        return [{"channel": "tv", "mean": 1.0}]

    def get_contribution_metrics_by_time(self, filters):
        assert isinstance(filters, AnalysisFilters)
        return [{"channel": "tv", "time": "2024-01-01", "mean": 1.0}]

    def has_revenue_per_kpi(self):
        return self._overrides.get("has_revenue_per_kpi", True)

    def has_rf_channels(self):
        return self._overrides.get("has_rf_channels", True)

    def geo_names(self):
        return self._overrides.get("geo_names", ["US-CA", "US-NY"])

    def get_model_fit(self, filters):
        assert isinstance(filters, AnalysisFilters)
        return [{"time": "2024-01-01", "actual": 1.0, "expected": 1.1}]

    def get_model_overview(self):
        return {"model_type": "geo", "geo_names": ["US-CA"]}

    def get_reach_frequency(self, filters):
        assert isinstance(filters, AnalysisFilters)
        return [{"channel": "tv", "mean": 1.0}]

    def get_roi(self, filters):
        assert isinstance(filters, AnalysisFilters)
        return [{"channel": "tv", "mean": 2.0}]

    def get_baseline_summary_metrics(self, filters):
        assert isinstance(filters, AnalysisFilters)
        return [{"channel": "baseline", "mean": 1.0}]


class FakeCatalog:
    def __init__(self, facade=None):
        self._facade = facade or FakeFacade()

    def get_facade(self, model_id):
        return self._facade

    def get_interrogator(self, model_id):
        return self._facade


def test_dispatch_rehydrates_filters_and_shapes_result():
    out = analysis_ops.run_operation(
        FakeCatalog(),
        "get_contribution",
        "m1",
        {"output_type": "contribution_metrics", "filters": {}},
    )
    assert out["output_type"] == "contribution_metrics" and out["rows"]


def test_unknown_op_raises():
    with pytest.raises(MeridianMcpError):
        analysis_ops.run_operation(FakeCatalog(), "nope", "m1", {})


def test_sanitize_nan():
    assert analysis_ops.sanitize_nan(
        {"a": float("nan"), "b": [float("inf"), 1.0]}
    ) == {"a": None, "b": [None, 1.0]}


def test_channel_summary_revenue_guard_raises_when_no_revenue():
    catalog = FakeCatalog(FakeFacade(has_revenue_per_kpi=False))
    with pytest.raises(MetricNotSupportedError):
        analysis_ops.run_operation(
            catalog,
            "get_channel_summary",
            "m1",
            {"output_type": "roi", "filters": {}},
        )


def test_channel_summary_non_revenue_type_bypasses_guard():
    catalog = FakeCatalog(FakeFacade(has_revenue_per_kpi=False))
    out = analysis_ops.run_operation(
        catalog,
        "get_channel_summary",
        "m1",
        {"output_type": "baseline_summary_metrics", "filters": {}},
    )
    assert out["output_type"] == "baseline_summary_metrics"


def test_reach_frequency_guard_raises_when_no_rf_channels():
    catalog = FakeCatalog(FakeFacade(has_rf_channels=False))
    with pytest.raises(MetricNotSupportedError):
        analysis_ops.run_operation(
            catalog, "get_reach_frequency", "m1", {"filters": {}}
        )


def test_model_fit_unknown_geo_raises():
    catalog = FakeCatalog(FakeFacade(geo_names=["US-CA"]))
    with pytest.raises(MissingModelDataError):
        analysis_ops.run_operation(
            catalog,
            "get_model_fit",
            "m1",
            {"filters": {"geos": ["US-ZZ"]}},
        )


def test_model_overview_returns_raw_overview_without_decoration():
    out = analysis_ops.run_operation(FakeCatalog(), "get_model_overview", "m1", {})
    assert out["model_id"] == "m1"
    assert out["model_type"] == "geo"
    assert "available_tool_options" not in out


class _FakePosterior:
    sizes = {"chain": 2, "draw": 100}


class _FakeInferenceData:
    posterior = _FakePosterior()


class _FakeMmm:
    inference_data = _FakeInferenceData()


class FakeOptimizerFacade:
    _mmm = _FakeMmm()

    def __init__(self, *, valid=True, kind="historical"):
        self._valid = valid
        self._kind = kind

    def channel_order(self):
        return ["tv", "search"]

    def has_revenue_per_kpi(self):
        return True

    def resolve_use_kpi(self, config):
        return False

    def get_data_inputs(self):
        return {"media": ["tv", "search"], "rf_media": []}

    def geo_names(self):
        return ["US-CA", "US-NY"]

    def get_time_values(self):
        return ["2024-01-01", "2024-01-08"]

    def validate_future(self, config):
        if not self._valid:
            raise ValueError("future config invalid")


class FakeOptimizerCatalog:
    def __init__(self, facade):
        self._facade = facade

    def get_optimizer_facade(self, model_id):
        return self._facade


def test_preflight_optimization_returns_expected_keys_for_valid_historical_config():
    catalog = FakeOptimizerCatalog(FakeOptimizerFacade())
    out = analysis_ops.run_operation(
        catalog,
        "preflight_optimization",
        "m1",
        {"config": {"scenario": {"type": "fixed_budget"}}},
    )
    assert out["channel_order"] == ["tv", "search"]
    assert out["has_revenue_per_kpi"] is True
    assert out["use_kpi"] is False
    assert out["size_features"] == {
        "n_geos": 2,
        "n_time_units": 2,
        "n_channels": 2,
        "n_posterior_samples": 200,
    }
    assert out["validation_error"] is None


def test_preflight_optimization_reports_validation_error_for_invalid_future_config():
    catalog = FakeOptimizerCatalog(FakeOptimizerFacade(valid=False))
    out = analysis_ops.run_operation(
        catalog,
        "preflight_optimization",
        "m1",
        {
            "config": {
                "kind": "future",
                "scenario": {"type": "fixed_budget"},
                "future": {"start_date": "2099-01-01", "horizon": 4},
            }
        },
    )
    assert out["validation_error"] is not None
    assert out["validation_error"]["error_code"] == "invalid_optimization_config"
    # channel_order/size_features still populated -- worker doesn't abort.
    assert out["channel_order"] == ["tv", "search"]

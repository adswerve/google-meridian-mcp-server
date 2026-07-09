"""Unit tests for the worker-side analysis op dispatch (execution/analysis_ops.py).

These tests use fakes only -- no real Meridian model is loaded. Op bodies are
ported verbatim from AnalysisService; these tests check dispatch, filter
rehydration, model-validation guards, and NaN sanitization.
"""

import json

import pytest

from google_meridian_mcp_server.domain.errors import (
    InvalidOutputTypeError,
    MeridianMcpError,
    MetricNotSupportedError,
    MissingModelDataError,
)
from google_meridian_mcp_server.domain.filters import AnalysisFilters
from google_meridian_mcp_server.execution import analysis_ops, worker


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


def test_invalid_output_type_raises_invalid_output_type_not_keyerror():
    # Critical faithful-port guard: an unknown output_type on a dispatch op must
    # raise InvalidOutputTypeError (rc 0, invalid_output_type + valid_types in
    # details), NOT a bare KeyError (which would surface as internal_error rc 1).
    with pytest.raises(InvalidOutputTypeError) as excinfo:
        analysis_ops.run_operation(
            FakeCatalog(),
            "get_contribution",
            "m1",
            {"output_type": "bogus_type", "filters": {}},
        )
    assert "contribution_metrics" in excinfo.value.details["valid_types"]


# --- get_channel_data / get_training_data (extractor-backed ops) -------------


class FakeExtractorCatalog:
    """Catalog whose resolve() returns a sentinel; extractors are monkeypatched."""

    def __init__(self, sentinel=object()):
        self._sentinel = sentinel

    def resolve(self, model_id):
        return self._sentinel


def test_get_channel_data_extracts_filters_and_shapes_result(monkeypatch):
    rows = [
        {"channel": "tv", "geo": "US-CA", "spend": 10.0},
        {"channel": "search", "geo": "US-NY", "spend": 20.0},
    ]
    monkeypatch.setattr(analysis_ops, "extract_channel_data", lambda mmm: rows)
    captured = {}

    def fake_filter_records(records, **kwargs):
        captured.update(kwargs)
        return records

    monkeypatch.setattr(analysis_ops, "filter_records", fake_filter_records)

    out = analysis_ops.run_operation(
        FakeExtractorCatalog(),
        "get_channel_data",
        "m1",
        {"filters": {"geos": ["US-CA"]}},
    )
    assert out["model_id"] == "m1"
    assert out["row_count"] == 2
    assert "channel" in out["columns"]
    # filter_records received the rehydrated filter dims.
    assert captured["geos"] == ["US-CA"]


def test_get_training_data_reports_datasets_and_single_dataset(monkeypatch):
    rows = [{"time": "2024-01-01", "geo": "US-CA", "kpi": 1.0}]
    monkeypatch.setattr(
        analysis_ops, "extract_training_datasets", lambda mmm, datasets: rows
    )
    monkeypatch.setattr(
        analysis_ops, "filter_records", lambda records, **kwargs: records
    )

    out = analysis_ops.run_operation(
        FakeExtractorCatalog(),
        "get_training_data",
        "m1",
        {"datasets": ["kpi"], "filters": {}},
    )
    assert out["datasets"] == ["kpi"]
    assert out["dataset"] == "kpi"  # single dataset -> dataset field set
    assert out["row_count"] == 1


def test_get_training_data_multiple_datasets_omits_dataset_field(monkeypatch):
    rows = [{"time": "2024-01-01", "kpi": 1.0, "media": 2.0}]
    monkeypatch.setattr(
        analysis_ops, "extract_training_datasets", lambda mmm, datasets: rows
    )
    monkeypatch.setattr(
        analysis_ops, "filter_records", lambda records, **kwargs: records
    )

    out = analysis_ops.run_operation(
        FakeExtractorCatalog(),
        "get_training_data",
        "m1",
        {"datasets": ["kpi", "media"], "filters": {}},
    )
    assert out["datasets"] == ["kpi", "media"]
    assert "dataset" not in out  # >1 dataset -> no single dataset field


# --- get_spend_scenario (guard-heavy) ----------------------------------------


class FakeSpendFacade:
    def __init__(self, *, media=("tv",), rf_media=(), use_kpi=False):
        self._media = list(media)
        self._rf_media = list(rf_media)
        self._use_kpi = use_kpi

    def get_data_inputs(self):
        return {"media": self._media, "rf_media": self._rf_media}

    def resolve_use_kpi(self, filters):
        assert isinstance(filters, AnalysisFilters)
        return self._use_kpi

    def resolve_base_spend(self, channel, filters):
        assert isinstance(filters, AnalysisFilters)
        return 100.0

    def spend_response(self, channel, spends, filters):
        assert isinstance(filters, AnalysisFilters)
        # base outcome then new outcome; means scale with spend for realism.
        return [
            {"mean": spends[0] * 2.0},
            {"mean": spends[1] * 2.0},
        ]


class FakeSpendCatalog:
    def __init__(self, facade):
        self._facade = facade

    def get_facade(self, model_id):
        return self._facade

    def get_interrogator(self, model_id):
        return self._facade


def test_get_spend_scenario_happy_path():
    catalog = FakeSpendCatalog(FakeSpendFacade(media=("tv",)))
    out = analysis_ops.run_operation(
        catalog,
        "get_spend_scenario",
        "m1",
        {
            "channel": "tv",
            "spend_increase": 50.0,
            "base_spend": None,
            "filters": {},
        },
    )
    assert out["channel"] == "tv"
    assert out["channel_type"] == "paid_media"
    assert out["outcome_mode"] == "revenue"
    assert out["base_spend"] == 100.0
    assert out["new_spend"] == 150.0
    assert out["base_outcome"]["mean"] == 200.0
    assert out["new_outcome"]["mean"] == 300.0


def test_get_spend_scenario_rf_channel_type():
    catalog = FakeSpendCatalog(FakeSpendFacade(media=(), rf_media=("video",)))
    out = analysis_ops.run_operation(
        catalog,
        "get_spend_scenario",
        "m1",
        {"channel": "video", "spend_increase": 10.0, "base_spend": 100.0, "filters": {}},
    )
    assert out["channel_type"] == "rf"


def test_get_spend_scenario_unknown_channel_raises():
    catalog = FakeSpendCatalog(FakeSpendFacade(media=("tv",)))
    with pytest.raises(MissingModelDataError):
        analysis_ops.run_operation(
            catalog,
            "get_spend_scenario",
            "m1",
            {"channel": "ghost", "spend_increase": 10.0, "base_spend": None, "filters": {}},
        )


def test_get_spend_scenario_nonpositive_base_spend_raises():
    catalog = FakeSpendCatalog(FakeSpendFacade(media=("tv",)))
    with pytest.raises(MissingModelDataError):
        analysis_ops.run_operation(
            catalog,
            "get_spend_scenario",
            "m1",
            {"channel": "tv", "spend_increase": 10.0, "base_spend": 0.0, "filters": {}},
        )


# --- worker.run_analysis IPC contract ----------------------------------------


class _NanFacade:
    def get_contribution_metrics(self, filters):
        assert isinstance(filters, AnalysisFilters)
        return [{"channel": "tv", "mean": float("nan")}]


class _NanCatalog:
    def get_facade(self, model_id):
        return _NanFacade()

    def get_interrogator(self, model_id):
        return _NanFacade()


class _BoomCatalog:
    """A catalog whose facade lookup raises a plain (non-domain) Exception."""

    def get_facade(self, model_id):
        raise RuntimeError("unexpected boom")

    def get_interrogator(self, model_id):
        raise RuntimeError("unexpected boom")


def _write_request(tmp_path, payload):
    req = tmp_path / "req.json"
    req.write_text(json.dumps(payload))
    return str(req), str(tmp_path / "resp.json")


def test_run_analysis_success_writes_ok_payload(tmp_path):
    req_path, resp_path = _write_request(
        tmp_path,
        {
            "operation": "get_contribution",
            "model_id": "m1",
            "params": {"output_type": "contribution_metrics", "filters": {}},
        },
    )
    rc = worker.run_analysis(req_path, resp_path, catalog=FakeCatalog())
    assert rc == 0
    payload = json.loads(open(resp_path).read())
    assert payload["ok"] is True
    assert payload["result"]["output_type"] == "contribution_metrics"


def test_run_analysis_domain_error_writes_error_payload_rc0(tmp_path):
    # Unknown op -> InvalidOutputTypeError (a MeridianMcpError) -> ok False, rc 0.
    req_path, resp_path = _write_request(
        tmp_path,
        {"operation": "nope", "model_id": "m1", "params": {}},
    )
    rc = worker.run_analysis(req_path, resp_path, catalog=FakeCatalog())
    assert rc == 0
    payload = json.loads(open(resp_path).read())
    assert payload["ok"] is False
    assert payload["error"]["error_code"] == "invalid_output_type"


def test_run_analysis_unexpected_exception_writes_internal_error_rc1(tmp_path):
    req_path, resp_path = _write_request(
        tmp_path,
        {
            "operation": "get_contribution",
            "model_id": "m1",
            "params": {"output_type": "contribution_metrics", "filters": {}},
        },
    )
    rc = worker.run_analysis(req_path, resp_path, catalog=_BoomCatalog())
    assert rc == 1
    payload = json.loads(open(resp_path).read())
    assert payload["ok"] is False
    assert payload["error"]["error_code"] == "internal_error"


def test_run_analysis_sanitizes_nan_to_null(tmp_path):
    req_path, resp_path = _write_request(
        tmp_path,
        {
            "operation": "get_contribution",
            "model_id": "m1",
            "params": {"output_type": "contribution_metrics", "filters": {}},
        },
    )
    rc = worker.run_analysis(req_path, resp_path, catalog=_NanCatalog())
    assert rc == 0
    raw = open(resp_path).read()
    # allow_nan=False path produced valid strict JSON (no NaN literal).
    assert "NaN" not in raw
    payload = json.loads(raw)  # would raise if NaN literal leaked
    assert payload["ok"] is True
    # the nan cell round-tripped to null.
    assert payload["result"]["rows"] == [["tv", None]]

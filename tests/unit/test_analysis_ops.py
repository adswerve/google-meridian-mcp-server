"""Unit tests for the worker-side analysis op dispatch (execution/analysis_ops.py).

These tests use fakes only -- no real Meridian model is loaded. Op bodies are
ported verbatim from AnalysisService; these tests check dispatch, filter
rehydration, model-validation guards, and NaN sanitization.
"""

import json
from types import SimpleNamespace

import pytest
import xarray as xr

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
    assert analysis_ops.sanitize_nan({"a": float("nan"), "b": [float("inf"), 1.0]}) == {
        "a": None,
        "b": [None, 1.0],
    }


# --- generic dispatch coverage across all 4 dispatch-table ops ---------------
# (migrated from tests/unit/test_analysis_service.py::TestAnalysisServiceDispatch
# -- Task 10 moved the facade dispatch itself worker-side, so this now
# exercises analysis_ops.run_operation directly against a fake facade.)


class _GenericDispatchFacade:
    def __init__(self):
        self.calls: list[str] = []

    def has_revenue_per_kpi(self) -> bool:
        return True

    def __getattr__(self, name: str):
        if name.startswith("get_"):
            return self._make(name)
        raise AttributeError(name)

    def _make(self, name: str):
        def _method(filters):
            self.calls.append(name)
            return [{"method": name, "filters": filters.model_dump(mode="json")}]

        return _method


class _GenericDispatchCatalog:
    def __init__(self, facade):
        self._facade = facade

    def get_facade(self, model_id):
        return self._facade

    def get_interrogator(self, model_id):
        return self._facade


@pytest.mark.parametrize(
    ("operation", "output_type", "expected_method"),
    [
        ("get_channel_summary", "roi", "get_roi"),
        ("get_contribution", "contribution_metrics", "get_contribution_metrics"),
        ("get_adstock_decay", "alpha_summary", "get_alpha_summary"),
        (
            "get_response_curves",
            "response_curve_summary",
            "get_response_curve_summary",
        ),
    ],
)
def test_dispatch_ops_route_to_expected_facade_method(
    operation: str, output_type: str, expected_method: str
):
    facade = _GenericDispatchFacade()
    out = analysis_ops.run_operation(
        _GenericDispatchCatalog(facade),
        operation,
        "m1",
        {"output_type": output_type, "filters": {"channels": ["tv"]}},
    )

    assert facade.calls == [expected_method]
    assert out["output_type"] == output_type
    assert out["columns"] == ["method", "filters"]
    assert out["rows"][0][0] == expected_method
    assert out["rows"][0][1]["channels"] == ["tv"]


@pytest.mark.parametrize(
    ("operation", "output_type", "failing_method"),
    [
        ("get_channel_summary", "roi", "get_roi"),
        ("get_contribution", "contribution_metrics", "get_contribution_metrics"),
        ("get_adstock_decay", "alpha_summary", "get_alpha_summary"),
        (
            "get_response_curves",
            "response_curve_summary",
            "get_response_curve_summary",
        ),
    ],
)
def test_dispatch_ops_wrap_facade_exceptions_as_missing_model_data(
    operation: str, output_type: str, failing_method: str
):
    class _BoomFacade:
        def has_revenue_per_kpi(self):
            return True

        def __getattr__(self, name):
            if name == failing_method:

                def _boom(filters):
                    raise RuntimeError("missing rows")

                return _boom
            raise AttributeError(name)

    catalog = _GenericDispatchCatalog(_BoomFacade())
    with pytest.raises(MissingModelDataError, match="missing rows"):
        analysis_ops.run_operation(
            catalog, operation, "m1", {"output_type": output_type, "filters": {}}
        )


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


def test_reach_frequency_returns_columnar_when_rf_present():
    # migrated from tests/unit/test_analysis_service.py
    # ::test_reach_frequency_columnar_when_rf_present -- the facade call is
    # now worker-side.
    catalog = FakeCatalog(FakeFacade(has_rf_channels=True))
    out = analysis_ops.run_operation(
        catalog, "get_reach_frequency", "m1", {"filters": {}}
    )
    assert out["row_count"] == 1
    assert "channel" in out["columns"]


def test_model_fit_unknown_geo_raises():
    catalog = FakeCatalog(FakeFacade(geo_names=["US-CA"]))
    with pytest.raises(MissingModelDataError):
        analysis_ops.run_operation(
            catalog,
            "get_model_fit",
            "m1",
            {"filters": {"geos": ["US-ZZ"]}},
        )


def test_model_fit_accepts_known_geo_and_returns_columnar():
    # migrated from tests/unit/test_analysis_service.py
    # ::test_get_model_fit_accepts_known_geo / ::test_get_model_fit_returns_columnar
    catalog = FakeCatalog(FakeFacade(geo_names=["US-CA"]))
    out = analysis_ops.run_operation(
        catalog, "get_model_fit", "m1", {"filters": {"geos": ["US-CA"]}}
    )
    assert out["row_count"] == 1
    assert "actual" in out["columns"] and "expected" in out["columns"]


def test_model_overview_returns_raw_overview_without_decoration():
    out = analysis_ops.run_operation(FakeCatalog(), "get_model_overview", "m1", {})
    assert out["model_id"] == "m1"
    assert out["model_type"] == "geo"
    assert "available_tool_options" not in out


class _RealInterrogatorCatalog:
    """Wraps a real MeridianInterrogator over an xr-based fake model, so the
    op's ``get_model_overview`` call exercises the actual field-extraction
    logic (migrated from tests/unit/test_analysis_service.py
    ::TestModelOverview -- Task 10 moved the interrogator call worker-side;
    the pure ``available_tool_options`` decoration it used to also assert on
    now lives in AnalysisService._decorate_overview and is covered there)."""

    def __init__(self, model):
        self._model = model

    def get_interrogator(self, model_id):
        from google_meridian_mcp_server.meridian.interrogator import (
            MeridianInterrogator,
        )

        return MeridianInterrogator(self._model)


def _build_overview_model():
    time = xr.DataArray(
        ["2024-01-01", "2024-01-08"],
        coords={"time": ["2024-01-01", "2024-01-08"]},
        dims=("time",),
    )
    geo = xr.DataArray(["us", "ca"], coords={"geo": ["us", "ca"]}, dims=("geo",))
    population = xr.DataArray([100, 200], coords={"geo": ["us", "ca"]}, dims=("geo",))
    media_channel = xr.DataArray(
        ["search", "tv"],
        coords={"media_channel": ["search", "tv"]},
        dims=("media_channel",),
    )
    rf_channel = xr.DataArray(
        ["youtube"], coords={"rf_channel": ["youtube"]}, dims=("rf_channel",)
    )
    non_media_channel = xr.DataArray(
        ["promo"], coords={"non_media_channel": ["promo"]}, dims=("non_media_channel",)
    )
    organic_media_channel = xr.DataArray(
        ["email"],
        coords={"organic_media_channel": ["email"]},
        dims=("organic_media_channel",),
    )
    organic_rf_channel = xr.DataArray(
        ["podcast"],
        coords={"organic_rf_channel": ["podcast"]},
        dims=("organic_rf_channel",),
    )
    control_variable = xr.DataArray(
        ["price"], coords={"control_variable": ["price"]}, dims=("control_variable",)
    )
    kpi = xr.DataArray(
        [[10.0, 12.0]],
        coords={"geo": ["us"], "time": ["2024-01-01", "2024-01-08"]},
        dims=("geo", "time"),
    )
    return SimpleNamespace(
        is_national=False,
        input_data=SimpleNamespace(
            time=time,
            geo=geo,
            population=population,
            media_channel=media_channel,
            rf_channel=rf_channel,
            non_media_channel=non_media_channel,
            organic_media_channel=organic_media_channel,
            organic_rf_channel=organic_rf_channel,
            control_variable=control_variable,
            kpi=kpi,
            revenue_per_kpi=object(),
            media=object(),
            media_spend=object(),
            reach=object(),
            frequency=object(),
            rf_spend=object(),
            organic_media=object(),
            organic_reach=object(),
            organic_frequency=object(),
            non_media_treatments=object(),
            controls=object(),
        ),
    )


def test_model_overview_exposes_model_setup_from_real_interrogator():
    catalog = _RealInterrogatorCatalog(_build_overview_model())

    out = analysis_ops.run_operation(catalog, "get_model_overview", "m1", {})

    assert out["model_id"] == "m1"
    assert out["model_type"] == "geo"
    assert out["time"] == {
        "start": "2024-01-01",
        "end": "2024-01-08",
        "count": 2,
        "values": ["2024-01-01", "2024-01-08"],
    }
    assert out["geo_names"] == ["ca", "us"]
    assert out["total_population"] == 300
    assert out["media_channels"] == ["search", "tv"]
    assert out["rf_channels"] == ["youtube"]
    assert out["total_channels"] == 3
    assert out["data_inputs"]["organic_media"] == ["email"]
    assert out["data_inputs"]["organic_rf_media"] == ["podcast"]
    assert out["data_schema"]["rf_media"]["spend"] == ["youtube_rf_spend"]
    assert "search_spend" in out["input_column_names"]
    assert "youtube_frequency" in out["input_column_names"]
    assert out["available_training_datasets"] == [
        "kpi",
        "revenue_per_kpi",
        "population",
        "media",
        "media_spend",
        "reach",
        "frequency",
        "rf_spend",
        "organic_media",
        "organic_reach",
        "organic_frequency",
        "non_media_treatments",
        "controls",
    ]
    assert out["metric_views"] == ["kpi", "revenue"]
    # No decoration worker-side (Task 10): that's applied server-side, pure.
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
        {
            "channel": "video",
            "spend_increase": 10.0,
            "base_spend": 100.0,
            "filters": {},
        },
    )
    assert out["channel_type"] == "rf"


def test_get_spend_scenario_uses_provided_base_spend_over_resolve():
    # migrated from tests/unit/test_analysis_service.py
    # ::test_spend_scenario_uses_provided_base_spend -- resolve_base_spend
    # would return 100.0 (FakeSpendFacade default); an explicit base_spend
    # must be used instead and resolve_base_spend must not be consulted.
    catalog = FakeSpendCatalog(FakeSpendFacade(media=("search",)))
    out = analysis_ops.run_operation(
        catalog,
        "get_spend_scenario",
        "m1",
        {
            "channel": "search",
            "spend_increase": 20.0,
            "base_spend": 50.0,
            "filters": {},
        },
    )
    assert out["base_spend"] == 50.0
    assert out["new_spend"] == 70.0


def test_get_spend_scenario_summary_contract():
    """get_spend_scenario returns exactly the 15 documented summary keys.

    Migrated from tests/contract/test_analysis_tools.py
    ::TestAnalysisToolContracts.test_spend_scenario_summary_contract -- the
    facade call + summary building now happens worker-side (Task 10)."""
    expected_keys = frozenset(
        {
            "model_id",
            "channel",
            "channel_type",
            "outcome_mode",
            "base_spend",
            "spend_increase",
            "new_spend",
            "spend_increase_pct",
            "base_outcome",
            "new_outcome",
            "expected_outcome_increase",
            "expected_outcome_increase_pct",
            "efficiency",
            "marginal_efficiency",
            "efficiency_at_new",
        }
    )

    class _Facade:
        def get_data_inputs(self):
            return {"media": ["search"], "rf_media": []}

        def resolve_use_kpi(self, filters):
            return False  # revenue mode

        def resolve_base_spend(self, channel, filters):
            return 100.0

        def spend_response(self, channel, points, filters):
            return [
                {"mean": 400.0, "ci_lo": 350.0, "ci_hi": 450.0},
                {"mean": 460.0, "ci_lo": 400.0, "ci_hi": 520.0},
            ]

    class _Catalog:
        def get_facade(self, model_id):
            return _Facade()

    summary = analysis_ops.run_operation(
        _Catalog(),
        "get_spend_scenario",
        "test-model",
        {
            "channel": "search",
            "spend_increase": 20.0,
            "base_spend": None,
            "filters": {},
        },
    )

    assert set(summary.keys()) == expected_keys
    assert len(summary) == 15
    assert summary["outcome_mode"] in {"revenue", "kpi"}


def test_get_spend_scenario_unknown_channel_raises():
    catalog = FakeSpendCatalog(FakeSpendFacade(media=("tv",)))
    with pytest.raises(MissingModelDataError):
        analysis_ops.run_operation(
            catalog,
            "get_spend_scenario",
            "m1",
            {
                "channel": "ghost",
                "spend_increase": 10.0,
                "base_spend": None,
                "filters": {},
            },
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


def test_sanitize_nan_handles_numpy_bool_ndarray_datetime_and_bytes():
    """F7: sanitize_nan must handle anything else json.dump would otherwise
    crash on: np.bool_, np.ndarray (recursively, including embedded NaN),
    datetime/date (-> ISO 8601 string), and bytes (-> utf-8 decoded)."""
    import datetime as dt

    import numpy as np

    out = analysis_ops.sanitize_nan(
        {
            "flag": np.bool_(True),
            "arr": np.array([1.0, float("nan"), 3.0]),
            "when": dt.datetime(2026, 1, 1, 12, 0, 0),
            "day": dt.date(2026, 1, 2),
            "raw": b"hello",
        }
    )
    assert out["flag"] is True and isinstance(out["flag"], bool)
    assert out["arr"] == [1.0, None, 3.0]
    assert out["when"] == "2026-01-01T12:00:00"
    assert out["day"] == "2026-01-02"
    assert out["raw"] == "hello"


class _UnserializableFacade:
    """Returns a value _round_measure passes through untouched (not a bool
    or float) and sanitize_nan doesn't recognize either -- json.dump chokes
    on it, exercising run_analysis's fallback payload path."""

    def get_contribution_metrics(self, filters):
        return [{"channel": "tv", "mean": object()}]


class _UnserializableCatalog:
    def get_facade(self, model_id):
        return _UnserializableFacade()

    def get_interrogator(self, model_id):
        return _UnserializableFacade()


def test_run_analysis_unserializable_result_falls_back_to_internal_error(tmp_path):
    """F7: if sanitize_nan/json.dump itself raises (an op returned a value
    sanitize_nan doesn't know how to handle), run_analysis must still write a
    minimal, ALWAYS-serializable internal_error payload and return rc 1 --
    not leave "no response" behind, which the caller would misread as the
    worker never having run at all rather than having run and failed to
    report back cleanly."""
    req_path, resp_path = _write_request(
        tmp_path,
        {
            "operation": "get_contribution",
            "model_id": "m1",
            "params": {"output_type": "contribution_metrics", "filters": {}},
        },
    )
    rc = worker.run_analysis(req_path, resp_path, catalog=_UnserializableCatalog())
    assert rc == 1
    payload = json.loads(open(resp_path).read())  # must be valid JSON
    assert payload == {
        "ok": False,
        "error": {
            "error_code": "internal_error",
            "message": "TypeError",
            "details": {},
        },
    }


def test_run_analysis_writes_compact_separators(tmp_path):
    """The bytes on disk must equal the compact serialization exactly.
    Reverting to default separators makes resp.json 9.9% larger for no
    benefit -- every byte crosses the wire and lands in an agent's context."""
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
    written = open(resp_path).read()
    assert written == json.dumps(
        json.loads(written), separators=(",", ":"), allow_nan=False
    )

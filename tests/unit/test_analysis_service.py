"""Unit tests for analysis service orchestration and validation."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest import mock

import pytest
import xarray as xr
from pydantic import ValidationError

from google_meridian_mcp_server.domain.errors import (
    DatasetNotAvailableError,
    InvalidOutputTypeError,
    MetricNotSupportedError,
    MissingModelDataError,
)
from google_meridian_mcp_server.domain.filters import AnalysisFilters, normalize_filters
from google_meridian_mcp_server.persistence.cache import ResultCache
from google_meridian_mcp_server.services.analysis_service import AnalysisService


class _FakeInterrogator:
    def __init__(self, has_revenue):
        self._has_revenue = has_revenue

    def has_revenue_per_kpi(self):
        return self._has_revenue


class _FakeCatalog:
    def __init__(self, has_revenue):
        self._interrogator = _FakeInterrogator(has_revenue)

    def get_interrogator(self, model_id):
        return self._interrogator


@pytest.mark.parametrize("output_type", ["roi", "marginal_roi"])
def test_channel_summary_rejects_roi_on_no_revenue_model(output_type):
    service = AnalysisService(catalog=_FakeCatalog(has_revenue=False))
    with pytest.raises(MetricNotSupportedError) as exc:
        service.get_channel_summary("kpi-only", output_type, None)
    assert exc.value.error_code == "metric_not_supported"
    assert exc.value.details["output_type"] == output_type


class _FakeNoRevenueCpikCatalog:
    """Catalog stub for no-revenue models that supports cpik/marginal_cpik dispatch."""

    class _Interrogator:
        def has_revenue_per_kpi(self):
            return False

    class _Facade:
        def get_cpik(self, filters):
            return [{"channel": "tv", "cpik": 0.5}]

        def get_marginal_cpik(self, filters):
            return [{"channel": "tv", "marginal_cpik": 0.6}]

    def get_interrogator(self, model_id):
        return self._Interrogator()

    def get_facade(self, model_id):
        return self._Facade()


@pytest.mark.parametrize("output_type", ["cpik", "marginal_cpik"])
def test_channel_summary_allows_cpik_on_no_revenue_model(output_type):
    service = AnalysisService(catalog=_FakeNoRevenueCpikCatalog())
    result = service.get_channel_summary("kpi-only", output_type, None)
    assert result["output_type"] == output_type
    assert result["row_count"] == 1


class _StubCatalog:
    def __init__(self, model):
        self._model = model

    def resolve(self, model_id: str):
        assert model_id == "m1"
        return self._model

    def get_interrogator(self, model_id: str):
        assert model_id == "m1"
        from google_meridian_mcp_server.meridian.interrogator import (
            MeridianInterrogator,
        )

        return MeridianInterrogator(self._model)


def _build_training_model():
    kpi = xr.DataArray(
        [[10.0, 12.0]],
        coords={"geo": ["us"], "time": ["2024-01-01", "2024-01-08"]},
        dims=("geo", "time"),
    )
    media_spend = xr.DataArray(
        [[[1.0, 2.0], [3.0, 4.0]]],
        coords={
            "geo": ["us"],
            "time": ["2024-01-01", "2024-01-08"],
            "channel": ["search", "tv"],
        },
        dims=("geo", "time", "channel"),
    )
    return SimpleNamespace(
        input_data=SimpleNamespace(
            kpi=kpi,
            media_spend=media_spend,
            controls=None,
        )
    )


def _build_analysis_service() -> AnalysisService:
    return AnalysisService(
        catalog=_StubCatalog(_build_training_model()),
        result_cache=ResultCache(enabled=False, ttl_seconds=None),
    )


class TestNormalizeFilters:
    def test_empty_input_returns_defaults(self):
        f = normalize_filters(None)
        assert f.aggregate_times is True
        assert f.geos == []
        assert f.channels == []

    def test_passes_through_valid_fields(self):
        raw = {
            "start_date": "2024-01-01",
            "end_date": "2024-12-31",
            "geos": ["us", "uk"],
            "channels": ["tv", "search"],
            "aggregate_times": False,
            "include_non_paid": True,
            "use_kpi": True,
        }
        f = normalize_filters(raw)
        assert f.start_date == date(2024, 1, 1)
        assert f.end_date == date(2024, 12, 31)
        assert f.geos == ["us", "uk"]
        assert f.channels == ["tv", "search"]
        assert f.aggregate_times is False
        assert f.include_non_paid is True
        assert f.use_kpi is True

    def test_null_lists_become_empty(self):
        f = normalize_filters({"geos": None, "channels": None})
        assert f.geos == []
        assert f.channels == []

    def test_deduplicates_and_trims_filter_lists(self):
        f = normalize_filters({"channels": [" tv ", "search", "tv", ""]})
        assert f.channels == ["tv", "search"]

    def test_rejects_unknown_filter_fields(self):
        with pytest.raises(ValidationError):
            normalize_filters({"unexpected": True})

    def test_rejects_invalid_date_ranges(self):
        with pytest.raises(ValidationError):
            normalize_filters(
                {
                    "start_date": "2024-12-31",
                    "end_date": "2024-01-01",
                }
            )

    def test_rejects_non_list_channel_filters(self):
        with pytest.raises(TypeError):
            normalize_filters({"channels": "search"})

    def test_rejects_non_string_filter_values(self):
        with pytest.raises(TypeError):
            normalize_filters({"geos": ["us", 3]})


class TestInvalidOutputType:
    def test_error_includes_valid_types(self):
        err = InvalidOutputTypeError("bad_type", ["roi", "cpik"])
        assert err.error_code == "invalid_output_type"
        assert "bad_type" in str(err)
        assert err.details["valid_types"] == ["roi", "cpik"]


class TestAnalysisFiltersImmutability:
    def test_frozen_dataclass(self):
        f = AnalysisFilters(start_date="2024-01-01")
        with pytest.raises(ValidationError):
            f.start_date = "2025-01-01"


class TestTrainingDataSelection:
    def test_get_training_data_merges_multiple_selected_datasets(self):
        result = _build_analysis_service().get_training_data(
            "m1", ["kpi", "media_spend"], None
        )

        assert result["datasets"] == ["kpi", "media_spend"]
        assert "dataset" not in result
        assert "result_metadata" not in result
        assert "data" not in result
        assert result["row_count"] == 4
        assert result["columns"] == ["geo", "time", "channel", "kpi", "media_spend"]
        assert len(result["rows"]) == 4
        assert all(len(row) == len(result["columns"]) for row in result["rows"])

    def test_get_training_data_deduplicates_dataset_selection(self):
        result = _build_analysis_service().get_training_data("m1", ["kpi", "kpi"], None)

        assert result["dataset"] == "kpi"
        assert result["datasets"] == ["kpi"]
        assert result["row_count"] == 2
        assert result["columns"] == ["geo", "time", "kpi"]
        assert "result_metadata" not in result
        assert len(result["rows"]) == 2

    def test_get_training_data_rejects_unknown_dataset(self):
        with pytest.raises(DatasetNotAvailableError):
            _build_analysis_service().get_training_data("m1", ["unknown"], None)


class TestModelOverview:
    def test_get_model_overview_exposes_model_setup_and_tool_options(self):
        time = xr.DataArray(
            ["2024-01-01", "2024-01-08"],
            coords={"time": ["2024-01-01", "2024-01-08"]},
            dims=("time",),
        )
        geo = xr.DataArray(["us", "ca"], coords={"geo": ["us", "ca"]}, dims=("geo",))
        population = xr.DataArray(
            [100, 200], coords={"geo": ["us", "ca"]}, dims=("geo",)
        )
        media_channel = xr.DataArray(
            ["search", "tv"],
            coords={"media_channel": ["search", "tv"]},
            dims=("media_channel",),
        )
        rf_channel = xr.DataArray(
            ["youtube"],
            coords={"rf_channel": ["youtube"]},
            dims=("rf_channel",),
        )
        non_media_channel = xr.DataArray(
            ["promo"],
            coords={"non_media_channel": ["promo"]},
            dims=("non_media_channel",),
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
            ["price"],
            coords={"control_variable": ["price"]},
            dims=("control_variable",),
        )
        kpi = xr.DataArray(
            [[10.0, 12.0]],
            coords={"geo": ["us"], "time": ["2024-01-01", "2024-01-08"]},
            dims=("geo", "time"),
        )

        model = SimpleNamespace(
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
        service = AnalysisService(
            catalog=_StubCatalog(model),
            result_cache=ResultCache(enabled=False, ttl_seconds=None),
        )

        result = service.get_model_overview("m1")

        assert result["model_id"] == "m1"
        assert result["model_type"] == "geo"
        assert result["time"] == {
            "start": "2024-01-01",
            "end": "2024-01-08",
            "count": 2,
            "values": ["2024-01-01", "2024-01-08"],
        }
        assert result["geo_names"] == ["ca", "us"]
        assert result["total_population"] == 300
        assert result["media_channels"] == ["search", "tv"]
        assert result["rf_channels"] == ["youtube"]
        assert result["total_channels"] == 3
        assert result["data_inputs"]["organic_media"] == ["email"]
        assert result["data_inputs"]["organic_rf_media"] == ["podcast"]
        assert result["data_schema"]["rf_media"]["spend"] == ["youtube_rf_spend"]
        assert "search_spend" in result["input_column_names"]
        assert "youtube_frequency" in result["input_column_names"]
        assert result["available_training_datasets"] == [
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
        assert result["metric_views"] == ["kpi", "revenue"]
        assert result["available_tool_options"]["get_channel_summary"] == {
            "output_type": [
                "baseline_summary_metrics",
                "paid_summary_metrics",
                "roi",
                "cpik",
                "marginal_roi",
                "marginal_cpik",
            ]
        }
        assert result["available_tool_options"]["get_adstock_decay"] == {
            "output_type": ["adstock_decay", "alpha_summary"]
        }
        assert "result_metadata" not in result


class _DispatchFacade:
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


class _DispatchCatalog:
    def __init__(self, facade, interrogator=None):
        self.facade = facade
        self.interrogator = interrogator or facade
        self.get_facade = mock.Mock(return_value=facade)
        self.get_interrogator = mock.Mock(return_value=self.interrogator)


class TestAnalysisServiceDispatch:
    @pytest.mark.parametrize(
        ("method_name", "output_type", "expected_method"),
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
    def test_dispatches_to_expected_facade_method(
        self, method_name: str, output_type: str, expected_method: str
    ):
        facade = _DispatchFacade()
        service = AnalysisService(
            catalog=_DispatchCatalog(facade),
            result_cache=ResultCache(enabled=False, ttl_seconds=None),
        )

        result = getattr(service, method_name)("m1", output_type, {"channels": ["tv"]})

        assert facade.calls == [expected_method]
        assert result["output_type"] == output_type
        assert "result_metadata" not in result
        assert result["columns"] == ["method", "filters"]
        assert result["rows"][0][0] == expected_method
        assert result["rows"][0][1]["channels"] == ["tv"]

    @pytest.mark.parametrize(
        ("method_name", "output_type"),
        [
            ("get_channel_summary", "roi"),
            ("get_contribution", "contribution_metrics"),
            ("get_adstock_decay", "alpha_summary"),
            ("get_response_curves", "response_curve_summary"),
        ],
    )
    def test_wraps_facade_exceptions_as_missing_model_data(
        self, method_name: str, output_type: str
    ):
        failing_methods = {
            "get_channel_summary": "get_roi",
            "get_contribution": "get_contribution_metrics",
            "get_adstock_decay": "get_alpha_summary",
            "get_response_curves": "get_response_curve_summary",
        }
        catalog = _DispatchCatalog(_DispatchFacade())
        service = AnalysisService(
            catalog=catalog,
            result_cache=ResultCache(enabled=False, ttl_seconds=None),
        )
        catalog.get_facade.return_value = SimpleNamespace(
            **{
                failing_methods[method_name]: mock.Mock(
                    side_effect=RuntimeError("missing rows")
                )
            }
        )

        with pytest.raises(MissingModelDataError, match="missing rows"):
            getattr(service, method_name)("m1", output_type, None)


class TestAnalysisServiceCaching:
    def test_cached_short_circuits_without_computing(self):
        cache = mock.Mock()
        cache.get.return_value = {"cached": True}
        service = AnalysisService(catalog=mock.Mock(), result_cache=cache)
        compute = mock.Mock(side_effect=AssertionError("should not run"))

        result = service._cached("tool", "m1", {"a": 1}, compute)

        assert result == {"cached": True}
        compute.assert_not_called()
        cache.put.assert_not_called()

    def test_get_model_overview_uses_cache_on_subsequent_calls(self):
        interrogator = mock.Mock()
        interrogator.get_model_overview.return_value = {
            "model_type": "geo",
            "available_training_datasets": ["kpi"],
        }
        service = AnalysisService(
            catalog=_DispatchCatalog(facade=mock.Mock(), interrogator=interrogator),
            result_cache=ResultCache(enabled=True, ttl_seconds=None),
        )

        first = service.get_model_overview("m1")
        second = service.get_model_overview("m1")

        assert first == second
        interrogator.get_model_overview.assert_called_once()


class _OverviewCatalog:
    def __init__(self, overview):
        self._overview = overview

    class _Interrogator:
        def __init__(self, overview):
            self._overview = overview

        def get_model_overview(self):
            return dict(self._overview)

    def get_interrogator(self, model_id):
        return self._Interrogator(self._overview)


def _base_overview(has_revenue, rf_channels):
    return {
        "available_training_datasets": ["kpi", "media", "media_spend"],
        "has_revenue_per_kpi": has_revenue,
        "rf_channels": rf_channels,
    }


def test_overview_prunes_roi_for_no_revenue_model():
    catalog = _OverviewCatalog(_base_overview(has_revenue=False, rf_channels=["yt"]))
    service = AnalysisService(catalog=catalog)
    overview = service.get_model_overview("kpi-only")
    types = overview["available_tool_options"]["get_channel_summary"]["output_type"]
    assert "roi" not in types and "marginal_roi" not in types
    assert "cpik" in types and "marginal_cpik" in types


def test_overview_keeps_roi_for_revenue_model():
    catalog = _OverviewCatalog(_base_overview(has_revenue=True, rf_channels=[]))
    service = AnalysisService(catalog=catalog)
    overview = service.get_model_overview("rev")
    types = overview["available_tool_options"]["get_channel_summary"]["output_type"]
    assert "roi" in types and "marginal_roi" in types


def test_training_data_applies_geo_filter(monkeypatch):
    import google_meridian_mcp_server.services.analysis_service as svc

    rows = [
        {"geo": "us", "time": "2023-01-01T00:00:00", "kpi": 1.0},
        {"geo": "ca", "time": "2023-01-01T00:00:00", "kpi": 2.0},
    ]
    monkeypatch.setattr(svc, "extract_training_datasets", lambda mmm, datasets: rows)

    class _Catalog:
        def resolve(self, model_id):
            return object()

    service = svc.AnalysisService(catalog=_Catalog())
    result = service.get_training_data("m", ["kpi"], {"geos": ["us"]})
    assert result["row_count"] == 1
    assert result["rows"][0][result["columns"].index("geo")] == "us"


class _ModelFitCatalog:
    def __init__(self, rows):
        self._rows = rows

    class _Facade:
        def __init__(self, rows):
            self._rows = rows

        def get_model_fit(self, filters):
            return self._rows

    def get_facade(self, model_id):
        return self._Facade(self._rows)


def test_get_model_fit_returns_columnar():
    rows = [
        {"time": "2023-01-01", "expected": 10.0, "actual": 11.0, "baseline": 4.0,
         "expected_ci_lo": 9.0, "expected_ci_hi": 11.0, "baseline_ci_lo": 3.0,
         "baseline_ci_hi": 5.0, "residual": 1.0},
    ]
    service = AnalysisService(catalog=_ModelFitCatalog(rows))
    result = service.get_model_fit("m", None)
    assert result["model_id"] == "m"
    assert result["row_count"] == 1
    assert "expected" in result["columns"] and "residual" in result["columns"]
    assert "data" not in result and "result_metadata" not in result


class _RFCatalog:
    def __init__(self, has_rf, rows):
        self._has_rf = has_rf
        self._rows = rows

    class _Facade:
        def __init__(self, rows):
            self._rows = rows

        def get_reach_frequency(self, filters):
            return self._rows

    class _Interrogator:
        def __init__(self, has_rf):
            self._has_rf = has_rf

        def has_rf_channels(self):
            return self._has_rf

    def get_facade(self, model_id):
        return self._Facade(self._rows)

    def get_interrogator(self, model_id):
        return self._Interrogator(self._has_rf)


def test_reach_frequency_columnar_when_rf_present():
    rows = [{"channel": "yt", "frequency": 1.0, "roi": 2.0, "ci_lo": 1.5,
             "ci_hi": 2.5, "optimal_frequency": 3.0}]
    service = AnalysisService(catalog=_RFCatalog(has_rf=True, rows=rows))
    result = service.get_reach_frequency("m", None)
    assert result["row_count"] == 1
    assert "optimal_frequency" in result["columns"]
    assert "data" not in result and "result_metadata" not in result


def test_reach_frequency_errors_without_rf():
    service = AnalysisService(catalog=_RFCatalog(has_rf=False, rows=[]))
    with pytest.raises(MetricNotSupportedError) as exc:
        service.get_reach_frequency("m", None)
    assert exc.value.details["reason"].startswith("model has no reach")


class TestRoundMeasure:
    @pytest.mark.parametrize(
        "value,expected",
        [
            (1.23456789, 1.23457),
            (0.000123456789, 0.000123457),
            (1234567.0, 1234570.0),
            (-2.0 / 3.0, -0.666667),
            (True, True),
            (False, False),
            (42, 42),
            ("text", "text"),
            (None, None),
        ],
    )
    def test_rounds_floats_to_six_significant_figures(self, value, expected):
        result = AnalysisService._round_measure(value)
        assert result == expected
        assert type(result) is type(expected)

    def test_bool_is_not_rounded_as_float(self):
        # bool is a subclass of int/float; it must pass through untouched.
        assert AnalysisService._round_measure(True) is True
        assert AnalysisService._round_measure(False) is False


class _FakeScenarioFacade:
    def __init__(self, *, has_revenue=True, base_spend=100.0, outcomes=None):
        self._has_revenue = has_revenue
        self._base_spend = base_spend
        self._outcomes = outcomes or [
            {"mean": 400.0, "ci_lo": 350.0, "ci_hi": 450.0},
            {"mean": 460.0, "ci_lo": 400.0, "ci_hi": 520.0},
        ]
        self.spend_response_calls = []

    def get_data_inputs(self):
        return {"media": ["search", "tv"], "rf_media": ["youtube"]}

    def resolve_use_kpi(self, filters):
        return not self._has_revenue

    def resolve_base_spend(self, channel, filters):
        return self._base_spend

    def spend_response(self, channel, spend_points, filters):
        self.spend_response_calls.append(list(spend_points))
        return self._outcomes


class _FakeScenarioCatalog:
    def __init__(self, facade):
        self._facade = facade

    def get_facade(self, model_id):
        return self._facade


def _scenario_service(facade, *, cache_enabled=False) -> AnalysisService:
    return AnalysisService(
        catalog=_FakeScenarioCatalog(facade),
        result_cache=ResultCache(enabled=cache_enabled, ttl_seconds=None),
    )


def test_spend_scenario_revenue_mode_computes_roi_family():
    facade = _FakeScenarioFacade(has_revenue=True, base_spend=100.0)
    result = _scenario_service(facade).get_spend_scenario(
        "m1", "search", 20.0, None, None
    )
    assert result["outcome_mode"] == "revenue"
    assert result["channel_type"] == "paid_media"
    assert result["base_spend"] == 100.0
    assert result["new_spend"] == 120.0
    assert result["efficiency"] == 4.0
    assert result["marginal_efficiency"] == 3.0
    assert result["efficiency_at_new"] == pytest.approx(3.83333, rel=1e-4)
    assert result["expected_outcome_increase"] == 60.0
    assert result["base_outcome"] == {"mean": 400.0, "ci_lo": 350.0, "ci_hi": 450.0}
    assert facade.spend_response_calls == [[100.0, 120.0]]


def test_spend_scenario_kpi_mode_computes_cpik_family():
    facade = _FakeScenarioFacade(has_revenue=False, base_spend=100.0)
    result = _scenario_service(facade).get_spend_scenario(
        "m1", "search", 20.0, None, None
    )
    assert result["outcome_mode"] == "kpi"
    assert result["efficiency"] == 0.25
    assert result["marginal_efficiency"] == pytest.approx(0.333333, rel=1e-4)
    assert result["efficiency_at_new"] == pytest.approx(0.26087, rel=1e-4)


def test_spend_scenario_uses_provided_base_spend():
    facade = _FakeScenarioFacade(has_revenue=True, base_spend=999.0)
    result = _scenario_service(facade).get_spend_scenario(
        "m1", "search", 20.0, 50.0, None
    )
    assert result["base_spend"] == 50.0
    assert result["new_spend"] == 70.0
    assert facade.spend_response_calls == [[50.0, 70.0]]


def test_spend_scenario_rejects_unknown_channel():
    facade = _FakeScenarioFacade()
    with pytest.raises(MissingModelDataError):
        _scenario_service(facade).get_spend_scenario("m1", "nope", 20.0, None, None)


def test_spend_scenario_rejects_non_positive_base_spend():
    facade = _FakeScenarioFacade()
    with pytest.raises(MissingModelDataError):
        _scenario_service(facade).get_spend_scenario("m1", "search", 20.0, 0.0, None)


def test_spend_scenario_zero_lift_yields_null_efficiency():
    facade = _FakeScenarioFacade(
        has_revenue=False,
        base_spend=100.0,
        outcomes=[
            {"mean": 0.0, "ci_lo": 0.0, "ci_hi": 0.0},
            {"mean": 0.0, "ci_lo": 0.0, "ci_hi": 0.0},
        ],
    )
    result = _scenario_service(facade).get_spend_scenario(
        "m1", "search", 20.0, None, None
    )
    assert result["efficiency"] is None
    assert result["marginal_efficiency"] is None


def test_spend_scenario_caches_result():
    facade = _FakeScenarioFacade(has_revenue=True)
    service = _scenario_service(facade, cache_enabled=True)
    first = service.get_spend_scenario("m1", "search", 20.0, None, None)
    second = service.get_spend_scenario("m1", "search", 20.0, None, None)
    assert first == second
    assert len(facade.spend_response_calls) == 1

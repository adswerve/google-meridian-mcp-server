"""Unit tests for AnalyzerFacade helpers beyond grouped analysis outputs."""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
from unittest import mock

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from google_meridian_mcp_server.domain.filters import AnalysisFilters
from google_meridian_mcp_server.meridian.analyzer_facade import AnalyzerFacade
from google_meridian_mcp_server.meridian.interrogator import MeridianInterrogator


def test_get_data_returns_wide_inputs_with_media_and_rf_spend_columns():
    time_values = ["2024-01-01", "2024-01-08"]
    input_data = SimpleNamespace(
        time=xr.DataArray(time_values, coords={"time": time_values}, dims=("time",)),
        geo=xr.DataArray(["us"], coords={"geo": ["us"]}, dims=("geo",)),
        population=xr.DataArray([100], coords={"geo": ["us"]}, dims=("geo",)),
        kpi=xr.DataArray(
            [[10.0, 12.0]],
            coords={"geo": ["us"], "time": time_values},
            dims=("geo", "time"),
            name="kpi",
        ),
        revenue_per_kpi=None,
        media_channel=xr.DataArray(
            ["search"],
            coords={"media_channel": ["search"]},
            dims=("media_channel",),
        ),
        media=xr.DataArray(
            [[[100.0], [120.0]]],
            coords={
                "geo": ["us"],
                "media_time": time_values,
                "media_channel": ["search"],
            },
            dims=("geo", "media_time", "media_channel"),
            name="media",
        ),
        media_spend=xr.DataArray(
            [[[50.0], [70.0]]],
            coords={"geo": ["us"], "time": time_values, "media_channel": ["search"]},
            dims=("geo", "time", "media_channel"),
            name="media_spend",
        ),
        rf_channel=xr.DataArray(
            ["youtube"],
            coords={"rf_channel": ["youtube"]},
            dims=("rf_channel",),
        ),
        reach=xr.DataArray(
            [[[80.0], [90.0]]],
            coords={
                "geo": ["us"],
                "media_time": time_values,
                "rf_channel": ["youtube"],
            },
            dims=("geo", "media_time", "rf_channel"),
            name="reach",
        ),
        frequency=xr.DataArray(
            [[[1.2], [1.5]]],
            coords={
                "geo": ["us"],
                "media_time": time_values,
                "rf_channel": ["youtube"],
            },
            dims=("geo", "media_time", "rf_channel"),
            name="frequency",
        ),
        rf_spend=xr.DataArray(
            [[[40.0], [45.0]]],
            coords={"geo": ["us"], "time": time_values, "rf_channel": ["youtube"]},
            dims=("geo", "time", "rf_channel"),
            name="rf_spend",
        ),
        organic_media_channel=None,
        organic_media=None,
        organic_rf_channel=None,
        organic_reach=None,
        organic_frequency=None,
        non_media_channel=None,
        non_media_treatments=None,
        control_variable=None,
        controls=None,
    )

    data = MeridianInterrogator(SimpleNamespace(input_data=input_data)).get_data()

    assert list(data.index.astype(str)) == time_values
    assert data["kpi"].tolist() == [10.0, 12.0]
    assert data["population"].tolist() == [100, 100]
    assert data["search"].tolist() == [100.0, 120.0]
    assert data["search_spend"].tolist() == [50.0, 70.0]
    assert data["youtube_reach"].tolist() == [80.0, 90.0]
    assert data["youtube_frequency"].tolist() == [1.2, 1.5]
    assert data["youtube_rf_spend"].tolist() == [40.0, 45.0]


def test_get_carryover_filters_to_posterior_integer_time_units():
    facade = AnalyzerFacade(SimpleNamespace(input_data=SimpleNamespace()))
    facade._analyzer = mock.Mock()
    facade._analyzer.adstock_decay.return_value = pd.DataFrame(
        {
            "channel": ["search", "search", "tv"],
            "distribution": ["posterior", "prior", "posterior"],
            "is_int_time_unit": [True, True, True],
            "mean": [1.5, 0.5, 2.0],
            "ci_lo": [1.0, 0.2, 1.5],
            "ci_hi": [2.0, 0.8, 2.5],
        }
    )

    mean, ci_lo, ci_hi = facade.get_carryover("search")

    assert mean.tolist() == [1.5]
    assert ci_lo.tolist() == [1.0]
    assert ci_hi.tolist() == [2.0]


def test_get_baseline_summary_metrics_uses_analyzer_and_returns_posterior_only():
    facade = AnalyzerFacade(SimpleNamespace(input_data=SimpleNamespace()))
    facade._analyzer = mock.Mock()
    facade._analyzer.baseline_summary_metrics.return_value = xr.Dataset(
        data_vars={
            "baseline_outcome": (
                ("channel", "metric", "distribution"),
                np.array([[[50.0, 60.0], [48.0, 58.0], [45.0, 55.0], [52.0, 65.0]]]),
            )
        },
        coords={
            "channel": ["baseline"],
            "metric": ["mean", "median", "ci_lo", "ci_hi"],
            "distribution": ["prior", "posterior"],
        },
    )

    rows = facade.get_baseline_summary_metrics(AnalysisFilters())

    assert rows == [
        {"channel": "baseline", "metric": "mean", "baseline_outcome": 60.0},
        {"channel": "baseline", "metric": "median", "baseline_outcome": 58.0},
        {"channel": "baseline", "metric": "ci_lo", "baseline_outcome": 55.0},
        {"channel": "baseline", "metric": "ci_hi", "baseline_outcome": 65.0},
    ]
    facade._analyzer.baseline_summary_metrics.assert_called_once()


def test_get_spend_column_uses_rf_suffix_for_rf_channels():
    input_data = SimpleNamespace(
        rf_channel=xr.DataArray(
            ["youtube"],
            coords={"rf_channel": ["youtube"]},
            dims=("rf_channel",),
        )
    )
    facade = AnalyzerFacade(SimpleNamespace(input_data=input_data))

    assert facade._get_spend_column("youtube") == "youtube_rf_spend"
    assert facade._get_spend_column("search") == "search_spend"


def test_apply_saturation_forwards_use_kpi_and_interpolates():
    facade = AnalyzerFacade(
        SimpleNamespace(
            input_data=SimpleNamespace(rf_channel=None),
            expand_selected_time_dims=mock.Mock(
                return_value=["2024-01-01", "2024-01-08"]
            ),
        )
    )
    facade.get_data = mock.Mock(
        return_value=pd.DataFrame(
            {"search_spend": [100.0, 200.0]},
            index=pd.Index(["2024-01-01", "2024-01-08"], name="time"),
        )
    )
    facade._analyzer = mock.Mock()
    response_df = pd.DataFrame(
        {
            "metric": ["mean", "mean", "ci_lo", "ci_lo", "ci_hi", "ci_hi"],
            "spend": [200.0, 400.0, 200.0, 400.0, 200.0, 400.0],
            "incremental_outcome": [20.0, 40.0, 18.0, 36.0, 22.0, 44.0],
        },
        index=pd.MultiIndex.from_tuples(
            [
                ("search", 0),
                ("search", 1),
                ("search", 0),
                ("search", 1),
                ("search", 0),
                ("search", 1),
            ],
            names=["channel", "row"],
        ),
    )
    facade._analyzer.response_curves.return_value.to_dataframe.return_value = (
        response_df
    )

    mean, ci_lo, ci_hi = facade.apply_saturation(
        "search",
        [150.0],
        geos=["us"],
        dt_start="2024-01-01",
        dt_end="2024-01-31",
        use_kpi=True,
    )

    assert np.allclose(mean, [15.0])
    assert np.allclose(ci_lo, [13.5])
    assert np.allclose(ci_hi, [16.5])
    facade._analyzer.response_curves.assert_called_once()
    assert facade._analyzer.response_curves.call_args.kwargs["use_kpi"] is True
    assert facade._analyzer.response_curves.call_args.kwargs["selected_geos"] == ["us"]
    assert facade._analyzer.response_curves.call_args.kwargs["selected_times"] == [
        "2024-01-01",
        "2024-01-08",
    ]


def test_media_summary_defaults_to_kpi_when_no_revenue():
    """With no revenue_per_kpi in the model, _get_media_summary must pass use_kpi=True."""
    input_data = SimpleNamespace(
        revenue_per_kpi=None,
        rf_channel=None,
        media_channel=None,
        non_media_channel=None,
        organic_media_channel=None,
        organic_rf_channel=None,
        control_variable=None,
    )
    facade = AnalyzerFacade(SimpleNamespace(input_data=input_data))

    captured = {}

    class _FakeMediaSummary:
        def __init__(self, *args, **kwargs):
            captured["use_kpi"] = kwargs.get("use_kpi")

    visualizer_module = ModuleType("meridian.analysis.visualizer")
    visualizer_module.MediaSummary = _FakeMediaSummary
    analysis_module = ModuleType("meridian.analysis")
    analysis_module.visualizer = visualizer_module
    meridian_module = ModuleType("meridian")
    meridian_module.analysis = analysis_module

    with mock.patch.dict(
        sys.modules,
        {
            "meridian": meridian_module,
            "meridian.analysis": analysis_module,
            "meridian.analysis.visualizer": visualizer_module,
        },
    ):
        facade._get_media_summary(AnalysisFilters())

    assert captured["use_kpi"] is True


def test_media_summary_is_cached_by_use_kpi_and_confidence_level():
    media_summary_ctor = mock.Mock()

    class _FakeMediaSummary:
        def __init__(self, *args, **kwargs):
            media_summary_ctor(*args, **kwargs)

    visualizer_module = ModuleType("meridian.analysis.visualizer")
    visualizer_module.MediaSummary = _FakeMediaSummary
    analysis_module = ModuleType("meridian.analysis")
    analysis_module.visualizer = visualizer_module
    meridian_module = ModuleType("meridian")
    meridian_module.analysis = analysis_module

    facade = AnalyzerFacade(SimpleNamespace(input_data=SimpleNamespace()))

    with mock.patch.dict(
        sys.modules,
        {
            "meridian": meridian_module,
            "meridian.analysis": analysis_module,
            "meridian.analysis.visualizer": visualizer_module,
        },
    ):
        first = facade._get_media_summary(AnalysisFilters(), confidence_level=0.9)
        second = facade._get_media_summary(AnalysisFilters(), confidence_level=0.9)
        third = facade._get_media_summary(
            AnalysisFilters(use_kpi=False), confidence_level=0.9
        )

    assert first is second
    assert third is not first
    assert media_summary_ctor.call_count == 2


@pytest.mark.parametrize(
    ("method_name", "metric_name"),
    [
        ("get_roi", "roi"),
        ("get_cpik", "cpik"),
        ("get_marginal_roi", "mroi"),
        ("get_marginal_cpik", "mroi"),
    ],
)
def test_metric_extractors_return_empty_when_metric_missing(method_name, metric_name):
    facade = AnalyzerFacade(SimpleNamespace(input_data=SimpleNamespace()))
    media_summary = mock.Mock()
    media_summary.get_paid_summary_metrics.return_value = SimpleNamespace(
        data_vars={"other_metric"}
    )
    facade._get_media_summary = mock.Mock(return_value=media_summary)

    result = getattr(facade, method_name)(AnalysisFilters())

    assert result == []


def test_get_marginal_metrics_use_mroi_and_strip_distribution():
    facade = AnalyzerFacade(SimpleNamespace(input_data=SimpleNamespace()))
    media_summary = mock.Mock()
    media_summary.get_paid_summary_metrics.return_value = xr.Dataset(
        data_vars={
            "mroi": (
                ("channel", "metric", "distribution"),
                np.array([[[1.5, 2.0], [1.0, 1.5], [0.8, 1.2], [2.2, 3.0]]]),
            )
        },
        coords={
            "channel": ["search"],
            "metric": ["mean", "median", "ci_lo", "ci_hi"],
            "distribution": ["prior", "posterior"],
        },
    )
    facade._get_media_summary = mock.Mock(return_value=media_summary)

    roi_rows = facade.get_marginal_roi(AnalysisFilters())
    cpik_rows = facade.get_marginal_cpik(AnalysisFilters())

    assert roi_rows == [
        {"channel": "search", "metric": "mean", "marginal_roi": 2.0},
        {"channel": "search", "metric": "median", "marginal_roi": 1.5},
        {"channel": "search", "metric": "ci_lo", "marginal_roi": 1.2},
        {"channel": "search", "metric": "ci_hi", "marginal_roi": 3.0},
    ]
    assert cpik_rows == [
        {"channel": "search", "metric": "mean", "marginal_cpik": 0.5},
        {
            "channel": "search",
            "metric": "median",
            "marginal_cpik": 0.6666666666666666,
        },
        {
            "channel": "search",
            "metric": "ci_lo",
            "marginal_cpik": 0.3333333333333333,
        },
        {
            "channel": "search",
            "metric": "ci_hi",
            "marginal_cpik": 0.8333333333333334,
        },
    ]


def test_get_contribution_metrics_defaults_include_non_paid_and_supports_false_override():
    facade = AnalyzerFacade(SimpleNamespace(input_data=SimpleNamespace()))
    media_summary = mock.Mock()
    media_summary.contribution_metrics.return_value = pd.DataFrame(
        [{"channel": "search", "value": 1.0}]
    )
    facade._get_media_summary = mock.Mock(return_value=media_summary)

    facade.get_contribution_metrics(AnalysisFilters())
    facade.get_contribution_metrics(AnalysisFilters(include_non_paid=False))

    first_call = media_summary.contribution_metrics.call_args_list[0]
    second_call = media_summary.contribution_metrics.call_args_list[1]
    assert first_call.kwargs["include_non_paid"] is True
    assert second_call.kwargs["include_non_paid"] is False


def test_get_alpha_summary_includes_all_supported_channel_families():
    posterior = SimpleNamespace(
        alpha_m=SimpleNamespace(values=np.array([[[0.4]], [[0.6]]])),
        alpha_rf=SimpleNamespace(values=np.array([[[0.7]], [[0.9]]])),
        alpha_om=SimpleNamespace(values=np.array([[[0.2]], [[0.4]]])),
        alpha_orf=SimpleNamespace(values=np.array([[[0.1]], [[0.3]]])),
    )
    facade = AnalyzerFacade(
        SimpleNamespace(
            input_data=SimpleNamespace(
                media_channel=xr.DataArray(["search"], dims=("media_channel",)),
                rf_channel=xr.DataArray(["youtube"], dims=("rf_channel",)),
                organic_media_channel=xr.DataArray(
                    ["email"], dims=("organic_media_channel",)
                ),
                organic_rf_channel=xr.DataArray(
                    ["podcast"], dims=("organic_rf_channel",)
                ),
            ),
            inference_data=SimpleNamespace(posterior=posterior),
        )
    )

    rows = facade.get_alpha_summary(AnalysisFilters())

    assert {(row["channel"], row["channel_type"]) for row in rows} == {
        ("search", "media"),
        ("youtube", "rf"),
        ("email", "organic_media"),
        ("podcast", "organic_rf"),
    }
    assert all("alpha_mean" in row for row in rows)


def test_get_adstock_decay_returns_posterior_only():
    facade = AnalyzerFacade(SimpleNamespace(input_data=SimpleNamespace()))
    facade._analyzer = mock.Mock()
    facade._analyzer.adstock_decay.return_value = pd.DataFrame(
        {
            "channel": ["search", "search"],
            "distribution": ["prior", "posterior"],
            "mean": [0.5, 1.5],
            "ci_lo": [0.2, 1.0],
            "ci_hi": [0.8, 2.0],
        }
    )

    rows = facade.get_adstock_decay(AnalysisFilters(channels=["search"]))

    assert rows == [{"channel": "search", "mean": 1.5, "ci_lo": 1.0, "ci_hi": 2.0}]


def test_get_response_curves_returns_numeric_curve_rows():
    facade = AnalyzerFacade(
        SimpleNamespace(
            input_data=SimpleNamespace(),
            expand_selected_time_dims=mock.Mock(return_value=["2024-01-01"]),
        )
    )
    facade._analyzer = mock.Mock()
    facade._analyzer.response_curves.return_value = xr.Dataset(
        data_vars={
            "spend": (
                ("spend_multiplier", "channel"),
                np.array([[100.0, 50.0], [200.0, 100.0]]),
            ),
            "incremental_outcome": (
                ("spend_multiplier", "channel", "metric"),
                np.array(
                    [
                        [[10.0, 8.0, 12.0], [5.0, 4.0, 6.0]],
                        [[20.0, 16.0, 24.0], [10.0, 8.0, 12.0]],
                    ]
                ),
            ),
        },
        coords={
            "spend_multiplier": [1.0, 2.0],
            "channel": ["search", "tv"],
            "metric": ["mean", "ci_lo", "ci_hi"],
        },
    )

    rows = facade.get_response_curves(
        AnalysisFilters(
            channels=["tv"],
            start_date="2024-01-01",
            end_date="2024-01-31",
            use_kpi=True,
        )
    )

    assert rows == [
        {
            "spend_multiplier": 1.0,
            "channel": "tv",
            "metric": "mean",
            "spend": 50.0,
            "incremental_outcome": 5.0,
        },
        {
            "spend_multiplier": 1.0,
            "channel": "tv",
            "metric": "ci_lo",
            "spend": 50.0,
            "incremental_outcome": 4.0,
        },
        {
            "spend_multiplier": 1.0,
            "channel": "tv",
            "metric": "ci_hi",
            "spend": 50.0,
            "incremental_outcome": 6.0,
        },
        {
            "spend_multiplier": 2.0,
            "channel": "tv",
            "metric": "mean",
            "spend": 100.0,
            "incremental_outcome": 10.0,
        },
        {
            "spend_multiplier": 2.0,
            "channel": "tv",
            "metric": "ci_lo",
            "spend": 100.0,
            "incremental_outcome": 8.0,
        },
        {
            "spend_multiplier": 2.0,
            "channel": "tv",
            "metric": "ci_hi",
            "spend": 100.0,
            "incremental_outcome": 12.0,
        },
    ]
    assert facade._analyzer.response_curves.call_args.kwargs["selected_times"] == [
        "2024-01-01"
    ]
    assert facade._analyzer.response_curves.call_args.kwargs["use_kpi"] is True


def test_get_response_curve_summary_returns_pivoted_numeric_summary():
    facade = AnalyzerFacade(
        SimpleNamespace(
            input_data=SimpleNamespace(),
            expand_selected_time_dims=mock.Mock(return_value=None),
        )
    )
    facade._analyzer = mock.Mock()
    facade._analyzer.response_curves.return_value = xr.Dataset(
        data_vars={
            "spend": (
                ("spend_multiplier", "channel"),
                np.array([[100.0, 50.0], [200.0, 100.0]]),
            ),
            "incremental_outcome": (
                ("spend_multiplier", "channel", "metric"),
                np.array(
                    [
                        [[10.0, 8.0, 12.0], [5.0, 4.0, 6.0]],
                        [[20.0, 16.0, 24.0], [10.0, 8.0, 12.0]],
                    ]
                ),
            ),
        },
        coords={
            "spend_multiplier": [1.0, 2.0],
            "channel": ["search", "tv"],
            "metric": ["mean", "ci_lo", "ci_hi"],
        },
    )

    rows = facade.get_response_curve_summary(AnalysisFilters(channels=["tv"]))

    assert rows == [
        {
            "channel": "tv",
            "spend": 50.0,
            "spend_multiplier": 1.0,
            "mean": 5.0,
            "ci_lo": 4.0,
            "ci_hi": 6.0,
        },
        {
            "channel": "tv",
            "spend": 100.0,
            "spend_multiplier": 2.0,
            "mean": 10.0,
            "ci_lo": 8.0,
            "ci_hi": 12.0,
        },
    ]


def test_interpolate_with_extrapolation_handles_single_point_and_edges():
    single = AnalyzerFacade._interpolate_with_extrapolation(
        np.array([1.0, 2.0]),
        pd.Series([3.0]),
        pd.Series([7.0]),
    )
    extrapolated = AnalyzerFacade._interpolate_with_extrapolation(
        np.array([0.0, 1.0, 3.0]),
        pd.Series([1.0, 2.0]),
        pd.Series([10.0, 20.0]),
    )

    assert np.allclose(single, [7.0, 7.0])
    assert np.allclose(extrapolated, [0.0, 10.0, 30.0])


def test_interpolate_with_extrapolation_requires_points():
    with pytest.raises(ValueError, match="requires at least one point"):
        AnalyzerFacade._interpolate_with_extrapolation(
            np.array([1.0]),
            pd.Series(dtype=float),
            pd.Series(dtype=float),
        )


def test_apply_saturation_rejects_empty_spend_values():
    facade = AnalyzerFacade(SimpleNamespace(input_data=SimpleNamespace()))

    with pytest.raises(ValueError, match="At least one spend value"):
        facade.apply_saturation("search", [])


def test_apply_saturation_rejects_empty_data_slice():
    facade = AnalyzerFacade(
        SimpleNamespace(input_data=SimpleNamespace(rf_channel=None))
    )
    facade.get_data = mock.Mock(return_value=pd.DataFrame())

    with pytest.raises(ValueError, match="No model data is available"):
        facade.apply_saturation("search", [1.0])


def test_apply_saturation_rejects_missing_spend_column():
    facade = AnalyzerFacade(
        SimpleNamespace(input_data=SimpleNamespace(rf_channel=None))
    )
    facade.get_data = mock.Mock(return_value=pd.DataFrame({"other": [1.0]}))

    with pytest.raises(
        ValueError, match="Spend column 'search_spend' is not available"
    ):
        facade.apply_saturation("search", [1.0])


def test_apply_saturation_rejects_non_positive_mean_spend():
    facade = AnalyzerFacade(
        SimpleNamespace(input_data=SimpleNamespace(rf_channel=None))
    )
    facade.get_data = mock.Mock(return_value=pd.DataFrame({"search_spend": [0.0, 0.0]}))

    with pytest.raises(ValueError, match="must have a positive mean spend"):
        facade.apply_saturation("search", [1.0])

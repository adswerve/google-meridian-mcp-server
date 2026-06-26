"""Unit tests for MeridianInterrogator metadata and data extraction helpers."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd
import xarray as xr

from google_meridian_mcp_server.domain.filters import AnalysisFilters
from google_meridian_mcp_server.meridian.interrogator import MeridianInterrogator


def _interrogator(*, revenue, rf_channels):
    input_data = SimpleNamespace(
        revenue_per_kpi=object() if revenue else None,
        rf_channel=(__import__("numpy").array(rf_channels) if rf_channels else None),
        media_channel=None,
        non_media_channel=None,
        organic_media_channel=None,
        organic_rf_channel=None,
        control_variable=None,
    )
    return MeridianInterrogator(SimpleNamespace(input_data=input_data))


def test_has_revenue_per_kpi_reflects_input_data():
    assert _interrogator(revenue=True, rf_channels=[]).has_revenue_per_kpi() is True
    assert _interrogator(revenue=False, rf_channels=[]).has_revenue_per_kpi() is False


def test_has_rf_channels_reflects_rf_coord():
    assert _interrogator(revenue=True, rf_channels=["yt"]).has_rf_channels() is True
    assert _interrogator(revenue=True, rf_channels=[]).has_rf_channels() is False


def test_resolve_use_kpi_defaults_from_revenue_capability():
    revenue = _interrogator(revenue=True, rf_channels=[])
    kpi_only = _interrogator(revenue=False, rf_channels=[])
    # No explicit use_kpi -> revenue model queries revenue (False), kpi-only queries kpi (True).
    assert revenue.resolve_use_kpi(AnalysisFilters()) is False
    assert kpi_only.resolve_use_kpi(AnalysisFilters()) is True
    # Explicit use_kpi is honored.
    assert revenue.resolve_use_kpi(AnalysisFilters(use_kpi=True)) is True
    assert kpi_only.resolve_use_kpi(AnalysisFilters(use_kpi=False)) is False


def _build_full_model(is_national=False):
    time_values = ["2024-01-01", "2024-01-08"]
    geos = ["us", "ca"]

    return SimpleNamespace(
        is_national=is_national,
        input_data=SimpleNamespace(
            time=xr.DataArray(
                time_values, coords={"time": time_values}, dims=("time",)
            ),
            geo=xr.DataArray(geos, coords={"geo": geos}, dims=("geo",)),
            population=xr.DataArray([100, 200], coords={"geo": geos}, dims=("geo",)),
            kpi=xr.DataArray(
                [[10.0, 12.0], [7.0, 9.0]],
                coords={"geo": geos, "time": time_values},
                dims=("geo", "time"),
                name="kpi",
            ),
            revenue_per_kpi=xr.DataArray(
                [[2.0, 2.5], [1.5, 2.0]],
                coords={"geo": geos, "time": time_values},
                dims=("geo", "time"),
                name="revenue_per_kpi",
            ),
            media_channel=xr.DataArray(
                ["search"],
                coords={"media_channel": ["search"]},
                dims=("media_channel",),
            ),
            media=xr.DataArray(
                [[[100.0], [110.0]], [[80.0], [90.0]]],
                coords={
                    "geo": geos,
                    "media_time": time_values,
                    "media_channel": ["search"],
                },
                dims=("geo", "media_time", "media_channel"),
                name="media",
            ),
            media_spend=xr.DataArray(
                [[[50.0], [55.0]], [[40.0], [45.0]]],
                coords={"geo": geos, "time": time_values, "media_channel": ["search"]},
                dims=("geo", "time", "media_channel"),
                name="media_spend",
            ),
            rf_channel=xr.DataArray(
                ["youtube"],
                coords={"rf_channel": ["youtube"]},
                dims=("rf_channel",),
            ),
            reach=xr.DataArray(
                [[[70.0], [75.0]], [[60.0], [65.0]]],
                coords={
                    "geo": geos,
                    "media_time": time_values,
                    "rf_channel": ["youtube"],
                },
                dims=("geo", "media_time", "rf_channel"),
                name="reach",
            ),
            frequency=xr.DataArray(
                [[[1.1], [1.2]], [[1.3], [1.4]]],
                coords={
                    "geo": geos,
                    "media_time": time_values,
                    "rf_channel": ["youtube"],
                },
                dims=("geo", "media_time", "rf_channel"),
                name="frequency",
            ),
            rf_spend=xr.DataArray(
                [[[30.0], [32.0]], [[20.0], [22.0]]],
                coords={"geo": geos, "time": time_values, "rf_channel": ["youtube"]},
                dims=("geo", "time", "rf_channel"),
                name="rf_spend",
            ),
            organic_media_channel=xr.DataArray(
                ["email"],
                coords={"organic_media_channel": ["email"]},
                dims=("organic_media_channel",),
            ),
            organic_media=xr.DataArray(
                [[[12.0], [13.0]], [[9.0], [10.0]]],
                coords={
                    "geo": geos,
                    "media_time": time_values,
                    "organic_media_channel": ["email"],
                },
                dims=("geo", "media_time", "organic_media_channel"),
                name="organic_media",
            ),
            organic_rf_channel=xr.DataArray(
                ["podcast"],
                coords={"organic_rf_channel": ["podcast"]},
                dims=("organic_rf_channel",),
            ),
            organic_reach=xr.DataArray(
                [[[8.0], [9.0]], [[6.0], [7.0]]],
                coords={
                    "geo": geos,
                    "media_time": time_values,
                    "organic_rf_channel": ["podcast"],
                },
                dims=("geo", "media_time", "organic_rf_channel"),
                name="organic_reach",
            ),
            organic_frequency=xr.DataArray(
                [[[1.8], [1.9]], [[1.6], [1.7]]],
                coords={
                    "geo": geos,
                    "media_time": time_values,
                    "organic_rf_channel": ["podcast"],
                },
                dims=("geo", "media_time", "organic_rf_channel"),
                name="organic_frequency",
            ),
            non_media_channel=xr.DataArray(
                ["promo"],
                coords={"non_media_channel": ["promo"]},
                dims=("non_media_channel",),
            ),
            non_media_treatments=xr.DataArray(
                [[[2.0], [3.0]], [[1.0], [2.0]]],
                coords={
                    "geo": geos,
                    "time": time_values,
                    "non_media_channel": ["promo"],
                },
                dims=("geo", "time", "non_media_channel"),
                name="non_media_treatments",
            ),
            control_variable=xr.DataArray(
                ["price"],
                coords={"control_variable": ["price"]},
                dims=("control_variable",),
            ),
            controls=xr.DataArray(
                [[[5.0], [6.0]], [[4.0], [5.0]]],
                coords={
                    "geo": geos,
                    "time": time_values,
                    "control_variable": ["price"],
                },
                dims=("geo", "time", "control_variable"),
                name="controls",
            ),
        ),
    )


def test_is_national_handles_bool_and_callable_values():
    assert (
        MeridianInterrogator(_build_full_model(is_national=True)).is_national() is True
    )
    assert (
        MeridianInterrogator(_build_full_model(is_national=lambda: False)).is_national()
        is False
    )


def test_get_geos_info_returns_empty_frame_when_geo_or_population_missing():
    no_geo = MeridianInterrogator(
        SimpleNamespace(input_data=SimpleNamespace(population=None))
    )
    no_population = MeridianInterrogator(
        SimpleNamespace(input_data=SimpleNamespace(geo=None))
    )

    assert list(no_geo.get_geos_info().columns) == ["geo", "population"]
    assert no_geo.get_geos_info().empty
    assert no_population.get_geos_info().empty


def test_get_available_training_datasets_only_returns_present_inputs():
    model = SimpleNamespace(
        input_data=SimpleNamespace(
            kpi=object(),
            controls=object(),
            revenue_per_kpi=None,
            population=None,
            media=None,
            media_spend=None,
            reach=None,
            frequency=None,
            rf_spend=None,
            organic_media=None,
            organic_reach=None,
            organic_frequency=None,
            non_media_treatments=None,
        )
    )

    assert MeridianInterrogator(model).get_available_training_datasets() == [
        "kpi",
        "controls",
    ]


def test_get_data_schema_and_input_columns_include_kpi_only_when_requested():
    interrogator = MeridianInterrogator(_build_full_model())

    schema_without_kpi = interrogator.get_data_schema(include_kpi=False)
    schema_with_kpi = interrogator.get_data_schema(include_kpi=True)
    columns = interrogator.get_input_column_names(include_kpi=True)

    assert "kpi" not in schema_without_kpi
    assert schema_with_kpi["kpi"] == ["kpi"]
    assert schema_with_kpi["population"] == ["population"]
    assert schema_with_kpi["revenue_per_kpi"] == ["revenue_per_kpi"]
    assert columns[:4] == [
        "search",
        "search_spend",
        "youtube_reach",
        "youtube_frequency",
    ]
    assert len(columns) == len(set(columns))


def test_get_data_supports_geo_and_date_filtering_without_geo_aggregation():
    data = MeridianInterrogator(_build_full_model()).get_data(
        agg_geos=False,
        geos=["us"],
        dt_start="2024-01-08",
        dt_end="2024-01-08",
    )

    assert list(data.index.names) == ["geo", "time"]
    assert list(data.index.get_level_values("geo")) == ["us"]
    assert list(data.index.get_level_values("time").astype(str)) == ["2024-01-08"]
    assert data.loc[("us", "2024-01-08"), "email"] == 13.0
    assert data.loc[("us", "2024-01-08"), "podcast_organic_reach"] == 9.0
    assert data.loc[("us", "2024-01-08"), "promo"] == 3.0
    assert data.loc[("us", "2024-01-08"), "price"] == 6.0


def test_to_python_converts_numpy_and_datetime_values():
    assert MeridianInterrogator._to_python(np.int64(3)) == 3
    assert MeridianInterrogator._to_python(np.float64(1.5)) == 1.5
    assert MeridianInterrogator._to_python(np.bool_(True)) is True
    assert MeridianInterrogator._to_python(np.datetime64("2024-01-01")).startswith(
        "2024-01-01"
    )
    assert MeridianInterrogator._to_python(pd.Timestamp("2024-01-08")).startswith(
        "2024-01-08"
    )

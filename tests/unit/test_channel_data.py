from types import SimpleNamespace

import xarray as xr

from google_meridian_mcp_server.meridian.dataset_mapper import extract_channel_data


def _input_data():
    times = ["2023-01-01", "2023-01-08"]
    return SimpleNamespace(
        media_channel=xr.DataArray(["tv"], coords={"media_channel": ["tv"]}, dims=("media_channel",)),
        media=xr.DataArray(
            [[[100.0], [120.0]]],
            coords={"geo": ["us"], "media_time": times, "media_channel": ["tv"]},
            dims=("geo", "media_time", "media_channel"), name="media",
        ),
        media_spend=xr.DataArray(
            [[[5.0], [6.0]]],
            coords={"geo": ["us"], "time": times, "media_channel": ["tv"]},
            dims=("geo", "time", "media_channel"), name="media_spend",
        ),
        rf_channel=xr.DataArray(["yt"], coords={"rf_channel": ["yt"]}, dims=("rf_channel",)),
        reach=xr.DataArray(
            [[[80.0], [90.0]]],
            coords={"geo": ["us"], "media_time": times, "rf_channel": ["yt"]},
            dims=("geo", "media_time", "rf_channel"), name="reach",
        ),
        frequency=xr.DataArray(
            [[[1.2], [1.5]]],
            coords={"geo": ["us"], "media_time": times, "rf_channel": ["yt"]},
            dims=("geo", "media_time", "rf_channel"), name="frequency",
        ),
        rf_spend=xr.DataArray(
            [[[4.0], [4.5]]],
            coords={"geo": ["us"], "time": times, "rf_channel": ["yt"]},
            dims=("geo", "time", "rf_channel"), name="rf_spend",
        ),
        organic_media_channel=None, organic_media=None,
        organic_rf_channel=None, organic_reach=None, organic_frequency=None,
        non_media_channel=None, non_media_treatments=None,
    )


def test_channel_data_long_has_types_and_null_padding():
    rows = extract_channel_data(SimpleNamespace(input_data=_input_data()))
    tv = next(r for r in rows if r["channel"] == "tv")
    yt = next(r for r in rows if r["channel"] == "yt")
    assert tv["channel_type"] == "paid_media"
    assert tv["impressions"] == 100.0 and tv["spend"] == 5.0
    assert tv["reach"] is None and tv["rf_spend"] is None
    assert yt["channel_type"] == "rf"
    assert yt["reach"] == 80.0 and yt["frequency"] == 1.2 and yt["rf_spend"] == 4.0
    assert yt["impressions"] is None and yt["spend"] is None
    # Unified column set across all rows.
    assert set(tv) == set(yt)

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


def _organic_non_media_input_data():
    times = ["2023-01-01", "2023-01-08"]
    return SimpleNamespace(
        # No paid media or RF.
        media_channel=None, media=None, media_spend=None,
        rf_channel=None, reach=None, frequency=None, rf_spend=None,
        # Organic media.
        organic_media_channel=xr.DataArray(
            ["email"], coords={"organic_media_channel": ["email"]}, dims=("organic_media_channel",)
        ),
        organic_media=xr.DataArray(
            [[[50.0], [60.0]]],
            coords={"geo": ["us"], "media_time": times, "organic_media_channel": ["email"]},
            dims=("geo", "media_time", "organic_media_channel"),
            name="organic_media",
        ),
        # Organic RF.
        organic_rf_channel=xr.DataArray(
            ["podcast"], coords={"organic_rf_channel": ["podcast"]}, dims=("organic_rf_channel",)
        ),
        organic_reach=xr.DataArray(
            [[[200.0], [210.0]]],
            coords={"geo": ["us"], "media_time": times, "organic_rf_channel": ["podcast"]},
            dims=("geo", "media_time", "organic_rf_channel"),
            name="organic_reach",
        ),
        organic_frequency=xr.DataArray(
            [[[2.0], [2.1]]],
            coords={"geo": ["us"], "media_time": times, "organic_rf_channel": ["podcast"]},
            dims=("geo", "media_time", "organic_rf_channel"),
            name="organic_frequency",
        ),
        # Non-media.
        non_media_channel=xr.DataArray(
            ["promo"], coords={"non_media_channel": ["promo"]}, dims=("non_media_channel",)
        ),
        non_media_treatments=xr.DataArray(
            [[[1.0], [2.0]]],
            coords={"geo": ["us"], "time": times, "non_media_channel": ["promo"]},
            dims=("geo", "time", "non_media_channel"),
            name="non_media_treatments",
        ),
    )


def test_channel_data_organic_and_non_media_types():
    rows = extract_channel_data(SimpleNamespace(input_data=_organic_non_media_input_data()))
    email = next(r for r in rows if r["channel"] == "email")
    podcast = next(r for r in rows if r["channel"] == "podcast")
    promo = next(r for r in rows if r["channel"] == "promo")

    # organic_media: impressions populated; spend/reach/frequency/rf_spend/value null.
    assert email["channel_type"] == "organic_media"
    assert email["impressions"] == 50.0
    assert email["spend"] is None and email["reach"] is None
    assert email["frequency"] is None and email["rf_spend"] is None and email["value"] is None

    # organic_rf: reach/frequency populated; others null.
    assert podcast["channel_type"] == "organic_rf"
    assert podcast["reach"] == 200.0 and podcast["frequency"] == 2.0
    assert podcast["impressions"] is None and podcast["spend"] is None
    assert podcast["rf_spend"] is None and podcast["value"] is None

    # non_media: value populated; others null.
    assert promo["channel_type"] == "non_media"
    assert promo["value"] == 1.0
    assert promo["impressions"] is None and promo["spend"] is None
    assert promo["reach"] is None and promo["frequency"] is None and promo["rf_spend"] is None

    # All rows must share the same column set.
    assert set(email) == set(podcast) == set(promo)

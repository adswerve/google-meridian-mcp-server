"""validate_full_funnel: every error message and the KPI-mismatch warning (fakes)."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest
import xarray as xr

from google_meridian_mcp_server.domain.errors import InvalidFullFunnelModelError
from google_meridian_mcp_server.meridian.full_funnel import loading
from google_meridian_mcp_server.meridian.full_funnel.validation import (
    validate_full_funnel,
)

GEOS = ("g1", "g2")
TIMES = ("2024-01-01", "2024-01-08", "2024-01-15")


def _stage(
    *,
    paid,
    organic=(),
    saturation=None,
    geos=GEOS,
    times=TIMES,
    chains=1,
    draws=10,
    prior_draws=10,
    kpi=None,
    organic_values=None,
    lag=0,
):
    shape = (len(geos), len(times))
    kpi = np.arange(np.prod(shape), dtype=float).reshape(shape) if kpi is None else kpi
    om = om_values = None
    if organic:
        base = (
            organic_values
            if organic_values is not None
            else np.stack([kpi] * len(organic), axis=-1)
        )
        if lag:
            base = np.concatenate(
                [np.zeros((shape[0], lag, len(organic))), base], axis=1
            )
        om = xr.DataArray(list(organic), dims="organic_media_channel")
        om_values = xr.DataArray(
            base,
            dims=("geo", "media_time", "organic_media_channel"),
            coords={"organic_media_channel": list(organic)},
        )
    input_data = SimpleNamespace(
        geo=xr.DataArray(list(geos)),
        time=xr.DataArray(list(times)),
        kpi=xr.DataArray(kpi, dims=("geo", "time")),
        organic_media_channel=om,
        organic_media=om_values,
        get_all_paid_channels=lambda: np.asarray(paid),
    )
    return SimpleNamespace(
        model_context=SimpleNamespace(
            input_data=input_data,
            model_spec=SimpleNamespace(saturation_spec=saturation or {}),
        ),
        inference_data=SimpleNamespace(
            posterior=SimpleNamespace(
                chain=SimpleNamespace(size=chains), draw=SimpleNamespace(size=draws)
            ),
            prior=SimpleNamespace(
                chain=SimpleNamespace(size=1), draw=SimpleNamespace(size=prior_draws)
            ),
        ),
    )


def _pair():
    s1 = _stage(paid=["A"])
    s2 = _stage(paid=["A", "B"], organic=["M1"], saturation={"M1": "none"})
    return s2, {"M1": s1}


def test_valid_pair_has_no_warnings():
    s2, meds = _pair()
    assert validate_full_funnel(s2, meds) == []


def test_unknown_mediator_name_lists_valid_channels():
    s2, meds = _pair()
    with pytest.raises(InvalidFullFunnelModelError) as exc:
        validate_full_funnel(s2, {"m1": meds["M1"]})
    assert "mediators/m1.binpb" in str(exc.value)
    assert "found: M1" in str(exc.value)


def test_no_organic_media_in_kpi_model():
    with pytest.raises(InvalidFullFunnelModelError, match="no organic media"):
        validate_full_funnel(_stage(paid=["A"]), {"M1": _stage(paid=["A"])})


@pytest.mark.parametrize("saturation", [{"M1": "hill"}, {}, "hill"])
def test_saturation_must_be_none(saturation):
    s2, meds = _pair()
    s2.model_context.model_spec.saturation_spec = saturation
    with pytest.raises(InvalidFullFunnelModelError, match='"M1": "none"'):
        validate_full_funnel(s2, meds)


def test_string_saturation_none_is_accepted():
    s2, meds = _pair()
    s2.model_context.model_spec.saturation_spec = "none"
    assert validate_full_funnel(s2, meds) == []


@pytest.mark.parametrize("geos", [("g1",), ("g2", "g1")])
def test_geo_mismatch_or_order(geos):
    s2, _ = _pair()
    with pytest.raises(InvalidFullFunnelModelError, match="different geos"):
        validate_full_funnel(s2, {"M1": _stage(paid=["A"], geos=geos)})


def test_time_mismatch():
    s2, _ = _pair()
    with pytest.raises(InvalidFullFunnelModelError, match="different time periods"):
        validate_full_funnel(s2, {"M1": _stage(paid=["A"], times=TIMES[:2])})


def test_posterior_draw_mismatch():
    s2, _ = _pair()
    with pytest.raises(InvalidFullFunnelModelError, match="same n_chains and n_keep"):
        validate_full_funnel(s2, {"M1": _stage(paid=["A"], draws=5)})


def test_prior_draw_mismatch():
    s2, _ = _pair()
    with pytest.raises(InvalidFullFunnelModelError, match="sample_prior"):
        validate_full_funnel(s2, {"M1": _stage(paid=["A"], prior_draws=3)})


def test_missing_paid_channel():
    s2, _ = _pair()
    with pytest.raises(
        InvalidFullFunnelModelError, match="missing from the KPI model: TV"
    ):
        validate_full_funnel(s2, {"M1": _stage(paid=["TV"])})


def test_kpi_not_matching_organic_column_is_a_warning():
    s2, meds = _pair()
    s2.model_context.input_data.organic_media = (
        s2.model_context.input_data.organic_media * 2
    )
    warnings = validate_full_funnel(s2, meds)
    assert len(warnings) == 1 and "M1" in warnings[0]


def test_lagged_media_history_is_not_a_kpi_mismatch():
    s1 = _stage(paid=["A"])
    s2 = _stage(paid=["A"], organic=["M1"], saturation={"M1": "none"}, lag=2)
    assert validate_full_funnel(s2, {"M1": s1}) == []


def test_load_full_funnel_loads_sorted_and_validates(monkeypatch):
    s2, meds = _pair()
    meds["M0"] = _stage(paid=["A"])
    s2.model_context.input_data.organic_media_channel = xr.DataArray(
        ["M0", "M1"], dims="organic_media_channel"
    )
    s2.model_context.input_data.organic_media = xr.DataArray(
        np.stack([s2.model_context.input_data.kpi.values] * 2, axis=-1),
        dims=("geo", "media_time", "organic_media_channel"),
        coords={"organic_media_channel": ["M0", "M1"]},
    )
    s2.model_context.model_spec.saturation_spec = {"M0": "none", "M1": "none"}
    by_path = {"s2": s2, "p0": meds["M0"], "p1": meds["M1"]}
    monkeypatch.setattr(loading, "load_meridian_model", lambda p: by_path[str(p)])
    stage2, loaded = loading.load_full_funnel("s2", {"M1": "p1", "M0": "p0"})
    assert stage2 is s2
    assert list(loaded) == ["M0", "M1"]

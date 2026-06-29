"""Guard: get_model_fit depends on Meridian's ModelFit._transform_data_to_dataframe.

If a Meridian upgrade renames/removes this private method or drops the
selected_geos/selected_times parameters, get_model_fit's geo filtering breaks.
This test fails loudly and points to
docs/superpowers/plans/2026-06-29-model-fit-geo-filtering.md.
"""

from __future__ import annotations

import inspect


def test_modelfit_transform_exposes_geo_and_time_params():
    from meridian.analysis import visualizer

    assert hasattr(visualizer.ModelFit, "_transform_data_to_dataframe")
    params = inspect.signature(
        visualizer.ModelFit._transform_data_to_dataframe
    ).parameters
    assert "selected_times" in params
    assert "selected_geos" in params


def test_modelfit_dataframe_schema_constants_unchanged():
    """Guard the column names/values _reshape_model_fit hard-codes.

    _reshape_model_fit pivots ModelFit's long frame using these literal
    strings; if Meridian renames any, the reshape breaks with a KeyError.
    """
    from meridian import constants

    assert constants.TYPE == "type"
    assert constants.MEAN == "mean"
    assert constants.CI_LO == "ci_lo"
    assert constants.CI_HI == "ci_hi"
    assert constants.EXPECTED == "expected"
    assert constants.BASELINE == "baseline"
    assert constants.ACTUAL == "actual"
    assert constants.TIME == "time"

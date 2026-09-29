"""_with_paid_split edge cases on a synthetic dataset (no Meridian model needed)."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import xarray as xr

from google_meridian_mcp_server.domain.filters import AnalysisFilters
from google_meridian_mcp_server.meridian.analyzer_facade import AnalyzerFacade


def _facade_with_split(direct, indirect):
    facade = AnalyzerFacade.__new__(AnalyzerFacade)
    split = SimpleNamespace(direct=direct, indirect=indirect)
    facade._funnel_split = lambda filters, *, aggregate_times: split
    return facade


def test_zero_spend_roi_split_is_null_not_inf():
    channels, metrics = ["A", "B", "All Channels"], ["mean", "median"]

    def arr(values):
        return xr.DataArray(
            values,
            dims=("channel", "metric"),
            coords={"channel": channels, "metric": metrics},
        )

    ds = xr.Dataset(
        {
            "incremental_outcome": arr(np.ones((3, 2))),
            "spend": xr.DataArray(
                [0.0, 10.0, 10.0], dims=("channel",), coords={"channel": channels}
            ),
            "roi": arr(np.ones((3, 2))),
        }
    )
    facade = _facade_with_split(
        direct={"A": 2.0, "B": 4.0}, indirect={"A": 1.0, "B": 1.0}
    )

    out = facade._with_paid_split(ds, AnalysisFilters(), include_roi=True)

    roi_direct = out["roi_direct"].sel(metric="mean").to_series().to_dict()
    assert np.isnan(roi_direct["A"])  # 2.0 / 0.0 would be inf
    assert roi_direct["B"] == 0.4
    assert roi_direct["All Channels"] == 0.6  # (2 + 4) / 10
    assert out["roi_indirect"].sel(metric="mean", channel="All Channels") == 0.2
    assert np.isnan(out["roi_direct"].sel(metric="median")).all()

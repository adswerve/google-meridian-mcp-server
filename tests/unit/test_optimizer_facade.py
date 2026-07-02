# tests/unit/test_optimizer_facade.py
import numpy as np
import xarray as xr

from google_meridian_mcp_server.meridian.optimizer_facade import OptimizerFacade


def _dataset(
    channels, *, budget, total_outcome, total_roi, spend, roi, mroi, cpik, eff, inc
):
    metrics = ["mean", "median", "ci_lo", "ci_hi"]

    def per_channel(values_by_metric):
        return xr.DataArray(
            np.array(
                [
                    [values_by_metric[m][c] for m in metrics]
                    for c in range(len(channels))
                ]
            ),
            dims=("channel", "metric"),
            coords={"channel": channels, "metric": metrics},
        )

    ds = xr.Dataset(
        {
            "spend": xr.DataArray(
                np.array(spend), dims="channel", coords={"channel": channels}
            ),
            "pct_of_spend": xr.DataArray(
                np.array(spend) / np.sum(spend),
                dims="channel",
                coords={"channel": channels},
            ),
            "incremental_outcome": per_channel(inc),
            "roi": per_channel(roi),
            "mroi": per_channel(mroi),
            "cpik": per_channel(cpik),
            "effectiveness": per_channel(eff),
        }
    )
    ds.attrs.update(
        budget=budget, total_incremental_outcome=total_outcome, total_roi=total_roi
    )
    return ds


def _const(channels, value):
    return {c: value for c in range(len(channels))}


def test_build_result_revenue_mode():
    channels = ["tv", "search"]
    common = dict(
        roi={m: _const(channels, 3.0) for m in ["mean", "median", "ci_lo", "ci_hi"]},
        mroi={m: _const(channels, 2.0) for m in ["mean", "median", "ci_lo", "ci_hi"]},
        cpik={m: _const(channels, 0.5) for m in ["mean", "median", "ci_lo", "ci_hi"]},
        eff={m: _const(channels, 0.1) for m in ["mean", "median", "ci_lo", "ci_hi"]},
        inc={m: _const(channels, 1000.0) for m in ["mean", "median", "ci_lo", "ci_hi"]},
    )
    nonopt = _dataset(
        channels,
        budget=1000.0,
        total_outcome=2000.0,
        total_roi=2.0,
        spend=[600.0, 400.0],
        **common,
    )
    opt = _dataset(
        channels,
        budget=1000.0,
        total_outcome=2600.0,
        total_roi=2.6,
        spend=[300.0, 700.0],
        **common,
    )

    result = OptimizerFacade.build_result(nonopt, opt, use_kpi=False)
    assert result["outcome_mode"] == "revenue"
    assert result["summary"]["optimized_efficiency"] == 2.6
    assert result["summary"]["non_optimized_efficiency"] == 2.0
    initial = {r["channel"]: r for r in result["channel_tables"]["initial"]}
    assert initial["tv"]["spend"] == 600.0
    assert initial["tv"]["roi"] == 3.0
    # spend_delta sorted negatives-first then positives-descending
    deltas = {r["channel"]: r["spend"] for r in result["spend_delta"]}
    assert deltas["tv"] == -300.0 and deltas["search"] == 300.0
    assert result["allocation"][0]["channel"] in channels


def test_build_result_kpi_mode_inverts_efficiency():
    channels = ["tv"]
    common = dict(
        roi={m: _const(channels, 4.0) for m in ["mean", "median", "ci_lo", "ci_hi"]},
        mroi={m: _const(channels, 2.0) for m in ["mean", "median", "ci_lo", "ci_hi"]},
        cpik={m: _const(channels, 0.25) for m in ["mean", "median", "ci_lo", "ci_hi"]},
        eff={m: _const(channels, 0.1) for m in ["mean", "median", "ci_lo", "ci_hi"]},
        inc={m: _const(channels, 100.0) for m in ["mean", "median", "ci_lo", "ci_hi"]},
    )
    nonopt = _dataset(
        channels,
        budget=100.0,
        total_outcome=100.0,
        total_roi=4.0,
        spend=[100.0],
        **common,
    )
    opt = _dataset(
        channels,
        budget=100.0,
        total_outcome=100.0,
        total_roi=4.0,
        spend=[100.0],
        **common,
    )
    result = OptimizerFacade.build_result(nonopt, opt, use_kpi=True)
    assert result["outcome_mode"] == "kpi"
    assert result["summary"]["optimized_efficiency"] == 0.25  # 1/total_roi


def test_kpi_mode_zero_total_roi_yields_none_efficiency():
    """FIX 5: KPI mode with total_roi==0 → optimized_efficiency is None, not inf."""
    channels = ["tv"]
    common = dict(
        roi={m: _const(channels, 0.0) for m in ["mean", "median", "ci_lo", "ci_hi"]},
        mroi={m: _const(channels, 0.0) for m in ["mean", "median", "ci_lo", "ci_hi"]},
        cpik={m: _const(channels, 0.0) for m in ["mean", "median", "ci_lo", "ci_hi"]},
        eff={m: _const(channels, 0.0) for m in ["mean", "median", "ci_lo", "ci_hi"]},
        inc={m: _const(channels, 0.0) for m in ["mean", "median", "ci_lo", "ci_hi"]},
    )
    nonopt = _dataset(
        channels,
        budget=100.0,
        total_outcome=0.0,
        total_roi=0.0,
        spend=[100.0],
        **common,
    )
    opt = _dataset(
        channels,
        budget=100.0,
        total_outcome=0.0,
        total_roi=0.0,
        spend=[100.0],
        **common,
    )
    result = OptimizerFacade.build_result(nonopt, opt, use_kpi=True)
    assert result["summary"]["optimized_efficiency"] is None
    assert result["summary"]["non_optimized_efficiency"] is None


def test_sig6_non_finite_returns_none():
    """FIX 5: _sig6 returns None for inf, -inf, nan, and None."""
    import math

    from google_meridian_mcp_server.meridian.optimizer_facade import _sig6

    assert _sig6(float("inf")) is None
    assert _sig6(float("-inf")) is None
    assert _sig6(float("nan")) is None
    assert _sig6(None) is None
    # Finite values still work
    assert _sig6(2.5) == 2.5
    assert not math.isnan(_sig6(2.5))


def _fake_response_curves():
    # dims: channel x spend_multiplier, metric coord; var incremental_outcome
    channels = ["tv", "search"]
    multipliers = [0.0, 1.0, 2.0]
    spend = np.array([[0.0, 100.0, 200.0], [0.0, 50.0, 100.0]])
    inc = np.array([[0.0, 300.0, 450.0], [0.0, 120.0, 150.0]])
    return xr.Dataset(
        {
            "spend": (("channel", "spend_multiplier"), spend),
            "incremental_outcome": (
                ("channel", "spend_multiplier", "metric"),
                inc[:, :, None],
            ),
        },
        coords={
            "channel": channels,
            "spend_multiplier": multipliers,
            "metric": ["mean"],
        },
    )


def test_response_curve_rows_shape_and_rounding():
    rows = OptimizerFacade._response_curve_rows(_fake_response_curves())
    assert {"channel", "spend", "incremental_outcome"} == set(rows[0])
    # one row per (channel, spend_multiplier) point
    assert len(rows) == 6
    tv0 = next(r for r in rows if r["channel"] == "tv" and r["spend"] == 100.0)
    assert tv0["incremental_outcome"] == 300.0

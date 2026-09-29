# tests/unit/test_optimizer_facade.py
from unittest.mock import MagicMock

import numpy as np
import pytest
import xarray as xr

from google_meridian_mcp_server.domain.optimization import (
    FutureOptimizationConfig,
    OptimizationConfig,
)
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


def test_build_result_includes_assumptions_when_provided():
    channels = ["tv", "search"]
    common = dict(
        roi={m: _const(channels, 3.0) for m in ["mean", "median", "ci_lo", "ci_hi"]},
        mroi={m: _const(channels, 2.0) for m in ["mean", "median", "ci_lo", "ci_hi"]},
        cpik={m: _const(channels, 0.5) for m in ["mean", "median", "ci_lo", "ci_hi"]},
        eff={m: _const(channels, 0.1) for m in ["mean", "median", "ci_lo", "ci_hi"]},
        inc={m: _const(channels, 1000.0) for m in ["mean", "median", "ci_lo", "ci_hi"]},
    )
    ds = _dataset(
        channels,
        budget=1000.0,
        total_outcome=2000.0,
        total_roi=2.0,
        spend=[600.0, 400.0],
        **common,
    )
    assumptions = {
        "budget": 1000.0,
        "budget_source": "derived_from_reference",
        "reference_mode": "full_history_average",
        "excluded_channels": ["tv"],
    }
    result = OptimizerFacade.build_result(
        ds, ds, use_kpi=False, assumptions=assumptions
    )
    assert result["assumptions"] == assumptions

    # Historical/no assumptions → key absent.
    result2 = OptimizerFacade.build_result(ds, ds, use_kpi=False)
    assert "assumptions" not in result2


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


def test_run_enriches_curves_but_run_future_does_not(monkeypatch):
    """FIX I1: historical `_run` still enriches with response_curves; a future
    run (enrich_curves=False) never calls get_response_curves() at all, since
    Meridian's get_response_curves ignores new_data and would silently return
    curves reflecting historical, not future, cost/flighting."""
    channels = ["tv"]
    common = dict(
        roi={m: _const(channels, 3.0) for m in ["mean", "median", "ci_lo", "ci_hi"]},
        mroi={m: _const(channels, 2.0) for m in ["mean", "median", "ci_lo", "ci_hi"]},
        cpik={m: _const(channels, 0.5) for m in ["mean", "median", "ci_lo", "ci_hi"]},
        eff={m: _const(channels, 0.1) for m in ["mean", "median", "ci_lo", "ci_hi"]},
        inc={m: _const(channels, 100.0) for m in ["mean", "median", "ci_lo", "ci_hi"]},
    )
    nonopt = _dataset(
        channels,
        budget=100.0,
        total_outcome=100.0,
        total_roi=3.0,
        spend=[100.0],
        **common,
    )
    opt = _dataset(
        channels,
        budget=100.0,
        total_outcome=100.0,
        total_roi=3.0,
        spend=[100.0],
        **common,
    )

    calls = {"n": 0}

    class FakeResults:
        nonoptimized_data = nonopt
        optimized_data = opt

        def get_response_curves(self):
            calls["n"] += 1
            return _fake_response_curves()

    class FakeBudgetOptimizer:
        def __init__(self, mmm):
            pass

        def optimize(self, **kwargs):
            return FakeResults()

    import meridian.analysis.optimizer as optimizer_mod

    monkeypatch.setattr(optimizer_mod, "BudgetOptimizer", FakeBudgetOptimizer)

    facade = OptimizerFacade.__new__(OptimizerFacade)
    facade._mmm = MagicMock()
    facade._mediators = {}
    facade.resolve_use_kpi = MagicMock(return_value=False)
    build_kwargs = lambda config, opt, use_kpi: {}  # noqa: E731

    historical_result = facade._run(None, build_kwargs)
    assert "response_curves" in historical_result
    assert calls["n"] == 1

    future_result = facade._run(None, build_kwargs, enrich_curves=False)
    assert "response_curves" not in future_result
    assert calls["n"] == 1  # get_response_curves must not be called for future


def test_execute_dispatches_by_kind(monkeypatch):
    facade = OptimizerFacade.__new__(OptimizerFacade)  # no real model needed
    facade.run = MagicMock(return_value={"ran": "historical"})
    facade.run_future = MagicMock(return_value={"ran": "future"})

    hist = OptimizationConfig.model_validate({"scenario": {"type": "fixed_budget"}})
    fut = FutureOptimizationConfig.model_validate(
        {
            "scenario": {"type": "fixed_budget"},
            "future": {"start_date": "2026-10-01", "horizon": 4},
        }
    )
    assert facade.execute(hist) == {"ran": "historical"}
    assert facade.execute(fut) == {"ran": "future"}
    facade.run.assert_called_once_with(hist)
    facade.run_future.assert_called_once_with(fut)


def test_seed_cpmu_raises_on_dark_channel_zero_media_units():
    """FIX M1: spend>0 but zero summed media units for a channel over the
    reference window must raise a clear, named ValueError rather than silently
    producing an inf/NaN cost-per-media-unit that flows into optimize()."""
    facade = OptimizerFacade.__new__(OptimizerFacade)
    channels = ["tv", "dark_channel"]
    n_geo, n_time = 1, 3

    media_spend = np.ones((n_geo, n_time, len(channels)))  # spend > 0 for both
    media = np.zeros((n_geo, n_time, len(channels)))
    media[..., 0] = 5.0  # "tv" has media units; "dark_channel" stays all-zero

    input_data = MagicMock()
    input_data.media_spend.values = media_spend
    input_data.media.values = media
    facade._mmm = MagicMock(input_data=input_data)
    facade.get_time_values = MagicMock(
        return_value=["2024-01-01", "2024-01-08", "2024-01-15"]
    )
    facade.get_data_inputs = MagicMock(return_value={"media": channels, "rf_media": []})

    with pytest.raises(ValueError, match="dark_channel"):
        facade._seed_cpmu(window=[0, 1, 2])


def test_seed_cprf_raises_on_dark_channel_zero_impressions():
    """FIX M1: same guard for the RF path — zero summed reach*frequency
    (impressions) for a channel with spend > 0 must raise, not divide by zero."""
    facade = OptimizerFacade.__new__(OptimizerFacade)
    channels = ["yt_rf", "dark_rf"]
    n_geo, n_time = 1, 3

    rf_spend = np.ones((n_geo, n_time, len(channels)))  # spend > 0 for both
    reach = np.zeros((n_geo, n_time, len(channels)))
    frequency = np.zeros((n_geo, n_time, len(channels)))
    reach[..., 0] = 10.0
    frequency[..., 0] = 2.0  # "yt_rf" has impressions; "dark_rf" stays all-zero

    input_data = MagicMock()
    input_data.rf_spend.values = rf_spend
    input_data.reach.values = reach
    input_data.frequency.values = frequency
    facade._mmm = MagicMock(input_data=input_data)
    facade.get_time_values = MagicMock(
        return_value=["2024-01-01", "2024-01-08", "2024-01-15"]
    )
    facade.get_data_inputs = MagicMock(return_value={"media": [], "rf_media": channels})

    with pytest.raises(ValueError, match="dark_rf"):
        facade._seed_cprf(window=[0, 1, 2])


def test_seed_cpmu_tolerates_excluded_dark_channel():
    """Excluding a zero-media-unit channel must NOT raise; its cpmu is a finite
    positive placeholder (spend is forced to 0 downstream, so the value is inert)."""
    facade = OptimizerFacade.__new__(OptimizerFacade)
    channels = ["tv", "dark_channel"]
    media_spend = np.ones((1, 3, len(channels)))
    media = np.zeros((1, 3, len(channels)))
    media[..., 0] = 5.0  # dark_channel stays all-zero
    input_data = MagicMock()
    input_data.media_spend.values = media_spend
    input_data.media.values = media
    facade._mmm = MagicMock(input_data=input_data)
    facade.get_time_values = MagicMock(
        return_value=["2024-01-01", "2024-01-08", "2024-01-15"]
    )
    facade.get_data_inputs = MagicMock(return_value={"media": channels, "rf_media": []})

    cpmu = facade._seed_cpmu(window=[0, 1, 2], excluded_idx=frozenset({1}))
    assert np.isfinite(cpmu).all()
    assert cpmu[1] > 0  # benign placeholder for the excluded dark channel


def test_seed_cprf_tolerates_excluded_dark_channel():
    facade = OptimizerFacade.__new__(OptimizerFacade)
    channels = ["yt_rf", "dark_rf"]
    rf_spend = np.ones((1, 3, len(channels)))
    reach = np.zeros((1, 3, len(channels)))
    frequency = np.zeros((1, 3, len(channels)))
    reach[..., 0] = 10.0
    frequency[..., 0] = 2.0
    input_data = MagicMock()
    input_data.rf_spend.values = rf_spend
    input_data.reach.values = reach
    input_data.frequency.values = frequency
    facade._mmm = MagicMock(input_data=input_data)
    facade.get_time_values = MagicMock(
        return_value=["2024-01-01", "2024-01-08", "2024-01-15"]
    )
    facade.get_data_inputs = MagicMock(return_value={"media": [], "rf_media": channels})

    cprf = facade._seed_cprf(window=[0, 1, 2], excluded_idx=frozenset({1}))
    assert np.isfinite(cprf).all()
    assert cprf[1] > 0


def test_seed_cpmu_bumps_excluded_zero_spend_channel_to_one():
    """The real dark channel is zero-spend AND zero-units: spend_sum=0, safe=1.0
    -> cpmu=0, and _benign_excluded_cost must bump it to exactly 1.0 so Meridian
    never sees a zero-cost channel. This is the branch the spend>0 test misses."""
    facade = OptimizerFacade.__new__(OptimizerFacade)
    channels = ["tv", "dark_channel"]
    media_spend = np.ones((1, 3, len(channels)))
    media = np.zeros((1, 3, len(channels)))
    media[..., 0] = 5.0
    media_spend[..., 1] = 0.0  # dark_channel: zero spend AND zero units
    input_data = MagicMock()
    input_data.media_spend.values = media_spend
    input_data.media.values = media
    facade._mmm = MagicMock(input_data=input_data)
    facade.get_time_values = MagicMock(
        return_value=["2024-01-01", "2024-01-08", "2024-01-15"]
    )
    facade.get_data_inputs = MagicMock(return_value={"media": channels, "rf_media": []})

    cpmu = facade._seed_cpmu(window=[0, 1, 2], excluded_idx=frozenset({1}))
    assert cpmu[1] == 1.0  # bump fired (0/1 -> 0 -> 1.0)


def test_seed_cpmu_check_is_window_scoped():
    """A channel dark only INSIDE the reference window raises; the same channel
    active outside the window does not save it — the guard is window-scoped."""
    facade = OptimizerFacade.__new__(OptimizerFacade)
    channels = ["tv", "windowed_dark"]
    media_spend = np.ones((1, 3, len(channels)))
    media = np.ones((1, 3, len(channels)))
    media[:, 1:, 1] = 0.0  # windowed_dark has units only at period 0
    input_data = MagicMock()
    input_data.media_spend.values = media_spend
    input_data.media.values = media
    facade._mmm = MagicMock(input_data=input_data)
    facade.get_time_values = MagicMock(
        return_value=["2024-01-01", "2024-01-08", "2024-01-15"]
    )
    facade.get_data_inputs = MagicMock(return_value={"media": channels, "rf_media": []})

    # window [1, 2] -> windowed_dark has zero units -> raises
    with pytest.raises(ValueError, match="windowed_dark"):
        facade._seed_cpmu(window=[1, 2])
    # window [0] -> windowed_dark has units -> no raise
    facade._seed_cpmu(window=[0])


def test_validate_future_rejects_flat_spend_granularity_at_submit():
    """FIX M2: a model whose media_spend tensor lacks a time axis (2-D, no geo
    dimension) must be rejected by validate_future -- at submit time, before
    any tensors are built -- with the same message the worker-side `_spend_np`
    guard uses, rather than surfacing only as a failed run later."""
    facade = OptimizerFacade.__new__(OptimizerFacade)
    weekly_times = [
        "2024-01-01",
        "2024-01-08",
        "2024-01-15",
        "2024-01-22",
        "2024-01-29",
        "2024-02-05",
    ]
    input_data = MagicMock()
    # 2-D (time, channel) -- missing the geo axis that a real InputData tensor has.
    input_data.media_spend.values = np.ones((len(weekly_times), 2))
    facade._mmm = MagicMock(input_data=input_data)
    facade.get_time_values = MagicMock(return_value=weekly_times)
    facade.get_data_inputs = MagicMock(
        return_value={"media": ["tv", "search"], "rf_media": []}
    )
    facade.channel_order = MagicMock(return_value=["tv", "search"])

    config = FutureOptimizationConfig.model_validate(
        {
            "scenario": {"type": "fixed_budget"},
            "future": {
                "start_date": "2024-03-01",
                "horizon": 4,
                "reference": {"mode": "trailing"},
            },
        }
    )

    with pytest.raises(ValueError, match="unsupported spend granularity"):
        facade.validate_future(config)


def test_validate_future_fails_fast_on_dark_non_excluded_channel():
    """A non-excluded channel with zero media units over the reference window
    must raise at submit (validate_future), naming the channel and pointing at
    excluded_channels, rather than surfacing later as a FAILED run."""
    facade = OptimizerFacade.__new__(OptimizerFacade)
    channels = ["tv", "dark_channel"]
    weekly = [
        "2024-01-01",
        "2024-01-08",
        "2024-01-15",
        "2024-01-22",
        "2024-01-29",
        "2024-02-05",
        "2024-02-12",
        "2024-02-19",
    ]
    media_spend = np.ones((1, len(weekly), len(channels)))
    media = np.zeros((1, len(weekly), len(channels)))
    media[..., 0] = 5.0  # dark_channel has zero media units everywhere
    input_data = MagicMock()
    input_data.media_spend.values = media_spend
    input_data.media.values = media
    facade._mmm = MagicMock(input_data=input_data)
    facade.get_time_values = MagicMock(return_value=weekly)
    facade.get_data_inputs = MagicMock(return_value={"media": channels, "rf_media": []})
    facade.channel_order = MagicMock(return_value=channels)

    cfg = FutureOptimizationConfig.model_validate(
        {
            "scenario": {"type": "fixed_budget"},
            "future": {
                "start_date": "2024-03-01",
                "horizon": 4,
                "reference": {"mode": "trailing"},
            },
        }
    )
    with pytest.raises(ValueError, match="dark_channel"):
        facade.validate_future(cfg)


def test_validate_future_passes_when_dark_channel_excluded():
    """Same setup, but excluding the dark channel makes validate_future pass."""
    facade = OptimizerFacade.__new__(OptimizerFacade)
    channels = ["tv", "dark_channel"]
    weekly = [
        "2024-01-01",
        "2024-01-08",
        "2024-01-15",
        "2024-01-22",
        "2024-01-29",
        "2024-02-05",
        "2024-02-12",
        "2024-02-19",
    ]
    media_spend = np.ones((1, len(weekly), len(channels)))
    media = np.zeros((1, len(weekly), len(channels)))
    media[..., 0] = 5.0
    input_data = MagicMock()
    input_data.media_spend.values = media_spend
    input_data.media.values = media
    facade._mmm = MagicMock(input_data=input_data)
    facade.get_time_values = MagicMock(return_value=weekly)
    facade.get_data_inputs = MagicMock(return_value={"media": channels, "rf_media": []})
    facade.channel_order = MagicMock(return_value=channels)

    cfg = FutureOptimizationConfig.model_validate(
        {
            "scenario": {"type": "fixed_budget"},
            "future": {
                "start_date": "2024-03-01",
                "horizon": 4,
                "reference": {"mode": "trailing"},
                "excluded_channels": ["dark_channel"],
            },
        }
    )
    facade.validate_future(cfg)  # must not raise


def test_channel_rows_add_split_only_when_direct_given():
    metrics = ["mean", "median"]
    ds = xr.Dataset(
        {
            "spend": (("channel",), [10.0]),
            "pct_of_spend": (("channel",), [1.0]),
            "incremental_outcome": (("channel", "metric"), [[30.0, 29.0]]),
            "roi": (("channel", "metric"), [[3.0, 2.9]]),
            "mroi": (("channel", "metric"), [[1.0, 1.0]]),
            "cpik": (("channel", "metric"), [[0.3, 0.3]]),
            "effectiveness": (("channel", "metric"), [[0.1, 0.1]]),
        },
        coords={"channel": ["A"], "metric": metrics},
    )
    plain = OptimizerFacade._channel_rows(ds, use_kpi=False)[0]
    assert "incremental_outcome_direct" not in plain
    assert "incremental_outcome_indirect" not in plain
    split = OptimizerFacade._channel_rows(ds, use_kpi=False, direct={"A": 20.0})[0]
    assert split["incremental_outcome_direct"] == 20.0
    assert split["incremental_outcome_indirect"] == 10.0


def _run_datasets():
    channels = ["tv"]
    stats = ["mean", "median", "ci_lo", "ci_hi"]

    def build(inc):
        return _dataset(
            channels,
            budget=100.0,
            total_outcome=inc,
            total_roi=3.0,
            spend=[100.0],
            roi={m: _const(channels, 3.0) for m in stats},
            mroi={m: _const(channels, 2.0) for m in stats},
            cpik={m: _const(channels, 0.5) for m in stats},
            eff={m: _const(channels, 0.1) for m in stats},
            inc={m: _const(channels, inc) for m in stats},
        )

    return build(100.0), build(140.0)


def _recording_optimizer(monkeypatch, results):
    """Replace BudgetOptimizer with a fake that records how it was constructed."""
    import meridian.analysis.optimizer as optimizer_mod

    constructed = []

    class RecordingBudgetOptimizer:
        def __init__(self, *args, **kwargs):
            constructed.append((args, kwargs))

        def optimize(self, **kwargs):
            return results

    monkeypatch.setattr(optimizer_mod, "BudgetOptimizer", RecordingBudgetOptimizer)
    return constructed


def _fake_results():
    nonopt, opt = _run_datasets()
    results = MagicMock()
    results.nonoptimized_data = nonopt
    results.optimized_data = opt
    results.get_response_curves.return_value = _fake_response_curves()
    return results


def test_run_hands_the_full_funnel_analyzer_to_the_optimizer_and_splits(monkeypatch):
    results = _fake_results()
    results.optimization_grid.optimal_frequency = optimal_frequency = object()
    constructed = _recording_optimizer(monkeypatch, results)
    analyzer = object()
    facade = OptimizerFacade.__new__(OptimizerFacade)
    facade._mmm = mmm = MagicMock()
    facade._mediators = {"M1": object()}
    facade.resolve_use_kpi = MagicMock(return_value=False)
    facade._get_analyzer = MagicMock(return_value=analyzer)
    facade._direct_incremental = MagicMock(side_effect=[{"tv": 30.0}, {"tv": 50.0}])
    build_kwargs = lambda config, opt, use_kpi: {"start_date": None}  # noqa: E731

    result = facade._run(None, build_kwargs)

    assert constructed == [((mmm,), {"analyzer": analyzer})]
    initial_call, optimized_call = facade._direct_incremental.call_args_list
    assert initial_call.args[:2] == (results, results.nonoptimized_data)
    assert optimized_call.args[:2] == (results, results.optimized_data)
    assert initial_call.args[2] == {"start_date": None}  # the optimize() kwargs
    assert initial_call.args[3] is False  # use_kpi
    # The optimized allocation is scored at the grid's optimal frequency, the
    # initial one at the historical frequency, exactly as Meridian scores them.
    assert initial_call.kwargs.get("optimal_frequency") is None
    assert optimized_call.kwargs["optimal_frequency"] is optimal_frequency
    (initial,) = result["channel_tables"]["initial"]
    (optimized,) = result["channel_tables"]["optimized"]
    assert (
        initial["incremental_outcome_direct"],
        initial["incremental_outcome_indirect"],
    ) == (30.0, 70.0)
    assert (
        optimized["incremental_outcome_direct"],
        optimized["incremental_outcome_indirect"],
    ) == (50.0, 90.0)


def test_run_on_a_single_model_builds_the_optimizer_as_before(monkeypatch):
    results = _fake_results()
    constructed = _recording_optimizer(monkeypatch, results)
    facade = OptimizerFacade.__new__(OptimizerFacade)
    facade._mmm = mmm = MagicMock()
    facade._mediators = {}
    facade.resolve_use_kpi = MagicMock(return_value=False)
    facade._get_analyzer = MagicMock()
    facade._direct_incremental = MagicMock()

    result = facade._run(None, lambda config, opt, use_kpi: {})

    assert constructed == [((mmm,), {})]
    facade._get_analyzer.assert_not_called()
    facade._direct_incremental.assert_not_called()
    (row,) = result["channel_tables"]["optimized"]
    assert "incremental_outcome_direct" not in row
    assert "incremental_outcome_indirect" not in row


def _future_facade(mediators):
    """A facade whose future-kwargs inputs are faked: weekly history, two channels."""
    facade = OptimizerFacade.__new__(OptimizerFacade)
    facade._mediators = mediators
    facade.get_time_values = lambda: [
        "2026-01-05",
        "2026-01-12",
        "2026-01-19",
        "2026-01-26",
    ]
    facade.get_data_inputs = lambda: {"media": ["A", "B"], "rf_media": []}
    facade.has_revenue_per_kpi = lambda: False
    facade._seed_cpmu = lambda window, excluded=frozenset(): np.ones(2)
    facade._seed_spend_flighting = lambda kind, window, horizon, average: np.full(
        (1, horizon, 2), 5.0
    )
    facade._carried_allocation = lambda window: {"A": 1.0, "B": 1.0}
    return facade


def _future_config(**future):
    return FutureOptimizationConfig.model_validate(
        {
            "scenario": {"type": "fixed_budget"},
            "future": {"start_date": "2026-02-02", "horizon": 3, **future},
        }
    )


def test_future_kwargs_select_the_whole_future_window():
    """AnalyzerFullFunnel supports only the whole future window, never a sub-window."""
    facade = _future_facade({"M1": object()})
    kwargs = facade._future_kwargs(_future_config(), MagicMock(), False)
    assert kwargs["start_date"] == "2026-02-02"
    assert kwargs["end_date"] == "2026-02-16"


def test_future_assumptions_carry_full_funnel_only_on_full_funnel_models():
    config = _future_config()
    full = _future_facade({"M1": object(), "M2": object()})
    assumptions = full._future_kwargs(config, MagicMock(), False)["_assumptions"]
    assert assumptions["full_funnel"] == {
        "mediators": ["M1", "M2"],
        "mediator_treatment": "predicted_from_planned_spend",
    }
    single = _future_facade({})
    assumptions = single._future_kwargs(config, MagicMock(), False)["_assumptions"]
    assert "full_funnel" not in assumptions
    assert set(assumptions) == {
        "budget",
        "budget_source",
        "reference_mode",
        "excluded_channels",
    }


def test_full_funnel_sizing_multiplier_is_applied_only_to_full_funnel():
    from types import SimpleNamespace

    from google_meridian_mcp_server.execution.routing import model_size_features

    def interrogator(mediators):
        return SimpleNamespace(
            is_full_funnel=bool(mediators),
            mediator_names=list(mediators),
            get_data_inputs=lambda: {"media": ["A", "B"], "rf_media": ["C"]},
            _mmm=SimpleNamespace(
                inference_data=SimpleNamespace(
                    posterior=SimpleNamespace(sizes={"chain": 2, "draw": 10})
                )
            ),
            geo_names=lambda: ["g1", "g2"],
            get_time_values=lambda: ["t"] * 5,
        )

    single = model_size_features(interrogator({}))
    assert single == {
        "n_geos": 2,
        "n_time_units": 5,
        "n_channels": 3,
        "n_posterior_samples": 20,
    }
    full = model_size_features(interrogator({"M1": 1, "M2": 2}))
    assert full == {**single, "n_models": 3}

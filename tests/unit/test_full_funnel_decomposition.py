"""Decomposition arithmetic and relabel reconciliation on synthetic inputs."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from google_meridian_mcp_server.meridian.full_funnel import decomposition as dec

PAID = ["A", "B", "C"]


class _FakeAnalyzer:
    """incremental_outcome -> (chain=1, draw=2, [time=2,] channel) with given means."""

    def __init__(self, paid_means, all_means=None):
        self.paid = np.asarray(paid_means, float)
        self.all = None if all_means is None else np.asarray(all_means, float)
        self.calls: list[dict] = []

    def incremental_outcome(self, **kw):
        self.calls.append(kw)
        base = self.all if kw["include_non_paid_channels"] else self.paid
        if not kw["aggregate_times"]:
            base = np.stack([base * 0.25, base * 0.75])  # two periods
        return np.stack([base * 0.9, base * 1.1])[None]  # mean over draws == base


def _two_mediator_split(aggregate_times=True, **kw):
    direct = _FakeAnalyzer([100, 50, 20], all_means=[100, 50, 20, 40, 30])
    full = _FakeAnalyzer([130, 60, 20])  # A +30 (M1 25, M2 5), B +10 (M2)
    single = {"M1": _FakeAnalyzer([125, 50, 20]), "M2": _FakeAnalyzer([105, 60, 20])}
    split = dec.compute_funnel_split(
        direct_analyzer=direct,
        full_analyzer=full,
        single_mediator_analyzers=single,
        paid_channels=PAID,
        all_channels=PAID + ["M1", "M2"],
        aggregate_times=aggregate_times,
        **kw,
    )
    return split, direct


def test_split_values_and_additivity():
    split, _ = _two_mediator_split()
    m1, m2 = split.mediators["M1"], split.mediators["M2"]
    assert m1.indirect_by_channel == pytest.approx({"A": 25.0, "B": 0.0, "C": 0.0})
    assert m2.indirect_by_channel == pytest.approx({"A": 5.0, "B": 10.0, "C": 0.0})
    assert (m1.organic_total, m1.built_by_media, m1.rest) == pytest.approx((40, 25, 15))
    assert (m2.organic_total, m2.built_by_media, m2.rest) == pytest.approx((30, 15, 15))
    for c in PAID:
        assert split.total[c] == pytest.approx(
            split.direct[c] + m1.indirect_by_channel[c] + m2.indirect_by_channel[c]
        )
    assert split.indirect == pytest.approx({"A": 30.0, "B": 10.0, "C": 0.0})
    assert split.rest_total == pytest.approx(30.0)


def test_split_forwards_filters_to_meridian():
    _, direct = _two_mediator_split(
        selected_times=["t1"], selected_geos=["g1"], use_kpi=True
    )
    kw = direct.calls[0]
    assert (kw["selected_times"], kw["selected_geos"], kw["use_kpi"]) == (
        ["t1"],
        ["g1"],
        True,
    )
    assert kw["use_posterior"] is True


def test_split_time_varying_values_are_arrays():
    split, _ = _two_mediator_split(aggregate_times=False)
    np.testing.assert_allclose(split.mediators["M1"].rest, [3.75, 11.25])


def test_channel_count_mismatch_is_loud():
    with pytest.raises(ValueError, match="channel"):
        dec.compute_funnel_split(
            direct_analyzer=_FakeAnalyzer([1, 2, 3], all_means=[1, 2]),
            full_analyzer=_FakeAnalyzer([1, 2, 3]),
            single_mediator_analyzers={},
            paid_channels=PAID,
            all_channels=PAID + ["M1"],
        )


def _summary_ds(times=None):
    """Mimics summary_metrics(include_non_paid_channels=True): FF paid + O_m rows."""
    channels = ["A", "B", "C", "M1", "M2", dec.ALL_CHANNELS]
    io = {"A": 130.0, "B": 60.0, "C": 20.0, "M1": 40.0, "M2": 30.0}
    io[dec.ALL_CHANNELS] = sum(io.values())  # 280
    total = 1000.0
    metrics, dists = ["mean", "median", "ci_lo", "ci_hi"], ["prior", "posterior"]
    base = np.array([[[io[c]] * 2 for _ in metrics] for c in channels])
    dims = ("channel", "metric", "distribution")
    coords = {"channel": channels, "metric": metrics, "distribution": dists}
    if times is not None:
        base = np.stack([base * w for w in times])
        dims = ("time", *dims)
        coords["time"] = [f"t{i}" for i in range(len(times))]
        total_arr = np.array([total * w for w in times])[:, None, None, None]
    else:
        total_arr = total
    return xr.Dataset(
        {
            "incremental_outcome": (dims, base),
            "pct_of_contribution": (dims, base / total_arr * 100),
        },
        coords=coords,
    )


def _split_for_ds(times=None):
    def v(x):
        return x if times is None else np.array([x * w for w in times])

    m1 = dec.MediatorSplit("M1", v(40.0), v(25.0), v(15.0), {"A": v(25.0)})
    m2 = dec.MediatorSplit("M2", v(30.0), v(15.0), v(15.0), {"A": v(5.0), "B": v(10.0)})
    return dec.FunnelSplit(
        direct={"A": v(100.0), "B": v(50.0), "C": v(20.0)},
        total={"A": v(130.0), "B": v(60.0), "C": v(20.0)},
        mediators={"M1": m1, "M2": m2},
    )


PM = {"distribution": "posterior", "metric": "mean"}


def _baseline(ds):
    all_io = ds.incremental_outcome.sel(channel=dec.ALL_CHANNELS, **PM)
    all_pct = ds.pct_of_contribution.sel(channel=dec.ALL_CHANNELS, **PM)
    return all_io / (all_pct / 100) - all_io


def test_relabel_rows_and_percentages_reconcile():
    labels = {n: dec.rest_label(n) for n in ("M1", "M2")}
    out = dec.relabel_mediator_rows(_summary_ds(), _split_for_ds(), labels)
    io = out.incremental_outcome.sel(**PM)
    pct = out.pct_of_contribution.sel(**PM)
    assert "M1" not in out.channel.values
    assert float(io.sel(channel=labels["M1"])) == pytest.approx(15.0)
    assert float(pct.sel(channel=labels["M1"])) == pytest.approx(1.5)
    assert float(io.sel(channel=dec.ALL_CHANNELS)) == pytest.approx(240.0)  # 280-40
    rows = io.where(io.channel != dec.ALL_CHANNELS, drop=True)
    assert float(rows.sum()) == pytest.approx(float(io.sel(channel=dec.ALL_CHANNELS)))
    prow = pct.where(pct.channel != dec.ALL_CHANNELS, drop=True)
    assert float(prow.sum()) == pytest.approx(float(pct.sel(channel=dec.ALL_CHANNELS)))
    # Meridian's waterfall: total = all/pct stays 1000, so baseline = 1000 - 240.
    assert float(_baseline(out)) == pytest.approx(760.0)


def test_relabel_nulls_derived_and_prior_cells_only():
    labels = {"M1": dec.rest_label("M1"), "M2": dec.rest_label("M2")}
    out = dec.relabel_mediator_rows(_summary_ds(), _split_for_ds(), labels)
    for ch in (labels["M1"], dec.ALL_CHANNELS):
        for met in ("median", "ci_lo", "ci_hi"):
            cell = out.incremental_outcome.sel(
                channel=ch, distribution="posterior", metric=met
            )
            assert np.isnan(float(cell))
        assert np.isnan(
            float(
                out.incremental_outcome.sel(
                    channel=ch, distribution="prior", metric="mean"
                )
            )
        )
    untouched = out.incremental_outcome.sel(
        channel="A", distribution="prior", metric="mean"
    )
    assert float(untouched) == pytest.approx(130.0)


def test_relabel_does_not_mutate_input():
    ds = _summary_ds()
    dec.relabel_mediator_rows(ds, _split_for_ds(), {"M1": "x", "M2": "y"})
    assert "M1" in ds.channel.values
    assert float(ds.incremental_outcome.sel(channel="M1", **PM)) == 40.0


def test_relabel_time_varying_reconciles_per_period():
    weights = [0.25, 0.75]
    out = dec.relabel_mediator_rows(
        _summary_ds(weights), _split_for_ds(weights), {"M1": "x", "M2": "y"}
    )
    np.testing.assert_allclose(_baseline(out).values, [190.0, 570.0])


def test_zero_organic_total_does_not_divide_by_zero():
    split = _split_for_ds()
    zero = dec.MediatorSplit("M1", 0.0, 0.0, 0.0, {"A": 0.0})
    split = dec.FunnelSplit(
        split.direct, split.total, {"M1": zero, "M2": split.mediators["M2"]}
    )
    ds = _summary_ds()
    ds["incremental_outcome"].loc[{"channel": "M1"}] = 0.0
    out = dec.relabel_mediator_rows(ds, split, {"M1": "x", "M2": "y"})
    assert float(out.incremental_outcome.sel(channel="x", **PM)) == 0.0


def test_negative_rest_is_passed_through_not_clamped():
    split = _split_for_ds()
    neg = dec.MediatorSplit("M1", 40.0, 55.0, -15.0, {"A": 55.0})
    split = dec.FunnelSplit(
        split.direct, split.total, {"M1": neg, "M2": split.mediators["M2"]}
    )
    out = dec.relabel_mediator_rows(_summary_ds(), split, {"M1": "x", "M2": "y"})
    assert float(out.incremental_outcome.sel(channel="x", **PM)) == pytest.approx(-15.0)


def _baseline_summary_ds(times=None):
    metrics, dists = ["mean", "median", "ci_lo", "ci_hi"], ["prior", "posterior"]
    shape = (1, len(metrics), 2)
    bl = np.full(shape, 790.0)
    pct = np.full(shape, 79.0)
    dims = ("channel", "metric", "distribution")
    coords = {"channel": ["baseline"], "metric": metrics, "distribution": dists}
    if times is not None:
        bl = np.stack([bl * w for w in times])
        pct = np.stack([pct for _ in times])
        dims = ("time", *dims)
        coords["time"] = [f"t{i}" for i in range(len(times))]
    return xr.Dataset(
        {"baseline_outcome": (dims, bl), "pct_of_contribution": (dims, pct)},
        coords=coords,
    )


def test_adjust_baseline_summary_subtracts_rest_and_rescales_pct():
    out = dec.adjust_baseline_summary(_baseline_summary_ds(), 30.0)
    mean = out.baseline_outcome.sel(channel="baseline", **PM)
    assert float(mean) == pytest.approx(760.0)
    pct = out.pct_of_contribution.sel(channel="baseline", **PM)
    assert float(pct) == pytest.approx(79.0 * 760.0 / 790.0)
    for met in ("median", "ci_lo", "ci_hi"):
        cell = out.baseline_outcome.sel(
            channel="baseline", distribution="posterior", metric=met
        )
        assert np.isnan(float(cell))


def test_adjust_baseline_summary_per_period():
    out = dec.adjust_baseline_summary(
        _baseline_summary_ds([0.25, 0.75]), np.array([10.0, 20.0])
    )
    np.testing.assert_allclose(
        out.baseline_outcome.sel(channel="baseline", **PM).values, [187.5, 572.5]
    )


def test_adjust_model_fit_baseline():
    df = pd.DataFrame(
        {
            "time": ["t0", "t1"],
            "expected": [10.0, 20.0],
            "baseline": [5.0, 8.0],
            "baseline_ci_lo": [4.0, 7.0],
            "baseline_ci_hi": [6.0, 9.0],
        }
    )
    out = dec.adjust_model_fit_baseline(df, np.array([1.0, 2.0]))
    assert out["baseline"].tolist() == [4.0, 6.0]
    assert out["baseline_ci_lo"].isna().all() and out["baseline_ci_hi"].isna().all()
    assert out["expected"].tolist() == [10.0, 20.0]
    assert df["baseline"].tolist() == [5.0, 8.0]  # input not mutated
    with pytest.raises(ValueError, match="periods"):
        dec.adjust_model_fit_baseline(df, np.array([1.0]))

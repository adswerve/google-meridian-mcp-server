"""Full-funnel decomposition: direct / indirect / brand-equity rest.

Every input is a Meridian posterior (Analyzer.incremental_outcome). The only arithmetic
here is subtraction of posterior means (mean of a difference == difference of means):

    indirect[m][c] = FF_m(c) - D(c)      FF_m: full-funnel analyzer over mediator m only
    rest[m]        = O_m - sum_c indirect[m][c]

Valid because stage 2 models each mediator with saturation "none" (linear -> additive).
Derived values carry no credible interval; their median/CI/prior cells are set to NaN.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
import pandas as pd
import xarray as xr

ALL_CHANNELS = "All Channels"  # meridian.constants.ALL_CHANNELS
Value = float | np.ndarray
_DERIVED_METRICS = ("median", "ci_lo", "ci_hi")
_POSTERIOR_MEAN = {"distribution": "posterior", "metric": "mean"}


def rest_label(name: str) -> str:
    return f"{name} (brand equity, rest)"


@dataclasses.dataclass(frozen=True)
class MediatorSplit:
    name: str
    organic_total: Value
    built_by_media: Value
    rest: Value
    indirect_by_channel: dict[str, Value]


@dataclasses.dataclass(frozen=True)
class FunnelSplit:
    direct: dict[str, Value]
    total: dict[str, Value]
    mediators: dict[str, MediatorSplit]

    @property
    def indirect(self) -> dict[str, Value]:
        return {
            c: _as_value(np.asarray(self.total[c]) - np.asarray(self.direct[c]))
            for c in self.direct
        }

    @property
    def rest_total(self) -> Value:
        return _as_value(
            sum((np.asarray(m.rest, dtype=float) for m in self.mediators.values()), 0.0)
        )


def _as_value(x: Any) -> Value:
    arr = np.asarray(x, dtype=float)
    return float(arr) if arr.ndim == 0 else arr


def _posterior_mean_by_channel(
    analyzer: Any, channels: Sequence[str], **kwargs: Any
) -> dict[str, Value]:
    # (chain, draw, [time,] channel) -> posterior mean per channel.
    arr = np.asarray(analyzer.incremental_outcome(use_posterior=True, **kwargs), float)
    if arr.shape[-1] != len(channels):
        raise ValueError(
            f"incremental_outcome returned {arr.shape[-1]} channel columns, "
            f"expected {len(channels)} ({list(channels)})"
        )
    means = arr.mean(axis=(0, 1))
    return {c: _as_value(means[..., i]) for i, c in enumerate(channels)}


def compute_funnel_split(
    *,
    direct_analyzer: Any,
    full_analyzer: Any,
    single_mediator_analyzers: Mapping[str, Any],
    paid_channels: Sequence[str],
    all_channels: Sequence[str],
    selected_times: Sequence[str] | None = None,
    selected_geos: Sequence[str] | None = None,
    use_kpi: bool = False,
    aggregate_times: bool = True,
) -> FunnelSplit:
    common = {
        "selected_times": list(selected_times) if selected_times else None,
        "selected_geos": list(selected_geos) if selected_geos else None,
        "use_kpi": use_kpi,
        "aggregate_times": aggregate_times,
    }
    direct_all = _posterior_mean_by_channel(
        direct_analyzer, all_channels, include_non_paid_channels=True, **common
    )
    direct = {c: direct_all[c] for c in paid_channels}
    total = _posterior_mean_by_channel(
        full_analyzer, paid_channels, include_non_paid_channels=False, **common
    )
    mediators: dict[str, MediatorSplit] = {}
    for name in sorted(single_mediator_analyzers):
        ff_m = _posterior_mean_by_channel(
            single_mediator_analyzers[name],
            paid_channels,
            include_non_paid_channels=False,
            **common,
        )
        indirect = {
            c: _as_value(np.asarray(ff_m[c]) - np.asarray(direct[c]))
            for c in paid_channels
        }
        built = _as_value(sum((np.asarray(v) for v in indirect.values()), 0.0))
        organic = direct_all[name]
        mediators[name] = MediatorSplit(
            name=name,
            organic_total=organic,
            built_by_media=built,
            rest=_as_value(np.asarray(organic) - np.asarray(built)),
            indirect_by_channel=indirect,
        )
    return FunnelSplit(direct=direct, total=total, mediators=mediators)


def _null_derived(out: xr.Dataset, var: str, channels: Sequence[str]) -> None:
    for ch in channels:
        for met in _DERIVED_METRICS:
            out[var].loc[
                {"channel": ch, "distribution": "posterior", "metric": met}
            ] = np.nan
        if "prior" in out.distribution.values:
            out[var].loc[{"channel": ch, "distribution": "prior"}] = np.nan


def relabel_mediator_rows(
    ds: xr.Dataset, split: FunnelSplit, labels: Mapping[str, str]
) -> xr.Dataset:
    """Assign each mediator row its rest and lower All Channels by what media built.

    Meridian's contribution waterfall derives the baseline from the All Channels row
    (total = all / pct; baseline = total - all), so with total unchanged, paid (full
    funnel) + rest + other non-paid + baseline == the full-funnel expected outcome.
    Values are ASSIGNED (not scaled) so a zero organic total cannot divide by zero.
    """
    out = ds.copy(deep=True)
    io = out["incremental_outcome"]
    all_io = np.asarray(io.sel(channel=ALL_CHANNELS, **_POSTERIOR_MEAN), dtype=float)
    all_pct = np.asarray(
        out["pct_of_contribution"].sel(channel=ALL_CHANNELS, **_POSTERIOR_MEAN),
        dtype=float,
    )
    with np.errstate(divide="ignore", invalid="ignore"):
        total = all_io / (all_pct / 100.0)
    channels = [str(c) for c in out.channel.values]
    built_sum = np.zeros_like(all_io)
    relabelled: list[str] = []
    for name, med in split.mediators.items():
        if name not in channels:
            continue
        rest = np.asarray(med.rest, dtype=float)
        out["incremental_outcome"].loc[{"channel": name, **_POSTERIOR_MEAN}] = rest
        with np.errstate(divide="ignore", invalid="ignore"):
            out["pct_of_contribution"].loc[{"channel": name, **_POSTERIOR_MEAN}] = (
                rest / total * 100.0
            )
        built_sum = built_sum + np.asarray(med.built_by_media, dtype=float)
        relabelled.append(name)
    new_all = all_io - built_sum
    out["incremental_outcome"].loc[{"channel": ALL_CHANNELS, **_POSTERIOR_MEAN}] = (
        new_all
    )
    with np.errstate(divide="ignore", invalid="ignore"):
        out["pct_of_contribution"].loc[{"channel": ALL_CHANNELS, **_POSTERIOR_MEAN}] = (
            new_all / total * 100.0
        )
    for var in ("incremental_outcome", "pct_of_contribution"):
        _null_derived(out, var, [*relabelled, ALL_CHANNELS])
    return out.assign_coords(channel=[labels.get(c, c) for c in channels])


def adjust_baseline_summary(ds: xr.Dataset, rest_total: Value) -> xr.Dataset:
    """Lower baseline_summary_metrics' posterior-mean baseline by sum(rest).

    Google's zero-media counterfactual keeps each mediator at stage 1's no-media level,
    so its baseline still contains the brand-equity rest the contribution table lists as
    its own row. Subtracting it gives one baseline everywhere.
    """
    out = ds.copy(deep=True)
    old = out["baseline_outcome"].sel(**_POSTERIOR_MEAN)
    rest = np.asarray(rest_total, dtype=float)
    rest_da = (
        xr.DataArray(rest, dims=["time"], coords={"time": old["time"]})
        if rest.ndim == 1
        else float(rest)
    )
    new = old - rest_da
    old_pct = out["pct_of_contribution"].sel(**_POSTERIOR_MEAN)
    with np.errstate(divide="ignore", invalid="ignore"):
        new_pct = old_pct * (new / old)
    out["baseline_outcome"].loc[_POSTERIOR_MEAN] = new.transpose(*old.dims)
    out["pct_of_contribution"].loc[_POSTERIOR_MEAN] = new_pct.transpose(*old_pct.dims)
    for var in ("baseline_outcome", "pct_of_contribution"):
        for met in _DERIVED_METRICS:
            out[var].loc[{"distribution": "posterior", "metric": met}] = np.nan
    return out


def adjust_model_fit_baseline(
    df: pd.DataFrame, rest_by_time: np.ndarray
) -> pd.DataFrame:
    """Per-period model-fit baseline minus per-period sum(rest); baseline CIs -> NaN."""
    rest = np.asarray(rest_by_time, dtype=float).reshape(-1)
    if len(rest) != len(df):
        raise ValueError(
            f"rest has {len(rest)} periods but model fit has {len(df)} rows"
        )
    out = df.copy()
    out["baseline"] = out["baseline"].to_numpy(dtype=float) - rest
    out["baseline_ci_lo"] = np.nan
    out["baseline_ci_hi"] = np.nan
    return out

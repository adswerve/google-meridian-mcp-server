"""Grouped Meridian analysis facade — wraps Analyzer and MediaSummary calls."""

from __future__ import annotations

import functools
from collections.abc import Sequence
from typing import Any

import numpy as np
import pandas as pd
import xarray as xr

from google_meridian_mcp_server.domain.filters import AnalysisFilters
from google_meridian_mcp_server.meridian.dataset_mapper import (
    dataset_to_records,
    filter_records,
)
from google_meridian_mcp_server.meridian.interrogator import MeridianInterrogator


class AnalyzerFacade(MeridianInterrogator):
    """Provides a simplified interface over Meridian's Analyzer and MediaSummary."""

    def __init__(self, mmm: Any) -> None:
        super().__init__(mmm)
        self._media_summary_cache: dict[tuple, Any] = {}

    def _expand_selected_times(self, filters: AnalysisFilters) -> list[str] | None:
        if filters.start_date is None and filters.end_date is None:
            return None
        return self._mmm.expand_selected_time_dims(
            filters.start_date.isoformat() if filters.start_date else None,
            filters.end_date.isoformat() if filters.end_date else None,
        )

    @staticmethod
    def _selected_geos(filters: AnalysisFilters) -> list[str] | None:
        return list(filters.geos) or None

    @staticmethod
    def _filter_channels(data: Any, channels: Sequence[str]) -> Any:
        if not channels:
            return data
        if isinstance(data, pd.DataFrame):
            if "channel" not in data.columns:
                return data
            return data[data["channel"].isin(channels)].copy()
        if hasattr(data, "coords") and "channel" in data.coords:
            available = {
                str(value) for value in np.asarray(data.coords["channel"]).tolist()
            }
            selected = [channel for channel in channels if channel in available]
            if not selected:
                return data.isel(channel=slice(0, 0))
            return data.sel(channel=selected)
        return data

    @staticmethod
    def _posterior_only(data: Any) -> Any:
        if isinstance(data, pd.DataFrame):
            if "distribution" not in data.columns:
                return data
            filtered = data.copy()
            if (filtered["distribution"] == "posterior").any():
                filtered = filtered[filtered["distribution"] == "posterior"].copy()
            return filtered.drop(columns=["distribution"])
        if hasattr(data, "coords") and "distribution" in data.coords:
            return data.sel(distribution="posterior", drop=True)
        return data

    def _records_from_output(
        self,
        data: Any,
        *,
        channels: Sequence[str] = (),
        var_name: str | None = None,
    ) -> list[dict]:
        filtered = self._filter_channels(data, channels)
        posterior_only = self._posterior_only(filtered)
        return dataset_to_records(posterior_only, var_name)

    def _get_media_summary(
        self,
        filters: AnalysisFilters | None = None,
        confidence_level: float = 0.9,
    ):
        filters = filters or AnalysisFilters()
        use_kpi = self.resolve_use_kpi(filters)
        selected_geos = tuple(filters.geos)
        selected_times = tuple(self._expand_selected_times(filters) or ())
        key = (use_kpi, confidence_level, selected_geos, selected_times)
        if key not in self._media_summary_cache:
            from meridian.analysis import visualizer as visualizer_mod

            class FilteredMediaSummary(visualizer_mod.MediaSummary):
                def __init__(
                    self, *args, selected_geos: list[str] | None = None, **kwargs
                ):
                    super().__init__(*args, **kwargs)
                    self._selected_geos = selected_geos

                @functools.lru_cache(maxsize=128)
                def get_paid_summary_metrics(self, aggregate_times: bool = True):
                    return self._analyzer.summary_metrics(
                        selected_geos=self._selected_geos,
                        selected_times=self._selected_times,
                        marginal_roi_by_reach=self._marginal_roi_by_reach,
                        use_kpi=self._use_kpi,
                        confidence_level=self._confidence_level,
                        include_non_paid_channels=False,
                        aggregate_times=aggregate_times,
                    )

                @functools.lru_cache(maxsize=128)
                def get_all_summary_metrics(self, aggregate_times: bool = True):
                    return self._analyzer.summary_metrics(
                        selected_geos=self._selected_geos,
                        selected_times=self._selected_times,
                        use_kpi=self._use_kpi,
                        confidence_level=self._confidence_level,
                        include_non_paid_channels=True,
                        non_media_baseline_values=self._non_media_baseline_values,
                        aggregate_times=aggregate_times,
                    )

            self._media_summary_cache[key] = FilteredMediaSummary(
                self._mmm,
                selected_geos=list(selected_geos) or None,
                selected_times=list(selected_times) or None,
                confidence_level=confidence_level,
                use_kpi=use_kpi,
            )
        return self._media_summary_cache[key]

    # -- Channel summary methods ------------------------------------------------

    def get_paid_summary_metrics(self, filters: AnalysisFilters) -> list[dict]:
        ms = self._get_media_summary(filters)
        ds = ms.get_paid_summary_metrics(
            aggregate_times=filters.aggregate_times,
        )
        return self._records_from_output(ds, channels=filters.channels)

    def get_baseline_summary_metrics(self, filters: AnalysisFilters) -> list[dict]:
        ds = self._get_analyzer().baseline_summary_metrics(
            selected_geos=self._selected_geos(filters),
            selected_times=self._expand_selected_times(filters),
            aggregate_times=filters.aggregate_times,
            use_kpi=self.resolve_use_kpi(filters),
        )
        return self._records_from_output(ds)

    def get_roi(self, filters: AnalysisFilters) -> list[dict]:
        ms = self._get_media_summary(filters)
        ds = ms.get_paid_summary_metrics(aggregate_times=filters.aggregate_times)
        if "roi" in ds.data_vars:
            return self._records_from_output(
                ds["roi"], channels=filters.channels, var_name="roi"
            )
        return []

    def get_cpik(self, filters: AnalysisFilters) -> list[dict]:
        ms = self._get_media_summary(filters)
        ds = ms.get_paid_summary_metrics(aggregate_times=filters.aggregate_times)
        if "cpik" in ds.data_vars:
            return self._records_from_output(
                ds["cpik"], channels=filters.channels, var_name="cpik"
            )
        return []

    def get_marginal_roi(self, filters: AnalysisFilters) -> list[dict]:
        ms = self._get_media_summary(filters)
        ds = ms.get_paid_summary_metrics(aggregate_times=filters.aggregate_times)
        if "mroi" in ds.data_vars:
            return self._records_from_output(
                ds["mroi"],
                channels=filters.channels,
                var_name="marginal_roi",
            )
        return []

    def get_marginal_cpik(self, filters: AnalysisFilters) -> list[dict]:
        ms = self._get_media_summary(filters)
        ds = ms.get_paid_summary_metrics(aggregate_times=filters.aggregate_times)
        if "mroi" in ds.data_vars:
            marginal_cpik = xr.where(ds["mroi"] == 0, np.inf, 1 / ds["mroi"]).rename(
                "marginal_cpik"
            )
            if "metric" in marginal_cpik.coords:
                metric_values = {
                    str(value)
                    for value in np.asarray(marginal_cpik.coords["metric"]).tolist()
                }
                if {"ci_lo", "ci_hi"} <= metric_values:
                    ci_lo = marginal_cpik.sel(metric="ci_lo")
                    ci_hi = marginal_cpik.sel(metric="ci_hi")
                    lower_bound = xr.apply_ufunc(np.minimum, ci_lo, ci_hi)
                    upper_bound = xr.apply_ufunc(np.maximum, ci_lo, ci_hi)
                    marginal_cpik.loc[{"metric": "ci_lo"}] = lower_bound
                    marginal_cpik.loc[{"metric": "ci_hi"}] = upper_bound
            return self._records_from_output(
                marginal_cpik,
                channels=filters.channels,
                var_name="marginal_cpik",
            )
        return []

    # -- Contribution methods ---------------------------------------------------

    def get_contribution_metrics(self, filters: AnalysisFilters) -> list[dict]:
        ms = self._get_media_summary(filters)
        include_non_paid = (
            filters.include_non_paid if filters.include_non_paid is not None else True
        )
        df = ms.contribution_metrics(
            selected_channels=filters.channels or None,
            include_non_paid=include_non_paid,
            aggregate_times=filters.aggregate_times,
        )
        return dataset_to_records(df)

    def get_contribution_metrics_by_time(self, filters: AnalysisFilters) -> list[dict]:
        ms = self._get_media_summary(filters)
        include_non_paid = (
            filters.include_non_paid if filters.include_non_paid is not None else True
        )
        df = ms.contribution_metrics(
            selected_channels=filters.channels or None,
            include_non_paid=include_non_paid,
            aggregate_times=False,
        )
        return dataset_to_records(df)

    # -- Response dynamics methods ----------------------------------------------

    def get_adstock_decay(self, filters: AnalysisFilters) -> list[dict]:
        analyzer = self._get_analyzer()
        df = analyzer.adstock_decay(confidence_level=0.9)
        return self._records_from_output(df, channels=filters.channels)

    def get_alpha_summary(self, filters: AnalysisFilters) -> list[dict]:
        posterior = self._mmm.inference_data.posterior
        input_data = self._mmm.input_data
        rows: list[dict] = []

        alpha_sources = [
            ("alpha_m", "media_channel", "media"),
            ("alpha_rf", "rf_channel", "rf"),
            ("alpha_om", "organic_media_channel", "organic_media"),
            ("alpha_orf", "organic_rf_channel", "organic_rf"),
        ]

        for alpha_attr, coord_attr, ch_type in alpha_sources:
            if not hasattr(posterior, alpha_attr):
                continue

            alpha_vals = getattr(posterior, alpha_attr).values
            flat = alpha_vals.reshape(-1, alpha_vals.shape[-1])

            channels = getattr(input_data, coord_attr, None)
            if channels is None:
                continue
            channel_names = list(np.asarray(channels))

            for i, ch_name in enumerate(channel_names):
                if filters.channels and ch_name not in filters.channels:
                    continue
                samples = flat[:, i]
                rows.append(
                    {
                        "channel": str(ch_name),
                        "channel_type": ch_type,
                        "alpha_mean": float(np.mean(samples)),
                        "alpha_median": float(np.median(samples)),
                        "alpha_std": float(np.std(samples)),
                    }
                )

        return rows

    # -- Response curves methods ------------------------------------------------

    def get_carryover(
        self, data_input: str
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        decay = self._get_analyzer().adstock_decay()
        decay_df = (
            decay
            if isinstance(decay, pd.DataFrame)
            else decay.to_dataframe().reset_index()
        )
        filtered = decay_df[
            (decay_df["channel"] == data_input)
            & (decay_df["distribution"] == "posterior")
            & (decay_df["is_int_time_unit"])
        ]
        return (
            filtered["mean"].to_numpy(),
            filtered["ci_lo"].to_numpy(),
            filtered["ci_hi"].to_numpy(),
        )

    def _get_spend_column(self, data_input: str) -> str:
        """Return the spend column name for a given channel."""
        rf_channels = self.get_data_inputs().get("rf_media", [])
        if data_input in rf_channels:
            return f"{data_input}_rf_spend"
        return f"{data_input}_spend"

    def apply_saturation(
        self,
        data_input: str,
        spend: Sequence[float],
        geos: list[str] | None = None,
        dt_start: str | None = None,
        dt_end: str | None = None,
        use_kpi: bool = False,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        spend_values = np.asarray(spend, dtype=float)
        if spend_values.size == 0:
            raise ValueError("At least one spend value is required.")

        data = self.get_data(agg_geos=True, geos=geos, dt_start=dt_start, dt_end=dt_end)
        if data.empty:
            raise ValueError(
                "No model data is available for the requested saturation slice."
            )

        spend_column = self._get_spend_column(data_input)
        if spend_column not in data.columns:
            raise ValueError(f"Spend column '{spend_column}' is not available.")

        time_units = len(data.index)
        if time_units <= 0:
            raise ValueError(
                "At least one time unit is required for saturation analysis."
            )

        mean_spend = float(data[spend_column].sum()) / time_units
        if mean_spend <= 0:
            raise ValueError(
                f"Spend column '{spend_column}' must have a positive mean spend."
            )

        spend_multipliers = np.linspace(
            0.0, np.ceil(float(spend_values.max()) / mean_spend), 50
        )
        selected_times = (
            self._mmm.expand_selected_time_dims(dt_start, dt_end)
            if dt_start is not None or dt_end is not None
            else None
        )

        response_curves = self._get_analyzer().response_curves(
            spend_multipliers=list(spend_multipliers),
            selected_geos=geos,
            selected_times=selected_times,
            use_kpi=use_kpi,
        )
        response_df = response_curves.to_dataframe().reset_index()
        response_df = response_df[response_df["channel"] == data_input].copy()
        response_df[["spend", "incremental_outcome"]] = response_df[
            ["spend", "incremental_outcome"]
        ].apply(lambda column: column / time_units)

        mean_df = response_df[response_df["metric"] == "mean"].sort_values("spend")
        ci_lo_df = response_df[response_df["metric"] == "ci_lo"].sort_values("spend")
        ci_hi_df = response_df[response_df["metric"] == "ci_hi"].sort_values("spend")

        return (
            self._interpolate_with_extrapolation(
                spend_values, mean_df["spend"], mean_df["incremental_outcome"]
            ),
            self._interpolate_with_extrapolation(
                spend_values, ci_lo_df["spend"], ci_lo_df["incremental_outcome"]
            ),
            self._interpolate_with_extrapolation(
                spend_values, ci_hi_df["spend"], ci_hi_df["incremental_outcome"]
            ),
        )

    def resolve_base_spend(self, channel: str, filters: AnalysisFilters) -> float:
        """Historical average spend per time unit for ``channel`` over the slice."""
        data = self.get_data(
            agg_geos=True,
            geos=self._selected_geos(filters),
            dt_start=filters.start_date.isoformat() if filters.start_date else None,
            dt_end=filters.end_date.isoformat() if filters.end_date else None,
        )
        spend_column = self._get_spend_column(channel)
        if data.empty or spend_column not in data.columns:
            raise ValueError(f"No spend data is available for channel '{channel}'.")
        time_units = len(data.index)
        return float(data[spend_column].sum()) / time_units

    def spend_response(
        self, channel: str, spend_points: Sequence[float], filters: AnalysisFilters
    ) -> list[dict]:
        """Outcome (mean/ci_lo/ci_hi) at each spend point via ``apply_saturation``."""
        mean, ci_lo, ci_hi = self.apply_saturation(
            channel,
            list(spend_points),
            geos=self._selected_geos(filters),
            dt_start=filters.start_date.isoformat() if filters.start_date else None,
            dt_end=filters.end_date.isoformat() if filters.end_date else None,
            use_kpi=self.resolve_use_kpi(filters),
        )
        return [
            {
                "mean": float(mean[i]),
                "ci_lo": float(ci_lo[i]),
                "ci_hi": float(ci_hi[i]),
            }
            for i in range(len(spend_points))
        ]

    def get_response_curves(self, filters: AnalysisFilters) -> list[dict]:
        ds = self._get_analyzer().response_curves(
            selected_geos=self._selected_geos(filters),
            selected_times=self._expand_selected_times(filters),
            use_kpi=self.resolve_use_kpi(filters),
        )
        return self._records_from_output(ds, channels=filters.channels)

    def get_response_curve_summary(self, filters: AnalysisFilters) -> list[dict]:
        ds = self._get_analyzer().response_curves(
            selected_geos=self._selected_geos(filters),
            selected_times=self._expand_selected_times(filters),
            use_kpi=self.resolve_use_kpi(filters),
        )
        ds = self._filter_channels(ds, filters.channels)
        df = (
            ds[["spend", "incremental_outcome"]]
            .to_dataframe()
            .reset_index()
            .pivot(
                index=["channel", "spend", "spend_multiplier"],
                columns="metric",
                values="incremental_outcome",
            )
            .reset_index()
        )
        df.columns.name = None
        return dataset_to_records(df)

    # -- Reach & frequency methods ---------------------------------------------

    def get_reach_frequency(self, filters: AnalysisFilters) -> list[dict]:
        ds = self._get_analyzer().optimal_freq(
            selected_geos=self._selected_geos(filters),
            selected_times=self._expand_selected_times(filters),
            use_kpi=self.resolve_use_kpi(filters),
            confidence_level=0.9,
        )
        roi = ds["roi"].to_dataframe(name="roi").reset_index()
        roi_wide = (
            roi.pivot(index=["rf_channel", "frequency"], columns="metric", values="roi")
            .reset_index()
            .rename(columns={"mean": "roi"})
        )
        optimal = (
            ds["optimal_frequency"].to_dataframe(name="optimal_frequency").reset_index()
        )
        if "metric" in optimal.columns:
            optimal = optimal[optimal["metric"] == "mean"].drop(columns="metric")
        merged = roi_wide.merge(optimal, on="rf_channel").rename(
            columns={"rf_channel": "channel"}
        )
        ordered = ["channel", "frequency", "roi", "ci_lo", "ci_hi", "optimal_frequency"]
        merged = merged.reindex(columns=[c for c in ordered if c in merged.columns])
        merged.columns.name = None
        if filters.channels:
            merged = merged[merged["channel"].isin(filters.channels)].copy()
        return dataset_to_records(merged)

    # -- Model fit methods ------------------------------------------------------

    def get_model_fit(self, filters: AnalysisFilters) -> list[dict]:
        ds = self._get_analyzer().expected_vs_actual_data(
            aggregate_geos=True,
            aggregate_times=False,
            use_kpi=self.resolve_use_kpi(filters),
            confidence_level=0.9,
        )

        def _wide(var_name: str) -> pd.DataFrame:
            frame = ds[var_name].to_dataframe(name=var_name).reset_index()
            pivoted = frame.pivot(index="time", columns="metric", values=var_name)
            return pivoted.rename(
                columns={
                    "mean": var_name,
                    "ci_lo": f"{var_name}_ci_lo",
                    "ci_hi": f"{var_name}_ci_hi",
                }
            )

        expected = _wide("expected")
        baseline = _wide("baseline")
        actual = ds["actual"].to_dataframe(name="actual").reset_index()
        if "metric" in actual.columns:
            actual = actual[actual["metric"] == "mean"].drop(columns="metric")

        merged = expected.join(baseline).reset_index().merge(actual, on="time")
        merged["residual"] = merged["actual"] - merged["expected"]
        ordered = [
            "time",
            "expected",
            "expected_ci_lo",
            "expected_ci_hi",
            "actual",
            "baseline",
            "baseline_ci_lo",
            "baseline_ci_hi",
            "residual",
        ]
        merged = merged.reindex(columns=[c for c in ordered if c in merged.columns])
        records = dataset_to_records(merged)
        return filter_records(
            records,
            start_date=filters.start_date,
            end_date=filters.end_date,
        )

    @staticmethod
    def _interpolate_with_extrapolation(
        x: np.ndarray, xp: pd.Series, fp: pd.Series
    ) -> np.ndarray:
        xp_values = xp.to_numpy(dtype=float)
        fp_values = fp.to_numpy(dtype=float)
        if xp_values.size == 0 or fp_values.size == 0:
            raise ValueError(
                "Response-curve interpolation requires at least one point."
            )
        if xp_values.size == 1:
            return np.full(x.shape, fp_values[0], dtype=float)

        order = np.argsort(xp_values)
        xp_values = xp_values[order]
        fp_values = fp_values[order]

        interpolated = np.interp(x, xp_values, fp_values)
        left_mask = x < xp_values[0]
        right_mask = x > xp_values[-1]

        left_slope = (fp_values[1] - fp_values[0]) / (xp_values[1] - xp_values[0])
        right_slope = (fp_values[-1] - fp_values[-2]) / (xp_values[-1] - xp_values[-2])

        if left_mask.any():
            interpolated[left_mask] = (
                fp_values[0] + (x[left_mask] - xp_values[0]) * left_slope
            )
        if right_mask.any():
            interpolated[right_mask] = (
                fp_values[-1] + (x[right_mask] - xp_values[-1]) * right_slope
            )

        return interpolated

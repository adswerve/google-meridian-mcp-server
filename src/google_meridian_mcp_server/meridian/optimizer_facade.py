"""Facade over Meridian BudgetOptimizer: run optimization, build structured result."""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from google_meridian_mcp_server.domain.optimization import (
    BaseOptimizationConfig,
    OptimizationConfig,
    to_optimize_kwargs,
)
from google_meridian_mcp_server.meridian.interrogator import MeridianInterrogator


def _best_effort(fn):
    try:
        return fn()
    except Exception:  # noqa: BLE001 - response curves are best-effort enrichment
        return None


def _sig6(value: float | None) -> float | None:
    """Round to 6 significant figures, strict-JSON-safe.

    Returns None for None, NaN, or infinite inputs so the result is always
    JSON-serialisable without relying on non-standard NaN/Inf extensions.
    """
    if value is None:
        return None
    f = float(value)
    if not math.isfinite(f):
        return None
    return float(f"{f:.6g}")


class OptimizerFacade(MeridianInterrogator):
    """Runs BudgetOptimizer and shapes its OptimizationResults into JSON."""

    def channel_order(self) -> list[str]:
        inputs = self.get_data_inputs()
        return list(inputs["media"]) + list(inputs["rf_media"])

    def resolve_use_kpi(self, config: BaseOptimizationConfig) -> bool:
        if config.use_kpi is not None:
            return config.use_kpi
        return not self.has_revenue_per_kpi()

    def execute(self, config) -> dict[str, Any]:
        if getattr(config, "kind", "historical") == "future":
            return self.run_future(config)
        return self.run(config)

    def _run(self, config, build_kwargs) -> dict[str, Any]:
        from meridian.analysis import optimizer as optimizer_mod

        use_kpi = self.resolve_use_kpi(config)
        opt = optimizer_mod.BudgetOptimizer(self._mmm)
        kwargs = build_kwargs(config, opt, use_kpi)
        results = opt.optimize(**kwargs)
        curves = _best_effort(lambda: results.get_response_curves())
        return self.build_result(
            results.nonoptimized_data,
            results.optimized_data,
            use_kpi=use_kpi,
            response_curves=curves,
        )

    def run(self, config: OptimizationConfig) -> dict[str, Any]:
        return self._run(config, self._historical_kwargs)

    def _historical_kwargs(self, config, opt, use_kpi) -> dict[str, Any]:
        return to_optimize_kwargs(
            config, channel_order=self.channel_order(), use_kpi=use_kpi
        )

    def run_future(self, config) -> dict[str, Any]:
        return self._run(config, self._future_kwargs)

    def _future_kwargs(self, config, opt, use_kpi) -> dict[str, Any]:
        from google_meridian_mcp_server.meridian import future_data as fd

        f = config.future
        times = self.get_time_values()
        cadence = fd.infer_cadence_days(times)
        time_labels = fd.future_time_labels(f.start_date, f.horizon, cadence)
        if time_labels[0] <= times[-1][:10]:
            raise ValueError(
                "future start_date must be after the last training period."
            )
        window = fd.reference_indices(
            f.reference.mode, f.horizon, f.start_date, times, cadence
        )

        media_order = self.get_data_inputs()["media"]
        rf_order = self.get_data_inputs()["rf_media"]
        fd.validate_channel_keys(f.cost_multipliers, media_order + rf_order)
        average = f.reference.mode == "full_history_average"

        seeded_total = 0.0
        tensor_kwargs: dict[str, Any] = {"time": time_labels}
        if media_order:
            tensor_kwargs["cpmu"] = fd.apply_cost_multipliers(
                self._seed_cpmu(window), f.cost_multipliers, media_order
            )
            media_spend = self._seed_spend_flighting(
                "media_spend", window, f.horizon, average
            )
            tensor_kwargs["media_spend"] = media_spend
            seeded_total += float(np.asarray(media_spend).sum())
        if rf_order:
            tensor_kwargs["cprf"] = fd.apply_cost_multipliers(
                self._seed_cprf(window), f.cost_multipliers, rf_order
            )
            rf_spend = self._seed_spend_flighting(
                "rf_spend", window, f.horizon, average
            )
            tensor_kwargs["rf_spend"] = rf_spend
            seeded_total += float(np.asarray(rf_spend).sum())
        if self.has_revenue_per_kpi():
            tensor_kwargs["revenue_per_kpi"] = (
                self._seed_revenue_per_kpi(window, f.horizon, average)
                * f.revenue_per_kpi_multiplier
            )

        new_data = opt.create_optimization_tensors(**tensor_kwargs)

        carried = self._carried_allocation(window)  # {channel: weight}
        pct = fd.normalize_planned_allocation(
            f.planned_allocation, carried, self.channel_order()
        )
        fixed_budget = config.scenario.type == "fixed_budget"
        scenario_budget = getattr(config.scenario, "budget", None)
        # Budget defaults to the SEEDED future flighting total (horizon periods), NOT the
        # raw reference-window total — the two differ for full_history_average.
        budget = fd.resolve_budget(scenario_budget, fixed_budget, seeded_total)

        kwargs = to_optimize_kwargs(
            config, channel_order=self.channel_order(), use_kpi=use_kpi
        )
        kwargs.pop("start_date", None)
        kwargs.pop("end_date", None)
        kwargs.update(
            new_data=new_data,
            start_date=time_labels[0],
            end_date=time_labels[-1],
            pct_of_spend=pct,
            budget=budget,
        )
        return kwargs

    # -- private seed helpers (read self._mmm.input_data as NumPy) ------------

    def _n_times(self) -> int:
        return len(self.get_time_values())

    def _trim_media(self, arr: np.ndarray) -> np.ndarray:
        """Trim a media-family tensor to its final ``n_times`` slice.

        media/reach/frequency are indexed by the longer ``media_time`` axis; only
        the final ``n_times`` periods align to ``input_data.time``.
        """
        return arr[:, -self._n_times() :, :]

    def _spend_np(self, kind: str) -> np.ndarray:
        """Return a spend tensor as ``(n_geos, time, n_channels)`` float array."""
        arr = np.asarray(getattr(self._mmm.input_data, kind).values, dtype=float)
        if arr.ndim < 3:
            raise ValueError("unsupported spend granularity for future optimization")
        return arr

    def _seed_cpmu(self, window: list[int]) -> np.ndarray:
        spend = self._spend_np("media_spend")
        media = self._trim_media(
            np.asarray(self._mmm.input_data.media.values, dtype=float)
        )
        spend_sum = spend[:, window, :].sum(axis=(0, 1))
        media_sum = media[:, window, :].sum(axis=(0, 1))
        return spend_sum / media_sum

    def _seed_cprf(self, window: list[int]) -> np.ndarray:
        spend = self._spend_np("rf_spend")
        reach = self._trim_media(
            np.asarray(self._mmm.input_data.reach.values, dtype=float)
        )
        frequency = self._trim_media(
            np.asarray(self._mmm.input_data.frequency.values, dtype=float)
        )
        impressions = reach * frequency
        spend_sum = spend[:, window, :].sum(axis=(0, 1))
        impressions_sum = impressions[:, window, :].sum(axis=(0, 1))
        return spend_sum / impressions_sum

    def _seed_spend_flighting(
        self, kind: str, window: list[int], horizon: int, average: bool
    ) -> np.ndarray:
        spend = self._spend_np(kind)
        if average:
            per_period = spend.mean(axis=1, keepdims=True)
            return np.repeat(per_period, horizon, axis=1)
        return spend[:, window, :]

    def _seed_revenue_per_kpi(
        self, window: list[int], horizon: int, average: bool
    ) -> np.ndarray:
        rpk = np.asarray(self._mmm.input_data.revenue_per_kpi.values, dtype=float)
        if average:
            per_period = rpk.mean(axis=1, keepdims=True)
            return np.repeat(per_period, horizon, axis=1)
        return rpk[:, window]

    def _carried_allocation(self, window: list[int]) -> dict[str, float]:
        inputs = self.get_data_inputs()
        weights: dict[str, float] = {}
        if inputs["media"]:
            per_channel = self._spend_np("media_spend")[:, window, :].sum(axis=(0, 1))
            for i, channel in enumerate(inputs["media"]):
                weights[channel] = float(per_channel[i])
        if inputs["rf_media"]:
            per_channel = self._spend_np("rf_spend")[:, window, :].sum(axis=(0, 1))
            for i, channel in enumerate(inputs["rf_media"]):
                weights[channel] = float(per_channel[i])
        return weights

    @staticmethod
    def build_result(
        nonopt, opt, *, use_kpi: bool, response_curves=None
    ) -> dict[str, Any]:
        outcome_mode = "kpi" if use_kpi else "revenue"
        result = {
            "outcome_mode": outcome_mode,
            "summary": OptimizerFacade._summary(nonopt, opt, use_kpi),
            "channel_tables": {
                "initial": OptimizerFacade._channel_rows(nonopt, use_kpi),
                "optimized": OptimizerFacade._channel_rows(opt, use_kpi),
            },
            "allocation": OptimizerFacade._allocation(opt),
            "spend_delta": OptimizerFacade._spend_delta(nonopt, opt),
        }
        if response_curves is not None:
            result["response_curves"] = OptimizerFacade._response_curve_rows(
                response_curves
            )
        return result

    @staticmethod
    def _response_curve_rows(curves) -> list[dict[str, Any]]:
        """Flatten get_response_curves() to per-(channel, spend) points (metric=mean)."""
        data = curves
        if "metric" in getattr(data, "dims", {}):
            data = data.sel(metric="mean", drop=True)
        channels = [str(c) for c in data.coords["channel"].values.tolist()]
        rows: list[dict[str, Any]] = []
        for channel in channels:
            sub = data.sel(channel=channel)
            spends = sub["spend"].values.tolist()
            incs = sub["incremental_outcome"].values.tolist()
            for spend, inc in zip(spends, incs):
                rows.append(
                    {
                        "channel": channel,
                        "spend": _sig6(float(spend)),
                        "incremental_outcome": _sig6(float(inc)),
                    }
                )
        return rows

    @staticmethod
    def _efficiency(total_roi: float, use_kpi: bool) -> float | None:
        if not use_kpi:
            return total_roi
        # Zero denominator in KPI mode → no meaningful efficiency; return None
        # (matches the codebase's zero-denominator→null convention).
        if total_roi == 0:
            return None
        return 1.0 / total_roi

    @staticmethod
    def _summary(nonopt, opt, use_kpi: bool) -> dict[str, float]:
        return {
            "non_optimized_budget": _sig6(nonopt.attrs["budget"]),
            "optimized_budget": _sig6(opt.attrs["budget"]),
            "non_optimized_efficiency": _sig6(
                OptimizerFacade._efficiency(float(nonopt.attrs["total_roi"]), use_kpi)
            ),
            "optimized_efficiency": _sig6(
                OptimizerFacade._efficiency(float(opt.attrs["total_roi"]), use_kpi)
            ),
            "non_optimized_incremental_outcome": _sig6(
                nonopt.attrs["total_incremental_outcome"]
            ),
            "optimized_incremental_outcome": _sig6(
                opt.attrs["total_incremental_outcome"]
            ),
        }

    @staticmethod
    def _channel_rows(data, use_kpi: bool) -> list[dict[str, Any]]:
        channels = [str(c) for c in data.coords["channel"].values.tolist()]
        rows: list[dict[str, Any]] = []
        for channel in channels:
            spend = float(data["spend"].sel(channel=channel).sum().values)
            pct = float(data["pct_of_spend"].sel(channel=channel).values) * 100.0
            inc = float(
                data["incremental_outcome"]
                .sel(channel=channel, metric="mean")
                .sum()
                .values
            )
            roi = float(data["roi"].sel(channel=channel, metric="mean").values)
            mroi = float(data["mroi"].sel(channel=channel, metric="mean").values)
            cpik = float(data["cpik"].sel(channel=channel, metric="median").values)
            eff = float(
                data["effectiveness"].sel(channel=channel, metric="mean").values
            )
            rows.append(
                {
                    "channel": channel,
                    "spend": _sig6(spend),
                    "pct_of_spend": _sig6(pct),
                    "incremental_outcome": _sig6(inc),
                    "roi": _sig6(roi),
                    "mroi": _sig6(mroi),
                    "cpik": _sig6(cpik),
                    "effectiveness": _sig6(eff),
                }
            )
        return rows

    @staticmethod
    def _allocation(opt) -> list[dict[str, Any]]:
        channels = [str(c) for c in opt.coords["channel"].values.tolist()]
        return [
            {
                "channel": c,
                "spend": _sig6(float(opt["spend"].sel(channel=c).sum().values)),
            }
            for c in channels
        ]

    @staticmethod
    def _spend_delta(nonopt, opt) -> list[dict[str, Any]]:
        channels = [str(c) for c in opt.coords["channel"].values.tolist()]
        deltas = [
            (
                c,
                float(opt["spend"].sel(channel=c).sum().values)
                - float(nonopt["spend"].sel(channel=c).sum().values),
            )
            for c in channels
        ]
        negative = sorted([d for d in deltas if d[1] < 0], key=lambda d: d[1])
        positive = sorted(
            [d for d in deltas if d[1] >= 0], key=lambda d: d[1], reverse=True
        )
        return [{"channel": c, "spend": _sig6(v)} for c, v in (negative + positive)]

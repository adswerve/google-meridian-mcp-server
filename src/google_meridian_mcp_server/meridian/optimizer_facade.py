"""Facade over Meridian BudgetOptimizer: run optimization, build structured result."""

from __future__ import annotations

import math
from typing import Any

from google_meridian_mcp_server.domain.optimization import (
    OptimizationConfig,
    to_optimize_kwargs,
)
from google_meridian_mcp_server.meridian.interrogator import MeridianInterrogator


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

    def resolve_use_kpi(self, config: OptimizationConfig) -> bool:
        if config.use_kpi is not None:
            return config.use_kpi
        return not self.has_revenue_per_kpi()

    def run(self, config: OptimizationConfig) -> dict[str, Any]:
        from meridian.analysis import optimizer as optimizer_mod

        use_kpi = self.resolve_use_kpi(config)
        kwargs = to_optimize_kwargs(
            config, channel_order=self.channel_order(), use_kpi=use_kpi
        )
        budget_optimizer = optimizer_mod.BudgetOptimizer(self._mmm)
        results = budget_optimizer.optimize(**kwargs)
        try:
            curves = results.get_response_curves()
        except Exception:  # noqa: BLE001 - response curves are best-effort enrichment
            curves = None
        return self.build_result(
            results.nonoptimized_data,
            results.optimized_data,
            use_kpi=use_kpi,
            response_curves=curves,
        )

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

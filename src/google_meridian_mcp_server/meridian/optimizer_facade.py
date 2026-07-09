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

    def _run(
        self, config, build_kwargs, *, enrich_curves: bool = True
    ) -> dict[str, Any]:
        from meridian.analysis import optimizer as optimizer_mod

        use_kpi = self.resolve_use_kpi(config)
        opt = optimizer_mod.BudgetOptimizer(self._mmm)
        kwargs = build_kwargs(config, opt, use_kpi)
        assumptions = kwargs.pop("_assumptions", None)
        results = opt.optimize(**kwargs)
        curves = (
            _best_effort(lambda: results.get_response_curves())
            if enrich_curves
            else None
        )
        return self.build_result(
            results.nonoptimized_data,
            results.optimized_data,
            use_kpi=use_kpi,
            response_curves=curves,
            assumptions=assumptions,
        )

    def run(self, config: OptimizationConfig) -> dict[str, Any]:
        return self._run(config, self._historical_kwargs)

    def _historical_kwargs(self, config, opt, use_kpi) -> dict[str, Any]:
        return to_optimize_kwargs(
            config, channel_order=self.channel_order(), use_kpi=use_kpi
        )

    def run_future(self, config) -> dict[str, Any]:
        return self._run(config, self._future_kwargs, enrich_curves=False)

    def validate_future(self, config) -> None:
        """Pure up-front guards for future optimization: no `optimize()` call.

        Runs the pure submit-time guards (start_date, reference window,
        channel-key validity, exclusion validity) so invalid future configs
        fail fast without touching the model. The zero-denominator
        reference-window condition is now checked here for non-excluded
        channels (a fast `invalid_optimization_config` at submit); the
        build-time seed guard remains as defense-in-depth.
        """
        from google_meridian_mcp_server.meridian import future_data as fd

        f = config.future
        times = self.get_time_values()
        cadence = fd.infer_cadence_days(times)
        labels = fd.future_time_labels(f.start_date, f.horizon, cadence)
        if labels[0] <= times[-1][:10]:
            raise ValueError(
                "future start_date must be after the last training period."
            )
        window = fd.reference_indices(
            f.reference.mode, f.horizon, f.start_date, times, cadence
        )

        inputs = self.get_data_inputs()
        if inputs["media"]:
            self._spend_np("media_spend")  # cheap ndim guard; raises if not 3-D
        if inputs["rf_media"]:
            self._spend_np("rf_spend")  # cheap ndim guard; raises if not 3-D

        media_order = self.get_data_inputs()["media"]
        rf_order = self.get_data_inputs()["rf_media"]
        fd.validate_channel_keys(f.cost_multipliers, media_order + rf_order)

        fd.validate_channel_keys(f.planned_allocation, self.channel_order())

        fd.validate_excluded_channels(
            f.excluded_channels,
            f.planned_allocation,
            f.cost_multipliers,
            self.channel_order(),
        )

        # Fail fast: a non-excluded channel with a zero-denominator reference
        # window would otherwise surface only as a FAILED run at build time.
        excluded_set = set(f.excluded_channels or [])
        if media_order:
            media_excluded_idx = frozenset(
                i for i, ch in enumerate(media_order) if ch in excluded_set
            )
            self._raise_on_zero_denominator(
                self._media_unit_sum(window), "media", skip=media_excluded_idx
            )
        if rf_order:
            rf_excluded_idx = frozenset(
                i for i, ch in enumerate(rf_order) if ch in excluded_set
            )
            self._raise_on_zero_denominator(
                self._rf_impression_sum(window), "rf", skip=rf_excluded_idx
            )

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
        excluded_set = set(f.excluded_channels or [])
        media_excluded_idx = frozenset(
            i for i, ch in enumerate(media_order) if ch in excluded_set
        )
        rf_excluded_idx = frozenset(
            i for i, ch in enumerate(rf_order) if ch in excluded_set
        )
        average = f.reference.mode == "full_history_average"

        seeded_total = 0.0
        tensor_kwargs: dict[str, Any] = {"time": time_labels}
        if media_order:
            tensor_kwargs["cpmu"] = fd.apply_cost_multipliers(
                self._seed_cpmu(window, media_excluded_idx),
                f.cost_multipliers,
                media_order,
            )
            media_spend = self._seed_spend_flighting(
                "media_spend", window, f.horizon, average
            )
            tensor_kwargs["media_spend"] = media_spend
            seeded_total += float(np.asarray(media_spend).sum())
        if rf_order:
            tensor_kwargs["cprf"] = fd.apply_cost_multipliers(
                self._seed_cprf(window, rf_excluded_idx),
                f.cost_multipliers,
                rf_order,
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

        if f.excluded_channels:
            # Re-validated here (not only in validate_future) because execute()/
            # run_future reach _future_kwargs without necessarily calling
            # validate_future first.
            fd.validate_excluded_channels(
                f.excluded_channels,
                f.planned_allocation,
                f.cost_multipliers,
                self.channel_order(),
            )
            if pct is None:
                total_carried = sum(carried.values())
                if total_carried <= 0:
                    raise ValueError(
                        "reference window has zero spend; cannot build a baseline "
                        "allocation for excluded channels — pick a different reference."
                    )
                pct = [carried[ch] / total_carried for ch in self.channel_order()]
        fixed_budget = config.scenario.type == "fixed_budget"
        scenario_budget = getattr(config.scenario, "budget", None)
        # Budget defaults to the SEEDED future flighting total (horizon periods), NOT the
        # raw reference-window total — the two differ for full_history_average.
        budget = fd.resolve_budget(scenario_budget, fixed_budget, seeded_total)

        kwargs = to_optimize_kwargs(
            config, channel_order=self.channel_order(), use_kpi=use_kpi
        )
        kwargs.update(
            new_data=new_data,
            start_date=time_labels[0],
            end_date=time_labels[-1],
            pct_of_spend=pct,
            budget=budget,
        )
        if f.excluded_channels:
            new_pct, lower, upper = fd.apply_exclusions(
                pct,
                kwargs["spend_constraint_lower"],
                kwargs["spend_constraint_upper"],
                f.excluded_channels,
                self.channel_order(),
            )
            kwargs.update(
                pct_of_spend=new_pct,
                spend_constraint_lower=lower,
                spend_constraint_upper=upper,
            )
        kwargs["_assumptions"] = {
            "budget": budget if fixed_budget else None,
            "budget_source": (
                (
                    "explicit"
                    if scenario_budget is not None
                    else "derived_from_reference"
                )
                if fixed_budget
                else "determined_by_target"
            ),
            "reference_mode": f.reference.mode,
            "excluded_channels": list(f.excluded_channels or []),
        }
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

    def _media_unit_sum(self, window: list[int]) -> np.ndarray:
        media = self._trim_media(
            np.asarray(self._mmm.input_data.media.values, dtype=float)
        )
        return media[:, window, :].sum(axis=(0, 1))

    def _rf_impression_sum(self, window: list[int]) -> np.ndarray:
        reach = self._trim_media(
            np.asarray(self._mmm.input_data.reach.values, dtype=float)
        )
        frequency = self._trim_media(
            np.asarray(self._mmm.input_data.frequency.values, dtype=float)
        )
        return (reach * frequency)[:, window, :].sum(axis=(0, 1))

    def _seed_cpmu(
        self, window: list[int], excluded_idx: frozenset[int] = frozenset()
    ) -> np.ndarray:
        spend_sum = self._spend_np("media_spend")[:, window, :].sum(axis=(0, 1))
        media_sum = self._media_unit_sum(window)
        self._raise_on_zero_denominator(media_sum, "media", skip=excluded_idx)
        safe = np.where(media_sum == 0, 1.0, media_sum)
        return self._benign_excluded_cost(spend_sum / safe, excluded_idx)

    def _seed_cprf(
        self, window: list[int], excluded_idx: frozenset[int] = frozenset()
    ) -> np.ndarray:
        spend_sum = self._spend_np("rf_spend")[:, window, :].sum(axis=(0, 1))
        impressions_sum = self._rf_impression_sum(window)
        self._raise_on_zero_denominator(impressions_sum, "rf", skip=excluded_idx)
        safe = np.where(impressions_sum == 0, 1.0, impressions_sum)
        return self._benign_excluded_cost(spend_sum / safe, excluded_idx)

    @staticmethod
    def _benign_excluded_cost(
        cost: np.ndarray, excluded_idx: frozenset[int]
    ) -> np.ndarray:
        """Excluded channels are forced to 0 spend with 0/0 bounds, so their
        per-unit cost never affects the result; it only needs to be finite and
        positive so downstream unit math (units = spend / cost) is defined."""
        for i in excluded_idx:
            if not np.isfinite(cost[i]) or cost[i] <= 0:
                cost[i] = 1.0
        return cost

    def _raise_on_zero_denominator(
        self, denom_sum: np.ndarray, family: str, *, skip: frozenset[int] = frozenset()
    ) -> None:
        """Guard against zero media units/impressions for a NON-excluded channel
        in the reference window (a zero denominator would otherwise yield inf/NaN
        cost-per-unit and a 'completed' run full of null metrics). Excluded
        channels are skipped — their cost is inert (see _benign_excluded_cost)."""
        zero_idx = [
            int(i)
            for i in np.flatnonzero(np.asarray(denom_sum) == 0)
            if int(i) not in skip
        ]
        if not zero_idx:
            return
        order = (
            self.get_data_inputs()["media"]
            if family == "media"
            else self.get_data_inputs()["rf_media"]
        )
        names = [order[i] for i in zero_idx]
        units = "media units" if family == "media" else "RF impressions"
        raise ValueError(
            f"channel(s) {names} have zero {units} in the chosen reference window "
            "— either exclude them (excluded_channels) or pick a window where they "
            "were active"
        )

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
        nonopt, opt, *, use_kpi: bool, response_curves=None, assumptions=None
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
        if assumptions is not None:
            result["assumptions"] = assumptions
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

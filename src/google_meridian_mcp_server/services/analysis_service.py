"""Service layer for grouped analysis operations.

Task 10: routes every public method through a subprocess ``runner`` (a
``SyncSubprocessExecutor``) instead of calling the Meridian facade/catalog
in-process. Only PURE validation stays here (output_type membership,
dataset-selection normalization/validation, filter normalization, result
caching, and the pure ``get_model_overview`` decoration). Model-dependent
validation (revenue/RF/geo guards) and the actual facade calls now live
worker-side in ``execution/analysis_ops.py``.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from google_meridian_mcp_server.domain.errors import (
    DatasetNotAvailableError,
    InvalidOutputTypeError,
)
from google_meridian_mcp_server.domain.filters import (
    TRAINING_DATASETS,
    AnalysisFilters,
    normalize_filters,
)
from google_meridian_mcp_server.persistence.cache import ResultCache

log = logging.getLogger(__name__)

CHANNEL_SUMMARY_TYPE_ORDER = (
    "baseline_summary_metrics",
    "paid_summary_metrics",
    "roi",
    "cpik",
    "marginal_roi",
    "marginal_cpik",
)
CHANNEL_SUMMARY_TYPES = frozenset(CHANNEL_SUMMARY_TYPE_ORDER)
REVENUE_ONLY_CHANNEL_SUMMARY_TYPES = frozenset({"roi", "marginal_roi"})

CONTRIBUTION_TYPE_ORDER = ("contribution_metrics", "contribution_metrics_by_time")
CONTRIBUTION_TYPES = frozenset(CONTRIBUTION_TYPE_ORDER)

RESPONSE_DYNAMICS_TYPE_ORDER = ("adstock_decay", "alpha_summary")
RESPONSE_DYNAMICS_TYPES = frozenset(RESPONSE_DYNAMICS_TYPE_ORDER)

RESPONSE_CURVE_TYPE_ORDER = ("response_curves", "response_curve_summary")
RESPONSE_CURVE_TYPES = frozenset(RESPONSE_CURVE_TYPE_ORDER)


class AnalysisService:
    """Orchestrates grouped analysis queries via the subprocess runner.

    Pure validation (output_type membership, dataset selection) and result
    caching happen here without spawning a worker. Model-dependent guards
    and the actual Meridian facade calls happen worker-side.
    """

    def __init__(self, runner: Any, result_cache: ResultCache | None = None) -> None:
        self._runner = runner
        self._cache = result_cache

    @staticmethod
    def _filter_key(filters: AnalysisFilters) -> dict[str, Any]:
        return filters.model_dump(mode="json")

    @staticmethod
    def _build_result(
        *,
        model_id: str,
        rows: list[dict[str, Any]],
        dataset: str | None = None,
        datasets: list[str] | None = None,
        output_type: str | None = None,
    ) -> dict[str, Any]:
        columns = AnalysisService._ordered_columns(rows)
        result: dict[str, Any] = {"model_id": model_id}
        if output_type is not None:
            result["output_type"] = output_type
        if dataset is not None:
            result["dataset"] = dataset
        if datasets is not None:
            result["datasets"] = datasets
        result["columns"] = columns
        result["rows"] = [
            [AnalysisService._round_measure(row.get(column)) for column in columns]
            for row in rows
        ]
        result["row_count"] = len(rows)
        return result

    @staticmethod
    def _round_measure(value: Any) -> Any:
        # Cells are scalars by the time they reach here (dataset_mapper
        # normalizes measures to scalars), so rounding is intentionally
        # shallow: bools and ints pass through, floats round to 6 sig figs.
        if isinstance(value, bool):
            return value
        if isinstance(value, float):
            return float(f"{value:.6g}")
        return value

    @staticmethod
    def _safe_ratio(numerator: float, denominator: float) -> float | None:
        if not denominator:
            return None
        return numerator / denominator

    @staticmethod
    def _ordered_columns(rows: list[dict[str, Any]]) -> list[str]:
        columns: list[str] = []
        for row in rows:
            for column in row:
                if column not in columns:
                    columns.append(column)
        return columns

    @staticmethod
    def _normalize_dataset_selection(
        model_id: str, dataset: str | Sequence[str]
    ) -> list[str]:
        if isinstance(dataset, str):
            normalized = [dataset]
        else:
            normalized = []
            for value in dataset:
                if value not in normalized:
                    normalized.append(value)

        if not normalized:
            raise DatasetNotAvailableError(model_id, "")

        invalid_datasets = [
            value for value in normalized if value not in TRAINING_DATASETS
        ]
        if invalid_datasets:
            raise DatasetNotAvailableError(model_id, invalid_datasets[0])

        return normalized

    async def _cached(
        self,
        tool_name: str,
        model_id: str,
        params: dict[str, Any],
        operation: str,
    ) -> dict[str, Any]:
        if self._cache:
            hit = self._cache.get(tool_name, model_id, params)
            if hit is not None:
                log.debug("Cache hit: %s / %s", tool_name, model_id)
                return hit

        result = await self._runner.run(operation, model_id, params)

        if self._cache:
            self._cache.put(tool_name, model_id, params, result)
        return result

    async def get_training_data(
        self,
        model_id: str,
        dataset: str | Sequence[str],
        filters: AnalysisFilters | dict | None,
    ) -> dict[str, Any]:
        normalized_filters = normalize_filters(filters)
        datasets = self._normalize_dataset_selection(model_id, dataset)

        params = {
            "datasets": datasets,
            "filters": self._filter_key(normalized_filters),
        }
        return await self._cached(
            "get_training_data", model_id, params, "get_training_data"
        )

    async def get_channel_data(
        self, model_id: str, filters: AnalysisFilters | dict | None
    ) -> dict[str, Any]:
        normalized_filters = normalize_filters(filters)
        params = {"filters": self._filter_key(normalized_filters)}
        return await self._cached(
            "get_channel_data", model_id, params, "get_channel_data"
        )

    async def get_model_overview(self, model_id: str) -> dict[str, Any]:
        raw = await self._cached(
            "get_model_overview", model_id, {}, "get_model_overview"
        )
        return self._decorate_overview(model_id, raw)

    @classmethod
    def _decorate_overview(
        cls, model_id: str, raw_overview: dict[str, Any]
    ) -> dict[str, Any]:
        overview = dict(raw_overview)

        has_revenue = overview.get("has_revenue_per_kpi", False)
        channel_summary_types = [
            output_type
            for output_type in CHANNEL_SUMMARY_TYPE_ORDER
            if has_revenue or output_type not in REVENUE_ONLY_CHANNEL_SUMMARY_TYPES
        ]
        overview["available_tool_options"] = {
            "get_training_data": {
                "dataset": overview["available_training_datasets"],
            },
            "get_channel_summary": {
                "output_type": channel_summary_types,
            },
            "get_contribution": {
                "output_type": list(CONTRIBUTION_TYPE_ORDER),
            },
            "get_adstock_decay": {
                "output_type": list(RESPONSE_DYNAMICS_TYPE_ORDER),
            },
            "get_response_curves": {
                "output_type": list(RESPONSE_CURVE_TYPE_ORDER),
            },
            "get_channel_data": {},
            "get_model_fit": {},
            "get_spend_scenario": {
                "channel": overview.get("media_channels", [])
                + overview.get("rf_channels", []),
            },
            "run_optimization": {
                "channels": overview.get("media_channels", [])
                + overview.get("rf_channels", []),
                "geos": overview.get("geo_names", []),
                "use_kpi_togglable": overview.get("has_revenue_per_kpi", False),
                "scenarios": ["fixed_budget", "target_roas", "target_mroas"],
            },
        }
        if overview.get("rf_channels"):
            overview["available_tool_options"]["get_reach_frequency"] = {}
        return {"model_id": model_id, **overview}

    async def get_channel_summary(
        self,
        model_id: str,
        output_type: str,
        filters: AnalysisFilters | dict | None,
    ) -> dict[str, Any]:
        if output_type not in CHANNEL_SUMMARY_TYPES:
            raise InvalidOutputTypeError(output_type, sorted(CHANNEL_SUMMARY_TYPES))
        normalized_filters = normalize_filters(filters)
        params = {
            "output_type": output_type,
            "filters": self._filter_key(normalized_filters),
        }
        return await self._cached(
            "get_channel_summary", model_id, params, "get_channel_summary"
        )

    async def get_contribution(
        self,
        model_id: str,
        output_type: str,
        filters: AnalysisFilters | dict | None,
    ) -> dict[str, Any]:
        if output_type not in CONTRIBUTION_TYPES:
            raise InvalidOutputTypeError(output_type, sorted(CONTRIBUTION_TYPES))
        normalized_filters = normalize_filters(filters)
        params = {
            "output_type": output_type,
            "filters": self._filter_key(normalized_filters),
        }
        return await self._cached(
            "get_contribution", model_id, params, "get_contribution"
        )

    async def get_adstock_decay(
        self,
        model_id: str,
        output_type: str,
        filters: AnalysisFilters | dict | None,
    ) -> dict[str, Any]:
        if output_type not in RESPONSE_DYNAMICS_TYPES:
            raise InvalidOutputTypeError(output_type, sorted(RESPONSE_DYNAMICS_TYPES))
        normalized_filters = normalize_filters(filters)
        params = {
            "output_type": output_type,
            "filters": self._filter_key(normalized_filters),
        }
        return await self._cached(
            "get_adstock_decay", model_id, params, "get_adstock_decay"
        )

    async def get_response_curves(
        self,
        model_id: str,
        output_type: str,
        filters: AnalysisFilters | dict | None,
    ) -> dict[str, Any]:
        if output_type not in RESPONSE_CURVE_TYPES:
            raise InvalidOutputTypeError(output_type, sorted(RESPONSE_CURVE_TYPES))
        normalized_filters = normalize_filters(filters)
        params = {
            "output_type": output_type,
            "filters": self._filter_key(normalized_filters),
        }
        return await self._cached(
            "get_response_curves", model_id, params, "get_response_curves"
        )

    async def get_reach_frequency(
        self, model_id: str, filters: AnalysisFilters | dict | None
    ) -> dict[str, Any]:
        normalized_filters = normalize_filters(filters)
        params = {"filters": self._filter_key(normalized_filters)}
        return await self._cached(
            "get_reach_frequency", model_id, params, "get_reach_frequency"
        )

    async def get_model_fit(
        self, model_id: str, filters: AnalysisFilters | dict | None
    ) -> dict[str, Any]:
        normalized_filters = normalize_filters(filters)
        params = {"filters": self._filter_key(normalized_filters)}
        return await self._cached("get_model_fit", model_id, params, "get_model_fit")

    async def get_spend_scenario(
        self,
        model_id: str,
        channel: str,
        spend_increase: float,
        base_spend: float | None,
        filters: AnalysisFilters | dict | None,
    ) -> dict[str, Any]:
        normalized_filters = normalize_filters(filters)
        params = {
            "channel": channel,
            "spend_increase": spend_increase,
            "base_spend": base_spend,
            "filters": self._filter_key(normalized_filters),
        }
        return await self._cached(
            "get_spend_scenario", model_id, params, "get_spend_scenario"
        )

    @staticmethod
    def _build_spend_scenario(
        *,
        model_id: str,
        channel: str,
        channel_type: str,
        outcome_mode: str,
        base_spend: float,
        spend_increase: float,
        new_spend: float,
        base_outcome: dict[str, Any],
        new_outcome: dict[str, Any],
    ) -> dict[str, Any]:
        b = base_outcome["mean"]
        n = new_outcome["mean"]
        delta = n - b
        if outcome_mode == "revenue":
            efficiency = AnalysisService._safe_ratio(b, base_spend)
            marginal_efficiency = AnalysisService._safe_ratio(delta, spend_increase)
            efficiency_at_new = AnalysisService._safe_ratio(n, new_spend)
        else:
            efficiency = AnalysisService._safe_ratio(base_spend, b)
            marginal_efficiency = AnalysisService._safe_ratio(spend_increase, delta)
            efficiency_at_new = AnalysisService._safe_ratio(new_spend, n)

        summary = {
            "model_id": model_id,
            "channel": channel,
            "channel_type": channel_type,
            "outcome_mode": outcome_mode,
            "base_spend": base_spend,
            "spend_increase": spend_increase,
            "new_spend": new_spend,
            "spend_increase_pct": AnalysisService._safe_ratio(
                100.0 * spend_increase, base_spend
            ),
            "base_outcome": base_outcome,
            "new_outcome": new_outcome,
            "expected_outcome_increase": delta,
            "expected_outcome_increase_pct": AnalysisService._safe_ratio(
                100.0 * delta, b
            ),
            "efficiency": efficiency,
            "marginal_efficiency": marginal_efficiency,
            "efficiency_at_new": efficiency_at_new,
        }
        return {
            key: AnalysisService._round_value(value) for key, value in summary.items()
        }

    @classmethod
    def _round_value(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return {key: cls._round_value(inner) for key, inner in value.items()}
        return cls._round_measure(value)

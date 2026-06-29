"""Service layer for grouped analysis operations."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from typing import Any

from google_meridian_mcp_server.domain.errors import (
    DatasetNotAvailableError,
    InvalidOutputTypeError,
    MetricNotSupportedError,
    MissingModelDataError,
)
from google_meridian_mcp_server.domain.filters import AnalysisFilters, normalize_filters
from google_meridian_mcp_server.meridian.catalog import ModelCatalog
from google_meridian_mcp_server.meridian.dataset_mapper import (
    TRAINING_DATASETS,
    extract_channel_data,
    extract_training_datasets,
    filter_records,
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
    """Orchestrates grouped analysis queries across the catalog and Meridian facade."""

    def __init__(
        self, catalog: ModelCatalog, result_cache: ResultCache | None = None
    ) -> None:
        self._catalog = catalog
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

    def _cached(
        self,
        tool_name: str,
        model_id: str,
        params: dict[str, Any],
        compute: Callable[[], dict[str, Any]],
    ) -> dict[str, Any]:
        if self._cache:
            cached = self._cache.get(tool_name, model_id, params)
            if cached is not None:
                log.debug("Cache hit: %s / %s", tool_name, model_id)
                return cached

        result = compute()
        if self._cache:
            self._cache.put(tool_name, model_id, params, result)
        return result

    def _run_facade_query(
        self,
        *,
        tool_name: str,
        model_id: str,
        output_type: str,
        filters: AnalysisFilters,
        valid_types: frozenset[str],
        dispatch: dict[str, str],
    ) -> dict[str, Any]:
        if output_type not in valid_types:
            raise InvalidOutputTypeError(output_type, sorted(valid_types))

        params = {"output_type": output_type, "filters": self._filter_key(filters)}

        def _compute() -> dict[str, Any]:
            facade = self._catalog.get_facade(model_id)
            method_name = dispatch[output_type]
            try:
                rows = getattr(facade, method_name)(filters)
            except Exception as exc:
                raise MissingModelDataError(model_id, str(exc)) from exc
            return self._build_result(
                model_id=model_id,
                output_type=output_type,
                rows=rows,
            )

        return self._cached(tool_name, model_id, params, _compute)

    def get_training_data(
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

        def _compute() -> dict[str, Any]:
            try:
                rows = extract_training_datasets(
                    self._catalog.resolve(model_id), datasets
                )
            except Exception as exc:
                raise MissingModelDataError(model_id, str(exc)) from exc
            rows = filter_records(
                rows,
                start_date=normalized_filters.start_date,
                end_date=normalized_filters.end_date,
                geos=normalized_filters.geos,
                channels=normalized_filters.channels,
            )
            return self._build_result(
                model_id=model_id,
                dataset=datasets[0] if len(datasets) == 1 else None,
                datasets=datasets,
                rows=rows,
            )

        return self._cached("get_training_data", model_id, params, _compute)

    def get_channel_data(
        self, model_id: str, filters: AnalysisFilters | dict | None
    ) -> dict[str, Any]:
        normalized_filters = normalize_filters(filters)
        params = {"filters": self._filter_key(normalized_filters)}

        def _compute() -> dict[str, Any]:
            try:
                rows = extract_channel_data(self._catalog.resolve(model_id))
            except Exception as exc:
                raise MissingModelDataError(model_id, str(exc)) from exc
            rows = filter_records(
                rows,
                start_date=normalized_filters.start_date,
                end_date=normalized_filters.end_date,
                geos=normalized_filters.geos,
                channels=normalized_filters.channels,
            )
            return self._build_result(model_id=model_id, rows=rows)

        return self._cached("get_channel_data", model_id, params, _compute)

    def get_model_overview(self, model_id: str) -> dict[str, Any]:
        def _compute() -> dict[str, Any]:
            try:
                overview = self._catalog.get_interrogator(model_id).get_model_overview()
            except Exception as exc:
                raise MissingModelDataError(model_id, str(exc)) from exc

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
            }
            if overview.get("rf_channels"):
                overview["available_tool_options"]["get_reach_frequency"] = {}
            result = {"model_id": model_id, **overview}
            return result

        return self._cached("get_model_overview", model_id, {}, _compute)

    def get_channel_summary(
        self,
        model_id: str,
        output_type: str,
        filters: AnalysisFilters | dict | None,
    ) -> dict[str, Any]:
        if output_type in REVENUE_ONLY_CHANNEL_SUMMARY_TYPES:
            interrogator = self._catalog.get_interrogator(model_id)
            if not interrogator.has_revenue_per_kpi():
                raise MetricNotSupportedError(
                    model_id,
                    output_type,
                    "model has no revenue_per_kpi; ROI metrics require revenue",
                )
        return self._run_facade_query(
            tool_name="get_channel_summary",
            model_id=model_id,
            output_type=output_type,
            filters=normalize_filters(filters),
            valid_types=CHANNEL_SUMMARY_TYPES,
            dispatch={
                "baseline_summary_metrics": "get_baseline_summary_metrics",
                "paid_summary_metrics": "get_paid_summary_metrics",
                "roi": "get_roi",
                "cpik": "get_cpik",
                "marginal_roi": "get_marginal_roi",
                "marginal_cpik": "get_marginal_cpik",
            },
        )

    def get_contribution(
        self,
        model_id: str,
        output_type: str,
        filters: AnalysisFilters | dict | None,
    ) -> dict[str, Any]:
        return self._run_facade_query(
            tool_name="get_contribution",
            model_id=model_id,
            output_type=output_type,
            filters=normalize_filters(filters),
            valid_types=CONTRIBUTION_TYPES,
            dispatch={
                "contribution_metrics": "get_contribution_metrics",
                "contribution_metrics_by_time": "get_contribution_metrics_by_time",
            },
        )

    def get_adstock_decay(
        self,
        model_id: str,
        output_type: str,
        filters: AnalysisFilters | dict | None,
    ) -> dict[str, Any]:
        return self._run_facade_query(
            tool_name="get_adstock_decay",
            model_id=model_id,
            output_type=output_type,
            filters=normalize_filters(filters),
            valid_types=RESPONSE_DYNAMICS_TYPES,
            dispatch={
                "adstock_decay": "get_adstock_decay",
                "alpha_summary": "get_alpha_summary",
            },
        )

    def get_response_curves(
        self,
        model_id: str,
        output_type: str,
        filters: AnalysisFilters | dict | None,
    ) -> dict[str, Any]:
        return self._run_facade_query(
            tool_name="get_response_curves",
            model_id=model_id,
            output_type=output_type,
            filters=normalize_filters(filters),
            valid_types=RESPONSE_CURVE_TYPES,
            dispatch={
                "response_curves": "get_response_curves",
                "response_curve_summary": "get_response_curve_summary",
            },
        )

    def get_reach_frequency(
        self, model_id: str, filters: AnalysisFilters | dict | None
    ) -> dict[str, Any]:
        interrogator = self._catalog.get_interrogator(model_id)
        if not interrogator.has_rf_channels():
            raise MetricNotSupportedError(
                model_id,
                "reach_frequency",
                "model has no reach & frequency channels",
            )
        normalized_filters = normalize_filters(filters)
        params = {"filters": self._filter_key(normalized_filters)}

        def _compute() -> dict[str, Any]:
            facade = self._catalog.get_facade(model_id)
            try:
                rows = facade.get_reach_frequency(normalized_filters)
            except Exception as exc:
                raise MissingModelDataError(model_id, str(exc)) from exc
            return self._build_result(model_id=model_id, rows=rows)

        return self._cached("get_reach_frequency", model_id, params, _compute)

    def get_model_fit(
        self, model_id: str, filters: AnalysisFilters | dict | None
    ) -> dict[str, Any]:
        normalized_filters = normalize_filters(filters)
        if normalized_filters.geos:
            valid_geos = set(self._catalog.get_interrogator(model_id).geo_names())
            unknown = [geo for geo in normalized_filters.geos if geo not in valid_geos]
            if unknown:
                raise MissingModelDataError(
                    model_id, f"unknown geo(s): {', '.join(unknown)}"
                )
        params = {"filters": self._filter_key(normalized_filters)}

        def _compute() -> dict[str, Any]:
            facade = self._catalog.get_facade(model_id)
            try:
                rows = facade.get_model_fit(normalized_filters)
            except Exception as exc:
                raise MissingModelDataError(model_id, str(exc)) from exc
            return self._build_result(model_id=model_id, rows=rows)

        return self._cached("get_model_fit", model_id, params, _compute)

    def get_spend_scenario(
        self,
        model_id: str,
        channel: str,
        spend_increase: float,
        base_spend: float | None,
        filters: AnalysisFilters | dict | None,
    ) -> dict[str, Any]:
        normalized_filters = normalize_filters(filters)
        facade = self._catalog.get_facade(model_id)

        data_inputs = facade.get_data_inputs()
        if channel in data_inputs["media"]:
            channel_type = "paid_media"
        elif channel in data_inputs["rf_media"]:
            channel_type = "rf"
        else:
            raise MissingModelDataError(
                model_id,
                f"channel '{channel}' is not a paid media or RF channel",
            )

        if base_spend is not None and base_spend <= 0:
            raise MissingModelDataError(
                model_id, "base_spend must be a positive number"
            )

        outcome_mode = (
            "kpi" if facade.resolve_use_kpi(normalized_filters) else "revenue"
        )
        params = {
            "channel": channel,
            "spend_increase": spend_increase,
            "base_spend": base_spend,
            "filters": self._filter_key(normalized_filters),
        }

        def _compute() -> dict[str, Any]:
            try:
                resolved_base = (
                    base_spend
                    if base_spend is not None
                    else facade.resolve_base_spend(channel, normalized_filters)
                )
                new_spend = resolved_base + spend_increase
                outcomes = facade.spend_response(
                    channel, [resolved_base, new_spend], normalized_filters
                )
            except Exception as exc:
                raise MissingModelDataError(model_id, str(exc)) from exc

            return self._build_spend_scenario(
                model_id=model_id,
                channel=channel,
                channel_type=channel_type,
                outcome_mode=outcome_mode,
                base_spend=resolved_base,
                spend_increase=spend_increase,
                new_spend=new_spend,
                base_outcome=outcomes[0],
                new_outcome=outcomes[1],
            )

        return self._cached("get_spend_scenario", model_id, params, _compute)

    def _build_spend_scenario(
        self,
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
            efficiency = self._safe_ratio(b, base_spend)
            marginal_efficiency = self._safe_ratio(delta, spend_increase)
            efficiency_at_new = self._safe_ratio(n, new_spend)
        else:
            efficiency = self._safe_ratio(base_spend, b)
            marginal_efficiency = self._safe_ratio(spend_increase, delta)
            efficiency_at_new = self._safe_ratio(new_spend, n)

        summary = {
            "model_id": model_id,
            "channel": channel,
            "channel_type": channel_type,
            "outcome_mode": outcome_mode,
            "base_spend": base_spend,
            "spend_increase": spend_increase,
            "new_spend": new_spend,
            "spend_increase_pct": self._safe_ratio(100.0 * spend_increase, base_spend),
            "base_outcome": base_outcome,
            "new_outcome": new_outcome,
            "expected_outcome_increase": delta,
            "expected_outcome_increase_pct": self._safe_ratio(100.0 * delta, b),
            "efficiency": efficiency,
            "marginal_efficiency": marginal_efficiency,
            "efficiency_at_new": efficiency_at_new,
        }
        return {key: self._round_value(value) for key, value in summary.items()}

    @classmethod
    def _round_value(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return {key: cls._round_value(inner) for key, inner in value.items()}
        return cls._round_measure(value)

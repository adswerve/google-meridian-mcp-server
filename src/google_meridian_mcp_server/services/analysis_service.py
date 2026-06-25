"""Service layer for grouped analysis operations."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from typing import Any

from google_meridian_mcp_server.domain.errors import (
    DatasetNotAvailableError,
    InvalidOutputTypeError,
    MissingModelDataError,
)
from google_meridian_mcp_server.domain.filters import AnalysisFilters, normalize_filters
from google_meridian_mcp_server.meridian.catalog import ModelCatalog
from google_meridian_mcp_server.meridian.dataset_mapper import (
    TRAINING_DATASETS,
    extract_training_datasets,
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
        if isinstance(value, bool):
            return value
        if isinstance(value, float):
            return float(f"{value:.6g}")
        return value

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
            return self._build_result(
                model_id=model_id,
                dataset=datasets[0] if len(datasets) == 1 else None,
                datasets=datasets,
                rows=rows,
            )

        return self._cached("get_training_data", model_id, params, _compute)

    def get_model_overview(self, model_id: str) -> dict[str, Any]:
        def _compute() -> dict[str, Any]:
            try:
                overview = self._catalog.get_interrogator(model_id).get_model_overview()
            except Exception as exc:
                raise MissingModelDataError(model_id, str(exc)) from exc

            overview["available_tool_options"] = {
                "get_training_data": {
                    "dataset": overview["available_training_datasets"],
                },
                "get_channel_summary": {
                    "output_type": list(CHANNEL_SUMMARY_TYPE_ORDER),
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
            }
            result = {"model_id": model_id, **overview}
            return result

        return self._cached("get_model_overview", model_id, {}, _compute)

    def get_channel_summary(
        self,
        model_id: str,
        output_type: str,
        filters: AnalysisFilters | dict | None,
    ) -> dict[str, Any]:
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

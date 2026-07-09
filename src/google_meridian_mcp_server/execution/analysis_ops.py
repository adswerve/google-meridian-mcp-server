"""Worker-side Meridian op dispatch (imported only inside worker subprocesses).

Each op below is the MODEL-DEPENDENT HALF of the corresponding
``AnalysisService`` method (or ``OptimizationService`` submit-time
validation, for ``preflight_optimization``): the pre-cache model
validation guards, the facade call, and result shaping. The PURE half
(cache lookups, `available_tool_options` decoration, compute-tier
scoring) stays server-side and is layered back on by the caller.

Filters always arrive from the server as a plain dict (JSON round-trip),
so every op that touches filters rehydrates them via ``normalize_filters``
before handing them to a facade method -- facade methods are typed
``(self, filters: AnalysisFilters)`` and do attribute access.
"""

from __future__ import annotations

import math
from typing import Any, Callable

import numpy as np

from google_meridian_mcp_server.domain.errors import (
    InvalidOutputTypeError,
    MeridianMcpError,
    MetricNotSupportedError,
    MissingModelDataError,
)
from google_meridian_mcp_server.domain.filters import normalize_filters
from google_meridian_mcp_server.domain.optimization import (
    FutureOptimizationConfig,
    OptimizationConfig,
    to_optimize_kwargs,
)
from google_meridian_mcp_server.execution import routing
from google_meridian_mcp_server.meridian.dataset_mapper import (
    extract_channel_data,
    extract_training_datasets,
    filter_records,
)
from google_meridian_mcp_server.services.analysis_service import AnalysisService

CHANNEL_SUMMARY_DISPATCH = {
    "baseline_summary_metrics": "get_baseline_summary_metrics",
    "paid_summary_metrics": "get_paid_summary_metrics",
    "roi": "get_roi",
    "cpik": "get_cpik",
    "marginal_roi": "get_marginal_roi",
    "marginal_cpik": "get_marginal_cpik",
}
REVENUE_ONLY_CHANNEL_SUMMARY_TYPES = frozenset({"roi", "marginal_roi"})

CONTRIBUTION_DISPATCH = {
    "contribution_metrics": "get_contribution_metrics",
    "contribution_metrics_by_time": "get_contribution_metrics_by_time",
}

RESPONSE_DYNAMICS_DISPATCH = {
    "adstock_decay": "get_adstock_decay",
    "alpha_summary": "get_alpha_summary",
}

RESPONSE_CURVE_DISPATCH = {
    "response_curves": "get_response_curves",
    "response_curve_summary": "get_response_curve_summary",
}


def sanitize_nan(obj: Any) -> Any:
    """Recursively replace non-finite floats (nan/inf/-inf) with None.

    Applied to the WHOLE response payload (including error details) right
    before the strict (``allow_nan=False``) JSON dump at the IPC boundary.

    Belt-and-suspenders: handles ``np.floating``/``np.integer`` (a stray
    unconverted numpy scalar would otherwise crash `json.dump` outright,
    NaN or not) and tuples (json.dump serializes them as lists, but a NaN
    inside one would slip past a dict/list-only check and crash the dump).
    """
    if isinstance(obj, (float, np.floating)):
        value = float(obj)
        return value if math.isfinite(value) else None
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, dict):
        return {k: sanitize_nan(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [sanitize_nan(v) for v in obj]
    return obj


def _dispatch_facade_query(
    catalog: Any, model_id: str, params: dict, dispatch: dict[str, str]
) -> dict:
    facade = catalog.get_facade(model_id)
    filters = normalize_filters(params["filters"])
    output_type = params["output_type"]
    if output_type not in dispatch:
        raise InvalidOutputTypeError(output_type, sorted(dispatch))
    method_name = dispatch[output_type]
    try:
        rows = getattr(facade, method_name)(filters)
    except Exception as exc:  # worker boundary
        raise MissingModelDataError(model_id, str(exc)) from exc
    return AnalysisService._build_result(
        model_id=model_id, output_type=output_type, rows=rows
    )


def _get_contribution(catalog: Any, model_id: str, params: dict) -> dict:
    return _dispatch_facade_query(catalog, model_id, params, CONTRIBUTION_DISPATCH)


def _get_channel_summary(catalog: Any, model_id: str, params: dict) -> dict:
    output_type = params["output_type"]
    if output_type in REVENUE_ONLY_CHANNEL_SUMMARY_TYPES:
        interrogator = catalog.get_interrogator(model_id)
        if not interrogator.has_revenue_per_kpi():
            raise MetricNotSupportedError(
                model_id,
                output_type,
                "model has no revenue_per_kpi; ROI metrics require revenue",
            )
    return _dispatch_facade_query(catalog, model_id, params, CHANNEL_SUMMARY_DISPATCH)


def _get_adstock_decay(catalog: Any, model_id: str, params: dict) -> dict:
    return _dispatch_facade_query(catalog, model_id, params, RESPONSE_DYNAMICS_DISPATCH)


def _get_response_curves(catalog: Any, model_id: str, params: dict) -> dict:
    return _dispatch_facade_query(catalog, model_id, params, RESPONSE_CURVE_DISPATCH)


def _get_reach_frequency(catalog: Any, model_id: str, params: dict) -> dict:
    interrogator = catalog.get_interrogator(model_id)
    if not interrogator.has_rf_channels():
        raise MetricNotSupportedError(
            model_id,
            "reach_frequency",
            "model has no reach & frequency channels",
        )
    facade = catalog.get_facade(model_id)
    filters = normalize_filters(params["filters"])
    try:
        rows = facade.get_reach_frequency(filters)
    except Exception as exc:  # worker boundary
        raise MissingModelDataError(model_id, str(exc)) from exc
    return AnalysisService._build_result(model_id=model_id, rows=rows)


def _get_model_fit(catalog: Any, model_id: str, params: dict) -> dict:
    filters = normalize_filters(params["filters"])
    if filters.geos:
        valid_geos = set(catalog.get_interrogator(model_id).geo_names())
        unknown = [geo for geo in filters.geos if geo not in valid_geos]
        if unknown:
            raise MissingModelDataError(
                model_id, f"unknown geo(s): {', '.join(unknown)}"
            )
    facade = catalog.get_facade(model_id)
    try:
        rows = facade.get_model_fit(filters)
    except Exception as exc:  # worker boundary
        raise MissingModelDataError(model_id, str(exc)) from exc
    return AnalysisService._build_result(model_id=model_id, rows=rows)


def _get_model_overview(catalog: Any, model_id: str, params: dict) -> dict:
    interrogator = catalog.get_interrogator(model_id)
    try:
        overview = interrogator.get_model_overview()
    except Exception as exc:  # worker boundary
        raise MissingModelDataError(model_id, str(exc)) from exc
    # Raw overview only -- `available_tool_options` decoration is pure and
    # stays server-side (Task 10).
    return {"model_id": model_id, **overview}


def _get_channel_data(catalog: Any, model_id: str, params: dict) -> dict:
    filters = normalize_filters(params["filters"])
    try:
        rows = extract_channel_data(catalog.resolve(model_id))
    except Exception as exc:  # worker boundary
        raise MissingModelDataError(model_id, str(exc)) from exc
    rows = filter_records(
        rows,
        start_date=filters.start_date,
        end_date=filters.end_date,
        geos=filters.geos,
        channels=filters.channels,
    )
    return AnalysisService._build_result(model_id=model_id, rows=rows)


def _get_training_data(catalog: Any, model_id: str, params: dict) -> dict:
    filters = normalize_filters(params["filters"])
    datasets: list[str] = params["datasets"]
    try:
        rows = extract_training_datasets(catalog.resolve(model_id), datasets)
    except Exception as exc:  # worker boundary
        raise MissingModelDataError(model_id, str(exc)) from exc
    rows = filter_records(
        rows,
        start_date=filters.start_date,
        end_date=filters.end_date,
        geos=filters.geos,
        channels=filters.channels,
    )
    return AnalysisService._build_result(
        model_id=model_id,
        dataset=datasets[0] if len(datasets) == 1 else None,
        datasets=datasets,
        rows=rows,
    )


def _get_spend_scenario(catalog: Any, model_id: str, params: dict) -> dict:
    filters = normalize_filters(params["filters"])
    facade = catalog.get_facade(model_id)
    channel = params["channel"]
    spend_increase = params["spend_increase"]
    base_spend = params.get("base_spend")

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

    outcome_mode = "kpi" if facade.resolve_use_kpi(filters) else "revenue"

    try:
        resolved_base = (
            base_spend
            if base_spend is not None
            else facade.resolve_base_spend(channel, filters)
        )
        new_spend = resolved_base + spend_increase
        outcomes = facade.spend_response(channel, [resolved_base, new_spend], filters)
    except Exception as exc:  # worker boundary
        raise MissingModelDataError(model_id, str(exc)) from exc

    return AnalysisService._build_spend_scenario(
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


def _preflight_optimization(catalog: Any, model_id: str, params: dict) -> dict:
    config_dict = params["config"]
    facade = catalog.get_optimizer_facade(model_id)

    validation_error: dict[str, Any] | None = None
    try:
        if config_dict.get("kind") == "future":
            config = FutureOptimizationConfig.model_validate(config_dict)
            facade.validate_future(config)  # pure guards, no optimize()
        else:
            config = OptimizationConfig.model_validate(config_dict)
            use_kpi_for_kwargs = facade.resolve_use_kpi(config)
            to_optimize_kwargs(
                config,
                channel_order=facade.channel_order(),
                use_kpi=use_kpi_for_kwargs,
            )
    except MeridianMcpError as exc:
        validation_error = exc.to_payload()
    except Exception as exc:  # pydantic ValidationError or ValueError
        validation_error = {
            "error_code": "invalid_optimization_config",
            "message": str(exc),
            "details": {},
        }

    # use_kpi mirrors OptimizerFacade.resolve_use_kpi's logic but is derived
    # straight from the raw config dict so it's available even when
    # validation_error is set (e.g. a per_channel constraint error is still
    # actionable together with channel_order/use_kpi).
    raw_use_kpi = config_dict.get("use_kpi")
    use_kpi = raw_use_kpi if raw_use_kpi is not None else not facade.has_revenue_per_kpi()

    return {
        "channel_order": facade.channel_order(),
        "has_revenue_per_kpi": facade.has_revenue_per_kpi(),
        "use_kpi": use_kpi,
        "size_features": routing.model_size_features(facade),
        "validation_error": validation_error,
    }


ANALYSIS_OPS: dict[str, Callable[[Any, str, dict], dict]] = {
    "get_contribution": _get_contribution,
    "get_channel_summary": _get_channel_summary,
    "get_adstock_decay": _get_adstock_decay,
    "get_response_curves": _get_response_curves,
    "get_reach_frequency": _get_reach_frequency,
    "get_model_fit": _get_model_fit,
    "get_model_overview": _get_model_overview,
    "get_channel_data": _get_channel_data,
    "get_training_data": _get_training_data,
    "get_spend_scenario": _get_spend_scenario,
    "preflight_optimization": _preflight_optimization,
}


def run_operation(catalog: Any, operation: str, model_id: str, params: dict) -> dict:
    fn = ANALYSIS_OPS.get(operation)
    if fn is None:
        raise InvalidOutputTypeError(operation, sorted(ANALYSIS_OPS))
    return fn(catalog, model_id, params)

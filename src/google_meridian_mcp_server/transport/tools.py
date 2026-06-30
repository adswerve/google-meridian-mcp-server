"""MCP tool definitions registered onto the FastMCP server instance."""

from __future__ import annotations

from typing import Annotated, Any

from fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from google_meridian_mcp_server.domain.errors import MeridianMcpError
from google_meridian_mcp_server.domain.filters import (
    AnalysisFilters,
    ChannelSummaryType,
    ContributionType,
    ResponseCurveType,
    ResponseDynamicsType,
    TrainingDataset,
    normalize_filters,
)
from google_meridian_mcp_server.domain.optimization import OptimizationConfig
from google_meridian_mcp_server.services.analysis_service import AnalysisService
from google_meridian_mcp_server.services.model_catalog_service import (
    ModelCatalogService,
)
from google_meridian_mcp_server.services.optimization_service import OptimizationService

READ_ONLY_TOOL_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    idempotentHint=True,
    openWorldHint=False,
)


def _error_response(error: MeridianMcpError) -> dict[str, Any]:
    return {
        "error_code": error.error_code,
        "message": str(error),
        "details": error.details,
    }


def _catalog_service(ctx: Context) -> ModelCatalogService:
    return ModelCatalogService(ctx.lifespan_context["model_catalog"])


def _analysis_service(ctx: Context) -> AnalysisService:
    return AnalysisService(
        catalog=ctx.lifespan_context["model_catalog"],
        result_cache=ctx.lifespan_context["result_cache"],
    )


def _optimization_service(ctx: Context) -> OptimizationService:
    return OptimizationService(
        catalog=ctx.lifespan_context["model_catalog"],
        registry=ctx.lifespan_context["optimization_registry"],
        executor=ctx.lifespan_context["optimization_executor"],
        cfg=ctx.lifespan_context["config"],
    )


def register_tools(mcp: FastMCP) -> None:
    """Register all tool handlers on the provided FastMCP server instance."""

    @mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
    async def list_models(ctx: Context) -> list[dict[str, Any]] | dict[str, Any]:
        """List all available Meridian marketing-mix models. Call this first to get model_id values needed by every other tool. Returns id, display_name, format, and last_modified for each model."""
        try:
            return _catalog_service(ctx).list_models()
        except MeridianMcpError as error:
            return _error_response(error)

    @mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
    async def get_model_overview(
        model_id: Annotated[
            str,
            Field(
                min_length=1,
                description="Model identifier from list_models (e.g. 'model-2026-Q1').",
            ),
        ],
        ctx: Context,
    ) -> dict[str, Any]:
        """Get full model metadata including time range, geos, channels, and the valid parameter values for every other tool. Call this after list_models and before analysis tools. The response includes an 'available_tool_options' section that maps each tool name to its accepted 'output_type' or 'dataset' enum values."""
        try:
            return _analysis_service(ctx).get_model_overview(model_id)
        except MeridianMcpError as error:
            return _error_response(error)

    @mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
    async def get_training_data(
        model_id: Annotated[
            str,
            Field(
                min_length=1,
                description="Model identifier from list_models (e.g. 'geo-revenue').",
            ),
        ],
        dataset: Annotated[
            list[TrainingDataset],
            Field(
                min_length=1,
                description="One or more training-data tables to retrieve and merge. Common values: 'kpi' (target metric), 'media' (media impressions), 'media_spend' (spend by channel), 'controls' (control variables). Check get_model_overview 'available_tool_options.get_training_data.dataset' for the full list available on this model.",
            ),
        ],
        ctx: Context,
        filters: Annotated[
            AnalysisFilters | None,
            Field(
                description="Optional filters to slice the data by date range, geos, or channels before returning.",
            ),
        ] = None,
    ) -> dict[str, Any]:
        """Retrieve raw input datasets by name (e.g. 'media_spend', 'kpi', 'controls', 'population') merged into one table — including non-channel series. Use when you want a specific dataset as stored. To investigate a channel's full picture across types, use get_channel_data instead."""
        try:
            return _analysis_service(ctx).get_training_data(
                model_id,
                dataset,
                normalize_filters(filters),
            )
        except MeridianMcpError as error:
            return _error_response(error)

    @mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
    async def get_channel_summary(
        model_id: Annotated[
            str,
            Field(
                min_length=1,
                description="Model identifier from list_models (e.g. 'geo-revenue').",
            ),
        ],
        output_type: Annotated[
            ChannelSummaryType,
            Field(
                description="Which summary metric to compute. 'paid_summary_metrics': spend, impressions, and KPI lift per channel. 'baseline_summary_metrics': intercept and control contributions. 'roi': return on investment per channel. 'cpik': cost per incremental KPI unit. 'marginal_roi': incremental ROI at current spend. 'marginal_cpik': incremental cost per KPI at current spend.",
            ),
        ],
        ctx: Context,
        filters: Annotated[
            AnalysisFilters | None,
            Field(
                description="Optional filters to restrict results by date range, geos, or channels.",
            ),
        ] = None,
    ) -> dict[str, Any]:
        """Get channel-level performance summaries from the fitted model. Use this to answer questions like 'which channel has the best ROI?', 'what is the baseline contribution?', or 'what is the marginal return on the next dollar of spend?'."""
        try:
            return _analysis_service(ctx).get_channel_summary(
                model_id,
                output_type,
                normalize_filters(filters),
            )
        except MeridianMcpError as error:
            return _error_response(error)

    @mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
    async def get_contribution(
        model_id: Annotated[
            str,
            Field(
                min_length=1,
                description="Model identifier from list_models (e.g. 'geo-revenue').",
            ),
        ],
        output_type: Annotated[
            ContributionType,
            Field(
                description="Which contribution view to compute. 'contribution_metrics': total KPI contribution per channel (aggregated). 'contribution_metrics_by_time': contribution broken down by time period, useful for trend analysis.",
            ),
        ],
        ctx: Context,
        filters: Annotated[
            AnalysisFilters | None,
            Field(
                description="Optional filters to restrict results by date range, geos, or channels. Set 'include_non_paid' to true to include organic and non-media channels.",
            ),
        ] = None,
    ) -> dict[str, Any]:
        """Get how much each media channel contributed to the KPI. Use this to answer 'what share of conversions did each channel drive?' or 'how did channel contributions change over time?'."""
        try:
            return _analysis_service(ctx).get_contribution(
                model_id,
                output_type,
                normalize_filters(filters),
            )
        except MeridianMcpError as error:
            return _error_response(error)

    @mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
    async def get_adstock_decay(
        model_id: Annotated[
            str,
            Field(
                min_length=1,
                description="Model identifier from list_models (e.g. 'geo-revenue').",
            ),
        ],
        output_type: Annotated[
            ResponseDynamicsType,
            Field(
                description="Which adstock metric to compute. 'adstock_decay': how media effect decays over time after exposure (carryover curves per channel). 'alpha_summary': the shape parameter controlling how quickly diminishing returns set in per channel.",
            ),
        ],
        ctx: Context,
        filters: Annotated[
            AnalysisFilters | None,
            Field(
                description="Optional filters. Only 'channels' is commonly used here to restrict to specific media channels.",
            ),
        ] = None,
    ) -> dict[str, Any]:
        """Get media carryover (adstock) dynamics — how long a channel's effect persists after exposure. Use this to answer 'how quickly does TV advertising effect decay?' or 'which channels have the longest-lasting impact?'."""
        try:
            return _analysis_service(ctx).get_adstock_decay(
                model_id,
                output_type,
                normalize_filters(filters),
            )
        except MeridianMcpError as error:
            return _error_response(error)

    @mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
    async def get_response_curves(
        model_id: Annotated[
            str,
            Field(
                min_length=1,
                description="Model identifier from list_models (e.g. 'geo-revenue').",
            ),
        ],
        output_type: Annotated[
            ResponseCurveType,
            Field(
                description="Which response-curve output to compute. 'response_curves': full spend-vs-KPI curves per channel at multiple spend multipliers (for plotting). 'response_curve_summary': a concise table with spend, mean response, and confidence intervals per channel.",
            ),
        ],
        ctx: Context,
        filters: Annotated[
            AnalysisFilters | None,
            Field(
                description="Optional filters to restrict results by date range, geos, or channels.",
            ),
        ] = None,
    ) -> dict[str, Any]:
        """Get the spend-response relationship for each channel — how KPI changes as spend increases or decreases. Use this to answer 'what happens if we double search spend?' or 'which channels show diminishing returns?'."""
        try:
            return _analysis_service(ctx).get_response_curves(
                model_id,
                output_type,
                normalize_filters(filters),
            )
        except MeridianMcpError as error:
            return _error_response(error)

    @mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
    async def get_reach_frequency(
        model_id: Annotated[
            str,
            Field(
                min_length=1,
                description="Model identifier from list_models (e.g. 'geo-revenue').",
            ),
        ],
        ctx: Context,
        filters: Annotated[
            AnalysisFilters | None,
            Field(
                description="Optional filters to restrict by date range, geos, or RF channels.",
            ),
        ] = None,
    ) -> dict[str, Any]:
        """Get optimal-frequency analysis for reach & frequency channels: expected ROI across weekly frequency levels plus the optimal frequency per channel. Only available for models with reach & frequency data."""
        try:
            return _analysis_service(ctx).get_reach_frequency(
                model_id,
                normalize_filters(filters),
            )
        except MeridianMcpError as error:
            return _error_response(error)

    @mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
    async def get_channel_data(
        model_id: Annotated[
            str,
            Field(
                min_length=1,
                description="Model identifier from list_models (e.g. 'geo-revenue').",
            ),
        ],
        ctx: Context,
        filters: Annotated[
            AnalysisFilters | None,
            Field(
                description="Optional filters to restrict by date range, geos, or channels.",
            ),
        ] = None,
    ) -> dict[str, Any]:
        """Everything about a channel in one table — spend, impressions, reach/frequency — across all channel types (paid media, RF, organic, non-media). Use to investigate one or more channels directly. For raw datasets by name (including non-channel series like KPI or controls), use get_training_data instead."""
        try:
            return _analysis_service(ctx).get_channel_data(
                model_id,
                normalize_filters(filters),
            )
        except MeridianMcpError as error:
            return _error_response(error)

    @mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
    async def get_model_fit(
        model_id: Annotated[
            str,
            Field(
                min_length=1,
                description="Model identifier from list_models (e.g. 'geo-revenue').",
            ),
        ],
        ctx: Context,
        filters: Annotated[
            AnalysisFilters | None,
            Field(
                description="Optional filters: start_date/end_date slice the time range; geos restricts which markets are included before results are aggregated to one national series (per-geo breakdown is not returned).",
            ),
        ] = None,
    ) -> dict[str, Any]:
        """Get model fit over time: expected vs actual outcome, baseline, and residual (actual - expected) per time period, with confidence intervals. Pass a 'geos' filter to fit only selected markets (aggregated to one series). Use this to judge how well the model tracks observed outcomes."""
        try:
            return _analysis_service(ctx).get_model_fit(
                model_id,
                normalize_filters(filters),
            )
        except MeridianMcpError as error:
            return _error_response(error)

    @mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
    async def get_spend_scenario(
        model_id: Annotated[
            str,
            Field(
                min_length=1,
                description="Model identifier from list_models (e.g. 'geo-revenue').",
            ),
        ],
        channel: Annotated[
            str,
            Field(
                min_length=1,
                description="A single paid-media or RF channel to simulate. Valid values are in get_model_overview 'available_tool_options.get_spend_scenario.channel'.",
            ),
        ],
        spend_increase: Annotated[
            float,
            Field(
                ge=0,
                description="Extra spend PER TIME UNIT to add on top of base spend. Use 0 to get base-only efficiency.",
            ),
        ],
        ctx: Context,
        base_spend: Annotated[
            float | None,
            Field(
                gt=0,
                description="Base spend PER TIME UNIT for the channel. Omit to default to the channel's historical average over the selected date/geo slice.",
            ),
        ] = None,
        filters: Annotated[
            AnalysisFilters | None,
            Field(
                description="Optional filters: start_date/end_date/geos slice the model; use_kpi selects the efficiency family (defaults to the model's capability).",
            ),
        ] = None,
    ) -> dict[str, Any]:
        """Simulate adding spend to one channel: returns expected outcome lift and efficiency (ROI/mROI for revenue models, CPIK/mCPIK otherwise) at the base and increased spend levels. Spend is PER TIME UNIT. Use this to answer 'what happens to ROI if I add $X per week to search?'."""
        try:
            return _analysis_service(ctx).get_spend_scenario(
                model_id,
                channel,
                spend_increase,
                base_spend,
                normalize_filters(filters),
            )
        except MeridianMcpError as error:
            return _error_response(error)

    @mcp.tool
    async def run_optimization(
        model_id: Annotated[
            str, Field(min_length=1, description="Model identifier from list_models.")
        ],
        config: Annotated[
            OptimizationConfig,
            Field(
                description=(
                    "Optimization scenario + constraints. scenario is one of "
                    "{type:'fixed_budget', budget?} | {type:'target_roas', target_value} | "
                    "{type:'target_mroas', target_value}. constraint is "
                    "{mode:'global', pct} or {mode:'per_channel', bounds:{channel:{lower_pct,upper_pct}}}. "
                    "Optional start_date/end_date (ISO), selected_geos, use_kpi. "
                    "See get_model_overview.available_tool_options.run_optimization for valid channels/geos."
                )
            ),
        ],
        ctx: Context,
        label: Annotated[
            str | None, Field(description="Human label for this run.")
        ] = None,
        note: Annotated[
            str | None, Field(description="Free-text intent for this run.")
        ] = None,
        compute_tier: Annotated[
            str, Field(description="auto | local | cloud_cpu | cloud_gpu.")
        ] = "auto",
        force_rerun: Annotated[
            bool, Field(description="Recompute even if an identical run exists.")
        ] = False,
    ) -> dict[str, Any]:
        """Start a budget optimization (long-running). Returns a run_id immediately; poll get_optimization_status, then get_optimization_result. Reuses an identical prior run unless force_rerun is set."""
        try:
            return _optimization_service(ctx).run_optimization(
                model_id,
                config.model_dump(mode="json"),
                label=label,
                note=note,
                compute_tier=compute_tier,
                force_rerun=force_rerun,
            )
        except MeridianMcpError as error:
            return _error_response(error)

    @mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
    async def get_optimization_status(
        run_id: Annotated[
            str, Field(min_length=1, description="run_id from run_optimization.")
        ],
        ctx: Context,
    ) -> dict[str, Any]:
        """Poll an optimization run: status (queued/running/completed/failed), phase, heartbeat, elapsed time, and error if any."""
        try:
            return _optimization_service(ctx).get_status(run_id)
        except MeridianMcpError as error:
            return _error_response(error)

    @mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
    async def get_optimization_result(
        run_id: Annotated[
            str, Field(min_length=1, description="run_id from run_optimization.")
        ],
        ctx: Context,
    ) -> dict[str, Any]:
        """Fetch the full structured optimization result. Errors with optimization_not_ready until the run is completed."""
        try:
            return _optimization_service(ctx).get_result(run_id)
        except MeridianMcpError as error:
            return _error_response(error)

    @mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
    async def list_optimizations(
        ctx: Context,
        model_id: Annotated[
            str | None, Field(description="Filter to one model.")
        ] = None,
        status: Annotated[
            str | None,
            Field(
                description="Filter by status: queued/running/completed/failed/canceled."
            ),
        ] = None,
        limit: Annotated[
            int | None, Field(ge=1, description="Max runs to return (newest first).")
        ] = None,
    ) -> dict[str, Any]:
        """List past optimization runs with their config summary, status, and headline result. Use to find and reuse prior work."""
        try:
            return _optimization_service(ctx).list_runs(
                model_id=model_id, status=status, limit=limit
            )
        except MeridianMcpError as error:
            return _error_response(error)

    @mcp.tool
    async def delete_optimization(
        run_id: Annotated[str, Field(min_length=1, description="run_id to delete.")],
        ctx: Context,
    ) -> dict[str, Any]:
        """Permanently delete one optimization run and its result from the registry."""
        try:
            return _optimization_service(ctx).delete(run_id)
        except MeridianMcpError as error:
            return _error_response(error)

    @mcp.tool
    async def cancel_optimization(
        run_id: Annotated[str, Field(min_length=1, description="run_id to cancel.")],
        ctx: Context,
    ) -> dict[str, Any]:
        """Best-effort cancel of a queued or running optimization run."""
        try:
            return _optimization_service(ctx).cancel(run_id)
        except MeridianMcpError as error:
            return _error_response(error)

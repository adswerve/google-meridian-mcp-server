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
from google_meridian_mcp_server.services.analysis_service import AnalysisService
from google_meridian_mcp_server.services.model_catalog_service import (
    ModelCatalogService,
)

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
                description="Optional filters. Only start_date/end_date apply here; results are aggregated across all geos.",
            ),
        ] = None,
    ) -> dict[str, Any]:
        """Get model fit over time: expected vs actual outcome, baseline, and residual (actual - expected) per time period, with confidence intervals. Use this to judge how well the model tracks observed outcomes."""
        try:
            return _analysis_service(ctx).get_model_fit(
                model_id,
                normalize_filters(filters),
            )
        except MeridianMcpError as error:
            return _error_response(error)

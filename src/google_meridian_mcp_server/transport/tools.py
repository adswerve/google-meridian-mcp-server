"""MCP tool definitions registered onto the FastMCP server instance."""

from __future__ import annotations

import asyncio
import functools
import logging
from typing import Annotated, Any, Literal

from fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from google_meridian_mcp_server.domain.errors import InternalError, MeridianMcpError
from google_meridian_mcp_server.domain.filters import (
    AnalysisFilters,
    ChannelSummaryType,
    ContributionType,
    ResponseCurveType,
    ResponseDynamicsType,
    TrainingDataset,
)
from google_meridian_mcp_server.domain.optimization import (
    FutureOptimizationConfig,
    OptimizationConfig,
)
from google_meridian_mcp_server.services.analysis_service import AnalysisService
from google_meridian_mcp_server.services.model_catalog_service import (
    ModelCatalogService,
)
from google_meridian_mcp_server.services.optimization_service import OptimizationService

log = logging.getLogger(__name__)

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


def _guarded(fn):
    """Tool-surface catch-all: no handler may ever leak a raw exception.

    A ``MeridianMcpError`` becomes its own error envelope (same behavior the
    per-handler ``try/except MeridianMcpError`` blocks used to provide).
    Anything else (a bug, an OS error that slipped past a lower layer, etc.)
    becomes an ``internal_error`` envelope instead of an uncaught exception
    escaping into the MCP transport for a completely unrelated tool call.
    ``functools.wraps`` preserves ``__wrapped__`` so FastMCP's schema
    introspection (``inspect.signature``) still sees the original handler's
    parameters/annotations, not ``(*args, **kwargs)``.
    """

    @functools.wraps(fn)
    async def _wrapped(*args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            return await fn(*args, **kwargs)
        except MeridianMcpError as error:
            return _error_response(error)
        except Exception as exc:  # noqa: BLE001 - tool-surface catch-all
            # R2: MeridianMcpError above is a normal domain outcome and stays
            # unlogged; anything landing here is unexpected, so log it
            # server-side before converting it to the internal_error envelope
            # -- otherwise the operator gets no signal at all.
            log.exception("unhandled error in tool handler")
            return _error_response(InternalError(f"{type(exc).__name__}: {exc}"))

    return _wrapped


def _catalog_service(ctx: Context) -> ModelCatalogService:
    return ModelCatalogService(ctx.lifespan_context["discovery_cache"])


def _analysis_service(ctx: Context) -> AnalysisService:
    return AnalysisService(
        runner=ctx.lifespan_context["analysis_runner"],
        result_cache=ctx.lifespan_context["result_cache"],
    )


def _optimization_service(ctx: Context) -> OptimizationService:
    return OptimizationService(
        runner=ctx.lifespan_context["analysis_runner"],
        registry=ctx.lifespan_context["optimization_registry"],
        executor=ctx.lifespan_context["optimization_executor"],
        cfg=ctx.lifespan_context["config"],
        result_cache=ctx.lifespan_context["result_cache"],
    )


def register_tools(mcp: FastMCP) -> None:
    """Register all tool handlers on the provided FastMCP server instance."""

    @mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
    @_guarded
    async def list_models(ctx: Context) -> list[dict[str, Any]] | dict[str, Any]:
        """List all available Meridian marketing-mix models. Call this first to get model_id values needed by every other tool. Returns id, display_name, format, and last_modified for each model."""
        # F6: list_models does discovery I/O (local fs walk or GCS list) synchronously;
        # offload to a thread so a slow/degraded backend can't stall the event loop
        # (and therefore every other in-flight tool call) while this resolves.
        service = _catalog_service(ctx)
        return await asyncio.to_thread(service.list_models)

    @mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
    @_guarded
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
        return await _analysis_service(ctx).get_model_overview(model_id)

    @mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
    @_guarded
    async def get_training_data(
        model_id: Annotated[
            str,
            Field(
                min_length=1,
                description="Model identifier from list_models (e.g. 'model-2026-Q1').",
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
        """Retrieve raw input datasets by name (e.g. 'media_spend', 'kpi', 'controls', 'population') merged into one table — including non-channel series. Use when you want a specific dataset as stored. To investigate a channel's full picture across types, use get_channel_data instead. On a large model, asking for every dataset at once with no date range can come back empty when talking to a deployed server — ask for just the dataset(s) you actually need, or narrow the date range with filters, and it will come back reliably."""
        return await _analysis_service(ctx).get_training_data(
            model_id,
            dataset,
            filters,
        )

    @mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
    @_guarded
    async def get_channel_summary(
        model_id: Annotated[
            str,
            Field(
                min_length=1,
                description="Model identifier from list_models (e.g. 'model-2026-Q1').",
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
        return await _analysis_service(ctx).get_channel_summary(
            model_id,
            output_type,
            filters,
        )

    @mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
    @_guarded
    async def get_contribution(
        model_id: Annotated[
            str,
            Field(
                min_length=1,
                description="Model identifier from list_models (e.g. 'model-2026-Q1').",
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
        return await _analysis_service(ctx).get_contribution(
            model_id,
            output_type,
            filters,
        )

    @mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
    @_guarded
    async def get_adstock_decay(
        model_id: Annotated[
            str,
            Field(
                min_length=1,
                description="Model identifier from list_models (e.g. 'model-2026-Q1').",
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
                description="Optional filters. Only 'channels' is honored. Adstock decay and alpha are national, time-invariant posterior parameters, so date-range and geo filters cannot apply -- if you pass them they come back listed in 'ignored_filters'.",
            ),
        ] = None,
    ) -> dict[str, Any]:
        """Get media carryover (adstock) dynamics — how long a channel's effect persists after exposure. Use this to answer 'how quickly does TV advertising effect decay?' or 'which channels have the longest-lasting impact?'."""
        return await _analysis_service(ctx).get_adstock_decay(
            model_id,
            output_type,
            filters,
        )

    @mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
    @_guarded
    async def get_response_curves(
        model_id: Annotated[
            str,
            Field(
                min_length=1,
                description="Model identifier from list_models (e.g. 'model-2026-Q1').",
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
        return await _analysis_service(ctx).get_response_curves(
            model_id,
            output_type,
            filters,
        )

    @mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
    @_guarded
    async def get_reach_frequency(
        model_id: Annotated[
            str,
            Field(
                min_length=1,
                description="Model identifier from list_models (e.g. 'model-2026-Q1').",
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
        return await _analysis_service(ctx).get_reach_frequency(
            model_id,
            filters,
        )

    @mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
    @_guarded
    async def get_channel_data(
        model_id: Annotated[
            str,
            Field(
                min_length=1,
                description="Model identifier from list_models (e.g. 'model-2026-Q1').",
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
        return await _analysis_service(ctx).get_channel_data(
            model_id,
            filters,
        )

    @mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
    @_guarded
    async def get_model_fit(
        model_id: Annotated[
            str,
            Field(
                min_length=1,
                description="Model identifier from list_models (e.g. 'model-2026-Q1').",
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
        return await _analysis_service(ctx).get_model_fit(
            model_id,
            filters,
        )

    @mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
    @_guarded
    async def get_spend_scenario(
        model_id: Annotated[
            str,
            Field(
                min_length=1,
                description="Model identifier from list_models (e.g. 'model-2026-Q1').",
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
                description="Optional filters: start_date/end_date/geos slice the model; use_kpi selects the efficiency family (defaults to the model's capability). The channel is chosen by the 'channel' argument, so the 'channels' filter is not read and comes back in 'ignored_filters' if passed.",
            ),
        ] = None,
    ) -> dict[str, Any]:
        """Simulate adding spend to one channel: returns expected outcome lift and efficiency (ROI/mROI for revenue models, CPIK/mCPIK otherwise) at the base and increased spend levels. Spend is PER TIME UNIT. Use this to answer 'what happens to ROI if I add $X per week to search?'."""
        return await _analysis_service(ctx).get_spend_scenario(
            model_id,
            channel,
            spend_increase,
            base_spend,
            filters,
        )

    @mcp.tool
    @_guarded
    async def run_optimization(
        model_id: Annotated[
            str,
            Field(
                min_length=1,
                description="Model identifier from list_models (e.g. 'model-2026-Q1').",
            ),
        ],
        config: Annotated[
            OptimizationConfig,
            Field(
                description=(
                    "Optimization scenario + constraints (see the nested field "
                    "descriptions for details). scenario is one of "
                    "{type:'fixed_budget', budget?} | {type:'target_roas', target_value} | "
                    "{type:'target_mroas', target_value}. constraint is "
                    "{mode:'global', pct} or {mode:'per_channel', bounds:{channel:{lower_pct,upper_pct}}}. "
                    "Optional start_date/end_date, selected_geos, use_kpi. "
                    "Valid channels/geos: get_model_overview.available_tool_options.run_optimization."
                )
            ),
        ],
        ctx: Context,
        label: Annotated[
            str | None,
            Field(
                description="Human-readable label for this run, shown by "
                "list_optimizations. Omit to auto-generate one from the model and "
                "scenario type.",
            ),
        ] = None,
        note: Annotated[
            str | None,
            Field(
                description="Optional free-text describing the intent of this run; "
                "stored with the run. Omit to leave unset.",
            ),
        ] = None,
        compute_tier: Annotated[
            Literal["auto", "local", "cloud_cpu", "cloud_gpu"],
            Field(
                description="Where to run the optimization. 'auto' (default) picks "
                "the cheapest allowed backend from the problem size; 'local' runs "
                "in a subprocess on the server host; 'cloud_cpu'/'cloud_gpu' dispatch "
                "a Cloud Run Job (only if the server enables those tiers).",
            ),
        ] = "auto",
        force_rerun: Annotated[
            bool,
            Field(
                description="Set true to force a fresh computation even when an "
                "identical prior run (same model + config) exists; default false "
                "reuses that run's result.",
            ),
        ] = False,
    ) -> dict[str, Any]:
        """Optimize how budget is split across paid-media & RF channels. Answers "how should I reallocate spend?" or "what mix best hits a 2x ROAS target?". Supply a scenario (fixed_budget | target_roas | target_mroas) and spend constraints via `config`. Long-running: returns a run_id immediately — then poll get_optimization_status until status is 'completed', then read get_optimization_result. An identical prior run (same model + config) is reused unless force_rerun=true; browse prior runs with list_optimizations."""
        return await _optimization_service(ctx).run_optimization(
            model_id,
            config.model_dump(mode="json"),
            label=label,
            note=note,
            compute_tier=compute_tier,
            force_rerun=force_rerun,
        )

    @mcp.tool
    @_guarded
    async def run_future_optimization(
        model_id: Annotated[
            str, Field(min_length=1, description="Model identifier from list_models.")
        ],
        config: Annotated[
            FutureOptimizationConfig,
            Field(
                description=(
                    "Future optimization: same scenario + constraint as run_optimization, PLUS a "
                    "`future` block {start_date, horizon, reference, cost_multipliers?, "
                    "revenue_per_kpi_multiplier?, planned_allocation?}. start_date is the first future "
                    "period (after the model's last training date); horizon is the number of periods at "
                    "the model's cadence. reference selects the historical window carried forward for "
                    "cost/flighting/revenue/default-budget: {mode:'trailing'|'same_period_last_year'|"
                    "'full_history_average'}. cost_multipliers scale per-channel cost-per-media-unit; "
                    "planned_allocation sets your planned mix (partial dicts are normalized). "
                    "Set `future.excluded_channels` to a list of channels to fully pause (spend "
                    "forced to 0; their budget reallocates across the rest, total unchanged). "
                    "excluded_channels also exempts a channel from the reference-window "
                    "cost-basis requirement, so a dark channel can be excluded instead of "
                    "forcing a different reference window. "
                    "Valid channels/geos: get_model_overview.available_tool_options.run_optimization."
                )
            ),
        ],
        ctx: Context,
        label: Annotated[
            str | None,
            Field(description="Human-readable label; omit to auto-generate."),
        ] = None,
        note: Annotated[
            str | None,
            Field(description="Optional free-text intent; stored with the run."),
        ] = None,
        compute_tier: Annotated[
            Literal["auto", "local", "cloud_cpu", "cloud_gpu"],
            Field(
                description="Where to run; 'auto' (default) picks the cheapest allowed backend."
            ),
        ] = "auto",
        force_rerun: Annotated[
            bool,
            Field(
                description="Force fresh computation even if an identical prior run exists."
            ),
        ] = False,
    ) -> dict[str, Any]:
        """Optimize a FUTURE budget under explicit assumptions (not a demand forecast). Answers "how should I split next quarter's budget?" or "if TV CPMs rise 20%, what's the best future mix?". Meridian does not forecast the future: this carries forward a chosen historical reference window's costs/flighting/revenue (optionally scaled by cost_multipliers / revenue_per_kpi_multiplier) and optimizes the allocation over a future window you define with start_date + horizon. Long-running: returns a run_id immediately — poll get_optimization_status until 'completed', then get_optimization_result. Identical prior runs are reused unless force_rerun=true."""
        return await _optimization_service(ctx).run_future_optimization(
            model_id,
            config.model_dump(mode="json"),
            label=label,
            note=note,
            compute_tier=compute_tier,
            force_rerun=force_rerun,
        )

    @mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
    @_guarded
    async def get_optimization_status(
        run_id: Annotated[
            str,
            Field(
                min_length=1,
                description="run_id from run_optimization or run_future_optimization.",
            ),
        ],
        ctx: Context,
    ) -> dict[str, Any]:
        """Poll a run started by run_optimization or run_future_optimization. Returns status (queued/running/completed/failed/canceled), current phase, last heartbeat, elapsed time, and an error object if it failed. Call repeatedly until status is 'completed', then call get_optimization_result."""
        # F6: get_status drives executor.pump(), which can do registry/GCS I/O
        # (and, per live handle, a Cloud Run liveness RPC) synchronously; offload
        # so a slow backend can't block the whole tool surface.
        service = _optimization_service(ctx)
        return await asyncio.to_thread(service.get_status, run_id)

    @mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
    @_guarded
    async def get_optimization_result(
        run_id: Annotated[
            str,
            Field(
                min_length=1,
                description="run_id from run_optimization or run_future_optimization.",
            ),
        ],
        ctx: Context,
    ) -> dict[str, Any]:
        """Fetch the full structured result of a completed optimization: optimized-vs-current spend per channel, expected outcome lift, and per-channel efficiency (ROI/ROAS for revenue models, CPIK otherwise). Raises optimization_not_ready until get_optimization_status reports 'completed'. Answers 'what is the recommended budget allocation?'. Future-optimization results also carry an `assumptions` echo (budget, budget_source, reference_mode, excluded_channels) so an auto-derived budget is never silent."""
        # F6: registry read is synchronous (GCS on that backend); offload.
        service = _optimization_service(ctx)
        return await asyncio.to_thread(service.get_result, run_id)

    @mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
    @_guarded
    async def list_optimizations(
        ctx: Context,
        model_id: Annotated[
            str | None,
            Field(description="Filter to one model (e.g. 'model-2026-Q1')."),
        ] = None,
        status: Annotated[
            Literal["queued", "running", "completed", "failed", "canceled"] | None,
            Field(
                description="Filter to runs in this state. Omit to return all states.",
            ),
        ] = None,
        limit: Annotated[
            int | None, Field(ge=1, description="Max runs to return (newest first).")
        ] = None,
    ) -> dict[str, Any]:
        """List past optimization runs (newest first) with config summary, status, and headline result. Use to find and reuse prior work instead of re-running, or to get a run_id for get_optimization_result / delete_optimization. Filter by model_id and/or status."""
        # F6: registry listing is synchronous (GCS list on that backend); offload.
        service = _optimization_service(ctx)
        return await asyncio.to_thread(
            service.list_runs, model_id=model_id, status=status, limit=limit
        )

    @mcp.tool
    @_guarded
    async def delete_optimization(
        run_id: Annotated[str, Field(min_length=1, description="run_id to delete.")],
        ctx: Context,
    ) -> dict[str, Any]:
        """Permanently delete one optimization run and its stored result by run_id. Irreversible. Find run_ids via list_optimizations. To stop an in-flight run instead, use cancel_optimization."""
        # F6: cancels the executor entry + deletes registry files synchronously; offload.
        service = _optimization_service(ctx)
        return await asyncio.to_thread(service.delete, run_id)

    @mcp.tool
    @_guarded
    async def cancel_optimization(
        run_id: Annotated[str, Field(min_length=1, description="run_id to cancel.")],
        ctx: Context,
    ) -> dict[str, Any]:
        """Best-effort cancel of a queued or running optimization by run_id. Does not remove the run record (use delete_optimization for that) and has no effect on runs that already completed or failed."""
        # F6: terminates the executor handle + writes registry state synchronously; offload.
        service = _optimization_service(ctx)
        return await asyncio.to_thread(service.cancel, run_id)

"""Domain models for the budget optimization module."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Discriminator, Field, Tag, field_validator


class RunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


class RunPhase(str, Enum):
    LOADING_MODEL = "loading_model"
    BUILDING_GRID = "building_grid"
    OPTIMIZING = "optimizing"
    ASSEMBLING_RESULT = "assembling_result"
    UPLOADING = "uploading"


class OutcomeMode(str, Enum):
    REVENUE = "revenue"
    KPI = "kpi"


class FixedBudgetScenario(BaseModel):
    type: Literal["fixed_budget"] = "fixed_budget"
    budget: float | None = Field(
        default=None,
        gt=0,
        description="Total budget across channels for the whole selected range. "
        "Omit to use the model's historical total spend over the range.",
        examples=[1_200_000],
    )


class TargetRoasScenario(BaseModel):
    type: Literal["target_roas"]
    target_value: float = Field(
        gt=0,
        description="Target overall ROAS (revenue per spend). For KPI/no-revenue "
        "models this is read as a CPIK target and inverted internally.",
        examples=[2.0],
    )


class TargetMroasScenario(BaseModel):
    type: Literal["target_mroas"]
    target_value: float = Field(
        gt=0, description="Target marginal ROAS (mROAS).", examples=[1.5]
    )


Scenario = Annotated[
    FixedBudgetScenario | TargetRoasScenario | TargetMroasScenario,
    Field(discriminator="type"),
]


class GlobalConstraint(BaseModel):
    mode: Literal["global"] = "global"
    pct: float = Field(
        ge=0,
        le=1,
        description="Max fractional deviation from current spend applied to every "
        "channel (0.2 = +/-20%).",
        examples=[0.2],
    )


class ChannelBound(BaseModel):
    lower_pct: float = Field(ge=0, le=1)
    upper_pct: float = Field(ge=0, le=1)


class PerChannelConstraint(BaseModel):
    mode: Literal["per_channel"]
    bounds: dict[str, ChannelBound] = Field(
        description="Per-channel lower/upper fractional bounds; must cover every "
        "paid/RF channel. Valid channels: "
        "get_model_overview.available_tool_options.run_optimization.channels.",
    )


Constraint = Annotated[
    GlobalConstraint | PerChannelConstraint, Field(discriminator="mode")
]


class TrailingReference(BaseModel):
    mode: Literal["trailing"] = "trailing"


class SamePeriodLastYearReference(BaseModel):
    mode: Literal["same_period_last_year"]


class FullHistoryAverageReference(BaseModel):
    mode: Literal["full_history_average"]


Reference = Annotated[
    TrailingReference | SamePeriodLastYearReference | FullHistoryAverageReference,
    Field(discriminator="mode"),
]


class BaseOptimizationConfig(BaseModel):
    scenario: Scenario
    constraint: Constraint = Field(default_factory=lambda: GlobalConstraint(pct=0.3))
    selected_geos: list[str] | None = Field(
        default=None,
        description="Subset of geo identifiers to optimize over (e.g. "
        "['US-CA', 'US-NY']). Omit for all geos; ignored by national models. "
        "Valid values: "
        "get_model_overview.available_tool_options.run_optimization.geos.",
    )
    use_kpi: bool | None = Field(
        default=None,
        description="Objective family: false = revenue-based (ROAS/ROI), "
        "true = KPI-based (CPIK). Omit/null to use the model's native objective "
        "(revenue models -> ROAS, no-revenue models -> CPIK).",
    )


class OptimizationConfig(BaseOptimizationConfig):
    kind: Literal["historical"] = "historical"
    start_date: date | None = Field(
        default=None,
        description="Inclusive start date (ISO-8601, e.g. '2023-01-01') of the "
        "window to optimize over. Omit to use the model's full date range.",
    )
    end_date: date | None = Field(
        default=None,
        description="Inclusive end date (ISO-8601, e.g. '2023-12-31') of the "
        "window to optimize over. Omit to use the model's full date range.",
    )


class FutureBlock(BaseModel):
    start_date: date = Field(
        description="First future period (inclusive, ISO-8601). Must be after the "
        "model's last training period.",
        examples=["2026-10-01"],
    )
    horizon: int = Field(
        gt=0,
        description="Number of future periods to plan, at the model's own cadence "
        "(e.g. 13 = 13 weeks for a weekly model).",
        examples=[13],
    )
    reference: Reference = Field(
        default_factory=TrailingReference,
        description="Which historical window seeds carried-forward cost, flighting, "
        "revenue-per-KPI, and default budget. trailing = last `horizon` periods; "
        "same_period_last_year = the `horizon` periods one year before start_date, "
        "which must fall inside the model's data; "
        "full_history_average = average over all training periods.",
    )
    cost_multipliers: dict[str, float] | None = Field(
        default=None,
        description="Optional per-channel multipliers on carried-forward cost per "
        "media unit (1.2 = 20% more expensive; below 1.0 = cheaper). Channels omitted "
        "default to 1.0. Keys must be valid paid/RF channels. Example: {'TV': 1.15}.",
        examples=[{"TV": 1.15}],
    )
    revenue_per_kpi_multiplier: float = Field(
        default=1.0,
        gt=0,
        description="Scales carried-forward revenue-per-KPI (revenue models only; "
        "ignored when the model has no revenue-per-KPI). 1.1 = 10% higher value.",
    )
    planned_allocation: dict[str, float] | None = Field(
        default=None,
        description="Optional planned spend mix as shares of the budget (0.4 = 40%): "
        "the center that spend constraints bound around, and the 'current' baseline "
        "in the result. Naming every non-excluded channel: the shares are scaled to "
        "sum to 1. Naming only some: the named shares are kept exactly as given, and "
        "the rest (1 minus their sum) is split among the channels left out in "
        "proportion to their spend in the reference window. A partial mix whose "
        "shares sum to 1 or more, or whose left-out channels had no spend in the "
        "reference window, is refused (invalid_optimization_config). Excluded "
        "channels get 0 and are not counted as left out. Example: {'TV': 0.4, "
        "'Search': 0.35} leaves 0.25 for the other channels.",
        examples=[{"TV": 0.4, "Search": 0.35, "Social": 0.25}],
    )
    excluded_channels: list[str] | None = Field(
        default=None,
        description="Channels to fully pause/exclude from the future plan (spend "
        "forced to 0; their budget is reallocated across the remaining channels, "
        "total budget unchanged). Keys must be valid paid/RF channels and must NOT "
        "also appear in planned_allocation or cost_multipliers. Cannot exclude every "
        "channel. Example: ['TV'].",
        examples=[["TV"]],
    )

    @field_validator("cost_multipliers", "planned_allocation")
    @classmethod
    def _positive_weights(cls, v):
        if v and any(w <= 0 for w in v.values()):
            raise ValueError("weights must be > 0")
        return v


class FutureOptimizationConfig(BaseOptimizationConfig):
    kind: Literal["future"] = "future"
    future: FutureBlock


def _config_kind(v) -> str:
    if isinstance(v, dict):
        return v.get("kind", "historical")
    return getattr(v, "kind", "historical")


AnyOptimizationConfig = Annotated[
    Annotated[OptimizationConfig, Tag("historical")]
    | Annotated[FutureOptimizationConfig, Tag("future")],
    Discriminator(_config_kind),
]


class OptimizationRun(BaseModel):
    run_id: str
    label: str
    note: str | None = None
    model_id: str
    config: AnyOptimizationConfig
    config_fingerprint: str
    compute_tier_requested: str
    compute_tier_resolved: str
    size_score: int
    created_at: str
    meridian_version: str
    server_version: str
    model_version: str | None = None
    funnel: str = "single"


class OptimizationRunState(BaseModel):
    run_id: str
    status: RunStatus
    phase: RunPhase | None = None
    progress_fraction: float | None = None
    heartbeat_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    error: dict[str, Any] | None = None
    headline: str | None = None


class OptimizationRunDispatch(BaseModel):
    """Executor-owned record of a dispatch attempt.

    Deliberately separate from OptimizationRunState: the worker writes
    state.json independently and write_state is a full-document overwrite, so
    an executor write there can clobber a live RUNNING status. Only the
    executor ever writes this document.
    """

    run_id: str
    claimed_at: str
    execution_name: str | None = None


class OptimizationRunSummary(BaseModel):
    run_id: str
    label: str
    model_id: str
    config_summary: str
    status: RunStatus
    created_at: str
    finished_at: str | None = None
    headline: str | None = None


# Version of the rule that turns a future config's planned_allocation into
# shares. It is added to the run fingerprint of every future config that
# carries planned_allocation, so a run computed under an older rule is never
# reused. Bump it whenever that rule changes meaning.
#   1 (implicit, never hashed): channels left out were filled with their raw
#     reference-window spend in currency and normalised together with the
#     entered shares, which left the named channels almost none of the plan.
#   2: entered shares are kept; the rest is split among the channels left out
#     by reference-window spend (see future_data.normalize_planned_allocation).
# Configs without planned_allocation are not salted and keep their fingerprint.
PLANNED_ALLOCATION_RULE_VERSION = 2


def config_fingerprint(
    model_id: str,
    config: BaseOptimizationConfig,
    *,
    meridian_version: str,
    model_version: str | None = None,
) -> str:
    """Stable, order-insensitive fingerprint of (model_id, config, engine, model bytes).

    D10: the Meridian version enters as a KEYWORD parameter rather than being read here
    (no importlib.metadata in domain/). ``model_version`` (the catalog's hash of every
    stage file's etag) makes a replaced model -- or a replaced mediator -- miss reuse
    instead of silently serving a run computed from the old bytes. A future
    config with planned_allocation also hashes PLANNED_ALLOCATION_RULE_VERSION.
    """
    payload = config.model_dump(mode="json")
    if payload.get("selected_geos"):
        payload["selected_geos"] = sorted(payload["selected_geos"])
    fields = {
        "model_id": model_id,
        "config": payload,
        "meridian_version": meridian_version,
        "model_version": model_version,
    }
    future = getattr(config, "future", None)
    if future is not None and future.planned_allocation is not None:
        fields["planned_allocation_rule"] = PLANNED_ALLOCATION_RULE_VERSION
    raw = json.dumps(fields, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def _invert(value: float) -> float:
    return 1.0 / value


def to_optimize_kwargs(
    config: BaseOptimizationConfig, *, channel_order: list[str], use_kpi: bool
) -> dict[str, Any]:
    """Translate a BaseOptimizationConfig (historical or future) into
    BudgetOptimizer.optimize() kwargs."""
    scenario = config.scenario
    fixed_budget = scenario.type == "fixed_budget"
    budget = scenario.budget if scenario.type == "fixed_budget" else None
    target_roi = None
    target_mroi = None
    if scenario.type == "target_roas":
        target_roi = (
            _invert(scenario.target_value) if use_kpi else scenario.target_value
        )
    elif scenario.type == "target_mroas":
        target_mroi = (
            _invert(scenario.target_value) if use_kpi else scenario.target_value
        )

    constraint = config.constraint
    if constraint.mode == "global":
        spend_lower: float | list[float] = constraint.pct
        spend_upper: float | list[float] = constraint.pct
    else:
        missing = [ch for ch in channel_order if ch not in constraint.bounds]
        if missing:
            raise ValueError(
                f"per_channel constraint is missing bounds for channels: {missing}"
            )
        spend_lower = [constraint.bounds[ch].lower_pct for ch in channel_order]
        spend_upper = [constraint.bounds[ch].upper_pct for ch in channel_order]

    start_date = getattr(config, "start_date", None)
    end_date = getattr(config, "end_date", None)
    return {
        "fixed_budget": fixed_budget,
        "budget": budget,
        "target_roi": target_roi,
        "target_mroi": target_mroi,
        "spend_constraint_lower": spend_lower,
        "spend_constraint_upper": spend_upper,
        "selected_geos": config.selected_geos,
        "start_date": start_date.isoformat() if start_date else None,
        "end_date": end_date.isoformat() if end_date else None,
        "use_kpi": use_kpi,
    }

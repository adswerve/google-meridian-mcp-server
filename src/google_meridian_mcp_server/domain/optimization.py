"""Domain models for the budget optimization module."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field


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
        "paid/RF channel. Valid channels: see get_model_overview.",
    )


Constraint = Annotated[
    GlobalConstraint | PerChannelConstraint, Field(discriminator="mode")
]


class OptimizationConfig(BaseModel):
    scenario: Scenario
    constraint: Constraint = Field(default_factory=lambda: GlobalConstraint(pct=0.3))
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
    selected_geos: list[str] | None = Field(
        default=None,
        description="Subset of geo identifiers to optimize over (e.g. "
        "['US-CA', 'US-NY']). Omit for all geos; ignored by national models. "
        "Valid values: get_model_overview 'geos'.",
    )
    use_kpi: bool | None = Field(
        default=None,
        description="Objective family: false = revenue-based (ROAS/ROI), "
        "true = KPI-based (CPIK). Omit/null to use the model's native objective "
        "(revenue models -> ROAS, no-revenue models -> CPIK).",
    )


class OptimizationRun(BaseModel):
    run_id: str
    label: str
    note: str | None = None
    model_id: str
    config: OptimizationConfig
    config_fingerprint: str
    compute_tier_requested: str
    compute_tier_resolved: str
    backend: str
    size_score: int
    created_at: str
    meridian_version: str
    server_version: str


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


class OptimizationRunSummary(BaseModel):
    run_id: str
    label: str
    model_id: str
    config_summary: str
    status: RunStatus
    created_at: str
    finished_at: str | None = None
    headline: str | None = None


def config_fingerprint(model_id: str, config: OptimizationConfig) -> str:
    """Stable, order-insensitive fingerprint of (model_id, config)."""
    payload = config.model_dump(mode="json")
    if payload.get("selected_geos"):
        payload["selected_geos"] = sorted(payload["selected_geos"])
    raw = json.dumps({"model_id": model_id, "config": payload}, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def _invert(value: float) -> float:
    return 1.0 / value


def to_optimize_kwargs(
    config: OptimizationConfig, *, channel_order: list[str], use_kpi: bool
) -> dict[str, Any]:
    """Translate an OptimizationConfig into BudgetOptimizer.optimize() kwargs."""
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

    return {
        "fixed_budget": fixed_budget,
        "budget": budget,
        "target_roi": target_roi,
        "target_mroi": target_mroi,
        "spend_constraint_lower": spend_lower,
        "spend_constraint_upper": spend_upper,
        "selected_geos": config.selected_geos,
        "start_date": config.start_date.isoformat() if config.start_date else None,
        "end_date": config.end_date.isoformat() if config.end_date else None,
        "use_kpi": use_kpi,
    }

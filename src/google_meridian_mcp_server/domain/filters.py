"""Validated argument schemas shared by analysis tools."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

TrainingDataset = Literal[
    "kpi",
    "revenue_per_kpi",
    "population",
    "media",
    "media_spend",
    "reach",
    "frequency",
    "rf_spend",
    "organic_media",
    "organic_reach",
    "organic_frequency",
    "non_media_treatments",
    "controls",
]

ChannelSummaryType = Literal[
    "baseline_summary_metrics",
    "paid_summary_metrics",
    "roi",
    "cpik",
    "marginal_roi",
    "marginal_cpik",
]

ContributionType = Literal["contribution_metrics", "contribution_metrics_by_time"]

ResponseDynamicsType = Literal["adstock_decay", "alpha_summary"]

ResponseCurveType = Literal["response_curves", "response_curve_summary"]


class AnalysisFilters(BaseModel):
    """Normalized analysis filters accepted by grouped analysis tools."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    start_date: date | None = Field(
        default=None,
        description="Inclusive start date (ISO-8601, e.g. '2023-01-01'). Omit to include from the earliest date in the model.",
    )
    end_date: date | None = Field(
        default=None,
        description="Inclusive end date (ISO-8601, e.g. '2023-12-31'). Omit to include through the latest date in the model.",
    )
    geos: list[str] = Field(
        default_factory=list,
        description="Geo identifiers to include (e.g. ['US-CA', 'US-NY']). Omit or pass [] to include all geos. Valid values are listed in the get_model_overview response under 'geos'.",
    )
    channels: list[str] = Field(
        default_factory=list,
        description="Channel names to include (e.g. ['search', 'tv']). Omit or pass [] to include all channels. Valid values are listed in the get_model_overview response under 'data_inputs'.",
    )
    aggregate_geos: bool = Field(
        default=True,
        description="If true (default), aggregate results across all selected geos into a single row per time period. Set to false to get per-geo breakdowns.",
    )
    aggregate_times: bool = Field(
        default=True,
        description="If true (default), aggregate results across the full time range. Set to false to get per-period rows. Only meaningful for tools that support time breakdowns (e.g. get_contribution with 'contribution_metrics_by_time').",
    )
    include_non_paid: bool | None = Field(
        default=None,
        description="If true, include organic media and non-media treatment channels in the output. Only supported by get_contribution and selected get_channel_summary output types. Omit or null to use the tool default.",
    )
    use_kpi: bool | None = Field(
        default=None,
        description="If true, return KPI-denominated metrics (e.g. incremental KPI) instead of revenue-denominated ones. Only applicable when the model has revenue_per_kpi data. Omit or null to use the tool default.",
    )

    @field_validator("geos", "channels", mode="before")
    @classmethod
    def _normalize_string_list(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str) or not isinstance(value, Iterable):
            raise TypeError("Filter values must be provided as a list of strings.")

        cleaned: list[str] = []
        for item in value:
            if not isinstance(item, str):
                raise TypeError("Filter values must be strings.")
            stripped = item.strip()
            if stripped:
                cleaned.append(stripped)

        return list(dict.fromkeys(cleaned))

    @model_validator(mode="after")
    def _validate_date_range(self) -> "AnalysisFilters":
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("start_date must be on or before end_date")
        return self


def normalize_filters(raw: AnalysisFilters | dict | None) -> AnalysisFilters:
    """Validate and normalize raw filter input into AnalysisFilters."""
    if raw is None:
        return AnalysisFilters()
    if isinstance(raw, AnalysisFilters):
        return raw
    return AnalysisFilters.model_validate(raw)

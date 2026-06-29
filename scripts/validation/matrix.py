"""Declarative variant + expectation matrix for live validation."""

from __future__ import annotations

import dataclasses

ANALYSIS_TOOLS: dict[str, list[str]] = {
    "get_channel_summary": [
        "baseline_summary_metrics",
        "paid_summary_metrics",
        "roi",
        "cpik",
        "marginal_roi",
        "marginal_cpik",
    ],
    "get_contribution": ["contribution_metrics", "contribution_metrics_by_time"],
    "get_adstock_decay": ["adstock_decay", "alpha_summary"],
    "get_response_curves": ["response_curves", "response_curve_summary"],
}

# Output types that require revenue.
REVENUE_ONLY = {"roi", "marginal_roi"}


@dataclasses.dataclass(frozen=True)
class AdversarialCase:
    tool: str
    args: dict
    expected_error_code: str


def expected_valid(variant, tool: str, output_type: str | None) -> bool:
    """Whether (tool, output_type) is expected to return data for this variant."""
    if output_type in REVENUE_ONLY and not variant.factory_has_revenue():
        return False
    return True


def expected_outcome_mode(variant) -> str:
    """Default outcome mode for get_spend_scenario on this variant."""
    return "revenue" if variant.factory_has_revenue() else "kpi"


def adversarial_cases(variant) -> list[AdversarialCase]:
    """Adversarial calls that must return a specific typed error for this variant."""
    cases: list[AdversarialCase] = []
    if not variant.factory_has_revenue():
        for output_type in ("roi", "marginal_roi"):
            cases.append(
                AdversarialCase(
                    "get_channel_summary",
                    {"model_id": variant.key, "output_type": output_type},
                    "metric_not_supported",
                )
            )
    if not variant.with_rf:
        cases.append(
            AdversarialCase(
                "get_reach_frequency",
                {"model_id": variant.key},
                "metric_not_supported",
            )
        )
    cases.append(
        AdversarialCase(
            "get_spend_scenario",
            {
                "model_id": variant.key,
                "channel": "__no_such_channel__",
                "spend_increase": 1.0,
            },
            "missing_model_data",
        )
    )
    cases.append(
        AdversarialCase(
            "get_model_fit",
            {"model_id": variant.key, "filters": {"geos": ["__no_such_geo__"]}},
            "missing_model_data",
        )
    )
    return cases

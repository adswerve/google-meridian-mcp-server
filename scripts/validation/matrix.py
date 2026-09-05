"""Declarative variant + expectation matrix for live validation."""

from __future__ import annotations

import dataclasses
import statistics
from datetime import date, timedelta

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


# ---------------------------------------------------------------------------
# Baseline capture matrix (spec section 7.1)
#
# ToolCase carries exactly the five fields the spec pins down. `surface`
# replaces the first draft's `tiers`: analysis tools run in the Cloud Run
# SERVICE and have no CPU/GPU tier, so only surface="job" cases get the
# Phase 7 tier sweep.
# ---------------------------------------------------------------------------

# generate_validation_models.py declares seven VariantSpecs and then creates an
# EIGHTH fixture as a side effect (:109-115) with no spec of its own. Iterating
# VARIANTS therefore skips it -- and it is the only fixture that exercises the
# loader's pickle branch, which is the entire reason it exists.
PKL_FIXTURE = "national-revenue-pkl"

# Optimization is expensive and its behaviour does not vary by KPI/revenue
# shape in ways the other variants would expose, so -- exactly as
# live_validate already does -- only these two variants carry optimization
# cases. The pickle fixture is a byte-duplicate posterior of national-revenue;
# optimizing it again would buy nothing.
OPTIMIZATION_VARIANTS = frozenset({"national-revenue", "geo-revenue"})

FAR_FUTURE = "2099-01-01"


@dataclasses.dataclass(frozen=True)
class ToolCase:
    tool: str
    name: str  # stable slug -> snapshot filename
    args: dict  # per-variant placeholders resolved at build time
    requires: frozenset[str]  # {"revenue"}, {"rf"}, {"geo"} -> capability gate
    surface: str  # "service" | "job"


def fixture_specs():
    """Every fixture DIRECTORY, not every VariantSpec.

    The pickle fixture is a serialization of the fitted `national-revenue`
    model, so it has the same capabilities; it is excluded from optimization
    only because running the same posterior through the optimizer twice adds
    no coverage.
    """
    from scripts.generate_validation_models import VARIANTS, VariantSpec

    specs = list(VARIANTS)
    specs.append(VariantSpec(PKL_FIXTURE, "revenue", 1, True))
    return specs


def variant_capabilities(variant) -> frozenset[str]:
    """Which gated capabilities this fixture actually has."""
    caps = set()
    if variant.factory_has_revenue():
        caps.add("revenue")
    if variant.with_rf:
        caps.add("rf")
    if variant.n_geos > 1:
        caps.add("geo")
    if variant.key in OPTIMIZATION_VARIANTS:
        caps.add("optimize")
    return frozenset(caps)


def _next_period(overview: dict) -> str:
    """First period after the model's last training period, at its own cadence.

    ``FutureBlock.start_date`` must be after training data ends, and the
    ``trailing`` / ``same_period_last_year`` reference windows must still land
    inside history -- so "one cadence past the end" is the only future date
    that satisfies all three. Same derivation as
    ``scripts/qa/future_optimization_qa.py::compute_cadence_and_next_period``;
    duplicated rather than imported so the validation matrix does not depend
    on the QA gate.
    """
    values = sorted(
        date.fromisoformat(str(value)[:10]) for value in overview["time"]["values"]
    )
    cadence = int(statistics.median([(b - a).days for a, b in zip(values, values[1:])]))
    last = date.fromisoformat(str(overview["time"]["end"])[:10])
    return (last + timedelta(days=cadence)).isoformat()


def placeholder_context(variant, overview: dict) -> dict[str, object]:
    """Resolve every placeholder token against one variant's real model."""
    times = [str(value)[:10] for value in overview["time"]["values"]]
    channels = overview["media_channels"]
    rf_channels = overview["rf_channels"]
    geos = overview["geo_names"]
    return {
        "$MODEL_ID": variant.key,
        "$FIRST_CHANNEL": channels[0],
        "$RF_CHANNEL": rf_channels[0] if rf_channels else channels[0],
        "$FIRST_GEO": geos[0],
        "$START_DATE": times[0],
        "$MID_DATE": times[len(times) // 2],
        "$END_DATE": times[-1],
        "$NEXT_PERIOD": _next_period(overview),
        "$FAR_FUTURE": FAR_FUTURE,
    }


def resolve_placeholders(value, context: dict):
    """Recursively swap placeholder tokens for their per-variant values.

    KEYS are resolved as well as values: `cost_multipliers` and
    `planned_allocation` are channel-keyed dicts, so the channel placeholder
    appears as a key rather than a value.
    """
    if isinstance(value, str):
        return context.get(value, value)
    if isinstance(value, dict):
        return {
            (context.get(key, key) if isinstance(key, str) else key): (
                resolve_placeholders(item, context)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [resolve_placeholders(item, context) for item in value]
    return value


_NO_REQ: frozenset[str] = frozenset()
_REVENUE = frozenset({"revenue"})
_RF = frozenset({"rf"})
_GEO = frozenset({"geo"})
_OPT = frozenset({"optimize"})
_OPT_GEO = frozenset({"optimize", "geo"})
_OPT_REVENUE = frozenset({"optimize", "revenue"})

_FIXED_BUDGET = {"scenario": {"type": "fixed_budget"}}
_GLOBAL_20 = {"mode": "global", "pct": 0.2}

TOOL_CASES: tuple[ToolCase, ...] = (
    # --- structural, backend-independent -------------------------------
    ToolCase("list_models", "default", {}, _NO_REQ, "service"),
    ToolCase(
        "get_model_overview", "default", {"model_id": "$MODEL_ID"}, _NO_REQ, "service"
    ),
    # --- get_training_data: all / single / date window / geo+channel ----
    ToolCase(
        "get_training_data",
        "all_datasets",
        {
            "model_id": "$MODEL_ID",
            "dataset": ["kpi", "media", "media_spend", "controls", "population"],
        },
        _NO_REQ,
        "service",
    ),
    ToolCase(
        "get_training_data",
        "single_dataset",
        {"model_id": "$MODEL_ID", "dataset": ["media_spend"]},
        _NO_REQ,
        "service",
    ),
    ToolCase(
        "get_training_data",
        "date_window",
        {
            "model_id": "$MODEL_ID",
            "dataset": ["media_spend"],
            "filters": {"start_date": "$MID_DATE", "end_date": "$END_DATE"},
        },
        _NO_REQ,
        "service",
    ),
    ToolCase(
        "get_training_data",
        "geo_and_channel_filter",
        {
            "model_id": "$MODEL_ID",
            "dataset": ["media_spend"],
            "filters": {"geos": ["$FIRST_GEO"], "channels": ["$FIRST_CHANNEL"]},
        },
        _NO_REQ,
        "service",
    ),
    # --- get_channel_data: all / single / date window / geo subset ------
    ToolCase("get_channel_data", "all", {"model_id": "$MODEL_ID"}, _NO_REQ, "service"),
    ToolCase(
        "get_channel_data",
        "single_channel",
        {"model_id": "$MODEL_ID", "filters": {"channels": ["$FIRST_CHANNEL"]}},
        _NO_REQ,
        "service",
    ),
    ToolCase(
        "get_channel_data",
        "date_window",
        {
            "model_id": "$MODEL_ID",
            "filters": {"start_date": "$MID_DATE", "end_date": "$END_DATE"},
        },
        _NO_REQ,
        "service",
    ),
    ToolCase(
        "get_channel_data",
        "geo_subset",
        {"model_id": "$MODEL_ID", "filters": {"geos": ["$FIRST_GEO"]}},
        _GEO,
        "service",
    ),
    # --- get_channel_summary: six output types + window + channel subset -
    ToolCase(
        "get_channel_summary",
        "baseline_summary_metrics",
        {"model_id": "$MODEL_ID", "output_type": "baseline_summary_metrics"},
        _NO_REQ,
        "service",
    ),
    ToolCase(
        "get_channel_summary",
        "paid_summary_metrics",
        {"model_id": "$MODEL_ID", "output_type": "paid_summary_metrics"},
        _NO_REQ,
        "service",
    ),
    ToolCase(
        "get_channel_summary",
        "roi",
        {"model_id": "$MODEL_ID", "output_type": "roi"},
        _REVENUE,
        "service",
    ),
    ToolCase(
        "get_channel_summary",
        "cpik",
        {"model_id": "$MODEL_ID", "output_type": "cpik"},
        _NO_REQ,
        "service",
    ),
    ToolCase(
        "get_channel_summary",
        "marginal_roi",
        {"model_id": "$MODEL_ID", "output_type": "marginal_roi"},
        _REVENUE,
        "service",
    ),
    ToolCase(
        "get_channel_summary",
        "marginal_cpik",
        {"model_id": "$MODEL_ID", "output_type": "marginal_cpik"},
        _NO_REQ,
        "service",
    ),
    ToolCase(
        "get_channel_summary",
        "cpik_date_window",
        {
            "model_id": "$MODEL_ID",
            "output_type": "cpik",
            "filters": {"start_date": "$MID_DATE", "end_date": "$END_DATE"},
        },
        _NO_REQ,
        "service",
    ),
    ToolCase(
        "get_channel_summary",
        "cpik_channel_subset",
        {
            "model_id": "$MODEL_ID",
            "output_type": "cpik",
            "filters": {"channels": ["$FIRST_CHANNEL"]},
        },
        _NO_REQ,
        "service",
    ),
    # --- get_contribution: both output types + time window --------------
    ToolCase(
        "get_contribution",
        "contribution_metrics",
        {"model_id": "$MODEL_ID", "output_type": "contribution_metrics"},
        _NO_REQ,
        "service",
    ),
    ToolCase(
        "get_contribution",
        "contribution_metrics_by_time",
        {"model_id": "$MODEL_ID", "output_type": "contribution_metrics_by_time"},
        _NO_REQ,
        "service",
    ),
    ToolCase(
        "get_contribution",
        "by_time_date_window",
        {
            "model_id": "$MODEL_ID",
            "output_type": "contribution_metrics_by_time",
            "filters": {"start_date": "$MID_DATE", "end_date": "$END_DATE"},
        },
        _NO_REQ,
        "service",
    ),
    # --- get_adstock_decay: both output types ---------------------------
    ToolCase(
        "get_adstock_decay",
        "adstock_decay",
        {"model_id": "$MODEL_ID", "output_type": "adstock_decay"},
        _NO_REQ,
        "service",
    ),
    ToolCase(
        "get_adstock_decay",
        "alpha_summary",
        {"model_id": "$MODEL_ID", "output_type": "alpha_summary"},
        _NO_REQ,
        "service",
    ),
    # --- get_response_curves: both output types + channel subset --------
    # [R2] The spec's earlier "spend-multiplier variation" case was impossible:
    # get_response_curves(model_id, output_type, ctx, filters) has no such
    # argument (transport/tools.py:256).
    ToolCase(
        "get_response_curves",
        "response_curves",
        {"model_id": "$MODEL_ID", "output_type": "response_curves"},
        _NO_REQ,
        "service",
    ),
    ToolCase(
        "get_response_curves",
        "response_curve_summary",
        {"model_id": "$MODEL_ID", "output_type": "response_curve_summary"},
        _NO_REQ,
        "service",
    ),
    ToolCase(
        "get_response_curves",
        "curves_channel_subset",
        {
            "model_id": "$MODEL_ID",
            "output_type": "response_curves",
            "filters": {"channels": ["$FIRST_CHANNEL"]},
        },
        _NO_REQ,
        "service",
    ),
    # --- get_model_fit: default / geo / date window / use_kpi -----------
    # [R2] The spec's earlier "confidence level" case was impossible:
    # get_model_fit(model_id, ctx, filters) has no such argument
    # (transport/tools.py:335) and AnalysisFilters has no such field.
    ToolCase("get_model_fit", "default", {"model_id": "$MODEL_ID"}, _NO_REQ, "service"),
    ToolCase(
        "get_model_fit",
        "geo_filter",
        {"model_id": "$MODEL_ID", "filters": {"geos": ["$FIRST_GEO"]}},
        _GEO,
        "service",
    ),
    ToolCase(
        "get_model_fit",
        "date_window",
        {
            "model_id": "$MODEL_ID",
            "filters": {"start_date": "$MID_DATE", "end_date": "$END_DATE"},
        },
        _NO_REQ,
        "service",
    ),
    ToolCase(
        "get_model_fit",
        "use_kpi",
        {"model_id": "$MODEL_ID", "filters": {"use_kpi": True}},
        _NO_REQ,
        "service",
    ),
    # --- get_reach_frequency: default + date window (RF variants) -------
    ToolCase(
        "get_reach_frequency", "default", {"model_id": "$MODEL_ID"}, _RF, "service"
    ),
    ToolCase(
        "get_reach_frequency",
        "date_window",
        {
            "model_id": "$MODEL_ID",
            "filters": {"start_date": "$MID_DATE", "end_date": "$END_DATE"},
        },
        _RF,
        "service",
    ),
    # --- get_spend_scenario: five here, plus the unknown-channel case that
    # already exists as an AdversarialCase (matrix.py:64-74) -> six total.
    ToolCase(
        "get_spend_scenario",
        "default_base",
        {
            "model_id": "$MODEL_ID",
            "channel": "$FIRST_CHANNEL",
            "spend_increase": 1000.0,
        },
        _NO_REQ,
        "service",
    ),
    ToolCase(
        "get_spend_scenario",
        "explicit_base_spend",
        {
            "model_id": "$MODEL_ID",
            "channel": "$FIRST_CHANNEL",
            "spend_increase": 1000.0,
            "base_spend": 5000.0,
        },
        _NO_REQ,
        "service",
    ),
    ToolCase(
        "get_spend_scenario",
        "large_increase",
        {
            "model_id": "$MODEL_ID",
            "channel": "$FIRST_CHANNEL",
            "spend_increase": 1_000_000_000.0,
        },
        _NO_REQ,
        "service",
    ),
    ToolCase(
        "get_spend_scenario",
        "tiny_increase",
        {"model_id": "$MODEL_ID", "channel": "$FIRST_CHANNEL", "spend_increase": 0.01},
        _NO_REQ,
        "service",
    ),
    # spend_increase=0 makes the marginal ratio's denominator zero; the
    # documented behaviour is a null efficiency, not an error.
    ToolCase(
        "get_spend_scenario",
        "zero_denominator",
        {"model_id": "$MODEL_ID", "channel": "$FIRST_CHANNEL", "spend_increase": 0.0},
        _NO_REQ,
        "service",
    ),
    # --- run_optimization: six cases ------------------------------------
    ToolCase(
        "run_optimization",
        "fixed_budget",
        {
            "model_id": "$MODEL_ID",
            "config": {**_FIXED_BUDGET, "constraint": _GLOBAL_20},
        },
        _OPT,
        "job",
    ),
    ToolCase(
        "run_optimization",
        "target_roas",
        {
            "model_id": "$MODEL_ID",
            "config": {
                "scenario": {"type": "target_roas", "target_value": 2.0},
                "constraint": _GLOBAL_20,
            },
        },
        _OPT,
        "job",
    ),
    ToolCase(
        "run_optimization",
        "target_mroas",
        {
            "model_id": "$MODEL_ID",
            "config": {
                "scenario": {"type": "target_mroas", "target_value": 1.5},
                "constraint": _GLOBAL_20,
            },
        },
        _OPT,
        "job",
    ),
    ToolCase(
        "run_optimization",
        "geo_subset",
        {
            "model_id": "$MODEL_ID",
            "config": {
                **_FIXED_BUDGET,
                "constraint": _GLOBAL_20,
                "selected_geos": ["$FIRST_GEO"],
            },
        },
        _OPT_GEO,
        "job",
    ),
    ToolCase(
        "run_optimization",
        "date_window",
        {
            "model_id": "$MODEL_ID",
            "config": {
                **_FIXED_BUDGET,
                "constraint": _GLOBAL_20,
                "start_date": "$MID_DATE",
                "end_date": "$END_DATE",
            },
        },
        _OPT,
        "job",
    ),
    ToolCase(
        "run_optimization",
        "spend_constraints",
        {
            "model_id": "$MODEL_ID",
            "config": {**_FIXED_BUDGET, "constraint": {"mode": "global", "pct": 0.5}},
        },
        _OPT,
        "job",
    ),
    # --- run_future_optimization: six cases -----------------------------
    ToolCase(
        "run_future_optimization",
        "reference_trailing",
        {
            "model_id": "$MODEL_ID",
            "config": {
                **_FIXED_BUDGET,
                "future": {
                    "start_date": "$NEXT_PERIOD",
                    "horizon": 4,
                    "reference": {"mode": "trailing"},
                },
            },
        },
        _OPT,
        "job",
    ),
    ToolCase(
        "run_future_optimization",
        "reference_same_period_last_year",
        {
            "model_id": "$MODEL_ID",
            "config": {
                **_FIXED_BUDGET,
                "future": {
                    "start_date": "$NEXT_PERIOD",
                    "horizon": 4,
                    "reference": {"mode": "same_period_last_year"},
                },
            },
        },
        _OPT,
        "job",
    ),
    ToolCase(
        "run_future_optimization",
        "reference_full_history_average",
        {
            "model_id": "$MODEL_ID",
            "config": {
                **_FIXED_BUDGET,
                "future": {
                    "start_date": "$FAR_FUTURE",
                    "horizon": 4,
                    "reference": {"mode": "full_history_average"},
                },
            },
        },
        _OPT,
        "job",
    ),
    ToolCase(
        "run_future_optimization",
        "cost_multipliers",
        {
            "model_id": "$MODEL_ID",
            "config": {
                **_FIXED_BUDGET,
                "future": {
                    "start_date": "$NEXT_PERIOD",
                    "horizon": 4,
                    "cost_multipliers": {"$FIRST_CHANNEL": 1.15},
                },
            },
        },
        _OPT,
        "job",
    ),
    ToolCase(
        "run_future_optimization",
        "revenue_per_kpi_multiplier",
        {
            "model_id": "$MODEL_ID",
            "config": {
                **_FIXED_BUDGET,
                "future": {
                    "start_date": "$NEXT_PERIOD",
                    "horizon": 4,
                    "revenue_per_kpi_multiplier": 1.1,
                },
            },
        },
        _OPT_REVENUE,
        "job",
    ),
    ToolCase(
        "run_future_optimization",
        "planned_allocation_long_horizon",
        {
            "model_id": "$MODEL_ID",
            "config": {
                **_FIXED_BUDGET,
                "future": {
                    "start_date": "$NEXT_PERIOD",
                    "horizon": 13,
                    "planned_allocation": {"$FIRST_CHANNEL": 0.5},
                },
            },
        },
        _OPT,
        "job",
    ),
    # --- lifecycle (5 tools across two scenarios) -----------------------
    ToolCase(
        "lifecycle",
        "status_result_reuse_delete",
        {"model_id": "$MODEL_ID"},
        _OPT,
        "job",
    ),
    ToolCase("lifecycle", "cancel", {"model_id": "$MODEL_ID"}, _OPT, "job"),
)


def tool_cases(variant, overview: dict) -> list[ToolCase]:
    """The cases this variant supports, with placeholders resolved."""
    caps = variant_capabilities(variant)
    context = placeholder_context(variant, overview)
    return [
        dataclasses.replace(case, args=resolve_placeholders(case.args, context))
        for case in TOOL_CASES
        if case.requires <= caps
    ]


def adversarial_tool_cases(variant) -> list[ToolCase]:
    """The existing expected-error matrix, expressed as snapshot-able cases.

    Error envelopes are snapshotted, not raised: ``metric_not_supported``
    silently becoming a different code is a breaking change for every
    consuming agent, and invisible to a harness that records only successes.
    """
    return [
        ToolCase(
            tool=case.tool,
            name=f"err_{case.expected_error_code}_{index}",
            args=dict(case.args),
            requires=frozenset(),
            surface="service",
        )
        for index, case in enumerate(adversarial_cases(variant))
    ]

"""Which AnalysisFilters fields each analysis tool can actually honor.

This table is the single source of truth. ``narrow()`` strips inapplicable
filters at the service boundary BEFORE the worker payload is built, so a
facade method physically cannot receive a filter listed here as
inapplicable -- the table cannot drift into under-reporting.

Keyed by ``(tool_name, output_type)``; ``output_type`` is ``None`` for
tools that do not take one. A lookup miss raises: a new tool or output
type cannot ship without registering here.
"""

from __future__ import annotations

from google_meridian_mcp_server.domain.filters import AnalysisFilters

Key = tuple[str, str | None]

ALL_FILTER_FIELDS: frozenset[str] = frozenset(
    {
        "start_date",
        "end_date",
        "geos",
        "channels",
        "aggregate_times",
        "use_kpi",
        "include_non_paid",
    }
)

_DATES_GEOS = ("start_date", "end_date", "geos")
_SUMMARY = (*_DATES_GEOS, "aggregate_times", "use_kpi", "channels")
_CURVE = (*_DATES_GEOS, "use_kpi", "channels")


class UnregisteredAnalysisKey(LookupError):
    """A (tool, output_type) pair has no applicability entry.

    Developer-facing invariant, never reachable by an agent: every service
    method validates ``output_type`` before narrowing, so an unknown output
    type raises ``InvalidOutputTypeError`` first.
    """

    def __init__(self, key: Key) -> None:
        super().__init__(
            f"No applicability entry for {key!r}. Every tool accepting "
            "AnalysisFilters must be registered in FILTER_APPLICABILITY."
        )


FILTER_APPLICABILITY: dict[Key, frozenset[str]] = {
    ("get_channel_summary", "baseline_summary_metrics"): frozenset(
        (*_DATES_GEOS, "aggregate_times", "use_kpi")
    ),
    ("get_channel_summary", "paid_summary_metrics"): frozenset(_SUMMARY),
    ("get_channel_summary", "roi"): frozenset(_SUMMARY),
    ("get_channel_summary", "cpik"): frozenset(_SUMMARY),
    ("get_channel_summary", "marginal_roi"): frozenset(_SUMMARY),
    ("get_channel_summary", "marginal_cpik"): frozenset(_SUMMARY),
    ("get_contribution", "contribution_metrics"): frozenset(
        (*_SUMMARY, "include_non_paid")
    ),
    ("get_contribution", "contribution_metrics_by_time"): frozenset(
        (*_CURVE, "include_non_paid")
    ),
    ("get_adstock_decay", "adstock_decay"): frozenset({"channels"}),
    ("get_adstock_decay", "alpha_summary"): frozenset({"channels"}),
    ("get_response_curves", "response_curves"): frozenset(_CURVE),
    ("get_response_curves", "response_curve_summary"): frozenset(_CURVE),
    ("get_reach_frequency", None): frozenset(_CURVE),
    ("get_model_fit", None): frozenset((*_DATES_GEOS, "use_kpi")),
    ("get_channel_data", None): frozenset((*_DATES_GEOS, "channels")),
    ("get_training_data", None): frozenset((*_DATES_GEOS, "channels")),
    ("get_spend_scenario", None): frozenset((*_DATES_GEOS, "use_kpi")),
}

# Every string must be TRUE UNDER BOTH VALUES of its filter (spec 4): it
# states what the output type delivers, never a bare "ignored". A caller
# who sends include_non_paid=false to a paid-only output gets exactly what
# they asked for -- telling them only that it was "ignored" implies the
# reverse.
_PAID_ONLY = (
    "this output type is always paid-only; organic and non-media channels "
    "are never included, and include_non_paid is not read"
)
_ADSTOCK_TIME = (
    "adstock alpha is a time-invariant posterior parameter, so the date "
    "range is not read"
)
_ADSTOCK_GEO = (
    "adstock alpha is a national posterior parameter with no geo dimension; "
    "the geo selection is not read"
)
_ADSTOCK_AGG = (
    "adstock alpha has no calendar-time dimension; aggregate_times is not read"
)
_ADSTOCK_KPI = (
    "adstock alpha is unitless -- not denominated in revenue or KPI -- "
    "so use_kpi is not read"
)
_ADSTOCK_ORGANIC = (
    "organic media and organic RF channels are always included in this "
    "output; include_non_paid is not read"
)
_ADSTOCK_REASONS = {
    "start_date": _ADSTOCK_TIME,
    "end_date": _ADSTOCK_TIME,
    "geos": _ADSTOCK_GEO,
    "aggregate_times": _ADSTOCK_AGG,
    "use_kpi": _ADSTOCK_KPI,
    "include_non_paid": _ADSTOCK_ORGANIC,
}
_CURVE_REASONS = {
    "aggregate_times": (
        "response curves are always aggregated over the selected window; "
        "aggregate_times is not read"
    ),
    "include_non_paid": (
        "response curves cover paid channels only; include_non_paid is not read"
    ),
}

IGNORED_REASONS: dict[Key, dict[str, str]] = {
    ("get_channel_summary", "baseline_summary_metrics"): {
        "channels": (
            "the baseline has no channel dimension; this output type returns "
            "baseline rows only, so the channel selection is not read"
        ),
        "include_non_paid": (
            "this output type returns only the baseline; channel rows of any "
            "kind are never included, and include_non_paid is not read"
        ),
    },
    ("get_channel_summary", "paid_summary_metrics"): {"include_non_paid": _PAID_ONLY},
    ("get_channel_summary", "roi"): {"include_non_paid": _PAID_ONLY},
    ("get_channel_summary", "cpik"): {"include_non_paid": _PAID_ONLY},
    ("get_channel_summary", "marginal_roi"): {"include_non_paid": _PAID_ONLY},
    ("get_channel_summary", "marginal_cpik"): {"include_non_paid": _PAID_ONLY},
    ("get_contribution", "contribution_metrics"): {},
    ("get_contribution", "contribution_metrics_by_time"): {
        "aggregate_times": (
            "this output type is always per-period; aggregate_times is not read"
        ),
    },
    ("get_adstock_decay", "adstock_decay"): dict(_ADSTOCK_REASONS),
    ("get_adstock_decay", "alpha_summary"): dict(_ADSTOCK_REASONS),
    ("get_response_curves", "response_curves"): dict(_CURVE_REASONS),
    ("get_response_curves", "response_curve_summary"): dict(_CURVE_REASONS),
    ("get_reach_frequency", None): {
        "aggregate_times": (
            "optimal-frequency curves are always aggregated over the selected "
            "window; aggregate_times is not read"
        ),
        "include_non_paid": (
            "this tool covers paid reach & frequency channels only; "
            "include_non_paid is not read"
        ),
    },
    ("get_model_fit", None): {
        "channels": (
            "model fit returns one national time series, not per-channel rows; "
            "the channel selection is not read"
        ),
        "aggregate_times": (
            "model fit is always per-period; aggregate_times is not read"
        ),
        "include_non_paid": (
            "model fit returns expected/actual/baseline series rather than "
            "channel rows; include_non_paid is not read"
        ),
    },
    ("get_channel_data", None): {
        "aggregate_times": (
            "channel data returns raw per-period records; aggregate_times is not read"
        ),
        "use_kpi": (
            "channel data returns raw model inputs, which are not denominated "
            "in revenue or KPI; use_kpi is not read"
        ),
        "include_non_paid": (
            "channel data always stacks every channel-keyed input, organic "
            "included; include_non_paid is not read"
        ),
    },
    ("get_training_data", None): {
        # NOT "always per-period": the population dataset has no time
        # dimension at all (spec 5.3).
        "aggregate_times": (
            "training data returns raw records as stored; aggregate_times is not read"
        ),
        "use_kpi": (
            "training data returns raw model inputs, which are not denominated "
            "in revenue or KPI; use_kpi is not read"
        ),
        "include_non_paid": (
            "the dataset argument selects what is returned; include_non_paid "
            "is not read"
        ),
    },
    ("get_spend_scenario", None): {
        "channels": (
            "the channel is selected by the 'channel' argument; the channels "
            "filter is not read"
        ),
        "aggregate_times": (
            "the scenario is computed per time unit over the selected window; "
            "aggregate_times is not read"
        ),
        "include_non_paid": (
            "the scenario applies to a paid media or RF channel by "
            "construction; include_non_paid is not read"
        ),
    },
}

# Emitted unconditionally for these two output types only: the likeliest
# mislabelling is an agent that passes NO filters, calls adstock while
# writing a Q3 story, and labels the chart "Q3". ignored_filters cannot
# catch that -- nothing was supplied to report.
SCOPE_NOTE: dict[Key, str] = {
    ("get_adstock_decay", "adstock_decay"): "national, full training window",
    ("get_adstock_decay", "alpha_summary"): "national, time-invariant",
}

# Fields with a canonical "no constraint" value. Sending the sentinel asks
# for nothing, so it is never reported. aggregate_times is deliberately
# absent: it is a bare bool defaulting to True, so both values are real
# requests and either one is reported when explicitly sent (spec 4).
_SENTINELS: dict[str, object] = {
    "start_date": None,
    "end_date": None,
    "geos": [],
    "channels": [],
    "use_kpi": None,
    "include_non_paid": None,
}


def _field_default(name: str) -> object:
    """A FRESH schema default -- never a shared mutable."""
    default = AnalysisFilters.model_fields[name].get_default(call_default_factory=True)
    return default


def _is_reportable(filters: AnalysisFilters, field: str) -> bool:
    if field not in filters.model_fields_set:
        return False
    if field not in _SENTINELS:
        return True  # aggregate_times: no sentinel, any explicit send counts
    return getattr(filters, field) != _SENTINELS[field]


def narrow(
    filters: AnalysisFilters, key: Key
) -> tuple[AnalysisFilters, dict[str, str]]:
    """Reduce ``filters`` to what ``key`` can honor, and say what was dropped.

    Returns ``(effective, ignored)``. ``ignored`` maps each reported field
    to its reason and is derived from ``filters`` BEFORE the copy --
    ``model_copy(update=...)`` adds every updated key to
    ``model_fields_set``, so reading it afterwards would report fields the
    caller never sent.
    """
    try:
        applicable = FILTER_APPLICABILITY[key]
    except KeyError:
        raise UnregisteredAnalysisKey(key) from None

    inapplicable = ALL_FILTER_FIELDS - applicable
    if not inapplicable:
        return filters, {}

    reasons = IGNORED_REASONS[key]
    ignored = {
        field: reasons[field]
        for field in sorted(inapplicable)
        if _is_reportable(filters, field)
    }
    effective = filters.model_copy(
        update={field: _field_default(field) for field in inapplicable}
    )
    return effective, ignored


# Leading identity keys, across every envelope shape this server emits.
# The note goes after the last of these and before the payload, so an
# agent reads the caveat before the data it qualifies.
_IDENTITY_KEYS = frozenset(
    {"model_id", "output_type", "dataset", "datasets", "channel", "channel_type"}
)


def insert_note(result: dict, key: Key, ignored: dict[str, str]) -> dict:
    """Return a NEW dict carrying ``scope`` and/or ``ignored_filters``.

    Never mutates ``result``: ``ResultCache.get`` hands back the stored
    object by reference, so mutating it would poison the entry for every
    later caller.
    """
    scope = SCOPE_NOTE.get(key)
    if scope is None and not ignored:
        return result

    note: dict[str, object] = {}
    if scope is not None:
        note["scope"] = scope
    if ignored:
        note["ignored_filters"] = ignored

    out: dict = {}
    placed = False
    for name, value in result.items():
        if not placed and name not in _IDENTITY_KEYS:
            out.update(note)
            placed = True
        out[name] = value
    if not placed:  # envelope was identity keys only
        out.update(note)
    return out

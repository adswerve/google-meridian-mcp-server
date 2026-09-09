"""Unit tests for the filter applicability table and its helpers.

The table is the single source of truth for which AnalysisFilters fields
each (tool, output_type) can honor. Per AGENTS.md, every test here asserts
WHICH filter and WHICH reason -- never merely that a note appeared.
"""

from __future__ import annotations

from google_meridian_mcp_server.domain import applicability as ap
from google_meridian_mcp_server.domain.filters import AnalysisFilters

EXPECTED_APPLICABILITY = {
    ("get_channel_summary", "baseline_summary_metrics"): {
        "start_date",
        "end_date",
        "geos",
        "aggregate_times",
        "use_kpi",
    },
    ("get_channel_summary", "paid_summary_metrics"): {
        "start_date",
        "end_date",
        "geos",
        "aggregate_times",
        "use_kpi",
        "channels",
    },
    ("get_channel_summary", "roi"): {
        "start_date",
        "end_date",
        "geos",
        "aggregate_times",
        "use_kpi",
        "channels",
    },
    ("get_channel_summary", "cpik"): {
        "start_date",
        "end_date",
        "geos",
        "aggregate_times",
        "use_kpi",
        "channels",
    },
    ("get_channel_summary", "marginal_roi"): {
        "start_date",
        "end_date",
        "geos",
        "aggregate_times",
        "use_kpi",
        "channels",
    },
    ("get_channel_summary", "marginal_cpik"): {
        "start_date",
        "end_date",
        "geos",
        "aggregate_times",
        "use_kpi",
        "channels",
    },
    ("get_contribution", "contribution_metrics"): {
        "start_date",
        "end_date",
        "geos",
        "aggregate_times",
        "use_kpi",
        "channels",
        "include_non_paid",
    },
    ("get_contribution", "contribution_metrics_by_time"): {
        "start_date",
        "end_date",
        "geos",
        "use_kpi",
        "channels",
        "include_non_paid",
    },
    ("get_adstock_decay", "adstock_decay"): {"channels"},
    ("get_adstock_decay", "alpha_summary"): {"channels"},
    ("get_response_curves", "response_curves"): {
        "start_date",
        "end_date",
        "geos",
        "use_kpi",
        "channels",
    },
    ("get_response_curves", "response_curve_summary"): {
        "start_date",
        "end_date",
        "geos",
        "use_kpi",
        "channels",
    },
    ("get_reach_frequency", None): {
        "start_date",
        "end_date",
        "geos",
        "use_kpi",
        "channels",
    },
    ("get_model_fit", None): {"start_date", "end_date", "geos", "use_kpi"},
    ("get_channel_data", None): {"start_date", "end_date", "geos", "channels"},
    ("get_training_data", None): {"start_date", "end_date", "geos", "channels"},
    ("get_spend_scenario", None): {"start_date", "end_date", "geos", "use_kpi"},
}


def test_table_has_exactly_the_expected_17_entries():
    assert set(ap.FILTER_APPLICABILITY) == set(EXPECTED_APPLICABILITY)
    assert len(ap.FILTER_APPLICABILITY) == 17


def test_every_entry_declares_the_expected_applicable_fields():
    for key, expected in EXPECTED_APPLICABILITY.items():
        assert set(ap.FILTER_APPLICABILITY[key]) == expected, key


def test_all_filter_fields_matches_the_model():
    assert set(ap.ALL_FILTER_FIELDS) == set(AnalysisFilters.model_fields)


def test_ignored_reasons_covers_exactly_the_complement():
    """Spec 3.1 invariant: totality, as equality -- no gaps, no extras.

    A gap is a KeyError on a live call; an extra means a reason exists for
    a filter the tool actually honors.
    """
    for key, applicable in ap.FILTER_APPLICABILITY.items():
        complement = set(ap.ALL_FILTER_FIELDS) - set(applicable)
        assert set(ap.IGNORED_REASONS[key]) == complement, key


def test_scope_note_only_for_the_two_adstock_outputs():
    assert ap.SCOPE_NOTE == {
        ("get_adstock_decay", "adstock_decay"): "national, full training window",
        ("get_adstock_decay", "alpha_summary"): "national, time-invariant",
    }


def test_no_reason_string_is_a_bare_ignored():
    """Spec 4: every reason states delivered behaviour, true under BOTH values."""
    for key, reasons in ap.IGNORED_REASONS.items():
        for field, text in reasons.items():
            assert len(text) > 30, (key, field, text)
            assert text.strip().lower() != "ignored", (key, field)

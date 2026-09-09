"""Unit tests for the filter applicability table and its helpers.

The table is the single source of truth for which AnalysisFilters fields
each (tool, output_type) can honor. Per AGENTS.md, every test here asserts
WHICH filter and WHICH reason -- never merely that a note appeared.
"""

from __future__ import annotations

from datetime import date

import pytest

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


ADSTOCK = ("get_adstock_decay", "adstock_decay")
BY_TIME = ("get_contribution", "contribution_metrics_by_time")

# Schema defaults -- what a stripped field is reset TO (spec 3.3).
DEFAULTS = {
    "start_date": None,
    "end_date": None,
    "geos": [],
    "channels": [],
    "aggregate_times": True,
    "use_kpi": None,
    "include_non_paid": None,
}


def test_narrow_strips_inapplicable_and_reports_each_with_its_own_reason():
    filters = AnalysisFilters.model_validate(
        {
            "start_date": "2024-07-01",
            "end_date": "2024-09-30",
            "geos": ["US-CA"],
            "channels": ["tv"],
        }
    )
    effective, ignored = ap.narrow(filters, ADSTOCK)

    assert set(ignored) == {"start_date", "end_date", "geos"}
    assert "time-invariant" in ignored["start_date"]
    assert "national" in ignored["geos"]
    assert ignored["start_date"] != ignored["geos"]  # per-filter, not shared

    assert effective.channels == ["tv"]  # applicable: survives
    assert effective.start_date is None  # stripped -> schema default
    assert effective.end_date is None
    assert effective.geos == []


def test_narrow_leaves_applicable_filters_untouched():
    filters = AnalysisFilters.model_validate(
        {"start_date": "2024-07-01", "geos": ["US-CA"], "use_kpi": True}
    )
    effective, ignored = ap.narrow(filters, ("get_model_fit", None))

    assert ignored == {}
    assert effective.start_date == date(2024, 7, 1)
    assert effective.geos == ["US-CA"]
    assert effective.use_kpi is True


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"geos": ["US-CA"]}, {"geos"}),
        ({"geos": []}, set()),  # sentinel: all geos
        ({"start_date": "2024-07-01"}, {"start_date"}),
        ({"start_date": None}, set()),  # sentinel
        ({"end_date": "2024-09-30"}, {"end_date"}),
        ({"use_kpi": True}, {"use_kpi"}),
        ({"use_kpi": False}, {"use_kpi"}),
        ({"use_kpi": None}, set()),  # sentinel -- spec 4 H1 case
        ({"include_non_paid": False}, {"include_non_paid"}),
        ({"include_non_paid": None}, set()),  # sentinel
        ({"aggregate_times": False}, {"aggregate_times"}),
        ({"aggregate_times": True}, {"aggregate_times"}),  # no sentinel
        ({}, set()),  # nothing sent, nothing said
    ],
)
def test_detection_rule_by_field_kind(payload, expected):
    _, ignored = ap.narrow(AnalysisFilters.model_validate(payload), ADSTOCK)
    assert set(ignored) == expected


def test_aggregate_times_true_is_reported_on_by_time():
    """The case a default-based rule wrongly suppressed: the caller asked
    for the aggregated view and gets per-period rows."""
    _, ignored = ap.narrow(
        AnalysisFilters.model_validate({"aggregate_times": True}), BY_TIME
    )
    assert set(ignored) == {"aggregate_times"}
    assert "always per-period" in ignored["aggregate_times"]


def test_detection_is_not_computed_from_the_narrowed_object():
    """model_copy pollutes model_fields_set; reading it after the copy
    would report every stripped field on every call."""
    filters = AnalysisFilters.model_validate({"channels": ["tv"]})
    effective, ignored = ap.narrow(filters, ADSTOCK)
    assert ignored == {}
    assert set(effective.model_fields_set) >= {"channels"}


def test_narrow_does_not_mutate_the_callers_instance():
    filters = AnalysisFilters.model_validate({"geos": ["US-CA"]})
    ap.narrow(filters, ADSTOCK)
    assert filters.geos == ["US-CA"]


def test_unregistered_key_raises():
    with pytest.raises(ap.UnregisteredAnalysisKey):
        ap.narrow(AnalysisFilters(), ("get_new_tool", None))


ALPHA = ("get_adstock_decay", "alpha_summary")
MODEL_FIT = ("get_model_fit", None)
TRAINING = ("get_training_data", None)
SPEND = ("get_spend_scenario", None)


def test_note_lands_after_output_type_on_the_dispatch_shape():
    result = {
        "model_id": "m1",
        "output_type": "adstock_decay",
        "columns": ["channel"],
        "rows": [["tv"]],
        "row_count": 1,
    }
    out = ap.insert_note(result, ADSTOCK, {"geos": "reason text here"})
    assert list(out) == [
        "model_id",
        "output_type",
        "scope",
        "ignored_filters",
        "columns",
        "rows",
        "row_count",
    ]
    assert out["scope"] == "national, full training window"
    assert out["ignored_filters"] == {"geos": "reason text here"}


def test_note_lands_after_model_id_when_there_is_no_output_type():
    result = {"model_id": "m1", "columns": ["time"], "rows": [], "row_count": 0}
    out = ap.insert_note(result, MODEL_FIT, {"channels": "reason text here"})
    assert list(out) == [
        "model_id",
        "ignored_filters",
        "columns",
        "rows",
        "row_count",
    ]
    assert "scope" not in out  # only the two adstock outputs emit scope


def test_note_lands_after_datasets_on_the_training_data_shape():
    result = {
        "model_id": "m1",
        "dataset": "kpi",
        "datasets": ["kpi"],
        "columns": ["time"],
        "rows": [],
        "row_count": 0,
    }
    out = ap.insert_note(result, TRAINING, {"use_kpi": "reason text here"})
    assert list(out) == [
        "model_id",
        "dataset",
        "datasets",
        "ignored_filters",
        "columns",
        "rows",
        "row_count",
    ]


def test_note_lands_after_channel_type_on_the_spend_scenario_shape():
    result = {
        "model_id": "m1",
        "channel": "search",
        "channel_type": "paid_media",
        "outcome_mode": "revenue",
        "base_spend": 1000.0,
    }
    out = ap.insert_note(result, SPEND, {"channels": "reason text here"})
    assert list(out) == [
        "model_id",
        "channel",
        "channel_type",
        "ignored_filters",
        "outcome_mode",
        "base_spend",
    ]


def test_scope_is_emitted_even_with_nothing_ignored():
    """The agent that passes no filters and mislabels the chart anyway."""
    result = {
        "model_id": "m1",
        "output_type": "alpha_summary",
        "columns": [],
        "rows": [],
        "row_count": 0,
    }
    out = ap.insert_note(result, ALPHA, {})
    assert out["scope"] == "national, time-invariant"
    assert "ignored_filters" not in out


def test_returns_result_unchanged_when_neither_key_applies():
    result = {
        "model_id": "m1",
        "output_type": "roi",
        "columns": [],
        "rows": [],
        "row_count": 0,
    }
    out = ap.insert_note(result, ("get_channel_summary", "roi"), {})
    assert out is result
    assert "scope" not in out and "ignored_filters" not in out


def test_never_mutates_the_input():
    """ResultCache.get returns by reference; mutating would poison it."""
    result = {
        "model_id": "m1",
        "output_type": "adstock_decay",
        "columns": [],
        "rows": [],
        "row_count": 0,
    }
    snapshot = dict(result)
    ap.insert_note(result, ADSTOCK, {"geos": "reason text here"})
    assert result == snapshot


# A value that expresses a real constraint, per field.
PROBE = {
    "start_date": "2024-07-01",
    "end_date": "2024-09-30",
    "geos": ["US-CA"],
    "channels": ["tv"],
    "aggregate_times": False,
    "use_kpi": True,
    "include_non_paid": False,
}
PROBE_PARSED = {
    "start_date": date(2024, 7, 1),
    "end_date": date(2024, 9, 30),
    "geos": ["US-CA"],
    "channels": ["tv"],
    "aggregate_times": False,
    "use_kpi": True,
    "include_non_paid": False,
}

_ENTRIES = sorted(
    EXPECTED_APPLICABILITY.items(), key=lambda kv: (kv[0][0], kv[0][1] or "")
)


@pytest.mark.parametrize(("key", "applicable"), _ENTRIES, ids=lambda v: str(v))
def test_every_applicable_filter_survives_narrowing(key, applicable):
    """Catches an OVER-STRICT entry -- the regression that would collapse
    two different requests onto one cache key."""
    filters = AnalysisFilters.model_validate({f: PROBE[f] for f in applicable})
    effective, ignored = ap.narrow(filters, key)

    assert ignored == {}, f"{key} reported an applicable filter"
    for field in applicable:
        assert getattr(effective, field) == PROBE_PARSED[field], (key, field)


@pytest.mark.parametrize(("key", "applicable"), _ENTRIES, ids=lambda v: str(v))
def test_every_inapplicable_filter_is_reset_and_reported(key, applicable):
    inapplicable = set(ap.ALL_FILTER_FIELDS) - set(applicable)
    if not inapplicable:
        pytest.skip("entry honors every filter")

    filters = AnalysisFilters.model_validate({f: PROBE[f] for f in inapplicable})
    effective, ignored = ap.narrow(filters, key)

    assert set(ignored) == inapplicable, key
    for field in inapplicable:
        assert getattr(effective, field) == DEFAULTS[field], (key, field)
        assert ignored[field] == ap.IGNORED_REASONS[key][field], (key, field)

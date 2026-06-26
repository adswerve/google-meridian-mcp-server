"""Tests for the validation variant/expectation matrix."""

from __future__ import annotations

from scripts.generate_validation_models import VARIANTS
from scripts.validation import matrix


def test_variants_cover_full_matrix():
    keys = {v.key for v in VARIANTS}
    assert keys == {
        "national-revenue",
        "geo-revenue",
        "national-kpi-rpk",
        "geo-kpi-rpk",
        "national-kpi-only",
        "geo-kpi-only",
        "geo-revenue-media-only",
    }
    # Exactly one no-RF fixture, for the reach_frequency error path.
    assert [v.key for v in VARIANTS if not v.with_rf] == ["geo-revenue-media-only"]
    # National and geo both represented.
    assert {v.n_geos == 1 for v in VARIANTS} == {True, False}


def _variant(key):
    return next(v for v in VARIANTS if v.key == key)


def test_roi_valid_only_for_revenue_models():
    assert matrix.expected_valid(_variant("geo-revenue"), "get_channel_summary", "roi")
    assert not matrix.expected_valid(
        _variant("geo-kpi-only"), "get_channel_summary", "roi"
    )
    assert not matrix.expected_valid(
        _variant("national-kpi-only"), "get_channel_summary", "marginal_roi"
    )


def test_cpik_valid_for_all_models():
    for key in ("geo-revenue", "geo-kpi-only", "national-kpi-rpk"):
        assert matrix.expected_valid(_variant(key), "get_channel_summary", "cpik")


def test_adversarial_cases_cover_roi_on_kpi_only():
    cases = matrix.adversarial_cases(_variant("geo-kpi-only"))
    codes = {(c.tool, c.expected_error_code) for c in cases}
    assert ("get_channel_summary", "metric_not_supported") in codes


def test_adversarial_cases_cover_reach_frequency_on_media_only():
    cases = matrix.adversarial_cases(_variant("geo-revenue-media-only"))
    assert any(
        c.tool == "get_reach_frequency"
        and c.expected_error_code == "metric_not_supported"
        for c in cases
    )

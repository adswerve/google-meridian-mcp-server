"""Tests for the validation variant/expectation matrix."""

from __future__ import annotations

from scripts.generate_validation_models import VARIANTS


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

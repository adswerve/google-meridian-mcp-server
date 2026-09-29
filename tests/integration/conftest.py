"""Shared real-model fixtures for the full-funnel integration tests."""

from __future__ import annotations

import numpy as np
import pytest


@pytest.fixture(scope="session")
def ff_facade():
    from google_meridian_mcp_server.meridian.analyzer_facade import AnalyzerFacade
    from scripts.validation.fixtures import ensure_full_funnel_fixture

    stage2, mediators = ensure_full_funnel_fixture("geo-full-funnel")
    return AnalyzerFacade(stage2, mediators)


@pytest.fixture(scope="session")
def plain_facade():
    from google_meridian_mcp_server.meridian.analyzer_facade import AnalyzerFacade
    from scripts.validation.fixtures import ensure_fixture_model

    return AnalyzerFacade(ensure_fixture_model("geo-revenue"))


@pytest.fixture()
def ff_expected(ff_facade):
    def _expected(filters, aggregate_times: bool = True):
        arr = ff_facade._get_analyzer().expected_outcome(
            selected_geos=ff_facade._selected_geos(filters),
            selected_times=ff_facade._expand_selected_times(filters),
            use_kpi=ff_facade.resolve_use_kpi(filters),
            aggregate_times=aggregate_times,
        )
        mean = np.asarray(arr, dtype=float).mean(axis=(0, 1))
        return float(mean) if mean.ndim == 0 else mean

    return _expected

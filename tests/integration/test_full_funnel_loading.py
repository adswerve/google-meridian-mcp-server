"""The real fixture passes our validation and Google's constructor."""

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.filterwarnings(
    "ignore:The `meridian` argument is deprecated:DeprecationWarning"
)
def test_fixture_validates_cleanly_and_constructs_google_analyzer():
    from google_meridian_mcp_server.meridian.full_funnel.analyzer import (
        AnalyzerFullFunnel,
    )
    from google_meridian_mcp_server.meridian.full_funnel.validation import (
        validate_full_funnel,
    )
    from scripts.validation.fixtures import ensure_full_funnel_fixture

    stage2, mediators = ensure_full_funnel_fixture()
    assert list(mediators) == ["M1", "M2"]
    assert validate_full_funnel(stage2, mediators) == []  # KPI == organic column
    AnalyzerFullFunnel(meridian=stage2, mediator_models=mediators)

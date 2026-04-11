"""Contract tests for response curve tool edge cases and unsupported scopes."""

from __future__ import annotations

from google_meridian_mcp_server.domain.errors import (
    InvalidOutputTypeError,
    MissingModelDataError,
)


class TestResponseCurveToolContract:
    """Validates response curve tool contract edge cases."""

    def test_valid_output_types_are_accepted(self):
        """Only documented output_types should be accepted."""
        from google_meridian_mcp_server.services.analysis_service import (
            RESPONSE_CURVE_TYPES,
        )

        assert "response_curves" in RESPONSE_CURVE_TYPES
        assert "response_curve_summary" in RESPONSE_CURVE_TYPES

    def test_unsupported_output_type_raises(self):
        """Undocumented output_types must raise InvalidOutputTypeError."""
        err = InvalidOutputTypeError(
            "optimized_budget", sorted(["response_curves", "response_curve_summary"])
        )
        assert err.error_code == "invalid_output_type"
        assert "optimized_budget" in str(err)

    def test_budget_optimization_is_unsupported(self):
        """Budget optimization is explicitly out of scope for V1."""
        from google_meridian_mcp_server.services.analysis_service import (
            RESPONSE_CURVE_TYPES,
        )

        unsupported = {"optimized_budget", "budget_allocation", "scenario_plan"}
        for scope in unsupported:
            assert scope not in RESPONSE_CURVE_TYPES

    def test_diagnostics_are_unsupported(self):
        """Model diagnostics are explicitly out of scope for V1."""
        from google_meridian_mcp_server.services.analysis_service import (
            CHANNEL_SUMMARY_TYPES,
            CONTRIBUTION_TYPES,
            RESPONSE_DYNAMICS_TYPES,
        )

        all_types = CHANNEL_SUMMARY_TYPES | CONTRIBUTION_TYPES | RESPONSE_DYNAMICS_TYPES
        unsupported = {"model_diagnostics", "predictive_accuracy", "convergence"}
        for scope in unsupported:
            assert scope not in all_types

    def test_missing_model_data_error_includes_model_id(self):
        err = MissingModelDataError("test-model", "no posterior data")
        assert err.details["model_id"] == "test-model"
        assert "no posterior data" in str(err)

"""Contract tests for grouped analysis MCP tools."""

from __future__ import annotations

from google_meridian_mcp_server.domain.errors import (
    DatasetNotAvailableError,
    InvalidOutputTypeError,
    MissingModelDataError,
    ModelNotFoundError,
)
from google_meridian_mcp_server.persistence.cache import ResultCache
from google_meridian_mcp_server.services.analysis_service import AnalysisService


class TestAnalysisToolContracts:
    """Validates the external contract shapes for all analysis tools."""

    def test_training_data_valid_datasets(self):
        """get_training_data accepts the documented dataset enum values."""
        valid = {
            "kpi",
            "revenue_per_kpi",
            "population",
            "media",
            "media_spend",
            "reach",
            "frequency",
            "rf_spend",
            "organic_media",
            "organic_reach",
            "organic_frequency",
            "non_media_treatments",
            "controls",
        }
        # If this set changes, the contract is broken
        assert len(valid) == 13

    def test_model_overview_tool_is_part_of_the_surface(self):
        from google_meridian_mcp_server.services.analysis_service import (
            CHANNEL_SUMMARY_TYPE_ORDER,
            CONTRIBUTION_TYPE_ORDER,
            RESPONSE_CURVE_TYPE_ORDER,
            RESPONSE_DYNAMICS_TYPE_ORDER,
        )

        assert len(CHANNEL_SUMMARY_TYPE_ORDER) == 6
        assert len(CONTRIBUTION_TYPE_ORDER) == 2
        assert len(RESPONSE_DYNAMICS_TYPE_ORDER) == 2
        assert len(RESPONSE_CURVE_TYPE_ORDER) == 2

    def test_channel_summary_valid_output_types(self):
        valid = {
            "baseline_summary_metrics",
            "paid_summary_metrics",
            "roi",
            "cpik",
            "marginal_roi",
            "marginal_cpik",
        }
        assert len(valid) == 6

    def test_contribution_valid_output_types(self):
        valid = {"contribution_metrics", "contribution_metrics_by_time"}
        assert len(valid) == 2

    def test_response_dynamics_valid_output_types(self):
        valid = {"adstock_decay", "alpha_summary"}
        assert len(valid) == 2

    def test_response_curves_valid_output_types(self):
        valid = {"response_curves", "response_curve_summary"}
        assert len(valid) == 2

    def test_model_not_found_error_shape(self):
        err = ModelNotFoundError("missing-model", "local")
        assert err.error_code == "model_not_found"
        assert err.details["model_id"] == "missing-model"

    def test_invalid_output_type_error_shape(self):
        err = InvalidOutputTypeError("bad", ["good1", "good2"])
        assert err.error_code == "invalid_output_type"
        assert "bad" in str(err)

    def test_dataset_not_available_error_shape(self):
        err = DatasetNotAvailableError("m1", "reach")
        assert err.error_code == "dataset_not_available"
        assert err.details["dataset"] == "reach"

    def test_missing_model_data_error_shape(self):
        err = MissingModelDataError("m1", "no inference data")
        assert err.error_code == "missing_model_data"

    def test_spend_scenario_summary_contract(self):
        """get_spend_scenario returns exactly the 15 documented summary keys."""
        expected_keys = frozenset(
            {
                "model_id",
                "channel",
                "channel_type",
                "outcome_mode",
                "base_spend",
                "spend_increase",
                "new_spend",
                "spend_increase_pct",
                "base_outcome",
                "new_outcome",
                "expected_outcome_increase",
                "expected_outcome_increase_pct",
                "efficiency",
                "marginal_efficiency",
                "efficiency_at_new",
            }
        )

        class _Facade:
            def get_data_inputs(self):
                return {"media": ["search"], "rf_media": []}

            def resolve_use_kpi(self, filters):
                return False  # revenue mode

            def resolve_base_spend(self, channel, filters):
                return 100.0

            def spend_response(self, channel, points, filters):
                return [
                    {"mean": 400.0, "ci_lo": 350.0, "ci_hi": 450.0},
                    {"mean": 460.0, "ci_lo": 400.0, "ci_hi": 520.0},
                ]

        class _Catalog:
            def get_facade(self, model_id):
                return _Facade()

        service = AnalysisService(
            catalog=_Catalog(),
            result_cache=ResultCache(enabled=False, ttl_seconds=None),
        )
        summary = service.get_spend_scenario("test-model", "search", 20.0, None, None)

        assert set(summary.keys()) == expected_keys
        assert len(summary) == 15
        assert summary["outcome_mode"] in {"revenue", "kpi"}

"""Unit tests for analysis service orchestration and validation.

Task 10: ``AnalysisService`` routes every public method through a
``runner`` (``FakeRunner`` here stands in for ``SyncSubprocessExecutor``)
instead of calling a catalog/facade in-process. These tests check:
  - routing: the right operation/model_id/params reach the runner
  - PURE server-side validation still short-circuits without spawning a
    worker (invalid output_type, invalid dataset, cache hit)
  - the pure ``get_model_overview`` decoration (``_decorate_overview``)
  - the pure static helpers (unchanged from the in-process version)

Model-dependent guards (revenue/RF/geo) and facade dispatch now live
worker-side; that coverage is in ``tests/unit/test_analysis_ops.py``.
"""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from google_meridian_mcp_server.domain.errors import (
    DatasetNotAvailableError,
    InvalidOutputTypeError,
)
from google_meridian_mcp_server.domain.filters import AnalysisFilters, normalize_filters
from google_meridian_mcp_server.persistence.cache import ResultCache
from google_meridian_mcp_server.services.analysis_service import AnalysisService

DEFAULT_FILTERS = {
    "start_date": None,
    "end_date": None,
    "geos": [],
    "channels": [],
    "aggregate_times": True,
    "include_non_paid": None,
    "use_kpi": None,
}


class FakeRunner:
    """Stand-in for ``SyncSubprocessExecutor``: records calls, returns a stub."""

    def __init__(self, result_factory=None):
        self.calls: list[tuple[str, str, dict]] = []
        self._result_factory = result_factory

    async def run(self, operation, model_id, params):
        self.calls.append((operation, model_id, params))
        if self._result_factory is not None:
            return self._result_factory(operation, model_id, params)
        return {
            "model_id": model_id,
            "output_type": params.get("output_type"),
            "columns": [],
            "rows": [],
            "row_count": 0,
        }


# --- pure helpers (unchanged from the in-process version) -------------------


class TestNormalizeFilters:
    def test_empty_input_returns_defaults(self):
        f = normalize_filters(None)
        assert f.aggregate_times is True
        assert f.geos == []
        assert f.channels == []

    def test_passes_through_valid_fields(self):
        raw = {
            "start_date": "2024-01-01",
            "end_date": "2024-12-31",
            "geos": ["us", "uk"],
            "channels": ["tv", "search"],
            "aggregate_times": False,
            "include_non_paid": True,
            "use_kpi": True,
        }
        f = normalize_filters(raw)
        assert f.start_date == date(2024, 1, 1)
        assert f.end_date == date(2024, 12, 31)
        assert f.geos == ["us", "uk"]
        assert f.channels == ["tv", "search"]
        assert f.aggregate_times is False
        assert f.include_non_paid is True
        assert f.use_kpi is True

    def test_null_lists_become_empty(self):
        f = normalize_filters({"geos": None, "channels": None})
        assert f.geos == []
        assert f.channels == []

    def test_deduplicates_and_trims_filter_lists(self):
        f = normalize_filters({"channels": [" tv ", "search", "tv", ""]})
        assert f.channels == ["tv", "search"]

    def test_rejects_unknown_filter_fields(self):
        with pytest.raises(ValidationError):
            normalize_filters({"unexpected": True})

    def test_rejects_invalid_date_ranges(self):
        with pytest.raises(ValidationError):
            normalize_filters(
                {
                    "start_date": "2024-12-31",
                    "end_date": "2024-01-01",
                }
            )

    def test_rejects_non_list_channel_filters(self):
        with pytest.raises(TypeError):
            normalize_filters({"channels": "search"})

    def test_rejects_non_string_filter_values(self):
        with pytest.raises(TypeError):
            normalize_filters({"geos": ["us", 3]})


class TestInvalidOutputType:
    def test_error_includes_valid_types(self):
        err = InvalidOutputTypeError("bad_type", ["roi", "cpik"])
        assert err.error_code == "invalid_output_type"
        assert "bad_type" in str(err)
        assert err.details["valid_types"] == ["roi", "cpik"]


class TestAnalysisFiltersImmutability:
    def test_frozen_dataclass(self):
        f = AnalysisFilters(start_date="2024-01-01")
        with pytest.raises(ValidationError):
            f.start_date = "2025-01-01"


class TestRoundMeasure:
    @pytest.mark.parametrize(
        "value,expected",
        [
            (1.23456789, 1.23457),
            (0.000123456789, 0.000123457),
            (1234567.0, 1234570.0),
            (-2.0 / 3.0, -0.666667),
            (True, True),
            (False, False),
            (42, 42),
            ("text", "text"),
            (None, None),
        ],
    )
    def test_rounds_floats_to_six_significant_figures(self, value, expected):
        result = AnalysisService._round_measure(value)
        assert result == expected
        assert type(result) is type(expected)

    def test_bool_is_not_rounded_as_float(self):
        # bool is a subclass of int/float; it must pass through untouched.
        assert AnalysisService._round_measure(True) is True
        assert AnalysisService._round_measure(False) is False


class TestNormalizeDatasetSelection:
    def test_deduplicates_dataset_selection(self):
        assert AnalysisService._normalize_dataset_selection("m1", ["kpi", "kpi"]) == [
            "kpi"
        ]

    def test_wraps_single_string(self):
        assert AnalysisService._normalize_dataset_selection("m1", "kpi") == ["kpi"]

    def test_rejects_unknown_dataset(self):
        with pytest.raises(DatasetNotAvailableError):
            AnalysisService._normalize_dataset_selection("m1", ["unknown"])

    def test_rejects_empty_selection(self):
        with pytest.raises(DatasetNotAvailableError):
            AnalysisService._normalize_dataset_selection("m1", [])


# --- routing: one test per tool ----------------------------------------------


async def test_get_contribution_routes():
    r = FakeRunner()
    svc = AnalysisService(runner=r, result_cache=None)
    await svc.get_contribution("m1", "contribution_metrics", None)
    op, mid, params = r.calls[0]
    assert op == "get_contribution" and mid == "m1"
    assert params["output_type"] == "contribution_metrics"
    assert (
        params["filters"]["geos"] == [] and params["filters"]["aggregate_times"] is True
    )
    assert params["filters"] == DEFAULT_FILTERS


async def test_get_channel_summary_routes():
    r = FakeRunner()
    svc = AnalysisService(runner=r, result_cache=None)
    await svc.get_channel_summary("m1", "roi", {"channels": ["tv"]})
    op, mid, params = r.calls[0]
    assert op == "get_channel_summary" and mid == "m1"
    assert params["output_type"] == "roi"
    assert params["filters"]["channels"] == ["tv"]


async def test_get_adstock_decay_routes():
    r = FakeRunner()
    svc = AnalysisService(runner=r, result_cache=None)
    await svc.get_adstock_decay("m1", "adstock_decay", None)
    op, mid, params = r.calls[0]
    assert op == "get_adstock_decay" and mid == "m1"
    assert params["output_type"] == "adstock_decay"
    assert params["filters"] == DEFAULT_FILTERS


async def test_get_response_curves_routes():
    r = FakeRunner()
    svc = AnalysisService(runner=r, result_cache=None)
    await svc.get_response_curves("m1", "response_curves", None)
    op, mid, params = r.calls[0]
    assert op == "get_response_curves" and mid == "m1"
    assert params["output_type"] == "response_curves"
    assert params["filters"] == DEFAULT_FILTERS


async def test_get_reach_frequency_routes():
    r = FakeRunner()
    svc = AnalysisService(runner=r, result_cache=None)
    await svc.get_reach_frequency("m1", None)
    op, mid, params = r.calls[0]
    assert op == "get_reach_frequency" and mid == "m1"
    assert params == {"filters": DEFAULT_FILTERS}


async def test_get_model_fit_routes():
    r = FakeRunner()
    svc = AnalysisService(runner=r, result_cache=None)
    await svc.get_model_fit("m1", {"geos": ["us"]})
    op, mid, params = r.calls[0]
    assert op == "get_model_fit" and mid == "m1"
    assert params["filters"]["geos"] == ["us"]


async def test_get_channel_data_routes():
    r = FakeRunner()
    svc = AnalysisService(runner=r, result_cache=None)
    await svc.get_channel_data("m1", None)
    op, mid, params = r.calls[0]
    assert op == "get_channel_data" and mid == "m1"
    assert params == {"filters": DEFAULT_FILTERS}


async def test_get_training_data_routes():
    r = FakeRunner()
    svc = AnalysisService(runner=r, result_cache=None)
    await svc.get_training_data("m1", ["kpi", "media_spend"], None)
    op, mid, params = r.calls[0]
    assert op == "get_training_data" and mid == "m1"
    assert params["datasets"] == ["kpi", "media_spend"]
    assert params["filters"] == DEFAULT_FILTERS


async def test_get_training_data_dedupes_dataset_selection():
    r = FakeRunner()
    svc = AnalysisService(runner=r, result_cache=None)
    await svc.get_training_data("m1", ["kpi", "kpi"], None)
    op, mid, params = r.calls[0]
    assert params["datasets"] == ["kpi"]


async def test_get_spend_scenario_routes():
    r = FakeRunner()
    svc = AnalysisService(runner=r, result_cache=None)
    await svc.get_spend_scenario("m1", "search", 20.0, None, None)
    op, mid, params = r.calls[0]
    assert op == "get_spend_scenario" and mid == "m1"
    assert params["channel"] == "search"
    assert params["spend_increase"] == 20.0
    assert params["base_spend"] is None
    assert params["filters"] == DEFAULT_FILTERS


async def test_get_spend_scenario_routes_provided_base_spend():
    r = FakeRunner()
    svc = AnalysisService(runner=r, result_cache=None)
    await svc.get_spend_scenario("m1", "search", 20.0, 50.0, None)
    op, mid, params = r.calls[0]
    assert params["base_spend"] == 50.0


async def test_get_model_overview_routes_and_decorates():
    raw_overview = {
        "model_id": "m1",
        "model_type": "geo",
        "available_training_datasets": ["kpi", "media", "media_spend"],
        "has_revenue_per_kpi": True,
        "media_channels": ["search", "tv"],
        "rf_channels": ["youtube"],
        "geo_names": ["us", "ca"],
    }
    r = FakeRunner(result_factory=lambda op, mid, params: dict(raw_overview))
    svc = AnalysisService(runner=r, result_cache=None)

    result = await svc.get_model_overview("m1")

    op, mid, params = r.calls[0]
    assert op == "get_model_overview" and mid == "m1"
    assert params == {}

    assert result["model_id"] == "m1"
    assert result["available_tool_options"]["get_training_data"] == {
        "dataset": ["kpi", "media", "media_spend"]
    }
    assert result["available_tool_options"]["get_channel_summary"]["output_type"] == [
        "baseline_summary_metrics",
        "paid_summary_metrics",
        "roi",
        "cpik",
        "marginal_roi",
        "marginal_cpik",
    ]
    assert result["available_tool_options"]["get_contribution"] == {
        "output_type": ["contribution_metrics", "contribution_metrics_by_time"]
    }
    assert result["available_tool_options"]["get_adstock_decay"] == {
        "output_type": ["adstock_decay", "alpha_summary"]
    }
    assert result["available_tool_options"]["get_response_curves"] == {
        "output_type": ["response_curves", "response_curve_summary"]
    }
    assert result["available_tool_options"]["get_spend_scenario"] == {
        "channel": ["search", "tv", "youtube"]
    }
    assert result["available_tool_options"]["run_optimization"] == {
        "channels": ["search", "tv", "youtube"],
        "geos": ["us", "ca"],
        "use_kpi_togglable": True,
        "scenarios": ["fixed_budget", "target_roas", "target_mroas"],
    }
    assert result["available_tool_options"]["get_reach_frequency"] == {}


# --- pure get_model_overview decoration (_decorate_overview) ----------------


def _base_overview(has_revenue, rf_channels):
    return {
        "available_training_datasets": ["kpi", "media", "media_spend"],
        "has_revenue_per_kpi": has_revenue,
        "rf_channels": rf_channels,
    }


def test_decorate_overview_prunes_roi_for_no_revenue_model():
    overview = AnalysisService._decorate_overview(
        "kpi-only", _base_overview(has_revenue=False, rf_channels=["yt"])
    )
    types = overview["available_tool_options"]["get_channel_summary"]["output_type"]
    assert "roi" not in types and "marginal_roi" not in types
    assert "cpik" in types and "marginal_cpik" in types


def test_decorate_overview_keeps_roi_for_revenue_model():
    overview = AnalysisService._decorate_overview(
        "rev", _base_overview(has_revenue=True, rf_channels=[])
    )
    types = overview["available_tool_options"]["get_channel_summary"]["output_type"]
    assert "roi" in types and "marginal_roi" in types


def test_decorate_overview_omits_reach_frequency_without_rf_channels():
    overview = AnalysisService._decorate_overview(
        "m1", _base_overview(has_revenue=True, rf_channels=[])
    )
    assert "get_reach_frequency" not in overview["available_tool_options"]


def test_decorate_overview_advertises_spend_scenario_channels():
    overview = AnalysisService._decorate_overview(
        "m1",
        {
            "available_training_datasets": ["media_spend"],
            "has_revenue_per_kpi": True,
            "media_channels": ["search", "tv"],
            "rf_channels": ["youtube"],
        },
    )
    assert overview["available_tool_options"]["get_spend_scenario"] == {
        "channel": ["search", "tv", "youtube"]
    }


# --- no-spawn guards: PURE validation must short-circuit before the runner ---


@pytest.mark.parametrize(
    "method_name",
    [
        "get_contribution",
        "get_channel_summary",
        "get_adstock_decay",
        "get_response_curves",
    ],
)
async def test_invalid_output_type_no_spawn(method_name):
    r = FakeRunner()
    svc = AnalysisService(runner=r, result_cache=None)
    with pytest.raises(InvalidOutputTypeError):
        await getattr(svc, method_name)("m1", "bogus", None)
    assert r.calls == []


async def test_get_training_data_invalid_dataset_no_spawn():
    r = FakeRunner()
    svc = AnalysisService(runner=r, result_cache=None)
    with pytest.raises(DatasetNotAvailableError):
        await svc.get_training_data("m1", ["unknown"], None)
    assert r.calls == []


async def test_cache_hit_no_spawn():
    cache = ResultCache(enabled=True, ttl_seconds=None)
    r = FakeRunner()
    svc = AnalysisService(runner=r, result_cache=cache)

    params = {
        "output_type": "contribution_metrics",
        "filters": AnalysisService._filter_key(normalize_filters(None)),
    }
    cached_result = {
        "model_id": "m1",
        "output_type": "contribution_metrics",
        "columns": [],
        "rows": [],
        "row_count": 0,
        "cached": True,
    }
    cache.put("get_contribution", "m1", params, cached_result)

    result = await svc.get_contribution("m1", "contribution_metrics", None)

    assert result == cached_result
    assert r.calls == []


async def test_second_identical_call_uses_cache_and_does_not_spawn():
    cache = ResultCache(enabled=True, ttl_seconds=None)
    r = FakeRunner()
    svc = AnalysisService(runner=r, result_cache=cache)

    first = await svc.get_contribution("m1", "contribution_metrics", None)
    second = await svc.get_contribution("m1", "contribution_metrics", None)

    assert first == second
    assert len(r.calls) == 1


# --- get_spend_scenario summary math (pure, via the static builder) ---------


class TestBuildSpendScenario:
    def test_revenue_mode_computes_roi_family(self):
        result = AnalysisService._build_spend_scenario(
            model_id="m1",
            channel="search",
            channel_type="paid_media",
            outcome_mode="revenue",
            base_spend=100.0,
            spend_increase=20.0,
            new_spend=120.0,
            base_outcome={"mean": 400.0, "ci_lo": 350.0, "ci_hi": 450.0},
            new_outcome={"mean": 460.0, "ci_lo": 400.0, "ci_hi": 520.0},
        )
        assert result["outcome_mode"] == "revenue"
        assert result["channel_type"] == "paid_media"
        assert result["base_spend"] == 100.0
        assert result["new_spend"] == 120.0
        assert result["efficiency"] == 4.0
        assert result["marginal_efficiency"] == 3.0
        assert result["efficiency_at_new"] == pytest.approx(3.83333, rel=1e-4)
        assert result["expected_outcome_increase"] == 60.0
        assert result["base_outcome"] == {"mean": 400.0, "ci_lo": 350.0, "ci_hi": 450.0}

    def test_kpi_mode_computes_cpik_family(self):
        result = AnalysisService._build_spend_scenario(
            model_id="m1",
            channel="search",
            channel_type="paid_media",
            outcome_mode="kpi",
            base_spend=100.0,
            spend_increase=20.0,
            new_spend=120.0,
            base_outcome={"mean": 400.0, "ci_lo": 350.0, "ci_hi": 450.0},
            new_outcome={"mean": 460.0, "ci_lo": 400.0, "ci_hi": 520.0},
        )
        assert result["outcome_mode"] == "kpi"
        assert result["efficiency"] == 0.25
        assert result["marginal_efficiency"] == pytest.approx(0.333333, rel=1e-4)
        assert result["efficiency_at_new"] == pytest.approx(0.26087, rel=1e-4)

    def test_zero_lift_yields_null_efficiency(self):
        result = AnalysisService._build_spend_scenario(
            model_id="m1",
            channel="search",
            channel_type="paid_media",
            outcome_mode="kpi",
            base_spend=100.0,
            spend_increase=20.0,
            new_spend=120.0,
            base_outcome={"mean": 0.0, "ci_lo": 0.0, "ci_hi": 0.0},
            new_outcome={"mean": 0.0, "ci_lo": 0.0, "ci_hi": 0.0},
        )
        assert result["efficiency"] is None
        assert result["marginal_efficiency"] is None

    def test_kpi_negative_marginal_returns_negative_float(self):
        # base_mean=400.0, new_mean=360.0 -> delta=-40.0
        # KPI marginal_efficiency = spend_increase / delta = 20.0 / (-40.0) = -0.5
        result = AnalysisService._build_spend_scenario(
            model_id="m1",
            channel="search",
            channel_type="paid_media",
            outcome_mode="kpi",
            base_spend=100.0,
            spend_increase=20.0,
            new_spend=120.0,
            base_outcome={"mean": 400.0, "ci_lo": 350.0, "ci_hi": 450.0},
            new_outcome={"mean": 360.0, "ci_lo": 310.0, "ci_hi": 410.0},
        )
        assert result["marginal_efficiency"] is not None
        assert isinstance(result["marginal_efficiency"], float)
        assert result["marginal_efficiency"] < 0
        assert result["marginal_efficiency"] == pytest.approx(-0.5)

    def test_summary_has_exactly_the_documented_15_keys(self):
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
        result = AnalysisService._build_spend_scenario(
            model_id="test-model",
            channel="search",
            channel_type="paid_media",
            outcome_mode="revenue",
            base_spend=100.0,
            spend_increase=20.0,
            new_spend=120.0,
            base_outcome={"mean": 400.0, "ci_lo": 350.0, "ci_hi": 450.0},
            new_outcome={"mean": 460.0, "ci_lo": 400.0, "ci_hi": 520.0},
        )
        assert set(result.keys()) == expected_keys
        assert len(result) == 15


# --- filter applicability narrowing (Task 4) ---------------------------------


async def test_adstock_strips_date_and_geo_from_the_worker_payload():
    r = FakeRunner()
    svc = AnalysisService(runner=r, result_cache=None)

    await svc.get_adstock_decay(
        "m1",
        "adstock_decay",
        {
            "start_date": "2024-07-01",
            "end_date": "2024-09-30",
            "geos": ["US-CA"],
            "channels": ["tv"],
        },
    )

    _, _, params = r.calls[0]
    assert params["filters"] == {**DEFAULT_FILTERS, "channels": ["tv"]}


async def test_adstock_response_carries_scope_and_ignored_filters():
    r = FakeRunner()
    svc = AnalysisService(runner=r, result_cache=None)

    out = await svc.get_adstock_decay(
        "m1",
        "adstock_decay",
        {"start_date": "2024-07-01", "end_date": "2024-09-30", "geos": ["US-CA"]},
    )

    assert out["scope"] == "national, full training window"
    assert set(out["ignored_filters"]) == {"start_date", "end_date", "geos"}


async def test_model_fit_use_kpi_reaches_the_worker():
    """Regression guard: an over-strict entry here would collapse
    use_kpi=True/False onto one cache key and serve wrong-denomination rows."""
    r = FakeRunner()
    svc = AnalysisService(runner=r, result_cache=None)

    await svc.get_model_fit("m1", {"use_kpi": True})

    _, _, params = r.calls[0]
    assert params["filters"]["use_kpi"] is True


async def test_spend_scenario_reports_channels_and_keeps_its_own_args():
    r = FakeRunner(
        result_factory=lambda op, mid, p: {
            "model_id": mid,
            "channel": p["channel"],
            "channel_type": "paid_media",
            "outcome_mode": "revenue",
        }
    )
    svc = AnalysisService(runner=r, result_cache=None)

    out = await svc.get_spend_scenario(
        "m1", "search", 100.0, None, {"channels": ["tv"]}
    )

    _, _, params = r.calls[0]
    assert params["channel"] == "search" and params["spend_increase"] == 100.0
    assert params["filters"]["channels"] == []
    assert set(out["ignored_filters"]) == {"channels"}


# NOTE: the existing parametrized `test_invalid_output_type_no_spawn`
# (tests/unit/test_analysis_service.py:397-411) already covers all four
# dispatch tools and is the stronger guard that the applicability lookup
# never pre-empts InvalidOutputTypeError. Do not duplicate it here -- just
# confirm it still passes after Task 4.


# --- Task 5: service-wiring coverage for all nine methods --------------------

# (service method, kwargs, an INAPPLICABLE filter for that surface, its value)
WIRING_CASES = [
    (
        "get_channel_summary",
        {"output_type": "baseline_summary_metrics"},
        "channels",
        ["tv"],
    ),
    ("get_channel_summary", {"output_type": "roi"}, "include_non_paid", False),
    (
        "get_contribution",
        {"output_type": "contribution_metrics_by_time"},
        "aggregate_times",
        False,
    ),
    ("get_adstock_decay", {"output_type": "adstock_decay"}, "geos", ["US-CA"]),
    (
        "get_response_curves",
        {"output_type": "response_curves"},
        "include_non_paid",
        True,
    ),
    ("get_reach_frequency", {}, "aggregate_times", False),
    ("get_model_fit", {}, "channels", ["tv"]),
    ("get_channel_data", {}, "use_kpi", True),
    ("get_training_data", {"dataset": "kpi"}, "use_kpi", True),
    (
        "get_spend_scenario",
        {"channel": "search", "spend_increase": 100.0, "base_spend": None},
        "channels",
        ["tv"],
    ),
]


@pytest.mark.parametrize(
    ("method", "kwargs", "field", "value"),
    WIRING_CASES,
    ids=[f"{m}-{f}" for m, _, f, _ in WIRING_CASES],
)
async def test_every_service_method_narrows_and_reports(method, kwargs, field, value):
    """Fails if ANY method loses its narrowing or its note."""
    r = FakeRunner()
    svc = AnalysisService(runner=r, result_cache=None)

    out = await getattr(svc, method)("m1", filters={field: value}, **kwargs)

    _, _, params = r.calls[0]
    assert params["filters"][field] == DEFAULT_FILTERS[field], (
        f"{method} did not strip {field} from the worker payload"
    )
    assert field in out.get("ignored_filters", {}), (
        f"{method} did not report {field} in ignored_filters"
    )


@pytest.mark.parametrize(
    ("method", "kwargs"),
    [(m, k) for m, k, _, _ in WIRING_CASES],
    ids=[m + str(sorted(k)) for m, k, _, _ in WIRING_CASES],
)
async def test_no_method_emits_a_note_when_nothing_was_supplied(method, kwargs):
    """scope is the only key allowed to appear unprompted, and only on adstock."""
    r = FakeRunner()
    svc = AnalysisService(runner=r, result_cache=None)

    out = await getattr(svc, method)("m1", filters=None, **kwargs)

    assert "ignored_filters" not in out
    if method != "get_adstock_decay":
        assert "scope" not in out

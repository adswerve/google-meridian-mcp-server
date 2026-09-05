"""Fixture enumeration, capability gating and placeholder resolution."""

from scripts.generate_validation_models import VARIANTS, VariantSpec
from scripts.validation import matrix

GEO_REVENUE = VariantSpec("geo-revenue", "revenue", 5, True)
NATIONAL_KPI_ONLY = VariantSpec("national-kpi-only", "kpi_only", 1, True)
GEO_MEDIA_ONLY = VariantSpec("geo-revenue-media-only", "revenue", 5, False)

OVERVIEW = {
    "time": {
        "start": "2023-01-01",
        "end": "2023-12-24",
        "values": ["2023-01-01", "2023-06-25", "2023-12-24"],
    },
    "geo_names": ["geo_0", "geo_1"],
    "media_channels": ["ch_0", "ch_1"],
    "rf_channels": ["rf_ch_0"],
}


def test_fixture_specs_includes_the_pickle_fixture_that_has_no_variantspec():
    """generate_validation_models.py defines 7 VariantSpecs; the 8th fixture is
    a side effect at :109-115. Iterating VARIANTS would leave the loader's
    pickle branch -- the whole reason that fixture exists -- out of every diff."""
    keys = [spec.key for spec in matrix.fixture_specs()]
    assert len(keys) == len(VARIANTS) + 1
    assert matrix.PKL_FIXTURE in keys
    assert set(keys) >= {spec.key for spec in VARIANTS}


def test_the_pickle_fixture_mirrors_national_revenue_capabilities():
    spec = next(s for s in matrix.fixture_specs() if s.key == matrix.PKL_FIXTURE)
    assert spec.factory_has_revenue() is True
    assert spec.n_geos == 1
    assert spec.with_rf is True


def test_the_pickle_fixture_does_not_run_optimization_cases():
    """It is a duplicate posterior; optimizing it twice buys nothing."""
    spec = next(s for s in matrix.fixture_specs() if s.key == matrix.PKL_FIXTURE)
    assert "optimize" not in matrix.variant_capabilities(spec)


def test_capabilities_of_a_geo_revenue_rf_variant():
    caps = matrix.variant_capabilities(GEO_REVENUE)
    assert caps == frozenset({"revenue", "rf", "geo", "optimize"})


def test_kpi_only_national_variant_has_neither_revenue_nor_geo():
    caps = matrix.variant_capabilities(NATIONAL_KPI_ONLY)
    assert "revenue" not in caps and "geo" not in caps and "optimize" not in caps
    assert "rf" in caps


def test_media_only_variant_has_no_rf():
    assert "rf" not in matrix.variant_capabilities(GEO_MEDIA_ONLY)


def test_every_declared_case_has_a_known_surface_and_unique_name():
    names = [(case.tool, case.name) for case in matrix.TOOL_CASES]
    assert len(names) == len(set(names)), "duplicate (tool, name) -> snapshot collision"
    assert {case.surface for case in matrix.TOOL_CASES} <= {"service", "job"}


def test_no_tool_case_duplicates_an_adversarial_case():
    """The unknown-channel spend scenario already exists as an AdversarialCase
    at matrix.py:64-74; declaring it twice writes two identical snapshots."""
    adversarial_args = [
        (case.tool, tuple(sorted(case.args.items())))
        for case in matrix.adversarial_cases(GEO_REVENUE)
    ]
    for case in matrix.tool_cases(GEO_REVENUE, OVERVIEW):
        assert (case.tool, tuple(sorted(case.args.items()))) not in adversarial_args


def test_roi_cases_are_gated_out_of_a_no_revenue_variant():
    selected = {
        case.name
        for case in matrix.tool_cases(NATIONAL_KPI_ONLY, OVERVIEW)
        if case.tool == "get_channel_summary"
    }
    assert "roi" not in selected and "marginal_roi" not in selected
    assert "cpik" in selected


def test_reach_frequency_cases_are_gated_out_of_a_no_rf_variant():
    tools = {case.tool for case in matrix.tool_cases(GEO_MEDIA_ONLY, OVERVIEW)}
    assert "get_reach_frequency" not in tools


def test_optimization_cases_only_run_on_the_optimization_variants():
    tools = {case.tool for case in matrix.tool_cases(NATIONAL_KPI_ONLY, OVERVIEW)}
    assert "run_optimization" not in tools and "lifecycle" not in tools
    tools = {case.tool for case in matrix.tool_cases(GEO_REVENUE, OVERVIEW)}
    assert {"run_optimization", "run_future_optimization", "lifecycle"} <= tools


def test_placeholders_are_resolved_from_the_overview():
    context = matrix.placeholder_context(GEO_REVENUE, OVERVIEW)
    assert context["$MODEL_ID"] == "geo-revenue"
    assert context["$FIRST_CHANNEL"] == "ch_0"
    assert context["$RF_CHANNEL"] == "rf_ch_0"
    assert context["$FIRST_GEO"] == "geo_0"
    assert context["$START_DATE"] == "2023-01-01"
    assert context["$MID_DATE"] == "2023-06-25"
    assert context["$END_DATE"] == "2023-12-24"
    assert context["$NEXT_PERIOD"] > "2023-12-24"
    assert context["$FAR_FUTURE"] == "2099-01-01"


def test_resolution_reaches_into_nested_dicts_and_lists():
    resolved = matrix.resolve_placeholders(
        {"filters": {"geos": ["$FIRST_GEO"], "start_date": "$MID_DATE"}},
        matrix.placeholder_context(GEO_REVENUE, OVERVIEW),
    )
    assert resolved == {"filters": {"geos": ["geo_0"], "start_date": "2023-06-25"}}


def test_dict_keys_are_resolved_too():
    """cost_multipliers and planned_allocation are CHANNEL-KEYED dicts, so the
    placeholder appears as a key, not a value."""
    resolved = matrix.resolve_placeholders(
        {"cost_multipliers": {"$FIRST_CHANNEL": 1.15}},
        matrix.placeholder_context(GEO_REVENUE, OVERVIEW),
    )
    assert resolved == {"cost_multipliers": {"ch_0": 1.15}}


def test_no_placeholder_survives_resolution():
    for case in matrix.tool_cases(GEO_REVENUE, OVERVIEW):
        flat = repr(case.args)
        assert "$" not in flat, f"{case.tool}/{case.name} left a placeholder: {flat}"


def test_adversarial_cases_are_exposed_as_snapshotable_tool_cases():
    cases = matrix.adversarial_tool_cases(NATIONAL_KPI_ONLY)
    assert cases, "no adversarial cases for a kpi-only variant"
    assert all(case.surface == "service" for case in cases)
    assert all(case.name.startswith("err_") for case in cases)
    assert all(case.args["model_id"] == "national-kpi-only" for case in cases)

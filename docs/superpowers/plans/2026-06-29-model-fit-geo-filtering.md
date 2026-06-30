# Geo Filtering for `get_model_fit` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the `get_model_fit` MCP tool honor the `geos` filter by delegating to Meridian's `ModelFit` visualizer, matching the showcase app exactly.

**Architecture:** Replace `AnalyzerFacade.get_model_fit`'s direct `expected_vs_actual_data(aggregate_geos=True)` call with a cached Meridian `ModelFit` visualizer whose `_transform_data_to_dataframe(selected_times, selected_geos)` performs geo/time selection and national aggregation (including credible-interval summation) inside Meridian. The service validates unknown geo names; the live-validation suite exercises the geo path end-to-end.

**Tech Stack:** Python, FastMCP, Google Meridian (`meridian.analysis.visualizer.ModelFit`), pandas/xarray, pytest, ruff, uv.

## Global Constraints

- The columnar output schema of `get_model_fit` MUST stay exactly: `time, expected, expected_ci_lo, expected_ci_hi, actual, baseline, baseline_ci_lo, baseline_ci_hi, residual` (in that order).
- Use Meridian's output directly — do NOT reimplement geo aggregation or credible-interval math in our code. The cross-geo sum is Meridian's `_transform_data_to_dataframe`.
- National (no-geo) `ci_lo`/`ci_hi` values will change (summed per-geo intervals). This is intentional parity with the app; means/actuals/baseline are unchanged.
- Per-geo disaggregated output (`show_geo_level=True`) is out of scope.
- Meridian constant string values are fixed: type column `"type"` with values `"expected"`/`"baseline"`/`"actual"`; metric columns `"mean"`/`"ci_lo"`/`"ci_hi"`; index `"time"`.
- Lint after code changes: `uv run ruff check src tests scripts` and `uv run ruff format src tests scripts`.

---

### Task 1: Facade — geo-aware `get_model_fit` via Meridian `ModelFit`

**Files:**
- Modify: `src/google_meridian_mcp_server/meridian/analyzer_facade.py` (imports near lines 14-17, `__init__` line 24-26, `get_model_fit` lines 478-522)
- Test: `tests/unit/test_analyzer_facade.py`
- Test (contract guard): `tests/contract/test_meridian_modelfit_contract.py` (create)

**Interfaces:**
- Consumes (existing on `AnalyzerFacade`): `self._mmm`, `self.resolve_use_kpi(filters) -> bool`, `self._expand_selected_times(filters) -> list[str] | None`, `self._selected_geos(filters) -> list[str] | None`, `dataset_to_records(df) -> list[dict]`.
- Produces: `AnalyzerFacade.get_model_fit(filters: AnalysisFilters) -> list[dict]` (unchanged signature), plus new private `_get_model_fit(filters, confidence_level=0.9)` and static `_reshape_model_fit(df) -> pd.DataFrame`, and new instance attribute `self._model_fit_cache: dict[tuple, Any]`.

- [ ] **Step 1: Write the failing test for geo/time forwarding + reshape**

Add to `tests/unit/test_analyzer_facade.py`:

```python
from datetime import date


def test_get_model_fit_forwards_geo_time_and_reshapes_to_wide_schema():
    captured = {}
    long_df = pd.DataFrame(
        {
            "time": ["2023-01-01", "2023-01-01", "2023-01-01"],
            "type": ["expected", "baseline", "actual"],
            "mean": [10.0, 4.0, 11.0],
            "ci_lo": [9.0, 3.0, 11.0],
            "ci_hi": [11.0, 5.0, 11.0],
        }
    )

    class _FakeModelFit:
        def __init__(self, *args, **kwargs):
            captured["init_kwargs"] = kwargs

        def _transform_data_to_dataframe(
            self, selected_times=None, selected_geos=None
        ):
            captured["selected_times"] = selected_times
            captured["selected_geos"] = selected_geos
            return long_df

    visualizer_module = ModuleType("meridian.analysis.visualizer")
    visualizer_module.ModelFit = _FakeModelFit
    analysis_module = ModuleType("meridian.analysis")
    analysis_module.visualizer = visualizer_module
    meridian_module = ModuleType("meridian")
    meridian_module.analysis = analysis_module

    input_data = SimpleNamespace(revenue_per_kpi=None)
    facade = AnalyzerFacade(
        SimpleNamespace(
            input_data=input_data,
            expand_selected_time_dims=lambda start, end: ["2023-01-01"],
        )
    )
    filters = AnalysisFilters(geos=["us"], start_date=date(2023, 1, 1))

    with mock.patch.dict(
        sys.modules,
        {
            "meridian": meridian_module,
            "meridian.analysis": analysis_module,
            "meridian.analysis.visualizer": visualizer_module,
        },
    ):
        rows = facade.get_model_fit(filters)

    assert captured["selected_geos"] == ["us"]
    assert captured["selected_times"] == ["2023-01-01"]
    assert captured["init_kwargs"]["use_kpi"] is True
    assert list(rows[0].keys()) == [
        "time",
        "expected",
        "expected_ci_lo",
        "expected_ci_hi",
        "actual",
        "baseline",
        "baseline_ci_lo",
        "baseline_ci_hi",
        "residual",
    ]
    row = rows[0]
    assert row["expected"] == 10.0
    assert row["expected_ci_lo"] == 9.0
    assert row["expected_ci_hi"] == 11.0
    assert row["actual"] == 11.0
    assert row["baseline"] == 4.0
    assert row["baseline_ci_lo"] == 3.0
    assert row["residual"] == 1.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_analyzer_facade.py::test_get_model_fit_forwards_geo_time_and_reshapes_to_wide_schema -v`
Expected: FAIL — current `get_model_fit` calls `self._get_analyzer().expected_vs_actual_data(...)`, so it raises an `AttributeError`/error on the `SimpleNamespace` model (no `_transform_data_to_dataframe` path), or asserts mismatch.

- [ ] **Step 3: Add the `_model_fit_cache` attribute**

In `analyzer_facade.py`, modify `__init__` (currently lines 24-26):

```python
    def __init__(self, mmm: Any) -> None:
        super().__init__(mmm)
        self._media_summary_cache: dict[tuple, Any] = {}
        self._model_fit_cache: dict[tuple, Any] = {}
```

- [ ] **Step 4: Replace `get_model_fit` and add helpers**

In `analyzer_facade.py`, replace the entire current `get_model_fit` method (lines 478-522, under the `# -- Model fit methods --` comment) with:

```python
    def _get_model_fit(self, filters: AnalysisFilters, confidence_level: float = 0.9):
        use_kpi = self.resolve_use_kpi(filters)
        key = (use_kpi, confidence_level)
        if key not in self._model_fit_cache:
            from meridian.analysis import visualizer as visualizer_mod

            self._model_fit_cache[key] = visualizer_mod.ModelFit(
                self._mmm,
                use_kpi=use_kpi,
                confidence_level=confidence_level,
            )
        return self._model_fit_cache[key]

    def get_model_fit(self, filters: AnalysisFilters) -> list[dict]:
        model_fit = self._get_model_fit(filters)
        df = model_fit._transform_data_to_dataframe(
            selected_times=self._expand_selected_times(filters),
            selected_geos=self._selected_geos(filters),
        )
        return dataset_to_records(self._reshape_model_fit(df))

    @staticmethod
    def _reshape_model_fit(df: pd.DataFrame) -> pd.DataFrame:
        by_type = {
            fit_type: df[df["type"] == fit_type].set_index("time")
            for fit_type in ("expected", "baseline", "actual")
        }
        out = pd.DataFrame(index=by_type["expected"].index)
        out["expected"] = by_type["expected"]["mean"]
        out["expected_ci_lo"] = by_type["expected"]["ci_lo"]
        out["expected_ci_hi"] = by_type["expected"]["ci_hi"]
        out["actual"] = by_type["actual"]["mean"]
        out["baseline"] = by_type["baseline"]["mean"]
        out["baseline_ci_lo"] = by_type["baseline"]["ci_lo"]
        out["baseline_ci_hi"] = by_type["baseline"]["ci_hi"]
        out = out.reset_index()
        out["residual"] = out["actual"] - out["expected"]
        ordered = [
            "time",
            "expected",
            "expected_ci_lo",
            "expected_ci_hi",
            "actual",
            "baseline",
            "baseline_ci_lo",
            "baseline_ci_hi",
            "residual",
        ]
        return out[ordered]
```

- [ ] **Step 5: Remove the now-unused `filter_records` import**

In `analyzer_facade.py`, the import block (lines 14-17) currently is:

```python
from google_meridian_mcp_server.meridian.dataset_mapper import (
    dataset_to_records,
    filter_records,
)
```

`filter_records` was only used by the old `get_model_fit`. Replace with:

```python
from google_meridian_mcp_server.meridian.dataset_mapper import dataset_to_records
```

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_analyzer_facade.py::test_get_model_fit_forwards_geo_time_and_reshapes_to_wide_schema -v`
Expected: PASS

- [ ] **Step 7: Write the failing caching test**

Add to `tests/unit/test_analyzer_facade.py`:

```python
def test_model_fit_is_cached_by_use_kpi_and_confidence_level():
    model_fit_ctor = mock.Mock()

    class _FakeModelFit:
        def __init__(self, *args, **kwargs):
            model_fit_ctor(*args, **kwargs)

    visualizer_module = ModuleType("meridian.analysis.visualizer")
    visualizer_module.ModelFit = _FakeModelFit
    analysis_module = ModuleType("meridian.analysis")
    analysis_module.visualizer = visualizer_module
    meridian_module = ModuleType("meridian")
    meridian_module.analysis = analysis_module

    facade = AnalyzerFacade(
        SimpleNamespace(input_data=SimpleNamespace(revenue_per_kpi=None))
    )

    with mock.patch.dict(
        sys.modules,
        {
            "meridian": meridian_module,
            "meridian.analysis": analysis_module,
            "meridian.analysis.visualizer": visualizer_module,
        },
    ):
        first = facade._get_model_fit(AnalysisFilters())
        second = facade._get_model_fit(AnalysisFilters())

    assert first is second
    assert model_fit_ctor.call_count == 1
```

- [ ] **Step 8: Run the caching test**

Run: `uv run pytest tests/unit/test_analyzer_facade.py::test_model_fit_is_cached_by_use_kpi_and_confidence_level -v`
Expected: PASS (cache implemented in Step 4)

- [ ] **Step 9: Write the contract guard test for Meridian's private method**

Create `tests/contract/test_meridian_modelfit_contract.py`:

```python
"""Guard: get_model_fit depends on Meridian's ModelFit._transform_data_to_dataframe.

If a Meridian upgrade renames/removes this private method or drops the
selected_geos/selected_times parameters, get_model_fit's geo filtering breaks.
This test fails loudly and points to
docs/superpowers/plans/2026-06-29-model-fit-geo-filtering.md.
"""

from __future__ import annotations

import inspect


def test_modelfit_transform_exposes_geo_and_time_params():
    from meridian.analysis import visualizer

    assert hasattr(visualizer.ModelFit, "_transform_data_to_dataframe")
    params = inspect.signature(
        visualizer.ModelFit._transform_data_to_dataframe
    ).parameters
    assert "selected_times" in params
    assert "selected_geos" in params
```

- [ ] **Step 10: Run the contract guard test**

Run: `uv run pytest tests/contract/test_meridian_modelfit_contract.py -v`
Expected: PASS (against installed Meridian)

- [ ] **Step 11: Lint**

Run: `uv run ruff check src tests scripts && uv run ruff format src tests scripts`
Expected: no errors; formatting clean.

- [ ] **Step 12: Run the full facade + contract suites**

Run: `uv run pytest tests/unit/test_analyzer_facade.py tests/contract/test_meridian_modelfit_contract.py -v`
Expected: all PASS.

- [ ] **Step 13: Commit**

```bash
git add src/google_meridian_mcp_server/meridian/analyzer_facade.py tests/unit/test_analyzer_facade.py tests/contract/test_meridian_modelfit_contract.py
git commit -m "feat: geo-filter get_model_fit via Meridian ModelFit visualizer"
```

---

### Task 2: Service — unknown-geo validation + interrogator `geo_names`

**Files:**
- Modify: `src/google_meridian_mcp_server/meridian/interrogator.py` (add `geo_names` near `get_geos_info`, line ~48)
- Modify: `src/google_meridian_mcp_server/services/analysis_service.py` (`get_model_fit`, lines 392-406)
- Test: `tests/unit/test_analysis_service.py`

**Interfaces:**
- Consumes: `self._catalog.get_interrogator(model_id)`, `normalize_filters(filters)`, `MissingModelDataError(model_id, reason)`.
- Produces: `MeridianInterrogator.geo_names() -> list[str]`; `AnalysisService.get_model_fit` raises `MissingModelDataError` (error_code `missing_model_data`) when `filters.geos` contains a name absent from `geo_names()`.

- [ ] **Step 1: Write the failing test for unknown-geo rejection**

Add to `tests/unit/test_analysis_service.py` (the file already imports `MissingModelDataError` and `pytest`):

```python
class _ModelFitGeoCatalog:
    def __init__(self, geos, rows):
        self._geos = geos
        self._rows = rows

    class _Facade:
        def __init__(self, rows):
            self._rows = rows

        def get_model_fit(self, filters):
            return self._rows

    class _Interrogator:
        def __init__(self, geos):
            self._geos = geos

        def geo_names(self):
            return self._geos

    def get_facade(self, model_id):
        return self._Facade(self._rows)

    def get_interrogator(self, model_id):
        return self._Interrogator(self._geos)


def test_get_model_fit_rejects_unknown_geo():
    service = AnalysisService(catalog=_ModelFitGeoCatalog(geos=["us"], rows=[]))
    with pytest.raises(MissingModelDataError) as exc:
        service.get_model_fit("m", {"geos": ["__no_such_geo__"]})
    assert exc.value.error_code == "missing_model_data"
    # MissingModelDataError carries its reason in the message, not details
    # (details is just {"model_id": ...}).
    assert "__no_such_geo__" in str(exc.value)


def test_get_model_fit_accepts_known_geo():
    rows = [{"time": "2023-01-01", "expected": 1.0, "actual": 1.0, "residual": 0.0}]
    service = AnalysisService(catalog=_ModelFitGeoCatalog(geos=["us"], rows=rows))
    result = service.get_model_fit("m", {"geos": ["us"]})
    assert result["row_count"] == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_analysis_service.py::test_get_model_fit_rejects_unknown_geo tests/unit/test_analysis_service.py::test_get_model_fit_accepts_known_geo -v`
Expected: FAIL — `_ModelFitGeoCatalog._Facade` has no `get_interrogator` call path yet; `get_model_fit` does not validate geos, so the unknown-geo test does not raise.

- [ ] **Step 3: Add `geo_names` to the interrogator**

In `interrogator.py`, immediately after the `get_geos_info` method (ends ~line 60), add:

```python
    def geo_names(self) -> list[str]:
        geos = self.get_geos_info()
        if "geo" not in geos.columns:
            return []
        return [str(geo) for geo in geos["geo"].tolist()]
```

- [ ] **Step 4: Add geo validation to `AnalysisService.get_model_fit`**

In `analysis_service.py`, replace the start of `get_model_fit` (currently lines 392-396):

```python
    def get_model_fit(
        self, model_id: str, filters: AnalysisFilters | dict | None
    ) -> dict[str, Any]:
        normalized_filters = normalize_filters(filters)
        params = {"filters": self._filter_key(normalized_filters)}
```

with (insert the validation block before `params`):

```python
    def get_model_fit(
        self, model_id: str, filters: AnalysisFilters | dict | None
    ) -> dict[str, Any]:
        normalized_filters = normalize_filters(filters)
        if normalized_filters.geos:
            valid_geos = set(self._catalog.get_interrogator(model_id).geo_names())
            unknown = [geo for geo in normalized_filters.geos if geo not in valid_geos]
            if unknown:
                raise MissingModelDataError(
                    model_id, f"unknown geo(s): {', '.join(unknown)}"
                )
        params = {"filters": self._filter_key(normalized_filters)}
```

The rest of the method (the `_compute` closure and `return self._cached(...)`) is unchanged. `MissingModelDataError` is already imported in this module.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_analysis_service.py::test_get_model_fit_rejects_unknown_geo tests/unit/test_analysis_service.py::test_get_model_fit_accepts_known_geo -v`
Expected: PASS

- [ ] **Step 6: Confirm the existing no-geo test still passes**

Run: `uv run pytest tests/unit/test_analysis_service.py::test_get_model_fit_returns_columnar -v`
Expected: PASS — `_ModelFitCatalog` has no `get_interrogator`, but `filters=None` ⇒ `normalized_filters.geos == []` ⇒ validation skipped, so `get_interrogator` is never called.

- [ ] **Step 7: Lint**

Run: `uv run ruff check src tests scripts && uv run ruff format src tests scripts`
Expected: clean.

- [ ] **Step 8: Commit**

```bash
git add src/google_meridian_mcp_server/meridian/interrogator.py src/google_meridian_mcp_server/services/analysis_service.py tests/unit/test_analysis_service.py
git commit -m "feat: validate unknown geos in get_model_fit"
```

---

### Task 3: Live validation — end-to-end geo coverage

**Files:**
- Modify: `scripts/validation/runner.py` (after the single-output tools loop, lines 116-123)
- Modify: `scripts/validation/matrix.py` (`adversarial_cases`, lines 44-75)
- Test: the live validation suite itself is the test.

**Interfaces:**
- Consumes: `call(client, name, args)`, `assert_columnar(payload, label)`, `overview` (already fetched at runner.py:89; exposes `geo_names: list[str]`), `matrix.AdversarialCase`, `variant.key`.
- Produces: a `{model_id}/get_model_fit[geo]` check and a `get_model_fit` unknown-geo adversarial case per variant.

- [ ] **Step 1: Add the unknown-geo adversarial case to the matrix**

In `scripts/validation/matrix.py`, inside `adversarial_cases`, append before `return cases` (after the existing `get_spend_scenario` case at lines 64-74):

```python
    cases.append(
        AdversarialCase(
            "get_model_fit",
            {"model_id": variant.key, "filters": {"geos": ["__no_such_geo__"]}},
            "missing_model_data",
        )
    )
```

- [ ] **Step 2: Add the geo-filtered happy-path check to the runner**

In `scripts/validation/runner.py`, immediately after the `for tool in ("get_model_fit", "get_channel_data"):` block (ends line 123), insert:

```python
        # get_model_fit honors the geos filter end-to-end (transport→service→
        # facade→Meridian ModelFit). On multi-geo variants the filtered result
        # must differ from the unfiltered one; on national (1-geo) models a geo
        # filter equals no filter, so only validate shape.
        geo_names = overview.get("geo_names") or []
        if geo_names:
            label = f"{model_id}/get_model_fit[geo]"
            try:
                filtered = await call(
                    client,
                    "get_model_fit",
                    {"model_id": model_id, "filters": {"geos": [geo_names[0]]}},
                )
                assert_columnar(filtered, label)
                if len(geo_names) > 1:
                    unfiltered = await call(
                        client, "get_model_fit", {"model_id": model_id}
                    )
                    assert filtered["rows"] != unfiltered["rows"], (
                        f"{label}: geo filter not applied (rows identical to all-geo)"
                    )
                report.ok(label)
            except AssertionError as exc:
                report.fail(label, str(exc))
```

- [ ] **Step 3: Lint the scripts**

Run: `uv run ruff check scripts && uv run ruff format scripts`
Expected: clean.

- [ ] **Step 4: Run the live validation suite end-to-end**

Run: `uv run python -m scripts.validation.live_validate`
Expected: the matrix prints `get_model_fit[geo]` PASS for every variant and an `ADV/get_model_fit[...]->missing_model_data` PASS for every variant; the run ends with `LIVE VALIDATION PASSED`.

Note: if fixtures under `models/_validation/` are missing, the first run builds them via tiny real MCMC fits (a few minutes — not a hang). They are gitignored and never committed.

- [ ] **Step 5: Commit**

```bash
git add scripts/validation/runner.py scripts/validation/matrix.py
git commit -m "test: cover get_model_fit geo filtering in live validation"
```

---

### Task 4: Documentation — tool docstrings, parity doc, AGENTS

**Files:**
- Modify: `src/google_meridian_mcp_server/transport/tools.py` (`get_model_fit` docstring + `filters` field, lines 298-322)
- Modify: `docs/meridian-mcp-showcase-parity.md`
- Modify: `AGENTS.md` (live-validation expectation rules)

**Interfaces:** none (documentation + tool description copy only).

- [ ] **Step 1: Update the `get_model_fit` tool description and `filters` field**

In `transport/tools.py`, change the `filters` field description for `get_model_fit` (line 311) from:

```python
                description="Optional filters. Only start_date/end_date apply here; results are aggregated across all geos.",
```

to:

```python
                description="Optional filters: start_date/end_date slice the time range; geos restricts which markets are included before results are aggregated to one national series (per-geo breakdown is not returned).",
```

And update the tool docstring (line 315) from:

```python
        """Get model fit over time: expected vs actual outcome, baseline, and residual (actual - expected) per time period, with confidence intervals. Use this to judge how well the model tracks observed outcomes."""
```

to:

```python
        """Get model fit over time: expected vs actual outcome, baseline, and residual (actual - expected) per time period, with confidence intervals. Pass a 'geos' filter to fit only selected markets (aggregated to one series). Use this to judge how well the model tracks observed outcomes."""
```

- [ ] **Step 2: Update the parity doc**

In `docs/meridian-mcp-showcase-parity.md`:

- In the feature table, change the model-fit row's Notes from `**geo filter gap — see below**` to `geo-filterable (delegates to Meridian ModelFit)`.
- In the "Geo-level filtering parity" table, change the `Attribution → model fit` row's MCP-support cell to note `get_model_fit honors geos via Meridian's ModelFit visualizer` and its Parity cell from `✗ **gap**` to `✓`.
- Delete the entire `### Open gap: get_model_fit geo filtering` subsection.
- Add a one-line note where appropriate: national credible intervals now match the app (summed per-geo intervals from Meridian's `ModelFit`).

- [ ] **Step 3: Update AGENTS.md validation notes**

In `AGENTS.md`, under the live-validation "Expectation rules" bullet, add a sentence:

```
`get_model_fit` is valid on all variants and additionally honors a `geos`
filter (validated end-to-end); an unknown geo returns `missing_model_data`.
```

- [ ] **Step 4: Run the full test suite + lint**

Run: `uv run pytest && uv run ruff check src tests scripts`
Expected: all tests PASS; ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/google_meridian_mcp_server/transport/tools.py docs/meridian-mcp-showcase-parity.md AGENTS.md
git commit -m "docs: record get_model_fit geo filtering parity"
```

---

## Self-Review

**Spec coverage:**
- Facade delegation to Meridian `ModelFit` + caching + reshape to stable schema → Task 1.
- Both time and geo filtering delegated to Meridian → Task 1 (Step 4 passes `selected_times` and `selected_geos`).
- National CI behavior change (intentional) → documented in Global Constraints + Task 4 Step 2; test docstring note carried in Task 3 runner comment and parity doc.
- Service unknown-geo validation → Task 2.
- Tool/filter docstring updates → Task 4 Step 1.
- Live validation E2E (geo happy path, filter-applied check, unknown-geo adversarial) → Task 3.
- Output-schema regression stability → Task 1 Step 1 asserts exact column order; existing service test re-run in Task 2 Step 6.
- Private-API guard test → Task 1 Steps 9-10.
- Parity doc + AGENTS updates → Task 4.

**Placeholder scan:** No TBD/TODO; every code step shows complete code; commands have expected output. The unknown-geo test asserts on `str(exc.value)` because `MissingModelDataError.details` is `{"model_id": ...}` only (verified against `domain/errors.py`).

**Type consistency:** `get_model_fit(filters)` returns `list[dict]` (facade) wrapped by the unchanged service `_build_result`; `_get_model_fit`/`_reshape_model_fit` names used consistently; `geo_names()` defined in Task 2 Step 3 and consumed in Task 2 Step 4 and Task 3 (`overview["geo_names"]`, the existing overview key); Meridian column names (`type`/`mean`/`ci_lo`/`ci_hi`) match constants verified in the spec.

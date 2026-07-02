# Design: Geo filtering for `get_model_fit`

Date: 2026-06-29
Status: Proposed (awaiting review)

## Problem

The Attribution page in `../mmm-showcase` exposes a single-geo selector that
slices **every** chart on the page, including the expected-vs-actual **model
fit** chart (`src/ui/pages/attribution.py:323` passes `selected_geos` into
`ModelFit._transform_data_to_dataframe`). The MCP's `get_model_fit` tool does
not honor geo at all: `AnalyzerFacade.get_model_fit` calls
`expected_vs_actual_data(aggregate_geos=True)` and only forwards the date range.

This is the single remaining geo-filtering parity gap between the showcase app
and the MCP (see `docs/meridian-mcp-showcase-parity.md`). Every other in-scope
quantity that the app can filter by geo is already geo-filterable in the MCP.

### Secondary finding: national CIs already diverge

Meridian's `ModelFit` visualizer builds its dataset with `aggregate_geos=False`
and then sums **every** metric — including `ci_lo`/`ci_hi` — across geos
(`meridian/analysis/visualizer.py:690-693`, `groupby([time, type]).sum()`). So
the app's *national* model-fit credible intervals are the linear sum of per-geo
intervals.

The MCP today uses `expected_vs_actual_data(aggregate_geos=True)`, which derives
national intervals from the aggregated posterior. **The MCP and the app already
disagree on the national credible intervals**, before any geo filtering is added.

## Goal

`get_model_fit` honors the `geos` filter and produces output identical to the
showcase app's model-fit chart for both the national (no-geo) and geo-filtered
views, by using Meridian's own computation directly.

Non-goals (out of scope):

- Per-geo disaggregated output (one row per geo in a single call;
  `show_geo_level=True`). This remains the separate "Per-geo disaggregated
  metrics" future-work item in the parity doc. The app's model-fit chart also
  aggregates to national even when a geo is selected.
- Changing the columnar output schema of `get_model_fit`.
- Touching any other tool.

## Decision

**Delegate to Meridian's `ModelFit` visualizer and use its output directly — do
not reimplement the geo aggregation (including CI summation) in our facade.**

Rationale (per product decision): we want the numbers Meridian itself produces,
not a calculation layered on top. `ModelFit` is the same public visualizer the
showcase app uses, so delegating gives exact app parity and keeps every
arithmetic step inside Meridian's code. The cross-geo summation of credible
intervals is therefore Meridian's behavior, not ours.

Consequence we accept: the national (no-geo) `ci_lo`/`ci_hi` values **change**
from today's `aggregate_geos=True` proper intervals to Meridian's summed
per-geo intervals. Means, actuals, and baseline are unchanged. This is the point
— it makes the MCP match the app.

### Alternatives considered

- **Hybrid:** keep `aggregate_geos=True` when no geo filter is given, switch to
  filter-then-sum only when geos are specified. Rejected: preserves today's
  national output but keeps national CIs mismatched against the app, makes
  "omit geos" differ from "list all geos", and requires us to write the
  summation (the calculation we were asked not to own).
- **Public property + our own `.sel().sum()`:** use `ModelFit.model_fit_data`
  (public `xr.Dataset`) and aggregate ourselves. Rejected: that is "our own
  calculation on top of it," which the product decision explicitly excludes.

## Design

### Facade: `AnalyzerFacade.get_model_fit`

Replace the body that calls `expected_vs_actual_data(aggregate_geos=True)` and
manually pivots/merges. New flow:

1. Resolve `use_kpi = self.resolve_use_kpi(filters)`.
2. Get (or build and cache) a `ModelFit` for this model at
   `(use_kpi, confidence_level=0.9)`.
3. Compute `selected_times = self._expand_selected_times(filters)` (already
   exists) and `selected_geos = self._selected_geos(filters)` (already exists,
   returns `list(filters.geos) or None`).
4. Call
   `model_fit._transform_data_to_dataframe(selected_times=selected_times, selected_geos=selected_geos)`
   with `show_geo_level=False`, `include_baseline=True` (defaults). Meridian
   returns a long frame with columns `[time, type, ci_lo, ci_hi, mean]`, where
   `type ∈ {expected, baseline, actual}`, already aggregated to national across
   the selected geos.
5. Reshape to the existing wide output schema (see below). `residual` stays
   `actual − expected` (definitional, present today).

Both time and geo filtering now happen inside Meridian's transform. Today the
facade filters time *after* the fact via `filter_records`; that post-hoc step is
removed because `selected_times` is passed to Meridian directly.

#### ModelFit caching

Add a small cache analogous to `_media_summary_cache`:

```python
self._model_fit_cache: dict[tuple, Any] = {}
```

Keyed by `(use_kpi, confidence_level)`. `ModelFit.__init__` runs
`expected_vs_actual_data` (the posterior-heavy step) once; `selected_geos` and
`selected_times` are applied cheaply in `_transform_data_to_dataframe`, so one
cached `ModelFit` per `(use_kpi, confidence_level)` serves all geo/time slices.
`ModelFit` is imported lazily inside the method (mirroring how
`_get_media_summary` imports `visualizer` lazily) to keep import-time light.

#### Output schema (unchanged)

Columns, in order, exactly as today:

```
time,
expected, expected_ci_lo, expected_ci_hi,
actual,
baseline, baseline_ci_lo, baseline_ci_hi,
residual
```

Build by pivoting the long frame's `type` into columns: take `mean`/`ci_lo`/
`ci_hi` for `expected` and `baseline`; take `mean` only for `actual` (actual is
observed — its `ci_lo`/`ci_hi` collapse to the mean and are dropped, as today).
Then `residual = actual − expected`. Rounding goes through the existing
`_build_result` path unchanged.

### Service: `AnalysisService.get_model_fit`

No control-flow change — it already forwards `normalize_filters(filters)` and
caches on the filter key (which includes `geos`), so geo-keyed result caching
works automatically.

Add geo validation for a clean error: before delegating, if `filters.geos`
contains a name not in the model's geos, raise `MissingModelDataError(model_id,
"unknown geo(s): …")`. Without this, an unknown geo reaches xarray's `.sel(geo=
…)` and raises a `KeyError` that is wrapped as a generic `MissingModelDataError`
with an opaque message. Validation source: the interrogator's geo list (same
list surfaced by `get_model_overview`). National models expose a single geo and
filtering to it is valid.

### Transport: tool + filter docstrings

- `get_model_fit` tool docstring / `filters` field in
  `transport/tools.py`: remove "Only start_date/end_date apply here; results are
  aggregated across all geos." Replace with language noting that `geos` filters
  the markets included and results are aggregated to national across the
  selection (per-geo breakdown not provided).
- No change to `AnalysisFilters` — `geos` already exists and is documented.

## Error handling

| Case | Behavior |
| --- | --- |
| Unknown geo name in `geos` | `MissingModelDataError`, message lists the unknown geo(s) — validated in the service before delegating |
| `geos = []` / omitted | All geos (national aggregate), as today |
| National model (1 geo) | Works; filtering to the lone geo == national |
| Date range with no rows | Empty `rows`, `row_count = 0` (existing behavior) |
| Meridian raises during transform | Wrapped as `MissingModelDataError` (existing `try/except` in the service `_compute`) |

## Testing

- **Unit (`tests/unit/test_analyzer_facade.py`):**
  - Geo-filtered fit on a multi-geo fixture differs from the all-geo fit (geo
    filter is actually applied).
  - Single-geo selection returns that geo's aggregate; selecting all geos equals
    the no-geo result (consistency of the single delegated path).
  - National (1-geo) model returns a valid table with the geo filter set to its
    only geo.
  - A guard test that asserts `ModelFit._transform_data_to_dataframe` is present
    with the expected parameters, so a Meridian upgrade that changes this private
    method fails loudly here (see Risks).
- **Unit (`tests/unit/test_analysis_service.py`):** unknown-geo →
  `MissingModelDataError` with the offending name in the message.
- **Output-schema regression:** existing `get_model_fit` contract/unit
  assertions on column names must still pass unchanged (schema is stable).
- Note in the relevant test or its docstring that national `ci_lo`/`ci_hi`
  values intentionally changed to match the app.

### Live validation: end-to-end geo coverage (required)

The live validation suite (`scripts/validation/`) is the integration acceptance
gate — it drives an in-process FastMCP `Client(mcp)` over real fitted fixtures.
Today it calls `get_model_fit` with only `{"model_id": model_id}` and no filter
(`runner.py:117-123`). This change MUST extend that to exercise geo filtering
end-to-end through the actual tool call, so the whole path
(transport → service → facade → Meridian `ModelFit`) is tested, not just the
facade in isolation.

Required additions:

1. **Geo-filtered happy path (`scripts/validation/runner.py`).** In the
   per-variant loop, after the existing unfiltered `get_model_fit` call, pull a
   real geo from the overview (`overview["geos"]` — the same list
   `get_model_overview` already returns) and call:

   ```python
   await call(client, "get_model_fit",
              {"model_id": model_id, "filters": {"geos": [geo]}})
   ```

   Assert `assert_columnar` on the result. Label it distinctly, e.g.
   `{model_id}/get_model_fit[geo]`.

2. **Filter-actually-applied check.** On multi-geo variants (`variant.n_geos >
   1` — the `geo-*` fixtures), assert the geo-filtered rows differ from the
   unfiltered rows — proving the `geos` filter is wired through E2E and not
   silently ignored. On single-geo (national, `n_geos == 1`) variants, filtering
   to the lone geo equals the unfiltered result, so only assert columnar
   validity there. The geo to filter on is read at runtime from
   `overview["geos"]`.

3. **Unknown-geo error path (`scripts/validation/matrix.py`).** Add an
   `AdversarialCase` to `adversarial_cases(variant)` for every variant:

   ```python
   AdversarialCase(
       "get_model_fit",
       {"model_id": variant.key, "filters": {"geos": ["__no_such_geo__"]}},
       "missing_model_data",
   )
   ```

   This exercises the service-layer geo validation end-to-end and asserts the
   typed `missing_model_data` error rather than a crash.

4. The suite must end with `LIVE VALIDATION PASSED` across the full
   variant × tool matrix with these additions, on both national and geo
   fixtures.

## Risks & mitigations

- **Private Meridian API.** `_transform_data_to_dataframe` is underscore-private.
  The sibling showcase app already depends on it, so the coupling is shared and
  visible. Mitigation: the guard unit test above fails loudly if the
  method/signature changes on a Meridian upgrade, pointing to this design.
- **National CI behavior change.** Documented and intentional. Mitigation: call
  it out in the parity doc, in `AGENTS.md` validation notes if needed, and in
  the test docstring so it is not mistaken for a regression.

## Documentation updates

- `docs/meridian-mcp-showcase-parity.md`: move the model-fit row to fully
  supported, remove it from the "Open gap" section, and record that national
  CIs now match the app (summed per-geo intervals).
- `AGENTS.md`: if the live-validation expectation rules enumerate per-tool geo
  behavior, add `get_model_fit` geo support.

## Affected files

- `src/google_meridian_mcp_server/meridian/analyzer_facade.py` — rewrite
  `get_model_fit`, add `_model_fit_cache`.
- `src/google_meridian_mcp_server/services/analysis_service.py` — geo validation
  in `get_model_fit`.
- `src/google_meridian_mcp_server/transport/tools.py` — docstring updates.
- `tests/unit/test_analyzer_facade.py`, `tests/unit/test_analysis_service.py` —
  new tests.
- `scripts/validation/runner.py` — drive `get_model_fit` with a `geos` filter
  end-to-end; assert filter-applied on multi-geo variants.
- `scripts/validation/matrix.py` — unknown-geo `get_model_fit` adversarial case.
- `docs/meridian-mcp-showcase-parity.md`, `AGENTS.md` — docs.

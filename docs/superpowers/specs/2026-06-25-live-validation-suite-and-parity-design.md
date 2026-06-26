# Live MCP Validation Suite, Metric-Validity Fixes & Showcase Parity — Design

**Status:** Approved design (brainstorming output). Implementation plan to follow via writing-plans.
**Date:** 2026-06-25

## Goal

Make the Meridian MCP server provably correct across the full space of model
variants — national vs geo, and revenue vs KPI — by (a) generating dummy
models for every variant, (b) fixing the server so unsupported metrics fail
gracefully and overview output reflects each model's real capabilities, (c)
adding the tools needed to reach parity with the mmm-showcase app, and (d)
shipping a reusable live validation suite that asserts the whole matrix
against an in-process MCP client.

## Architecture

Five components, executed in order, plus a parity report written alongside:

1. **Dummy-model generator** — synthetic fitted models for every variant, on disk.
2. **Metric-validity fixes** — graceful typed errors + dynamic capability reporting.
3. **New tools** — `get_model_fit`, `get_reach_frequency`, `get_channel_data`.
4. **Live validation suite** — reusable matrix harness over an in-process client.
5. **Parity gap report** — every in-scope showcase chart mapped to a tool.

The suite (4) is the acceptance gate; it can only assert graceful behavior
after (2) and must cover the tools from (3). Hence the 1→2→3→4 order.

## Tech Stack

- Python 3.12+, `uv`-managed venv. All commands via `uv run`.
- `google-meridian==1.7.0` (`meridian.data.test_utils`, `meridian.analysis.analyzer.Analyzer`, `meridian.analysis.visualizer`, `meridian.model`, `meridian.schema.serde.meridian_serde`).
- FastMCP 3.4 in-process `Client(mcp)` for live testing.
- Existing layers: `transport/tools.py` → `services/analysis_service.py` → `meridian/analyzer_facade.py` / `meridian/interrogator.py` / `meridian/dataset_mapper.py`.

## Global Constraints

- Python `>=3.12,<3.15`; `google-meridian==1.7.0`.
- Columnar output envelope is the contract for all row-oriented tools:
  `{model_id, <selector>, columns, rows[][], row_count}`. No `data` key, no
  `result_metadata`. Measure floats round to 6 significant figures. New tools
  MUST emit this same envelope via `AnalysisService._build_result`.
- Grouped analysis tools return **posterior-only** rows; no `distribution` column.
- Generated model fixtures live under a **gitignored** `models/_validation/`
  directory; no model binaries are committed to git.
- Metric-validity contract = **Meridian truth**: CPIK and marginal CPIK are
  valid on every model; `roi` and `marginal_roi` are valid **only** when the
  model has revenue (`revenue_per_kpi is not None`, which includes
  `kpi_type=REVENUE` models whose `revenue_per_kpi` is auto-filled with ones).
- Domain errors flow `facade/service → MeridianMcpError subclass → transport
  error payload` `{error_code, message, details}`. New failure modes use typed
  domain errors, never bare exceptions.

---

## Background: facts that drive the design

From direct reading of `references/meridian` and the MCP source:

- **KPI vs revenue** is decided by `input_data.revenue_per_kpi is not None`.
  `kpi_type=REVENUE` auto-fills `revenue_per_kpi` with ones
  (`input_data.py:500-533`), so revenue models always have revenue. A
  `NON_REVENUE` model may or may not carry `revenue_per_kpi`.
- `Analyzer._use_kpi` (`analyzer.py:335-365`) **silently degrades**: asking ROI
  on a no-revenue model does not raise and does not return NaN — it warns and
  returns KPI-scaled numbers. CPIK forces `use_kpi=True` and is always valid
  (`analyzer.py:1782-1855`). Therefore the *only* output types that are
  genuinely invalid without revenue are `roi` and `marginal_roi`.
- **National = `n_geos == 1`** (`context.py:526-528`). Geo arguments on a
  national model are warned-and-coerced, never errored.
- `meridian.data.test_utils` provides ready builders:
  `sample_input_data_revenue`, `sample_input_data_non_revenue_revenue_per_kpi`,
  `sample_input_data_non_revenue_no_revenue_per_kpi` (each takes `n_geos`,
  `n_times`, channel counts, `seed`). A cheap real fit is
  `sample_prior` + `sample_posterior(n_chains=1, small draws)`. Persist with
  `meridian_serde.save_meridian(mmm, path)` for `.binpb`.

Current MCP gaps confirmed:

- `get_model_overview.available_tool_options` is **static** — it advertises
  `roi`/`cpik`/`marginal_roi`/`marginal_cpik` for every model, including
  KPI-only ones (`analysis_service.py:210-226`).
- The facade defaults `use_kpi=bool(filters.use_kpi)` = `False` everywhere, so
  no-revenue models are queried in revenue mode by default
  (`analyzer_facade.py:86,146,383,391`).
- `roi`/`marginal_roi`/`cpik`/`marginal_cpik` return an **empty list** when the
  expected var is absent — silent empty rather than a clear signal.
- `get_training_data` **ignores** its date/geo/channel filters despite the
  docstring promising slicing (`dataset_mapper.extract_training_datasets`).
- `aggregate_geos` is a **dead filter field** — no analysis facade method reads it.

mmm-showcase parity gaps (in-scope pages only; Summary Report, Optimization,
Model Diagnostics excluded): the app shows **model fit** (expected-vs-actual +
baseline + residual, via `ModelFit`/`expected_vs_actual_data`), **optimal
frequency / ROI-vs-frequency** (via `ReachAndFrequency`/`optimal_freq`), and
**raw channel input series** (Data Exploration). VIF is computed app-side with
statsmodels and is out of scope. The first two map to new tools; the third maps
to the new `get_channel_data` tool.

---

## Component 1 — Dummy-model generator

**File:** `scripts/generate_validation_models.py` (CLI; idempotent; `--force` rebuilds; `--out` overrides output dir).

**Variants (7 fixtures):**

| key | builder | n_geos | RF? | has_revenue |
|---|---|---|---|---|
| `national-revenue` | `sample_input_data_revenue` | 1 | yes | yes |
| `geo-revenue` | `sample_input_data_revenue` | 5 | yes | yes |
| `national-kpi-rpk` | `sample_input_data_non_revenue_revenue_per_kpi` | 1 | yes | yes |
| `geo-kpi-rpk` | `sample_input_data_non_revenue_revenue_per_kpi` | 5 | yes | yes |
| `national-kpi-only` | `sample_input_data_non_revenue_no_revenue_per_kpi` | 1 | yes | no |
| `geo-kpi-only` | `sample_input_data_non_revenue_no_revenue_per_kpi` | 5 | yes | no |
| `geo-revenue-media-only` | `sample_input_data_revenue` (no RF channels) | 5 | **no** | yes |

The first six form the clean 2×3 (geo × kpi) matrix, all with RF so reach/
frequency and `alpha_summary` RF paths are exercised. The seventh is a
targeted media-only fixture so the suite can assert the **no-RF graceful
error** path for `get_reach_frequency`.

**Channel shape:** RF variants build with 3 paid media + 2 RF channels, plus at
least 1 organic media, 1 organic RF, and 1 non-media treatment channel so
`get_channel_data` covers every channel type. The media-only variant omits RF
and organic RF.

**Fit:** construct `Meridian(input_data, ModelSpec())`; national variants let
Meridian apply its national spec automatically (`n_geos==1`). Run
`sample_prior(...)` then `sample_posterior(n_chains=1, n_adapt/n_burnin small,
n_keep≈10, seed=fixed)`. The suite asserts **shape and graceful behavior, not
numeric accuracy**, so a tiny posterior is sufficient and deterministic.

**Persistence:** save each model to `models/_validation/<key>/model.binpb` via
`meridian_serde.save_meridian`. Additionally save `national-revenue` as
`model.pkl` (a sibling fixture dir `national-revenue-pkl`) so the loader's
pickle branch is exercised. Skip any fixture whose file already exists unless
`--force`.

**Output dir:** `models/_validation/` (gitignored). Generator prints the path
and a one-line summary per fixture.

---

## Component 2 — Metric-validity fixes

**2.1 New domain error.** `domain/errors.py`: add `MetricNotSupportedError`
(subclass of `MeridianMcpError`) with `error_code = "metric_not_supported"` and
`details = {"model_id", "output_type", "reason"}`. Message reads, e.g.,
`Metric 'roi' is not supported for model '<id>': model has no revenue_per_kpi`.

**2.2 Revenue-gated metrics.** In `services/analysis_service.py`
`_run_facade_query` (and thus `get_channel_summary`), before computing: if
`output_type in {"roi", "marginal_roi"}` and the model has no revenue, raise
`MetricNotSupportedError`. Revenue capability comes from the interrogator —
`get_interrogator(model_id).has_revenue_per_kpi` (already computed at
`interrogator.py:162-163`). Resolve the interrogator once and reuse.

**2.3 Dynamic overview.** In `get_model_overview`, build
`available_tool_options` from the model's traits:
- `get_channel_summary.output_type`: full list for revenue models; drop `roi`
  and `marginal_roi` for no-revenue models.
- Add `get_reach_frequency` to the options **only when the model has RF
  channels**; add `get_model_fit` and `get_channel_data` unconditionally.
The overview payload also gains a `metric_views`/`has_revenue_per_kpi` echo
(already present) so agents have a machine-readable capability signal.

**2.4 Effective `use_kpi`.** In `meridian/analyzer_facade.py`, resolve the
effective `use_kpi` from the model's revenue capability when the caller did not
specify it: `use_kpi = filters.use_kpi if filters.use_kpi is not None else (not
has_revenue)`. No-revenue models are then queried in KPI mode (correct units,
no silent revenue-degrade); revenue models keep revenue mode. Explicit caller
values are still honored. The facade reads revenue capability from the model's
`input_data.revenue_per_kpi`.

**2.5 Folded-in correctness fixes.**
- `get_training_data` honors its filters: `extract_training_datasets` (or its
  caller) applies `start_date`/`end_date`, `geos`, and `channels` slicing to the
  merged rows before building the result. Datasets without a given dimension are
  unaffected by that dimension's filter.
- Remove the dead `aggregate_geos` field from `AnalysisFilters`
  (`domain/filters.py`) and any references; per-geo disaggregation is recorded
  as future work in the parity report. (The `geos` filter, which *is* wired
  through as `selected_geos`, stays.)

---

## Component 3 — New tools

All three emit the columnar envelope and are wired
`transport/tools.py` → `services/analysis_service.py` →
`meridian/analyzer_facade.py` (or `dataset_mapper.py` for channel data), with
unit tests and doc updates (AGENTS.md, README.md).

**3.1 `get_model_fit(model_id, filters)`** — backed by
`Analyzer.expected_vs_actual_data(aggregate_geos=True, aggregate_times=False,
use_kpi=<resolved>, confidence_level=0.9)`. Geo-aggregated (the underlying API
takes only `aggregate_geos`/`aggregate_times`, not `selected_geos`/`times`), so
`geos` is **not** a supported filter here; `start_date`/`end_date` are honored by
post-filtering the flattened rows on `time`. The returned Dataset carries
`expected` and `baseline` over a `metric` coord (`mean`/`ci_lo`/`ci_hi`) plus
`actual` (no `metric` dim); the facade pivots `metric` to wide columns. One row
per time period; columns: `time`, `expected`, `expected_ci_lo`,
`expected_ci_hi`, `actual`, `baseline`, `baseline_ci_lo`, `baseline_ci_hi`,
`residual` (= `actual − expected`). No `output_type` (single output).

**3.2 `get_reach_frequency(model_id, filters)`** — backed by
`visualizer.ReachAndFrequency(model).optimal_frequency_data` /
`Analyzer.optimal_freq`. One row per (channel, frequency); columns: `channel`,
`frequency`, `roi`, `ci_lo`, `ci_hi`, `optimal_frequency`. **RF-only**: if the
model has no RF channels, raise `MetricNotSupportedError`
(reason: `model has no reach & frequency channels`). No `output_type`. Honors
date/geo/channel filters where the underlying API allows.

**3.3 `get_channel_data(model_id, filters)`** — the per-channel investigate
view. Channel-keyed **long** format merging every channel-dimensioned input.
One row per (channel, geo, time); columns:
`channel`, `channel_type`, `geo`, `time`, `impressions`, `spend`, `reach`,
`frequency`, `rf_spend`, `value` — null where a column doesn't apply to that
channel type. `channel_type ∈ {paid_media, rf, organic_media, organic_rf,
non_media}`. Mapping of source `input_data` arrays:
- `paid_media`: `media` → `impressions`, `media_spend` → `spend`.
- `rf`: `reach` → `reach`, `frequency` → `frequency`, `rf_spend` → `rf_spend`.
- `organic_media`: `organic_media` → `impressions`.
- `organic_rf`: `organic_reach` → `reach`, `organic_frequency` → `frequency`.
- `non_media`: `non_media_treatments` → `value`.
Honors `start_date`/`end_date`, `geos`, and `channels` filters. Built in
`dataset_mapper.py` (a new builder alongside `extract_training_datasets`), since
it is input-data extraction, not an Analyzer computation. Non-channel series
(kpi, revenue_per_kpi, controls, population) remain the province of
`get_training_data`.

**Disambiguating docstrings (so the LLM picks the right one).** Keep the two
tools as separate, single-purpose tools (a `layout`-flag merge is rejected: the
two views select by different keys — datasets vs channels — so a mode flag would
make parameter validity mode-dependent, which degrades LLM tool selection). The
tool descriptions must be crisp and non-overlapping:
- `get_training_data`: "Raw input datasets by name — including non-channel
  series (KPI, revenue-per-KPI, controls, population). Use when you want a
  specific dataset as stored."
- `get_channel_data`: "Everything about a channel in one table — spend,
  impressions, reach/frequency — across all channel types. Use to investigate
  one or more channels directly."

---

## Component 4 — Live validation suite

**Package:** `scripts/validation/` — `matrix.py` (declarative expectations),
`runner.py` (client driver + assertions), and `live_validate.py` (entrypoint).
Supersedes `scripts/live_verify.py`, which is removed.

**Setup:** the entrypoint points `LOCAL_MODELS_ROOT` at `models/_validation`,
invokes the Component-1 generator if fixtures are missing (build-if-missing),
constructs the server with the local backend, and opens an in-process
`Client(mcp)`.

**Expectation model:** each variant has known traits (`has_revenue`,
`is_national`, `has_rf`) derived from its key. Per-tool validity is a predicate:
- `roi`, `marginal_roi` → valid iff `has_revenue`, else expect
  `metric_not_supported`.
- `get_reach_frequency` → valid iff `has_rf`, else expect `metric_not_supported`.
- every other tool/output_type → valid on all variants.

**Assertions:**
- `assert_columnar(payload)` — `model_id`, `columns`, `rows`, `row_count`
  present; `row_count == len(rows)`; no ragged rows; no legacy `data` /
  `result_metadata` keys; rows non-empty for combos expected to produce data.
- `assert_error(payload, code)` — `error_code == code`; non-empty `message`.

**Adversarial cases (per applicable variant):**
- `roi` / `marginal_roi` on a kpi-only model → `metric_not_supported`.
- `get_reach_frequency` on the media-only model → `metric_not_supported`.
- `get_model_overview` for a kpi-only model must **not** list `roi`/
  `marginal_roi` in `available_tool_options` (asserts 2.3 pruning).
- Bogus geo name on a national model → handled cleanly (error or coerced, asserted, no crash).
- Unknown `model_id` → typed not-found/error payload.
- Unknown channel filter → empty result (asserted) rather than crash.
- Inverted date range (`end_date < start_date`) → typed error or empty, asserted.

**National × geo coverage:** geo-sensitive tools (`get_channel_summary`,
`get_response_curves`, `get_model_fit`, `get_channel_data`) run against both a
national and a geo variant; national must not crash and per-geo requests are
handled cleanly.

**Output:** a `variant × tool` matrix printed as PASS / `EXPECTED-ERR` / FAIL,
plus summary counts. Exits **non-zero** on any mismatch so it is usable as a
live regression gate (`uv run python -m scripts.validation.live_validate` or
`uv run scripts/validation/live_validate.py`).

---

## Component 5 — Parity gap report

**File:** `docs/meridian-mcp-showcase-parity.md`. A table with one row per
in-scope mmm-showcase chart/data point (Home, Response Curves, Attribution, Lag
Effects, Reach & Frequency, Data Exploration — excluding Summary Report,
Optimization, Model Diagnostics). Columns: showcase item → backing Meridian
quantity → MCP tool → status (Supported / Partial / Unsupported / Out-of-scope)
→ notes. The report confirms `get_model_fit`, `get_reach_frequency`, and
`get_channel_data` close the previously-unsupported items, records VIF and
app-side efficiency arithmetic as out-of-scope, and lists any remaining
partials (e.g. per-geo disaggregation) as future work.

---

## Error contract (summary)

| Situation | error_code |
|---|---|
| `roi`/`marginal_roi` on no-revenue model | `metric_not_supported` |
| `get_reach_frequency` on no-RF model | `metric_not_supported` |
| Genuine Meridian computation failure | `missing_model_data` |
| Unknown/invalid `output_type` (defense-in-depth) | `invalid_output_type` |
| Unknown/empty training dataset | existing dataset error |
| Unknown `model_id` | existing catalog/not-found error |

## Out of scope

- VIF / multicollinearity (computed app-side with statsmodels, not a Meridian output).
- App-side efficiency arithmetic in the showcase Response Curves table (derivable by the agent from existing tool outputs).
- Per-geo disaggregated output (`aggregate_geos=False`): the dead flag is removed now; real disaggregation is future work.
- Summary Report, Budget Optimization, and Model Diagnostics showcase pages.
- Numeric-accuracy assertions on the dummy models (suite checks shape + graceful behavior only).

## Testing strategy

- **Unit tests** for every Component-2 and Component-3 change: at least one
  happy path and one error path each (e.g. `metric_not_supported` raised for
  `roi` on a no-revenue model; dynamic `available_tool_options` pruning;
  `get_model_fit`/`get_reach_frequency`/`get_channel_data` columnar shape;
  `get_training_data` filter application; effective `use_kpi` resolution).
- **The live validation suite** is the integration/acceptance gate over real
  fitted models across all variants.
- Existing gates stay green: `uv run pytest`, `uv run ruff check src tests`.

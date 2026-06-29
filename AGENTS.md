# AGENTS
## Project Focus
This repository builds a FastMCP server that exposes Google Meridian analysis tools to agents.
The design keeps the transport layer thin and pushes behavior into services, Meridian adapters,
and persistence helpers so agents can inspect models and request structured outputs safely.

## Runtime Shape
1. `server.py` creates the FastMCP app and lifespan state.
2. `config.py` reads environment variables into `RuntimeConfig`.
3. The lifespan selects `local` or `gcs` persistence.
4. Persistence caches wrap discovery, materialization, and result reuse.
5. `ModelCatalog` resolves a model id into a loaded Meridian model.
6. `transport/tools.py` maps tool calls to services.
7. `services/analysis_service.py` validates inputs and shapes responses.
8. `meridian/` contains Meridian-aware data extraction and analysis logic.

## Working Boundaries
- Keep agent-facing tool contracts in `src/google_meridian_mcp_server/transport/tools.py`.
- Keep orchestration logic in `src/google_meridian_mcp_server/services/`.
- Keep core validation and reusable types in `src/google_meridian_mcp_server/domain/`.
- Keep Meridian-specific adapter code in `src/google_meridian_mcp_server/meridian/`.
- Keep backend access and cache materialization in `src/google_meridian_mcp_server/persistence/`.
- Treat `references/` and `specs/` as reference material; do not couple runtime code to them.

## Key Files
- `src/google_meridian_mcp_server/server.py`
- `src/google_meridian_mcp_server/config.py`
- `src/google_meridian_mcp_server/transport/tools.py`
- `src/google_meridian_mcp_server/services/analysis_service.py`
- `src/google_meridian_mcp_server/services/model_catalog_service.py`
- `src/google_meridian_mcp_server/meridian/catalog.py`
- `src/google_meridian_mcp_server/meridian/interrogator.py`
- `src/google_meridian_mcp_server/meridian/analyzer_facade.py`
- `src/google_meridian_mcp_server/meridian/dataset_mapper.py`
- `src/google_meridian_mcp_server/persistence/base.py`
- `src/google_meridian_mcp_server/persistence/local_provider.py`
- `src/google_meridian_mcp_server/persistence/gcs_provider.py`
- `src/google_meridian_mcp_server/persistence/cache.py`

## Configuration
- `.env` and `.env.example` live at the repository root.
- External transport is configured as `streamable-http`.
- The FastMCP runner uses HTTP under the hood for the network transport.
- Local models may be flat files or nested paths such as `models/geo-revenue/model.binpb`.
- `LOCAL_MODELS_ROOT` is required for `PERSISTENCE_BACKEND=local`.
- `GCS_BUCKET` and `GCS_MODELS_PREFIX` are required for `PERSISTENCE_BACKEND=gcs`.
- `MODEL_CACHE_ROOT` defaults to `/tmp/mmm-models`.
- `RESULT_CACHE_ENABLED` defaults to true.
- `RESULT_CACHE_TTL_SECONDS` is optional but must be positive when set.
- `DISCOVERY_TTL_SECONDS` must be positive.

## Common Commands
- `uv run python -m google_meridian_mcp_server.server`
- `uv run pytest`
- `uv run ruff check src tests scripts`
- `uv run ruff format src tests scripts`
- `uv run python -m scripts.validation.live_validate` — live validation suite (see below)
- `uv run python scripts/generate_validation_models.py [--force]` — (re)build dummy fixtures

## Live Validation & Dummy Models
The live validation suite is the integration acceptance gate. It drives an
in-process FastMCP `Client(mcp)` over every tool across a matrix of dummy
fitted Meridian models — national vs geo, revenue vs KPI — plus adversarial
error-path checks, and exits non-zero on any mismatch.

- **Run it:** `uv run python -m scripts.validation.live_validate` (add `--force`
  to rebuild fixtures). The first run BUILDS the fixtures via real tiny
  MCMC fits — it takes a few minutes and is NOT a hang. It prints a
  variant×tool PASS / EXPECTED-ERR / FAIL matrix and ends with
  `LIVE VALIDATION PASSED` / `N failed`.
- **Fixtures** live under gitignored `models/_validation/` and are NEVER
  committed. The suite builds them if missing (build-if-missing).
- **Generator** `scripts/generate_validation_models.py` builds 7 variants:
  the 2×3 matrix `national|geo` × `revenue | kpi+revenue_per_kpi | kpi-only`
  (all with reach & frequency channels), plus one media-only
  `geo-revenue-media-only` (no RF, for the no-RF graceful-error path), plus a
  `.pkl` copy of `national-revenue` to exercise the loader's pickle branch.
  Model id == fixture directory name.
- **Suite layout:** `scripts/validation/matrix.py` (declarative expectations —
  which `(tool, output_type)` are expected-valid vs expected-error per variant,
  via `expected_valid`/`adversarial_cases`), `scripts/validation/runner.py`
  (client driver + `assert_columnar`/`assert_error`),
  `scripts/validation/live_validate.py` (entrypoint).
- **Expectation rules:** `roi`/`marginal_roi` are valid only for revenue and
  kpi+revenue_per_kpi variants (→ `metric_not_supported` on kpi-only);
  `get_reach_frequency` is valid only for RF variants; everything else is valid
  on all variants. Fixtures include organic media/RF + non-media channels so
  `get_channel_data` and `alpha_summary` exercise every channel type.
  `get_spend_scenario` is valid on every variant (channel derived from the
  overview); `outcome_mode` is `revenue` for revenue/kpi+rpk variants and `kpi`
  for kpi-only; an unknown channel returns `missing_model_data`.
  `get_model_fit` is valid on all variants and additionally honors a `geos`
  filter (validated end-to-end); an unknown geo returns `missing_model_data`.
- Showcase ↔ tool parity is tracked in `docs/meridian-mcp-showcase-parity.md`.

## Module Map
- **domain/models.py** — enums, `RuntimeConfig`, `ModelCatalogEntry`.
- **domain/filters.py** — MCP filter schema; normalizes channels/geos; defines output-type literals.
- **domain/errors.py** — error hierarchy with stable codes and payload details.
- **persistence/base.py** — shared path helpers: `build_model_id`, `build_display_name`, `build_cache_path`.
- **persistence/local_provider.py** — walks local directory; emits `ModelCatalogEntry` from filesystem metadata.
- **persistence/gcs_provider.py** — GCS client; converts blob names to stable ids; downloads to local cache.
- **persistence/cache.py** — `DiscoveryCache`, `MaterializationCache`, `ResultCache` (TTL-keyed in-memory).
- **meridian/loader.py** — auto-detects `.binpb` vs `.pkl`; loads through Meridian serde APIs.
- **meridian/catalog.py** — bridges entries to loaded Meridian objects; memoizes models and facades.
- **meridian/dataset_mapper.py** — converts xarray datasets to JSON-safe row dicts; merges on shared dims. `filter_records` slices rows by date/geo/channel (reused by training-data, channel-data — NOT model-fit anymore); `extract_channel_data` builds the per-channel long table. `_df_to_records` maps NaN → JSON `null` (numeric cells need `astype(object)` first).
- **meridian/interrogator.py** — model metadata extraction; builds `get_model_overview` payload. `geo_names()` returns the model's geo coord values (used by the service to validate `get_model_fit` geo filters).
- **meridian/analyzer_facade.py** — wraps `Analyzer`, `MediaSummary`, and the `ModelFit` visualizer; executes analysis; normalizes to posterior-only payloads. `get_model_fit` delegates geo/time selection to Meridian's `ModelFit` (cached per `(use_kpi, confidence_level)`), then `_reshape_model_fit` pivots the long frame to the wide schema.
- **services/model_catalog_service.py** — serializes `ModelCatalogEntry`; converts timestamps to ISO-8601.
- **services/analysis_service.py** — filter normalization; dispatch; result-cache integration; model-overview shaping.
- **transport/tools.py** — registers FastMCP tools; converts domain errors to standard error payload.
- **server.py** — lifespan startup; provider selection; `create_server()`, `mcp`, `run_server()`.

## Current Tool Surface
- `list_models`
- `get_model_overview`
- `get_training_data`
- `get_channel_summary`
- `get_contribution`
- `get_adstock_decay`
- `get_response_curves`
- `get_model_fit`
- `get_reach_frequency`
- `get_channel_data`
- `get_spend_scenario`

## Model Overview Expectations
The overview tool should tell an agent:
- what type of model it is
- whether the model is national or geo-based
- the time bounds and available dates
- which geos and populations exist
- which paid media, RF, organic, non-media, and control inputs exist
- which flat input column names appear in tabular views
- which training datasets are actually available
- which output types are valid for the other MCP tools

## Current Analysis Behavior
- Tabular tools return a columnar envelope: `model_id`, `output_type` (or `datasets`/`dataset`), `columns`, `rows`, `row_count`. No `data` key, no `result_metadata`.
- Measure floats are rounded to 6 significant figures.
- `get_model_overview` returns a nested object with `available_tool_options`; it has no `result_metadata`.
- `list_models` returns a list of serialized `ModelCatalogEntry` objects, keyed `model_id`, `display_name`, `model_format`, `source_backend`, `source_path`, `last_modified`, `status`, `etag_or_fingerprint`, `metadata`.
- Grouped analysis tools return posterior-only rows; no `distribution` column.
- `get_channel_summary` baseline summaries come from Meridian's analyzer baseline API, not `MediaSummary`.
- `marginal_roi` is sourced from Meridian's `mroi` output.
- `marginal_cpik` is derived from posterior `mroi` values and must keep CI bounds ordered after inversion.
- `get_response_curves` should return numeric curve rows, not channel metadata placeholders.
- `response_curve_summary` should return numeric summary rows with `channel`, `spend`, `spend_multiplier`, `mean`, `ci_lo`, and `ci_hi`.
- `roi` and `marginal_roi` raise `metric_not_supported` for models without revenue (`revenue_per_kpi is None`); `cpik`/`marginal_cpik` are valid for all models.
- `get_model_overview.available_tool_options` is dynamic: it omits `roi`/`marginal_roi` for no-revenue models and lists `get_reach_frequency` only for models with reach & frequency channels.
- The facade resolves `use_kpi` from the model's revenue capability when the caller does not set it (no-revenue models default to KPI mode).
- `get_training_data` applies date/geo/channel filters to the merged rows; the dead `aggregate_geos` filter field has been removed.
- `get_model_fit` returns expected/actual/baseline/residual over time and honors the `geos` filter. It delegates to Meridian's `ModelFit` visualizer and its private `_transform_data_to_dataframe(selected_times, selected_geos)`, which selects geos/times and aggregates to ONE national series inside Meridian — we do NOT reimplement the aggregation or CI math. National `ci_lo`/`ci_hi` are therefore Meridian's summed per-geo intervals (this matches the showcase app; means/actuals/baseline are unchanged — a deliberate change from the old `aggregate_geos=True` intervals, not a regression). An unknown geo raises `missing_model_data` (validated in the service via `interrogator.geo_names()`). This private-API + long-frame-schema coupling is guarded by `tests/contract/test_meridian_modelfit_contract.py` — if a Meridian upgrade breaks it, that test fails loudly. `get_reach_frequency` returns optimal-frequency ROI curves (RF-only, else `metric_not_supported`). `get_channel_data` returns a per-channel long table across all channel types.
- `get_training_data` vs `get_channel_data`: training-data is the raw per-dataset extractor (select by dataset name; the only path to non-channel series like KPI/controls/population); channel-data is the per-channel unified long view stacking every channel-keyed input (select by channel). They are separate tools by design — do not merge them behind a layout flag (the two select by different keys).
- `get_spend_scenario` simulates one channel's spend: inputs `channel`,
  `spend_increase`, optional `base_spend` (all PER TIME UNIT; base defaults to
  the channel's historical average over the slice), returns a summary object
  with `outcome_mode` (`revenue`|`kpi`) and an efficiency triplet
  (`efficiency`/`marginal_efficiency`/`efficiency_at_new` = ROI/mROI/ROI-at-new
  for revenue models, CPIK/mCPIK otherwise). Zero-denominator ratios return
  `null`. It activates the previously-staged saturation engine
  (`apply_saturation`/`get_data`); `get_carryover` remains unused.

## Current Test Coverage
- **unit/** — config/persistence, catalog/loader, interrogator, analysis_service, analyzer_facade, transport_tools, server, model_catalog_service, result_cache.
- **integration/** — provider filesystem behavior and cache interaction.
- **contract/** — supported enums and public tool-surface expectations; `test_meridian_modelfit_contract.py` guards Meridian's private `ModelFit._transform_data_to_dataframe` signature plus the long-frame schema constants (`type`/`mean`/`ci_lo`/`ci_hi`/`expected`/`baseline`/`actual`) that `get_model_fit` depends on.

## Editing Guidance
- Reuse `MeridianInterrogator` for shared model metadata and data extraction.
- Keep service responses JSON-safe and stable for agents.
- Prefer deterministic ordering in public payloads.
- Do not introduce broad exception swallowing.
- When adding a tool, wire it through `transport -> service -> meridian`.
- When adding model metadata, consider whether it belongs in the overview payload.

## Test Strategy Guidance
- Favor unit tests with xarray and pandas fakes for Meridian-facing logic.
- Favor mock-based tests for GCS, FastMCP, and Meridian import boundaries.
- Keep integration tests for provider filesystem behavior and cache interaction.
- Use contract tests for external shapes and documented enums.
- When adding a new analysis branch, add at least one happy path and one error path test.

## Working Guidelines
- Think before coding: state assumptions; if multiple interpretations exist, surface them; ask when unclear.
- Simplicity first: minimum code that solves the problem; no speculative abstractions, flags, or error handling for impossible cases.
- Surgical changes: touch only what the task requires; match existing style; don't refactor or reformat adjacent code; mention unrelated dead code instead of deleting it.
- Remove only the orphans your own change creates (now-unused imports/vars).
- Goal-driven execution: turn each task into a verifiable check (write/adjust a test, then make it pass); loop until `uv run pytest` and `uv run ruff check src tests` are green.

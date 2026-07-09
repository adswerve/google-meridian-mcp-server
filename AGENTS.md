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
- `REGISTRY_BACKEND` — `local` (Phase 1) or `gcs` (Phase 2); defaults to `PERSISTENCE_BACKEND`.
- `OPTIMIZATION_RUNS_ROOT` — local directory for run manifests, state files, and results; default `./optimizations`.
- `OPTIMIZATION_GCS_PREFIX` — GCS prefix for run files when `REGISTRY_BACKEND=gcs`; Phase 2.
- `OPTIMIZATION_ALLOWED_TIERS` — comma-separated permitted tiers (`local`, `cloud_cpu`, `cloud_gpu`); default `local`.
- `OPTIMIZATION_DEFAULT_TIER` — `auto` (heuristic) or a fixed tier name; default `auto`.
- `OPTIMIZATION_MAX_PARALLEL` — maximum concurrent optimization workers per server process; default `2`.
- `OPTIMIZATION_SIZE_THRESHOLDS` — two comma-separated integers (`small,large`) for the `auto` tier heuristic; default `10000000,100000000`.
- `OPTIMIZATION_BACKEND_LOCAL` — JAX backend for local workers (`tensorflow` or `jax`); default `tensorflow`.
- `OPTIMIZATION_BACKEND_CLOUD_CPU` — JAX backend for cloud CPU workers; default `jax`.
- `OPTIMIZATION_BACKEND_CLOUD_GPU` — JAX backend for cloud GPU workers; default `jax`.
- `OPTIMIZATION_HEARTBEAT_STALE_SECONDS` — seconds without a heartbeat before a running worker is reconciled as crashed; default `60`.
- `CLOUD_RUN_PROJECT` — GCP project for Cloud Run Jobs; required when any cloud tier is allowed.
- `CLOUD_RUN_REGION` — region for Cloud Run Jobs; required when any cloud tier is allowed.
- `CLOUD_RUN_JOB_CPU` — Cloud Run Job name for the `cloud_cpu` tier.
- `CLOUD_RUN_JOB_GPU` — Cloud Run Job name for the `cloud_gpu` tier.
- Cloud tiers require `REGISTRY_BACKEND=gcs`; validated at startup.

### Deployment (Terraform)
The full stack (Cloud Run service + CPU/GPU jobs, Artifact Registry, GCS) is provisioned
per client via `deploy/terraform/`. A single `terraform apply` builds+pushes all three images
via Cloud Build (content-hash tags), then provisions everything. The service and jobs share one identity: the compute engine default SA by default, or a
dedicated SA (created/adopted with least-privilege roles) when `service_account_id` is set. Per-client `terraform.tfvars`
and `backend.hcl` are uncommitted (`.example` committed); `.env` is local-dev only. Full runbook:
[README.md § Deploy to Google Cloud](README.md#deploy-to-google-cloud-terraform). When editing
`.tf`, use context7 (`/hashicorp/terraform-provider-google`) for current provider syntax.

## Common Commands
- `uv run python -m google_meridian_mcp_server.server`
- `uv run pytest`
- `uv run ruff check src tests scripts` / `uv run ruff format src tests scripts`
- `uv run python -m scripts.validation.live_validate` — live validation suite (see below); builds fixtures if missing (`--force` to rebuild)
- `OPTIMIZATION_ALLOWED_TIERS=local uv run python scripts/qa/future_optimization_qa.py` — final local QA gate for both optimization tools

## Live Validation & Dummy Models
The live validation suite is the integration acceptance gate: it drives an in-process
FastMCP `Client(mcp)` over every tool across the dummy fitted-model matrix (national vs
geo, revenue vs KPI) plus adversarial error-path checks, exiting non-zero on any mismatch.

- **Run it:** `uv run python -m scripts.validation.live_validate` (`--force` rebuilds
  fixtures). The first run BUILDS fixtures via real tiny MCMC fits — a few minutes, NOT a
  hang. Prints a variant×tool PASS / EXPECTED-ERR / FAIL matrix, ends with
  `LIVE VALIDATION PASSED` / `N failed`.
- **Fixtures** live under gitignored `models/_validation/` and are NEVER
  committed. The suite builds them if missing (build-if-missing).
- **Generator** `scripts/generate_validation_models.py` builds 7 variants: the
  2×3 `national|geo` × `revenue | kpi+revenue_per_kpi | kpi-only` matrix (all with
  reach & frequency), plus `geo-revenue-media-only` (no RF, for the no-RF error
  path) and a `.pkl` copy of `national-revenue` (loader pickle branch). Model id ==
  fixture directory name.
- **Suite layout** (`scripts/validation/`): `matrix.py` (declarative expected-valid vs
  expected-error per variant), `runner.py` (client driver + `assert_columnar`/`assert_error`),
  `live_validate.py` (entrypoint), `fixtures.py::ensure_fixture_model` (reusable loader).
- **Expectation rules:** `roi`/`marginal_roi` valid only for revenue + kpi+rpk
  variants (→ `metric_not_supported` on kpi-only); `get_reach_frequency` valid only
  for RF variants; `get_model_fit` honors a `geos` filter; unknown channel/geo →
  `missing_model_data`. Fixtures carry organic media/RF + non-media channels so
  `get_channel_data`/`alpha_summary` exercise every type; `outcome_mode` is
  `revenue` for revenue/kpi+rpk, `kpi` for kpi-only.
- The suite also gates optimization end-to-end (subprocess worker, `national-revenue`
  + `geo-revenue`): both `run_optimization` **and** `run_future_optimization` →
  poll → result → reuse → delete → verify-gone, plus future adversarial submits
  (non-future `start_date`, unknown `cost_multipliers` channel →
  `invalid_optimization_config`). A **local cloud-executor gate** (faked `jobs.run`,
  real worker) covers the cloud launch/liveness/cancel contract with no GCP project;
  a **cross-backend JAX gate** auto-runs when `jax` imports; a **real Cloud Run
  smoke** (`scripts.validation.cloud_smoke`, `CLOUD_SMOKE=1`) covers a live project
  (CPU tier verified).
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
- **domain/optimization.py** — enums (`RunStatus`, `RunPhase`, `ComputeTier`, `OutcomeMode`); `BaseOptimizationConfig` splits into `OptimizationConfig` (`kind="historical"`, `start_date`/`end_date`) and `FutureOptimizationConfig` (`kind="future"`, `future: FutureBlock`); `FutureBlock` (`start_date`, `horizon>0`, `reference`, `cost_multipliers>0`, `revenue_per_kpi_multiplier>0`, `planned_allocation>0`); `Reference` union (`trailing`/`same_period_last_year`/`full_history_average`). `AnyOptimizationConfig` uses a **callable** `Discriminator` defaulting a missing `kind`→`historical` so legacy run JSON (no `kind`) still deserializes — a plain `Field(discriminator="kind")` raises `union_tag_not_found`. `OptimizationRun`/`State`/`Summary`; `config_fingerprint`; `to_optimize_kwargs` (reads `start_date`/`end_date` via `getattr` → works for both kinds).
- **persistence/optimization_run_registry.py** — `OptimizationRunRegistry` ABC; `LocalOptimizationRunRegistry` (3-file layout per run: manifest, state, result; fingerprint index for reuse); `GcsOptimizationRunRegistry` (same 3-file + fingerprint layout on GCS; generation-guarded state writes via `write_state(*, expected_generation)` + `get_state_generation`); `RunNotFoundError`, `ResultNotReadyError`.
- **execution/routing.py** — `model_size_features`, `size_score`, `resolve_tier`; maps problem size to cheapest allowed compute tier; reads `OPTIMIZATION_SIZE_THRESHOLDS` and `OPTIMIZATION_ALLOWED_TIERS`.
- **execution/base_executor.py** — `BaseExecutor` ABC; concurrency gate (max-parallel semaphore), launch lifecycle, crash reconciliation via stale-heartbeat detection; `_fail_if_unfinished` no-ops on a deleted run (`RunNotFoundError` guard) so deleting a just-completed (unreaped) run can't break a later submit.
- **execution/subprocess_executor.py** — `SubprocessExecutor` (local tier); spawns a worker subprocess per run; passes run_id and config via env; crash/orphan reconciliation of runs left non-terminal by a server restart runs at startup.
- **execution/cloud_run_executor.py** — `CloudRunJobExecutor` (cloud tiers); launches worker as a Cloud Run Job execution (CPU or NVIDIA L4 GPU); selects per-tier JAX backend (`OPTIMIZATION_BACKEND_CLOUD_CPU`/`_GPU`); worker heartbeat thread + stale-heartbeat detection is the cloud crash signal; startup orphan reconcile handles runs left in-flight across server restarts.
- **execution/worker.py** — `run_worker`; loads the model, calls `OptimizerFacade.execute` (dispatches historical vs future by `config.kind`), writes result/state to registry; one function, no server imports.
- **meridian/optimizer_facade.py** — `OptimizerFacade` (extends `MeridianInterrogator`); wraps Meridian `BudgetOptimizer`; shapes `OptimizationResults` into the result dict (`summary`, `channel_tables`, `allocation`, `spend_delta`, `outcome_mode`, `response_curves`, future-only `assumptions`). `execute(config)` dispatches by `kind` to `run`/`run_future`, both via the shared `_run(config, build_kwargs, *, enrich_curves)` core. `run_future`/`_future_kwargs` build a `DataTensors` via Meridian `create_optimization_tensors` from a carried-forward reference window (`_seed_cpmu`/`_seed_cprf`/`_seed_spend_flighting`/`_carried_allocation`); `validate_future` runs pure submit-time guards. **Future runs pass `enrich_curves=False` → no `response_curves`** (Meridian's `get_response_curves` ignores `new_data.media`, so curves would silently reflect historical flighting). `_seed_*` skip excluded channels and raise on a zero media/impression denominator (dark channel) for the rest, instead of emitting NaN; the non-excluded check also runs earlier, at submit-time `validate_future`, for a fast fail. `_future_kwargs` stashes a private `_assumptions` dict (`budget`, `budget_source`, `reference_mode`, `excluded_channels`) that `_run` forwards into `build_result`'s top-level `assumptions` key — omitted for historical runs — so an auto-derived budget can never silently mislead.
- **meridian/future_data.py** — pure carry-forward helpers (NumPy + stdlib only, **no Meridian import**): `infer_cadence_days`, `future_time_labels`, `reference_indices` (cadence-aware `same_period_last_year`), `validate_channel_keys`, `normalize_planned_allocation`, `apply_cost_multipliers`, `resolve_budget` (fixed-budget default = seeded future-window total, **not** full history).
- **deploy/** — `Dockerfile.worker` (CPU), `Dockerfile.worker.gpu` (GPU; adds `jax[cuda12]` self-contained CUDA wheels — Cloud Run L4 provides the driver); images are built automatically by `terraform apply` via Cloud Build. Infrastructure provisioning is via `deploy/terraform/` (Terraform module).
- **services/optimization_service.py** — `OptimizationService`; `run_optimization` + `run_future_optimization` share a `_submit` core (fingerprint reuse → routing → executor launch); `run_future_optimization` first runs `facade.validate_future` (pure guards → `invalid_optimization_config` envelope on bad input, before any worker launch); status/result reads, list, delete.
- **bootstrap.py** — `build_model_catalog` (provider + caches → `ModelCatalog`) and `build_registry` (backend selection → `OptimizationRunRegistry`); shared by server lifespan and worker.

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
- `run_optimization`
- `run_future_optimization`
- `get_optimization_status`
- `get_optimization_result`
- `list_optimizations`
- `delete_optimization`
- `cancel_optimization`

### Optimization module
Historical (`run_optimization`) + future (`run_future_optimization`) budget optimization over one
shared async backend: registry, subprocess/Cloud Run workers, polling, tiers, result shaping. Cloud
path (GCS registry with generation-guarded writes, `CloudRunJobExecutor`, per-tier JAX backend,
`cancel_optimization`) verified end-to-end on `as-dev-anze` (CPU tier). Specs/plans under
`docs/superpowers/`.

**Future optimization** answers "how should I split next quarter's budget?". Meridian does **not**
forecast — the posterior is frozen on training data; it optimizes under user-supplied assumptions.
`_future_kwargs` carries forward cost-per-media-unit / flighting / revenue-per-KPI from a `reference`
window, optionally scaled by `cost_multipliers` / `revenue_per_kpi_multiplier` / `planned_allocation`,
and passes a `DataTensors` to `optimize(new_data=...)`. Gotchas that bit us: media/reach/frequency use
the `media_time` axis (≥ `n_times`) → trim to the last `n_times` before windowing; geo axis is always
present (national = size 1); the fixed-budget default is the seeded reference-window total, not the
full-history total. All three assumption knobs were empirically verified to move the optimized
allocation; `validate_future` must never reject a config the worker path would accept.
`future.excluded_channels` is the only real exclusion path (spend→0, reallocated, total unchanged); a `0/0` constraint only freezes, it does not exclude.

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
- **unit/** — config/persistence, catalog/loader, interrogator, analysis_service, analyzer_facade, transport_tools, server, model_catalog_service, result_cache; optimization domain/registry/routing/executor/cloud-executor/facade/service/worker; future config + `future_data` carry-forward helpers.
- **integration/** — provider filesystem behavior and cache interaction; `test_optimizer_facade_future.py` runs `run_future` against a real tiny fitted model (marked `integration`) and asserts a cost multiplier actually moves the allocation.
- **contract/** — supported enums and public tool-surface expectations; `test_meridian_modelfit_contract.py` guards Meridian's private `ModelFit._transform_data_to_dataframe` signature plus the long-frame schema constants (`type`/`mean`/`ci_lo`/`ci_hi`/`expected`/`baseline`/`actual`) that `get_model_fit` depends on; `test_optimization_tools.py` guards tool registration + `readOnlyHint` for all 7 optimization tools and the `run_future_optimization` envelope. Final local QA gate: `scripts/qa/future_optimization_qa.py` (both tools, 10 scenarios, local tier).

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

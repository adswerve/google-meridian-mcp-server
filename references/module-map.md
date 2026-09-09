# Module map (full detail)

Local-only overflow from `AGENTS.md`. `references/` is gitignored, so this file
exists only in a worktree where somebody wrote it. `AGENTS.md` keeps the subset
that is *not* re-derivable by reading the code; everything here is.

- **domain/models.py** — enums, `RuntimeConfig`, `ModelCatalogEntry`.
- **domain/filters.py** — MCP filter schema; normalizes channels/geos; output-type literals.
- **domain/errors.py** — error hierarchy with stable codes and payload details.
- **persistence/base.py** — path helpers: `build_model_id`, `build_display_name`, `build_cache_path`.
- **persistence/local_provider.py** — walks a local directory; emits `ModelCatalogEntry`
  from filesystem metadata. Line ~40 filters on `_SUPPORTED_EXTENSIONS`, derived from
  `domain.models.ModelFormat` (today: `.binpb` only).
- **persistence/gcs_provider.py** — GCS client; blob names to stable ids; downloads to cache.
- **persistence/cache.py** — `DiscoveryCache`, `MaterializationCache`, `ResultCache`
  (TTL-keyed, in-memory, per-service-instance).
- **meridian/loader.py** — loads `.binpb` through Meridian serde APIs; `.pkl` rejected with
  `UnsupportedModelFormatError` (`reports/pkl-format-removed.md`).
- **meridian/catalog.py** — bridges catalog entries to loaded Meridian objects; memoizes
  models and facades.
- **meridian/dataset_mapper.py** — xarray datasets to JSON-safe row dicts; merges on shared
  dims. `filter_records` slices by date/geo/channel (training-data and channel-data only —
  NOT model-fit). `extract_channel_data` builds the per-channel long table. `_df_to_records`
  maps NaN to JSON `null` (numeric cells need `astype(object)` first).
- **meridian/interrogator.py** — metadata extraction; builds `get_model_overview`.
  `geo_names()` returns the model's geo coord values (service uses it to validate
  `get_model_fit` geo filters).
- **meridian/analyzer_facade.py** — wraps `Analyzer`, `MediaSummary`, `ModelFit`; normalizes
  to posterior-only payloads. `get_model_fit` delegates geo/time selection to Meridian's
  `ModelFit` (cached per `(use_kpi, confidence_level)`), then `_reshape_model_fit` pivots the
  long frame to the wide schema.
- **services/model_catalog_service.py** — serializes `ModelCatalogEntry`; ISO-8601 timestamps.
- **services/analysis_service.py** — filter normalization, dispatch, result-cache integration,
  model-overview shaping.
- **transport/tools.py** — registers FastMCP tools; converts domain errors to the standard
  error payload.
- **server.py** — lifespan startup; provider selection; `create_server()`, `mcp`, `run_server()`.
- **bootstrap.py** — `build_model_catalog` and `build_registry`; shared by server lifespan
  and the worker.

## Optimization

- **domain/optimization.py** — enums (`RunStatus`, `RunPhase`, `ComputeTier`, `OutcomeMode`).
  `BaseOptimizationConfig` splits into `OptimizationConfig` (`kind="historical"`,
  `start_date`/`end_date`) and `FutureOptimizationConfig` (`kind="future"`, `future:
  FutureBlock`). `FutureBlock`: `start_date`, `horizon>0`, `reference`, `cost_multipliers>0`,
  `revenue_per_kpi_multiplier>0`, `planned_allocation>0`. `Reference` union
  (`trailing`/`same_period_last_year`/`full_history_average`). `AnyOptimizationConfig` uses a
  **callable** `Discriminator` defaulting a missing `kind` to `historical`, so legacy run JSON
  still deserializes — a plain `Field(discriminator="kind")` raises `union_tag_not_found`.
  `config_fingerprint` takes `meridian_version` as a third keyword parameter (the service
  supplies it; `domain/` may not import `importlib.metadata`). `OptimizationRun` has no
  `backend` field. `to_optimize_kwargs` reads `start_date`/`end_date` via `getattr`, so it
  works for both kinds.
- **persistence/optimization_run_registry.py** — `OptimizationRunRegistry` ABC;
  `LocalOptimizationRunRegistry` (3 files per run: manifest, state, result; plus a fingerprint
  index for reuse); `GcsOptimizationRunRegistry` (same layout on GCS, generation-guarded state
  writes via `write_state(*, expected_generation)` + `get_state_generation`);
  `RunNotFoundError`, `ResultNotReadyError`.
- **execution/routing.py** — `model_size_features`, `size_score`, `resolve_tier`; maps problem
  size to the cheapest allowed tier; reads `OPTIMIZATION_SIZE_THRESHOLDS` and
  `OPTIMIZATION_ALLOWED_TIERS`.
- **execution/base_executor.py** — `BaseExecutor` ABC; max-parallel semaphore, launch
  lifecycle, crash reconciliation via stale-heartbeat detection. `_fail_if_unfinished` no-ops
  on a deleted run (`RunNotFoundError` guard) so deleting a just-completed, unreaped run
  cannot break a later submit.
- **execution/subprocess_executor.py** — `SubprocessExecutor` (local tier); one worker
  subprocess per run; run_id and config via env; startup reconciliation of runs left
  non-terminal by a server restart.
- **execution/cloud_run_executor.py** — `CloudRunJobExecutor` (cloud tiers); launches the
  worker as a Cloud Run Job execution (CPU or NVIDIA L4 GPU); injects `MERIDIAN_BACKEND=jax`
  and `MERIDIAN_ENABLE_JAX_X64=true` as container overrides, because the Job container does
  not inherit the server's environment. Worker heartbeat thread plus stale-heartbeat
  detection is the cloud crash signal; startup orphan reconcile covers server restarts.
- **execution/base_subprocess.py** — Meridian-free launch mechanics; owns
  `MERIDIAN_BACKEND = "jax"`. See `AGENTS.md § Engine` for why the override/default split
  matters.
- **execution/worker.py** — `run_worker`; loads the model, calls `OptimizerFacade.execute`
  (dispatches historical vs future by `config.kind`), writes result and state to the registry.
  One function, no server imports.
- **services/optimization_service.py** — `run_optimization` and `run_future_optimization`
  share a `_submit` core (fingerprint reuse, routing, executor launch);
  `run_future_optimization` first runs `facade.validate_future` (pure guards, so bad input
  becomes an `invalid_optimization_config` envelope before any worker launch). Both envelopes
  return `meridian_version`, not `backend`.
- **meridian/optimizer_facade.py** — `OptimizerFacade` (extends `MeridianInterrogator`); wraps
  Meridian `BudgetOptimizer`; shapes `OptimizationResults` into `summary`, `channel_tables`,
  `allocation`, `spend_delta`, `outcome_mode`, `response_curves`, and future-only
  `assumptions`. `execute(config)` dispatches by `kind` to `run`/`run_future`, both via the
  shared `_run(config, build_kwargs, *, enrich_curves)` core.
  `run_future`/`_future_kwargs` build a `DataTensors` via Meridian
  `create_optimization_tensors` from a carried-forward reference window (`_seed_cpmu`,
  `_seed_cprf`, `_seed_spend_flighting`, `_carried_allocation`); `validate_future` runs pure
  submit-time guards. **Future runs pass `enrich_curves=False`, so no `response_curves`** —
  Meridian's `get_response_curves` ignores `new_data.media`, so curves would silently reflect
  historical flighting. `_seed_*` skip excluded channels and raise on a zero
  media/impression denominator (dark channel) rather than emitting NaN; the same check runs
  earlier at submit time for a fast fail. `_future_kwargs` stashes a private `_assumptions`
  dict (`budget`, `budget_source`, `reference_mode`, `excluded_channels`) that `_run` forwards
  into `build_result`'s top-level `assumptions` key (omitted for historical runs), so an
  auto-derived budget can never silently mislead.
- **meridian/future_data.py** — pure carry-forward helpers (NumPy + stdlib only, **no Meridian
  import**): `infer_cadence_days`, `future_time_labels`, `reference_indices` (cadence-aware
  `same_period_last_year`), `validate_channel_keys`, `normalize_planned_allocation`,
  `apply_cost_multipliers`, `resolve_budget` (fixed-budget default is the seeded future-window
  total, **not** full history).
- **deploy/** — root `Dockerfile` (server), `deploy/Dockerfile.worker` (CPU),
  `deploy/Dockerfile.worker.gpu` (GPU; adds `jax[cuda12]` self-contained CUDA wheels — Cloud
  Run L4 supplies the driver). All three run `pip install "."`. Images are built by
  `terraform apply` via Cloud Build; provisioning lives in `deploy/terraform/`.

## Future-optimization gotchas that actually bit us

Meridian does **not** forecast: the posterior is frozen on training data, and future
optimization optimizes under user-supplied assumptions.

- media/reach/frequency use the `media_time` axis (>= `n_times`) — trim to the last `n_times`
  before windowing.
- The geo axis is always present; national models have size 1.
- The fixed-budget default is the seeded reference-window total, not the full-history total.
- All three assumption knobs (`cost_multipliers`, `revenue_per_kpi_multiplier`,
  `planned_allocation`) were empirically verified to move the optimized allocation.
- `validate_future` must never reject a config the worker path would accept.
- `future.excluded_channels` is the only real exclusion path (spend to 0, reallocated, total
  unchanged). A `0/0` constraint only freezes a channel; it does not exclude it.

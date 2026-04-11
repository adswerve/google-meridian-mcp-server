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
- `python -m pip install -e .[dev]`
- `python -m google_meridian_mcp_server.server`
- `pytest`
- `ruff check src tests`
- `ruff format src tests`

## Module Map
### `domain/models.py`
- Defines transport, backend, model-format, and status enums.
- `RuntimeConfig` is the central validated runtime object.
- Validation happens in `RuntimeConfig.__post_init__`.
- `ModelCatalogEntry` is the canonical model discovery record.

### `domain/filters.py`
- Defines the MCP-visible filter schema with Pydantic.
- Normalizes channel and geo lists.
- Deduplicates user-provided string lists while preserving order.
- Rejects unknown fields with `extra="forbid"`.
- Holds the output-type literal definitions shared by the tool layer.

### `domain/errors.py`
- Central error hierarchy for stable error codes and payload details.
- The transport layer depends on these classes for consistent error shaping.

### `persistence/base.py`
- Holds shared path and naming helpers.
- `build_model_id` strips file suffixes and collapses nested `model.binpb` layouts.
- `build_display_name` converts stable ids to human-readable names.
- `build_cache_path` maps provider-relative paths into the local cache tree.

### `persistence/local_provider.py`
- Walks a local models directory recursively.
- Supports `.binpb` and `.pkl`.
- Emits `ModelCatalogEntry` instances from filesystem metadata.
- `materialize()` is effectively a local existence check.

### `persistence/gcs_provider.py`
- Lazily creates the Google Cloud Storage client.
- Separates authentication failures from generic backend failures.
- Converts blob names into stable relative paths and model ids.
- Downloads remote models into the local materialization cache.

### `persistence/cache.py`
- `DiscoveryCache` wraps provider discovery with a TTL.
- `MaterializationCache` delegates to the provider with a standard cache root.
- `ResultCache` is an optional in-memory cache keyed by tool, model, and params.

### `meridian/loader.py`
- Auto-detects `.binpb` vs `.pkl`.
- Loads Meridian models through Meridian's serde APIs.
- This is the only place that should switch on model file extension.

### `meridian/catalog.py`
- Bridges discovered entries to loaded Meridian objects.
- Caches fully loaded models for the process lifetime.
- Caches `AnalyzerFacade` instances separately from raw models.
- `get_interrogator()` currently returns the cached facade because the facade subclasses `MeridianInterrogator`.

### `meridian/dataset_mapper.py`
- Converts xarray datasets and data arrays into JSON-safe row dicts.
- Merges multiple training datasets on shared dimension columns.
- Preserves requested dataset ordering in the final response.
- Normalizes numpy and pandas scalar types into Python values.

### `meridian/interrogator.py`
- Owns model metadata extraction.
- Builds the `get_model_overview()` payload.
- Computes input schemas, channel lists, dataset availability, and flattened input column names.
- Builds a wide dataframe view of model inputs for downstream helpers.
- Handles paid media, RF media, organic media, organic RF, controls, non-media treatments, KPI, revenue per KPI, and population.

### `meridian/analyzer_facade.py`
- Wraps Meridian `Analyzer` and `MediaSummary`.
- Serves as the execution layer for analysis-service dispatch.
- Contains grouped summary methods, contribution methods, adstock helpers, and response-curve helpers.
- Includes carryover and saturation helpers ported from the showcase app.
- Uses a filtered `MediaSummary` wrapper so geo/time-aware summary requests match the selected slice.
- Normalizes grouped outputs to posterior-only payloads before transport serialization.

### `services/model_catalog_service.py`
- Serializes `ModelCatalogEntry` objects into JSON-safe dicts.
- Converts `last_modified` to ISO-8601 strings.

### `services/analysis_service.py`
- Normalizes filters before touching Meridian-specific code.
- Deduplicates requested training datasets.
- Validates output types for grouped analysis tools.
- Reshapes lower-level exceptions into `MissingModelDataError`.
- Adds stable `available_tool_options` to model overview responses.
- Owns result-cache integration for analysis requests.
- Exposes the public adstock tool name as `get_adstock_decay`.

### `transport/tools.py`
- Registers FastMCP tools.
- Applies read-only annotations consistently.
- Converts domain errors into a standard error payload.
- Keeps request schemas close to the public tool surface.

### `server.py`
- Creates shared runtime objects during lifespan startup.
- Chooses the provider implementation from config.
- Exposes `create_server()`, module-level `mcp`, and `run_server()`.
- `run_server()` switches between stdio and HTTP transport.

## Current Tool Surface
- `list_models`
- `get_model_overview`
- `get_training_data`
- `get_channel_summary`
- `get_contribution`
- `get_adstock_decay`
- `get_response_curves`

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
- Grouped analysis tools should return posterior-only rows.
- Prior rows should not be exposed through the MCP surface.
- The serialized result rows should not include a `distribution` field.
- `get_channel_summary` baseline summaries come from Meridian's analyzer baseline API, not `MediaSummary`.
- `marginal_roi` is sourced from Meridian's `mroi` output.
- `marginal_cpik` is derived from posterior `mroi` values and must keep CI bounds ordered after inversion.
- `get_adstock_decay` is the public tool name; keep older `get_response_dynamics` references out of docs and transport contracts.
- `get_response_curves` should return numeric curve rows, not channel metadata placeholders.
- `response_curve_summary` should return numeric summary rows with `channel`, `spend`, `spend_multiplier`, `mean`, `ci_lo`, and `ci_hi`.

## Current Test Coverage
- `tests/unit/test_config_and_persistence.py` covers runtime config validation, persistence helpers, and GCS provider behavior.
- `tests/unit/test_catalog_and_loader.py` covers loader suffix handling and catalog memoization.
- `tests/unit/test_interrogator.py` covers overview metadata and wide-input extraction branches.
- `tests/unit/test_analysis_service.py` covers filter normalization, dataset selection, cache usage, dispatch, and model-overview shaping.
- `tests/unit/test_analyzer_facade.py` covers posterior-only shaping, baseline summary routing, marginal ROI / CPKI handling, adstock outputs, response-curve outputs, carryover, and saturation paths.
- `tests/unit/test_transport_tools.py` covers registered FastMCP tool wrappers and standard error payloads.
- `tests/unit/test_server.py` covers provider selection and stdio vs HTTP startup behavior.
- `tests/unit/test_model_catalog_service.py` and `tests/unit/test_result_cache.py` cover catalog serialization and in-memory result caching.
- `tests/integration/test_model_providers.py` and `tests/integration/test_cached_analysis.py` cover provider and cache integration behavior.
- Contract tests pin supported enums and public tool-surface expectations.

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

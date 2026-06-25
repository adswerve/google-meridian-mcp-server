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
- `uv run ruff check src tests`
- `uv run ruff format src tests`

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
- **meridian/dataset_mapper.py** — converts xarray datasets to JSON-safe row dicts; merges on shared dims.
- **meridian/interrogator.py** — model metadata extraction; builds `get_model_overview` payload.
- **meridian/analyzer_facade.py** — wraps `Analyzer` and `MediaSummary`; executes analysis; normalizes to posterior-only payloads.
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
- `list_models` returns a list of `{id, display_name, format, last_modified}` objects.
- Grouped analysis tools return posterior-only rows; no `distribution` column.
- `get_channel_summary` baseline summaries come from Meridian's analyzer baseline API, not `MediaSummary`.
- `marginal_roi` is sourced from Meridian's `mroi` output.
- `marginal_cpik` is derived from posterior `mroi` values and must keep CI bounds ordered after inversion.
- `get_response_curves` should return numeric curve rows, not channel metadata placeholders.
- `response_curve_summary` should return numeric summary rows with `channel`, `spend`, `spend_multiplier`, `mean`, `ci_lo`, and `ci_hi`.

## Current Test Coverage
- **unit/** — config/persistence, catalog/loader, interrogator, analysis_service, analyzer_facade, transport_tools, server, model_catalog_service, result_cache.
- **integration/** — provider filesystem behavior and cache interaction.
- **contract/** — supported enums and public tool-surface expectations.

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

# Tool Description Clarity Pass — Design

**Date:** 2026-07-01
**Status:** Approved (pending spec review)
**Scope:** `src/google_meridian_mcp_server/transport/tools.py`,
`src/google_meridian_mcp_server/domain/optimization.py`,
`src/google_meridian_mcp_server/domain/filters.py` (descriptions only where noted)

## Goal

Make every MCP tool read to an LLM the way the analysis tools already do, so the
model can pick the right tool and fill every argument correctly from the schema
alone — without trial-and-error calls. Concretely, each tool's docstring should
convey **what it does / when to call it / an example question it answers**, and
each argument's `Field`/model description should be rich enough that the JSON
schema is self-documenting.

This is a documentation + safe-typing pass. **No service behavior changes.** The
only functional change is tightening two enum-like parameters from free `str` to
`Literal`, which constrains inputs that were already documented as the only valid
values.

## Motivation / Current State

- The **analysis tools** (`list_models` → `get_spend_scenario`) are already
  polished and consistent: what/when/example-question docstrings, workflow
  breadcrumbs ("call this after `list_models`"), and rich `Field` descriptions
  that reference `get_model_overview.available_tool_options`.
- The **optimization tools** (`run_optimization` → `cancel_optimization`) are
  thinner and inconsistent:
  - `run_optimization`'s entire `config` schema is crammed into one prose blob in
    a `Field(description=...)` while the backing `OptimizationConfig` model has
    terse field descriptions (e.g. `"ISO start; omit for full range."`).
  - `compute_tier` and `list_optimizations.status` are free `str` — allowed
    values live only in prose, so the JSON schema does not present them as an
    enum the LLM can see.
  - No lifecycle narrative tying `run → poll status → get result` together, and
    no "example question" framing.
- The `model_id` example is inconsistent across `Field` descriptions
  (`'geo-revenue'` vs `'model-2026-Q1'`).

## Design

### 1. Optimization tools reach parity with analysis tools

Rewrite the 6 optimization-tool docstrings to add the missing *when/why* framing
and a lifecycle breadcrumb so the LLM understands the flow:

- `run_optimization` → returns `run_id`; **poll** `get_optimization_status`
  until `completed`; **then** `get_optimization_result`.
- `list_optimizations` to discover/reuse prior runs; `cancel_optimization` /
  `delete_optimization` for lifecycle cleanup.

Flesh out the terse `OptimizationConfig` field descriptions (`start_date`,
`end_date`, `selected_geos`, `use_kpi`) to match the fuller `AnalysisFilters`
style, and point to
`get_model_overview.available_tool_options.run_optimization` for valid
channels/geos.

### 2. Make enum-like params self-documenting (safe typing)

- `run_optimization.compute_tier`: `str` → `Literal["auto", "local",
  "cloud_cpu", "cloud_gpu"]` (default `"auto"`).
- `list_optimizations.status`: `str | None` → `Literal["queued", "running",
  "completed", "failed", "canceled"] | None` (mirrors the existing `RunStatus`
  enum values).

The allowed values then render as an `enum` in the tool schema instead of being
buried in prose. Behavior is unchanged; validation gets stricter only on inputs
that were already documented as the only valid ones.

### 3. Consistency cleanups

- Standardize the `model_id` example to **`'model-2026-Q1'`** across every
  `Field` description (realistic naming; drops the stray `'geo-revenue'`
  examples).
- Keep the `run_optimization.config` prose blob but tighten it so it complements
  — rather than duplicates — the now-richer `OptimizationConfig` model
  descriptions.

### Explicitly out of scope

- Restructuring the `config` blob into a different shape (e.g. flattening
  scenario/constraint), renaming tools, or changing tool grouping. Those are a
  separate "deeper refactor" and are not part of this pass.
- Any change to service logic, response shapes, or Meridian adapters.

## Implementation Approach — Reviewer ↔ Editor Critique Loop

Descriptions are the deliverable, so they get iterated, not one-shotted. For the
tool docstrings and argument descriptions:

1. **Editor pass:** draft/revise all docstrings and `Field`/model descriptions.
2. **Reviewer pass:** critique against the checklist below; produce concrete,
   specific edit requests.
3. Repeat editor → reviewer for **at least 2 full loops**, continuing until the
   reviewer has no substantive findings.

**Reviewer checklist (per tool):**
- Does the docstring state *what it does*, *when to call it*, and *an example
  question it answers*?
- Are lifecycle/workflow breadcrumbs present and correct (ordering, tool names)?
- Does every argument description say what it is, the format, the default
  behavior when omitted, and where to find valid values?
- Are enum values discoverable from the schema (not only prose)?
- Is terminology consistent across tools (model_id example, ROI/ROAS, PER TIME
  UNIT, KPI vs revenue)?
- Any duplication between a `Field` prose blob and the backing pydantic model?

## Testing / Validation

1. `uv run ruff check src tests` and `uv run ruff format src tests` — clean.
2. `uv run pytest` — green. `tests/contract/test_optimization_tools.py` guards
   tool registration and `readOnlyHint` annotations; the transport-tools unit
   tests guard registration.
3. **Final gate (explicit user requirement): run the server locally** and
   inspect the generated schema for **every** tool — confirm each tool
   description and each input description/enum renders as expected and matches
   this spec. Use an in-process FastMCP `Client(mcp)` (or the running server) to
   dump `list_tools()` schemas and eyeball descriptions + `enum` fields for all
   17 tools. Boot the server per AGENTS.md
   (`uv run python -m google_meridian_mcp_server.server`) if a live transport
   check is wanted in addition to the in-process dump.

## Risks

- **Literal tightening rejects previously-accepted junk input.** Intended: those
  values were never valid. Low risk — documented behavior is preserved for all
  legitimate values.
- **Wording drift from actual service behavior.** Mitigated by the reviewer
  checklist item on correctness and by the local schema-vs-spec final gate.

# Tool Description Clarity Pass — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every MCP tool's docstring and argument schema self-documenting so an LLM can pick the right tool and fill every argument correctly from the schema alone.

**Architecture:** A documentation + safe-typing pass over `transport/tools.py` and the two `domain/` pydantic models. Only functional change: two enum-like `str` params become `Literal`. Tool docstrings and argument descriptions are refined through a reviewer↔editor critique loop (≥2 passes). Final gate is a local-server schema dump verifying all 17 tools.

**Tech Stack:** Python 3, FastMCP, Pydantic v2, pytest, ruff, `uv`.

## Global Constraints

- No service behavior changes. Only edit: `src/google_meridian_mcp_server/transport/tools.py`, `src/google_meridian_mcp_server/domain/optimization.py`; add tests only under `tests/`.
- Standardize every `model_id` `Field` example to exactly `'model-2026-Q1'`.
- Keep responses JSON-safe and payloads deterministically ordered (unchanged here).
- `uv run ruff check src tests` and `uv run pytest` must be green after every task.
- Do not tag commits with any Co-Authored-By / Opus trailer.
- The full tool surface is 17 tools: `list_models`, `get_model_overview`, `get_training_data`, `get_channel_summary`, `get_contribution`, `get_adstock_decay`, `get_response_curves`, `get_model_fit`, `get_reach_frequency`, `get_channel_data`, `get_spend_scenario`, `run_optimization`, `get_optimization_status`, `get_optimization_result`, `list_optimizations`, `delete_optimization`, `cancel_optimization`.

---

### Task 1: Safe typing + `model_id` example standardization

Convert the two enum-like `str` params to `Literal` so the JSON schema exposes an `enum`, and standardize the `model_id` example. This is the only task that changes validation behavior, so it carries a contract test.

**Files:**
- Modify: `src/google_meridian_mcp_server/transport/tools.py` (lines ~91–341 for `model_id` examples; `run_optimization.compute_tier` ~410–412; `list_optimizations.status` ~462–467)
- Test: `tests/contract/test_optimization_tools.py`

**Interfaces:**
- Consumes: `create_server()` from `google_meridian_mcp_server.server`; `mcp.list_tools()` returns tool objects with `.name` and `.inputSchema` (dict with `properties`).
- Produces: `run_optimization` input schema `properties.compute_tier.enum == ["auto","local","cloud_cpu","cloud_gpu"]`; `list_optimizations` input schema `properties.status` presents the 5 `RunStatus` values as an enum. Service call signatures unchanged (both still receive plain strings at runtime).

- [ ] **Step 1: Write the failing test**

Add to `tests/contract/test_optimization_tools.py`:

```python
def _prop(schema: dict, name: str) -> dict:
    return schema["properties"][name]


def _enum_values(prop: dict) -> set[str]:
    # Literal[...] renders either as a top-level "enum" or, for Optional,
    # inside an "anyOf" branch that carries the "enum".
    if "enum" in prop:
        return set(prop["enum"])
    for branch in prop.get("anyOf", []):
        if "enum" in branch:
            return set(branch["enum"])
    return set()


@pytest.mark.asyncio
async def test_compute_tier_and_status_are_enums():
    mcp = create_server()
    by_name = {t.name: t for t in await mcp.list_tools()}

    tier = _prop(by_name["run_optimization"].inputSchema, "compute_tier")
    assert _enum_values(tier) == {"auto", "local", "cloud_cpu", "cloud_gpu"}
    assert tier.get("default") == "auto"

    status = _prop(by_name["list_optimizations"].inputSchema, "status")
    assert _enum_values(status) == {
        "queued",
        "running",
        "completed",
        "failed",
        "canceled",
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/contract/test_optimization_tools.py::test_compute_tier_and_status_are_enums -v`
Expected: FAIL (current params are free `str`; `_enum_values` returns an empty set).

- [ ] **Step 3: Standardize the `model_id` example**

In `src/google_meridian_mcp_server/transport/tools.py`, replace every occurrence of the string `(e.g. 'geo-revenue').` inside a `model_id` `Field(description=...)` with `(e.g. 'model-2026-Q1').`. There are 9 such occurrences (lines 97, 131, 164, 197, 230, 263, 289, 315, 341); line 80 already uses `model-2026-Q1`.

- [ ] **Step 4: Type `compute_tier` as a Literal**

Replace the `compute_tier` parameter of `run_optimization`:

```python
        compute_tier: Annotated[
            Literal["auto", "local", "cloud_cpu", "cloud_gpu"],
            Field(
                description="Where to run the optimization. 'auto' (default) picks "
                "the cheapest allowed backend from the problem size; 'local' runs "
                "in-process; 'cloud_cpu'/'cloud_gpu' dispatch a Cloud Run Job "
                "(only if the server enables those tiers).",
            ),
        ] = "auto",
```

- [ ] **Step 5: Type `list_optimizations.status` as a Literal**

Replace the `status` parameter of `list_optimizations`:

```python
        status: Annotated[
            Literal["queued", "running", "completed", "failed", "canceled"] | None,
            Field(
                description="Filter to runs in this state. Omit to return all "
                "states.",
            ),
        ] = None,
```

- [ ] **Step 6: Add the `Literal` import**

Ensure the top-of-file typing import includes `Literal`. Change:

```python
from typing import Annotated, Any
```

to:

```python
from typing import Annotated, Any, Literal
```

- [ ] **Step 7: Run tests**

Run: `uv run pytest tests/contract/test_optimization_tools.py -v && uv run ruff check src tests`
Expected: PASS (new test green, existing contract tests still green, ruff clean).

- [ ] **Step 8: Commit**

```bash
git add src/google_meridian_mcp_server/transport/tools.py tests/contract/test_optimization_tools.py
git commit -m "refactor(tools): type compute_tier/status as Literal; standardize model_id example"
```

---

### Task 2: Optimization tool descriptions — reviewer↔editor critique loop

Bring the 6 optimization tools and the `OptimizationConfig` model up to the analysis-tool standard (what / when / example question / lifecycle breadcrumb). The draft text below is the editor's **starting point**; then run the critique loop.

**Files:**
- Modify: `src/google_meridian_mcp_server/transport/tools.py` (docstrings for `run_optimization`, `get_optimization_status`, `get_optimization_result`, `list_optimizations`, `delete_optimization`, `cancel_optimization`; `run_optimization.config` blob; `label`/`note`/`force_rerun` field text)
- Modify: `src/google_meridian_mcp_server/domain/optimization.py` (`OptimizationConfig.start_date`, `.end_date`, `.selected_geos`, `.use_kpi` descriptions)

**Interfaces:**
- Consumes: nothing new.
- Produces: docstrings only — no signature or schema-shape changes beyond description text. `OptimizationConfig` field *names/types* unchanged.

- [ ] **Step 1: Editor pass — rewrite the 6 optimization docstrings**

Apply these drafts in `transport/tools.py`:

`run_optimization`:
```python
        """Optimize how budget is split across paid-media & RF channels. Answers "how should I reallocate spend?" or "what mix best hits a 2x ROAS target?". Supply a scenario (fixed_budget | target_roas | target_mroas) and spend constraints via `config`. Long-running: returns a run_id immediately — then poll get_optimization_status until status is 'completed', then read get_optimization_result. An identical prior run (same model + config) is reused unless force_rerun=true; browse prior runs with list_optimizations."""
```

`get_optimization_status`:
```python
        """Poll a run started by run_optimization. Returns status (queued/running/completed/failed/canceled), current phase, last heartbeat, elapsed time, and an error object if it failed. Call repeatedly until status is 'completed', then call get_optimization_result."""
```

`get_optimization_result`:
```python
        """Fetch the full structured result of a completed optimization: optimized-vs-current spend per channel, expected outcome lift, and per-channel efficiency (ROI/ROAS for revenue models, CPIK otherwise). Raises optimization_not_ready until get_optimization_status reports 'completed'. Answers 'what is the recommended budget allocation?'."""
```

`list_optimizations`:
```python
        """List past optimization runs (newest first) with config summary, status, and headline result. Use to find and reuse prior work instead of re-running, or to get a run_id for get_optimization_result / delete_optimization. Filter by model_id and/or status."""
```

`delete_optimization`:
```python
        """Permanently delete one optimization run and its stored result by run_id. Irreversible. Find run_ids via list_optimizations. To stop an in-flight run instead, use cancel_optimization."""
```

`cancel_optimization`:
```python
        """Best-effort cancel of a queued or running optimization by run_id. Does not remove the run record (use delete_optimization for that) and has no effect on runs that already completed or failed."""
```

- [ ] **Step 2: Editor pass — enrich `OptimizationConfig` field descriptions**

In `domain/optimization.py`, replace the four terse descriptions:

```python
    start_date: date | None = Field(
        default=None,
        description="Inclusive start date (ISO-8601, e.g. '2023-01-01') of the "
        "window to optimize over. Omit to use the model's full date range.",
    )
    end_date: date | None = Field(
        default=None,
        description="Inclusive end date (ISO-8601, e.g. '2023-12-31') of the "
        "window to optimize over. Omit to use the model's full date range.",
    )
    selected_geos: list[str] | None = Field(
        default=None,
        description="Subset of geo identifiers to optimize over (e.g. "
        "['US-CA', 'US-NY']). Omit for all geos; ignored by national models. "
        "Valid values: get_model_overview 'geos'.",
    )
    use_kpi: bool | None = Field(
        default=None,
        description="Objective family: false = revenue-based (ROAS/ROI), "
        "true = KPI-based (CPIK). Omit/null to use the model's native objective "
        "(revenue models -> ROAS, no-revenue models -> CPIK).",
    )
```

- [ ] **Step 3: Reviewer pass 1 — critique against the checklist**

Read each edited docstring/description with fresh eyes and write down concrete findings against this checklist:
- States what it does, when to call it, and an example question it answers.
- Lifecycle/workflow breadcrumbs present and correct (tool names spelled exactly, ordering right).
- Every argument says: what it is, format, default-when-omitted, where to find valid values.
- Enum values discoverable from the schema (Task 1) — prose does not contradict them.
- Terminology consistent with analysis tools (`model-2026-Q1`, ROI/ROAS, PER TIME UNIT, KPI vs revenue).
- No duplication between the `run_optimization.config` prose blob and the now-richer `OptimizationConfig` descriptions — the blob should *summarize the shape* and defer field detail to the model.

Apply every finding.

- [ ] **Step 4: Reviewer pass 2 — repeat**

Run the same checklist again on the revised text. Continue additional editor→reviewer loops until a pass yields no substantive findings (minimum two full loops total per Steps 3–4).

- [ ] **Step 5: Tighten the `run_optimization.config` blob**

Ensure the `config` `Field` description summarizes the union shape but defers per-field detail to the model. Target text:

```python
                    "Optimization scenario + constraints (see the nested field "
                    "descriptions for details). scenario is one of "
                    "{type:'fixed_budget', budget?} | {type:'target_roas', target_value} | "
                    "{type:'target_mroas', target_value}. constraint is "
                    "{mode:'global', pct} or {mode:'per_channel', bounds:{channel:{lower_pct,upper_pct}}}. "
                    "Optional start_date/end_date, selected_geos, use_kpi. "
                    "Valid channels/geos: get_model_overview.available_tool_options.run_optimization."
```

- [ ] **Step 6: Verify nothing broke**

Run: `uv run pytest tests/contract/test_optimization_tools.py tests/unit/test_transport_tools.py -v && uv run ruff check src tests && uv run ruff format --check src`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/google_meridian_mcp_server/transport/tools.py src/google_meridian_mcp_server/domain/optimization.py
git commit -m "docs(tools): optimization tool + config descriptions to analysis-tool parity"
```

---

### Task 3: Analysis tool description polish — reviewer↔editor critique loop

The 11 analysis tools are already strong; this is a lighter loop to catch inconsistencies the reviewer surfaces (terminology, missing "valid values" pointers, example-question presence). Edit only where the reviewer finds a concrete gap — do not rewrite for its own sake.

**Files:**
- Modify: `src/google_meridian_mcp_server/transport/tools.py` (docstrings/Field text for the 11 analysis tools, lines ~66–383)

**Interfaces:**
- Consumes: nothing new. Produces: description text only.

- [ ] **Step 1: Reviewer pass 1**

Run the Task 2 checklist across all 11 analysis tools (`list_models` … `get_spend_scenario`). Record concrete findings only (e.g., a `Field` that omits where to find valid values, a docstring missing an example question, terminology that drifts from the optimization tools now that those are richer). Apply findings.

- [ ] **Step 2: Reviewer pass 2**

Repeat the checklist on the revised text. Continue loops until a pass yields no substantive findings (minimum two total). If pass 1 found nothing substantive, still perform pass 2 to confirm, then stop.

- [ ] **Step 3: Verify**

Run: `uv run pytest tests/unit/test_transport_tools.py -v && uv run ruff check src tests && uv run ruff format --check src`
Expected: PASS.

- [ ] **Step 4: Commit (skip if no edits were made)**

```bash
git add src/google_meridian_mcp_server/transport/tools.py
git commit -m "docs(tools): analysis tool description consistency polish"
```

---

### Task 4: Final validation gate — local-server schema dump for all 17 tools

The explicit acceptance gate: run the server locally and confirm every tool's description and every input's description/enum match this spec. Uses a throwaway dump script in the scratchpad (not committed).

**Files:**
- Create (scratchpad, not committed): `<scratchpad>/dump_schemas.py`

**Interfaces:**
- Consumes: `create_server()`; `mcp.list_tools()` → objects with `.name`, `.description`, `.inputSchema`.

- [ ] **Step 1: Write the in-process schema dump script**

Create `<scratchpad>/dump_schemas.py`:

```python
import asyncio
import json

from google_meridian_mcp_server.server import create_server


async def main() -> None:
    mcp = create_server()
    tools = sorted(await mcp.list_tools(), key=lambda t: t.name)
    print(f"TOOL COUNT: {len(tools)}")
    for t in tools:
        print("=" * 72)
        print(f"{t.name}\n  DESC: {t.description}")
        props = (t.inputSchema or {}).get("properties", {})
        for pname, p in props.items():
            enum = p.get("enum") or next(
                (b.get("enum") for b in p.get("anyOf", []) if b.get("enum")), None
            )
            desc = p.get("description", "<no description>")
            line = f"  - {pname}: {desc}"
            if enum:
                line += f"  [enum: {enum}]"
            print(line)


asyncio.run(main())
```

- [ ] **Step 2: Run the dump against the in-process server**

Run: `uv run python <scratchpad>/dump_schemas.py`
Expected: `TOOL COUNT: 17`; every tool prints a non-empty DESC; `run_optimization.compute_tier` shows `[enum: ['auto', 'local', 'cloud_cpu', 'cloud_gpu']]`; `list_optimizations.status` shows the 5-status enum; no `model_id` description contains `geo-revenue`; every optimization docstring names the correct next tool in the lifecycle.

- [ ] **Step 3: Manually verify the dump against this spec**

Read the printed output top to bottom and confirm, for all 17 tools: docstring has what/when/example-question; every argument description states format + default-when-omitted + where to find valid values; no leftover `geo-revenue`; enums present where expected. Note any mismatch and loop back to Task 2/3 to fix, then re-run the dump.

- [ ] **Step 4: Boot the HTTP server locally to confirm it starts clean**

Run (background, then stop): `uv run python -m google_meridian_mcp_server.server`
Expected: server starts and logs the streamable-http transport with no import/registration errors. Stop it once startup is confirmed. (This exercises the real transport path per AGENTS.md; the schema content itself is already verified in Steps 2–3 via the in-process client.)

- [ ] **Step 5: Full test + lint sweep**

Run: `uv run pytest && uv run ruff check src tests scripts && uv run ruff format --check src`
Expected: all green.

- [ ] **Step 6: Update AGENTS.md if terminology changed**

If any tool's documented behavior wording changed in a way that affects the "Current Analysis Behavior" or "Current Tool Surface" notes in `AGENTS.md`, update those lines to match. (Descriptions-only changes usually need no AGENTS.md edit — verify and skip if so.)

- [ ] **Step 7: Commit any final fixes**

```bash
git add -A
git commit -m "docs(tools): finalize description clarity pass after local schema validation"
```

---

## Self-Review

**Spec coverage:**
- Spec §1 (optimization parity docstrings + `OptimizationConfig` descriptions) → Task 2.
- Spec §2 (safe typing: `compute_tier`, `status` → Literal) → Task 1 (Steps 4–6) with contract test.
- Spec §3 (standardize `model_id` example; tighten config blob) → Task 1 Step 3 + Task 2 Step 5.
- Spec "Implementation Approach: reviewer↔editor loop ≥2 passes" → Task 2 Steps 3–4, Task 3 Steps 1–2.
- Spec "Testing/Validation: ruff/pytest + local-server schema dump for all tools" → Task 4.
- Spec "out of scope" (no config restructure/rename/regroup) → honored; no task touches shape/names.

**Placeholder scan:** No "TBD/TODO"; every code step shows literal text or exact edit instructions; the critique-loop steps ship concrete draft text plus an explicit checklist and stop condition rather than "refine later".

**Type consistency:** `compute_tier` Literal values `["auto","local","cloud_cpu","cloud_gpu"]` and `status` Literal values `["queued","running","completed","failed","canceled"]` are identical in Task 1's implementation and its test (`_enum_values`) and in Task 4's dump expectations. `create_server()` / `list_tools()` / `.inputSchema` / `.description` usage is consistent across Tasks 1 and 4.

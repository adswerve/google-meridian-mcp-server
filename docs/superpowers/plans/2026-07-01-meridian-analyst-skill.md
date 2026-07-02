# meridian-analyst Agent Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a bundled, agentskills.io-compliant `meridian-analyst` skill that teaches any connecting LLM how to use this Meridian MCP well — weighted toward budget optimization/reallocation and channel performance — served by the server via FastMCP's skills provider.

**Architecture:** A `skills/meridian-analyst/` directory (`SKILL.md` + `references/`) registered on the server through `SkillsDirectoryProvider`, plus a pointer in the server `instructions` string, plus a folder-drop fallback documented in the README. Content is authored under an implementer↔reviewer critique loop.

**Tech Stack:** FastMCP 3.4.2 (`fastmcp.server.providers.skills.SkillsDirectoryProvider`, `FastMCP.add_provider`), Markdown skill files, pytest contract test, `uv`.

## Global Constraints

_Every task's requirements implicitly include this section._

- **LLM understanding is the PRIMARY criterion and has precedence** over token economy. When they conflict, understanding wins. Default posture otherwise: say it once, concisely, defer depth to `references/`.
- **Do NOT re-document tool parameters.** Parameter/enum/signature detail lives in the tool descriptions. The skill points to tools; it never restates their fields. This is the anti-drift rule.
- **No new server tools and no analysis/optimization behavior changes.** This work is a provider registration + Markdown content only.
- **Client-agnostic & portable.** `SKILL.md` uses standard `name` + `description` frontmatter; all internal links are relative (`references/…`). The identical directory must work served-by-MCP or dropped into a client's skills folder verbatim.
- **Every content file is authored under an implementer↔reviewer critique loop — MINIMUM TWO iterations, more if the reviewer still finds material issues.** A single clean pass is not sufficient; run at least two.
- **Correctness against the live surface.** Every tool name, scenario type, enum, and workflow must match `src/google_meridian_mcp_server/transport/tools.py`, `src/google_meridian_mcp_server/domain/optimization.py`, and `AGENTS.md`. No invented capabilities.
- **Forward planning is a skill-only approximation** (spec §5.4.1) with the mandated caveats; `new_data` scenario planning is explicitly out of scope.
- **Commits:** conventional-commit messages; **do NOT add a Co-Authored-By / Opus trailer** (standing user preference).

**Canonical tool names** (cross-check the skill against this list; regenerate with the command in Task 2):
`list_models`, `get_model_overview`, `get_training_data`, `get_channel_summary`, `get_contribution`, `get_adstock_decay`, `get_response_curves`, `get_model_fit`, `get_reach_frequency`, `get_channel_data`, `get_spend_scenario`, `run_optimization`, `get_optimization_status`, `get_optimization_result`, `list_optimizations`, `delete_optimization`, `cancel_optimization`.

**Canonical optimization vocabulary** (from `domain/optimization.py`): scenario types `fixed_budget` / `target_roas` / `target_mroas`; constraint modes `global` / `per_channel`; run statuses `queued` / `running` / `completed` / `failed` / `canceled`; config fields `scenario`, `constraint`, `start_date`, `end_date`, `selected_geos`, `use_kpi`.

**Reference:** design spec `docs/superpowers/specs/2026-07-01-meridian-analyst-skill-design.md` (approved).

---

## File Structure

- Create: `skills/meridian-analyst/SKILL.md` — lean core; `description` is the activation trigger.
- Create: `skills/meridian-analyst/references/taxonomy.md` — model-type × tool-validity matrix + gotchas.
- Create: `skills/meridian-analyst/references/glossary.md` — MMM concepts, marketer-facing.
- Create: `skills/meridian-analyst/references/budget-optimization.md` — dominant playbook incl. forward planning.
- Create: `skills/meridian-analyst/references/channel-performance.md` — attribution / ranking / diagnostics.
- Modify: `src/google_meridian_mcp_server/server.py` — register provider + add `instructions`.
- Create: `tests/contract/test_skill_provider.py` — assert the skill resource is served and readable.
- Modify: `README.md` — add a "Bundled skill" section (folder-drop fallback).

---

## Task 1: Provider wiring + instructions pointer + contract test

Delivers a discoverable, tested skill resource. Content is a minimal valid scaffold here; depth is authored in Tasks 2–4.

**Files:**
- Modify: `src/google_meridian_mcp_server/server.py:63-71`
- Create: `skills/meridian-analyst/SKILL.md` (scaffold)
- Test: `tests/contract/test_skill_provider.py`

**Interfaces:**
- Consumes: `SkillsDirectoryProvider(roots, reload=False, main_file_name="SKILL.md", supporting_files="template")`; `FastMCP.add_provider(provider, *, namespace="")`. Confirmed present in `fastmcp==3.4.2`.
- Produces: MCP resources `skill://meridian-analyst/SKILL.md` and `skill://meridian-analyst/_manifest`; resource template `skill://meridian-analyst/{path*}` for `references/*`.

- [ ] **Step 1: Create the scaffold SKILL.md**

`skills/meridian-analyst/SKILL.md`:

```markdown
---
name: meridian-analyst
description: >-
  Use when analyzing a Google Meridian marketing-mix model through this MCP —
  budget optimization and reallocation, channel ROI/performance, response
  curves, adstock, reach & frequency, or model diagnostics. Routes business
  questions to the right tools and interprets the results.
---

# Meridian Analyst

Guidance for using this server's Meridian marketing-mix-model tools. Start every
analysis with `list_models` → `get_model_overview`, then read
`available_tool_options` to see which tools and metrics are valid for that model.

Detailed playbooks live in `references/` and are loaded on demand.
```

- [ ] **Step 2: Write the failing contract test**

`tests/contract/test_skill_provider.py`:

```python
"""Contract test: the bundled meridian-analyst skill is served and readable."""

from __future__ import annotations

import pytest

from fastmcp import Client
from google_meridian_mcp_server.server import create_server

SKILL_URI = "skill://meridian-analyst/SKILL.md"


@pytest.mark.asyncio
async def test_skill_resource_is_served_and_readable():
    mcp = create_server()
    async with Client(mcp) as client:
        uris = {str(r.uri) for r in await client.list_resources()}
        assert SKILL_URI in uris
        assert "skill://meridian-analyst/_manifest" in uris

        contents = await client.read_resource(SKILL_URI)
        text = contents[0].text
        assert "name: meridian-analyst" in text
        assert "description:" in text
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `uv run pytest tests/contract/test_skill_provider.py -v`
Expected: FAIL — resource not found (provider not yet registered).

- [ ] **Step 4: Wire the provider and instructions into `create_server`**

In `src/google_meridian_mcp_server/server.py`, add imports near the top:

```python
from pathlib import Path

from fastmcp.server.providers.skills import SkillsDirectoryProvider
```

Add module-level constants above `create_server`:

```python
_SKILLS_ROOT = Path(__file__).resolve().parents[2] / "skills"

_SERVER_INSTRUCTIONS = (
    "Google Meridian marketing-mix-model analysis and budget optimization tools. "
    "This server bundles a 'meridian-analyst' skill with orchestration, domain, and "
    "scenario guidance. Read skill://meridian-analyst/SKILL.md before analysis — "
    "especially for budget optimization, reallocation, or channel-performance "
    "questions."
)
```

Replace the body of `create_server` with:

```python
def create_server() -> FastMCP:
    """Build and return a configured FastMCP server instance."""
    mcp = FastMCP(
        "Google Meridian MCP Server",
        instructions=_SERVER_INSTRUCTIONS,
        lifespan=_lifespan,
    )

    register_tools(mcp)
    mcp.add_provider(SkillsDirectoryProvider(roots=_SKILLS_ROOT))
    return mcp
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `uv run pytest tests/contract/test_skill_provider.py -v`
Expected: PASS.

- [ ] **Step 6: Confirm clean server boot and full suite + lint**

Run: `uv run pytest -q`
Expected: all tests pass (existing count + the new one).
Run: `uv run ruff check src tests && uv run ruff format --check src tests`
Expected: clean.
Run (boot smoke, background + read log): start `uv run python -m google_meridian_mcp_server.server`, confirm it logs startup with no traceback, then stop it.
Expected: server starts; `_SKILLS_ROOT` resolves to the repo-root `skills/` directory (verify the path exists at runtime; if a packaging/deploy context can't see repo-root `skills/`, note it for the README/deploy follow-up).

- [ ] **Step 7: Commit**

```bash
git add skills/meridian-analyst/SKILL.md src/google_meridian_mcp_server/server.py tests/contract/test_skill_provider.py
git commit -m "feat(skill): serve bundled meridian-analyst skill via FastMCP provider"
```

---

## Task 2: Author SKILL.md core + taxonomy.md + glossary.md (critique loop)

Foundational content both playbooks depend on. **Author under the critique loop (≥2 iterations).**

**Files:**
- Modify: `skills/meridian-analyst/SKILL.md`
- Create: `skills/meridian-analyst/references/taxonomy.md`
- Create: `skills/meridian-analyst/references/glossary.md`

**Interfaces:**
- Consumes: the scaffold SKILL.md and served-resource wiring from Task 1; spec §5.1–5.3.
- Produces: the always-on core (`SKILL.md`) and the two shared foundation references that Tasks 3–4 link to.

- [ ] **Step 1: Regenerate the canonical tool list to cross-check against**

Run:
```bash
uv run python -c "import asyncio; from google_meridian_mcp_server.server import create_server; print(sorted(t.name for t in asyncio.run(create_server().list_tools())))"
```
Use the printed list as the source of truth for tool names in all content. Also skim `AGENTS.md` "Current Analysis Behavior" / "Model Overview Expectations" and `domain/optimization.py` for taxonomy/enum facts.

- [ ] **Step 2: Write `SKILL.md` core** (keep it lean — this is always-on)

Expand the scaffold body to include, in this order:
1. One paragraph — what this MCP is (Meridian MMM analysis tools + a long-running optimization module).
2. **The golden path** — always `list_models` → `get_model_overview`; read `available_tool_options` to learn which tools/metrics are legal for *this* model before calling anything else.
3. **Cardinal rules** (the easy-to-get-wrong ones):
   - Optimization is asynchronous: `run_optimization` returns a `run_id`; poll `get_optimization_status` until `completed`, then `get_optimization_result`. Never assume a synchronous answer.
   - Pick the objective by the model's revenue capability: revenue models → ROAS/ROI; no-revenue models → CPIK. `get_model_overview` tells you which.
   - RF tools (`get_reach_frequency`) apply only to RF models; revenue metrics only to revenue models.
   - Always report credible intervals (`ci_lo`/`ci_hi`); never present a point estimate as certain.
4. Taxonomy in ~5 lines, then "full matrix → `references/taxonomy.md`".
5. **Routing table**: budget questions → `references/budget-optimization.md`; channel/performance questions → `references/channel-performance.md`; unknown term → `references/glossary.md`.

- [ ] **Step 3: Write `references/taxonomy.md`**

The full model-type matrix and consequences (from `AGENTS.md`):
- national vs. geo; revenue vs. KPI-only vs. KPI+`revenue_per_kpi`.
- Valid tools/metrics per type: `roi`/`marginal_roi` → `metric_not_supported` on no-revenue; `cpik`/`marginal_cpik` valid everywhere; `get_reach_frequency` RF-only.
- How to read validity off `get_model_overview.available_tool_options` at runtime instead of guessing.

- [ ] **Step 4: Write `references/glossary.md`**

1–2 sentences each, marketer-facing: ROAS, marginal ROI (mROI), CPIK, contribution vs. response curve, adstock/carryover, saturation / diminishing returns, reach & frequency, base vs. incremental, credible interval.

- [ ] **Step 5: Correctness cross-check**

Verify every tool name used appears in the Step-1 list; every metric/enum term matches `domain/optimization.py` / `AGENTS.md`; no tool *parameters* are restated (Global Constraint). Confirm the contract test still passes:
Run: `uv run pytest tests/contract/test_skill_provider.py -q`
Expected: PASS.

- [ ] **Step 6: Critique loop (≥2 iterations)**

Reviewer critiques against: (1) LLM understanding — would an LLM route correctly and interpret results right from this alone? (2) correctness vs. live surface; (3) no param duplication; (4) token economy (lean core, depth in references), never at the expense of understanding. Implementer fine-tunes. **Repeat at least twice**; continue until a pass yields no material findings.

- [ ] **Step 7: Commit**

```bash
git add skills/meridian-analyst/SKILL.md skills/meridian-analyst/references/taxonomy.md skills/meridian-analyst/references/glossary.md
git commit -m "docs(skill): author SKILL.md core, taxonomy, and glossary"
```

---

## Task 3: Author budget-optimization.md — the dominant playbook (critique loop)

The highest-value reference. Includes the forward-planning approximation. **Author under the critique loop (≥2 iterations).**

**Files:**
- Create: `skills/meridian-analyst/references/budget-optimization.md`

**Interfaces:**
- Consumes: taxonomy/glossary from Task 2; spec §5.4 and §5.4.1; the canonical optimization vocabulary in Global Constraints.
- Produces: the reference `SKILL.md`'s routing table points to for budget questions.

- [ ] **Step 1: Write the scenario library**

A table mapping each business question to tool routing (from spec §5.4):

| User question | Routing |
|---|---|
| "How should I allocate my whole budget?" | `run_optimization` `fixed_budget` (omit budget = current total) → poll → result |
| "What if I add N% more budget — where does it go?" | `fixed_budget` with raised `budget`; diff allocation vs. current |
| "N% budget cut — where do I cut with least damage?" | `fixed_budget` with lowered `budget` |
| "Shift $X from channel A → B — predicted impact?" | No single tool: two `get_spend_scenario` calls (A down, B up), or a `per_channel` constraint; compare marginal outcomes |
| "Hit / maintain a target ROAS" | `run_optimization` `target_roas` |
| "Max spend per channel at a minimum marginal-ROI hurdle" | `target_mroas` |
| "Which channels are saturated / over-invested?" | `get_response_curves` + marginal ROI: where mROI < target → past the plateau |
| "Which channels have headroom / under-invested?" | High marginal ROI → push more |
| "Don't move any channel more than ±X% / freeze channel Z" | `constraint`: `global` pct vs. `per_channel` bounds |
| "Optimize just for Q4 / a specific window" | `start_date`/`end_date` window |
| "Reallocate within specific regions only" | `selected_geos` |
| "Plan NEXT quarter's budget (forward-looking)" | Forward-planning approximation — see the forward-planning section below |

- [ ] **Step 2: Write the two teaching points**

1. **"Shift A→B" has no dedicated tool** — express via spend scenarios or per-channel constraints.
2. **Saturation / headroom is a reasoning step** — read response curves *and* marginal ROI together; not one tool call.

- [ ] **Step 3: Write the full lifecycle section**

Submit → poll (`queued`/`running`/`completed`/`failed`/`canceled`) → result; how to read the result (summary, per-channel before/after, allocation, spend-delta, `outcome_mode`); fingerprint reuse; interpreting optimized vs. non-optimized.

- [ ] **Step 4: Write the forward-planning section** (spec §5.4.1)

- Frame honestly: MMM optimization is backward-looking.
- The approximation with existing knobs: set `budget` to the planned future total; set `start_date`/`end_date` to a recent window resembling expected future conditions (last Q4 → next Q4); apply real planning constraints; read via **marginal ROI**, not average ROI.
- **Mandated caveats on every forward recommendation:** assumes CPM, price/LTV, and response-curve shapes stay stable; extrapolation risk beyond historical spend; forecasts *incremental outcome*, not absolute future KPI; validate large moves with geo/holdout experiments.
- One line noting the server does not expose Meridian's `new_data` scenario-planning knob (documented limitation).

- [ ] **Step 5: Correctness cross-check**

Every scenario type / constraint mode / status / config field must match `domain/optimization.py`. No `run_optimization` *parameter schemas* restated (point to the tool). Contract test still green:
Run: `uv run pytest tests/contract/test_skill_provider.py -q`
Expected: PASS.

- [ ] **Step 6: Critique loop (≥2 iterations)**

Same rubric as Task 2 Step 6, with extra attention to: does an LLM correctly route the *forward-planning* question to the approximation AND always attach the caveats? **Repeat at least twice.**

- [ ] **Step 7: Commit**

```bash
git add skills/meridian-analyst/references/budget-optimization.md
git commit -m "docs(skill): author budget-optimization playbook incl. forward planning"
```

---

## Task 4: Author channel-performance.md (critique loop)

The second dominant reference. **Author under the critique loop (≥2 iterations).**

**Files:**
- Create: `skills/meridian-analyst/references/channel-performance.md`

**Interfaces:**
- Consumes: taxonomy/glossary from Task 2; spec §5.5.
- Produces: the reference `SKILL.md`'s routing table points to for channel/performance questions.

- [ ] **Step 1: Write the reference**

Cover, each as a short question → tool routing + how to read the result:
- Rank channels by ROI / contribution / efficiency → `get_channel_summary`.
- Base vs. incremental → `get_contribution` + baseline.
- Carryover → `get_adstock_decay`; saturation curves → `get_response_curves`.
- Reach & frequency, optimal frequency → `get_reach_frequency` (RF-only; else `metric_not_supported`).
- Single-channel what-if → `get_spend_scenario` (per-time-unit inputs; efficiency triplet).
- Model trust / fit → `get_model_fit` (expected/actual/baseline/residual) and how to talk about it.
- Restate the credible-interval discipline in context.

- [ ] **Step 2: Correctness cross-check**

Tool names against the Task 2 Step-1 list; RF/revenue validity against taxonomy; no parameter schemas restated. Contract test green:
Run: `uv run pytest tests/contract/test_skill_provider.py -q`
Expected: PASS.

- [ ] **Step 3: Critique loop (≥2 iterations)**

Same rubric. **Repeat at least twice.**

- [ ] **Step 4: Commit**

```bash
git add skills/meridian-analyst/references/channel-performance.md
git commit -m "docs(skill): author channel-performance reference"
```

---

## Task 5: README "Bundled skill" section

Document discovery + the folder-drop fallback.

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add the section**

Add a "Bundled skill" section covering:
- The server bundles a `meridian-analyst` Agent Skill, discoverable over `skill://meridian-analyst/SKILL.md` when a client supports the skills provider.
- Fallback: copy `skills/meridian-analyst/` into the client's skills folder (e.g. `.claude/skills/`) verbatim — it is a standards-compliant Agent Skill (agentskills.io) and needs no conversion.
- One line: the skill teaches orchestration, taxonomy, budget-optimization/reallocation, and channel-performance workflows; it does not replace the per-tool descriptions.

- [ ] **Step 2: Verify + commit**

Run: `uv run pytest -q && uv run ruff check src tests`
Expected: green.
```bash
git add README.md
git commit -m "docs: document the bundled meridian-analyst skill and folder-drop fallback"
```

---

## Final whole-branch review

After Task 5, dispatch the broad code review (superpowers:requesting-code-review) on the full branch range, on the most capable model. Focus it on the Global Constraints — especially **LLM understanding**, **no param duplication**, and **correctness vs. the live tool surface** — plus the accumulated Minor findings from the per-task critique loops. Then use superpowers:finishing-a-development-branch.

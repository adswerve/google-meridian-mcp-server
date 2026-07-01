# Design — `meridian-analyst` Agent Skill (bundled with the MCP server)

**Date:** 2026-07-01
**Status:** Proposed (awaiting review)
**Topic:** A bundled Agent Skill that ships *with* the Meridian MCP server and
teaches any connecting LLM the orchestration, domain, and scenario knowledge that
cannot live inside individual tool descriptions — weighted toward the dominant
use cases: **budget optimization / reallocation** and **channel performance**.

---

## 1. Motivation

The server exposes 17 well-described tools, but every tool description is
necessarily *local*: it documents one tool's parameters in isolation. It cannot
express the knowledge an LLM needs to use the MCP *well*:

- **Cross-tool workflows** — the optimization lifecycle is `run_optimization` →
  poll `get_optimization_status` until `completed` → `get_optimization_result`.
  No single description can teach a multi-tool sequence.
- **Model-taxonomy decision-making** — national vs. geo, revenue vs. KPI-only vs.
  KPI+`revenue_per_kpi`, and which tools/metrics are legal for *this* model
  (`roi` is invalid on no-revenue models; `get_reach_frequency` only exists for
  RF models). This is the #1 way an LLM misuses the MCP today.
- **Domain grounding** — what ROAS, marginal ROI, CPIK, adstock/carryover,
  saturation, and reach & frequency actually *mean*, so the LLM interprets
  results instead of just fetching numbers.
- **Scenario routing** — turning a plain-English business question
  ("where do I cut 10%?") into the right tool sequence and the right
  `run_optimization` scenario type.

The goal is **to ship this expertise as part of the product**: a skill bundled in
the server package, discoverable the moment a client connects, so any
skills-compatible client gets a capable MMM analyst rather than raw tools.

### 1.1 Confirmed facts grounding the design

- **The skills mechanism exists in our pinned stack.** `fastmcp==3.4.2` ships
  `fastmcp.server.providers.skills` (verified importable). A
  `SkillsDirectoryProvider(roots=...)` exposes a skill directory over `skill://`
  URIs; a client lists MCP resources, reads `skill://<name>/SKILL.md` plus the
  `_manifest`, and loads supporting files on demand.
- **The Agent Skills format is an open standard** (originally Anthropic's, now at
  agentskills.io, adopted by Claude Code, Cursor, Gemini CLI, Copilot, etc.). A
  skill is a folder with a required `SKILL.md` (`name` + `description` frontmatter
  minimum + instructions) and optional `references/`, `scripts/`, `assets/`
  subdirs. Progressive disclosure is a 3-stage contract: **discovery**
  (name+description only), **activation** (full `SKILL.md`), **execution** (load
  `references/*` on demand).
- **Meridian's three budget-optimization framings map 1:1 onto our
  `run_optimization` scenario types**: max outcome at a fixed budget →
  `fixed_budget`; hit a target overall ROI → `target_roas`; target marginal ROI
  per channel → `target_mroas`. This is confirmed by the domain models in
  `src/google_meridian_mcp_server/domain/optimization.py`.
- **The taxonomy and gotchas already exist, battle-tested**, in `AGENTS.md`
  ("Current Analysis Behavior", "Model Overview Expectations"). The skill lifts
  and reframes them for an analyst audience — it does not invent them.

---

## 2. Goals & non-goals

### Primary criterion (in priority order)

1. **LLM understanding first.** The skill is judged primarily on whether it makes
   an LLM use the MCP *correctly and confidently*. This has precedence over every
   other consideration.
2. **Token economy second.** The skill must not bloat the context window. Favor
   progressive disclosure: a lean `SKILL.md`, depth deferred to `references/`.
   When (1) and (2) conflict, (1) wins — but the default posture is to say things
   once, concisely, and link rather than repeat.

### Non-goals (explicit — these keep drift low)

- **Do NOT re-document tool parameters.** Parameter detail lives in the tool
  descriptions (already invested in). The skill *points to* the tools; it never
  restates their signatures, enum values, or field-level rules. This is the
  single rule that prevents the skill from going stale when a tool changes.
- **No new server tools or behavior changes.** This is documentation + a provider
  registration, not a functional change to any analysis or optimization path.
- **No client-specific content.** The skill must read identically whether served
  over `skill://` or dropped into a client's skills folder.

---

## 3. Delivery architecture

Three delivery paths, one canonical source of bytes:

1. **Primary — bundled & discoverable.** Register a
   `SkillsDirectoryProvider` in `create_server()` pointed at the in-package
   `skills/` directory, so the skill ships inside the server and is discoverable
   over `skill://meridian-analyst/SKILL.md` the moment a client connects.
2. **Defensive — instructions pointer.** Append ~2–3 lines to the server
   `instructions` string ("This server bundles a `meridian-analyst` skill; read
   `skill://meridian-analyst/SKILL.md` before analysis"). Clients that ignore the
   skills provider but read `instructions` still get pointed at it.
3. **Fallback — folder-drop.** Because the directory is a standards-compliant
   Agent Skill, a user of any client that doesn't surface `skill://` resources can
   copy `skills/meridian-analyst/` into that client's skills folder (e.g.
   `.claude/skills/`) verbatim — no conversion. Documented in the README.

**Portability rule:** `SKILL.md` frontmatter uses the standard `name` +
`description` fields, and all internal links are relative
(`references/…`), so the identical directory works served-by-MCP or
dropped-in-a-folder with zero edits.

---

## 4. File structure (agentskills.io-compliant)

```
skills/
└── meridian-analyst/
    ├── SKILL.md                    # lean core; description = the trigger
    └── references/
        ├── taxonomy.md             # model-type × tool-validity matrix + gotchas
        ├── glossary.md             # MMM concepts, marketer-facing
        ├── budget-optimization.md  # dominant playbook (deep)
        └── channel-performance.md  # attribution / ranking / diagnostics (deep)
```

The two dominant use cases — **budget optimization/reallocation** and **channel
performance** — each get a dedicated, thorough reference. `taxonomy.md` and
`glossary.md` are shared foundations both playbooks lean on.

---

## 5. Content design

### 5.1 `SKILL.md` (lean core — the always-activated layer)

Frontmatter:

```yaml
---
name: meridian-analyst
description: >-
  Use when analyzing a Google Meridian marketing-mix model through this MCP —
  budget optimization and reallocation, channel ROI/performance, response curves,
  adstock, reach & frequency, or model diagnostics. Routes business questions to
  the right tools and interprets the results.
---
```

Body (concise; everything below is a pointer or a rule, not a treatise):

- **What this MCP is** — one paragraph: a Meridian MMM server; analysis tools +
  a long-running optimization module.
- **The golden path** — always start with `list_models` → `get_model_overview`;
  read `available_tool_options` to learn which tools/metrics are legal for *this*
  model before calling anything else.
- **Cardinal rules** (the high-value, easy-to-get-wrong ones):
  - Optimization is asynchronous: `run_optimization` returns a `run_id`; poll
    `get_optimization_status` until `completed`, then `get_optimization_result`.
    Never assume it returned the answer synchronously.
  - Pick the objective by the model's revenue capability: revenue models →
    ROAS/ROI; no-revenue models → CPIK. `get_model_overview` tells you which.
  - RF tools apply only to RF models; revenue metrics only to revenue models.
  - Always report credible intervals (`ci_lo`/`ci_hi`); never present a point
    estimate as certain.
- **Taxonomy in ~5 lines**, then "for the full matrix see
  `references/taxonomy.md`."
- **Where to go next** — a short routing table: budget questions →
  `references/budget-optimization.md`; channel/performance questions →
  `references/channel-performance.md`; term you don't know →
  `references/glossary.md`.

### 5.2 `references/taxonomy.md`

The full model-type matrix and its consequences, lifted from `AGENTS.md`:

- national vs. geo; revenue vs. KPI-only vs. KPI+`revenue_per_kpi`.
- Which tools/metrics are valid per type (e.g. `roi`/`marginal_roi` →
  `metric_not_supported` on no-revenue; `get_reach_frequency` RF-only;
  `cpik`/`marginal_cpik` valid everywhere).
- How to read this off `get_model_overview.available_tool_options` at runtime
  rather than guessing.

### 5.3 `references/glossary.md`

1–2 sentences each, marketer-facing: ROAS, marginal ROI (mROI), CPIK, contribution
vs. response curve, adstock/carryover, saturation / diminishing returns, reach &
frequency, base vs. incremental, credible interval.

### 5.4 `references/budget-optimization.md` (dominant playbook)

The scenario library, each row = a business question → tool routing:

| User question | Routing |
|---|---|
| "How should I allocate my whole budget?" | `run_optimization` `fixed_budget` (omit budget = current total) → poll → result |
| "What if I add N% more budget — where does it go?" | `fixed_budget` with raised `budget`; diff allocation vs. current |
| "N% budget cut — where do I cut with least damage?" | `fixed_budget` with lowered `budget`; result shows least-harmful pullbacks |
| "Shift $X from channel A → B — predicted impact?" | No single tool: two `get_spend_scenario` calls (A down, B up), or a `per_channel` constraint; compare marginal outcomes |
| "Hit / maintain a target ROAS" | `run_optimization` `target_roas` |
| "Max spend per channel at a minimum marginal-ROI hurdle" | `target_mroas` |
| "Which channels are saturated / over-invested?" | `get_response_curves` + marginal ROI: where mROI < target → past the plateau |
| "Which channels have headroom / under-invested?" | High marginal ROI → push more |
| "Don't move any channel more than ±X% / freeze channel Z" | `constraint`: `global` pct vs. `per_channel` bounds |
| "Optimize just for Q4 / a specific window (flighting, seasonality)" | `start_date`/`end_date` window |
| "Reallocate within specific regions only" | `selected_geos` |

Plus two teaching points an LLM reliably gets wrong:

1. **"Shift A→B" has no dedicated tool** — express it via spend scenarios or
   per-channel constraints.
2. **Saturation / headroom is a reasoning step** — read response curves *and*
   marginal ROI together; it is not a single tool call.

And the lifecycle in full: submit → poll (`queued`/`running`/`completed`/
`failed`/`canceled`) → result; how to read the result (summary, per-channel
before/after, allocation, spend-delta, `outcome_mode`); fingerprint reuse; how to
interpret optimized vs. non-optimized.

### 5.5 `references/channel-performance.md`

- Rank channels by ROI / contribution / efficiency (`get_channel_summary`).
- Base vs. incremental (`get_contribution` + baseline).
- Carryover (`get_adstock_decay`); saturation curves (`get_response_curves`).
- Reach & frequency, optimal frequency (`get_reach_frequency`, RF-only).
- Single-channel what-if (`get_spend_scenario`).
- Model trust / fit (`get_model_fit`) and how to talk about it.
- The credible-interval discipline restated in context.

---

## 6. Server wiring

In `create_server()` (`src/google_meridian_mcp_server/server.py`), after the app
is built:

```python
from pathlib import Path
from fastmcp.server.providers.skills import SkillsDirectoryProvider

_SKILLS_ROOT = Path(__file__).resolve().parents[2] / "skills"
mcp.add_provider(SkillsDirectoryProvider(roots=_SKILLS_ROOT))
```

(Exact import path and `add_provider`/constructor signature to be confirmed
against installed `fastmcp==3.4.2` during the implementation spike — see §8.)
Also append the defensive pointer to the existing `instructions` string.

---

## 7. Authoring process — critique loop (REQUIRED)

The skill's *content quality* is the whole point, so it is authored under an
explicit **implementer ↔ reviewer critique loop**, not written once:

1. **Implementer** writes/refines `SKILL.md` + the four references.
2. **Reviewer** critiques against the acceptance criteria below and returns
   concrete, actionable findings.
3. **Implementer** fine-tunes to address the findings.
4. Repeat. **Minimum two full loops; more if the reviewer still finds material
   issues.** The loop ends only when a review pass returns no material findings.

The reviewer critiques for, in priority order:

- **LLM understanding (primary):** Would an LLM, reading only this, route a
  budget/channel question to the correct tools and interpret the result
  correctly? Are the cardinal rules unambiguous? Is the taxonomy correct?
- **Correctness:** Every tool name, scenario type, enum, and workflow matches the
  actual server surface (cross-checked against `transport/tools.py`,
  `domain/optimization.py`, `AGENTS.md`). No invented capabilities.
- **No param duplication:** the non-goal in §2 is respected.
- **Token economy (secondary):** `SKILL.md` stays lean; depth is deferred to
  `references/`; nothing is said twice. Not at the expense of understanding.

---

## 8. Validation

- **Implementation spike (do first):** register the provider locally, boot the
  server, and confirm (a) the server starts clean with the provider, and (b) a
  real client (in-process `Client(mcp)` and Claude Code) can list and read the
  `skill://meridian-analyst/SKILL.md` resource. This confirms/corrects the exact
  FastMCP API and validates the primary delivery path before content authoring.
- **Contract test:** a lightweight test asserting the skill resource is
  registered and its `SKILL.md` is reachable (mirrors the existing
  `contract/` tests).
- **Content review:** the §7 critique loop is the substantive gate.
- **Docs:** README gains a short "Bundled skill" section documenting the
  folder-drop fallback.

---

## 9. Risks & mitigations

- **Drift** (skill vs. tools) → mitigated by the §2 non-goal (never restate
  params) and the §7 correctness review cross-checking the live tool surface.
- **Uneven client support for `skill://`** → mitigated by the 3-path delivery
  (§3): instructions pointer + folder-drop fallback.
- **FastMCP provider API differs from the doc** → the §8 spike confirms the exact
  signature before we build content on top of it.
- **Token bloat** → progressive disclosure; only `SKILL.md` is always-on.

---

## 10. Open questions

- Exact `SkillsDirectoryProvider` constructor/registration signature in
  `fastmcp==3.4.2` (resolved by the §8 spike).
- Whether to also expose the skill as a plain MCP resource for resource-aware
  clients that don't implement the skills provider (deferred; revisit if the
  spike shows weak client support).

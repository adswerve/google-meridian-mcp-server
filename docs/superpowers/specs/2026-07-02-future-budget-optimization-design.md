# Future Budget Optimization — Design

**Date:** 2026-07-02
**Status:** Approved (brainstorming)
**Scope:** Add a future-facing budget optimization tool to the MCP server, sharing the
existing optimization backend (registry, worker, polling, tiers, result shaping).

## 1. Motivation

Today `run_optimization` reallocates a model's **historical** budget across channels over
a past date window. Users also want to plan **forward**: "how should I split next quarter's
budget?", "we have $X for Q4, allocate it", "TV CPMs are rising 20% next quarter — re-optimize".

Meridian supports this through the *same* `BudgetOptimizer.optimize()` call plus one extra
argument, `new_data` (a `DataTensors`), and future `start_date`/`end_date`. So the natural
shape is: **one new submission tool, one config extension, one branch in the facade — every
other piece of the optimization module is reused unchanged.**

## 2. The load-bearing constraint (must be surfaced to users)

**Meridian does not forecast the future.** The Bayesian posterior is frozen on the training
data; the trained response curves are date-agnostic. "Future optimization" therefore means:

> Given the user's *assumptions* about the future — future cost per media unit, future
> flighting, future revenue-per-KPI, a future budget, and a planned mix — find the optimal
> allocation, using the frozen trained response curves.

The user (agent) supplies the assumptions; Meridian supplies the response shape. With all
assumptions left at their carry-forward defaults, a future run is **directionally comparable** to
a historical run over the reference window — not identical: a historical-window run also captures
lagged outcome carried in from media executed *before* the window, whereas the future `new_data`
window starts cold, so under strong adstock the two diverge systematically (`optimizer.py:1472-1477`).
The tool's value comes from the **overrides** (cost/revenue multipliers, arbitrary horizon,
planned mix) — the tool description will state this plainly so results are never mistaken for a
demand forecast.

## 3. Mechanics reference (traced from Meridian source)

- `optimize(new_data=DataTensors(...), start_date, end_date, ...)` runs the optimization on
  the supplied tensors while keeping the trained posterior. A future window **requires**
  `new_data` because `start_date`/`end_date` must fall within `new_data.time`
  (`optimizer.py:1511-1514`).
- `create_optimization_tensors(time, cpmu, cprf, media/media_spend, rf_impressions/rf_spend,
  revenue_per_kpi, use_optimal_frequency)` (`optimizer.py:1776`) builds a valid `DataTensors`
  from simple inputs: cost-per-media-unit, a flighting pattern, and revenue-per-KPI. This is
  the agent-friendly door — we never hand-build raw geo×time×channel tensors.
- `pct_of_spend` sets the **center** of the search: `center_spend = budget × pct_of_spend`
  (`optimizer.py:229`); default is the reference allocation `hist_spend/sum(hist_spend)`
  (`optimizer.py:3012`). It must cover every paid+RF channel and sum to 1.0
  (`optimizer.py:3006-3009`).
- `spend_constraint_lower/upper` set the **box width** around that center:
  `[(1−lower)·center, (1+upper)·center]` (`optimizer.py:3080-3083`). Center (`pct_of_spend`)
  and width (`spend_constraint`) are orthogonal and stack.
- Cost per media unit converts spend→media units; a "cost multiplier" is
  `CPMU_future = multiplier × CPMU_reference`. Higher cost ⇒ fewer media units per dollar ⇒
  lower ROI/mROI ⇒ optimizer shifts budget away from that channel, all else equal.

## 4. Design decisions (from brainstorming)

| Decision | Choice |
|---|---|
| Tool surface | One new `run_future_optimization`; five existing run-id tools reused unchanged |
| Assumption surface | Simple: horizon + per-channel cost multipliers + revenue multiplier + optional planned mix; carry-forward defaults |
| Horizon spec | Explicit future `start_date` + `horizon` count of periods (model cadence) |
| Reference window | Three presets: `trailing`, `same_period_last_year`, `full_history_average` (discriminated union, extensible to `custom` later) |
| Budget seeding | Reference window seeds the default budget when `fixed_budget` omits an explicit budget (enables YoY budget) |
| `pct_of_spend` | Exposed on the **future tool only** as `planned_allocation` |
| `planned_allocation` validation | **Friendly normalize** — fill missing channels from carried-forward mix, renormalize to sum 1.0 |

## 5. Tool surface

Add **one** submission tool:

- `run_future_optimization(model_id, config, label?, note?, compute_tier="auto", force_rerun=false)`
  → returns the same submit envelope (`run_id`, `status`, `compute_tier_resolved`, `backend`,
  `size_score`, `reused`) into the **same** registry.

Reused **unchanged** (already run-id based, result-shape agnostic):
`get_optimization_status`, `get_optimization_result`, `list_optimizations`,
`cancel_optimization`, `delete_optimization`.

## 6. Config — shared base + two subclasses

The core is factored into a **`BaseOptimizationConfig`** holding only the fields common to both
optimizers; each optimizer subclasses it and adds only what is genuinely its own. This is real
reuse without a leaky abstraction — `FutureOptimizationConfig` does **not** inherit the
historical `start_date`/`end_date` window selectors (the future window comes from
`future.start_date + future.horizon`), so neither subclass carries a field that doesn't apply to
it.

```
BaseOptimizationConfig(BaseModel)            # reusable core
  scenario:      Scenario                     # fixed_budget | target_roas | target_mroas
  constraint:    Constraint                   # global | per_channel, default global ±0.3
  selected_geos: list[str] | None
  use_kpi:       bool | None

OptimizationConfig(BaseOptimizationConfig)    # historical (existing tool, now rebased)
  kind: Literal["historical"] = "historical"  # discriminator (persistence + worker dispatch)
  start_date:  date | None                    # historical window
  end_date:    date | None

FutureOptimizationConfig(BaseOptimizationConfig)   # future (new tool)
  kind: Literal["future"] = "future"          # discriminator
  future:
    start_date:   date             # required; first future period (inclusive)
    horizon:      int (>0)         # required; number of future periods at model cadence
    reference:    Reference        # default {mode: "trailing"}
    cost_multipliers:            dict[channel, float] | None   # default all 1.0
    revenue_per_kpi_multiplier:  float                          # default 1.0
    planned_allocation:          dict[channel, float] | None   # optional pct_of_spend center
```

The `kind` discriminator lets `OptimizationRun.config` be a discriminated union so a persisted
run round-trips to the correct class on reload and the worker dispatches by type (see §10).

**Backward-compatibility caveat (verified against pydantic 2.13).** A plain
`Field(discriminator="kind")` does **not** fall back to the field default when `kind` is absent —
it raises `union_tag_not_found`, which would break `get_record`/`list_optimizations` for every
pre-upgrade `record.json` (which has no `kind`). Use a **callable discriminator** that defaults a
missing tag to `"historical"`:

```python
from pydantic import Discriminator, Tag

def _config_kind(v) -> str:
    if isinstance(v, dict):
        return v.get("kind", "historical")
    return getattr(v, "kind", "historical")

AnyOptimizationConfig = Annotated[
    Annotated[OptimizationConfig, Tag("historical")]
    | Annotated[FutureOptimizationConfig, Tag("future")],
    Discriminator(_config_kind),
]
```

A test must round-trip a full `OptimizationRun` JSON **without** `kind` through
`model_validate_json` (not just `OptimizationConfig.model_validate`, which never exercises the
union path).

`Reference` is a discriminated union (mirrors existing `Scenario`/`Constraint` pattern):

```
Reference =
  { mode: "trailing" }                 # last `horizon` periods of training data
  { mode: "same_period_last_year" }    # `horizon` periods starting one year before start_date
  { mode: "full_history_average" }     # average over all training periods
# (future: { mode: "custom", reference_start_date, reference_end_date })
```

`start_date` must be after the model's last training period (else
`invalid_optimization_config`). `same_period_last_year` requires ≥1 year of training data
aligned to the window (else `invalid_optimization_config`).

## 7. Carry-forward semantics

**Two independent axes.** It is important not to conflate them:

- **Allocation axis** — *how much* spend, and *how it splits*. Set by `budget` (total) and
  `planned_allocation`/`pct_of_spend` (split / box center). When both are given explicitly,
  the reference window is **not** consulted for them.
- **Cost-structure axis** — *what a dollar buys and when*. Cost-per-media-unit, flighting
  shape, and revenue-per-KPI. These are **always** required for a future window (the trained
  response curve maps media units → outcome, not dollars → outcome), and they can only come
  from the reference window (optionally scaled by `cost_multipliers` /
  `revenue_per_kpi_multiplier`). Setting `planned_allocation` + `budget` does **not** remove
  this need — without cost/flighting, `optimize()` cannot evaluate the future periods at all.

The selected reference window seeds, per channel:

1. **cost-per-media-unit** (`spend ÷ media units` over the window) → multiplied by
   `cost_multipliers`;
2. **flighting pattern** (relative spend shape) → projected onto the `horizon` future periods
   labeled `start_date, start_date+interval, …` (interval inferred from `InputData.time`
   spacing);
3. **revenue-per-KPI** → multiplied by `revenue_per_kpi_multiplier` (revenue models only;
   ignored/no-op when the model has no revenue-per-KPI);
4. **default budget** — when `fixed_budget` omits an explicit `budget`, it falls back to the
   **total of the seeded future flighting** (i.e. `horizon` periods of carried-forward spend), an
   explicit `budget` always overrides. **Not** the raw reference-window total: for
   `full_history_average` the window spans *all* training periods, so summing it would over-budget
   by ~`n_history / horizon`. Equivalently, pass `budget=None` and let Meridian default it from
   `new_data` over the future window (`optimizer.py:2209-2217`). For `trailing` /
   `same_period_last_year` the window length equals `horizon`, so window-total and seeded-flighting
   total coincide.

Preset → window mapping:
- `trailing` → the last `horizon` training periods.
- `same_period_last_year` → the `horizon` periods beginning ~one year before `start_date`, using a
  **cadence-aware year** (`round(365 / cadence_days)` periods back, e.g. 52 weeks for weekly), not
  a hardcoded 365 days — otherwise a next-period `start_date` lands one day before the earliest
  label and the window is spuriously rejected as "insufficient history".
- `full_history_average` → all training periods, averaged to a per-period cost/flighting.

For `full_history_average`, the per-period average is tiled across the `horizon` future
periods (flat flighting). For `trailing`/`same_period_last_year`, the window length equals
`horizon` by construction, so the flighting maps one-to-one.

## 8. `planned_allocation` (pct_of_spend on the future tool)

- Maps directly to `optimize(pct_of_spend=...)`: sets the **center** the `spend_constraint`
  box is applied around, and defines the **"non-optimized" baseline** in the result — so the
  result's `initial` table reads as *"your planned mix"* vs the `optimized` best mix.
- **If omitted** → `pct_of_spend=None`, and Meridian centers on the carried-forward reference
  allocation.
- **Friendly-normalize rule** (applied in the facade before calling Meridian, which is
  strict): (a) any channel the user omits is filled with its carried-forward weight; (b) the
  full vector is then renormalized to sum to 1.0. This lets the agent pass a partial or
  unnormalized dict (e.g. `{TV: 0.4}` or raw dollar-share guesses) without error.

## 9. Facade — one shared execution core, two input builders

The design goal: **`run` and `run_future` share the entire execution path** and differ **only**
in the `optimize()` kwargs they prepare. So the facade has one private core that owns everything
common — creating the `BudgetOptimizer`, calling `optimize`, best-effort response curves, and
`build_result` — and two thin public entry points that only *build inputs*.

```python
# ---- shared execution core (the only place optimize() is called) ----
def _run(self, config, build_kwargs) -> dict:
    from meridian.analysis import optimizer as optimizer_mod
    use_kpi = self.resolve_use_kpi(config)
    opt = optimizer_mod.BudgetOptimizer(self._mmm)          # created once, passed to the builder
    kwargs = build_kwargs(config, opt, use_kpi)             # the ONLY per-mode difference
    results = opt.optimize(**kwargs)
    curves = _best_effort(lambda: results.get_response_curves())
    return self.build_result(results.nonoptimized_data, results.optimized_data,
                             use_kpi=use_kpi, response_curves=curves)

# ---- historical entry point: just builds kwargs ----
def run(self, config: OptimizationConfig) -> dict:
    return self._run(config, self._historical_kwargs)

def _historical_kwargs(self, config, opt, use_kpi) -> dict:
    return to_optimize_kwargs(config, channel_order=self.channel_order(), use_kpi=use_kpi)
    # to_optimize_kwargs already emits start_date/end_date for the historical window

# ---- future entry point: just builds kwargs (needs `opt` for create_optimization_tensors) ----
def run_future(self, config: FutureOptimizationConfig) -> dict:
    return self._run(config, self._future_kwargs)

def _future_kwargs(self, config, opt, use_kpi) -> dict:
    f = config.future
    window      = self._reference_window(f.reference, f.horizon, f.start_date)   # indices into InputData
    time_lbls   = self._future_time_labels(f.start_date, f.horizon)
    cpmu, cprf  = self._carry_forward_costs(window, f.cost_multipliers)
    flighting   = self._carry_forward_flighting(window)                          # media_spend / rf_spend shape
    rev_per_kpi = self._carry_forward_rev_per_kpi(window, f.revenue_per_kpi_multiplier)
    new_data = opt.create_optimization_tensors(
        time=time_lbls, cpmu=cpmu, cprf=cprf, media_spend=flighting["media"],
        rf_spend=flighting["rf"], revenue_per_kpi=rev_per_kpi)
    kwargs = to_optimize_kwargs(config, channel_order=self.channel_order(), use_kpi=use_kpi)
    kwargs.pop("start_date", None); kwargs.pop("end_date", None)                 # replaced by future window
    kwargs.update(
        new_data=new_data,
        start_date=f.start_date.isoformat(), end_date=time_lbls[-1],
        pct_of_spend=self._normalize_planned_allocation(f.planned_allocation, window),  # or None
        budget=self._resolve_budget(config, window))                            # scenario budget, or window total
    return kwargs
```

So the split is clean: **`_run` is the common core** (identical for both, the single caller of
`optimize`/`build_result`), and `run`/`run_future` contribute *only* their `build_kwargs`
function. Reused verbatim by both: `build_result`, `_channel_rows`, `_summary`, `_allocation`,
`_spend_delta`, `_response_curve_rows`, `resolve_use_kpi`, `channel_order`, and the shared
`to_optimize_kwargs` core (which translates the `BaseOptimizationConfig` scenario + constraint +
geos + use_kpi once; each builder layers on its own window kwargs).

## 10. Service / worker / registry

- `to_optimize_kwargs` translates the shared `BaseOptimizationConfig` core; `run_future` adds
  the `new_data`/future-dates/`pct_of_spend`/`budget` kwargs on top.
- `config_fingerprint` hashes the whole config (incl. `kind` and the future block), so
  reuse-by-fingerprint and `force_rerun` work; historical and future runs with otherwise-identical
  fields never collide because `kind` differs. Nested-dict hashing is stable
  (`json.dumps(sort_keys=True)` sorts recursively). **Note:** because historical dumps now include
  `"kind": "historical"`, fingerprints computed *before* this change won't match new submissions —
  pre-upgrade runs recompute once on their first re-request. This is acceptable; call it out in the
  changelog.
- **Dispatch is by the `kind` discriminator** (§6): `OptimizationRun.config` is the discriminated
  union, so the worker calls `facade.run` for `kind="historical"` and `facade.run_future` for
  `kind="future"` — no side channel, and persisted runs reload into the correct class. Registry,
  state machine, phases, heartbeat, tiers, and size scoring are unchanged.

## 11. Result shape

Identical to the historical tool (`outcome_mode`, `summary`, `channel_tables.initial/optimized`,
`allocation`, `spend_delta`, optional `response_curves`). For future runs, `initial` reflects
the planned/carried-forward baseline and `optimized` the recommended future mix.

## 12. Validation & error handling

Two distinct rejection layers — be explicit about which fires where:

**Pydantic input validation (FastMCP, before the tool body):** rejected as a protocol-level tool
error, *not* the `invalid_optimization_config` envelope, because `config` is typed
`FutureOptimizationConfig`:
- `horizon <= 0` (`gt=0`).
- `revenue_per_kpi_multiplier <= 0` (`gt=0`).
- `cost_multipliers` / `planned_allocation` values `<= 0` (a `0`/negative multiplier makes
  `cpmu → 0` and `media = spend / cpmu` blow up inside Meridian) — enforced via a `field_validator`
  on `FutureBlock`.

**Service-layer `validate_future` → `InvalidOptimizationConfigError` (flat envelope
`{error_code, message, details}`):**
- `start_date` not after the model's last training period.
- `same_period_last_year` without sufficient/aligned history (see §7's cadence-aware year, not a
  hardcoded 365 days).
- `cost_multipliers` / `planned_allocation` referencing an unknown channel (validate keys once
  against the **union** of media + RF channels, then apply per-subset — do **not** check a
  media-keyed multiplier against the RF-only channel list).
- `revenue_per_kpi_multiplier != 1.0` on a model without revenue-per-KPI → ignored (documented
  no-op; the tensor simply isn't built).
- Unsupported spend granularity (e.g. a model with 1-D aggregate `media_spend` lacking a time
  axis) → typed error rather than a crash.

## 13. Testing

- **Unit:** `FutureOptimizationConfig` + `Reference` union validation; future-date-label
  generation and cadence inference; the three reference-window selectors; carry-forward cost /
  flighting / revenue math; cost-multiplier application; `planned_allocation` friendly-normalize
  (partial, unnormalized, unknown channel); budget seeding from reference window; the future
  path of `to_optimize_kwargs`.
- **Contract:** `run_future_optimization` submit envelope + reuse-by-fingerprint + `force_rerun`.
- **Live validation:** extend the end-to-end gate to submit a future run on `national-revenue`
  and `geo-revenue`, poll → result, and assert the columnar result shape; plus adversarial
  cases (unknown channel in `cost_multipliers`, `horizon <= 0`, non-future `start_date`).

### 13.1 Final QA pass — local live run (mandatory gate)

Before the work is declared done, a **manual/scripted end-to-end QA pass** must run the server
**locally** and exercise **both** tools through their full lifecycle (submit → poll
`get_optimization_status` until `completed` → `get_optimization_result`). Execution is
**local tier only** — set `OPTIMIZATION_ALLOWED_TIERS=local` and **do not** use `cloud_cpu` or
`cloud_gpu`. Drive it via a real MCP client (in-process `Client(mcp)`, matching the existing
live-validation harness) against the dummy fitted models (`national-revenue`, `geo-revenue`).

Run **3–5 scenarios per tool**, and each set must include at least one **adversarial** case
whose error envelope is asserted. Representative coverage:

**`run_optimization` (historical):**
1. `fixed_budget`, budget omitted (reallocate historical total), `global ±0.3` — happy path.
2. `fixed_budget` with budget +20% and a `per_channel` constraint that freezes one channel
   (0/0) — happy path.
3. `target_roas` on a revenue model (flexible budget) — happy path.
4. *Adversarial:* `per_channel` constraint missing a channel → `invalid_optimization_config`
   (flat envelope `{error_code, message, details}`).
5. *Adversarial:* unknown geo in `selected_geos` — note this surfaces as a **failed run**
   (`optimization_failed` after polling), not a submit-time envelope; assert accordingly.

**`run_future_optimization`:**
1. `fixed_budget`, `reference=trailing`, `horizon=13`, future `start_date`, default multipliers
   — happy path.
2. `reference=same_period_last_year` + `cost_multipliers` on one channel + a **partial**
   `planned_allocation` (exercises friendly-normalize) — happy path.
3. `target_roas` future with `revenue_per_kpi_multiplier != 1.0` — happy path.
4. `reference=full_history_average` — happy path.
5. *Adversarial:* non-future `start_date`, unknown channel in `cost_multipliers`, and
   `same_period_last_year` with insufficient history → each asserts the flat
   `invalid_optimization_config` envelope. (`horizon <= 0` is rejected earlier by pydantic input
   validation as a protocol-level tool error — assert that separately, not as the envelope.)

The pass must confirm results are well-formed (JSON-safe, expected columnar shape,
`outcome_mode` correct) and that reuse-by-fingerprint and `force_rerun` behave. Record the
outcome (which scenarios ran, pass/fail) as evidence before completion — no success claim
without it.

## 14. Skill update — `skills/meridian-analyst` (mandatory)

The `meridian-analyst` skill must be brought fully in line with the new tool. It currently
contains claims that the new tool **directly contradicts** and that will actively mislead the
LLM if left stale — most critically in `references/budget-optimization.md`:

> "The server does **not** expose Meridian's `new_data` scenario-planning input, so true
> forward scenario planning is a documented limitation of this server. Do not claim or attempt
> it."

### 14.1 Files and changes

- **`references/budget-optimization.md`** — rewrite the **"Forward-looking planning"** section:
  it must now route forward questions to `run_future_optimization` (not the
  historical-window approximation), document the future workflow (submit → poll → result via
  the shared lifecycle), and explain when to use the future tool vs. the historical one.
  Update the routing-table row *"Plan NEXT quarter's budget"* to point at the real tool.
  Retain the honest caveats but **re-scope** them: Meridian still does not forecast demand or
  costs; the future tool optimizes under **user-supplied assumptions** (carry-forward defaults
  + `cost_multipliers` / `revenue_per_kpi_multiplier` / `planned_allocation`). Remove the
  "documented limitation / do not attempt" language.
- **`SKILL.md`** — add `run_future_optimization` to the cardinal-rules/optimization-lifecycle
  description (same async poll→result flow; same run-id tools). Note that the five run-id tools
  serve both historical and future runs.
- **`references/taxonomy.md` / `references/glossary.md`** — add the new concepts an LLM will
  need to route and interpret correctly: **reference window** (trailing / same-period-last-year
  / full-history-average), **cost multiplier** (cost-per-media-unit scaling), **flighting**,
  **planned allocation** (`pct_of_spend` center), and the allocation-axis vs cost-structure-axis
  distinction from §7.
- **`references/consultation.md`** (new) — the consultative guidance layer defined in §14.4.
  Linked from `SKILL.md` as a cardinal rule and referenced from `budget-optimization.md`.

### 14.2 Tool description & input documentation (best practice)

Follow the same standard as the existing tools (the quality bar set by `run_optimization` /
`get_spend_scenario`): the `run_future_optimization` tool docstring and every `config` field's
`Field(description=...)` must be **self-contained and unambiguous for an LLM caller** —

- one-line "what business question this answers" opener, then the async poll→result contract;
- **every** input documented with type, default, units (periods vs dates), valid range, and
  where to source valid channel/geo names (`get_model_overview.available_tool_options`);
- the honesty guardrail from §5 stated in the description (optimizes under assumptions; not a
  demand forecast);
- examples on the non-obvious fields (`cost_multipliers`, `reference`, `planned_allocation`),
  matching the example style already used in `domain/optimization.py`.

### 14.3 Mandated writer → reviewer critique loop

The skill update must be produced through an explicit **writer → reviewer critique loop**, run
for **at least two full loops** (more if the reviewer still finds issues):

1. **Writer** drafts/updates the skill files.
2. **Reviewer** critiques the draft against a rubric and returns concrete, actionable findings.
3. Writer revises; repeat until the reviewer signs off *and* at least two loops have completed.

Reviewer rubric (each finding must cite the file/section):
- **Accuracy** — every statement matches the tool as actually implemented (no stale "not
  supported" claims; field names, defaults, and presets match the code).
- **Input completeness** — every `config` field is documented with type, default, units,
  range, and channel/geo sourcing; nothing undocumented or ambiguous.
- **LLM-routing clarity** — the routing table and cardinal rules make it unmistakable *when* to
  use `run_future_optimization` vs `run_optimization`, and how to read the result.
- **Caveat correctness** — forward caveats reflect the new reality (assumptions, not forecast;
  extrapolation risk; validate with experiment) without over- or under-claiming.
- **Consistency & voice** — matches the existing skill's structure, terminology, and altitude;
  cross-references (`taxonomy.md`, `glossary.md`, `consultation.md`) are correct.
- **Consultative layer** — the §14.4 guidance is present, calibrated (not an interrogation),
  translates jargon to business language, and the plain-language→tool-field mappings are
  correct.

The loop is part of the implementation deliverable, not optional polish. The implementation plan
must schedule these loops as explicit steps.

## 14.4 Consultative guidance layer (the skill acts as a consultant)

Most callers are marketers/CMOs who ask **high-level, vague questions** ("optimize my Q4
budget", "where should I put more money?") and do not know the tool's fields or options. The
skill must make the LLM **walk them through the decision**, not run on silent defaults. Core
premise: **do not assume — clarify** — but calibrated so it still feels like an "easy button".

### Interaction model — hybrid (ask gaps → propose → confirm)

For a vague ask, the LLM:
1. **Elicits only the genuinely-unknowable gaps first** — the few things it cannot responsibly
   guess (the goal, whether budget is held/added/cut and by how much, hard constraints like
   contracts/freezes, the future period for forward planning). Ask these **adaptively, 2–4
   related questions per round**, in **plain business language** (never jargon like `cpmu`,
   `flighting`, `pct_of_spend`).
2. **Proposes a concrete plan of action** — including **how many runs/scenarios** to run and
   why (e.g. a `fixed_budget` baseline + a `target_roas` variant + a future scenario), each
   described in business terms, with **every remaining assumption named explicitly**
   (reference window, carried-forward costs, constraint band, window/geos).
3. **Confirmation gate, calibrated to ambiguity/stakes** — when inputs are still ambiguous or
   the move is large/high-stakes, get explicit go-ahead before running; when the user already
   gave concrete inputs and the move is low-stakes, a brief plan restatement is enough and the
   LLM may proceed. Never run on an **unstated** assumption.

### Scope

Applies to **all decision-heavy flows**: `run_optimization`, `run_future_optimization`,
`get_spend_scenario`, target-setting, and multi-run comparisons — one consistent consultative
pattern across the skill.

### Per-flow "minimum to elicit" checklists

`consultation.md` provides, per flow, the minimum a plan needs before running — each item is
something the LLM must either learn from the user or **name as an assumption to confirm**:

- **Any optimization** — goal (grow / hit target / cut); budget change (hold / add / cut + how
  much); hard constraints (contracts, freezes, max movement); window & geos; risk appetite
  (tight vs loose constraint band).
- **Future optimization additionally** — future period (start + horizon); expected cost changes
  (→ `cost_multipliers`); expected price/LTV changes (→ `revenue_per_kpi_multiplier`); planned
  mix if any (→ `planned_allocation`); which reference window best represents the future
  (recent trend vs same-season-last-year vs stable average).

### Plain-language → tool-field translation

`consultation.md` includes a translation table so the LLM maps business answers to fields
without exposing internals, e.g.: "grow as efficiently as possible with $2M" → `fixed_budget`,
`budget=2_000_000`; "hit 3× ROAS" → `target_roas 3.0`; "TV CPMs up ~15%" → `cost_multipliers
{TV: 1.15}`; "we're locked into our search contract" → `per_channel` freeze on search;
"holidays are bigger this year, plan like last December" → future tool, `same_period_last_year`
reference.

### Anti-patterns (call out explicitly)

- Dumping internal jargon on the user instead of translating it.
- Asking for things `get_model_overview` already answers.
- Running high-stakes/ambiguous requests on silent defaults.
- Over-interrogating a user who already gave concrete specifics.

The reviewer rubric (§14.3) checks this layer is present and correctly calibrated.

## 15. Out of scope (deferred, non-breaking to add later)

- `Reference` `custom` window (arbitrary reference dates + flighting resampling).
- Multi-period / rolling plans and cross-quarter schedules.
- Any actual demand/cost forecasting — explicitly not Meridian's model.

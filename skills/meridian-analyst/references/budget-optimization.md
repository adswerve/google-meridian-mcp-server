# Budget optimization

The playbook for every "how should I spend / reallocate / hit a target" question.
Optimization runs on the **budget-optimization module**: it is asynchronous and it
reallocates spend across the model's channels to improve the objective the model
supports (ROAS for revenue-capable models, CPIK for KPI-only — see
`taxonomy.md`). This file routes the question, runs the lifecycle, reads
the result, and handles forward-looking planning. It does **not** restate
`run_optimization`'s input schema — call the tool for field types, defaults, and
validation; the names below are for routing and interpretation only.

Before anything here, complete the golden path (`list_models` →
`get_model_overview`) and read `available_tool_options`: it lists the legal
channels and geos for this model. Never optimize over a channel or geo it does not
list.

## Scenario library — route the question

`run_optimization` takes one **scenario** (the objective) and one **constraint**
(how far each channel may move), plus optional window/geo knobs. Map the business
question to them. The `constraint` is optional — if the user gives no movement
limit, omit it and the tool applies a sensible default band; only set one when
they state a real limit.

| User question | Routing |
| --- | --- |
| "How should I allocate my whole budget?" | `run_optimization`, `fixed_budget` scenario, **omit** the budget (defaults to current historical total over the window) → poll → result |
| "What if I add N% more budget — where does it go?" | `fixed_budget` with `budget` raised to the new total; read `spend_delta` / compare `channel_tables.initial` vs `optimized` |
| "N% budget cut — where do I cut with least damage?" | `fixed_budget` with `budget` lowered; `spend_delta` shows where the model pulls money out first |
| "Shift $X from channel A → B — predicted impact?" | **No single tool.** Two `get_spend_scenario` calls (A down, B up) or a `per_channel` constraint; compare marginal outcomes — see teaching point 1 |
| "Hit / maintain a target ROAS" | `target_roas` scenario (on a KPI-only model this is read as a CPIK target automatically) |
| "Max spend per channel while holding a minimum marginal-ROI hurdle" | `target_mroas` scenario |
| "Which channels are saturated / over-invested?" | `get_response_curves` **and** marginal ROI together — see teaching point 2 |
| "Which channels have headroom / are under-invested?" | Same reasoning: high marginal ROI + still-rising curve → push more |
| "Don't move any channel more than ±X% / freeze channel Z" | `constraint`: `global` (one pct band on every channel) vs. `per_channel` (explicit lower/upper bounds; freeze = set both to 0) |
| "Optimize just for Q4 / a specific window" | `start_date` / `end_date` |
| "Reallocate within specific regions only" | `selected_geos` (geo models only; ignored by national) |
| "Plan NEXT quarter's budget (forward-looking)" | `run_future_optimization` — see the "Forward-looking planning" section; **always** attach its caveats |

The valid **scenario** types are exactly `fixed_budget`, `target_roas`,
`target_mroas`. The valid **constraint** modes are exactly `global` and
`per_channel`. Do not invent others.

## Two teaching points an LLM gets wrong

**1. "Shift $X from A → B" has no dedicated tool.** There is no "move budget
between channels" tool. Express it one of two ways:
- **Two `get_spend_scenario` calls:** simulate A at a *lower* base spend and B at a
  *higher* spend, then compare the marginal ROI you *lose* on A against the
  marginal ROI you *gain* on B. If B's marginal ROI at the added dollars exceeds
  A's marginal ROI at the dollars removed, the shift is net-positive; otherwise it
  destroys outcome. (`get_spend_scenario` is a single-channel what-if; deeper
  single-channel analysis lives in `channel-performance.md`.)
- **A `per_channel` constraint in `run_optimization`** that lets only A and B move
  (pin the rest) and read the outcome delta. Prefer this when you want the module
  to find the best split rather than testing one fixed amount.

**2. Saturation / headroom is a reasoning step, not a tool call.** No tool returns
"saturated: true". You derive it by reading **two** things together:
- `get_response_curves` — is the channel's curve still climbing or has it flattened
  (see "saturation" in `glossary.md`)?
- **Marginal ROI** (`mroi` in the result, or `marginal_roi` from analysis) — a
  channel whose marginal ROI has dropped below your target/hurdle is **past the
  plateau → over-invested**; a channel with high marginal ROI and a still-rising
  curve has **headroom → under-invested**. Average ROI alone does not tell you
  this; a channel can have great average ROI and zero headroom.

## The optimization lifecycle

Optimization is asynchronous (see the cardinal rules in `SKILL.md`). Never treat
the submit call as the answer.

1. **Submit** — `run_optimization` returns a `run_id` and a reuse flag, not
   results. If an identical `(model_id, config)` was optimized before, the module
   returns that prior run instead of recomputing (config-fingerprint reuse) — a
   reused run may already be `completed`.
2. **Poll** — `get_optimization_status` until the status is terminal. Statuses are
   exactly: `queued`, `running` (both in-flight — keep polling), then one of
   `completed`, `failed`, `canceled` (terminal — stop). While in-flight the status
   may also carry a coarse phase and progress fraction for feedback; on `failed`
   it carries an error payload to report. First runs can sit in `queued` for a
   while during cold start — that is not a hang.
3. **Result** — only when `completed`, call `get_optimization_result`. On `failed`,
   surface the error; on `canceled`, report that it was stopped and do not invent
   numbers.

**Managing runs:** `list_optimizations` lists prior/in-flight runs for a model;
`cancel_optimization` stops a `queued`/`running` run; `delete_optimization` removes
a stored run. Use these to find a reusable result or clean up.

## Reading the result

`get_optimization_result` returns point-estimate fields (the module reports means,
not credible intervals — corroborate a close call against the channel-level
credible intervals from the analysis tools). The key fields:

- **`outcome_mode`** — `revenue` or `kpi`. This tells you how to read every
  efficiency number: `revenue` → efficiency is ROAS (**higher is better**); `kpi`
  → efficiency is CPIK (**lower is better**). Read it first; misreading direction
  inverts the whole recommendation.
- **`summary`** — the headline before/after: non-optimized vs optimized total
  budget, total incremental outcome, and total efficiency. For a `fixed_budget`
  run the budget is unchanged, so the story is "same money, more incremental
  outcome / better efficiency."
- **`channel_tables`** — `initial` and `optimized` rows per channel (spend,
  pct_of_spend, incremental_outcome, roi, mroi, cpik, effectiveness). Diff the two
  to explain *why* the plan moves money.
- **`allocation`** — the recommended optimized spend per channel (the answer to
  "where should the money go").
- **`spend_delta`** — per-channel change, cuts first then increases. This is your
  "take $ out of these, put it into those" narrative.
- **`response_curves`** (when present) — per-channel (spend, incremental_outcome)
  points; use them to show headroom vs. saturation behind the allocation.
- **`assumptions`** (future runs only) — the plan's own assumptions echoed back:
  `budget`, `budget_source` (`explicit` / `derived_from_reference` /
  `determined_by_target`), `reference_mode`, and `excluded_channels`. State these to
  the user verbatim — this is the authoritative record of what the plan assumed,
  including the auto-derived budget when none was given.

**Optimized vs. non-optimized:** the value of the run is the *difference*. Lead
with what changed (`spend_delta`) and the summary lift, not the raw optimized
totals. If optimized and non-optimized are nearly identical, the current plan is
already near-optimal under the constraint — say so rather than manufacturing a
change.

## Interrogate the result like a marketer

The allocation is the start of the conversation, not the end. Before presenting it,
check for the higher-value findings a raw reallocation hides:

- **Every channel's marginal ROI below ~1.0x → the lever is total budget, not the
  mix.** At that point the last dollar in *every* channel returns less than a
  dollar; a `fixed_budget` reallocation cannot fix over-investment. Offer a
  `target_mroas` run (or a lower-budget test) instead.
- **Don't freeze your best channel.** If a frozen or tightly-bound channel has the
  highest ROI/mROI, flag that the constraint is capping your upside — freezing the
  most efficient channel is usually backwards.
- **A low-ROI channel pinned to its lower bound** means the band is protecting weak
  spend; the model wanted to cut further. Offer a looser-band or unconstrained
  scenario to show the true optimum before deciding what's operationally feasible.
- **ROAS is revenue, not profit.** Remind the user to apply contribution margin
  before acting; a ~1.0x ROAS plan can be unprofitable.

## Choosing scenario and constraint (interpretation)

- **`fixed_budget`** — total budget is held (or set explicitly) and the module
  finds the best split. Default for allocation/reallocation and add/cut questions.
- **`target_roas`** — find the allocation that reaches a target overall ROAS. On a
  KPI-only model the same knob is interpreted as a CPIK target.
- **`target_mroas`** — spend up to the point where the *next* dollar still clears a
  marginal-ROI hurdle; use it for "how much can each channel absorb before it stops
  paying off."

Both `target_*` scenarios are **flexible-budget**: to hit the target the module may
recommend spending *more or less overall*, so total spend can change — unlike
`fixed_budget`, which holds the total. The objective family follows the model's
revenue capability (`taxonomy.md`); a `use_kpi` knob can force
revenue-vs-KPI objective, but only override it with a clear reason.
- **`global` constraint** — one symmetric band (e.g. ±20%) on every channel; the
  simple default for "keep the plan realistic."
- **`per_channel` constraint** — explicit lower/upper bounds per channel; use it to
  freeze a channel (bounds 0/0), protect a contracted channel, or allow only some
  channels to move. It must cover every paid/RF channel — read the legal channel
  list from `available_tool_options`.

Tighter constraints yield smaller, safer moves; loose constraints yield larger,
higher-variance moves that lean harder on the model being right out toward the
edges of historical spend.

## Forward-looking planning (plan NEXT period's budget)

Be honest with the user first: **Meridian does not forecast demand, costs, or
prices.** There is no crystal ball here — `run_future_optimization` optimizes an
allocation for a future window **under assumptions the user (or you, on their
behalf) supplies**; it does not predict what those assumptions should be. For
vague or high-stakes forward asks, elicit the assumptions first — see
`consultation.md`.

Use **`run_future_optimization`**, not `run_optimization`, whenever the question
is about a period **after** the model's last training date ("next quarter",
"next year's holiday season", "if TV gets more expensive"). It shares the exact
same scenario/constraint vocabulary and async lifecycle as `run_optimization`
(submit → poll `get_optimization_status` → `get_optimization_result` — see "The
optimization lifecycle" above), plus one additional required block: `future`.

**When to use which tool:**
- **`run_optimization`** — reallocate spend the model already observed (historical
  window, defaults to full training range). Use for "how should I have spent" /
  "how should I reallocate now within data I have."
- **`run_future_optimization`** — allocate spend for a window **beyond** the
  model's training data. Use for "how should I split next quarter's budget" /
  "plan for a future period."

**The `future` block, in plain terms:**
- **`start_date`** (date, required) — first day of the future period; must be
  after the model's last training date.
- **`horizon`** (integer periods, required, must be a positive integer `> 0`) —
  how many periods to plan, at the model's own cadence (e.g. `13` = 13 weeks on
  a weekly model, not 13 days).
- **`reference`** (optional, default `trailing`; one of three modes — see
  `glossary.md` "reference window" and `taxonomy.md` for routing detail) — which
  historical window's cost-per-media-unit, flighting (time-shape of spend), and
  revenue-per-KPI get carried forward as the future's cost structure:
  - `trailing` (default) — the last `horizon` periods of the training data,
    ending at the model's last training period (**not** wall-clock today — a
    model trained through 2025-06 anchors here regardless of the current
    date). Best for "keep recent conditions."
  - `same_period_last_year` — the `horizon` periods exactly one year before
    `start_date`. Best for seasonal planning ("plan like last December").
  - `full_history_average` — the average over the whole training range. Best
    when recent data is noisy or unrepresentative.
- **`cost_multipliers`** (optional `dict[channel, float]`, default 1.0 per
  channel, **values must be `> 0`**) — scales the carried-forward
  cost-per-media-unit per channel; `1.15` = 15% more expensive, `0.9` = 10%
  cheaper. Example: expecting TV CPMs to rise 15% → `{"TV": 1.15}`. A
  multiplier above 1 makes a channel less efficient, so the optimizer shifts
  spend away from it; below 1 does the opposite. A multiplier of `0` is
  **rejected** by validation, not treated as "free" — there is no way to zero
  out a channel's cost this way.
- **`revenue_per_kpi_multiplier`** (optional float, default `1.0`, **must be
  `> 0`**) — scales carried-forward revenue-per-KPI (revenue-capable models
  only; ignored on KPI-only models). `1.1` = prices/LTV expected 10% higher.
- **`planned_allocation`** (optional `dict[channel, float]`, default: the
  carried-forward mix, **values must be `> 0`**) — your intended future spend
  mix; it is the center that spend constraints bound around, and shows up as
  the "current"/baseline mix in the result (comparable to `channel_tables.initial`
  in a historical run). Partial dicts are accepted — unlisted channels are
  filled from the carried-forward mix and the whole vector is renormalized to
  sum to 1.
- **`excluded_channels`** (optional `list[str]`, default none) — channels to
  fully pause for the future window; their spend is forced to 0 (they still
  appear in the result with spend 0) and their share of the budget is
  reallocated across the remaining channels (total unchanged — "pause TV, spend
  it elsewhere"). Must be valid paid/RF channels and must **not** also appear in
  `planned_allocation` or `cost_multipliers` (both reject `0`); a `0/0`
  `per_channel` constraint **freezes** a channel at its current spend instead of
  pausing it, so none of those three fields can substitute for this one. Only
  future runs support full exclusion — historical `run_optimization` supports
  freeze (`0/0`) but not exclusion. You cannot exclude every channel. Example:
  expecting to go dark on TV next quarter → `{"excluded_channels": ["TV"]}`.
- **Omitted `budget` in a `fixed_budget` scenario, for a future run** — unlike
  `run_optimization` (where an omitted budget defaults to the model's full
  historical total), here it defaults to the chosen `reference` window's
  carried-forward spend total, seeded/scaled to the `horizon` length. Different
  `reference` modes therefore imply materially different default budgets (e.g.
  `same_period_last_year` seeds from last year's same-quarter spend total,
  `full_history_average` seeds from a horizon-scaled long-run average) — if the
  user did not give an explicit number, say out loud what total you are
  assuming and why.

**Dark-channel preflight.** Before a future run, check for channels with no recent
spend (`get_channel_data`). A channel that is dark over the chosen `reference`
window has no cost-per-unit basis: either pick a window where it had spend, or
exclude it via `excluded_channels` above, which seeds its cost correctly for the
window. Whether a dark channel is retired for good or coming back is a business
decision — confirm it with the user rather than silently dropping it.

**Future runs omit `response_curves`.** Unlike `run_optimization`, a
`run_future_optimization` result never includes `response_curves` — Meridian
cannot recompute response curves under the future run's own cost/flighting
assumptions, so lean on `channel_tables`/`allocation`/`summary` for the plan.

Everything else — `scenario` (`fixed_budget`/`target_roas`/`target_mroas`),
`constraint` (`global`/`per_channel`), `selected_geos`, `use_kpi` — works the
same way as in `run_optimization` (see the scenario library above), **with one
exception**: in a `fixed_budget` scenario, an omitted `budget` does **not**
default to the model's full historical total the way it does in
`run_optimization` — see the bullet above for what it defaults to instead.
Channel and geo names still come from
`get_model_overview.available_tool_options.run_optimization`.

**Mandated caveats — attach ALL of these to every forward recommendation:**
- Meridian is **not forecasting** cost, price, demand, or seasonality — every
  number in the `future` block (or its defaults) is a supplied assumption, not a
  prediction. The optimizer is only as good as those assumptions.
- **State which `reference` window was used and why** — it silently determines
  the carried-forward cost/flighting/revenue-per-KPI *and*, whenever a
  `fixed_budget` scenario's `budget` is left unset, the assumed total budget
  for the plan too. So the user should know whether the plan assumes "recent
  conditions," "same season last year," or "a stable long-run average" — don't
  reconstruct the reference window or the implied budget by hand; read the
  result's `assumptions` field (`reference_mode`, `budget`, `budget_source`) and
  state it verbatim, including the auto-derived budget when none was given.
- **Extrapolation risk:** pushing a channel beyond its historical spend range (or
  applying a large `cost_multipliers`/`revenue_per_kpi_multiplier` shift) is the
  least reliable part of the curve — treat large moves skeptically.
- The numbers are **incremental outcome under the assumed cost structure**, not
  the absolute future KPI/revenue — baseline demand and macro price shifts are
  not modeled.
- **Validate large moves with a geo or holdout experiment** before committing real
  budget; the model informs the hypothesis, the experiment confirms it.

Never present a forward plan as a certainty, and never omit these caveats.

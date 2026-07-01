# Budget optimization

The playbook for every "how should I spend / reallocate / hit a target" question.
Optimization runs on the **budget-optimization module**: it is asynchronous and it
reallocates spend across the model's channels to improve the objective the model
supports (ROAS for revenue-capable models, CPIK for KPI-only — see
`references/taxonomy.md`). This file routes the question, runs the lifecycle, reads
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
question to them:

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
| "Plan NEXT quarter's budget (forward-looking)" | Forward-planning approximation — see the final section; **always** attach its caveats |

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
  single-channel analysis lives in `references/channel-performance.md`.)
- **A `per_channel` constraint in `run_optimization`** that lets only A and B move
  (pin the rest) and read the outcome delta. Prefer this when you want the module
  to find the best split rather than testing one fixed amount.

**2. Saturation / headroom is a reasoning step, not a tool call.** No tool returns
"saturated: true". You derive it by reading **two** things together:
- `get_response_curves` — is the channel's curve still climbing or has it flattened
  (see "saturation" in `references/glossary.md`)?
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

**Optimized vs. non-optimized:** the value of the run is the *difference*. Lead
with what changed (`spend_delta`) and the summary lift, not the raw optimized
totals. If optimized and non-optimized are nearly identical, the current plan is
already near-optimal under the constraint — say so rather than manufacturing a
change.

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
revenue capability (`references/taxonomy.md`); a `use_kpi` knob can force
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

Be honest with the user first: **MMM optimization is backward-looking.** It fits on
historical data and recommends the best *reallocation of past spend* — it is not a
forecast of the future. The server does **not** expose Meridian's `new_data`
scenario-planning input, so true forward scenario planning is a documented
limitation of this server. Do not claim or attempt it.

You can still give a defensible forward approximation using only the existing
knobs:

1. Set `budget` (a `fixed_budget` scenario) to the **planned future total**.
2. Set `start_date` / `end_date` to a **recent window that resembles expected
   future conditions** — e.g. optimize over last year's Q4 to inform this year's
   Q4, so seasonality and price environment are comparable.
3. Apply the **real planning constraints** (contracts, channel freezes, max
   movement) via `global` / `per_channel`.
4. Read the plan through **marginal ROI (`mroi`)**, not average ROI: the forward
   question is "where does the *next* dollar work hardest," and marginal ROI is
   what survives extrapolation best.

**Mandated caveats — attach ALL of these to every forward recommendation:**
- It assumes CPMs, prices/LTV, and the response-curve shapes stay stable into the
  planned period; if any shift, the plan degrades.
- **Extrapolation risk:** pushing a channel beyond its historical spend range is
  the least reliable part of the curve — treat large increases skeptically.
- The numbers forecast **incremental outcome** (the lift media causes), **not the
  absolute future KPI/revenue** — baseline demand, seasonality, and price are not
  being predicted here.
- **Validate large moves with a geo or holdout experiment** before committing real
  budget; the model informs the hypothesis, the experiment confirms it.

Never present a forward plan as a certainty, and never omit these caveats.

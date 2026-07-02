# Consultation (asking before running)

How to handle a vague or high-level ask — "help me plan next quarter's budget",
"where should we spend more", "is our TV spend working" — before reaching for a
tool. This is the layer that sits **in front of** `run_optimization`,
`run_future_optimization`, `get_spend_scenario`, target-setting, and multi-run
comparisons. One pattern covers all of them.

## Scope

Applies to every decision-heavy flow on this server:
- `run_optimization` and `run_future_optimization` (any scenario/constraint)
- `get_spend_scenario` what-ifs
- Setting a target ROAS/mROAS/CPIK
- Comparing multiple runs/scenarios against each other

It does **not** apply to pure read-only analysis (channel ROI, contribution,
response curves, model fit) — those answer questions directly and carry no
"unstated assumption" risk; run them without a consultative pass.

## Interaction model — hybrid: ask gaps → propose → confirm

**1. Elicit only genuinely-unknowable gaps.** You cannot infer these from the
model — they are business facts only the user has:
- The **goal** (grow the outcome, hit a specific target, cut cost, defend
  current performance).
- Whether budget is being **held, added, or cut** — and by roughly how much.
- **Hard constraints** — contracts, spend freezes, a channel that cannot move, a
  maximum allowed movement.
- For forward planning, the **future period** — when it starts and how long it
  runs.

Ask adaptively, **2–4 related questions per round**, in plain business language.
Never surface internal field names or jargon in a question — no `cpmu`,
`flighting`, `pct_of_spend`, `mode`, `reference`. Ask "is next quarter's TV
buying expected to get more or less expensive?", not "what `cost_multipliers`
should I use for TV?".

**2. Propose a concrete plan.** Once the genuinely-unknowable gaps are answered
(or the user has clearly already stated them), propose the runs you intend to
make — in business terms, not tool-call terms:
- How many runs/scenarios, and why each one earns its place (e.g. "I'll run a
  baseline that holds your $2M budget and reallocates it, plus a second run
  that targets 3x ROAS so you can compare the two shapes").
- For forward planning, name it as a distinct future scenario, not a variant of
  the historical baseline.
- **Name every remaining assumption explicitly** — the reference window
  ("I'll use last year's same quarter as the cost baseline since you mentioned
  seasonality"), the constraint band ("±20% per channel, so no channel swings
  wildly"), the window/geos being optimized. An assumption that is not stated
  out loud is an assumption the user cannot correct.

**3. Confirmation gate — calibrated to ambiguity and stakes.**
- **Ambiguous or high-stakes** (large budget, a forward/future plan, a request
  that could be read multiple ways) → get explicit go-ahead on the proposed plan
  before running anything.
- **Concrete and low-stakes** (the user already gave specific numbers and a
  clear scenario) → a brief one-line restatement of the plan is enough; proceed
  without waiting for a separate "yes."
- **Never** run a decision-heavy tool on an assumption that was not stated to
  the user first, regardless of stakes. Silent defaults are for genuinely
  inconsequential knobs (e.g. reusing a prior identical run) — not for the
  reference window, the budget change, or hard constraints.

## Minimum to elicit, per flow

Each item below must either be **learned from the user** or **named out loud as
an assumption you are making**, before the run.

**Any optimization (`run_optimization` or `run_future_optimization`):**
- Goal: grow / hit a target / cut cost.
- Budget change: hold / add / cut, and roughly how much.
- Hard constraints: contracts, freezes, channels that cannot move.
- Window and geos being optimized.
- Risk appetite: tight constraint band (small, safe moves) vs. loose (larger,
  higher-variance moves).

**Future optimization, additionally:**
- The future period: when it starts and how long it runs.
- Expected cost changes → `cost_multipliers`.
- Expected price/LTV changes → `revenue_per_kpi_multiplier`.
- A planned spend mix, if the user already has one in mind → `planned_allocation`.
- Which historical window best represents the future: a recent trend, the same
  season last year, or a stable long-run average → `reference`.
- Any channel to **pause entirely** next period → `excluded_channels`.

## Plain language → tool-field translation

| What the user says | What you set |
| --- | --- |
| "Grow as efficiently as possible with $2M" | `scenario: {type: "fixed_budget", budget: 2_000_000}` |
| "Reallocate what we're already spending, don't change the total" | `scenario: {type: "fixed_budget"}` (omit `budget`) |
| "Hit a 3x return on spend" | `scenario: {type: "target_roas", target_value: 3.0}` |
| "Keep pushing spend as long as the next dollar still returns at least 1.5x" | `scenario: {type: "target_mroas", target_value: 1.5}` |
| "Don't let any channel move more than 20%" | `constraint: {mode: "global", pct: 0.2}` |
| "We're locked into our search contract, it can't change" | `constraint: {mode: "per_channel", bounds: {Search: {lower_pct: 0, upper_pct: 0}, ...}}` |
| "Pause / stop channel X entirely next quarter" | `run_future_optimization`, `future.excluded_channels: ["X"]`. X's spend is forced to 0 and its budget is spent across the remaining channels ("pause TV, spend it elsewhere") — total budget unchanged. Do **not** use `planned_allocation`/`cost_multipliers` (reject `0`) or `0/0` constraint bounds (those **freeze X at its current spend**, they do not pause it). Historical `run_optimization` cannot fully exclude a channel — only future runs can. |
| "Just look at our California and New York markets" | `selected_geos: ["US-CA", "US-NY"]` |
| "Plan next quarter starting October 1st for 13 weeks" | `run_future_optimization`, `future.start_date: "2026-10-01"`, `future.horizon: 13` |
| "TV CPMs are expected to be up about 15%" | `future.cost_multipliers: {"TV": 1.15}` |
| "We think our average order value will be 10% higher" | `future.revenue_per_kpi_multiplier: 1.1` |
| "Holidays are bigger this year — plan it like last December" | `run_future_optimization`, `future.reference: {mode: "same_period_last_year"}` |
| "Just use whatever we've been spending most recently as the baseline" | `future.reference: {mode: "trailing"}` (the default) |
| "Our recent data is noisy — use a normal, stable baseline" | `future.reference: {mode: "full_history_average"}` |
| "We're planning to put 40% into TV and 35% into Search" | `future.planned_allocation: {"TV": 0.4, "Search": 0.35}` |

## Anti-patterns

- **Dumping internal jargon on the user** instead of translating — asking "what
  `reference` mode do you want?" rather than "should I plan off recent spend, the
  same period last year, or a long-run average?"
- **Asking for facts `get_model_overview` already answers** — channel/geo names,
  whether the model is revenue-capable, the training date range. Look these up;
  don't make the user repeat them.
- **Running a high-stakes or ambiguous request on silent defaults** — e.g.
  launching a future optimization with the default `trailing` reference without
  ever telling the user that's what determines the assumed cost structure.
- **Over-interrogating a user who already gave concrete specifics** — if they
  said "optimize my $2M Q4 budget, freeze Search, target 3x ROAS," you have goal,
  budget, constraint, and window already; restate the plan in one line and go,
  don't re-ask for things already given.
- **Confusing freeze with exclude.** "Pause channel X entirely" is
  `future.excluded_channels: ["X"]` in a **future** run — not
  `planned_allocation: {"X": 0}`/`cost_multipliers: {"X": 0}` (both rejected,
  must be `> 0`) and not a `0/0` `per_channel` constraint (that **freezes X at
  its current spend** — the opposite of pausing). Historical runs cannot fully
  exclude a channel; if the user needs that, plan it as a future run.

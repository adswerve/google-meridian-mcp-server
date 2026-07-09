# Glossary

Marketer-facing definitions for the terms used across this skill. For which
metrics are valid on which model, see `taxonomy.md`.

**ROAS / ROI (`roi`)** — Return on ad spend: incremental revenue driven per unit
of spend (3.0 = $3 of revenue per $1 spent). Higher is better. Defined only for
revenue-capable models. This server uses ROAS and ROI interchangeably.

**Marginal ROI / mROAS (`marginal_roi`)** — The ROI of the *next* dollar on a
channel, not its average ROI so far. Because of diminishing returns, mROI is
usually below average ROI; it is what tells you where added budget works hardest.

**CPIK** — Cost Per Incremental KPI: spend divided by the extra KPI units it
caused (e.g. cost per incremental conversion). It is the inverse of ROI, so here
**lower is better**. CPIK is the efficiency metric for KPI-only models and is
valid on every model (`cpik`/`marginal_cpik`).

**Contribution vs. response curve** — Contribution is the outcome a channel
actually drove at its historical spend (a single point or share). A response
curve is the modeled outcome across a *range* of spend levels — it shows how
outcome would move if you spent more or less, which contribution alone cannot.

**Adstock / carryover** — Advertising's effect persists after the exposure:
today's spend keeps driving outcome in later periods, decaying over time.
`get_adstock_decay` shows how fast a channel's effect fades.

**Saturation / diminishing returns** — Each extra dollar on a channel returns
less than the last as the channel saturates, so response curves bend and flatten.
This is why marginal ROI falls as spend rises and why reallocation usually beats
piling more budget onto one channel.

**Reach & frequency (RF)** — Reach is how many distinct people saw an ad;
frequency is how many times each did. RF channels are modeled on exposure rather
than raw spend; `get_reach_frequency` (RF models only) shows ROI across frequency
levels to find an efficient frequency.

**Base vs. incremental** — Base (baseline) outcome is what would have happened
with no paid media — organic demand, seasonality, price. Incremental outcome is
the lift the media actually caused. MMM credits channels only for the incremental
part; the base is not attributable to any channel.

**Credible interval (`ci_lo`/`ci_hi`)** — Meridian is Bayesian, so every estimate
is a distribution, not a single number. The credible interval is the plausible
range for the true value; a wide interval means high uncertainty. Always report
it with the mean, and never present the mean as exact.

**Reference window (`reference`, in `run_future_optimization`)** — The historical
window whose cost-per-media-unit, flighting, and revenue-per-KPI are carried
forward as the future period's cost structure. All three modes resolve against
the model's **training data** — none of them have any notion of wall-clock
"today": `trailing` (the last `horizon` periods of the training data, ending at
the model's last training period — "keep recent conditions"; a model trained
through 2025-06 anchors here regardless of the current date), `same_period_last_year`
(the `horizon` periods exactly one year before `start_date` — "plan like the same
season last year"), and `full_history_average` (the average over all training
periods — "use a stable long-run baseline"). Routing detail: `taxonomy.md`.

**Cost multiplier (`cost_multipliers`)** — A per-channel scaling factor applied to
the reference window's carried-forward cost-per-media-unit in a future
optimization. `1.15` = 15% more expensive; below `1.0` = cheaper. Raising a
channel's cost multiplier makes it less efficient, so the optimizer shifts budget
away from it; lowering it does the opposite. Channels omitted default to `1.0`.

**Flighting** — The time-shape of spend across periods within a window (e.g. spend
weighted toward the last two weeks of a quarter rather than spread evenly). In
`run_future_optimization`, flighting is one of the things carried forward from the
chosen reference window — it is not something you set directly.

**Planned allocation (`planned_allocation`)** — Your intended future spend mix,
supplied to `run_future_optimization`. It is the center that spend constraints
bound around, and it appears as the "current"/baseline mix in the result (the
future-run counterpart of `channel_tables.initial`). Unlisted channels are filled
from the carried-forward mix and the vector is renormalized to sum to 1.

**Excluded channels (`excluded_channels`)** — A list of channels to fully pause
in a future optimization: their spend is forced to 0 and reallocated across the
remaining channels (total budget unchanged). This is the *only* way to zero a
channel — a `0/0` spend constraint freezes a channel at its current spend, and
`cost_multipliers`/`planned_allocation` reject 0. Future runs only.

**Assumptions (`assumptions`, in a future optimization result)** — the plan's own
inputs echoed back by `get_optimization_result`: `budget` (the total spend
assumed), `budget_source` — how that budget was arrived at (`explicit`: the user
gave a number; `derived_from_reference`: carried forward from the `reference`
window's spend, scaled to the horizon; `determined_by_target`: implied by a
`target_roas`/`target_mroas` scenario rather than a fixed number) —
`reference_mode` (which reference window was used), and `excluded_channels`
(which channels were paused). This is the authoritative record of what a future
plan assumed — state it verbatim rather than reconstructing the reference window
or implied budget by hand. Future runs only. Full field reference:
`budget-optimization.md`.

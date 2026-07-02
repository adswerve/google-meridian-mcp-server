# Channel performance & diagnostics

The playbook for questions about **one channel or a comparison across channels** —
ROI/efficiency ranking, contribution, carryover, saturation, reach & frequency,
single-channel spend what-ifs, and whether the model can be trusted. For
**whole-budget allocation/reallocation** ("how should I split my budget", target
ROAS/mROAS), stop and use `references/budget-optimization.md` instead — that is a
different (asynchronous) tool.

Before anything here, complete the golden path (`list_models` →
`get_model_overview`) and read `available_tool_options`: it is authoritative over
this file for which tools/metrics are legal on **this** model. If a tool or metric
is not listed there, do not call it. Revenue-vs-KPI and RF gating are summarized in
context below; the full matrix is `references/taxonomy.md`. Terms (ROAS, mROI,
CPIK, adstock, contribution, saturation, base vs. incremental) are defined in
`references/glossary.md` — this file interprets results, it does not re-teach them.

These tools are **read-only and synchronous**: call, get the answer. They also do
not restate their own input schemas here — call the tool for parameter types,
`output_type` enums, and filters; the names below are for routing and reading the
result.

## Route the question

| The question is about… | Tool | Read this from the result |
| --- | --- | --- |
| Rank/compare channels by ROI, CPIK, or marginal efficiency | `get_channel_summary` | one metric per channel; pick the `output_type` that matches the metric asked |
| How much outcome each channel drove (share / over time) | `get_contribution` | per-channel incremental contribution, total or by time |
| Base vs. incremental — what media caused vs. what would happen anyway | `get_contribution` **+** baseline | media = incremental contribution; baseline = `get_channel_summary` baseline view |
| Carryover / how long an effect lasts after exposure | `get_adstock_decay` | decay curve (and shape parameter) per channel |
| Saturation / diminishing returns / "what if we spend more or less" | `get_response_curves` | outcome across a *range* of spend per channel |
| Reach & frequency, optimal frequency | `get_reach_frequency` | ROI across frequency levels + optimal frequency (**RF models only**) |
| Single-channel spend what-if ("add $X/week to search → ROI?") | `get_spend_scenario` | base vs. new outcome + the efficiency triplet |
| Does the model fit / can I trust it | `get_model_fit` | expected vs. actual, baseline, residual over time |
| All raw series for one channel (spend, impressions, reach/freq) | `get_channel_data` | one table per channel; use `get_training_data` for non-channel series |

## Reading each result

**`get_channel_summary` — ranking and efficiency.** One row per channel for the
metric you request. `roi`/`marginal_roi` exist **only on revenue-capable models**;
on a KPI-only model they return `metric_not_supported` — fall back to
`cpik`/`marginal_cpik`, which are valid everywhere (`references/taxonomy.md`).
Direction depends on the metric: **ROI higher is better; CPIK lower is better** —
state which you ranked on, because they sort in opposite directions. Average ROI
answers "which channel paid off historically"; **marginal** ROI answers "where does
the *next* dollar work" — do not use average ROI to argue for adding budget. This
tool's paid summary view also carries per-channel **KPI lift**, so you can rank by
contribution *magnitude* here; reach for `get_contribution` when the question is
specifically about contribution *share* or its trend over time. To explain *why* a
channel ranks where it does, pair this with the response curve.

**`get_contribution` + baseline — base vs. incremental.** Contribution is the
**incremental** outcome each media channel drove — the lift media caused, at its
historical spend. The aggregate view gives each channel's share; the by-time view
gives the trend. The **baseline** (what would have happened with no paid media:
organic demand, seasonality, price) is not a channel here — read it from
`get_channel_summary`'s baseline summary view. So "base vs. incremental" is two
reads: media contribution (this tool) against baseline (channel summary). Note
contribution is a **single point at historical spend** — it cannot tell you what
happens if you spend more; that is the response curve's job (glossary: "contribution
vs. response curve").

**`get_adstock_decay` — carryover.** Shows how fast a channel's effect fades after
exposure: a slow decay means today's spend keeps paying out for several periods (a
reason short-window ROI understates a channel); a fast decay means the effect is
nearly all immediate. Compare curves across channels to say which have the
longest-lasting impact. This is about *timing of effect*, not *how much* — do not
read an ROI off it.

**`get_response_curves` — saturation and spend sensitivity.** Outcome across a
*range* of spend per channel. A curve still climbing steeply = headroom; a flat curve
= saturated, extra spend barely moves outcome (glossary: "saturation"). This is the
tool for "what if we double search spend". **Saturation is a reading, not a flag** —
no field says `saturated: true`; you infer it from the curve's shape, and confirm
with marginal ROI from `get_channel_summary` (a channel is over-invested when its
curve has flattened *and* marginal ROI has fallen below your hurdle — the full
headroom-vs-saturation reasoning is `references/budget-optimization.md`, teaching
point 2). Reading beyond the historical spend range is the least reliable part of
the curve — treat large extrapolations skeptically.

**`get_reach_frequency` — optimal frequency (RF models only).** Returns expected ROI
across weekly frequency levels plus the optimal frequency per channel — use it to
answer "how many times should each person see the ad". It is gated on model
*structure*: only models with reach/frequency channels expose it. On a non-RF model
it is absent from `available_tool_options` and returns `metric_not_supported` — this
is independent of the revenue axis (`references/taxonomy.md`); do not offer frequency
advice for a non-RF model.

**`get_spend_scenario` — single-channel what-if.** Simulates one channel at a base
and an increased spend level. **Inputs are PER TIME UNIT** (e.g. per week), not a
lump sum for the whole window — frame the question and the answer in per-period
terms. Read `outcome_mode` first (`revenue` → efficiency is ROI/mROI, higher better;
`kpi` → CPIK/mCPIK, lower better); it decides the direction of every number. The
result carries an **efficiency triplet**:
- `efficiency` — at the base spend (the channel's current efficiency),
- `marginal_efficiency` — of the **added** spend alone (the ROI/CPIK of the extra
  dollars); this is what decides whether the increase is worth it,
- `efficiency_at_new` — the blended efficiency at the new total.

Judge the increase on `marginal_efficiency`, not `efficiency`: a channel with great
base ROI can still have poor marginal ROI once saturated. There is **no
"move budget from A to B" tool** — model a shift as two scenarios (A lower, B higher)
and compare B's `marginal_efficiency` on the added dollars against A's on the removed
ones (see `references/budget-optimization.md`, teaching point 1).

**`get_model_fit` — model trust.** Per time period it returns `expected` (model),
`actual` (observed), `baseline` (no-media), and `residual` (`actual − expected`),
with credible intervals on expected and baseline. How to talk about it: the model
tracks reality well when expected sits close to actual and residuals are small and
patternless. Watch for **structured** residuals — a run of same-sign residuals, or
big misses around promotions/launches — which mean the model is missing something and
its channel estimates deserve more caution. It returns one aggregated national series
(a `geos` filter fits selected markets but still aggregates; no per-geo breakdown).
This tool does not return a single fit score (no R²/goodness-of-fit number) — describe
fit from the expected-vs-actual gap and the residual pattern, and never invent a
percentage.

## Credible intervals — say the range, not just the point

Meridian is Bayesian: every estimate is a distribution. `get_channel_summary`,
`get_contribution`, `get_response_curves`, `get_reach_frequency`, and
`get_model_fit` report means with `ci_lo`/`ci_hi`; `get_spend_scenario`'s
`base_outcome`/`new_outcome` carry the interval too. **Always report the interval
with the mean.** A wide interval means low confidence, not a precise number — and a
ranking whose channels' intervals overlap heavily is not a reliable ranking, so say
the order is uncertain rather than presenting a false precise winner. (Full
discipline: `references/glossary.md`, "credible interval".)

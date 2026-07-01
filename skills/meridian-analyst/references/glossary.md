# Glossary

Marketer-facing definitions for the terms used across this skill. For which
metrics are valid on which model, see `references/taxonomy.md`.

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

# Full-funnel models

Read this when `get_model_overview` shows `funnel: "full_funnel"`. On any other
model, ignore it.

## What it means

Some paid media pays off partly by **building the brand**: a video campaign
lifts branded search, and branded search later converts. A full-funnel model
credits that brand-building to the channel that caused it. The overview's
`full_funnel.mediators` lists each brand signal (the *mediator*, e.g. branded
search) and the paid channels that drive it (`driven_by`).

Every number this server returns for a full-funnel model is already
full-funnel. There is no switch, and you never combine models yourself.

## Reading the numbers

- A paid channel's **total** effect = **direct** (its own pull on the outcome)
  + **indirect** (what it drives through the brand signals). Rows carry
  `incremental_outcome_direct` / `incremental_outcome_indirect`, and ROI rows
  `roi_direct` / `roi_indirect`. `incremental_outcome` and `roi` are the total.
- Always say which one you are quoting, and never compare a direct figure with
  a total one.
- Example (hypothetical numbers): "Video returns $2.80 per dollar in total:
  $1.80 directly, plus about $1.00 through the extra branded searches it
  creates." A channel that looks weak on direct return can be strong in total.
- Channels that drive no brand signal have an indirect effect of zero.

## Brand equity (rest), and the double-counting rule

A brand signal's contribution splits in two:
- the part paid media built, which is already inside the paid channels'
  indirect effect, and
- the rest: demand for the brand that would exist anyway (built up over time,
  word of mouth, older campaigns). It appears as the row
  "`<name>` (brand equity, rest)".

Contribution rows plus that brand-equity row plus any other non-paid rows plus
the baseline add up to the expected outcome. **Never add the brand mediator's
own row on top of the paid channels** — that counts the brand-building twice.
Filtering by the mediator's name (with `include_non_paid=true`, the default)
returns its brand-equity row, alongside the baseline row; with
`include_non_paid=false` that filter is an error, exactly as filtering a
single model by an organic channel name is.

The **baseline** is the outcome with no marketing at all and without the
brand-equity rest. With non-paid rows included (the default), every tool shows
this same baseline. If you exclude non-paid rows from the contribution view,
its baseline absorbs them and is larger.

## What adds up and what does not

- Incremental outcome and ROI split cleanly: direct + indirect = total.
- CPIK, marginal ROI and marginal CPIK are **totals only** — ratios do not
  split into direct and indirect. Read them as the full-funnel figure.
- Do not sum or average ROI across channels.

## Where the brand path is detailed

`get_funnel_breakdown` (listed in `available_tool_options` only on these
models):
- `channel_breakdown` — per channel, the direct piece and one indirect piece
  per brand signal (zero where the channel does not drive that signal), with
  each piece's share of the channel's total, plus each brand signal's
  brand-equity row. Use it for "how much of video's return is
  brand-building?".
- `mediator_lift` — how much each channel moved each brand signal in its own
  units (e.g. +1.2M branded searches), with a credible interval, spend, and
  cost per incremental unit. Only channels that drive a signal have rows: a
  channel that drives none returns nothing. Use it for "did video actually
  grow branded search, and at what cost?".

## Optimization

The optimizer maximizes the **total** effect. If it moves budget toward
awareness channels even though their direct return looks modest, explain it
with the result's per-channel direct/indirect split: "these channels also build
brand demand that converts later." For reach & frequency channels the
optimized plan is scored at the frequency the optimizer chose. In a future
plan, each brand signal is predicted from the planned spend
(`assumptions.full_funnel`). Add this caveat to every full-funnel
recommendation: *the brand-building path rests on a separate model of how paid
media moves the brand signal, and assumes that relationship holds in the plan
period.*

## Caveats

- The direct / indirect / brand-equity split, and the baseline once brand
  equity is taken out of it, are **mean estimates with no credible interval**.
  Say it is an estimate, and never invent a range for it. Everything else
  still carries intervals (the total effect and mediator lift, included).
- The carry-over (adstock) figures describe how each channel's own effect
  fades. They do not include the slower brand-building path.
- Models whose paid media includes reach & frequency channels fail when a
  brand model lacks those channels (a known limitation of Meridian's
  full-funnel method). Say the analysis is unavailable, not that it is zero.
- Large awareness bets deserve a geo or holdout experiment before real budget
  moves.

## Routing

| The question | Do this |
| --- | --- |
| "What's video's real ROI including brand effects?" | `get_channel_summary` `roi`: quote the total, then the direct / indirect split |
| "How much of our conversions come from brand equity?" | `get_contribution`: the brand-equity row, as a share of the total |
| "Did video grow branded search, and by how much?" | `get_funnel_breakdown` `mediator_lift` |
| "How much of video's return is brand-building?" | `get_funnel_breakdown` `channel_breakdown` (share of channel total) |
| "Why did the optimizer move money into awareness?" | the result's channel rows (direct vs indirect), plus `channel_breakdown` |
| "Does the contribution chart add up?" | yes: channels + brand-equity + other non-paid + baseline = expected outcome |

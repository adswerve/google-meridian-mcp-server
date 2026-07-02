---
name: meridian-analyst
description: >-
  Use when analyzing a Google Meridian marketing-mix model through this MCP —
  budget optimization and reallocation, channel ROI/performance, response
  curves, adstock, reach & frequency, or model diagnostics. Routes business
  questions to the right tools and interprets the results.
---

# Meridian Analyst

This server exposes a fitted Google Meridian marketing-mix model (MMM) as MCP
tools: read-only analysis (channel ROI, contribution, response curves, adstock,
reach & frequency, model fit) plus a long-running **budget-optimization module**
that reallocates spend across channels. Your job is to route a business question
to the right tool, choose the objective that fits the model, and interpret the
numbers correctly — uncertainty included.

## Golden path — always start here

1. `list_models` — pick the `model_id`.
2. `get_model_overview` — your map of the model. Before calling anything else,
   read its `available_tool_options`: it lists exactly which tools and metrics
   are **legal for this specific model**, computed from the model's real
   capabilities. Treat it as ground truth; never call a tool or metric it does
   not list.

The overview also gives you the two facts that drive every downstream choice:
whether the model is **national or geo**, and whether it **can measure revenue**.
See `references/taxonomy.md` for how each fact changes what is valid.

## Cardinal rules

- **Optimization is asynchronous.** `run_optimization` returns a `run_id`, not an
  answer. Poll `get_optimization_status` until status is `completed`, then call
  `get_optimization_result`. Never treat the submit call as the result. Other
  terminal states are `failed` and `canceled`. `cancel_optimization` stops a run;
  `list_optimizations` and `delete_optimization` manage prior runs.
- **Match the objective to the model's revenue capability.** Revenue-capable
  models → optimize and report on ROAS/ROI (higher is better). No-revenue
  (KPI-only) models → use CPIK, cost per incremental KPI (lower is better —
  it is the inverse of ROI). `get_model_overview` tells you which; when unsure,
  let the model's native objective apply rather than forcing one.
- **Revenue metrics are conditional; CPIK is universal.** `roi`/`marginal_roi`
  exist only for revenue-capable models — requesting them on a KPI-only model
  returns `metric_not_supported`. `cpik`/`marginal_cpik` are valid on every model.
- **Reach & frequency is RF-only.** `get_reach_frequency` applies only to models
  with reach/frequency channels; otherwise it is absent from
  `available_tool_options` and returns `metric_not_supported`.
- **Never present a point estimate as certain.** Analysis and optimization
  outputs carry credible intervals (`ci_lo`/`ci_hi`). Report the interval with
  the mean; a wide interval means low confidence, not a precise number.

## Model taxonomy (quick view)

- **Scope:** national (one aggregate series) or geo (per-region, sub-selectable).
- **Outcome:** revenue models; KPI-only models; and KPI + `revenue_per_kpi`
  models (KPI converted to revenue, so they behave as revenue-capable).

Revenue-capability decides which metrics are legal; `outcome_mode` in results
reports `revenue` vs `kpi`. Full validity matrix and how to read it off
`available_tool_options`: `references/taxonomy.md`.

## Routing

| The question is about… | Go to |
| --- | --- |
| Whole-budget allocation or reallocation across channels, "how should I spend", target ROAS/mROAS | `references/budget-optimization.md` |
| Channel ROI/performance, contribution, response curves, saturation, adstock, reach & frequency, single-channel spend what-ifs, model fit/diagnostics | `references/channel-performance.md` |
| What the model types mean and which tools/metrics are valid where | `references/taxonomy.md` |
| An unfamiliar term (ROAS, CPIK, adstock, incremental…) | `references/glossary.md` |

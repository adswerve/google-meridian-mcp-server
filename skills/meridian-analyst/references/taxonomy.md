# Model taxonomy

Two independent axes describe every Meridian model on this server. Read both off
`get_model_overview` before choosing tools or metrics.

## Axis 1 — geographic scope

| Type | What it is | Consequence |
| --- | --- | --- |
| **National** | One aggregated series for the whole market. | No geo dimension; geo filtering and per-geo optimization do not apply. |
| **Geo** | Per-region series (e.g. `US-CA`, `US-NY`) with populations. | You can slice, report, or optimize over a subset of regions, or aggregate across them. |

## Axis 2 — outcome / revenue capability

This axis decides which metrics are legal.

| Type | Revenue-capable? | Native objective | `outcome_mode` |
| --- | --- | --- | --- |
| **Revenue** | Yes | ROAS/ROI | `revenue` |
| **KPI + `revenue_per_kpi`** | Yes — KPI × revenue-per-KPI becomes revenue | ROAS/ROI | `revenue` |
| **KPI-only** | No | CPIK | `kpi` |

"Revenue-capable" means the model can express the outcome in revenue terms.
KPI-only models can only express cost per incremental KPI.

## Metric / tool validity matrix

| Metric or tool | Revenue | KPI + revenue_per_kpi | KPI-only |
| --- | --- | --- | --- |
| `roi`, `marginal_roi` | valid | valid | `metric_not_supported` |
| `cpik`, `marginal_cpik` | valid | valid | valid |
| `get_reach_frequency` | RF channels only | RF channels only | RF channels only |
| every other analysis tool | valid | valid | valid |

- `roi`/`marginal_roi` require revenue; on a KPI-only model they raise
  `metric_not_supported`.
- `cpik`/`marginal_cpik` are defined for every model — always a safe efficiency
  metric to fall back on.
- `get_reach_frequency` is gated on model *structure* (must have reach/frequency
  channels), independent of the revenue axis; on a non-RF model it is not offered
  and raises `metric_not_supported`.

## Read validity at runtime — do not guess

`get_model_overview.available_tool_options` is computed from the loaded model, so
it already reflects both axes:

- It **omits** `roi`/`marginal_roi` for no-revenue models.
- It **lists** `get_reach_frequency` only when the model has RF channels.
- It enumerates the legal channels and geos to use when optimizing.

Rule: if a tool or metric is not in `available_tool_options`, do not call it. The
overview is authoritative over any assumption from this document.

## Optimization objective follows the same axis

`run_optimization` picks its objective from the same revenue capability: revenue
models optimize ROAS, KPI-only models optimize CPIK. A ROAS-style target given
against a KPI-only model is interpreted as a CPIK target automatically. Normally
let the model's native objective apply; override only with a clear reason. Full
workflow: `budget-optimization.md`.

## Future optimization: two independent axes of input

`run_future_optimization` splits its inputs into two axes that move
independently — knowing which axis a business statement belongs to is what lets
you translate it into the right field.

| Axis | Covers | Set by |
| --- | --- | --- |
| **Allocation axis** | The budget total and the spend mix you intend to run | `scenario` (same as `run_optimization`) + `planned_allocation` |
| **Cost-structure axis** | Cost-per-media-unit, flighting, and revenue-per-KPI — always carried forward from a historical window, never entered directly | `future.reference`, optionally scaled by `future.cost_multipliers` / `future.revenue_per_kpi_multiplier` |

A statement like "we plan to spend $2M, weighted toward search" is an
**allocation-axis** input (`scenario.budget` + `planned_allocation`). A statement
like "TV is getting 15% more expensive" or "plan like last year's holiday season"
is a **cost-structure-axis** input (`cost_multipliers` or `reference`
respectively) — there is no way to set cost-per-media-unit or flighting directly;
they only ever come from the chosen `reference` window. See `glossary.md`
("reference window", "cost multiplier", "flighting", "planned allocation") for
term definitions and `budget-optimization.md` for the full field reference.

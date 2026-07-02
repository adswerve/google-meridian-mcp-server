# mmm-showcase ↔ Meridian MCP Parity

Scope: Home, Response Curves, Attribution, Lag Effects, Reach & Frequency, Data
Exploration, Budget Optimization (Phase 2). Excluded: Summary Report, Model Diagnostics.

Verified against `../mmm-showcase` pages under `src/ui/pages/` and the MCP tools
in `src/google_meridian_mcp_server/`.

## Feature parity

| Showcase item | Page | Meridian quantity | MCP tool | Status | Notes |
|---|---|---|---|---|---|
| Model metadata (time range, geos, channels, model type) | Home | input_data coords | get_model_overview | Supported | |
| Channel inventory (media/RF/organic/non-media/controls) | Home | input_data coords | get_model_overview | Supported | |
| Geographic markets + population | Home | input_data.geo/population | get_model_overview | Supported | geo list feeds every other tool's `geos` filter |
| Historical channel spend time-series | Response Curves | input_data.media_spend/rf_spend | get_channel_data | Supported | per-channel long |
| Response/saturation curve (incremental outcome vs spend) | Response Curves | Analyzer.response_curves | get_response_curves | Supported | |
| ROI / CPIK / mROI efficiency at spend points | Response Curves | derived from response curve | get_spend_scenario | Supported | what-if spend simulation; returns efficiency triplet at base and new spend |
| Media summary table (paid + non-paid) | Attribution | Analyzer.summary_metrics | get_channel_summary (paid_summary_metrics) | Supported | |
| % outcome contribution & % spend by channel | Attribution | summary_metrics | get_channel_summary / get_contribution | Supported | |
| ROI / CPIK by channel | Attribution | summary_metrics | get_channel_summary (roi/cpik) | Supported | roi gated to revenue models |
| Contribution waterfall (incl. baseline & non-paid) | Attribution | contribution_metrics | get_contribution | Supported | |
| Response curves (all channels) | Attribution | Analyzer.response_curves | get_response_curves | Supported | |
| Model fit: expected vs actual + baseline + residual | Attribution | Analyzer.expected_vs_actual_data | get_model_fit | Supported | geo-filterable (delegates to Meridian ModelFit) |
| Adstock decay curves | Lag Effects | Analyzer.adstock_decay | get_adstock_decay | Supported | |
| Optimal frequency / ROI-vs-frequency | Reach & Frequency | Analyzer.optimal_freq | get_reach_frequency | Supported | RF-only |
| Raw input series (impressions, spend, reach, freq, controls, KPI) | Data Exploration | input_data arrays | get_channel_data + get_training_data | Supported | channel-keyed vs raw datasets |
| VIF / multicollinearity | Data Exploration | statsmodels over scaled arrays | — | Out-of-scope | computed app-side, not a Meridian output |
| Per-geo disaggregated metrics (one row per geo) | (various) | aggregate_geos=False | — | Future work | app filters/aggregates to a geo selection; true per-geo breakdown in one call not yet exposed |
| Budget optimization (fixed-budget / target-ROAS) | Budget Optimization | BudgetOptimizer | run_optimization, get_optimization_status, get_optimization_result, list_optimizations, delete_optimization, cancel_optimization | Phase 2 complete | structured result = summary + channel tables + allocation + spend-delta + response_curves; local subprocess + Cloud Run CPU/GPU tiers (JAX backend); cancel tool; CPU real-smoke verified against as-dev-anze; remaining non-goals: rendered HTML/PDF report, reach-&-frequency optimization |

Every in-scope showcase item is now backed by a tool; only VIF (app-side
computation) and true per-geo disaggregation remain unsupported.

## Geo-level filtering parity

The user-facing requirement: wherever the app exposes a geo control, the MCP
must be able to filter the same quantity by geo (via the `geos` list on
`AnalysisFilters`). Geo controls were inventoried page-by-page:

| Page | App geo control | MCP geo support | Parity |
|---|---|---|---|
| Home | none — markets table is metadata | n/a (lists geos) | ✓ |
| Response Curves | single-geo selectbox (`attribution`-style) | get_channel_data, get_response_curves, get_spend_scenario all honor `geos` | ✓ |
| Attribution | single-geo selectbox drives the whole page | get_channel_summary, get_contribution, get_response_curves honor `geos` | ✓ partial |
| Attribution → model fit | same geo selector feeds the fit/prediction chart (`attribution.py:323` passes `selected_geos`) | get_model_fit honors geos via Meridian's ModelFit visualizer | ✓ |
| Lag Effects | none — adstock/alpha are geo-independent posterior params | get_adstock_decay ignores `geos` (matches) | ✓ |
| Reach & Frequency | none — date range only | get_reach_frequency honors `geos` (superset) | ✓ |
| Data Exploration | multi-geo `multiselect` + "Aggregate Geo-level Data" toggle | get_training_data, get_channel_data honor `geos` (list ⇒ multi-geo) | ✓ |

Notes:
- The MCP `geos` filter accepts a **list**, so it is a superset of the app's
  single-geo selectboxes (Response Curves, Attribution) and matches the
  multiselect on Data Exploration.
- `get_channel_summary` (all six output types) and `get_contribution` apply the
  selection through `FilteredMediaSummary(selected_geos=…)` — the same
  mechanism the app's Attribution page uses, so contribution geo-filtering
  flows via the overridden `get_*_summary_metrics`.
- `get_adstock_decay`/`alpha_summary` are geo-independent (model posterior
  parameters); the Lag Effects page exposes no geo control either, so omitting
  geo there is correct parity, not a gap.

Note: national credible intervals now match the app (summed per-geo intervals from Meridian's `ModelFit`).

# mmm-showcase ↔ Meridian MCP Parity

Scope: Home, Response Curves, Attribution, Lag Effects, Reach & Frequency, Data
Exploration. Excluded: Summary Report, Budget Optimization, Model Diagnostics.

| Showcase item | Page | Meridian quantity | MCP tool | Status | Notes |
|---|---|---|---|---|---|
| Model metadata (time range, geos, channels, model type) | Home | input_data coords | get_model_overview | Supported | |
| Channel inventory (media/RF/organic/non-media/controls) | Home | input_data coords | get_model_overview | Supported | |
| Geographic markets + population | Home | input_data.geo/population | get_model_overview | Supported | |
| Historical channel spend time-series | Response Curves | input_data.media_spend/rf_spend | get_channel_data | Supported | per-channel long |
| Response/saturation curve (incremental outcome vs spend) | Response Curves | Analyzer.response_curves | get_response_curves | Supported | |
| ROI / CPIK / mROI efficiency at spend points | Response Curves | derived from response curve | get_spend_scenario | Supported (new) | what-if spend simulation; returns efficiency triplet at base and new spend |
| Media summary table (paid + non-paid) | Attribution | Analyzer.summary_metrics | get_channel_summary (paid_summary_metrics) | Supported | |
| % outcome contribution & % spend by channel | Attribution | summary_metrics | get_channel_summary / get_contribution | Supported | |
| ROI / CPIK by channel | Attribution | summary_metrics | get_channel_summary (roi/cpik) | Supported | roi gated to revenue models |
| Contribution waterfall (incl. baseline & non-paid) | Attribution | contribution_metrics | get_contribution | Supported | |
| Response curves (all channels) | Attribution | Analyzer.response_curves | get_response_curves | Supported | |
| Model fit: expected vs actual + baseline + residual | Attribution | Analyzer.expected_vs_actual_data | get_model_fit | Supported (new) | geo-aggregated |
| Adstock decay curves | Lag Effects | Analyzer.adstock_decay | get_adstock_decay | Supported | |
| Optimal frequency / ROI-vs-frequency | Reach & Frequency | Analyzer.optimal_freq | get_reach_frequency | Supported (new) | RF-only |
| Raw input series (impressions, spend, reach, freq, controls, KPI) | Data Exploration | input_data arrays | get_channel_data + get_training_data | Supported | channel-keyed vs raw datasets |
| VIF / multicollinearity | Data Exploration | statsmodels over scaled arrays | — | Out-of-scope | computed app-side, not a Meridian output |
| Per-geo disaggregated metrics | (various) | aggregate_geos=False | — | Future work | dead flag removed; not yet implemented |

New tools `get_model_fit`, `get_reach_frequency`, `get_channel_data`, and
`get_spend_scenario` close the previously-unsupported items. VIF remains out of
scope.

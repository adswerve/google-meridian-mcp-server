# Skills audit: `skills/meridian-analyst/` against Meridian 2.0

Task 22 (Phase 6b). Every behavioural claim in `skills/meridian-analyst/SKILL.md`
and `skills/meridian-analyst/references/*.md` was inventoried and checked
against a live in-process server call on the refit 2.0 fixtures
(`models/_validation/*`), not just read for plausibility.

Method: `uv run python -c "..."` driving `fastmcp.Client(mcp)` in-process, with
`PERSISTENCE_BACKEND=local`, `LOCAL_MODELS_ROOT=models/_validation`,
`RESULT_CACHE_ENABLED=false`. Optimization calls used an isolated
`OPTIMIZATION_RUNS_ROOT` (a scratch directory) so they could not collide with
the concurrently-running `live_validate` process, which owns the shared
`models/_validation/_runs` / `_cloud_runs` paths. `live_validate` itself was
never invoked, and `capture_baseline --label v1.7-engine` was never invoked.
Models used: `geo-revenue`, `national-revenue`, `geo-kpi-only`,
`national-kpi-only`, `geo-kpi-rpk`, `national-kpi-rpk`,
`geo-revenue-media-only`.

## Full-funnel rows (36 onward)

Rows 36 onward were added with `references/full-funnel.md` (Task 18). They were
run the same way on `models/_validation/geo-full-funnel` (mediators `M1` driven
by `A`; `M2` driven by `A` and `B`; paid channels `A`, `B`, `C`; `C` drives
neither), plus `geo-revenue` / `national-revenue` as the single-model contrast.
Optimization calls used a scratch `OPTIMIZATION_RUNS_ROOT`. The line numbers
cited in rows 1-35 were refreshed against the edited skill files. Rows 53 and 54
(reach & frequency) could not be run against a fixture; they are marked
unverified live.

## Verified claims

| # | Claim | Source | Verifying call | Result |
| - | --- | --- | --- | --- |
| 1 | `list_models` → `get_model_overview` is the required first step; overview's `available_tool_options` lists legal tools/metrics for that model | SKILL.md:20-27 | `list_models`; `get_model_overview` on 7 models | `available_tool_options` present on every model, keyed by tool name, enumerating `output_type`/`channel`/`scenarios`/etc. Confirmed. |
| 2 | Overview tells you national vs. geo, and revenue-capability | SKILL.md:29-32, taxonomy.md:1-25 | `get_model_overview` | Overview carries `model_type` (`geo`/`national`), `is_national` (bool), `has_revenue_per_kpi` (bool). Confirmed. |
| 3 | Optimization is async: `run_optimization`/`run_future_optimization` return a `run_id`, not an answer | SKILL.md:36-39 | `run_optimization` submit on `national-revenue` | Submit returned `{"run_id", "status":"queued", "compute_tier_resolved", "meridian_version", "size_score", "reused"}` — no result fields. Confirmed. |
| 4 | Poll `get_optimization_status` until terminal; terminal = `failed`/`canceled` (`completed` also terminal) | SKILL.md:38-41, budget-optimization.md:77-82 | polled the above run | Observed sequence `queued` → `running` → `completed`. Confirmed. |
| 5 | Status carries a coarse phase + progress fraction while in-flight, an error object on `failed` | budget-optimization.md:80-82 | `get_optimization_status` | Status object keys: `run_id, status, phase, progress_fraction, heartbeat_at, started_at, finished_at, elapsed_seconds, compute_tier, meridian_version, error`. `phase`/`progress_fraction`/`error` all present as claimed. Did not force a real failure, so the *content* of `error` on a genuine failure is unverified (see "Cannot verify"). |
| 6 | The five run-id tools (`get_optimization_status`, `get_optimization_result`, `list_optimizations`, `cancel_optimization`, `delete_optimization`) manage historical and future runs identically | SKILL.md:40-43 | Ran both a historical and a future run through all five tools | Both run kinds used the same `run_id` namespace and the same five tools with no kind-specific branching visible from the outside. Confirmed. |
| 7 | Revenue-capable models optimize/report ROAS; KPI-only use CPIK | SKILL.md:50-54, taxonomy.md:14-25 | overview + `run_optimization` result `outcome_mode` on `geo-revenue` vs `geo-kpi-only` | `geo-revenue` → `outcome_mode: "revenue"`; `geo-kpi-only` → `"kpi"`. Confirmed. |
| 8 | `roi`/`marginal_roi` exist only for revenue-capable models; KPI-only → `metric_not_supported` | SKILL.md:55-57, taxonomy.md:31-43 | `get_channel_summary(geo-kpi-only, output_type="roi")` | `{"error_code":"metric_not_supported","message":"Metric 'roi' is not supported for model 'geo-kpi-only': model has no revenue_per_kpi; ROI metrics require revenue", ...}`. Confirmed. |
| 9 | `cpik`/`marginal_cpik` valid on every model | SKILL.md:57, taxonomy.md:32 | `get_channel_summary(geo-revenue, "cpik")` succeeded; `available_tool_options.get_channel_summary.output_type` includes `cpik`/`marginal_cpik` on every one of the 7 models | Confirmed. |
| 10 | `get_reach_frequency` is RF-only; absent from `available_tool_options` and `metric_not_supported` otherwise | SKILL.md:58-60, taxonomy.md:33,41-43 | overview on `geo-revenue-media-only` (no RF channels) omits `get_reach_frequency` from `available_tool_options`; direct call returns `metric_not_supported` (`"model has no reach & frequency channels"`) | Confirmed both halves. |
| 11 | RF gating is independent of the revenue axis — a KPI-only model can still have `get_reach_frequency` | taxonomy.md:41-43, channel-performance.md:92-96 | `geo-kpi-only` and `national-kpi-only` overviews both list `get_reach_frequency` (they have `rf_ch_0`/`rf_ch_1`) | Confirmed. |
| 12 | On a KPI-only RF model, `get_reach_frequency`'s `roi` column is still returned, in KPI units per spend | channel-performance.md:92-96 | `get_reach_frequency(geo-kpi-only)` | Returned `columns: [channel, frequency, roi, ci_lo, ci_hi, optimal_frequency]` with numeric `roi` values (e.g. 1.35 at freq 1.0) on a model whose `get_channel_summary` has no `roi` at all. Confirmed. |
| 13 | Analysis outputs carry `ci_lo`/`ci_hi`; `get_channel_data` (raw series) has no CI | SKILL.md:66-70, channel-performance.md:127-137 | `get_channel_data`, `get_model_fit`, `get_spend_scenario`, `get_response_curves(response_curve_summary)`, `get_channel_summary(baseline_summary_metrics)` | `get_channel_data` columns: `channel, channel_type, geo, time, impressions, spend, reach, frequency, rf_spend, value` — no CI. The other four all carry `ci_lo`/`ci_hi` (or `expected_ci_lo/hi`, `baseline_ci_lo/hi`). Confirmed. |
| 14 | `get_optimization_result` reports point estimates (means), not credible intervals | budget-optimization.md:93-95 | `run_optimization` result | `channel_tables`/`summary`/`allocation`/`spend_delta` contain only plain numeric fields — no `ci_lo`/`ci_hi` anywhere in the envelope. Confirmed. |
| 15 | Valid `scenario` types are exactly `fixed_budget`/`target_roas`/`target_mroas`; valid `constraint` modes exactly `global`/`per_channel` | budget-optimization.md:40-42 | `available_tool_options.run_optimization.scenarios` on all 7 models; tool schema (`OptimizationConfig`) | Every model lists exactly `["fixed_budget","target_roas","target_mroas"]`; `BaseOptimizationConfig`/constraint models in `domain/optimization.py` define exactly `Literal["global"]`/`Literal["per_channel"]`. Confirmed. |
| 16 | Identical `(model_id, config)` reuses a prior run unless `force_rerun=true`; a reused run may already be `completed` | budget-optimization.md:73-76 | Resubmitted the exact same `national-revenue` `fixed_budget` config | Second submit returned `"reused": true`, `"status": "completed"` immediately (same `run_id` as the first). Confirmed. |
| 17 | `get_optimization_result` fields: `outcome_mode`, `summary` (non/optimized budget, incremental outcome, efficiency), `channel_tables.initial/optimized` (spend, pct_of_spend, incremental_outcome, roi, mroi, cpik, effectiveness), `allocation`, `spend_delta` (cuts first, then increases), `response_curves` | budget-optimization.md:91-124 | `run_optimization` result on `national-revenue` | All fields present with exactly those names; `spend_delta` order observed as `[-15, -11, +13, +12, +1]` — cuts first. Confirmed. |
| 18 | `assumptions` (future runs only): `budget`, `budget_source` (`explicit`/`derived_from_reference`/`determined_by_target`), `reference_mode`, `excluded_channels` | budget-optimization.md:117-124, glossary.md:103-114 | `run_future_optimization` result on `national-revenue`, no `budget` given | `assumptions: {"budget": 25.49..., "budget_source": "derived_from_reference", "reference_mode": "trailing", "excluded_channels": []}`. All three `budget_source` literal strings also confirmed present verbatim in `meridian/optimizer_facade.py:264-271`. Confirmed (explicit/determined_by_target values confirmed at the source-code level, not separately round-tripped through a live call — see "Cannot verify"). |
| 19 | Historical `run_optimization` results have **no** `assumptions` field | budget-optimization.md:117 (implicit) | `run_optimization` result | Result keys: `run_id, outcome_mode, summary, channel_tables, allocation, spend_delta, response_curves` — no `assumptions`. Confirmed. |
| 20 | Future runs omit `response_curves` | budget-optimization.md:268-271 | `run_future_optimization` result | `"response_curves" in result` → `False`. Confirmed. |
| 21 | `cost_multipliers` values must be `>0`; `0` is rejected, not treated as free | budget-optimization.md:222-229 | `run_future_optimization` with `cost_multipliers: {"ch_0": 0}` | Pydantic validation error: `"Value error, weights must be > 0"`. Confirmed. |
| 22 | `planned_allocation` values must be `>0`; `0` rejected | budget-optimization.md:233-239 | `run_future_optimization` with `planned_allocation: {"ch_0": 0}` | Same `"weights must be > 0"` error. Confirmed. |
| 23 | `excluded_channels`: forces spend to 0, channel still appears in the result with spend 0, budget reallocated (total unchanged) | budget-optimization.md:240-250, glossary.md:97-101 | `run_future_optimization` with `excluded_channels: ["ch_0"]` | `assumptions.excluded_channels: ["ch_0"]`; `channel_tables.optimized` still has a `ch_0` row with `spend: 0.0, roi: null, mroi: null`; `summary.optimized_budget` unchanged from the run without exclusion. Confirmed. |
| 24 | Omitted `budget` in a future `fixed_budget` run defaults to the `reference` window's carried-forward spend, not the full historical total | budget-optimization.md:251-259 | future run above | `assumptions.budget = 25.49` (a 4-period trailing total, not `national-revenue`'s 320 full-history total from the historical run). Confirmed distinct from the historical-run default. |
| 25 | `get_training_data` merges named datasets into one table | budget-optimization.md (routing), SKILL.md, tool docstring | `get_training_data(geo-revenue, ["kpi","media_spend"])` | Returned one merged table, columns `[geo, time, media_channel, kpi, media_spend]`. Confirmed. |
| 26 | `get_model_fit` fields: `expected`/`actual`/`baseline`/`residual`, CIs on `expected` and `baseline`, no fit-score field | channel-performance.md:115-125 | `get_model_fit(geo-revenue)` | Columns: `time, expected, expected_ci_lo, expected_ci_hi, actual, baseline, baseline_ci_lo, baseline_ci_hi, residual`. No R²/score field. Confirmed. |
| 27 | `get_model_fit` with a `geos` filter aggregates to one national series, no per-geo breakdown | channel-performance.md:121-122 | `get_model_fit(geo-revenue, filters={"geos":[one geo]})` | `row_count == 52` (one row per time period, not per geo × time). Confirmed. |
| 28 | `get_channel_summary`'s paid summary view also carries per-channel KPI lift (`incremental_outcome`) | channel-performance.md:48-50 | `get_channel_summary(geo-revenue, "paid_summary_metrics")` | Columns include `incremental_outcome`, `pct_of_contribution` alongside spend/impressions. Confirmed. |
| 29 | `get_spend_scenario` returns the efficiency triplet `efficiency`/`marginal_efficiency`/`efficiency_at_new`, `outcome_mode` decides direction | channel-performance.md:98-113, budget-optimization.md | `get_spend_scenario(geo-revenue, "ch_0", 100)` and same on `geo-kpi-only` | Both calls returned exactly those three fields plus `outcome_mode` (`"revenue"` / `"kpi"`) and `base_outcome`/`new_outcome` with `ci_lo`/`ci_hi`. Confirmed. |
| 30 | No tool returns `saturated: true`; saturation is read off `get_response_curves` + marginal ROI | budget-optimization.md:58-66, channel-performance.md:74-84 | `get_response_curves(response_curve_summary)`, `get_channel_summary` | Neither response carries any boolean/flag field; only numeric spend/outcome/ROI series. Confirmed (absence). |
| 31 | `cancel_optimization` is best-effort; has no effect on an already-`completed`/`failed` run | budget-optimization.md:87-89 | `cancel_optimization` on a completed run, then re-checked status | `cancel_optimization` returned `{"status":"canceled"}` in its own reply, but `get_optimization_status` immediately after still reported `"completed"` — the stored run was unaffected. Confirmed. |
| 32 | `delete_optimization` permanently removes a run; irreversible | budget-optimization.md:87-89 | `delete_optimization` then `get_optimization_result` | Delete returned `{"deleted": true}`; subsequent `get_optimization_result` → `{"error_code":"optimization_run_not_found", ...}`. Confirmed. |
| 33 | `list_optimizations` shows config summary, status, headline result | budget-optimization.md:87-89 | `list_optimizations(model_id="national-revenue")` | Row carried `run_id, label, model_id, config_summary, status, created_at, finished_at, headline` (e.g. `"ROAS 3.53625 -> 5.01456 at budget 320.0"`). Confirmed. |
| 34 | There is no "move $ from A to B" tool; model it as two `get_spend_scenario` calls or a `per_channel` constraint | budget-optimization.md:44-56, channel-performance.md:110-113 | Tool inventory (`transport/tools.py`) | No such tool exists among the 15 registered tools. Confirmed (by exhaustive absence). |
| 35 | `get_contribution` default output includes a `baseline` row (organic + non-media folded in) | channel-performance.md:53-67 (**pre-correction text claimed the opposite**) | `get_contribution(geo-revenue, "contribution_metrics")` with default filters, and again with `include_non_paid: false` | Both calls returned a row `["baseline", 1915.73, 0.2859]` / `["baseline", 2991.98, 0.4466]` respectively — `baseline` is always a row in this output, and its value with `include_non_paid=true` matches `get_channel_summary`'s baseline `mean` exactly (1915.73). **This contradicted the skill's original claim — see Corrections.** |
| 36 | Overview of a full-funnel model carries `funnel: "full_funnel"` and a `full_funnel.mediators` block listing each brand signal, its `driven_by` channels and its brand-equity label | full-funnel.md:1-12, SKILL.md:29-31 | `get_model_overview(geo-full-funnel)`; `list_models` | `funnel: "full_funnel"`; `full_funnel.mediators = [{name: M1, driven_by: [A], brand_equity_label: "M1 (brand equity, rest)"}, {name: M2, driven_by: [A, B], brand_equity_label: "M2 (brand equity, rest)"}]`. `list_models` lists the same model with `funnel: "full_funnel"`, `mediators: [M1, M2]`. Confirmed. |
| 37 | Every other model is `funnel: "single"` and has no `full_funnel` block | full-funnel.md:3-4, SKILL.md:88-89 | `get_model_overview(geo-revenue)`; `list_models` | `funnel: "single"`, no `full_funnel` key; all 7 other `list_models` rows show `funnel: "single"`. Confirmed. |
| 38 | `get_funnel_breakdown` is listed in `available_tool_options` only on full-funnel models; on a single model it is not offered and returns an error | full-funnel.md:59-62, taxonomy.md:52 | `get_model_overview` on `geo-full-funnel` and `geo-revenue`; `get_funnel_breakdown(geo-revenue, mediator_lift)` | Listed on `geo-full-funnel` (`output_type: [channel_breakdown, mediator_lift]`, `channels: [A, B, C, M1, M2]`, `mediators: [M1, M2]`), absent on `geo-revenue`. The direct call on `geo-revenue` returned `metric_not_supported` ("model is not a full-funnel model"). Confirmed. |
| 39 | Direct + indirect = total, for ROI and for incremental outcome, on the mean rows of the channel summary | full-funnel.md:19-22, 54 | `get_channel_summary(geo-full-funnel, roi)` and `paid_summary_metrics` | `roi` A 0.471571 = 0.460919 + 0.0106518; B 0.110273 = 0.092393 + 0.0178804; All Channels 0.307251 = 0.297945 + 0.00930692. `incremental_outcome` A 74715.7 = 73028.1 + 1687.68 (sums agree to rounding). Split columns are populated on `mean` rows only; median, `ci_lo`, `ci_hi` rows carry the total's interval and null split. Confirmed. |
| 40 | A channel that drives no brand signal has zero indirect effect | full-funnel.md:28 | Same `roi` / `paid_summary_metrics` calls; `get_contribution` | Channel `C`: `roi_indirect` 0.0, `incremental_outcome_indirect` 0.0, `incremental_outcome_direct` 52996.7 = total. Confirmed. |
| 41 | `get_contribution` shows each brand signal only as "`<name>` (brand equity, rest)"; the raw mediator series is not a row | full-funnel.md:30-37, SKILL.md:61-65 | `get_contribution(geo-full-funnel, contribution_metrics)` | Rows: `baseline`, `A`, `C`, `B`, `M2 (brand equity, rest)`, `M1 (brand equity, rest)`. No raw `M1` / `M2` row. Confirmed. |
| 42 | Contribution rows + brand-equity rows + other non-paid rows + baseline add up to the expected outcome | full-funnel.md:39-41, 110 | `get_contribution` (aggregate and by time) against `get_model_fit` | Aggregate: 2548320 + 74715.7 + 52996.7 + 16539.7 + 9497.81 + 3614.26 = 2705684.2 vs summed `expected` 2705686.9 (rounding in 6-significant-figure cells); shares sum to 0.9999995. By time: for all 52 periods the per-period rows sum to that period's `expected` within 0.1%. Confirmed. |
| 43 | There is one baseline: with non-paid rows included, the baseline is the same in `get_contribution`, `get_channel_summary` baseline view and `get_model_fit` | full-funnel.md:47-50 | `get_contribution` default; `get_channel_summary(baseline_summary_metrics)`; `get_model_fit` summed over the window | 2548320.0 (contribution) = 2548320.0 (channel summary `baseline_outcome`) vs 2548322.8 (model-fit baseline summed; rounding). Confirmed. |
| 44 | With `include_non_paid=false` the contribution baseline absorbs the brand-equity rows and is larger | full-funnel.md:49-50 | `get_contribution(..., filters={include_non_paid: false})` | Rows `baseline` (2561430), `A`, `C`, `B` only. 2561430 = 2548320 + 9497.81 + 3614.26 (2561432; rounding). Confirmed. |
| 45 | Filtering contribution by a mediator's name returns its brand-equity row (plus the baseline row) with `include_non_paid=true`; with `include_non_paid=false` it errors, as filtering a single model by an organic channel name does | full-funnel.md:42-45 | `get_contribution(geo-full-funnel, channels=["M1"])`, again with `include_non_paid: false`; `get_contribution(geo-revenue, channels=[organic_channel_0], include_non_paid: false)` | Default: rows `baseline`, `M1 (brand equity, rest)` (3614.26); the by-time view with `channels=["M1"]` gives the same two rows per period. `include_non_paid: false`: `missing_model_data` ("not all values found in index 'channel'"), identical to the single-model organic-name error. Confirmed. The brief said the mediator filter simply returns the row; the qualifier on `include_non_paid` and the extra baseline row come from the live payload. |
| 46 | CPIK, marginal ROI and marginal CPIK are totals only: no direct / indirect columns | full-funnel.md:55-56, glossary.md:11-21 | `get_channel_summary` with `cpik`, `marginal_roi`, `marginal_cpik` | Columns are only `channel, metric, <metric>`; no `_direct` / `_indirect` column. Confirmed. (`roi` alone has `roi_direct` / `roi_indirect`.) |
| 47 | `channel_breakdown`: per channel a direct piece and an indirect piece per brand signal (zero where the channel does not drive it), each with a share of the channel total, plus a brand-equity row per brand signal | full-funnel.md:63-67 | `get_funnel_breakdown(geo-full-funnel, channel_breakdown)` | 11 rows: for A, B, C one `direct` row and one `indirect` row for each of M1, M2 (B/M1, C/M1, C/M2 are 0.0 with share 0.0), plus `M1 (brand equity, rest)` 3614.26 and `M2 (brand equity, rest)` 9497.81 with null share. A shares: 0.977412 + 0.0153651 + 0.0072229 = 1. Confirmed; this corrects the brief's assumption that only driving channels get an indirect row. |
| 48 | `mediator_lift`: units in the brand signal's own scale, with credible interval, spend and cost per incremental unit; only driving channels have rows, and a paid channel that drives none returns no rows | full-funnel.md:68-72 | `get_funnel_breakdown(geo-full-funnel, mediator_lift)`, again with `channels=["C"]` | 3 rows (M1/A, M2/A, M2/B) with `incremental_units`, `incremental_units_ci_lo` / `_ci_hi` (e.g. M1/A 122766 [103614, 138575]), `spend`, `cost_per_incremental_unit`. Filtered to `C`: `columns: []`, `rows: []`, `row_count: 0`. Confirmed. |
| 49 | The direct / indirect / brand-equity split and the adjusted baseline carry no interval; everything else keeps its interval | full-funnel.md:89-92, SKILL.md:66-70, channel-performance.md:144-146 | `get_channel_summary(baseline_summary_metrics)`, `get_model_fit`, `channel_breakdown`, `roi` rows | `baseline_summary_metrics`: only the `mean` row has a value (2548320); median / `ci_lo` / `ci_hi` null. `get_model_fit`: `baseline_ci_lo` / `baseline_ci_hi` null, `expected_ci_*` populated. `channel_breakdown` has no interval columns. `roi` total and `mediator_lift` keep theirs. Confirmed. The brief only named the split; the baseline having no interval is a live finding and was added to the skill. |
| 50 | Optimizer maximizes the total effect: result channel rows carry `incremental_outcome_direct` / `incremental_outcome_indirect` that add up to `incremental_outcome` | full-funnel.md:76-78, budget-optimization.md:105-110 | `run_optimization(geo-full-funnel, fixed_budget)` then `get_optimization_result` | Optimized A: 91716.8 = 89694.2 + 2022.64; B: 13021.7 = 10919.5 + 2102.22; C: indirect ~7e-12 (zero). Budget moves B -> A (-45000 / +47500). Confirmed. Historical result has no `assumptions` key. |
| 51 | Future optimization records `assumptions.full_funnel` (the mediators and how they were predicted); single-model future results do not | full-funnel.md:80-82, consultation.md:143-146 | `run_future_optimization(geo-full-funnel, fixed_budget, start 2024-01-01, horizon 4)`; same on `national-revenue` | Full-funnel: `assumptions.full_funnel = {mediators: [M1, M2], mediator_treatment: "predicted_from_planned_spend"}`, rows carry the split. `national-revenue`: `assumptions` has no `full_funnel` key and no split columns. Confirmed. |
| 52 | Adstock figures are unchanged: the model's own carry-over parameters per channel, no brand-building path | full-funnel.md:93-94, channel-performance.md:78-80 | `get_adstock_decay(geo-full-funnel, alpha_summary)` | One `alpha` row per media channel (A, B, C) and per organic input (M1, M2); no full-funnel/indirect field. Confirmed by shape; the "does not include the brand path" reading follows the design (adstock is a stage-2 parameter). |
| 53 | A model with reach & frequency channels fails when a brand model lacks them; the analysis is unavailable, not zero | full-funnel.md:95-97 | not runnable | No full-funnel fixture has reach & frequency channels, so this was not exercised live. Source: the spec's Known limitations (Google's vendored `_map_new_data_to_mediator` crashes on `np.concatenate([])`, surfacing as `missing_model_data` on analysis tools and a failed run in optimization). Unverified live. |
| 54 | For reach & frequency channels the optimized split is scored at the optimizer's optimal frequency | full-funnel.md:79-80 | not runnable live (same missing fixture) | Verified at the unit level only: `tests/unit/test_full_funnel_optimizer_guard.py` asserts the optimized call receives the grid's `optimal_frequency` and the initial call `None`. Unverified live. |

## Corrections made in this PR

### 1. `channel-performance.md` — `get_contribution` does return a `baseline` row

**File:** `skills/meridian-analyst/references/channel-performance.md`, the
"Route the question" table row for "Base vs. incremental", and the
`get_contribution` paragraph immediately below it.

**What it said:** "The baseline ... is not a channel here — read it from
`get_channel_summary`'s baseline summary view. So 'base vs. incremental' is two
reads." This told the agent it must always make two separate tool calls to see
both halves.

**Failing verification:** `get_contribution(geo-revenue, "contribution_metrics")`
with no filters returns a row `["baseline", 1915.73, 0.285934]` in the same
table as the channel rows, and the value matches `get_channel_summary`'s
baseline `mean` exactly. Re-running with `include_non_paid: false` still
produces a `baseline` row (organic/non-media contributions fold into it
instead of getting their own rows) — `baseline` is unconditionally present.
This is the single most consequential finding in the audit: the old guidance
would have an agent make an unnecessary second tool call and could imply the
two numbers are unrelated when they are the same underlying quantity viewed
two ways.

**Fix:** Rewrote both spots to say `get_contribution` already returns the
`baseline` row by default in one call, and that `get_channel_summary`'s
baseline view is for when you specifically need the baseline's own credible
interval (mean/median/ci_lo/ci_hi) rather than just its point share. Kept the
marketer-friendly register — no new jargon introduced.

No other correction was needed. The specific things the brief flagged as
likely-stale — a configurable backend, `MERIDIAN_BACKEND`/
`OPTIMIZATION_BACKEND_*` env vars, `.pkl` models, and an optimization envelope
`backend` field — do not appear anywhere in `skills/meridian-analyst/**`
(confirmed by `grep -rniE "backend|\.pkl|MERIDIAN_BACKEND|OPTIMIZATION_BACKEND"
skills/`, zero hits), so there was nothing to correct on that front. The
skill never asserted a specific numeric example captured from 1.7 fixtures
either — all illustrative numbers in the docs are clearly hypothetical
("e.g. still earns at least 1.5x", "±20%"), not captured output, so the
"posterior changed" risk called out in the task did not materialize as a
correction here.

## Meridian 2.0's official skills

`references/` is gitignored in this repo (`.gitignore:22`) and not present in
this worktree; read instead from the local `google/meridian` clone checked out
alongside it at `references/meridian`,
at tag `v2.0.0` — `.agents/skills.json` and the five
`skills/meridian_*/SKILL.md` files. Meridian's own skills are a different genre from ours: **they guide an
agent writing and running Meridian *Python scripts* end-to-end** (data
loading, model spec, fitting, saving, visualizing, optimizing, scenario
export), whereas `meridian-analyst` guides an agent **using this MCP server's
tools** against an already-fitted model it does not build or touch directly.
There is no overlap in mechanism (script authoring vs. tool calls), but there
is useful overlap in *topic coverage*:

| Meridian's skill | Covers | Nearest analogue here |
| --- | --- | --- |
| `meridian_doc_consultant` | RAG-style lookup into Meridian's own docs to answer conceptual questions (knots, priors, data format) | `glossary.md` + `taxonomy.md` — but ours defines terms inline rather than pointing at external docs, since our tools are read-only and pre-fitted |
| `meridian_model_building` | Interactive, checkpoint-gated script generation to load data, configure `ModelSpec`, run EDA, fit, and save a model | **Out of scope for us entirely** — this server never builds or fits a model; it only serves one that already exists |
| `meridian_result_visualization` | Loads a fitted model and produces standard report artifacts (model fit/health check, results summary) via a generated script | `channel-performance.md`'s `get_model_fit`/`get_channel_summary` routing — same information, delivered as structured tool output instead of a rendered report |
| `meridian_budget_optimization` | Loads a fitted model and writes/runs a script that calls Meridian's `BudgetOptimizer` for allocation/target-ROI scenarios | `budget-optimization.md` — the closest analogue; both cover `fixed_budget`/`target_roas`/`target_mroas`-shaped scenarios, but theirs is "write the optimizer call," ours is "call `run_optimization`, poll, read the result" |
| `meridian_scenario_planner` | Generates Scenario Planner data and serializes it for a Colab/Looker Studio handoff (a downstream deliverable, not our concern) | No analogue — this server has no notion of exporting to Looker Studio |

All five of Meridian's skills share an "Interactivity Checkpoint Rule" —
mandatory structured multiple-choice check-ins at each step before proceeding,
enforced via a specific `ask_question`-style tool. Our `consultation.md`
covers similar ground (elicit → propose → confirm before a decision-heavy run)
but is calibrated to ambiguity/stakes rather than mandatory at every step,
and speaks in plain business language rather than presenting tool-call
choices to the user.

## Recommendations (NOT done here — out of scope)

- Consider a short "what this skill is not" note pointing at
  `meridian_model_building`/`meridian_result_visualization` for anyone who
  actually needs to fit or re-fit a model from raw data — this server and
  skill assume a model already exists.
- The `get_contribution` default-`baseline`-row behavior (correction #1 above)
  is also loosely documented in the tool's own docstring/description in
  `transport/tools.py` ("Get how much each media channel contributed to the
  KPI" — doesn't mention the baseline row). Worth a tool-description tweak in
  a future pass, independent of the skill text.
- `taxonomy.md`'s "Future optimization: two independent axes" framing
  (allocation vs. cost-structure) is accurate but was verified only
  structurally (the fields exist and behave as scoped); a follow-up could
  exercise `same_period_last_year` and `full_history_average` reference modes
  end-to-end (only `trailing` was run here) for full symmetry.
- Meridian's own skills lean on a mandatory structured checkpoint tool
  (`ask_question`); if this server's client surface ever exposes an
  equivalent, `consultation.md`'s calibrated (not-always-mandatory) gate could
  be revisited against that pattern — but that is a design decision, not an
  audit finding.

## Cannot verify

- The exact **content** of `get_optimization_status`'s `error` payload on a
  genuine `failed` run was not exercised (doing so deliberately would mean
  crafting a failing config, which risks producing noise indistinguishable
  from a real regression while another agent's `live_validate` run is
  in flight). The **presence** of the `error` field and the `failed`/`canceled`
  terminal states are confirmed structurally (schema + code), just not a live
  failure round-trip.
- `budget_source: "explicit"` and `"determined_by_target"` were confirmed to
  exist verbatim in `meridian/optimizer_facade.py`, but only
  `"derived_from_reference"` was produced by an actual run in this audit (the
  other two require, respectively, an explicit `budget` and a `target_roas`/
  `target_mroas` scenario, neither of which this audit's minimal runs used).
- `reference` modes `same_period_last_year` and `full_history_average` were
  confirmed to exist as valid enum literals in `domain/optimization.py` but
  only `trailing` was exercised through a live `run_future_optimization` call.

## Test / lint status

- `uv run pytest -q`: see task report.
- `uv run ruff check src scripts tests`: see task report.
- `uv run ruff format --check src scripts tests`: see task report.

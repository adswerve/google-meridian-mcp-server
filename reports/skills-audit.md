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

## Verified claims

| # | Claim | Source | Verifying call | Result |
| - | --- | --- | --- | --- |
| 1 | `list_models` → `get_model_overview` is the required first step; overview's `available_tool_options` lists legal tools/metrics for that model | SKILL.md:19-26 | `list_models`; `get_model_overview` on 7 models | `available_tool_options` present on every model, keyed by tool name, enumerating `output_type`/`channel`/`scenarios`/etc. Confirmed. |
| 2 | Overview tells you national vs. geo, and revenue-capability | SKILL.md:28-30, taxonomy.md:1-24 | `get_model_overview` | Overview carries `model_type` (`geo`/`national`), `is_national` (bool), `has_revenue_per_kpi` (bool). Confirmed. |
| 3 | Optimization is async: `run_optimization`/`run_future_optimization` return a `run_id`, not an answer | SKILL.md:34-37 | `run_optimization` submit on `national-revenue` | Submit returned `{"run_id", "status":"queued", "compute_tier_resolved", "meridian_version", "size_score", "reused"}` — no result fields. Confirmed. |
| 4 | Poll `get_optimization_status` until terminal; terminal = `failed`/`canceled` (`completed` also terminal) | SKILL.md:36-39, budget-optimization.md:77-82 | polled the above run | Observed sequence `queued` → `running` → `completed`. Confirmed. |
| 5 | Status carries a coarse phase + progress fraction while in-flight, an error object on `failed` | budget-optimization.md:80-82 | `get_optimization_status` | Status object keys: `run_id, status, phase, progress_fraction, heartbeat_at, started_at, finished_at, elapsed_seconds, compute_tier, meridian_version, error`. `phase`/`progress_fraction`/`error` all present as claimed. Did not force a real failure, so the *content* of `error` on a genuine failure is unverified (see "Cannot verify"). |
| 6 | The five run-id tools (`get_optimization_status`, `get_optimization_result`, `list_optimizations`, `cancel_optimization`, `delete_optimization`) manage historical and future runs identically | SKILL.md:38-41 | Ran both a historical and a future run through all five tools | Both run kinds used the same `run_id` namespace and the same five tools with no kind-specific branching visible from the outside. Confirmed. |
| 7 | Revenue-capable models optimize/report ROAS; KPI-only use CPIK | SKILL.md:48-52, taxonomy.md:13-24 | overview + `run_optimization` result `outcome_mode` on `geo-revenue` vs `geo-kpi-only` | `geo-revenue` → `outcome_mode: "revenue"`; `geo-kpi-only` → `"kpi"`. Confirmed. |
| 8 | `roi`/`marginal_roi` exist only for revenue-capable models; KPI-only → `metric_not_supported` | SKILL.md:53-55, taxonomy.md:30-41 | `get_channel_summary(geo-kpi-only, output_type="roi")` | `{"error_code":"metric_not_supported","message":"Metric 'roi' is not supported for model 'geo-kpi-only': model has no revenue_per_kpi; ROI metrics require revenue", ...}`. Confirmed. |
| 9 | `cpik`/`marginal_cpik` valid on every model | SKILL.md:55, taxonomy.md:31 | `get_channel_summary(geo-revenue, "cpik")` succeeded; `available_tool_options.get_channel_summary.output_type` includes `cpik`/`marginal_cpik` on every one of the 7 models | Confirmed. |
| 10 | `get_reach_frequency` is RF-only; absent from `available_tool_options` and `metric_not_supported` otherwise | SKILL.md:56-58, taxonomy.md:32,39-41 | overview on `geo-revenue-media-only` (no RF channels) omits `get_reach_frequency` from `available_tool_options`; direct call returns `metric_not_supported` (`"model has no reach & frequency channels"`) | Confirmed both halves. |
| 11 | RF gating is independent of the revenue axis — a KPI-only model can still have `get_reach_frequency` | taxonomy.md:39-41, channel-performance.md:87-91 | `geo-kpi-only` and `national-kpi-only` overviews both list `get_reach_frequency` (they have `rf_ch_0`/`rf_ch_1`) | Confirmed. |
| 12 | On a KPI-only RF model, `get_reach_frequency`'s `roi` column is still returned, in KPI units per spend | channel-performance.md:87-91 | `get_reach_frequency(geo-kpi-only)` | Returned `columns: [channel, frequency, roi, ci_lo, ci_hi, optimal_frequency]` with numeric `roi` values (e.g. 1.35 at freq 1.0) on a model whose `get_channel_summary` has no `roi` at all. Confirmed. |
| 13 | Analysis outputs carry `ci_lo`/`ci_hi`; `get_channel_data` (raw series) has no CI | SKILL.md:59-61, channel-performance.md:122-132 | `get_channel_data`, `get_model_fit`, `get_spend_scenario`, `get_response_curves(response_curve_summary)`, `get_channel_summary(baseline_summary_metrics)` | `get_channel_data` columns: `channel, channel_type, geo, time, impressions, spend, reach, frequency, rf_spend, value` — no CI. The other four all carry `ci_lo`/`ci_hi` (or `expected_ci_lo/hi`, `baseline_ci_lo/hi`). Confirmed. |
| 14 | `get_optimization_result` reports point estimates (means), not credible intervals | budget-optimization.md:93-95 | `run_optimization` result | `channel_tables`/`summary`/`allocation`/`spend_delta` contain only plain numeric fields — no `ci_lo`/`ci_hi` anywhere in the envelope. Confirmed. |
| 15 | Valid `scenario` types are exactly `fixed_budget`/`target_roas`/`target_mroas`; valid `constraint` modes exactly `global`/`per_channel` | budget-optimization.md:40-42 | `available_tool_options.run_optimization.scenarios` on all 7 models; tool schema (`OptimizationConfig`) | Every model lists exactly `["fixed_budget","target_roas","target_mroas"]`; `BaseOptimizationConfig`/constraint models in `domain/optimization.py` define exactly `Literal["global"]`/`Literal["per_channel"]`. Confirmed. |
| 16 | Identical `(model_id, config)` reuses a prior run unless `force_rerun=true`; a reused run may already be `completed` | budget-optimization.md:73-76 | Resubmitted the exact same `national-revenue` `fixed_budget` config | Second submit returned `"reused": true`, `"status": "completed"` immediately (same `run_id` as the first). Confirmed. |
| 17 | `get_optimization_result` fields: `outcome_mode`, `summary` (non/optimized budget, incremental outcome, efficiency), `channel_tables.initial/optimized` (spend, pct_of_spend, incremental_outcome, roi, mroi, cpik, effectiveness), `allocation`, `spend_delta` (cuts first, then increases), `response_curves` | budget-optimization.md:91-121 | `run_optimization` result on `national-revenue` | All fields present with exactly those names; `spend_delta` order observed as `[-15, -11, +13, +12, +1]` — cuts first. Confirmed. |
| 18 | `assumptions` (future runs only): `budget`, `budget_source` (`explicit`/`derived_from_reference`/`determined_by_target`), `reference_mode`, `excluded_channels` | budget-optimization.md:114-121, glossary.md:82-93 | `run_future_optimization` result on `national-revenue`, no `budget` given | `assumptions: {"budget": 25.49..., "budget_source": "derived_from_reference", "reference_mode": "trailing", "excluded_channels": []}`. All three `budget_source` literal strings also confirmed present verbatim in `meridian/optimizer_facade.py:264-271`. Confirmed (explicit/determined_by_target values confirmed at the source-code level, not separately round-tripped through a live call — see "Cannot verify"). |
| 19 | Historical `run_optimization` results have **no** `assumptions` field | budget-optimization.md:114 (implicit) | `run_optimization` result | Result keys: `run_id, outcome_mode, summary, channel_tables, allocation, spend_delta, response_curves` — no `assumptions`. Confirmed. |
| 20 | Future runs omit `response_curves` | budget-optimization.md:265-268 | `run_future_optimization` result | `"response_curves" in result` → `False`. Confirmed. |
| 21 | `cost_multipliers` values must be `>0`; `0` is rejected, not treated as free | budget-optimization.md:219-226 | `run_future_optimization` with `cost_multipliers: {"ch_0": 0}` | Pydantic validation error: `"Value error, weights must be > 0"`. Confirmed. |
| 22 | `planned_allocation` values must be `>0`; `0` rejected | budget-optimization.md:230-236 | `run_future_optimization` with `planned_allocation: {"ch_0": 0}` | Same `"weights must be > 0"` error. Confirmed. |
| 23 | `excluded_channels`: forces spend to 0, channel still appears in the result with spend 0, budget reallocated (total unchanged) | budget-optimization.md:237-247, glossary.md:76-80 | `run_future_optimization` with `excluded_channels: ["ch_0"]` | `assumptions.excluded_channels: ["ch_0"]`; `channel_tables.optimized` still has a `ch_0` row with `spend: 0.0, roi: null, mroi: null`; `summary.optimized_budget` unchanged from the run without exclusion. Confirmed. |
| 24 | Omitted `budget` in a future `fixed_budget` run defaults to the `reference` window's carried-forward spend, not the full historical total | budget-optimization.md:248-256 | future run above | `assumptions.budget = 25.49` (a 4-period trailing total, not `national-revenue`'s 320 full-history total from the historical run). Confirmed distinct from the historical-run default. |
| 25 | `get_training_data` merges named datasets into one table | budget-optimization.md (routing), SKILL.md, tool docstring | `get_training_data(geo-revenue, ["kpi","media_spend"])` | Returned one merged table, columns `[geo, time, media_channel, kpi, media_spend]`. Confirmed. |
| 26 | `get_model_fit` fields: `expected`/`actual`/`baseline`/`residual`, CIs on `expected` and `baseline`, no fit-score field | channel-performance.md:110-120 | `get_model_fit(geo-revenue)` | Columns: `time, expected, expected_ci_lo, expected_ci_hi, actual, baseline, baseline_ci_lo, baseline_ci_hi, residual`. No R²/score field. Confirmed. |
| 27 | `get_model_fit` with a `geos` filter aggregates to one national series, no per-geo breakdown | channel-performance.md:116-117 | `get_model_fit(geo-revenue, filters={"geos":[one geo]})` | `row_count == 52` (one row per time period, not per geo × time). Confirmed. |
| 28 | `get_channel_summary`'s paid summary view also carries per-channel KPI lift (`incremental_outcome`) | channel-performance.md:47-49 | `get_channel_summary(geo-revenue, "paid_summary_metrics")` | Columns include `incremental_outcome`, `pct_of_contribution` alongside spend/impressions. Confirmed. |
| 29 | `get_spend_scenario` returns the efficiency triplet `efficiency`/`marginal_efficiency`/`efficiency_at_new`, `outcome_mode` decides direction | channel-performance.md:93-108, budget-optimization.md | `get_spend_scenario(geo-revenue, "ch_0", 100)` and same on `geo-kpi-only` | Both calls returned exactly those three fields plus `outcome_mode` (`"revenue"` / `"kpi"`) and `base_outcome`/`new_outcome` with `ci_lo`/`ci_hi`. Confirmed. |
| 30 | No tool returns `saturated: true`; saturation is read off `get_response_curves` + marginal ROI | budget-optimization.md:58-66, channel-performance.md:70-79 | `get_response_curves(response_curve_summary)`, `get_channel_summary` | Neither response carries any boolean/flag field; only numeric spend/outcome/ROI series. Confirmed (absence). |
| 31 | `cancel_optimization` is best-effort; has no effect on an already-`completed`/`failed` run | budget-optimization.md:87-89 | `cancel_optimization` on a completed run, then re-checked status | `cancel_optimization` returned `{"status":"canceled"}` in its own reply, but `get_optimization_status` immediately after still reported `"completed"` — the stored run was unaffected. Confirmed. |
| 32 | `delete_optimization` permanently removes a run; irreversible | budget-optimization.md:87-89 | `delete_optimization` then `get_optimization_result` | Delete returned `{"deleted": true}`; subsequent `get_optimization_result` → `{"error_code":"optimization_run_not_found", ...}`. Confirmed. |
| 33 | `list_optimizations` shows config summary, status, headline result | budget-optimization.md:87-89 | `list_optimizations(model_id="national-revenue")` | Row carried `run_id, label, model_id, config_summary, status, created_at, finished_at, headline` (e.g. `"ROAS 3.53625 -> 5.01456 at budget 320.0"`). Confirmed. |
| 34 | There is no "move $ from A to B" tool; model it as two `get_spend_scenario` calls or a `per_channel` constraint | budget-optimization.md:44-56, channel-performance.md:105-108 | Tool inventory (`transport/tools.py`) | No such tool exists among the 15 registered tools. Confirmed (by exhaustive absence). |
| 35 | `get_contribution` default output includes a `baseline` row (organic + non-media folded in) | channel-performance.md:52-61 (**pre-correction text claimed the opposite**) | `get_contribution(geo-revenue, "contribution_metrics")` with default filters, and again with `include_non_paid: false` | Both calls returned a row `["baseline", 1915.73, 0.2859]` / `["baseline", 2991.98, 0.4466]` respectively — `baseline` is always a row in this output, and its value with `include_non_paid=true` matches `get_channel_summary`'s baseline `mean` exactly (1915.73). **This contradicted the skill's original claim — see Corrections.** |

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

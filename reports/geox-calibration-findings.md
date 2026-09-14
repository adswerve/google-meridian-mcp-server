# GeoX / Channel Calibration Research Spike (Task 23)

**Scope:** findings only. No production code, no tool surface. This is Phase 6c of the
Meridian 2.0 upgrade — establishing what `meridian/analysis/review/` and `meridian-geox`
make possible, on real Meridian 2.0.0 APIs run against a refit fixture, so a later plan
can be written from facts.

**Fixture used:** `models/_validation/geo-revenue/model.binpb` — 3 media channels
(`ch_0`, `ch_1`, `ch_2`) + 2 RF channels (`rf_ch_0`, `rf_ch_1`), `revenue_per_kpi` set,
converged. Loaded exactly as every other spike step: `meridian_serde.load_meridian(...)`.

---

## 1. What the API is

### `results.build_calibration_recommendation_text`

Verbatim signature:

```python
(recommended_channels: collections.abc.Sequence[str] | None = None, driver_issues_by_channel: collections.abc.Mapping[str, collections.abc.Sequence[str]] | None = None, location: str = 'channel_recommendation', calibration_score: float | None = None) -> str
```

Verbatim docstring:

```
Constructs the calibration recommendation message for a given location.

Args:
  recommended_channels: List of uncalibrated channel names with score below
    threshold.
  driver_issues_by_channel: Mapping of uncalibrated channel names to their
    flagged driver issue names.
  location: One of constants.CALIBRATION_TEXT_METRICS_CHECK,
    constants.CALIBRATION_TEXT_CALIBRATION_SUMMARY, or
    constants.CALIBRATION_TEXT_CHANNEL_RECOMMENDATION.
  calibration_score: Overall calibration score (used for metrics_check).

Returns:
  The formatted recommendation string for the user.
```

Verbatim `constants.HIGH_VARIANCE_ROI_RECOMMENDATION`:

```
We recommend calibrating these channels using an incrementality experiment to reduce posterior ROI uncertainty.
```

Both ship in **core** Meridian (`meridian.analysis.review`, no extra required).
`meridian-geox` is consumed by exactly one file in the whole `meridian` package:
`meridian/model/calibration/adapters/meridian_geox.py` — the pre-fit,
prior-building path this server does not use (see §3, §5).

### The rest of `meridian.analysis.review`

`dir(review)` → `['checks', 'configs', 'constants', 'plots', 'results', 'reviewer']`
`dir(reviewer)` → the module exposes exactly one public class: `ModelReviewer`
(everything else in its `dir()` — `checks`, `configs`, `az`, `np`, etc. — is an
imported submodule/library, not part of the intended API).

**`reviewer.ModelReviewer`** is the orchestrator. Constructed with
`model_context=mmm.model_context, inference_data=mmm.inference_data` (both come
straight off a loaded `meridian.model.model.Meridian` object — no experiment data,
no geo assignment). `.run(*, selected_geos=None, selected_times=None)` executes a
battery of checks and returns a `results.ReviewSummary`. It runs a `ConvergenceCheck`
first; if the model didn't converge, everything else is skipped.

**9 checks total, enumerated from `checks.py` (all in core, all run purely on a
fitted model — no experiment/geo-lift inputs needed by any of them):**

| Check | Question it answers | Needs custom ROI prior? | Needs `revenue_per_kpi`? |
|---|---|---|---|
| `ConvergenceCheck` | Did MCMC converge (max R-hat)? | no | no |
| `BaselineCheck` | Is posterior probability of negative baseline too high? | no | no |
| `BayesianPPPCheck` | Is total observed outcome consistent with the posterior predictive? | no | no |
| `GoodnessOfFitCheck` | R², MAPE, wMAPE (train/test if holdout used) | no | no |
| `PriorPosteriorShiftCheck` | Did any channel's ROI posterior fail to move away from its prior (weak signal)? | yes (ROI prior type) | no |
| `ROIConsistencyCheck` | Does posterior ROI mean fall in the tail of a **custom** prior? | yes, and only relevant if the prior differs from Meridian's default | no |
| `ImplausibleROICheck` | Is spend-weighted ROI outside a plausible band (0.5–20)? | yes | **yes** (skipped otherwise) |
| `HighVarianceCheck` | Is the posterior ROI credible interval too wide relative to the prior, spend-weighted? | yes | **yes** |
| `PotentialBiasCheck` | Is a channel's spend poorly correlated with all control variables (confounding risk)? | no | **yes** (via the same calibration-checks gate) |

The three calibration-relevant checks (`ImplausibleROICheck`, `HighVarianceCheck`,
`PotentialBiasCheck`) are gated off (`_should_skip_calibration_checks`) when the model
has no media/RF channels, when `revenue_per_kpi is None`, or when the inference data
has no posterior — never on the presence of experiment data. Our fixture has
`revenue_per_kpi` set, so all three ran.

`ReviewSummary` (the return type) exposes, per channel, a **composite calibration
score** (0–100, geometric/log-scaled blend of the three calibration checks — see
`_normalized_center_bowl`, `_normalized_half_bowl`, `_potential_bias_score` in
`results.py`), a boolean `channel_calibration_status` (true only if the channel's ROI
prior is already a `calibration_base.CalibratedDistribution` — i.e. was already built
via the pre-fit calibration path), and the derived properties that directly answer
"which channels should be geo-lift-tested":

- `channels_recommended_for_calibration` — uncalibrated channels scoring below
  `constants.CALIBRATION_SCORE_THRESHOLD = 67.5`.
- `channel_calibration_recommendations` — per-channel dict with score + which of the
  four driver checks (high ROI / low ROI / high variance / potential bias) fired.
- `has_calibration_warning`, `calibration_score` (model-level average).

---

## 2. What it returns, run against the refit fixture (verbatim)

Console command:
```python
from meridian.schema.serde import meridian_serde
from meridian.analysis.review import reviewer, results

mmm = meridian_serde.load_meridian('models/_validation/geo-revenue/model.binpb')
rev = reviewer.ModelReviewer(model_context=mmm.model_context, inference_data=mmm.inference_data)
summary = rev.run()
```

It ran to completion, no errors, on an unmodified fixture from the exact loading path
every other spike step uses (`meridian_serde.load_meridian` → `Analyzer`/`ModelReviewer`
constructed from `model_context` + `inference_data`, no experiment/geo inputs).

`repr(summary)`:

```
========================================
Model Quality Checks
========================================
Overall Status: PASS
Summary: Passed: No major quality issues were identified.
Health Score: 100.0

Check Results:
----------------------------------------
Convergence Check:
  Status: PASS
  Recommendation: The model has likely converged, as all parameters have R-hat values < 1.2.
----------------------------------------
Baseline Check:
  Status: PASS
  Recommendation: The posterior probability that the baseline is negative is 0.10. We recommend visually inspecting the baseline time series in the Model Fit charts to confirm this.
----------------------------------------
BayesianPPP Check:
  Status: PASS
  Recommendation: The Bayesian posterior predictive p-value is 0.30. The observed total outcome is consistent with the model's posterior predictive distribution.
----------------------------------------
GoodnessOfFit Check:
  Status: PASS
  Recommendation: R-squared = 0.9435, MAPE = 0.0537, and wMAPE = 0.0523. These goodness-of-fit metrics are intended for guidance and relative comparison.
----------------------------------------
PriorPosteriorShift Check:
  Status: PASS
  Recommendation: The model has successfully learned from the data. This is a positive sign that your data was informative.

===================================================================================================================
Channel Calibration Recommendation
===================================================================================================================
Channel              | Calibration Score  | High ROI        | Low ROI         | High Variance ROI | Potential Bias
-------------------------------------------------------------------------------------------------------------------
ch_0                 | 95.1               | Non-Driver      | Non-Driver      | Non-Driver        | Non-Driver
ch_1                 | 95.1               | Non-Driver      | Non-Driver      | Non-Driver        | Non-Driver
ch_2                 | 95.6               | Non-Driver      | Non-Driver      | Non-Driver        | Non-Driver
rf_ch_0              | 89.6               | Non-Driver      | Non-Driver      | Non-Driver        | Non-Driver
rf_ch_1              | 87.9               | Non-Driver      | Non-Driver      | Non-Driver        | Non-Driver
-------------------------------------------------------------------------------------------------------------------
```

`build_calibration_recommendation_text` called with this fixture's *real* recommendation
data (`summary.channels_recommended_for_calibration`, `summary._uncalibrated_channels_with_driver_issues()`,
`summary.calibration_score`) at all three `location` values:

```
--- location=metrics_check ---
The overall calibration score is 92.7/100. No channels require calibration.

--- location=calibration_summary ---
No channels require calibration.

--- location=channel_recommendation ---
No channels require calibration. We recommend reviewing the table and plots below to check for channels near the boundaries that may be good candidates for calibration via an incrementality experiment such as those run with Meridian GeoX.
```

This fixture is a *healthy* model — nothing crosses a threshold, so the "flagged
channel" branch of the function was never exercised by real data. To confirm that
branch actually produces something useful, the same real function was called again
with the raw signature it needs (`recommended_channels`, `driver_issues_by_channel`) but
synthetic values standing in for what a flagged model would produce:

```python
results.build_calibration_recommendation_text(
    recommended_channels=['rf_ch_1', 'ch_2'],
    driver_issues_by_channel={'rf_ch_1': ['high_variance'], 'ch_2': ['high_roi', 'potential_bias']},
    location=results.constants.CALIBRATION_TEXT_CHANNEL_RECOMMENDATION,
)
```
```
We recommend incrementality experiments to improve prior accuracy for 'rf_ch_1' and 'ch_2': 'rf_ch_1' shows issues with high_variance and 'ch_2' shows issues with high_roi and potential_bias. See Channel calibration recommendation below for more details.
```
```python
results.build_calibration_recommendation_text(
    recommended_channels=['rf_ch_1', 'ch_2'],
    driver_issues_by_channel={'rf_ch_1': ['high_variance'], 'ch_2': ['high_roi', 'potential_bias']},
    location=results.constants.CALIBRATION_TEXT_METRICS_CHECK,
    calibration_score=61.4,
)
```
```
The overall calibration score is 61.4/100. We recommend incrementality experiments to improve prior accuracy for 'rf_ch_1' and 'ch_2'.
```
(These two calls are marked synthetic input, real function — the text itself came out
of the real `build_calibration_recommendation_text`, not a mock.)

Real per-channel numbers from the fixture's three calibration checks (spend share,
posterior ROI mean, relative credible-interval width, max control correlation —
these are exactly the numbers `HighVarianceCheck`/`ImplausibleROICheck`/`PotentialBiasCheck`
compute and that would back a tool response):

```
ImplausibleROICheckResult -> case: PASS
  ch_0     spend_share=0.212  roi_mean=2.831  spend_weighted_roi=0.600
  ch_1     spend_share=0.199  roi_mean=5.015  spend_weighted_roi=0.996
  ch_2     spend_share=0.184  roi_mean=2.978  spend_weighted_roi=0.549
  rf_ch_0  spend_share=0.196  roi_mean=0.855  spend_weighted_roi=0.168
  rf_ch_1  spend_share=0.209  roi_mean=0.992  spend_weighted_roi=0.207

HighVarianceCheckResult -> case: PASS
  ch_0     spend_share=0.212  relative_width_ratio=0.734
  ch_1     spend_share=0.199  relative_width_ratio=0.620
  ch_2     spend_share=0.184  relative_width_ratio=1.335   <- above 1.0 unweighted, but
                                                                spend_share * ratio = 0.246,
                                                                below the 1.0 REVIEW threshold
  rf_ch_0  spend_share=0.196  relative_width_ratio=0.606
  rf_ch_1  spend_share=0.209  relative_width_ratio=0.664

PotentialBiasCheckResult -> case: PASS
  ch_0     max_abs_correlation=0.484
  ch_1     max_abs_correlation=0.531
  ch_2     max_abs_correlation=0.529
  rf_ch_0  max_abs_correlation=0.423
  rf_ch_1  max_abs_correlation=0.266
```
Note `ch_2`'s raw relative-width ratio (1.335) exceeds the config's `high_variance_threshold`
default of 1.0, but the check flags on the **spend-weighted** ratio (`ratio * spend_share`
= 0.246), which stays under threshold — this is why the fixture reads clean even though
one channel's raw uncertainty looks high. That weighting detail matters for anyone
designing a tool response around this: a raw ratio alone is misleading; the tool should
surface the spend-weighted figure the check actually gates on.

---

## 3. What `meridian-geox` adds — and whether we can use it

`meridian_geox` (`1.0.1`, installed) is a **standalone geo-experiment toolkit**, not an
extension of the review module. `dir(meridian_geox.api)` (imported live, in-process):

```
['AnalysisConfig', 'AnalysisMetrics', 'AnalysisResult', 'Budget', 'Constraints',
 'DesignConfig', 'DesignSet', 'GeoAssignmentRule', 'GeoGroup', 'Methodology',
 'PerCellDesign', 'QualityCheckConfig', 'QualityCheckResult', 'TestType', ... ]
```

This is a design-and-analyze toolkit for actual geo-holdout experiments: `Design`/
`DesignConfig`/`GeoAssignmentRule` to split geos into test/control groups and pick a
budget, and `AnalysisConfig`/`AnalysisResult`/`AnalysisMetrics` to analyze the raw
experiment results (with an iCPD — incremental-cost-per-... — point estimate and an
`estimated_bau_spend`) once the experiment has run. None of it touches a fitted Meridian
model.

Its single integration point into `meridian` core is
`meridian/model/calibration/adapters/meridian_geox.py::resolve_meridian_geox_source`,
which takes a `geox_api.AnalysisResult` (the completed experiment's analysis, not
anything derivable from a fitted model) plus `kpi_type`/`revenue_per_kpi`, and converts
it into a `calibration_base.CalibrationData` object — which then feeds
`meridian/model/calibration/` (the pre-fit `prior_builder.py` path) to construct a
`CalibratedDistribution` **prior**, before `sample_posterior` is ever called.

**Verdict: `meridian-geox` requires inputs this server never receives** — raw geo-level
panel data to design a split, and a completed real-world experiment's analysis result
to calibrate from. A fitted `.binpb` model contains neither. There is no code path from
"load a fitted model" to "call anything in `meridian_geox.api`" — it is not gated behind
missing config, it is architecturally a different pipeline stage. This is why the task
brief said the `geox` extra is pinned "by explicit instruction, for the intended later
tool surface" rather than because this spike needs it: confirmed, it doesn't.

---

## 4. Does the review module answer "which channels should I geo-lift-test"?

**Yes, directly**, via `ReviewSummary.channels_recommended_for_calibration` /
`channel_calibration_recommendations` / `has_calibration_warning`, all computed purely
from a fitted model's posterior, its priors, and its input data — no experiment data
required. This is a materially different (and better) answer than "does the server
have geo-lift data" (no) — the review module instead flags candidates for a *future*
geo-lift test by looking at where the existing fitted model's ROI estimates already
look implausible, too wide, or confounded with controls. That is exactly the
recommendation surface a marketer needs before deciding whether to run an experiment
at all — it prioritizes which channels are worth testing, using nothing but a model
that's already been fit.

The caveat: the composite calibration score conflates "already calibrated" (has a
`CalibratedDistribution` prior — never true for this server's fitted models, since we
don't build priors via `meridian/model/calibration/`) with "recommendation reasoning."
For every model this server will ever load, `channel_calibration_status` will be
`{channel: False}` for all channels and `calibrated_channel_names` will be `[]` — the
"already calibrated, skip it" branch of the UI/report logic is simply dead code for us,
which is fine (it degrades gracefully to "show me every channel's score"), but worth
recording so nobody is surprised a future tool never reports a channel as calibrated.

---

## 5. What a read-only tool surface would look like (sketch — not implemented)

Following the server's existing pattern (`AnalysisService` in
`src/google_meridian_mcp_server/services/analysis_service.py` validates + caches,
routes through the subprocess `runner` to a worker-side op in
`src/google_meridian_mcp_server/execution/analysis_ops.py` that holds the actual
Meridian facade/`Analyzer` call):

- **Tool name:** `get_calibration_review`
- **Arguments:** `model_id: str` (existing pattern), optional `selected_geos: list[str]`,
  optional `selected_times: list[str]` (both already accepted by `ModelReviewer.run`).
- **Worker-side implementation:** construct `reviewer.ModelReviewer(model_context=...,
  inference_data=...)` from the already-loaded `Meridian` object (same object every
  other analysis op already has in hand — no new load path), call `.run()`.
- **Response shape (marketer language, not the raw dataclass):**
  - `overall_status` / `summary_message` (already marketer-worded by Meridian, e.g.
    "Passed: No major quality issues were identified.")
  - `health_score` (0–100)
  - `channels`: list of `{channel_name, calibration_score, flags: {high_roi, low_roi,
    high_variance, potential_bias} (booleans, not internal enum names)}`
  - `recommended_for_testing`: list of channel names below the calibration threshold —
    this is the direct "who should I geo-lift-test" answer
  - `recommendation_text`: the `build_calibration_recommendation_text` output at
    `CALIBRATION_TEXT_CHANNEL_RECOMMENDATION`, already in prose a marketer can read
    without translation
  - Explicitly omit: `channel_calibration_status`/`calibrated_channel_names` as
    surfaced fields (always empty/False for us — see §4), the Altair `*_chart_json`
    blobs (presentation-layer HTML/JS, not a data payload an MCP tool should return),
    and `correlation_matrix` (geo × channel × control, useful internally but not
    something to serialize whole to a marketer).
- Second candidate, smaller: **`get_model_health`** — a thin cut of the same call
  surfacing only `overall_status`, `summary_message`, `health_score`, and the five
  non-calibration checks (convergence/baseline/BayesianPPP/goodness-of-fit/
  prior-posterior-shift) in marketer language, as a "is this model trustworthy" tool
  distinct from the calibration-recommendation one. Genuinely useful on its own —
  several teams may want "is my model healthy" without the calibration framing at all.
- Everything under `meridian_geox.api` (`Design`, `AnalysisResult`, etc.): **not a
  candidate** — no inputs from a fitted model can populate it (see §3).

---

## 6. Open questions for a future design

1. **Naming for marketers.** "Calibration score," "high variance ROI," "potential
   bias" are still fairly technical. The repo's marketer-friendly-language rule will
   need real rewording work here (e.g. "how confident are we in this channel's ROI"
   rather than "high variance ROI") — Meridian's own `recommendation` strings are
   already close but not fully there (they say things like "posterior distribution"
   and "R-hat").
2. **Threshold configurability.** `CALIBRATION_SCORE_THRESHOLD` (67.5),
   `high_variance_threshold` (1.0), `roi_upper_bound`/`roi_lower_bound` (20.0/0.5),
   `correlation_threshold` (0.1) are all Meridian defaults. Should a future tool accept
   overrides (`configs.ImplausibleROIConfig(...)` etc. are all constructible), or pin
   defaults for consistency across models? `ModelReviewer.__init__` accepts a full
   custom `post_convergence_checks` battery, so this is available if wanted.
3. **What "recommended for calibration" means without any calibrated channels.**
   Since no model we load will ever have a `CalibratedDistribution` prior, is
   `channels_recommended_for_calibration` (score-based) the right primary signal, or
   should a future tool instead surface the three raw per-channel checks directly and
   let the marketer/analyst judge without the composite score's specific weights
   (`CALIBRATION_IMPLAUSIBLE_ROI_WEIGHT` etc. — not inspected in this spike)?
4. **Caching/perf.** `ModelReviewer.run()` calls `az.hdi`, bootstrap resampling
   (`PriorPosteriorShiftCheck`, default 1000 bootstraps), and Pearson correlation over
   the full geo×time×channel×control tensor. Not measured here, but likely
   non-trivial for larger models — should this be cached the way `get_channel_summary`
   etc. already are (`ResultCache` in `analysis_service.py`)?
5. **`selected_geos`/`selected_times` filtering.** `ModelReviewer.run` accepts these
   directly, matching the filter pattern this server's other tools already use
   (`AnalysisFilters`) — worth confirming during actual tool design whether per-geo
   calibration review is something marketers would ask for, or whether the aggregate
   view is enough.
6. **`meridian_geox` install footprint.** Confirmed a live import
   (`import meridian_geox; from meridian_geox import api`) succeeds with the `[geox]`
   extra installed and costs nothing at import time — no experiment/geo data needed
   just to have the package present. So keeping the extra pinned per the plan's
   instruction is low-cost even though nothing in this spike calls it.

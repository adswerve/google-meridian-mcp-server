# Deferred research from the Meridian 2.0 upgrade

This is a synthesis, not a source. It exists so that whoever plans the *next*
round of work (GeoX/calibration tooling, weekly-grid optimization, or anything
adjacent) can start from facts already gathered, instead of re-deriving them.
Every number and claim below traces to a committed report under `reports/` —
follow the pointers for the real evidence; nothing here is re-derived or
estimated independently.

This file lives in `references/`, which is gitignored in this repository
(`.gitignore:22`) except for `skills/meridian-analyst/references/`. That means
this file itself is **local, uncommitted scratch material** — it will not
survive a fresh clone or `git worktree add`. The durable record of this
research is the reports it points to. Re-run this synthesis (or regenerate
these files) if you're working from a fresh checkout and they aren't here.

Both research spikes below were deliberately scoped to *findings only* — no
production code, no new tools, no default changed. Neither is a decision.

---

## 1. GeoX / channel calibration

**Full detail:** `reports/geox-calibration-findings.md` (Task 23, Phase 6c).

### The counter-intuitive headline

The useful entry point for this server is **`meridian.analysis.review`**
(specifically its `ModelReviewer` / `ReviewSummary`), which operates
*post-fit*, on a model this server already has loaded. It is **not**
`meridian/model/calibration/` — that package builds *priors*, pre-fit, before
`sample_posterior` is ever called, and this server never touches that stage
of the pipeline. Anyone starting this research fresh would naturally reach
for the calibration package by name; the report exists partly to redirect
that instinct.

### What `ModelReviewer` gives you

- Ships in **core** Meridian — no `meridian_geox` extra required, no
  experiment data needed.
- Constructed from `model_context` + `inference_data` off an already-loaded
  `Meridian` object — the same object every other analysis op in this server
  already holds.
- Runs **9 checks** (convergence, baseline, Bayesian PPP, goodness-of-fit,
  prior-posterior shift, plus three calibration-relevant checks — implausible
  ROI, high-variance ROI, potential bias — gated on `revenue_per_kpi` being
  set, not on experiment data being present).
- `ReviewSummary.channels_recommended_for_calibration` is a direct,
  actionable answer to "which channels should I geo-lift-test" — computed
  purely from the fitted model's posterior and priors. This is the single
  most useful finding of the spike: it reframes the question from "does this
  server have geo-lift data" (no) to "does the existing fitted model's own
  posterior already tell us where a lift test would pay off" (yes).
- Caveat worth carrying forward: the composite score conflates "already
  calibrated via `meridian/model/calibration/`" with "recommended for
  calibration." Every model this server will ever load will show
  `channel_calibration_status: False` for all channels, since we never build
  priors that way — that branch of Meridian's own logic is permanently dead
  code for us, and degrades gracefully, but a future tool should not surface
  `channel_calibration_status` as if it means something for our models.

### `meridian-geox` was ruled out — and the reasoning generalizes

`meridian_geox` is a standalone geo-experiment design-and-analysis toolkit
(splitting geos into test/control, picking a budget, analyzing a completed
experiment's raw results). Its **only** integration point into Meridian core
is one adapter (`meridian/model/calibration/adapters/meridian_geox.py`) that
converts a *completed experiment's analysis result* into a *prior*, before
fitting. A fitted `.binpb` model — everything this server ever loads —
contains neither of the inputs `meridian_geox` needs (raw geo panel data, or
a finished experiment's analysis). There is no code path from "load a fitted
model" to "call anything in `meridian_geox.api`." The general lesson: a
capability that requires inputs this server structurally never receives is
not a future tool candidate, no matter how relevant it sounds by name — worth
remembering the next time an adjacent Meridian package looks promising.

### Two tool candidates sketched (not built)

- **`get_calibration_review`** — full `ModelReviewer.run()` output, reshaped
  into marketer language: `overall_status`, `health_score`, per-channel
  `{calibration_score, flags}`, `recommended_for_testing`, and the
  already-prose `recommendation_text` from
  `results.build_calibration_recommendation_text`. Deliberately omits the
  dead-for-us calibration-status fields, the Altair chart blobs, and the raw
  correlation matrix.
- **`get_model_health`** — a smaller cut: just the five non-calibration
  checks, as a standalone "is this model trustworthy" tool distinct from the
  calibration-recommendation framing.

Open questions the spike flagged for a future design: marketer-friendly
rewording of terms like "high variance ROI" and "potential bias"; whether
threshold config (e.g. `CALIBRATION_SCORE_THRESHOLD = 67.5`) should be
overridable; whether the composite score or the three raw per-channel checks
should be the primary signal, given no model will ever have a calibrated
channel; caching/perf of `ModelReviewer.run()` (not measured); and whether
`selected_geos`/`selected_times` filtering (which `ModelReviewer.run` already
accepts) is something marketers would actually ask for.

---

## 2. `WeeklyOptimizationGrid`

**Full detail:** `reports/weekly-optimization-grid-measurements.md` (Task 26,
spec §11.5).

### Correction to the original premise

The premise going in was that a cached weekly grid could serve any
date-window optimization at effectively the classic path's accuracy. That is
**not what the measurements found**. `weekly_optimization_grid.py` fills its
fine-grained spend grid via `np.interp` — **linear** interpolation between
coarser evaluated knots — over response curves that are **concave**
(saturating). A chord across a concave curve lies below it, so the
interpolated outcome is **systematically under-estimated** between knots.
This is a biased approximation with a known direction, not "the same answer,
cheaper." The one non-trivial measured error (on the `geo-revenue` fixture,
at `multiplier_step=0.05`) came out negative, exactly matching that
prediction. The error shrank to zero/float-precision at finer steps
(0.01, 0.005) on both fixtures tested — but that threshold is
**fixture-scale-dependent**, not proven universal; only two tiny fixtures and
two step values were tested.

### The trade-off, measured

- Slicing a cached grid (`to_optimization_grid`, ~2 ms) really is roughly
  three orders of magnitude cheaper than building one (~1.6–1.9 s on these
  fixtures) — that ratio is the number that justifies build-once/slice-many
  in principle.
- **But two findings weaken the case for adopting it as-is:**
  1. `BudgetOptimizer.optimize()` after the slice is **not free** — it still
     ran 0.3–1.7 s per call in this spike, and on both fixtures a sub-window
     optimize cost *more* than the full-span classic optimize. Slicing
     removes the `create()` cost per call, not the `optimize()` cost — so the
     "grid removes the need for GPU/heavy compute" hypothesis is weakened,
     not confirmed, by these numbers.
  2. **`selected_geos=None` is a hard, permanent gap**, not a tuning
     question. Neither `WeeklyOptimizationGrid.create()` nor
     `to_optimization_grid` accepts a geo argument anywhere in the API —
     confirmed by introspection, not inference. A grid built this way
     structurally cannot answer any geo-filtered optimization question. This
     server's existing tools *do* accept geo filters today. Any adoption of
     the weekly path would need an explicit, reported fallback to the
     classic path whenever a caller requests `selected_geos`.

Other confirmed hard constraints: scenario bounds (`max_budget_percent_decrease`
etc.) are baked in at `create()` time, and a `to_optimization_grid` request
outside those bounds returns `None` silently (only a `UserWarning`, never a
raised exception) — any caller must explicitly check for `None`.

### The spike's own verdict

Its own recommendation: **feed this into a Phase 8 plan; not worth building
a tool surface yet.** It self-flags its weaknesses — toy validation fixtures
(1 and 5 geos, not production scale), only one case that actually produced a
nonzero interpolation error, and GPU cost left **completely unmeasured** (the
spike's brief ruled out provisioning GPU infrastructure just to get one
number). No infrastructure sizing decision — GPU vs. CPU, retire vs. keep —
should be drawn from this report.

---

## 3. Explicitly out of scope — no research done

Listed here only so nobody assumes these were considered and rejected. They
were never investigated in this upgrade at all:

- **Full-funnel / brand-equity modelling.**
- **A GeoX tool surface** beyond the calibration-review sketch above (i.e.
  actually exposing geo-experiment design/analysis, which §1 found requires
  inputs this server doesn't have — that finding is the entirety of what
  exists on this topic; nothing about a broader GeoX surface was explored).

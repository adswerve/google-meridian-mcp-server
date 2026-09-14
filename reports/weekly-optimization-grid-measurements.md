# `WeeklyOptimizationGrid` Measurement Spike (Task 26, Spec §11.5)

**Scope:** findings only. No production code, no tool surface, no default change.
Phase 8 (§11.6) gets its own plan once these numbers exist; nothing here is a
decision.

**Environment:** Meridian 2.0.0, `MERIDIAN_BACKEND=jax`,
`MERIDIAN_ENABLE_JAX_X64=true`, Python 3.13.13, CPU only (see §4/Q2 on GPU).

**Fixtures used** (`models/_validation/*/model.binpb`, refit on 2.0):

| Fixture | Geos | Weeks | Media channels | RF channels |
| --- | --- | --- | --- | --- |
| `national-revenue` | 1 | 52 | 3 | 2 |
| `geo-revenue` | 5 | 52 | 3 | 2 |

**These are the repository's tiny validation fixtures, not production-scale
models.** Every timing/memory number below is dominated by fixed process
overhead (JAX/TF import, MCMC-draw bookkeeping) at this scale — see the
caveat in §9. The script that produced every number below is
`scripts/spikes/weekly_grid_spike.py`; raw JSON is at
`/tmp/weekly-grid-national-revenue.json` and
`/tmp/weekly-grid-geo-revenue.json` (not committed — ephemeral spike output).

---

## 1. What it is

`WeeklyOptimizationGrid.create(analyzer, ...)` takes an `Analyzer` (not a
`Meridian` model) and pre-computes incremental outcomes at shape
`(n_channels, n_spend_multipliers, n_times)` — it retains the time dimension,
so the same grid can serve any date window later. `to_optimization_grid(
start_date, end_date, budget, spend_constraint_lower, spend_constraint_upper)`
slices that cube down to a window, sums the retained time dimension, and
returns an ordinary `optimizer.OptimizationGrid` — the same object the
existing `BudgetOptimizer.optimize(optimization_grid=...)` already accepts
(that signature is unchanged from 1.7.0). `combine()` concatenates grids by
date. The expensive posterior work — the part that scales with MCMC draws
and channels — happens once, in `create()`; everything after that is array
slicing and lookup.

---

## 2. It is an approximation with a direction

`weekly_optimization_grid.py:810-813` builds the outcome at each fine
dollar-level spend point by **linear interpolation** (`np.interp`) over
outcomes evaluated only at `multiplier_step`-spaced knots:

```python
summed_outcomes = np.sum(channel_outcomes[:, week_indices], axis=1)
# Linear interpolation lookup.
incremental_outcome_grid[valid_mask, i] = np.interp(multipliers, channel_mults, summed_outcomes)
```

Response curves are concave (saturating); a chord between two points on a
concave curve lies **below** the curve. Between knots, the interpolated
outcome is therefore **systematically under-estimated** — biased, not noise.
The honest framing is "nearly the same answer, much cheaper," not "the same
answer."

**Measured, signed error, `total_incremental_outcome`, weekly vs. classic:**

| Model | `multiplier_step` | Δ outcome (signed) | Δ outcome (relative) | Worst per-channel spend Δ (abs / rel) | Sign matches concavity? |
| --- | --- | --- | --- | --- | --- |
| `national-revenue` | 0.05 | +4.5e-13 (float noise) | ~0 | 0.0 / 0.0% | n/a — no measurable error |
| `national-revenue` | 0.01 | +4.5e-13 | ~0 | 0.0 / 0.0% | n/a |
| `national-revenue` | 0.005 | +4.5e-13 | ~0 | 0.0 / 0.0% | n/a |
| `geo-revenue` | 0.05 | **−0.136** | −3.25e-5 | 5.0 spend units / **2.15%** | **yes** |
| `geo-revenue` | 0.01 | 0.0 (exact) | 0.0 | 0.0 / 0.0% | n/a |
| `geo-revenue` | 0.005 | 0.0 (exact) | 0.0 | 0.0 / 0.0% | n/a |

Direction confirmed where an error was measurable at all: the one case with a
non-trivial delta (`geo-revenue`, step 0.05) is negative, matching the
concavity prediction exactly (`sign_matches_concavity_prediction: true`).

On `national-revenue` the classic optimum turned out to be a no-reallocation
corner solution (optimized spend == historical spend for every channel), so
no interpolation ever occurred — not evidence the grid is error-free, just
evidence this particular fixture's unconstrained optimum sits on a multiplier
knot (1.0) by construction. `geo-revenue`'s optimum is a genuine
reallocation, so it is the only one of the two fixtures that actually
exercises interpolation.

**Why the error vanishes at 0.01/0.005 but not at 0.05:** the classic path is
*also* a grid search — `optimizer.py`'s `_create_grids`/`get_round_factor`
builds an integer-dollar spend grid from `budget` and `gtol`, independent of
`multiplier_step`. The weekly path builds that same fine dollar-level spend
grid, but fills it by interpolating between coarser `multiplier_step` knots.
As `multiplier_step` shrinks, the curvature between adjacent knots shrinks
with it; on this fixture's response-curve scale, 0.01 and 0.005 are already
fine enough that the interpolated value at the optimum's spend point matches
the directly-evaluated classic value to displayed float precision. **This is
a fixture-scale-dependent result, not a proof that 0.01 is universally
safe** — a production model with sharper curvature (fewer channels sharing
saturation, larger budget swings) could show measurable error at steps this
fine. Only one fixture (`geo-revenue`, step 0.05) produced a directly
observed non-zero error in this spike.

---

## 3. Q1 — interpolation error vs. the classic path

Answered above (§2). Summary: **typical case (fine step, either fixture):
zero to float-precision. Worst observed case (`geo-revenue`, step 0.05):
total outcome −0.136 (−0.00325%), worst single-channel spend allocation off
by 5 units (2.15% relative).** The one non-trivial measurement is negative,
consistent with (not merely "not contradicting") the concavity argument.
Two multiplier steps and two model sizes is a thin sample — this is a
direction confirmation, not a distribution across many draws or fixtures.

---

## 4. Q2 — `create()` wall-clock and peak memory under x64

Peak RSS measured **per configuration, in a child process** (`_child()` in
the spike script), because `ru_maxrss` is a high-water mark and repeated
sampling in one process is monotone by construction.

| Model | `multiplier_step` | Build seconds | Build peak RSS (MB) | Device |
| --- | --- | --- | --- | --- |
| `national-revenue` | 0.05 | 1.618 | 934.1 | CPU |
| `national-revenue` | 0.01 | 1.603 | 932.4 | CPU |
| `national-revenue` | 0.005 | 1.661 | 932.8 | CPU |
| `national-revenue` | classic (reference) | 1.809 | 918.9 | CPU |
| `geo-revenue` | 0.05 | 1.878 | 960.2 | CPU |
| `geo-revenue` | 0.01 | 1.819 | 961.6 | CPU |
| `geo-revenue` | 0.005 | 1.844 | 955.2 | CPU |
| `geo-revenue` | classic (reference) | 2.341 | 935.4 | CPU |

**Build cost is essentially flat across `multiplier_step`** on these
fixtures — the dominant cost is the `Analyzer`'s posterior evaluation
(fixed per model), not the number of multiplier knots. Build peak RSS is in
the same ~900-970 MB band as the classic optimizer's own peak, i.e. **at
this fixture size, `create()` does not cost meaningfully more memory than a
single classic optimization** — but at ~1-5 channels x 1-5 geos x 52 weeks,
the fixture is nowhere near where `batch_size=10`'s stated purpose ("avoid
memory exhaustion") would bind.

**GPU: UNMEASURED.** No GPU measurement was taken. This spike's brief
forbids disturbing infrastructure other agents are concurrently using, and
provisioning a GPU tier to obtain one number was judged out of scope for a
measurement spike. **No GPU/CPU infrastructure decision can be made from
this report.**

> **Correction.** An earlier revision of this paragraph stated that the
> dev/GPU stack "was destroyed in Task 25 (cloud teardown)". That is false
> and was never true: Task 25 had not run at the time of writing, and Task 24
> was blocked before provisioning anything (`gcloud` reauthentication failed
> non-interactively), so no cloud resource was created or destroyed and no
> cost was incurred. The GPU measurement is absent because it was never
> taken, not because infrastructure was removed.
The theoretical argument stands unverified: the expensive posterior work
moves into `create()` (not eliminated), and `batch_size=10` documented as
existing "to avoid memory exhaustion" plus x64-by-default both point toward
`create()` being the memory-bound step on large models — but "points
toward" is not a measurement. This needs a real GPU run on a production-size
model before any GPU-retirement decision is made.

---

## 5. Q3 — cached-reuse cost (the number that would justify the approach)

One `create()` call, then four `to_optimization_grid()` + `optimize()` calls
against the same cached grid, over the full span and three sub-windows:

**`national-revenue`** — build once: 1.600 s.

| Window | Slice seconds | Optimize seconds |
| --- | --- | --- |
| full span | 0.00245 | 0.288 |
| quarter 1 | 0.00209 | 0.986 |
| quarter 2 | 0.00210 | 0.875 |
| quarter 3 | 0.00209 | 0.865 |

**`geo-revenue`** — build once: 1.823 s.

| Window | Slice seconds | Optimize seconds |
| --- | --- | --- |
| full span | 0.00241 | 0.302 |
| quarter 1 | 0.00210 | 1.476 |
| quarter 2 | 0.00209 | 1.343 |
| quarter 3 | 0.00208 | 1.337 |

**The slice itself (`to_optimization_grid`) is ~2 ms regardless of window,
model, or geo count — roughly three orders of magnitude cheaper than the
~1.6-1.9 s build.** That is the number that justifies build-once/slice-many.
But **`optimize()` after the slice is not free** — it ran 0.3-1.5 s per call
here, and sub-window optimizes cost *more* than the full-span one on both
fixtures. `BudgetOptimizer.optimize()` still runs its own gradient-free
search over the supplied grid even when the grid itself is precomputed and
cached; the slice removes the `create()` cost from each call, not the
`optimize()` cost. On these small fixtures the total reused-call cost
(slice + optimize) is dominated by `optimize()`, not by the slice.

---

## 6. Q4 — does `combine()` give arbitrary windows without recomputation?

Yes, on both fixtures, with the documented constraints. Two adjacent,
non-overlapping half-span grids (same `multiplier_step`, same default
bounds) were built and combined:

| Model | Build (2 halves) seconds | `combine()` seconds | `combine_error` | Union slice returns `None`? | Covers full span? |
| --- | --- | --- | --- | --- | --- |
| `national-revenue` | 1.702 | 0.00195 | none | no | **yes** |
| `geo-revenue` | 1.971 | 0.00205 | none | no | **yes** |

`combine()` itself is cheap (~2 ms, pure concatenation) and the merged grid's
`to_optimization_grid` over the full original span works and returns exactly
the original date range. **But building the two halves cost more than
building the one full-span grid** (1.70-1.97 s for two halves vs. 1.60-1.88 s
for one full grid) — `combine()` is not a way to make building cheaper by
building in pieces; each `create()` call still pays the full posterior-
evaluation cost independent of the span length on these fixtures. Its value
is for genuinely separate `create()` calls (e.g., grids built at different
times, or with different `new_data`) that need to be merged after the fact,
not for splitting one build into cheaper pieces.

Per the API contract (not exercised here, since both spike grids shared
every setting), `combine()` raises if `multiplier_step`, `use_kpi`,
`use_posterior`, the bounds, `n_rf_channels`, or `use_optimal_frequency`
differ between grids, or if dates overlap or are discontinuous — a real
constraint on any per-model grid cache that lets bounds vary by request.

---

## 7. Q5 — bounds width sweep

Build cost/memory at three `(max_budget_percent_decrease,
max_budget_percent_increase, max_constraint_variation)` settings, same
`multiplier_step` (reference: first `--steps` value, 0.05):

**`national-revenue`:**

| Bounds (decrease, increase, variation) | Build seconds | Build peak RSS (MB) | Optimize seconds |
| --- | --- | --- | --- |
| 0.9, 1.0, 0.3 (default) | 1.671 | 930.3 | 0.287 |
| 0.5, 0.5, 0.15 (narrow) | 1.657 | 930.5 | **1.264** |
| 0.95, 2.0, 0.5 (wide) | 1.515 | 928.3 | 0.291 |

**`geo-revenue`:**

| Bounds (decrease, increase, variation) | Build seconds | Build peak RSS (MB) | Optimize seconds |
| --- | --- | --- | --- |
| 0.9, 1.0, 0.3 (default) | 1.837 | 959.2 | 0.310 |
| 0.5, 0.5, 0.15 (narrow) | 1.823 | 956.4 | **1.745** |
| 0.95, 2.0, 0.5 (wide) | 1.749 | 949.0 | 0.360 |

**Build cost and peak memory are essentially invariant to bounds width** on
both fixtures — bounds only change the range `to_optimization_grid` slices
out of an already-built grid, not the cost of `create()` itself. The
narrowest, most symmetric bound (0.5/0.5/0.15) consistently made the
downstream `BudgetOptimizer.optimize()` call 4-6x slower on both fixtures —
a real, repeatable effect, but its cause (more search iterations needed to
satisfy a tight symmetric constraint vs. an asymmetric or wide one) was not
instrumented further in this spike and should not be assumed to generalize
without checking on a larger model.

**Directly demonstrated quiet-`None` failure mode:** building a grid with
narrow bounds (`decrease=0.5, increase=0.5, variation=0.15`) and then calling
`to_optimization_grid(spend_constraint_lower=0.9, spend_constraint_upper=0.9)`
— a request wider than what the grid covers — returned `None` and only
emitted a `UserWarning` ("Bounds are not within the grid...[per-channel
min/max]"), never raised:

```
result: None
WARNING: Bounds are not within the grid. Error message:
Lower bound 31 for channel ch_0 is below the mimimum spend of the grid 133.0.
Upper bound 593 for channel ch_0 is above the maximum spend of the grid 538.0.
...
```

A caller that doesn't explicitly check for `None` gets silent failure, not
an exception.

---

## 8. Hard constraints confirmed

Verified directly against installed `meridian==2.0.0`
(`weekly_optimization_grid.py`, this environment's site-packages):

- **No geo selection, anywhere in the weekly path.** `WeeklyOptimizationGrid.create()`'s
  signature has no geo parameter, and `to_optimization_grid` hardcodes
  `selected_geos=None` at `weekly_optimization_grid.py:754` when constructing
  the `OptimizationGrid`. Confirmed by introspection:
  `create geo args: NONE`, `to_optimization_grid geo args: NONE`. **A grid
  built this way cannot answer a geo-filtered optimization question at
  all** — there is no parameter to request one, and the returned
  `OptimizationGrid` is permanently stamped `selected_geos=None`. This
  server's tools accept `selected_geos` today; any adoption of the weekly
  path needs an explicit, reported fallback to the classic path whenever a
  request specifies `selected_geos`.
- **Scenario bounds are baked in at build time.** `create()` stores
  `max_budget_percent_decrease` (default 0.9), `max_budget_percent_increase`
  (default 1.0), and `max_constraint_variation` (default 0.3);
  `_validate_optimization_bounds` (`weekly_optimization_grid.py:571`) checks
  every `to_optimization_grid` request's bounds against the grid's built-in
  min/max spend per channel. **A cached grid serves a range of scenarios,
  not every scenario** — a request outside that range is refused (silently,
  see next point), so a per-model cache needs to key on the bounds it was
  built with, not just the model.
- **`to_optimization_grid` returns `None` on validation failure, not a
  raise** (`weekly_optimization_grid.py:663, :713`) — confirmed by direct
  reproduction in §7. This is a quiet failure mode: any caller of the weekly
  path must explicitly check for `None` and fall back or error out itself;
  nothing in the library raises on the caller's behalf.

---

## 9. What these numbers imply

Written as **input to the Phase 8 plan, not as a decision.**

- **The accuracy/cost trade is real and asymmetric in scale.** Slicing a
  cached grid (`to_optimization_grid`, ~2 ms) is roughly 1,000x cheaper than
  building one (~1.6-1.9 s on these fixtures), and the interpolation error
  it trades away is small where it was measurable at all (worst observed:
  2.15% on one channel, −0.00325% on the total outcome) and disappears at
  finer steps on these fixtures. **But "cheaper" only applies to the slice.**
  `BudgetOptimizer.optimize()` itself is not free even with a precomputed
  grid (0.3-1.7 s here, sometimes *more* than the classic path's own
  optimize step) — a Phase 8 plan should budget for optimize-after-slice
  cost, not assume slicing alone makes optimization free.
- **`multiplier_step`:** on these fixtures, 0.01 already matched the classic
  path to float precision, while 0.05 showed a small but real, correctly-
  signed error. This spike cannot recommend a production default from two
  small fixtures and two step values that bracket the transition so
  narrowly — the honest recommendation is: **default no coarser than 0.01,
  and re-run this same script against a production-scale model (more
  channels, sharper saturation, larger budget swings) before finalizing,
  since the point at which error becomes negligible is fixture-scale-
  dependent, not universal.**
- **Default bounds:** `(0.9, 1.0, 0.3)` (the library's own defaults) had no
  measurable extra build cost vs. the narrower or wider bounds tested, so
  there is no cost reason to narrow them. The real constraint is that
  bounds are baked in at build time and a request outside them quietly
  returns `None` (§8) — a Phase 8 cache keyed on `(model_id, bounds, ...)`
  needs to choose a small number of bound presets deliberately, not let
  every caller's bounds silently miss the cache or silently fail.
- **GPU:** **unmeasured — no decision follows from this report.** The
  theoretical direction (posterior cost moves into `create()`, not away;
  `batch_size=10` and x64 both push toward `create()` being memory-bound on
  large models) is unchanged from the brief, but this spike could not verify
  it, because the dev/GPU stack no longer exists and re-provisioning it was
  out of scope. **Whoever writes the Phase 8 plan must either get a real GPU
  measurement on a production-size model first, or explicitly carry this as
  an open risk** — "GPU may or may not still be needed for `create()` on
  large models" is not resolved here.
- **`selected_geos`:** the weekly path cannot express it at all (§8). Any
  Phase 8 tool surface must fall back to the classic path — explicitly, and
  reported to the caller — whenever `selected_geos` is requested. This is
  not a tuning question; it is a hard capability gap.
- **Overall:** the grid is a genuine cost/accuracy trade with a
  well-understood direction of error, not a free win and not something to
  reject either. Whether it is *worth* a tool surface depends on numbers
  this spike could not produce (GPU, production-model build cost and error)
  as much as on the numbers it did produce.

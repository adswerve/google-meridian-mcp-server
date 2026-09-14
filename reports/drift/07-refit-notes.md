# Phase 5 refit notes

Fixtures rebuilt with `scripts/generate_validation_models.py --force` on
google-meridian 2.0.0, JAX backend, 64-bit precision, Python 3.13.13.

Scope note: the brief predates a scope change. `.pkl` support was removed
entirely in commit `c7c4f0d` after the harness proved Meridian 2.0 on JAX
cannot run inference on a pickle saved under TensorFlow, so
`generate_validation_models.py` no longer has a `national-revenue-pkl`
variant. **Seven** fixtures were refitted, not eight:
`national-revenue`, `geo-revenue`, `national-kpi-rpk`, `geo-kpi-rpk`,
`national-kpi-only`, `geo-kpi-only`, `geo-revenue-media-only`. The stale
`models/_validation/national-revenue-pkl/` directory from before the removal
was left untouched by the generator (it has no corresponding `VariantSpec`
so the generator neither rebuilds nor deletes it); it is not part of this
refit and not read by any current test.

Command:

```
uv run python -u scripts/generate_validation_models.py --force
```

Output: seven `built <variant> -> models/_validation/<variant>/model.binpb`
lines, no traceback, run time ~3 minutes wall clock (background job, polled
via ps on the command line rather than process name since it runs under
`uv`).

## 1.7.1 -- strict exact coordinate match ordering across channels

**Not observed.** The refit log (`/tmp/refit.log`) has zero hits for
`dtype|precision|float64|float32|aks|knot|coordinate|order|align` (the grep
the brief specifies). More directly, the golden most exposed to this change,
`tests/integration/goldens/geo-kpi-only__get_channel_data.json` (a 2185-row
ordered array), was diffed row-by-row against a live `get_channel_data` call
against the refit `geo-kpi-only` fixture:

```
columns same: True
row_count: 2185 -> 2185
row0 same: True
rows all same: True
```

All 2185 rows are byte-identical in position and value to the golden
captured on the 1.7-built fixture. No reordering occurred. This fixture
generator builds `InputData` via `meridian.data.test_utils` factories with a
fixed, already-canonical geo/channel coordinate order (`seed=0`,
alphabetically-generated geo/channel names), so there was no
non-canonical ordering for the 1.7.1 fix to correct in the first place --
consistent with "not observed" rather than "observed but no-op."

## 1.8.0 -- prior-distribution dtype validation against 64-bit JAX precision

**Not observed as a warning or rejection.** The refit ran to completion under
JAX/FLOAT64 (confirmed via `fixture_probe.py`, below) with no dtype/precision
warnings or errors from `meridian.model.prior_distribution` beyond the
expected, unrelated "Hierarchical distribution parameters must be
deterministically zero for national models" notices (these fire on every
national-variant build in both 1.7 and 2.0 and are about hierarchical-prior
structure, not dtype). No `UserWarning`/exception mentioning dtype or
precision mismatch appeared in the log for any of the seven variants.

## 1.8.0 -- AKS fallback fix for non-national models

**Not observed as a warning or failure.** All four `geo-*` variants
(`geo-revenue`, `geo-kpi-rpk`, `geo-kpi-only`, `geo-revenue-media-only`)
built cleanly with no AKS-related warnings, errors, or fallback messages in
the refit log. `future_optimization_qa.py` (11/11) and the golden check both
exercise `geo-kpi-only` post-refit without incident.

## Goldens

**Both goldens still match on refit fixtures** -- `uv run pytest
tests/integration/test_analysis_subprocess.py -q` gives `3 passed`. No
regeneration was needed or performed; `tests/integration/goldens/` is
unchanged in this commit.

## Provenance (before -> after), `national-revenue`

```
before: {"trained_backend": "TENSORFLOW", "trained_precision": "FLOAT32", "trained_model_version": "1.7.0", "backend_mismatch": true}
after:  {"trained_backend": "JAX",        "trained_precision": "FLOAT64", "trained_model_version": "2.0.0", "backend_mismatch": false}
```

File hash also changed (`2facc5e6...` -> `29be11a8...`), and all seven
refit fixtures differ byte-for-byte from their `models/_baseline_fixtures_v1.7/`
counterparts (`diff -q` reports "differ" for all seven; the v1.7 archive
itself is untouched -- same sha256 before and after this task).

## Baseline capture directory (`models/_validation/_baseline/`)

Confirmed intact and untouched by the refit: `v1.7-engine`, `v2.0-tf`, and
`v2.0-jax` subdirectories all present with their captured label JSON files.
`generate_validation_models.py` only ever writes to
`models/_validation/<VariantSpec.key>/model.binpb` for the seven keys in its
`VARIANTS` list -- it has no code path that touches `_baseline`,
`_cloud_runs`, `_cloud_runs_jax`, or `_runs`, so no defensive move-aside was
needed. `v1.7-engine` (irreplaceable; this machine no longer has Meridian
1.7) was verified present before and after the refit.

## An unrelated pytest failure surfaced by the refit (not a golden, reported for visibility)

`uv run pytest -q` -> `1 failed, 674 passed` (675 total, same count as the
pre-refit baseline of 675 passed). The failure is
`tests/integration/test_optimizer_facade_future.py::test_run_future_cost_multiplier_shifts_away`,
which asserts that applying a 100x cost multiplier to the first media
channel (`ch_0`) of the `national-revenue` fixture changes the optimizer's
allocation away from baseline.

Root cause, confirmed by direct inspection: with the refit posterior, the
optimizer's *baseline* (no multiplier) optimized spend for `ch_0` already
sits at its spend-constraint floor (`0.7 * initial spend`, rounded to whole
currency units: `0.7 * 7.0 = 4.9 -> 5.0`). Since it starts at the floor, a
100x cost multiplier cannot push it any lower, so `opt_allocation(bumped) ==
opt_allocation(base)` and the "must differ" assertion trips -- even though
the multiplier's downstream effect on efficiency/outcome IS being applied
correctly (`optimized_efficiency` and `optimized_incremental_outcome` both
drop between base and bumped, exactly as expected when the cost basis
increases spend without changing quantity). This is precisely the failure
mode the test's own docstring anticipates for RF channels ("RF channels can
sit at their spend-constraint floor ... and the test would pass trivially
even if the cost path were broken") -- it just happens to also apply to
`ch_0` under the new 2.0/JAX posterior, where it didn't under the old
1.7/TF one. `OPTIMIZATION_ALLOWED_TIERS=local uv run python
scripts/qa/future_optimization_qa.py` independently exercises the same
cost-multiplier code path across 11 scenarios and reports `FUTURE-OPT QA
PASSED (11/11)`, corroborating that the cost-multiplier mechanism itself is
intact and this is a fixture-dependent test-assumption fragility, not a
functional regression. Out of this task's scope (`test_optimizer_facade_future.py`
is not a golden and not listed as a possibly-modified file in the task
brief), so it was left as-is and reported here for the next task/owner to
triage -- likely a symptom of posterior resampling, which Task 20's diff #3
is designed to isolate.

## Gates
- `live_validate`: NOT RUN (out of scope for this task -- reserved for Task 20).
- `future_optimization_qa.py`: FUTURE-OPT QA PASSED (11/11)
- `pytest -q`: 1 failed, 674 passed (675 total; failure is
  `test_run_future_cost_multiplier_shifts_away`, analyzed above -- not a
  golden, not fixed in this task)
- `pytest tests/integration/test_analysis_subprocess.py -q` (goldens only): 3 passed
- `ruff check src scripts tests`: All checks passed!
- `ruff format --check src scripts tests`: 110 files already formatted

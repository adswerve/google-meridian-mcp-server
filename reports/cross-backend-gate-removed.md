# The cross-backend JAX gate: removed, not repaired

Task 16 (Phase 4c) deleted `live_validate.py`'s cross-backend JAX gate rather
than fixing it. This note records what it used to prove, why it cannot be
fixed instead of removed, and what covers that ground now that it is gone.

## What the gate used to prove

Before the Meridian 2.0 upgrade, the local cloud-executor section of
`scripts/validation/live_validate.py` ran `national-revenue`'s optimization
twice against two differently-configured `OptimizationService` instances:

- `backend="tensorflow"` (also covering `geo-revenue`)
- `backend="jax"` (`national-revenue` only, skipped if `jax` was not
  importable)

Each call threaded `optimization_backend_cloud_cpu=backend` into
`RuntimeConfig(...)`, and the real worker subprocess picked that field up to
select its computation backend. The point of running the JAX leg was to
prove that a model **fitted under one engine could be optimized correctly
under the other** -- the one thing a same-process, same-fixture unit test
can never exercise, because it requires a real worker subprocess actually
switching backends mid-suite.

## Why it cannot be repaired, only removed

Task 15 removed the `optimization_backend_local` / `optimization_backend_cloud_cpu`
/ `optimization_backend_cloud_gpu` fields from `RuntimeConfig` entirely and
fixed the backend as the single module constant
`base_subprocess.MERIDIAN_BACKEND = "jax"` (D2). Two things follow, both
found by review after Task 16's plan was written:

1. **The gate was already silently defanged, not merely stale.** Pydantic's
   default `extra="ignore"` means passing
   `optimization_backend_cloud_cpu=backend` into `RuntimeConfig(...)` no
   longer raises -- it is dropped without error. `CloudRunJobExecutor`'s
   worker launch always injects the `MERIDIAN_BACKEND` module constant
   (`jax`) into the child environment regardless of `cfg`. This was
   confirmed empirically (Task 15's own live-gate run, and reconfirmed
   while implementing this task): Meridian's fixture-provenance warning
   (`trained using TENSORFLOW, but the current backend is JAX`) appeared
   **inside the section labelled `backend="tensorflow"`**. Both the
   `"tensorflow"`-labelled and the `"jax"`-labelled runs were executing on
   JAX. A gate that reports PASS for something it is not testing is worse
   than no gate at all -- it was testing JAX-vs-JAX and calling it
   TF-vs-JAX.
2. **There is only one engine left to cross.** With `optimization_backend_*`
   gone and JAX fixed as the sole computation backend everywhere in `src/`,
   there is no second backend value to pass to a repaired version of the
   gate even if the plumbing were restored. "Cross-backend" requires two
   backends; Meridian 2.0's Phase 4 leaves exactly one.

Repairing the gate to genuinely cross backends would mean re-introducing the
backend knob Task 15 (D2) deliberately removed -- reversing the point of the
upgrade. So it is deleted, not patched, and its disappearance is recorded
here instead of left to be discovered as a silent gap.

## What covers this ground now

Nothing does, and that is a real, acknowledged coverage loss: **no gate any
longer proves that a model fitted under one engine optimizes correctly under
another engine.** With JAX as the only computation backend, this class of
bug (an optimizer that works on the backend it was tested on but not on the
one it will actually run under) cannot recur *within this codebase* -- there
is no other backend to diverge onto. The residual risk is entirely upstream:
if `meridian.backend` itself regresses in how it optimizes a TF-era-fitted
`InferenceData` under JAX, nothing in this suite would catch it, because
nothing in this suite runs a second engine to compare against.

What remains, and what it does and does not cover:

- **The local cloud-executor gate** (`scripts/validation/live_validate.py`,
  faked `jobs.run`, real worker subprocess) still runs
  `national-revenue` and `geo-revenue` optimizations end-to-end through the
  cloud code path, on the one supported backend (JAX). It proves the cloud
  launch/liveness/cancel contract works; it does not and cannot prove
  anything about backend portability.
- **The real Cloud Run smoke** (`scripts.validation.cloud_smoke`,
  `CLOUD_SMOKE=1`) covers a live GCP project (CPU tier verified). Same
  scope limit: one backend, no cross-check.
- **The drift-report suite** (`reports/drift/`, spec §4) diffs behavioural
  snapshots across Meridian versions/backends captured at different times
  (e.g. `v1.7-engine` vs `v2.0-tf` vs the planned `v2.0-jax`), including the
  per-fixture `trained_backend` / `current_backend` provenance this task's
  `probe_backend()` fix makes trustworthy again. This is the closest thing
  to a replacement: it can show that a JAX-trained-and-optimized model
  produces the same headline numbers as a TF-trained one within tolerance.
  It is not a live functional gate, though -- it compares recorded snapshots,
  it does not exercise a real subprocess optimizing a foreign-backend model
  on every CI run the way the deleted gate did.

`AGENTS.md` records the same removal, in the module-map section describing
`scripts/validation/`.

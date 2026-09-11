# Reports

This directory is the evidence behind a major dependency upgrade to this server: Meridian
1.7 → 2.0, which came bundled with a backend change (TensorFlow → JAX, 64-bit precision). An
MCP server exists to hand an AI agent real marketing-mix-model numbers, so a dependency bump
is only trustworthy if it can be shown not to have silently changed those numbers. These
reports are that check, kept here so a reader can verify the upgrade's behavior directly
rather than take it on faith.

**Start with `verification-gaps.md` if you read one file.** It records what this evidence does
*not* cover, so nobody infers more coverage from the reports below than they actually provide.

## Drift reports (`drift/`)

A purpose-built harness (`scripts/validation/capture_baseline.py` and `diff_baseline.py`,
documented in `AGENTS.md`) snapshots every tool call's response under a labeled environment,
then diffs two labels leaf-by-leaf with numeric tolerances. Each report is one such diff:

- `01-meridian-version.md` — Meridian 1.7 → 2.0 on TensorFlow, isolating the engine bump from
  the backend change. PASS.
- `02-backend-precision.md` — TensorFlow → JAX with 64-bit precision, across the full fixture
  set including the since-removed `.pkl` format. FAIL, fully triaged: every failure traces to
  `.pkl` being unrunnable on JAX (see `pkl-format-removed.md`).
- `02b-backend-precision-supported-surface.md` — the same diff re-run over only the format this
  server actually serves (`.binpb`), isolating the real numeric drift the backend change
  causes.
- `03-posterior-resampling.md` — a control diff: how much two fits of the *same* model on the
  *same* backend differ run to run, giving the other reports' tolerances a baseline for
  "normal" noise. Truncated to its analysis and verdict; the underlying ~59,000-row per-leaf
  tables were machine-generated from scratch directories that no longer exist and carry no
  conclusion the analysis doesn't already state.
- `04-cloud-vs-local.md` — a deployed Cloud Run service compared against a local run, plus a
  documented `get_training_data` delivery limitation (see its "Update" section for what a
  later, more precise experiment found).
- `07-refit-notes.md` — notes from rebuilding the validation fixtures under Meridian 2.0 on
  JAX.

## Removed-capability records

- `cross-backend-gate-removed.md` — why an integration gate that used to prove a model fitted
  on one backend could still be optimized on another was deleted rather than repaired, once
  only one backend remained.
- `pkl-format-removed.md` — why `.pkl` model support was dropped rather than migrated:
  Meridian 2.0 on JAX cannot run inference on a pickle saved under TensorFlow.

## Research spikes

- `geox-calibration-findings.md` — a findings-only spike into Meridian's GeoX /
  channel-calibration review tooling; no production code changed.
- `skills-audit.md` — every behavioral claim in the bundled `skills/meridian-analyst/` skill,
  checked against a live server call rather than just read for plausibility.
- `weekly-optimization-grid-measurements.md` — the measurements behind the compute-tier sizing
  grid in the README's "Which tier does `auto` pick?" section.

## Optimization queue durability

- `durable-queue-live-verification.md` — **pending live execution.** The durable-queue /
  restart / cancel acceptance gate (`QUEUE_SMOKE=1` in `scripts/validation/cloud_smoke.py`)
  proves, against a real deployed Cloud Run stack, that the queue/claim/adoption/cancel
  mechanisms from the 2026-09-10 optimization-durability-and-encoding plan survive an actual
  process boundary — not just an in-process fake. The script (Phases 0-3 plus two near-free
  checks) is implemented and gated behind `QUEUE_SMOKE=1`; it has not yet been run, because it
  needs interactive `gcloud` re-authentication this environment cannot perform, spends real
  money (13 real optimizer executions (3 each for Phases 0, 1, 2, 2b, plus 1 cancelled in Phase 3), roughly 4-6 hours wall-clock), and mutates a shared Cloud Run service's
  `--max-instances`. This report will be written from the script's real console output once an
  operator with live credentials runs it.

## `verification-gaps.md`

The gaps in the evidence above: environment moves that were never drift-baselined, a config
surface the drift matrix doesn't exercise, and other honestly-stated limits on what these
reports prove.

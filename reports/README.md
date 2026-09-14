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

- `durable-queue-live-verification.md` — **PASSED, 2026-09-11.** The durable-queue /
  restart / cancel acceptance gate (`QUEUE_SMOKE=1` in `scripts/validation/cloud_smoke.py`)
  run against a real deployed Cloud Run stack in `as-dev-anze`/`us-central1`: Phases 0-3 plus
  the two near-free checks, 13 optimizer executions, 37 minutes. Phase 2b is the headline —
  two runs genuinely executing and one genuinely queued when the serving container was
  replaced, all three recovered from GCS by the new revision with exactly three executions for
  three runs. The report quotes real console output throughout and states its own limits: the
  gate mutates the service's `--max-instances` (pinned to 1, restored to 2 afterwards), and
  Phase 2 observed one adoption rather than two because `reconcile_orphans()` took ~70s over a
  bucket of 80 runs.

## `verification-gaps.md`

The gaps in the evidence above: environment moves that were never drift-baselined, a config
surface the drift matrix doesn't exercise, and other honestly-stated limits on what these
reports prove.

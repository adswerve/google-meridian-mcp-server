# AGENTS

A FastMCP server exposing Google Meridian MMM analysis to agents. The transport layer stays
thin; behaviour lives in services, Meridian adapters, and persistence helpers.

## Runtime shape

`server.py` builds the FastMCP app and lifespan state → `config.py` reads env into
`RuntimeConfig` → the lifespan picks `local` or `gcs` persistence → caches wrap discovery,
materialization and result reuse → `ModelCatalog` resolves a model id to a loaded Meridian
model → `transport/tools.py` maps tool calls to services → `services/analysis_service.py`
validates inputs and shapes responses → `meridian/` does the Meridian-aware work.

## Layer boundaries

| Put this | Here |
| --- | --- |
| Agent-facing tool contracts | `transport/tools.py` |
| Orchestration | `services/` |
| Validation, reusable types | `domain/` (no `importlib.metadata`, no Meridian) |
| Meridian adapters | `meridian/` |
| Backend access, cache materialization | `persistence/` |
| Launch mechanics, workers, routing | `execution/` |

`ruff` enforces this: `meridian`, `tensorflow`, `tensorflow_probability`, `jax` and
`google_meridian_mcp_server.meridian` are banned imports outside `meridian/`, `worker.py`,
`analysis_ops.py`, tests and scripts. Treat `references/` and `specs/` as reference material;
never couple runtime code to them.

## Environment

Python `>=3.13,<3.14`; `google-meridian[schema,geox]>=2.0,<3`; `fastmcp>=4,<5`; ruff
`target-version = "py313"`. `uv.lock` is **tracked**, but no Dockerfile consumes it — all
three (`Dockerfile`, `deploy/Dockerfile.worker`, `deploy/Dockerfile.worker.gpu`) run
`pip install "."`, so images re-resolve dependencies at build time. The lock pins developer
and CI environments only.

### Engine: JAX with 64-bit precision, everywhere

See [README.md's "Optimization tiers & concepts"](README.md#optimization-tiers--concepts) for
the user-facing statement that every tier runs JAX with 64-bit precision and that there is no
per-tier engine choice — that is canonical; do not restate it here. Implementation detail for
contributors: `MERIDIAN_BACKEND = "jax"` is a **module constant** in
`execution/base_subprocess.py`, not a setting, and `RuntimeConfig.backend_for_tier` **no longer
exists** — do not reintroduce it. `child_env` applies `_HYGIENE_DEFAULTS` with `setdefault` (thread counts stay
operator-tunable) but `_HYGIENE_OVERRIDES` **unconditionally**: `TF_CPP_MIN_LOG_LEVEL`,
`MERIDIAN_BACKEND`, `MERIDIAN_ENABLE_JAX_X64="true"`. That split is deliberate — `os.environ`
stopped being a proxy for operator intent once `jax/__init__.py` began writing
`TF_CPP_MIN_LOG_LEVEL` into it at import time. Both apply before `env_base` and `extra`, so
explicit executor configuration still wins. `CloudRunJobExecutor` re-injects the two Meridian
vars as container overrides, because a Cloud Run Job does not inherit the server's env.

### Model format: `.binpb` only

`.pkl` support is **removed**. `ModelFormat.PKL` is gone and the loader rejects pickles with
an actionable `UnsupportedModelFormatError`. Meridian 2.0 on JAX cannot run inference on a
pickle saved under TensorFlow — that is the reason, not tidiness. See
`reports/pkl-format-removed.md`.

## Commands

```
uv run python -m google_meridian_mcp_server.server
uv run pytest                                    # 832 passed, 1 skipped on this branch
uv run ruff check src scripts tests              # and `ruff format`
uv run python -m scripts.validation.live_validate            # integration gate; --force rebuilds fixtures
OPTIMIZATION_TIER=local uv run python scripts/qa/future_optimization_qa.py
```

## Live validation

`scripts/validation/live_validate.py` is the integration acceptance gate: it drives an
in-process FastMCP `Client(mcp)` over every tool across the fixture matrix plus adversarial
error paths, and exits non-zero on any mismatch. 146 assertions. It prints a variant×tool
PASS / EXPECTED-ERR / FAIL matrix and ends with `LIVE VALIDATION PASSED` or `N failed`.

- **Do not run two instances concurrently.** It `rmtree`s fixed shared paths at startup
  (`live_validate.py:117-118`, and the shared `_runs` root at `:141`), so a second instance
  destroys the first's run records and produces failures that look like real regressions.
- **Fixtures** live under gitignored `models/_validation/` and are never committed. The first
  run BUILDS them via real tiny MCMC fits — a few minutes, not a hang.
- **Generator** `scripts/generate_validation_models.py` builds **7** variants: the 2×3
  `national|geo` × `revenue | kpi+revenue_per_kpi | kpi-only` matrix (all with reach and
  frequency), plus `geo-revenue-media-only` for the no-RF error path. Model id == fixture
  directory name.
- **Expectations** live declaratively in `matrix.py`: `roi`/`marginal_roi` only for revenue
  and kpi+rpk variants (else `metric_not_supported`), `get_reach_frequency` only for RF
  variants, unknown channel or geo → `missing_model_data`.
- It also gates optimization end-to-end for `national-revenue` and `geo-revenue`: both
  `run_optimization` and `run_future_optimization` → poll → result → reuse → delete →
  verify-gone, plus future adversarial submits. A **local cloud-executor gate** (faked
  `jobs.run`, real worker) covers the cloud launch/liveness/cancel contract with no GCP
  project; an opt-in **Cloud Run smoke** (`cloud_smoke`, `CLOUD_SMOKE=1`) covers a live one.
  The cloud gate prints its own summary line, `cloud gate: 3/3 checks passed` — the 2
  `run_optimization[cloud_cpu]` checks (`national-revenue`, `geo-revenue`) plus the
  restart-recovery check added when durability landed. This count is separate from the 146
  assertions above: that figure is only `Report.ok()` calls from `run_matrix(client)`, which
  never counted the cloud gate's checks, before or after.
- The **cross-backend JAX gate is deleted** — with one engine there is nothing to cross-check.
  That is a real coverage loss: no gate now proves a model fitted under one engine optimizes
  correctly under another. See `reports/cross-backend-gate-removed.md`.

## Drift harness

`scripts/validation/{payloads,normalize,manifest,fixture_probe,matrix,capture_baseline,acknowledged,diff_baseline}.py`
snapshot every tool case under a label and classify two labels against each other into a
Markdown report. Kept after the upgrade: it is the cheapest way to prove a bump changed nothing.

- **Capture:** `uv run python -m scripts.validation.capture_baseline --label <L>`
  (`--transport http --url ...` for a deployed server, `--compute-tier` to force a tier,
  `--tools`/`--cases` for a subset, `--force` to overwrite). Budget ~27 real optimizer
  subprocess runs per label.
- **Snapshots are envelopes:** `{capture_env, known_racy_fields?, payload}`. Diffs compare
  `payload` only. `capture_env` includes package versions, so a label captured under a
  different environment is **stale**, and recapturing it is gated behind
  `--allow-stale-recapture`.
- `manifest.json` is written **LAST**, so an incomplete capture cannot present itself as
  complete. A label directory without one is incomplete.
- **Isolation:** in-process captures use a fresh `OPTIMIZATION_RUNS_ROOT`,
  `RESULT_CACHE_ENABLED=false` and `force_rerun=true`. Without this the fingerprint
  short-circuit serves a later capture from an earlier one and the diff reports a spurious
  clean result on exactly the runs being measured. Over HTTP only `force_rerun` crosses the
  wire.
- **Failures are fatal, not snapshots.** A raised tool call aborts the case and exits
  non-zero; only tool-level error envelopes get snapshotted.
- **Diff:** `uv run python -m scripts.validation.diff_baseline <a> <b> --report out.md`.
  Manifests compare before data; differing fixture fingerprints abort unless
  `--allow-fixture-change`. Verdicts: FAIL (structural, including any change to the identity
  integers `row_count`, `size_score`, `count`, `geo_count`, `total_channels`), REVIEW
  (numeric, over relative tolerance with an absolute floor first), ACKNOWLEDGED
  (pre-registered in `acknowledged.py` with a reason), PASS. Non-zero on FAIL or REVIEW.

> **DESTRUCTIVE HAZARD.** The `v1.7-engine` label **cannot be regenerated** — Meridian 1.7 is
> uninstalled. A durable archive of all five labels and both fixture sets lives outside the
> repo at `~/meridian-2.0-baseline-archive/`. Never `--force` or recapture that label.

## What the upgrade proved, and what it did not

- **Meridian 1.7 → 2.0 on TensorFlow:** 300 of 330 payloads byte-identical, **zero structural
  changes** across 16,452 numeric leaves, max drift 1.27e-4. (`reports/drift/01-*.md`)
- **TensorFlow → JAX/float64:** 24.5% of numeric leaves moved, median 3.8e-6, 7 leaves over
  the 1e-3 tolerance — all characterised. (`reports/drift/02b-*.md`)
- Both cloud tiers ran real optimizations. (`reports/drift/04-cloud-vs-local.md`)
- **`reports/verification-gaps.md` records what was NOT covered** — notably that the fastmcp
  `3.4.7 → 4.0.3` and Python `3.12 → 3.13` moves were never drift-baselined. Read it before
  claiming coverage.

## Known issues a fresh session should not rediscover

- **`config_fingerprint` excludes `compute_tier`.** It hashes `(model_id, config,
  meridian_version)`, and `compute_tier` is not a config field. Requesting a different tier
  for an identical model+config silently returns the existing run (`reused=true`); pass
  `force_rerun=true` for a genuine second run. Unresolved — the user has not decided yet.
- **Unfiltered `get_training_data` is a context-budget concern, not a transport failure.**
  It was misdiagnosed three times — as a Cloud Run delivery ceiling, as a timeout, and as
  a size ceiling — before measurement showed the server was always healthy. The real cause
  was a Python-client SSE event-size cap reached only when a handler outran mcp's 15s
  mode-switch window; `json_response=True` in `server.py` makes that branch unreachable.
  Note the trade: `application/json` replies are not chunked, so Cloud Run's 32 MiB
  non-streaming response limit now applies where the SSE path was exempt. The largest real
  payload is ~16 MB, comfortably under it, but a response over 32 MiB would now fail at the
  edge rather than return a clean error. Still pass a dataset or date filter: the unfiltered
  payload is megabytes and will overflow an agent's context.
  (`reports/drift/04-cloud-vs-local.md`)
- **The optimization queue drains on request arrival, not in the background.** A
  cloud run recovered at startup is re-enqueued and pumped once; after that,
  `pump()` runs only from `submit` and `get_optimization_status`. Nothing
  schedules it, so a queued run whose instance goes idle waits for the next
  request or the next instance start. Durable, but not self-driving.
- **`OPTIMIZATION_MAX_PARALLEL` is per instance, and counts launches this
  instance has not yet observed completing** — not concurrent executions. With
  `max_instance_count = 2` the effective ceiling is twice the value, and a slot
  is released only when a `pump()` reaps the handle. After a restart, adopted
  handles occupy slots, so a recovered queued run waits for an adopted run to
  finish *and* for a later request.
- **A dispatch claim whose process died before `run_job()` is resolved at the
  next instance start**, not immediately: `reconcile_orphans` is startup-only,
  and a claim younger than `DEFAULT_DISPATCH_STALE_SECONDS` (1800s) is left
  alone because a peer may still be inside `run_job()` or cold-starting.
- **Startup reconciliation costs roughly nine GCS RPCs per run in the bucket**,
  regardless of status: `list()` downloads every `record.json` plus a
  `get_state` — itself an `exists()` check plus a download
  (`optimization_run_registry.py:315-321`) — for every run on every call
  (`:330-358`), `limit` is applied after the scan (`:358`) so it cannot help,
  and the cloud override scans the bucket **three times, not two**:
  `list(status=RUNNING)` inside the base class's `reconcile_orphans`, an
  unremoved duplicate `list(status=RUNNING)` call at `cloud_run_executor.py:79`,
  then `list(status=QUEUED)` at `:87` — plus a `get_dispatch` per RUNNING/QUEUED
  candidate. Order 1,800 RPCs at 200 runs, 9,000 at 1,000. It runs inside the
  ASGI lifespan before `$PORT` binds, against Cloud Run's ~240s startup budget,
  so past roughly 1,000 runs a cold start is at risk. Bounding it needs an
  `index/queued/` prefix mirroring `index/by_fingerprint/` — deliberately
  deferred.
- **Startup reconciliation is best-effort and unretried** (`server.py:67-70`): a
  single GCS hiccup skips it entirely with only a warning, and the queued runs
  wait for the next instance start. Deliberate — a retry loop in the ASGI
  lifespan would trade a stranded run for a failed boot.
- **`cancel_optimization` still has three dishonest windows.** One: a run
  dispatched but whose execution name was not recorded (a transient write
  failure) cannot be terminated by name, even now that `cancel()` also checks
  `get_dispatch` for a name recorded by another instance. Two: `_terminate` is
  best-effort (`cloud_run_executor.py:223-228`), so a `cancel_execution` RPC
  that itself fails still yields `canceled` while the execution bills on.
  Three: adoption now gives a `RUNNING` run a recorded `execution_name`, and
  when `_reconcile_stale` fails such a run on a stale heartbeat it pops the
  handle (`base_executor.py:244`) without calling `_terminate` — the
  execution keeps billing with nothing left that will ever cancel it. Before
  adoption existed there was no name to terminate, so this is not a
  regression, but the information to close it now exists. All three narrowed
  from "always after a restart", none eliminated.

## The recurring defect shape — the most transferable lesson

Tests that assert *that* something happened but never *which*. An anchoring test passed under
both the correct anchor and the broken one, and that is what let two Criticals ship. Every
test must **fail when its rule is reverted**. Assert the specific value, the specific window,
the specific channel — never just "a result came back".

## Working guidelines

- Think before coding: state assumptions; surface competing interpretations; ask when unclear.
- Simplicity first: the minimum code that solves the problem. No speculative abstractions,
  flags, or error handling for impossible cases.
- Surgical changes: touch only what the task requires; match existing style; do not refactor
  or reformat adjacent code; mention unrelated dead code instead of deleting it. Remove only
  the orphans your own change creates.
- Goal-driven: turn each task into a verifiable check (write or adjust a test, then make it
  pass); loop until `uv run pytest` and `uv run ruff check src scripts tests` are green.
- Reuse `MeridianInterrogator` for shared model metadata and data extraction. Keep responses
  JSON-safe, stably ordered, and free of broad exception swallowing.
- Deterministic ordering in all public payloads: never let a tool response's row, column, or
  list order depend on incidental factors like dict/set iteration or filesystem/GCS listing
  order — sort explicitly wherever order is not otherwise meaningful. (No test currently
  enforces this rule repo-wide.)
- When adding a tool, wire it `transport → service → meridian`. When adding model metadata,
  consider whether it belongs in the overview payload.
- Tests: unit tests with xarray/pandas fakes for Meridian-facing logic; mocks at the GCS,
  FastMCP and Meridian import boundaries; integration tests for provider filesystem and cache
  behaviour; contract tests for external shapes and documented enums. Every new analysis
  branch gets at least one happy path and one error path.
- Editing `.tf`? Use context7 (`/hashicorp/terraform-provider-google`) for current syntax.

## Deployment

The full stack (Cloud Run service + CPU/GPU jobs, Artifact Registry, GCS) is provisioned per
client via `deploy/terraform/`. One `terraform apply` builds and pushes all three images via
Cloud Build (content-hash tags), then provisions everything. Service and jobs share one
identity: the compute engine default SA, or a dedicated least-privilege SA when
`service_account_id` is set. Per-client `terraform.tfvars` and `backend.hcl` are uncommitted
(`.example` committed); `.env` is local-dev only. Runbook:
[README.md](README.md#deploy-to-google-cloud-terraform).

## Further reading

Committed evidence lives under `reports/` (start at `reports/README.md`) — link there for
anything a collaborator needs. `references/` is **gitignored**, so the files below are
local-only and absent from a fresh clone. Expected; not a broken repo.

- `references/module-map.md` — the full per-module map, plus the future-optimization gotchas.
- `references/tool-contracts.md` — the 18-tool surface, per-tool response contracts, the full
  env-var reference, the test-coverage map, and the accepted follow-ups.
- `references/research-deferred-work.md` — GeoX / `ModelReviewer` findings,
  `WeeklyOptimizationGrid` measurements, and what is out of scope.
- `references/skills-research.md` — the skill audit method and findings.

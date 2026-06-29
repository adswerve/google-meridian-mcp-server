# Design — Budget Optimization module (`run_optimization` + registry)

**Date:** 2026-06-29
**Status:** Proposed (awaiting review)
**Topic:** A multi-tool "optimization module" for the Meridian MCP server that runs
Meridian budget optimization as a durable, long-running, retrievable job —
mirroring the mmm-showcase Budget Optimization page, with a pluggable execution
backend (local subprocess or Cloud Run Jobs, CPU/GPU) and a durable run registry.

---

## 1. Motivation

The mmm-showcase **Budget Optimization** page lets a user pick a date range,
geos, a scenario (Fixed Budget / Target ROAS / Target mROAS), an objective
(ROAS/ROI vs CPIK for dual models), and spend constraints (global % or
per-channel), then runs `meridian.analysis.optimizer.BudgetOptimizer.optimize()`
in a short-lived subprocess and renders a summary, per-channel before/after
tables, allocation + spend-delta charts, and an HTML report.

The MCP server has **no optimization capability at all**. Adding it is not a
single tool: optimization is a **long-running process** (seconds for national
models, minutes for large geo models), results must be **durable, listable, and
retrievable across sessions**, and the heavy compute should be **offloadable off
a lean MCP host** onto Cloud Run (optionally GPU). This calls for a small
*module* — a submit/poll job API plus a durable run registry plus a pluggable
executor — not a one-shot tool.

The Meridian version pinned by this server (`google-meridian==1.7.0`) exposes the
**exact** `optimize()` signature the showcase calls
(`start_date`, `end_date`, `fixed_budget`, `budget`, `target_roi`, `target_mroi`,
`spend_constraint_lower/upper`, `use_kpi`, …), so the showcase worker logic is
directly portable. Its `OptimizationResults` carries `.nonoptimized_data` and
`.optimized_data` xarray datasets.

### 1.1 Confirmed facts grounding the design

- **The optimization path is backend-aware.** `meridian/analysis/optimizer.py`
  imports `from meridian import backend` and types tensors as `backend.Tensor`.
  The Meridian compute backend is selected by `MERIDIAN_BACKEND` (`tensorflow`
  default | `jax`), read at import time in `backend/config.py`
  (`_initialize_backend()`), and is **process-global** — it cannot change within
  a running process. JAX uses XLA and is more memory-efficient; the win applies
  to `optimize()` (our hot path), not just MCMC training (which this server never
  does).
- **`optimize()` exposes no progress callback.** The dominant compute is
  `_create_grids()` looping `for i in range(n_grid_rows)` calling
  `incremental_outcome(...)` (optimizer.py:2781). Fine-grained % requires
  subclassing; coarse phase + heartbeat progress does not.
- **Cloud Run GPU Jobs are GA**: NVIDIA L4 (24 GiB VRAM), min 4 CPU / 16 GiB,
  per-second billing, scale-to-zero. GPU is a **job-creation-time** setting
  (not per-execution), so CPU vs GPU = two pre-deployed job definitions. Image
  streaming makes large CUDA images boot quickly; submit+poll hides cold start.

---

## 2. Scope & non-goals

**In scope**
- A submit/poll job API: `run_optimization`, `get_optimization_status`,
  `get_optimization_result`, `list_optimizations`, `delete_optimization`
  (+ `cancel_optimization`, Phase 2).
- A durable run **registry** with `local` and `gcs` providers, mirroring the
  existing model-persistence backend split.
- A pluggable **executor**: `BaseExecutor` template + `SubprocessExecutor`
  (Phase 1) + `CloudRunJobExecutor` (Phase 2), sharing one worker entrypoint.
- A **routing heuristic** (`size_score`) selecting a compute tier, with a 4-way
  `compute_tier` override and deploy-time allowed-tier caps.
- **Per-tier** Meridian backend (TF/JAX) selection.
- Full **option parity** with the showcase optimizer.
- Config-fingerprint **reuse** (return prior completed run unless `force_rerun`).
- Migration of `RuntimeConfig` to a **validated pydantic model** covering the
  full server config (existing + new).

**Non-goals**
- **Rendered reports** (HTML/PDF/screenshot). The result is structured JSON, but
  **complete enough to reproduce everything the showcase HTML report shows**. No
  Playwright/browser dependency.
- **Model training / `sample_posterior`.** This server only runs inference on
  already-fitted models.
- **Auto-retention / TTL.** Runs are kept until explicitly deleted.
- **Streaming partial results.** Status/phase/heartbeat only; the result appears
  atomically on completion.
- **Reach & frequency optimization** (`optimize_frequency`) — a separate future
  module; this spec covers budget optimization only.

---

## 3. Architecture overview

Wiring follows the established `transport → service → meridian` pattern, plus a
new `execution/` package and a registry provider in `persistence/`.

```
agent
  │  run_optimization / get_status / get_result / list / delete
  ▼
transport/tools.py
  ▼
services/optimization_service.py
  │  validate config · fingerprint · reuse check · build run record
  ▼
execution/BaseExecutor  (template method — identical lifecycle)
  1. write record.json  status=queued        ─────────────►  REGISTRY
  2. routing.size_score → resolve compute_tier (within allowed tiers)
  3. _launch(run_id, request)   ◄── ONLY abstract method
  4. supervise: stale heartbeat & no terminal status → status=failed
        │                                   │
  SubprocessExecutor                CloudRunJobExecutor
  spawn `python -m ...worker`       jobs.run(job=cpu|gpu, env=RUN_ID,...)
        │                                   │
        └──────────────┬────────────────────┘
                       ▼
        execution/worker.py   (SHARED — same code both tiers)
          set MERIDIAN_BACKEND for this tier (before importing meridian)
          state.json running → phases → completed|failed   ──► REGISTRY
          load model (GCS/local) · BudgetOptimizer.optimize()
          build structured result · write result.json       ──► REGISTRY
                       ▼
        REGISTRY (Cloud Storage | local fs)  ◄── list/status/result READ only
```

**Key invariant:** the **worker** owns the run's `running → completed|failed`
transitions, progress, and the result write. Because both executors launch the
same worker, both write to the registry identically. The executor only does
pre-launch bookkeeping (queue record, fingerprint/reuse, routing) and
crash-detection. This is what makes "subprocess and Cloud Run behave the same and
both write to Cloud Storage" true by construction.

---

## 4. Tool surface

All tools convert domain errors to the standard error payload, as existing tools
do. Read-only tools carry READ_ONLY annotations; `run_optimization` and
`delete_optimization`/`cancel_optimization` are mutating.

| Tool | Inputs | Returns |
|---|---|---|
| `run_optimization` | `model_id`, `config: OptimizationConfig`, `label?`, `note?`, `compute_tier="auto"`, `force_rerun=false` | `{run_id, status, compute_tier_resolved, backend, size_score, reused}` |
| `get_optimization_status` | `run_id` | `{run_id, status, phase, progress_fraction?, heartbeat_at, started_at, finished_at, elapsed_seconds, compute_tier, backend, error?}` |
| `get_optimization_result` | `run_id` | full structured result (see §6); error `optimization_not_ready` if status ≠ `completed` |
| `list_optimizations` | `model_id?`, `status?`, `limit?` | list of run summaries: `{run_id, label, model_id, config_summary, status, created_at, finished_at, headline}` |
| `delete_optimization` | `run_id` | `{run_id, deleted: true}` |
| `cancel_optimization` *(Phase 2)* | `run_id` | `{run_id, status}` — best-effort stop of a queued/running run |

- **Submission semantics.** `run_optimization` validates the config, computes the
  config fingerprint, and (unless `force_rerun`) returns any existing `completed`
  run with that fingerprint (`reused: true`, original `run_id`). Otherwise it
  writes a `queued` record, resolves the tier, launches a worker, and returns
  immediately with `reused: false`. It never blocks on the optimization.
- **`config_summary`** (in listings) is a compact human/agent-legible string,
  e.g. `fixed_budget · 2023-01-02..2023-12-25 · all geos · ROAS · ±20%`.
- **`headline`** (set on completion) is a one-line result, e.g.
  `ROAS 3.1 → 3.8 at fixed $1.2M`. `null` while running.
- **Discovery.** `get_model_overview.available_tool_options.run_optimization`
  surfaces `channels`, `geos` (list or count), `use_kpi_togglable`, and the
  scenario list, so the agent reads the menu before composing a config.

---

## 5. `OptimizationConfig` (LLM-legible, full parity)

A pydantic model designed so the JSON schema the LLM sees is unambiguous. Maps
1:1 to `BudgetOptimizer.optimize()`.

### 5.1 Shape

```python
class FixedBudgetScenario(BaseModel):
    type: Literal["fixed_budget"] = "fixed_budget"
    budget: float | None = Field(
        None, gt=0,
        description="Total budget across channels for the whole selected range. "
                    "Omit to use the model's historical total spend over the range.",
        examples=[1_200_000],
    )

class TargetRoasScenario(BaseModel):
    type: Literal["target_roas"]
    target_value: float = Field(
        gt=0,
        description="Target overall ROAS (revenue per spend) the optimizer should "
                    "hit by flexing total budget. For KPI/no-revenue models this is "
                    "interpreted as a CPIK target and inverted internally.",
        examples=[2.0],
    )

class TargetMroasScenario(BaseModel):
    type: Literal["target_mroas"]
    target_value: float = Field(gt=0, description="Target marginal ROAS (mROAS).",
                                examples=[1.5])

Scenario = Annotated[
    FixedBudgetScenario | TargetRoasScenario | TargetMroasScenario,
    Field(discriminator="type"),
]

class GlobalConstraint(BaseModel):
    mode: Literal["global"] = "global"
    pct: float = Field(
        ge=0, le=1,
        description="Max fractional deviation from current spend applied to every "
                    "channel, as a fraction (0.2 = ±20%).",
        examples=[0.2],
    )

class PerChannelConstraint(BaseModel):
    mode: Literal["per_channel"]
    bounds: dict[str, ChannelBound] = Field(
        description="Per-channel lower/upper fractional bounds; must cover every "
                    "paid/RF channel. See get_model_overview for valid channels.",
    )
    # ChannelBound: {lower_pct: float[0..1], upper_pct: float[0..1]}

Constraint = Annotated[GlobalConstraint | PerChannelConstraint,
                       Field(discriminator="mode")]

class OptimizationConfig(BaseModel):
    scenario: Scenario
    constraint: Constraint = GlobalConstraint(pct=0.3)   # optimizer default
    start_date: date | None = Field(None, description="ISO start; omit for full range.")
    end_date: date | None = Field(None, description="ISO end; omit for full range.")
    selected_geos: list[str] | None = Field(
        None, description="Subset of geos; omit for all. Ignored by national models. "
                          "Valid geos: see get_model_overview.")
    use_kpi: bool | None = Field(
        None, description="Objective: false=ROAS/ROI, true=CPIK. Omit to use the "
                          "model's native objective (revenue→ROAS, no-revenue→CPIK). "
                          "Only meaningful for dual revenue+KPI models.")
```

### 5.2 Mapping to `optimize()`

- `fixed_budget` → `fixed_budget=True, budget=<budget or historical>`.
- `target_roas` → `fixed_budget=False, target_roi=<value>` (inverted to a CPIK
  target when `use_kpi`, exactly as the showcase's `_resolve_optimizer_targets`).
- `target_mroas` → `fixed_budget=False, target_mroi=<value>` (same inversion).
- `global` constraint → `spend_constraint_lower=pct, spend_constraint_upper=pct`.
- `per_channel` → channel-ordered `spend_constraint_lower/upper` lists aligned to
  the model's channel order.
- `use_kpi` resolution reuses the facade's existing `resolve_use_kpi` rule
  (revenue models default to revenue/ROAS; no-revenue default to KPI/CPIK).

### 5.3 Validation (explicit, agent-friendly errors)

- Unknown geo / channel → `"unknown channel 'tv'; valid: [...]"`.
- `per_channel` bounds not covering every paid/RF channel → explicit list of
  missing channels.
- `start_date > end_date`, or dates outside the model range → explicit message.
- `target_value <= 0`, `pct` outside `[0,1]` → pydantic field errors.

---

## 6. Run record & result payload

### 6.1 Run record (registry, JSON)

| Field | Notes |
|---|---|
| `run_id` | `<model-slug>-<YYYYMMDDHHMMSS>-<short-hash>` |
| `label` | Auto-derived from config; overridable via `run_optimization(label=…)` |
| `note` | Free-text agent intent ("what we were doing"); optional |
| `model_id`, `config`, `config_fingerprint` | Fingerprint = sha256 of normalized `{model_id, config}`; the reuse key |
| `compute_tier_requested`, `compute_tier_resolved`, `backend` | Routing + backend provenance |
| `size_score` | The heuristic score (§8) |
| `status` | `queued`/`running`/`completed`/`failed`/`canceled` |
| `phase` | Worker milestone within `running` |
| `progress_fraction` | Optional 0–1 (stretch) |
| `heartbeat_at`, `created_at`, `started_at`, `finished_at` | ISO-8601 |
| `error` | `{code, message, traceback}` on failure |
| `headline` | One-line result on completion |
| `meridian_version`, `server_version` | Provenance |

### 6.2 Result payload (structured; reproduces the HTML report)

Ported from the showcase `_build_*` helpers, minus Playwright. Floats rounded to
6 significant figures (existing convention).

```jsonc
{
  "model_id": "geo-revenue",
  "run_id": "geo-revenue-20260629T101500-ab12cd",
  "outcome_mode": "revenue",                  // "revenue" | "kpi"
  "summary": {
    "non_optimized_budget": ..., "optimized_budget": ...,
    "non_optimized_efficiency": ..., "optimized_efficiency": ...,   // ROAS | CPIK
    "non_optimized_incremental_outcome": ..., "optimized_incremental_outcome": ...
  },
  "channel_tables": {
    "initial":   [ { "channel": "...", "spend": ..., "pct_of_spend": ...,
                     "incremental_outcome": ..., "roi": ..., "mroi": ...,
                     "cpik": ..., "effectiveness": ... }, ... ],
    "optimized": [ ... same shape ... ]
  },
  "allocation":  [ { "channel": "...", "spend": ... }, ... ],       // optimized
  "spend_delta": [ { "channel": "...", "spend": ... }, ... ],       // opt - non-opt, sorted
  "response_curves": [ { "channel": "...", "spend": ..., "incremental_outcome": ... }, ... ]
}
```

Efficiency fields are ROAS-family in `revenue` mode and CPIK-family in `kpi`
mode (the showcase derives CPIK as `1/roi`); `outcome_mode` tells the agent how
to read them. `roi`/`mroi`/`cpik` columns are populated per `outcome_mode` (the
showcase shows one family at a time).

---

## 7. Registry (`persistence/optimization_run_registry.py`)

A provider interface mirroring the model-persistence split. Backend chosen by
config (§11); `local` and `gcs` implementations behind one interface.

### 7.1 Interface

```python
class OptimizationRunRegistry(abc.ABC):
    def create(self, record: OptimizationRun) -> None                  # write record.json (once)
    def write_state(self, run_id, state, *, expected_generation=None) -> None  # state.json
    def write_result(self, run_id, result) -> None               # result.json (once)
    def get_record(self, run_id) -> OptimizationRun
    def get_state(self, run_id) -> OptimizationRunState
    def get_result(self, run_id) -> dict
    def list(self, *, model_id=None, status=None, limit=None) -> list[OptimizationRunSummary]
    def delete(self, run_id) -> None
    def find_by_fingerprint(self, fingerprint) -> str | None     # index lookup
    def put_fingerprint(self, fingerprint, run_id) -> None
```

### 7.2 Layout (identical local dirs ↔ GCS prefixes)

```
<root>/                                  # OPTIMIZATION_RUNS_ROOT (local) | OPTIMIZATION_GCS_PREFIX (gcs)
  runs/<run_id>/
    record.json   # WRITE-ONCE at submit; static metadata
    state.json    # HOT+SMALL; overwritten on every transition AND heartbeat
    result.json   # WRITE-ONCE on completion; large
  index/by_fingerprint/<fingerprint>     # tiny pointer {run_id}; O(1) reuse, no scan
```

- `list()` lists `runs/` (delimiter), reads the two *small* objects per run
  (`record`+`state`); never reads `result.json`.
- Heartbeat overwrites only `state.json`. On GCS, `write_state` uses
  `if-generation-match` (optimistic concurrency) so a stale-heartbeat supervisor
  and a live worker cannot clobber each other.
- `delete()` removes `runs/<run_id>/*` and the fingerprint pointer.
- Scaling note: for thousands of runs, `index/` can grow a listing shard; not
  built now, listed as future work.

---

## 8. Execution & routing

### 8.1 `BaseExecutor` (template method)

```python
class BaseExecutor(abc.ABC):
    def submit(self, record: OptimizationRun) -> None:
        self._registry.create(record)                    # status=queued
        # routing already resolved tier into record by the service
        self._launch(record.run_id, record.to_request()) # subclass
    @abc.abstractmethod
    def _launch(self, run_id: str, request: dict) -> None: ...
    def reconcile(self, run_id: str) -> None:
        # called by status polls / a sweep: if heartbeat stale past threshold
        # and no terminal status → write_state(status=failed, error=worker_lost)
```

- **Concurrency**: a bounded gate (`OPTIMIZATION_MAX_PARALLEL`, default 2). When
  full, new runs stay `queued` and a small dispatcher launches them as slots free.
- **`SubprocessExecutor._launch`**: spawns `python -m
  google_meridian_mcp_server.execution.worker` with `RUN_ID` + registry config in
  env (like the showcase's request/result-file plumbing, but the worker writes
  the registry directly rather than a result file). Non-blocking to the async
  event loop.
- **`CloudRunJobExecutor._launch`** *(Phase 2)*: calls Cloud Run `jobs.run` on
  the CPU or GPU job (by resolved tier), passing `RUN_ID` + registry env as
  execution overrides. `reconcile` additionally cross-checks the Cloud Run
  execution status API.

### 8.2 Worker (`execution/worker.py`, shared)

1. Read `RUN_ID` + registry/backend config from env.
2. **Set `MERIDIAN_BACKEND` for this tier before importing meridian** (§9).
3. `write_state(running, phase=loading_model)`; start a daemon **heartbeat**
   thread that rewrites `state.json.heartbeat_at` every ~5–10s.
4. Load the model (from GCS or local, via the existing loader/materialization).
5. `phase=building_grid → optimizing` around `BudgetOptimizer.optimize(...)`.
6. `phase=assembling_result`: build the structured payload (§6.2).
7. `phase=uploading`: `write_result(...)`; `write_state(completed, headline=…)`.
8. On any exception: `write_state(failed, error=…)` and exit non-zero.

### 8.3 Progress model

- `status` enum + worker-emitted `phase` milestones + `heartbeat_at` make
  progress non-static without a Meridian callback.
- `get_optimization_status` returns `phase`, `heartbeat_at`, `elapsed_seconds`.
- **Stretch (Phase 2/optional):** a thin `OptimizationGrid`/grid-creation wrapper
  that knows `n_grid_rows` and writes `progress_fraction = i/n_grid_rows`.

### 8.4 Routing heuristic (`execution/routing.py`)

Cost scales with the posterior-evaluation matrix; computed from interrogator
metadata (no heavy load):

```
size_score ≈ n_geos × n_time_units × n_channels × n_posterior_samples
                                                   (= n_chains × n_draws)
```

| `size_score` | resolved tier | runs on |
|---|---|---|
| `< T_local` | `local` | subprocess on the MCP host |
| `T_local … T_gpu` | `cloud_cpu` | Cloud Run Job, CPU def |
| `≥ T_gpu` | `cloud_gpu` | Cloud Run Job, GPU (L4) def |

- `T_local`, `T_gpu` are config (`OPTIMIZATION_SIZE_THRESHOLDS`), **calibrated
  empirically** — not hardcoded magic numbers.
- The heuristic only ever resolves to an **allowed** tier (§11); if its first
  choice is disallowed it falls back to the nearest allowed tier (e.g. `local`
  disabled → `cloud_cpu`).
- `compute_tier` request overrides the heuristic: `auto` | `local` |
  `cloud_cpu` | `cloud_gpu`. `"big job but CPU"` = `cloud_cpu`.

---

## 9. Backend policy (per-tier TF/JAX)

- Backend is set **per worker, via env var**, by tier:
  `OPTIMIZATION_BACKEND_LOCAL` (default `tensorflow`),
  `OPTIMIZATION_BACKEND_CLOUD_CPU` (default `jax`),
  `OPTIMIZATION_BACKEND_CLOUD_GPU` (default `jax`).
- The worker sets `os.environ["MERIDIAN_BACKEND"]` **before importing meridian**
  (it's read at import time and is process-global). Each run is a fresh process,
  so no per-request switching is needed.
- Rationale: JAX/XLA speed + lower GPU memory where it matters (cloud, esp. GPU);
  TF on the small/local tier to avoid XLA compile cold-start on trivial runs.
- The lean MCP server itself never imports meridian for compute, so its backend
  is irrelevant.
- **Cross-backend guardrail.** Models are fit under TF; loading + optimizing them
  under JAX must be validated. A fit-under-TF → optimize-under-JAX check is a
  **hard gate** in the live-validation suite (§13).

---

## 10. Reuse / fingerprint

- `config_fingerprint = sha256(json(normalized {model_id, config}))` with sorted
  keys and canonicalized dates/geos.
- `run_optimization` (unless `force_rerun=true`) calls
  `registry.find_by_fingerprint`; on a `completed` hit it returns that run with
  `reused: true`. A `queued`/`running` hit with the same fingerprint also
  short-circuits (returns the in-flight run) to avoid duplicate compute.
- `force_rerun=true` always creates a new run (new `run_id`, same fingerprint;
  the fingerprint pointer is updated to the newest completed run).

---

## 11. Deployment configuration — full validated pydantic model

**`RuntimeConfig` migrates from a frozen dataclass to a pydantic `BaseModel`.**
All existing validation (`transport`, `persistence_backend`, required roots,
positive TTLs) is preserved with the same error messages, re-expressed as
`field_validator`/`model_validator`. `load_config()` remains the env→model
adapter (reading `os.getenv`, constructing the model); pydantic owns validation.
The full server config — existing + new — is validated in one model, failing
fast at startup.

### 11.1 New fields / env vars

**Backends**
| Env | Values | Default |
|---|---|---|
| `PERSISTENCE_BACKEND` | `local`/`gcs` | `local` (existing) |
| `REGISTRY_BACKEND` | `local`/`gcs` | follows `PERSISTENCE_BACKEND` |
| `OPTIMIZATION_RUNS_ROOT` | path | `./optimizations` (registry=local) |
| `OPTIMIZATION_GCS_PREFIX` | prefix | `optimizations/` (registry=gcs; reuses `GCS_BUCKET`) |

**Allowed executor tiers**
| Env | Values | Default |
|---|---|---|
| `OPTIMIZATION_ALLOWED_TIERS` | csv ⊂ `local,cloud_cpu,cloud_gpu` | `local` |
| `OPTIMIZATION_DEFAULT_TIER` | `auto`/a tier | `auto` |
| `OPTIMIZATION_MAX_PARALLEL` | int > 0 | `2` |
| `OPTIMIZATION_SIZE_THRESHOLDS` | `T_local,T_gpu` | tunable |

**Per-tier backend + Cloud Run** (required only if a `cloud_*` tier is allowed)
| Env | Values | Default |
|---|---|---|
| `OPTIMIZATION_BACKEND_LOCAL`/`_CLOUD_CPU`/`_CLOUD_GPU` | `tensorflow`/`jax` | `tf`/`jax`/`jax` |
| `CLOUD_RUN_PROJECT`/`_REGION` | string | — |
| `CLOUD_RUN_JOB_CPU`/`_JOB_GPU` | job name | — |

### 11.2 Startup guardrails (`model_validator`, fail fast)

- Any `cloud_*` tier allowed ⇒ `REGISTRY_BACKEND == gcs` **and** `GCS_BUCKET` +
  Cloud Run project/region/job(s) present — else a clear startup error.
- `OPTIMIZATION_DEFAULT_TIER` (if not `auto`) and every requested `compute_tier`
  must be in `OPTIMIZATION_ALLOWED_TIERS`; a disallowed request → validation
  error naming the allowed set.
- `local` not allowed ⇒ the server never runs optimization in-process (pure
  offload), matching the lean-host intent.
- `OPTIMIZATION_MAX_PARALLEL > 0`; thresholds parse to two positive ascending ints.

---

## 12. Module layout / affected files

| File | Change |
|---|---|
| `domain/models.py` | `RuntimeConfig` → pydantic `BaseModel` + validators; new tier/backend enums |
| `domain/optimization.py` *(new)* | `OptimizationConfig` (+ scenario/constraint unions), `OptimizationRun`, `OptimizationRunState`, `OptimizationRunSummary`, status/phase/tier enums |
| `config.py` | Read new env vars; build the pydantic `RuntimeConfig` |
| `persistence/optimization_run_registry.py` *(new)* | `OptimizationRunRegistry` interface + `LocalOptimizationRunRegistry` + `GcsOptimizationRunRegistry` |
| `execution/__init__.py`, `base_executor.py`, `subprocess_executor.py`, `cloud_run_executor.py`, `worker.py`, `routing.py` *(new package)* | Executors, shared worker, routing heuristic, concurrency gate |
| `meridian/optimizer_facade.py` *(new)* | Wraps `BudgetOptimizer`; ports showcase `_build_*` result helpers (no Playwright) |
| `meridian/interrogator.py` | Expose `size_score` inputs (geos, time units, channels, posterior sample count) if not already available |
| `services/optimization_service.py` *(new)* | Validate · fingerprint · reuse · route · submit · registry reads for status/result/list/delete |
| `services/analysis_service.py` | Add `run_optimization` to `get_model_overview.available_tool_options` |
| `transport/tools.py` | Register the 5 (Phase 1) / 6 (Phase 2) optimization tools |
| `server.py` | Build registry + executor(s) in lifespan; wire `OptimizationService` |
| `Dockerfile` (+ a GPU variant / two job defs) *(Phase 2)* | Worker image; JAX-on-GPU CUDA build |
| `.env.example`, `AGENTS.md`, `docs/meridian-mcp-showcase-parity.md` | Docs |

---

## 13. Testing & live validation

Follow the existing tiers (unit with xarray/pandas fakes; mocks at GCS/FastMCP/
Meridian boundaries; integration for provider filesystem behavior; contract for
tool shapes).

**Unit**
- `OptimizationConfig`: discriminated-union parsing; `optimize()` arg mapping
  (incl. KPI target inversion); validation errors (unknown channel/geo, partial
  per-channel bounds, bad dates).
- `routing.size_score` + tier resolution, incl. allowed-tier fallback.
- `OptimizationRunRegistry` (local): create/state/result/list/delete; fingerprint
  index; `list` reads only small objects.
- `BaseExecutor`: queue record, reuse short-circuit, stale-heartbeat → failed
  reconciliation; concurrency gate (queued beyond `max_parallel`).
- `optimizer_facade`: builds the structured payload from a fake
  `OptimizationResults` (revenue and KPI modes).
- `RuntimeConfig` pydantic: preserves existing checks; new guardrails
  (cloud tier ⇒ gcs registry; disallowed default tier; bad thresholds).

**Contract**
- The 5/6 tools registered with correct annotations and documented shapes
  (`run_optimization` returns the submit envelope; `get_optimization_result`
  returns the structured payload, not a columnar envelope).

**Integration**
- Subprocess executor end-to-end on a dummy fitted model → registry record
  transitions `queued→running→completed` and a readable result (local registry).

**Live validation suite** (the integration acceptance gate)
- Add a `run_optimization` happy path per variant using `local` tier +
  subprocess + local registry: submit, poll `get_optimization_status` to
  `completed`, assert the structured result keys + `outcome_mode`.
- Adversarial: unknown channel in `per_channel`, disallowed `compute_tier`,
  `get_optimization_result` before completion → `optimization_not_ready`.
- **Cross-backend gate:** at least one variant run with the worker forced to
  `MERIDIAN_BACKEND=jax` (a model fit under TF) must `complete` with a valid
  result — certifying the JAX inference path. Skipped with a logged notice if
  `jax` isn't installed in the validation environment (never silently passed).
- Reuse: a second identical submit returns `reused: true` with the same `run_id`;
  `force_rerun` creates a new one.

**Incremental live-execution gate — per executor, both model shapes.**
Every concrete `BaseExecutor` subclass is **live-tested as it is built**, not
only in a single end-of-project pass. The acceptance criterion for *each*
executor is a **full live local MCP execution test** — an in-process
`Client(mcp)` driving the real tool chain `run_optimization → poll
get_optimization_status → get_optimization_result` end to end, against **both a
national and a geo-level model** (reusing the suite's existing `national-*` and
`geo-*` fixtures, whose tiny MCMC fits keep `optimize()` fast).

- A reusable harness, `assert_live_optimization(client, executor, variant)`,
  encapsulates submit→poll→result and the result-shape/`outcome_mode` assertions
  so each executor is certified by the same checks.
- **`SubprocessExecutor` (Phase 1):** the harness runs the real worker subprocess
  against the **local** registry. Gate = green for one national **and** one geo
  fixture before the executor is considered done.
- **`CloudRunJobExecutor` (Phase 2):** the harness runs **locally** with only the
  `jobs.run` API call faked — the fake invokes the **identical worker** as a
  local process using the cloud executor's launch contract (env overrides,
  `RUN_ID`) against a **GCS-backed registry exercised through a local fake/dir
  behind `GcsOptimizationRunRegistry`**. So the worker, env-passing, registry write path,
  state transitions, heartbeat, and reconcile are all *live*; only the GCP RPC is
  stubbed. Gate = green for one national **and** one geo fixture. (A real-GCP
  smoke test against a deployed CPU job is an optional CI extension, not the
  local gate.)
- These per-executor gates run within `live_validate`; the matrix prints a
  `national`/`geo` × executor PASS/FAIL block. A new executor is not "done" until
  its national **and** geo rows pass.

Plus **one full end-to-end pass** at the close of each phase: the entire
`live_validate` matrix (every variant × every tool, both model shapes) green.

Pass bar unchanged: `uv run pytest`, `uv run ruff check src tests`, and
`uv run python -m scripts.validation.live_validate` all green.

---

## 14. Phasing

Both executors are in scope; phasing sequences implementation so Phase 1 ships
with **zero GCP dependency**.

- **Phase 1 (local, no GCP):** pydantic `RuntimeConfig`; `OptimizationConfig` +
  records; `OptimizationRunRegistry` interface + `LocalOptimizationRunRegistry`; `BaseExecutor` +
  `SubprocessExecutor`; `optimizer_facade`; routing (local tier); the 5 core
  tools; reuse/fingerprint; full unit + contract + live-validation coverage.
  **Done-gate for `SubprocessExecutor`:** the per-executor live-execution gate
  (§13) green for a national **and** a geo fixture, then the full `live_validate`
  matrix green.
- **Phase 2 (cloud):** `GcsOptimizationRunRegistry`; `CloudRunJobExecutor`; worker image(s)
  + CPU/GPU job defs; per-tier JAX backend + cross-backend gate; `cancel`;
  optional `progress_fraction`; deployment guardrails for cloud tiers.
  **Done-gate for `CloudRunJobExecutor`:** the per-executor live-execution gate
  (§13) green for a national **and** a geo fixture (local fake of `jobs.run`,
  live worker + registry), then the full `live_validate` matrix green.

Each phase becomes its own implementation plan (writing-plans). Within a phase,
an executor is built **test-first against the live gate** — its national + geo
live-execution rows must pass before the phase's end-to-end pass is attempted.

---

## 15. Open questions / future work

- **`cancel` semantics** for an in-flight Cloud Run execution (cancel the
  execution via API) vs a local subprocess (signal/kill) — detailed in Phase 2.
- **Listing at scale** (thousands of runs) → a sharded `index/` listing; deferred.
- **Reach-&-frequency optimization** as a sibling module reusing the same
  executor/registry — out of scope here.
- **`progress_fraction`** via grid-row instrumentation — optional stretch.
```

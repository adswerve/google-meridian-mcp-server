# Budget Optimization Module — Phase 2 (Cloud Run + GCS) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the local-only Phase 1 optimization module with a GCS-backed run registry, a Cloud Run Jobs executor (CPU/GPU, per-tier TF/JAX backend), worker progress heartbeats, run cancellation, `response_curves` in the result payload, and live validation that proves it works both locally (faked `jobs.run`) and against **real Cloud Run Jobs deployed to `as-dev-anze`**.

**Architecture:** Phase 1 established the invariant that the **worker** owns `running → completed|failed` transitions and the result write, while the **executor** only does pre-launch bookkeeping (queue, fingerprint/reuse, routing) and crash detection. Phase 2 keeps that invariant exactly: the *same* `execution/worker.py` runs in a local subprocess (Phase 1) or a Cloud Run container (Phase 2). Only two things become backend-aware: where the registry persists (`local` dir vs `gcs` blobs) and how the worker is launched (`subprocess.Popen` vs `jobs.run`). Because both executors launch the identical worker against the identical registry interface, "subprocess and Cloud Run behave the same" holds by construction.

**Tech Stack:** Python 3.12+, FastMCP, `google-meridian==1.7.0`, `google-cloud-run>=0.10` (new), `google-cloud-storage` (existing), pydantic v2, Cloud Run Jobs (GA, CPU + NVIDIA L4 GPU), Docker, `uv`/`pytest`/`ruff`.

## Global Constraints

These bind **every** task. Copy them into every reviewer prompt.

- **Reuse the Phase 1 invariant.** The worker owns `running → completed|failed` and the result write. Executors never write `completed`/`running` state for a run; they only write `queued` (on submit) and `failed` (on confirmed crash). Do not move result-building into an executor.
- **One worker, both tiers.** `execution/worker.py` must run unchanged in a Cloud Run container. The only per-tier inputs are env vars (`OPTIMIZATION_RUN_ID`, `MERIDIAN_BACKEND`, plus the `PERSISTENCE_BACKEND`/`REGISTRY_BACKEND`/bucket vars baked into the job definition).
- **`MERIDIAN_BACKEND` is set before importing meridian.** It is read at import time and is process-global. The worker sets `os.environ["MERIDIAN_BACKEND"]` before any `meridian`/catalog import (already true in Phase 1 `main()`); cloud launches pass it as a container env override.
- **Registry layout is identical across providers** (spec §7.2): `runs/<run_id>/{record.json,state.json,result.json}` + `index/by_fingerprint/<fingerprint>`. `record.json` and `result.json` are write-once; `state.json` is overwritten on every transition and heartbeat. `list()` reads only `record.json` + `state.json`, never `result.json`.
- **Floats rounded to 6 significant figures**, JSON-strict (no NaN/Inf) via the existing `_sig6` helper in `meridian/optimizer_facade.py`. `response_curves` follows the same rule.
- **Cloud tiers require GCS.** Any `cloud_cpu`/`cloud_gpu` in `OPTIMIZATION_ALLOWED_TIERS` ⇒ `REGISTRY_BACKEND == gcs` **and** `GCS_BUCKET` + Cloud Run project/region/job(s) present — validated at startup with a clear error (extends the Phase 1 guardrail already in `RuntimeConfig._check`).
- **Per-tier backend defaults:** `OPTIMIZATION_BACKEND_LOCAL=tensorflow`, `OPTIMIZATION_BACKEND_CLOUD_CPU=jax`, `OPTIMIZATION_BACKEND_CLOUD_GPU=jax`.
- **No Co-Authored-By trailer** on any commit (project standing rule).
- **Pass bar (unchanged):** `uv run pytest`, `uv run ruff check src tests scripts`, `uv run ruff format src tests scripts`, and `uv run python -m scripts.validation.live_validate` all green. The cloud live gate (Task 9) and the real-GCP smoke (Task 10) are additional gates.
- **GCP project for real cloud tests:** `as-dev-anze`. Real-GCP steps are opt-in (require ADC creds) and must **skip with a logged notice**, never silently pass, when creds/flags are absent.

---

## File Structure

| File | Phase 2 change |
|---|---|
| `src/google_meridian_mcp_server/domain/models.py` | `RuntimeConfig`: per-tier backend fields, Cloud Run fields, `backend_for_tier()`, extended `model_validator` guardrails |
| `src/google_meridian_mcp_server/config.py` | Read the new env vars |
| `src/google_meridian_mcp_server/meridian/optimizer_facade.py` | Add `response_curves` to `build_result` + `run` |
| `src/google_meridian_mcp_server/execution/worker.py` | Heartbeat daemon thread; phase milestones; coarse `progress_fraction` |
| `src/google_meridian_mcp_server/persistence/optimization_run_registry.py` | `GcsOptimizationRunRegistry`; `expected_generation` kwarg on `write_state`; `get_state_generation` |
| `src/google_meridian_mcp_server/execution/base_executor.py` | `cancel()` template + abstract `_cancel`/`_terminate`; expose `_reconcile_stale` for cloud |
| `src/google_meridian_mcp_server/execution/subprocess_executor.py` | Implement `_terminate` (kill the process) |
| `src/google_meridian_mcp_server/execution/cloud_run_executor.py` *(new)* | `CloudRunJobExecutor`: `_launch` via `jobs.run`, `_is_alive` via executions API, `_terminate` via cancel |
| `src/google_meridian_mcp_server/bootstrap.py` | `build_registry` returns GCS registry; `build_executor(cfg, registry)` chooses executor by allowed tiers |
| `src/google_meridian_mcp_server/services/optimization_service.py` | `cancel(run_id)`; cloud reconcile on `get_status` |
| `src/google_meridian_mcp_server/transport/tools.py` | Register `cancel_optimization` |
| `src/google_meridian_mcp_server/services/analysis_service.py` | (no change required; discovery already lists `run_optimization`) |
| `src/google_meridian_mcp_server/server.py` | Build executor via `build_executor`; startup orphan-reconcile sweep |
| `deploy/Dockerfile.worker`, `deploy/Dockerfile.worker.gpu`, `deploy/deploy_jobs.sh` *(new)* | Worker images + Cloud Run job creation for `as-dev-anze` |
| `scripts/validation/cloud_fake.py` *(new)* | In-process fake of `jobs.run` that launches the real worker locally |
| `scripts/validation/runner.py`, `scripts/validation/live_validate.py` | Cloud-executor + cross-backend live gates |
| `scripts/validation/cloud_smoke.py` *(new)* | Opt-in real Cloud Run smoke test against `as-dev-anze` |
| `README.md`, `AGENTS.md`, `.env.example`, `docs/meridian-mcp-showcase-parity.md` | Docs |
| `pyproject.toml` | Add `google-cloud-run`; optional `jax` extra |

---

## Task 1: Cloud configuration & guardrails

**Files:**
- Modify: `src/google_meridian_mcp_server/domain/models.py`
- Modify: `src/google_meridian_mcp_server/config.py`
- Modify: `pyproject.toml`
- Test: `tests/unit/test_runtime_config.py` (add cases; create if absent)

**Interfaces:**
- Consumes: existing `RuntimeConfig` (pydantic `BaseModel(frozen=True)`), `ComputeTier`, `PersistenceBackend`.
- Produces: new fields `optimization_backend_cloud_cpu`, `optimization_backend_cloud_gpu`, `cloud_run_project`, `cloud_run_region`, `cloud_run_job_cpu`, `cloud_run_job_gpu`; method `backend_for_tier(tier: str) -> str`; extended startup guardrails. Later tasks call `cfg.backend_for_tier(...)`, `cfg.cloud_run_*`.

- [ ] **Step 1: Add the dependency**

In `pyproject.toml`, add to `dependencies` (after `google-cloud-storage`):

```toml
    "google-cloud-run>=0.10,<1",
```

Then add an optional extra for the JAX backend (used by cloud workers / cross-backend tests):

```toml
[project.optional-dependencies]
dev = [
    "pytest>=9",
    "pytest-asyncio>=0.23",
    "ruff>=0.15,<1",
]
jax = [
    "jax>=0.4",
]
```

Run: `uv sync` — expect it to resolve `google-cloud-run`.

- [ ] **Step 2: Write the failing test**

Add to `tests/unit/test_runtime_config.py`:

```python
import pytest
from pydantic import ValidationError

from google_meridian_mcp_server.domain.models import RuntimeConfig


def _local_kwargs(**over):
    base = dict(persistence_backend="local", local_models_root="/models")
    base.update(over)
    return base


def test_backend_for_tier_defaults():
    cfg = RuntimeConfig(**_local_kwargs())
    assert cfg.backend_for_tier("local") == "tensorflow"
    assert cfg.backend_for_tier("cloud_cpu") == "jax"
    assert cfg.backend_for_tier("cloud_gpu") == "jax"


def test_cloud_tier_requires_gcs_registry_and_cloud_run_fields():
    # cloud tier allowed but no gcs registry -> error (Phase 1 guardrail)
    with pytest.raises(ValidationError, match="gcs registry"):
        RuntimeConfig(**_local_kwargs(optimization_allowed_tiers=("local", "cloud_cpu")))
    # gcs registry present but Cloud Run coordinates missing -> error
    with pytest.raises(ValidationError, match="CLOUD_RUN_PROJECT"):
        RuntimeConfig(
            persistence_backend="gcs",
            gcs_bucket="b",
            gcs_models_prefix="models/",
            registry_backend="gcs",
            optimization_allowed_tiers=("local", "cloud_cpu"),
        )


def test_cloud_tier_fully_configured_is_valid():
    cfg = RuntimeConfig(
        persistence_backend="gcs",
        gcs_bucket="b",
        gcs_models_prefix="models/",
        registry_backend="gcs",
        optimization_allowed_tiers=("cloud_cpu", "cloud_gpu"),
        cloud_run_project="as-dev-anze",
        cloud_run_region="us-central1",
        cloud_run_job_cpu="meridian-opt-cpu",
        cloud_run_job_gpu="meridian-opt-gpu",
    )
    assert cfg.cloud_run_project == "as-dev-anze"
```

Run: `uv run pytest tests/unit/test_runtime_config.py -v` — expect FAIL (`backend_for_tier`/fields/guardrails absent).

- [ ] **Step 3: Add the fields, method, and guardrails**

In `domain/models.py`, inside `RuntimeConfig`, add fields after `optimization_backend_local`:

```python
    optimization_backend_cloud_cpu: str = "jax"
    optimization_backend_cloud_gpu: str = "jax"
    cloud_run_project: str | None = None
    cloud_run_region: str | None = None
    cloud_run_job_cpu: str | None = None
    cloud_run_job_gpu: str | None = None
```

Add the helper method (next to `resolved_registry_backend`):

```python
    def backend_for_tier(self, tier: str) -> str:
        return {
            ComputeTier.LOCAL.value: self.optimization_backend_local,
            ComputeTier.CLOUD_CPU.value: self.optimization_backend_cloud_cpu,
            ComputeTier.CLOUD_GPU.value: self.optimization_backend_cloud_gpu,
        }[tier]

    def cloud_run_job_for_tier(self, tier: str) -> str | None:
        return {
            ComputeTier.CLOUD_CPU.value: self.cloud_run_job_cpu,
            ComputeTier.CLOUD_GPU.value: self.cloud_run_job_gpu,
        }.get(tier)
```

Extend the `model_validator(mode="after") _check`, in the existing `cloud_tiers & set(...)` block, **after** the gcs-registry check:

```python
        cloud_tiers = {ComputeTier.CLOUD_CPU.value, ComputeTier.CLOUD_GPU.value}
        allowed_cloud = cloud_tiers & set(self.optimization_allowed_tiers)
        if allowed_cloud:
            if self.resolved_registry_backend != PersistenceBackend.GCS.value:
                raise ValueError(
                    "cloud tiers require a gcs registry (set REGISTRY_BACKEND=gcs)"
                )
            if not self.cloud_run_project or not self.cloud_run_region:
                raise ValueError(
                    "cloud tiers require CLOUD_RUN_PROJECT and CLOUD_RUN_REGION"
                )
            if ComputeTier.CLOUD_CPU.value in allowed_cloud and not self.cloud_run_job_cpu:
                raise ValueError("cloud_cpu tier requires CLOUD_RUN_JOB_CPU")
            if ComputeTier.CLOUD_GPU.value in allowed_cloud and not self.cloud_run_job_gpu:
                raise ValueError("cloud_gpu tier requires CLOUD_RUN_JOB_GPU")
```

(Replace the existing 4-line `cloud_tiers` block with the above; keep the `return self` that follows.)

- [ ] **Step 4: Read the env vars**

In `config.py` `load_config()`, add to the `RuntimeConfig(...)` constructor:

```python
        optimization_backend_cloud_cpu=os.getenv("OPTIMIZATION_BACKEND_CLOUD_CPU", "jax"),
        optimization_backend_cloud_gpu=os.getenv("OPTIMIZATION_BACKEND_CLOUD_GPU", "jax"),
        cloud_run_project=os.getenv("CLOUD_RUN_PROJECT"),
        cloud_run_region=os.getenv("CLOUD_RUN_REGION"),
        cloud_run_job_cpu=os.getenv("CLOUD_RUN_JOB_CPU"),
        cloud_run_job_gpu=os.getenv("CLOUD_RUN_JOB_GPU"),
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/unit/test_runtime_config.py -v` — expect PASS.
Run: `uv run pytest` — expect the full suite still green.

- [ ] **Step 6: Commit**

```bash
git add src/google_meridian_mcp_server/domain/models.py src/google_meridian_mcp_server/config.py pyproject.toml tests/unit/test_runtime_config.py
git commit -m "feat(opt): cloud config fields, per-tier backend, startup guardrails"
```

---

## Task 2: `response_curves` in the optimizer facade

**Files:**
- Modify: `src/google_meridian_mcp_server/meridian/optimizer_facade.py`
- Test: `tests/unit/test_optimizer_facade.py`

**Interfaces:**
- Consumes: a Meridian `OptimizationResults` object exposing `.get_response_curves() -> xarray.Dataset` with dims `(channel, spend, spend_multiplier, metric)` and variable `incremental_outcome` (confirmed in `references/meridian/meridian/analysis/optimizer.py:980`).
- Produces: a new `response_curves` key in the dict returned by `build_result`/`run`, shaped `[{channel, spend, incremental_outcome}, ...]` (spec §6.2). Completes the Phase-1 deferral.

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_optimizer_facade.py` (mirror the existing fake-xarray style used for `build_result`):

```python
import numpy as np
import xarray as xr

from google_meridian_mcp_server.meridian.optimizer_facade import OptimizerFacade


def _fake_response_curves():
    # dims: channel x spend_multiplier, metric coord; var incremental_outcome
    channels = ["tv", "search"]
    multipliers = [0.0, 1.0, 2.0]
    spend = np.array([[0.0, 100.0, 200.0], [0.0, 50.0, 100.0]])
    inc = np.array([[0.0, 300.0, 450.0], [0.0, 120.0, 150.0]])
    return xr.Dataset(
        {
            "spend": (("channel", "spend_multiplier"), spend),
            "incremental_outcome": (
                ("channel", "spend_multiplier", "metric"),
                inc[:, :, None],
            ),
        },
        coords={
            "channel": channels,
            "spend_multiplier": multipliers,
            "metric": ["mean"],
        },
    )


def test_response_curve_rows_shape_and_rounding():
    rows = OptimizerFacade._response_curve_rows(_fake_response_curves())
    assert {"channel", "spend", "incremental_outcome"} == set(rows[0])
    # one row per (channel, spend_multiplier) point
    assert len(rows) == 6
    tv0 = next(r for r in rows if r["channel"] == "tv" and r["spend"] == 100.0)
    assert tv0["incremental_outcome"] == 300.0
```

Run: `uv run pytest tests/unit/test_optimizer_facade.py -k response_curve -v` — expect FAIL (`_response_curve_rows` undefined).

- [ ] **Step 2: Implement `_response_curve_rows` and thread it through**

In `optimizer_facade.py`, add the static method:

```python
    @staticmethod
    def _response_curve_rows(curves) -> list[dict[str, Any]]:
        """Flatten get_response_curves() to per-(channel, spend) points (metric=mean)."""
        data = curves
        if "metric" in getattr(data, "dims", {}):
            data = data.sel(metric="mean", drop=True)
        channels = [str(c) for c in data.coords["channel"].values.tolist()]
        rows: list[dict[str, Any]] = []
        for channel in channels:
            sub = data.sel(channel=channel)
            spends = sub["spend"].values.tolist()
            incs = sub["incremental_outcome"].values.tolist()
            for spend, inc in zip(spends, incs):
                rows.append(
                    {
                        "channel": channel,
                        "spend": _sig6(float(spend)),
                        "incremental_outcome": _sig6(float(inc)),
                    }
                )
        return rows
```

Change `build_result` to accept and emit response curves:

```python
    @staticmethod
    def build_result(nonopt, opt, *, use_kpi: bool, response_curves=None) -> dict[str, Any]:
        outcome_mode = "kpi" if use_kpi else "revenue"
        result = {
            "outcome_mode": outcome_mode,
            "summary": OptimizerFacade._summary(nonopt, opt, use_kpi),
            "channel_tables": {
                "initial": OptimizerFacade._channel_rows(nonopt, use_kpi),
                "optimized": OptimizerFacade._channel_rows(opt, use_kpi),
            },
            "allocation": OptimizerFacade._allocation(opt),
            "spend_delta": OptimizerFacade._spend_delta(nonopt, opt),
        }
        if response_curves is not None:
            result["response_curves"] = OptimizerFacade._response_curve_rows(
                response_curves
            )
        return result
```

In `run`, compute and pass the curves (guarded so a curve failure never fails the whole optimization):

```python
        results = budget_optimizer.optimize(**kwargs)
        try:
            curves = results.get_response_curves()
        except Exception:  # noqa: BLE001 - response curves are best-effort enrichment
            curves = None
        return self.build_result(
            results.nonoptimized_data,
            results.optimized_data,
            use_kpi=use_kpi,
            response_curves=curves,
        )
```

- [ ] **Step 3: Run tests**

Run: `uv run pytest tests/unit/test_optimizer_facade.py -v` — expect PASS (existing build_result tests still pass; new ones green).

- [ ] **Step 4: Commit**

```bash
git add src/google_meridian_mcp_server/meridian/optimizer_facade.py tests/unit/test_optimizer_facade.py
git commit -m "feat(opt): add response_curves to structured optimization result"
```

---

## Task 3: Worker heartbeat thread + phase milestones

**Files:**
- Modify: `src/google_meridian_mcp_server/execution/worker.py`
- Test: `tests/unit/test_worker.py`

**Interfaces:**
- Consumes: `OptimizationRunRegistry.write_state`, `RunPhase` (`LOADING_MODEL`, `BUILDING_GRID`, `OPTIMIZING`, `ASSEMBLING_RESULT`, `UPLOADING`), `OptimizationRunState`.
- Produces: a background daemon that rewrites `state.json.heartbeat_at` every `heartbeat_interval` seconds while `optimize()` runs, plus a coarse `progress_fraction` advanced per phase. This is what makes cloud-tier stale-heartbeat reconciliation (Task 5/7) a valid crash signal — without it a multi-minute cloud run would look dead.

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_worker.py` a test that the worker emits at least one heartbeat *during* a slow optimize and reaches `completed`:

```python
import time
from typing import Any

from google_meridian_mcp_server.domain.optimization import RunStatus
from google_meridian_mcp_server.execution.worker import run_worker


class _RecordingRegistry:
    def __init__(self, record):
        self._record = record
        self.states: list[Any] = []

    def get_record(self, run_id):
        return self._record

    def write_state(self, state):
        self.states.append(state)

    def write_result(self, run_id, result):
        pass


class _SlowFacade:
    def run(self, config):
        time.sleep(0.6)  # longer than the test heartbeat interval
        return {"outcome_mode": "revenue", "summary": {}}


class _Catalog:
    def get_optimizer_facade(self, model_id):
        return _SlowFacade()


def test_worker_emits_heartbeats_during_optimize(make_run_record):
    record = make_run_record()  # fixture: a minimal OptimizationRun
    registry = _RecordingRegistry(record)
    rc = run_worker(
        record.run_id,
        registry=registry,
        catalog=_Catalog(),
        backend="tensorflow",
        heartbeat_interval=0.2,
    )
    assert rc == 0
    heartbeats = [s.heartbeat_at for s in registry.states if s.heartbeat_at]
    # initial running write + >=1 background heartbeat + terminal
    assert len(heartbeats) >= 3
    assert registry.states[-1].status == RunStatus.COMPLETED
```

> If `make_run_record` is not already a fixture, add it to `tests/conftest.py` building a minimal `OptimizationRun` (reuse the construction from the Phase 1 worker tests).

Run: `uv run pytest tests/unit/test_worker.py -k heartbeat -v` — expect FAIL (`heartbeat_interval` param + thread absent).

- [ ] **Step 2: Implement the heartbeat thread**

Rewrite `run_worker` in `worker.py` to spawn a daemon heartbeat thread around the optimize call. Replace the body with:

```python
import threading


def run_worker(
    run_id: str,
    *,
    registry: OptimizationRunRegistry,
    catalog: Any,
    backend: str,
    heartbeat_interval: float = 8.0,
) -> int:
    # NOTE: `backend` is applied via MERIDIAN_BACKEND before the meridian import
    # in main(); kept in the signature for provenance/symmetry.
    record = registry.get_record(run_id)
    started = _now()
    registry.write_state(
        OptimizationRunState(
            run_id=run_id,
            status=RunStatus.RUNNING,
            phase=RunPhase.LOADING_MODEL,
            progress_fraction=0.05,
            started_at=started,
            heartbeat_at=started,
        )
    )

    stop = threading.Event()
    phase_box = {"phase": RunPhase.LOADING_MODEL, "progress": 0.05}

    def _beat() -> None:
        while not stop.wait(heartbeat_interval):
            registry.write_state(
                OptimizationRunState(
                    run_id=run_id,
                    status=RunStatus.RUNNING,
                    phase=phase_box["phase"],
                    progress_fraction=phase_box["progress"],
                    started_at=started,
                    heartbeat_at=_now(),
                )
            )

    beat = threading.Thread(target=_beat, daemon=True)
    beat.start()
    try:
        facade = catalog.get_optimizer_facade(record.model_id)
        phase_box["phase"] = RunPhase.OPTIMIZING
        phase_box["progress"] = 0.3
        registry.write_state(
            OptimizationRunState(
                run_id=run_id,
                status=RunStatus.RUNNING,
                phase=RunPhase.OPTIMIZING,
                progress_fraction=0.3,
                started_at=started,
                heartbeat_at=_now(),
            )
        )
        result = facade.run(record.config)
        phase_box["phase"] = RunPhase.UPLOADING
        phase_box["progress"] = 0.95
        registry.write_result(run_id, result)
        registry.write_state(
            OptimizationRunState(
                run_id=run_id,
                status=RunStatus.COMPLETED,
                progress_fraction=1.0,
                started_at=started,
                finished_at=_now(),
                headline=_headline(result),
            )
        )
        return 0
    except Exception as exc:  # noqa: BLE001 - worker boundary: record then exit non-zero
        registry.write_state(
            OptimizationRunState(
                run_id=run_id,
                status=RunStatus.FAILED,
                started_at=started,
                finished_at=_now(),
                error={
                    "code": "optimization_failed",
                    "message": str(exc),
                    "traceback": traceback.format_exc(),
                },
            )
        )
        return 1
    finally:
        stop.set()
        beat.join(timeout=1.0)
```

Keep `main()` unchanged except passing the configured interval (optional): leave the default — `main()` calls `run_worker(...)` without `heartbeat_interval`, using the 8.0s default.

- [ ] **Step 3: Run tests**

Run: `uv run pytest tests/unit/test_worker.py -v` — expect PASS (existing worker tests + new heartbeat test). The existing Phase 1 worker tests assert state transitions; confirm they still hold (status order RUNNING→…→COMPLETED, FAILED shape unchanged).

- [ ] **Step 4: Commit**

```bash
git add src/google_meridian_mcp_server/execution/worker.py tests/unit/test_worker.py tests/conftest.py
git commit -m "feat(opt): worker heartbeat thread + phase/progress milestones"
```

---

## Task 4: `GcsOptimizationRunRegistry`

**Files:**
- Modify: `src/google_meridian_mcp_server/persistence/optimization_run_registry.py`
- Modify: `src/google_meridian_mcp_server/bootstrap.py`
- Test: `tests/integration/test_gcs_optimization_registry.py`

**Interfaces:**
- Consumes: `OptimizationRun`, `OptimizationRunState`, `OptimizationRunSummary`, `RunStatus`, `build_config_summary` (existing); a GCS client (injectable for tests).
- Produces: `GcsOptimizationRunRegistry(bucket, prefix, *, client_factory=None)` implementing the full `OptimizationRunRegistry` ABC over blobs, with the **identical** 3-file + fingerprint-index layout; an `expected_generation` kwarg on `write_state` and a `get_state_generation(run_id) -> int | None` method added to the ABC and the local impl (local returns `None`, ignores the kwarg). `bootstrap.build_registry` returns it when `resolved_registry_backend == gcs`.

- [ ] **Step 1: Extend the ABC (non-breaking)**

In `optimization_run_registry.py`, change the abstract `write_state` signature and add `get_state_generation`:

```python
    @abc.abstractmethod
    def write_state(
        self, state: OptimizationRunState, *, expected_generation: int | None = None
    ) -> None: ...
    @abc.abstractmethod
    def get_state_generation(self, run_id: str) -> int | None: ...
```

Update `LocalOptimizationRunRegistry.write_state` to accept and ignore the kwarg, and add the method:

```python
    def write_state(self, state, *, expected_generation=None):
        d = self._run_dir(state.run_id)
        if not d.is_dir():
            raise RunNotFoundError(state.run_id)
        _atomic_write(d / "state.json", state.model_dump_json(indent=2))

    def get_state_generation(self, run_id: str) -> int | None:
        return None  # local fs has no generation/precondition concept
```

Update the Phase-1 callers that pass `write_state(state)` positionally — they remain valid (kwarg is optional).

- [ ] **Step 2: Write the failing integration test (in-memory fake GCS)**

Create `tests/integration/test_gcs_optimization_registry.py`. Use a minimal in-memory fake that mimics the `google-cloud-storage` surface the registry uses (`bucket(name)`, `blob(name)`, `upload_from_string(text, if_generation_match=...)`, `download_as_text()`, `exists()`, `delete()`, `generation`, `list_blobs(prefix=..., delimiter=...)`):

```python
import pytest

from google_meridian_mcp_server.domain.optimization import (
    OptimizationConfig, OptimizationRun, OptimizationRunState, RunStatus,
)
from google_meridian_mcp_server.persistence.optimization_run_registry import (
    GcsOptimizationRunRegistry, ResultNotReadyError,
)
from tests.fakes.fake_gcs import FakeGcsClient  # add this fake (see Step 3)


def _run(run_id="m-1"):
    return OptimizationRun(
        run_id=run_id, label="l", model_id="m",
        config=OptimizationConfig.model_validate({"scenario": {"type": "fixed_budget"}}),
        config_fingerprint="fp1", compute_tier_requested="auto",
        compute_tier_resolved="cloud_cpu", backend="jax", size_score=1,
        created_at="2026-06-30T00:00:00+00:00", meridian_version="1.7.0",
        server_version="0.1.0",
    )


@pytest.fixture
def registry():
    client = FakeGcsClient()
    return GcsOptimizationRunRegistry("bucket", "optimizations/", client_factory=lambda: client)


def test_create_state_result_roundtrip(registry):
    run = _run()
    registry.create(run)
    registry.write_state(OptimizationRunState(run_id="m-1", status=RunStatus.RUNNING))
    with pytest.raises(ResultNotReadyError):
        registry.get_result("m-1")
    registry.write_result("m-1", {"outcome_mode": "revenue"})
    assert registry.get_result("m-1")["outcome_mode"] == "revenue"
    assert registry.get_record("m-1").model_id == "m"
    assert registry.get_state("m-1").status == RunStatus.RUNNING


def test_list_reads_only_small_blobs_and_filters(registry):
    registry.create(_run("m-1"))
    registry.write_state(OptimizationRunState(run_id="m-1", status=RunStatus.COMPLETED))
    registry.create(_run("m-2"))
    registry.write_state(OptimizationRunState(run_id="m-2", status=RunStatus.RUNNING))
    completed = registry.list(status=RunStatus.COMPLETED)
    assert [s.run_id for s in completed] == ["m-1"]
    assert registry.client.reads_of("result.json") == 0  # never reads result on list


def test_fingerprint_index_and_delete(registry):
    run = _run()
    registry.create(run)
    registry.put_fingerprint("fp1", "m-1")
    assert registry.find_by_fingerprint("fp1") == "m-1"
    registry.delete("m-1")
    assert registry.find_by_fingerprint("fp1") is None


def test_write_state_generation_precondition(registry):
    registry.create(_run())
    registry.write_state(OptimizationRunState(run_id="m-1", status=RunStatus.RUNNING))
    gen = registry.get_state_generation("m-1")
    # a competing write bumps the generation
    registry.write_state(OptimizationRunState(run_id="m-1", status=RunStatus.RUNNING))
    with pytest.raises(Exception):
        registry.write_state(
            OptimizationRunState(run_id="m-1", status=RunStatus.FAILED),
            expected_generation=gen,
        )
```

Run: `uv run pytest tests/integration/test_gcs_optimization_registry.py -v` — expect FAIL (registry + fake absent).

- [ ] **Step 3: Add the in-memory fake GCS**

Create `tests/fakes/__init__.py` (empty) and `tests/fakes/fake_gcs.py`:

```python
"""Minimal in-memory stand-in for the google-cloud-storage surface we use."""

from __future__ import annotations


class _PreconditionFailed(Exception):
    pass


class FakeBlob:
    def __init__(self, store: dict, name: str, counters: dict):
        self._store = store
        self.name = name
        self._counters = counters

    @property
    def generation(self):
        entry = self._store.get(self.name)
        return entry[1] if entry else None

    def exists(self):
        return self.name in self._store

    def upload_from_string(self, text, *, if_generation_match=None, **_):
        cur = self._store.get(self.name)
        cur_gen = cur[1] if cur else 0
        if if_generation_match is not None and if_generation_match != cur_gen:
            raise _PreconditionFailed(self.name)
        self._store[self.name] = (text, cur_gen + 1)

    def download_as_text(self):
        self._counters[self.name.rsplit("/", 1)[-1]] = (
            self._counters.get(self.name.rsplit("/", 1)[-1], 0) + 1
        )
        return self._store[self.name][0]

    def delete(self):
        self._store.pop(self.name, None)


class FakeBucket:
    def __init__(self, store, counters):
        self._store = store
        self._counters = counters

    def blob(self, name):
        return FakeBlob(self._store, name, self._counters)

    def list_blobs(self, prefix="", delimiter=None):
        names = [n for n in self._store if n.startswith(prefix)]
        return [FakeBlob(self._store, n, self._counters) for n in names]


class FakeGcsClient:
    def __init__(self):
        self._store: dict = {}
        self._counters: dict = {}

    def bucket(self, name):
        return FakeBucket(self._store, self._counters)

    def reads_of(self, basename: str) -> int:
        return self._counters.get(basename, 0)
```

Expose `client` on the registry (`self.client`) so the test can call `registry.client.reads_of(...)`.

- [ ] **Step 4: Implement `GcsOptimizationRunRegistry`**

Append to `optimization_run_registry.py`. Mirror the local layout; raise `_PreconditionFailed`-equivalent on generation mismatch (catch `google.api_core.exceptions.PreconditionFailed` in production; the fake raises a generic `Exception`, which the test expects):

```python
class GcsOptimizationRunRegistry(OptimizationRunRegistry):
    def __init__(self, bucket: str, prefix: str, *, client_factory=None) -> None:
        self._bucket_name = bucket
        self._prefix = prefix.rstrip("/")
        self._client_factory = client_factory or self._default_client
        self.client = self._client_factory()

    @staticmethod
    def _default_client():
        from google.cloud import storage  # lazy import
        return storage.Client()

    def _bucket(self):
        return self.client.bucket(self._bucket_name)

    def _run_prefix(self, run_id: str) -> str:
        return f"{self._prefix}/runs/{run_id}"

    def _blob(self, path: str):
        return self._bucket().blob(path)

    def create(self, run: OptimizationRun) -> None:
        self._blob(f"{self._run_prefix(run.run_id)}/record.json").upload_from_string(
            run.model_dump_json(indent=2)
        )

    def write_state(self, state, *, expected_generation=None) -> None:
        blob = self._blob(f"{self._run_prefix(state.run_id)}/state.json")
        kwargs = {}
        if expected_generation is not None:
            kwargs["if_generation_match"] = expected_generation
        blob.upload_from_string(state.model_dump_json(indent=2), **kwargs)

    def write_result(self, run_id: str, result: dict) -> None:
        self._blob(f"{self._run_prefix(run_id)}/result.json").upload_from_string(
            json.dumps(result, indent=2)
        )

    def get_record(self, run_id: str) -> OptimizationRun:
        blob = self._blob(f"{self._run_prefix(run_id)}/record.json")
        if not blob.exists():
            raise RunNotFoundError(run_id)
        return OptimizationRun.model_validate_json(blob.download_as_text())

    def get_state(self, run_id: str) -> OptimizationRunState:
        blob = self._blob(f"{self._run_prefix(run_id)}/state.json")
        if not blob.exists():
            if not self._blob(f"{self._run_prefix(run_id)}/record.json").exists():
                raise RunNotFoundError(run_id)
            return OptimizationRunState(run_id=run_id, status=RunStatus.QUEUED)
        return OptimizationRunState.model_validate_json(blob.download_as_text())

    def get_state_generation(self, run_id: str) -> int | None:
        blob = self._blob(f"{self._run_prefix(run_id)}/state.json")
        return blob.generation if blob.exists() else None

    def get_result(self, run_id: str) -> dict:
        state = self.get_state(run_id)
        blob = self._blob(f"{self._run_prefix(run_id)}/result.json")
        if not blob.exists():
            raise ResultNotReadyError(run_id, state.status.value)
        return json.loads(blob.download_as_text())

    def list(self, *, model_id=None, status=None, limit=None):
        prefix = f"{self._prefix}/runs/"
        record_blobs = [
            b for b in self._bucket().list_blobs(prefix=prefix)
            if b.name.endswith("/record.json")
        ]
        summaries: list[OptimizationRunSummary] = []
        for b in record_blobs:
            run = OptimizationRun.model_validate_json(b.download_as_text())
            if model_id is not None and run.model_id != model_id:
                continue
            state = self.get_state(run.run_id)
            if status is not None and state.status != status:
                continue
            summaries.append(
                OptimizationRunSummary(
                    run_id=run.run_id, label=run.label, model_id=run.model_id,
                    config_summary=build_config_summary(run), status=state.status,
                    created_at=run.created_at, finished_at=state.finished_at,
                    headline=state.headline,
                )
            )
        summaries.sort(key=lambda s: s.created_at, reverse=True)
        return summaries[:limit] if limit else summaries

    def delete(self, run_id: str) -> None:
        prefix = self._run_prefix(run_id)
        record = self._blob(f"{prefix}/record.json")
        if not record.exists():
            raise RunNotFoundError(run_id)
        fp = OptimizationRun.model_validate_json(record.download_as_text()).config_fingerprint
        pointer = self._blob(f"{self._prefix}/index/by_fingerprint/{fp}")
        if pointer.exists() and pointer.download_as_text().strip() == run_id:
            pointer.delete()
        for name in (f"{prefix}/record.json", f"{prefix}/state.json", f"{prefix}/result.json"):
            blob = self._blob(name)
            if blob.exists():
                blob.delete()

    def find_by_fingerprint(self, fingerprint: str) -> str | None:
        blob = self._blob(f"{self._prefix}/index/by_fingerprint/{fingerprint}")
        return blob.download_as_text().strip() if blob.exists() else None

    def put_fingerprint(self, fingerprint: str, run_id: str) -> None:
        self._blob(f"{self._prefix}/index/by_fingerprint/{fingerprint}").upload_from_string(run_id)
```

- [ ] **Step 5: Wire `build_registry`**

In `bootstrap.py`, replace the Phase-1 stub:

```python
def build_registry(cfg: RuntimeConfig) -> OptimizationRunRegistry:
    if cfg.resolved_registry_backend == PersistenceBackend.GCS.value:
        from google_meridian_mcp_server.persistence.optimization_run_registry import (
            GcsOptimizationRunRegistry,
        )
        return GcsOptimizationRunRegistry(cfg.gcs_bucket, cfg.optimization_gcs_prefix)
    return LocalOptimizationRunRegistry(cfg.optimization_runs_root)
```

- [ ] **Step 6: Run tests**

Run: `uv run pytest tests/integration/test_gcs_optimization_registry.py tests/unit -v` — expect PASS. Confirm the Phase-1 local-registry tests still pass (the `write_state` kwarg is backward-compatible).

- [ ] **Step 7: Commit**

```bash
git add src/google_meridian_mcp_server/persistence/optimization_run_registry.py src/google_meridian_mcp_server/bootstrap.py tests/integration/test_gcs_optimization_registry.py tests/fakes/
git commit -m "feat(opt): GCS run registry with generation-guarded state writes"
```

---

## Task 5: `CloudRunJobExecutor`

**Files:**
- Modify: `src/google_meridian_mcp_server/execution/base_executor.py`
- Create: `src/google_meridian_mcp_server/execution/cloud_run_executor.py`
- Test: `tests/unit/test_cloud_run_executor.py`

**Interfaces:**
- Consumes: `BaseExecutor` (gate/pump/reap/reconcile), `OptimizationRun`, the `google.cloud.run_v2` types (`JobsClient`, `ExecutionsClient`, `RunJobRequest`, `EnvVar`), `RuntimeConfig.cloud_run_*` + `backend_for_tier`.
- Produces: `CloudRunJobExecutor(registry, *, cfg, max_parallel, heartbeat_stale_seconds, jobs_client=None, executions_client=None)`. `_launch` calls `jobs.run` on the per-tier job with env overrides (`OPTIMIZATION_RUN_ID`, `MERIDIAN_BACKEND`); the handle is the created Execution resource name. `_is_alive` queries the executions API. Crash detection uses `_reconcile_stale` (heartbeats from Task 3 make this valid).

- [ ] **Step 1: Add a `_reap` hook for cloud reconciliation in `BaseExecutor`**

The Phase-1 `_reap` intentionally does **not** call `_reconcile_stale` for alive handles (correct for local). For cloud, a handle that is no longer alive *or* whose heartbeat is stale should fail the run. Add an overridable hook so the cloud subclass can opt into stale reconciliation without changing local behavior. In `base_executor.py`, factor the dead-handle branch:

```python
    def _reap(self) -> None:
        for run_id, handle in list(self._handles.items()):
            if self._is_alive(handle):
                self._on_alive(run_id)
                continue
            del self._handles[run_id]
            self._fail_if_unfinished(run_id, "worker exited without writing a result")

    def _on_alive(self, run_id: str) -> None:
        """Hook: local tier no-ops; cloud tier checks stale heartbeats."""
        return

    def _fail_if_unfinished(self, run_id: str, message: str) -> None:
        state = self._registry.get_state(run_id)
        if state.status in (RunStatus.RUNNING, RunStatus.QUEUED):
            self._registry.write_state(
                OptimizationRunState(
                    run_id=run_id,
                    status=RunStatus.FAILED,
                    error={"code": "worker_lost", "message": message},
                )
            )
```

Keep `_reconcile_stale` but have it call `_fail_if_unfinished`-style guarded write using `expected_generation` (so a live heartbeat between read and write rejects the false failure):

```python
    def _reconcile_stale(self, run_id: str) -> None:
        gen = self._registry.get_state_generation(run_id)
        state = self._registry.get_state(run_id)
        if state.status != RunStatus.RUNNING or not state.heartbeat_at:
            return
        last = datetime.fromisoformat(state.heartbeat_at)
        age = (datetime.now(timezone.utc) - last).total_seconds()
        if age > self._stale_seconds:
            try:
                self._registry.write_state(
                    OptimizationRunState(
                        run_id=run_id,
                        status=RunStatus.FAILED,
                        error={"code": "worker_lost", "message": f"heartbeat stale ({int(age)}s)"},
                    ),
                    expected_generation=gen,
                )
            except Exception:  # noqa: BLE001 - precondition failed => worker still alive
                return
            self._handles.pop(run_id, None)
```

- [ ] **Step 2: Write the failing test (fake clients)**

Create `tests/unit/test_cloud_run_executor.py`:

```python
from types import SimpleNamespace

from google_meridian_mcp_server.domain.optimization import (
    OptimizationConfig, OptimizationRun, RunStatus, OptimizationRunState,
)
from google_meridian_mcp_server.execution.cloud_run_executor import CloudRunJobExecutor


def _run(tier="cloud_cpu"):
    return OptimizationRun(
        run_id="m-1", label="l", model_id="m",
        config=OptimizationConfig.model_validate({"scenario": {"type": "fixed_budget"}}),
        config_fingerprint="fp", compute_tier_requested="auto",
        compute_tier_resolved=tier, backend="jax", size_score=1,
        created_at="2026-06-30T00:00:00+00:00", meridian_version="1.7.0", server_version="0.1.0",
    )


class _FakeJobs:
    def __init__(self):
        self.calls = []

    def run_job(self, request):
        self.calls.append(request)
        return SimpleNamespace(metadata=SimpleNamespace(name="exec-123"))


class _FakeExecutions:
    def __init__(self, alive=True):
        self.alive = alive

    def get_execution(self, name):
        # completion_time empty -> alive
        return SimpleNamespace(completion_time=None if self.alive else "2026-06-30T00:01:00Z")


class _Registry:
    def __init__(self):
        self.states = {}

    def write_state(self, state, *, expected_generation=None):
        self.states[state.run_id] = state

    def get_record(self, run_id):
        return _run()

    def get_state(self, run_id):
        return self.states.get(run_id, OptimizationRunState(run_id=run_id, status=RunStatus.QUEUED))

    def get_state_generation(self, run_id):
        return 1


def _cfg():
    from google_meridian_mcp_server.domain.models import RuntimeConfig
    return RuntimeConfig(
        persistence_backend="gcs", gcs_bucket="b", gcs_models_prefix="m/",
        registry_backend="gcs",
        optimization_allowed_tiers=("cloud_cpu", "cloud_gpu"),
        cloud_run_project="as-dev-anze", cloud_run_region="us-central1",
        cloud_run_job_cpu="opt-cpu", cloud_run_job_gpu="opt-gpu",
    )


def test_launch_calls_run_job_with_env_overrides():
    jobs = _FakeJobs()
    ex = CloudRunJobExecutor(
        _Registry(), cfg=_cfg(), max_parallel=2, heartbeat_stale_seconds=60,
        jobs_client=jobs, executions_client=_FakeExecutions(),
    )
    ex.submit(_run("cloud_cpu"))
    assert len(jobs.calls) == 1
    req = jobs.calls[0]
    assert "opt-cpu" in req.name  # cpu job selected by tier
    env_names = {e.name for e in req.overrides.container_overrides[0].env}
    assert {"OPTIMIZATION_RUN_ID", "MERIDIAN_BACKEND"} <= env_names


def test_is_alive_reflects_execution_completion():
    ex = CloudRunJobExecutor(
        _Registry(), cfg=_cfg(), max_parallel=2, heartbeat_stale_seconds=60,
        jobs_client=_FakeJobs(), executions_client=_FakeExecutions(alive=False),
    )
    assert ex._is_alive("exec-123") is False
```

Run: `uv run pytest tests/unit/test_cloud_run_executor.py -v` — expect FAIL (module absent).

- [ ] **Step 3: Implement the executor**

Create `cloud_run_executor.py`:

```python
"""Executor that runs the worker as a Cloud Run Job execution."""

from __future__ import annotations

from typing import Any

from google_meridian_mcp_server.domain.models import RuntimeConfig
from google_meridian_mcp_server.domain.optimization import OptimizationRun
from google_meridian_mcp_server.execution.base_executor import BaseExecutor
from google_meridian_mcp_server.persistence.optimization_run_registry import (
    OptimizationRunRegistry,
)


class CloudRunJobExecutor(BaseExecutor):
    def __init__(
        self,
        registry: OptimizationRunRegistry,
        *,
        cfg: RuntimeConfig,
        max_parallel: int,
        heartbeat_stale_seconds: int,
        jobs_client: Any | None = None,
        executions_client: Any | None = None,
    ) -> None:
        super().__init__(
            registry,
            max_parallel=max_parallel,
            heartbeat_stale_seconds=heartbeat_stale_seconds,
        )
        self._cfg = cfg
        self._jobs = jobs_client or self._default_jobs_client()
        self._executions = executions_client or self._default_executions_client()

    @staticmethod
    def _default_jobs_client():
        from google.cloud import run_v2
        return run_v2.JobsClient()

    @staticmethod
    def _default_executions_client():
        from google.cloud import run_v2
        return run_v2.ExecutionsClient()

    def _job_name(self, tier: str) -> str:
        job = self._cfg.cloud_run_job_for_tier(tier)
        return (
            f"projects/{self._cfg.cloud_run_project}"
            f"/locations/{self._cfg.cloud_run_region}/jobs/{job}"
        )

    def _launch(self, run: OptimizationRun) -> Any:
        from google.cloud import run_v2

        tier = run.compute_tier_resolved
        backend = self._cfg.backend_for_tier(tier)
        env = [
            run_v2.EnvVar(name="OPTIMIZATION_RUN_ID", value=run.run_id),
            run_v2.EnvVar(name="MERIDIAN_BACKEND", value=backend),
        ]
        request = run_v2.RunJobRequest(
            name=self._job_name(tier),
            overrides=run_v2.RunJobRequest.Overrides(
                container_overrides=[
                    run_v2.RunJobRequest.Overrides.ContainerOverride(env=env)
                ]
            ),
        )
        operation = self._jobs.run_job(request)
        # Do NOT block on operation.result(); the worker drives the registry.
        return operation.metadata.name  # the Execution resource name

    def _is_alive(self, handle: Any) -> bool:
        execution = self._executions.get_execution(handle)
        return not getattr(execution, "completion_time", None)

    def _on_alive(self, run_id: str) -> None:
        # Remote liveness is coarse; stale heartbeat is the authoritative crash signal.
        self._reconcile_stale(run_id)

    def _terminate(self, handle: Any) -> None:
        # Best-effort cancel of the running execution (used by cancel_optimization).
        try:
            self._executions.cancel_execution(name=handle)
        except Exception:  # noqa: BLE001 - best effort
            pass
```

> Note for the implementer: `RunJobRequest` types live under `google.cloud.run_v2`. If `operation.metadata.name` is not populated in your client version, fall back to `operation.metadata.execution` or the operation name — assert the chosen attribute in the live gate (Task 9), which exercises the real launch contract through the fake.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_cloud_run_executor.py tests/unit/test_base_executor.py -v` — expect PASS. Confirm the refactored `_reap` keeps the Phase-1 base-executor tests green (local no-op `_on_alive`).

- [ ] **Step 5: Commit**

```bash
git add src/google_meridian_mcp_server/execution/base_executor.py src/google_meridian_mcp_server/execution/cloud_run_executor.py tests/unit/test_cloud_run_executor.py
git commit -m "feat(opt): Cloud Run Jobs executor + cloud-tier stale reconciliation"
```

---

## Task 6: `cancel_optimization`

**Files:**
- Modify: `src/google_meridian_mcp_server/execution/base_executor.py`
- Modify: `src/google_meridian_mcp_server/execution/subprocess_executor.py`
- Modify: `src/google_meridian_mcp_server/services/optimization_service.py`
- Modify: `src/google_meridian_mcp_server/transport/tools.py`
- Test: `tests/unit/test_optimization_service.py`, `tests/contract/test_optimization_tools.py`

**Interfaces:**
- Consumes: `BaseExecutor` handle map; `RunStatus.CANCELED`; the cloud `_terminate` (Task 5) and a new subprocess `_terminate`.
- Produces: `BaseExecutor.cancel(run_id)` (terminate handle if tracked, then write `canceled` state if not already terminal); `OptimizationService.cancel(run_id)`; the `cancel_optimization` MCP tool (mutating, no read-only annotation).

- [ ] **Step 1: Write the failing tests**

Service test (add to `tests/unit/test_optimization_service.py`):

```python
def test_cancel_marks_canceled_and_terminates(...):
    # build a service over a fake registry+executor with one RUNNING run
    out = service.cancel("m-1")
    assert out == {"run_id": "m-1", "status": "canceled"}
    assert fake_executor.terminated == ["m-1"]
    assert registry.get_state("m-1").status == RunStatus.CANCELED
```

Contract test (add to `tests/contract/test_optimization_tools.py`):

```python
@pytest.mark.asyncio
async def test_cancel_tool_registered_not_readonly():
    mcp = create_server()
    by_name = {t.name: t for t in await mcp.list_tools()}
    assert "cancel_optimization" in by_name
    assert by_name["cancel_optimization"].annotations.readOnlyHint is not True
```

Run both — expect FAIL.

- [ ] **Step 2: Add `cancel` to `BaseExecutor` and `_terminate` to subclasses**

In `base_executor.py`:

```python
    @abc.abstractmethod
    def _terminate(self, handle: Any) -> None: ...

    def cancel(self, run_id: str) -> None:
        handle = self._handles.pop(run_id, None)
        if handle is not None:
            self._terminate(handle)
        try:
            self._queue.remove(run_id)
        except ValueError:
            pass
        state = self._registry.get_state(run_id)
        if state.status in (RunStatus.QUEUED, RunStatus.RUNNING):
            self._registry.write_state(
                OptimizationRunState(run_id=run_id, status=RunStatus.CANCELED)
            )
```

In `subprocess_executor.py` add:

```python
    def _terminate(self, handle: Any) -> None:
        handle.terminate()
```

(`CloudRunJobExecutor._terminate` already added in Task 5.)

- [ ] **Step 3: Add `OptimizationService.cancel` and the tool**

In `optimization_service.py`:

```python
    def cancel(self, run_id: str) -> dict[str, Any]:
        self._registry.get_record(run_id)  # raises RunNotFoundError if unknown
        self._executor.cancel(run_id)
        return {"run_id": run_id, "status": RunStatus.CANCELED.value}
```

In `transport/tools.py`, register (mutating — no read-only annotation):

```python
    @mcp.tool
    async def cancel_optimization(
        run_id: Annotated[str, Field(min_length=1, description="run_id to cancel.")],
        ctx: Context,
    ) -> dict[str, Any]:
        """Best-effort cancel of a queued or running optimization run."""
        try:
            return _optimization_service(ctx).cancel(run_id)
        except MeridianMcpError as error:
            return _error_response(error)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_optimization_service.py tests/contract/test_optimization_tools.py -v` — expect PASS.

- [ ] **Step 5: Commit**

```bash
git add src/google_meridian_mcp_server/execution/base_executor.py src/google_meridian_mcp_server/execution/subprocess_executor.py src/google_meridian_mcp_server/services/optimization_service.py src/google_meridian_mcp_server/transport/tools.py tests/unit/test_optimization_service.py tests/contract/test_optimization_tools.py
git commit -m "feat(opt): cancel_optimization tool + executor termination"
```

---

## Task 7: Server wiring — executor selection + startup reconcile

**Files:**
- Modify: `src/google_meridian_mcp_server/bootstrap.py`
- Modify: `src/google_meridian_mcp_server/server.py`
- Modify: `src/google_meridian_mcp_server/services/optimization_service.py`
- Test: `tests/unit/test_bootstrap.py`, `tests/unit/test_server.py`

**Interfaces:**
- Consumes: `RuntimeConfig.optimization_allowed_tiers`, the two executors, the registry.
- Produces: `bootstrap.build_executor(cfg, registry) -> BaseExecutor` (cloud executor when allowed tiers are cloud-only; subprocess when `local` is allowed); a startup sweep `reconcile_orphans(registry, executor)` invoked in the lifespan; `OptimizationService.get_status` calls `executor.pump()` (already) which now reconciles cloud stale runs via `_on_alive`.

- [ ] **Step 1: Write the failing test**

`tests/unit/test_bootstrap.py`:

```python
def test_build_executor_local():
    cfg = RuntimeConfig(persistence_backend="local", local_models_root="/m")
    ex = build_executor(cfg, _FakeRegistry())
    assert ex.__class__.__name__ == "SubprocessExecutor"


def test_build_executor_cloud_only():
    cfg = RuntimeConfig(
        persistence_backend="gcs", gcs_bucket="b", gcs_models_prefix="m/",
        registry_backend="gcs", optimization_allowed_tiers=("cloud_cpu",),
        cloud_run_project="as-dev-anze", cloud_run_region="us-central1",
        cloud_run_job_cpu="opt-cpu",
    )
    ex = build_executor(cfg, _FakeRegistry(), jobs_client=object(), executions_client=object())
    assert ex.__class__.__name__ == "CloudRunJobExecutor"
```

Run — expect FAIL.

- [ ] **Step 2: Implement `build_executor` and `reconcile_orphans`**

In `bootstrap.py`:

```python
def build_executor(cfg, registry, *, jobs_client=None, executions_client=None):
    from google_meridian_mcp_server.domain.models import ComputeTier
    allowed = set(cfg.optimization_allowed_tiers)
    if ComputeTier.LOCAL.value in allowed:
        from google_meridian_mcp_server.execution.subprocess_executor import (
            SubprocessExecutor,
        )
        return SubprocessExecutor(
            registry,
            max_parallel=cfg.optimization_max_parallel,
            heartbeat_stale_seconds=cfg.optimization_heartbeat_stale_seconds,
            backend=cfg.optimization_backend_local,
        )
    from google_meridian_mcp_server.execution.cloud_run_executor import (
        CloudRunJobExecutor,
    )
    return CloudRunJobExecutor(
        registry, cfg=cfg,
        max_parallel=cfg.optimization_max_parallel,
        heartbeat_stale_seconds=cfg.optimization_heartbeat_stale_seconds,
        jobs_client=jobs_client, executions_client=executions_client,
    )


def reconcile_orphans(registry, executor) -> None:
    """On startup, fail runs left RUNNING with a stale heartbeat (crash during downtime)."""
    from google_meridian_mcp_server.domain.optimization import RunStatus
    for summary in registry.list(status=RunStatus.RUNNING):
        executor._reconcile_stale(summary.run_id)
```

> `reconcile_orphans` is safe now (Task 3): a still-running detached worker keeps its heartbeat fresh, so the stale check won't false-fail it. Runs that truly died during downtime have a stale heartbeat and get failed.

- [ ] **Step 3: Wire the lifespan**

In `server.py` `_lifespan`, replace the direct `SubprocessExecutor(...)` construction with:

```python
    from google_meridian_mcp_server.bootstrap import build_executor, build_registry, reconcile_orphans

    optimization_registry = build_registry(cfg)
    optimization_executor = build_executor(cfg, optimization_registry)
    try:
        reconcile_orphans(optimization_registry, optimization_executor)
    except Exception:  # noqa: BLE001 - reconcile is best-effort startup hygiene
        log.warning("startup orphan reconcile failed", exc_info=True)
```

(The yielded dict keys are unchanged.)

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_bootstrap.py tests/unit/test_server.py -v` — expect PASS.
Run: `uv run pytest` — full suite green.

- [ ] **Step 5: Commit**

```bash
git add src/google_meridian_mcp_server/bootstrap.py src/google_meridian_mcp_server/server.py tests/unit/test_bootstrap.py tests/unit/test_server.py
git commit -m "feat(opt): executor selection by tier + startup orphan reconcile"
```

---

## Task 8: Worker container images + Cloud Run job definitions

**Files:**
- Create: `deploy/Dockerfile.worker`, `deploy/Dockerfile.worker.gpu`, `deploy/deploy_jobs.sh`, `deploy/README.md`
- Test: build verification (documented commands; no unit test — verified end-to-end by Task 10)

**Interfaces:**
- Consumes: the package entrypoint `python -m google_meridian_mcp_server.execution.worker` and the env contract (`OPTIMIZATION_RUN_ID`, `MERIDIAN_BACKEND`, plus `PERSISTENCE_BACKEND=gcs`, `GCS_BUCKET`, `GCS_MODELS_PREFIX`, `REGISTRY_BACKEND=gcs`, `OPTIMIZATION_GCS_PREFIX`).
- Produces: two container images and two Cloud Run job definitions (`meridian-opt-cpu`, `meridian-opt-gpu`) in `as-dev-anze`, with backend/bucket env baked in; the executor (Task 5) supplies `OPTIMIZATION_RUN_ID`/`MERIDIAN_BACKEND` per execution via overrides.

- [ ] **Step 1: CPU worker image**

Create `deploy/Dockerfile.worker`:

```dockerfile
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --upgrade pip && pip install ".[jax]"
# Default backend; overridden per-execution by the executor.
ENV MERIDIAN_BACKEND=jax
ENTRYPOINT ["python", "-m", "google_meridian_mcp_server.execution.worker"]
```

- [ ] **Step 2: GPU worker image**

Create `deploy/Dockerfile.worker.gpu` (CUDA base + JAX CUDA wheels):

```dockerfile
FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y python3.12 python3-pip && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --upgrade pip && pip install "." "jax[cuda12]>=0.4"
ENV MERIDIAN_BACKEND=jax
ENTRYPOINT ["python3", "-m", "google_meridian_mcp_server.execution.worker"]
```

- [ ] **Step 3: Deploy script**

Create `deploy/deploy_jobs.sh` (idempotent create-or-update; targets `as-dev-anze`):

```bash
#!/usr/bin/env bash
set -euo pipefail

PROJECT="${CLOUD_RUN_PROJECT:-as-dev-anze}"
REGION="${CLOUD_RUN_REGION:-us-central1}"
BUCKET="${GCS_BUCKET:?set GCS_BUCKET}"
MODELS_PREFIX="${GCS_MODELS_PREFIX:?set GCS_MODELS_PREFIX}"
OPT_PREFIX="${OPTIMIZATION_GCS_PREFIX:-optimizations/}"
REPO="${REGION}-docker.pkg.dev/${PROJECT}/meridian"

ENV_VARS="PERSISTENCE_BACKEND=gcs,REGISTRY_BACKEND=gcs,GCS_BUCKET=${BUCKET},GCS_MODELS_PREFIX=${MODELS_PREFIX},OPTIMIZATION_GCS_PREFIX=${OPT_PREFIX}"

# CPU image + job
gcloud builds submit --project "$PROJECT" --tag "${REPO}/opt-cpu:latest" \
  --config /dev/stdin <<EOF || docker build -f deploy/Dockerfile.worker -t "${REPO}/opt-cpu:latest" . && docker push "${REPO}/opt-cpu:latest"
steps:
- name: gcr.io/cloud-builders/docker
  args: ["build","-f","deploy/Dockerfile.worker","-t","${REPO}/opt-cpu:latest","."]
images: ["${REPO}/opt-cpu:latest"]
EOF

gcloud run jobs deploy meridian-opt-cpu --project "$PROJECT" --region "$REGION" \
  --image "${REPO}/opt-cpu:latest" --cpu 4 --memory 16Gi --max-retries 0 --task-timeout 3600 \
  --set-env-vars "$ENV_VARS"

# GPU image + job (NVIDIA L4)
docker build -f deploy/Dockerfile.worker.gpu -t "${REPO}/opt-gpu:latest" .
docker push "${REPO}/opt-gpu:latest"
gcloud run jobs deploy meridian-opt-gpu --project "$PROJECT" --region "$REGION" \
  --image "${REPO}/opt-gpu:latest" --cpu 4 --memory 16Gi --gpu 1 --gpu-type nvidia-l4 \
  --max-retries 0 --task-timeout 3600 --set-env-vars "$ENV_VARS"

echo "Deployed meridian-opt-cpu and meridian-opt-gpu to ${PROJECT}/${REGION}"
```

Make it executable: `chmod +x deploy/deploy_jobs.sh`.

- [ ] **Step 4: Document**

Create `deploy/README.md` describing: prerequisites (ADC, Artifact Registry repo `meridian`, a GCS bucket with at least one fitted model under `GCS_MODELS_PREFIX`), how to run `deploy/deploy_jobs.sh`, the env contract, and the per-execution overrides the MCP server injects. Note that the worker must be able to read models from the same bucket/prefix.

- [ ] **Step 5: Verify the CPU image builds locally**

Run: `docker build -f deploy/Dockerfile.worker -t meridian-opt-cpu:test .`
Expected: image builds and `pip install ".[jax]"` resolves. (Do not push here; Task 10 deploys.)

- [ ] **Step 6: Commit**

```bash
git add deploy/
git commit -m "build(opt): Cloud Run worker images (CPU/GPU) + job deploy script"
```

---

## Task 9: Local cloud-executor live gate + cross-backend JAX gate

**Files:**
- Create: `scripts/validation/cloud_fake.py`
- Modify: `scripts/validation/runner.py`, `scripts/validation/live_validate.py`
- Test: the suite itself — `uv run python -m scripts.validation.live_validate`

**Interfaces:**
- Consumes: the in-process `Client(mcp)`, the existing `national-*`/`geo-*` fixtures, `GcsOptimizationRunRegistry` (with the `FakeGcsClient` from Task 4), `CloudRunJobExecutor` (with a fake `jobs.run` that launches the **real worker** locally), and the `assert_live_optimization` harness from Phase 1.
- Produces: a `cloud`/`subprocess` × `national`/`geo` PASS block driven through the identical tool chain, plus one cross-backend (`MERIDIAN_BACKEND=jax`) run that must complete or skip-with-notice.

- [ ] **Step 1: Build the cloud fake**

Create `scripts/validation/cloud_fake.py`: a fake `jobs_client` whose `run_job(request)` extracts the env overrides and launches the real worker as a local subprocess (the cloud launch contract, exercised live), plus a fake `executions_client` that reports liveness by polling the subprocess:

```python
"""In-process fake of Cloud Run jobs.run that launches the real worker locally.

Exercises the CloudRunJobExecutor launch contract end-to-end (env overrides,
RUN_ID, worker, registry writes, heartbeat, reconcile) with only the GCP RPC stubbed.
"""

from __future__ import annotations

import os
import subprocess
import sys
from types import SimpleNamespace


class FakeJobsClient:
    def __init__(self, *, base_env: dict):
        self._base_env = base_env
        self.procs: dict[str, subprocess.Popen] = {}

    def run_job(self, request):
        overrides = request.overrides.container_overrides[0]
        env = dict(os.environ)
        env.update(self._base_env)
        for var in overrides.env:
            env[var.name] = var.value
        run_id = env["OPTIMIZATION_RUN_ID"]
        proc = subprocess.Popen(
            [sys.executable, "-m", "google_meridian_mcp_server.execution.worker"],
            env=env,
        )
        self.procs[run_id] = proc
        name = f"exec-{run_id}"
        self._by_exec = getattr(self, "_by_exec", {})
        self._by_exec[name] = proc
        return SimpleNamespace(metadata=SimpleNamespace(name=name))


class FakeExecutionsClient:
    def __init__(self, jobs: FakeJobsClient):
        self._jobs = jobs

    def get_execution(self, name):
        proc = self._jobs._by_exec.get(name)
        alive = proc is not None and proc.poll() is None
        return SimpleNamespace(completion_time=None if alive else "done")

    def cancel_execution(self, *, name):
        proc = self._jobs._by_exec.get(name)
        if proc and proc.poll() is None:
            proc.terminate()
```

- [ ] **Step 2: Add the cloud live block to `runner.py`**

The harness `assert_live_optimization(client, model_id, *, overview)` already drives submit→poll→result→reuse. For the cloud gate, build a **second** in-process server whose lifespan uses the GCS registry (FakeGcsClient) + CloudRunJobExecutor (FakeJobsClient). Add a helper `run_cloud_matrix(...)` invoked from `live_validate`, or extend `run_matrix` to accept an injected executor/registry. Concretely, add to `runner.py`:

```python
async def assert_cloud_live_optimization(client, model_id, *, overview):
    # identical assertions to the local gate; the difference is the executor wiring,
    # set up by the caller (live_validate) via a cloud-configured server.
    await assert_live_optimization(client, model_id, overview=overview)
```

- [ ] **Step 3: Wire a cloud-configured server in `live_validate.py`**

Add a second pass in `live_validate._run()` after the local matrix. Construct a `FastMCP` whose lifespan yields a GCS-backed registry (FakeGcsClient) and a `CloudRunJobExecutor` using `FakeJobsClient`/`FakeExecutionsClient`, pointing the worker subprocess at the local fixtures via env. Because the worker reads `PERSISTENCE_BACKEND`/`REGISTRY_BACKEND` from env, set the fake base env so the worker loads models locally but writes the registry through the fake GCS dir behind `GcsOptimizationRunRegistry`:

```python
    # --- Cloud executor gate (faked jobs.run, real worker, GCS-fake registry) ---
    from scripts.validation.cloud_fake import FakeExecutionsClient, FakeJobsClient
    from tests.fakes.fake_gcs import FakeGcsClient
    from google_meridian_mcp_server.persistence.optimization_run_registry import (
        GcsOptimizationRunRegistry,
    )
    # base env that makes the worker write to the SAME fake-GCS registry is not
    # possible across processes with an in-memory fake, so the cloud gate uses a
    # local-dir registry for the worker and asserts the executor launch contract +
    # state transitions. (Real GCS is covered by Task 10.)
```

> **Important wiring note for the implementer:** an in-memory `FakeGcsClient` cannot be shared across the worker subprocess boundary. For this local gate, run the worker against a **local-dir** registry (set `OPTIMIZATION_RUNS_ROOT` in the fake base env and `REGISTRY_BACKEND=local`), while the MCP-server side reads the same dir via `LocalOptimizationRunRegistry`. This still exercises live: the `CloudRunJobExecutor` launch contract (env overrides, RUN_ID, job selection), the real worker, heartbeats, `_is_alive`/reconcile, and the full tool chain. The GCS *blob* path is unit/integration-tested in Task 4 and end-to-end in Task 10. Print a one-line notice making this boundary explicit so the gate never overstates coverage.

- [ ] **Step 4: Add the cross-backend JAX gate**

In `live_validate._run()`, add one run of a national fixture with the worker forced to `MERIDIAN_BACKEND=jax`. If `import jax` fails, print `SKIP: jax not installed (cross-backend gate)` and do not fail; otherwise assert the run reaches `completed` with a valid result. This certifies a TF-fit model optimizes under JAX.

- [ ] **Step 5: Run the suite**

Run: `uv run python -m scripts.validation.live_validate`
Expected: the matrix prints the Phase-1 local rows PASS, the new `cloud` rows for `national-revenue` and `geo-revenue` PASS, the cross-backend row PASS (or SKIP with notice), and ends with `LIVE VALIDATION PASSED`.

- [ ] **Step 6: Commit**

```bash
git add scripts/validation/cloud_fake.py scripts/validation/runner.py scripts/validation/live_validate.py
git commit -m "test(opt): local cloud-executor live gate + cross-backend JAX gate"
```

---

## Task 10: Real Cloud Run smoke test (`as-dev-anze`)

**Files:**
- Create: `scripts/validation/cloud_smoke.py`
- Test: opt-in manual/CI run against real Cloud Run

**Interfaces:**
- Consumes: a deployed `meridian-opt-cpu` job (Task 8), a real GCS bucket with a fitted model, real `JobsClient`/`ExecutionsClient`, the real `GcsOptimizationRunRegistry`, and `CloudRunJobExecutor` wired through `OptimizationService`.
- Produces: a standalone script that submits one real optimization to Cloud Run, polls to completion through the registry, asserts the structured result, and exits non-zero on failure. Opt-in via `CLOUD_SMOKE=1` (+ ADC); **skips with a logged notice** otherwise.

- [ ] **Step 1: Write the smoke script**

Create `scripts/validation/cloud_smoke.py`:

```python
"""Opt-in real Cloud Run smoke test against as-dev-anze.

Run:
  CLOUD_SMOKE=1 CLOUD_RUN_PROJECT=as-dev-anze CLOUD_RUN_REGION=us-central1 \
  CLOUD_RUN_JOB_CPU=meridian-opt-cpu GCS_BUCKET=<bucket> GCS_MODELS_PREFIX=<prefix> \
  PERSISTENCE_BACKEND=gcs REGISTRY_BACKEND=gcs OPTIMIZATION_ALLOWED_TIERS=cloud_cpu \
  OPTIMIZATION_DEFAULT_TIER=cloud_cpu MODEL_ID=<model_id> \
  uv run python -m scripts.validation.cloud_smoke
"""

from __future__ import annotations

import os
import sys
import time


def main() -> int:
    if os.getenv("CLOUD_SMOKE") != "1":
        print("SKIP: set CLOUD_SMOKE=1 (and ADC creds) to run the real Cloud Run smoke test")
        return 0

    from google_meridian_mcp_server.bootstrap import build_executor, build_registry, build_model_catalog
    from google_meridian_mcp_server.config import load_config
    from google_meridian_mcp_server.services.optimization_service import OptimizationService

    cfg = load_config()
    registry = build_registry(cfg)
    executor = build_executor(cfg, registry)
    catalog = build_model_catalog(cfg)
    service = OptimizationService(catalog=catalog, registry=registry, executor=executor, cfg=cfg)

    model_id = os.environ["MODEL_ID"]
    config = {"scenario": {"type": "fixed_budget"}, "constraint": {"mode": "global", "pct": 0.2}}
    submit = service.run_optimization(model_id, config, compute_tier="cloud_cpu")
    run_id = submit["run_id"]
    assert submit["compute_tier_resolved"] == "cloud_cpu", submit
    print(f"submitted {run_id} -> Cloud Run; polling...")

    deadline = time.time() + 1800  # 30 min
    status = None
    while time.time() < deadline:
        status = service.get_status(run_id)
        print(f"  status={status['status']} phase={status['phase']} elapsed={status['elapsed_seconds']}")
        if status["status"] in ("completed", "failed"):
            break
        time.sleep(15)

    assert status and status["status"] == "completed", f"run did not complete: {status}"
    result = service.get_result(run_id)
    for key in ("summary", "channel_tables", "allocation", "spend_delta", "outcome_mode"):
        assert key in result, f"missing {key}: {list(result)}"
    print("REAL CLOUD RUN SMOKE PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Deploy and run against `as-dev-anze`**

Run (with ADC for `as-dev-anze`, a bucket holding a fitted model):

```bash
export CLOUD_RUN_PROJECT=as-dev-anze CLOUD_RUN_REGION=us-central1
export GCS_BUCKET=<bucket> GCS_MODELS_PREFIX=<prefix>
bash deploy/deploy_jobs.sh           # Task 8 images/jobs
CLOUD_SMOKE=1 CLOUD_RUN_JOB_CPU=meridian-opt-cpu \
  PERSISTENCE_BACKEND=gcs REGISTRY_BACKEND=gcs \
  OPTIMIZATION_ALLOWED_TIERS=cloud_cpu OPTIMIZATION_DEFAULT_TIER=cloud_cpu \
  MODEL_ID=<model_id> uv run python -m scripts.validation.cloud_smoke
```

Expected: `submitted … -> Cloud Run; polling…` then status transitions to `completed` and `REAL CLOUD RUN SMOKE PASSED`. If creds/flags absent, the script prints the SKIP notice and exits 0.

> If the real run fails, treat it as a real defect: inspect the Cloud Run execution logs (`gcloud run jobs executions list/describe`), confirm the worker could read the model from GCS and write the registry, and fix before proceeding. This is the gate that proves the whole cloud path works.

- [ ] **Step 3: Commit**

```bash
git add scripts/validation/cloud_smoke.py
git commit -m "test(opt): opt-in real Cloud Run smoke test (as-dev-anze)"
```

---

## Task 11: Full suite, lint, and docs

**Files:**
- Modify: `README.md`, `AGENTS.md`, `.env.example`, `docs/meridian-mcp-showcase-parity.md`

- [ ] **Step 1: Full suite + lint**

Run: `uv run pytest` — expect all green.
Run: `uv run ruff check src tests scripts` then `uv run ruff format src tests scripts` — expect clean.
Run: `uv run python -m scripts.validation.live_validate` — expect `LIVE VALIDATION PASSED` including the cloud + cross-backend rows.

- [ ] **Step 2: Update `.env.example`**

Append the Phase-2 env vars with defaults and one-line comments:

```bash
# Optimization module — Phase 2 (cloud tiers)
# Allow cloud tiers (requires REGISTRY_BACKEND=gcs + Cloud Run coordinates below):
# OPTIMIZATION_ALLOWED_TIERS=local,cloud_cpu,cloud_gpu
OPTIMIZATION_BACKEND_CLOUD_CPU=jax
OPTIMIZATION_BACKEND_CLOUD_GPU=jax
# REGISTRY_BACKEND=gcs
# OPTIMIZATION_GCS_PREFIX=optimizations/
# CLOUD_RUN_PROJECT=as-dev-anze
# CLOUD_RUN_REGION=us-central1
# CLOUD_RUN_JOB_CPU=meridian-opt-cpu
# CLOUD_RUN_JOB_GPU=meridian-opt-gpu
```

- [ ] **Step 3: Update `AGENTS.md`**

Add to the tool surface: `cancel_optimization`. Add to the module map: `execution/cloud_run_executor.py`, `persistence/optimization_run_registry.py::GcsOptimizationRunRegistry`, `deploy/`. Document the new env vars (per-tier backends, Cloud Run coordinates) and the cloud execution model (worker runs in a Cloud Run Job; registry on GCS; heartbeat + stale reconcile is the cloud crash signal; startup orphan reconcile). Note `response_curves` is now in the result payload. Note the two new live gates (faked-`jobs.run` local gate + opt-in real Cloud Run smoke against `as-dev-anze`).

- [ ] **Step 4: Update `README.md`**

Add a "Budget optimization (cloud)" subsection: the five+one tools (`run_optimization`, `get_optimization_status`, `get_optimization_result`, `list_optimizations`, `delete_optimization`, `cancel_optimization`); how to enable cloud tiers (env block); a pointer to `deploy/README.md` for deploying the Cloud Run jobs to `as-dev-anze`; and how to run the live gates (`scripts.validation.live_validate` for local, `scripts.validation.cloud_smoke` for real Cloud Run).

- [ ] **Step 5: Update the parity doc**

In `docs/meridian-mcp-showcase-parity.md`, mark the Budget Optimization page parity as **Phase 2 complete**: cloud tiers (CPU/GPU), per-tier JAX backend, `response_curves`, and `cancel`. Note the only remaining non-goals (rendered HTML/PDF report; reach-&-frequency optimization).

- [ ] **Step 6: Commit**

```bash
git add README.md AGENTS.md .env.example docs/meridian-mcp-showcase-parity.md
git commit -m "docs: document optimization module Phase 2 (cloud, cancel, response_curves)"
```

---

## Self-Review

**Spec coverage (Phase 2 scope, spec §14):**
- `GcsOptimizationRunRegistry` → Task 4. ✓ (generation-guarded state writes per §7.2)
- `CloudRunJobExecutor` → Task 5. ✓ (`jobs.run` overrides, executions liveness, stale reconcile)
- Worker image(s) + CPU/GPU job defs → Task 8. ✓
- Per-tier JAX backend + cross-backend gate → Tasks 1 (config) + 9 (gate). ✓
- `cancel` → Task 6. ✓
- Optional `progress_fraction` → Task 3 (coarse, per-phase). ✓ (fine per-grid-row instrumentation listed as future work)
- Deployment guardrails for cloud tiers → Task 1. ✓
- `response_curves` (deferred from Phase 1) → Task 2. ✓
- Per-executor live gate (national + geo) + full matrix → Task 9; real Cloud Run smoke → Task 10. ✓
- Docs (README + AGENTS.md + .env.example + parity) → Task 11. ✓

**Placeholder scan:** Every code step shows complete code; every run step gives an exact command + expected output. The two areas the implementer must confirm against the live client (the `run_v2` operation→execution-name attribute in Task 5; the in-memory-fake cross-process boundary in Task 9) are flagged explicitly with the verification path, not left vague.

**Type consistency:** `RuntimeConfig.backend_for_tier`/`cloud_run_job_for_tier`/`cloud_run_*` (Task 1) are consumed by `CloudRunJobExecutor` (Task 5) and `build_executor` (Task 7). `GcsOptimizationRunRegistry` (Task 4) implements the same ABC the executors and service already consume; the added `write_state(*, expected_generation)` + `get_state_generation` are applied in both the local and GCS impls and used by `_reconcile_stale` (Task 5). `cancel`/`_terminate` (Task 6) are defined on `BaseExecutor` and implemented by both subclasses. The worker env contract (Task 3 heartbeats, Task 8 job env, Task 5 overrides) is consistent across launch, container, and gate.

**Known deviation:** fine-grained `progress_fraction` (per grid-row) remains future work (spec §8.3 marks it optional stretch); Phase 2 ships a coarse per-phase fraction. The in-memory `FakeGcsClient` cannot cross the worker subprocess boundary, so the *local* cloud gate (Task 9) runs the worker against a local-dir registry while still exercising the full cloud launch/liveness/cancel contract; the real GCS blob path is proven by Task 10 against `as-dev-anze`.

---

## Future Work (out of scope for Phase 2)

- Fine-grained `progress_fraction` via an `OptimizationGrid` subclass that knows `n_grid_rows`.
- Sharded `index/` listing for thousands of runs (spec §15).
- Reach-&-frequency optimization as a sibling module reusing the executor/registry.
- Auto-retention/TTL for runs.
- A real-GPU smoke test (Task 10 covers CPU; GPU job is deployed but its live smoke is manual).

# Budget Optimization Module — Phase 1 (local, no-GCP) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the budget-optimization module end-to-end on a local host with zero GCP dependency: a submit/poll job API (`run_optimization`, `get_optimization_status`, `get_optimization_result`, `list_optimizations`, `delete_optimization`), a durable local run registry, a `BaseExecutor` + `SubprocessExecutor`, a size-score router (local tier only this phase), config-fingerprint reuse, and a validated pydantic `RuntimeConfig`.

**Architecture:** Follows the repo's `transport → service → meridian` layering plus a new `execution/` package and a registry provider in `persistence/`. `run_optimization` validates an `OptimizationConfig`, fingerprints it, reuses a prior completed run when possible, else writes a `queued` run record to the registry and launches a **shared worker** (`execution/worker.py`) as a subprocess. The worker owns the run's `running → completed|failed` transitions, a heartbeat, and the structured result write. Status/result/list tools only read the registry. The same worker entrypoint is what Phase 2 will launch as a Cloud Run Job.

**Tech Stack:** Python 3.12, `google-meridian==1.7.0` (`meridian.analysis.optimizer.BudgetOptimizer`), FastMCP, pydantic v2, `uv`, `ruff`, `pytest`.

## Global Constraints

- **Meridian pin:** `google-meridian[schema]==1.7.0`. `BudgetOptimizer.optimize()` signature: `(new_data=None, use_posterior=True, selected_geos=None, selected_times=None, start_date=None, end_date=None, fixed_budget=True, budget=None, pct_of_spend=None, spend_constraint_lower=None, spend_constraint_upper=None, target_roi=None, target_mroi=None, gtol=1e-4, use_optimal_frequency=True, max_frequency=None, use_kpi=False, confidence_level=0.9, batch_size=100, optimization_grid=None) -> OptimizationResults`.
- **No new runtime dependencies** in Phase 1 (no GCP client; that is Phase 2).
- **JSON-safe, deterministic outputs.** Floats rounded to **6 significant figures** (reuse the existing rounding convention: `float(f"{value:.6g}")`). Stable key/row ordering.
- **Wiring rule:** new tools go `transport → service → meridian`/`execution`/`persistence`. No business logic in `transport/tools.py`.
- **Error rule:** domain errors subclass `MeridianMcpError` with a stable `error_code`; transport converts them via the existing `_error_response`. No broad `except` swallowing.
- **Surgical changes:** match existing style; do not refactor adjacent code beyond what a task needs.
- **Commands:** `uv run pytest`, `uv run ruff check src tests scripts`, `uv run ruff format src tests scripts`, `uv run python -m scripts.validation.live_validate`.
- **Scope note / deviation from spec §6.2:** Phase 1's structured result contains `summary`, `channel_tables` (initial+optimized), `allocation`, and `spend_delta` — exactly the structured artifacts the showcase page renders. The spec's `response_curves` element exists only inside the showcase's *rendered HTML* (not its structured artifacts) and is **deferred to Phase 2**; it will be sourced from the optimization grid then. This is a deliberate, documented gap.

---

## File Structure

**New files**
- `src/google_meridian_mcp_server/domain/optimization.py` — `OptimizationConfig` (scenario/constraint discriminated unions), `OptimizationRun`, `OptimizationRunState`, `OptimizationRunSummary`, enums (`RunStatus`, `RunPhase`, `ComputeTier`, `OutcomeMode`), `config_fingerprint()`.
- `src/google_meridian_mcp_server/meridian/optimizer_facade.py` — `OptimizerFacade`: wraps `BudgetOptimizer`, maps config → `optimize()` kwargs, builds the structured result.
- `src/google_meridian_mcp_server/persistence/optimization_run_registry.py` — `OptimizationRunRegistry` ABC + `LocalOptimizationRunRegistry`.
- `src/google_meridian_mcp_server/execution/__init__.py`
- `src/google_meridian_mcp_server/execution/routing.py` — `model_size_features`, `size_score`, `resolve_tier`.
- `src/google_meridian_mcp_server/execution/base_executor.py` — `BaseExecutor` (template + concurrency gate + reconcile).
- `src/google_meridian_mcp_server/execution/subprocess_executor.py` — `SubprocessExecutor`.
- `src/google_meridian_mcp_server/execution/worker.py` — shared worker entrypoint.
- `src/google_meridian_mcp_server/bootstrap.py` — `build_model_catalog(cfg)`, `build_registry(cfg)` (shared by server lifespan + worker).
- `src/google_meridian_mcp_server/services/optimization_service.py` — `OptimizationService`.
- Tests: `tests/unit/test_optimization_config.py`, `test_optimization_mapping.py`, `test_optimization_run_registry.py`, `test_routing.py`, `test_optimizer_facade.py`, `test_optimization_worker.py`, `test_subprocess_executor.py`, `test_optimization_service.py`; `tests/contract/test_optimization_tools.py`.

**Modified files**
- `src/google_meridian_mcp_server/domain/models.py` — `RuntimeConfig` dataclass → pydantic; new enums/fields.
- `src/google_meridian_mcp_server/config.py` — read new env vars.
- `src/google_meridian_mcp_server/server.py` — lifespan builds registry + executor + wires `OptimizationService`; use `bootstrap`.
- `src/google_meridian_mcp_server/transport/tools.py` — register 5 optimization tools.
- `src/google_meridian_mcp_server/services/analysis_service.py` — add `run_optimization` to `available_tool_options`.
- `scripts/validation/matrix.py`, `scripts/validation/runner.py` — per-executor live gate (national + geo), adversarial, reuse.
- `AGENTS.md`, `.env.example`, `docs/meridian-mcp-showcase-parity.md` — docs.

---

## Task 1: Migrate `RuntimeConfig` to a validated pydantic model

**Files:**
- Modify: `src/google_meridian_mcp_server/domain/models.py:32-68`
- Modify: `src/google_meridian_mcp_server/config.py:25-39`
- Test: `tests/unit/test_config_and_persistence.py`

**Interfaces:**
- Produces: `RuntimeConfig` (pydantic `BaseModel`, frozen) with all existing fields plus `registry_backend: str`, `optimization_runs_root: str`, `optimization_gcs_prefix: str`, `optimization_allowed_tiers: tuple[str, ...]`, `optimization_default_tier: str`, `optimization_max_parallel: int`, `optimization_size_thresholds: tuple[int, int]`, `optimization_heartbeat_stale_seconds: int`. New enum `ComputeTier(str, Enum)` with `LOCAL="local"`, `CLOUD_CPU="cloud_cpu"`, `CLOUD_GPU="cloud_gpu"`. `load_config()` builds it from env.

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_config_and_persistence.py`:

```python
import pytest
from pydantic import ValidationError

from google_meridian_mcp_server.domain.models import RuntimeConfig


def test_runtime_config_defaults_local():
    cfg = RuntimeConfig(persistence_backend="local", local_models_root="/models")
    assert cfg.registry_backend == "local"            # follows persistence_backend
    assert cfg.optimization_allowed_tiers == ("local",)
    assert cfg.optimization_default_tier == "auto"
    assert cfg.optimization_max_parallel == 2
    assert cfg.optimization_size_thresholds == (1_000_000, 100_000_000)


def test_runtime_config_local_requires_models_root():
    with pytest.raises(ValidationError, match="LOCAL_MODELS_ROOT"):
        RuntimeConfig(persistence_backend="local", local_models_root=None)


def test_runtime_config_cloud_tier_requires_gcs_registry():
    with pytest.raises(ValidationError, match="cloud .* require .* gcs registry"):
        RuntimeConfig(
            persistence_backend="local",
            local_models_root="/models",
            registry_backend="local",
            optimization_allowed_tiers=("cloud_cpu",),
        )


def test_runtime_config_default_tier_must_be_allowed():
    with pytest.raises(ValidationError, match="not in allowed tiers"):
        RuntimeConfig(
            persistence_backend="local",
            local_models_root="/models",
            optimization_default_tier="cloud_gpu",
            optimization_allowed_tiers=("local",),
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_config_and_persistence.py -k runtime_config -v`
Expected: FAIL (pydantic ValidationError patterns not met; fields missing).

- [ ] **Step 3: Implement the pydantic model**

In `domain/models.py`, replace the `@dataclass(frozen=True) class RuntimeConfig` (lines 32-68) with a pydantic model. Add `from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator` at the top and a `ComputeTier` enum near the other enums:

```python
class ComputeTier(str, Enum):
    LOCAL = "local"
    CLOUD_CPU = "cloud_cpu"
    CLOUD_GPU = "cloud_gpu"


class RuntimeConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    transport: str = "streamable-http"
    persistence_backend: str = "local"
    local_models_root: str | None = None
    gcs_bucket: str | None = None
    gcs_models_prefix: str | None = None
    discovery_ttl_seconds: int = 7200
    model_cache_root: str = "/tmp/mmm-models"
    result_cache_enabled: bool = True
    result_cache_ttl_seconds: int | None = None

    # Optimization module
    registry_backend: str | None = None  # None → follows persistence_backend
    optimization_runs_root: str = "./optimizations"
    optimization_gcs_prefix: str = "optimizations/"
    optimization_allowed_tiers: tuple[str, ...] = ("local",)
    optimization_default_tier: str = "auto"
    optimization_max_parallel: int = 2
    optimization_size_thresholds: tuple[int, int] = (1_000_000, 100_000_000)
    optimization_heartbeat_stale_seconds: int = 60

    @field_validator("transport")
    @classmethod
    def _check_transport(cls, value: str) -> str:
        valid = {t.value for t in Transport}
        if value not in valid:
            raise ValueError(
                f"Unsupported transport '{value}'. Expected one of: {sorted(valid)}"
            )
        return value

    @model_validator(mode="after")
    def _check(self) -> "RuntimeConfig":
        if self.persistence_backend == PersistenceBackend.LOCAL.value:
            if not self.local_models_root:
                raise ValueError("LOCAL_MODELS_ROOT is required when PERSISTENCE_BACKEND=local")
        elif self.persistence_backend == PersistenceBackend.GCS.value:
            if not self.gcs_bucket:
                raise ValueError("GCS_BUCKET is required when PERSISTENCE_BACKEND=gcs")
            if not self.gcs_models_prefix:
                raise ValueError("GCS_MODELS_PREFIX is required when PERSISTENCE_BACKEND=gcs")
        else:
            raise ValueError(f"Unsupported PERSISTENCE_BACKEND '{self.persistence_backend}'")

        if self.discovery_ttl_seconds <= 0:
            raise ValueError("DISCOVERY_TTL_SECONDS must be positive")
        if self.result_cache_ttl_seconds is not None and self.result_cache_ttl_seconds <= 0:
            raise ValueError("RESULT_CACHE_TTL_SECONDS must be positive")

        valid_tiers = {t.value for t in ComputeTier}
        for tier in self.optimization_allowed_tiers:
            if tier not in valid_tiers:
                raise ValueError(f"Unknown optimization tier '{tier}'. Valid: {sorted(valid_tiers)}")
        if not self.optimization_allowed_tiers:
            raise ValueError("OPTIMIZATION_ALLOWED_TIERS must list at least one tier")
        if self.optimization_default_tier != "auto" and (
            self.optimization_default_tier not in self.optimization_allowed_tiers
        ):
            raise ValueError(
                f"OPTIMIZATION_DEFAULT_TIER '{self.optimization_default_tier}' not in allowed tiers "
                f"{list(self.optimization_allowed_tiers)}"
            )
        if self.optimization_max_parallel <= 0:
            raise ValueError("OPTIMIZATION_MAX_PARALLEL must be positive")
        lo, hi = self.optimization_size_thresholds
        if not (0 < lo < hi):
            raise ValueError("OPTIMIZATION_SIZE_THRESHOLDS must be two ascending positive ints")

        cloud_tiers = {ComputeTier.CLOUD_CPU.value, ComputeTier.CLOUD_GPU.value}
        if cloud_tiers & set(self.optimization_allowed_tiers):
            if self.resolved_registry_backend != PersistenceBackend.GCS.value:
                raise ValueError("cloud tiers require a gcs registry (set REGISTRY_BACKEND=gcs)")
        return self

    @property
    def resolved_registry_backend(self) -> str:
        return self.registry_backend or self.persistence_backend
```

- [ ] **Step 4: Update `load_config()` to populate the new fields**

In `config.py`, add a CSV/int helper and pass the new env vars:

```python
def _read_csv(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    value = os.getenv(name)
    if not value:
        return default
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _read_thresholds(name: str, default: tuple[int, int]) -> tuple[int, int]:
    value = os.getenv(name)
    if not value:
        return default
    parts = [int(item.strip()) for item in value.split(",")]
    if len(parts) != 2:
        raise ValueError(f"{name} must be 'T_local,T_gpu'")
    return (parts[0], parts[1])
```

and extend the `RuntimeConfig(...)` call in `load_config()` with:

```python
        registry_backend=os.getenv("REGISTRY_BACKEND"),
        optimization_runs_root=os.getenv("OPTIMIZATION_RUNS_ROOT", "./optimizations"),
        optimization_gcs_prefix=os.getenv("OPTIMIZATION_GCS_PREFIX", "optimizations/"),
        optimization_allowed_tiers=_read_csv("OPTIMIZATION_ALLOWED_TIERS", ("local",)),
        optimization_default_tier=os.getenv("OPTIMIZATION_DEFAULT_TIER", "auto"),
        optimization_max_parallel=int(os.getenv("OPTIMIZATION_MAX_PARALLEL", "2")),
        optimization_size_thresholds=_read_thresholds(
            "OPTIMIZATION_SIZE_THRESHOLDS", (1_000_000, 100_000_000)
        ),
        optimization_heartbeat_stale_seconds=int(
            os.getenv("OPTIMIZATION_HEARTBEAT_STALE_SECONDS", "60")
        ),
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_config_and_persistence.py -v`
Expected: PASS. Then `uv run pytest tests/unit/test_server.py -v` (lifespan still constructs config) — Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/google_meridian_mcp_server/domain/models.py src/google_meridian_mcp_server/config.py tests/unit/test_config_and_persistence.py
git commit -m "feat: validated pydantic RuntimeConfig with optimization config fields"
```

---

## Task 2: Optimization domain models

**Files:**
- Create: `src/google_meridian_mcp_server/domain/optimization.py`
- Test: `tests/unit/test_optimization_config.py`

**Interfaces:**
- Produces:
  - Enums: `RunStatus(QUEUED, RUNNING, COMPLETED, FAILED, CANCELED)`, `RunPhase(LOADING_MODEL, BUILDING_GRID, OPTIMIZING, ASSEMBLING_RESULT, UPLOADING)`, `OutcomeMode(REVENUE, KPI)`.
  - `FixedBudgetScenario`, `TargetRoasScenario`, `TargetMroasScenario`, discriminated as `Scenario`.
  - `GlobalConstraint`, `PerChannelConstraint` (with `ChannelBound`), discriminated as `Constraint`.
  - `OptimizationConfig(scenario, constraint=GlobalConstraint(pct=0.3), start_date=None, end_date=None, selected_geos=None, use_kpi=None)`.
  - `OptimizationRun` (record), `OptimizationRunState`, `OptimizationRunSummary` pydantic models.
  - `config_fingerprint(model_id: str, config: OptimizationConfig) -> str`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_optimization_config.py
import pytest
from pydantic import ValidationError

from google_meridian_mcp_server.domain.optimization import (
    GlobalConstraint,
    OptimizationConfig,
    PerChannelConstraint,
    config_fingerprint,
)


def test_fixed_budget_scenario_parses_from_dict():
    cfg = OptimizationConfig.model_validate(
        {"scenario": {"type": "fixed_budget", "budget": 1_200_000}}
    )
    assert cfg.scenario.type == "fixed_budget"
    assert cfg.scenario.budget == 1_200_000
    assert isinstance(cfg.constraint, GlobalConstraint)
    assert cfg.constraint.pct == 0.3


def test_target_roas_scenario_parses():
    cfg = OptimizationConfig.model_validate(
        {"scenario": {"type": "target_roas", "target_value": 2.0}}
    )
    assert cfg.scenario.type == "target_roas"
    assert cfg.scenario.target_value == 2.0


def test_per_channel_constraint_parses():
    cfg = OptimizationConfig.model_validate(
        {
            "scenario": {"type": "fixed_budget"},
            "constraint": {
                "mode": "per_channel",
                "bounds": {"tv": {"lower_pct": 0.1, "upper_pct": 0.3}},
            },
        }
    )
    assert isinstance(cfg.constraint, PerChannelConstraint)
    assert cfg.constraint.bounds["tv"].upper_pct == 0.3


def test_target_value_must_be_positive():
    with pytest.raises(ValidationError):
        OptimizationConfig.model_validate(
            {"scenario": {"type": "target_roas", "target_value": 0}}
        )


def test_fingerprint_is_stable_and_order_insensitive():
    a = OptimizationConfig.model_validate(
        {"scenario": {"type": "fixed_budget", "budget": 100.0}, "selected_geos": ["b", "a"]}
    )
    b = OptimizationConfig.model_validate(
        {"scenario": {"type": "fixed_budget", "budget": 100.0}, "selected_geos": ["a", "b"]}
    )
    assert config_fingerprint("m", a) == config_fingerprint("m", b)
    assert config_fingerprint("m", a) != config_fingerprint("other", a)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_optimization_config.py -v`
Expected: FAIL (module does not exist).

- [ ] **Step 3: Implement the domain models**

```python
# src/google_meridian_mcp_server/domain/optimization.py
"""Domain models for the budget optimization module."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field


class RunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


class RunPhase(str, Enum):
    LOADING_MODEL = "loading_model"
    BUILDING_GRID = "building_grid"
    OPTIMIZING = "optimizing"
    ASSEMBLING_RESULT = "assembling_result"
    UPLOADING = "uploading"


class OutcomeMode(str, Enum):
    REVENUE = "revenue"
    KPI = "kpi"


class FixedBudgetScenario(BaseModel):
    type: Literal["fixed_budget"] = "fixed_budget"
    budget: float | None = Field(
        default=None, gt=0,
        description="Total budget across channels for the whole selected range. "
        "Omit to use the model's historical total spend over the range.",
        examples=[1_200_000],
    )


class TargetRoasScenario(BaseModel):
    type: Literal["target_roas"]
    target_value: float = Field(
        gt=0,
        description="Target overall ROAS (revenue per spend). For KPI/no-revenue "
        "models this is read as a CPIK target and inverted internally.",
        examples=[2.0],
    )


class TargetMroasScenario(BaseModel):
    type: Literal["target_mroas"]
    target_value: float = Field(
        gt=0, description="Target marginal ROAS (mROAS).", examples=[1.5]
    )


Scenario = Annotated[
    FixedBudgetScenario | TargetRoasScenario | TargetMroasScenario,
    Field(discriminator="type"),
]


class GlobalConstraint(BaseModel):
    mode: Literal["global"] = "global"
    pct: float = Field(
        ge=0, le=1,
        description="Max fractional deviation from current spend applied to every "
        "channel (0.2 = +/-20%).",
        examples=[0.2],
    )


class ChannelBound(BaseModel):
    lower_pct: float = Field(ge=0, le=1)
    upper_pct: float = Field(ge=0, le=1)


class PerChannelConstraint(BaseModel):
    mode: Literal["per_channel"]
    bounds: dict[str, ChannelBound] = Field(
        description="Per-channel lower/upper fractional bounds; must cover every "
        "paid/RF channel. Valid channels: see get_model_overview.",
    )


Constraint = Annotated[
    GlobalConstraint | PerChannelConstraint, Field(discriminator="mode")
]


class OptimizationConfig(BaseModel):
    scenario: Scenario
    constraint: Constraint = Field(default_factory=lambda: GlobalConstraint(pct=0.3))
    start_date: date | None = Field(default=None, description="ISO start; omit for full range.")
    end_date: date | None = Field(default=None, description="ISO end; omit for full range.")
    selected_geos: list[str] | None = Field(
        default=None,
        description="Subset of geos; omit for all. Ignored by national models.",
    )
    use_kpi: bool | None = Field(
        default=None,
        description="Objective: false=ROAS/ROI, true=CPIK. Omit to use the model's "
        "native objective (revenue->ROAS, no-revenue->CPIK).",
    )


class OptimizationRun(BaseModel):
    run_id: str
    label: str
    note: str | None = None
    model_id: str
    config: OptimizationConfig
    config_fingerprint: str
    compute_tier_requested: str
    compute_tier_resolved: str
    backend: str
    size_score: int
    created_at: str
    meridian_version: str
    server_version: str


class OptimizationRunState(BaseModel):
    run_id: str
    status: RunStatus
    phase: RunPhase | None = None
    progress_fraction: float | None = None
    heartbeat_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    error: dict[str, Any] | None = None
    headline: str | None = None


class OptimizationRunSummary(BaseModel):
    run_id: str
    label: str
    model_id: str
    config_summary: str
    status: RunStatus
    created_at: str
    finished_at: str | None = None
    headline: str | None = None


def config_fingerprint(model_id: str, config: OptimizationConfig) -> str:
    """Stable, order-insensitive fingerprint of (model_id, config)."""
    payload = config.model_dump(mode="json")
    if payload.get("selected_geos"):
        payload["selected_geos"] = sorted(payload["selected_geos"])
    raw = json.dumps({"model_id": model_id, "config": payload}, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_optimization_config.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/google_meridian_mcp_server/domain/optimization.py tests/unit/test_optimization_config.py
git commit -m "feat: optimization domain models (config, run record, fingerprint)"
```

---

## Task 3: Config → `optimize()` kwargs mapping

**Files:**
- Modify: `src/google_meridian_mcp_server/domain/optimization.py` (add `to_optimize_kwargs`)
- Test: `tests/unit/test_optimization_mapping.py`

**Interfaces:**
- Consumes: `OptimizationConfig`, `OutcomeMode`.
- Produces: `to_optimize_kwargs(config, *, channel_order: list[str], use_kpi: bool) -> dict[str, Any]` returning the exact keyword args for `BudgetOptimizer.optimize()` (`fixed_budget`, `budget`, `target_roi`, `target_mroi`, `spend_constraint_lower`, `spend_constraint_upper`, `selected_geos`, `start_date`, `end_date`, `use_kpi`). Raises `ValueError` listing missing channels when a `per_channel` constraint does not cover `channel_order`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_optimization_mapping.py
import pytest

from google_meridian_mcp_server.domain.optimization import (
    OptimizationConfig,
    to_optimize_kwargs,
)

CHANNELS = ["tv", "search"]


def _cfg(d):
    return OptimizationConfig.model_validate(d)


def test_fixed_budget_global_revenue():
    kw = to_optimize_kwargs(
        _cfg({"scenario": {"type": "fixed_budget", "budget": 500.0},
              "constraint": {"mode": "global", "pct": 0.2}}),
        channel_order=CHANNELS, use_kpi=False,
    )
    assert kw["fixed_budget"] is True
    assert kw["budget"] == 500.0
    assert kw["target_roi"] is None and kw["target_mroi"] is None
    assert kw["spend_constraint_lower"] == 0.2 and kw["spend_constraint_upper"] == 0.2
    assert kw["use_kpi"] is False


def test_target_roas_revenue_not_inverted():
    kw = to_optimize_kwargs(
        _cfg({"scenario": {"type": "target_roas", "target_value": 4.0}}),
        channel_order=CHANNELS, use_kpi=False,
    )
    assert kw["fixed_budget"] is False
    assert kw["target_roi"] == 4.0


def test_target_roas_kpi_inverted():
    kw = to_optimize_kwargs(
        _cfg({"scenario": {"type": "target_roas", "target_value": 4.0}}),
        channel_order=CHANNELS, use_kpi=True,
    )
    assert kw["target_roi"] == pytest.approx(0.25)  # 1/4 CPIK target


def test_per_channel_constraint_orders_to_channels():
    kw = to_optimize_kwargs(
        _cfg({"scenario": {"type": "fixed_budget"},
              "constraint": {"mode": "per_channel", "bounds": {
                  "search": {"lower_pct": 0.1, "upper_pct": 0.5},
                  "tv": {"lower_pct": 0.2, "upper_pct": 0.4}}}}),
        channel_order=CHANNELS, use_kpi=False,
    )
    assert kw["spend_constraint_lower"] == [0.2, 0.1]   # tv, search order
    assert kw["spend_constraint_upper"] == [0.4, 0.5]


def test_per_channel_missing_channel_raises():
    with pytest.raises(ValueError, match="search"):
        to_optimize_kwargs(
            _cfg({"scenario": {"type": "fixed_budget"},
                  "constraint": {"mode": "per_channel",
                                 "bounds": {"tv": {"lower_pct": 0.2, "upper_pct": 0.4}}}}),
            channel_order=CHANNELS, use_kpi=False,
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_optimization_mapping.py -v`
Expected: FAIL (`to_optimize_kwargs` not defined).

- [ ] **Step 3: Implement the mapping**

Append to `domain/optimization.py`:

```python
def _invert(value: float) -> float:
    return 1.0 / value


def to_optimize_kwargs(
    config: OptimizationConfig, *, channel_order: list[str], use_kpi: bool
) -> dict[str, Any]:
    """Translate an OptimizationConfig into BudgetOptimizer.optimize() kwargs."""
    scenario = config.scenario
    fixed_budget = scenario.type == "fixed_budget"
    budget = scenario.budget if scenario.type == "fixed_budget" else None
    target_roi = None
    target_mroi = None
    if scenario.type == "target_roas":
        target_roi = _invert(scenario.target_value) if use_kpi else scenario.target_value
    elif scenario.type == "target_mroas":
        target_mroi = _invert(scenario.target_value) if use_kpi else scenario.target_value

    constraint = config.constraint
    if constraint.mode == "global":
        spend_lower: float | list[float] = constraint.pct
        spend_upper: float | list[float] = constraint.pct
    else:
        missing = [ch for ch in channel_order if ch not in constraint.bounds]
        if missing:
            raise ValueError(
                f"per_channel constraint is missing bounds for channels: {missing}"
            )
        spend_lower = [constraint.bounds[ch].lower_pct for ch in channel_order]
        spend_upper = [constraint.bounds[ch].upper_pct for ch in channel_order]

    return {
        "fixed_budget": fixed_budget,
        "budget": budget,
        "target_roi": target_roi,
        "target_mroi": target_mroi,
        "spend_constraint_lower": spend_lower,
        "spend_constraint_upper": spend_upper,
        "selected_geos": config.selected_geos,
        "start_date": config.start_date.isoformat() if config.start_date else None,
        "end_date": config.end_date.isoformat() if config.end_date else None,
        "use_kpi": use_kpi,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_optimization_mapping.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/google_meridian_mcp_server/domain/optimization.py tests/unit/test_optimization_mapping.py
git commit -m "feat: map OptimizationConfig to BudgetOptimizer.optimize kwargs"
```

---

## Task 4: Local run registry

**Files:**
- Create: `src/google_meridian_mcp_server/persistence/optimization_run_registry.py`
- Test: `tests/unit/test_optimization_run_registry.py`

**Interfaces:**
- Consumes: `OptimizationRun`, `OptimizationRunState`, `OptimizationRunSummary`, `RunStatus` from `domain.optimization`.
- Produces: `OptimizationRunRegistry` ABC with `create(run)`, `write_state(state)`, `write_result(run_id, result: dict)`, `get_record(run_id) -> OptimizationRun`, `get_state(run_id) -> OptimizationRunState`, `get_result(run_id) -> dict`, `list(model_id=None, status=None, limit=None) -> list[OptimizationRunSummary]`, `delete(run_id)`, `find_by_fingerprint(fp) -> str | None`, `put_fingerprint(fp, run_id)`. Plus `RunNotFoundError(MeridianMcpError)` and `ResultNotReadyError(MeridianMcpError)`. `LocalOptimizationRunRegistry(root: str)`. A `build_config_summary(run: OptimizationRun) -> str` helper.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_optimization_run_registry.py
import pytest

from google_meridian_mcp_server.domain.optimization import (
    OptimizationConfig,
    OptimizationRun,
    OptimizationRunState,
    RunStatus,
)
from google_meridian_mcp_server.persistence.optimization_run_registry import (
    LocalOptimizationRunRegistry,
    ResultNotReadyError,
    RunNotFoundError,
)


def _run(run_id="m-1-abc", model_id="m", fp="fp1"):
    cfg = OptimizationConfig.model_validate({"scenario": {"type": "fixed_budget"}})
    return OptimizationRun(
        run_id=run_id, label="label", model_id=model_id, config=cfg,
        config_fingerprint=fp, compute_tier_requested="auto",
        compute_tier_resolved="local", backend="tensorflow", size_score=10,
        created_at="2026-06-29T00:00:00+00:00", meridian_version="1.7.0",
        server_version="0.1.0",
    )


def test_create_and_get_record(tmp_path):
    reg = LocalOptimizationRunRegistry(str(tmp_path))
    reg.create(_run())
    got = reg.get_record("m-1-abc")
    assert got.model_id == "m" and got.label == "label"


def test_state_roundtrip_and_result_gate(tmp_path):
    reg = LocalOptimizationRunRegistry(str(tmp_path))
    reg.create(_run())
    reg.write_state(OptimizationRunState(run_id="m-1-abc", status=RunStatus.RUNNING))
    assert reg.get_state("m-1-abc").status == RunStatus.RUNNING
    with pytest.raises(ResultNotReadyError):
        reg.get_result("m-1-abc")
    reg.write_result("m-1-abc", {"summary": {"x": 1}})
    assert reg.get_result("m-1-abc") == {"summary": {"x": 1}}


def test_list_filters_and_never_reads_result(tmp_path):
    reg = LocalOptimizationRunRegistry(str(tmp_path))
    reg.create(_run(run_id="m-1", model_id="m"))
    reg.create(_run(run_id="n-1", model_id="n"))
    reg.write_state(OptimizationRunState(run_id="m-1", status=RunStatus.COMPLETED))
    reg.write_state(OptimizationRunState(run_id="n-1", status=RunStatus.RUNNING))
    completed = reg.list(status=RunStatus.COMPLETED)
    assert [s.run_id for s in completed] == ["m-1"]
    assert reg.list(model_id="n")[0].run_id == "n-1"


def test_fingerprint_index(tmp_path):
    reg = LocalOptimizationRunRegistry(str(tmp_path))
    reg.create(_run(run_id="m-1", fp="fpX"))
    reg.put_fingerprint("fpX", "m-1")
    assert reg.find_by_fingerprint("fpX") == "m-1"
    assert reg.find_by_fingerprint("nope") is None


def test_delete_removes_run_and_missing_raises(tmp_path):
    reg = LocalOptimizationRunRegistry(str(tmp_path))
    reg.create(_run(run_id="m-1"))
    reg.delete("m-1")
    with pytest.raises(RunNotFoundError):
        reg.get_record("m-1")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_optimization_run_registry.py -v`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement the interface + local provider**

```python
# src/google_meridian_mcp_server/persistence/optimization_run_registry.py
"""Durable registry for optimization runs (interface + local provider)."""

from __future__ import annotations

import abc
import json
from pathlib import Path

from google_meridian_mcp_server.domain.errors import MeridianMcpError
from google_meridian_mcp_server.domain.optimization import (
    OptimizationRun,
    OptimizationRunState,
    OptimizationRunSummary,
    RunStatus,
)


class RunNotFoundError(MeridianMcpError):
    def __init__(self, run_id: str):
        super().__init__(
            error_code="optimization_run_not_found",
            message=f"Optimization run '{run_id}' was not found.",
            details={"run_id": run_id},
        )


class ResultNotReadyError(MeridianMcpError):
    def __init__(self, run_id: str, status: str):
        super().__init__(
            error_code="optimization_not_ready",
            message=f"Optimization run '{run_id}' has no result yet (status={status}).",
            details={"run_id": run_id, "status": status},
        )


def build_config_summary(run: OptimizationRun) -> str:
    cfg = run.config
    scenario = cfg.scenario.type
    dates = (
        f"{cfg.start_date or 'start'}..{cfg.end_date or 'end'}"
    )
    geos = "all geos" if not cfg.selected_geos else f"{len(cfg.selected_geos)} geos"
    objective = "KPI" if cfg.use_kpi else "ROAS"
    constraint = (
        f"+/-{int(cfg.constraint.pct * 100)}%"
        if cfg.constraint.mode == "global"
        else "per-channel"
    )
    return f"{scenario} . {dates} . {geos} . {objective} . {constraint}"


class OptimizationRunRegistry(abc.ABC):
    @abc.abstractmethod
    def create(self, run: OptimizationRun) -> None: ...
    @abc.abstractmethod
    def write_state(self, state: OptimizationRunState) -> None: ...
    @abc.abstractmethod
    def write_result(self, run_id: str, result: dict) -> None: ...
    @abc.abstractmethod
    def get_record(self, run_id: str) -> OptimizationRun: ...
    @abc.abstractmethod
    def get_state(self, run_id: str) -> OptimizationRunState: ...
    @abc.abstractmethod
    def get_result(self, run_id: str) -> dict: ...
    @abc.abstractmethod
    def list(
        self, *, model_id: str | None = None, status: RunStatus | None = None,
        limit: int | None = None,
    ) -> list[OptimizationRunSummary]: ...
    @abc.abstractmethod
    def delete(self, run_id: str) -> None: ...
    @abc.abstractmethod
    def find_by_fingerprint(self, fingerprint: str) -> str | None: ...
    @abc.abstractmethod
    def put_fingerprint(self, fingerprint: str, run_id: str) -> None: ...


class LocalOptimizationRunRegistry(OptimizationRunRegistry):
    def __init__(self, root: str) -> None:
        self._root = Path(root)
        self._runs = self._root / "runs"
        self._index = self._root / "index" / "by_fingerprint"

    def _run_dir(self, run_id: str) -> Path:
        return self._runs / run_id

    def create(self, run: OptimizationRun) -> None:
        d = self._run_dir(run.run_id)
        d.mkdir(parents=True, exist_ok=True)
        (d / "record.json").write_text(run.model_dump_json(indent=2))

    def write_state(self, state: OptimizationRunState) -> None:
        d = self._run_dir(state.run_id)
        if not d.is_dir():
            raise RunNotFoundError(state.run_id)
        (d / "state.json").write_text(state.model_dump_json(indent=2))

    def write_result(self, run_id: str, result: dict) -> None:
        d = self._run_dir(run_id)
        if not d.is_dir():
            raise RunNotFoundError(run_id)
        (d / "result.json").write_text(json.dumps(result, indent=2))

    def get_record(self, run_id: str) -> OptimizationRun:
        path = self._run_dir(run_id) / "record.json"
        if not path.is_file():
            raise RunNotFoundError(run_id)
        return OptimizationRun.model_validate_json(path.read_text())

    def get_state(self, run_id: str) -> OptimizationRunState:
        path = self._run_dir(run_id) / "state.json"
        if not path.is_file():
            if not self._run_dir(run_id).is_dir():
                raise RunNotFoundError(run_id)
            return OptimizationRunState(run_id=run_id, status=RunStatus.QUEUED)
        return OptimizationRunState.model_validate_json(path.read_text())

    def get_result(self, run_id: str) -> dict:
        state = self.get_state(run_id)
        path = self._run_dir(run_id) / "result.json"
        if not path.is_file():
            raise ResultNotReadyError(run_id, state.status.value)
        return json.loads(path.read_text())

    def list(self, *, model_id=None, status=None, limit=None):
        if not self._runs.is_dir():
            return []
        summaries: list[OptimizationRunSummary] = []
        for d in sorted(self._runs.iterdir()):
            record_path = d / "record.json"
            if not record_path.is_file():
                continue
            run = OptimizationRun.model_validate_json(record_path.read_text())
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
        d = self._run_dir(run_id)
        if not d.is_dir():
            raise RunNotFoundError(run_id)
        record_path = d / "record.json"
        if record_path.is_file():
            fp = OptimizationRun.model_validate_json(record_path.read_text()).config_fingerprint
            pointer = self._index / fp
            if pointer.is_file() and pointer.read_text().strip() == run_id:
                pointer.unlink()
        for child in d.iterdir():
            child.unlink()
        d.rmdir()

    def find_by_fingerprint(self, fingerprint: str) -> str | None:
        pointer = self._index / fingerprint
        return pointer.read_text().strip() if pointer.is_file() else None

    def put_fingerprint(self, fingerprint: str, run_id: str) -> None:
        self._index.mkdir(parents=True, exist_ok=True)
        (self._index / fingerprint).write_text(run_id)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_optimization_run_registry.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/google_meridian_mcp_server/persistence/optimization_run_registry.py tests/unit/test_optimization_run_registry.py
git commit -m "feat: local optimization run registry with fingerprint index"
```

---

## Task 5: Routing heuristic

**Files:**
- Create: `src/google_meridian_mcp_server/execution/__init__.py` (empty)
- Create: `src/google_meridian_mcp_server/execution/routing.py`
- Test: `tests/unit/test_routing.py`

**Interfaces:**
- Consumes: `ComputeTier` from `domain.models`; a `MeridianInterrogator`-like object exposing `geo_names()`, `get_time_values()`, `get_data_inputs()`, and `_mmm.inference_data.posterior`.
- Produces:
  - `model_size_features(interrogator) -> dict` with `n_geos`, `n_time_units`, `n_channels`, `n_posterior_samples`.
  - `size_score(features: dict) -> int`.
  - `resolve_tier(score, *, requested, allowed, thresholds) -> str` — returns a tier value, honoring an explicit request, mapping `auto` via thresholds, and falling back to the nearest allowed tier.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_routing.py
import pytest

from google_meridian_mcp_server.execution.routing import resolve_tier, size_score


def test_size_score_multiplies_dims():
    assert size_score(
        {"n_geos": 5, "n_time_units": 100, "n_channels": 3, "n_posterior_samples": 400}
    ) == 5 * 100 * 3 * 400


def test_resolve_tier_auto_thresholds():
    allowed = ("local", "cloud_cpu", "cloud_gpu")
    th = (1_000, 1_000_000)
    assert resolve_tier(500, requested="auto", allowed=allowed, thresholds=th) == "local"
    assert resolve_tier(50_000, requested="auto", allowed=allowed, thresholds=th) == "cloud_cpu"
    assert resolve_tier(5_000_000, requested="auto", allowed=allowed, thresholds=th) == "cloud_gpu"


def test_resolve_tier_explicit_request_must_be_allowed():
    with pytest.raises(ValueError, match="not allowed"):
        resolve_tier(10, requested="cloud_gpu", allowed=("local",), thresholds=(1, 2))
    assert resolve_tier(10, requested="local", allowed=("local",), thresholds=(1, 2)) == "local"


def test_resolve_tier_auto_falls_back_to_nearest_allowed():
    # local disabled: a small job still routes to the cheapest allowed cloud tier.
    assert resolve_tier(
        10, requested="auto", allowed=("cloud_cpu", "cloud_gpu"), thresholds=(1_000, 1_000_000)
    ) == "cloud_cpu"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_routing.py -v`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement routing**

```python
# src/google_meridian_mcp_server/execution/routing.py
"""Problem-size heuristic and compute-tier resolution."""

from __future__ import annotations

from typing import Any

from google_meridian_mcp_server.domain.models import ComputeTier

# Cheapest-first ordering used for nearest-allowed fallback.
_TIER_ORDER = (ComputeTier.LOCAL.value, ComputeTier.CLOUD_CPU.value, ComputeTier.CLOUD_GPU.value)


def model_size_features(interrogator: Any) -> dict[str, int]:
    inputs = interrogator.get_data_inputs()
    n_channels = len(inputs["media"]) + len(inputs["rf_media"])
    posterior = interrogator._mmm.inference_data.posterior
    sizes = dict(posterior.sizes)
    n_posterior_samples = int(sizes.get("chain", 1)) * int(sizes.get("draw", 1))
    return {
        "n_geos": max(1, len(interrogator.geo_names())),
        "n_time_units": max(1, len(interrogator.get_time_values())),
        "n_channels": max(1, n_channels),
        "n_posterior_samples": max(1, n_posterior_samples),
    }


def size_score(features: dict[str, int]) -> int:
    return (
        features["n_geos"]
        * features["n_time_units"]
        * features["n_channels"]
        * features["n_posterior_samples"]
    )


def _ideal_auto_tier(score: int, thresholds: tuple[int, int]) -> str:
    t_local, t_gpu = thresholds
    if score < t_local:
        return ComputeTier.LOCAL.value
    if score < t_gpu:
        return ComputeTier.CLOUD_CPU.value
    return ComputeTier.CLOUD_GPU.value


def resolve_tier(
    score: int, *, requested: str, allowed: tuple[str, ...], thresholds: tuple[int, int]
) -> str:
    if requested != "auto":
        if requested not in allowed:
            raise ValueError(
                f"compute_tier '{requested}' is not allowed by this deployment "
                f"(allowed: {list(allowed)})"
            )
        return requested
    ideal = _ideal_auto_tier(score, thresholds)
    if ideal in allowed:
        return ideal
    # Nearest-allowed fallback: scan from the ideal tier toward more capable,
    # then toward cheaper, returning the first allowed tier.
    order = list(_TIER_ORDER)
    idx = order.index(ideal)
    for candidate in order[idx:] + order[:idx][::-1]:
        if candidate in allowed:
            return candidate
    raise ValueError(f"no allowed tier among {list(allowed)}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_routing.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/google_meridian_mcp_server/execution/__init__.py src/google_meridian_mcp_server/execution/routing.py tests/unit/test_routing.py
git commit -m "feat: size-score routing heuristic with allowed-tier resolution"
```

---

## Task 6: Optimizer facade (run optimize + build structured result)

**Files:**
- Create: `src/google_meridian_mcp_server/meridian/optimizer_facade.py`
- Test: `tests/unit/test_optimizer_facade.py`

**Interfaces:**
- Consumes: a loaded Meridian `mmm`, `OptimizationConfig`, `to_optimize_kwargs`, `MeridianInterrogator` (for channel order + use_kpi resolution).
- Produces: `OptimizerFacade(mmm)` with:
  - `resolve_use_kpi(config) -> bool`
  - `channel_order() -> list[str]`
  - `run(config) -> dict` — calls `BudgetOptimizer.optimize(**kwargs)` and returns the structured result dict (`outcome_mode`, `summary`, `channel_tables`, `allocation`, `spend_delta`).
  - `build_result(nonopt, opt, *, use_kpi) -> dict` — pure builder over two xarray datasets (tested directly with fakes).

- [ ] **Step 1: Write the failing test**

Use lightweight fakes that mimic the `optimized_data`/`nonoptimized_data` xarray API the showcase reads (`.coords`, `.attrs`, `["spend"].sel(channel=...).sum().values`, `["pct_of_spend"]`, `["incremental_outcome"].sel(channel=..., metric="mean").sum().values`, `["roi"]`, `["mroi"]`, `["cpik"]`, `["effectiveness"]`). Provide a small helper building an `xarray.Dataset` so we exercise the real API, not a mock.

```python
# tests/unit/test_optimizer_facade.py
import numpy as np
import xarray as xr

from google_meridian_mcp_server.meridian.optimizer_facade import OptimizerFacade


def _dataset(channels, *, budget, total_outcome, total_roi, spend, roi, mroi, cpik, eff, inc):
    metrics = ["mean", "median", "ci_lo", "ci_hi"]

    def per_channel(values_by_metric):
        return xr.DataArray(
            np.array([[values_by_metric[m][c] for m in metrics] for c in range(len(channels))]),
            dims=("channel", "metric"), coords={"channel": channels, "metric": metrics},
        )

    ds = xr.Dataset(
        {
            "spend": xr.DataArray(np.array(spend), dims="channel", coords={"channel": channels}),
            "pct_of_spend": xr.DataArray(
                np.array(spend) / np.sum(spend), dims="channel", coords={"channel": channels}
            ),
            "incremental_outcome": per_channel(inc),
            "roi": per_channel(roi),
            "mroi": per_channel(mroi),
            "cpik": per_channel(cpik),
            "effectiveness": per_channel(eff),
        }
    )
    ds.attrs.update(
        budget=budget, total_incremental_outcome=total_outcome, total_roi=total_roi
    )
    return ds


def _const(channels, value):
    return {c: value for c in range(len(channels))}


def test_build_result_revenue_mode():
    channels = ["tv", "search"]
    common = dict(
        roi={m: _const(channels, 3.0) for m in ["mean", "median", "ci_lo", "ci_hi"]},
        mroi={m: _const(channels, 2.0) for m in ["mean", "median", "ci_lo", "ci_hi"]},
        cpik={m: _const(channels, 0.5) for m in ["mean", "median", "ci_lo", "ci_hi"]},
        eff={m: _const(channels, 0.1) for m in ["mean", "median", "ci_lo", "ci_hi"]},
        inc={m: _const(channels, 1000.0) for m in ["mean", "median", "ci_lo", "ci_hi"]},
    )
    nonopt = _dataset(channels, budget=1000.0, total_outcome=2000.0, total_roi=2.0,
                      spend=[600.0, 400.0], **common)
    opt = _dataset(channels, budget=1000.0, total_outcome=2600.0, total_roi=2.6,
                   spend=[300.0, 700.0], **common)

    result = OptimizerFacade.build_result(nonopt, opt, use_kpi=False)
    assert result["outcome_mode"] == "revenue"
    assert result["summary"]["optimized_efficiency"] == 2.6
    assert result["summary"]["non_optimized_efficiency"] == 2.0
    initial = {r["channel"]: r for r in result["channel_tables"]["initial"]}
    assert initial["tv"]["spend"] == 600.0
    assert initial["tv"]["roi"] == 3.0
    # spend_delta sorted negatives-first then positives-descending
    deltas = {r["channel"]: r["spend"] for r in result["spend_delta"]}
    assert deltas["tv"] == -300.0 and deltas["search"] == 300.0
    assert result["allocation"][0]["channel"] in channels


def test_build_result_kpi_mode_inverts_efficiency():
    channels = ["tv"]
    common = dict(
        roi={m: _const(channels, 4.0) for m in ["mean", "median", "ci_lo", "ci_hi"]},
        mroi={m: _const(channels, 2.0) for m in ["mean", "median", "ci_lo", "ci_hi"]},
        cpik={m: _const(channels, 0.25) for m in ["mean", "median", "ci_lo", "ci_hi"]},
        eff={m: _const(channels, 0.1) for m in ["mean", "median", "ci_lo", "ci_hi"]},
        inc={m: _const(channels, 100.0) for m in ["mean", "median", "ci_lo", "ci_hi"]},
    )
    nonopt = _dataset(channels, budget=100.0, total_outcome=100.0, total_roi=4.0,
                      spend=[100.0], **common)
    opt = _dataset(channels, budget=100.0, total_outcome=100.0, total_roi=4.0,
                   spend=[100.0], **common)
    result = OptimizerFacade.build_result(nonopt, opt, use_kpi=True)
    assert result["outcome_mode"] == "kpi"
    assert result["summary"]["optimized_efficiency"] == 0.25  # 1/total_roi
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_optimizer_facade.py -v`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement the facade**

Port the showcase `_build_*` helpers (revenue uses `roi`/`mroi`; kpi uses `cpik`/`1/total_roi`). `run()` builds kwargs and calls Meridian.

```python
# src/google_meridian_mcp_server/meridian/optimizer_facade.py
"""Facade over Meridian BudgetOptimizer: run optimization, build structured result."""

from __future__ import annotations

from typing import Any

from google_meridian_mcp_server.domain.optimization import (
    OptimizationConfig,
    to_optimize_kwargs,
)
from google_meridian_mcp_server.meridian.interrogator import MeridianInterrogator


def _sig6(value: float) -> float:
    return float(f"{float(value):.6g}")


class OptimizerFacade(MeridianInterrogator):
    """Runs BudgetOptimizer and shapes its OptimizationResults into JSON."""

    def channel_order(self) -> list[str]:
        inputs = self.get_data_inputs()
        return list(inputs["media"]) + list(inputs["rf_media"])

    def resolve_use_kpi(self, config: OptimizationConfig) -> bool:
        if config.use_kpi is not None:
            return config.use_kpi
        return not self.has_revenue_per_kpi()

    def run(self, config: OptimizationConfig) -> dict[str, Any]:
        from meridian.analysis import optimizer as optimizer_mod

        use_kpi = self.resolve_use_kpi(config)
        kwargs = to_optimize_kwargs(
            config, channel_order=self.channel_order(), use_kpi=use_kpi
        )
        budget_optimizer = optimizer_mod.BudgetOptimizer(self._mmm)
        results = budget_optimizer.optimize(**kwargs)
        return self.build_result(
            results.nonoptimized_data, results.optimized_data, use_kpi=use_kpi
        )

    @staticmethod
    def build_result(nonopt, opt, *, use_kpi: bool) -> dict[str, Any]:
        outcome_mode = "kpi" if use_kpi else "revenue"
        return {
            "outcome_mode": outcome_mode,
            "summary": OptimizerFacade._summary(nonopt, opt, use_kpi),
            "channel_tables": {
                "initial": OptimizerFacade._channel_rows(nonopt, use_kpi),
                "optimized": OptimizerFacade._channel_rows(opt, use_kpi),
            },
            "allocation": OptimizerFacade._allocation(opt),
            "spend_delta": OptimizerFacade._spend_delta(nonopt, opt),
        }

    @staticmethod
    def _efficiency(total_roi: float, use_kpi: bool) -> float:
        if not use_kpi:
            return total_roi
        return float("inf") if total_roi == 0 else 1.0 / total_roi

    @staticmethod
    def _summary(nonopt, opt, use_kpi: bool) -> dict[str, float]:
        return {
            "non_optimized_budget": _sig6(nonopt.attrs["budget"]),
            "optimized_budget": _sig6(opt.attrs["budget"]),
            "non_optimized_efficiency": _sig6(
                OptimizerFacade._efficiency(float(nonopt.attrs["total_roi"]), use_kpi)
            ),
            "optimized_efficiency": _sig6(
                OptimizerFacade._efficiency(float(opt.attrs["total_roi"]), use_kpi)
            ),
            "non_optimized_incremental_outcome": _sig6(
                nonopt.attrs["total_incremental_outcome"]
            ),
            "optimized_incremental_outcome": _sig6(
                opt.attrs["total_incremental_outcome"]
            ),
        }

    @staticmethod
    def _channel_rows(data, use_kpi: bool) -> list[dict[str, Any]]:
        channels = [str(c) for c in data.coords["channel"].values.tolist()]
        rows: list[dict[str, Any]] = []
        for channel in channels:
            spend = float(data["spend"].sel(channel=channel).sum().values)
            pct = float(data["pct_of_spend"].sel(channel=channel).values) * 100.0
            inc = float(
                data["incremental_outcome"].sel(channel=channel, metric="mean").sum().values
            )
            roi = float(data["roi"].sel(channel=channel, metric="mean").values)
            mroi = float(data["mroi"].sel(channel=channel, metric="mean").values)
            cpik = float(data["cpik"].sel(channel=channel, metric="median").values)
            eff = float(data["effectiveness"].sel(channel=channel, metric="mean").values)
            rows.append(
                {
                    "channel": channel,
                    "spend": _sig6(spend),
                    "pct_of_spend": _sig6(pct),
                    "incremental_outcome": _sig6(inc),
                    "roi": _sig6(roi),
                    "mroi": _sig6(mroi),
                    "cpik": _sig6(cpik),
                    "effectiveness": _sig6(eff),
                }
            )
        return rows

    @staticmethod
    def _allocation(opt) -> list[dict[str, Any]]:
        channels = [str(c) for c in opt.coords["channel"].values.tolist()]
        return [
            {"channel": c, "spend": _sig6(float(opt["spend"].sel(channel=c).sum().values))}
            for c in channels
        ]

    @staticmethod
    def _spend_delta(nonopt, opt) -> list[dict[str, Any]]:
        channels = [str(c) for c in opt.coords["channel"].values.tolist()]
        deltas = [
            (
                c,
                float(opt["spend"].sel(channel=c).sum().values)
                - float(nonopt["spend"].sel(channel=c).sum().values),
            )
            for c in channels
        ]
        negative = sorted([d for d in deltas if d[1] < 0], key=lambda d: d[1])
        positive = sorted([d for d in deltas if d[1] >= 0], key=lambda d: d[1], reverse=True)
        return [{"channel": c, "spend": _sig6(v)} for c, v in (negative + positive)]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_optimizer_facade.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/google_meridian_mcp_server/meridian/optimizer_facade.py tests/unit/test_optimizer_facade.py
git commit -m "feat: optimizer facade builds structured optimization result"
```

---

## Task 7: Shared bootstrap helpers

**Files:**
- Create: `src/google_meridian_mcp_server/bootstrap.py`
- Modify: `src/google_meridian_mcp_server/server.py:26-59` (lifespan reuses bootstrap)
- Test: `tests/unit/test_bootstrap.py`

**Interfaces:**
- Consumes: `RuntimeConfig`, providers, caches, `ModelCatalog`.
- Produces:
  - `build_model_catalog(cfg) -> ModelCatalog`
  - `build_registry(cfg) -> OptimizationRunRegistry` (Phase 1: returns `LocalOptimizationRunRegistry`; raises `ValueError` for gcs registry — that's Phase 2).

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_bootstrap.py
import pytest

from google_meridian_mcp_server.bootstrap import build_model_catalog, build_registry
from google_meridian_mcp_server.domain.models import RuntimeConfig
from google_meridian_mcp_server.meridian.catalog import ModelCatalog
from google_meridian_mcp_server.persistence.optimization_run_registry import (
    LocalOptimizationRunRegistry,
)


def _cfg(tmp_path, **over):
    base = dict(persistence_backend="local", local_models_root=str(tmp_path),
                optimization_runs_root=str(tmp_path / "runs"))
    base.update(over)
    return RuntimeConfig(**base)


def test_build_model_catalog(tmp_path):
    assert isinstance(build_model_catalog(_cfg(tmp_path)), ModelCatalog)


def test_build_registry_local(tmp_path):
    assert isinstance(build_registry(_cfg(tmp_path)), LocalOptimizationRunRegistry)


def test_build_registry_gcs_not_supported_phase1(tmp_path):
    cfg = _cfg(tmp_path, registry_backend="gcs", gcs_bucket="b", gcs_models_prefix="p/")
    with pytest.raises(ValueError, match="Phase 2"):
        build_registry(cfg)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_bootstrap.py -v`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement bootstrap and refactor the lifespan**

```python
# src/google_meridian_mcp_server/bootstrap.py
"""Shared construction of runtime objects (used by the server lifespan and worker)."""

from __future__ import annotations

from google_meridian_mcp_server.domain.models import PersistenceBackend, RuntimeConfig
from google_meridian_mcp_server.meridian.catalog import ModelCatalog
from google_meridian_mcp_server.persistence.cache import (
    DiscoveryCache,
    MaterializationCache,
)
from google_meridian_mcp_server.persistence.gcs_provider import GcsModelProvider
from google_meridian_mcp_server.persistence.local_provider import LocalModelProvider
from google_meridian_mcp_server.persistence.optimization_run_registry import (
    LocalOptimizationRunRegistry,
    OptimizationRunRegistry,
)


def build_model_catalog(cfg: RuntimeConfig) -> ModelCatalog:
    if cfg.persistence_backend == PersistenceBackend.GCS.value:
        provider = GcsModelProvider(cfg.gcs_bucket, cfg.gcs_models_prefix)
    else:
        provider = LocalModelProvider(cfg.local_models_root)
    discovery = DiscoveryCache(provider, cfg.discovery_ttl_seconds)
    materialization = MaterializationCache(provider, cfg.model_cache_root)
    return ModelCatalog(discovery, materialization)


def build_registry(cfg: RuntimeConfig) -> OptimizationRunRegistry:
    if cfg.resolved_registry_backend == PersistenceBackend.GCS.value:
        raise ValueError("gcs registry is implemented in Phase 2, not yet available")
    return LocalOptimizationRunRegistry(cfg.optimization_runs_root)
```

Then refactor `server.py` `_lifespan` to use `build_model_catalog(cfg)` in place of the inline provider/cache/catalog construction (lines 36-44), keeping `result_cache` as-is. (Executor/service wiring is added in Task 10.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_bootstrap.py tests/unit/test_server.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/google_meridian_mcp_server/bootstrap.py src/google_meridian_mcp_server/server.py tests/unit/test_bootstrap.py
git commit -m "feat: shared bootstrap helpers for catalog and registry"
```

---

## Task 8: Worker entrypoint

**Files:**
- Create: `src/google_meridian_mcp_server/execution/worker.py`
- Test: `tests/unit/test_optimization_worker.py`

**Interfaces:**
- Consumes: a registry (`OptimizationRunRegistry`), a `ModelCatalog`, `OptimizerFacade`, `RunStatus`/`RunPhase`, `datetime`.
- Produces:
  - `run_worker(run_id, *, registry, catalog, backend) -> int` — the testable core: sets state running, runs the optimization, writes result + completed (or failed), returns exit code.
  - `main(argv=None) -> int` — CLI entry: reads `RUN_ID` + backend from env, sets `MERIDIAN_BACKEND`, builds catalog+registry from `load_config()`, calls `run_worker`. Importable as `python -m google_meridian_mcp_server.execution.worker`.

- [ ] **Step 1: Write the failing test**

Drive `run_worker` directly with a fake catalog whose `get_facade` returns a stub `OptimizerFacade`-like object, and a real `LocalOptimizationRunRegistry`.

```python
# tests/unit/test_optimization_worker.py
import pytest

from google_meridian_mcp_server.domain.optimization import (
    OptimizationConfig,
    OptimizationRun,
    RunStatus,
)
from google_meridian_mcp_server.execution.worker import run_worker
from google_meridian_mcp_server.persistence.optimization_run_registry import (
    LocalOptimizationRunRegistry,
)


class _FakeFacade:
    def __init__(self, result=None, boom=False):
        self._result = result or {"outcome_mode": "revenue", "summary": {
            "optimized_efficiency": 2.6, "non_optimized_efficiency": 2.0,
            "optimized_budget": 1000.0}}
        self._boom = boom

    def run(self, config):
        if self._boom:
            raise RuntimeError("optimize blew up")
        return self._result


class _FakeCatalog:
    def __init__(self, facade):
        self._facade = facade

    def get_facade(self, model_id):
        return self._facade


def _seed_run(reg, run_id="m-1"):
    cfg = OptimizationConfig.model_validate({"scenario": {"type": "fixed_budget"}})
    reg.create(OptimizationRun(
        run_id=run_id, label="l", model_id="m", config=cfg, config_fingerprint="fp",
        compute_tier_requested="auto", compute_tier_resolved="local",
        backend="tensorflow", size_score=1, created_at="2026-06-29T00:00:00+00:00",
        meridian_version="1.7.0", server_version="0.1.0"))


def test_worker_happy_path_writes_result_and_completed(tmp_path):
    reg = LocalOptimizationRunRegistry(str(tmp_path))
    _seed_run(reg)
    code = run_worker("m-1", registry=reg, catalog=_FakeCatalog(_FakeFacade()), backend="tensorflow")
    assert code == 0
    assert reg.get_state("m-1").status == RunStatus.COMPLETED
    assert reg.get_state("m-1").headline is not None
    assert reg.get_result("m-1")["summary"]["optimized_efficiency"] == 2.6


def test_worker_failure_writes_failed_state(tmp_path):
    reg = LocalOptimizationRunRegistry(str(tmp_path))
    _seed_run(reg)
    code = run_worker("m-1", registry=reg, catalog=_FakeCatalog(_FakeFacade(boom=True)),
                      backend="tensorflow")
    assert code == 1
    state = reg.get_state("m-1")
    assert state.status == RunStatus.FAILED
    assert "optimize blew up" in state.error["message"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_optimization_worker.py -v`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement the worker**

```python
# src/google_meridian_mcp_server/execution/worker.py
"""Shared optimization worker: runs one optimization and writes it to the registry."""

from __future__ import annotations

import os
import sys
import traceback
from datetime import datetime, timezone
from typing import Any

from google_meridian_mcp_server.domain.optimization import (
    OptimizationRunState,
    RunPhase,
    RunStatus,
)
from google_meridian_mcp_server.persistence.optimization_run_registry import (
    OptimizationRunRegistry,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _headline(result: dict[str, Any]) -> str:
    summary = result.get("summary", {})
    mode = result.get("outcome_mode", "revenue")
    label = "ROAS" if mode == "revenue" else "CPIK"
    non_opt = summary.get("non_optimized_efficiency")
    opt = summary.get("optimized_efficiency")
    budget = summary.get("optimized_budget")
    return f"{label} {non_opt} -> {opt} at budget {budget}"


def run_worker(
    run_id: str, *, registry: OptimizationRunRegistry, catalog: Any, backend: str
) -> int:
    record = registry.get_record(run_id)
    started = _now()
    registry.write_state(
        OptimizationRunState(
            run_id=run_id, status=RunStatus.RUNNING, phase=RunPhase.LOADING_MODEL,
            started_at=started, heartbeat_at=started,
        )
    )
    try:
        facade = catalog.get_facade(record.model_id)
        registry.write_state(
            OptimizationRunState(
                run_id=run_id, status=RunStatus.RUNNING, phase=RunPhase.OPTIMIZING,
                started_at=started, heartbeat_at=_now(),
            )
        )
        result = facade.run(record.config)
        registry.write_result(run_id, result)
        registry.write_state(
            OptimizationRunState(
                run_id=run_id, status=RunStatus.COMPLETED, started_at=started,
                finished_at=_now(), headline=_headline(result),
            )
        )
        return 0
    except Exception as exc:  # noqa: BLE001 - worker boundary: record then exit non-zero
        registry.write_state(
            OptimizationRunState(
                run_id=run_id, status=RunStatus.FAILED, started_at=started,
                finished_at=_now(),
                error={"code": "optimization_failed", "message": str(exc),
                       "traceback": traceback.format_exc()},
            )
        )
        return 1


def main(argv: list[str] | None = None) -> int:
    run_id = os.environ["OPTIMIZATION_RUN_ID"]
    backend = os.environ.get("MERIDIAN_BACKEND", "tensorflow")
    os.environ["MERIDIAN_BACKEND"] = backend  # set before importing meridian (catalog does)

    from google_meridian_mcp_server.bootstrap import build_model_catalog, build_registry
    from google_meridian_mcp_server.config import load_config

    cfg = load_config()
    return run_worker(
        run_id, registry=build_registry(cfg), catalog=build_model_catalog(cfg), backend=backend
    )


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
```

> Heartbeat note: in Phase 1 the worker writes `heartbeat_at` at each phase transition (sufficient for the tiny validation runs and for crash detection). A periodic daemon-thread heartbeat for long geo runs is added in Phase 2 alongside `progress_fraction`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_optimization_worker.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/google_meridian_mcp_server/execution/worker.py tests/unit/test_optimization_worker.py
git commit -m "feat: shared optimization worker writes run lifecycle to registry"
```

---

## Task 9: BaseExecutor + SubprocessExecutor

**Files:**
- Create: `src/google_meridian_mcp_server/execution/base_executor.py`
- Create: `src/google_meridian_mcp_server/execution/subprocess_executor.py`
- Test: `tests/unit/test_subprocess_executor.py`

**Interfaces:**
- Consumes: `OptimizationRunRegistry`, `OptimizationRun`, `OptimizationRunState`, `RunStatus`, `RuntimeConfig` (for `optimization_max_parallel`, `optimization_heartbeat_stale_seconds`).
- Produces:
  - `BaseExecutor(registry, *, max_parallel, heartbeat_stale_seconds)` with `submit(run: OptimizationRun) -> None` (writes queued state, then `pump()`), `pump() -> None` (reap finished, launch queued while slots free, reconcile stale), abstract `_launch(run) -> Any` (returns an opaque handle) and `_is_alive(handle) -> bool`.
  - `SubprocessExecutor(registry, *, max_parallel, heartbeat_stale_seconds, backend, python_executable=sys.executable)` implementing `_launch` (spawn the worker module with env) and `_is_alive` (`Popen.poll() is None`).

- [ ] **Step 1: Write the failing test**

Use a fake subclass of `BaseExecutor` that launches an in-test "process" object so we can test the gate/reconcile logic deterministically without real subprocesses, plus one test that `SubprocessExecutor` builds the right command/env (monkeypatch `subprocess.Popen`).

```python
# tests/unit/test_subprocess_executor.py
import subprocess

from google_meridian_mcp_server.domain.optimization import (
    OptimizationConfig,
    OptimizationRun,
    OptimizationRunState,
    RunStatus,
)
from google_meridian_mcp_server.execution.base_executor import BaseExecutor
from google_meridian_mcp_server.execution.subprocess_executor import SubprocessExecutor
from google_meridian_mcp_server.persistence.optimization_run_registry import (
    LocalOptimizationRunRegistry,
)


def _run(run_id):
    cfg = OptimizationConfig.model_validate({"scenario": {"type": "fixed_budget"}})
    return OptimizationRun(
        run_id=run_id, label="l", model_id="m", config=cfg, config_fingerprint="fp",
        compute_tier_requested="auto", compute_tier_resolved="local",
        backend="tensorflow", size_score=1, created_at="2026-06-29T00:00:00+00:00",
        meridian_version="1.7.0", server_version="0.1.0")


class _Handle:
    def __init__(self):
        self.alive = True


class _FakeExecutor(BaseExecutor):
    def __init__(self, registry, **kw):
        super().__init__(registry, **kw)
        self.launched: list[str] = []

    def _launch(self, run):
        self.launched.append(run.run_id)
        return _Handle()

    def _is_alive(self, handle):
        return handle.alive


def test_gate_limits_concurrent_launches(tmp_path):
    reg = LocalOptimizationRunRegistry(str(tmp_path))
    ex = _FakeExecutor(reg, max_parallel=1, heartbeat_stale_seconds=60)
    reg.create(_run("a")); ex.submit(_run("a"))
    reg.create(_run("b")); ex.submit(_run("b"))
    assert ex.launched == ["a"]                       # b is gated
    assert reg.get_state("b").status == RunStatus.QUEUED
    # finish a -> next pump launches b
    ex._handles["a"].alive = False
    ex.pump()
    assert ex.launched == ["a", "b"]


def test_subprocess_executor_builds_worker_command(tmp_path, monkeypatch):
    reg = LocalOptimizationRunRegistry(str(tmp_path))
    captured = {}

    class _Popen:
        def __init__(self, cmd, env=None):
            captured["cmd"] = cmd
            captured["env"] = env
        def poll(self):
            return None

    monkeypatch.setattr(subprocess, "Popen", _Popen)
    ex = SubprocessExecutor(reg, max_parallel=2, heartbeat_stale_seconds=60, backend="jax")
    reg.create(_run("a"))
    ex.submit(_run("a"))
    assert "google_meridian_mcp_server.execution.worker" in captured["cmd"]
    assert captured["env"]["OPTIMIZATION_RUN_ID"] == "a"
    assert captured["env"]["MERIDIAN_BACKEND"] == "jax"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_subprocess_executor.py -v`
Expected: FAIL (modules missing).

- [ ] **Step 3: Implement BaseExecutor and SubprocessExecutor**

```python
# src/google_meridian_mcp_server/execution/base_executor.py
"""Executor template: concurrency gate, launch lifecycle, crash reconciliation."""

from __future__ import annotations

import abc
from datetime import datetime, timezone
from typing import Any

from google_meridian_mcp_server.domain.optimization import (
    OptimizationRun,
    OptimizationRunState,
    RunStatus,
)
from google_meridian_mcp_server.persistence.optimization_run_registry import (
    OptimizationRunRegistry,
)


class BaseExecutor(abc.ABC):
    def __init__(
        self, registry: OptimizationRunRegistry, *, max_parallel: int,
        heartbeat_stale_seconds: int,
    ) -> None:
        self._registry = registry
        self._max_parallel = max_parallel
        self._stale_seconds = heartbeat_stale_seconds
        self._handles: dict[str, Any] = {}
        self._queue: list[str] = []

    @abc.abstractmethod
    def _launch(self, run: OptimizationRun) -> Any: ...
    @abc.abstractmethod
    def _is_alive(self, handle: Any) -> bool: ...

    def submit(self, run: OptimizationRun) -> None:
        self._registry.write_state(
            OptimizationRunState(run_id=run.run_id, status=RunStatus.QUEUED)
        )
        self._queue.append(run.run_id)
        self.pump()

    def pump(self) -> None:
        self._reap()
        while self._queue and len(self._handles) < self._max_parallel:
            run_id = self._queue.pop(0)
            run = self._registry.get_record(run_id)
            self._handles[run_id] = self._launch(run)

    def _reap(self) -> None:
        for run_id, handle in list(self._handles.items()):
            if self._is_alive(handle):
                self._reconcile_stale(run_id)
                continue
            del self._handles[run_id]
            state = self._registry.get_state(run_id)
            if state.status in (RunStatus.RUNNING, RunStatus.QUEUED):
                # process exited without writing a terminal state -> crashed.
                self._registry.write_state(
                    OptimizationRunState(
                        run_id=run_id, status=RunStatus.FAILED,
                        error={"code": "worker_lost",
                               "message": "worker exited without writing a result"},
                    )
                )

    def _reconcile_stale(self, run_id: str) -> None:
        state = self._registry.get_state(run_id)
        if state.status != RunStatus.RUNNING or not state.heartbeat_at:
            return
        last = datetime.fromisoformat(state.heartbeat_at)
        age = (datetime.now(timezone.utc) - last).total_seconds()
        if age > self._stale_seconds:
            self._registry.write_state(
                OptimizationRunState(
                    run_id=run_id, status=RunStatus.FAILED,
                    error={"code": "worker_lost",
                           "message": f"heartbeat stale ({int(age)}s)"},
                )
            )
            self._handles.pop(run_id, None)
```

```python
# src/google_meridian_mcp_server/execution/subprocess_executor.py
"""Executor that runs the worker as a local subprocess."""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Any

from google_meridian_mcp_server.domain.optimization import OptimizationRun
from google_meridian_mcp_server.execution.base_executor import BaseExecutor
from google_meridian_mcp_server.persistence.optimization_run_registry import (
    OptimizationRunRegistry,
)


class SubprocessExecutor(BaseExecutor):
    def __init__(
        self, registry: OptimizationRunRegistry, *, max_parallel: int,
        heartbeat_stale_seconds: int, backend: str,
        python_executable: str = sys.executable,
    ) -> None:
        super().__init__(
            registry, max_parallel=max_parallel,
            heartbeat_stale_seconds=heartbeat_stale_seconds,
        )
        self._backend = backend
        self._python = python_executable

    def _launch(self, run: OptimizationRun) -> Any:
        env = dict(os.environ)
        env["OPTIMIZATION_RUN_ID"] = run.run_id
        env["MERIDIAN_BACKEND"] = self._backend
        return subprocess.Popen(
            [self._python, "-m", "google_meridian_mcp_server.execution.worker"],
            env=env,
        )

    def _is_alive(self, handle: Any) -> bool:
        return handle.poll() is None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_subprocess_executor.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/google_meridian_mcp_server/execution/base_executor.py src/google_meridian_mcp_server/execution/subprocess_executor.py tests/unit/test_subprocess_executor.py
git commit -m "feat: base executor gate/reconcile + subprocess executor"
```

---

## Task 10: OptimizationService

**Files:**
- Create: `src/google_meridian_mcp_server/services/optimization_service.py`
- Test: `tests/unit/test_optimization_service.py`

**Interfaces:**
- Consumes: `ModelCatalog` (`get_interrogator`, `get_facade`), an executor (`submit`/`pump`), `OptimizationRunRegistry`, routing functions, `config_fingerprint`, `RuntimeConfig`.
- Produces: `OptimizationService(catalog, registry, executor, cfg)` with:
  - `run_optimization(model_id, config_dict, *, label=None, note=None, compute_tier="auto", force_rerun=False) -> dict` → `{run_id, status, compute_tier_resolved, backend, size_score, reused}`.
  - `get_status(run_id) -> dict`, `get_result(run_id) -> dict`, `list_runs(model_id=None, status=None, limit=None) -> dict`, `delete(run_id) -> dict`.
  - Raises `ModelNotFoundError` for unknown model; validates config via pydantic; validates per-channel coverage via `to_optimize_kwargs` dry-run (catches `ValueError` → `InvalidOptimizationConfigError`).

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_optimization_service.py
import pytest

from google_meridian_mcp_server.domain.optimization import RunStatus
from google_meridian_mcp_server.domain.models import RuntimeConfig
from google_meridian_mcp_server.persistence.optimization_run_registry import (
    LocalOptimizationRunRegistry,
)
from google_meridian_mcp_server.services.optimization_service import OptimizationService


class _Interrogator:
    def geo_names(self): return ["g1", "g2"]
    def get_time_values(self): return [str(i) for i in range(10)]
    def get_data_inputs(self):
        return {"media": ["tv", "search"], "rf_media": []}
    has = True
    class _P:
        sizes = {"chain": 2, "draw": 50}
    class _M:
        class inference_data:
            posterior = _P()
    _mmm = _M()


class _Facade(_Interrogator):
    def resolve_use_kpi(self, config): return False
    def channel_order(self): return ["tv", "search"]


class _Catalog:
    def __init__(self): self._f = _Facade()
    def get_interrogator(self, model_id): return self._f
    def get_facade(self, model_id): return self._f


class _Catalog404(_Catalog):
    def get_interrogator(self, model_id):
        from google_meridian_mcp_server.domain.errors import ModelNotFoundError
        raise ModelNotFoundError(model_id)


class _Executor:
    def __init__(self): self.submitted = []
    def submit(self, run): self.submitted.append(run.run_id)
    def pump(self): pass


def _svc(tmp_path, catalog=None):
    cfg = RuntimeConfig(persistence_backend="local", local_models_root=str(tmp_path),
                        optimization_runs_root=str(tmp_path / "runs"))
    reg = LocalOptimizationRunRegistry(str(tmp_path / "runs"))
    return OptimizationService(catalog or _Catalog(), reg, _Executor(), cfg), reg


def test_run_optimization_creates_queued_run(tmp_path):
    svc, reg = _svc(tmp_path)
    out = svc.run_optimization("m", {"scenario": {"type": "fixed_budget"}})
    assert out["reused"] is False
    assert out["compute_tier_resolved"] == "local"
    assert reg.get_record(out["run_id"]).model_id == "m"
    assert svc._executor.submitted == [out["run_id"]]


def test_identical_config_reuses_completed_run(tmp_path):
    svc, reg = _svc(tmp_path)
    first = svc.run_optimization("m", {"scenario": {"type": "fixed_budget"}})
    from google_meridian_mcp_server.domain.optimization import OptimizationRunState
    reg.write_state(OptimizationRunState(run_id=first["run_id"], status=RunStatus.COMPLETED))
    again = svc.run_optimization("m", {"scenario": {"type": "fixed_budget"}})
    assert again["reused"] is True
    assert again["run_id"] == first["run_id"]


def test_force_rerun_bypasses_reuse(tmp_path):
    svc, reg = _svc(tmp_path)
    first = svc.run_optimization("m", {"scenario": {"type": "fixed_budget"}})
    from google_meridian_mcp_server.domain.optimization import OptimizationRunState
    reg.write_state(OptimizationRunState(run_id=first["run_id"], status=RunStatus.COMPLETED))
    again = svc.run_optimization("m", {"scenario": {"type": "fixed_budget"}}, force_rerun=True)
    assert again["reused"] is False and again["run_id"] != first["run_id"]


def test_unknown_model_raises(tmp_path):
    from google_meridian_mcp_server.domain.errors import ModelNotFoundError
    svc, _ = _svc(tmp_path, catalog=_Catalog404())
    with pytest.raises(ModelNotFoundError):
        svc.run_optimization("nope", {"scenario": {"type": "fixed_budget"}})


def test_invalid_per_channel_config_raises(tmp_path):
    from google_meridian_mcp_server.services.optimization_service import (
        InvalidOptimizationConfigError,
    )
    svc, _ = _svc(tmp_path)
    with pytest.raises(InvalidOptimizationConfigError):
        svc.run_optimization("m", {"scenario": {"type": "fixed_budget"},
            "constraint": {"mode": "per_channel", "bounds": {"tv": {"lower_pct": 0.1, "upper_pct": 0.2}}}})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_optimization_service.py -v`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement the service**

```python
# src/google_meridian_mcp_server/services/optimization_service.py
"""Service orchestrating optimization submission, reuse, and registry reads."""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Any

from google_meridian_mcp_server.domain.errors import MeridianMcpError
from google_meridian_mcp_server.domain.models import RuntimeConfig
from google_meridian_mcp_server.domain.optimization import (
    OptimizationConfig,
    OptimizationRun,
    RunStatus,
    config_fingerprint,
    to_optimize_kwargs,
)
from google_meridian_mcp_server.execution.routing import (
    model_size_features,
    resolve_tier,
    size_score,
)
from google_meridian_mcp_server.persistence.optimization_run_registry import (
    OptimizationRunRegistry,
)

_MERIDIAN_VERSION = "1.7.0"
_SERVER_VERSION = "0.1.0"


class InvalidOptimizationConfigError(MeridianMcpError):
    def __init__(self, reason: str):
        super().__init__(
            error_code="invalid_optimization_config",
            message=f"Invalid optimization config: {reason}",
        )


def _slug(model_id: str) -> str:
    return model_id.replace("/", "-")


def _default_label(model_id: str, config: OptimizationConfig) -> str:
    return f"{_slug(model_id)} {config.scenario.type}"


class OptimizationService:
    def __init__(
        self, catalog: Any, registry: OptimizationRunRegistry, executor: Any,
        cfg: RuntimeConfig,
    ) -> None:
        self._catalog = catalog
        self._registry = registry
        self._executor = executor
        self._cfg = cfg

    def run_optimization(
        self, model_id: str, config_dict: dict, *, label: str | None = None,
        note: str | None = None, compute_tier: str = "auto", force_rerun: bool = False,
    ) -> dict[str, Any]:
        facade = self._catalog.get_facade(model_id)  # raises ModelNotFoundError
        try:
            config = OptimizationConfig.model_validate(config_dict)
        except Exception as exc:  # pydantic ValidationError
            raise InvalidOptimizationConfigError(str(exc)) from exc

        use_kpi = facade.resolve_use_kpi(config)
        try:
            to_optimize_kwargs(config, channel_order=facade.channel_order(), use_kpi=use_kpi)
        except ValueError as exc:
            raise InvalidOptimizationConfigError(str(exc)) from exc

        fingerprint = config_fingerprint(model_id, config)
        if not force_rerun:
            existing_id = self._registry.find_by_fingerprint(fingerprint)
            if existing_id is not None:
                state = self._registry.get_state(existing_id)
                if state.status in (RunStatus.COMPLETED, RunStatus.RUNNING, RunStatus.QUEUED):
                    record = self._registry.get_record(existing_id)
                    return self._submit_envelope(record, reused=True)

        features = model_size_features(facade)
        score = size_score(features)
        resolved = resolve_tier(
            score, requested=compute_tier,
            allowed=self._cfg.optimization_allowed_tiers,
            thresholds=self._cfg.optimization_size_thresholds,
        )
        backend = self._cfg.optimization_backend_local  # local-only this phase

        run_id = f"{_slug(model_id)}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}-{secrets.token_hex(3)}"
        record = OptimizationRun(
            run_id=run_id, label=label or _default_label(model_id, config), note=note,
            model_id=model_id, config=config, config_fingerprint=fingerprint,
            compute_tier_requested=compute_tier, compute_tier_resolved=resolved,
            backend=backend, size_score=score, created_at=datetime.now(timezone.utc).isoformat(),
            meridian_version=_MERIDIAN_VERSION, server_version=_SERVER_VERSION,
        )
        self._registry.create(record)
        self._registry.put_fingerprint(fingerprint, run_id)
        self._executor.submit(record)
        return self._submit_envelope(record, reused=False)

    @staticmethod
    def _submit_envelope(record: OptimizationRun, *, reused: bool) -> dict[str, Any]:
        return {
            "run_id": record.run_id,
            "status": RunStatus.QUEUED.value if not reused else RunStatus.COMPLETED.value,
            "compute_tier_resolved": record.compute_tier_resolved,
            "backend": record.backend,
            "size_score": record.size_score,
            "reused": reused,
        }

    def get_status(self, run_id: str) -> dict[str, Any]:
        self._executor.pump()
        record = self._registry.get_record(run_id)
        state = self._registry.get_state(run_id)
        elapsed = None
        if state.started_at:
            end = state.finished_at or datetime.now(timezone.utc).isoformat()
            elapsed = (datetime.fromisoformat(end) - datetime.fromisoformat(state.started_at)).total_seconds()
        return {
            "run_id": run_id, "status": state.status.value,
            "phase": state.phase.value if state.phase else None,
            "progress_fraction": state.progress_fraction,
            "heartbeat_at": state.heartbeat_at, "started_at": state.started_at,
            "finished_at": state.finished_at, "elapsed_seconds": elapsed,
            "compute_tier": record.compute_tier_resolved, "backend": record.backend,
            "error": state.error,
        }

    def get_result(self, run_id: str) -> dict[str, Any]:
        result = self._registry.get_result(run_id)  # raises ResultNotReadyError
        return {"run_id": run_id, **result}

    def list_runs(self, model_id=None, status=None, limit=None) -> dict[str, Any]:
        status_enum = RunStatus(status) if status else None
        summaries = self._registry.list(model_id=model_id, status=status_enum, limit=limit)
        return {"runs": [s.model_dump(mode="json") for s in summaries], "count": len(summaries)}

    def delete(self, run_id: str) -> dict[str, Any]:
        self._registry.delete(run_id)
        return {"run_id": run_id, "deleted": True}
```

> This task adds one config field used above: `optimization_backend_local`. Add it to `RuntimeConfig` (default `"tensorflow"`) and to `load_config()` (`os.getenv("OPTIMIZATION_BACKEND_LOCAL", "tensorflow")`) as part of this task, with a one-line test in `test_config_and_persistence.py` asserting the default. (Cloud per-tier backends are Phase 2.)

- [ ] **Step 2b: Add the `optimization_backend_local` field**

Add to `RuntimeConfig` after `optimization_heartbeat_stale_seconds`:

```python
    optimization_backend_local: str = "tensorflow"
```

and to `load_config()`:

```python
        optimization_backend_local=os.getenv("OPTIMIZATION_BACKEND_LOCAL", "tensorflow"),
```

- [ ] **Step 3 (run): Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_optimization_service.py tests/unit/test_config_and_persistence.py -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add src/google_meridian_mcp_server/services/optimization_service.py src/google_meridian_mcp_server/domain/models.py src/google_meridian_mcp_server/config.py tests/unit/test_optimization_service.py tests/unit/test_config_and_persistence.py
git commit -m "feat: optimization service (submit, reuse, routing, registry reads)"
```

---

## Task 11: Transport tools + lifespan wiring + discovery

**Files:**
- Modify: `src/google_meridian_mcp_server/transport/tools.py`
- Modify: `src/google_meridian_mcp_server/server.py` (lifespan builds registry + executor + service deps)
- Modify: `src/google_meridian_mcp_server/services/analysis_service.py:255-280` (add `run_optimization` to `available_tool_options`)
- Test: `tests/contract/test_optimization_tools.py`

**Interfaces:**
- Consumes: `OptimizationService`, `OptimizationConfig`, lifespan context keys `model_catalog`, `optimization_registry`, `optimization_executor`, `config`.
- Produces: five MCP tools — `run_optimization`, `get_optimization_status`, `get_optimization_result`, `list_optimizations`, `delete_optimization`.

- [ ] **Step 1: Write the failing test**

```python
# tests/contract/test_optimization_tools.py
import pytest
from fastmcp import Client

from google_meridian_mcp_server.server import create_server


@pytest.mark.asyncio
async def test_optimization_tools_registered():
    mcp = create_server()
    tools = {t.name for t in await mcp.get_tools()}
    assert {
        "run_optimization", "get_optimization_status", "get_optimization_result",
        "list_optimizations", "delete_optimization",
    } <= tools


@pytest.mark.asyncio
async def test_run_optimization_annotations_not_readonly():
    mcp = create_server()
    by_name = {t.name: t for t in await mcp.get_tools()}
    assert by_name["run_optimization"].annotations.readOnlyHint is not True
    assert by_name["get_optimization_status"].annotations.readOnlyHint is True
```

> Note: `mcp.get_tools()` is the FastMCP introspection API used by the existing contract tests; mirror their exact call style in `tests/contract/test_analysis_tools.py` if it differs.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/contract/test_optimization_tools.py -v`
Expected: FAIL (tools not registered).

- [ ] **Step 3: Wire the lifespan**

In `server.py` `_lifespan`, after building `model_catalog` and `result_cache`, add:

```python
    from google_meridian_mcp_server.bootstrap import build_registry
    from google_meridian_mcp_server.execution.subprocess_executor import SubprocessExecutor

    optimization_registry = build_registry(cfg)
    optimization_executor = SubprocessExecutor(
        optimization_registry,
        max_parallel=cfg.optimization_max_parallel,
        heartbeat_stale_seconds=cfg.optimization_heartbeat_stale_seconds,
        backend=cfg.optimization_backend_local,
    )
```

and extend the yielded dict:

```python
    yield {
        "config": cfg,
        "model_catalog": model_catalog,
        "result_cache": result_cache,
        "optimization_registry": optimization_registry,
        "optimization_executor": optimization_executor,
    }
```

- [ ] **Step 4: Register the tools**

In `transport/tools.py`, add an `_optimization_service` helper and the five tools. Add imports `from google_meridian_mcp_server.services.optimization_service import OptimizationService` and `from google_meridian_mcp_server.domain.optimization import OptimizationConfig`.

```python
    def _optimization_service(ctx: Context) -> OptimizationService:
        return OptimizationService(
            catalog=ctx.lifespan_context["model_catalog"],
            registry=ctx.lifespan_context["optimization_registry"],
            executor=ctx.lifespan_context["optimization_executor"],
            cfg=ctx.lifespan_context["config"],
        )

    @mcp.tool
    async def run_optimization(
        model_id: Annotated[str, Field(min_length=1, description="Model identifier from list_models.")],
        config: Annotated[OptimizationConfig, Field(description=(
            "Optimization scenario + constraints. scenario is one of "
            "{type:'fixed_budget', budget?} | {type:'target_roas', target_value} | "
            "{type:'target_mroas', target_value}. constraint is "
            "{mode:'global', pct} or {mode:'per_channel', bounds:{channel:{lower_pct,upper_pct}}}. "
            "Optional start_date/end_date (ISO), selected_geos, use_kpi. "
            "See get_model_overview.available_tool_options.run_optimization for valid channels/geos."))],
        ctx: Context,
        label: Annotated[str | None, Field(description="Human label for this run.")] = None,
        note: Annotated[str | None, Field(description="Free-text intent for this run.")] = None,
        compute_tier: Annotated[str, Field(description="auto | local | cloud_cpu | cloud_gpu.")] = "auto",
        force_rerun: Annotated[bool, Field(description="Recompute even if an identical run exists.")] = False,
    ) -> dict[str, Any]:
        """Start a budget optimization (long-running). Returns a run_id immediately; poll get_optimization_status, then get_optimization_result. Reuses an identical prior run unless force_rerun is set."""
        try:
            return _optimization_service(ctx).run_optimization(
                model_id, config.model_dump(mode="json"), label=label, note=note,
                compute_tier=compute_tier, force_rerun=force_rerun,
            )
        except MeridianMcpError as error:
            return _error_response(error)

    @mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
    async def get_optimization_status(
        run_id: Annotated[str, Field(min_length=1, description="run_id from run_optimization.")],
        ctx: Context,
    ) -> dict[str, Any]:
        """Poll an optimization run: status (queued/running/completed/failed), phase, heartbeat, elapsed time, and error if any."""
        try:
            return _optimization_service(ctx).get_status(run_id)
        except MeridianMcpError as error:
            return _error_response(error)

    @mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
    async def get_optimization_result(
        run_id: Annotated[str, Field(min_length=1, description="run_id from run_optimization.")],
        ctx: Context,
    ) -> dict[str, Any]:
        """Fetch the full structured optimization result. Errors with optimization_not_ready until the run is completed."""
        try:
            return _optimization_service(ctx).get_result(run_id)
        except MeridianMcpError as error:
            return _error_response(error)

    @mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
    async def list_optimizations(
        ctx: Context,
        model_id: Annotated[str | None, Field(description="Filter to one model.")] = None,
        status: Annotated[str | None, Field(description="Filter by status: queued/running/completed/failed/canceled.")] = None,
        limit: Annotated[int | None, Field(ge=1, description="Max runs to return (newest first).")] = None,
    ) -> dict[str, Any]:
        """List past optimization runs with their config summary, status, and headline result. Use to find and reuse prior work."""
        try:
            return _optimization_service(ctx).list_runs(model_id=model_id, status=status, limit=limit)
        except MeridianMcpError as error:
            return _error_response(error)

    @mcp.tool
    async def delete_optimization(
        run_id: Annotated[str, Field(min_length=1, description="run_id to delete.")],
        ctx: Context,
    ) -> dict[str, Any]:
        """Permanently delete one optimization run and its result from the registry."""
        try:
            return _optimization_service(ctx).delete(run_id)
        except MeridianMcpError as error:
            return _error_response(error)
```

- [ ] **Step 5: Add `run_optimization` to discovery**

In `analysis_service.py`, inside `get_model_overview`'s `available_tool_options` dict (after `get_spend_scenario`), add:

```python
                "run_optimization": {
                    "channels": overview.get("media_channels", [])
                    + overview.get("rf_channels", []),
                    "geos": overview.get("geo_names", []),
                    "use_kpi_togglable": overview.get("has_revenue_per_kpi", False)
                    and getattr(self._catalog.get_interrogator(model_id), "_mmm", None)
                    is not None,
                    "scenarios": ["fixed_budget", "target_roas", "target_mroas"],
                },
```

> Keep it simple: `use_kpi_togglable` is true only for dual revenue+KPI models. If the inline `getattr` check reads awkwardly against the existing style, compute `has_revenue` (already in scope as `overview["has_revenue_per_kpi"]`) and a `has_kpi` from `overview["metric_views"]` and set `use_kpi_togglable = "kpi" in metric_views and "revenue" in metric_views`.

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/contract/test_optimization_tools.py tests/unit/test_analysis_service.py tests/unit/test_transport_tools.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/google_meridian_mcp_server/transport/tools.py src/google_meridian_mcp_server/server.py src/google_meridian_mcp_server/services/analysis_service.py tests/contract/test_optimization_tools.py
git commit -m "feat: register optimization tools, wire lifespan, add discovery"
```

---

## Task 12: Live-validation gate (national + geo, subprocess executor)

**Files:**
- Modify: `scripts/validation/runner.py`
- Modify: `scripts/validation/matrix.py`
- Test: the suite itself is the test: `uv run python -m scripts.validation.live_validate`

**Interfaces:**
- Consumes: in-process `Client(mcp)`, `call`, the existing `VARIANTS` (national-* / geo-* fixtures).
- Produces: `assert_live_optimization(client, model_id, *, outcome_mode)` and a per-variant block in `run_matrix` exercising the full `run_optimization → poll → get_result` chain plus reuse and an adversarial check.

- [ ] **Step 1: Add the live-optimization harness to `runner.py`**

```python
async def assert_live_optimization(client, model_id: str, *, overview) -> None:
    channels = overview.get("media_channels") or overview.get("rf_channels")
    config = {"scenario": {"type": "fixed_budget"}, "constraint": {"mode": "global", "pct": 0.2}}
    submit = await call(client, "run_optimization", {"model_id": model_id, "config": config})
    assert "error_code" not in submit, f"submit error: {submit}"
    run_id = submit["run_id"]
    assert submit["compute_tier_resolved"] == "local", f"expected local tier, got {submit}"

    import asyncio
    status = None
    for _ in range(120):  # tiny fixtures finish fast; cap ~60s
        status = await call(client, "get_optimization_status", {"run_id": run_id})
        if status["status"] in ("completed", "failed"):
            break
        await asyncio.sleep(0.5)
    assert status and status["status"] == "completed", f"run did not complete: {status}"

    result = await call(client, "get_optimization_result", {"run_id": run_id})
    for key in ("summary", "channel_tables", "allocation", "spend_delta", "outcome_mode"):
        assert key in result, f"result missing '{key}': {result.keys()}"
    assert {"initial", "optimized"} <= set(result["channel_tables"]), "missing channel tables"

    # Reuse: identical submit returns the same run, flagged reused.
    again = await call(client, "run_optimization", {"model_id": model_id, "config": config})
    assert again["reused"] is True and again["run_id"] == run_id, f"reuse failed: {again}"
```

- [ ] **Step 2: Call it for one national and one geo fixture in `run_matrix`**

In `run_matrix`, after the per-variant spend-scenario block, add (still inside the `for variant in VARIANTS` loop, reusing `overview`):

```python
        if model_id in ("national-revenue", "geo-revenue"):
            label = f"{model_id}/run_optimization[live,local,subprocess]"
            try:
                await assert_live_optimization(client, model_id, overview=overview)
                report.ok(label)
            except AssertionError as exc:
                report.fail(label, str(exc))
```

- [ ] **Step 3: Add an adversarial not-ready check via matrix**

In `matrix.py` `adversarial_cases`, append for every variant a get-result-before-submit case is not meaningful (no run_id yet), so instead add a runner-level adversarial in `run_matrix` after the live block:

```python
        if model_id == "national-revenue":
            label = "GLOBAL/ADV/result-not-ready"
            try:
                payload = await call(client, "get_optimization_result",
                                     {"run_id": "does-not-exist"})
                assert_error(payload, "optimization_run_not_found", label)
                report.ok(label)
            except AssertionError as exc:
                report.fail(label, str(exc))
```

- [ ] **Step 4: Run the full live validation suite**

Run: `uv run python -m scripts.validation.live_validate`
Expected: the matrix prints `national-revenue/run_optimization[live,local,subprocess] PASS` and `geo-revenue/run_optimization[live,local,subprocess] PASS`, the reuse and not-found rows PASS, and the run ends with `LIVE VALIDATION PASSED`.

> Important: `live_validate` forces `RESULT_CACHE_ENABLED=false` and sets `LOCAL_MODELS_ROOT`. The optimization registry defaults to `./optimizations`; set `OPTIMIZATION_RUNS_ROOT` to a temp dir at the top of `live_validate._run()` so the suite does not pollute the repo. Add: `os.environ.setdefault("OPTIMIZATION_RUNS_ROOT", str(DEFAULT_OUT_ROOT / "_runs"))`.

- [ ] **Step 5: Commit**

```bash
git add scripts/validation/runner.py scripts/validation/matrix.py scripts/validation/live_validate.py
git commit -m "test: live optimization gate (national+geo, subprocess, local registry)"
```

---

## Task 13: Full suite, lint, and docs

**Files:**
- Modify: `AGENTS.md`, `.env.example`, `docs/meridian-mcp-showcase-parity.md`

- [ ] **Step 1: Run the full unit/contract/integration suite and lint**

Run: `uv run pytest`
Expected: PASS (all green).
Run: `uv run ruff check src tests scripts` then `uv run ruff format src tests scripts`
Expected: no errors; formatting clean.

- [ ] **Step 2: Update `AGENTS.md`**

Add to **Current Tool Surface**: `run_optimization`, `get_optimization_status`, `get_optimization_result`, `list_optimizations`, `delete_optimization`. Add a **Module Map** entry block for `domain/optimization.py`, `persistence/optimization_run_registry.py`, `execution/` (routing, base_executor, subprocess_executor, worker), `meridian/optimizer_facade.py`, `services/optimization_service.py`, `bootstrap.py`. Add a Configuration block documenting the new env vars (`REGISTRY_BACKEND`, `OPTIMIZATION_RUNS_ROOT`, `OPTIMIZATION_GCS_PREFIX`, `OPTIMIZATION_ALLOWED_TIERS`, `OPTIMIZATION_DEFAULT_TIER`, `OPTIMIZATION_MAX_PARALLEL`, `OPTIMIZATION_SIZE_THRESHOLDS`, `OPTIMIZATION_BACKEND_LOCAL`, `OPTIMIZATION_HEARTBEAT_STALE_SECONDS`). Note the per-executor live gate is part of the validation suite.

- [ ] **Step 3: Update `.env.example`**

Append the new env vars with Phase-1 defaults and one-line comments:

```bash
# Optimization module (Phase 1: local executor only)
REGISTRY_BACKEND=local
OPTIMIZATION_RUNS_ROOT=./optimizations
OPTIMIZATION_ALLOWED_TIERS=local
OPTIMIZATION_DEFAULT_TIER=auto
OPTIMIZATION_MAX_PARALLEL=2
OPTIMIZATION_SIZE_THRESHOLDS=1000000,100000000
OPTIMIZATION_BACKEND_LOCAL=tensorflow
OPTIMIZATION_HEARTBEAT_STALE_SECONDS=60
```

- [ ] **Step 4: Update the parity doc**

In `docs/meridian-mcp-showcase-parity.md`, mark the Budget Optimization page parity as **Phase 1 complete (local executor; structured result = summary + channel tables + allocation + spend-delta)**; note Cloud Run tiers, JAX backend, and `response_curves` are Phase 2.

- [ ] **Step 5: Commit**

```bash
git add AGENTS.md .env.example docs/meridian-mcp-showcase-parity.md
git commit -m "docs: document optimization module (Phase 1)"
```

---

## Self-Review

**Spec coverage (Phase 1 scope, spec §14):**
- Pydantic `RuntimeConfig` + guardrails → Task 1. ✓
- `OptimizationConfig` + records + fingerprint → Task 2; optimize() mapping → Task 3. ✓
- `OptimizationRunRegistry` + `LocalOptimizationRunRegistry` (3-file layout, fingerprint index) → Task 4. ✓
- Routing heuristic (size_score, allowed-tier resolution) → Task 5. ✓
- Optimizer facade / structured result → Task 6 (response_curves explicitly deferred — see Global Constraints scope note). ✓
- `BaseExecutor` + `SubprocessExecutor` (gate, reconcile) → Task 9; shared worker → Task 8; bootstrap → Task 7. ✓
- `OptimizationService` (submit, reuse, routing, registry reads) → Task 10. ✓
- 5 tools + lifespan wiring + discovery → Task 11. ✓
- Reuse/fingerprint → Tasks 2/4/10. ✓
- Full unit + contract coverage → every task; live-validation per-executor gate (national + geo) + reuse + adversarial → Task 12; full suite + lint + docs → Task 13. ✓
- Deployment config knobs (backends, allowed tiers) → Tasks 1/10/13. ✓

**Placeholder scan:** No `TBD`/`TODO`/"add error handling"; every code step shows complete code; every run step gives an exact command + expected output.

**Type consistency:** `OptimizationConfig`, `OptimizationRun`, `OptimizationRunState`, `OptimizationRunSummary`, `RunStatus`, `RunPhase`, `ComputeTier`, `OptimizerFacade`, `OptimizationRunRegistry`, `LocalOptimizationRunRegistry`, `BaseExecutor`, `SubprocessExecutor`, `OptimizationService`, `to_optimize_kwargs`, `config_fingerprint`, `model_size_features`, `size_score`, `resolve_tier`, `build_model_catalog`, `build_registry`, `run_worker` are used consistently across tasks. Lifespan context keys `optimization_registry` / `optimization_executor` match between Task 11's wiring and the service consumer.

**Known deviation:** `response_curves` (spec §6.2) deferred to Phase 2 — documented in Global Constraints and the parity doc.

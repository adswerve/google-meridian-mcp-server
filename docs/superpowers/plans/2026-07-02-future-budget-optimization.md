# Future Budget Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `run_future_optimization` MCP tool that optimizes a *future* budget under user-supplied assumptions, reusing the entire existing optimization backend (registry, worker, polling, tiers, result shaping).

**Architecture:** A shared `BaseOptimizationConfig` splits into historical (`OptimizationConfig`) and future (`FutureOptimizationConfig`) subclasses distinguished by a `kind` discriminator. The `OptimizerFacade` gains one shared execution core (`_run`) fed by two kwargs builders; the future builder carries forward cost/flighting/revenue from a reference window and builds a Meridian `DataTensors` via `create_optimization_tensors`. Pure carry-forward math lives in a new testable module. The worker dispatches by `kind`; everything else (registry, polling, fingerprint reuse, tiers) is unchanged.

**Tech Stack:** Python 3.12, Pydantic v2, FastMCP, Google Meridian (`meridian.analysis.optimizer`), NumPy, pytest, `uv`, `ruff`.

## Global Constraints

- Meridian version pinned at `1.7.0`; server version `0.1.0` (see `services/optimization_service.py`).
- Strict-JSON-safe outputs only — no NaN/Inf; reuse `_sig6` from `optimizer_facade.py`.
- New errors must raise `InvalidOptimizationConfigError` (error_code `invalid_optimization_config`) for a consistent envelope.
- Follow existing patterns: discriminated unions via `Annotated[... , Field(discriminator=...)]`; `from __future__ import annotations` at top of every module.
- Lint/format gate: `uv run ruff check src tests scripts` and `uv run ruff format src tests scripts` must pass.
- Final QA (Task 12) runs **local tier only**: `OPTIMIZATION_ALLOWED_TIERS=local`; never `cloud_cpu`/`cloud_gpu`.
- Backward compatibility: existing persisted historical runs (no `kind` field) must still deserialize — `kind` defaults to `"historical"`.

## File Structure

- `src/google_meridian_mcp_server/domain/optimization.py` — **modify**: add `Reference` union, `FutureBlock`, `BaseOptimizationConfig`, `kind` on both configs, `FutureOptimizationConfig`, `AnyOptimizationConfig` union; refactor `to_optimize_kwargs`; retype `OptimizationRun.config`.
- `src/google_meridian_mcp_server/meridian/future_data.py` — **create**: pure carry-forward/label/normalize helpers (no Meridian imports; NumPy + stdlib only).
- `src/google_meridian_mcp_server/meridian/optimizer_facade.py` — **modify**: extract `_run` core; add `execute`, `run_future`, `_historical_kwargs`, `_future_kwargs`, and thin data-pulling wrappers that call `future_data`.
- `src/google_meridian_mcp_server/execution/worker.py` — **modify**: dispatch via `facade.execute(record.config)`.
- `src/google_meridian_mcp_server/services/optimization_service.py` — **modify**: add `run_future_optimization` + shared `_submit` helper + future validation.
- `src/google_meridian_mcp_server/persistence/optimization_run_registry.py` — **modify**: `build_config_summary` handles future configs.
- `src/google_meridian_mcp_server/transport/tools.py` — **modify**: register `run_future_optimization`; clarify run-id tool docstrings cover both tools.
- `skills/meridian-analyst/**` — **modify/create**: skill update via writer→reviewer loop (Task 13).
- `scripts/validation/matrix.py`, `scripts/validation/runner.py` — **modify**: live-validation coverage (Task 11).
- `scripts/qa/future_optimization_qa.py` — **create**: local final-QA driver (Task 12).
- Tests: `tests/unit/test_future_optimization_config.py`, `tests/unit/test_future_data.py`, `tests/integration/test_optimizer_facade_future.py`, `tests/unit/test_optimization_service.py` (extend), `tests/contract/test_optimization_tools.py` (extend).

---

### Task 1: Domain — future config models + discriminator

**Files:**
- Modify: `src/google_meridian_mcp_server/domain/optimization.py`
- Test: `tests/unit/test_future_optimization_config.py`

**Interfaces:**
- Consumes: existing `Scenario`, `Constraint`, `GlobalConstraint`, `OptimizationRun`.
- Produces:
  - `class BaseOptimizationConfig(BaseModel)` with `scenario: Scenario`, `constraint: Constraint` (default `GlobalConstraint(pct=0.3)`), `selected_geos: list[str] | None`, `use_kpi: bool | None`.
  - `class OptimizationConfig(BaseOptimizationConfig)` adds `kind: Literal["historical"] = "historical"`, `start_date: date | None`, `end_date: date | None`.
  - `Reference = Annotated[TrailingReference | SamePeriodLastYearReference | FullHistoryAverageReference, Field(discriminator="mode")]` with modes `"trailing"`, `"same_period_last_year"`, `"full_history_average"`.
  - `class FutureBlock(BaseModel)` with `start_date: date`, `horizon: int (gt=0)`, `reference: Reference` (default `TrailingReference()`), `cost_multipliers: dict[str, float] | None`, `revenue_per_kpi_multiplier: float = 1.0`, `planned_allocation: dict[str, float] | None`.
  - `class FutureOptimizationConfig(BaseOptimizationConfig)` adds `kind: Literal["future"] = "future"`, `future: FutureBlock`.
  - `AnyOptimizationConfig` — a union with a **callable** `Discriminator` that defaults a missing `kind` to `"historical"` (a plain `Field(discriminator="kind")` raises on legacy JSON — see Step 3).
  - `OptimizationRun.config: AnyOptimizationConfig`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_future_optimization_config.py
import pytest
from pydantic import TypeAdapter, ValidationError

from google_meridian_mcp_server.domain.optimization import (
    AnyOptimizationConfig,
    FutureOptimizationConfig,
    OptimizationConfig,
)


def test_historical_kind_defaults_and_backward_compat():
    # Old persisted config without `kind` still parses as historical.
    cfg = OptimizationConfig.model_validate(
        {"scenario": {"type": "fixed_budget", "budget": 500.0}}
    )
    assert cfg.kind == "historical"


def test_future_config_parses_with_defaults():
    cfg = FutureOptimizationConfig.model_validate(
        {
            "scenario": {"type": "fixed_budget"},
            "future": {"start_date": "2026-10-01", "horizon": 13},
        }
    )
    assert cfg.kind == "future"
    assert cfg.future.horizon == 13
    assert cfg.future.reference.mode == "trailing"
    assert cfg.future.revenue_per_kpi_multiplier == 1.0
    assert cfg.future.cost_multipliers is None


def test_future_config_rejects_nonpositive_horizon():
    with pytest.raises(ValidationError):
        FutureOptimizationConfig.model_validate(
            {
                "scenario": {"type": "fixed_budget"},
                "future": {"start_date": "2026-10-01", "horizon": 0},
            }
        )


def test_reference_same_period_last_year_and_full_history():
    for mode in ("same_period_last_year", "full_history_average"):
        cfg = FutureOptimizationConfig.model_validate(
            {
                "scenario": {"type": "fixed_budget"},
                "future": {
                    "start_date": "2026-10-01",
                    "horizon": 4,
                    "reference": {"mode": mode},
                },
            }
        )
        assert cfg.future.reference.mode == mode


def test_any_config_discriminates_by_kind():
    adapter = TypeAdapter(AnyOptimizationConfig)
    hist = adapter.validate_python(
        {"kind": "historical", "scenario": {"type": "fixed_budget"}}
    )
    fut = adapter.validate_python(
        {
            "kind": "future",
            "scenario": {"type": "fixed_budget"},
            "future": {"start_date": "2026-10-01", "horizon": 5},
        }
    )
    assert isinstance(hist, OptimizationConfig)
    assert isinstance(fut, FutureOptimizationConfig)


def test_any_config_defaults_missing_kind_to_historical():
    # Legacy persisted config JSON has no `kind`; the union must still parse it.
    adapter = TypeAdapter(AnyOptimizationConfig)
    parsed = adapter.validate_python({"scenario": {"type": "fixed_budget"}})
    assert isinstance(parsed, OptimizationConfig)


def test_optimization_run_roundtrips_legacy_json_without_kind():
    # The real regression surface: OptimizationRun.model_validate_json on old records.
    from google_meridian_mcp_server.domain.optimization import OptimizationRun

    legacy = {
        "run_id": "r1", "label": "l", "model_id": "m",
        "config": {"scenario": {"type": "fixed_budget"}},
        "config_fingerprint": "f", "compute_tier_requested": "auto",
        "compute_tier_resolved": "local", "backend": "tensorflow", "size_score": 1,
        "created_at": "2026-01-01T00:00:00+00:00",
        "meridian_version": "1.7.0", "server_version": "0.1.0",
    }
    run = OptimizationRun.model_validate_json(json.dumps(legacy))
    assert run.config.kind == "historical"


def test_future_block_rejects_nonpositive_multiplier():
    with pytest.raises(ValidationError):
        FutureOptimizationConfig.model_validate(
            {"scenario": {"type": "fixed_budget"},
             "future": {"start_date": "2026-10-01", "horizon": 4,
                        "cost_multipliers": {"tv": 0.0}}}
        )
```

Add `import json` to the test module imports.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_future_optimization_config.py -v`
Expected: FAIL with `ImportError` (`AnyOptimizationConfig`/`FutureOptimizationConfig` not defined).

- [ ] **Step 3: Write minimal implementation**

In `domain/optimization.py`, after the existing `Constraint` union and before `OptimizationConfig`, add the reference union and future block, then restructure the configs:

```python
class TrailingReference(BaseModel):
    mode: Literal["trailing"] = "trailing"


class SamePeriodLastYearReference(BaseModel):
    mode: Literal["same_period_last_year"]


class FullHistoryAverageReference(BaseModel):
    mode: Literal["full_history_average"]


Reference = Annotated[
    TrailingReference | SamePeriodLastYearReference | FullHistoryAverageReference,
    Field(discriminator="mode"),
]


class BaseOptimizationConfig(BaseModel):
    scenario: Scenario
    constraint: Constraint = Field(default_factory=lambda: GlobalConstraint(pct=0.3))
    selected_geos: list[str] | None = Field(
        default=None,
        description="Subset of geo identifiers to optimize over (e.g. "
        "['US-CA', 'US-NY']). Omit for all geos; ignored by national models. "
        "Valid values: "
        "get_model_overview.available_tool_options.run_optimization.geos.",
    )
    use_kpi: bool | None = Field(
        default=None,
        description="Objective family: false = revenue-based (ROAS/ROI), "
        "true = KPI-based (CPIK). Omit/null to use the model's native objective "
        "(revenue models -> ROAS, no-revenue models -> CPIK).",
    )


class OptimizationConfig(BaseOptimizationConfig):
    kind: Literal["historical"] = "historical"
    start_date: date | None = Field(
        default=None,
        description="Inclusive start date (ISO-8601, e.g. '2023-01-01') of the "
        "window to optimize over. Omit to use the model's full date range.",
    )
    end_date: date | None = Field(
        default=None,
        description="Inclusive end date (ISO-8601, e.g. '2023-12-31') of the "
        "window to optimize over. Omit to use the model's full date range.",
    )


class FutureBlock(BaseModel):
    start_date: date = Field(
        description="First future period (inclusive, ISO-8601). Must be after the "
        "model's last training period.",
        examples=["2026-10-01"],
    )
    horizon: int = Field(
        gt=0,
        description="Number of future periods to plan, at the model's own cadence "
        "(e.g. 13 = 13 weeks for a weekly model).",
        examples=[13],
    )
    reference: Reference = Field(
        default_factory=TrailingReference,
        description="Which historical window seeds carried-forward cost, flighting, "
        "revenue-per-KPI, and default budget. trailing = last `horizon` periods; "
        "same_period_last_year = the `horizon` periods one year before start_date; "
        "full_history_average = average over all training periods.",
    )
    cost_multipliers: dict[str, float] | None = Field(
        default=None,
        description="Optional per-channel multipliers on carried-forward cost per "
        "media unit (1.2 = 20% more expensive; below 1.0 = cheaper). Channels omitted "
        "default to 1.0. Keys must be valid paid/RF channels. Example: {'TV': 1.15}.",
        examples=[{"TV": 1.15}],
    )
    revenue_per_kpi_multiplier: float = Field(
        default=1.0,
        gt=0,
        description="Scales carried-forward revenue-per-KPI (revenue models only; "
        "ignored when the model has no revenue-per-KPI). 1.1 = 10% higher value.",
    )
    planned_allocation: dict[str, float] | None = Field(
        default=None,
        description="Optional planned spend mix (the center that spend constraints "
        "bound around, and the 'current' baseline in the result). Partial/unnormalized "
        "dicts are accepted: missing channels are filled from the carried-forward mix "
        "and the whole vector is renormalized to sum to 1. Example: {'TV': 0.4, 'Search': 0.35}.",
        examples=[{"TV": 0.4, "Search": 0.35, "Social": 0.25}],
    )


class FutureOptimizationConfig(BaseOptimizationConfig):
    kind: Literal["future"] = "future"
    future: FutureBlock
```

Add positivity validation for the multiplier dicts (a `0`/negative multiplier makes `cpmu → 0`
and `media = spend / cpmu` blow up in Meridian). On `FutureBlock`:

```python
from pydantic import field_validator

    @field_validator("cost_multipliers", "planned_allocation")
    @classmethod
    def _positive_weights(cls, v):
        if v and any(w <= 0 for w in v.values()):
            raise ValueError("weights must be > 0")
        return v
```

Define the union with a **callable discriminator** — a plain `Field(discriminator="kind")` does
NOT fall back to the default when `kind` is absent (verified: pydantic 2.13 raises
`union_tag_not_found`), which would break `model_validate_json` for every pre-upgrade
`record.json`:

```python
from pydantic import Discriminator, Tag


def _config_kind(v) -> str:
    if isinstance(v, dict):
        return v.get("kind", "historical")
    return getattr(v, "kind", "historical")


AnyOptimizationConfig = Annotated[
    Annotated[OptimizationConfig, Tag("historical")]
    | Annotated[FutureOptimizationConfig, Tag("future")],
    Discriminator(_config_kind),
]
```

Then change `OptimizationRun.config` type from `OptimizationConfig` to `AnyOptimizationConfig`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_future_optimization_config.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Run the existing optimization suite to confirm no regressions**

Run: `uv run pytest tests/unit/test_optimization_config.py tests/unit/test_optimization_mapping.py tests/unit/test_optimization_run_registry.py -v`
Expected: PASS (existing tests still green; `OptimizationConfig` still validates as before).

- [ ] **Step 6: Commit**

```bash
git add src/google_meridian_mcp_server/domain/optimization.py tests/unit/test_future_optimization_config.py
git commit -m "feat: future/historical optimization config split with kind discriminator"
```

---

### Task 2: Domain — `to_optimize_kwargs` works for the shared base

**Files:**
- Modify: `src/google_meridian_mcp_server/domain/optimization.py:179-221`
- Test: `tests/unit/test_optimization_mapping.py`

**Interfaces:**
- Produces: `to_optimize_kwargs(config: BaseOptimizationConfig, *, channel_order, use_kpi) -> dict` — emits `start_date`/`end_date` only when present on the config (historical), so it is safe to call for both config kinds.
- Consumes: `config.scenario`, `config.constraint`, `config.selected_geos`, `config.use_kpi`, and `getattr(config, "start_date"/"end_date", None)`.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/unit/test_optimization_mapping.py
from google_meridian_mcp_server.domain.optimization import FutureOptimizationConfig


def test_to_optimize_kwargs_on_future_config_omits_historical_dates():
    cfg = FutureOptimizationConfig.model_validate(
        {
            "scenario": {"type": "fixed_budget", "budget": 1000.0},
            "constraint": {"mode": "global", "pct": 0.25},
            "future": {"start_date": "2026-10-01", "horizon": 13},
        }
    )
    kw = to_optimize_kwargs(cfg, channel_order=CHANNELS, use_kpi=False)
    # base translation still works…
    assert kw["fixed_budget"] is True and kw["budget"] == 1000.0
    assert kw["spend_constraint_lower"] == 0.25
    # …and no historical window is emitted for a future config.
    assert kw["start_date"] is None and kw["end_date"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_optimization_mapping.py::test_to_optimize_kwargs_on_future_config_omits_historical_dates -v`
Expected: FAIL with `AttributeError: 'FutureOptimizationConfig' object has no attribute 'start_date'`.

- [ ] **Step 3: Write minimal implementation**

In `to_optimize_kwargs`, change the type hint to `BaseOptimizationConfig` and replace the two `config.start_date`/`config.end_date` accesses with `getattr`:

```python
def to_optimize_kwargs(
    config: BaseOptimizationConfig, *, channel_order: list[str], use_kpi: bool
) -> dict[str, Any]:
    ...
    start_date = getattr(config, "start_date", None)
    end_date = getattr(config, "end_date", None)
    return {
        "fixed_budget": fixed_budget,
        "budget": budget,
        "target_roi": target_roi,
        "target_mroi": target_mroi,
        "spend_constraint_lower": spend_lower,
        "spend_constraint_upper": spend_upper,
        "selected_geos": config.selected_geos,
        "start_date": start_date.isoformat() if start_date else None,
        "end_date": end_date.isoformat() if end_date else None,
        "use_kpi": use_kpi,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_optimization_mapping.py -v`
Expected: PASS (all existing + the new one).

- [ ] **Step 5: Commit**

```bash
git add src/google_meridian_mcp_server/domain/optimization.py tests/unit/test_optimization_mapping.py
git commit -m "refactor: to_optimize_kwargs translates BaseOptimizationConfig core for both kinds"
```

---

### Task 3: Pure carry-forward helpers (`meridian/future_data.py`)

**Files:**
- Create: `src/google_meridian_mcp_server/meridian/future_data.py`
- Test: `tests/unit/test_future_data.py`

**Interfaces:**
- Produces (all pure — NumPy/stdlib only, no Meridian import):
  - `infer_cadence_days(times: list[str]) -> int` — median consecutive delta (days) of ISO time labels.
  - `future_time_labels(start_date: date, horizon: int, cadence_days: int) -> list[str]` — `horizon` ISO labels from `start_date` stepping `cadence_days`.
  - `reference_indices(mode: str, horizon: int, start_date: date, times: list[str], cadence_days: int) -> list[int]` — indices into `times` for the seed window; raises `ValueError` when infeasible. `full_history_average` returns all indices. `same_period_last_year` steps back a **cadence-aware year** (`round(365/cadence_days)` periods), not a hardcoded 365 days.
  - `normalize_planned_allocation(planned: dict[str, float] | None, carried: dict[str, float], channel_order: list[str]) -> list[float] | None` — friendly-normalize to a full vector summing to 1 in `channel_order`; `None` passes through as `None`. Unknown channels raise `ValueError`.
  - `validate_channel_keys(mapping: dict[str, float] | None, valid_channels: list[str]) -> None` — raises `ValueError` if any key is not in `valid_channels`. Called **once** against the union of media + RF channels.
  - `apply_cost_multipliers(cpmu: "np.ndarray", multipliers: dict[str, float] | None, channel_order: list[str]) -> "np.ndarray"` — elementwise scale for channels present in `channel_order`; **ignores** keys outside it (so it can be called per-subset with the full dict). Does NOT validate — use `validate_channel_keys` for that.
  - `resolve_budget(scenario_budget: float | None, fixed_budget: bool, seeded_flighting_total: float) -> float | None` — explicit budget wins; else the seeded future flighting total for fixed-budget; `None` for flexible.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_future_data.py
from datetime import date, timedelta

import numpy as np
import pytest

from google_meridian_mcp_server.meridian import future_data as fd

WEEKLY = [f"2025-{m:02d}-{d:02d}" for (m, d) in
          [(1, 6), (1, 13), (1, 20), (1, 27), (2, 3), (2, 10)]]  # 7-day cadence


def test_infer_cadence_days_weekly():
    assert fd.infer_cadence_days(WEEKLY) == 7


def test_future_time_labels_steps_cadence():
    labels = fd.future_time_labels(date(2026, 10, 1), 3, 7)
    assert labels == ["2026-10-01", "2026-10-08", "2026-10-15"]


def test_reference_indices_trailing_takes_last_horizon():
    times = [f"2025-01-{d:02d}" for d in range(1, 11)]  # 10 daily points
    assert fd.reference_indices("trailing", 3, date(2025, 2, 1), times, 1) == [7, 8, 9]


def test_reference_indices_full_history_returns_all():
    times = [f"2025-01-{d:02d}" for d in range(1, 6)]
    assert fd.reference_indices("full_history_average", 2, date(2025, 2, 1), times, 1) == [0, 1, 2, 3, 4]


def test_reference_indices_same_period_last_year_cadence_aware():
    # weekly, exactly 52 periods ending just before start; cadence-aware year = 52 wk back
    times = [np.datetime_as_string(np.datetime64("2025-10-06") + np.timedelta64(7 * i, "D"), unit="D")
             for i in range(52)]
    start = date.fromisoformat(times[-1]) + timedelta(days=7)
    idx = fd.reference_indices("same_period_last_year", 3, start, times, 7)
    assert len(idx) == 3
    assert idx[0] == 0  # 52 weeks back from the next period == first label


def test_reference_indices_same_period_last_year_insufficient_history():
    times = [f"2026-09-{d:02d}" for d in range(1, 8)]  # < 1 year before start
    with pytest.raises(ValueError):
        fd.reference_indices("same_period_last_year", 3, date(2026, 10, 1), times, 1)


def test_normalize_planned_allocation_fills_and_renormalizes():
    carried = {"tv": 0.5, "search": 0.3, "social": 0.2}
    out = fd.normalize_planned_allocation({"tv": 0.4}, carried, ["tv", "search", "social"])
    assert pytest.approx(sum(out)) == 1.0
    assert out[0] == pytest.approx(0.4 / (0.4 + 0.3 + 0.2))


def test_normalize_planned_allocation_none_passthrough():
    assert fd.normalize_planned_allocation(None, {"tv": 1.0}, ["tv"]) is None


def test_normalize_planned_allocation_unknown_channel_raises():
    with pytest.raises(ValueError):
        fd.normalize_planned_allocation({"nope": 1.0}, {"tv": 1.0}, ["tv"])


def test_apply_cost_multipliers_scales_named_channel_ignores_others():
    # Called per-subset with the full dict: RF-keyed entries are ignored for the media list.
    cpmu = np.array([2.0, 4.0])
    out = fd.apply_cost_multipliers(cpmu, {"search": 1.5, "yt_rf": 2.0}, ["tv", "search"])
    assert out.tolist() == [2.0, 6.0]


def test_validate_channel_keys_raises_on_unknown():
    with pytest.raises(ValueError):
        fd.validate_channel_keys({"nope": 2.0}, ["tv", "search"])


def test_validate_channel_keys_accepts_union_membership():
    fd.validate_channel_keys({"tv": 1.2, "yt_rf": 0.9}, ["tv", "search", "yt_rf"])  # no raise


def test_resolve_budget_prefers_explicit_then_seeded_then_none():
    assert fd.resolve_budget(1000.0, True, 800.0) == 1000.0
    assert fd.resolve_budget(None, True, 800.0) == 800.0
    assert fd.resolve_budget(None, False, 800.0) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_future_data.py -v`
Expected: FAIL with `ModuleNotFoundError` (no `future_data`).

- [ ] **Step 3: Write minimal implementation**

```python
# src/google_meridian_mcp_server/meridian/future_data.py
"""Pure carry-forward helpers for future budget optimization (no Meridian imports)."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np


def infer_cadence_days(times: list[str]) -> int:
    if len(times) < 2:
        raise ValueError("Need at least two time points to infer cadence.")
    days = np.diff(np.array(times, dtype="datetime64[D]")).astype(int)
    return int(np.median(days))


def future_time_labels(start_date: date, horizon: int, cadence_days: int) -> list[str]:
    return [
        (start_date + timedelta(days=cadence_days * i)).isoformat()
        for i in range(horizon)
    ]


def reference_indices(
    mode: str, horizon: int, start_date: date, times: list[str], cadence_days: int
) -> list[int]:
    n = len(times)
    if mode == "full_history_average":
        return list(range(n))
    if mode == "trailing":
        if horizon > n:
            raise ValueError(
                f"horizon {horizon} exceeds available history ({n} periods)."
            )
        return list(range(n - horizon, n))
    if mode == "same_period_last_year":
        # Cadence-aware year: step back a whole number of periods (~365 days), so a
        # next-period start_date lands exactly on the label one year prior.
        periods_per_year = max(round(365 / cadence_days), 1)
        target = np.datetime64(str(start_date)) - np.timedelta64(
            periods_per_year * cadence_days, "D"
        )
        arr = np.array(times, dtype="datetime64[D]")
        if arr[0] > target:
            raise ValueError(
                "same_period_last_year requires history starting at least one year "
                "before start_date."
            )
        anchor = int(np.searchsorted(arr, target, side="right")) - 1
        anchor = max(anchor, 0)
        if anchor + horizon > n:
            raise ValueError(
                "Not enough history one year before start_date to cover the horizon."
            )
        return list(range(anchor, anchor + horizon))
    raise ValueError(f"Unknown reference mode: {mode}")


def validate_channel_keys(
    mapping: dict[str, float] | None, valid_channels: list[str]
) -> None:
    if not mapping:
        return
    unknown = [ch for ch in mapping if ch not in valid_channels]
    if unknown:
        raise ValueError(f"unknown channels: {unknown}")


def normalize_planned_allocation(
    planned: dict[str, float] | None,
    carried: dict[str, float],
    channel_order: list[str],
) -> list[float] | None:
    if planned is None:
        return None
    unknown = [ch for ch in planned if ch not in channel_order]
    if unknown:
        raise ValueError(f"planned_allocation has unknown channels: {unknown}")
    merged = {ch: planned.get(ch, carried.get(ch, 0.0)) for ch in channel_order}
    total = sum(merged.values())
    if total <= 0:
        raise ValueError("planned_allocation weights sum to zero.")
    return [merged[ch] / total for ch in channel_order]


def apply_cost_multipliers(
    cpmu: np.ndarray, multipliers: dict[str, float] | None, channel_order: list[str]
) -> np.ndarray:
    # Applies only channels present in channel_order; ignores keys outside it so the
    # SAME full multipliers dict can be applied to the media subset and the RF subset.
    out = np.array(cpmu, dtype=float)
    if not multipliers:
        return out
    for i, ch in enumerate(channel_order):
        out[i] *= float(multipliers.get(ch, 1.0))
    return out


def resolve_budget(
    scenario_budget: float | None, fixed_budget: bool, seeded_flighting_total: float
) -> float | None:
    if not fixed_budget:
        return None
    if scenario_budget is not None:
        return scenario_budget
    return seeded_flighting_total
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_future_data.py -v`
Expected: PASS (all cases).

- [ ] **Step 5: Commit**

```bash
git add src/google_meridian_mcp_server/meridian/future_data.py tests/unit/test_future_data.py
git commit -m "feat: pure carry-forward helpers for future optimization"
```

---

### Task 4: Facade — shared `_run` core + `execute` dispatch (behavior-preserving)

**Files:**
- Modify: `src/google_meridian_mcp_server/meridian/optimizer_facade.py:41-59`
- Test: `tests/unit/test_optimizer_facade.py`

**Interfaces:**
- Consumes: `resolve_use_kpi`, `channel_order`, `build_result`, `to_optimize_kwargs`.
- Produces:
  - `_run(self, config, build_kwargs) -> dict` — creates `BudgetOptimizer`, calls `build_kwargs(config, opt, use_kpi)`, runs `optimize`, best-effort response curves, `build_result`.
  - `run(self, config: OptimizationConfig) -> dict` — delegates to `_run` with `_historical_kwargs` (unchanged behavior).
  - `_historical_kwargs(self, config, opt, use_kpi) -> dict`.
  - `execute(self, config) -> dict` — dispatches `run` vs `run_future` by `config.kind`.
- `resolve_use_kpi` signature broadened to accept `BaseOptimizationConfig` (it only reads `config.use_kpi` and `has_revenue_per_kpi()`).

- [ ] **Step 1: Write the failing test**

```python
# add to tests/unit/test_optimizer_facade.py
from unittest.mock import MagicMock

from google_meridian_mcp_server.domain.optimization import (
    FutureOptimizationConfig,
    OptimizationConfig,
)
from google_meridian_mcp_server.meridian.optimizer_facade import OptimizerFacade


def test_execute_dispatches_by_kind(monkeypatch):
    facade = OptimizerFacade.__new__(OptimizerFacade)  # no real model needed
    facade.run = MagicMock(return_value={"ran": "historical"})
    facade.run_future = MagicMock(return_value={"ran": "future"})

    hist = OptimizationConfig.model_validate({"scenario": {"type": "fixed_budget"}})
    fut = FutureOptimizationConfig.model_validate(
        {"scenario": {"type": "fixed_budget"},
         "future": {"start_date": "2026-10-01", "horizon": 4}}
    )
    assert facade.execute(hist) == {"ran": "historical"}
    assert facade.execute(fut) == {"ran": "future"}
    facade.run.assert_called_once_with(hist)
    facade.run_future.assert_called_once_with(fut)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_optimizer_facade.py::test_execute_dispatches_by_kind -v`
Expected: FAIL with `AttributeError: 'OptimizerFacade' object has no attribute 'execute'`.

- [ ] **Step 3: Write minimal implementation**

Replace the current `run` method body and add the core + dispatch. Also add a module-level `_best_effort`:

```python
def _best_effort(fn):
    try:
        return fn()
    except Exception:  # noqa: BLE001 - response curves are best-effort enrichment
        return None
```

```python
    def execute(self, config) -> dict[str, Any]:
        if getattr(config, "kind", "historical") == "future":
            return self.run_future(config)
        return self.run(config)

    def _run(self, config, build_kwargs) -> dict[str, Any]:
        from meridian.analysis import optimizer as optimizer_mod

        use_kpi = self.resolve_use_kpi(config)
        opt = optimizer_mod.BudgetOptimizer(self._mmm)
        kwargs = build_kwargs(config, opt, use_kpi)
        results = opt.optimize(**kwargs)
        curves = _best_effort(lambda: results.get_response_curves())
        return self.build_result(
            results.nonoptimized_data,
            results.optimized_data,
            use_kpi=use_kpi,
            response_curves=curves,
        )

    def run(self, config: OptimizationConfig) -> dict[str, Any]:
        return self._run(config, self._historical_kwargs)

    def _historical_kwargs(self, config, opt, use_kpi) -> dict[str, Any]:
        return to_optimize_kwargs(
            config, channel_order=self.channel_order(), use_kpi=use_kpi
        )
```

Broaden `resolve_use_kpi` type hint to `BaseOptimizationConfig` (import it).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_optimizer_facade.py -v`
Expected: PASS (new dispatch test + existing facade tests unchanged).

- [ ] **Step 5: Commit**

```bash
git add src/google_meridian_mcp_server/meridian/optimizer_facade.py tests/unit/test_optimizer_facade.py
git commit -m "refactor: OptimizerFacade shared _run core + execute() kind dispatch"
```

---

### Task 5: Facade — `run_future` + `_future_kwargs` wiring (integration)

**Files:**
- Modify: `src/google_meridian_mcp_server/meridian/optimizer_facade.py`
- Test: `tests/integration/test_optimizer_facade_future.py`

**Interfaces:**
- Consumes: `future_data` helpers (Task 3); `self._mmm.input_data` tensors (`media`, `media_spend`, `reach`, `frequency`, `rf_spend`, `revenue_per_kpi`); `self.get_data_inputs()`, `self.channel_order()`, `self.get_time_values()`, `self.has_revenue_per_kpi()`.
- Produces: `run_future(self, config: FutureOptimizationConfig) -> dict` and `_future_kwargs(self, config, opt, use_kpi) -> dict`; private data-pulling helpers `_seed_cpmu(window)`, `_seed_cprf(window)`, `_seed_spend_flighting(kind, window, horizon, average)`, `_seed_revenue_per_kpi(window, horizon, average)`, `_carried_allocation(window)`.

- [ ] **Step 0: Register the `integration` marker**

In `pyproject.toml` under `[tool.pytest.ini_options]`, add:

```toml
markers = ["integration: exercises a real fitted Meridian model (slow; builds fixtures)"]
```

**Note on tensors (verified against `optimizer.py:1776-1911`, validation `2807-2919`):**
`create_optimization_tensors` accepts per-channel scalar `cpmu`/`cprf` `(n_channels,)` plus a spend
flighting `(n_geos, T, n_channels)`; it derives `media = media_spend / cpmu`. Only the *relative*
flighting matters. Three correctness details Fable-5 review surfaced:

1. **Media-time axis.** `input_data.media`/`reach`/`frequency` are indexed by `media_time`, whose
   length is `n_media_times ≥ n_times` — only the **final `n_times`** periods align to
   `input_data.time`. Trim media-family tensors to their last `n_times` slice **before** applying
   window indices, or the window selects periods shifted by the lag padding.
2. **Geo dim is always present** (national models carry a geo axis of size 1). Treat every tensor
   as `(n_geos, T, n_channels)` uniformly — a 3-D tensor passes validation as-is.
3. **`cost_multipliers` cover media+RF together.** Validate keys **once** against the union
   (`media_order + rf_order`) via `validate_channel_keys`, then call `apply_cost_multipliers` on
   each subset with the *same full dict* (it ignores out-of-subset keys). Never validate a
   media-keyed multiplier against the RF-only list.

This task is verified against the real dummy fixture, which is why it is integration-level.

- [ ] **Step 1: Write the failing test (integration against a built fixture)**

```python
# tests/integration/test_optimizer_facade_future.py
"""Integration: run_future against a real tiny fitted model (built-if-missing)."""
import pytest

from google_meridian_mcp_server.domain.optimization import FutureOptimizationConfig

pytestmark = pytest.mark.integration  # register in pyproject (see Step 0)


@pytest.fixture(scope="module")
def national_revenue_facade():
    # Reuse the validation fixtures + loader; build-if-missing.
    from scripts.validation.fixtures import ensure_fixture_model  # helper (Task 11)
    from google_meridian_mcp_server.meridian.optimizer_facade import OptimizerFacade

    mmm = ensure_fixture_model("national-revenue")
    return OptimizerFacade(mmm)


def _future_cfg(facade, **future_over):
    times = facade.get_time_values()
    from datetime import date, timedelta
    last = date.fromisoformat(times[-1][:10])
    start = (last + timedelta(days=7)).isoformat()
    future = {"start_date": start, "horizon": 4, **future_over}
    return FutureOptimizationConfig.model_validate(
        {"scenario": {"type": "fixed_budget"}, "future": future}
    )


def test_run_future_trailing_default_wellformed(national_revenue_facade):
    facade = national_revenue_facade
    result = facade.run_future(_future_cfg(facade))
    assert result["outcome_mode"] in ("revenue", "kpi")
    assert {"summary", "channel_tables", "allocation", "spend_delta"} <= result.keys()
    assert result["channel_tables"]["optimized"]  # non-empty rows


def test_run_future_cost_multiplier_shifts_away(national_revenue_facade):
    facade = national_revenue_facade
    channel = facade.channel_order()[0]
    base = facade.run_future(_future_cfg(facade))
    bumped = facade.run_future(
        _future_cfg(facade, cost_multipliers={channel: 3.0})
    )

    def opt_spend(res, ch):
        rows = {r["channel"]: r["spend"] for r in res["channel_tables"]["optimized"]}
        return rows[ch]

    # Tripling one channel's cost should not increase its optimized spend.
    assert opt_spend(bumped, channel) <= opt_spend(base, channel) + 1e-6
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_optimizer_facade_future.py -v`
Expected: FAIL — `AttributeError: 'OptimizerFacade' object has no attribute 'run_future'` (or `ImportError` for the `ensure_fixture_model` helper if Task 11 not yet done; if so, run this test after Task 11 — note ordering below).

> **Ordering note:** `ensure_fixture_model` is introduced in Task 11 Step 3. If executing strictly in order, implement that small helper first (it is a thin wrapper over the existing generator + loader) or temporarily load the fixture inline. Keep the helper extraction in Task 11.

- [ ] **Step 3: Write minimal implementation**

Add to `OptimizerFacade`:

```python
    def run_future(self, config) -> dict[str, Any]:
        return self._run(config, self._future_kwargs)

    def _future_kwargs(self, config, opt, use_kpi) -> dict[str, Any]:
        from google_meridian_mcp_server.meridian import future_data as fd

        f = config.future
        times = self.get_time_values()
        cadence = fd.infer_cadence_days(times)
        time_labels = fd.future_time_labels(f.start_date, f.horizon, cadence)
        if time_labels[0] <= times[-1][:10]:
            raise ValueError("future start_date must be after the last training period.")
        window = fd.reference_indices(
            f.reference.mode, f.horizon, f.start_date, times, cadence
        )

        media_order = self.get_data_inputs()["media"]
        rf_order = self.get_data_inputs()["rf_media"]
        fd.validate_channel_keys(f.cost_multipliers, media_order + rf_order)
        average = f.reference.mode == "full_history_average"

        seeded_total = 0.0
        tensor_kwargs: dict[str, Any] = {"time": time_labels}
        if media_order:
            tensor_kwargs["cpmu"] = fd.apply_cost_multipliers(
                self._seed_cpmu(window), f.cost_multipliers, media_order
            )
            media_spend = self._seed_spend_flighting("media_spend", window, f.horizon, average)
            tensor_kwargs["media_spend"] = media_spend
            seeded_total += float(np.asarray(media_spend).sum())
        if rf_order:
            tensor_kwargs["cprf"] = fd.apply_cost_multipliers(
                self._seed_cprf(window), f.cost_multipliers, rf_order
            )
            rf_spend = self._seed_spend_flighting("rf_spend", window, f.horizon, average)
            tensor_kwargs["rf_spend"] = rf_spend
            seeded_total += float(np.asarray(rf_spend).sum())
        if self.has_revenue_per_kpi():
            tensor_kwargs["revenue_per_kpi"] = (
                self._seed_revenue_per_kpi(window, f.horizon, average)
                * f.revenue_per_kpi_multiplier
            )

        new_data = opt.create_optimization_tensors(**tensor_kwargs)

        carried = self._carried_allocation(window)  # {channel: weight}
        pct = fd.normalize_planned_allocation(
            f.planned_allocation, carried, self.channel_order()
        )
        fixed_budget = config.scenario.type == "fixed_budget"
        scenario_budget = getattr(config.scenario, "budget", None)
        # Budget defaults to the SEEDED future flighting total (horizon periods), NOT the raw
        # reference-window total — the two differ for full_history_average.
        budget = fd.resolve_budget(scenario_budget, fixed_budget, seeded_total)

        kwargs = to_optimize_kwargs(
            config, channel_order=self.channel_order(), use_kpi=use_kpi
        )
        kwargs.pop("start_date", None)
        kwargs.pop("end_date", None)
        kwargs.update(
            new_data=new_data,
            start_date=time_labels[0],
            end_date=time_labels[-1],
            pct_of_spend=pct,
            budget=budget,
        )
        return kwargs
```

Add the private data-pulling helpers that read `self._mmm.input_data` as NumPy (`import numpy as np`
at the top of the facade module). Treat every tensor as `(n_geos, T, n_channels)` — national models
carry `n_geos == 1`. **Trim media-family tensors** (`media`, `reach`, `frequency`) to their final
`n_times` slice (`arr[:, -n_times:, :]`) before applying `window` indices, since they are indexed by
the longer `media_time` axis; spend / `revenue_per_kpi` tensors already align to `time`.
- `_seed_cpmu(window)`: per channel, `window_media_spend.sum(over geo,time) / trimmed_window_media.sum(over geo,time)` → `(n_media_channels,)`.
- `_seed_cprf(window)`: per RF channel, `window_rf_spend.sum / window_rf_impressions.sum` with impressions = `reach × frequency` (trimmed) → `(n_rf_channels,)`.
- `_seed_spend_flighting(kind, window, horizon, average)`: for `trailing`/`same_period` slice the spend tensor at `window` on the time axis (length == `horizon`); for `average` compute the per-period mean over all periods and tile it to `horizon`. Returns `(n_geos, horizon, n_channels)`.
- `_seed_revenue_per_kpi(window, horizon, average)`: same windowing on the `(n_geos, time)` revenue-per-KPI tensor.
- `_carried_allocation(window)`: per-channel share of total paid+RF spend over the window → `{channel: weight}` for `normalize_planned_allocation`.

If a model's `media_spend` is a 1-D aggregate lacking a time axis (legal in `InputData`), raise a
typed `ValueError("unsupported spend granularity for future optimization")` rather than crashing.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/integration/test_optimizer_facade_future.py -v`
Expected: PASS (well-formed result; cost-multiplier monotonicity holds).

- [ ] **Step 5: Commit**

```bash
git add src/google_meridian_mcp_server/meridian/optimizer_facade.py tests/integration/test_optimizer_facade_future.py
git commit -m "feat: run_future builds DataTensors from carried-forward reference window"
```

---

### Task 6: Worker — dispatch by kind

**Files:**
- Modify: `src/google_meridian_mcp_server/execution/worker.py:92`
- Test: `tests/unit/test_optimization_worker.py`

**Interfaces:**
- Consumes: `facade.execute(record.config)` (Task 4).
- Produces: worker now runs both historical and future records unchanged otherwise.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/unit/test_optimization_worker.py — assert the worker calls execute(), not run()
def test_worker_uses_execute_for_dispatch(monkeypatch, tmp_path):
    # Build a fake catalog whose facade records which method was called.
    calls = {}

    class FakeFacade:
        def execute(self, config):
            calls["execute"] = config
            return {"summary": {}, "outcome_mode": "revenue"}

        def run(self, config):  # must NOT be called directly by the worker
            calls["run"] = config
            return {}

    class FakeCatalog:
        def get_optimizer_facade(self, model_id):
            return FakeFacade()

    # ... reuse the existing worker-test harness to create a QUEUED record and registry,
    # then call run_worker(...) with FakeCatalog and assert "execute" in calls and "run" not in calls.
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_optimization_worker.py::test_worker_uses_execute_for_dispatch -v`
Expected: FAIL (worker currently calls `facade.run`, so `"run"` is recorded and `"execute"` is absent).

- [ ] **Step 3: Write minimal implementation**

In `worker.py`, change line 92 from `result = facade.run(record.config)` to:

```python
        result = facade.execute(record.config)
```

**Also update the existing worker tests:** the current `_FakeFacade` in
`tests/unit/test_optimization_worker.py` defines only `run()`. Give it an `execute(self, config)`
that delegates to `run(config)` (mirroring the real facade), so the existing worker tests keep
passing after the switch:

```python
    def execute(self, config):
        return self.run(config)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_optimization_worker.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/google_meridian_mcp_server/execution/worker.py tests/unit/test_optimization_worker.py
git commit -m "refactor: worker dispatches optimization by config kind via execute()"
```

---

### Task 7: Service — `run_future_optimization` + shared submit

**Files:**
- Modify: `src/google_meridian_mcp_server/services/optimization_service.py`
- Test: `tests/unit/test_optimization_service.py`

**Interfaces:**
- Produces: `run_future_optimization(self, model_id, config_dict, *, label=None, note=None, compute_tier="auto", force_rerun=False) -> dict` returning the same submit envelope.
- Internals: extract `_submit(self, model_id, config, *, label, note, compute_tier, force_rerun) -> dict` shared by both entry points (fingerprint reuse, tier resolve, record create, executor submit). `run_optimization` and `run_future_optimization` differ only in which config class they validate and their pre-submit validation.
- Future validation: build the facade, then dry-run `_future_kwargs` guards by calling a lightweight `facade.validate_future(config)` that runs the pure checks (cadence/window/multiplier/allocation channels, future start_date) without invoking `optimize`; raise `InvalidOptimizationConfigError` on `ValueError`.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/unit/test_optimization_service.py
def test_run_future_optimization_returns_queued_envelope(service_with_fakes):
    service, fakes = service_with_fakes
    out = service.run_future_optimization(
        "national-revenue",
        {
            "scenario": {"type": "fixed_budget"},
            "future": {"start_date": "2099-01-01", "horizon": 4},
        },
    )
    assert out["status"] == "queued"
    assert out["reused"] is False
    assert "run_id" in out


def test_run_future_optimization_reuses_identical_run(service_with_fakes):
    service, fakes = service_with_fakes
    cfg = {
        "scenario": {"type": "fixed_budget"},
        "future": {"start_date": "2099-01-01", "horizon": 4},
    }
    first = service.run_future_optimization("national-revenue", cfg)
    second = service.run_future_optimization("national-revenue", cfg)
    assert second["reused"] is True
    assert second["run_id"] == first["run_id"]


def test_run_future_invalid_config_raises(service_with_fakes):
    service, _ = service_with_fakes
    with pytest.raises(InvalidOptimizationConfigError):
        service.run_future_optimization(
            "national-revenue",
            {"scenario": {"type": "fixed_budget"},
             "future": {"start_date": "2099-01-01", "horizon": 0}},
        )
```

**Note — no `service_with_fakes` fixture exists yet.** `tests/unit/test_optimization_service.py`
currently builds services inline from `tmp_path`. Add a `service_with_fakes` pytest fixture to that
file that wires an `OptimizationService` with: a fake catalog returning a fake facade (providing
`resolve_use_kpi`, `channel_order`, and a `validate_future(config)` that raises `ValueError` only
for `horizon <= 0` / unknown channels), the existing local registry over `tmp_path`, and a fake
executor recording `submit`. Model it on the harness the existing tests in this file already use.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_optimization_service.py -k future -v`
Expected: FAIL with `AttributeError: 'OptimizationService' object has no attribute 'run_future_optimization'`.

- [ ] **Step 3: Write minimal implementation**

Refactor `run_optimization` to delegate to a shared `_submit`, then add the future entry point. Sketch:

```python
    def run_optimization(self, model_id, config_dict, *, label=None, note=None,
                         compute_tier="auto", force_rerun=False):
        facade = self._catalog.get_optimizer_facade(model_id)
        try:
            config = OptimizationConfig.model_validate(config_dict)
        except Exception as exc:
            raise InvalidOptimizationConfigError(str(exc)) from exc
        use_kpi = facade.resolve_use_kpi(config)
        try:
            to_optimize_kwargs(config, channel_order=facade.channel_order(), use_kpi=use_kpi)
        except ValueError as exc:
            raise InvalidOptimizationConfigError(str(exc)) from exc
        return self._submit(model_id, config, facade=facade, label=label, note=note,
                            compute_tier=compute_tier, force_rerun=force_rerun)

    def run_future_optimization(self, model_id, config_dict, *, label=None, note=None,
                                compute_tier="auto", force_rerun=False):
        facade = self._catalog.get_optimizer_facade(model_id)
        try:
            config = FutureOptimizationConfig.model_validate(config_dict)
        except Exception as exc:
            raise InvalidOptimizationConfigError(str(exc)) from exc
        try:
            facade.validate_future(config)  # pure guards, no optimize()
        except ValueError as exc:
            raise InvalidOptimizationConfigError(str(exc)) from exc
        return self._submit(model_id, config, facade=facade, label=label, note=note,
                            compute_tier=compute_tier, force_rerun=force_rerun)
```

`_submit` contains the current fingerprint-reuse / tier-resolve / record-create / executor-submit body (unchanged), operating on any `config`. `_default_label` already uses `config.scenario.type`, which exists on both.

Add `OptimizerFacade.validate_future(config)` that runs the pure checks from `_future_kwargs` up to (not including) `create_optimization_tensors`: cadence inference; `time_labels[0] > times[-1][:10]` (future start); `reference_indices` feasibility (cadence-aware); `validate_channel_keys(cost_multipliers, media_order + rf_order)` — the **union**, not per-subset; and `normalize_planned_allocation` channel check (via `channel_order()`). Raise `ValueError` on failure. (The `horizon <= 0` and non-positive-multiplier cases are already rejected by pydantic before the service sees the config — do not re-check them here.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_optimization_service.py -v`
Expected: PASS (existing + new future tests).

- [ ] **Step 5: Commit**

```bash
git add src/google_meridian_mcp_server/services/optimization_service.py src/google_meridian_mcp_server/meridian/optimizer_facade.py tests/unit/test_optimization_service.py
git commit -m "feat: OptimizationService.run_future_optimization sharing submit path"
```

---

### Task 8: Registry — config summary for future runs

**Files:**
- Modify: `src/google_meridian_mcp_server/persistence/optimization_run_registry.py:38-52`
- Test: `tests/unit/test_optimization_run_registry.py`

**Interfaces:**
- Consumes: `run.config` (now `AnyOptimizationConfig`).
- Produces: `build_config_summary(run)` returns a human string for both kinds, e.g. `"future fixed_budget • 13 wk from 2026-10-01 • trailing"`.

- [ ] **Step 1: Write the failing test**

**Note:** `tests/unit/test_optimization_run_registry.py` has no `_make_run` helper yet. Add a small
factory that builds an `OptimizationRun` from a `config` dict (filling the other required
`OptimizationRun` fields with fixed dummy values), used by this test.

```python
# add to tests/unit/test_optimization_run_registry.py
def test_build_config_summary_future():
    run = _make_run(  # helper you add in this test file
        config={
            "kind": "future",
            "scenario": {"type": "fixed_budget"},
            "future": {"start_date": "2026-10-01", "horizon": 13,
                       "reference": {"mode": "trailing"}},
        }
    )
    summary = build_config_summary(run)
    assert "future" in summary and "fixed_budget" in summary
    assert "2026-10-01" in summary and "13" in summary
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_optimization_run_registry.py::test_build_config_summary_future -v`
Expected: FAIL (current summary assumes historical fields / lacks future info).

- [ ] **Step 3: Write minimal implementation**

Extend `build_config_summary`:

```python
def build_config_summary(run: OptimizationRun) -> str:
    cfg = run.config
    if getattr(cfg, "kind", "historical") == "future":
        f = cfg.future
        return (
            f"future {cfg.scenario.type} • {f.horizon} periods from "
            f"{f.start_date.isoformat()} • {f.reference.mode}"
        )
    # ... existing historical branch unchanged ...
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_optimization_run_registry.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/google_meridian_mcp_server/persistence/optimization_run_registry.py tests/unit/test_optimization_run_registry.py
git commit -m "feat: config summary covers future optimization runs"
```

---

### Task 9: Transport — register `run_future_optimization`

**Files:**
- Modify: `src/google_meridian_mcp_server/transport/tools.py`
- Test: `tests/contract/test_optimization_tools.py` (Task 10 covers assertions; this task wires the tool)

**Interfaces:**
- Consumes: `_optimization_service(ctx).run_future_optimization(...)`, `FutureOptimizationConfig`.
- Produces: MCP tool `run_future_optimization(model_id, config: FutureOptimizationConfig, ctx, label?, note?, compute_tier?, force_rerun?)`.

- [ ] **Step 1: Add the tool (implementation-first; contract test in Task 10)**

Register beside `run_optimization`, mirroring its parameter annotations. Docstring must state the honesty guardrail and the async contract:

```python
    @mcp.tool
    async def run_future_optimization(
        model_id: Annotated[str, Field(min_length=1, description="Model identifier from list_models.")],
        config: Annotated[
            FutureOptimizationConfig,
            Field(description=(
                "Future optimization: same scenario + constraint as run_optimization, PLUS a "
                "`future` block {start_date, horizon, reference, cost_multipliers?, "
                "revenue_per_kpi_multiplier?, planned_allocation?}. start_date is the first future "
                "period (after the model's last training date); horizon is the number of periods at "
                "the model's cadence. reference selects the historical window carried forward for "
                "cost/flighting/revenue/default-budget: {mode:'trailing'|'same_period_last_year'|"
                "'full_history_average'}. cost_multipliers scale per-channel cost-per-media-unit; "
                "planned_allocation sets your planned mix (partial dicts are normalized). "
                "Valid channels/geos: get_model_overview.available_tool_options.run_optimization."
            )),
        ],
        ctx: Context,
        label: Annotated[str | None, Field(description="Human-readable label; omit to auto-generate.")] = None,
        note: Annotated[str | None, Field(description="Optional free-text intent; stored with the run.")] = None,
        compute_tier: Annotated[
            Literal["auto", "local", "cloud_cpu", "cloud_gpu"],
            Field(description="Where to run; 'auto' (default) picks the cheapest allowed backend."),
        ] = "auto",
        force_rerun: Annotated[bool, Field(description="Force fresh computation even if an identical prior run exists.")] = False,
    ) -> dict[str, Any]:
        """Optimize a FUTURE budget under explicit assumptions (not a demand forecast). Answers "how should I split next quarter's budget?" or "if TV CPMs rise 20%, what's the best future mix?". Meridian does not forecast the future: this carries forward a chosen historical reference window's costs/flighting/revenue (optionally scaled by cost_multipliers / revenue_per_kpi_multiplier) and optimizes the allocation over a future window you define with start_date + horizon. Long-running: returns a run_id immediately — poll get_optimization_status until 'completed', then get_optimization_result. Identical prior runs are reused unless force_rerun=true."""
        try:
            return _optimization_service(ctx).run_future_optimization(
                model_id, config.model_dump(mode="json"),
                label=label, note=note, compute_tier=compute_tier, force_rerun=force_rerun,
            )
        except MeridianMcpError as error:
            return _error_response(error)
```

Also update the five run-id tool docstrings that currently say "from run_optimization" to "from run_optimization or run_future_optimization" (status/result/`run_id` field descriptions), so the LLM knows they serve both.

- [ ] **Step 2: Import `FutureOptimizationConfig`** at the top of `tools.py` alongside `OptimizationConfig`.

- [ ] **Step 3: Smoke-check the server lists the tool**

Run:
```bash
uv run python -c "
import asyncio
from fastmcp import Client
from google_meridian_mcp_server.server import mcp

async def main():
    async with Client(mcp) as c:
        names = [t.name for t in await c.list_tools()]
    print('run_future_optimization' in names)

asyncio.run(main())
"
```
Expected: prints `True`.

- [ ] **Step 4: Commit**

```bash
git add src/google_meridian_mcp_server/transport/tools.py
git commit -m "feat: register run_future_optimization MCP tool"
```

---

### Task 10: Contract test — `run_future_optimization` envelope

**Files:**
- Modify: `tests/contract/test_optimization_tools.py`

**Interfaces:**
- Consumes: in-process `Client(mcp)` harness already used by this file.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/contract/test_optimization_tools.py
async def test_run_future_optimization_submit_envelope(client):
    res = await client.call_tool("run_future_optimization", {
        "model_id": "national-revenue",
        "config": {
            "scenario": {"type": "fixed_budget"},
            "future": {"start_date": "2099-01-01", "horizon": 4},
        },
    })
    data = res.data
    assert data["status"] in ("queued", "running", "completed")
    assert "run_id" in data and "compute_tier_resolved" in data


async def test_run_future_optimization_unknown_channel_errors(client):
    # Reaches the service → flat invalid_optimization_config envelope.
    res = await client.call_tool("run_future_optimization", {
        "model_id": "national-revenue",
        "config": {
            "scenario": {"type": "fixed_budget"},
            "future": {"start_date": "2099-01-01", "horizon": 4,
                       "cost_multipliers": {"not_a_channel": 1.2}},
        },
    })
    assert res.data["error_code"] == "invalid_optimization_config"


async def test_run_future_optimization_horizon_zero_is_protocol_error(client):
    # horizon=0 violates the FutureBlock pydantic gt=0 at FastMCP input validation,
    # BEFORE the tool body — so it's a protocol-level tool error, not the flat envelope.
    with pytest.raises(Exception):  # replace with the file's ToolError type if imported
        await client.call_tool("run_future_optimization", {
            "model_id": "national-revenue",
            "config": {
                "scenario": {"type": "fixed_budget"},
                "future": {"start_date": "2099-01-01", "horizon": 0},
            },
        })
```

Two things to match the existing file: (1) its flat error envelope is `{"error_code", "message",
"details"}` — assert `res.data["error_code"]`, not a nested `error.code`; confirm by reading
`_error_response` in `transport/tools.py`. (2) reuse the file's existing async in-process `client`
fixture and its `ToolError`/exception type for the protocol-error case.

- [ ] **Step 2: Run test to verify it fails, then confirm it passes after Task 9 is in place**

Run: `uv run pytest tests/contract/test_optimization_tools.py -k future -v`
Expected: PASS once Task 9 is committed (FAIL if Task 9 not yet done — do Task 9 first).

- [ ] **Step 3: Commit**

```bash
git add tests/contract/test_optimization_tools.py
git commit -m "test: contract coverage for run_future_optimization envelope + validation"
```

---

### Task 11: Live-validation coverage + fixture helper

**Files:**
- Modify: `scripts/validation/matrix.py`, `scripts/validation/runner.py`
- Create: `scripts/validation/fixtures.py` (thin `ensure_fixture_model(model_id)` helper wrapping the existing generator + loader)

**Interfaces:**
- Produces: `ensure_fixture_model(model_id) -> mmm` (build-if-missing, returns a loaded Meridian model) — reused by Task 5.
- Extends the declarative matrix so `run_future_optimization` is exercised on `national-revenue` and `geo-revenue` (submit → poll → result), with adversarial cases.

- [ ] **Step 1: Add `ensure_fixture_model`**

Extract the "build if missing, then load" logic the suite already performs into `scripts/validation/fixtures.py` so both the live suite and unit Task 5 import one helper. Implement using the existing generator entrypoint (`scripts/generate_validation_models.py`) and the model loader used by `ModelCatalog`.

- [ ] **Step 2: Extend the matrix**

Add `run_future_optimization` expectations: expected-valid on `national-revenue` and `geo-revenue` with `{scenario: fixed_budget, future: {start_date: <after last train week>, horizon: 4, reference: trailing}}`. Adversarial cases must be ones that reach the service and return the flat envelope (the runner asserts `payload.get("error_code")`): **non-future `start_date`** and **unknown channel in `cost_multipliers`** → `invalid_optimization_config`. Do **not** put `horizon=0` in the matrix — it's rejected by pydantic at the tool boundary (a protocol error), not the envelope the runner checks.

- [ ] **Step 3: Run the live validation suite**

Run: `OPTIMIZATION_ALLOWED_TIERS=local uv run python -m scripts.validation.live_validate`
Expected: ends with `LIVE VALIDATION PASSED`; the matrix shows `run_future_optimization` PASS/EXPECTED-ERR columns.

- [ ] **Step 4: Commit**

```bash
git add scripts/validation/
git commit -m "test: live-validation coverage for run_future_optimization"
```

---

### Task 12: Final QA pass — local live driver (mandatory gate)

**Files:**
- Create: `scripts/qa/future_optimization_qa.py`

**Interfaces:**
- Drives an in-process `Client(mcp)` through **both** tools, 5 scenarios each (per spec §13.1), local tier only, printing a PASS/FAIL report and exiting non-zero on any failure.

- [ ] **Step 1: Write the QA driver**

Implement the 10 scenarios from spec §13.1 (5 historical, 5 future), each: submit → poll
`get_optimization_status` until terminal → assert. Happy paths assert `completed` + well-formed
result (`outcome_mode` present, non-empty `channel_tables.optimized`, JSON round-trips). Adversarial
cases assert the **right layer** (per spec §12/§13.1):
- submit returns the flat `{"error_code": "invalid_optimization_config"}` envelope — for the
  `per_channel`-missing-channel, non-future `start_date`, unknown-channel `cost_multipliers`, and
  `same_period_last_year`-insufficient-history cases;
- **failed run** (submit succeeds, then polling ends in `status == "failed"`) — for the unknown-geo
  historical case;
- **protocol-level tool error** (exception on `call_tool`, no envelope) — for `horizon <= 0`.

Include the reuse-by-fingerprint check (submit an identical config twice → `reused: true`) and one
`force_rerun=true` check. Set `OPTIMIZATION_ALLOWED_TIERS=local` in-process before building config.

- [ ] **Step 2: Run the QA driver and capture evidence**

Run: `OPTIMIZATION_ALLOWED_TIERS=local uv run python scripts/qa/future_optimization_qa.py`
Expected: prints a per-scenario table ending `FUTURE-OPT QA PASSED (10/10)`; exit code 0. Paste the output into the PR description as the completion evidence required by spec §13.1.

- [ ] **Step 3: Full suite + lint gate**

Run: `uv run pytest && uv run ruff check src tests scripts && uv run ruff format --check src tests scripts`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add scripts/qa/future_optimization_qa.py
git commit -m "test: local final-QA driver for both optimization tools"
```

---

### Task 13: Skill update — `meridian-analyst` (writer → reviewer loop, ≥2 loops)

**Files:**
- Modify: `skills/meridian-analyst/SKILL.md`, `skills/meridian-analyst/references/budget-optimization.md`, `skills/meridian-analyst/references/taxonomy.md`, `skills/meridian-analyst/references/glossary.md`
- Create: `skills/meridian-analyst/references/consultation.md`

**Interfaces:** none (documentation). Content requirements per spec §14.1–§14.4.

- [ ] **Step 1 — Writer, loop 1: draft all skill edits**

Apply the §14.1 content changes:
- Rewrite the **"Forward-looking planning"** section of `budget-optimization.md` to route forward questions to `run_future_optimization` (submit → poll → result), explain future-vs-historical tool choice, and **remove** the stale "server does not expose new_data / do not attempt it" paragraph. Update the routing-table row *"Plan NEXT quarter's budget"* to point at the real tool.
- Add `run_future_optimization` to `SKILL.md` cardinal rules (same async lifecycle; the five run-id tools serve both tools) and add a cardinal rule linking `consultation.md`.
- Add glossary/taxonomy entries: reference window, cost multiplier, flighting, planned allocation, allocation-axis vs cost-structure-axis.
- Create `consultation.md` with the §14.4 layer: hybrid interaction model, calibrated confirm gate, per-flow "minimum to elicit" checklists, plain-language→tool-field translation table, and anti-patterns.

- [ ] **Step 2 — Reviewer, loop 1: critique against the §14.3 rubric**

Dispatch a reviewer to score the draft against the six rubric dimensions (Accuracy, Input completeness, LLM-routing clarity, Caveat correctness, Consistency & voice, Consultative layer). Reviewer must cite file/section for each finding and cross-check every field name/default/preset against the *implemented* `FutureBlock`/`Reference` in `domain/optimization.py` and the tool docstring in `tools.py`. Capture findings.

- [ ] **Step 3 — Writer, loop 2: revise against findings**

Apply every actionable finding from Step 2. Fix any stale field names, missing input docs, or mis-scoped caveats.

- [ ] **Step 4 — Reviewer, loop 2: re-review and sign off**

Re-run the rubric. If new findings surface, run additional writer→reviewer loops until the reviewer signs off (minimum two loops already satisfied). Record the sign-off.

- [ ] **Step 5: Verify skill references resolve and commit**

Run: `uv run python -c "import pathlib; [print(p) for p in pathlib.Path('skills/meridian-analyst').rglob('*.md')]"`
Expected: lists `consultation.md` plus the four modified files. Confirm the stale limitation text is
gone — the actual wording is *"does **not** expose"* / references `new_data`, so grep for those:
`grep -rniE "does .*not.* expose|scenario-planning input|do not (claim|attempt)" skills/meridian-analyst || echo "clean"` → prints `clean`.

```bash
git add skills/meridian-analyst/
git commit -m "docs: update meridian-analyst skill for future optimization + consultative layer"
```

---

## Self-Review

**Spec coverage check:**
- §5 tool surface → Task 9. §6 config split + discriminator → Task 1. §6 `to_optimize_kwargs` reuse → Task 2. §7 carry-forward semantics → Tasks 3 (pure math) + 5 (wiring). §8 planned_allocation normalize → Task 3 + 5. §9 shared `_run`/builders → Tasks 4 + 5. §10 worker/registry/fingerprint → Tasks 6 + 8 (fingerprint reuse verified in Tasks 7/12). §11 result shape → reused `build_result` (Tasks 4/5). §12 validation/errors → Tasks 3 (pure raises) + 7 (`validate_future` → `InvalidOptimizationConfigError`). §13 testing → Tasks 1–11; §13.1 final QA → Task 12. §14 skill update + writer/reviewer loop → Task 13. §14.4 consultation → Task 13.
- No spec section is unassigned.

**Placeholder scan:** No "TBD/handle appropriately" — the one acknowledged risk (tensor shape/granularity in Task 5) is bounded by an explicit integration test rather than left vague. Skill prose content (Task 13) is defined by the spec sections it cites.

**Type consistency:** `AnyOptimizationConfig`, `FutureOptimizationConfig`, `FutureBlock`, `Reference`, `execute`, `run_future`, `_future_kwargs`, `validate_future`, `run_future_optimization`, `ensure_fixture_model` are named identically wherever referenced across tasks.

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-07-02-future-budget-optimization.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

**Which approach?**

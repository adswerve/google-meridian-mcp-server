# Future Channel Exclusion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a real "pause/exclude a channel" capability to `run_future_optimization` via a new `future.excluded_channels` field, and correct the analyst-skill docs that falsely claim exclusion is possible with `0/0` constraint bounds.

**Architecture:** Exclusion forces the channel's baseline weight (`pct_of_spend`) to 0 and renormalizes the rest, plus clamps its spend-constraint bounds to `0/0`. Meridian then pins it to spend 0 (verified live). Pure math/validation helpers live in `meridian/future_data.py`; the facade's `_future_kwargs` wires them in. Future-only — historical runs are unchanged and their docs are corrected to say exclusion is unsupported there.

**Tech Stack:** Python 3.12, Pydantic v2, Google Meridian `BudgetOptimizer`, FastMCP, pytest, `uv`.

## Global Constraints

- Scope is **future-only**: touch `FutureBlock`/future path only; do **not** change `run_optimization` (historical) behavior or `to_optimize_kwargs`' existing per-channel "missing bounds" rule.
- Budget behavior: with `fixed_budget`, exclusion **reallocates** the excluded channel's budget across remaining channels — **total budget unchanged**.
- A channel may **not** appear in both `excluded_channels` and `planned_allocation`/`cost_multipliers` — reject with a clear `ValueError`.
- Cannot exclude **every** channel — at least one must remain (remaining baseline weight > 0).
- Do **not** change the existing `> 0` validation on `cost_multipliers`/`planned_allocation`.
- Keep `0/0` bounds documented as **freeze at baseline** (correct); only remove the false "`0/0` excludes a channel" claims.
- All exclusion validation raises `ValueError` (the service maps it to the `invalid_optimization_config` envelope).
- Commits: **no** Claude/Opus `Co-Authored-By` trailer.
- Run tests with `uv run pytest`; integration/live tests need `OPTIMIZATION_ALLOWED_TIERS=local`.

---

### Task 1: Pure helpers — `validate_excluded_channels` + `apply_exclusions`

**Files:**
- Modify: `src/google_meridian_mcp_server/meridian/future_data.py` (append two functions)
- Test: `tests/unit/test_future_data.py` (append)

**Interfaces:**
- Consumes: nothing new (pure functions, `numpy` already imported but not needed here).
- Produces:
  - `validate_excluded_channels(excluded: list[str] | None, planned_allocation: dict[str, float] | None, cost_multipliers: dict[str, float] | None, channel_order: list[str]) -> None` — raises `ValueError` on unknown channel, overlap with planned/cost, or all-excluded.
  - `apply_exclusions(pct: list[float], spend_lower, spend_upper, excluded: list[str] | None, channel_order: list[str]) -> tuple[list[float], list[float], list[float]]` — returns `(new_pct, lower_list, upper_list)` with excluded weights zeroed + renormalized and excluded bounds set to 0; scalar bounds are expanded to per-channel lists. Raises `ValueError` if remaining weight sums to 0.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_future_data.py`:

```python
ORDER = ["ch_0", "ch_1", "ch_2", "rf_ch_0", "rf_ch_1"]


def test_validate_excluded_channels_ok():
    fd.validate_excluded_channels(["ch_0"], None, None, ORDER)  # no raise


def test_validate_excluded_channels_none_is_noop():
    fd.validate_excluded_channels(None, {"ch_0": 0.5}, {"ch_0": 1.2}, ORDER)


def test_validate_excluded_channels_unknown():
    with pytest.raises(ValueError, match="unknown"):
        fd.validate_excluded_channels(["nope"], None, None, ORDER)


def test_validate_excluded_channels_overlap_planned():
    with pytest.raises(ValueError, match="planned_allocation"):
        fd.validate_excluded_channels(["ch_0"], {"ch_0": 0.4}, None, ORDER)


def test_validate_excluded_channels_overlap_cost():
    with pytest.raises(ValueError, match="cost_multipliers"):
        fd.validate_excluded_channels(["ch_1"], None, {"ch_1": 1.5}, ORDER)


def test_validate_excluded_channels_all_excluded():
    with pytest.raises(ValueError, match="every channel"):
        fd.validate_excluded_channels(list(ORDER), None, None, ORDER)


def test_apply_exclusions_none_returns_inputs_as_lists():
    pct = [0.2, 0.2, 0.2, 0.2, 0.2]
    new_pct, lo, hi = fd.apply_exclusions(pct, 0.3, 0.3, None, ORDER)
    assert new_pct == pct
    assert lo == [0.3] * 5 and hi == [0.3] * 5


def test_apply_exclusions_zeroes_and_renormalizes_global_bounds():
    pct = [0.2, 0.2, 0.2, 0.2, 0.2]
    new_pct, lo, hi = fd.apply_exclusions(pct, 0.3, 0.3, ["ch_0"], ORDER)
    assert new_pct[0] == 0.0
    assert lo[0] == 0.0 and hi[0] == 0.0
    assert abs(sum(new_pct) - 1.0) < 1e-9
    assert all(abs(w - 0.25) < 1e-9 for w in new_pct[1:])  # 0.2/0.8
    assert lo[1:] == [0.3] * 4 and hi[1:] == [0.3] * 4


def test_apply_exclusions_per_channel_bounds_list():
    pct = [0.2, 0.2, 0.2, 0.2, 0.2]
    lower = [0.1, 0.2, 0.3, 0.4, 0.5]
    upper = [0.5, 0.4, 0.3, 0.2, 0.1]
    new_pct, lo, hi = fd.apply_exclusions(pct, lower, upper, ["ch_2"], ORDER)
    assert new_pct[2] == 0.0 and lo[2] == 0.0 and hi[2] == 0.0
    assert lo[0] == 0.1 and hi[0] == 0.5  # untouched


def test_apply_exclusions_all_zero_raises():
    with pytest.raises(ValueError, match="zero baseline"):
        fd.apply_exclusions([0.0, 0.0], 0.3, 0.3, [], ["a", "b"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_future_data.py -k "excluded or apply_exclusions" -v`
Expected: FAIL with `AttributeError: module ... has no attribute 'validate_excluded_channels'`.

- [ ] **Step 3: Implement the helpers**

Append to `src/google_meridian_mcp_server/meridian/future_data.py`:

```python
def validate_excluded_channels(
    excluded: list[str] | None,
    planned_allocation: dict[str, float] | None,
    cost_multipliers: dict[str, float] | None,
    channel_order: list[str],
) -> None:
    if not excluded:
        return
    unknown = [ch for ch in excluded if ch not in channel_order]
    if unknown:
        raise ValueError(f"excluded_channels has unknown channels: {unknown}")
    excluded_set = set(excluded)
    if planned_allocation:
        overlap = sorted(excluded_set & set(planned_allocation))
        if overlap:
            raise ValueError(
                f"channels cannot be both excluded and in planned_allocation: {overlap}"
            )
    if cost_multipliers:
        overlap = sorted(excluded_set & set(cost_multipliers))
        if overlap:
            raise ValueError(
                f"channels cannot be both excluded and in cost_multipliers: {overlap}"
            )
    if set(channel_order) <= excluded_set:
        raise ValueError("cannot exclude every channel; at least one must remain.")


def apply_exclusions(
    pct: list[float],
    spend_lower,
    spend_upper,
    excluded: list[str] | None,
    channel_order: list[str],
) -> tuple[list[float], list[float], list[float]]:
    n = len(channel_order)
    lower = (
        list(spend_lower)
        if isinstance(spend_lower, (list, tuple))
        else [spend_lower] * n
    )
    upper = (
        list(spend_upper)
        if isinstance(spend_upper, (list, tuple))
        else [spend_upper] * n
    )
    if not excluded:
        return list(pct), lower, upper
    excluded_idx = {channel_order.index(ch) for ch in excluded}
    new_pct = [0.0 if i in excluded_idx else pct[i] for i in range(n)]
    total = sum(new_pct)
    if total <= 0:
        raise ValueError("excluding these channels leaves zero baseline weight.")
    new_pct = [w / total for w in new_pct]
    for i in excluded_idx:
        lower[i] = 0.0
        upper[i] = 0.0
    return new_pct, lower, upper
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_future_data.py -k "excluded or apply_exclusions" -v`
Expected: PASS (10 tests).

- [ ] **Step 5: Commit**

```bash
git add src/google_meridian_mcp_server/meridian/future_data.py tests/unit/test_future_data.py
git commit -m "feat: pure helpers for future channel exclusion (validate + apply)"
```

---

### Task 2: Add `excluded_channels` field to `FutureBlock`

**Files:**
- Modify: `src/google_meridian_mcp_server/domain/optimization.py` (`FutureBlock`, after `planned_allocation`, before the `_positive_weights` validator)
- Test: `tests/unit/test_future_optimization_config.py` (append)

**Interfaces:**
- Consumes: nothing.
- Produces: `FutureBlock.excluded_channels: list[str] | None` (default `None`), reachable via `FutureOptimizationConfig.future.excluded_channels` and `AnyOptimizationConfig` discriminated parsing.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_future_optimization_config.py`:

```python
def test_future_block_accepts_excluded_channels():
    from google_meridian_mcp_server.domain.optimization import FutureOptimizationConfig

    cfg = FutureOptimizationConfig.model_validate(
        {
            "scenario": {"type": "fixed_budget"},
            "future": {
                "start_date": "2099-01-01",
                "horizon": 4,
                "excluded_channels": ["TV"],
            },
        }
    )
    assert cfg.future.excluded_channels == ["TV"]


def test_future_block_excluded_channels_defaults_none():
    from google_meridian_mcp_server.domain.optimization import FutureBlock

    fb = FutureBlock.model_validate({"start_date": "2099-01-01", "horizon": 4})
    assert fb.excluded_channels is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_future_optimization_config.py -k excluded_channels -v`
Expected: FAIL (`excluded_channels` unset → `AttributeError` / equals `None` mismatch on the first test).

- [ ] **Step 3: Add the field**

In `src/google_meridian_mcp_server/domain/optimization.py`, inside `FutureBlock`, immediately after the `planned_allocation` field and before `@field_validator("cost_multipliers", "planned_allocation")`:

```python
    excluded_channels: list[str] | None = Field(
        default=None,
        description="Channels to fully pause/exclude from the future plan (spend "
        "forced to 0; their budget is reallocated across the remaining channels, "
        "total budget unchanged). Keys must be valid paid/RF channels and must NOT "
        "also appear in planned_allocation or cost_multipliers. Cannot exclude every "
        "channel. Example: ['TV'].",
        examples=[["TV"]],
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_future_optimization_config.py -k excluded_channels -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/google_meridian_mcp_server/domain/optimization.py tests/unit/test_future_optimization_config.py
git commit -m "feat: FutureBlock.excluded_channels field"
```

---

### Task 3: Wire exclusion into `OptimizerFacade` (validate + build kwargs)

**Files:**
- Modify: `src/google_meridian_mcp_server/meridian/optimizer_facade.py` (`validate_future`, `_future_kwargs`)
- Test: `tests/integration/test_optimizer_facade_future.py` (append)

**Interfaces:**
- Consumes: `future_data.validate_excluded_channels`, `future_data.apply_exclusions` (Task 1); `FutureBlock.excluded_channels` (Task 2); existing `self.channel_order()`, `self._carried_allocation(window)`, `fd.normalize_planned_allocation`.
- Produces: `run_future(config)` honors `config.future.excluded_channels` — excluded channels pinned to spend 0 in the result; validation raised at submit-time via `validate_future`.

- [ ] **Step 1: Write the failing test**

Append to `tests/integration/test_optimizer_facade_future.py`:

```python
def _spend_map(rows):
    return {r["channel"]: r["spend"] for r in rows}


def test_run_future_excludes_channel(national_revenue_facade):
    facade = national_revenue_facade
    result = facade.run_future(_future_cfg(facade, excluded_channels=["ch_0"]))
    initial = _spend_map(result["channel_tables"]["initial"])
    optimized = _spend_map(result["channel_tables"]["optimized"])
    # Excluded channel present in BOTH tables, pinned to 0.
    assert "ch_0" in initial and "ch_0" in optimized
    assert initial["ch_0"] == 0 or initial["ch_0"] is None or initial["ch_0"] == 0.0
    assert optimized["ch_0"] == 0 or optimized["ch_0"] == 0.0
    # Remaining channels carry all the spend.
    assert sum(v for k, v in optimized.items() if k != "ch_0") > 0


def test_run_future_exclude_unknown_channel_raises(national_revenue_facade):
    facade = national_revenue_facade
    with pytest.raises(ValueError, match="unknown"):
        facade.validate_future(_future_cfg(facade, excluded_channels=["nope"]))


def test_run_future_exclude_all_raises(national_revenue_facade):
    facade = national_revenue_facade
    every = facade.channel_order()
    with pytest.raises(ValueError, match="every channel"):
        facade.validate_future(_future_cfg(facade, excluded_channels=every))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `OPTIMIZATION_ALLOWED_TIERS=local uv run pytest tests/integration/test_optimizer_facade_future.py -k exclude -v`
Expected: FAIL — `test_run_future_excludes_channel` shows `ch_0` still carrying nonzero spend (exclusion not wired); the two validate tests fail to raise.

- [ ] **Step 3: Wire into the facade**

In `src/google_meridian_mcp_server/meridian/optimizer_facade.py`:

(a) In `validate_future`, after the existing `fd.validate_channel_keys(f.planned_allocation, self.channel_order())` line, add:

```python
        fd.validate_excluded_channels(
            f.excluded_channels,
            f.planned_allocation,
            f.cost_multipliers,
            self.channel_order(),
        )
```

(b) In `_future_kwargs`, locate the block:

```python
        carried = self._carried_allocation(window)  # {channel: weight}
        pct = fd.normalize_planned_allocation(
            f.planned_allocation, carried, self.channel_order()
        )
```

Immediately after it, add (materialize a baseline vector when excluding and none was planned):

```python
        if f.excluded_channels:
            fd.validate_excluded_channels(
                f.excluded_channels,
                f.planned_allocation,
                f.cost_multipliers,
                self.channel_order(),
            )
            if pct is None:
                total_carried = sum(carried.values())
                pct = [
                    carried[ch] / total_carried for ch in self.channel_order()
                ]
```

Then locate the final block that builds and returns kwargs:

```python
        kwargs = to_optimize_kwargs(
            config, channel_order=self.channel_order(), use_kpi=use_kpi
        )
        kwargs.update(
            new_data=new_data,
            start_date=time_labels[0],
            end_date=time_labels[-1],
            pct_of_spend=pct,
            budget=budget,
        )
        return kwargs
```

Replace the `return kwargs` line (keep everything above it) with:

```python
        if f.excluded_channels:
            new_pct, lower, upper = fd.apply_exclusions(
                pct,
                kwargs["spend_constraint_lower"],
                kwargs["spend_constraint_upper"],
                f.excluded_channels,
                self.channel_order(),
            )
            kwargs.update(
                pct_of_spend=new_pct,
                spend_constraint_lower=lower,
                spend_constraint_upper=upper,
            )
        return kwargs
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `OPTIMIZATION_ALLOWED_TIERS=local uv run pytest tests/integration/test_optimizer_facade_future.py -k exclude -v`
Expected: PASS (3 tests). Then run the whole future integration file to confirm no regression:
Run: `OPTIMIZATION_ALLOWED_TIERS=local uv run pytest tests/integration/test_optimizer_facade_future.py -v`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add src/google_meridian_mcp_server/meridian/optimizer_facade.py tests/integration/test_optimizer_facade_future.py
git commit -m "feat: run_future honors excluded_channels (pin to 0, reallocate rest)"
```

---

### Task 4: Contract coverage — tool accepts `excluded_channels`; error envelopes

**Files:**
- Modify: `tests/contract/test_optimization_tools.py` (append; reuse the existing `client` fixture)

**Interfaces:**
- Consumes: the in-process `client` fixture already defined in this file (wired to the `national-revenue` fixture); tool `run_future_optimization`.
- Produces: contract guarantees that a valid `excluded_channels` submits, and that unknown-channel / all-excluded produce the flat `invalid_optimization_config` envelope.

- [ ] **Step 1: Write the failing tests**

Append to `tests/contract/test_optimization_tools.py`:

```python
@pytest.mark.asyncio
async def test_run_future_optimization_excluded_channels_submits(client):
    res = await client.call_tool(
        "run_future_optimization",
        {
            "model_id": "national-revenue",
            "config": {
                "scenario": {"type": "fixed_budget"},
                "future": {
                    "start_date": "2099-01-01",
                    "horizon": 4,
                    "excluded_channels": ["ch_0"],
                },
            },
        },
    )
    data = res.data
    assert data["status"] in ("queued", "running", "completed")
    assert "run_id" in data


@pytest.mark.asyncio
async def test_run_future_optimization_exclude_unknown_channel_errors(client):
    res = await client.call_tool(
        "run_future_optimization",
        {
            "model_id": "national-revenue",
            "config": {
                "scenario": {"type": "fixed_budget"},
                "future": {
                    "start_date": "2099-01-01",
                    "horizon": 4,
                    "excluded_channels": ["not_a_channel"],
                },
            },
        },
    )
    assert res.data["error_code"] == "invalid_optimization_config"


@pytest.mark.asyncio
async def test_run_future_optimization_exclude_all_errors(client):
    res = await client.call_tool(
        "run_future_optimization",
        {
            "model_id": "national-revenue",
            "config": {
                "scenario": {"type": "fixed_budget"},
                "future": {
                    "start_date": "2099-01-01",
                    "horizon": 4,
                    "excluded_channels": [
                        "ch_0",
                        "ch_1",
                        "ch_2",
                        "rf_ch_0",
                        "rf_ch_1",
                    ],
                },
            },
        },
    )
    assert res.data["error_code"] == "invalid_optimization_config"
```

- [ ] **Step 2: Run tests to verify they fail (or error)**

Run: `OPTIMIZATION_ALLOWED_TIERS=local uv run pytest tests/contract/test_optimization_tools.py -k "excluded or exclude" -v`
Expected: before the facade wiring is reachable these still fail — run AFTER Task 3. The unknown-channel and all-excluded tests fail if the error is not surfaced as `invalid_optimization_config`; the submit test fails if the field is rejected.

Note: any submitted run spawns a worker. If this file already reaps runs (it does for the existing submit-envelope test), mirror that cleanup for `test_run_future_optimization_excluded_channels_submits` — add `delete_optimization` on its `run_id` in a `try/finally` exactly like the existing `test_run_future_optimization_submit_envelope` sibling.

- [ ] **Step 3: Add reap cleanup to the submit test**

Update `test_run_future_optimization_excluded_channels_submits` to reap the run, matching the existing pattern used by `test_run_future_optimization_submit_envelope` in this file:

```python
@pytest.mark.asyncio
async def test_run_future_optimization_excluded_channels_submits(client):
    res = await client.call_tool(
        "run_future_optimization",
        {
            "model_id": "national-revenue",
            "config": {
                "scenario": {"type": "fixed_budget"},
                "future": {
                    "start_date": "2099-01-01",
                    "horizon": 4,
                    "excluded_channels": ["ch_0"],
                },
            },
        },
    )
    data = res.data
    assert data["status"] in ("queued", "running", "completed")
    run_id = data["run_id"]
    await client.call_tool("delete_optimization", {"run_id": run_id})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `OPTIMIZATION_ALLOWED_TIERS=local uv run pytest tests/contract/test_optimization_tools.py -k "excluded or exclude" -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add tests/contract/test_optimization_tools.py
git commit -m "test: contract coverage for future excluded_channels + error envelopes"
```

---

### Task 5: Live-validation + local QA coverage

**Files:**
- Modify: `scripts/validation/runner.py` (`assert_live_future_optimization`)
- Modify: `scripts/qa/future_optimization_qa.py` (add one exclusion scenario)

**Interfaces:**
- Consumes: existing `call(...)`, `poll_to_terminal(...)`, `assert_error(...)` helpers already used in these files; tool `run_future_optimization`.
- Produces: one live end-to-end exclusion assertion in the validation suite and one QA scenario, both proving the excluded channel's optimized spend is 0.

- [ ] **Step 1: Read the current `assert_live_future_optimization` happy-path block**

Run: `sed -n '157,235p' scripts/validation/runner.py`
Expected: you see the submit → poll → result asserts and the `try/finally` that reaps the happy-path run via `delete_optimization`.

- [ ] **Step 2: Add an exclusion assertion inside the existing `try` block**

Inside `assert_live_future_optimization`, within the existing `try:` (after the result-key asserts, before the reuse-check), add a second submit that excludes the first channel and asserts its optimized spend is 0. Use the model's channel list from `overview` (the function already has `overview`); pick the first paid/RF channel. Insert:

```python
        # Exclusion: pause the first channel; assert it is pinned to 0 spend.
        first_channel = overview["available_tool_options"]["run_optimization"][
            "channels"
        ][0]
        excl_submit = await call(
            client,
            "run_future_optimization",
            {
                "model_id": model_id,
                "config": {
                    "scenario": {"type": "fixed_budget"},
                    "future": {
                        "start_date": "2099-01-01",
                        "horizon": 4,
                        "excluded_channels": [first_channel],
                    },
                },
            },
        )
        excl_run_id = excl_submit["run_id"]
        try:
            excl_status = await poll_to_terminal(client, excl_run_id)
            assert excl_status == "completed", (
                f"{model_id}/exclude expected completed, got {excl_status}"
            )
            excl_result = await call(
                client, "get_optimization_result", {"run_id": excl_run_id}
            )
            opt_rows = excl_result["channel_tables"]["optimized"]
            excl_spend = next(
                r["spend"] for r in opt_rows if r["channel"] == first_channel
            )
            assert excl_spend in (0, 0.0, None), (
                f"{model_id}/exclude expected 0 spend for {first_channel}, "
                f"got {excl_spend}"
            )
        finally:
            await call(client, "delete_optimization", {"run_id": excl_run_id})
```

Note: if `poll_to_terminal`'s signature in `runner.py` differs from `(client, run_id)`, match the existing call in this same function; do not invent a signature.

- [ ] **Step 3: Run the live suite for one model to verify**

Run: `OPTIMIZATION_ALLOWED_TIERS=local uv run python -m scripts.validation.live_validate 2>&1 | tail -5`
Expected: `LIVE VALIDATION PASSED` with the count increased (the added exclusion assertion runs inside the existing future labels, so the label count is unchanged but the assertion executes; a clean PASS with 0 failed is the gate).

- [ ] **Step 4: Add a QA scenario**

Read the existing future scenarios and their registration:

Run: `sed -n '305,420p' scripts/qa/future_optimization_qa.py`

Add a new `scenario_f6` mirroring the structure of the nearest future scenario (submit `run_future_optimization` with `excluded_channels=[first_channel]`, poll, assert the excluded channel's optimized spend is 0, then reap the run — track its `run_id` in the same finally-sweep the file already uses). Register it in the scenario list/counter the same way the other `scenario_f*` are registered, and update the "10 scenarios"/expected-count wording in the module docstring and any `PASSED (N/N)` assertion to the new total. Keep the run-reaping finally-sweep intact.

- [ ] **Step 5: Run the QA driver**

Run: `OPTIMIZATION_ALLOWED_TIERS=local uv run python -m scripts.qa.future_optimization_qa 2>&1 | tail -15`
Expected: the per-scenario table shows the new exclusion scenario PASS and the suite prints its updated `... QA PASSED (N/N)` and exits 0.

- [ ] **Step 6: Commit**

```bash
git add scripts/validation/runner.py scripts/qa/future_optimization_qa.py
git commit -m "test: live-validation + QA coverage for future channel exclusion"
```

---

### Task 6: Documentation — correct the false claims, document `excluded_channels`

**Files:**
- Modify: `skills/meridian-analyst/references/consultation.md`
- Modify: `skills/meridian-analyst/references/budget-optimization.md`
- Modify: `skills/meridian-analyst/references/glossary.md`
- Modify: `src/google_meridian_mcp_server/transport/tools.py` (`run_future_optimization` docstring)
- Modify: `AGENTS.md` (future-optimization section; keep under the 250-line cap)

**Interfaces:**
- Consumes: the shipped `future.excluded_channels` semantics from Tasks 2–3.
- Produces: docs that (a) keep `0/0` = freeze, (b) remove every "`0/0` excludes a channel" claim, (c) document `future.excluded_channels` as the real exclusion path with the "pause TV, spend it elsewhere" example, (d) state historical runs cannot fully exclude.

- [ ] **Step 1: Fix `consultation.md`**

In the "Plain language → tool-field translation" table, replace the row:

```
| "Pause / stop channel X entirely next quarter" | **Not** `planned_allocation: {"X": 0}` or `cost_multipliers: {"X": 0}` — both are rejected by validation (must be `> 0`). Use `constraint: {mode: "per_channel", bounds: {X: {lower_pct: 0, upper_pct: 0}, ...}}` to freeze X's spend at 0; every other channel still needs an entry in `bounds`. |
```

with:

```
| "Pause / stop channel X entirely next quarter" | `run_future_optimization`, `future.excluded_channels: ["X"]`. X's spend is forced to 0 and its budget is spent across the remaining channels ("pause TV, spend it elsewhere") — total budget unchanged. Do **not** use `planned_allocation`/`cost_multipliers` (reject `0`) or `0/0` constraint bounds (those **freeze X at its current spend**, they do not pause it). Historical `run_optimization` cannot fully exclude a channel — only future runs can. |
```

In the "Anti-patterns" section, replace the final bullet ("Reaching for a zero weight to exclude a channel …") with:

```
- **Confusing freeze with exclude.** "Pause channel X entirely" is
  `future.excluded_channels: ["X"]` in a **future** run — not
  `planned_allocation: {"X": 0}`/`cost_multipliers: {"X": 0}` (both rejected,
  must be `> 0`) and not a `0/0` `per_channel` constraint (that **freezes X at
  its current spend** — the opposite of pausing). Historical runs cannot fully
  exclude a channel; if the user needs that, plan it as a future run.
```

Leave the "locked into our search contract" row (`0/0` = freeze) unchanged — it is correct.

In the "Minimum to elicit, per flow" → "Future optimization, additionally:" list, add one bullet:

```
- Any channel to **pause entirely** next period → `excluded_channels`.
```

- [ ] **Step 2: Fix `budget-optimization.md`**

In the "Forward-looking planning" section, replace the bullet that begins "**Excluding a channel entirely is not a zero weight.**" (the one telling users to use a `per_channel` constraint with bounds frozen at 0) with:

```
- **Pausing / excluding a channel entirely** is `future.excluded_channels`, a
  list of channels to force to 0 spend for the future window. Their budget is
  reallocated across the remaining channels (total budget unchanged — "pause
  TV, spend it elsewhere"), and they still appear in the result with spend 0.
  `planned_allocation` and `cost_multipliers` both reject `0` (must be `> 0`),
  and a `0/0` `per_channel` constraint **freezes a channel at its current
  spend** rather than pausing it — so neither can exclude a channel. Only
  future runs support full exclusion; historical `run_optimization` supports
  freeze (`0/0`) but not exclusion.
```

In the `future` block field list (the "**The `future` block, in plain terms:**" list), add a new bullet after the `planned_allocation` bullet:

```
- **`excluded_channels`** (optional `list[str]`, default none) — channels to
  fully pause for the future window; their spend is forced to 0 and their share
  of the budget is reallocated across the remaining channels (total unchanged).
  Must be valid paid/RF channels and must **not** also appear in
  `planned_allocation` or `cost_multipliers`; you cannot exclude every channel.
  Example: expecting to go dark on TV next quarter → `{"excluded_channels":
  ["TV"]}`.
```

Where the scenario table / `per_channel` interpretation says "freeze = set both to 0" / "freeze a channel (bounds 0/0)", leave the **freeze** wording as-is (correct) — do not add any claim that `0/0` excludes.

- [ ] **Step 3: Add a glossary entry**

In `skills/meridian-analyst/references/glossary.md`, immediately after the "**Planned allocation (`planned_allocation`)**" entry, add:

```
**Excluded channels (`excluded_channels`)** — A list of channels to fully pause
in a future optimization: their spend is forced to 0 and reallocated across the
remaining channels (total budget unchanged). This is the *only* way to zero a
channel — a `0/0` spend constraint freezes a channel at its current spend, and
`cost_multipliers`/`planned_allocation` reject 0. Future runs only.
```

- [ ] **Step 4: Update the tool docstring**

In `src/google_meridian_mcp_server/transport/tools.py`, in the `run_future_optimization` tool docstring, add one sentence to the future-block description noting `excluded_channels`:

```
Set `future.excluded_channels` to a list of channels to fully pause (spend
forced to 0; their budget reallocates across the rest, total unchanged).
```

Locate the exact insertion point:

Run: `grep -n "def run_future_optimization\|cost_multipliers\|planned_allocation\|excluded" src/google_meridian_mcp_server/transport/tools.py`

Add the sentence adjacent to where the future block's other knobs are described.

- [ ] **Step 5: Update `AGENTS.md` (stay under 250 lines)**

Run: `grep -n "future\|excluded\|cost_multipliers\|run_future_optimization" AGENTS.md`
Run: `wc -l AGENTS.md`

In the future-optimization section, add a concise note that `future.excluded_channels` fully pauses channels (spend 0, budget reallocated, total unchanged) — the only real exclusion path; `0/0` bounds only freeze. Keep the total under 250 lines (trim an adjacent redundant phrase if needed).

Run: `wc -l AGENTS.md`
Expected: ≤ 250.

- [ ] **Step 6: Verify docs reference nothing false and commit**

Run: `grep -rn "freeze X's spend at 0\|bounds frozen at 0\|exclude.*0/0\|0/0.*exclude" skills/ AGENTS.md`
Expected: no matches (all false "exclude via 0/0" claims removed).

```bash
git add skills/meridian-analyst/references/consultation.md skills/meridian-analyst/references/budget-optimization.md skills/meridian-analyst/references/glossary.md src/google_meridian_mcp_server/transport/tools.py AGENTS.md
git commit -m "docs: excluded_channels as the real exclusion path; fix 0/0 freeze-vs-exclude"
```

---

### Task 7: Bump version to 0.3.1

**Files:**
- Modify: `pyproject.toml:7`
- Modify: `README.md:1`

**Interfaces:**
- Consumes: nothing.
- Produces: version string `0.3.1` in both the package metadata and the README title.

- [ ] **Step 1: Bump `pyproject.toml`**

Change line 7 from `version = "0.3.0"` to:

```toml
version = "0.3.1"
```

- [ ] **Step 2: Bump the README title**

Change `README.md` line 1 from `# Google Meridian MCP Server [v0.3.0]` to:

```markdown
# Google Meridian MCP Server [v0.3.1]
```

- [ ] **Step 3: Verify no stale `0.3.0` remains in these two files**

Run: `grep -rn "0\.3\.0" pyproject.toml README.md`
Expected: no matches.

- [ ] **Step 4: Confirm the package still imports at the new version**

Run: `uv run python -c "import importlib.metadata as m; print(m.version('google-meridian-mcp-server'))"`
Expected: prints `0.3.1` (run after `uv` re-syncs; if it prints `0.3.0`, run `uv sync` first, then re-run).

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml README.md
git commit -m "chore: bump version to 0.3.1"
```

---

## Final verification (after all tasks)

- [ ] Full unit + contract suite: `OPTIMIZATION_ALLOWED_TIERS=local uv run pytest tests/unit tests/contract -q` → all pass.
- [ ] Future integration: `OPTIMIZATION_ALLOWED_TIERS=local uv run pytest tests/integration/test_optimizer_facade_future.py -q` → all pass.
- [ ] Lint/format: `uv run ruff check . && uv run ruff format --check .` → clean.
- [ ] Live validation: `OPTIMIZATION_ALLOWED_TIERS=local uv run python -m scripts.validation.live_validate 2>&1 | tail -3` → `LIVE VALIDATION PASSED`, 0 failed.
- [ ] Local QA: `OPTIMIZATION_ALLOWED_TIERS=local uv run python -m scripts.qa.future_optimization_qa 2>&1 | tail -3` → `QA PASSED (N/N)`, exit 0.

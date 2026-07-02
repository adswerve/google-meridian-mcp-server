# get_spend_scenario Tool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a single-channel "what-if" spend tool (`get_spend_scenario`) that returns ROI/mROI or CPIK/mCPIK efficiency for a base spend + increment, mirroring the mmm-showcase Response Curves page.

**Architecture:** Wire `transport → service → meridian`, reusing the already-staged saturation engine (`AnalyzerFacade.apply_saturation` / `get_data` / `_get_spend_column`). Two thin new facade methods expose base-spend resolution and response lookup; the service does channel validation + efficiency arithmetic and shapes a summary object; the transport layer registers the tool.

**Tech Stack:** Python 3.11+, FastMCP, pydantic, numpy/pandas/xarray, pytest, ruff, uv.

**Spec:** `docs/superpowers/specs/2026-06-26-spend-scenario-tool-design.md`

## Global Constraints

- `base_spend` and `spend_increase` are spend **per time unit** (not totals); the tool docstring must say so.
- Measure floats are rounded to **6 significant figures** in the response (reuse `AnalysisService._round_measure`).
- Efficiency ratios with a zero denominator return **`null`** (no exception).
- Responses must be **JSON-safe** and use **deterministic key ordering**.
- **No new dependencies.** No changes to existing tools' response envelopes.
- Facade failures are wrapped as `MissingModelDataError` (no broad exception swallowing beyond that one boundary, matching existing service methods).
- Each commit message ends with the `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` trailer.
- After every task: `uv run ruff check src tests scripts` must be clean.

---

## File Structure

| File | Responsibility | Change |
|------|----------------|--------|
| `src/google_meridian_mcp_server/meridian/analyzer_facade.py` | Meridian computation | Add `resolve_base_spend`, `spend_response` (reuse staged engine unchanged) |
| `src/google_meridian_mcp_server/services/analysis_service.py` | Orchestration + shaping | Add `_safe_ratio`, `get_spend_scenario`, `_build_spend_scenario`; advertise tool in `get_model_overview` |
| `src/google_meridian_mcp_server/transport/tools.py` | Tool registration | Add `get_spend_scenario` handler |
| `tests/unit/test_analyzer_facade.py` | Facade unit tests | Add tests for the two new methods |
| `tests/unit/test_analysis_service.py` | Service unit tests | Add scenario + discovery tests |
| `tests/unit/test_transport_tools.py` | Transport unit tests | Add registration/dispatch test |
| `scripts/validation/matrix.py` | Live-validation expectations | Add `expected_outcome_mode` + unknown-channel adversarial |
| `scripts/validation/runner.py` | Live-validation driver | Add `assert_summary` + per-variant happy-path call |
| `AGENTS.md`, `docs/meridian-mcp-showcase-parity.md`, `docs/architecture-review.md` | Docs | Document the new tool |

---

## Task 1: Facade methods — `resolve_base_spend` + `spend_response`

**Files:**
- Modify: `src/google_meridian_mcp_server/meridian/analyzer_facade.py`
- Test: `tests/unit/test_analyzer_facade.py`

**Interfaces:**
- Consumes (existing, unchanged): `AnalyzerFacade.get_data(agg_geos, geos, dt_start, dt_end)`, `AnalyzerFacade._get_spend_column(channel)`, `AnalyzerFacade.apply_saturation(channel, spend, geos, dt_start, dt_end, use_kpi)`, `AnalyzerFacade._selected_geos(filters)`, `MeridianInterrogator.resolve_use_kpi(filters)`.
- Produces:
  - `resolve_base_spend(self, channel: str, filters: AnalysisFilters) -> float`
  - `spend_response(self, channel: str, spend_points: Sequence[float], filters: AnalysisFilters) -> list[dict]` where each dict is `{"mean": float, "ci_lo": float, "ci_hi": float}`, one per input spend point, in order.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_analyzer_facade.py`:

```python
def test_resolve_base_spend_returns_average_spend_per_time_unit():
    facade = AnalyzerFacade(
        SimpleNamespace(input_data=SimpleNamespace(rf_channel=None))
    )
    facade.get_data = mock.Mock(
        return_value=pd.DataFrame(
            {"search_spend": [100.0, 200.0, 300.0]},
            index=pd.Index(
                ["2024-01-01", "2024-01-08", "2024-01-15"], name="time"
            ),
        )
    )

    assert facade.resolve_base_spend("search", AnalysisFilters()) == 200.0


def test_resolve_base_spend_raises_when_spend_column_missing():
    facade = AnalyzerFacade(
        SimpleNamespace(input_data=SimpleNamespace(rf_channel=None))
    )
    facade.get_data = mock.Mock(
        return_value=pd.DataFrame(
            {"tv_spend": [1.0]},
            index=pd.Index(["2024-01-01"], name="time"),
        )
    )

    with pytest.raises(ValueError):
        facade.resolve_base_spend("search", AnalysisFilters())


def test_spend_response_zips_apply_saturation_arrays_in_order():
    facade = AnalyzerFacade(SimpleNamespace(input_data=SimpleNamespace()))
    facade.resolve_use_kpi = mock.Mock(return_value=False)
    facade.apply_saturation = mock.Mock(
        return_value=(
            np.array([10.0, 12.0]),
            np.array([9.0, 11.0]),
            np.array([11.0, 13.0]),
        )
    )

    rows = facade.spend_response("search", [100.0, 120.0], AnalysisFilters())

    assert rows == [
        {"mean": 10.0, "ci_lo": 9.0, "ci_hi": 11.0},
        {"mean": 12.0, "ci_lo": 11.0, "ci_hi": 13.0},
    ]
    assert facade.apply_saturation.call_args.kwargs["use_kpi"] is False
    assert facade.apply_saturation.call_args.args[1] == [100.0, 120.0]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_analyzer_facade.py::test_resolve_base_spend_returns_average_spend_per_time_unit tests/unit/test_analyzer_facade.py::test_spend_response_zips_apply_saturation_arrays_in_order -v`
Expected: FAIL with `AttributeError: 'AnalyzerFacade' object has no attribute 'resolve_base_spend'` / `... 'spend_response'`.

- [ ] **Step 3: Implement the two methods**

In `src/google_meridian_mcp_server/meridian/analyzer_facade.py`, add these methods to the `AnalyzerFacade` class (place them just after `apply_saturation`, before `get_response_curves`):

```python
    def resolve_base_spend(
        self, channel: str, filters: AnalysisFilters
    ) -> float:
        """Historical average spend per time unit for ``channel`` over the slice."""
        data = self.get_data(
            agg_geos=True,
            geos=self._selected_geos(filters),
            dt_start=filters.start_date.isoformat() if filters.start_date else None,
            dt_end=filters.end_date.isoformat() if filters.end_date else None,
        )
        spend_column = self._get_spend_column(channel)
        if data.empty or spend_column not in data.columns:
            raise ValueError(
                f"No spend data is available for channel '{channel}'."
            )
        time_units = len(data.index)
        return float(data[spend_column].sum()) / time_units

    def spend_response(
        self, channel: str, spend_points: Sequence[float], filters: AnalysisFilters
    ) -> list[dict]:
        """Outcome (mean/ci_lo/ci_hi) at each spend point via ``apply_saturation``."""
        mean, ci_lo, ci_hi = self.apply_saturation(
            channel,
            list(spend_points),
            geos=self._selected_geos(filters),
            dt_start=filters.start_date.isoformat() if filters.start_date else None,
            dt_end=filters.end_date.isoformat() if filters.end_date else None,
            use_kpi=self.resolve_use_kpi(filters),
        )
        return [
            {
                "mean": float(mean[i]),
                "ci_lo": float(ci_lo[i]),
                "ci_hi": float(ci_hi[i]),
            }
            for i in range(len(spend_points))
        ]
```

(`Sequence` is already imported at the top of the file; `AnalysisFilters` is already imported.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_analyzer_facade.py -v`
Expected: PASS (all, including the new three).

- [ ] **Step 5: Commit**

```bash
git add src/google_meridian_mcp_server/meridian/analyzer_facade.py tests/unit/test_analyzer_facade.py
git commit -m "feat: add resolve_base_spend and spend_response facade methods"
```

---

## Task 2: Service method — `get_spend_scenario` + efficiency math

**Files:**
- Modify: `src/google_meridian_mcp_server/services/analysis_service.py`
- Test: `tests/unit/test_analysis_service.py`

**Interfaces:**
- Consumes: `ModelCatalog.get_facade(model_id)` → object with `get_data_inputs()`, `resolve_use_kpi(filters)`, `resolve_base_spend(channel, filters)`, `spend_response(channel, spend_points, filters)`; `normalize_filters`; `MissingModelDataError`; existing `self._cached`, `self._filter_key`, `self._round_measure`.
- Produces: `AnalysisService.get_spend_scenario(self, model_id: str, channel: str, spend_increase: float, base_spend: float | None, filters) -> dict[str, Any]` returning the summary object documented in the spec (keys: `model_id`, `channel`, `channel_type`, `outcome_mode`, `base_spend`, `spend_increase`, `new_spend`, `spend_increase_pct`, `base_outcome`, `new_outcome`, `expected_outcome_increase`, `expected_outcome_increase_pct`, `efficiency`, `marginal_efficiency`, `efficiency_at_new`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_analysis_service.py`:

```python
class _FakeScenarioFacade:
    def __init__(self, *, has_revenue=True, base_spend=100.0, outcomes=None):
        self._has_revenue = has_revenue
        self._base_spend = base_spend
        self._outcomes = outcomes or [
            {"mean": 400.0, "ci_lo": 350.0, "ci_hi": 450.0},
            {"mean": 460.0, "ci_lo": 400.0, "ci_hi": 520.0},
        ]
        self.spend_response_calls = []

    def get_data_inputs(self):
        return {"media": ["search", "tv"], "rf_media": ["youtube"]}

    def resolve_use_kpi(self, filters):
        return not self._has_revenue

    def resolve_base_spend(self, channel, filters):
        return self._base_spend

    def spend_response(self, channel, spend_points, filters):
        self.spend_response_calls.append(list(spend_points))
        return self._outcomes


class _FakeScenarioCatalog:
    def __init__(self, facade):
        self._facade = facade

    def get_facade(self, model_id):
        return self._facade


def _scenario_service(facade, *, cache_enabled=False) -> AnalysisService:
    return AnalysisService(
        catalog=_FakeScenarioCatalog(facade),
        result_cache=ResultCache(enabled=cache_enabled, ttl_seconds=None),
    )


def test_spend_scenario_revenue_mode_computes_roi_family():
    facade = _FakeScenarioFacade(has_revenue=True, base_spend=100.0)
    result = _scenario_service(facade).get_spend_scenario(
        "m1", "search", 20.0, None, None
    )
    assert result["outcome_mode"] == "revenue"
    assert result["channel_type"] == "paid_media"
    assert result["base_spend"] == 100.0
    assert result["new_spend"] == 120.0
    assert result["efficiency"] == 4.0
    assert result["marginal_efficiency"] == 3.0
    assert result["efficiency_at_new"] == pytest.approx(3.83333, rel=1e-4)
    assert result["expected_outcome_increase"] == 60.0
    assert result["base_outcome"] == {"mean": 400.0, "ci_lo": 350.0, "ci_hi": 450.0}
    assert facade.spend_response_calls == [[100.0, 120.0]]


def test_spend_scenario_kpi_mode_computes_cpik_family():
    facade = _FakeScenarioFacade(has_revenue=False, base_spend=100.0)
    result = _scenario_service(facade).get_spend_scenario(
        "m1", "search", 20.0, None, None
    )
    assert result["outcome_mode"] == "kpi"
    assert result["efficiency"] == 0.25
    assert result["marginal_efficiency"] == pytest.approx(0.333333, rel=1e-4)
    assert result["efficiency_at_new"] == pytest.approx(0.26087, rel=1e-4)


def test_spend_scenario_uses_provided_base_spend():
    facade = _FakeScenarioFacade(has_revenue=True, base_spend=999.0)
    result = _scenario_service(facade).get_spend_scenario(
        "m1", "search", 20.0, 50.0, None
    )
    assert result["base_spend"] == 50.0
    assert result["new_spend"] == 70.0
    assert facade.spend_response_calls == [[50.0, 70.0]]


def test_spend_scenario_rejects_unknown_channel():
    facade = _FakeScenarioFacade()
    with pytest.raises(MissingModelDataError):
        _scenario_service(facade).get_spend_scenario("m1", "nope", 20.0, None, None)


def test_spend_scenario_rejects_non_positive_base_spend():
    facade = _FakeScenarioFacade()
    with pytest.raises(MissingModelDataError):
        _scenario_service(facade).get_spend_scenario("m1", "search", 20.0, 0.0, None)


def test_spend_scenario_zero_lift_yields_null_efficiency():
    facade = _FakeScenarioFacade(
        has_revenue=False,
        base_spend=100.0,
        outcomes=[
            {"mean": 0.0, "ci_lo": 0.0, "ci_hi": 0.0},
            {"mean": 0.0, "ci_lo": 0.0, "ci_hi": 0.0},
        ],
    )
    result = _scenario_service(facade).get_spend_scenario(
        "m1", "search", 20.0, None, None
    )
    assert result["efficiency"] is None
    assert result["marginal_efficiency"] is None


def test_spend_scenario_caches_result():
    facade = _FakeScenarioFacade(has_revenue=True)
    service = _scenario_service(facade, cache_enabled=True)
    first = service.get_spend_scenario("m1", "search", 20.0, None, None)
    second = service.get_spend_scenario("m1", "search", 20.0, None, None)
    assert first == second
    assert len(facade.spend_response_calls) == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_analysis_service.py -k spend_scenario -v`
Expected: FAIL with `AttributeError: 'AnalysisService' object has no attribute 'get_spend_scenario'`.

- [ ] **Step 3: Implement the service method + helpers**

In `src/google_meridian_mcp_server/services/analysis_service.py`, add a static helper and the public method to the `AnalysisService` class. Place `_safe_ratio` next to `_round_measure`, and `get_spend_scenario` after `get_model_fit`:

```python
    @staticmethod
    def _safe_ratio(numerator: float, denominator: float) -> float | None:
        if not denominator:
            return None
        return numerator / denominator

    def get_spend_scenario(
        self,
        model_id: str,
        channel: str,
        spend_increase: float,
        base_spend: float | None,
        filters: AnalysisFilters | dict | None,
    ) -> dict[str, Any]:
        normalized_filters = normalize_filters(filters)
        facade = self._catalog.get_facade(model_id)

        data_inputs = facade.get_data_inputs()
        if channel in data_inputs["media"]:
            channel_type = "paid_media"
        elif channel in data_inputs["rf_media"]:
            channel_type = "rf"
        else:
            raise MissingModelDataError(
                model_id,
                f"channel '{channel}' is not a paid media or RF channel",
            )

        if base_spend is not None and base_spend <= 0:
            raise MissingModelDataError(
                model_id, "base_spend must be a positive number"
            )

        outcome_mode = "kpi" if facade.resolve_use_kpi(normalized_filters) else "revenue"
        params = {
            "channel": channel,
            "spend_increase": spend_increase,
            "base_spend": base_spend,
            "filters": self._filter_key(normalized_filters),
        }

        def _compute() -> dict[str, Any]:
            try:
                resolved_base = (
                    base_spend
                    if base_spend is not None
                    else facade.resolve_base_spend(channel, normalized_filters)
                )
                new_spend = resolved_base + spend_increase
                outcomes = facade.spend_response(
                    channel, [resolved_base, new_spend], normalized_filters
                )
            except Exception as exc:
                raise MissingModelDataError(model_id, str(exc)) from exc

            return self._build_spend_scenario(
                model_id=model_id,
                channel=channel,
                channel_type=channel_type,
                outcome_mode=outcome_mode,
                base_spend=resolved_base,
                spend_increase=spend_increase,
                new_spend=new_spend,
                base_outcome=outcomes[0],
                new_outcome=outcomes[1],
            )

        return self._cached("get_spend_scenario", model_id, params, _compute)

    def _build_spend_scenario(
        self,
        *,
        model_id: str,
        channel: str,
        channel_type: str,
        outcome_mode: str,
        base_spend: float,
        spend_increase: float,
        new_spend: float,
        base_outcome: dict[str, Any],
        new_outcome: dict[str, Any],
    ) -> dict[str, Any]:
        b = base_outcome["mean"]
        n = new_outcome["mean"]
        delta = n - b
        if outcome_mode == "revenue":
            efficiency = self._safe_ratio(b, base_spend)
            marginal_efficiency = self._safe_ratio(delta, spend_increase)
            efficiency_at_new = self._safe_ratio(n, new_spend)
        else:
            efficiency = self._safe_ratio(base_spend, b)
            marginal_efficiency = self._safe_ratio(spend_increase, delta)
            efficiency_at_new = self._safe_ratio(new_spend, n)

        summary = {
            "model_id": model_id,
            "channel": channel,
            "channel_type": channel_type,
            "outcome_mode": outcome_mode,
            "base_spend": base_spend,
            "spend_increase": spend_increase,
            "new_spend": new_spend,
            "spend_increase_pct": self._safe_ratio(100.0 * spend_increase, base_spend),
            "base_outcome": base_outcome,
            "new_outcome": new_outcome,
            "expected_outcome_increase": delta,
            "expected_outcome_increase_pct": self._safe_ratio(100.0 * delta, b),
            "efficiency": efficiency,
            "marginal_efficiency": marginal_efficiency,
            "efficiency_at_new": efficiency_at_new,
        }
        return {key: self._round_value(value) for key, value in summary.items()}

    @classmethod
    def _round_value(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return {key: cls._round_value(inner) for key, inner in value.items()}
        return cls._round_measure(value)
```

(`normalize_filters`, `MissingModelDataError`, and `Any` are already imported in this module.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_analysis_service.py -k spend_scenario -v`
Expected: PASS (all 7).

- [ ] **Step 5: Commit**

```bash
git add src/google_meridian_mcp_server/services/analysis_service.py tests/unit/test_analysis_service.py
git commit -m "feat: add get_spend_scenario service method with efficiency math"
```

---

## Task 3: Advertise the tool in `get_model_overview`

**Files:**
- Modify: `src/google_meridian_mcp_server/services/analysis_service.py:236-273` (the `get_model_overview` method)
- Test: `tests/unit/test_analysis_service.py`

**Interfaces:**
- Consumes: existing `get_model_overview` overview dict (already contains `media_channels` and `rf_channels`).
- Produces: `overview["available_tool_options"]["get_spend_scenario"] == {"channel": media_channels + rf_channels}`.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_analysis_service.py`:

```python
class _FakeOverviewCatalog:
    def __init__(self, overview):
        self._overview = overview

    def get_interrogator(self, model_id):
        snapshot = dict(self._overview)
        return SimpleNamespace(get_model_overview=lambda: snapshot)


def test_overview_advertises_spend_scenario_channels():
    overview = {
        "available_training_datasets": ["media_spend"],
        "has_revenue_per_kpi": True,
        "media_channels": ["search", "tv"],
        "rf_channels": ["youtube"],
    }
    service = AnalysisService(
        catalog=_FakeOverviewCatalog(overview),
        result_cache=ResultCache(enabled=False, ttl_seconds=None),
    )
    result = service.get_model_overview("m1")
    assert result["available_tool_options"]["get_spend_scenario"] == {
        "channel": ["search", "tv", "youtube"]
    }
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/unit/test_analysis_service.py::test_overview_advertises_spend_scenario_channels -v`
Expected: FAIL with `KeyError: 'get_spend_scenario'`.

- [ ] **Step 3: Add the advertisement**

In `src/google_meridian_mcp_server/services/analysis_service.py`, inside `get_model_overview`'s `_compute`, add the entry to the `available_tool_options` dict literal. Locate the existing block:

```python
                "get_channel_data": {},
                "get_model_fit": {},
            }
```

and change it to:

```python
                "get_channel_data": {},
                "get_model_fit": {},
                "get_spend_scenario": {
                    "channel": overview["media_channels"] + overview["rf_channels"],
                },
            }
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/unit/test_analysis_service.py::test_overview_advertises_spend_scenario_channels -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/google_meridian_mcp_server/services/analysis_service.py tests/unit/test_analysis_service.py
git commit -m "feat: advertise get_spend_scenario in model overview"
```

---

## Task 4: Register the `get_spend_scenario` transport tool

**Files:**
- Modify: `src/google_meridian_mcp_server/transport/tools.py`
- Test: `tests/unit/test_transport_tools.py`

**Interfaces:**
- Consumes: `AnalysisService.get_spend_scenario(model_id, channel, spend_increase, base_spend, filters)`, `normalize_filters`, `_error_response`, `_analysis_service`.
- Produces: a FastMCP tool `get_spend_scenario(model_id, channel, spend_increase, ctx, base_spend=None, filters=None)` returning the summary dict or an error payload.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_transport_tools.py` (inside the module, after the existing tests). This reuses the `_FakeFastMCP` pattern already in the file:

```python
@pytest.mark.asyncio
async def test_register_tools_exposes_get_spend_scenario(
    monkeypatch: pytest.MonkeyPatch,
):
    mcp = _FakeFastMCP()
    captured = {}

    def _get_spend_scenario(model_id, channel, spend_increase, base_spend, filters):
        captured["args"] = (model_id, channel, spend_increase, base_spend)
        captured["filters"] = filters
        return {"model_id": model_id, "channel": channel, "outcome_mode": "revenue"}

    analysis_service = SimpleNamespace(get_spend_scenario=_get_spend_scenario)
    monkeypatch.setattr(
        tools_module, "_analysis_service", lambda ctx: analysis_service
    )

    tools_module.register_tools(mcp)
    ctx = SimpleNamespace(lifespan_context={})

    result = await mcp.tools["get_spend_scenario"]("m1", "search", 1000.0, ctx)

    assert result == {
        "model_id": "m1",
        "channel": "search",
        "outcome_mode": "revenue",
    }
    assert captured["args"] == ("m1", "search", 1000.0, None)
    assert isinstance(captured["filters"], AnalysisFilters)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/unit/test_transport_tools.py::test_register_tools_exposes_get_spend_scenario -v`
Expected: FAIL with `KeyError: 'get_spend_scenario'`.

- [ ] **Step 3: Register the tool**

In `src/google_meridian_mcp_server/transport/tools.py`, add this handler inside `register_tools`, after the `get_model_fit` tool (the last one in the function):

```python
    @mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
    async def get_spend_scenario(
        model_id: Annotated[
            str,
            Field(
                min_length=1,
                description="Model identifier from list_models (e.g. 'geo-revenue').",
            ),
        ],
        channel: Annotated[
            str,
            Field(
                min_length=1,
                description="A single paid-media or RF channel to simulate. Valid values are in get_model_overview 'available_tool_options.get_spend_scenario.channel'.",
            ),
        ],
        spend_increase: Annotated[
            float,
            Field(
                ge=0,
                description="Extra spend PER TIME UNIT to add on top of base spend. Use 0 to get base-only efficiency.",
            ),
        ],
        ctx: Context,
        base_spend: Annotated[
            float | None,
            Field(
                gt=0,
                description="Base spend PER TIME UNIT for the channel. Omit to default to the channel's historical average over the selected date/geo slice.",
            ),
        ] = None,
        filters: Annotated[
            AnalysisFilters | None,
            Field(
                description="Optional filters: start_date/end_date/geos slice the model; use_kpi selects the efficiency family (defaults to the model's capability).",
            ),
        ] = None,
    ) -> dict[str, Any]:
        """Simulate adding spend to one channel: returns expected outcome lift and efficiency (ROI/mROI for revenue models, CPIK/mCPIK otherwise) at the base and increased spend levels. Spend is PER TIME UNIT. Use this to answer 'what happens to ROI if I add $X per week to search?'."""
        try:
            return _analysis_service(ctx).get_spend_scenario(
                model_id,
                channel,
                spend_increase,
                base_spend,
                normalize_filters(filters),
            )
        except MeridianMcpError as error:
            return _error_response(error)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_transport_tools.py -v`
Expected: PASS (all, including the new test).

- [ ] **Step 5: Verify the full unit + contract suite is green**

Run: `uv run pytest tests/unit tests/contract -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/google_meridian_mcp_server/transport/tools.py tests/unit/test_transport_tools.py
git commit -m "feat: register get_spend_scenario MCP tool"
```

---

## Task 5: Wire `get_spend_scenario` into the live validation suite

**Files:**
- Modify: `scripts/validation/matrix.py`
- Modify: `scripts/validation/runner.py`
- Test: the live validation suite itself (`scripts/validation/live_validate.py`)

**Interfaces:**
- Consumes: `generate_validation_models.VARIANTS` (each has `.key`, `.with_rf`, `.factory_has_revenue()`), the in-process `Client(mcp)`, `get_model_overview` (exposes `media_channels` / `rf_channels`).
- Produces: `matrix.expected_outcome_mode(variant) -> str`; a new `runner.assert_summary(...)`; a per-variant happy-path call and an unknown-channel adversarial case.

- [ ] **Step 1: Add the matrix helper + adversarial case**

In `scripts/validation/matrix.py`, add this function after `expected_valid`:

```python
def expected_outcome_mode(variant) -> str:
    """Default outcome mode for get_spend_scenario on this variant."""
    return "revenue" if variant.factory_has_revenue() else "kpi"
```

Then, in `adversarial_cases`, add an unknown-channel case for every variant. Change the end of the function from:

```python
    if not variant.with_rf:
        cases.append(
            AdversarialCase(
                "get_reach_frequency",
                {"model_id": variant.key},
                "metric_not_supported",
            )
        )
    return cases
```

to:

```python
    if not variant.with_rf:
        cases.append(
            AdversarialCase(
                "get_reach_frequency",
                {"model_id": variant.key},
                "metric_not_supported",
            )
        )
    cases.append(
        AdversarialCase(
            "get_spend_scenario",
            {
                "model_id": variant.key,
                "channel": "__no_such_channel__",
                "spend_increase": 1.0,
            },
            "missing_model_data",
        )
    )
    return cases
```

- [ ] **Step 2: Add the `assert_summary` helper to the runner**

In `scripts/validation/runner.py`, add this function after `assert_error`:

```python
def assert_summary(payload, label: str, *, required_keys, outcome_mode: str) -> None:
    assert isinstance(payload, dict), f"{label}: expected dict, got {type(payload)}"
    assert "error_code" not in payload, f"{label}: unexpected error {payload}"
    for key in required_keys:
        assert key in payload, f"{label}: missing '{key}'"
    assert payload["outcome_mode"] == outcome_mode, (
        f"{label}: outcome_mode {payload['outcome_mode']} != {outcome_mode}"
    )
```

- [ ] **Step 3: Add the per-variant happy-path call in `run_matrix`**

In `scripts/validation/runner.py`, inside `run_matrix`, the per-variant loop already fetches `overview` at the top and has a block for `get_model_fit` / `get_channel_data`. Immediately after that `for tool in ("get_model_fit", "get_channel_data"):` block (and before the `if variant.with_rf:` block), insert:

```python
        # Spend scenario: derive a channel from the overview, assert summary shape.
        channel_pool = overview.get("media_channels") or overview.get("rf_channels")
        if channel_pool:
            label = f"{model_id}/get_spend_scenario"
            try:
                payload = await call(
                    client,
                    "get_spend_scenario",
                    {
                        "model_id": model_id,
                        "channel": channel_pool[0],
                        "spend_increase": 1000.0,
                    },
                )
                assert_summary(
                    payload,
                    label,
                    required_keys=(
                        "model_id",
                        "channel",
                        "channel_type",
                        "outcome_mode",
                        "base_spend",
                        "new_spend",
                        "base_outcome",
                        "new_outcome",
                        "efficiency",
                        "marginal_efficiency",
                        "efficiency_at_new",
                    ),
                    outcome_mode=matrix.expected_outcome_mode(variant),
                )
                report.ok(label)
            except AssertionError as exc:
                report.fail(label, str(exc))
```

Note: `overview` is fetched in a `try/except` at the top of the loop; if that assertion block already failed, `overview` is still bound to the returned dict, so `.get(...)` is safe.

- [ ] **Step 4: Run the live validation suite**

Run: `uv run python -m scripts.validation.live_validate`
Expected: the matrix prints `get_spend_scenario` rows (7 happy-path + 7 adversarial `ADV/get_spend_scenario[...]->missing_model_data`) and ends with `LIVE VALIDATION PASSED`.

> NOTE: if `models/_validation/` is empty this first builds the dummy fixtures via tiny real MCMC fits — it takes a few minutes and is NOT a hang.

- [ ] **Step 5: Run the matrix/runner unit tests**

Run: `uv run pytest tests/unit/test_validation_matrix.py tests/unit/test_validation_runner.py -v`
Expected: PASS. If a test asserts the exact count of adversarial cases per variant, update it to include the new unknown-channel case (one extra per variant).

- [ ] **Step 6: Commit**

```bash
git add scripts/validation/matrix.py scripts/validation/runner.py tests/unit/test_validation_matrix.py tests/unit/test_validation_runner.py
git commit -m "test: cover get_spend_scenario in the live validation matrix"
```

---

## Task 6: Documentation

**Files:**
- Modify: `AGENTS.md`
- Modify: `docs/meridian-mcp-showcase-parity.md`
- Modify: `docs/architecture-review.md`

**Interfaces:** none (docs only).

- [ ] **Step 1: Update `AGENTS.md`**

1. In the **Current Tool Surface** list, add `- get_spend_scenario` after `- get_channel_data`.
2. In the **Current Analysis Behavior** section, add a bullet:

```markdown
- `get_spend_scenario` simulates one channel's spend: inputs `channel`,
  `spend_increase`, optional `base_spend` (all PER TIME UNIT; base defaults to
  the channel's historical average over the slice), returns a summary object
  with `outcome_mode` (`revenue`|`kpi`) and an efficiency triplet
  (`efficiency`/`marginal_efficiency`/`efficiency_at_new` = ROI/mROI/ROI-at-new
  for revenue models, CPIK/mCPIK otherwise). Zero-denominator ratios return
  `null`. It activates the previously-staged saturation engine
  (`apply_saturation`/`get_data`); `get_carryover` remains unused.
```

3. In the **Live Validation & Dummy Models** section, add to the expectation rules:

```markdown
  `get_spend_scenario` is valid on every variant (channel derived from the
  overview); `outcome_mode` is `revenue` for revenue/kpi+rpk variants and `kpi`
  for kpi-only; an unknown channel returns `missing_model_data`.
```

- [ ] **Step 2: Update `docs/meridian-mcp-showcase-parity.md`**

Mark the Response Curves what-if scenario as covered by `get_spend_scenario` (change its status to parity-achieved and reference the tool). Match the file's existing table/section format.

- [ ] **Step 3: Update `docs/architecture-review.md`**

In §6.1 and §6.2, note that the staged saturation engine (`apply_saturation`, `get_data` + extractors, `_get_spend_column`, `_interpolate_with_extrapolation`) is now **live** via `get_spend_scenario` and is no longer a deletion candidate. Leave `get_carryover` flagged as the one remaining staged-but-unused method.

- [ ] **Step 4: Final full verification**

Run: `uv run pytest`
Expected: PASS.

Run: `uv run ruff check src tests scripts`
Expected: clean.

Run: `uv run ruff format --check src tests scripts`
Expected: clean (run `uv run ruff format src tests scripts` if not).

- [ ] **Step 5: Commit**

```bash
git add AGENTS.md docs/meridian-mcp-showcase-parity.md docs/architecture-review.md
git commit -m "docs: document get_spend_scenario tool and parity"
```

---

## Self-Review Notes (for the planner — not an execution step)

- **Spec coverage:** §2 contract → Tasks 2/4; §3.1 facade → Task 1; §3.2 service math → Task 2; §3.3 discovery → Task 3; §4 use_kpi rule → Task 2 (`outcome_mode`); §5/§5.1 live validation → Task 5; §6 docs → Task 6. All covered.
- **Type consistency:** `resolve_base_spend`/`spend_response`/`get_spend_scenario`/`_safe_ratio`/`_build_spend_scenario`/`_round_value`/`expected_outcome_mode`/`assert_summary` names are used identically across tasks.
- **Output keys** in the Task 2 summary, the Task 5 `required_keys`, and the spec §2 contract match.

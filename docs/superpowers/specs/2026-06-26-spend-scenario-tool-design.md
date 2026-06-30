# Design — `get_spend_scenario` tool

**Date:** 2026-06-26
**Status:** Proposed (awaiting review)
**Topic:** A single-channel "what-if" spend tool for the Meridian MCP server,
mirroring the mmm-showcase Response Curves page.

---

## 1. Motivation

The mmm-showcase Response Curves page lets a user pick a channel, date range,
geo, a base spend level, and an extra "what if I added $X" increment, and see
the resulting efficiency (ROI/mROI or CPIK/mCPIK) and expected lift. The MCP
server has no equivalent: `get_response_curves` returns the raw curve points,
but nothing answers the decision question *"if I add $X to this channel, what
happens to ROI?"*.

The engine that powers the showcase page was already **ported into this server
but never wired to a tool** — it currently sits unused on the Meridian
adapters:

- `AnalyzerFacade.apply_saturation`, `_get_spend_column`,
  `_interpolate_with_extrapolation`
- `MeridianInterrogator.get_data` and its private per-channel extractors

This work activates that staged engine behind a new tool rather than deleting
it. (See the architecture review, `docs/architecture-review.md`, §6.1–6.2.)

### Out of scope / explicit non-goals

- **Curve points / plotting data** — covered by the existing
  `get_response_curves` / `get_response_curve_summary`. This tool returns a
  summary object only.
- **Multiple channels per call** — single channel only. Agents loop to compare.
- **Percentage / multiplier spend inputs** — spend is expressed in absolute
  dollars.
- **Both efficiency families in one call** — one family per call, chosen by
  `use_kpi` (see §4).
- **`get_carryover`** — also staged-but-unused, but NOT needed by this tool
  (the showcase uses it on a different, adstock page). It remains unused after
  this work. Keep-or-delete is a **separate decision**, tracked in the
  architecture review, not part of this spec.

---

## 2. Tool contract

**Tool name:** `get_spend_scenario` (read-only, idempotent).

### Inputs

| Param | Type | Required | Notes |
|-------|------|----------|-------|
| `model_id` | `str` | yes | Model identifier from `list_models`. |
| `channel` | `str` | yes | A single **paid media or RF** channel that has a spend column. |
| `spend_increase` | `float` | yes | Extra spend **per time unit** added on top of base. May be 0 (returns base-only efficiency; marginal fields become `null`). Negative is rejected. |
| `base_spend` | `float \| None` | no | Base spend **per time unit**. Defaults to the historical average spend for the channel over the selected slice. Must be `> 0` when provided. |
| `filters` | `AnalysisFilters \| None` | no | `start_date` / `end_date` / `geos` slice the model; `use_kpi` selects the efficiency family (defaults to model capability). `channels`, `aggregate_times`, `include_non_paid` are not used by this tool. |

**Per-time-unit semantics (critical).** `base_spend` and `spend_increase` are
spend *per time unit* (e.g. per week), matching the showcase, where
`apply_saturation` normalizes outcome by the number of time units in the slice.
An agent reasoning in *total* spend over the range would be off by the period
count. The tool description string MUST state "per time unit" explicitly.

### Output

A single JSON object (floats rounded to 6 significant figures, as in every
other tool):

```jsonc
{
  "model_id": "geo-revenue",
  "channel": "search",
  "channel_type": "paid_media",          // or "rf"
  "outcome_mode": "revenue",             // "revenue" | "kpi"
  "base_spend": 10000.0,
  "spend_increase": 2000.0,
  "new_spend": 12000.0,
  "spend_increase_pct": 20.0,
  "base_outcome": { "mean": 40000.0, "ci_lo": 35000.0, "ci_hi": 45000.0 },
  "new_outcome":  { "mean": 46000.0, "ci_lo": 40000.0, "ci_hi": 52000.0 },
  "expected_outcome_increase": 6000.0,
  "expected_outcome_increase_pct": 15.0,
  "efficiency": 4.0,                      // ROI at base spend (revenue mode)
  "marginal_efficiency": 3.0,            // mROI over the increment
  "efficiency_at_new": 3.8333            // ROI at new spend
}
```

**Field naming by `outcome_mode`:** in `revenue` mode the three efficiency
fields are ROI-family values (`efficiency` = ROI, `marginal_efficiency` = mROI,
`efficiency_at_new` = ROI at new spend). In `kpi` mode they are CPIK-family
values (CPIK / mCPIK / CPIK at new spend). The **keys stay the same**
(`efficiency`, `marginal_efficiency`, `efficiency_at_new`); `outcome_mode`
tells the agent how to read them. This keeps the response schema stable while
remaining honest about units.

> Rationale for stable keys over `roi`/`cpik`-named keys: a fixed schema is
> easier for agents to parse, and `outcome_mode` already disambiguates. The
> showcase shows one family at a time for the same reason.

### Error behavior

| Condition | Error |
|-----------|-------|
| Unknown channel, or channel is not paid/RF (no spend column) | `MissingModelDataError` (model_id, reason) |
| `base_spend` provided but `<= 0` | `MissingModelDataError` (invalid base spend) |
| `spend_increase < 0` | pydantic validation rejects at the transport boundary |
| Empty data slice (filters select no rows) | `MissingModelDataError` |
| Any Meridian/`apply_saturation` failure | wrapped as `MissingModelDataError` |

Divide-by-zero in the efficiency math (zero base spend after defaulting, or
zero lift) does **not** raise — the affected field is returned as `null`,
matching the showcase's "N/A" handling.

---

## 3. Architecture & data flow

Wiring follows the established `transport → service → meridian` pattern. The
only new Meridian code is two thin facade methods that reuse the staged engine
verbatim.

```
agent → transport.get_spend_scenario(model_id, channel, spend_increase, base_spend, filters)
  → AnalysisService.get_spend_scenario(...)
      1. normalize_filters(filters)
      2. facade = catalog.get_facade(model_id)
      3. validate channel ∈ (media ∪ rf_media)         via interrogator.get_data_inputs()
      4. resolve use_kpi                                via facade.resolve_use_kpi(filters)
      5. ResultCache.get("get_spend_scenario", model_id, params)   → return on hit
      6. base_spend = base_spend or facade.resolve_base_spend(channel, filters)
      7. outcomes = facade.spend_response(channel, [base_spend, base_spend + spend_increase], filters)
      8. compute efficiency triplet (pure arithmetic, in the service)
      9. assemble + round summary
     10. ResultCache.put(...) ; return
```

### 3.1 New facade methods (`meridian/analyzer_facade.py`)

Both reuse existing, currently-unused code without modifying it.

```python
def resolve_base_spend(self, channel: str, filters: AnalysisFilters) -> float:
    """Historical average spend per time unit for `channel` over the slice."""
    data = self.get_data(
        agg_geos=True,
        geos=self._selected_geos(filters),
        dt_start=filters.start_date.isoformat() if filters.start_date else None,
        dt_end=filters.end_date.isoformat() if filters.end_date else None,
    )
    spend_column = self._get_spend_column(channel)   # raises if absent
    if data.empty or spend_column not in data.columns:
        raise ValueError(f"No spend data available for channel '{channel}'.")
    time_units = len(data.index)
    return float(data[spend_column].sum()) / time_units

def spend_response(
    self, channel: str, spend_points: Sequence[float], filters: AnalysisFilters
) -> list[dict]:
    """Outcome (mean/ci_lo/ci_hi) at each spend point, via apply_saturation."""
    mean, ci_lo, ci_hi = self.apply_saturation(
        channel,
        spend_points,
        geos=self._selected_geos(filters),
        dt_start=filters.start_date.isoformat() if filters.start_date else None,
        dt_end=filters.end_date.isoformat() if filters.end_date else None,
        use_kpi=self.resolve_use_kpi(filters),
    )
    return [
        {"mean": float(mean[i]), "ci_lo": float(ci_lo[i]), "ci_hi": float(ci_hi[i])}
        for i in range(len(spend_points))
    ]
```

`apply_saturation`, `get_data`, `_get_spend_column`,
`_interpolate_with_extrapolation` are unchanged.

### 3.2 Service method (`services/analysis_service.py`)

`get_spend_scenario(self, model_id, channel, spend_increase, base_spend, filters)`:

- Resolves the facade; validates `channel` is in `media ∪ rf_media` (else
  `MissingModelDataError`); determines `channel_type` (`paid_media` vs `rf`).
- Builds the cache `params` from `channel`, `spend_increase`, `base_spend`, and
  the filter key; wraps the compute in `self._cached("get_spend_scenario", ...)`.
- Inside compute: default `base_spend` via `facade.resolve_base_spend`;
  `outcomes = facade.spend_response(channel, [base, new], filters)`;
  compute the efficiency triplet; assemble the summary dict.
- Wraps any facade `Exception` as `MissingModelDataError`, like the other
  service methods.

**Efficiency arithmetic** (let `b = base_outcome.mean`, `n = new_outcome.mean`,
`s0 = base_spend`, `inc = spend_increase`, `s1 = new_spend`). A small helper
returns `None` on a zero denominator instead of raising.

| Field | revenue mode (ROI) | kpi mode (CPIK) |
|-------|--------------------|-----------------|
| `efficiency` | `b / s0` | `s0 / b` |
| `marginal_efficiency` | `(n − b) / inc` | `inc / (n − b)` |
| `efficiency_at_new` | `n / s1` | `s1 / n` |
| `expected_outcome_increase` | `n − b` | `n − b` |
| `expected_outcome_increase_pct` | `100 * (n / b − 1)` | `100 * (n / b − 1)` |
| `spend_increase_pct` | `100 * inc / s0` | `100 * inc / s0` |

When `spend_increase == 0`: `new_outcome` equals `base_outcome`, so
`expected_outcome_increase == 0` and `expected_outcome_increase_pct == 0`;
`marginal_efficiency` is `null` (its denominator `inc` is zero);
`spend_increase_pct == 0`. `efficiency` and `efficiency_at_new` are still
computed (and are equal, since `new_spend == base_spend`).

### 3.3 Discovery (`get_model_overview`)

Add `get_spend_scenario` to `available_tool_options` so agents discover it.
It has no `output_type`; surface the valid channels as a hint:

```python
overview["available_tool_options"]["get_spend_scenario"] = {
    "channel": media_channels + rf_channels,
}
```

(Available on all models — every fitted model has at least one paid/RF channel
with spend. If a model somehow has none, omit the entry.)

---

## 4. The `use_kpi` / outcome-mode rule

- `outcome_mode = "kpi"` when `resolve_use_kpi(filters)` is true, else
  `"revenue"`.
- `resolve_use_kpi` (existing) returns `filters.use_kpi` when set, else
  `not has_revenue_per_kpi()` — i.e. revenue models default to revenue/ROI,
  no-revenue models default to KPI/CPIK.
- No-revenue models therefore always return CPIK-family values; passing
  `use_kpi=false` on a no-revenue model still yields KPI mode (there is no
  revenue to denominate ROI). This is consistent with the rest of the server,
  where `roi`/`marginal_roi` raise `metric_not_supported` for no-revenue
  models — here the analogous behavior is "you get CPIK."

> Note: unlike `get_channel_summary`, this tool does **not** raise
> `metric_not_supported`; it always returns the family appropriate to the
> resolved mode. ROI simply isn't offered for no-revenue models because the
> mode resolves to KPI.

---

## 5. Testing

Follow the existing tiers and fakes (xarray/pandas fakes for Meridian, mocks at
import boundaries).

**`tests/unit/test_analyzer_facade.py`**
- `resolve_base_spend` returns total/time-units for a known spend column;
  raises on a missing column; raises on an empty slice.
- `spend_response` zips `apply_saturation`'s three arrays into per-point dicts
  of the right length.
- (Existing `apply_saturation` / `get_data` / `_get_spend_column` /
  `_interpolate_with_extrapolation` tests now exercise live code.)

**`tests/unit/test_analysis_service.py`**
- Revenue model happy path: mocked facade outcomes → correct ROI/mROI/ROI-at-new
  and lift fields; `outcome_mode == "revenue"`.
- KPI model: `outcome_mode == "kpi"`, CPIK/mCPIK fields correct.
- `base_spend` omitted → `resolve_base_spend` is consulted; provided → used as-is.
- Unknown / non-paid channel → `MissingModelDataError`.
- Divide-by-zero (zero lift) → affected field is `null`, no exception.
- `spend_increase == 0` → marginal fields handled per §3.2.
- Result is cached (second call hits `ResultCache`).

**`tests/contract/test_analysis_tools.py`**
- `get_spend_scenario` is registered with READ_ONLY annotations and the
  documented summary shape (no `output_type`, no `columns`/`rows` envelope —
  this tool returns a nested summary object, like `get_model_overview`).

**Live validation suite** — see §5.1 for the concrete script changes. The
suite is the integration acceptance gate (`uv run python -m
scripts.validation.live_validate`); this tool MUST be exercised on every
variant in the matrix, valid and adversarial, before the work is considered
done.

### 5.1 Live validation suite changes (concrete)

The suite drives an in-process `Client(mcp)` over all 7 fixtures
(`scripts/generate_validation_models.py::VARIANTS`). `get_spend_scenario` is a
single-call tool with bespoke args (a `channel` + `spend_increase`), so it is
wired like the other non-`output_type` tools (`get_model_fit`,
`get_channel_data`, `get_reach_frequency`) rather than added to
`matrix.ANALYSIS_TOOLS`.

**Channel selection — do NOT hardcode fixture channel names.** The factory
channel names are an implementation detail of Meridian's `test_utils`. The
runner already fetches `get_model_overview` per variant; reuse it and pick
`channel = (overview["media_channels"] or overview["rf_channels"])[0]`. Every
fixture has ≥1 paid media channel (`N_MEDIA_CHANNELS = 3`), so this always
resolves.

**Expected `outcome_mode` per variant.** `resolve_use_kpi` defaults to
`not has_revenue_per_kpi()`, so the default mode tracks
`variant.factory_has_revenue()` exactly:
- `revenue` and `kpi_rpk` variants → `outcome_mode == "revenue"` (ROI family)
- `kpi_only` variants → `outcome_mode == "kpi"` (CPIK family)

**`scripts/validation/matrix.py`:**
- Add `def expected_outcome_mode(variant) -> str:` returning
  `"revenue" if variant.factory_has_revenue() else "kpi"`.
- In `adversarial_cases(variant)`, append (for every variant) an unknown-channel
  case so the existing adversarial loop picks it up with no runner change:
  ```python
  AdversarialCase(
      "get_spend_scenario",
      {"model_id": variant.key, "channel": "__no_such_channel__", "spend_increase": 1.0},
      "missing_model_data",
  )
  ```

**`scripts/validation/runner.py`:**
- Add an assertion helper alongside `assert_columnar`/`assert_error`:
  ```python
  def assert_summary(payload, label, *, required_keys, outcome_mode):
      assert isinstance(payload, dict), f"{label}: expected dict, got {type(payload)}"
      assert "error_code" not in payload, f"{label}: unexpected error {payload}"
      for key in required_keys:
          assert key in payload, f"{label}: missing '{key}'"
      assert payload["outcome_mode"] == outcome_mode, (
          f"{label}: outcome_mode {payload['outcome_mode']} != {outcome_mode}"
      )
  ```
- In `run_matrix`, inside the per-variant loop (after the
  `get_model_fit`/`get_channel_data` block, reusing the `overview` already
  fetched at the top of the loop), add a happy-path call:
  ```python
  channel = (overview.get("media_channels") or overview.get("rf_channels"))[0]
  label = f"{model_id}/get_spend_scenario"
  try:
      payload = await call(client, "get_spend_scenario",
          {"model_id": model_id, "channel": channel, "spend_increase": 1000.0})
      assert_summary(payload, label,
          required_keys=("model_id", "channel", "channel_type", "outcome_mode",
                         "base_spend", "new_spend", "base_outcome", "new_outcome",
                         "efficiency", "marginal_efficiency", "efficiency_at_new"),
          outcome_mode=matrix.expected_outcome_mode(variant))
      report.ok(label)
  except AssertionError as exc:
      report.fail(label, str(exc))
  ```
- The unknown-channel adversarial needs **no runner change** — the existing
  `for case in matrix.adversarial_cases(variant)` loop already runs it and
  asserts `missing_model_data`.

**Assertion is structural, not value-based.** It checks the keys, types, and
`outcome_mode` — never specific numbers. This keeps it robust to the fixtures'
spend scale (the fixed `spend_increase=1000.0` may be large or small relative
to a fixture's historical spend; `marginal_efficiency` is allowed to be `null`
if the lift difference rounds to zero — `assert_summary` only requires the key
to be present).

**`scripts/validation/live_validate.py`** needs **no change** — it just runs
`run_matrix`. `RESULT_CACHE_ENABLED` is already forced off there, so each call
recomputes.

**The pass bar:** `uv run python -m scripts.validation.live_validate` prints the
variant×tool matrix including `get_spend_scenario` rows (7 happy-path + 7
adversarial) and ends with `LIVE VALIDATION PASSED`. This is part of the
definition of done, alongside `uv run pytest` and `uv run ruff check`.

---

## 6. Documentation updates

- `AGENTS.md` — add `get_spend_scenario` to the Current Tool Surface; add a
  Current Analysis Behavior line describing the per-time-unit semantics, the
  `outcome_mode` keying, and that it activates the previously-staged saturation
  engine. In the **Live Validation & Dummy Models** section, note that
  `get_spend_scenario` is covered by the matrix (happy path on every variant +
  unknown-channel adversarial).
- `docs/meridian-mcp-showcase-parity.md` — mark the Response Curves what-if
  parity gap as closed by this tool.
- `docs/architecture-review.md` — update §6.1/§6.2 to note the staged engine is
  now live (no longer deletion candidates); leave `get_carryover` flagged.

---

## 7. Affected files (summary)

| File | Change |
|------|--------|
| `transport/tools.py` | New `get_spend_scenario` tool handler. |
| `services/analysis_service.py` | New `get_spend_scenario` method; add to `available_tool_options`. |
| `meridian/analyzer_facade.py` | New `resolve_base_spend` + `spend_response`; existing saturation engine reused unchanged. |
| `tests/unit/test_analyzer_facade.py` | Tests for the two new methods. |
| `tests/unit/test_analysis_service.py` | Service happy/error/caching tests. |
| `tests/contract/test_analysis_tools.py` | Registration + shape contract. |
| `scripts/validation/matrix.py` | `expected_outcome_mode` helper + unknown-channel adversarial case (§5.1). |
| `scripts/validation/runner.py` | `assert_summary` helper + per-variant happy-path `get_spend_scenario` call (§5.1). |
| `AGENTS.md`, `docs/meridian-mcp-showcase-parity.md`, `docs/architecture-review.md` | Docs. |

No changes to response envelopes of existing tools. No new dependencies.

# Future Channel Exclusion — Design Spec

**Date:** 2026-07-02
**Status:** Approved (design)
**Scope:** Add a real "exclude / pause a channel" capability to
`run_future_optimization`, and correct the analyst-skill docs that currently
claim channel exclusion is possible via `0/0` constraint bounds (it is not).

## Problem

The bundled analyst skill tells users they can "pause / stop channel X entirely"
by setting a `per_channel` constraint with `{lower_pct: 0, upper_pct: 0}`. This is
**false**. Meridian's spend-constraint bounds are multiplicative around each
channel's baseline spend (`pct_of_spend × budget`):

```
lower_bound = (1 - lower_pct) × baseline
upper_bound = (1 + upper_pct) × baseline
```

So `0/0` yields `[baseline, baseline]` — it **freezes the channel at its current
spend**, the opposite of pausing. And because `upper_bound = (1 + upper_pct) ×
baseline ≥ baseline > 0`, no bound setting can ever pin a channel to exactly `0`
unless its baseline is already `0`. Cost multipliers cannot do it either
(`cost_multiplier = 0` → `cpmu = 0` → divide-by-zero, and is rejected by
validation; conceptually it makes a channel *infinitely cheap*, the reverse of
pausing).

There is currently **no supported way to fully zero/exclude a channel** in either
historical or future runs.

## Verified mechanism

Live probe against the `national-revenue` fixture (`BudgetOptimizer.optimize`)
confirmed both facts we build on:

- **Exclusion works via a zero baseline.** Passing a `pct_of_spend` vector with a
  `0` entry (remaining weights renormalized to sum to 1) plus that channel's
  bounds at `0/0` is **accepted** by Meridian. The excluded channel is pinned to
  spend `0` in both the non-optimized and optimized allocation; its budget share
  reallocates across the remaining channels (baseline total unchanged).
- **Freeze is safe.** `0/0` bounds on a *nonzero*-baseline channel does **not**
  error on the degenerate zero-width band, and holds the channel exactly at
  baseline. So the existing "freeze a contracted channel" guidance is correct and
  stays.

**Why future-only:** future runs already build their own baseline weight vector
(`normalize_planned_allocation`), so zeroing a channel there is clean and fully in
our control. Historical runs pass `pct_of_spend=None` and let Meridian derive the
baseline from historical spend over the selected window; excluding a channel there
would require replicating Meridian's exact windowing
(`start_date`/`end_date`/`selected_geos`) to avoid a silent baseline mismatch —
materially riskier, and out of scope. Historical runs keep freeze-only; their docs
are corrected to say so.

## Feature design

### Config surface

Add one optional field to `FutureBlock`
(`src/google_meridian_mcp_server/domain/optimization.py`):

```python
excluded_channels: list[str] | None = Field(
    default=None,
    description=(
        "Channels to fully pause/exclude from the future plan (spend forced to 0; "
        "their budget is reallocated across the remaining channels, total budget "
        "unchanged). Keys must be valid paid/RF channels and must not also appear "
        "in planned_allocation or cost_multipliers. Cannot exclude every channel. "
        "Example: ['TV']."
    ),
    examples=[["TV"]],
)
```

`FutureBlock` already has a `_positive_weights` validator for
`cost_multipliers`/`planned_allocation`; `excluded_channels` is a list, validated
separately (structural checks that don't need the model live in the pydantic
model; channel-name validity needs the model and lives in `validate_future`).

### Budget behavior (decided)

With `fixed_budget`, exclusion **reallocates** the excluded channel's budget across
the remaining channels; the **total budget is unchanged**. A user who also wants to
cut that money lowers `budget` explicitly. (Flexible-budget `target_*` scenarios:
the channel is still pinned at 0 and the optimizer sizes the rest to hit the
target.)

### Where the logic lives

**Pure helpers in `src/google_meridian_mcp_server/meridian/future_data.py`** (no
Meridian imports, fully unit-testable):

1. `validate_excluded_channels(excluded, planned_allocation, cost_multipliers, channel_order)`
   — raises `ValueError` on: unknown channel; overlap with `planned_allocation`
   keys; overlap with `cost_multipliers` keys; excluding all channels.
2. `apply_exclusions(pct, spend_lower, spend_upper, excluded, channel_order)` —
   given the baseline weight vector `pct` (list, sums to 1) and the spend-constraint
   bounds (`spend_lower`/`spend_upper`, each either a scalar from a `global`
   constraint or a per-channel list from a `per_channel` constraint), returns
   `(pct2, lower_list, upper_list)` where:
   - excluded channels' weights are set to `0` and the remaining weights are
     renormalized to sum to 1 (raise if the remaining sum is `0`);
   - scalar bounds are expanded to per-channel lists;
   - excluded channels' lower/upper bounds are set to `0`.

**Wiring in `OptimizerFacade` (`meridian/optimizer_facade.py`):**

- `_future_kwargs`: after computing `pct` via `normalize_planned_allocation` and
  obtaining base kwargs from `to_optimize_kwargs` (which yields
  `spend_constraint_lower`/`spend_constraint_upper`), call `apply_exclusions` and
  overwrite `pct_of_spend`, `spend_constraint_lower`, `spend_constraint_upper` in
  the kwargs.
- `validate_future`: call `validate_excluded_channels(...)` with `channel_order()`.

No change to `to_optimize_kwargs`' existing per-channel "missing bounds" rule: a
`per_channel` constraint still must cover every channel (including excluded ones);
`apply_exclusions` simply overrides the excluded ones to `0/0`.

### Error handling

All exclusion validation raises `ValueError`, which the service layer already maps
to the flat `invalid_optimization_config` envelope (same path as
`cost_multipliers`/`planned_allocation` validation). `horizon`/pydantic-level
constraints remain protocol-level `ToolError`s. Unknown channels, all-excluded,
and overlap all surface as `invalid_optimization_config`.

## Documentation changes

### `skills/meridian-analyst/references/consultation.md`
- **Translation table**, "Pause / stop channel X entirely next quarter" row:
  replace the false `0/0` guidance with
  `future.excluded_channels: ["X"]` (future runs), and note historical runs cannot
  fully exclude. Add the **"pause TV, spend it elsewhere"** framing.
- **Anti-patterns**, "Reaching for a zero weight to exclude a channel" bullet:
  correct it — full exclusion in a future run is `future.excluded_channels`, not a
  `0/0` constraint (which freezes at baseline).
- Keep the "locked into our search contract" row (`0/0` = freeze) — it is correct.
- **Minimum-to-elicit, Future optimization**: add "channels to pause entirely →
  `excluded_channels`".

### `skills/meridian-analyst/references/budget-optimization.md`
- Scenario table / `per_channel` interpretation: keep `0/0` = **freeze**, but stop
  implying it can exclude.
- Forward-looking "Excluding a channel entirely is not a zero weight" bullet:
  rewrite to point at `future.excluded_channels`; state that excluded channels'
  budget reallocates to the rest (total unchanged) and they appear in the result
  with spend `0`.
- Add `excluded_channels` to the `future` block field list, with the
  "pause TV, spend it elsewhere" example.

### `skills/meridian-analyst/references/glossary.md` / `taxonomy.md`
- Add a short `excluded_channels` glossary entry if the file lists the other
  future knobs (keep parallel to `cost_multipliers`/`planned_allocation`).

### Tool docstring (`transport/tools.py`) and `AGENTS.md`
- Mention `excluded_channels` in the `run_future_optimization` tool docstring and
  the AGENTS.md future-optimization section (respect the 250-line cap; trim if
  needed).

## Testing

- **Unit** (`tests/unit/test_future_data.py`): `validate_excluded_channels` (unknown,
  overlap-with-planned, overlap-with-cost, all-excluded, happy path) and
  `apply_exclusions` (global scalar → expanded+zeroed list; per-channel list zeroed;
  renormalization correctness; remaining-sum-zero raises).
- **Unit** (`tests/unit/test_future_optimization_config.py`): `FutureBlock` accepts
  `excluded_channels`; round-trips through `AnyOptimizationConfig`.
- **Integration** (`tests/integration/test_optimizer_facade_future.py`): facade
  `run_future` with `excluded_channels=['ch_0']` on a fixture → `ch_0` spend `0` in
  both `channel_tables.initial` and `.optimized`; other channels' spend sums to the
  budget; excluded channel absent from neither table (present with 0).
- **Contract** (`tests/contract/test_optimization_tools.py`): `run_future_optimization`
  accepts `excluded_channels`; unknown channel → `invalid_optimization_config`;
  excluding all channels → `invalid_optimization_config`.
- **Live validation** (`scripts/validation/runner.py`): extend
  `assert_live_future_optimization` with one `excluded_channels` submit →
  poll → result assertion (excluded channel spend 0).
- **Local QA** (`scripts/qa/future_optimization_qa.py`): add one exclusion scenario.

## Out of scope

- Historical (`run_optimization`) exclusion — freeze-only; docs corrected to say so.
  A follow-up spec can add it with windowed-baseline replication.
- Any change to `cost_multipliers`/`planned_allocation` "must be > 0" validation.

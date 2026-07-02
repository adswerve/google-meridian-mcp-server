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
    if excluded is None:
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

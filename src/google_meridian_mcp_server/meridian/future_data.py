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
        step = np.timedelta64(cadence_days, "D")
        start = np.datetime64(str(start_date))
        target = start - periods_per_year * step
        arr = np.array(times, dtype="datetime64[D]")
        # The window is the `horizon` periods starting at the one containing
        # target; all of it must lie inside [arr[0], arr[-1] + step).
        anchor = int(np.searchsorted(arr, target, side="right")) - 1
        if target < arr[0] or target >= arr[-1] + step or anchor + horizon > n:
            one_day = np.timedelta64(1, "D")
            raise ValueError(
                f"same_period_last_year needs data for {target} to "
                f"{target + horizon * step - one_day} (one year before the planned "
                f"{start} to {start + horizon * step - one_day}), but the model's "
                f"data covers {arr[0]} to {arr[-1] + step - one_day}. Choose a "
                "start_date whose date one year earlier falls inside the model's "
                "data, or use the trailing or full_history_average reference."
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


# A partial mix whose shares sum to within this of 1 leaves nothing for the
# channels it omits (sum([0.01, 0.29, 0.7]) is 1 - 1.1e-16 in floats).
_NOTHING_LEFT_TOLERANCE = 1e-9


def normalize_planned_allocation(
    planned: dict[str, float] | None,
    carried_spend: dict[str, float],
    channel_order: list[str],
    excluded: list[str] | None = None,
) -> list[float] | None:
    """Turn a submitted planned mix into one share per channel, in channel_order.

    ``carried_spend`` is each channel's spend over the reference window, in
    currency; only its proportions among the omitted channels are used.
    Excluded channels get 0 and count as neither named nor omitted.

    - Every non-excluded channel named: the shares are scaled to sum to 1.
    - Some omitted: the named shares are kept exactly as given, and what is left
      (1 minus their sum) is split among the omitted channels in proportion to
      their reference-window spend. Refused when the named shares already sum
      to 1 or more, or when the omitted channels had no reference-window spend.
    """
    if planned is None:
        return None
    unknown = [ch for ch in planned if ch not in channel_order]
    if unknown:
        raise ValueError(f"planned_allocation has unknown channels: {unknown}")
    excluded_set = set(excluded or ())
    named_total = sum(planned.values())
    omitted = [
        ch for ch in channel_order if ch not in planned and ch not in excluded_set
    ]
    if not omitted:
        if named_total <= 0:
            raise ValueError("planned_allocation weights sum to zero.")
        return [planned.get(ch, 0.0) / named_total for ch in channel_order]

    remainder = 1.0 - named_total
    if remainder <= _NOTHING_LEFT_TOLERANCE:
        raise ValueError(
            f"planned_allocation leaves out {omitted}, but the shares it names "
            f"add up to 1 or more ({named_total:g}), so nothing is left for the "
            "channels it leaves out. Give shares as fractions of 1 (0.4 = 40%) "
            "that add up to less than 1, or name every channel (the shares are "
            "then scaled to add up to 1)."
        )
    omitted_spend = sum(carried_spend.get(ch, 0.0) for ch in omitted)
    if omitted_spend <= 0:
        raise ValueError(
            f"planned_allocation leaves out {omitted}, but {omitted} had no spend "
            "in the reference window, so there is no reference mix to split the "
            f"remaining {remainder:g} of the plan by. Name those channels, exclude "
            "them (excluded_channels), or pick a different reference window."
        )
    shares = {ch: 0.0 for ch in channel_order}  # excluded channels stay at 0
    shares.update(planned)
    for ch in omitted:
        shares[ch] = remainder * carried_spend.get(ch, 0.0) / omitted_spend
    return [shares[ch] for ch in channel_order]


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
    """Zero out excluded channels' weights (renormalizing the rest) and clamp their
    bounds to 0. Assumes channel names were already validated by
    validate_excluded_channels."""
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

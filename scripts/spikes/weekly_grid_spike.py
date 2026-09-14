"""Spec 11.5 measurements for WeeklyOptimizationGrid. Findings only.

Nothing here becomes production code. Its output decides multiplier_step, the
default bounds, and whether the GPU tier stays -- and none of those decisions
may be made before these numbers exist.

Peak memory is measured PER CONFIGURATION, in a child process.
resource.getrusage(RUSAGE_SELF).ru_maxrss is a process high-water mark, so
sampling it repeatedly in one process produces a monotone sequence that says
nothing about any individual configuration.

Usage:
  MERIDIAN_BACKEND=jax MERIDIAN_ENABLE_JAX_X64=true \
      uv run python -m scripts.spikes.weekly_grid_spike \
      --mode all --model-id geo-revenue --steps 0.05 0.01 0.005 \
      --out /tmp/weekly-grid-geo-revenue.json
"""

from __future__ import annotations

import argparse
import json
import resource
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

MODELS_ROOT = Path("models/_validation")


def peak_rss_mb() -> float:
    """This process's peak RSS. Linux reports KiB, macOS reports bytes."""
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return usage / 1024 if sys.platform.startswith("linux") else usage / (1024 * 1024)


def load_model(model_id: str):
    from meridian.schema.serde import meridian_serde

    return meridian_serde.load_meridian(str(MODELS_ROOT / model_id / "model.binpb"))


def allocation(optimized_data) -> dict[str, float]:
    """channel -> optimized spend, from OptimizationResults.optimized_data."""
    return {
        str(channel): float(optimized_data["spend"].sel(channel=channel).item())
        for channel in optimized_data.coords["channel"].values
    }


def total_outcome(optimized_data) -> float:
    return float(optimized_data.attrs["total_incremental_outcome"])


def summarize(optimized_data) -> dict[str, Any]:
    return {
        "allocation": allocation(optimized_data),
        "total_incremental_outcome": total_outcome(optimized_data),
        "budget": float(optimized_data.attrs["budget"]),
    }


def run_classic(model_id: str) -> dict[str, Any]:
    """Q1 reference: the existing path evaluates the model at each grid point."""
    from meridian.analysis.optimizer import BudgetOptimizer

    mmm = load_model(model_id)
    started = time.perf_counter()
    results = BudgetOptimizer(mmm).optimize(fixed_budget=True)
    seconds = time.perf_counter() - started
    return {
        "mode": "classic",
        "seconds": seconds,
        "peak_rss_mb": peak_rss_mb(),
        **summarize(results.optimized_data),
    }


def _build_grid(mmm, *, multiplier_step, decrease, increase, variation):
    from meridian.analysis.analyzer import Analyzer
    from meridian.analysis.weekly_optimization_grid import WeeklyOptimizationGrid

    return WeeklyOptimizationGrid.create(
        Analyzer(mmm),  # an Analyzer, NOT the Meridian model
        multiplier_step=multiplier_step,
        max_budget_percent_decrease=decrease,
        max_budget_percent_increase=increase,
        max_constraint_variation=variation,
    )


def run_weekly(
    model_id: str, multiplier_step, decrease: float, increase: float, variation: float
) -> dict[str, Any]:
    """Q1/Q2/Q5: build cost, peak memory, and the optimized result."""
    from meridian.analysis.optimizer import BudgetOptimizer

    mmm = load_model(model_id)
    started = time.perf_counter()
    grid = _build_grid(
        mmm,
        multiplier_step=multiplier_step,
        decrease=decrease,
        increase=increase,
        variation=variation,
    )
    build_seconds = time.perf_counter() - started
    build_peak_rss_mb = peak_rss_mb()

    started = time.perf_counter()
    og = grid.to_optimization_grid()  # no `constraints` parameter exists
    slice_seconds = time.perf_counter() - started
    if og is None:
        return {
            "mode": "weekly",
            "multiplier_step": multiplier_step,
            "bounds": [decrease, increase, variation],
            "build_seconds": build_seconds,
            "build_peak_rss_mb": build_peak_rss_mb,
            "slice_seconds": slice_seconds,
            "to_optimization_grid_returned_none": True,
        }

    started = time.perf_counter()
    results = BudgetOptimizer(mmm).optimize(fixed_budget=True, optimization_grid=og)
    optimize_seconds = time.perf_counter() - started
    return {
        "mode": "weekly",
        "multiplier_step": multiplier_step,
        "bounds": [decrease, increase, variation],
        "build_seconds": build_seconds,
        "build_peak_rss_mb": build_peak_rss_mb,
        "slice_seconds": slice_seconds,
        "optimize_seconds": optimize_seconds,
        "peak_rss_mb": peak_rss_mb(),
        "to_optimization_grid_returned_none": False,
        **summarize(results.optimized_data),
    }


def run_reuse(model_id: str, multiplier_step) -> dict[str, Any]:
    """Q3: second-and-subsequent optimization cost from ONE cached grid."""
    from meridian.analysis.optimizer import BudgetOptimizer

    mmm = load_model(model_id)
    started = time.perf_counter()
    grid = _build_grid(
        mmm, multiplier_step=multiplier_step, decrease=0.9, increase=1.0, variation=0.3
    )
    build_seconds = time.perf_counter() - started

    times = list(grid.time)
    quarter = max(1, len(times) // 4)
    windows = [
        (times[0], times[-1]),
        (times[0], times[quarter]),
        (times[quarter], times[2 * quarter]),
        (times[2 * quarter], times[-1]),
    ]
    optimizer = BudgetOptimizer(mmm)
    reuses = []
    for start, end in windows:
        started = time.perf_counter()
        og = grid.to_optimization_grid(start_date=start, end_date=end)
        slice_seconds = time.perf_counter() - started
        if og is None:
            reuses.append({"window": [start, end], "returned_none": True})
            continue
        started = time.perf_counter()
        results = optimizer.optimize(fixed_budget=True, optimization_grid=og)
        reuses.append(
            {
                "window": [start, end],
                "returned_none": False,
                "slice_seconds": slice_seconds,
                "optimize_seconds": time.perf_counter() - started,
                "total_incremental_outcome": total_outcome(results.optimized_data),
            }
        )
    return {
        "mode": "reuse",
        "multiplier_step": multiplier_step,
        "build_seconds": build_seconds,
        "peak_rss_mb": peak_rss_mb(),
        "reuses": reuses,
    }


def run_combine(model_id: str, multiplier_step) -> dict[str, Any]:
    """Q4: does combine() give arbitrary windows without recomputation?

    Builds two grids over adjacent, non-overlapping halves, concatenates them,
    and slices the union out of the combined grid. If the union slice works,
    an arbitrary window really is available without a third create().
    """
    from meridian.analysis.analyzer import Analyzer
    from meridian.analysis.weekly_optimization_grid import WeeklyOptimizationGrid

    mmm = load_model(model_id)
    analyzer = Analyzer(mmm)
    full = WeeklyOptimizationGrid.create(analyzer, multiplier_step=multiplier_step)
    times = list(full.time)
    mid = len(times) // 2

    started = time.perf_counter()
    first = WeeklyOptimizationGrid.create(
        analyzer,
        start_date=times[0],
        end_date=times[mid - 1],
        multiplier_step=full.multiplier_step,
    )
    second = WeeklyOptimizationGrid.create(
        analyzer,
        start_date=times[mid],
        end_date=times[-1],
        multiplier_step=full.multiplier_step,
    )
    build_two_seconds = time.perf_counter() - started

    started = time.perf_counter()
    try:
        combined = WeeklyOptimizationGrid.combine([first, second])
        combine_error = None
    except Exception as exc:  # noqa: BLE001 - spike: record and report
        combined, combine_error = None, f"{type(exc).__name__}: {exc}"
    combine_seconds = time.perf_counter() - started

    union = None
    if combined is not None:
        started = time.perf_counter()
        og = combined.to_optimization_grid(start_date=times[0], end_date=times[-1])
        union = {
            "slice_seconds": time.perf_counter() - started,
            "returned_none": og is None,
            "combined_time_span": [str(combined.time[0]), str(combined.time[-1])],
            "covers_full_span": list(combined.time) == times,
        }
    return {
        "mode": "combine",
        "multiplier_step": multiplier_step,
        "build_two_halves_seconds": build_two_seconds,
        "combine_seconds": combine_seconds,
        "combine_error": combine_error,
        "union_slice": union,
        "peak_rss_mb": peak_rss_mb(),
    }


def _child(args: list[str]) -> dict[str, Any]:
    """Run one configuration in its own interpreter and read back its JSON.

    One process per configuration is what makes the peak-RSS number mean
    something: ru_maxrss is a high-water mark, so a second measurement in the
    same process can never be lower than the first.
    """
    completed = subprocess.run(
        [sys.executable, "-m", "scripts.spikes.weekly_grid_spike", *args],
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        return {"error": completed.stderr[-2000:]}
    return json.loads(completed.stdout.strip().splitlines()[-1])


def signed_error(classic: dict, weekly: dict) -> dict[str, Any]:
    """Q1: per-channel allocation and total-outcome error, WITH ITS SIGN.

    Concavity predicts the weekly path under-estimates between knots, so a
    consistently NEGATIVE total_outcome_signed_delta is the expected result
    and a positive one needs explaining.
    """
    if weekly.get("to_optimization_grid_returned_none") or "allocation" not in weekly:
        return {"unavailable": True}
    ca, wa = classic["allocation"], weekly["allocation"]
    per_channel = {channel: wa[channel] - ca[channel] for channel in ca}
    ct = classic["total_incremental_outcome"]
    wt = weekly["total_incremental_outcome"]
    return {
        "per_channel_spend_delta": per_channel,
        "worst_abs_channel_delta": max(abs(v) for v in per_channel.values()),
        "worst_rel_channel_delta": max(abs(wa[c] - ca[c]) / ca[c] for c in ca if ca[c]),
        "total_outcome_classic": ct,
        "total_outcome_weekly": wt,
        "total_outcome_signed_delta": wt - ct,
        "total_outcome_relative_delta": (wt - ct) / ct if ct else None,
        "sign_matches_concavity_prediction": (wt - ct) <= 0,
    }


def run_all(model_id: str, steps: list[float], bounds: list[list[float]]) -> dict:
    record: dict[str, Any] = {"model_id": model_id, "weekly": [], "bounds_sweep": []}
    record["classic"] = _child(["--mode", "classic", "--model-id", model_id])
    for step in steps:
        weekly = _child(
            ["--mode", "weekly", "--model-id", model_id, "--multiplier-step", str(step)]
        )
        weekly["signed_error_vs_classic"] = signed_error(record["classic"], weekly)
        record["weekly"].append(weekly)
        print(
            f"step={step} build={weekly.get('build_seconds')} "
            f"peak_rss={weekly.get('build_peak_rss_mb')} "
            f"outcome_delta={weekly['signed_error_vs_classic'].get('total_outcome_signed_delta')}"
        )
    reference_step = str(steps[0])
    record["reuse"] = _child(
        ["--mode", "reuse", "--model-id", model_id, "--multiplier-step", reference_step]
    )
    record["combine"] = _child(
        [
            "--mode",
            "combine",
            "--model-id",
            model_id,
            "--multiplier-step",
            reference_step,
        ]
    )
    for decrease, increase, variation in bounds:
        record["bounds_sweep"].append(
            _child(
                [
                    "--mode",
                    "weekly",
                    "--model-id",
                    model_id,
                    "--multiplier-step",
                    reference_step,
                    "--bounds-decrease",
                    str(decrease),
                    "--bounds-increase",
                    str(increase),
                    "--bounds-variation",
                    str(variation),
                ]
            )
        )
        print(f"bounds={decrease},{increase},{variation} done")
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=("all", "classic", "weekly", "reuse", "combine"),
        default="all",
    )
    parser.add_argument("--model-id", default="geo-revenue")
    parser.add_argument("--steps", nargs="*", type=float, default=[0.05, 0.01, 0.005])
    parser.add_argument("--multiplier-step", type=float, default=None)
    parser.add_argument("--bounds-decrease", type=float, default=0.9)
    parser.add_argument("--bounds-increase", type=float, default=1.0)
    parser.add_argument("--bounds-variation", type=float, default=0.3)
    parser.add_argument("--out", default="/tmp/weekly-grid-measurements.json")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    if args.mode == "classic":
        print(json.dumps(run_classic(args.model_id), default=str))
        return 0
    if args.mode == "weekly":
        print(
            json.dumps(
                run_weekly(
                    args.model_id,
                    args.multiplier_step,
                    args.bounds_decrease,
                    args.bounds_increase,
                    args.bounds_variation,
                ),
                default=str,
            )
        )
        return 0
    if args.mode == "reuse":
        print(json.dumps(run_reuse(args.model_id, args.multiplier_step), default=str))
        return 0
    if args.mode == "combine":
        print(json.dumps(run_combine(args.model_id, args.multiplier_step), default=str))
        return 0

    bounds = [[0.9, 1.0, 0.3], [0.5, 0.5, 0.15], [0.95, 2.0, 0.5]]
    record = run_all(args.model_id, args.steps, bounds)
    Path(args.out).write_text(json.dumps(record, indent=2, default=str))
    print(f"measurements -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

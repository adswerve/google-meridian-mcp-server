"""Final QA gate: drive BOTH optimization tools through 11 live scenarios.

Standalone script. Builds/reuses the tiny fitted-model fixtures under
``models/_validation`` (same ones scripts/validation/live_validate.py and the
contract tests use), wires an in-process ``Client(mcp)`` against the LOCAL
tier only, and exercises 5 ``run_optimization`` (historical) + 6
``run_future_optimization`` (future) scenarios per spec section 13.1: happy
paths assert a completed run with a well-formed result; adversarial cases
assert the *right layer* fails (flat ``invalid_optimization_config`` envelope
at submit, a failed terminal run, or a protocol-level ``ToolError``). The 6th
future scenario covers ``future.excluded_channels``, pinning a channel's
optimized spend to 0.

Usage:
  OPTIMIZATION_ALLOWED_TIERS=local uv run python scripts/qa/future_optimization_qa.py

Prints a per-scenario PASS/FAIL table, ending ``FUTURE-OPT QA PASSED (11/11)``
and exiting 0 iff every scenario passes; otherwise exits 1.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import statistics
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_VALIDATION_MODELS_ROOT = _REPO_ROOT / "models" / "_validation"

NATIONAL = "national-revenue"
GEO = "geo-revenue"
FAR_FUTURE = "2099-01-01"
NON_FUTURE = "2020-01-01"

_TERMINAL = {"completed", "failed", "canceled"}


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------


class Report:
    def __init__(self) -> None:
        self.rows: list[tuple[str, bool, str]] = []

    def record(self, label: str, ok: bool, detail: str = "") -> None:
        self.rows.append((label, ok, detail))

    @property
    def all_passed(self) -> bool:
        return all(ok for _, ok, _ in self.rows)

    def print_table(self) -> None:
        width = max((len(label) for label, _, _ in self.rows), default=0)
        print("\n=== FUTURE-OPT QA report ===")
        for label, ok, detail in self.rows:
            tag = "PASS" if ok else "FAIL"
            suffix = f"  -- {detail}" if detail and not ok else ""
            print(f"  {tag:4}  {label.ljust(width)}{suffix}")


async def _run_scenario(report: Report, label: str, coro) -> Any:
    try:
        value = await coro
    except Exception as exc:  # noqa: BLE001 - QA driver: report and keep going
        report.record(label, False, repr(exc))
        return None
    report.record(label, True)
    return value


# --------------------------------------------------------------------------
# In-process MCP client helpers
# --------------------------------------------------------------------------


def _content_to_obj(res: Any) -> Any:
    if getattr(res, "data", None) is not None:
        return res.data
    if getattr(res, "structured_content", None) is not None:
        return res.structured_content
    block = res.content[0]
    text = getattr(block, "text", block)
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return text


def _unwrap(obj: Any) -> Any:
    if isinstance(obj, dict) and set(obj.keys()) == {"result"}:
        return obj["result"]
    return obj


async def call(client, name: str, args: dict) -> Any:
    res = await client.call_tool(name, args)
    return _unwrap(_content_to_obj(res))


async def poll_to_terminal(
    client, run_id: str, *, cap: int = 240, interval: float = 0.5
):
    status = None
    for _ in range(cap):
        status = await call(client, "get_optimization_status", {"run_id": run_id})
        if status["status"] in _TERMINAL:
            return status
        await asyncio.sleep(interval)
    raise AssertionError(f"run {run_id} did not reach a terminal state: {status}")


def assert_well_formed(result: dict, label: str) -> None:
    assert isinstance(result, dict), (
        f"{label}: expected dict result, got {type(result)}"
    )
    assert "error_code" not in result, f"{label}: unexpected error {result}"
    assert "outcome_mode" in result, f"{label}: missing 'outcome_mode'"
    optimized = (result.get("channel_tables") or {}).get("optimized")
    assert optimized, f"{label}: empty channel_tables.optimized"
    # Strict JSON round-trip: no NaN/Inf allowed to slip through.
    json.dumps(result, allow_nan=False)


def compute_cadence_and_next_period(overview: dict) -> tuple[int, date]:
    values = overview["time"]["values"]
    dates = sorted(date.fromisoformat(str(v)[:10]) for v in values)
    diffs = [(b - a).days for a, b in zip(dates, dates[1:])]
    cadence = int(statistics.median(diffs))
    last = date.fromisoformat(str(overview["time"]["end"])[:10])
    return cadence, last + timedelta(days=cadence)


# --------------------------------------------------------------------------
# Historical run_optimization scenarios (spec 13.1, 5 scenarios)
# --------------------------------------------------------------------------


async def scenario_h1(client, channels: list[str]) -> dict:
    """fixed_budget (budget omitted) + global pct constraint -> happy."""
    config = {
        "scenario": {"type": "fixed_budget"},
        "constraint": {"mode": "global", "pct": 0.3},
    }
    # Every run_id we create (initial submit, reuse resubmit, force_rerun
    # resubmit) is appended here BEFORE any assertion on that response runs,
    # so a failing assertion can never leak an orphaned run in `finally`.
    created_run_ids: list[str] = []
    submit = await call(
        client, "run_optimization", {"model_id": NATIONAL, "config": config}
    )
    assert "error_code" not in submit, f"submit error: {submit}"
    run_id = submit["run_id"]
    created_run_ids.append(run_id)
    assert submit["compute_tier_resolved"] == "local", f"expected local tier: {submit}"

    try:
        status = await poll_to_terminal(client, run_id)
        assert status["status"] == "completed", f"did not complete: {status}"

        result = await call(client, "get_optimization_result", {"run_id": run_id})
        assert_well_formed(result, "H1")

        # Reuse-by-fingerprint: identical resubmit -> reused True, same run_id.
        again = await call(
            client, "run_optimization", {"model_id": NATIONAL, "config": config}
        )
        if again.get("run_id"):
            created_run_ids.append(again["run_id"])
        assert again.get("reused") is True and again["run_id"] == run_id, (
            f"reuse-by-fingerprint failed: {again}"
        )

        # force_rerun=True: brand-new run, reused False.
        forced = await call(
            client,
            "run_optimization",
            {"model_id": NATIONAL, "config": config, "force_rerun": True},
        )
        if forced.get("run_id"):
            created_run_ids.append(forced["run_id"])
        assert forced.get("reused") is False and forced["run_id"] != run_id, (
            f"force_rerun failed: {forced}"
        )
        return result
    finally:
        # Best-effort sweep of every run created above, deduped (the reuse
        # resubmit returns the same run_id as the initial submit) so a
        # cancel/delete failure on one run never blocks cleanup of the rest.
        for rid in dict.fromkeys(created_run_ids):
            try:
                await call(client, "cancel_optimization", {"run_id": rid})
            except Exception:  # noqa: BLE001 - best-effort cleanup
                pass
            try:
                await call(client, "delete_optimization", {"run_id": rid})
            except Exception:  # noqa: BLE001 - best-effort cleanup
                pass


async def scenario_h2(client, channels: list[str], *, budget: float) -> dict:
    """fixed_budget(+20%) + per_channel constraint freezing one channel -> happy."""
    frozen = channels[0]
    bounds = {
        ch: (
            {"lower_pct": 0.0, "upper_pct": 0.0}
            if ch == frozen
            else {"lower_pct": 0.2, "upper_pct": 0.2}
        )
        for ch in channels
    }
    config = {
        "scenario": {"type": "fixed_budget", "budget": budget},
        "constraint": {"mode": "per_channel", "bounds": bounds},
    }
    submit = await call(
        client, "run_optimization", {"model_id": NATIONAL, "config": config}
    )
    assert "error_code" not in submit, f"submit error: {submit}"
    run_id = submit["run_id"]
    try:
        status = await poll_to_terminal(client, run_id)
        assert status["status"] == "completed", f"did not complete: {status}"

        result = await call(client, "get_optimization_result", {"run_id": run_id})
        assert_well_formed(result, "H2")

        init_row = next(
            r for r in result["channel_tables"]["initial"] if r["channel"] == frozen
        )
        opt_row = next(
            r for r in result["channel_tables"]["optimized"] if r["channel"] == frozen
        )
        tol = max(1e-6, abs(init_row["spend"]) * 1e-3)
        assert abs(opt_row["spend"] - init_row["spend"]) <= tol, (
            f"frozen channel {frozen!r} spend moved: "
            f"{init_row['spend']} -> {opt_row['spend']}"
        )
        return result
    finally:
        await call(client, "delete_optimization", {"run_id": run_id})


async def scenario_h3(client) -> dict:
    """target_roas on a revenue model, flexible budget -> happy."""
    config = {"scenario": {"type": "target_roas", "target_value": 2.0}}
    submit = await call(client, "run_optimization", {"model_id": GEO, "config": config})
    assert "error_code" not in submit, f"submit error: {submit}"
    run_id = submit["run_id"]
    try:
        status = await poll_to_terminal(client, run_id)
        assert status["status"] == "completed", f"did not complete: {status}"

        result = await call(client, "get_optimization_result", {"run_id": run_id})
        assert_well_formed(result, "H3")
        return result
    finally:
        await call(client, "delete_optimization", {"run_id": run_id})


async def scenario_h4(client, channels: list[str]) -> None:
    """Adversarial: per_channel bounds missing a channel -> flat submit envelope."""
    bounds = {ch: {"lower_pct": 0.2, "upper_pct": 0.2} for ch in channels[:-1]}
    config = {
        "scenario": {"type": "fixed_budget"},
        "constraint": {"mode": "per_channel", "bounds": bounds},
    }
    submit = await call(
        client, "run_optimization", {"model_id": NATIONAL, "config": config}
    )
    assert submit.get("error_code") == "invalid_optimization_config", (
        f"expected flat invalid_optimization_config envelope, got {submit}"
    )


async def scenario_h5(client) -> None:
    """Adversarial: unknown geo in selected_geos -> submit OK, run ends FAILED."""
    config = {
        "scenario": {"type": "fixed_budget"},
        "constraint": {"mode": "global", "pct": 0.2},
        "selected_geos": ["__no_such_geo__"],
    }
    submit = await call(client, "run_optimization", {"model_id": GEO, "config": config})
    assert "error_code" not in submit, f"submit unexpectedly rejected: {submit}"
    run_id = submit["run_id"]
    try:
        status = await poll_to_terminal(client, run_id)
        assert status["status"] == "failed", (
            f"expected a FAILED terminal run, got: {status}"
        )
    finally:
        await call(client, "delete_optimization", {"run_id": run_id})


# --------------------------------------------------------------------------
# run_future_optimization scenarios (spec 13.1, 5 scenarios + 1 exclusion)
# --------------------------------------------------------------------------


async def scenario_f1(client, national_overview: dict) -> dict:
    """fixed_budget, reference=trailing, horizon=13, default multipliers -> happy."""
    _, next_period = compute_cadence_and_next_period(national_overview)
    config = {
        "scenario": {"type": "fixed_budget"},
        "future": {
            "start_date": next_period.isoformat(),
            "horizon": 13,
            "reference": {"mode": "trailing"},
        },
    }
    submit = await call(
        client, "run_future_optimization", {"model_id": NATIONAL, "config": config}
    )
    assert "error_code" not in submit, f"submit error: {submit}"
    run_id = submit["run_id"]
    assert submit["compute_tier_resolved"] == "local", f"expected local tier: {submit}"
    try:
        status = await poll_to_terminal(client, run_id)
        assert status["status"] == "completed", f"did not complete: {status}"

        result = await call(client, "get_optimization_result", {"run_id": run_id})
        assert_well_formed(result, "F1")
        return result
    finally:
        await call(client, "delete_optimization", {"run_id": run_id})


async def scenario_f2(client, geo_overview: dict) -> dict:
    """same_period_last_year (start=next_period) + cost_multipliers + partial
    planned_allocation (friendly-normalize) -> happy."""
    _, next_period = compute_cadence_and_next_period(geo_overview)
    channels = geo_overview["media_channels"] + geo_overview["rf_channels"]
    target = channels[0]
    config = {
        "scenario": {"type": "fixed_budget"},
        "future": {
            "start_date": next_period.isoformat(),
            "horizon": 4,
            "reference": {"mode": "same_period_last_year"},
            "cost_multipliers": {target: 1.15},
            "planned_allocation": {target: 0.5},  # partial dict: exercises normalize
        },
    }
    submit = await call(
        client, "run_future_optimization", {"model_id": GEO, "config": config}
    )
    assert "error_code" not in submit, f"submit error: {submit}"
    run_id = submit["run_id"]
    try:
        status = await poll_to_terminal(client, run_id)
        assert status["status"] == "completed", f"did not complete: {status}"

        result = await call(client, "get_optimization_result", {"run_id": run_id})
        assert_well_formed(result, "F2")
        return result
    finally:
        await call(client, "delete_optimization", {"run_id": run_id})


async def scenario_f3(client, national_overview: dict) -> dict:
    """target_roas future with revenue_per_kpi_multiplier != 1.0 -> happy."""
    _, next_period = compute_cadence_and_next_period(national_overview)
    config = {
        "scenario": {"type": "target_roas", "target_value": 2.0},
        "future": {
            "start_date": next_period.isoformat(),
            "horizon": 4,
            "reference": {"mode": "trailing"},
            "revenue_per_kpi_multiplier": 1.1,
        },
    }
    submit = await call(
        client, "run_future_optimization", {"model_id": NATIONAL, "config": config}
    )
    assert "error_code" not in submit, f"submit error: {submit}"
    run_id = submit["run_id"]
    try:
        status = await poll_to_terminal(client, run_id)
        assert status["status"] == "completed", f"did not complete: {status}"

        result = await call(client, "get_optimization_result", {"run_id": run_id})
        assert_well_formed(result, "F3")
        return result
    finally:
        await call(client, "delete_optimization", {"run_id": run_id})


async def scenario_f4(client) -> dict:
    """reference=full_history_average, any future start_date -> happy."""
    config = {
        "scenario": {"type": "fixed_budget"},
        "future": {
            "start_date": FAR_FUTURE,
            "horizon": 4,
            "reference": {"mode": "full_history_average"},
        },
    }
    submit = await call(
        client, "run_future_optimization", {"model_id": GEO, "config": config}
    )
    assert "error_code" not in submit, f"submit error: {submit}"
    run_id = submit["run_id"]
    try:
        status = await poll_to_terminal(client, run_id)
        assert status["status"] == "completed", f"did not complete: {status}"

        result = await call(client, "get_optimization_result", {"run_id": run_id})
        assert_well_formed(result, "F4")
        return result
    finally:
        await call(client, "delete_optimization", {"run_id": run_id})


async def scenario_f5(client, national_overview: dict) -> None:
    """Adversarial: (a) non-future start_date, (b) unknown cost_multipliers
    channel, (c) same_period_last_year w/ insufficient history -> flat
    invalid_optimization_config envelopes at submit. Plus: horizon<=0 raises a
    PROTOCOL-level ToolError (NOT the flat envelope), asserted separately."""
    from fastmcp.exceptions import ToolError

    _, next_period = compute_cadence_and_next_period(national_overview)

    # (a) non-future start_date.
    non_future_cfg = {
        "scenario": {"type": "fixed_budget"},
        "future": {
            "start_date": NON_FUTURE,
            "horizon": 4,
            "reference": {"mode": "trailing"},
        },
    }
    res_a = await call(
        client,
        "run_future_optimization",
        {"model_id": NATIONAL, "config": non_future_cfg},
    )
    assert res_a.get("error_code") == "invalid_optimization_config", (
        f"(a) non-future start_date: expected flat envelope, got {res_a}"
    )

    # (b) unknown channel in cost_multipliers.
    bad_channel_cfg = {
        "scenario": {"type": "fixed_budget"},
        "future": {
            "start_date": next_period.isoformat(),
            "horizon": 4,
            "reference": {"mode": "trailing"},
            "cost_multipliers": {"__no_such_channel__": 1.2},
        },
    }
    res_b = await call(
        client,
        "run_future_optimization",
        {"model_id": NATIONAL, "config": bad_channel_cfg},
    )
    assert res_b.get("error_code") == "invalid_optimization_config", (
        f"(b) unknown cost_multipliers channel: expected flat envelope, got {res_b}"
    )

    # (c) same_period_last_year with a far-future start_date -> insufficient history.
    far_future_cfg = {
        "scenario": {"type": "fixed_budget"},
        "future": {
            "start_date": FAR_FUTURE,
            "horizon": 4,
            "reference": {"mode": "same_period_last_year"},
        },
    }
    res_c = await call(
        client,
        "run_future_optimization",
        {"model_id": NATIONAL, "config": far_future_cfg},
    )
    assert res_c.get("error_code") == "invalid_optimization_config", (
        f"(c) same_period_last_year insufficient history: expected flat envelope, "
        f"got {res_c}"
    )

    # (d) horizon<=0 -> protocol-level ToolError, raised BEFORE the tool body runs.
    zero_horizon_cfg = {
        "scenario": {"type": "fixed_budget"},
        "future": {
            "start_date": next_period.isoformat(),
            "horizon": 0,
            "reference": {"mode": "trailing"},
        },
    }
    try:
        await client.call_tool(
            "run_future_optimization",
            {"model_id": NATIONAL, "config": zero_horizon_cfg},
        )
    except ToolError:
        pass
    else:
        raise AssertionError(
            "(d) horizon<=0 did not raise a protocol-level fastmcp.exceptions.ToolError"
        )


async def scenario_f6(client, national_overview: dict) -> dict:
    """future.excluded_channels pins the excluded channel to 0 optimized
    spend, reallocating its budget across the remaining channels -> happy."""
    channels = national_overview["media_channels"] + national_overview["rf_channels"]
    excluded = channels[0]
    config = {
        "scenario": {"type": "fixed_budget"},
        "future": {
            "start_date": FAR_FUTURE,
            "horizon": 4,
            "excluded_channels": [excluded],
        },
    }
    submit = await call(
        client, "run_future_optimization", {"model_id": NATIONAL, "config": config}
    )
    assert "error_code" not in submit, f"submit error: {submit}"
    run_id = submit["run_id"]
    try:
        status = await poll_to_terminal(client, run_id)
        assert status["status"] == "completed", f"did not complete: {status}"

        result = await call(client, "get_optimization_result", {"run_id": run_id})
        assert_well_formed(result, "F6")

        opt_row = next(
            r for r in result["channel_tables"]["optimized"] if r["channel"] == excluded
        )
        assert opt_row["spend"] in (0, 0.0, None), (
            f"excluded channel {excluded!r} expected 0 optimized spend, "
            f"got {opt_row['spend']}"
        )
        return result
    finally:
        await call(client, "delete_optimization", {"run_id": run_id})


# --------------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------------


async def run_qa() -> int:
    from scripts.generate_validation_models import build_all

    build_all(_VALIDATION_MODELS_ROOT, force=False)

    tmp_runs_root = tempfile.mkdtemp(prefix="future-opt-qa-runs-")
    os.environ["OPTIMIZATION_ALLOWED_TIERS"] = "local"
    os.environ["PERSISTENCE_BACKEND"] = "local"
    os.environ["LOCAL_MODELS_ROOT"] = str(_VALIDATION_MODELS_ROOT)
    os.environ["RESULT_CACHE_ENABLED"] = "false"
    os.environ["OPTIMIZATION_RUNS_ROOT"] = tmp_runs_root

    from fastmcp import Client

    from google_meridian_mcp_server.server import create_server

    mcp = create_server()
    report = Report()

    try:
        async with Client(mcp) as client:
            national_overview = await call(
                client, "get_model_overview", {"model_id": NATIONAL}
            )
            geo_overview = await call(client, "get_model_overview", {"model_id": GEO})
            national_channels = (
                national_overview["media_channels"] + national_overview["rf_channels"]
            )

            print("=== Historical run_optimization scenarios (5) ===")
            h1_result = await _run_scenario(
                report,
                "H1 fixed_budget(no-budget)+global-pct[happy,reuse,force_rerun]",
                scenario_h1(client, national_channels),
            )
            base_budget = None
            if isinstance(h1_result, dict):
                base_budget = h1_result.get("summary", {}).get("non_optimized_budget")
            h2_budget = (base_budget * 1.2) if base_budget else 100_000.0

            await _run_scenario(
                report,
                "H2 fixed_budget(+20%)+per_channel-freeze[happy]",
                scenario_h2(client, national_channels, budget=h2_budget),
            )
            await _run_scenario(report, "H3 target_roas[happy]", scenario_h3(client))
            await _run_scenario(
                report,
                "H4 per_channel-missing-channel[adversarial->invalid_optimization_config]",
                scenario_h4(client, national_channels),
            )
            await _run_scenario(
                report,
                "H5 unknown-geo[adversarial->FAILED run]",
                scenario_h5(client),
            )

            print("=== run_future_optimization scenarios (6) ===")
            await _run_scenario(
                report,
                "F1 trailing/horizon=13/default-multipliers[happy]",
                scenario_f1(client, national_overview),
            )
            await _run_scenario(
                report,
                "F2 same_period_last_year+cost_multipliers+partial-planned_allocation[happy]",
                scenario_f2(client, geo_overview),
            )
            await _run_scenario(
                report,
                "F3 target_roas+revenue_per_kpi_multiplier[happy]",
                scenario_f3(client, national_overview),
            )
            await _run_scenario(
                report,
                "F4 full_history_average[happy]",
                scenario_f4(client),
            )
            await _run_scenario(
                report,
                "F5 adversarial(non-future,unknown-channel,insufficient-history)"
                "+protocol-error(horizon<=0)",
                scenario_f5(client, national_overview),
            )
            await _run_scenario(
                report,
                "F6 excluded_channels[happy, excluded channel pinned to 0 spend]",
                scenario_f6(client, national_overview),
            )
    finally:
        shutil.rmtree(tmp_runs_root, ignore_errors=True)

    report.print_table()

    total = len(report.rows)
    passed = sum(1 for _, ok, _ in report.rows if ok)
    if report.all_passed:
        print(f"\nFUTURE-OPT QA PASSED ({passed}/{total})")
        return 0
    print(f"\nFUTURE-OPT QA FAILED ({passed}/{total})")
    return 1


def main() -> int:
    return asyncio.run(run_qa())


if __name__ == "__main__":
    sys.exit(main())

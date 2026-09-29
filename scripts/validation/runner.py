"""In-process MCP client driver and assertions for live validation."""

from __future__ import annotations

import asyncio
import dataclasses

from scripts.validation import matrix
from scripts.validation.payloads import extract


@dataclasses.dataclass
class Report:
    passed: list[str] = dataclasses.field(default_factory=list)
    failed: list[str] = dataclasses.field(default_factory=list)

    def ok(self, label: str) -> None:
        self.passed.append(label)
        print(f"  PASS {label}")

    def fail(self, label: str, reason: str) -> None:
        self.failed.append(f"{label}: {reason}")
        print(f"  FAIL {label}: {reason}")


async def call(client, name, args):
    res = await client.call_tool(name, args)
    return extract(res)


def assert_columnar(payload, label: str) -> None:
    assert isinstance(payload, dict), f"{label}: expected dict, got {type(payload)}"
    assert "error_code" not in payload, f"{label}: unexpected error {payload}"
    for key in ("model_id", "columns", "rows", "row_count"):
        assert key in payload, f"{label}: missing '{key}'"
    assert payload["row_count"] == len(payload["rows"]), f"{label}: row_count mismatch"
    for row in payload["rows"]:
        assert len(row) == len(payload["columns"]), f"{label}: ragged row"
    assert "data" not in payload and "result_metadata" not in payload, (
        f"{label}: legacy keys present"
    )


def assert_error(payload, code: str | None, label: str) -> None:
    assert isinstance(payload, dict), (
        f"{label}: expected dict error, got {type(payload)}"
    )
    if code is None:
        assert "error_code" in payload, f"{label}: expected an error, got {payload}"
        return
    assert payload.get("error_code") == code, (
        f"{label}: expected error_code={code}, got {payload.get('error_code')}"
    )


def assert_summary(payload, label: str, *, required_keys, outcome_mode: str) -> None:
    assert isinstance(payload, dict), f"{label}: expected dict, got {type(payload)}"
    assert "error_code" not in payload, f"{label}: unexpected error {payload}"
    for key in required_keys:
        assert key in payload, f"{label}: missing '{key}'"
    assert payload["outcome_mode"] == outcome_mode, (
        f"{label}: outcome_mode {payload['outcome_mode']} != {outcome_mode}"
    )


def _rows(payload) -> list[dict]:
    return [dict(zip(payload["columns"], row)) for row in payload["rows"]]


def _close(a: float, b: float, rel: float = 1e-5) -> bool:
    """Payload numbers are rounded to 6 significant figures, hence rel 1e-5."""
    return abs(a - b) <= rel * max(abs(a), abs(b), 1e-12)


_TERMINAL_STATUSES = ("completed", "failed", "canceled")
FULL_FUNNEL_MAX_POLLS = 480  # ~240s: full-funnel optimization is slower


async def _await_run(client, run_id: str, *, max_polls: int = 120) -> dict:
    """Poll get_optimization_status until terminal; max_polls * 0.5s is the cap."""
    status = None
    for _ in range(max_polls):
        status = await call(client, "get_optimization_status", {"run_id": run_id})
        if status["status"] in _TERMINAL_STATUSES:
            break
        await asyncio.sleep(0.5)
    return status


def _assert_direct_plus_indirect(result, label: str) -> None:
    """Every optimized channel row: direct + indirect == total incremental outcome."""
    rows = result["channel_tables"]["optimized"]
    assert rows, f"{label}: no optimized channel rows"
    for row in rows:
        assert _close(
            row["incremental_outcome_direct"] + row["incremental_outcome_indirect"],
            row["incremental_outcome"],
        ), f"{label}: direct + indirect != total in {row}"


async def assert_live_optimization(
    client, model_id: str, *, overview, max_polls: int = 120
) -> None:
    config = {
        "scenario": {"type": "fixed_budget"},
        "constraint": {"mode": "global", "pct": 0.2},
    }
    submit = await call(
        client, "run_optimization", {"model_id": model_id, "config": config}
    )
    assert "error_code" not in submit, f"submit error: {submit}"
    run_id = submit["run_id"]
    assert submit["compute_tier_resolved"] == "local", (
        f"expected local tier, got {submit}"
    )

    try:
        status = await _await_run(client, run_id, max_polls=max_polls)
        assert status and status["status"] == "completed", (
            f"run did not complete: {status}"
        )

        result = await call(client, "get_optimization_result", {"run_id": run_id})
        for key in (
            "summary",
            "channel_tables",
            "allocation",
            "spend_delta",
            "outcome_mode",
        ):
            assert key in result, f"result missing '{key}': {result.keys()}"
        assert {"initial", "optimized"} <= set(result["channel_tables"]), (
            "missing channel tables"
        )
        if overview.get("funnel") == "full_funnel":
            _assert_direct_plus_indirect(result, f"{model_id}/optimization")

        # Reuse: identical submit returns the same run, flagged reused.
        again = await call(
            client, "run_optimization", {"model_id": model_id, "config": config}
        )
        assert again["reused"] is True and again["run_id"] == run_id, (
            f"reuse failed: {again}"
        )

        # list_optimizations must surface this run for the model.
        listing = await call(client, "list_optimizations", {"model_id": model_id})
        assert "error_code" not in listing, f"list_optimizations error: {listing}"
        listed_ids = {r["run_id"] for r in listing["runs"]}
        assert run_id in listed_ids, (
            f"run {run_id} not in list_optimizations: {listed_ids}"
        )
        assert listing["count"] == len(listing["runs"]), (
            f"list count mismatch: {listing['count']} != {len(listing['runs'])}"
        )
    finally:
        # Always reap the happy-path run, even if a poll/reuse/list assertion
        # above raised -- otherwise its on-disk artifacts (record/state/result
        # + fingerprint index pointer) would leak. Mirrors
        # assert_live_future_optimization's pattern above.
        deleted = await call(client, "delete_optimization", {"run_id": run_id})

    # delete_optimization removes it; a subsequent status lookup must 404 (typed).
    assert deleted.get("deleted") is True and deleted.get("run_id") == run_id, (
        f"delete failed: {deleted}"
    )
    gone = await call(client, "get_optimization_status", {"run_id": run_id})
    assert_error(gone, "optimization_run_not_found", f"{model_id}/deleted-run-status")


async def assert_live_future_optimization(
    client, model_id: str, *, overview, max_polls: int = 120
) -> None:
    # 2099-01-01 is always safely after any fixture's last training period, so
    # the "trailing" reference window (last `horizon` periods before start_date)
    # is always covered by history.
    future_config = {
        "scenario": {"type": "fixed_budget"},
        "future": {
            "start_date": "2099-01-01",
            "horizon": 4,
            "reference": {"mode": "trailing"},
        },
    }
    submit = await call(
        client,
        "run_future_optimization",
        {"model_id": model_id, "config": future_config},
    )
    assert "error_code" not in submit, f"submit error: {submit}"
    run_id = submit["run_id"]
    assert submit["compute_tier_resolved"] == "local", (
        f"expected local tier, got {submit}"
    )

    try:
        status = await _await_run(client, run_id, max_polls=max_polls)
        assert status and status["status"] == "completed", (
            f"run did not complete: {status}"
        )

        result = await call(client, "get_optimization_result", {"run_id": run_id})
        for key in (
            "summary",
            "channel_tables",
            "allocation",
            "spend_delta",
            "outcome_mode",
        ):
            assert key in result, f"result missing '{key}': {result.keys()}"
        assert {"initial", "optimized"} <= set(result["channel_tables"]), (
            "missing channel tables"
        )
        if overview.get("funnel") == "full_funnel":
            _assert_direct_plus_indirect(result, f"{model_id}/future optimization")

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
        assert "error_code" not in excl_submit, (
            f"{model_id}/exclude submit errored: {excl_submit}"
        )
        excl_run_id = excl_submit["run_id"]
        try:
            excl_status = await _await_run(client, excl_run_id, max_polls=max_polls)
            assert excl_status and excl_status["status"] == "completed", (
                f"{model_id}/exclude expected completed, got {excl_status}"
            )
            excl_result = await call(
                client, "get_optimization_result", {"run_id": excl_run_id}
            )
            opt_rows = excl_result["channel_tables"]["optimized"]
            excl_spend = next(
                r["spend"] for r in opt_rows if r["channel"] == first_channel
            )
            assert excl_spend in (0, 0.0), (
                f"{model_id}/exclude expected 0 spend for {first_channel}, "
                f"got {excl_spend}"
            )
        finally:
            await call(client, "delete_optimization", {"run_id": excl_run_id})

        # Reuse: identical submit returns the same run, flagged reused.
        again = await call(
            client,
            "run_future_optimization",
            {"model_id": model_id, "config": future_config},
        )
        assert again["reused"] is True and again["run_id"] == run_id, (
            f"reuse failed: {again}"
        )

        # Adversarial: non-future start_date reaches the service -> flat envelope.
        non_future_config = {
            "scenario": {"type": "fixed_budget"},
            "future": {
                "start_date": "2020-01-01",
                "horizon": 4,
                "reference": {"mode": "trailing"},
            },
        }
        non_future = await call(
            client,
            "run_future_optimization",
            {"model_id": model_id, "config": non_future_config},
        )
        assert_error(
            non_future,
            "invalid_optimization_config",
            f"{model_id}/future-non-future-start-date",
        )

        # Adversarial: unknown channel in cost_multipliers -> flat envelope.
        bad_channel_config = {
            "scenario": {"type": "fixed_budget"},
            "future": {
                "start_date": "2099-01-01",
                "horizon": 4,
                "reference": {"mode": "trailing"},
                "cost_multipliers": {"__no_such_channel__": 1.2},
            },
        }
        bad_channel = await call(
            client,
            "run_future_optimization",
            {"model_id": model_id, "config": bad_channel_config},
        )
        assert_error(
            bad_channel,
            "invalid_optimization_config",
            f"{model_id}/future-unknown-cost-multiplier-channel",
        )
    finally:
        # Always reap the happy-path run, even if a poll/reuse/adversarial
        # assertion above raised -- otherwise its on-disk artifacts (record/
        # state/result + fingerprint index pointer) would leak.
        deleted = await call(client, "delete_optimization", {"run_id": run_id})

    # delete_optimization removes it; a subsequent status lookup must 404 (typed).
    assert deleted.get("deleted") is True and deleted.get("run_id") == run_id, (
        f"delete failed: {deleted}"
    )
    gone = await call(client, "get_optimization_status", {"run_id": run_id})
    assert_error(
        gone, "optimization_run_not_found", f"{model_id}/deleted-future-run-status"
    )


async def assert_cloud_live_optimization(service, model_id: str) -> None:
    """Drive the OptimizationService directly to prove the CloudRunJobExecutor
    launch/liveness/cancel contract end-to-end (faked jobs.run, real worker).

    The MCP transport path for run_optimization is already covered by the local
    matrix; this gate targets the executor + worker + registry + launch + cancel
    contract, so it calls the service API directly.
    """
    import time

    config = {
        "scenario": {"type": "fixed_budget"},
        "constraint": {"mode": "global", "pct": 0.2},
    }
    submit = await service.run_optimization(model_id, config, compute_tier="cloud_cpu")
    run_id = submit["run_id"]
    assert submit["compute_tier_resolved"] == "cloud_cpu", (
        f"expected cloud_cpu tier, got {submit}"
    )
    assert submit["reused"] is False, f"fresh submit should not be reused: {submit}"

    status = None
    for _ in range(240):  # ~120s cap; real Meridian optimize takes several seconds
        status = service.get_status(run_id)
        if status["status"] in ("completed", "failed"):
            break
        time.sleep(0.5)
    assert status and status["status"] == "completed", (
        f"cloud run did not complete: {status}"
    )

    result = service.get_result(run_id)
    for key in (
        "summary",
        "channel_tables",
        "allocation",
        "spend_delta",
        "outcome_mode",
    ):
        assert key in result, f"result missing '{key}': {list(result.keys())}"
    assert {"initial", "optimized"} <= set(result["channel_tables"]), (
        f"missing channel tables: {list(result['channel_tables'])}"
    )

    # Reuse: identical submit returns the same run, flagged reused.
    again = await service.run_optimization(model_id, config, compute_tier="cloud_cpu")
    assert again["reused"] is True and again["run_id"] == run_id, (
        f"reuse failed: {again}"
    )

    # Cancel: a DIFFERENT config yields a fresh run; cancel must not raise. The
    # run may already be terminal (worker is fast), so accept canceled OR a
    # terminal status and report which.
    cancel_config = {
        "scenario": {"type": "fixed_budget"},
        "constraint": {"mode": "global", "pct": 0.35},
    }
    fresh = await service.run_optimization(
        model_id, cancel_config, compute_tier="cloud_cpu"
    )
    fresh_id = fresh["run_id"]
    assert fresh_id != run_id, f"cancel run should be fresh: {fresh}"
    service.cancel(fresh_id)  # must not raise
    final = service.get_status(fresh_id)["status"]
    assert final in ("canceled", "completed"), (
        f"unexpected post-cancel status (failed is not acceptable): {final}"
    )
    print(f"    cancel({model_id}) final status = {final}")


async def run_matrix(client) -> Report:
    from scripts.generate_validation_models import FULL_FUNNEL_VARIANTS, VARIANTS

    report = Report()
    for variant in [*VARIANTS, *FULL_FUNNEL_VARIANTS]:
        model_id = variant.key
        # Overview: must load and must prune ROI for no-revenue models.
        overview = await call(client, "get_model_overview", {"model_id": model_id})
        try:
            assert "available_tool_options" in overview, "no available_tool_options"
            cs_types = overview["available_tool_options"]["get_channel_summary"][
                "output_type"
            ]
            if not variant.factory_has_revenue():
                assert "roi" not in cs_types and "marginal_roi" not in cs_types, (
                    "roi advertised for no-revenue model"
                )
            report.ok(f"{model_id}/get_model_overview")
        except AssertionError as exc:
            report.fail(f"{model_id}/get_model_overview", str(exc))

        # Happy path: analysis tools that should return data.
        for tool, output_types in matrix.ANALYSIS_TOOLS.items():
            for output_type in output_types:
                if not matrix.expected_valid(variant, tool, output_type):
                    continue
                label = f"{model_id}/{tool}[{output_type}]"
                try:
                    payload = await call(
                        client, tool, {"model_id": model_id, "output_type": output_type}
                    )
                    assert_columnar(payload, label)
                    report.ok(label)
                except AssertionError as exc:
                    report.fail(label, str(exc))

        # Single-output new tools.
        for tool in ("get_model_fit", "get_channel_data"):
            label = f"{model_id}/{tool}"
            try:
                assert_columnar(await call(client, tool, {"model_id": model_id}), label)
                report.ok(label)
            except AssertionError as exc:
                report.fail(label, str(exc))

        # get_model_fit honors the geos filter end-to-end (transport→service→
        # facade→Meridian ModelFit). On multi-geo variants the filtered result
        # must differ from the unfiltered one; on national (1-geo) models a geo
        # filter equals no filter, so only validate shape.
        geo_names = overview.get("geo_names") or []
        if geo_names:
            label = f"{model_id}/get_model_fit[geo]"
            try:
                filtered = await call(
                    client,
                    "get_model_fit",
                    {"model_id": model_id, "filters": {"geos": [geo_names[0]]}},
                )
                assert_columnar(filtered, label)
                if len(geo_names) > 1:
                    unfiltered = await call(
                        client, "get_model_fit", {"model_id": model_id}
                    )
                    assert filtered["rows"] != unfiltered["rows"], (
                        f"{label}: geo filter not applied (rows identical to all-geo)"
                    )
                report.ok(label)
            except AssertionError as exc:
                report.fail(label, str(exc))

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

        if variant.with_rf:
            label = f"{model_id}/get_reach_frequency"
            try:
                assert_columnar(
                    await call(client, "get_reach_frequency", {"model_id": model_id}),
                    label,
                )
                report.ok(label)
            except AssertionError as exc:
                report.fail(label, str(exc))

        # Adversarial: typed errors.
        for case in matrix.adversarial_cases(variant):
            label = f"{model_id}/ADV/{case.tool}[{case.args.get('output_type', '')}]->{case.expected_error_code}"
            try:
                payload = await call(client, case.tool, case.args)
                assert_error(payload, case.expected_error_code, label)
                report.ok(label)
            except AssertionError as exc:
                report.fail(label, str(exc))

        # Live optimization: end-to-end subprocess worker for national and geo
        # revenue models and the full-funnel model. The full-funnel model gets a
        # longer poll cap: its optimizer scores every candidate through the
        # mediator models.
        if model_id in ("national-revenue", "geo-revenue", "geo-full-funnel"):
            is_ff = bool(getattr(variant, "mediators", ()))
            max_polls = FULL_FUNNEL_MAX_POLLS if is_ff else 120
            label = f"{model_id}/run_optimization[live,local,subprocess]"
            try:
                await assert_live_optimization(
                    client, model_id, overview=overview, max_polls=max_polls
                )
                report.ok(label)
            except AssertionError as exc:
                report.fail(label, str(exc))

            future_label = f"{model_id}/run_future_optimization[live,local,subprocess]"
            try:
                await assert_live_future_optimization(
                    client, model_id, overview=overview, max_polls=max_polls
                )
                report.ok(future_label)
            except AssertionError as exc:
                report.fail(future_label, str(exc))

        if getattr(variant, "mediators", ()):
            await assert_full_funnel_variant(client, report, variant, overview)
            label = f"{model_id}/full_funnel[future optimization assumptions]"
            try:
                await assert_full_funnel_optimization(
                    client, model_id, max_polls=FULL_FUNNEL_MAX_POLLS
                )
                report.ok(label)
            except AssertionError as exc:
                report.fail(label, str(exc))

        # Adversarial: result for unknown run_id must return typed error.
        if model_id == "national-revenue":
            label = "GLOBAL/ADV/result-not-found"
            try:
                payload = await call(
                    client, "get_optimization_result", {"run_id": "does-not-exist"}
                )
                assert_error(payload, "optimization_run_not_found", label)
                report.ok(label)
            except AssertionError as exc:
                report.fail(label, str(exc))

    # Global adversarial: unknown model id must return a typed error, not crash.
    label = "GLOBAL/ADV/unknown-model"
    try:
        payload = await call(
            client, "get_model_overview", {"model_id": "does-not-exist"}
        )
        assert_error(payload, None, label)
        report.ok(label)
    except AssertionError as exc:
        report.fail(label, str(exc))

    label = "GLOBAL/ADV/get_funnel_breakdown[single-model]->metric_not_supported"
    try:
        payload = await call(
            client,
            "get_funnel_breakdown",
            {"model_id": "geo-revenue", "output_type": "channel_breakdown"},
        )
        assert_error(payload, "metric_not_supported", label)
        report.ok(label)
    except AssertionError as exc:
        report.fail(label, str(exc))

    for tool, extra in (
        ("get_channel_data", {}),
        ("get_training_data", {"dataset": ["kpi"]}),
    ):
        label = f"GLOBAL/ADV/{tool}[unknown-model]->model_not_found"
        try:
            payload = await call(client, tool, {"model_id": "does-not-exist", **extra})
            assert_error(payload, "model_not_found", label)
            report.ok(label)
        except AssertionError as exc:
            report.fail(label, str(exc))

    return report


async def assert_full_funnel_variant(client, report, variant, overview) -> None:
    """Full-funnel checks over the wire (values are 6-sig-fig rounded)."""
    model_id = variant.key
    names = list(variant.mediator_names())
    labels = {n: f"{n} (brand equity, rest)" for n in names}
    driver = variant.mediators[0][1][0]  # a paid channel that drives a mediator

    label = f"{model_id}/full_funnel[overview]"
    try:
        assert overview.get("funnel") == "full_funnel", overview.get("funnel")
        got = [m["name"] for m in overview["full_funnel"]["mediators"]]
        assert got == names, got
        assert "get_funnel_breakdown" in overview["available_tool_options"]
        report.ok(label)
    except AssertionError as exc:
        report.fail(label, str(exc))

    contribution = await call(
        client,
        "get_contribution",
        {"model_id": model_id, "output_type": "contribution_metrics"},
    )

    label = f"{model_id}/full_funnel[contribution]"
    try:
        assert_columnar(contribution, label)
        by = {r["channel"]: r for r in _rows(contribution)}
        for n in names:
            assert labels[n] in by and n not in by, f"{n} not relabelled"
        row = by[driver]
        assert _close(
            row["incremental_outcome_direct"] + row["incremental_outcome_indirect"],
            row["incremental_outcome"],
        ), row
        assert row["incremental_outcome_indirect"] > 0, row
        report.ok(label)
    except AssertionError as exc:
        report.fail(label, str(exc))

    label = f"{model_id}/full_funnel[one baseline]"
    try:
        base_row = next(r for r in _rows(contribution) if r["channel"] == "baseline")
        summary = await call(
            client,
            "get_channel_summary",
            {"model_id": model_id, "output_type": "baseline_summary_metrics"},
        )
        mean = next(r for r in _rows(summary) if r["metric"] == "mean")
        assert _close(mean["baseline_outcome"], base_row["incremental_outcome"]), (
            mean,
            base_row,
        )
        report.ok(label)
    except AssertionError as exc:
        report.fail(label, str(exc))

    for output_type in ("channel_breakdown", "mediator_lift"):
        label = f"{model_id}/get_funnel_breakdown[{output_type}]"
        try:
            payload = await call(
                client,
                "get_funnel_breakdown",
                {"model_id": model_id, "output_type": output_type},
            )
            assert_columnar(payload, label)
            assert payload["row_count"] > 0, label
            report.ok(label)
        except AssertionError as exc:
            report.fail(label, str(exc))

    label = f"{model_id}/ADV/get_funnel_breakdown[unknown channel]->missing_model_data"
    try:
        payload = await call(
            client,
            "get_funnel_breakdown",
            {
                "model_id": model_id,
                "output_type": "channel_breakdown",
                "filters": {"channels": ["NOPE"]},
            },
        )
        assert_error(payload, "missing_model_data", label)
        report.ok(label)
    except AssertionError as exc:
        report.fail(label, str(exc))


async def assert_full_funnel_optimization(
    client, model_id: str, *, max_polls: int = FULL_FUNNEL_MAX_POLLS
) -> None:
    """The full-funnel-only optimization case: a future run.

    The historical run (and its direct + indirect == total check) is already made
    by ``assert_live_optimization``, so it is not repeated here. The generic
    future run starts 2099-01-01; this one starts 2099-01-05 so the two do not
    share a fingerprint, and asserts the mediator-treatment assumption.
    """
    future = await call(
        client,
        "run_future_optimization",
        {
            "model_id": model_id,
            "config": {
                "scenario": {"type": "fixed_budget"},
                "future": {"start_date": "2099-01-05", "horizon": 4},
            },
        },
    )
    assert "error_code" not in future, f"submit error: {future}"
    run_id = future["run_id"]
    try:
        status = await _await_run(client, run_id, max_polls=max_polls)
        assert status and status["status"] == "completed", (
            f"future run did not complete: {status}"
        )
        result = await call(client, "get_optimization_result", {"run_id": run_id})
        _assert_direct_plus_indirect(result, f"{model_id}/future optimization")
        treatment = result["assumptions"]["full_funnel"]["mediator_treatment"]
        assert treatment == "predicted_from_planned_spend", result["assumptions"]
    finally:
        await call(client, "delete_optimization", {"run_id": run_id})

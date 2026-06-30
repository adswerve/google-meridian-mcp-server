"""In-process MCP client driver and assertions for live validation."""

from __future__ import annotations

import dataclasses
import json

from scripts.validation import matrix


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


def _content_to_obj(result):
    if getattr(result, "structured_content", None) is not None:
        return result.structured_content
    if getattr(result, "data", None) is not None:
        return result.data
    block = result.content[0]
    text = getattr(block, "text", block)
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return text


def _unwrap(obj):
    if isinstance(obj, dict) and set(obj.keys()) == {"result"}:
        return obj["result"]
    return obj


async def call(client, name, args):
    res = await client.call_tool(name, args)
    return _unwrap(_content_to_obj(res))


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


async def assert_live_optimization(client, model_id: str, *, overview) -> None:
    import asyncio

    config = {"scenario": {"type": "fixed_budget"}, "constraint": {"mode": "global", "pct": 0.2}}
    submit = await call(client, "run_optimization", {"model_id": model_id, "config": config})
    assert "error_code" not in submit, f"submit error: {submit}"
    run_id = submit["run_id"]
    assert submit["compute_tier_resolved"] == "local", f"expected local tier, got {submit}"

    status = None
    for _ in range(120):  # tiny fixtures finish fast; cap ~60s
        status = await call(client, "get_optimization_status", {"run_id": run_id})
        if status["status"] in ("completed", "failed"):
            break
        await asyncio.sleep(0.5)
    assert status and status["status"] == "completed", f"run did not complete: {status}"

    result = await call(client, "get_optimization_result", {"run_id": run_id})
    for key in ("summary", "channel_tables", "allocation", "spend_delta", "outcome_mode"):
        assert key in result, f"result missing '{key}': {result.keys()}"
    assert {"initial", "optimized"} <= set(result["channel_tables"]), "missing channel tables"

    # Reuse: identical submit returns the same run, flagged reused.
    again = await call(client, "run_optimization", {"model_id": model_id, "config": config})
    assert again["reused"] is True and again["run_id"] == run_id, f"reuse failed: {again}"


def assert_cloud_live_optimization(service, model_id: str) -> None:
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
    submit = service.run_optimization(model_id, config, compute_tier="cloud_cpu")
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
    for key in ("summary", "channel_tables", "allocation", "spend_delta", "outcome_mode"):
        assert key in result, f"result missing '{key}': {list(result.keys())}"

    # Reuse: identical submit returns the same run, flagged reused.
    again = service.run_optimization(model_id, config, compute_tier="cloud_cpu")
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
    fresh = service.run_optimization(model_id, cancel_config, compute_tier="cloud_cpu")
    fresh_id = fresh["run_id"]
    assert fresh_id != run_id, f"cancel run should be fresh: {fresh}"
    service.cancel(fresh_id)  # must not raise
    final = service.get_status(fresh_id)["status"]
    assert final in ("canceled", "completed", "failed"), (
        f"unexpected post-cancel status: {final}"
    )
    print(f"    cancel({model_id}) final status = {final}")


async def run_matrix(client) -> Report:
    from scripts.generate_validation_models import VARIANTS

    report = Report()
    for variant in VARIANTS:
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

        # Live optimization: end-to-end subprocess worker for national and geo revenue models.
        if model_id in ("national-revenue", "geo-revenue"):
            label = f"{model_id}/run_optimization[live,local,subprocess]"
            try:
                await assert_live_optimization(client, model_id, overview=overview)
                report.ok(label)
            except AssertionError as exc:
                report.fail(label, str(exc))

        # Adversarial: result for unknown run_id must return typed error.
        if model_id == "national-revenue":
            label = "GLOBAL/ADV/result-not-found"
            try:
                payload = await call(client, "get_optimization_result",
                                     {"run_id": "does-not-exist"})
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

    # Loader smoke: the .pkl fixture must load through the pickle branch.
    label = "GLOBAL/loader-pkl/national-revenue-pkl"
    try:
        overview = await call(
            client, "get_model_overview", {"model_id": "national-revenue-pkl"}
        )
        assert "available_tool_options" in overview, (
            f"{label}: pkl model failed to load"
        )
        report.ok(label)
    except AssertionError as exc:
        report.fail(label, str(exc))

    return report

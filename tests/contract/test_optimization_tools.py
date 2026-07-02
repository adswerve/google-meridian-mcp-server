"""Contract tests for optimization MCP tools registration and annotations."""

from __future__ import annotations

import pytest

from google_meridian_mcp_server.server import create_server


@pytest.mark.asyncio
async def test_optimization_tools_registered():
    mcp = create_server()
    tools = {t.name for t in await mcp.list_tools()}
    assert {
        "run_optimization",
        "get_optimization_status",
        "get_optimization_result",
        "list_optimizations",
        "delete_optimization",
    } <= tools


@pytest.mark.asyncio
async def test_run_optimization_annotations_not_readonly():
    mcp = create_server()
    by_name = {t.name: t for t in await mcp.list_tools()}
    # run_optimization has no annotations (bare @mcp.tool) — not read-only
    run_opt_annotations = by_name["run_optimization"].annotations
    assert run_opt_annotations is None or run_opt_annotations.readOnlyHint is not True
    assert by_name["get_optimization_status"].annotations.readOnlyHint is True
    assert by_name["get_optimization_result"].annotations.readOnlyHint is True
    assert by_name["list_optimizations"].annotations.readOnlyHint is True
    delete_ann = by_name["delete_optimization"].annotations
    assert delete_ann is None or delete_ann.readOnlyHint is not True


@pytest.mark.asyncio
async def test_cancel_tool_registered_not_readonly():
    mcp = create_server()
    by_name = {t.name: t for t in await mcp.list_tools()}
    assert "cancel_optimization" in by_name
    cancel_ann = by_name["cancel_optimization"].annotations
    assert cancel_ann is None or cancel_ann.readOnlyHint is not True


def _prop(schema: dict, name: str) -> dict:
    return schema["properties"][name]


def _enum_values(prop: dict) -> set[str]:
    # Literal[...] renders either as a top-level "enum" or, for Optional,
    # inside an "anyOf" branch that carries the "enum".
    if "enum" in prop:
        return set(prop["enum"])
    for branch in prop.get("anyOf", []):
        if "enum" in branch:
            return set(branch["enum"])
    return set()


@pytest.mark.asyncio
async def test_compute_tier_and_status_are_enums():
    mcp = create_server()
    by_name = {t.name: t for t in await mcp.list_tools()}

    tier = _prop(by_name["run_optimization"].to_mcp_tool().inputSchema, "compute_tier")
    assert _enum_values(tier) == {"auto", "local", "cloud_cpu", "cloud_gpu"}
    assert tier.get("default") == "auto"

    status = _prop(by_name["list_optimizations"].to_mcp_tool().inputSchema, "status")
    assert _enum_values(status) == {
        "queued",
        "running",
        "completed",
        "failed",
        "canceled",
    }
    # status is optional: omitting it returns all states.
    assert status.get("default") is None
    assert any(branch.get("type") == "null" for branch in status.get("anyOf", []))

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

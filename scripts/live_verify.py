"""Live adversarial MCP verification against a real Meridian model.

Usage:
  uv run python scripts/live_verify.py          # uses .env (gcs backend)
  MERIDIAN_VERIFY_LOCAL=1 uv run python scripts/live_verify.py  # local backend
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

from fastmcp import Client


def _content_to_obj(result):
    """Extract the structured/text payload from a FastMCP tool result."""
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
    """FastMCP may wrap a list return under {'result': [...]}; normalize it."""
    if isinstance(obj, dict) and set(obj.keys()) == {"result"}:
        return obj["result"]
    return obj


async def call(client, name, args):
    res = await client.call_tool(name, args)
    return _unwrap(_content_to_obj(res))


def assert_columnar(payload, tool):
    assert isinstance(payload, dict), f"{tool}: expected dict, got {type(payload)}"
    assert "error_code" not in payload, f"{tool}: unexpected error {payload}"
    for key in ("model_id", "columns", "rows", "row_count"):
        assert key in payload, f"{tool}: missing '{key}'"
    assert payload["row_count"] == len(payload["rows"]), f"{tool}: row_count mismatch"
    for row in payload["rows"]:
        assert len(row) == len(payload["columns"]), f"{tool}: ragged row"
    assert "data" not in payload and "result_metadata" not in payload, (
        f"{tool}: legacy keys present"
    )
    print(f"  OK {tool}: {payload['row_count']} rows x {len(payload['columns'])} cols")


async def run():
    from google_meridian_mcp_server.server import mcp

    async with Client(mcp) as client:
        # --- discovery ---
        models = await call(client, "list_models", {})
        assert isinstance(models, list) and models, f"list_models empty: {models}"
        model_id = models[0]["model_id"]
        print(f"Backend models: {[m['model_id'] for m in models]}; using {model_id!r}")

        overview = await call(client, "get_model_overview", {"model_id": model_id})
        assert "available_tool_options" in overview, "overview missing tool options"
        assert "result_metadata" not in overview, "overview still has result_metadata"
        opts = overview["available_tool_options"]
        print(f"  OK get_model_overview: model_type={overview.get('model_type')}")

        # --- happy path: every tool, every output_type the model supports ---
        datasets = opts["get_training_data"]["dataset"][:2] or ["kpi"]
        assert_columnar(
            await call(
                client, "get_training_data", {"model_id": model_id, "dataset": datasets}
            ),
            "get_training_data",
        )
        for tool in (
            "get_channel_summary",
            "get_contribution",
            "get_adstock_decay",
            "get_response_curves",
        ):
            for output_type in opts[tool]["output_type"]:
                assert_columnar(
                    await call(
                        client,
                        tool,
                        {"model_id": model_id, "output_type": output_type},
                    ),
                    f"{tool}[{output_type}]",
                )

        # --- adversarial: every call must return a clean error, never crash ---
        print("Adversarial pass:")
        cases = [
            ("get_model_overview", {"model_id": "does-not-exist"}),
            ("get_model_overview", {"model_id": "   "}),
            ("get_channel_summary", {"model_id": model_id, "output_type": "nonsense"}),
            ("get_training_data", {"model_id": model_id, "dataset": ["bogus"]}),
            (
                "get_channel_summary",
                {
                    "model_id": model_id,
                    "output_type": "roi",
                    "filters": {"unexpected_field": True},
                },
            ),
            (
                "get_contribution",
                {
                    "model_id": model_id,
                    "output_type": "contribution_metrics",
                    "filters": {"geos": ["__no_such_geo__"]},
                },
            ),
        ]
        for tool, args in cases:
            try:
                payload = await call(client, tool, args)
            except Exception as exc:  # tool-input validation may raise client-side
                print(f"  OK {tool} {args}: rejected ({type(exc).__name__})")
                continue
            if isinstance(payload, dict) and "error_code" in payload:
                print(f"  OK {tool}: error_code={payload['error_code']}")
            elif isinstance(payload, dict) and "columns" in payload:
                # empty-geo filter is allowed to succeed with zero rows
                print(f"  OK {tool}: handled gracefully ({payload['row_count']} rows)")
            else:
                raise AssertionError(f"{tool} {args}: unexpected payload {payload}")

    print("LIVE VERIFICATION PASSED")


if __name__ == "__main__":
    if os.getenv("MERIDIAN_VERIFY_LOCAL"):
        os.environ["PERSISTENCE_BACKEND"] = "local"
        os.environ["LOCAL_MODELS_ROOT"] = os.getenv("LOCAL_MODELS_ROOT", "./models")
    asyncio.run(run())
    sys.exit(0)

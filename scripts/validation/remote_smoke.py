"""Live smoke test against a DEPLOYED Meridian MCP server (streamable-http).

Usage:
  uv run python -m scripts.validation.remote_smoke --url https://<service>.run.app
  uv run python -m scripts.validation.remote_smoke --url ... --run-optimization \
      --model-id <id>

Exits non-zero on any failure. Read-only by default; --run-optimization launches
a REAL cloud optimization job and polls it to completion.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from fastmcp import Client


def normalize_mcp_url(base: str) -> str:
    """Return the streamable-http endpoint URL for a service base URL.

    The endpoint has NO trailing slash. FastMCP serves streamable-http at
    ``/mcp``; requesting ``/mcp/`` returns a 307 redirect to ``/mcp``, and
    behind a TLS-terminating proxy (Cloud Run) that redirect's Location is
    ``http://`` — which breaks the POST. Targeting ``/mcp`` directly avoids it.
    """
    base = base.rstrip("/")
    if base.endswith("/mcp"):
        return base
    return base + "/mcp"


def _data(result):
    """Extract the structured payload from a FastMCP CallToolResult."""
    return getattr(result, "data", result)


async def _run(url: str, model_id: str | None, run_opt: bool, poll_timeout: int) -> int:
    endpoint = normalize_mcp_url(url)
    print(f"Connecting to {endpoint}")
    async with Client(endpoint) as client:
        tools = [t.name for t in await client.list_tools()]
        print(f"Tools: {sorted(tools)}")
        for required in ("list_models", "get_model_overview", "run_optimization"):
            if required not in tools:
                print(f"FAIL: deployed server missing tool {required}")
                return 1

        models = _data(await client.call_tool("list_models", {}))
        print(f"list_models -> {models}")
        if not models:
            print("FAIL: no models returned by deployed server")
            return 1

        resolved = model_id or (
            models[0]["model_id"] if isinstance(models[0], dict) else models[0]
        )
        overview = _data(
            await client.call_tool("get_model_overview", {"model_id": resolved})
        )
        if not overview or (isinstance(overview, dict) and overview.get("error")):
            print(f"FAIL: get_model_overview errored: {overview}")
            return 1
        print(f"get_model_overview({resolved}) OK")

        if not run_opt:
            print("PASS: read-only smoke test")
            return 0

        started = _data(
            await client.call_tool(
                "run_optimization",
                {
                    "model_id": resolved,
                    "config": {
                        "scenario": {"type": "fixed_budget"},
                        "constraint": {"mode": "global", "pct": 0.3},
                    },
                    "compute_tier": "cloud_cpu",
                    "label": "remote-smoke",
                },
            )
        )
        run_id = started.get("run_id") if isinstance(started, dict) else None
        if not run_id:
            print(f"FAIL: run_optimization did not return a run_id: {started}")
            return 1
        print(f"run_optimization -> run_id={run_id}; polling...")

        waited = 0
        interval = 10
        while waited < poll_timeout:
            status = _data(
                await client.call_tool("get_optimization_status", {"run_id": run_id})
            )
            state = status.get("status") if isinstance(status, dict) else None
            print(f"  [{waited}s] status={state}")
            if state == "completed":
                result = _data(
                    await client.call_tool(
                        "get_optimization_result", {"run_id": run_id}
                    )
                )
                ok = bool(result) and not (
                    isinstance(result, dict) and result.get("error")
                )
                print("PASS: cloud optimization completed" if ok else f"FAIL: {result}")
                return 0 if ok else 1
            if state == "failed":
                print(f"FAIL: optimization failed: {status}")
                return 1
            await asyncio.sleep(interval)
            waited += interval

        print(f"FAIL: optimization did not finish within {poll_timeout}s")
        return 1


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Smoke-test a deployed Meridian MCP server."
    )
    p.add_argument("--url", default=os.getenv("MERIDIAN_MCP_URL"))
    p.add_argument("--model-id", default=None)
    p.add_argument("--run-optimization", action="store_true")
    p.add_argument("--poll-timeout", type=int, default=1800)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    if not args.url:
        print("FAIL: provide --url or set MERIDIAN_MCP_URL")
        return 2
    return asyncio.run(
        _run(args.url, args.model_id, args.run_optimization, args.poll_timeout)
    )


if __name__ == "__main__":
    raise SystemExit(main())

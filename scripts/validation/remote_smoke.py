"""Live smoke test against a DEPLOYED Meridian MCP server (streamable-http).

Usage:
  uv run python -m scripts.validation.remote_smoke --url https://<service>.run.app
  uv run python -m scripts.validation.remote_smoke --url ... --run-optimization \
      --model-id <id> --compute-tier cloud_gpu --force-rerun

Exits non-zero on any failure. Read-only by default; --run-optimization launches
a REAL cloud optimization job and polls it to completion.

Set MCP_AUTH_TOKEN (`gcloud auth print-identity-token`) when the deployed
service requires an identity token; the client is built by capture_baseline's
build_client, which attaches and re-mints it.

--force-rerun matters when smoking a second tier against the same deployment:
config_fingerprint hashes (model_id, config, meridian_version) and EXCLUDES
compute_tier, so a cloud_gpu smoke that reuses the cloud_cpu run would report
PASS without the GPU job ever executing. The tier assertion below is the
backstop for that.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from scripts.validation.payloads import extract


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


async def _run(
    url: str,
    model_id: str | None,
    run_opt: bool,
    poll_timeout: int,
    compute_tier: str,
    force_rerun: bool,
) -> int:
    # Imported here, not at module scope: capture_baseline imports
    # normalize_mcp_url from this module, so a top-level import is circular.
    from scripts.validation.capture_baseline import build_client

    endpoint = normalize_mcp_url(url)
    authed = "with" if os.environ.get("MCP_AUTH_TOKEN") else "without"
    print(f"Connecting to {endpoint} ({authed} MCP_AUTH_TOKEN)")
    async with build_client("http", url) as client:
        tools = [t.name for t in await client.list_tools()]
        print(f"Tools: {sorted(tools)}")
        for required in (
            "list_models",
            "get_model_overview",
            "run_optimization",
            "get_optimization_status",
            "get_optimization_result",
        ):
            if required not in tools:
                print(f"FAIL: deployed server missing tool {required}")
                return 1

        models = extract(await client.call_tool("list_models", {}))
        print(f"list_models -> {models}")
        if not models:
            print("FAIL: no models returned by deployed server")
            return 1

        resolved = model_id or (
            models[0]["model_id"] if isinstance(models[0], dict) else models[0]
        )
        overview = extract(
            await client.call_tool("get_model_overview", {"model_id": resolved})
        )
        if not overview or (isinstance(overview, dict) and overview.get("error")):
            print(f"FAIL: get_model_overview errored: {overview}")
            return 1
        print(f"get_model_overview({resolved}) OK")

        if not run_opt:
            print("PASS: read-only smoke test")
            return 0

        started = extract(
            await client.call_tool(
                "run_optimization",
                {
                    "model_id": resolved,
                    "config": {
                        "scenario": {"type": "fixed_budget"},
                        "constraint": {"mode": "global", "pct": 0.3},
                    },
                    "compute_tier": compute_tier,
                    "force_rerun": force_rerun,
                    "label": f"remote-smoke-{compute_tier}",
                },
            )
        )
        run_id = started.get("run_id") if isinstance(started, dict) else None
        if not run_id:
            print(f"FAIL: run_optimization did not return a run_id: {started}")
            return 1
        resolved = started.get("compute_tier_resolved")
        if resolved != compute_tier:
            print(
                f"FAIL: requested compute_tier={compute_tier!r} but the server "
                f"resolved {resolved!r}: {started}"
            )
            return 1
        if started.get("reused"):
            print(
                f"FAIL: run {run_id} was reused, so no {compute_tier} execution "
                f"happened; pass --force-rerun: {started}"
            )
            return 1
        print(f"run_optimization -> run_id={run_id} tier={resolved}; polling...")

        waited = 0
        interval = 10
        while waited < poll_timeout:
            status = extract(
                await client.call_tool("get_optimization_status", {"run_id": run_id})
            )
            state = status.get("status") if isinstance(status, dict) else None
            print(f"  [{waited}s] status={state}")
            if state == "completed":
                result = extract(
                    await client.call_tool(
                        "get_optimization_result", {"run_id": run_id}
                    )
                )
                ok = bool(result) and not (
                    isinstance(result, dict) and result.get("error")
                )
                print(
                    f"PASS: cloud optimization completed ({compute_tier})"
                    if ok
                    else f"FAIL: {result}"
                )
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
    p.add_argument(
        "--compute-tier",
        default="cloud_cpu",
        choices=["cloud_cpu", "cloud_gpu", "auto"],
    )
    p.add_argument("--force-rerun", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    if not args.url:
        print("FAIL: provide --url or set MERIDIAN_MCP_URL")
        return 2
    return asyncio.run(
        _run(
            args.url,
            args.model_id,
            args.run_optimization,
            args.poll_timeout,
            args.compute_tier,
            args.force_rerun,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())

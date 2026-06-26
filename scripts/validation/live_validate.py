"""Live validation: build fixtures if missing, run the matrix, exit non-zero on failure.

Usage:
  uv run python -m scripts.validation.live_validate
  uv run python -m scripts.validation.live_validate --force   # rebuild fixtures
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from scripts.generate_validation_models import DEFAULT_OUT_ROOT, build_all
from scripts.validation.runner import run_matrix


def _ensure_fixtures(force: bool) -> None:
    build_all(DEFAULT_OUT_ROOT, force=force)


async def _run() -> int:
    os.environ["PERSISTENCE_BACKEND"] = "local"
    os.environ["LOCAL_MODELS_ROOT"] = str(DEFAULT_OUT_ROOT)
    os.environ.setdefault("RESULT_CACHE_ENABLED", "false")

    from fastmcp import Client

    from google_meridian_mcp_server.server import mcp

    async with Client(mcp) as client:
        report = await run_matrix(client)

    print(f"\n{len(report.passed)} passed, {len(report.failed)} failed")
    if report.failed:
        print("FAILURES:")
        for item in report.failed:
            print(f"  - {item}")
        return 1
    print("LIVE VALIDATION PASSED")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="Rebuild fixtures first")
    args = parser.parse_args()
    if not (DEFAULT_OUT_ROOT.exists() and any(DEFAULT_OUT_ROOT.iterdir())) or args.force:
        _ensure_fixtures(args.force)
    sys.exit(asyncio.run(_run()))


if __name__ == "__main__":
    main()

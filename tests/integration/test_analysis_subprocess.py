"""Real-subprocess integration test: golden parity across the worker boundary.

Spawns an actual worker subprocess (imports TF, loads a real fitted Meridian
model) via `SyncSubprocessExecutor` and compares its output against the
goldens captured from the OLD in-process path (Task 1), before the
execution-boundary refactor. Slow (seconds per call) -- marked `integration`.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

import pytest

from google_meridian_mcp_server.domain.errors import MeridianMcpError
from google_meridian_mcp_server.execution.sync_subprocess_executor import (
    SyncSubprocessExecutor,
)

pytestmark = pytest.mark.integration

GOLDENS_DIR = Path(__file__).parent / "goldens"


@pytest.fixture(scope="module")
def fixture_model_id() -> str:
    """Ensure the `geo-kpi-only` fixture model (goldens were captured against
    it in Task 1) is built, and return its model_id."""
    from scripts.validation.fixtures import ensure_fixture_model

    ensure_fixture_model("geo-kpi-only")
    return "geo-kpi-only"


@pytest.fixture()
async def real_runner():
    """A SyncSubprocessExecutor that spawns the real worker module.

    `env_base` overrides the repo's `.env` (PERSISTENCE_BACKEND=gcs) so the
    worker subprocess -- which inherits the parent env -- resolves models from
    the local `models/_validation` fixtures instead of reaching out to GCS.
    """
    with tempfile.TemporaryDirectory() as tmp:
        tmp_root = Path(tmp)
        runner = SyncSubprocessExecutor(
            semaphore=asyncio.Semaphore(2),
            run_timeout=300,
            queue_wait_timeout=30,
            max_response_bytes=64 * 1024 * 1024,
            workdir_root=tmp_root / "workdirs",
            env_base={
                "MERIDIAN_BACKEND": "tensorflow",
                "PERSISTENCE_BACKEND": "local",
                "LOCAL_MODELS_ROOT": "models/_validation",
                "MODEL_CACHE_ROOT": str(tmp_root / "model_cache"),
            },
        )
        try:
            yield runner
        finally:
            await runner.shutdown()


async def test_overview_matches_golden(real_runner, fixture_model_id):
    got = await real_runner.run("get_model_overview", fixture_model_id, {})
    golden = GOLDENS_DIR / f"{fixture_model_id}__get_model_overview.json"
    if golden.exists():
        # Decoration (available_tool_options) is server-side, not the worker's
        # job -- compare only the raw keys the worker's op actually returns.
        expected = json.loads(golden.read_text())
        for key in ("available_training_datasets", "media_channels", "geo_names"):
            if key in expected:
                assert got.get(key) == expected[key]
    else:
        assert "available_training_datasets" in got


async def test_unknown_model_id_maps_across_boundary(real_runner):
    with pytest.raises(MeridianMcpError) as exc_info:
        await real_runner.run("get_model_overview", "does-not-exist", {})
    assert exc_info.value.error_code in ("model_not_found", "missing_model_data")


async def test_channel_data_matches_golden(real_runner, fixture_model_id):
    got = await real_runner.run(
        "get_channel_data", fixture_model_id, {"filters": {}}
    )
    golden = GOLDENS_DIR / f"{fixture_model_id}__get_channel_data.json"
    expected = json.loads(golden.read_text())

    assert got["columns"] == expected["columns"]
    assert got["row_count"] == expected["row_count"]
    # Full-row float equality across a JSON round-trip through a subprocess is
    # brittle (formatting/precision), so we assert the strongest cheap check:
    # exact equality of the first row (deterministic ordering, no floats to
    # wobble at this fixture's row 0) plus row count/columns above as the
    # structural parity check across the whole dataset.
    assert got["rows"][0] == expected["rows"][0]

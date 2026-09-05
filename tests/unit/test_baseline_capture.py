"""Snapshot layout, atomic writes, resumability and failure accounting."""

import json
import os

import pytest

from scripts.validation import capture_baseline as cb
from scripts.validation.matrix import ToolCase

CASE = ToolCase("get_channel_summary", "roi", {}, frozenset(), "service")


class _Result:
    def __init__(self, payload):
        self.structured_content = payload
        self.data = None
        self.content = []


def test_snapshot_path_is_label_variant_tool_case(tmp_path):
    path = cb.case_path(tmp_path, "v1.7-engine", "geo-revenue", CASE)
    assert (
        path
        == tmp_path / "v1.7-engine" / "geo-revenue" / "get_channel_summary__roi.json"
    )


def test_write_snapshot_normalizes_volatile_fields(tmp_path):
    path = tmp_path / "a" / "b.json"
    cb.write_snapshot(path, {"run_id": "m-20260904T101500-a1b2c3", "rows": [[1.0]]})
    assert json.loads(path.read_text()) == {"run_id": "<run_id>", "rows": [[1.0]]}


def test_write_snapshot_leaves_no_temp_file_behind(tmp_path):
    path = tmp_path / "b.json"
    cb.write_snapshot(path, {"ok": True})
    assert sorted(p.name for p in tmp_path.iterdir()) == ["b.json"]


def test_write_snapshot_overwrites_in_place(tmp_path):
    path = tmp_path / "b.json"
    cb.write_snapshot(path, {"v": 1})
    cb.write_snapshot(path, {"v": 2})
    assert json.loads(path.read_text()) == {"v": 2}


def test_prepare_env_isolates_the_registry(tmp_path, monkeypatch):
    monkeypatch.setenv("RESULT_CACHE_ENABLED", "true")
    monkeypatch.setenv("OPTIMIZATION_RUNS_ROOT", "/somewhere/shared")
    cb.prepare_env(str(tmp_path / "runs"))
    assert os.environ["OPTIMIZATION_RUNS_ROOT"] == str(tmp_path / "runs")
    assert os.environ["RESULT_CACHE_ENABLED"] == "false"
    assert os.environ["PERSISTENCE_BACKEND"] == "local"
    assert os.environ["REGISTRY_BACKEND"] == "local"
    assert os.environ["OPTIMIZATION_ALLOWED_TIERS"] == "local"


def test_worker_env_reports_what_the_workers_actually_get():
    env = cb.worker_env()
    assert "PATH" in env
    # Phase 0 is still on Meridian 1.7, where server.py defaults this to
    # tensorflow; from Phase 4 the module constant makes it jax. Either way it
    # must be PRESENT, because the manifest records it.
    assert "MERIDIAN_BACKEND" in env or "TF_CPP_MIN_LOG_LEVEL" in env


def test_selected_cases_honours_tool_and_case_filters():
    cases = [
        ToolCase("get_model_fit", "default", {}, frozenset(), "service"),
        ToolCase("get_model_fit", "geo_filter", {}, frozenset(), "service"),
        ToolCase("get_channel_data", "all", {}, frozenset(), "service"),
    ]
    assert [
        c.name
        for c in cb.selected_cases(cases, tools=["get_model_fit"], cases_filter=None)
    ] == ["default", "geo_filter"]
    assert [
        c.tool for c in cb.selected_cases(cases, tools=None, cases_filter=["all"])
    ] == ["get_channel_data"]
    assert len(cb.selected_cases(cases, tools=None, cases_filter=None)) == 3


async def test_call_tool_returns_error_envelopes_as_data():
    """A typed error envelope IS the result. metric_not_supported silently
    becoming a different code is a breaking change for every consuming agent."""

    class _Client:
        async def call_tool(self, name, args):
            return _Result({"error_code": "metric_not_supported"})

    payload = await cb.call_tool(_Client(), "get_channel_summary", {})
    assert payload == {"error_code": "metric_not_supported"}


async def test_call_tool_raises_on_a_transport_error_instead_of_snapshotting_it():
    """A blip must NOT be written to disk: the file's existence would make the
    resumed rerun skip the case, permanently."""

    class _Client:
        async def call_tool(self, name, args):
            raise TimeoutError("read timed out")

    with pytest.raises(cb.CaptureFailure, match="TimeoutError"):
        await cb.call_tool(_Client(), "get_model_fit", {})


async def test_optimization_case_passes_force_rerun_and_the_requested_tier():
    calls = []

    class _Client:
        async def call_tool(self, name, args):
            calls.append((name, args))
            if name in ("run_optimization", "run_future_optimization"):
                return _Result({"run_id": "r1", "compute_tier_resolved": "cloud_gpu"})
            if name == "get_optimization_status":
                return _Result({"status": "completed"})
            return _Result({"ok": True})

    out = await cb.run_optimization_case(
        _Client(), "run_optimization", {"model_id": "m"}, compute_tier="cloud_gpu"
    )
    submit_args = calls[0][1]
    assert submit_args["force_rerun"] is True
    assert submit_args["compute_tier"] == "cloud_gpu"
    assert out["submit"]["compute_tier_resolved"] == "cloud_gpu"
    assert ("delete_optimization", {"run_id": "r1"}) in calls


async def test_optimization_case_omits_compute_tier_when_not_requested():
    calls = []

    class _Client:
        async def call_tool(self, name, args):
            calls.append((name, args))
            if name == "run_optimization":
                return _Result({"run_id": "r1"})
            if name == "get_optimization_status":
                return _Result({"status": "completed"})
            return _Result({"ok": True})

    await cb.run_optimization_case(
        _Client(), "run_optimization", {"model_id": "m"}, compute_tier=None
    )
    assert "compute_tier" not in calls[0][1]


async def test_lifecycle_case_survives_a_submit_that_returns_an_error_envelope():
    class _Client:
        async def call_tool(self, name, args):
            return _Result({"error_code": "invalid_optimization_config"})

    out = await cb.run_lifecycle_case(_Client(), "m", "cancel", compute_tier=None)
    assert out == {"submit": {"error_code": "invalid_optimization_config"}}

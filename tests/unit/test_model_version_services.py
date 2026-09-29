"""model_version joins cache keys and fingerprints; unknown models fail fast."""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace

import pytest

from google_meridian_mcp_server.domain.errors import ModelNotFoundError
from google_meridian_mcp_server.domain.models import (
    MediatorFile,
    ModelCatalogEntry,
    RuntimeConfig,
)
from google_meridian_mcp_server.domain.optimization import (
    OptimizationRunState,
    RunStatus,
)
from google_meridian_mcp_server.persistence.cache import ResultCache
from google_meridian_mcp_server.persistence.optimization_run_registry import (
    LocalOptimizationRunRegistry,
)
from google_meridian_mcp_server.services.analysis_service import AnalysisService
from google_meridian_mcp_server.services.optimization_service import OptimizationService
from google_meridian_mcp_server.transport import tools as tools_module


def _entry(version="v1", mediators=()):
    return ModelCatalogEntry(
        model_id="m",
        display_name="M",
        source_backend="local",
        source_path="/r/m/model.binpb",
        model_format="binpb",
        mediators=mediators,
        model_version=version,
    )


class _Discovery:
    def __init__(self, *entries_per_call):
        self.entries = list(entries_per_call)
        self.invalidated = 0

    def get_model(self, model_id):
        return self.entries[0] if len(self.entries) == 1 else self.entries.pop(0)

    def invalidate(self):
        self.invalidated += 1


class _Runner:
    def __init__(self, result=None):
        self.calls = []
        self._result = result or {
            "model_id": "m",
            "columns": [],
            "rows": [],
            "row_count": 0,
        }

    async def run(self, operation, model_id, params):
        self.calls.append((operation, model_id, params))
        return self._result


async def test_cache_key_changes_with_model_version():
    runner, cache, discovery = (
        _Runner(),
        ResultCache(enabled=True),
        _Discovery(_entry("v1")),
    )
    svc = AnalysisService(runner, result_cache=cache, discovery=discovery)
    await svc.get_model_fit("m", None)
    await svc.get_model_fit("m", None)
    assert len(runner.calls) == 1  # same version -> cache hit
    discovery.entries = [_entry("v2")]
    await svc.get_model_fit("m", None)
    assert len(runner.calls) == 2  # replaced file -> miss


async def test_funnel_breakdown_cache_key_changes_with_model_version():
    runner, cache, discovery = (
        _Runner(),
        ResultCache(enabled=True),
        _Discovery(_entry("v1")),
    )
    svc = AnalysisService(runner, result_cache=cache, discovery=discovery)
    await svc.get_funnel_breakdown("m", "channel_breakdown", None)
    await svc.get_funnel_breakdown("m", "channel_breakdown", None)
    assert len(runner.calls) == 1
    discovery.entries = [_entry("v2")]
    await svc.get_funnel_breakdown("m", "channel_breakdown", None)
    assert len(runner.calls) == 2


async def test_model_version_is_not_sent_to_the_worker():
    runner = _Runner()
    await AnalysisService(runner, discovery=_Discovery(_entry("v1"))).get_model_fit(
        "m", None
    )
    assert "model_version" not in runner.calls[0][2]


async def test_unknown_model_invalidates_retries_once_then_raises_without_worker():
    runner, discovery = _Runner(), _Discovery(None)
    with pytest.raises(ModelNotFoundError):
        await AnalysisService(runner, discovery=discovery).get_model_fit("nope", None)
    assert discovery.invalidated == 1
    assert runner.calls == []


async def test_model_uploaded_since_last_refresh_is_found_after_invalidate():
    runner, discovery = _Runner(), _Discovery(None, _entry("v1"))
    await AnalysisService(runner, discovery=discovery).get_model_fit("m", None)
    assert discovery.invalidated == 1 and len(runner.calls) == 1


async def test_lookup_runs_discovery_off_the_event_loop(monkeypatch):
    import asyncio

    from google_meridian_mcp_server.services import model_catalog_service as mcs

    seen = []
    real = asyncio.to_thread

    async def _spy(fn, *args, **kwargs):
        seen.append(getattr(fn, "__name__", fn))
        return await real(fn, *args, **kwargs)

    monkeypatch.setattr(mcs.asyncio, "to_thread", _spy)
    await mcs.lookup_catalog_entry(_Discovery(_entry("v1")), "m")
    assert seen == ["get_model"]


async def test_without_discovery_cache_keys_are_unchanged():
    runner, cache = _Runner(), ResultCache(enabled=True)
    svc = AnalysisService(runner, result_cache=cache)
    await svc.get_model_fit("m", None)
    op, model_id, params = runner.calls[0]
    assert cache.get(op, model_id, params) is not None  # legacy key, no version


def _preflight():
    return {
        "channel_order": ["A"],
        "has_revenue_per_kpi": True,
        "use_kpi": False,
        "size_features": {
            "n_geos": 1,
            "n_time_units": 1,
            "n_channels": 1,
            "n_posterior_samples": 1,
        },
        "validation_error": None,
    }


class _Executor:
    def submit(self, run):
        pass

    def pump(self):
        pass


def _opt_svc(tmp_path, discovery):
    cfg = RuntimeConfig(
        persistence_backend="local",
        local_models_root=str(tmp_path),
        optimization_runs_root=str(tmp_path / "runs"),
    )
    reg = LocalOptimizationRunRegistry(str(tmp_path / "runs"))
    svc = OptimizationService(
        _Runner(_preflight()), reg, _Executor(), cfg, discovery=discovery
    )
    return svc, reg


async def test_replaced_model_is_not_reused_and_record_carries_identity(tmp_path):
    ff = _entry("v1", mediators=(MediatorFile("M1", "/r/m/mediators/M1.binpb", "e"),))
    discovery = _Discovery(ff)
    svc, reg = _opt_svc(tmp_path, discovery)
    first = await svc.run_optimization("m", {"scenario": {"type": "fixed_budget"}})
    record = reg.get_record(first["run_id"])
    assert (record.model_version, record.funnel) == ("v1", "full_funnel")
    reg.write_state(
        OptimizationRunState(run_id=first["run_id"], status=RunStatus.COMPLETED)
    )
    again = await svc.run_optimization("m", {"scenario": {"type": "fixed_budget"}})
    assert again["reused"] is True
    discovery.entries = [dataclasses.replace(ff, model_version="v2")]
    third = await svc.run_optimization("m", {"scenario": {"type": "fixed_budget"}})
    assert third["reused"] is False


def test_tool_factories_wire_the_discovery_cache(tmp_path):
    discovery = _Discovery(_entry("v1"))
    ctx = SimpleNamespace(
        lifespan_context={
            "analysis_runner": _Runner(),
            "result_cache": ResultCache(enabled=True),
            "discovery_cache": discovery,
            "optimization_registry": LocalOptimizationRunRegistry(
                str(tmp_path / "runs")
            ),
            "optimization_executor": _Executor(),
            "config": RuntimeConfig(
                persistence_backend="local",
                local_models_root=str(tmp_path),
                optimization_runs_root=str(tmp_path / "runs"),
            ),
        }
    )
    assert tools_module._analysis_service(ctx)._discovery is discovery
    assert tools_module._optimization_service(ctx)._discovery is discovery

# tests/unit/test_optimization_worker.py
import time
from typing import Any

from google_meridian_mcp_server.domain.optimization import (
    OptimizationConfig,
    OptimizationRun,
    RunStatus,
)
from google_meridian_mcp_server.execution.worker import run_worker
from google_meridian_mcp_server.meridian.catalog import ModelCatalog
from google_meridian_mcp_server.meridian.optimizer_facade import OptimizerFacade
from google_meridian_mcp_server.persistence.optimization_run_registry import (
    LocalOptimizationRunRegistry,
)


class _FakeFacade:
    def __init__(self, result=None, boom=False):
        self._result = result or {
            "outcome_mode": "revenue",
            "summary": {
                "optimized_efficiency": 2.6,
                "non_optimized_efficiency": 2.0,
                "optimized_budget": 1000.0,
            },
        }
        self._boom = boom

    def run(self, config):
        if self._boom:
            raise RuntimeError("optimize blew up")
        return self._result


class _FakeCatalog:
    def __init__(self, facade):
        self._facade = facade

    def get_optimizer_facade(self, model_id):
        return self._facade


def _seed_run(reg, run_id="m-1"):
    cfg = OptimizationConfig.model_validate({"scenario": {"type": "fixed_budget"}})
    reg.create(
        OptimizationRun(
            run_id=run_id,
            label="l",
            model_id="m",
            config=cfg,
            config_fingerprint="fp",
            compute_tier_requested="auto",
            compute_tier_resolved="local",
            backend="tensorflow",
            size_score=1,
            created_at="2026-06-29T00:00:00+00:00",
            meridian_version="1.7.0",
            server_version="0.1.0",
        )
    )


def test_worker_happy_path_writes_result_and_completed(tmp_path):
    reg = LocalOptimizationRunRegistry(str(tmp_path))
    _seed_run(reg)
    code = run_worker(
        "m-1", registry=reg, catalog=_FakeCatalog(_FakeFacade()), backend="tensorflow"
    )
    assert code == 0
    assert reg.get_state("m-1").status == RunStatus.COMPLETED
    assert reg.get_state("m-1").headline is not None
    assert reg.get_result("m-1")["summary"]["optimized_efficiency"] == 2.6


def test_worker_failure_writes_failed_state(tmp_path):
    reg = LocalOptimizationRunRegistry(str(tmp_path))
    _seed_run(reg)
    code = run_worker(
        "m-1",
        registry=reg,
        catalog=_FakeCatalog(_FakeFacade(boom=True)),
        backend="tensorflow",
    )
    assert code == 1
    state = reg.get_state("m-1")
    assert state.status == RunStatus.FAILED
    assert "optimize blew up" in state.error["message"]


class _RecordingRegistry:
    def __init__(self, record):
        self._record = record
        self.states: list[Any] = []

    def get_record(self, run_id):
        return self._record

    def write_state(self, state):
        self.states.append(state)

    def write_result(self, run_id, result):
        pass


class _SlowFacade:
    def run(self, config):
        time.sleep(0.6)  # longer than the test heartbeat interval
        return {"outcome_mode": "revenue", "summary": {}}


class _Catalog:
    def get_optimizer_facade(self, model_id):
        return _SlowFacade()


def test_worker_emits_heartbeats_during_optimize(tmp_path):
    cfg = OptimizationConfig.model_validate({"scenario": {"type": "fixed_budget"}})
    record = OptimizationRun(
        run_id="m-1",
        label="l",
        model_id="m",
        config=cfg,
        config_fingerprint="fp",
        compute_tier_requested="auto",
        compute_tier_resolved="local",
        backend="tensorflow",
        size_score=1,
        created_at="2026-06-29T00:00:00+00:00",
        meridian_version="1.7.0",
        server_version="0.1.0",
    )
    registry = _RecordingRegistry(record)
    rc = run_worker(
        record.run_id,
        registry=registry,
        catalog=_Catalog(),
        backend="tensorflow",
        heartbeat_interval=0.2,
    )
    assert rc == 0
    heartbeats = [s.heartbeat_at for s in registry.states if s.heartbeat_at]
    # initial running write + >=1 background heartbeat + terminal
    assert len(heartbeats) >= 3
    assert registry.states[-1].status == RunStatus.COMPLETED


def test_catalog_get_optimizer_facade_returns_and_caches(monkeypatch):
    """ModelCatalog.get_optimizer_facade returns an OptimizerFacade and caches it."""
    # Build a minimal ModelCatalog without real persistence dependencies
    from unittest.mock import MagicMock

    discovery_cache = MagicMock()
    materialization_cache = MagicMock()
    catalog = ModelCatalog(
        discovery_cache=discovery_cache,
        materialization_cache=materialization_cache,
    )

    sentinel = object()
    monkeypatch.setattr(catalog, "resolve", lambda model_id: sentinel)

    facade1 = catalog.get_optimizer_facade("m")
    facade2 = catalog.get_optimizer_facade("m")

    assert isinstance(facade1, OptimizerFacade)
    # Same cached instance
    assert facade1 is facade2

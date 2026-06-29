# tests/unit/test_optimization_service.py
import pytest

from google_meridian_mcp_server.domain.models import RuntimeConfig
from google_meridian_mcp_server.domain.optimization import RunStatus
from google_meridian_mcp_server.persistence.optimization_run_registry import (
    LocalOptimizationRunRegistry,
)
from google_meridian_mcp_server.services.optimization_service import OptimizationService


class _Posterior:
    sizes = {"chain": 2, "draw": 50}


class _InferenceData:
    posterior = _Posterior()


class _MMM:
    inference_data = _InferenceData()


class _Interrogator:
    def geo_names(self):
        return ["g1", "g2"]

    def get_time_values(self):
        return [str(i) for i in range(10)]

    def get_data_inputs(self):
        return {"media": ["tv", "search"], "rf_media": []}

    has = True
    _mmm = _MMM()


class _Facade(_Interrogator):
    def resolve_use_kpi(self, config):
        return False

    def channel_order(self):
        return ["tv", "search"]


class _Catalog:
    def __init__(self):
        self._f = _Facade()

    def get_interrogator(self, model_id):
        return self._f

    def get_facade(self, model_id):
        return self._f

    def get_optimizer_facade(self, model_id):
        return self._f


class _Catalog404(_Catalog):
    def get_optimizer_facade(self, model_id):
        from google_meridian_mcp_server.domain.errors import ModelNotFoundError

        raise ModelNotFoundError(model_id)


class _Executor:
    def __init__(self):
        self.submitted = []

    def submit(self, run):
        self.submitted.append(run.run_id)

    def pump(self):
        pass


def _svc(tmp_path, catalog=None):
    cfg = RuntimeConfig(
        persistence_backend="local",
        local_models_root=str(tmp_path),
        optimization_runs_root=str(tmp_path / "runs"),
    )
    reg = LocalOptimizationRunRegistry(str(tmp_path / "runs"))
    return OptimizationService(catalog or _Catalog(), reg, _Executor(), cfg), reg


def test_run_optimization_creates_queued_run(tmp_path):
    svc, reg = _svc(tmp_path)
    out = svc.run_optimization("m", {"scenario": {"type": "fixed_budget"}})
    assert out["reused"] is False
    assert out["compute_tier_resolved"] == "local"
    assert reg.get_record(out["run_id"]).model_id == "m"
    assert svc._executor.submitted == [out["run_id"]]


def test_identical_config_reuses_completed_run(tmp_path):
    svc, reg = _svc(tmp_path)
    first = svc.run_optimization("m", {"scenario": {"type": "fixed_budget"}})
    from google_meridian_mcp_server.domain.optimization import OptimizationRunState

    reg.write_state(
        OptimizationRunState(run_id=first["run_id"], status=RunStatus.COMPLETED)
    )
    again = svc.run_optimization("m", {"scenario": {"type": "fixed_budget"}})
    assert again["reused"] is True
    assert again["run_id"] == first["run_id"]


def test_force_rerun_bypasses_reuse(tmp_path):
    svc, reg = _svc(tmp_path)
    first = svc.run_optimization("m", {"scenario": {"type": "fixed_budget"}})
    from google_meridian_mcp_server.domain.optimization import OptimizationRunState

    reg.write_state(
        OptimizationRunState(run_id=first["run_id"], status=RunStatus.COMPLETED)
    )
    again = svc.run_optimization(
        "m", {"scenario": {"type": "fixed_budget"}}, force_rerun=True
    )
    assert again["reused"] is False and again["run_id"] != first["run_id"]


def test_unknown_model_raises(tmp_path):
    from google_meridian_mcp_server.domain.errors import ModelNotFoundError

    svc, _ = _svc(tmp_path, catalog=_Catalog404())
    with pytest.raises(ModelNotFoundError):
        svc.run_optimization("nope", {"scenario": {"type": "fixed_budget"}})


def test_invalid_per_channel_config_raises(tmp_path):
    from google_meridian_mcp_server.services.optimization_service import (
        InvalidOptimizationConfigError,
    )

    svc, _ = _svc(tmp_path)
    with pytest.raises(InvalidOptimizationConfigError):
        svc.run_optimization(
            "m",
            {
                "scenario": {"type": "fixed_budget"},
                "constraint": {
                    "mode": "per_channel",
                    "bounds": {"tv": {"lower_pct": 0.1, "upper_pct": 0.2}},
                },
            },
        )

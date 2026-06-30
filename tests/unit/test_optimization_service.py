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


class _CancellableExecutor:
    """Fake executor that records cancellations and mirrors what BaseExecutor.cancel does."""

    def __init__(self, registry):
        self._registry = registry
        self.submitted = []
        self.terminated = []

    def submit(self, run):
        self.submitted.append(run.run_id)

    def pump(self):
        pass

    def cancel(self, run_id):
        from google_meridian_mcp_server.domain.optimization import OptimizationRunState

        self.terminated.append(run_id)
        state = self._registry.get_state(run_id)
        if state.status in (RunStatus.QUEUED, RunStatus.RUNNING):
            self._registry.write_state(
                OptimizationRunState(run_id=run_id, status=RunStatus.CANCELED)
            )


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


def test_disallowed_compute_tier_raises_typed_error(tmp_path):
    """FIX 2: disallowed compute_tier → InvalidOptimizationConfigError, not bare ValueError."""
    from google_meridian_mcp_server.services.optimization_service import (
        InvalidOptimizationConfigError,
    )

    svc, _ = _svc(tmp_path)
    with pytest.raises(InvalidOptimizationConfigError):
        svc.run_optimization(
            "m",
            {"scenario": {"type": "fixed_budget"}},
            compute_tier="cloud_gpu",  # not in allowed tiers (default: local only)
        )


def test_list_runs_bad_status_raises_typed_error(tmp_path):
    """FIX 2: unknown status string in list_runs → InvalidOptimizationConfigError, not bare ValueError."""
    from google_meridian_mcp_server.services.optimization_service import (
        InvalidOptimizationConfigError,
    )

    svc, _ = _svc(tmp_path)
    with pytest.raises(InvalidOptimizationConfigError):
        svc.list_runs(status="bogus")


def test_reused_running_run_reports_running_status(tmp_path):
    """FIX 6: reused envelope reports actual status, not hardcoded 'completed'."""
    from google_meridian_mcp_server.domain.optimization import OptimizationRunState

    svc, reg = _svc(tmp_path)
    first = svc.run_optimization("m", {"scenario": {"type": "fixed_budget"}})

    # Advance to RUNNING
    reg.write_state(
        OptimizationRunState(run_id=first["run_id"], status=RunStatus.RUNNING)
    )

    again = svc.run_optimization("m", {"scenario": {"type": "fixed_budget"}})
    assert again["reused"] is True
    assert again["status"] == "running"


def test_identical_config_reuses_completed_run_reports_completed(tmp_path):
    """FIX 6 regression: COMPLETED reuse must still report 'completed'."""
    from google_meridian_mcp_server.domain.optimization import OptimizationRunState

    svc, reg = _svc(tmp_path)
    first = svc.run_optimization("m", {"scenario": {"type": "fixed_budget"}})
    reg.write_state(
        OptimizationRunState(run_id=first["run_id"], status=RunStatus.COMPLETED)
    )
    again = svc.run_optimization("m", {"scenario": {"type": "fixed_budget"}})
    assert again["reused"] is True
    assert again["status"] == "completed"


def test_cancel_marks_canceled_and_terminates(tmp_path):
    """Task 6: cancel returns the correct envelope, calls executor.cancel, and the state is CANCELED."""
    from google_meridian_mcp_server.domain.optimization import OptimizationRunState

    cfg = RuntimeConfig(
        persistence_backend="local",
        local_models_root=str(tmp_path),
        optimization_runs_root=str(tmp_path / "runs"),
    )
    reg = LocalOptimizationRunRegistry(str(tmp_path / "runs"))
    executor = _CancellableExecutor(reg)
    svc = OptimizationService(_Catalog(), reg, executor, cfg)

    # Create a run then advance it to RUNNING
    out = svc.run_optimization("m", {"scenario": {"type": "fixed_budget"}})
    run_id = out["run_id"]
    reg.write_state(OptimizationRunState(run_id=run_id, status=RunStatus.RUNNING))

    result = svc.cancel(run_id)

    assert result == {"run_id": run_id, "status": "canceled"}
    assert executor.terminated == [run_id]
    assert reg.get_state(run_id).status == RunStatus.CANCELED

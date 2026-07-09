# tests/unit/test_optimization_service.py
import sys

import pytest

from google_meridian_mcp_server.domain.models import RuntimeConfig
from google_meridian_mcp_server.domain.optimization import RunStatus
from google_meridian_mcp_server.persistence.cache import ResultCache
from google_meridian_mcp_server.persistence.optimization_run_registry import (
    LocalOptimizationRunRegistry,
)
from google_meridian_mcp_server.services.optimization_service import (
    InvalidOptimizationConfigError,
    OptimizationService,
)

_SIZE_FEATURES = {
    "n_geos": 2,
    "n_time_units": 10,
    "n_channels": 2,
    "n_posterior_samples": 100,
}


def _preflight_ok(**overrides) -> dict:
    payload = {
        "channel_order": ["tv", "search"],
        "has_revenue_per_kpi": True,
        "use_kpi": False,
        "size_features": dict(_SIZE_FEATURES),
        "validation_error": None,
    }
    payload.update(overrides)
    return payload


class FakeRunner:
    """Fake worker runner: records calls, returns a canned preflight dict
    (or raises, if `to_raise` is set) instead of ever touching Meridian."""

    def __init__(self, result=None, *, to_raise: Exception | None = None):
        self.calls: list[tuple[str, str, dict]] = []
        self._result = result if result is not None else _preflight_ok()
        self._to_raise = to_raise

    async def run(self, operation, model_id, params):
        self.calls.append((operation, model_id, dict(params)))
        if self._to_raise is not None:
            raise self._to_raise
        return self._result


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


def _svc(tmp_path, runner=None, result_cache=None):
    cfg = RuntimeConfig(
        persistence_backend="local",
        local_models_root=str(tmp_path),
        optimization_runs_root=str(tmp_path / "runs"),
    )
    reg = LocalOptimizationRunRegistry(str(tmp_path / "runs"))
    return (
        OptimizationService(
            runner or FakeRunner(), reg, _Executor(), cfg, result_cache=result_cache
        ),
        reg,
    )


@pytest.fixture
def service_with_fakes(tmp_path):
    """OptimizationService wired with a FakeRunner, for future-optimization tests."""
    runner = FakeRunner()
    executor = _Executor()
    cfg = RuntimeConfig(
        persistence_backend="local",
        local_models_root=str(tmp_path),
        optimization_runs_root=str(tmp_path / "runs"),
    )
    registry = LocalOptimizationRunRegistry(str(tmp_path / "runs"))
    service = OptimizationService(runner, registry, executor, cfg)
    fakes = {"runner": runner, "executor": executor, "registry": registry}
    return service, fakes


@pytest.mark.asyncio
async def test_submit_does_not_import_meridian(tmp_path):
    """Crux guard: submitting an optimization must never import the meridian
    package into the SERVER process -- all model-derived values come from the
    (faked, here) worker-side preflight_optimization op."""
    svc, _ = _svc(tmp_path)
    before = {m for m in sys.modules if m.split(".")[0] == "meridian"}

    await svc.run_optimization("m1", {"scenario": {"type": "fixed_budget"}})

    after = {m for m in sys.modules if m.split(".")[0] == "meridian"}
    assert after == before


@pytest.mark.asyncio
async def test_run_optimization_creates_queued_run(tmp_path):
    svc, reg = _svc(tmp_path)
    out = await svc.run_optimization("m", {"scenario": {"type": "fixed_budget"}})
    assert out["reused"] is False
    assert out["compute_tier_resolved"] == "local"
    assert reg.get_record(out["run_id"]).model_id == "m"
    assert svc._executor.submitted == [out["run_id"]]


@pytest.mark.asyncio
async def test_identical_config_reuses_completed_run(tmp_path):
    svc, reg = _svc(tmp_path)
    first = await svc.run_optimization("m", {"scenario": {"type": "fixed_budget"}})
    from google_meridian_mcp_server.domain.optimization import OptimizationRunState

    reg.write_state(
        OptimizationRunState(run_id=first["run_id"], status=RunStatus.COMPLETED)
    )
    again = await svc.run_optimization("m", {"scenario": {"type": "fixed_budget"}})
    assert again["reused"] is True
    assert again["run_id"] == first["run_id"]


@pytest.mark.asyncio
async def test_preflight_is_cached_by_config_fingerprint_across_service_instances(
    tmp_path,
):
    """Fable finding 3: a fresh OptimizationService is constructed PER TOOL
    CALL (see transport/tools.py:_optimization_service), so a bespoke
    instance-local preflight cache never survives past a single call -- it
    caches nothing, ever. The cache must live in the lifespan-scoped
    ResultCache instead. Simulate that here with a SHARED enabled ResultCache
    passed to TWO separate OptimizationService instances (one per simulated
    tool call); the underlying runner.run for preflight_optimization must be
    invoked only ONCE across both submits of the same config. Use
    force_rerun on the second call so registry dedup doesn't short-circuit
    inside _submit and mask this."""
    runner = FakeRunner()
    shared_cache = ResultCache(enabled=True)
    config = {"scenario": {"type": "fixed_budget"}}

    svc1, _ = _svc(tmp_path, runner=runner, result_cache=shared_cache)
    await svc1.run_optimization("m", config)

    svc2, _ = _svc(tmp_path, runner=runner, result_cache=shared_cache)
    await svc2.run_optimization("m", config, force_rerun=True)

    preflight_calls = [c for c in runner.calls if c[0] == "preflight_optimization"]
    assert len(preflight_calls) == 1


@pytest.mark.asyncio
async def test_preflight_cache_is_config_dependent_not_model_only(tmp_path):
    """A DIFFERENT config for the same model_id must NOT reuse a cached
    preflight from a different config -- validation_error/use_kpi are
    config-dependent, not model-only (correctness, not just performance).
    Uses the same shared-ResultCache-across-instances setup as the fingerprint
    cache-hit test above."""
    runner = FakeRunner()
    shared_cache = ResultCache(enabled=True)

    svc1, _ = _svc(tmp_path, runner=runner, result_cache=shared_cache)
    await svc1.run_optimization("m", {"scenario": {"type": "fixed_budget"}})

    svc2, _ = _svc(tmp_path, runner=runner, result_cache=shared_cache)
    await svc2.run_optimization(
        "m", {"scenario": {"type": "target_roas", "target_value": 2.0}}
    )

    preflight_calls = [c for c in runner.calls if c[0] == "preflight_optimization"]
    assert len(preflight_calls) == 2


@pytest.mark.asyncio
async def test_preflight_not_cached_without_result_cache(tmp_path):
    """No result_cache (None, the default) -> no caching at all: every
    submission re-runs preflight. Guards against accidentally reintroducing
    an always-on cache with no way to disable it."""
    runner = FakeRunner()
    config = {"scenario": {"type": "fixed_budget"}}

    svc, _ = _svc(tmp_path, runner=runner)  # result_cache=None
    await svc.run_optimization("m", config)
    await svc.run_optimization("m", config, force_rerun=True)

    preflight_calls = [c for c in runner.calls if c[0] == "preflight_optimization"]
    assert len(preflight_calls) == 2


@pytest.mark.asyncio
async def test_force_rerun_bypasses_reuse(tmp_path):
    svc, reg = _svc(tmp_path)
    first = await svc.run_optimization("m", {"scenario": {"type": "fixed_budget"}})
    from google_meridian_mcp_server.domain.optimization import OptimizationRunState

    reg.write_state(
        OptimizationRunState(run_id=first["run_id"], status=RunStatus.COMPLETED)
    )
    again = await svc.run_optimization(
        "m", {"scenario": {"type": "fixed_budget"}}, force_rerun=True
    )
    assert again["reused"] is False and again["run_id"] != first["run_id"]


@pytest.mark.asyncio
async def test_unknown_model_raises(tmp_path):
    from google_meridian_mcp_server.domain.errors import ModelNotFoundError

    runner = FakeRunner(to_raise=ModelNotFoundError("nope"))
    svc, _ = _svc(tmp_path, runner=runner)
    with pytest.raises(ModelNotFoundError):
        await svc.run_optimization("nope", {"scenario": {"type": "fixed_budget"}})


@pytest.mark.asyncio
async def test_invalid_per_channel_config_raises(tmp_path):
    """A per_channel constraint missing bounds for a channel is only
    detectable with the model's channel_order (worker-side, via preflight's
    to_optimize_kwargs check) -- simulate that via the preflight
    validation_error, same as the real _preflight_optimization op would
    surface it."""
    runner = FakeRunner(
        result=_preflight_ok(
            validation_error={
                "error_code": "invalid_optimization_config",
                "message": "per_channel constraint is missing bounds for channels: ['search']",
                "details": {},
            }
        )
    )
    svc, _ = _svc(tmp_path, runner=runner)
    with pytest.raises(InvalidOptimizationConfigError):
        await svc.run_optimization(
            "m",
            {
                "scenario": {"type": "fixed_budget"},
                "constraint": {
                    "mode": "per_channel",
                    "bounds": {"tv": {"lower_pct": 0.1, "upper_pct": 0.2}},
                },
            },
        )


@pytest.mark.asyncio
async def test_preflight_validation_error_raises_invalid_optimization_config(tmp_path):
    """A validation_error surfaced from the worker-side preflight op (e.g. an
    unknown per_channel constraint channel, detected only with model access)
    must raise InvalidOptimizationConfigError BEFORE the run is launched."""
    runner = FakeRunner(
        result=_preflight_ok(
            validation_error={
                "error_code": "invalid_optimization_config",
                "message": "unknown channels: ['bogus']",
                "details": {},
            }
        )
    )
    svc, reg = _svc(tmp_path, runner=runner)
    with pytest.raises(InvalidOptimizationConfigError):
        await svc.run_optimization("m", {"scenario": {"type": "fixed_budget"}})
    # Nothing was launched: no run record was ever created.
    assert reg.list() == []


@pytest.mark.asyncio
async def test_disallowed_compute_tier_raises_typed_error(tmp_path):
    """FIX 2: disallowed compute_tier -> InvalidOptimizationConfigError, not bare ValueError."""
    svc, _ = _svc(tmp_path)
    with pytest.raises(InvalidOptimizationConfigError):
        await svc.run_optimization(
            "m",
            {"scenario": {"type": "fixed_budget"}},
            compute_tier="cloud_gpu",  # not in allowed tiers (default: local only)
        )


def test_list_runs_bad_status_raises_typed_error(tmp_path):
    """FIX 2: unknown status string in list_runs -> InvalidOptimizationConfigError, not bare ValueError."""
    svc, _ = _svc(tmp_path)
    with pytest.raises(InvalidOptimizationConfigError):
        svc.list_runs(status="bogus")


@pytest.mark.asyncio
async def test_reused_running_run_reports_running_status(tmp_path):
    """FIX 6: reused envelope reports actual status, not hardcoded 'completed'."""
    from google_meridian_mcp_server.domain.optimization import OptimizationRunState

    svc, reg = _svc(tmp_path)
    first = await svc.run_optimization("m", {"scenario": {"type": "fixed_budget"}})

    # Advance to RUNNING
    reg.write_state(
        OptimizationRunState(run_id=first["run_id"], status=RunStatus.RUNNING)
    )

    again = await svc.run_optimization("m", {"scenario": {"type": "fixed_budget"}})
    assert again["reused"] is True
    assert again["status"] == "running"


@pytest.mark.asyncio
async def test_identical_config_reuses_completed_run_reports_completed(tmp_path):
    """FIX 6 regression: COMPLETED reuse must still report 'completed'."""
    from google_meridian_mcp_server.domain.optimization import OptimizationRunState

    svc, reg = _svc(tmp_path)
    first = await svc.run_optimization("m", {"scenario": {"type": "fixed_budget"}})
    reg.write_state(
        OptimizationRunState(run_id=first["run_id"], status=RunStatus.COMPLETED)
    )
    again = await svc.run_optimization("m", {"scenario": {"type": "fixed_budget"}})
    assert again["reused"] is True
    assert again["status"] == "completed"


@pytest.mark.asyncio
async def test_cloud_tier_run_records_jax_backend(tmp_path):
    """FIX 1: a run resolved to cloud_cpu records backend == 'jax'."""
    cfg = RuntimeConfig(
        persistence_backend="local",
        local_models_root=str(tmp_path),
        optimization_runs_root=str(tmp_path / "runs"),
        registry_backend="gcs",
        gcs_bucket="b",
        optimization_allowed_tiers=("cloud_cpu",),
        cloud_run_project="proj",
        cloud_run_region="us-central1",
        cloud_run_job_cpu="meridian-opt-cpu",
    )
    reg = LocalOptimizationRunRegistry(str(tmp_path / "runs"))
    svc = OptimizationService(FakeRunner(), reg, _Executor(), cfg)
    out = await svc.run_optimization(
        "m", {"scenario": {"type": "fixed_budget"}}, compute_tier="cloud_cpu"
    )
    assert out["backend"] == "jax"
    assert reg.get_record(out["run_id"]).backend == "jax"


@pytest.mark.asyncio
async def test_local_tier_run_records_local_backend(tmp_path):
    """FIX 1: a local run still records the local (tensorflow) backend."""
    svc, reg = _svc(tmp_path)
    out = await svc.run_optimization("m", {"scenario": {"type": "fixed_budget"}})
    assert out["backend"] == "tensorflow"
    assert reg.get_record(out["run_id"]).backend == "tensorflow"


def test_cancel_unknown_run_raises_run_not_found(tmp_path):
    """FIX 4: cancel('unknown-run-id') raises RunNotFoundError."""
    from google_meridian_mcp_server.persistence.optimization_run_registry import (
        RunNotFoundError,
    )

    svc, _ = _svc(tmp_path)
    with pytest.raises(RunNotFoundError):
        svc.cancel("unknown-run-id")


@pytest.mark.asyncio
async def test_cancel_marks_canceled_and_terminates(tmp_path):
    """Task 6: cancel returns the correct envelope, calls executor.cancel, and the state is CANCELED."""
    from google_meridian_mcp_server.domain.optimization import OptimizationRunState

    cfg = RuntimeConfig(
        persistence_backend="local",
        local_models_root=str(tmp_path),
        optimization_runs_root=str(tmp_path / "runs"),
    )
    reg = LocalOptimizationRunRegistry(str(tmp_path / "runs"))
    executor = _CancellableExecutor(reg)
    svc = OptimizationService(FakeRunner(), reg, executor, cfg)

    # Create a run then advance it to RUNNING
    out = await svc.run_optimization("m", {"scenario": {"type": "fixed_budget"}})
    run_id = out["run_id"]
    reg.write_state(OptimizationRunState(run_id=run_id, status=RunStatus.RUNNING))

    result = svc.cancel(run_id)

    assert result == {"run_id": run_id, "status": "canceled"}
    assert executor.terminated == [run_id]
    assert reg.get_state(run_id).status == RunStatus.CANCELED


@pytest.mark.asyncio
async def test_run_future_optimization_returns_queued_envelope(service_with_fakes):
    service, fakes = service_with_fakes
    out = await service.run_future_optimization(
        "national-revenue",
        {
            "scenario": {"type": "fixed_budget"},
            "future": {"start_date": "2099-01-01", "horizon": 4},
        },
    )
    assert out["status"] == "queued"
    assert out["reused"] is False
    assert "run_id" in out


@pytest.mark.asyncio
async def test_run_future_optimization_reuses_identical_run(service_with_fakes):
    service, fakes = service_with_fakes
    cfg = {
        "scenario": {"type": "fixed_budget"},
        "future": {"start_date": "2099-01-01", "horizon": 4},
    }
    first = await service.run_future_optimization("national-revenue", cfg)
    second = await service.run_future_optimization("national-revenue", cfg)
    assert second["reused"] is True
    assert second["run_id"] == first["run_id"]


@pytest.mark.asyncio
async def test_run_future_invalid_config_raises(service_with_fakes):
    service, _ = service_with_fakes
    with pytest.raises(InvalidOptimizationConfigError):
        await service.run_future_optimization(
            "national-revenue",
            {
                "scenario": {"type": "fixed_budget"},
                "future": {"start_date": "2099-01-01", "horizon": 0},
            },
        )


@pytest.mark.asyncio
async def test_preflight_receives_validated_config_dump_with_kind(
    service_with_fakes,
):
    """Fable finding 6: preflight must branch on the VALIDATED config's dump,
    not the raw incoming config_dict -- a direct service caller can omit the
    'kind' key entirely (pydantic defaults it), and the raw dict would then
    lack the discriminator the worker-side op branches on. Assert the params
    passed to the preflight_optimization runner call carry the correct
    'kind' even when the caller-supplied dict omits it."""
    service, fakes = service_with_fakes
    await service.run_future_optimization(
        "national-revenue",
        {
            # no "kind" key at all -- caller omitted the discriminator
            "scenario": {"type": "fixed_budget"},
            "future": {"start_date": "2099-01-01", "horizon": 4},
        },
    )
    preflight_calls = [
        c for c in fakes["runner"].calls if c[0] == "preflight_optimization"
    ]
    assert len(preflight_calls) == 1
    _, _, params = preflight_calls[0]
    assert params["config"]["kind"] == "future"


@pytest.mark.asyncio
async def test_run_future_validate_future_error_becomes_invalid_config(tmp_path):
    """FIX 7: a config that passes pydantic but fails the worker-side
    facade.validate_future (unknown cost_multipliers channel) must surface as
    InvalidOptimizationConfigError via preflight's validation_error, not a
    bare ValueError."""
    runner = FakeRunner(
        result=_preflight_ok(
            validation_error={
                "error_code": "invalid_optimization_config",
                "message": "unknown channels: ['not_a_channel']",
                "details": {},
            }
        )
    )
    svc, _ = _svc(tmp_path, runner=runner)
    with pytest.raises(InvalidOptimizationConfigError):
        await svc.run_future_optimization(
            "national-revenue",
            {
                "scenario": {"type": "fixed_budget"},
                "future": {
                    "start_date": "2099-01-01",
                    "horizon": 4,
                    "cost_multipliers": {"not_a_channel": 1.2},
                },
            },
        )

import pytest

from google_meridian_mcp_server.domain.optimization import (
    OptimizationConfig,
    OptimizationRun,
    OptimizationRunState,
    RunStatus,
)
from google_meridian_mcp_server.persistence.optimization_run_registry import (
    LocalOptimizationRunRegistry,
    ResultNotReadyError,
    RunNotFoundError,
)


def _run(run_id="m-1-abc", model_id="m", fp="fp1"):
    cfg = OptimizationConfig.model_validate({"scenario": {"type": "fixed_budget"}})
    return OptimizationRun(
        run_id=run_id,
        label="label",
        model_id=model_id,
        config=cfg,
        config_fingerprint=fp,
        compute_tier_requested="auto",
        compute_tier_resolved="local",
        backend="tensorflow",
        size_score=10,
        created_at="2026-06-29T00:00:00+00:00",
        meridian_version="1.7.0",
        server_version="0.1.0",
    )


def test_create_and_get_record(tmp_path):
    reg = LocalOptimizationRunRegistry(str(tmp_path))
    reg.create(_run())
    got = reg.get_record("m-1-abc")
    assert got.model_id == "m" and got.label == "label"


def test_state_roundtrip_and_result_gate(tmp_path):
    reg = LocalOptimizationRunRegistry(str(tmp_path))
    reg.create(_run())
    reg.write_state(OptimizationRunState(run_id="m-1-abc", status=RunStatus.RUNNING))
    assert reg.get_state("m-1-abc").status == RunStatus.RUNNING
    with pytest.raises(ResultNotReadyError):
        reg.get_result("m-1-abc")
    reg.write_result("m-1-abc", {"summary": {"x": 1}})
    assert reg.get_result("m-1-abc") == {"summary": {"x": 1}}


def test_list_filters_and_never_reads_result(tmp_path):
    reg = LocalOptimizationRunRegistry(str(tmp_path))
    reg.create(_run(run_id="m-1", model_id="m"))
    reg.create(_run(run_id="n-1", model_id="n"))
    reg.write_state(OptimizationRunState(run_id="m-1", status=RunStatus.COMPLETED))
    reg.write_state(OptimizationRunState(run_id="n-1", status=RunStatus.RUNNING))
    completed = reg.list(status=RunStatus.COMPLETED)
    assert [s.run_id for s in completed] == ["m-1"]
    assert reg.list(model_id="n")[0].run_id == "n-1"


def test_fingerprint_index(tmp_path):
    reg = LocalOptimizationRunRegistry(str(tmp_path))
    reg.create(_run(run_id="m-1", fp="fpX"))
    reg.put_fingerprint("fpX", "m-1")
    assert reg.find_by_fingerprint("fpX") == "m-1"
    assert reg.find_by_fingerprint("nope") is None


def test_delete_removes_run_and_missing_raises(tmp_path):
    reg = LocalOptimizationRunRegistry(str(tmp_path))
    reg.create(_run(run_id="m-1"))
    reg.delete("m-1")
    with pytest.raises(RunNotFoundError):
        reg.get_record("m-1")


def test_write_state_leaves_parseable_file(tmp_path):
    """FIX 4: atomic write — re-writing state.json leaves a valid, parseable file."""
    reg = LocalOptimizationRunRegistry(str(tmp_path))
    reg.create(_run())
    reg.write_state(OptimizationRunState(run_id="m-1-abc", status=RunStatus.RUNNING))
    reg.write_state(OptimizationRunState(run_id="m-1-abc", status=RunStatus.COMPLETED))
    # Both reads must succeed without json parse errors
    state = reg.get_state("m-1-abc")
    assert state.status == RunStatus.COMPLETED

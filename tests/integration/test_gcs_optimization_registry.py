import pytest

from google_meridian_mcp_server.domain.optimization import (
    OptimizationConfig,
    OptimizationRun,
    OptimizationRunState,
    RunStatus,
)
from google_meridian_mcp_server.persistence.optimization_run_registry import (
    GcsOptimizationRunRegistry,
    ResultNotReadyError,
)
from tests.fakes.fake_gcs import FakeGcsClient


def _run(run_id="m-1"):
    return OptimizationRun(
        run_id=run_id,
        label="l",
        model_id="m",
        config=OptimizationConfig.model_validate(
            {"scenario": {"type": "fixed_budget"}}
        ),
        config_fingerprint="fp1",
        compute_tier_requested="auto",
        compute_tier_resolved="cloud_cpu",
        backend="jax",
        size_score=1,
        created_at="2026-06-30T00:00:00+00:00",
        meridian_version="1.7.0",
        server_version="0.1.0",
    )


@pytest.fixture
def registry():
    client = FakeGcsClient()
    return GcsOptimizationRunRegistry(
        "bucket", "optimizations/", client_factory=lambda: client
    )


def test_create_state_result_roundtrip(registry):
    run = _run()
    registry.create(run)
    registry.write_state(OptimizationRunState(run_id="m-1", status=RunStatus.RUNNING))
    with pytest.raises(ResultNotReadyError):
        registry.get_result("m-1")
    registry.write_result("m-1", {"outcome_mode": "revenue"})
    assert registry.get_result("m-1")["outcome_mode"] == "revenue"
    assert registry.get_record("m-1").model_id == "m"
    assert registry.get_state("m-1").status == RunStatus.RUNNING


def test_list_reads_only_small_blobs_and_filters(registry):
    registry.create(_run("m-1"))
    registry.write_state(OptimizationRunState(run_id="m-1", status=RunStatus.COMPLETED))
    registry.create(_run("m-2"))
    registry.write_state(OptimizationRunState(run_id="m-2", status=RunStatus.RUNNING))
    completed = registry.list(status=RunStatus.COMPLETED)
    assert [s.run_id for s in completed] == ["m-1"]
    assert registry.client.reads_of("result.json") == 0  # never reads result on list


def test_fingerprint_index_and_delete(registry):
    run = _run()
    registry.create(run)
    registry.put_fingerprint("fp1", "m-1")
    assert registry.find_by_fingerprint("fp1") == "m-1"
    registry.delete("m-1")
    assert registry.find_by_fingerprint("fp1") is None


def test_write_state_generation_precondition(registry):
    registry.create(_run())
    registry.write_state(OptimizationRunState(run_id="m-1", status=RunStatus.RUNNING))
    gen = registry.get_state_generation("m-1")
    # a competing write bumps the generation
    registry.write_state(OptimizationRunState(run_id="m-1", status=RunStatus.RUNNING))
    with pytest.raises(Exception):
        registry.write_state(
            OptimizationRunState(run_id="m-1", status=RunStatus.FAILED),
            expected_generation=gen,
        )

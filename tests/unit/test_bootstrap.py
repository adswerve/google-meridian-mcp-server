"""Unit tests for the shared bootstrap helpers."""

from google_meridian_mcp_server.bootstrap import (
    build_executor,
    build_model_catalog,
    build_registry,
)
from google_meridian_mcp_server.domain.models import RuntimeConfig
from google_meridian_mcp_server.meridian.catalog import ModelCatalog
from google_meridian_mcp_server.persistence.optimization_run_registry import (
    GcsOptimizationRunRegistry,
    LocalOptimizationRunRegistry,
)


class _FakeRegistry:
    """Minimal registry stub for executor construction tests."""

    def list(self, *, status=None):
        return []


def _cfg(tmp_path, **over):
    base = dict(
        persistence_backend="local",
        local_models_root=str(tmp_path),
        optimization_runs_root=str(tmp_path / "runs"),
    )
    base.update(over)
    return RuntimeConfig(**base)


def test_build_model_catalog(tmp_path):
    assert isinstance(build_model_catalog(_cfg(tmp_path)), ModelCatalog)


def test_build_registry_local(tmp_path):
    assert isinstance(build_registry(_cfg(tmp_path)), LocalOptimizationRunRegistry)


def test_build_registry_gcs(tmp_path, monkeypatch):
    monkeypatch.setattr(
        GcsOptimizationRunRegistry,
        "_default_client",
        staticmethod(lambda: object()),
    )
    cfg = _cfg(tmp_path, registry_backend="gcs", gcs_bucket="b", gcs_models_prefix="p/")
    assert isinstance(build_registry(cfg), GcsOptimizationRunRegistry)


def test_build_executor_local():
    cfg = RuntimeConfig(persistence_backend="local", local_models_root="/m")
    ex = build_executor(cfg, _FakeRegistry())
    assert ex.__class__.__name__ == "SubprocessExecutor"


def test_build_executor_cloud_only():
    cfg = RuntimeConfig(
        persistence_backend="gcs",
        gcs_bucket="b",
        gcs_models_prefix="m/",
        registry_backend="gcs",
        optimization_allowed_tiers=("cloud_cpu",),
        cloud_run_project="as-dev-anze",
        cloud_run_region="us-central1",
        cloud_run_job_cpu="opt-cpu",
    )
    ex = build_executor(
        cfg, _FakeRegistry(), jobs_client=object(), executions_client=object()
    )
    assert ex.__class__.__name__ == "CloudRunJobExecutor"

"""Unit tests for the shared bootstrap helpers."""

import pytest

from google_meridian_mcp_server.bootstrap import build_model_catalog, build_registry
from google_meridian_mcp_server.domain.models import RuntimeConfig
from google_meridian_mcp_server.meridian.catalog import ModelCatalog
from google_meridian_mcp_server.persistence.optimization_run_registry import (
    LocalOptimizationRunRegistry,
)


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


def test_build_registry_gcs_not_supported_phase1(tmp_path):
    cfg = _cfg(tmp_path, registry_backend="gcs", gcs_bucket="b", gcs_models_prefix="p/")
    with pytest.raises(ValueError, match="Phase 2"):
        build_registry(cfg)

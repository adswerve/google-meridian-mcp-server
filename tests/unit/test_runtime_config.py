"""Unit tests for RuntimeConfig cloud-tier fields and guardrails (Phase 2)."""

import pytest
from pydantic import ValidationError

from google_meridian_mcp_server.domain.models import RuntimeConfig


def _local_kwargs(**over):
    base = dict(persistence_backend="local", local_models_root="/models")
    base.update(over)
    return base


def test_backend_for_tier_defaults():
    cfg = RuntimeConfig(**_local_kwargs())
    assert cfg.backend_for_tier("local") == "tensorflow"
    assert cfg.backend_for_tier("cloud_cpu") == "jax"
    assert cfg.backend_for_tier("cloud_gpu") == "jax"


def test_cloud_tier_requires_gcs_registry_and_cloud_run_fields():
    # cloud tier allowed but no gcs registry -> error (Phase 1 guardrail)
    with pytest.raises(ValidationError, match="gcs registry"):
        RuntimeConfig(
            **_local_kwargs(optimization_allowed_tiers=("local", "cloud_cpu"))
        )
    # gcs registry present but Cloud Run coordinates missing -> error
    with pytest.raises(ValidationError, match="CLOUD_RUN_PROJECT"):
        RuntimeConfig(
            persistence_backend="gcs",
            gcs_bucket="b",
            gcs_models_prefix="models/",
            registry_backend="gcs",
            optimization_allowed_tiers=("local", "cloud_cpu"),
        )


def test_cloud_tier_with_gcs_registry_but_no_bucket_raises():
    """FIX 2: cloud tier + gcs registry but no GCS_BUCKET raises ValidationError."""
    with pytest.raises(ValidationError, match="GCS_BUCKET"):
        RuntimeConfig(
            persistence_backend="local",
            local_models_root="/models",
            registry_backend="gcs",
            optimization_allowed_tiers=("local", "cloud_cpu"),
            cloud_run_project="proj",
            cloud_run_region="us-central1",
            cloud_run_job_cpu="opt-cpu",
            # gcs_bucket intentionally omitted
        )


def test_cloud_tier_fully_configured_is_valid():
    cfg = RuntimeConfig(
        persistence_backend="gcs",
        gcs_bucket="b",
        gcs_models_prefix="models/",
        registry_backend="gcs",
        optimization_allowed_tiers=("cloud_cpu", "cloud_gpu"),
        cloud_run_project="example-dev-project",
        cloud_run_region="us-central1",
        cloud_run_job_cpu="meridian-opt-cpu",
        cloud_run_job_gpu="meridian-opt-gpu",
    )
    assert cfg.cloud_run_project == "example-dev-project"


def test_analysis_runner_config_defaults(sample_runtime_config):
    cfg = sample_runtime_config
    assert cfg.analysis_max_parallel == 2
    assert cfg.analysis_worker_timeout == 300.0
    assert cfg.analysis_queue_wait_timeout == 30.0
    assert cfg.analysis_max_response_bytes == 64 * 1024 * 1024
    assert cfg.analysis_workdir_root == "/tmp/mmm-analysis"


def test_analysis_runner_config_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("PERSISTENCE_BACKEND", "local")
    monkeypatch.setenv("LOCAL_MODELS_ROOT", str(tmp_path))
    monkeypatch.setenv("ANALYSIS_MAX_PARALLEL", "4")
    monkeypatch.setenv("ANALYSIS_WORKER_TIMEOUT", "600")
    from google_meridian_mcp_server.config import load_config

    cfg = load_config()
    assert cfg.analysis_max_parallel == 4
    assert cfg.analysis_worker_timeout == 600.0

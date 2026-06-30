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


def test_cloud_tier_fully_configured_is_valid():
    cfg = RuntimeConfig(
        persistence_backend="gcs",
        gcs_bucket="b",
        gcs_models_prefix="models/",
        registry_backend="gcs",
        optimization_allowed_tiers=("cloud_cpu", "cloud_gpu"),
        cloud_run_project="as-dev-anze",
        cloud_run_region="us-central1",
        cloud_run_job_cpu="meridian-opt-cpu",
        cloud_run_job_gpu="meridian-opt-gpu",
    )
    assert cfg.cloud_run_project == "as-dev-anze"

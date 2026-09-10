"""Unit tests for RuntimeConfig cloud-tier fields and guardrails (Phase 2)."""

import pytest
from pydantic import ValidationError

from google_meridian_mcp_server.domain.models import RuntimeConfig


def _local_kwargs(**over):
    base = dict(persistence_backend="local", local_models_root="/models")
    base.update(over)
    return base


def test_the_backend_knob_is_gone():
    """D2: JAX everywhere; the knob is removed entirely rather than defaulted."""
    cfg = RuntimeConfig(**_local_kwargs())
    assert not hasattr(cfg, "backend_for_tier")
    assert not hasattr(cfg, "optimization_backend_local")
    assert not hasattr(cfg, "optimization_backend_cloud_cpu")
    assert not hasattr(cfg, "optimization_backend_cloud_gpu")


def test_removed_knobs_are_gone():
    """Every knob this change deletes must stop EXISTING, not merely stop being
    documented. RuntimeConfig is frozen but not extra='forbid', so a re-added
    field would be silently accepted everywhere else; this is the only guard."""
    cfg = RuntimeConfig(**_local_kwargs())
    for name in (
        "analysis_max_parallel",
        "analysis_queue_wait_timeout",
        "registry_backend",
        "resolved_registry_backend",
        "optimization_allowed_tiers",
        "optimization_default_tier",
        "optimization_size_thresholds",
        "discovery_ttl_seconds",
        "optimization_heartbeat_stale_seconds",
        "analysis_workdir_root",
        "analysis_workdir_ttl_seconds",
    ):
        assert not hasattr(cfg, name), f"{name} came back"


def test_cloud_tier_requires_gcs_persistence_and_cloud_run_fields():
    # cloud tier configured but persistence_backend isn't gcs -> error (Phase 1 guardrail)
    with pytest.raises(ValidationError, match="PERSISTENCE_BACKEND=gcs"):
        RuntimeConfig(**_local_kwargs(optimization_tier="cloud_cpu"))
    # gcs persistence present but Cloud Run coordinates missing -> error
    with pytest.raises(ValidationError, match="CLOUD_RUN_PROJECT"):
        RuntimeConfig(
            persistence_backend="gcs",
            gcs_bucket="b",
            gcs_models_prefix="models/",
            optimization_tier="cloud_cpu",
        )


# test_cloud_tier_with_gcs_registry_but_no_bucket_raises deleted: its
# decoupled-registry premise (PERSISTENCE_BACKEND=local + a gcs registry) no
# longer exists. The rule it exercised (GCS_BUCKET required for cloud tiers)
# is still covered by test_requires_gcs_bucket_for_gcs_backend in
# tests/unit/test_config_and_persistence.py.


def test_cloud_tier_fully_configured_is_valid():
    cfg = RuntimeConfig(
        persistence_backend="gcs",
        gcs_bucket="b",
        gcs_models_prefix="models/",
        optimization_tier="cloud_auto",
        cloud_run_project="example-dev-project",
        cloud_run_region="us-central1",
        cloud_run_job_cpu="meridian-opt-cpu",
        cloud_run_job_gpu="meridian-opt-gpu",
    )
    assert cfg.cloud_run_project == "example-dev-project"
    assert cfg.optimization_tier == "cloud_auto"


def test_unknown_optimization_tier_names_the_four_legal_values():
    with pytest.raises(ValidationError) as exc:
        RuntimeConfig(**_local_kwargs(optimization_tier="cloud"))
    for value in ("local", "cloud_cpu", "cloud_gpu", "cloud_auto"):
        assert value in str(exc.value)


def test_cloud_auto_requires_both_job_names():
    base = dict(
        persistence_backend="gcs",
        gcs_bucket="b",
        gcs_models_prefix="m/",
        optimization_tier="cloud_auto",
        cloud_run_project="p",
        cloud_run_region="r",
    )
    with pytest.raises(ValidationError, match="CLOUD_RUN_JOB_CPU"):
        RuntimeConfig(**base, cloud_run_job_gpu="g")
    with pytest.raises(ValidationError, match="CLOUD_RUN_JOB_GPU"):
        RuntimeConfig(**base, cloud_run_job_cpu="c")


def test_local_tier_with_gcs_persistence_is_legal():
    """Section 1: the run registry follows the persistence backend, so gcs
    models with a local optimization tier is a supported combination."""
    cfg = RuntimeConfig(
        persistence_backend="gcs",
        gcs_bucket="b",
        gcs_models_prefix="m/",
        optimization_tier="local",
    )
    assert cfg.optimization_tier == "local"


def test_analysis_runner_config_defaults(sample_runtime_config):
    cfg = sample_runtime_config
    assert cfg.analysis_worker_timeout == 300.0
    assert cfg.analysis_max_response_bytes == 4 * 1024 * 1024


def test_analysis_runner_config_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("PERSISTENCE_BACKEND", "local")
    monkeypatch.setenv("LOCAL_MODELS_ROOT", str(tmp_path))
    monkeypatch.setenv("ANALYSIS_WORKER_TIMEOUT", "600")
    from google_meridian_mcp_server.config import load_config

    cfg = load_config()
    assert cfg.analysis_worker_timeout == 600.0


def test_analysis_max_response_bytes_default_comes_from_load_config(
    monkeypatch, tmp_path
):
    """The SHIPPED default, read the way production reads it.

    ``test_analysis_runner_config_defaults`` constructs RuntimeConfig directly,
    so it only pins ``domain/models.py``'s dataclass fallback. ``load_config()``
    always supplies this field from ``os.getenv``, so production actually reads
    ``config.py``'s literal. Changing one and not the other leaves the suite
    green while shipping the wrong ceiling -- this test is the half-fix guard.
    """
    monkeypatch.setenv("PERSISTENCE_BACKEND", "local")
    monkeypatch.setenv("LOCAL_MODELS_ROOT", str(tmp_path))
    monkeypatch.delenv("ANALYSIS_MAX_RESPONSE_BYTES", raising=False)
    from google_meridian_mcp_server.config import load_config

    cfg = load_config()
    assert cfg.analysis_max_response_bytes == 4 * 1024 * 1024 == 4_194_304

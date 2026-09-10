"""Core domain models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator, model_validator


class Transport(str, Enum):
    STDIO = "stdio"
    STREAMABLE_HTTP = "streamable-http"


class PersistenceBackend(str, Enum):
    LOCAL = "local"
    GCS = "gcs"


class ComputeTier(str, Enum):
    LOCAL = "local"
    CLOUD_CPU = "cloud_cpu"
    CLOUD_GPU = "cloud_gpu"


class OptimizationMode(str, Enum):
    """Where THIS DEPLOYMENT runs optimizations -- a deployment mode, not a tier.

    Distinct from ComputeTier on purpose: every ComputeTier member must be
    dispatchable (cloud_run_job_for_tier maps them to Job names), and CLOUD_AUTO
    is not -- it resolves to CLOUD_CPU or CLOUD_GPU before anything dispatches.
    Declared here rather than in execution/routing.py because RuntimeConfig._check
    validates against it, and domain/ must never import from execution/.
    """

    LOCAL = "local"
    CLOUD_CPU = "cloud_cpu"
    CLOUD_GPU = "cloud_gpu"
    CLOUD_AUTO = "cloud_auto"


class ModelFormat(str, Enum):
    BINPB = "binpb"


class ModelStatus(str, Enum):
    READY = "ready"
    MISSING = "missing"
    INVALID = "invalid"


class RuntimeConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    transport: str = "streamable-http"
    persistence_backend: str = "local"
    local_models_root: str | None = None
    gcs_bucket: str | None = None
    gcs_models_prefix: str | None = None
    model_cache_root: str = "/tmp/mmm-models"
    result_cache_enabled: bool = True
    result_cache_ttl_seconds: int | None = None

    # Optimization module
    optimization_runs_root: str = "./optimizations"
    optimization_gcs_prefix: str = "optimizations/"
    optimization_tier: str = OptimizationMode.LOCAL.value
    optimization_max_parallel: int = 2
    cloud_run_project: str | None = None
    cloud_run_region: str | None = None
    cloud_run_job_cpu: str | None = None
    cloud_run_job_gpu: str | None = None

    # Analysis subprocess runner
    analysis_worker_timeout: float = 300.0

    @field_validator("transport")
    @classmethod
    def _check_transport(cls, value: str) -> str:
        valid = {t.value for t in Transport}
        if value not in valid:
            raise ValueError(
                f"Unsupported transport '{value}'. Expected one of: {sorted(valid)}"
            )
        return value

    @model_validator(mode="after")
    def _check(self) -> "RuntimeConfig":
        if self.persistence_backend == PersistenceBackend.LOCAL.value:
            if not self.local_models_root:
                raise ValueError(
                    "LOCAL_MODELS_ROOT is required when PERSISTENCE_BACKEND=local"
                )
        elif self.persistence_backend == PersistenceBackend.GCS.value:
            if not self.gcs_bucket:
                raise ValueError("GCS_BUCKET is required when PERSISTENCE_BACKEND=gcs")
            if not self.gcs_models_prefix:
                raise ValueError(
                    "GCS_MODELS_PREFIX is required when PERSISTENCE_BACKEND=gcs"
                )
        else:
            raise ValueError(
                f"Unsupported PERSISTENCE_BACKEND '{self.persistence_backend}'"
            )

        if (
            self.result_cache_ttl_seconds is not None
            and self.result_cache_ttl_seconds <= 0
        ):
            raise ValueError("RESULT_CACHE_TTL_SECONDS must be positive")

        valid_modes = {m.value for m in OptimizationMode}
        if self.optimization_tier not in valid_modes:
            raise ValueError(
                f"Unknown OPTIMIZATION_TIER '{self.optimization_tier}'. "
                f"Valid: {sorted(valid_modes)}"
            )
        if self.optimization_max_parallel <= 0:
            raise ValueError("OPTIMIZATION_MAX_PARALLEL must be positive")

        if self.optimization_tier != OptimizationMode.LOCAL.value:
            if self.persistence_backend != PersistenceBackend.GCS.value:
                raise ValueError(
                    "cloud optimization tiers require PERSISTENCE_BACKEND=gcs: a "
                    "Cloud Run Job worker cannot read the server's local disk"
                )
            if not self.cloud_run_project or not self.cloud_run_region:
                raise ValueError(
                    "cloud tiers require CLOUD_RUN_PROJECT and CLOUD_RUN_REGION"
                )
            needs_cpu = {
                OptimizationMode.CLOUD_CPU.value,
                OptimizationMode.CLOUD_AUTO.value,
            }
            needs_gpu = {
                OptimizationMode.CLOUD_GPU.value,
                OptimizationMode.CLOUD_AUTO.value,
            }
            if self.optimization_tier in needs_cpu and not self.cloud_run_job_cpu:
                raise ValueError(
                    f"OPTIMIZATION_TIER={self.optimization_tier} requires CLOUD_RUN_JOB_CPU"
                )
            if self.optimization_tier in needs_gpu and not self.cloud_run_job_gpu:
                raise ValueError(
                    f"OPTIMIZATION_TIER={self.optimization_tier} requires CLOUD_RUN_JOB_GPU"
                )
        return self

    def cloud_run_job_for_tier(self, tier: str) -> str | None:
        return {
            ComputeTier.CLOUD_CPU.value: self.cloud_run_job_cpu,
            ComputeTier.CLOUD_GPU.value: self.cloud_run_job_gpu,
        }.get(tier)


@dataclass(frozen=True)
class ModelCatalogEntry:
    model_id: str
    display_name: str
    source_backend: str
    source_path: str
    model_format: str
    last_modified: datetime | None = None
    etag_or_fingerprint: str | None = None
    status: str = ModelStatus.READY.value
    metadata: dict[str, Any] = field(default_factory=dict)

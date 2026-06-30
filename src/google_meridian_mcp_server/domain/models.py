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


class ModelFormat(str, Enum):
    BINPB = "binpb"
    PKL = "pkl"


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
    discovery_ttl_seconds: int = 7200
    model_cache_root: str = "/tmp/mmm-models"
    result_cache_enabled: bool = True
    result_cache_ttl_seconds: int | None = None

    # Optimization module
    registry_backend: str | None = None  # None → follows persistence_backend
    optimization_runs_root: str = "./optimizations"
    optimization_gcs_prefix: str = "optimizations/"
    optimization_allowed_tiers: tuple[str, ...] = ("local",)
    optimization_default_tier: str = "auto"
    optimization_max_parallel: int = 2
    optimization_size_thresholds: tuple[int, int] = (1_000_000, 100_000_000)
    optimization_heartbeat_stale_seconds: int = 60
    optimization_backend_local: str = "tensorflow"
    optimization_backend_cloud_cpu: str = "jax"
    optimization_backend_cloud_gpu: str = "jax"
    cloud_run_project: str | None = None
    cloud_run_region: str | None = None
    cloud_run_job_cpu: str | None = None
    cloud_run_job_gpu: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _set_registry_backend_default(cls, values: Any) -> Any:
        if isinstance(values, dict) and values.get("registry_backend") is None:
            values["registry_backend"] = values.get("persistence_backend", "local")
        return values

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

        if self.discovery_ttl_seconds <= 0:
            raise ValueError("DISCOVERY_TTL_SECONDS must be positive")
        if (
            self.result_cache_ttl_seconds is not None
            and self.result_cache_ttl_seconds <= 0
        ):
            raise ValueError("RESULT_CACHE_TTL_SECONDS must be positive")

        valid_tiers = {t.value for t in ComputeTier}
        for tier in self.optimization_allowed_tiers:
            if tier not in valid_tiers:
                raise ValueError(
                    f"Unknown optimization tier '{tier}'. Valid: {sorted(valid_tiers)}"
                )
        if not self.optimization_allowed_tiers:
            raise ValueError("OPTIMIZATION_ALLOWED_TIERS must list at least one tier")
        if self.optimization_default_tier != "auto" and (
            self.optimization_default_tier not in self.optimization_allowed_tiers
        ):
            raise ValueError(
                f"OPTIMIZATION_DEFAULT_TIER '{self.optimization_default_tier}' not in allowed tiers "
                f"{list(self.optimization_allowed_tiers)}"
            )
        if self.optimization_max_parallel <= 0:
            raise ValueError("OPTIMIZATION_MAX_PARALLEL must be positive")
        lo, hi = self.optimization_size_thresholds
        if not (0 < lo < hi):
            raise ValueError(
                "OPTIMIZATION_SIZE_THRESHOLDS must be two ascending positive ints"
            )

        cloud_tiers = {ComputeTier.CLOUD_CPU.value, ComputeTier.CLOUD_GPU.value}
        allowed_cloud = cloud_tiers & set(self.optimization_allowed_tiers)
        if allowed_cloud:
            if self.resolved_registry_backend != PersistenceBackend.GCS.value:
                raise ValueError(
                    "cloud tiers require a gcs registry (set REGISTRY_BACKEND=gcs)"
                )
            if not self.gcs_bucket:
                raise ValueError("cloud tiers require GCS_BUCKET")
            if not self.cloud_run_project or not self.cloud_run_region:
                raise ValueError(
                    "cloud tiers require CLOUD_RUN_PROJECT and CLOUD_RUN_REGION"
                )
            if (
                ComputeTier.CLOUD_CPU.value in allowed_cloud
                and not self.cloud_run_job_cpu
            ):
                raise ValueError("cloud_cpu tier requires CLOUD_RUN_JOB_CPU")
            if (
                ComputeTier.CLOUD_GPU.value in allowed_cloud
                and not self.cloud_run_job_gpu
            ):
                raise ValueError("cloud_gpu tier requires CLOUD_RUN_JOB_GPU")
        return self

    @property
    def resolved_registry_backend(self) -> str:
        return self.registry_backend or self.persistence_backend

    def backend_for_tier(self, tier: str) -> str:
        return {
            ComputeTier.LOCAL.value: self.optimization_backend_local,
            ComputeTier.CLOUD_CPU.value: self.optimization_backend_cloud_cpu,
            ComputeTier.CLOUD_GPU.value: self.optimization_backend_cloud_gpu,
        }[tier]

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

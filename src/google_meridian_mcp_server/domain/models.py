"""Core domain models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class Transport(str, Enum):
    STDIO = "stdio"
    STREAMABLE_HTTP = "streamable-http"


class PersistenceBackend(str, Enum):
    LOCAL = "local"
    GCS = "gcs"


class ModelFormat(str, Enum):
    BINPB = "binpb"
    PKL = "pkl"


class ModelStatus(str, Enum):
    READY = "ready"
    MISSING = "missing"
    INVALID = "invalid"


@dataclass(frozen=True)
class RuntimeConfig:
    transport: str = "streamable-http"
    persistence_backend: str = "local"
    local_models_root: str | None = None
    gcs_bucket: str | None = None
    gcs_models_prefix: str | None = None
    discovery_ttl_seconds: int = 7200
    model_cache_root: str = "/tmp/mmm-models"
    result_cache_enabled: bool = True
    result_cache_ttl_seconds: int | None = None

    def __post_init__(self) -> None:
        if self.transport not in {transport.value for transport in Transport}:
            raise ValueError(
                f"Unsupported transport '{self.transport}'. Expected one of: "
                f"{sorted(transport.value for transport in Transport)}"
            )
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
        if self.discovery_ttl_seconds <= 0:
            raise ValueError("DISCOVERY_TTL_SECONDS must be positive")
        if (
            self.result_cache_ttl_seconds is not None
            and self.result_cache_ttl_seconds <= 0
        ):
            raise ValueError("RESULT_CACHE_TTL_SECONDS must be positive")


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

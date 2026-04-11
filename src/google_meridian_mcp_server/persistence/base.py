"""Provider interfaces and shared model-path helpers."""

from __future__ import annotations

import abc
from pathlib import Path, PurePosixPath

from google_meridian_mcp_server.domain.models import ModelCatalogEntry


def build_model_id(relative_path: str | Path | PurePosixPath) -> str:
    """Build a stable model identifier from a backend-relative model path."""
    normalized_path = PurePosixPath(relative_path).with_suffix("")
    parts = normalized_path.parts
    if len(parts) > 1 and parts[-1] == "model":
        parts = parts[:-1]
    return "/".join(parts)


def build_display_name(model_id: str) -> str:
    """Build a human-readable display name from a stable model identifier."""
    humanized = model_id.replace("/", " / ").replace("_", " ").replace("-", " ")
    return " ".join(humanized.split()).title()


def build_cache_path(
    cache_root: Path, relative_path: str | Path | PurePosixPath
) -> Path:
    """Map a backend-relative model path into the local materialization cache."""
    return cache_root.joinpath(*PurePosixPath(relative_path).parts)


class ModelProvider(abc.ABC):
    """Abstract interface for model discovery and retrieval."""

    @abc.abstractmethod
    def discover(self) -> list[ModelCatalogEntry]:
        """List all models available in this backend."""

    @abc.abstractmethod
    def materialize(self, entry: ModelCatalogEntry, dest_dir: Path) -> Path:
        """Ensure a model file is available locally and return its path.

        For local providers this may simply return the source path.
        For remote providers this downloads to dest_dir if not already cached.
        """

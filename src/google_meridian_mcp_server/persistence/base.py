"""Provider interfaces and shared model-path helpers."""

from __future__ import annotations

import abc
import hashlib
import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath

from google_meridian_mcp_server.domain.models import MediatorFile, ModelCatalogEntry

log = logging.getLogger(__name__)

MEDIATORS_DIR = "mediators"
_STAGE2_NAME = "model.binpb"


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


@dataclass(frozen=True)
class ListedFile:
    """One supported model file as a provider listed it (no Meridian, no I/O)."""

    relative_path: PurePosixPath
    source_path: str
    etag_or_fingerprint: str | None
    last_modified: datetime | None


@dataclass(frozen=True)
class GroupedModel:
    model_id: str
    stage2: ListedFile
    mediators: tuple[MediatorFile, ...]
    model_version: str | None


def compute_model_version(
    stage2_etag: str | None, mediators: Sequence[MediatorFile]
) -> str | None:
    """Hash of every stage file's etag: "model" first, then mediators by name."""
    pairs = [["model", stage2_etag]] + [
        [f"mediator:{m.name}", m.etag_or_fingerprint]
        for m in sorted(mediators, key=lambda m: m.name)
    ]
    if any(etag is None for _, etag in pairs):
        return None
    return hashlib.sha256(json.dumps(pairs).encode()).hexdigest()[:16]


def group_model_files(files: Sequence[ListedFile]) -> list[GroupedModel]:
    """Fold ``<dir>/mediators/*.binpb`` into ``<dir>/model.binpb``; sort by model_id.

    A mediators/ file with no sibling model.binpb is an orphan: skipped with a warning,
    never a model of its own.
    """
    listed = {f.relative_path for f in files}
    mediators_by_dir: dict[PurePosixPath, list[ListedFile]] = {}
    standalone: list[ListedFile] = []
    for f in files:
        path = f.relative_path
        if path.parent.name == MEDIATORS_DIR:
            exp_dir = path.parent.parent
            if exp_dir / _STAGE2_NAME in listed:
                mediators_by_dir.setdefault(exp_dir, []).append(f)
            else:
                log.warning(
                    "Skipping orphan mediator file %s: no %s next to its mediators/ folder",
                    path,
                    exp_dir / _STAGE2_NAME,
                )
            continue
        standalone.append(f)

    grouped: list[GroupedModel] = []
    for f in standalone:
        mediators: tuple[MediatorFile, ...] = ()
        if f.relative_path.name == _STAGE2_NAME:
            mediators = tuple(
                sorted(
                    (
                        MediatorFile(
                            name=m.relative_path.stem,
                            source_path=m.source_path,
                            etag_or_fingerprint=m.etag_or_fingerprint,
                        )
                        for m in mediators_by_dir.get(f.relative_path.parent, [])
                    ),
                    key=lambda m: m.name,
                )
            )
        grouped.append(
            GroupedModel(
                model_id=build_model_id(f.relative_path),
                stage2=f,
                mediators=mediators,
                model_version=compute_model_version(f.etag_or_fingerprint, mediators),
            )
        )
    return sorted(grouped, key=lambda g: g.model_id)


def build_catalog_entries(
    files: Sequence[ListedFile], source_backend: str
) -> list[ModelCatalogEntry]:
    """Group a provider's listed files and map each group to a catalog entry."""
    return [
        ModelCatalogEntry(
            model_id=g.model_id,
            display_name=build_display_name(g.model_id),
            source_backend=source_backend,
            source_path=g.stage2.source_path,
            model_format=g.stage2.relative_path.suffix.lstrip(".").lower(),
            last_modified=g.stage2.last_modified,
            etag_or_fingerprint=g.stage2.etag_or_fingerprint,
            mediators=g.mediators,
            model_version=g.model_version,
        )
        for g in group_model_files(files)
    ]


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

    def materialize_mediator(
        self, entry: ModelCatalogEntry, mediator: MediatorFile, dest_dir: Path
    ) -> Path:
        """Ensure one mediator file of a full-funnel entry is available locally."""
        raise NotImplementedError(
            f"{type(self).__name__} does not support full-funnel mediator files"
        )

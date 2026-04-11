"""Local filesystem model provider."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from google_meridian_mcp_server.domain.errors import BackendUnavailableError
from google_meridian_mcp_server.domain.models import (
    ModelCatalogEntry,
    ModelFormat,
    PersistenceBackend,
)
from google_meridian_mcp_server.persistence.base import (
    ModelProvider,
    build_display_name,
    build_model_id,
)

log = logging.getLogger(__name__)

_SUPPORTED_EXTENSIONS = {f".{f.value}" for f in ModelFormat}


class LocalModelProvider(ModelProvider):
    """Discovers and provides models from a local directory."""

    def __init__(self, models_root: str) -> None:
        self._root = Path(models_root)

    def discover(self) -> list[ModelCatalogEntry]:
        if not self._root.is_dir():
            raise BackendUnavailableError(
                "local", f"Models root does not exist: {self._root}"
            )

        entries: list[ModelCatalogEntry] = []
        for path in sorted(self._root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in _SUPPORTED_EXTENSIONS:
                continue

            relative_path = path.relative_to(self._root)
            fmt = path.suffix.lstrip(".").lower()
            stat = path.stat()
            model_id = build_model_id(relative_path)

            entries.append(
                ModelCatalogEntry(
                    model_id=model_id,
                    display_name=build_display_name(model_id),
                    source_backend=PersistenceBackend.LOCAL.value,
                    source_path=str(path.resolve()),
                    model_format=fmt,
                    last_modified=datetime.fromtimestamp(
                        stat.st_mtime, tz=timezone.utc
                    ),
                    etag_or_fingerprint=f"size:{stat.st_size}:mtime:{stat.st_mtime}",
                )
            )

        log.info(
            "Local provider discovered %d model(s) under %s", len(entries), self._root
        )
        return entries

    def materialize(self, entry: ModelCatalogEntry, dest_dir: Path) -> Path:
        """For local models the source path is already local."""
        source = Path(entry.source_path)
        if not source.is_file():
            raise FileNotFoundError(f"Local model file not found: {source}")
        return source

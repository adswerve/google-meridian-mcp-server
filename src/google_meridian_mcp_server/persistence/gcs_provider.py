"""Google Cloud Storage model provider with ADC support."""

from __future__ import annotations

import logging
from pathlib import Path, PurePosixPath

from google_meridian_mcp_server.domain.errors import (
    AuthenticationFailedError,
    BackendUnavailableError,
)
from google_meridian_mcp_server.domain.models import (
    ModelCatalogEntry,
    ModelFormat,
    PersistenceBackend,
)
from google_meridian_mcp_server.persistence.base import (
    ModelProvider,
    build_cache_path,
    build_display_name,
    build_model_id,
)

log = logging.getLogger(__name__)

_SUPPORTED_EXTENSIONS = {f".{f.value}" for f in ModelFormat}


class GcsModelProvider(ModelProvider):
    """Discovers and downloads models from a GCS bucket prefix."""

    def __init__(self, bucket_name: str, models_prefix: str) -> None:
        self._bucket_name = bucket_name
        self._models_prefix = models_prefix.rstrip("/")

    def _blob_prefix(self) -> str:
        return f"{self._models_prefix}/" if self._models_prefix else ""

    def _relative_path_from_blob_name(self, blob_name: str) -> PurePosixPath:
        prefix = self._blob_prefix()
        relative_name = blob_name[len(prefix) :] if prefix else blob_name
        return PurePosixPath(relative_name)

    def _get_client(self):
        """Lazy import and construct a GCS client using ADC."""
        try:
            from google.cloud import storage

            return storage.Client()
        except Exception as exc:
            raise AuthenticationFailedError("gcs", str(exc)) from exc

    def discover(self) -> list[ModelCatalogEntry]:
        try:
            client = self._get_client()
            bucket = client.bucket(self._bucket_name)
        except AuthenticationFailedError:
            raise
        except Exception as exc:
            raise BackendUnavailableError("gcs", str(exc)) from exc

        prefix = self._blob_prefix()
        entries: list[ModelCatalogEntry] = []

        try:
            blobs = list(bucket.list_blobs(prefix=prefix))
        except Exception as exc:
            raise BackendUnavailableError("gcs", str(exc)) from exc

        for blob in blobs:
            name = blob.name
            relative_path = self._relative_path_from_blob_name(name)

            ext = Path(name).suffix.lower()
            if ext not in _SUPPORTED_EXTENSIONS:
                continue

            fmt = ext.lstrip(".")
            model_id = build_model_id(relative_path)

            entries.append(
                ModelCatalogEntry(
                    model_id=model_id,
                    display_name=build_display_name(model_id),
                    source_backend=PersistenceBackend.GCS.value,
                    source_path=f"gs://{self._bucket_name}/{name}",
                    model_format=fmt,
                    last_modified=blob.updated,
                    etag_or_fingerprint=blob.etag,
                )
            )

        log.info(
            "GCS provider discovered %d model(s) in gs://%s/%s",
            len(entries),
            self._bucket_name,
            self._models_prefix,
        )
        return entries

    def materialize(self, entry: ModelCatalogEntry, dest_dir: Path) -> Path:
        """Download a GCS model to a local cache directory if not present."""
        gs_prefix = f"gs://{self._bucket_name}/"
        blob_name = entry.source_path[len(gs_prefix) :]
        relative_path = self._relative_path_from_blob_name(blob_name)
        local_path = build_cache_path(dest_dir, relative_path)
        local_path.parent.mkdir(parents=True, exist_ok=True)

        if local_path.is_file() and entry.etag_or_fingerprint:
            log.debug("Cache hit for %s at %s", entry.model_id, local_path)
            return local_path

        log.info("Downloading %s to %s", entry.source_path, local_path)
        client = self._get_client()
        bucket = client.bucket(self._bucket_name)

        blob = bucket.blob(blob_name)
        blob.download_to_filename(str(local_path))

        return local_path

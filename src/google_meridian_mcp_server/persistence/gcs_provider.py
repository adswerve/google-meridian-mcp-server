"""Google Cloud Storage model provider with ADC support."""

from __future__ import annotations

import logging
import os
from pathlib import Path, PurePosixPath

from google_meridian_mcp_server.domain.errors import (
    AuthenticationFailedError,
    BackendUnavailableError,
)
from google_meridian_mcp_server.domain.models import (
    MediatorFile,
    ModelCatalogEntry,
    ModelFormat,
    PersistenceBackend,
)
from google_meridian_mcp_server.persistence.base import (
    ListedFile,
    ModelProvider,
    build_cache_path,
    build_catalog_entries,
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
        try:
            blobs = list(bucket.list_blobs(prefix=prefix))
        except Exception as exc:
            raise BackendUnavailableError("gcs", str(exc)) from exc

        listed: list[ListedFile] = []
        for blob in blobs:
            name = blob.name
            if Path(name).suffix.lower() not in _SUPPORTED_EXTENSIONS:
                continue
            listed.append(
                ListedFile(
                    relative_path=self._relative_path_from_blob_name(name),
                    source_path=f"gs://{self._bucket_name}/{name}",
                    etag_or_fingerprint=blob.etag,
                    last_modified=blob.updated,
                )
            )

        entries = build_catalog_entries(listed, PersistenceBackend.GCS.value)

        log.info(
            "GCS provider discovered %d model(s) in gs://%s/%s",
            len(entries),
            self._bucket_name,
            self._models_prefix,
        )
        return entries

    def materialize(self, entry: ModelCatalogEntry, dest_dir: Path) -> Path:
        return self._materialize_blob(
            entry.source_path, entry.etag_or_fingerprint, dest_dir, entry.model_id
        )

    def materialize_mediator(
        self, entry: ModelCatalogEntry, mediator: MediatorFile, dest_dir: Path
    ) -> Path:
        return self._materialize_blob(
            mediator.source_path,
            mediator.etag_or_fingerprint,
            dest_dir,
            f"{entry.model_id}/mediators/{mediator.name}",
        )

    def _materialize_blob(
        self, source_path: str, etag: str | None, dest_dir: Path, label: str
    ) -> Path:
        """Download a GCS blob to a local cache directory if not present, or
        if the cached copy's etag no longer matches the expected one."""
        gs_prefix = f"gs://{self._bucket_name}/"
        blob_name = source_path[len(gs_prefix) :]
        relative_path = self._relative_path_from_blob_name(blob_name)
        local_path = build_cache_path(dest_dir, relative_path)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        etag_path = local_path.parent / f"{local_path.name}.etag"

        # F12: a cached file's mere PRESENCE was previously treated as a hit
        # whenever the entry HAD an etag, without ever comparing it to what
        # was actually cached -- so a re-uploaded model (new etag, same local
        # path) was never re-downloaded, and workers analyzed the stale model
        # forever. The sidecar records the etag the CACHED file was downloaded
        # with; only a match is a real cache hit.
        #
        # (A duplicate-submit race -- two concurrent materialize() calls both
        # missing the cache and both downloading -- is benign: both write the
        # same content via the atomic .part+os.replace path below, so the
        # last os.replace just wins harmlessly. No code change needed there.)
        if local_path.is_file() and etag:
            cached_etag = etag_path.read_text().strip() if etag_path.is_file() else None
            if cached_etag == etag:
                log.debug("Cache hit for %s at %s", label, local_path)
                return local_path
            log.info(
                "Cached etag for %s is stale (cached=%s, current=%s); re-downloading",
                label,
                cached_etag,
                etag,
            )

        log.info("Downloading %s to %s", source_path, local_path)
        client = self._get_client()
        bucket = client.bucket(self._bucket_name)

        blob = bucket.blob(blob_name)
        part_path = local_path.with_suffix(local_path.suffix + f".part.{os.getpid()}")
        try:
            blob.download_to_filename(str(part_path))
            os.replace(part_path, local_path)
        finally:
            part_path.unlink(missing_ok=True)

        if etag:
            etag_part = etag_path.with_name(f"{etag_path.name}.part.{os.getpid()}")
            try:
                etag_part.write_text(etag)
                os.replace(etag_part, etag_path)
            finally:
                etag_part.unlink(missing_ok=True)

        return local_path

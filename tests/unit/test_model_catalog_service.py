"""Unit tests for model catalog orchestration."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from google_meridian_mcp_server.domain.errors import (
    BackendUnavailableError,
    ModelNotFoundError,
)
from google_meridian_mcp_server.domain.models import (
    ModelCatalogEntry,
    ModelStatus,
    PersistenceBackend,
)
from google_meridian_mcp_server.meridian.catalog import ModelCatalog
from google_meridian_mcp_server.persistence.base import ModelProvider
from google_meridian_mcp_server.persistence.cache import (
    DiscoveryCache,
    MaterializationCache,
)


def _make_entry(model_id: str = "test-model", fmt: str = "binpb") -> ModelCatalogEntry:
    return ModelCatalogEntry(
        model_id=model_id,
        display_name="Test Model",
        source_backend=PersistenceBackend.LOCAL.value,
        source_path=f"/fake/{model_id}.{fmt}",
        model_format=fmt,
        last_modified=datetime(2026, 1, 1, tzinfo=timezone.utc),
        etag_or_fingerprint="abc123",
        status=ModelStatus.READY.value,
    )


class FakeProvider(ModelProvider):
    def __init__(self, entries: list[ModelCatalogEntry] | None = None):
        self._entries = entries or []

    def discover(self) -> list[ModelCatalogEntry]:
        return self._entries

    def materialize(self, entry: ModelCatalogEntry, dest_dir: Path) -> Path:
        return Path(entry.source_path)


class TestModelCatalogListEntries:
    def test_returns_discovered_models(self):
        entry = _make_entry("alpha")
        provider = FakeProvider([entry])
        dc = DiscoveryCache(provider, ttl_seconds=3600)
        mc = MaterializationCache(provider, "/tmp/test")
        catalog = ModelCatalog(dc, mc)

        result = catalog.list_entries()
        assert len(result) == 1
        assert result[0].model_id == "alpha"

    def test_returns_empty_list_when_no_models(self):
        provider = FakeProvider([])
        dc = DiscoveryCache(provider, ttl_seconds=3600)
        mc = MaterializationCache(provider, "/tmp/test")
        catalog = ModelCatalog(dc, mc)

        assert catalog.list_entries() == []

    def test_caches_discovery_results(self):
        provider = FakeProvider([_make_entry("a")])
        dc = DiscoveryCache(provider, ttl_seconds=3600)
        mc = MaterializationCache(provider, "/tmp/test")
        catalog = ModelCatalog(dc, mc)

        first = catalog.list_entries()
        # Mutate provider — catalog should still return cached
        provider._entries = []
        second = catalog.list_entries()

        assert len(first) == 1
        assert len(second) == 1

    def test_catalog_service_leaves_missing_last_modified_as_none(self):
        from google_meridian_mcp_server.services.model_catalog_service import (
            ModelCatalogService,
        )

        entry = ModelCatalogEntry(
            model_id="alpha",
            display_name="Alpha",
            source_backend=PersistenceBackend.LOCAL.value,
            source_path="/fake/alpha.binpb",
            model_format="binpb",
            last_modified=None,
        )
        provider = FakeProvider([entry])
        dc = DiscoveryCache(provider, ttl_seconds=3600)
        mc = MaterializationCache(provider, "/tmp/test")

        payload = ModelCatalogService(ModelCatalog(dc, mc)).list_models()

        assert payload[0]["last_modified"] is None


class TestModelCatalogResolve:
    def test_raises_for_unknown_model_id(self):
        provider = FakeProvider([])
        dc = DiscoveryCache(provider, ttl_seconds=3600)
        mc = MaterializationCache(provider, "/tmp/test")
        catalog = ModelCatalog(dc, mc)

        with pytest.raises(ModelNotFoundError):
            catalog.resolve("nonexistent")

    def test_backend_error_propagates(self):
        class FailingProvider(ModelProvider):
            def discover(self):
                raise BackendUnavailableError("local", "disk full")

            def materialize(self, entry, dest_dir):
                return Path()

        dc = DiscoveryCache(FailingProvider(), ttl_seconds=3600)
        mc = MaterializationCache(FailingProvider(), "/tmp/test")
        catalog = ModelCatalog(dc, mc)

        with pytest.raises(BackendUnavailableError):
            catalog.list_entries()

"""Integration tests for local and GCS model providers."""

from __future__ import annotations

from pathlib import Path

import pytest

from google_meridian_mcp_server.domain.errors import BackendUnavailableError
from google_meridian_mcp_server.domain.models import ModelStatus, PersistenceBackend
from google_meridian_mcp_server.persistence.local_provider import LocalModelProvider


class TestLocalModelProvider:
    def test_discovers_binpb_files(self, tmp_path: Path):
        (tmp_path / "model_a.binpb").write_bytes(b"fake")
        (tmp_path / "model_b.pkl").write_bytes(b"fake")
        (tmp_path / "notes.txt").write_text("ignore me")

        provider = LocalModelProvider(str(tmp_path))
        entries = provider.discover()

        assert len(entries) == 2
        ids = {e.model_id for e in entries}
        assert ids == {"model_a", "model_b"}

    def test_discovers_nested_model_layout(self, tmp_path: Path):
        model_dir = tmp_path / "geo-revenue"
        model_dir.mkdir()
        (model_dir / "model.binpb").write_bytes(b"fake")

        provider = LocalModelProvider(str(tmp_path))
        entries = provider.discover()

        assert len(entries) == 1
        assert entries[0].model_id == "geo-revenue"
        assert entries[0].display_name == "Geo Revenue"

    def test_discover_returns_correct_metadata(self, tmp_path: Path):
        (tmp_path / "test.binpb").write_bytes(b"data")
        provider = LocalModelProvider(str(tmp_path))
        entries = provider.discover()

        assert len(entries) == 1
        entry = entries[0]
        assert entry.model_id == "test"
        assert entry.source_backend == PersistenceBackend.LOCAL.value
        assert entry.model_format == "binpb"
        assert entry.status == ModelStatus.READY.value
        assert entry.last_modified is not None
        assert entry.etag_or_fingerprint is not None

    def test_discover_empty_directory(self, tmp_path: Path):
        provider = LocalModelProvider(str(tmp_path))
        entries = provider.discover()
        assert entries == []

    def test_discover_missing_directory_raises(self):
        provider = LocalModelProvider("/nonexistent/path")
        with pytest.raises(BackendUnavailableError):
            provider.discover()

    def test_materialize_returns_source_path(self, tmp_path: Path):
        model_file = tmp_path / "m.binpb"
        model_file.write_bytes(b"data")

        provider = LocalModelProvider(str(tmp_path))
        entries = provider.discover()
        path = provider.materialize(entries[0], tmp_path / "cache")

        assert path == model_file

    def test_materialize_missing_file_raises(self, tmp_path: Path):
        from google_meridian_mcp_server.domain.models import ModelCatalogEntry

        entry = ModelCatalogEntry(
            model_id="gone",
            display_name="Gone",
            source_backend="local",
            source_path=str(tmp_path / "gone.binpb"),
            model_format="binpb",
        )
        provider = LocalModelProvider(str(tmp_path))
        with pytest.raises(FileNotFoundError):
            provider.materialize(entry, tmp_path / "cache")

    def test_display_name_formatting(self, tmp_path: Path):
        (tmp_path / "my-cool_model.binpb").write_bytes(b"x")
        provider = LocalModelProvider(str(tmp_path))
        entries = provider.discover()
        assert entries[0].display_name == "My Cool Model"

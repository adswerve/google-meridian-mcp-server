"""Unit tests for configuration parsing and shared persistence helpers."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from unittest import mock

import pytest
from pydantic import ValidationError

from google_meridian_mcp_server.config import _read_bool, load_config
from google_meridian_mcp_server.domain.errors import (
    AuthenticationFailedError,
    BackendUnavailableError,
)
from google_meridian_mcp_server.domain.models import ModelCatalogEntry, RuntimeConfig
from google_meridian_mcp_server.persistence.base import (
    build_cache_path,
    build_display_name,
    build_model_id,
)
from google_meridian_mcp_server.persistence.gcs_provider import GcsModelProvider


class TestReadBool:
    def test_returns_default_when_env_missing(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("BOOL_VAR", raising=False)
        assert _read_bool("BOOL_VAR", default=True) is True
        assert _read_bool("BOOL_VAR", default=False) is False

    @pytest.mark.parametrize("value", ["1", "true", " TRUE ", "yes", "on"])
    def test_recognizes_truthy_values(
        self, monkeypatch: pytest.MonkeyPatch, value: str
    ):
        monkeypatch.setenv("BOOL_VAR", value)
        assert _read_bool("BOOL_VAR", default=False) is True

    @pytest.mark.parametrize("value", ["0", "false", "no", "off", " anything "])
    def test_treats_other_values_as_false(
        self, monkeypatch: pytest.MonkeyPatch, value: str
    ):
        monkeypatch.setenv("BOOL_VAR", value)
        assert _read_bool("BOOL_VAR", default=True) is False


class TestLoadConfig:
    def test_reads_local_backend_settings(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("MCP_TRANSPORT", "stdio")
        monkeypatch.setenv("PERSISTENCE_BACKEND", "local")
        monkeypatch.setenv("LOCAL_MODELS_ROOT", "/models")
        monkeypatch.setenv("DISCOVERY_TTL_SECONDS", "12")
        monkeypatch.setenv("MODEL_CACHE_ROOT", "/tmp/cache")
        monkeypatch.setenv("RESULT_CACHE_ENABLED", "off")
        monkeypatch.setenv("RESULT_CACHE_TTL_SECONDS", "30")
        monkeypatch.delenv("GCS_BUCKET", raising=False)
        monkeypatch.delenv("GCS_MODELS_PREFIX", raising=False)

        cfg = load_config()

        assert cfg.transport == "stdio"
        assert cfg.persistence_backend == "local"
        assert cfg.local_models_root == "/models"
        assert cfg.discovery_ttl_seconds == 12
        assert cfg.model_cache_root == "/tmp/cache"
        assert cfg.result_cache_enabled is False
        assert cfg.result_cache_ttl_seconds == 30


class TestRuntimeConfigValidation:
    def test_rejects_invalid_transport(self):
        with pytest.raises(ValueError, match="Unsupported transport"):
            RuntimeConfig(
                transport="socket",
                persistence_backend="local",
                local_models_root="/models",
            )

    def test_requires_local_models_root_for_local_backend(self):
        with pytest.raises(ValueError, match="LOCAL_MODELS_ROOT"):
            RuntimeConfig(persistence_backend="local", local_models_root=None)

    def test_requires_gcs_bucket_for_gcs_backend(self):
        with pytest.raises(ValueError, match="GCS_BUCKET"):
            RuntimeConfig(
                persistence_backend="gcs",
                gcs_bucket=None,
                gcs_models_prefix="models",
            )

    def test_requires_gcs_models_prefix_for_gcs_backend(self):
        with pytest.raises(ValueError, match="GCS_MODELS_PREFIX"):
            RuntimeConfig(
                persistence_backend="gcs",
                gcs_bucket="bucket",
                gcs_models_prefix=None,
            )

    def test_requires_positive_discovery_ttl(self):
        with pytest.raises(ValueError, match="DISCOVERY_TTL_SECONDS"):
            RuntimeConfig(
                persistence_backend="local",
                local_models_root="/models",
                discovery_ttl_seconds=0,
            )

    def test_requires_positive_result_cache_ttl(self):
        with pytest.raises(ValueError, match="RESULT_CACHE_TTL_SECONDS"):
            RuntimeConfig(
                persistence_backend="local",
                local_models_root="/models",
                result_cache_ttl_seconds=0,
            )


class TestOptimizationConfig:
    def test_runtime_config_defaults_local(self):
        cfg = RuntimeConfig(persistence_backend="local", local_models_root="/models")
        assert cfg.registry_backend == "local"  # follows persistence_backend
        assert cfg.optimization_allowed_tiers == ("local",)
        assert cfg.optimization_default_tier == "auto"
        assert cfg.optimization_max_parallel == 2
        assert cfg.optimization_size_thresholds == (10_000_000, 100_000_000)
        assert cfg.optimization_backend_local == "tensorflow"

    def test_runtime_config_local_requires_models_root(self):
        with pytest.raises(ValidationError, match="LOCAL_MODELS_ROOT"):
            RuntimeConfig(persistence_backend="local", local_models_root=None)

    def test_runtime_config_cloud_tier_requires_gcs_registry(self):
        with pytest.raises(ValidationError, match="cloud .* require .* gcs registry"):
            RuntimeConfig(
                persistence_backend="local",
                local_models_root="/models",
                registry_backend="local",
                optimization_allowed_tiers=("cloud_cpu",),
            )

    def test_runtime_config_default_tier_must_be_allowed(self):
        with pytest.raises(ValidationError, match="not in allowed tiers"):
            RuntimeConfig(
                persistence_backend="local",
                local_models_root="/models",
                optimization_default_tier="cloud_gpu",
                optimization_allowed_tiers=("local",),
            )


class TestPersistenceHelpers:
    def test_build_model_id_strips_suffix_and_nested_model_name(self):
        assert build_model_id("geo-revenue/model.binpb") == "geo-revenue"
        assert build_model_id(Path("retail/model.pkl")) == "retail"
        assert build_model_id(PurePosixPath("nested/demo.binpb")) == "nested/demo"

    def test_build_display_name_humanizes_delimiters(self):
        assert (
            build_display_name("geo-revenue/my_cool-model")
            == "Geo Revenue / My Cool Model"
        )

    def test_build_cache_path_preserves_nested_relative_paths(self):
        path = build_cache_path(Path("/tmp/cache"), "geo/revenue/model.binpb")
        assert path == Path("/tmp/cache/geo/revenue/model.binpb")


class _FakeBlob:
    def __init__(self, name: str, updated: object = None, etag: str | None = None):
        self.name = name
        self.updated = updated
        self.etag = etag
        self.download_to_filename = mock.Mock()


class _FakeBucket:
    def __init__(self, blobs: list[_FakeBlob] | None = None):
        self._blobs = blobs or []
        self.list_blobs = mock.Mock(return_value=self._blobs)
        self.blob_map = {blob.name: blob for blob in self._blobs}

    def blob(self, name: str) -> _FakeBlob:
        return self.blob_map[name]


class _FakeClient:
    def __init__(self, bucket: _FakeBucket):
        self._bucket = bucket

    def bucket(self, bucket_name: str) -> _FakeBucket:
        assert bucket_name == "bucket"
        return self._bucket


class TestGcsModelProvider:
    def test_blob_prefix_and_relative_path_normalization(self):
        provider = GcsModelProvider("bucket", "models/root/")
        assert provider._blob_prefix() == "models/root/"
        assert provider._relative_path_from_blob_name(
            "models/root/geo/model.binpb"
        ) == PurePosixPath("geo/model.binpb")

    def test_get_client_wraps_auth_failures(self, monkeypatch: pytest.MonkeyPatch):
        provider = GcsModelProvider("bucket", "models")

        class _FailingStorage:
            @staticmethod
            def Client():
                raise RuntimeError("bad adc")

        import google.cloud

        monkeypatch.setattr(google.cloud, "storage", _FailingStorage, raising=False)

        with pytest.raises(AuthenticationFailedError, match="bad adc"):
            provider._get_client()

    def test_discover_propagates_authentication_errors(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        provider = GcsModelProvider("bucket", "models")
        monkeypatch.setattr(
            provider,
            "_get_client",
            mock.Mock(
                side_effect=AuthenticationFailedError("gcs", "credentials unavailable")
            ),
        )

        with pytest.raises(AuthenticationFailedError):
            provider.discover()

    def test_discover_wraps_bucket_access_failures(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        provider = GcsModelProvider("bucket", "models")
        failing_client = SimpleNamespace(
            bucket=mock.Mock(side_effect=RuntimeError("bucket missing"))
        )
        monkeypatch.setattr(
            provider, "_get_client", mock.Mock(return_value=failing_client)
        )

        with pytest.raises(BackendUnavailableError, match="bucket missing"):
            provider.discover()

    def test_discover_wraps_blob_listing_failures(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        provider = GcsModelProvider("bucket", "models")
        bucket = _FakeBucket()
        bucket.list_blobs.side_effect = RuntimeError("list failed")
        monkeypatch.setattr(
            provider, "_get_client", mock.Mock(return_value=_FakeClient(bucket))
        )

        with pytest.raises(BackendUnavailableError, match="list failed"):
            provider.discover()

    def test_discover_skips_unsupported_blob_extensions(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        blobs = [
            _FakeBlob("models/root/geo/model.binpb", etag="a"),
            _FakeBlob("models/root/notes.txt", etag="b"),
            _FakeBlob("models/root/demo.pkl", etag="c"),
        ]
        bucket = _FakeBucket(blobs)
        provider = GcsModelProvider("bucket", "models/root")
        monkeypatch.setattr(
            provider, "_get_client", mock.Mock(return_value=_FakeClient(bucket))
        )

        entries = provider.discover()

        assert [entry.model_id for entry in entries] == ["geo", "demo"]
        assert entries[0].source_path == "gs://bucket/models/root/geo/model.binpb"
        assert entries[1].model_format == "pkl"

    def test_materialize_returns_existing_cached_file_without_download(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        provider = GcsModelProvider("bucket", "models")
        entry = ModelCatalogEntry(
            model_id="geo",
            display_name="Geo",
            source_backend="gcs",
            source_path="gs://bucket/models/geo/model.binpb",
            model_format="binpb",
            etag_or_fingerprint="etag-1",
        )
        cached_file = tmp_path / "geo" / "model.binpb"
        cached_file.parent.mkdir(parents=True)
        cached_file.write_bytes(b"cached")
        monkeypatch.setattr(
            provider, "_get_client", mock.Mock(side_effect=AssertionError)
        )

        assert provider.materialize(entry, tmp_path) == cached_file

    def test_materialize_downloads_to_nested_cache_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        provider = GcsModelProvider("bucket", "models")
        blob = _FakeBlob("models/geo/model.binpb")
        bucket = _FakeBucket([blob])
        entry = ModelCatalogEntry(
            model_id="geo",
            display_name="Geo",
            source_backend="gcs",
            source_path="gs://bucket/models/geo/model.binpb",
            model_format="binpb",
        )
        monkeypatch.setattr(
            provider, "_get_client", mock.Mock(return_value=_FakeClient(bucket))
        )

        local_path = provider.materialize(entry, tmp_path)

        assert local_path == tmp_path / "geo" / "model.binpb"
        blob.download_to_filename.assert_called_once_with(str(local_path))

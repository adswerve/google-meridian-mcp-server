"""Unit tests for loader and model-catalog caching behavior."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest import mock

import pytest

from google_meridian_mcp_server.domain.errors import UnsupportedModelFormatError
from google_meridian_mcp_server.domain.models import ModelCatalogEntry
from google_meridian_mcp_server.meridian.catalog import ModelCatalog
from google_meridian_mcp_server.meridian.loader import load_meridian_model
from google_meridian_mcp_server.persistence.cache import (
    DiscoveryCache,
    MaterializationCache,
)


def _make_entry(
    model_id: str = "demo", path: str = "/tmp/demo.binpb"
) -> ModelCatalogEntry:
    return ModelCatalogEntry(
        model_id=model_id,
        display_name="Demo",
        source_backend="local",
        source_path=path,
        model_format=Path(path).suffix.lstrip("."),
        last_modified=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


class _Provider:
    def __init__(self, entry: ModelCatalogEntry):
        self._entry = entry
        self.materialize = mock.Mock(return_value=Path(entry.source_path))
        self.discover = mock.Mock(return_value=[entry])


class TestMeridianLoader:
    def test_loads_binpb_models_via_meridian_serde(self, monkeypatch):
        fake_serde = SimpleNamespace(
            load_meridian=mock.Mock(return_value="proto-model")
        )
        serde_module = ModuleType("meridian.schema.serde")
        serde_module.meridian_serde = fake_serde
        schema_module = ModuleType("meridian.schema")
        meridian_module = ModuleType("meridian")

        with mock.patch.dict(
            sys.modules,
            {
                "meridian": meridian_module,
                "meridian.schema": schema_module,
                "meridian.schema.serde": serde_module,
            },
        ):
            result = load_meridian_model(Path("/tmp/model.binpb"))

        assert result == "proto-model"
        fake_serde.load_meridian.assert_called_once_with("/tmp/model.binpb")

    def test_rejects_pkl_models_with_an_actionable_error(self, tmp_path):
        """.pkl is no longer supported (Meridian 2.0 dropped save_mmm/load_mmm).

        Uses a dummy path in a temp dir rather than any fixture under
        models/ -- this must fail if the rejection is ever removed, e.g. by
        someone reinstating a pickle-loading branch in the loader.
        """
        dummy_path = tmp_path / "model.pkl"

        with pytest.raises(UnsupportedModelFormatError) as exc_info:
            load_meridian_model(dummy_path)

        error = exc_info.value
        payload = error.to_payload()
        assert payload["error_code"] == "unsupported_model_format"
        assert ".binpb" in payload["message"]
        assert "pickle" in payload["message"].lower()
        assert payload["details"]["extension"] == ".pkl"
        assert payload["details"]["expected_extension"] == ".binpb"

    def test_rejects_other_unsupported_model_extensions(self, tmp_path):
        dummy_path = tmp_path / "model.json"

        with pytest.raises(UnsupportedModelFormatError) as exc_info:
            load_meridian_model(dummy_path)

        payload = exc_info.value.to_payload()
        assert payload["error_code"] == "unsupported_model_format"
        assert ".binpb" in payload["message"]


def test_discovery_cache_defaults_to_the_two_hour_catalog_ttl():
    """Demoted from DISCOVERY_TTL_SECONDS. The VALUE is the contract now."""
    from google_meridian_mcp_server.persistence.cache import (
        DEFAULT_DISCOVERY_TTL_SECONDS,
        DiscoveryCache,
    )

    assert DEFAULT_DISCOVERY_TTL_SECONDS == 7200
    assert DiscoveryCache(object())._ttl == 7200


class TestModelCatalogCaching:
    def test_resolve_memoizes_loaded_models(self, monkeypatch):
        entry = _make_entry("demo", "/tmp/demo.binpb")
        provider = _Provider(entry)
        catalog = ModelCatalog(
            DiscoveryCache(provider, ttl_seconds=3600),
            MaterializationCache(provider, "/tmp/cache"),
        )
        loader = mock.Mock(return_value=object())
        monkeypatch.setattr(
            "google_meridian_mcp_server.meridian.catalog.load_meridian_model", loader
        )

        first = catalog.resolve("demo")
        second = catalog.resolve("demo")

        assert first is second
        provider.materialize.assert_called_once()
        loader.assert_called_once_with(Path("/tmp/demo.binpb"))

    def test_get_facade_memoizes_analyzer_facade_instances(self):
        entry = _make_entry()
        provider = _Provider(entry)
        catalog = ModelCatalog(
            DiscoveryCache(provider, ttl_seconds=3600),
            MaterializationCache(provider, "/tmp/cache"),
        )
        catalog.resolve = mock.Mock(return_value="loaded-model")  # type: ignore[method-assign]

        with mock.patch(
            "google_meridian_mcp_server.meridian.catalog.AnalyzerFacade",
            side_effect=lambda model: {"model": model},
        ) as facade_cls:
            first = catalog.get_facade("demo")
            second = catalog.get_facade("demo")

        assert first is second
        assert first == {"model": "loaded-model"}
        catalog.resolve.assert_called_once_with("demo")
        facade_cls.assert_called_once_with("loaded-model")

    def test_get_interrogator_reuses_cached_facade(self):
        entry = _make_entry()
        provider = _Provider(entry)
        catalog = ModelCatalog(
            DiscoveryCache(provider, ttl_seconds=3600),
            MaterializationCache(provider, "/tmp/cache"),
        )
        facade = object()
        catalog.get_facade = mock.Mock(return_value=facade)  # type: ignore[method-assign]

        assert catalog.get_interrogator("demo") is facade
        catalog.get_facade.assert_called_once_with("demo")

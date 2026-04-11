"""Unit tests for loader and model-catalog caching behavior."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest import mock

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

    def test_loads_pickle_models_via_meridian_model_module(self):
        fake_model_module = SimpleNamespace(
            load_mmm=mock.Mock(return_value="pickle-model")
        )
        model_package = ModuleType("meridian.model")
        model_package.model = fake_model_module
        meridian_module = ModuleType("meridian")

        with mock.patch.dict(
            sys.modules,
            {
                "meridian": meridian_module,
                "meridian.model": model_package,
            },
        ):
            result = load_meridian_model(Path("/tmp/model.pkl"))

        assert result == "pickle-model"
        fake_model_module.load_mmm.assert_called_once_with("/tmp/model.pkl")

    def test_rejects_unsupported_model_extensions(self):
        try:
            load_meridian_model(Path("/tmp/model.json"))
        except ValueError as exc:
            assert "Unsupported model format" in str(exc)
        else:
            raise AssertionError(
                "Expected unsupported model format to raise ValueError"
            )


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

"""Catalog metadata extraction and model availability checks."""

from __future__ import annotations

import logging
from typing import Any

from google_meridian_mcp_server.domain.errors import ModelNotFoundError
from google_meridian_mcp_server.domain.models import ModelCatalogEntry
from google_meridian_mcp_server.meridian.analyzer_facade import AnalyzerFacade
from google_meridian_mcp_server.meridian.interrogator import MeridianInterrogator
from google_meridian_mcp_server.meridian.loader import load_meridian_model
from google_meridian_mcp_server.meridian.optimizer_facade import OptimizerFacade
from google_meridian_mcp_server.persistence.cache import (
    DiscoveryCache,
    MaterializationCache,
)

log = logging.getLogger(__name__)


class ModelCatalog:
    """Resolves model_id to loaded Meridian objects."""

    def __init__(
        self,
        discovery_cache: DiscoveryCache,
        materialization_cache: MaterializationCache,
    ) -> None:
        self._discovery = discovery_cache
        self._materialization = materialization_cache
        self._loaded: dict[str, Any] = {}
        self._facades: dict[str, AnalyzerFacade] = {}
        self._optimizer_facades: dict[str, OptimizerFacade] = {}

    def list_entries(self) -> list[ModelCatalogEntry]:
        return self._discovery.list_models()

    def resolve(self, model_id: str) -> Any:
        """Resolve a model_id to a loaded Meridian model instance.

        Caches loaded models in memory for the lifetime of the server.
        """
        if model_id in self._loaded:
            return self._loaded[model_id]

        entry = self._discovery.get_model(model_id)
        if entry is None:
            raise ModelNotFoundError(model_id)

        local_path = self._materialization.get_local_path(entry)
        mmm = load_meridian_model(local_path)
        self._loaded[model_id] = mmm
        log.info("Model '%s' loaded and cached in memory", model_id)
        return mmm

    def get_facade(self, model_id: str) -> AnalyzerFacade:
        """Resolve a model_id to a cached AnalyzerFacade instance."""
        if model_id not in self._facades:
            self._facades[model_id] = AnalyzerFacade(self.resolve(model_id))
        return self._facades[model_id]

    def get_optimizer_facade(self, model_id: str) -> OptimizerFacade:
        """Resolve a model_id to a cached OptimizerFacade (runs BudgetOptimizer)."""
        if model_id not in self._optimizer_facades:
            self._optimizer_facades[model_id] = OptimizerFacade(self.resolve(model_id))
        return self._optimizer_facades[model_id]

    def get_interrogator(self, model_id: str) -> MeridianInterrogator:
        """Resolve a model_id to a metadata-focused interrogator instance."""
        return self.get_facade(model_id)

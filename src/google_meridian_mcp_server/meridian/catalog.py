"""Catalog metadata extraction and model availability checks."""

from __future__ import annotations

import logging
from typing import Any

from google_meridian_mcp_server.domain.errors import ModelNotFoundError
from google_meridian_mcp_server.domain.models import ModelCatalogEntry
from google_meridian_mcp_server.meridian.analyzer_facade import AnalyzerFacade
from google_meridian_mcp_server.meridian.full_funnel.loading import load_full_funnel
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
        self._mediators: dict[str, dict[str, Any]] = {}
        self._facades: dict[str, AnalyzerFacade] = {}
        self._optimizer_facades: dict[str, OptimizerFacade] = {}

    def list_entries(self) -> list[ModelCatalogEntry]:
        return self._discovery.list_models()

    def resolve(self, model_id: str) -> Any:
        """Resolve a model_id to its (stage-2) Meridian model, cached for the process."""
        if model_id in self._loaded:
            return self._loaded[model_id]

        entry = self._discovery.get_model(model_id)
        if entry is None:
            raise ModelNotFoundError(model_id)

        if entry.mediators:
            stage2_path, mediator_paths = self._materialization.get_local_paths(entry)
            mmm, mediators = load_full_funnel(stage2_path, mediator_paths)
        else:
            mmm = load_meridian_model(self._materialization.get_local_path(entry))
            mediators = {}
        self._loaded[model_id] = mmm
        self._mediators[model_id] = mediators
        log.info(
            "Model '%s' loaded and cached in memory (%d mediator model(s))",
            model_id,
            len(mediators),
        )
        return mmm

    def resolve_mediators(self, model_id: str) -> dict[str, Any]:
        """{mediator: stage-1 Meridian} for a full-funnel model; {} otherwise."""
        self.resolve(model_id)
        return self._mediators[model_id]

    def get_facade(self, model_id: str) -> AnalyzerFacade:
        """Resolve a model_id to a cached AnalyzerFacade instance.

        Full funnel: the analyzer is built HERE, outside every analysis op's try block,
        so Google's constructor validation surfaces as invalid_full_funnel_model.
        """
        if model_id not in self._facades:
            mmm = self.resolve(model_id)
            mediators = self._mediators.get(model_id)
            # Single models keep the one-argument construction (existing tests pin it).
            facade = (
                AnalyzerFacade(mmm, mediators) if mediators else AnalyzerFacade(mmm)
            )
            if mediators:
                facade._get_analyzer()
            self._facades[model_id] = facade
        return self._facades[model_id]

    def get_optimizer_facade(self, model_id: str) -> OptimizerFacade:
        """Resolve a model_id to a cached OptimizerFacade (runs BudgetOptimizer)."""
        if model_id not in self._optimizer_facades:
            mmm = self.resolve(model_id)
            mediators = self._mediators.get(model_id)
            facade = (
                OptimizerFacade(mmm, mediators) if mediators else OptimizerFacade(mmm)
            )
            if mediators:
                facade._get_analyzer()
            self._optimizer_facades[model_id] = facade
        return self._optimizer_facades[model_id]

    def get_interrogator(self, model_id: str) -> MeridianInterrogator:
        """Resolve a model_id to a metadata-focused interrogator instance."""
        return self.get_facade(model_id)

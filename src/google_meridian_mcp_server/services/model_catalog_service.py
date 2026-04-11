"""Service layer for model catalog operations."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from google_meridian_mcp_server.meridian.catalog import ModelCatalog


class ModelCatalogService:
    """Thin orchestration layer for model catalog queries."""

    def __init__(self, catalog: ModelCatalog) -> None:
        self._catalog = catalog

    def list_models(self) -> list[dict[str, Any]]:
        """Return catalog entries as JSON-serializable dictionaries."""
        results: list[dict[str, Any]] = []
        for entry in self._catalog.list_entries():
            payload = asdict(entry)
            if payload["last_modified"] is not None:
                payload["last_modified"] = payload["last_modified"].isoformat()
            results.append(payload)
        return results

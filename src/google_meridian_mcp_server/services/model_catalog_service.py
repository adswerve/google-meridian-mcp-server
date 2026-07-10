"""Service layer for model catalog operations.

Task 11: backed by the lightweight ``DiscoveryCache`` (discovery only, no
facades/materialization) so the server never imports Meridian.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from google_meridian_mcp_server.persistence.cache import DiscoveryCache


class ModelCatalogService:
    """Thin orchestration layer for model catalog queries."""

    def __init__(self, discovery: DiscoveryCache) -> None:
        self._discovery = discovery

    def list_models(self) -> list[dict[str, Any]]:
        """Return catalog entries as JSON-serializable dictionaries."""
        results: list[dict[str, Any]] = []
        for entry in self._discovery.list_models():
            payload = asdict(entry)
            if payload["last_modified"] is not None:
                payload["last_modified"] = payload["last_modified"].isoformat()
            results.append(payload)
        return results

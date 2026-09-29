"""Service layer for model catalog operations.

Task 11: backed by the lightweight ``DiscoveryCache`` (discovery only, no
facades/materialization) so the server never imports Meridian.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from typing import Any

from google_meridian_mcp_server.domain.errors import ModelNotFoundError
from google_meridian_mcp_server.domain.models import ModelCatalogEntry
from google_meridian_mcp_server.persistence.cache import DiscoveryCache


async def lookup_catalog_entry(
    discovery: DiscoveryCache, model_id: str
) -> ModelCatalogEntry:
    """Server-side catalog lookup (Meridian-free).

    get_model can trigger a full provider discover (blocking I/O), so it runs on a
    thread, as list_models does (F6). A miss invalidates and retries once, so a model
    uploaded since the last 2h refresh is still found; only then ModelNotFoundError.
    """
    entry = await asyncio.to_thread(discovery.get_model, model_id)
    if entry is None:
        discovery.invalidate()
        entry = await asyncio.to_thread(discovery.get_model, model_id)
    if entry is None:
        raise ModelNotFoundError(model_id)
    return entry


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
            payload["mediators"] = [m.name for m in entry.mediators]
            payload["funnel"] = "full_funnel" if entry.mediators else "single"
            results.append(payload)
        return results

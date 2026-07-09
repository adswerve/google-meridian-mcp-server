"""Discovery, materialization, and result-cache infrastructure."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

from google_meridian_mcp_server.domain.models import ModelCatalogEntry
from google_meridian_mcp_server.persistence.base import ModelProvider

log = logging.getLogger(__name__)

DEFAULT_RESULT_CACHE_MAX_ENTRIES = 256


class DiscoveryCache:
    """TTL-based cache for model catalog discovery results."""

    def __init__(self, provider: ModelProvider, ttl_seconds: int) -> None:
        self._provider = provider
        self._ttl = ttl_seconds
        self._entries: list[ModelCatalogEntry] = []
        self._last_refresh: float = 0.0

    def list_models(self) -> list[ModelCatalogEntry]:
        now = time.monotonic()
        if not self._entries or (now - self._last_refresh) >= self._ttl:
            self._entries = self._provider.discover()
            self._last_refresh = now
            log.debug("Discovery cache refreshed: %d entries", len(self._entries))
        return list(self._entries)

    def get_model(self, model_id: str) -> ModelCatalogEntry | None:
        for entry in self.list_models():
            if entry.model_id == model_id:
                return entry
        return None

    def invalidate(self) -> None:
        self._entries = []
        self._last_refresh = 0.0


class MaterializationCache:
    """Ensures remote models are downloaded locally only once."""

    def __init__(self, provider: ModelProvider, cache_root: str) -> None:
        self._provider = provider
        self._cache_dir = Path(cache_root)

    def get_local_path(self, entry: ModelCatalogEntry) -> Path:
        return self._provider.materialize(entry, self._cache_dir)


class ResultCache:
    """Optional in-memory cache for repeated analysis results.

    Bounded LRU (max ``max_entries``, default 256) on top of the existing TTL
    behavior: an unbounded cache would grow forever under enough distinct
    (tool, model_id, params) keys and slowly OOM the server. ``OrderedDict``
    ordering doubles as recency tracking -- ``move_to_end`` on both read and
    write hits, ``popitem(last=False)`` (oldest) to evict on overflow.
    """

    def __init__(
        self,
        enabled: bool = True,
        ttl_seconds: int | None = None,
        max_entries: int = DEFAULT_RESULT_CACHE_MAX_ENTRIES,
    ) -> None:
        self._enabled = enabled
        self._ttl = ttl_seconds
        self._max_entries = max_entries
        self._store: OrderedDict[str, tuple[float, Any]] = OrderedDict()

    @staticmethod
    def _make_key(tool_name: str, model_id: str, params: dict) -> str:
        raw = json.dumps(
            {"tool": tool_name, "model_id": model_id, **params},
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(raw.encode()).hexdigest()

    def get(self, tool_name: str, model_id: str, params: dict) -> Any | None:
        if not self._enabled:
            return None
        key = self._make_key(tool_name, model_id, params)
        entry = self._store.get(key)
        if entry is None:
            return None
        ts, value = entry
        if self._ttl and (time.monotonic() - ts) >= self._ttl:
            del self._store[key]
            return None
        self._store.move_to_end(key)  # mark most-recently-used
        return value

    def put(self, tool_name: str, model_id: str, params: dict, value: Any) -> None:
        if not self._enabled:
            return
        key = self._make_key(tool_name, model_id, params)
        self._store[key] = (time.monotonic(), value)
        self._store.move_to_end(key)  # mark most-recently-used
        while len(self._store) > self._max_entries:
            self._store.popitem(last=False)  # evict oldest

    def invalidate(self) -> None:
        self._store.clear()

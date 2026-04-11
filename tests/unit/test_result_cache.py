"""Unit tests for internal result-cache policies."""

from __future__ import annotations

from google_meridian_mcp_server.persistence.cache import ResultCache


class TestResultCacheEnabled:
    def test_put_and_get_returns_cached_value(self):
        cache = ResultCache(enabled=True)
        cache.put("tool_a", "model_1", {"k": "v"}, {"result": 42})
        result = cache.get("tool_a", "model_1", {"k": "v"})
        assert result == {"result": 42}

    def test_different_params_are_separate_keys(self):
        cache = ResultCache(enabled=True)
        cache.put("tool_a", "m1", {"x": 1}, "r1")
        cache.put("tool_a", "m1", {"x": 2}, "r2")
        assert cache.get("tool_a", "m1", {"x": 1}) == "r1"
        assert cache.get("tool_a", "m1", {"x": 2}) == "r2"

    def test_different_model_ids_are_separate(self):
        cache = ResultCache(enabled=True)
        cache.put("tool_a", "m1", {}, "r1")
        cache.put("tool_a", "m2", {}, "r2")
        assert cache.get("tool_a", "m1", {}) == "r1"
        assert cache.get("tool_a", "m2", {}) == "r2"

    def test_different_tool_names_are_separate(self):
        cache = ResultCache(enabled=True)
        cache.put("tool_a", "m1", {}, "r1")
        cache.put("tool_b", "m1", {}, "r2")
        assert cache.get("tool_a", "m1", {}) == "r1"
        assert cache.get("tool_b", "m1", {}) == "r2"

    def test_cache_miss_returns_none(self):
        cache = ResultCache(enabled=True)
        assert cache.get("tool_a", "m1", {}) is None

    def test_invalidate_clears_all(self):
        cache = ResultCache(enabled=True)
        cache.put("t", "m", {}, "v")
        cache.invalidate()
        assert cache.get("t", "m", {}) is None


class TestResultCacheDisabled:
    def test_disabled_cache_returns_none(self):
        cache = ResultCache(enabled=False)
        cache.put("t", "m", {}, "stored")
        assert cache.get("t", "m", {}) is None


class TestResultCacheTTL:
    def test_expired_entry_returns_none(self):
        cache = ResultCache(enabled=True, ttl_seconds=1)
        cache.put("t", "m", {}, "v")
        # Manually backdate the stored timestamp so it appears expired
        key = cache._make_key("t", "m", {})
        cache._store[key] = (cache._store[key][0] - 2, cache._store[key][1])
        assert cache.get("t", "m", {}) is None

    def test_non_expired_entry_returns_value(self):
        cache = ResultCache(enabled=True, ttl_seconds=3600)
        cache.put("t", "m", {}, "v")
        assert cache.get("t", "m", {}) == "v"


class TestResultCacheKeyDeterminism:
    def test_same_params_different_order_same_key(self):
        cache = ResultCache(enabled=True)
        cache.put("t", "m", {"b": 2, "a": 1}, "val")
        # Keys use sort_keys=True so order shouldn't matter
        result = cache.get("t", "m", {"a": 1, "b": 2})
        assert result == "val"

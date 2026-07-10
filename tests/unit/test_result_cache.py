"""Unit tests for internal result-cache policies."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

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


class TestResultCacheBoundedLru:
    def test_oldest_entries_evicted_past_max_entries(self):
        """F5: an unbounded ResultCache grows forever and slowly OOMs the
        server. Once more than max_entries distinct keys have been put, the
        OLDEST entries must be evicted and the cache size must never exceed
        the cap."""
        cache = ResultCache(enabled=True, max_entries=3)
        for i in range(5):
            cache.put("t", "m", {"i": i}, f"v{i}")

        assert len(cache._store) == 3
        # The oldest two (i=0, i=1) were evicted.
        assert cache.get("t", "m", {"i": 0}) is None
        assert cache.get("t", "m", {"i": 1}) is None
        # The most recent three survive.
        assert cache.get("t", "m", {"i": 2}) == "v2"
        assert cache.get("t", "m", {"i": 3}) == "v3"
        assert cache.get("t", "m", {"i": 4}) == "v4"

    def test_recently_read_entry_survives_eviction(self):
        """F5: LRU ordering -- reading an entry (get) marks it
        most-recently-used, so it survives an eviction that would otherwise
        take it out on pure insertion order."""
        cache = ResultCache(enabled=True, max_entries=2)
        cache.put("t", "m", {"i": 0}, "v0")
        cache.put("t", "m", {"i": 1}, "v1")

        cache.get("t", "m", {"i": 0})  # touch i=0 -> now most-recently-used

        cache.put("t", "m", {"i": 2}, "v2")  # over cap -> evict oldest (i=1, not i=0)

        assert cache.get("t", "m", {"i": 0}) == "v0"  # survived: was touched
        assert cache.get("t", "m", {"i": 1}) is None  # evicted: least recently used
        assert cache.get("t", "m", {"i": 2}) == "v2"

    def test_default_cap_matches_module_default(self):
        from google_meridian_mcp_server.persistence.cache import (
            DEFAULT_RESULT_CACHE_MAX_ENTRIES,
        )

        cache = ResultCache(enabled=True)
        for i in range(DEFAULT_RESULT_CACHE_MAX_ENTRIES + 10):
            cache.put("t", "m", {"i": i}, i)
        assert len(cache._store) == DEFAULT_RESULT_CACHE_MAX_ENTRIES


class TestResultCacheThreadSafety:
    def test_uses_a_lock(self):
        """R1: the F6 offload put optimization bookkeeping handlers on worker
        threads, so ResultCache's get/put (read-modify-write over a plain
        OrderedDict, including move_to_end/eviction) is no longer guaranteed
        to run only on the event-loop thread. Structural guard: a lock exists
        and is held across get/put."""
        cache = ResultCache(enabled=True)
        assert hasattr(cache, "_lock")

    def test_concurrent_get_put_from_many_threads_stays_within_cap(self):
        """Fire many concurrent get/put calls from a ThreadPoolExecutor: no
        exception should escape, and the bounded-LRU cap must never be
        exceeded even under concurrent eviction."""
        cache = ResultCache(enabled=True, max_entries=8)

        def _worker(i: int) -> None:
            for j in range(50):
                cache.put("t", "m", {"i": i, "j": j}, f"v{i}-{j}")
                cache.get("t", "m", {"i": i, "j": j})
                cache.get("t", "m", {"i": i, "j": max(j - 1, 0)})

        with ThreadPoolExecutor(max_workers=16) as pool:
            futures = [pool.submit(_worker, i) for i in range(16)]
            for f in futures:
                f.result()  # re-raises any exception from the worker

        assert len(cache._store) <= 8


class TestResultCacheKeyDeterminism:
    def test_same_params_different_order_same_key(self):
        cache = ResultCache(enabled=True)
        cache.put("t", "m", {"b": 2, "a": 1}, "val")
        # Keys use sort_keys=True so order shouldn't matter
        result = cache.get("t", "m", {"a": 1, "b": 2})
        assert result == "val"

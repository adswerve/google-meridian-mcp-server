"""Integration tests for transparent cached analysis responses."""

from __future__ import annotations

from google_meridian_mcp_server.persistence.cache import ResultCache


class TestTransparentCacheIntegration:
    """Verify that cache hits and misses produce the same external contract."""

    def test_cache_hit_returns_same_shape_as_miss(self):
        """Cached result must have identical shape to uncached result."""
        cache = ResultCache(enabled=True, ttl_seconds=3600)

        # Simulate an analysis result
        original_result = {
            "model_id": "test-model",
            "output_type": "roi",
            "row_count": 3,
            "data": [
                {"channel": "tv", "roi": 2.5},
                {"channel": "search", "roi": 3.1},
                {"channel": "social", "roi": 1.8},
            ],
        }

        params = {"output_type": "roi", "filters": {}}

        # First call: cache miss
        miss = cache.get("get_channel_summary", "test-model", params)
        assert miss is None

        # Store result
        cache.put("get_channel_summary", "test-model", params, original_result)

        # Second call: cache hit
        hit = cache.get("get_channel_summary", "test-model", params)
        assert hit is not None

        # Both must produce the same external contract
        assert hit == original_result
        assert hit["model_id"] == original_result["model_id"]
        assert hit["output_type"] == original_result["output_type"]
        assert hit["row_count"] == original_result["row_count"]

    def test_stale_cache_does_not_block_fresh_response(self):
        """Expired cache entries should not block re-computation."""
        cache = ResultCache(enabled=True, ttl_seconds=1)

        old_result = {"data": "old"}
        cache.put("tool", "m1", {}, old_result)

        # Backdate the entry so it appears expired
        key = cache._make_key("tool", "m1", {})
        cache._store[key] = (cache._store[key][0] - 2, cache._store[key][1])

        # Should return None (expired), allowing fresh computation
        assert cache.get("tool", "m1", {}) is None

    def test_disabled_cache_transparent_to_caller(self):
        """When cache is disabled, every call is a miss — same contract."""
        cache = ResultCache(enabled=False)

        cache.put("tool", "m1", {}, {"data": "cached"})
        result = cache.get("tool", "m1", {})

        # Always None when disabled
        assert result is None

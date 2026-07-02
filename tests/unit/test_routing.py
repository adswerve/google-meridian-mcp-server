import pytest

from google_meridian_mcp_server.execution.routing import resolve_tier, size_score


def test_size_score_multiplies_dims():
    assert (
        size_score(
            {
                "n_geos": 5,
                "n_time_units": 100,
                "n_channels": 3,
                "n_posterior_samples": 400,
            }
        )
        == 5 * 100 * 3 * 400
    )


def test_resolve_tier_auto_thresholds():
    allowed = ("local", "cloud_cpu", "cloud_gpu")
    th = (1_000, 1_000_000)
    assert (
        resolve_tier(500, requested="auto", allowed=allowed, thresholds=th) == "local"
    )
    assert (
        resolve_tier(50_000, requested="auto", allowed=allowed, thresholds=th)
        == "cloud_cpu"
    )
    assert (
        resolve_tier(5_000_000, requested="auto", allowed=allowed, thresholds=th)
        == "cloud_gpu"
    )


def test_resolve_tier_explicit_request_must_be_allowed():
    with pytest.raises(ValueError, match="not allowed"):
        resolve_tier(10, requested="cloud_gpu", allowed=("local",), thresholds=(1, 2))
    assert (
        resolve_tier(10, requested="local", allowed=("local",), thresholds=(1, 2))
        == "local"
    )


def test_resolve_tier_auto_falls_back_to_nearest_allowed():
    # local disabled: a small job still routes to the cheapest allowed cloud tier.
    assert (
        resolve_tier(
            10,
            requested="auto",
            allowed=("cloud_cpu", "cloud_gpu"),
            thresholds=(1_000, 1_000_000),
        )
        == "cloud_cpu"
    )

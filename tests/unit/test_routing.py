import pytest

from google_meridian_mcp_server.execution.routing import (
    _GPU_SIZE_THRESHOLD,
    resolve_tier,
    size_score,
)


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


def test_single_tier_modes_ignore_size_entirely():
    """local/cloud_cpu/cloud_gpu route every run to themselves. A mode that
    consulted the size score would fail on one of these three."""
    for mode in ("local", "cloud_cpu", "cloud_gpu"):
        assert resolve_tier(1, mode=mode, requested="auto") == mode
        assert resolve_tier(10**12, mode=mode, requested="auto") == mode


def test_cloud_auto_splits_cpu_and_gpu_at_the_threshold():
    """The cutoff is exclusive on the CPU side: `<` not `<=`."""
    assert (
        resolve_tier(_GPU_SIZE_THRESHOLD - 1, mode="cloud_auto", requested="auto")
        == "cloud_cpu"
    )
    assert (
        resolve_tier(_GPU_SIZE_THRESHOLD, mode="cloud_auto", requested="auto")
        == "cloud_gpu"
    )


def test_cloud_auto_never_resolves_to_local():
    assert resolve_tier(1, mode="cloud_auto", requested="auto") == "cloud_cpu"


def test_explicit_tier_is_honoured_when_the_mode_runs_it():
    assert resolve_tier(1, mode="cloud_auto", requested="cloud_gpu") == "cloud_gpu"
    assert resolve_tier(10**12, mode="cloud_auto", requested="cloud_cpu") == "cloud_cpu"
    assert resolve_tier(1, mode="local", requested="local") == "local"


@pytest.mark.parametrize(
    "mode,requested",
    [
        ("local", "cloud_gpu"),
        ("local", "cloud_cpu"),
        ("cloud_cpu", "local"),
        ("cloud_cpu", "cloud_gpu"),
        ("cloud_gpu", "cloud_cpu"),
        ("cloud_auto", "local"),
    ],
)
def test_incompatible_explicit_tier_raises_with_an_actionable_message(mode, requested):
    """Defect 2: this combination used to be ACCEPTED and then run on the
    wrong executor. The message must name both the request and the mode."""
    with pytest.raises(ValueError) as exc:
        resolve_tier(1, mode=mode, requested=requested)
    assert requested in str(exc.value)
    assert f"OPTIMIZATION_TIER={mode}" in str(exc.value)

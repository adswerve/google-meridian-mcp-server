"""Problem-size heuristic and compute-tier resolution."""

from __future__ import annotations

from typing import Any

from google_meridian_mcp_server.domain.models import ComputeTier

# Cheapest-first ordering used for nearest-allowed fallback.
_TIER_ORDER = (
    ComputeTier.LOCAL.value,
    ComputeTier.CLOUD_CPU.value,
    ComputeTier.CLOUD_GPU.value,
)


def model_size_features(interrogator: Any) -> dict[str, int]:
    inputs = interrogator.get_data_inputs()
    n_channels = len(inputs["media"]) + len(inputs["rf_media"])
    posterior = interrogator._mmm.inference_data.posterior
    sizes = dict(posterior.sizes)
    n_posterior_samples = int(sizes.get("chain", 1)) * int(sizes.get("draw", 1))
    return {
        "n_geos": max(1, len(interrogator.geo_names())),
        "n_time_units": max(1, len(interrogator.get_time_values())),
        "n_channels": max(1, n_channels),
        "n_posterior_samples": max(1, n_posterior_samples),
    }


def size_score(features: dict[str, int]) -> int:
    return (
        features["n_geos"]
        * features["n_time_units"]
        * features["n_channels"]
        * features["n_posterior_samples"]
    )


def _ideal_auto_tier(score: int, thresholds: tuple[int, int]) -> str:
    t_local, t_gpu = thresholds
    if score < t_local:
        return ComputeTier.LOCAL.value
    if score < t_gpu:
        return ComputeTier.CLOUD_CPU.value
    return ComputeTier.CLOUD_GPU.value


def resolve_tier(
    score: int, *, requested: str, allowed: tuple[str, ...], thresholds: tuple[int, int]
) -> str:
    if requested != "auto":
        if requested not in allowed:
            raise ValueError(
                f"compute_tier '{requested}' is not allowed by this deployment "
                f"(allowed: {list(allowed)})"
            )
        return requested
    ideal = _ideal_auto_tier(score, thresholds)
    if ideal in allowed:
        return ideal
    # Nearest-allowed fallback: scan from the ideal tier toward more capable,
    # then toward cheaper, returning the first allowed tier.
    order = list(_TIER_ORDER)
    idx = order.index(ideal)
    for candidate in order[idx:] + order[:idx][::-1]:
        if candidate in allowed:
            return candidate
    raise ValueError(f"no allowed tier among {list(allowed)}")

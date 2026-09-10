"""Problem-size heuristic and compute-tier resolution."""

from __future__ import annotations

from typing import Any

from google_meridian_mcp_server.domain.models import ComputeTier, OptimizationMode

# The single problem-size cutoff used by OPTIMIZATION_TIER=cloud_auto: below it
# a run goes to the CPU Job, at or above it to the GPU Job. Demoted from the
# two-element OPTIMIZATION_SIZE_THRESHOLDS env var, whose lower cutoff only ever
# chose local vs cloud -- a choice cloud_auto never makes. This retains today's
# upper cutoff exactly. size = geos x time_units x channels x posterior_samples.
_GPU_SIZE_THRESHOLD = 100_000_000

# Which explicit compute_tier values each deployment mode will honour.
_PERMITTED = {
    OptimizationMode.LOCAL.value: frozenset({ComputeTier.LOCAL.value}),
    OptimizationMode.CLOUD_CPU.value: frozenset({ComputeTier.CLOUD_CPU.value}),
    OptimizationMode.CLOUD_GPU.value: frozenset({ComputeTier.CLOUD_GPU.value}),
    OptimizationMode.CLOUD_AUTO.value: frozenset(
        {ComputeTier.CLOUD_CPU.value, ComputeTier.CLOUD_GPU.value}
    ),
}


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


def resolve_tier(score: int, *, mode: str, requested: str) -> str:
    """Resolve a request's compute tier against the deployment's single mode.

    There is no nearest-allowed fallback any more: with one mode there is
    nothing to fall back to. An explicit tier the deployment does not run is an
    error rather than a silent substitution -- the substitution is what let a
    cloud_gpu request run on a local subprocess.
    """
    permitted = _PERMITTED[mode]
    if requested != "auto":
        if requested not in permitted:
            raise ValueError(
                f"compute_tier '{requested}' is not available: this deployment "
                f"runs OPTIMIZATION_TIER={mode}"
            )
        return requested
    if mode == OptimizationMode.CLOUD_AUTO.value:
        return (
            ComputeTier.CLOUD_CPU.value
            if score < _GPU_SIZE_THRESHOLD
            else ComputeTier.CLOUD_GPU.value
        )
    return mode

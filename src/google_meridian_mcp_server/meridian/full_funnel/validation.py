"""File-level checks for a full-funnel experiment, run BEFORE Google's constructor.

Google's AnalyzerFullFunnel validates too, but with stage-numbered ValueErrors that do not
name the file to fix. These messages speak the operator's language: the folder layout.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np

from google_meridian_mcp_server.domain.errors import InvalidFullFunnelModelError


def _saturation_is_none(saturation_spec: Any, name: str) -> bool:
    if isinstance(saturation_spec, str):
        return saturation_spec == "none"
    return (saturation_spec or {}).get(name) == "none"


def _draws(inference_data: Any, group: str) -> tuple[int, int] | None:
    grp = getattr(inference_data, group, None)
    if grp is None:
        return None
    return int(grp.chain.size), int(grp.draw.size)


def validate_full_funnel(stage2: Any, mediators: Mapping[str, Any]) -> list[str]:
    """Raise InvalidFullFunnelModelError on a structural problem; return warnings."""
    data2 = stage2.model_context.input_data
    organic = (
        []
        if data2.organic_media_channel is None
        else [str(c) for c in data2.organic_media_channel.values]
    )
    if not organic:
        raise InvalidFullFunnelModelError(
            "model.binpb has no organic media channels, so nothing in mediators/ can "
            "be a mediator. Include each mediator as an organic_media channel in the "
            "KPI model and refit."
        )
    saturation = stage2.model_context.model_spec.saturation_spec
    paid2 = {str(c) for c in data2.get_all_paid_channels()}
    warnings: list[str] = []
    for name in sorted(mediators):
        stage1 = mediators[name]
        data1 = stage1.model_context.input_data
        file_ = f"mediators/{name}.binpb"
        if name not in organic:
            raise InvalidFullFunnelModelError(
                f"{file_} does not match any organic media channel of the KPI model "
                f"(found: {', '.join(organic)}). Rename the file to the channel name "
                "exactly."
            )
        if not _saturation_is_none(saturation, name):
            raise InvalidFullFunnelModelError(
                f'The KPI model must model mediator "{name}" linearly: set '
                f'saturation_spec={{"{name}": "none"}} in its ModelSpec and refit.'
            )
        if not np.array_equal(
            np.asarray(data1.geo.values), np.asarray(data2.geo.values)
        ):
            raise InvalidFullFunnelModelError(
                f"{file_} and the KPI model use different geos (or a different geo "
                "order)."
            )
        if not np.array_equal(
            np.asarray(data1.time.values), np.asarray(data2.time.values)
        ):
            raise InvalidFullFunnelModelError(
                f"{file_} and the KPI model use different time periods."
            )
        post1 = _draws(stage1.inference_data, "posterior")
        post2 = _draws(stage2.inference_data, "posterior")
        if post1 is not None and post2 is not None and post1 != post2:
            raise InvalidFullFunnelModelError(
                f"{file_} has {post1[0]}x{post1[1]} posterior draws, the KPI model "
                f"{post2[0]}x{post2[1]}. Fit both with the same n_chains and n_keep."
            )
        prior1 = _draws(stage1.inference_data, "prior")
        prior2 = _draws(stage2.inference_data, "prior")
        if prior1 is not None and prior2 is not None and prior1 != prior2:
            raise InvalidFullFunnelModelError(
                f"{file_} has {prior1[1]} prior draws, the KPI model {prior2[1]}. "
                "Call sample_prior with the same number of draws for both."
            )
        missing = sorted({str(c) for c in data1.get_all_paid_channels()} - paid2)
        if missing:
            raise InvalidFullFunnelModelError(
                f"{file_} uses paid channels missing from the KPI model: "
                f"{', '.join(missing)}."
            )
        kpi1 = np.asarray(data1.kpi.values, dtype=float)
        column = np.asarray(
            data2.organic_media.sel(organic_media_channel=name).values, dtype=float
        )[:, -kpi1.shape[1] :]  # organic_media spans media_time (lag history first)
        if column.shape != kpi1.shape or not np.allclose(column, kpi1, rtol=1e-5):
            warnings.append(
                f"{file_}: its KPI does not match the KPI model's organic column "
                f'"{name}". Check that the right file is in mediators/ (rescaled data '
                "can also cause this)."
            )
    return warnings

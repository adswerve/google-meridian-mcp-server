"""Thin build-if-missing loader for the validation fixture models.

Both the integration tests and the validation suite import ``ensure_fixture_model``
so there is a single place that knows how to materialize a fitted fixture.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from google_meridian_mcp_server.meridian.full_funnel.loading import load_full_funnel
from google_meridian_mcp_server.meridian.loader import load_meridian_model
from scripts.generate_validation_models import (
    DEFAULT_OUT_ROOT,
    FULL_FUNNEL_VARIANTS,
    build_all,
)


def ensure_fixture_model(model_id: str) -> Any:
    """Return a loaded, fitted Meridian model, building fixtures if missing."""
    if any(spec.key == model_id for spec in FULL_FUNNEL_VARIANTS):
        raise ValueError(
            f"{model_id!r} is a full-funnel fixture: load it with "
            "ensure_full_funnel_fixture (this helper would load stage 2 only)."
        )
    build_all(DEFAULT_OUT_ROOT, force=False)
    return load_meridian_model(DEFAULT_OUT_ROOT / model_id / "model.binpb")


def full_funnel_paths(model_id: str) -> tuple[Path, dict[str, Path]]:
    """(stage-2 path, {mediator: path}) for a full-funnel fixture, building if missing."""
    build_all(DEFAULT_OUT_ROOT, force=False)
    spec = next(s for s in FULL_FUNNEL_VARIANTS if s.key == model_id)
    root = DEFAULT_OUT_ROOT / model_id
    return root / "model.binpb", {
        name: root / "mediators" / f"{name}.binpb" for name in spec.mediator_names()
    }


def ensure_full_funnel_fixture(
    model_id: str = "geo-full-funnel",
) -> tuple[Any, dict[str, Any]]:
    """(stage-2 Meridian, {mediator: Meridian}) for a full-funnel fixture."""
    stage2_path, mediator_paths = full_funnel_paths(model_id)
    return load_full_funnel(stage2_path, mediator_paths)

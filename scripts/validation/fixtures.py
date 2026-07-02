"""Thin build-if-missing loader for the validation fixture models.

Both the integration tests and the validation suite import ``ensure_fixture_model``
so there is a single place that knows how to materialize a fitted fixture.
"""

from __future__ import annotations

from typing import Any

from google_meridian_mcp_server.meridian.loader import load_meridian_model
from scripts.generate_validation_models import DEFAULT_OUT_ROOT, build_all


def ensure_fixture_model(model_id: str) -> Any:
    """Return a loaded, fitted Meridian model, building fixtures if missing."""
    build_all(DEFAULT_OUT_ROOT, force=False)
    return load_meridian_model(DEFAULT_OUT_ROOT / model_id / "model.binpb")

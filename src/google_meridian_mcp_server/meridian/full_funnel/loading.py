"""Load a full-funnel experiment from explicit, already-materialized stage paths."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from google_meridian_mcp_server.meridian.full_funnel.validation import (
    validate_full_funnel,
)
from google_meridian_mcp_server.meridian.loader import load_meridian_model

log = logging.getLogger(__name__)


def load_full_funnel(
    stage2_path: Path | str, mediator_paths: Mapping[str, Path | str]
) -> tuple[Any, dict[str, Any]]:
    """(stage-2 Meridian, {mediator: stage-1 Meridian} sorted by name), validated."""
    stage2 = load_meridian_model(stage2_path)
    mediators = {
        name: load_meridian_model(mediator_paths[name])
        for name in sorted(mediator_paths)
    }
    for warning in validate_full_funnel(stage2, mediators):
        log.warning("full-funnel: %s", warning)
    return stage2, mediators

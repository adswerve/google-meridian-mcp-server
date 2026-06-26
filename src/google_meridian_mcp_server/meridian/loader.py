"""Meridian model materialization helpers."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


def load_meridian_model(model_path: Path | str) -> Any:
    """Load a Meridian model from a local file, auto-detecting format.

    Returns a ``meridian.model.model.Meridian`` instance.
    """
    model_path = Path(model_path)
    ext = model_path.suffix.lower()

    if ext == ".binpb":
        from meridian.schema.serde import meridian_serde

        log.info("Loading Meridian model (proto) from %s", model_path)
        return meridian_serde.load_meridian(str(model_path))
    elif ext == ".pkl":
        from meridian.model import model as model_mod

        log.info("Loading Meridian model (pickle) from %s", model_path)
        return model_mod.load_mmm(str(model_path))
    else:
        raise ValueError(f"Unsupported model format '{ext}'. Expected .binpb or .pkl")

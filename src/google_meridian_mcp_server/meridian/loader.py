"""Meridian model materialization helpers."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from google_meridian_mcp_server.domain.errors import UnsupportedModelFormatError

log = logging.getLogger(__name__)


def load_meridian_model(model_path: Path | str) -> Any:
    """Load a Meridian model from a local ``.binpb`` file.

    Returns a ``meridian.model.model.Meridian`` instance.

    Pickle (``.pkl``) models are not supported: Meridian 2.0 deprecated the
    ``save_mmm``/``load_mmm`` pickle API, and ``joblib.load`` restores
    TensorFlow ``EagerTensor``s regardless of the active backend, which
    crashes under JAX. See ``UnsupportedModelFormatError`` and
    ``reports/pkl-format-removed.md``.
    """
    model_path = Path(model_path)
    ext = model_path.suffix.lower()

    if ext == ".binpb":
        from meridian.schema.serde import meridian_serde

        log.info("Loading Meridian model (proto) from %s", model_path)
        return meridian_serde.load_meridian(str(model_path))

    raise UnsupportedModelFormatError(str(model_path), ext)

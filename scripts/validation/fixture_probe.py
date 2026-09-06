"""Fixture provenance probe. Runs in its OWN interpreter, deliberately, and
under the SAME environment the worker subprocesses get.

Own interpreter: importing meridian pulls in jax, and ``jax/__init__.py``
line 17 executes ``os.environ.setdefault('TF_CPP_MIN_LOG_LEVEL', '1')`` at
import time. The capture process spawns worker subprocesses that inherit its
environment, so if it imported meridian itself it would silently change what
every worker sees. ``tests/contract/test_server_meridian_free.py`` uses the
same subprocess pattern for the same reason.

Worker environment: the tools under test run in workers configured by
``BaseSubprocessExecutor.child_env()``. A probe run under the capture
process's own environment would report Meridian's defaults regardless of what
the tools actually ran on -- stamping the v2.0-tf manifest with the same
JAX/FLOAT64 values as v2.0-jax and destroying the whole point of having two
labels. ``manifest.probe_fixtures`` supplies the env; this module just reads.

Provenance comes from the serialized proto and the backend API, never from
warning text. Meridian only warns on a MISMATCH, so a refit label -- the one
whose provenance is cleanest -- would produce no warnings and therefore no
recorded provenance at all. The proto fields
(``computation_backend`` / ``computation_precision``) and the two backend
functions exist identically in v1.7.0 and v2.0.0, so this is version-agnostic.

Usage:
  python -m scripts.validation.fixture_probe models/_validation/national-revenue
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

_MODEL_SUFFIXES = (".binpb",)
_CHUNK = 1 << 20


def file_fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_provenance(model_path: Path) -> dict[str, Any]:
    """Trained vs current backend and precision.

    Only ``.binpb`` fixtures carry a proto; anything else reports only the
    current side (see ``_NO_PROVENANCE`` below).
    """
    from meridian import backend

    current_backend = backend.computation_backend().name
    current_precision = backend.computation_precision().name
    trained_backend = trained_precision = trained_version = None

    if model_path.suffix.lower() == ".binpb":
        from mmm.v1.model import mmm_kernel_pb2 as kernel_pb
        from mmm.v1.model.meridian import meridian_model_pb2 as meridian_pb

        kernel = kernel_pb.MmmKernel()
        kernel.ParseFromString(model_path.read_bytes())
        stored = meridian_pb.MeridianModel()
        kernel.model.Unpack(stored)
        trained_backend = meridian_pb.ComputationBackend.Name(
            stored.computation_backend
        )
        trained_precision = meridian_pb.ComputationPrecision.Name(
            stored.computation_precision
        )
        trained_version = stored.model_version

    return {
        "trained_backend": trained_backend,
        "trained_precision": trained_precision,
        "trained_model_version": trained_version,
        "current_backend": current_backend,
        "current_precision": current_precision,
        "backend_mismatch": (
            None if trained_backend is None else trained_backend != current_backend
        ),
        "precision_mismatch": (
            None
            if trained_precision is None
            else trained_precision != current_precision
        ),
    }


_NO_PROVENANCE = {
    "trained_backend": None,
    "trained_precision": None,
    "trained_model_version": None,
    "current_backend": None,
    "current_precision": None,
    "backend_mismatch": None,
    "precision_mismatch": None,
}


def probe(fixture_dir: Path) -> dict[str, Any]:
    """Fingerprint every file in a fixture and record its provenance."""
    fixture_dir = Path(fixture_dir)
    files = sorted(p for p in fixture_dir.rglob("*") if p.is_file())
    fingerprints = {
        str(path.relative_to(fixture_dir)): file_fingerprint(path) for path in files
    }
    model_files = [p for p in files if p.suffix.lower() in _MODEL_SUFFIXES]
    provenance = (
        _read_provenance(model_files[0]) if model_files else dict(_NO_PROVENANCE)
    )
    return {"fixture": fixture_dir.name, "files": fingerprints, **provenance}


def main(argv: list[str]) -> int:
    print(json.dumps(probe(Path(argv[1])), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

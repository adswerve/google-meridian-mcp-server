"""The capture manifest: everything that could explain a drift, recorded.

Written beside the snapshots, and written LAST, so a label directory without
a manifest is known-incomplete (spec section 7.2). ``diff_baseline`` compares
manifests BEFORE it compares any data, which is what makes it structurally
impossible to attribute posterior resampling to engine drift.
"""

from __future__ import annotations

import json
import platform
import subprocess
import sys
from importlib import metadata
from pathlib import Path
from typing import Any

MANIFEST_NAME = "manifest.json"

_TRACKED_PACKAGES = (
    "google-meridian",
    "fastmcp",
    "jax",
    "jaxlib",
    "tensorflow",
    "tensorflow-probability",
    "tfp-nightly",
    "pydantic",
)

# Recorded verbatim from the WORKER environment, not from this process.
_RECORDED_ENV_KEYS = (
    "MERIDIAN_BACKEND",
    "MERIDIAN_ENABLE_JAX_X64",
    "TF_CPP_MIN_LOG_LEVEL",
)


def package_versions() -> dict[str, str | None]:
    """Installed versions, read from distribution metadata -- no imports."""
    versions: dict[str, str | None] = {}
    for name in _TRACKED_PACKAGES:
        try:
            versions[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            versions[name] = None
    return versions


def probe_fixtures(
    fixture_root: Path,
    names: list[str],
    *,
    worker_env: dict[str, str],
    backend_override: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Run ``fixture_probe`` in a child interpreter, once per fixture.

    ``worker_env`` is the environment the tools under test actually ran in
    (``BaseSubprocessExecutor.child_env()``), so the recorded backend and
    precision describe the measurement, not this process.

    ``backend_override``, when given, replaces ``MERIDIAN_BACKEND`` in the
    subprocess env the probe actually runs under -- it does NOT mutate
    ``worker_env`` itself (that dict is still recorded verbatim by
    ``build_manifest``'s ``worker_env`` field and must stay frozen; see its
    docstring). This exists because ``worker_env`` -- ``child_env()`` off
    this driver process -- carries no ``MERIDIAN_BACKEND`` at all unless an
    operator happened to export one, while the real workers always get an
    explicit one from ``server.py``'s or ``worker.py``'s own resolution.
    Without this override the probe falls back to Meridian's OWN library
    default (JAX as of Meridian 2.0), silently misreporting provenance for
    every backend that isn't the library default -- exactly the bug
    ``capture_baseline.probe_backend()`` exists to close.
    """
    probe_env = dict(worker_env)
    if backend_override is not None:
        probe_env["MERIDIAN_BACKEND"] = backend_override
    probes: dict[str, dict[str, Any]] = {}
    for name in names:
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.validation.fixture_probe",
                str(Path(fixture_root) / name),
            ],
            capture_output=True,
            text=True,
            check=True,
            env=probe_env,
        )
        # Meridian and TF write banners to stdout on import; the probe's JSON
        # is always the last line.
        probes[name] = json.loads(completed.stdout.strip().splitlines()[-1])
    return probes


def build_manifest(
    *,
    label: str,
    transport: str,
    fixture_root: Path,
    fixture_names: list[str],
    worker_env: dict[str, str],
    probe_backend: str | None = None,
) -> dict[str, Any]:
    return {
        "label": label,
        "transport": transport,
        "python": platform.python_version(),
        "packages": package_versions(),
        # From the WORKER environment, recorded VERBATIM and never derived or
        # overridden here. This documents the environment this driver
        # process happened to INHERIT (a snapshot of `child_env()` with no
        # `env_base`), not what the real worker resolved MERIDIAN_BACKEND to
        # -- it is deliberately frozen: these keys feed every snapshot's
        # `capture_env` (see `capture_baseline._RECORDED_ENV_KEYS` /
        # `capture_environment`), and changing what this reports would mark
        # every existing snapshot in every existing label stale. The
        # `v1.7-engine` label's 330 snapshots cannot be regenerated (Meridian
        # 1.7 is no longer installed on this machine), so this field must
        # never change value for a given inherited environment, correct or
        # not. See `probe_backend` below for the corrected provenance.
        "worker_env": {key: worker_env.get(key) for key in _RECORDED_ENV_KEYS},
        # NEW (does not feed capture_env): the backend explicitly forced onto
        # the fixture PROBE, mirroring how the real analysis/optimization
        # workers resolve MERIDIAN_BACKEND (server.py's env_base / worker.py's
        # setdefault, both defaulting to "tensorflow"). This is what makes
        # `fixtures.*.current_backend` below describe the actual measurement
        # instead of Meridian's own library default (JAX, as of Meridian
        # 2.0). `None` means no override was supplied -- e.g. an older
        # manifest, or a caller that didn't pass one -- and the probe ran
        # under `worker_env` unmodified, exactly as before this fix.
        "probe_backend": probe_backend,
        "fixtures": probe_fixtures(
            Path(fixture_root),
            list(fixture_names),
            worker_env=worker_env,
            backend_override=probe_backend,
        ),
    }


def write_manifest(label_dir: Path, manifest: dict[str, Any]) -> Path:
    path = Path(label_dir) / MANIFEST_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True))
    return path


def read_manifest(label_dir: Path) -> dict[str, Any]:
    path = Path(label_dir) / MANIFEST_NAME
    if not path.exists():
        raise FileNotFoundError(
            f"{label_dir} has no {MANIFEST_NAME}: that capture is incomplete "
            "(the manifest is written last). Re-run capture_baseline for it."
        )
    return json.loads(path.read_text())


def fixture_fingerprints(manifest: dict[str, Any]) -> dict[str, dict[str, str]]:
    """Just the per-fixture file hashes, for the pre-diff manifest gate."""
    fixtures = manifest.get("fixtures")
    if not isinstance(fixtures, dict):
        raise SystemExit(
            f"malformed manifest for label {manifest.get('label')!r}: no 'fixtures' "
            "section. Delete the label directory and re-capture it."
        )
    try:
        return {name: probe["files"] for name, probe in fixtures.items()}
    except (TypeError, KeyError) as exc:
        raise SystemExit(
            f"malformed manifest for label {manifest.get('label')!r}: a fixture "
            f"entry has no 'files' map ({exc}). Delete the label directory and "
            "re-capture it."
        ) from exc

"""Fixture fingerprints and the capture manifest that carries them."""

import hashlib
import json

import pytest

from scripts.validation import manifest
from scripts.validation.fixture_probe import file_fingerprint, probe


def test_file_fingerprint_is_sha256_of_the_bytes(tmp_path):
    target = tmp_path / "model.bin"
    target.write_bytes(b"hello meridian")
    assert file_fingerprint(target) == hashlib.sha256(b"hello meridian").hexdigest()


def test_probe_fingerprints_every_file_relative_to_the_fixture_dir(tmp_path):
    fixture = tmp_path / "national-revenue"
    (fixture / "nested").mkdir(parents=True)
    (fixture / "a.txt").write_bytes(b"a")
    (fixture / "nested" / "b.txt").write_bytes(b"b")

    result = probe(fixture)

    assert result["fixture"] == "national-revenue"
    assert set(result["files"]) == {"a.txt", "nested/b.txt"}
    # No model file, so no provenance is read and nothing is claimed.
    assert result["trained_backend"] is None
    assert result["backend_mismatch"] is None


def test_probe_output_is_json_serializable(tmp_path):
    fixture = tmp_path / "f"
    fixture.mkdir()
    (fixture / "a.txt").write_bytes(b"a")
    json.dumps(probe(fixture))  # must not raise


def test_package_versions_reports_every_tracked_package():
    versions = manifest.package_versions()
    assert "google-meridian" in versions
    assert "fastmcp" in versions
    assert versions["fastmcp"] is not None


def test_probe_fixtures_passes_the_worker_env_to_the_child(tmp_path, monkeypatch):
    """The probe must see what the WORKERS see, not what the capture process
    happens to have -- otherwise the v2.0-tf manifest records JAX/FLOAT64."""
    captured = {}

    class _Completed:
        stdout = '{"fixture": "f", "files": {}}'

    def _fake_run(argv, **kwargs):
        captured["env"] = kwargs["env"]
        return _Completed()

    monkeypatch.setattr(manifest.subprocess, "run", _fake_run)
    manifest.probe_fixtures(
        tmp_path, ["f"], worker_env={"MERIDIAN_BACKEND": "tensorflow", "PATH": "/bin"}
    )
    assert captured["env"]["MERIDIAN_BACKEND"] == "tensorflow"


def test_probe_fixtures_backend_override_wins_over_worker_env(tmp_path, monkeypatch):
    """Regression guard: ``worker_env`` alone (no override) is exactly the
    original bug -- it carries no MERIDIAN_BACKEND unless an operator
    happened to export one, so the probe would fall back to Meridian's own
    library default. ``backend_override`` must win, and must not mutate the
    ``worker_env`` dict the caller passed in (that dict is also recorded
    verbatim in the manifest and must stay untouched)."""
    captured = {}

    class _Completed:
        stdout = '{"fixture": "f", "files": {}}'

    def _fake_run(argv, **kwargs):
        captured["env"] = kwargs["env"]
        return _Completed()

    monkeypatch.setattr(manifest.subprocess, "run", _fake_run)
    original_worker_env = {"PATH": "/bin"}  # deliberately no MERIDIAN_BACKEND
    manifest.probe_fixtures(
        tmp_path, ["f"], worker_env=original_worker_env, backend_override="tensorflow"
    )
    assert captured["env"]["MERIDIAN_BACKEND"] == "tensorflow"
    assert "MERIDIAN_BACKEND" not in original_worker_env  # not mutated


def test_probe_fixtures_without_a_backend_override_is_unchanged(tmp_path, monkeypatch):
    """No ``backend_override`` (the default) must reproduce the exact,
    pre-fix behaviour: whatever ``worker_env`` itself carries, untouched."""
    captured = {}

    class _Completed:
        stdout = '{"fixture": "f", "files": {}}'

    def _fake_run(argv, **kwargs):
        captured["env"] = kwargs["env"]
        return _Completed()

    monkeypatch.setattr(manifest.subprocess, "run", _fake_run)
    manifest.probe_fixtures(tmp_path, ["f"], worker_env={"PATH": "/bin"})
    assert "MERIDIAN_BACKEND" not in captured["env"]


def test_build_manifest_records_the_probe_backend_field(tmp_path, monkeypatch):
    """New, additive field: what backend was explicitly forced onto the
    probe, distinct from (and not derived from) the frozen ``worker_env``
    field -- so a reader can see what the tools genuinely ran on."""

    class _Completed:
        stdout = '{"fixture": "f", "files": {}}'

    monkeypatch.setattr(manifest.subprocess, "run", lambda argv, **kw: _Completed())
    built = manifest.build_manifest(
        label="v2.0-tf",
        transport="inprocess",
        fixture_root=tmp_path,
        fixture_names=["f"],
        worker_env={"MERIDIAN_BACKEND": None},
        probe_backend="tensorflow",
    )
    assert built["probe_backend"] == "tensorflow"
    # The frozen field must stay exactly what worker_env reported, unmodified
    # by the probe_backend override.
    assert built["worker_env"]["MERIDIAN_BACKEND"] is None


def test_manifest_records_the_worker_env_not_the_capture_process_env(
    tmp_path, monkeypatch
):
    class _Completed:
        stdout = '{"fixture": "f", "files": {}}'

    monkeypatch.setattr(manifest.subprocess, "run", lambda argv, **kw: _Completed())
    monkeypatch.setenv("MERIDIAN_BACKEND", "this-process-value")
    built = manifest.build_manifest(
        label="v2.0-tf",
        transport="inprocess",
        fixture_root=tmp_path,
        fixture_names=["f"],
        worker_env={
            "MERIDIAN_BACKEND": "tensorflow",
            "MERIDIAN_ENABLE_JAX_X64": "true",
        },
    )
    assert built["worker_env"]["MERIDIAN_BACKEND"] == "tensorflow"
    assert "this-process-value" not in json.dumps(built)


def test_write_then_read_manifest_roundtrips(tmp_path):
    payload = {"label": "v1.7-engine", "fixtures": {}}
    manifest.write_manifest(tmp_path, payload)
    assert manifest.read_manifest(tmp_path) == payload


def test_read_manifest_rejects_an_incomplete_capture(tmp_path):
    """The manifest is written LAST, so a directory without one is unfinished."""
    with pytest.raises(FileNotFoundError, match="incomplete"):
        manifest.read_manifest(tmp_path)


def test_fixture_fingerprints_projects_just_the_file_hashes():
    payload = {
        "fixtures": {
            "national-revenue": {
                "fixture": "national-revenue",
                "files": {"model.binpb": "abc"},
                "trained_backend": "TENSORFLOW",
            }
        }
    }
    assert manifest.fixture_fingerprints(payload) == {
        "national-revenue": {"model.binpb": "abc"}
    }


def test_fixture_fingerprints_gives_a_readable_error_on_a_malformed_manifest():
    with pytest.raises(SystemExit, match="malformed"):
        manifest.fixture_fingerprints({"label": "v1.7-engine"})

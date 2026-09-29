"""Guards for the vendored AnalyzerFullFunnel and the Meridian pin."""

from __future__ import annotations

import hashlib
import importlib.metadata
import inspect
import tomllib
from pathlib import Path

VENDORED = Path("src/google_meridian_mcp_server/meridian/full_funnel/analyzer.py")
MARKER = "# --- BEGIN VERBATIM GOOGLE CODE ---\n"
EXPECTED_BODY_SHA256 = (
    "75e737dc3d9c27639c43ee6b674028f91059e9f475356572ff9f9e8a37c93f15"
)


def test_vendored_body_is_byte_identical_to_upstream():
    body = VENDORED.read_text().split(MARKER, 1)[1]
    assert hashlib.sha256(body.encode()).hexdigest() == EXPECTED_BODY_SHA256, (
        "The vendored AnalyzerFullFunnel body changed. Never edit it: re-run "
        "scripts/vendor_full_funnel_analyzer.py and update this hash deliberately."
    )


def test_meridian_dependency_is_pinned_below_2_2():
    deps = tomllib.loads(Path("pyproject.toml").read_text())["project"]["dependencies"]
    assert "google-meridian[schema,geox]>=2.1,<2.2" in deps


def test_installed_meridian_is_a_validated_2_1_release():
    version = importlib.metadata.version("google-meridian")
    assert version.startswith("2.1."), (
        f"Meridian {version} is not validated for the vendored full-funnel analyzer; "
        "re-vendor and re-run the full-funnel gates before widening the pin."
    )


def test_vendored_class_subclasses_analyzer_with_expected_constructor():
    from meridian.analysis import analyzer

    from google_meridian_mcp_server.meridian.full_funnel.analyzer import (
        AnalyzerFullFunnel,
    )

    assert issubclass(AnalyzerFullFunnel, analyzer.Analyzer)
    params = list(inspect.signature(AnalyzerFullFunnel.__init__).parameters)
    assert params == [
        "self",
        "meridian",
        "mediator_models",
        "inference_data",
        "inference_data_mediators",
    ]


def test_every_dockerfile_ships_license_and_notice():
    for path in (
        "Dockerfile",
        "deploy/Dockerfile.worker",
        "deploy/Dockerfile.worker.gpu",
    ):
        text = Path(path).read_text()
        assert "COPY LICENSE NOTICE" in text, path

"""Contract test: the skills travel inside the built package, and a server with
no skills refuses to start.

The other skill tests import the package from the checkout, where the skills
sit beside the source. They cannot see a build that leaves the skills out, which
is how the Docker image once published none.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

import google_meridian_mcp_server.server as server_module

REPO = Path(__file__).resolve().parents[2]
PACKAGE = "google_meridian_mcp_server"

_LIST_SKILLS = """
import asyncio
from fastmcp import Client
from google_meridian_mcp_server import server

assert server.__file__.startswith("{root}"), server.__file__

async def main():
    async with Client(server.create_server()) as client:
        uris = {{str(r.uri) for r in await client.list_resources()}}
        text = (await client.read_resource("skill://meridian-analyst/SKILL.md"))[0].text
    print("URIS", sorted(uris))
    print("NAME", "name: meridian-analyst" in text)

asyncio.run(main())
"""


def _build_wheel(tmp_path: Path) -> Path:
    """Build the wheel from a copy of the sources, as `pip install .` would."""
    source = tmp_path / "source"
    source.mkdir()
    for name in ("pyproject.toml", "README.md", "LICENSE", "NOTICE"):
        shutil.copy(REPO / name, source / name)
    shutil.copytree(
        REPO / "src",
        source / "src",
        ignore=shutil.ignore_patterns("__pycache__", "*.egg-info"),
    )
    out = tmp_path / "dist"
    out.mkdir()
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import setuptools.build_meta as b; b.build_wheel(r'%s')" % out,
        ],
        cwd=source,
        check=True,
        capture_output=True,
    )
    return next(out.glob("*.whl"))


def test_built_wheel_carries_the_skills_and_serves_them(tmp_path):
    wheel = _build_wheel(tmp_path)
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        installed = (tmp_path / "installed").resolve()
        archive.extractall(installed)
    for member in (
        f"{PACKAGE}/skills/meridian-analyst/SKILL.md",
        f"{PACKAGE}/skills/meridian-analyst/references/full-funnel.md",
    ):
        assert member in names

    # Run the extracted copy from outside the checkout, so the checkout's own
    # skills folder cannot stand in for the packaged one.
    result = subprocess.run(
        [sys.executable, "-c", _LIST_SKILLS.format(root=installed)],
        cwd=tmp_path,  # PYTHONPATH outranks the checkout install
        env={
            "PYTHONPATH": str(installed),
            "PATH": "",
            "LOCAL_MODELS_ROOT": str(tmp_path),
            "MODEL_CACHE_ROOT": str(tmp_path / "cache"),
            "OPTIMIZATION_RUNS_ROOT": str(tmp_path / "runs"),
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "skill://meridian-analyst/SKILL.md" in result.stdout
    assert "NAME True" in result.stdout


def test_server_refuses_to_start_without_skills_and_names_the_path(
    tmp_path, monkeypatch
):
    missing = tmp_path / "no-skills-here"
    monkeypatch.setattr(server_module, "_SKILLS_ROOT", missing)
    with pytest.raises(RuntimeError) as raised:
        server_module.create_server()
    assert str(missing) in str(raised.value)

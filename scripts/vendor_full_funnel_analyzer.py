"""Vendor Google's userland AnalyzerFullFunnel from the Meridian demo notebook.

Re-run to re-vendor (e.g. after a Meridian upgrade); never hand-edit the output body.

  uv run python scripts/vendor_full_funnel_analyzer.py [--commit SHA]

Prints the SHA-256 of the verbatim body so tests/unit/test_full_funnel_vendored.py can pin it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import urllib.request
from pathlib import Path

DEFAULT_COMMIT = "675da46db5a0fc526e339ca90eddbd9ba18aa74d"
NOTEBOOK_URL = "https://raw.githubusercontent.com/google/meridian/{commit}/demo/Meridian_Full_Funnel.ipynb"
OUT = Path("src/google_meridian_mcp_server/meridian/full_funnel/analyzer.py")
MARKER = "# --- BEGIN VERBATIM GOOGLE CODE ---\n"
TITLE_LINE = '# @title {display-mode: "form"}\n'

HEADER = '''# Copyright 2026 The Meridian Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Google's userland full-funnel analyzer, vendored VERBATIM.

Source: https://github.com/google/meridian/blob/{commit}/demo/Meridian_Full_Funnel.ipynb
(the "Custom AnalyzerFullFunnel Definition" cell). Only this docstring and the import block
are ours; everything below the BEGIN VERBATIM marker is unmodified. Do not edit it --
re-vendor with scripts/vendor_full_funnel_analyzer.py. Excluded from ruff (pyproject.toml).
Known upstream limitation (left unpatched on purpose): a KPI model with reach & frequency
channels crashes in _map_new_data_to_mediator when a mediator model has none.
"""

# ruff: noqa
import dataclasses
from typing import Any, Mapping, Optional, Sequence

import arviz as az
import numpy as np
from meridian import backend
from meridian import constants
from meridian.analysis import analyzer
from meridian.analysis import optimizer
from meridian.analysis import tensors
from meridian.analysis import visualizer
from meridian.model import context
from meridian.model import equations
from meridian.model import model
from meridian.model import spec

'''


def extract_cell(notebook: dict) -> str:
    for cell in notebook["cells"]:
        source = "".join(cell["source"])
        if "class AnalyzerFullFunnel" in source:
            return source.replace(TITLE_LINE, "", 1).lstrip("\n")
    raise SystemExit("AnalyzerFullFunnel cell not found in notebook")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", default=DEFAULT_COMMIT)
    args = parser.parse_args()
    with urllib.request.urlopen(NOTEBOOK_URL.format(commit=args.commit)) as resp:
        notebook = json.load(resp)
    body = extract_cell(notebook)
    if not body.endswith("\n"):
        body += "\n"
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(HEADER.format(commit=args.commit) + MARKER + body)
    print(f"wrote {OUT}")
    print(f"body sha256: {hashlib.sha256(body.encode()).hexdigest()}")


if __name__ == "__main__":
    main()

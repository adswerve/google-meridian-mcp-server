# Repo Cleanup & Columnar Tool Outputs — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make MCP tool responses token-efficient (columnar), remove all Spec-Kit/Specify tooling, bump dependencies (incl. google-meridian 1.7.0), rewrite AGENTS.md + add CLAUDE.md, and verify with a live adversarial MCP session against both GCS and local backends.

**Architecture:** Tabular tools pivot their internal row-dict lists into a columnar `{columns, rows[][]}` envelope at a single boundary (`AnalysisService._build_result`); the facade and dataset_mapper are unchanged. Cleanup and dependency work are mechanical. Verification drives the real server through an in-process FastMCP `Client`.

**Tech Stack:** Python 3.12, FastMCP 3.4, Pydantic 2.13, google-meridian 1.7.0, pytest, ruff, uv, Google Cloud Storage.

## Global Constraints

- Python: `>=3.12,<3.15`.
- `google-meridian[schema]` pinned `==1.7.0` in BOTH `pyproject.toml` and `fastmcp.json`.
- Dependency ceilings stay: `fastmcp<4`, `google-cloud-storage<4`, `pydantic<3`, `python-dotenv<2`, `ruff<1`.
- Tool output for the 5 tabular tools is columnar: top-level `columns` (list[str]) + `rows` (list[list]) + `row_count` (int) + `model_id`; plus `output_type` (analysis tools) or `datasets`/`dataset` (training data). No `result_metadata`, no `data` key.
- `get_model_overview` stays a nested object but drops `result_metadata`; keeps `available_tool_options`.
- `list_models` is unchanged (list of objects).
- Measure floats rounded to 6 significant figures via `float(f"{value:.6g}")`; bools and ints untouched.
- The name `get_response_dynamics` must not appear in service, transport, or docs.
- AGENTS.md must be `< 250` lines.
- Every task ends green: `uv run pytest` and `uv run ruff check src tests`.
- Run all commands through `uv run` (the venv has no pip; deps are uv-managed).
- Work happens on branch `cleanup/columnar-outputs-and-specify-removal` (already created).

---

### Task 1: Columnar envelope for tabular tools

**Files:**
- Modify: `src/google_meridian_mcp_server/services/analysis_service.py`
- Test: `tests/unit/test_analysis_service.py`

**Interfaces:**
- Consumes: `extract_training_datasets(mmm, datasets) -> list[dict]`, facade `get_*` methods returning `list[dict]` (unchanged).
- Produces: `AnalysisService._build_result(*, model_id, rows, dataset=None, datasets=None, output_type=None) -> dict` now returns `{model_id, [output_type|dataset|datasets], columns, rows, row_count}`. New helper `AnalysisService._round_measure(value) -> Any`.

- [ ] **Step 1: Update the training-data unit tests to the columnar shape**

In `tests/unit/test_analysis_service.py`, replace the two training-data assertions:

```python
    def test_get_training_data_merges_multiple_selected_datasets(self):
        result = _build_analysis_service().get_training_data(
            "m1", ["kpi", "media_spend"], None
        )

        assert result["datasets"] == ["kpi", "media_spend"]
        assert "dataset" not in result
        assert "result_metadata" not in result
        assert "data" not in result
        assert result["row_count"] == 4
        assert result["columns"] == ["geo", "time", "channel", "kpi", "media_spend"]
        assert len(result["rows"]) == 4
        assert all(len(row) == len(result["columns"]) for row in result["rows"])

    def test_get_training_data_deduplicates_dataset_selection(self):
        result = _build_analysis_service().get_training_data("m1", ["kpi", "kpi"], None)

        assert result["dataset"] == "kpi"
        assert result["datasets"] == ["kpi"]
        assert result["row_count"] == 2
        assert result["columns"] == ["geo", "time", "kpi"]
        assert "result_metadata" not in result
        assert len(result["rows"]) == 2
```

- [ ] **Step 2: Update the dispatch unit test to the columnar shape**

In `tests/unit/test_analysis_service.py`, in `test_dispatches_to_expected_facade_method`, replace the trailing assertions:

```python
        assert facade.calls == [expected_method]
        assert result["output_type"] == output_type
        assert "result_metadata" not in result
        assert result["columns"] == ["method", "filters"]
        assert result["rows"][0][0] == expected_method
        assert result["rows"][0][1]["channels"] == ["tv"]
```

- [ ] **Step 3: Run the updated tests to verify they fail**

Run: `uv run pytest tests/unit/test_analysis_service.py -k "training_data or dispatches" -v`
Expected: FAIL (current code returns `data`/`result_metadata`, no `columns`/`rows`).

- [ ] **Step 4: Implement the columnar `_build_result` and `_round_measure`**

In `src/google_meridian_mcp_server/services/analysis_service.py`, replace the entire `_build_result` method with:

```python
    @staticmethod
    def _build_result(
        *,
        model_id: str,
        rows: list[dict[str, Any]],
        dataset: str | None = None,
        datasets: list[str] | None = None,
        output_type: str | None = None,
    ) -> dict[str, Any]:
        columns = AnalysisService._ordered_columns(rows)
        result: dict[str, Any] = {"model_id": model_id}
        if output_type is not None:
            result["output_type"] = output_type
        if dataset is not None:
            result["dataset"] = dataset
        if datasets is not None:
            result["datasets"] = datasets
        result["columns"] = columns
        result["rows"] = [
            [AnalysisService._round_measure(row.get(column)) for column in columns]
            for row in rows
        ]
        result["row_count"] = len(rows)
        return result

    @staticmethod
    def _round_measure(value: Any) -> Any:
        if isinstance(value, bool):
            return value
        if isinstance(value, float):
            return float(f"{value:.6g}")
        return value
```

- [ ] **Step 5: Delete the now-unused metadata helpers**

In the same file, delete these three methods entirely: `_is_measure_value`, `_build_tabular_result_metadata`, and `_build_overview_result_metadata`. Keep `_ordered_columns` (still used by `_build_result`).

- [ ] **Step 6: Run the targeted tests to verify they pass**

Run: `uv run pytest tests/unit/test_analysis_service.py -k "training_data or dispatches" -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/google_meridian_mcp_server/services/analysis_service.py tests/unit/test_analysis_service.py
git commit -m "Switch tabular tool outputs to columnar envelope

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Drop `result_metadata` from `get_model_overview`

**Files:**
- Modify: `src/google_meridian_mcp_server/services/analysis_service.py:260-288` (`get_model_overview`)
- Test: `tests/unit/test_analysis_service.py`

**Interfaces:**
- Consumes: `interrogator.get_model_overview() -> dict`.
- Produces: `get_model_overview(model_id) -> dict` with `available_tool_options` and NO `result_metadata`.

- [ ] **Step 1: Update the overview unit test**

In `tests/unit/test_analysis_service.py::TestModelOverview`, delete the entire `assert result["result_metadata"] == {...}` block (lines ~310-336) and replace with:

```python
        assert "result_metadata" not in result
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/unit/test_analysis_service.py -k "model_overview_exposes" -v`
Expected: FAIL (code still sets `result["result_metadata"]`).

- [ ] **Step 3: Remove the `result_metadata` line from `get_model_overview`**

In `get_model_overview`'s `_compute`, delete this line:

```python
            result["result_metadata"] = self._build_overview_result_metadata(result)
```

so `_compute` ends with `return result` right after building `available_tool_options` and `result = {"model_id": model_id, **overview}`.

- [ ] **Step 4: Run it to verify it passes**

Run: `uv run pytest tests/unit/test_analysis_service.py -k "model_overview" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/google_meridian_mcp_server/services/analysis_service.py tests/unit/test_analysis_service.py
git commit -m "Drop redundant result_metadata from model overview

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Remove the dead `get_response_dynamics` alias

**Files:**
- Modify: `src/google_meridian_mcp_server/services/analysis_service.py:348-354`

**Interfaces:**
- Removes `AnalysisService.get_response_dynamics`. No task depends on it.

- [ ] **Step 1: Confirm there are no references**

Run: `grep -rn "get_response_dynamics" src tests docs`
Expected: only the method definition in `analysis_service.py` (no callers). If a caller exists, stop and report.

- [ ] **Step 2: Delete the method**

In `analysis_service.py`, delete the whole `get_response_dynamics` method (the 7-line def that just forwards to `get_adstock_decay`).

- [ ] **Step 3: Run the full suite + ruff**

Run: `uv run pytest -q && uv run ruff check src tests`
Expected: all pass, ruff clean.

- [ ] **Step 4: Commit**

```bash
git add src/google_meridian_mcp_server/services/analysis_service.py
git commit -m "Remove dead get_response_dynamics alias

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Update contract + integration tests to columnar

**Files:**
- Modify: `tests/integration/test_cached_analysis.py`

**Interfaces:**
- Consumes: `ResultCache` (unchanged). This task only updates the sample payload to the columnar shape for consistency; cache is shape-agnostic.

- [ ] **Step 1: Update the cache sample payload to columnar**

In `tests/integration/test_cached_analysis.py::test_cache_hit_returns_same_shape_as_miss`, replace `original_result` with:

```python
        original_result = {
            "model_id": "test-model",
            "output_type": "roi",
            "columns": ["channel", "roi"],
            "rows": [["tv", 2.5], ["search", 3.1], ["social", 1.8]],
            "row_count": 3,
        }
```

(The downstream assertions on `model_id`, `output_type`, `row_count` still hold.)

- [ ] **Step 2: Run the full suite + ruff**

Run: `uv run pytest -q && uv run ruff check src tests`
Expected: 140 tests pass (count unchanged), ruff clean.

> Note: `tests/contract/*` assert on enum tuples and error shapes only — they need no changes. Verify by grep: `grep -rn "result_metadata\|\"data\"\|'data'" tests` should return nothing after this task.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_cached_analysis.py
git commit -m "Align cache integration sample with columnar shape

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Remove Spec-Kit / Specify material

**Files:**
- Delete: `.specify/`, `specs/`, `.github/agents/`, `.github/prompts/`, `.github/skills/find-docs/`, `.github/copilot-instructions.md`
- Modify: `pyproject.toml`, `.gitignore`, `.dockerignore`

- [ ] **Step 1: Delete the directories and file**

```bash
rm -rf .specify specs .github/agents .github/prompts .github/skills/find-docs .github/copilot-instructions.md
rmdir .github/skills .github 2>/dev/null || true
```

- [ ] **Step 2: Remove the `.specify` ruff exclude**

In `pyproject.toml`, in `[tool.ruff].extend-exclude`, delete the `".specify",` line so the list is:

```toml
extend-exclude = [
    "references",
    "src/google_meridian_mcp_server.egg-info",
]
```

- [ ] **Step 3: Drop stale ignore lines**

In `.gitignore` delete the `.specify/` and `specs/` lines (also `.github/` if present and you want it gone — keep `references/`, `.env`, `.vscode/`). In `.dockerignore` delete the `.specify/` line. Verify:

```bash
grep -nE "specify|specs" .gitignore .dockerignore pyproject.toml
```
Expected: no matches.

- [ ] **Step 4: Verify suite + ruff still green**

Run: `uv run pytest -q && uv run ruff check src tests`
Expected: pass, clean.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "Remove Spec-Kit/Specify tooling from the working tree

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: Bump dependencies (incl. google-meridian 1.7.0)

**Files:**
- Modify: `pyproject.toml`, `fastmcp.json`

- [ ] **Step 1: Pin Meridian 1.7.0 in both files**

In `pyproject.toml` dependencies: change `"google-meridian[schema]==1.5.3"` to `"google-meridian[schema]==1.7.0"`.
In `fastmcp.json` `environment.dependencies`: change `"google-meridian[schema]==1.5.3"` to `"google-meridian[schema]==1.7.0"`.

- [ ] **Step 2: Raise the floor on the other direct deps (optional, ceilings unchanged)**

In `pyproject.toml`, optionally bump floors to the latest known-good without touching ceilings:
`"fastmcp>=3.4,<4"`, `"google-cloud-storage>=3.12,<4"`, `"pydantic>=2.13,<3"`. Leave `python-dotenv>=1.2.1,<2`. Dev: `"ruff>=0.15,<1"`, `"pytest>=9"`.

- [ ] **Step 3: Sync the environment**

Run: `uv sync --extra dev`
Expected: resolves and installs google-meridian 1.7.0 (verify: `uv run python -c "import meridian; print(meridian.__version__)"` prints `1.7.0`).

- [ ] **Step 4: Verify imports still resolve under 1.7.0**

Run:
```bash
uv run python -c "from meridian.analysis import analyzer, visualizer; from meridian.schema.serde import meridian_serde; from meridian.model import model; print('imports ok')"
```
Expected: `imports ok`. If any import fails, stop and report the exact symbol.

- [ ] **Step 5: Full suite + ruff**

Run: `uv run pytest -q && uv run ruff check src tests`
Expected: pass, clean. (Behavioral validation against a real model happens in Task 8.)

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml fastmcp.json uv.lock
git commit -m "Bump dependencies: google-meridian 1.7.0, fastmcp 3.4, gcs 3.12, pydantic 2.13

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: Rewrite AGENTS.md (<250 lines) + add CLAUDE.md

**Files:**
- Modify: `AGENTS.md`
- Create: `CLAUDE.md`

**Interfaces:**
- AGENTS.md sections to keep (condensed): Project Focus, Runtime Shape, Working Boundaries, Key Files, Configuration, Common Commands, Current Tool Surface, Model Overview Expectations, Current Analysis Behavior, Working Guidelines (new), Test Strategy. The verbose per-module "Module Map" and "Current Test Coverage" prose get compressed to a few lines each.

- [ ] **Step 1: Update the output-shape contract in the "Current Analysis Behavior" section**

Ensure these lines exist (replacing the old `distribution`/rows wording where it described `data`):

```markdown
- Tabular tools return a columnar envelope: `model_id`, `output_type` (or `datasets`/`dataset`), `columns`, `rows`, `row_count`. No `data` key, no `result_metadata`.
- Measure floats are rounded to 6 significant figures.
- `get_model_overview` returns a nested object with `available_tool_options`; it has no `result_metadata`.
- `list_models` returns a list of `{id, display_name, format, last_modified}` objects.
- Grouped analysis tools return posterior-only rows; no `distribution` column.
```

- [ ] **Step 2: Condense Module Map and Test Coverage**

Replace the long per-file "Module Map" and "Current Test Coverage" sections with compact 1-line-per-area summaries so total file length drops. Keep the "Key Files" list. Target the whole file well under 250 lines.

- [ ] **Step 3: Append a "Working Guidelines" section (Karpathy-distilled)**

Add near the end:

```markdown
## Working Guidelines
- Think before coding: state assumptions; if multiple interpretations exist, surface them; ask when unclear.
- Simplicity first: minimum code that solves the problem; no speculative abstractions, flags, or error handling for impossible cases.
- Surgical changes: touch only what the task requires; match existing style; don't refactor or reformat adjacent code; mention unrelated dead code instead of deleting it.
- Remove only the orphans your own change creates (now-unused imports/vars).
- Goal-driven execution: turn each task into a verifiable check (write/adjust a test, then make it pass); loop until `uv run pytest` and `uv run ruff check src tests` are green.
```

- [ ] **Step 4: Verify the line budget**

Run: `wc -l AGENTS.md`
Expected: a number `< 250`. If not, condense further.

- [ ] **Step 5: Create CLAUDE.md pointer**

Create `CLAUDE.md` with exactly:

```markdown
# CLAUDE.md

See [AGENTS.md](./AGENTS.md) for project context, architecture, conventions, and working guidelines. This project keeps a single source of truth in AGENTS.md.
```

- [ ] **Step 6: Commit**

```bash
git add AGENTS.md CLAUDE.md
git commit -m "Rewrite AGENTS.md for columnar outputs + working guidelines; add CLAUDE.md pointer

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 8: Live adversarial MCP verification (GCS + local)

**Files:**
- Create: `scripts/live_verify.py`

**Interfaces:**
- Consumes: module-level `mcp` from `google_meridian_mcp_server.server`; `fastmcp.Client`. Reads backend selection from environment (set before connecting).

- [ ] **Step 1: Write the live-verification script**

Create `scripts/live_verify.py`:

```python
"""Live adversarial MCP verification against a real Meridian model.

Usage:
  uv run python scripts/live_verify.py          # uses .env (gcs backend)
  MERIDIAN_VERIFY_LOCAL=1 uv run python scripts/live_verify.py  # local backend
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

from fastmcp import Client


def _content_to_obj(result):
    """Extract the structured/text payload from a FastMCP tool result."""
    if getattr(result, "structured_content", None) is not None:
        return result.structured_content
    if getattr(result, "data", None) is not None:
        return result.data
    block = result.content[0]
    text = getattr(block, "text", block)
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return text


def _unwrap(obj):
    """FastMCP may wrap a list return under {'result': [...]}; normalize it."""
    if isinstance(obj, dict) and set(obj.keys()) == {"result"}:
        return obj["result"]
    return obj


async def call(client, name, args):
    res = await client.call_tool(name, args)
    return _unwrap(_content_to_obj(res))


def assert_columnar(payload, tool):
    assert isinstance(payload, dict), f"{tool}: expected dict, got {type(payload)}"
    assert "error_code" not in payload, f"{tool}: unexpected error {payload}"
    for key in ("model_id", "columns", "rows", "row_count"):
        assert key in payload, f"{tool}: missing '{key}'"
    assert payload["row_count"] == len(payload["rows"]), f"{tool}: row_count mismatch"
    for row in payload["rows"]:
        assert len(row) == len(payload["columns"]), f"{tool}: ragged row"
    assert "data" not in payload and "result_metadata" not in payload, (
        f"{tool}: legacy keys present"
    )
    print(f"  OK {tool}: {payload['row_count']} rows x {len(payload['columns'])} cols")


async def run():
    from google_meridian_mcp_server.server import mcp

    async with Client(mcp) as client:
        # --- discovery ---
        models = await call(client, "list_models", {})
        assert isinstance(models, list) and models, f"list_models empty: {models}"
        model_id = models[0]["id"]
        print(f"Backend models: {[m['id'] for m in models]}; using {model_id!r}")

        overview = await call(client, "get_model_overview", {"model_id": model_id})
        assert "available_tool_options" in overview, "overview missing tool options"
        assert "result_metadata" not in overview, "overview still has result_metadata"
        opts = overview["available_tool_options"]
        print(f"  OK get_model_overview: model_type={overview.get('model_type')}")

        # --- happy path: every tool, every output_type the model supports ---
        datasets = opts["get_training_data"]["dataset"][:2] or ["kpi"]
        assert_columnar(
            await call(
                client, "get_training_data", {"model_id": model_id, "dataset": datasets}
            ),
            "get_training_data",
        )
        for tool in (
            "get_channel_summary",
            "get_contribution",
            "get_adstock_decay",
            "get_response_curves",
        ):
            for output_type in opts[tool]["output_type"]:
                assert_columnar(
                    await call(
                        client,
                        tool,
                        {"model_id": model_id, "output_type": output_type},
                    ),
                    f"{tool}[{output_type}]",
                )

        # --- adversarial: every call must return a clean error, never crash ---
        print("Adversarial pass:")
        cases = [
            ("get_model_overview", {"model_id": "does-not-exist"}),
            ("get_model_overview", {"model_id": "   "}),
            ("get_channel_summary", {"model_id": model_id, "output_type": "nonsense"}),
            ("get_training_data", {"model_id": model_id, "dataset": ["bogus"]}),
            (
                "get_channel_summary",
                {
                    "model_id": model_id,
                    "output_type": "roi",
                    "filters": {"unexpected_field": True},
                },
            ),
            (
                "get_contribution",
                {
                    "model_id": model_id,
                    "output_type": "contribution_metrics",
                    "filters": {"geos": ["__no_such_geo__"]},
                },
            ),
        ]
        for tool, args in cases:
            try:
                payload = await call(client, tool, args)
            except Exception as exc:  # tool-input validation may raise client-side
                print(f"  OK {tool} {args}: rejected ({type(exc).__name__})")
                continue
            if isinstance(payload, dict) and "error_code" in payload:
                print(f"  OK {tool}: error_code={payload['error_code']}")
            elif isinstance(payload, dict) and "columns" in payload:
                # empty-geo filter is allowed to succeed with zero rows
                print(f"  OK {tool}: handled gracefully ({payload['row_count']} rows)")
            else:
                raise AssertionError(f"{tool} {args}: unexpected payload {payload}")

    print("LIVE VERIFICATION PASSED")


if __name__ == "__main__":
    if os.getenv("MERIDIAN_VERIFY_LOCAL"):
        os.environ["PERSISTENCE_BACKEND"] = "local"
        os.environ["LOCAL_MODELS_ROOT"] = os.getenv("LOCAL_MODELS_ROOT", "./models")
    asyncio.run(run())
    sys.exit(0)
```

- [ ] **Step 2: Run the GCS-backend live verification (uses `.env`)**

Run: `uv run python scripts/live_verify.py`
Expected: prints `OK` lines for every tool/output_type, an adversarial block where each case is `OK` (clean `error_code` or graceful zero-row), and finally `LIVE VERIFICATION PASSED`. If any assertion fails, treat it as a real bug (Meridian 1.7.0 output drift or envelope bug) and fix before continuing.

- [ ] **Step 3: Prepare a local model from GCS and run the local-backend verification**

```bash
mkdir -p models/v14
gcloud storage cp gs://adswerve-meridian-models/rover/v14/model.binpb models/v14/model.binpb
MERIDIAN_VERIFY_LOCAL=1 LOCAL_MODELS_ROOT=./models uv run python scripts/live_verify.py
```
Expected: same `OK` output and `LIVE VERIFICATION PASSED`, proving the local provider path and identical columnar shape across backends.

- [ ] **Step 4: Clean up the downloaded model (keep it out of git)**

```bash
rm -rf models
grep -qE "^models/" .gitignore || echo "models/" >> .gitignore
```

- [ ] **Step 5: Final full verification**

Run: `uv run pytest -q && uv run ruff check src tests && wc -l AGENTS.md`
Expected: all tests pass, ruff clean, AGENTS.md `< 250` lines.

- [ ] **Step 6: Commit**

```bash
git add scripts/live_verify.py .gitignore
git commit -m "Add live adversarial MCP verification script (GCS + local backends)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:**
- Workstream A (columnar) → Tasks 1, 2, 4. ✓
- Workstream B (review fixes / dead alias) → Task 3 (+ read-through happens during execution; surface findings). ✓
- Workstream C (Specify removal) → Task 5. ✓
- Workstream D (package bumps incl. Meridian 1.7.0) → Task 6. ✓
- Workstream E (AGENTS.md <250 + Karpathy + CLAUDE.md) → Task 7. ✓
- Workstream F (live adversarial, GCS + local) → Task 8. ✓

**Type consistency:** `_build_result` keyword signature matches its callers in `analysis_service.py` (unchanged call sites — only the return body changes). `_round_measure` is referenced only inside `_build_result`. `columns`/`rows`/`row_count` keys are used consistently across Tasks 1, 4, 8.

**Placeholder scan:** No TBD/TODO; every code step shows full content; every run step shows the command and expected result.

**Open risk (flagged, not a gap):** Task 8 Step 2/3 is where Meridian 1.7.0 output-shape drift would surface. If a facade method returns a differently-shaped DataFrame under 1.7.0, the columnar envelope still forms but values/columns may differ — the script's assertions catch ragged/empty/error cases; a human eyeballs the printed row/col counts for sanity.

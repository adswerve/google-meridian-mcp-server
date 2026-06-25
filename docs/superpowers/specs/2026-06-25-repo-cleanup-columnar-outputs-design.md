# Design: Repo cleanup, columnar tool outputs, package bumps & live verification

Date: 2026-06-25
Status: Approved (pending spec review)

## Goal

Do a thorough review-and-cleanup pass on the google-meridian-mcp-server repository:

1. Make MCP tool responses token-efficient (the current row-of-objects JSON is bloated).
2. Remove all Spec-Kit / Specify tooling from the working tree.
3. Bump dependencies, including google-meridian 1.5.3 -> 1.7.0.
4. Rewrite `AGENTS.md` so it accurately reflects the project, stays under 250 lines,
   and folds in the Karpathy behavioral guidelines; add a `CLAUDE.md` pointer.
5. Verify not just with unit tests/lint but by running the real server and driving
   every tool through a live, adversarial MCP client session.

## Baseline (verified before any change)

- 140 tests pass; `ruff check src tests` clean.
- Only 47 files are git-tracked. `.specify/`, `specs/`, `.github/`, `references/`,
  `.vscode/` are all gitignored and untracked (local-only).
- `references/` is a 33 MB local clone of the Meridian source (kept as reference).
- No local model exists: `.env` uses `PERSISTENCE_BACKEND=gcs` and
  `LOCAL_MODELS_ROOT` is commented out.

## Decisions

- Tabular tool output shape: **columnar** (`columns` + `rows[][]`).
- Drop the redundant `result_metadata` block everywhere (tabular + overview).
- Round measure floats to ~6 significant figures.
- The output-shape change is a **breaking contract change**; acceptable (no pinned
  external consumers yet). Contract tests are updated to the new shape.
- Package bumps include **google-meridian 1.7.0**.
- Keep `.vscode/` and `references/`; delete Specify/Spec-Kit material.

---

## Workstream A — Columnar tool outputs

### Current shape (to be replaced)

```json
{ "model_id": "...", "row_count": 2, "output_type": "roi",
  "data": [ {"channel":"TV","mean":1.2,"ci_lo":0.91,"ci_hi":1.53}, ... ],
  "result_metadata": {"format":"tabular","columns":[...],"dimensions":[...],"measures":[...]} }
```

Problems: keys repeat on every row; `result_metadata` re-lists the columns; worst
case is `get_response_curves` (channels x spend-multipliers x points).

### New shape (tabular tools)

```json
{ "model_id": "...", "output_type": "roi",
  "columns": ["channel","mean","ci_lo","ci_hi"],
  "rows": [ ["TV",1.2,0.91,1.53], ["Search",2.4,2.05,2.78] ],
  "row_count": 2 }
```

- Applies to: `get_training_data`, `get_channel_summary`, `get_contribution`,
  `get_adstock_decay`, `get_response_curves`.
- `get_training_data` keeps `datasets`; analysis tools keep `output_type`.
- `result_metadata` removed; `dimensions`/`measures` removed (YAGNI — agents can
  infer types from values).
- Measure floats (numeric, non-bool) rounded to 6 significant figures.

### Where it changes

- Single change point: `AnalysisService._build_result` pivots the internal list of
  row-dicts into `{columns, rows}`. The facade and `dataset_mapper` keep producing
  row-dicts internally — only the envelope boundary changes.
- Column order: preserve first-seen order across rows (reuse existing
  `_ordered_columns`). Missing keys in a given row serialize as `null` in the
  positional cell.
- Delete `_build_tabular_result_metadata`, `_build_overview_result_metadata`,
  `_is_measure_value`-based dimension/measure split (kept only what's needed for
  rounding).

### Unchanged tools

- `list_models`: small list of objects (id, display_name, format, last_modified).
- `get_model_overview`: nested metadata object. Remove its `result_metadata` block;
  keep `available_tool_options`.

### Tests

Update `tests/unit/test_analysis_service.py`, `tests/contract/*`,
`tests/integration/test_cached_analysis.py`, and any others asserting `data` /
`result_metadata` to assert on `columns`/`rows`/`row_count`. Test-first: change the
assertions to the target shape, watch them fail, then implement.

---

## Workstream B — Code review fixes

- Remove the dead `get_response_dynamics()` alias in `analysis_service.py`
  (AGENTS.md says keep that name off the surface). Confirm nothing references it.
- Full read-through of all 47 tracked source/test files for correctness bugs.
- Per Karpathy guideline #3 (surgical changes): surface non-trivial findings to the
  user rather than silently rewriting adjacent code. Only fix what traces to this
  task.

---

## Workstream C — Specify / Spec-Kit cleanup

Delete from disk:

- `.specify/`
- `specs/`
- `.github/agents/` (all `speckit.*`), `.github/prompts/` (all `speckit.*`),
  `.github/skills/find-docs/`, `.github/copilot-instructions.md`

Edits:

- `pyproject.toml`: remove `.specify` from `[tool.ruff].extend-exclude`.
- `.gitignore` / `.dockerignore`: drop the now-irrelevant `.specify/` and `specs/`
  lines.

Keep: `.vscode/`, `references/` (local-only, untracked).

---

## Workstream D — Package bumps

Update version constraints in both `pyproject.toml` and `fastmcp.json`:

- `google-meridian[schema]` 1.5.3 -> **1.7.0** (pinned in both files).
- `fastmcp` -> 3.4.x ceiling unchanged (`<4`); ensure installed 3.4.2 works.
- `google-cloud-storage` -> 3.12 (ceiling `<4` unchanged).
- `pydantic` -> 2.13 (ceiling `<3` unchanged).
- dev: `ruff` / `pytest` / `pytest-asyncio` minors.

Run `uv sync --extra dev`, then suite + ruff must stay green.

### Meridian 1.7.0 risk assessment

Symbols we import are all stable public surface and unaffected by the 1.6/1.7
internal moves:

- `meridian.analysis.analyzer.Analyzer`, `analyzer.summary_metrics`,
  `analyzer.adstock_decay`
- `meridian.analysis.visualizer.MediaSummary`
- `meridian.schema.serde.meridian_serde`
- `meridian.model.model` (Meridian class)

We do **not** import `DataTensors`/`DistributionTensors` (moved in 1.7.0) or
`NotFittedModelError` (moved in 1.6.0). Import-compat risk: low. Residual risk:
output-shape drift in `summary_metrics`/`adstock_decay`, validated by the live test
in Workstream F (synthetic model) rather than only by fakes.

---

## Workstream E — AGENTS.md + CLAUDE.md

- Rewrite `AGENTS.md` (< 250 lines), keeping the accurate parts (project focus,
  runtime shape, boundaries, key files, config, commands, tool surface, behavior
  contracts) and condensing the verbose Module Map / test-coverage prose.
- Update the output-shape section to describe the new columnar envelope.
- Append a compact "Working Guidelines" section distilled from the Karpathy
  CLAUDE.md (think-before-coding, simplicity-first, surgical changes,
  goal-driven/verified execution).
- New `CLAUDE.md`: a one-line pointer to `AGENTS.md`.

---

## Workstream F — Live adversarial MCP verification

The decisive verification step: run the real server and exercise it as a client.

### Synthetic model fixture

Because no real model is available, generate a tiny **fitted** Meridian model:

- Small dims (e.g. 2-3 geos, ~50 weeks, 2-3 paid channels, optional 1 control).
- Minimal sampling (1 chain, small `n_draws`/burnin) so it fits in a minute or two
  on CPU — enough to populate a posterior so every tool returns real numbers.
- Serialize to `.binpb` via `meridian_serde`, place under a temp
  `LOCAL_MODELS_ROOT`. Build script lives under `scripts/` (or the scratchpad) and
  is reusable; not shipped as a runtime dependency.

### Live client run

- Start the server (in-memory FastMCP client against the app, or stdio transport)
  with `PERSISTENCE_BACKEND=local` pointing at the fixture.
- Call every tool happy-path and assert the columnar shape + sane values:
  `list_models` -> `get_model_overview` -> `get_training_data` ->
  `get_channel_summary` (each output_type) -> `get_contribution` ->
  `get_adstock_decay` -> `get_response_curves`.

### Adversarial pass (try to break it)

- Unknown `model_id`; empty/whitespace `model_id`.
- Invalid `output_type` and invalid `dataset` enum values.
- Malformed/extra filter fields (must be rejected by `extra="forbid"`).
- Inverted date ranges; non-existent geos/channels (expect empty rows, not crash).
- Confirm errors come back as the standard `{error_code, message, details}` payload,
  not stack traces, and that the server stays up across all calls.

Document the live-test commands so they're repeatable.

---

## Verification gates

Per workstream and at the end:

- `uv run pytest` — all green (assertions updated for columnar).
- `uv run ruff check src tests` — clean.
- `AGENTS.md` line count < 250.
- Live adversarial MCP session passes happy-path + adversarial checks.

## Out of scope

- Refactoring unrelated to these workstreams.
- New analysis tools or new model metadata.
- Changing transport/auth/deployment topology.

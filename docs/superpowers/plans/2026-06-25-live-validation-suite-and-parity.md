# Live MCP Validation Suite, Metric-Validity Fixes & Showcase Parity — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Meridian MCP server provably correct across national/geo and revenue/KPI model variants by generating dummy models, fixing metric-validity behavior, adding parity tools, and shipping a reusable live validation suite.

**Architecture:** Five components executed in order — (1) a dummy-model generator producing 7 fitted fixtures, (2) metric-validity fixes (typed errors + dynamic capability reporting + effective `use_kpi`), (3) three new tools (`get_model_fit`, `get_reach_frequency`, `get_channel_data`), (4) a reusable live validation suite driving an in-process FastMCP client across an expectation matrix, and (5) a showcase parity report. The suite is the acceptance gate and must come last.

**Tech Stack:** Python 3.12+, `uv`, `google-meridian==1.7.0` (`meridian.data.test_utils`, `meridian.model`, `meridian.analysis.analyzer`/`visualizer`, `meridian.schema.serde.meridian_serde`), FastMCP 3.4 in-process `Client(mcp)`, pytest, ruff, pydantic v2, xarray/pandas/numpy.

## Global Constraints

- Python `>=3.12,<3.15`; `google-meridian==1.7.0`. All commands via `uv run`.
- Columnar envelope is the contract for every row-oriented tool: `{model_id, <optional selector>, columns, rows[][], row_count}`. No `data` key, no `result_metadata`. New tools MUST build results via `AnalysisService._build_result`.
- Measure floats round to 6 significant figures (handled by `_build_result`/`_round_measure` — do not re-round).
- Grouped analysis tools return posterior-only rows; no `distribution` column.
- Generated fixtures live under gitignored `models/_validation/`; no model binaries committed to git.
- Metric-validity = **Meridian truth**: `cpik`/`marginal_cpik` valid on every model; `roi`/`marginal_roi` valid only when the model has revenue (`input_data.revenue_per_kpi is not None`, which includes `kpi_type=REVENUE`).
- Errors flow `facade/service → MeridianMcpError subclass → transport payload {error_code, message, details}`. New failure modes use typed domain errors, never bare exceptions.
- `.superpowers/` is gitignored scratch; never `git add -A`. Stage files explicitly. `uv.lock` is gitignored — never `git add uv.lock`.

---

## File Structure

**Create:**
- `scripts/generate_validation_models.py` — builds the 7 dummy fixtures (synthetic data + tiny real fit + serialize).
- `scripts/validation/__init__.py` — package marker.
- `scripts/validation/matrix.py` — declarative variant + expectation matrix (pure data/functions).
- `scripts/validation/runner.py` — in-process client driver + columnar/error assertions.
- `scripts/validation/live_validate.py` — entrypoint: build-if-missing fixtures, run matrix, exit non-zero on mismatch.
- `docs/meridian-mcp-showcase-parity.md` — parity gap report.
- `tests/unit/test_validation_matrix.py` — tests for `scripts/validation/matrix.py`.
- `tests/unit/test_channel_data.py` — tests for the `get_channel_data` dataset_mapper builder.

**Modify:**
- `src/google_meridian_mcp_server/domain/errors.py` — add `MetricNotSupportedError`.
- `src/google_meridian_mcp_server/meridian/interrogator.py` — add `has_revenue_per_kpi`, `has_rf_channels`, `resolve_use_kpi`.
- `src/google_meridian_mcp_server/meridian/analyzer_facade.py` — effective `use_kpi`; new `get_model_fit`, `get_reach_frequency` methods.
- `src/google_meridian_mcp_server/meridian/dataset_mapper.py` — `filter_records`, `extract_channel_data`.
- `src/google_meridian_mcp_server/services/analysis_service.py` — revenue gate, dynamic overview options, training-data filtering, new tool methods.
- `src/google_meridian_mcp_server/domain/filters.py` — remove dead `aggregate_geos`.
- `src/google_meridian_mcp_server/transport/tools.py` — register 3 new tools; sharpen `get_training_data` description.
- `.gitignore` — add `models/_validation/` (or confirm `models/` already covers it).
- `AGENTS.md`, `README.md` — document new tools, error, columnar contract.
- `tests/unit/test_analysis_service.py`, `tests/unit/test_analyzer_facade.py`, `tests/unit/test_interrogator.py`, `tests/unit/test_transport_tools.py` — extend for new behavior.

**Remove:**
- `scripts/live_verify.py` — superseded by `scripts/validation/`.

---

## Task 1: Dummy-model generator

**Files:**
- Create: `scripts/generate_validation_models.py`
- Modify: `.gitignore`
- Test: `tests/unit/test_validation_matrix.py` is created in Task 11; this task adds a smoke test inline (see Step 6).

**Interfaces:**
- Produces: module-level `VARIANTS: list[VariantSpec]` and `build_variant(spec, out_root, force=False) -> Path`, `build_all(out_root, force=False) -> list[Path]`. `VariantSpec` is a dataclass with fields `key: str`, `factory: str` (one of `"revenue"`, `"kpi_rpk"`, `"kpi_only"`), `n_geos: int`, `with_rf: bool`. Fixtures land at `<out_root>/<key>/model.binpb`. Used by `scripts/validation/live_validate.py` (Task 12).

- [ ] **Step 1: Confirm `.gitignore` ignores the fixtures dir**

Check `.gitignore` for a `models/` entry. If `models/` is present, fixtures under `models/_validation/` are already ignored — do nothing. Otherwise add:

```gitignore
models/_validation/
```

Run: `git check-ignore models/_validation/x` → expect it to print the path (ignored).

- [ ] **Step 2: Write the generator module**

Create `scripts/generate_validation_models.py`:

```python
"""Generate dummy Meridian models for live validation across all variants.

Builds 7 fixtures: the 2x3 (national|geo) x (revenue|kpi_rpk|kpi_only) matrix
(all with reach & frequency channels) plus one media-only geo-revenue model so
the no-RF graceful-error path is exercised. Each model is built from synthetic
data, fitted with a tiny real posterior, and serialized to .binpb (one variant
also to .pkl to exercise the loader's pickle path).

Usage:
  uv run python scripts/generate_validation_models.py            # build if missing
  uv run python scripts/generate_validation_models.py --force    # rebuild all
  uv run python scripts/generate_validation_models.py --out DIR  # custom out dir
"""

from __future__ import annotations

import argparse
import dataclasses
from pathlib import Path

DEFAULT_OUT_ROOT = Path("models/_validation")

# Small but valid fit. Keep n_media_times >= n_times (random_dataset back-dates).
N_TIMES = 52
N_MEDIA_TIMES = 55
N_MEDIA_CHANNELS = 3
N_RF_CHANNELS = 2
N_ORGANIC_MEDIA = 1
N_ORGANIC_RF = 1
N_NON_MEDIA = 1
N_CONTROLS = 2
PRIOR_DRAWS = 10
POSTERIOR_KW = {"n_chains": 1, "n_adapt": 10, "n_burnin": 10, "n_keep": 10}


@dataclasses.dataclass(frozen=True)
class VariantSpec:
    key: str
    factory: str  # "revenue" | "kpi_rpk" | "kpi_only"
    n_geos: int
    with_rf: bool


VARIANTS: list[VariantSpec] = [
    VariantSpec("national-revenue", "revenue", 1, True),
    VariantSpec("geo-revenue", "revenue", 5, True),
    VariantSpec("national-kpi-rpk", "kpi_rpk", 1, True),
    VariantSpec("geo-kpi-rpk", "kpi_rpk", 5, True),
    VariantSpec("national-kpi-only", "kpi_only", 1, True),
    VariantSpec("geo-kpi-only", "kpi_only", 5, True),
    VariantSpec("geo-revenue-media-only", "revenue", 5, False),
]

_FACTORY_NAMES = {
    "revenue": "sample_input_data_revenue",
    "kpi_rpk": "sample_input_data_non_revenue_revenue_per_kpi",
    "kpi_only": "sample_input_data_non_revenue_no_revenue_per_kpi",
}


def _build_input_data(spec: VariantSpec):
    from meridian.data import test_utils

    factory = getattr(test_utils, _FACTORY_NAMES[spec.factory])
    kwargs = dict(
        n_geos=spec.n_geos,
        n_times=N_TIMES,
        n_media_times=N_MEDIA_TIMES,
        n_controls=N_CONTROLS,
        n_media_channels=N_MEDIA_CHANNELS,
        n_organic_media_channels=N_ORGANIC_MEDIA,
        n_non_media_channels=N_NON_MEDIA,
        seed=0,
    )
    if spec.with_rf:
        kwargs["n_rf_channels"] = N_RF_CHANNELS
        kwargs["n_organic_rf_channels"] = N_ORGANIC_RF
    return factory(**kwargs)


def _fit(input_data):
    from meridian.model import model, spec

    mmm = model.Meridian(input_data=input_data, model_spec=spec.ModelSpec())
    mmm.sample_prior(n_draws=PRIOR_DRAWS, seed=0)
    mmm.sample_posterior(seed=1, **POSTERIOR_KW)
    return mmm


def build_variant(
    variant: VariantSpec, out_root: Path = DEFAULT_OUT_ROOT, force: bool = False
) -> Path:
    from meridian.schema.serde import meridian_serde

    target_dir = out_root / variant.key
    target = target_dir / "model.binpb"
    if target.exists() and not force:
        print(f"  skip {variant.key} (exists)")
        return target
    target_dir.mkdir(parents=True, exist_ok=True)
    mmm = _fit(_build_input_data(variant))
    meridian_serde.save_meridian(mmm, str(target))
    print(f"  built {variant.key} -> {target}")
    # Exercise the loader's pickle branch with one extra .pkl fixture.
    if variant.key == "national-revenue":
        from meridian.model import model as model_mod

        pkl_dir = out_root / "national-revenue-pkl"
        pkl_dir.mkdir(parents=True, exist_ok=True)
        model_mod.save_mmm(mmm, str(pkl_dir / "model.pkl"))
        print(f"  built national-revenue-pkl -> {pkl_dir / 'model.pkl'}")
    return target


def build_all(out_root: Path = DEFAULT_OUT_ROOT, force: bool = False) -> list[Path]:
    print(f"Generating validation fixtures in {out_root} (force={force})")
    return [build_variant(variant, out_root, force) for variant in VARIANTS]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="Rebuild existing fixtures")
    parser.add_argument("--out", default=str(DEFAULT_OUT_ROOT), help="Output directory")
    args = parser.parse_args()
    build_all(Path(args.out), force=args.force)


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Verify the `test_utils` factory kwargs are accepted**

Run a quick import-and-introspect to confirm the installed Meridian accepts these kwargs (guards against a signature drift):

Run: `uv run python -c "from meridian.data import test_utils as t; import inspect; print(inspect.signature(t.sample_input_data_revenue))"`
Expected: prints a signature containing `n_geos`, `n_times`, `n_media_times`, `n_media_channels`, `n_rf_channels`, `n_organic_media_channels`, `n_organic_rf_channels`, `n_non_media_channels`, `n_controls`, `seed`.

- [ ] **Step 4: Build one fixture end-to-end (smoke)**

Run: `uv run python -c "from scripts.generate_validation_models import build_variant, VARIANTS, DEFAULT_OUT_ROOT; build_variant(VARIANTS[4])"`
(`VARIANTS[4]` is `national-kpi-only` — smallest geo count.)
Expected: prints `built national-kpi-only -> models/_validation/national-kpi-only/model.binpb` with no exception. This proves data→fit→serialize works. (Fit takes tens of seconds; that's expected.)

- [ ] **Step 5: Verify the saved fixture loads back through the MCP loader**

Run:
```bash
uv run python -c "
from google_meridian_mcp_server.meridian.loader import load_meridian_model
from google_meridian_mcp_server.meridian.interrogator import MeridianInterrogator
m = load_meridian_model('models/_validation/national-kpi-only/model.binpb')
it = MeridianInterrogator(m)
print('national:', it.is_national(), 'rf:', it.get_data_inputs()['rf_media'])
"
```
Expected: `national: True rf: [...]` (one or more RF channels), no exception. (If `is_national` or `get_data_inputs` is not yet aware of the fixture shape, that's a real bug to fix here.)

- [ ] **Step 6: Add a fast structural test (no fitting)**

Create `tests/unit/test_validation_matrix.py` with the variant-table test (the rest of this file is filled in Task 11):

```python
"""Tests for the validation variant/expectation matrix."""

from __future__ import annotations

from scripts.generate_validation_models import VARIANTS


def test_variants_cover_full_matrix():
    keys = {v.key for v in VARIANTS}
    assert keys == {
        "national-revenue",
        "geo-revenue",
        "national-kpi-rpk",
        "geo-kpi-rpk",
        "national-kpi-only",
        "geo-kpi-only",
        "geo-revenue-media-only",
    }
    # Exactly one no-RF fixture, for the reach_frequency error path.
    assert [v.key for v in VARIANTS if not v.with_rf] == ["geo-revenue-media-only"]
    # National and geo both represented.
    assert {v.n_geos == 1 for v in VARIANTS} == {True, False}
```

Run: `uv run pytest tests/unit/test_validation_matrix.py -v`
Expected: PASS.

- [ ] **Step 7: Run ruff and commit**

Run: `uv run ruff check scripts tests && uv run ruff format scripts tests`
```bash
git add scripts/generate_validation_models.py tests/unit/test_validation_matrix.py .gitignore
git commit -m "feat: add dummy-model generator for validation fixtures"
```

---

## Task 2: MetricNotSupportedError + interrogator capability helpers

**Files:**
- Modify: `src/google_meridian_mcp_server/domain/errors.py`
- Modify: `src/google_meridian_mcp_server/meridian/interrogator.py`
- Test: `tests/unit/test_interrogator.py`

**Interfaces:**
- Produces: `MetricNotSupportedError(model_id: str, output_type: str, reason: str)` with `error_code="metric_not_supported"`. `MeridianInterrogator.has_revenue_per_kpi() -> bool`, `.has_rf_channels() -> bool`, `.resolve_use_kpi(filters: AnalysisFilters) -> bool`. Consumed by Tasks 3, 4, 5, 8, 9, 10.

- [ ] **Step 1: Write failing tests for the interrogator helpers**

Add to `tests/unit/test_interrogator.py`:

```python
from types import SimpleNamespace

from google_meridian_mcp_server.domain.filters import AnalysisFilters
from google_meridian_mcp_server.meridian.interrogator import MeridianInterrogator


def _interrogator(*, revenue, rf_channels):
    input_data = SimpleNamespace(
        revenue_per_kpi=object() if revenue else None,
        rf_channel=(
            __import__("numpy").array(rf_channels) if rf_channels else None
        ),
        media_channel=None,
        non_media_channel=None,
        organic_media_channel=None,
        organic_rf_channel=None,
        control_variable=None,
    )
    return MeridianInterrogator(SimpleNamespace(input_data=input_data))


def test_has_revenue_per_kpi_reflects_input_data():
    assert _interrogator(revenue=True, rf_channels=[]).has_revenue_per_kpi() is True
    assert _interrogator(revenue=False, rf_channels=[]).has_revenue_per_kpi() is False


def test_has_rf_channels_reflects_rf_coord():
    assert _interrogator(revenue=True, rf_channels=["yt"]).has_rf_channels() is True
    assert _interrogator(revenue=True, rf_channels=[]).has_rf_channels() is False


def test_resolve_use_kpi_defaults_from_revenue_capability():
    revenue = _interrogator(revenue=True, rf_channels=[])
    kpi_only = _interrogator(revenue=False, rf_channels=[])
    # No explicit use_kpi -> revenue model queries revenue (False), kpi-only queries kpi (True).
    assert revenue.resolve_use_kpi(AnalysisFilters()) is False
    assert kpi_only.resolve_use_kpi(AnalysisFilters()) is True
    # Explicit use_kpi is honored.
    assert revenue.resolve_use_kpi(AnalysisFilters(use_kpi=True)) is True
    assert kpi_only.resolve_use_kpi(AnalysisFilters(use_kpi=False)) is False
```

Run: `uv run pytest tests/unit/test_interrogator.py -k "has_revenue or has_rf or resolve_use_kpi" -v`
Expected: FAIL (`AttributeError: ... has no attribute 'has_revenue_per_kpi'`).

- [ ] **Step 2: Add the error type**

Append to `src/google_meridian_mcp_server/domain/errors.py`:

```python
class MetricNotSupportedError(MeridianMcpError):
    def __init__(self, model_id: str, output_type: str, reason: str):
        super().__init__(
            error_code="metric_not_supported",
            message=(
                f"Metric '{output_type}' is not supported for model "
                f"'{model_id}': {reason}"
            ),
            details={
                "model_id": model_id,
                "output_type": output_type,
                "reason": reason,
            },
        )
```

- [ ] **Step 3: Add the interrogator helpers**

In `src/google_meridian_mcp_server/meridian/interrogator.py`, add the import near the top:

```python
from google_meridian_mcp_server.domain.filters import AnalysisFilters
```

Add these methods to `MeridianInterrogator` (e.g. just after `is_national`):

```python
    def has_revenue_per_kpi(self) -> bool:
        return getattr(self._mmm.input_data, "revenue_per_kpi", None) is not None

    def has_rf_channels(self) -> bool:
        return len(self.get_data_inputs()["rf_media"]) > 0

    def resolve_use_kpi(self, filters: AnalysisFilters) -> bool:
        if filters.use_kpi is not None:
            return filters.use_kpi
        return not self.has_revenue_per_kpi()
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/unit/test_interrogator.py -v`
Expected: PASS (new and existing).

- [ ] **Step 5: Ruff and commit**

Run: `uv run ruff check src tests && uv run ruff format src tests`
```bash
git add src/google_meridian_mcp_server/domain/errors.py src/google_meridian_mcp_server/meridian/interrogator.py tests/unit/test_interrogator.py
git commit -m "feat: add MetricNotSupportedError and interrogator capability helpers"
```

---

## Task 3: Revenue-gate ROI / marginal_roi in the service

**Files:**
- Modify: `src/google_meridian_mcp_server/services/analysis_service.py`
- Test: `tests/unit/test_analysis_service.py`

**Interfaces:**
- Consumes: `MetricNotSupportedError`, `catalog.get_interrogator(model_id).has_revenue_per_kpi()` (Task 2).
- Produces: `get_channel_summary` raises `MetricNotSupportedError` for `roi`/`marginal_roi` on no-revenue models, before any computation.

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_analysis_service.py` (mirror the existing fake-catalog style in that file; if a helper for a fake catalog already exists, reuse it):

```python
import pytest

from google_meridian_mcp_server.domain.errors import MetricNotSupportedError
from google_meridian_mcp_server.services.analysis_service import AnalysisService


class _FakeInterrogator:
    def __init__(self, has_revenue):
        self._has_revenue = has_revenue

    def has_revenue_per_kpi(self):
        return self._has_revenue


class _FakeCatalog:
    def __init__(self, has_revenue):
        self._interrogator = _FakeInterrogator(has_revenue)

    def get_interrogator(self, model_id):
        return self._interrogator


@pytest.mark.parametrize("output_type", ["roi", "marginal_roi"])
def test_channel_summary_rejects_roi_on_no_revenue_model(output_type):
    service = AnalysisService(catalog=_FakeCatalog(has_revenue=False))
    with pytest.raises(MetricNotSupportedError) as exc:
        service.get_channel_summary("kpi-only", output_type, None)
    assert exc.value.error_code == "metric_not_supported"
    assert exc.value.details["output_type"] == output_type
```

Run: `uv run pytest tests/unit/test_analysis_service.py -k rejects_roi -v`
Expected: FAIL (no error raised — the fake catalog has no `get_facade`, so it currently errors differently or computes).

- [ ] **Step 2: Add the gate**

In `src/google_meridian_mcp_server/services/analysis_service.py`, add the import:

```python
from google_meridian_mcp_server.domain.errors import (
    DatasetNotAvailableError,
    InvalidOutputTypeError,
    MetricNotSupportedError,
    MissingModelDataError,
)
```

Add a module constant near the other type-order constants:

```python
REVENUE_ONLY_CHANNEL_SUMMARY_TYPES = frozenset({"roi", "marginal_roi"})
```

Modify `get_channel_summary` to gate before dispatch:

```python
    def get_channel_summary(
        self,
        model_id: str,
        output_type: str,
        filters: AnalysisFilters | dict | None,
    ) -> dict[str, Any]:
        if output_type in REVENUE_ONLY_CHANNEL_SUMMARY_TYPES:
            interrogator = self._catalog.get_interrogator(model_id)
            if not interrogator.has_revenue_per_kpi():
                raise MetricNotSupportedError(
                    model_id,
                    output_type,
                    "model has no revenue_per_kpi; ROI metrics require revenue",
                )
        return self._run_facade_query(
            tool_name="get_channel_summary",
            model_id=model_id,
            output_type=output_type,
            filters=normalize_filters(filters),
            valid_types=CHANNEL_SUMMARY_TYPES,
            dispatch={
                "baseline_summary_metrics": "get_baseline_summary_metrics",
                "paid_summary_metrics": "get_paid_summary_metrics",
                "roi": "get_roi",
                "cpik": "get_cpik",
                "marginal_roi": "get_marginal_roi",
                "marginal_cpik": "get_marginal_cpik",
            },
        )
```

- [ ] **Step 3: Run the test**

Run: `uv run pytest tests/unit/test_analysis_service.py -k rejects_roi -v`
Expected: PASS.

- [ ] **Step 4: Run the full service test file + ruff, commit**

Run: `uv run pytest tests/unit/test_analysis_service.py -v && uv run ruff check src tests`
```bash
git add src/google_meridian_mcp_server/services/analysis_service.py tests/unit/test_analysis_service.py
git commit -m "feat: reject roi/marginal_roi on no-revenue models with typed error"
```

---

## Task 4: Effective `use_kpi` in the facade

**Files:**
- Modify: `src/google_meridian_mcp_server/meridian/analyzer_facade.py`
- Test: `tests/unit/test_analyzer_facade.py`

**Interfaces:**
- Consumes: `self.resolve_use_kpi(filters)` (Task 2; `AnalyzerFacade` extends `MeridianInterrogator`).
- Produces: every facade analysis call resolves `use_kpi` from the model's revenue capability when the caller leaves it unset.

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_analyzer_facade.py` a test that a no-revenue model defaults to `use_kpi=True` in the media-summary path. Use a fake that records the `use_kpi` passed to `summary_metrics`:

```python
def test_media_summary_defaults_to_kpi_when_no_revenue(monkeypatch):
    from google_meridian_mcp_server.domain.filters import AnalysisFilters
    from google_meridian_mcp_server.meridian.analyzer_facade import AnalyzerFacade

    input_data = SimpleNamespace(
        revenue_per_kpi=None,
        rf_channel=None,
        media_channel=None,
        non_media_channel=None,
        organic_media_channel=None,
        organic_rf_channel=None,
        control_variable=None,
    )
    facade = AnalyzerFacade(SimpleNamespace(input_data=input_data))

    captured = {}

    class _FakeMediaSummary:
        def __init__(self, *args, **kwargs):
            captured["use_kpi"] = kwargs.get("use_kpi")

    fake_vis = ModuleType("meridian.analysis.visualizer")
    fake_vis.MediaSummary = _FakeMediaSummary
    monkeypatch.setitem(sys.modules, "meridian.analysis.visualizer", fake_vis)
    # Patch the parent module attribute lookup used by the facade.
    monkeypatch.setitem(
        sys.modules, "meridian.analysis", ModuleType("meridian.analysis")
    )
    sys.modules["meridian.analysis"].visualizer = fake_vis

    facade._get_media_summary(AnalysisFilters())
    assert captured["use_kpi"] is True
```

Run: `uv run pytest tests/unit/test_analyzer_facade.py -k defaults_to_kpi -v`
Expected: FAIL (`captured["use_kpi"]` is `False` because the facade currently uses `bool(filters.use_kpi)`).

> Note for the implementer: if the monkeypatch import shim above proves brittle against how `_get_media_summary` imports `visualizer`, assert the simpler invariant instead — call `facade.resolve_use_kpi(AnalysisFilters())` directly and assert `True`, and assert the facade source no longer contains `bool(filters.use_kpi)`. The behavior under test is "no-revenue ⇒ use_kpi defaults True," which `resolve_use_kpi` already guarantees once wired.

- [ ] **Step 2: Replace `bool(filters.use_kpi)` with `self.resolve_use_kpi(filters)`**

In `src/google_meridian_mcp_server/meridian/analyzer_facade.py`, update these call sites:

`_get_media_summary` (line ~85):
```python
        use_kpi = self.resolve_use_kpi(filters)
```

`get_baseline_summary_metrics` (line ~146):
```python
            use_kpi=self.resolve_use_kpi(filters),
```

`get_response_curves` (line ~383):
```python
            use_kpi=self.resolve_use_kpi(filters),
```

`get_response_curve_summary` (line ~391):
```python
            use_kpi=self.resolve_use_kpi(filters),
```

Leave `apply_saturation` (a helper not wired to any tool) unchanged.

- [ ] **Step 3: Run the test**

Run: `uv run pytest tests/unit/test_analyzer_facade.py -k defaults_to_kpi -v`
Expected: PASS.

- [ ] **Step 4: Full facade tests + ruff, commit**

Run: `uv run pytest tests/unit/test_analyzer_facade.py -v && uv run ruff check src tests`
```bash
git add src/google_meridian_mcp_server/meridian/analyzer_facade.py tests/unit/test_analyzer_facade.py
git commit -m "feat: resolve effective use_kpi from model revenue capability"
```

---

## Task 5: Dynamic `available_tool_options` (prune ROI for no-revenue models)

**Files:**
- Modify: `src/google_meridian_mcp_server/services/analysis_service.py`
- Test: `tests/unit/test_analysis_service.py`

**Interfaces:**
- Consumes: `overview["has_revenue_per_kpi"]`, `overview["rf_channels"]` (already in the interrogator overview).
- Produces: `get_model_overview` returns `available_tool_options.get_channel_summary.output_type` without `roi`/`marginal_roi` for no-revenue models. (New-tool keys are added in Tasks 8–10.)

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_analysis_service.py`. Use a fake catalog whose interrogator returns a canned overview:

```python
class _OverviewCatalog:
    def __init__(self, overview):
        self._overview = overview

    class _Interrogator:
        def __init__(self, overview):
            self._overview = overview

        def get_model_overview(self):
            return dict(self._overview)

    def get_interrogator(self, model_id):
        return self._Interrogator(self._overview)


def _base_overview(has_revenue, rf_channels):
    return {
        "available_training_datasets": ["kpi", "media", "media_spend"],
        "has_revenue_per_kpi": has_revenue,
        "rf_channels": rf_channels,
    }


def test_overview_prunes_roi_for_no_revenue_model():
    catalog = _OverviewCatalog(_base_overview(has_revenue=False, rf_channels=["yt"]))
    service = AnalysisService(catalog=catalog)
    overview = service.get_model_overview("kpi-only")
    types = overview["available_tool_options"]["get_channel_summary"]["output_type"]
    assert "roi" not in types and "marginal_roi" not in types
    assert "cpik" in types and "marginal_cpik" in types


def test_overview_keeps_roi_for_revenue_model():
    catalog = _OverviewCatalog(_base_overview(has_revenue=True, rf_channels=[]))
    service = AnalysisService(catalog=catalog)
    overview = service.get_model_overview("rev")
    types = overview["available_tool_options"]["get_channel_summary"]["output_type"]
    assert "roi" in types and "marginal_roi" in types
```

Run: `uv run pytest tests/unit/test_analysis_service.py -k overview_prunes -v`
Expected: FAIL (current options are static — `roi` still present).

- [ ] **Step 2: Make the options dynamic**

In `src/google_meridian_mcp_server/services/analysis_service.py`, replace the `available_tool_options` construction inside `get_model_overview._compute` with:

```python
            has_revenue = overview.get("has_revenue_per_kpi", False)
            channel_summary_types = [
                output_type
                for output_type in CHANNEL_SUMMARY_TYPE_ORDER
                if has_revenue or output_type not in REVENUE_ONLY_CHANNEL_SUMMARY_TYPES
            ]
            overview["available_tool_options"] = {
                "get_training_data": {
                    "dataset": overview["available_training_datasets"],
                },
                "get_channel_summary": {
                    "output_type": channel_summary_types,
                },
                "get_contribution": {
                    "output_type": list(CONTRIBUTION_TYPE_ORDER),
                },
                "get_adstock_decay": {
                    "output_type": list(RESPONSE_DYNAMICS_TYPE_ORDER),
                },
                "get_response_curves": {
                    "output_type": list(RESPONSE_CURVE_TYPE_ORDER),
                },
            }
```

- [ ] **Step 3: Run the tests**

Run: `uv run pytest tests/unit/test_analysis_service.py -k overview -v`
Expected: PASS.

- [ ] **Step 4: Ruff, commit**

Run: `uv run ruff check src tests`
```bash
git add src/google_meridian_mcp_server/services/analysis_service.py tests/unit/test_analysis_service.py
git commit -m "feat: prune roi/marginal_roi from overview options for no-revenue models"
```

---

## Task 6: `get_training_data` honors its filters

**Files:**
- Modify: `src/google_meridian_mcp_server/meridian/dataset_mapper.py`
- Modify: `src/google_meridian_mcp_server/services/analysis_service.py`
- Test: `tests/unit/test_dataset_mapper.py` (create if absent) and `tests/unit/test_analysis_service.py`

**Interfaces:**
- Produces: `dataset_mapper.filter_records(records, *, start_date=None, end_date=None, geos=(), channels=()) -> list[dict]`. Consumed by the service `get_training_data`, and reused by Tasks 8 and 10.

- [ ] **Step 1: Write failing tests for `filter_records`**

Create `tests/unit/test_dataset_mapper.py` (or add to it):

```python
from datetime import date

from google_meridian_mcp_server.meridian.dataset_mapper import filter_records


def _rows():
    return [
        {"geo": "us", "time": "2023-01-01T00:00:00", "media_channel": "tv", "media_spend": 5.0},
        {"geo": "us", "time": "2023-02-01T00:00:00", "media_channel": "search", "media_spend": 3.0},
        {"geo": "ca", "time": "2023-02-01T00:00:00", "media_channel": "tv", "media_spend": 4.0},
    ]


def test_filter_records_by_geo():
    out = filter_records(_rows(), geos=["us"])
    assert {r["geo"] for r in out} == {"us"}


def test_filter_records_by_channel():
    out = filter_records(_rows(), channels=["tv"])
    assert {r["media_channel"] for r in out} == {"tv"}


def test_filter_records_by_date_range():
    out = filter_records(_rows(), start_date=date(2023, 2, 1), end_date=date(2023, 2, 28))
    assert all(r["time"].startswith("2023-02") for r in out)


def test_filter_records_ignores_dimension_when_absent():
    rows = [{"kpi": 10.0}, {"kpi": 12.0}]  # no geo/time/channel columns
    assert filter_records(rows, geos=["us"], channels=["tv"]) == rows
```

Run: `uv run pytest tests/unit/test_dataset_mapper.py -v`
Expected: FAIL (`filter_records` undefined).

- [ ] **Step 2: Implement `filter_records`**

Add to `src/google_meridian_mcp_server/meridian/dataset_mapper.py`:

```python
from datetime import date

_TIME_COLUMNS = ("time", "media_time")
_CHANNEL_SUFFIX = "_channel"


def _row_time(row: dict) -> date | None:
    for column in _TIME_COLUMNS:
        value = row.get(column)
        if value is not None:
            return pd.Timestamp(value).date()
    return None


def _row_channels(row: dict) -> list[str]:
    names: list[str] = []
    for key, value in row.items():
        if value is None:
            continue
        if key == "channel" or key.endswith(_CHANNEL_SUFFIX):
            names.append(str(value))
    return names


def filter_records(
    records: list[dict],
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    geos: Sequence[str] = (),
    channels: Sequence[str] = (),
) -> list[dict]:
    """Filter row dicts by date range, geo, and channel where those dims exist.

    A row is kept unless it carries the relevant dimension and falls outside the
    requested selection. Rows lacking a dimension are unaffected by that filter.
    """
    geo_set = {str(value) for value in geos}
    channel_set = {str(value) for value in channels}
    out: list[dict] = []
    for row in records:
        if geo_set and "geo" in row and str(row["geo"]) not in geo_set:
            continue
        if channel_set:
            row_channels = _row_channels(row)
            if row_channels and not (set(row_channels) & channel_set):
                continue
        if start_date or end_date:
            row_date = _row_time(row)
            if row_date is not None:
                if start_date and row_date < start_date:
                    continue
                if end_date and row_date > end_date:
                    continue
        out.append(row)
    return out
```

- [ ] **Step 3: Apply the filter in the service**

In `src/google_meridian_mcp_server/services/analysis_service.py`, update the import to include `filter_records`:

```python
from google_meridian_mcp_server.meridian.dataset_mapper import (
    TRAINING_DATASETS,
    extract_training_datasets,
    filter_records,
)
```

In `get_training_data._compute`, filter the rows before building the result:

```python
        def _compute() -> dict[str, Any]:
            try:
                rows = extract_training_datasets(
                    self._catalog.resolve(model_id), datasets
                )
            except Exception as exc:
                raise MissingModelDataError(model_id, str(exc)) from exc
            rows = filter_records(
                rows,
                start_date=normalized_filters.start_date,
                end_date=normalized_filters.end_date,
                geos=normalized_filters.geos,
                channels=normalized_filters.channels,
            )
            return self._build_result(
                model_id=model_id,
                dataset=datasets[0] if len(datasets) == 1 else None,
                datasets=datasets,
                rows=rows,
            )
```

- [ ] **Step 4: Add a service-level test that filtering is applied**

Add to `tests/unit/test_analysis_service.py` a test using a fake catalog whose `resolve` returns a stub and monkeypatching `extract_training_datasets`, OR (simpler) assert via the existing training-data test fixture if one exists. Minimal version:

```python
def test_training_data_applies_geo_filter(monkeypatch):
    import google_meridian_mcp_server.services.analysis_service as svc

    rows = [
        {"geo": "us", "time": "2023-01-01T00:00:00", "kpi": 1.0},
        {"geo": "ca", "time": "2023-01-01T00:00:00", "kpi": 2.0},
    ]
    monkeypatch.setattr(svc, "extract_training_datasets", lambda mmm, datasets: rows)

    class _Catalog:
        def resolve(self, model_id):
            return object()

    service = svc.AnalysisService(catalog=_Catalog())
    result = service.get_training_data("m", ["kpi"], {"geos": ["us"]})
    assert result["row_count"] == 1
    assert result["rows"][0][result["columns"].index("geo")] == "us"
```

Run: `uv run pytest tests/unit/test_dataset_mapper.py tests/unit/test_analysis_service.py -k "filter_records or training_data_applies" -v`
Expected: PASS.

- [ ] **Step 5: Ruff, commit**

Run: `uv run ruff check src tests`
```bash
git add src/google_meridian_mcp_server/meridian/dataset_mapper.py src/google_meridian_mcp_server/services/analysis_service.py tests/unit/test_dataset_mapper.py tests/unit/test_analysis_service.py
git commit -m "feat: apply date/geo/channel filters in get_training_data"
```

---

## Task 7: Remove the dead `aggregate_geos` filter field

**Files:**
- Modify: `src/google_meridian_mcp_server/domain/filters.py`
- Test: `tests/unit/test_transport_tools.py` / any test referencing `aggregate_geos`

**Interfaces:**
- Produces: `AnalysisFilters` no longer has `aggregate_geos`. (No facade method read it.)

- [ ] **Step 1: Find all references**

Run: `rg -n "aggregate_geos" src tests scripts docs`
Expected: references only in `domain/filters.py` (the field) and possibly tests. (If any `src` analysis/facade code reads it, STOP — that contradicts the spec's "dead field" claim; surface it.)

- [ ] **Step 2: Write/adjust the test**

Add to `tests/unit/test_transport_tools.py` (or `test_analysis_service.py`) a test that `aggregate_geos` is rejected as an unknown field (since `AnalysisFilters` uses `extra="forbid"`):

```python
import pytest
from pydantic import ValidationError

from google_meridian_mcp_server.domain.filters import AnalysisFilters


def test_aggregate_geos_is_no_longer_accepted():
    with pytest.raises(ValidationError):
        AnalysisFilters(aggregate_geos=False)
```

Run: `uv run pytest tests/unit/test_transport_tools.py -k aggregate_geos -v`
Expected: FAIL (currently accepted).

- [ ] **Step 3: Remove the field**

In `src/google_meridian_mcp_server/domain/filters.py`, delete the `aggregate_geos` field (lines ~64-67). Remove any other test references found in Step 1 that construct `AnalysisFilters(aggregate_geos=...)`.

- [ ] **Step 4: Run the test + full suite slice**

Run: `uv run pytest tests/unit/test_transport_tools.py tests/unit/test_analysis_service.py -v`
Expected: PASS.

- [ ] **Step 5: Ruff, commit**

Run: `uv run ruff check src tests`
```bash
git add src/google_meridian_mcp_server/domain/filters.py tests/unit/test_transport_tools.py
git commit -m "refactor: remove dead aggregate_geos filter field"
```

---

## Task 8: New tool `get_model_fit`

**Files:**
- Modify: `src/google_meridian_mcp_server/meridian/analyzer_facade.py`
- Modify: `src/google_meridian_mcp_server/services/analysis_service.py`
- Modify: `src/google_meridian_mcp_server/transport/tools.py`
- Test: `tests/unit/test_analysis_service.py`

**Interfaces:**
- Consumes: `resolve_use_kpi` (Task 2), `filter_records` (Task 6), `_build_result` (existing).
- Produces: `AnalyzerFacade.get_model_fit(filters) -> list[dict]`; `AnalysisService.get_model_fit(model_id, filters) -> dict`; MCP tool `get_model_fit`. Columns: `time, expected, expected_ci_lo, expected_ci_hi, actual, baseline, baseline_ci_lo, baseline_ci_hi, residual`.

- [ ] **Step 1: Write the failing service test (fake facade)**

Add to `tests/unit/test_analysis_service.py`:

```python
class _ModelFitCatalog:
    def __init__(self, rows):
        self._rows = rows

    class _Facade:
        def __init__(self, rows):
            self._rows = rows

        def get_model_fit(self, filters):
            return self._rows

    def get_facade(self, model_id):
        return self._Facade(self._rows)


def test_get_model_fit_returns_columnar(monkeypatch):
    rows = [
        {"time": "2023-01-01", "expected": 10.0, "actual": 11.0, "baseline": 4.0,
         "expected_ci_lo": 9.0, "expected_ci_hi": 11.0, "baseline_ci_lo": 3.0,
         "baseline_ci_hi": 5.0, "residual": 1.0},
    ]
    service = AnalysisService(catalog=_ModelFitCatalog(rows))
    result = service.get_model_fit("m", None)
    assert result["model_id"] == "m"
    assert result["row_count"] == 1
    assert "expected" in result["columns"] and "residual" in result["columns"]
    assert "data" not in result and "result_metadata" not in result
```

Run: `uv run pytest tests/unit/test_analysis_service.py -k get_model_fit -v`
Expected: FAIL (`AnalysisService` has no `get_model_fit`).

- [ ] **Step 2: Implement the facade method**

Add to `AnalyzerFacade` in `analyzer_facade.py` (import `filter_records` at top: `from google_meridian_mcp_server.meridian.dataset_mapper import dataset_to_records, filter_records`):

```python
    def get_model_fit(self, filters: AnalysisFilters) -> list[dict]:
        ds = self._get_analyzer().expected_vs_actual_data(
            aggregate_geos=True,
            aggregate_times=False,
            use_kpi=self.resolve_use_kpi(filters),
            confidence_level=0.9,
        )

        def _wide(var_name: str) -> pd.DataFrame:
            frame = ds[var_name].to_dataframe(name=var_name).reset_index()
            pivoted = frame.pivot(index="time", columns="metric", values=var_name)
            return pivoted.rename(
                columns={
                    "mean": var_name,
                    "ci_lo": f"{var_name}_ci_lo",
                    "ci_hi": f"{var_name}_ci_hi",
                }
            )

        expected = _wide("expected")
        baseline = _wide("baseline")
        actual = ds["actual"].to_dataframe(name="actual").reset_index()
        if "metric" in actual.columns:
            actual = actual[actual["metric"] == "mean"].drop(columns="metric")

        merged = expected.join(baseline).reset_index().merge(actual, on="time")
        merged["residual"] = merged["actual"] - merged["expected"]
        ordered = [
            "time",
            "expected",
            "expected_ci_lo",
            "expected_ci_hi",
            "actual",
            "baseline",
            "baseline_ci_lo",
            "baseline_ci_hi",
            "residual",
        ]
        merged = merged.reindex(columns=[c for c in ordered if c in merged.columns])
        records = dataset_to_records(merged)
        return filter_records(
            records,
            start_date=filters.start_date,
            end_date=filters.end_date,
        )
```

- [ ] **Step 3: Implement the service method**

Add to `AnalysisService` in `analysis_service.py`:

```python
    def get_model_fit(
        self, model_id: str, filters: AnalysisFilters | dict | None
    ) -> dict[str, Any]:
        normalized_filters = normalize_filters(filters)
        params = {"filters": self._filter_key(normalized_filters)}

        def _compute() -> dict[str, Any]:
            facade = self._catalog.get_facade(model_id)
            try:
                rows = facade.get_model_fit(normalized_filters)
            except Exception as exc:
                raise MissingModelDataError(model_id, str(exc)) from exc
            return self._build_result(model_id=model_id, rows=rows)

        return self._cached("get_model_fit", model_id, params, _compute)
```

- [ ] **Step 4: Register the MCP tool**

Add to `transport/tools.py` inside `register_tools` (after `get_response_curves`):

```python
    @mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
    async def get_model_fit(
        model_id: Annotated[
            str,
            Field(
                min_length=1,
                description="Model identifier from list_models (e.g. 'geo-revenue').",
            ),
        ],
        ctx: Context,
        filters: Annotated[
            AnalysisFilters | None,
            Field(
                description="Optional filters. Only start_date/end_date apply here; results are aggregated across all geos.",
            ),
        ] = None,
    ) -> dict[str, Any]:
        """Get model fit over time: expected vs actual outcome, baseline, and residual (actual - expected) per time period, with confidence intervals. Use this to judge how well the model tracks observed outcomes."""
        try:
            return _analysis_service(ctx).get_model_fit(
                model_id,
                normalize_filters(filters),
            )
        except MeridianMcpError as error:
            return _error_response(error)
```

- [ ] **Step 5: Advertise it in the overview options**

In `analysis_service.get_model_overview._compute`, add to the `available_tool_options` dict (after `get_response_curves`):

```python
                "get_model_fit": {},
```

- [ ] **Step 6: Run tests + ruff, commit**

Run: `uv run pytest tests/unit/test_analysis_service.py -k get_model_fit -v && uv run ruff check src tests`
Expected: PASS.
```bash
git add src/google_meridian_mcp_server/meridian/analyzer_facade.py src/google_meridian_mcp_server/services/analysis_service.py src/google_meridian_mcp_server/transport/tools.py tests/unit/test_analysis_service.py
git commit -m "feat: add get_model_fit tool (expected vs actual, baseline, residual)"
```

---

## Task 9: New tool `get_reach_frequency`

**Files:**
- Modify: `src/google_meridian_mcp_server/meridian/analyzer_facade.py`
- Modify: `src/google_meridian_mcp_server/services/analysis_service.py`
- Modify: `src/google_meridian_mcp_server/transport/tools.py`
- Test: `tests/unit/test_analysis_service.py`

**Interfaces:**
- Consumes: `resolve_use_kpi`, `has_rf_channels` (Task 2), `MetricNotSupportedError` (Task 2).
- Produces: `AnalyzerFacade.get_reach_frequency(filters) -> list[dict]`; `AnalysisService.get_reach_frequency(model_id, filters) -> dict` (raises `MetricNotSupportedError` when no RF channels); MCP tool `get_reach_frequency`. Columns: `channel, frequency, roi, ci_lo, ci_hi, optimal_frequency`.

- [ ] **Step 1: Write the failing service tests (fake facade + interrogator)**

Add to `tests/unit/test_analysis_service.py`:

```python
class _RFCatalog:
    def __init__(self, has_rf, rows):
        self._has_rf = has_rf
        self._rows = rows

    class _Facade:
        def __init__(self, rows):
            self._rows = rows

        def get_reach_frequency(self, filters):
            return self._rows

    class _Interrogator:
        def __init__(self, has_rf):
            self._has_rf = has_rf

        def has_rf_channels(self):
            return self._has_rf

    def get_facade(self, model_id):
        return self._Facade(self._rows)

    def get_interrogator(self, model_id):
        return self._Interrogator(self._has_rf)


def test_reach_frequency_columnar_when_rf_present():
    rows = [{"channel": "yt", "frequency": 1.0, "roi": 2.0, "ci_lo": 1.5,
             "ci_hi": 2.5, "optimal_frequency": 3.0}]
    service = AnalysisService(catalog=_RFCatalog(has_rf=True, rows=rows))
    result = service.get_reach_frequency("m", None)
    assert result["row_count"] == 1
    assert "optimal_frequency" in result["columns"]


def test_reach_frequency_errors_without_rf():
    service = AnalysisService(catalog=_RFCatalog(has_rf=False, rows=[]))
    with pytest.raises(MetricNotSupportedError) as exc:
        service.get_reach_frequency("m", None)
    assert exc.value.details["reason"].startswith("model has no reach")
```

Run: `uv run pytest tests/unit/test_analysis_service.py -k reach_frequency -v`
Expected: FAIL (no `get_reach_frequency`).

- [ ] **Step 2: Implement the facade method**

Add to `AnalyzerFacade`:

```python
    def get_reach_frequency(self, filters: AnalysisFilters) -> list[dict]:
        ds = self._get_analyzer().optimal_freq(
            selected_geos=self._selected_geos(filters),
            selected_times=self._expand_selected_times(filters),
            use_kpi=self.resolve_use_kpi(filters),
            confidence_level=0.9,
        )
        roi = ds["roi"].to_dataframe(name="roi").reset_index()
        roi_wide = (
            roi.pivot(index=["rf_channel", "frequency"], columns="metric", values="roi")
            .reset_index()
            .rename(columns={"mean": "roi"})
        )
        optimal = ds["optimal_frequency"].to_dataframe(
            name="optimal_frequency"
        ).reset_index()
        if "metric" in optimal.columns:
            optimal = optimal[optimal["metric"] == "mean"].drop(columns="metric")
        merged = roi_wide.merge(optimal, on="rf_channel").rename(
            columns={"rf_channel": "channel"}
        )
        ordered = ["channel", "frequency", "roi", "ci_lo", "ci_hi", "optimal_frequency"]
        merged = merged.reindex(columns=[c for c in ordered if c in merged.columns])
        if filters.channels:
            merged = merged[merged["channel"].isin(filters.channels)].copy()
        return dataset_to_records(merged)
```

- [ ] **Step 3: Implement the service method (with the RF gate)**

Add to `AnalysisService`:

```python
    def get_reach_frequency(
        self, model_id: str, filters: AnalysisFilters | dict | None
    ) -> dict[str, Any]:
        interrogator = self._catalog.get_interrogator(model_id)
        if not interrogator.has_rf_channels():
            raise MetricNotSupportedError(
                model_id,
                "reach_frequency",
                "model has no reach & frequency channels",
            )
        normalized_filters = normalize_filters(filters)
        params = {"filters": self._filter_key(normalized_filters)}

        def _compute() -> dict[str, Any]:
            facade = self._catalog.get_facade(model_id)
            try:
                rows = facade.get_reach_frequency(normalized_filters)
            except Exception as exc:
                raise MissingModelDataError(model_id, str(exc)) from exc
            return self._build_result(model_id=model_id, rows=rows)

        return self._cached("get_reach_frequency", model_id, params, _compute)
```

- [ ] **Step 4: Register the MCP tool**

Add to `transport/tools.py`:

```python
    @mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
    async def get_reach_frequency(
        model_id: Annotated[
            str,
            Field(
                min_length=1,
                description="Model identifier from list_models (e.g. 'geo-revenue').",
            ),
        ],
        ctx: Context,
        filters: Annotated[
            AnalysisFilters | None,
            Field(
                description="Optional filters to restrict by date range, geos, or RF channels.",
            ),
        ] = None,
    ) -> dict[str, Any]:
        """Get optimal-frequency analysis for reach & frequency channels: expected ROI across weekly frequency levels plus the optimal frequency per channel. Only available for models with reach & frequency data."""
        try:
            return _analysis_service(ctx).get_reach_frequency(
                model_id,
                normalize_filters(filters),
            )
        except MeridianMcpError as error:
            return _error_response(error)
```

- [ ] **Step 5: Advertise it conditionally in the overview options**

In `get_model_overview._compute`, after building `available_tool_options`, add:

```python
            if overview.get("rf_channels"):
                overview["available_tool_options"]["get_reach_frequency"] = {}
```

- [ ] **Step 6: Run tests + ruff, commit**

Run: `uv run pytest tests/unit/test_analysis_service.py -k reach_frequency -v && uv run ruff check src tests`
Expected: PASS.
```bash
git add src/google_meridian_mcp_server/meridian/analyzer_facade.py src/google_meridian_mcp_server/services/analysis_service.py src/google_meridian_mcp_server/transport/tools.py tests/unit/test_analysis_service.py
git commit -m "feat: add get_reach_frequency tool with no-RF graceful error"
```

---

## Task 10: New tool `get_channel_data`

**Files:**
- Modify: `src/google_meridian_mcp_server/meridian/dataset_mapper.py`
- Modify: `src/google_meridian_mcp_server/services/analysis_service.py`
- Modify: `src/google_meridian_mcp_server/transport/tools.py`
- Test: `tests/unit/test_channel_data.py`

**Interfaces:**
- Consumes: `filter_records` (Task 6), `_build_result`.
- Produces: `dataset_mapper.extract_channel_data(mmm) -> list[dict]`; `AnalysisService.get_channel_data(model_id, filters) -> dict`; MCP tool `get_channel_data`. Long format, one row per `(channel, geo, time)`; columns: `channel, channel_type, geo, time, impressions, spend, reach, frequency, rf_spend, value`.

- [ ] **Step 1: Write the failing builder test**

Create `tests/unit/test_channel_data.py`:

```python
from types import SimpleNamespace

import xarray as xr

from google_meridian_mcp_server.meridian.dataset_mapper import extract_channel_data


def _input_data():
    times = ["2023-01-01", "2023-01-08"]
    return SimpleNamespace(
        media_channel=xr.DataArray(["tv"], coords={"media_channel": ["tv"]}, dims=("media_channel",)),
        media=xr.DataArray(
            [[[100.0], [120.0]]],
            coords={"geo": ["us"], "media_time": times, "media_channel": ["tv"]},
            dims=("geo", "media_time", "media_channel"), name="media",
        ),
        media_spend=xr.DataArray(
            [[[5.0], [6.0]]],
            coords={"geo": ["us"], "time": times, "media_channel": ["tv"]},
            dims=("geo", "time", "media_channel"), name="media_spend",
        ),
        rf_channel=xr.DataArray(["yt"], coords={"rf_channel": ["yt"]}, dims=("rf_channel",)),
        reach=xr.DataArray(
            [[[80.0], [90.0]]],
            coords={"geo": ["us"], "media_time": times, "rf_channel": ["yt"]},
            dims=("geo", "media_time", "rf_channel"), name="reach",
        ),
        frequency=xr.DataArray(
            [[[1.2], [1.5]]],
            coords={"geo": ["us"], "media_time": times, "rf_channel": ["yt"]},
            dims=("geo", "media_time", "rf_channel"), name="frequency",
        ),
        rf_spend=xr.DataArray(
            [[[4.0], [4.5]]],
            coords={"geo": ["us"], "time": times, "rf_channel": ["yt"]},
            dims=("geo", "time", "rf_channel"), name="rf_spend",
        ),
        organic_media_channel=None, organic_media=None,
        organic_rf_channel=None, organic_reach=None, organic_frequency=None,
        non_media_channel=None, non_media_treatments=None,
    )


def test_channel_data_long_has_types_and_null_padding():
    rows = extract_channel_data(SimpleNamespace(input_data=_input_data()))
    by_channel = {r["channel"]: r for r in rows if r["time"]}
    tv = next(r for r in rows if r["channel"] == "tv")
    yt = next(r for r in rows if r["channel"] == "yt")
    assert tv["channel_type"] == "paid_media"
    assert tv["impressions"] == 100.0 and tv["spend"] == 5.0
    assert tv["reach"] is None and tv["rf_spend"] is None
    assert yt["channel_type"] == "rf"
    assert yt["reach"] == 80.0 and yt["frequency"] == 1.2 and yt["rf_spend"] == 4.0
    assert yt["impressions"] is None and yt["spend"] is None
    # Unified column set across all rows.
    assert set(tv) == set(yt)
```

Run: `uv run pytest tests/unit/test_channel_data.py -v`
Expected: FAIL (`extract_channel_data` undefined).

- [ ] **Step 2: Implement `extract_channel_data`**

Add to `src/google_meridian_mcp_server/meridian/dataset_mapper.py`:

```python
_CHANNEL_DATA_COLUMNS = [
    "channel",
    "channel_type",
    "geo",
    "time",
    "impressions",
    "spend",
    "reach",
    "frequency",
    "rf_spend",
    "value",
]

# (channel_type, channel_coord, [(array_attr, value_column), ...])
_CHANNEL_DATA_SOURCES = [
    ("paid_media", "media_channel", [("media", "impressions"), ("media_spend", "spend")]),
    ("rf", "rf_channel", [("reach", "reach"), ("frequency", "frequency"), ("rf_spend", "rf_spend")]),
    ("organic_media", "organic_media_channel", [("organic_media", "impressions")]),
    ("organic_rf", "organic_rf_channel", [("organic_reach", "reach"), ("organic_frequency", "frequency")]),
    ("non_media", "non_media_channel", [("non_media_treatments", "value")]),
]


def _channel_long_frame(array: Any, channel_coord: str, value_column: str) -> pd.DataFrame | None:
    if array is None:
        return None
    frame = array.to_dataframe(name=value_column).reset_index()
    if channel_coord not in frame.columns:
        return None
    frame = frame.rename(columns={channel_coord: "channel"})
    if "media_time" in frame.columns:
        frame = frame.rename(columns={"media_time": "time"})
    keep = [c for c in ("channel", "geo", "time", value_column) if c in frame.columns]
    return frame[keep]


def extract_channel_data(mmm: Any) -> list[dict]:
    """Extract every channel-keyed input as one unified long table."""
    input_data = mmm.input_data
    type_frames: list[pd.DataFrame] = []

    for channel_type, channel_coord, sources in _CHANNEL_DATA_SOURCES:
        merged: pd.DataFrame | None = None
        for array_attr, value_column in sources:
            frame = _channel_long_frame(
                getattr(input_data, array_attr, None), channel_coord, value_column
            )
            if frame is None:
                continue
            if merged is None:
                merged = frame
            else:
                join_keys = [c for c in merged.columns if c in frame.columns and c not in (value_column,)]
                merged = merged.merge(frame, how="outer", on=join_keys)
        if merged is None:
            continue
        merged["channel_type"] = channel_type
        type_frames.append(merged)

    if not type_frames:
        return []

    combined = pd.concat(type_frames, ignore_index=True, sort=False)
    combined = combined.reindex(columns=_CHANNEL_DATA_COLUMNS)
    return _df_to_records(combined)
```

- [ ] **Step 3: Run the builder test**

Run: `uv run pytest tests/unit/test_channel_data.py -v`
Expected: PASS.

- [ ] **Step 4: Implement the service method**

Add to `AnalysisService` (import `extract_channel_data` and `filter_records` are already imported from dataset_mapper — extend the import to include `extract_channel_data`):

```python
    def get_channel_data(
        self, model_id: str, filters: AnalysisFilters | dict | None
    ) -> dict[str, Any]:
        normalized_filters = normalize_filters(filters)
        params = {"filters": self._filter_key(normalized_filters)}

        def _compute() -> dict[str, Any]:
            try:
                rows = extract_channel_data(self._catalog.resolve(model_id))
            except Exception as exc:
                raise MissingModelDataError(model_id, str(exc)) from exc
            rows = filter_records(
                rows,
                start_date=normalized_filters.start_date,
                end_date=normalized_filters.end_date,
                geos=normalized_filters.geos,
                channels=normalized_filters.channels,
            )
            return self._build_result(model_id=model_id, rows=rows)

        return self._cached("get_channel_data", model_id, params, _compute)
```

- [ ] **Step 5: Register the MCP tool**

Add to `transport/tools.py`:

```python
    @mcp.tool(annotations=READ_ONLY_TOOL_ANNOTATIONS)
    async def get_channel_data(
        model_id: Annotated[
            str,
            Field(
                min_length=1,
                description="Model identifier from list_models (e.g. 'geo-revenue').",
            ),
        ],
        ctx: Context,
        filters: Annotated[
            AnalysisFilters | None,
            Field(
                description="Optional filters to restrict by date range, geos, or channels.",
            ),
        ] = None,
    ) -> dict[str, Any]:
        """Everything about a channel in one table — spend, impressions, reach/frequency — across all channel types (paid media, RF, organic, non-media). Use to investigate one or more channels directly. For raw datasets by name (including non-channel series like KPI or controls), use get_training_data instead."""
        try:
            return _analysis_service(ctx).get_channel_data(
                model_id,
                normalize_filters(filters),
            )
        except MeridianMcpError as error:
            return _error_response(error)
```

- [ ] **Step 6: Sharpen the `get_training_data` description**

In `transport/tools.py`, replace the `get_training_data` docstring with:

```python
        """Retrieve raw input datasets by name (e.g. 'media_spend', 'kpi', 'controls', 'population') merged into one table — including non-channel series. Use when you want a specific dataset as stored. To investigate a channel's full picture across types, use get_channel_data instead."""
```

- [ ] **Step 7: Advertise it in the overview options**

In `get_model_overview._compute` `available_tool_options`, add:

```python
                "get_channel_data": {},
```

- [ ] **Step 8: Run tests + ruff, commit**

Run: `uv run pytest tests/unit/test_channel_data.py tests/unit/test_analysis_service.py -v && uv run ruff check src tests`
Expected: PASS.
```bash
git add src/google_meridian_mcp_server/meridian/dataset_mapper.py src/google_meridian_mcp_server/services/analysis_service.py src/google_meridian_mcp_server/transport/tools.py tests/unit/test_channel_data.py
git commit -m "feat: add get_channel_data tool (per-channel long, all channel types)"
```

---

## Task 11: Validation expectation matrix

**Files:**
- Create: `scripts/validation/__init__.py`, `scripts/validation/matrix.py`
- Test: `tests/unit/test_validation_matrix.py`

**Interfaces:**
- Consumes: `VARIANTS` (Task 1).
- Produces: `matrix.ANALYSIS_TOOLS: dict[str, list[str]]`, `matrix.expected_valid(variant, tool, output_type) -> bool`, `matrix.adversarial_cases(variant) -> list[AdversarialCase]`. `AdversarialCase` is a dataclass `(tool: str, args: dict, expected_error_code: str)`. Consumed by the runner (Task 12).

- [ ] **Step 1: Write the failing matrix tests**

Append to `tests/unit/test_validation_matrix.py`:

```python
from scripts.generate_validation_models import VARIANTS as _VARIANTS
from scripts.validation import matrix


def _variant(key):
    return next(v for v in _VARIANTS if v.key == key)


def test_roi_valid_only_for_revenue_models():
    assert matrix.expected_valid(_variant("geo-revenue"), "get_channel_summary", "roi")
    assert not matrix.expected_valid(_variant("geo-kpi-only"), "get_channel_summary", "roi")
    assert not matrix.expected_valid(_variant("national-kpi-only"), "get_channel_summary", "marginal_roi")


def test_cpik_valid_for_all_models():
    for key in ("geo-revenue", "geo-kpi-only", "national-kpi-rpk"):
        assert matrix.expected_valid(_variant(key), "get_channel_summary", "cpik")


def test_adversarial_cases_cover_roi_on_kpi_only():
    cases = matrix.adversarial_cases(_variant("geo-kpi-only"))
    codes = {(c.tool, c.expected_error_code) for c in cases}
    assert ("get_channel_summary", "metric_not_supported") in codes


def test_adversarial_cases_cover_reach_frequency_on_media_only():
    cases = matrix.adversarial_cases(_variant("geo-revenue-media-only"))
    assert any(
        c.tool == "get_reach_frequency" and c.expected_error_code == "metric_not_supported"
        for c in cases
    )
```

Run: `uv run pytest tests/unit/test_validation_matrix.py -v`
Expected: FAIL (`scripts.validation.matrix` does not exist).

- [ ] **Step 2: Create the package marker**

Create `scripts/validation/__init__.py`:

```python
"""Reusable live validation suite for the Meridian MCP server."""
```

- [ ] **Step 3: Implement the matrix**

Create `scripts/validation/matrix.py`:

```python
"""Declarative variant + expectation matrix for live validation."""

from __future__ import annotations

import dataclasses

ANALYSIS_TOOLS: dict[str, list[str]] = {
    "get_channel_summary": [
        "baseline_summary_metrics",
        "paid_summary_metrics",
        "roi",
        "cpik",
        "marginal_roi",
        "marginal_cpik",
    ],
    "get_contribution": ["contribution_metrics", "contribution_metrics_by_time"],
    "get_adstock_decay": ["adstock_decay", "alpha_summary"],
    "get_response_curves": ["response_curves", "response_curve_summary"],
}

# Output types that require revenue.
REVENUE_ONLY = {"roi", "marginal_roi"}


@dataclasses.dataclass(frozen=True)
class AdversarialCase:
    tool: str
    args: dict
    expected_error_code: str


def expected_valid(variant, tool: str, output_type: str | None) -> bool:
    """Whether (tool, output_type) is expected to return data for this variant."""
    if output_type in REVENUE_ONLY and not variant.factory_has_revenue():
        return False
    return True


def adversarial_cases(variant) -> list[AdversarialCase]:
    """Adversarial calls that must return a specific typed error for this variant."""
    cases: list[AdversarialCase] = []
    if not variant.factory_has_revenue():
        for output_type in ("roi", "marginal_roi"):
            cases.append(
                AdversarialCase(
                    "get_channel_summary",
                    {"model_id": variant.key, "output_type": output_type},
                    "metric_not_supported",
                )
            )
    if not variant.with_rf:
        cases.append(
            AdversarialCase(
                "get_reach_frequency",
                {"model_id": variant.key},
                "metric_not_supported",
            )
        )
    return cases
```

- [ ] **Step 4: Add the `factory_has_revenue` helper to `VariantSpec`**

In `scripts/generate_validation_models.py`, add a method to `VariantSpec`:

```python
    def factory_has_revenue(self) -> bool:
        # revenue and kpi_rpk variants carry revenue_per_kpi; kpi_only does not.
        return self.factory in ("revenue", "kpi_rpk")
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/unit/test_validation_matrix.py -v`
Expected: PASS.

- [ ] **Step 6: Ruff, commit**

Run: `uv run ruff check scripts tests`
```bash
git add scripts/validation/__init__.py scripts/validation/matrix.py scripts/generate_validation_models.py tests/unit/test_validation_matrix.py
git commit -m "feat: add validation expectation matrix"
```

---

## Task 12: Validation runner + entrypoint (supersedes live_verify.py)

**Files:**
- Create: `scripts/validation/runner.py`, `scripts/validation/live_validate.py`
- Remove: `scripts/live_verify.py`
- Test: `tests/unit/test_validation_runner.py`

**Interfaces:**
- Consumes: `matrix` (Task 11), `generate_validation_models.build_all`/`VARIANTS` (Task 1), `fastmcp.Client`, `google_meridian_mcp_server.server.mcp`.
- Produces: `runner.assert_columnar(payload, label)`, `runner.assert_error(payload, code, label)`, `runner.run_matrix(client) -> Report`. `live_validate.main()` builds fixtures if missing, runs the matrix, prints the result table, and exits non-zero on any failure.

- [ ] **Step 1: Write failing tests for the pure assertions**

Create `tests/unit/test_validation_runner.py`:

```python
import pytest

from scripts.validation import runner


def test_assert_columnar_accepts_valid_payload():
    payload = {"model_id": "m", "columns": ["a"], "rows": [[1]], "row_count": 1}
    runner.assert_columnar(payload, "ok")  # no raise


def test_assert_columnar_rejects_legacy_keys():
    payload = {"model_id": "m", "columns": [], "rows": [], "row_count": 0, "data": []}
    with pytest.raises(AssertionError):
        runner.assert_columnar(payload, "legacy")


def test_assert_columnar_rejects_ragged_rows():
    payload = {"model_id": "m", "columns": ["a", "b"], "rows": [[1]], "row_count": 1}
    with pytest.raises(AssertionError):
        runner.assert_columnar(payload, "ragged")


def test_assert_error_matches_code():
    runner.assert_error({"error_code": "metric_not_supported"}, "metric_not_supported", "e")
    with pytest.raises(AssertionError):
        runner.assert_error({"error_code": "other"}, "metric_not_supported", "e")
```

Run: `uv run pytest tests/unit/test_validation_runner.py -v`
Expected: FAIL (`scripts.validation.runner` missing).

- [ ] **Step 2: Implement the runner**

Create `scripts/validation/runner.py`:

```python
"""In-process MCP client driver and assertions for live validation."""

from __future__ import annotations

import dataclasses
import json

from scripts.validation import matrix


@dataclasses.dataclass
class Report:
    passed: list[str] = dataclasses.field(default_factory=list)
    failed: list[str] = dataclasses.field(default_factory=list)

    def ok(self, label: str) -> None:
        self.passed.append(label)
        print(f"  PASS {label}")

    def fail(self, label: str, reason: str) -> None:
        self.failed.append(f"{label}: {reason}")
        print(f"  FAIL {label}: {reason}")


def _content_to_obj(result):
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
    if isinstance(obj, dict) and set(obj.keys()) == {"result"}:
        return obj["result"]
    return obj


async def call(client, name, args):
    res = await client.call_tool(name, args)
    return _unwrap(_content_to_obj(res))


def assert_columnar(payload, label: str) -> None:
    assert isinstance(payload, dict), f"{label}: expected dict, got {type(payload)}"
    assert "error_code" not in payload, f"{label}: unexpected error {payload}"
    for key in ("model_id", "columns", "rows", "row_count"):
        assert key in payload, f"{label}: missing '{key}'"
    assert payload["row_count"] == len(payload["rows"]), f"{label}: row_count mismatch"
    for row in payload["rows"]:
        assert len(row) == len(payload["columns"]), f"{label}: ragged row"
    assert "data" not in payload and "result_metadata" not in payload, (
        f"{label}: legacy keys present"
    )


def assert_error(payload, code: str | None, label: str) -> None:
    assert isinstance(payload, dict), f"{label}: expected dict error, got {type(payload)}"
    if code is None:
        assert "error_code" in payload, f"{label}: expected an error, got {payload}"
        return
    assert payload.get("error_code") == code, (
        f"{label}: expected error_code={code}, got {payload.get('error_code')}"
    )


async def run_matrix(client) -> Report:
    from scripts.generate_validation_models import VARIANTS

    report = Report()
    for variant in VARIANTS:
        model_id = variant.key
        # Overview: must load and must prune ROI for no-revenue models.
        overview = await call(client, "get_model_overview", {"model_id": model_id})
        try:
            assert "available_tool_options" in overview, "no available_tool_options"
            cs_types = overview["available_tool_options"]["get_channel_summary"]["output_type"]
            if not variant.factory_has_revenue():
                assert "roi" not in cs_types and "marginal_roi" not in cs_types, (
                    "roi advertised for no-revenue model"
                )
            report.ok(f"{model_id}/get_model_overview")
        except AssertionError as exc:
            report.fail(f"{model_id}/get_model_overview", str(exc))

        # Happy path: analysis tools that should return data.
        for tool, output_types in matrix.ANALYSIS_TOOLS.items():
            for output_type in output_types:
                if not matrix.expected_valid(variant, tool, output_type):
                    continue
                label = f"{model_id}/{tool}[{output_type}]"
                try:
                    payload = await call(
                        client, tool, {"model_id": model_id, "output_type": output_type}
                    )
                    assert_columnar(payload, label)
                    report.ok(label)
                except AssertionError as exc:
                    report.fail(label, str(exc))

        # Single-output new tools.
        for tool in ("get_model_fit", "get_channel_data"):
            label = f"{model_id}/{tool}"
            try:
                assert_columnar(await call(client, tool, {"model_id": model_id}), label)
                report.ok(label)
            except AssertionError as exc:
                report.fail(label, str(exc))

        if variant.with_rf:
            label = f"{model_id}/get_reach_frequency"
            try:
                assert_columnar(
                    await call(client, "get_reach_frequency", {"model_id": model_id}),
                    label,
                )
                report.ok(label)
            except AssertionError as exc:
                report.fail(label, str(exc))

        # Adversarial: typed errors.
        for case in matrix.adversarial_cases(variant):
            label = f"{model_id}/ADV/{case.tool}->{case.expected_error_code}"
            try:
                payload = await call(client, case.tool, case.args)
                assert_error(payload, case.expected_error_code, label)
                report.ok(label)
            except AssertionError as exc:
                report.fail(label, str(exc))

    # Global adversarial: unknown model id must return a typed error, not crash.
    label = "GLOBAL/ADV/unknown-model"
    try:
        payload = await call(client, "get_model_overview", {"model_id": "does-not-exist"})
        assert_error(payload, None, label)
        report.ok(label)
    except AssertionError as exc:
        report.fail(label, str(exc))

    # Loader smoke: the .pkl fixture must load through the pickle branch.
    label = "GLOBAL/loader-pkl/national-revenue-pkl"
    try:
        overview = await call(
            client, "get_model_overview", {"model_id": "national-revenue-pkl"}
        )
        assert "available_tool_options" in overview, f"{label}: pkl model failed to load"
        report.ok(label)
    except AssertionError as exc:
        report.fail(label, str(exc))

    return report
```

- [ ] **Step 3: Run the assertion tests**

Run: `uv run pytest tests/unit/test_validation_runner.py -v`
Expected: PASS.

- [ ] **Step 4: Implement the entrypoint**

Create `scripts/validation/live_validate.py`:

```python
"""Live validation: build fixtures if missing, run the matrix, exit non-zero on failure.

Usage:
  uv run python -m scripts.validation.live_validate
  uv run python -m scripts.validation.live_validate --force   # rebuild fixtures
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from scripts.generate_validation_models import DEFAULT_OUT_ROOT, build_all
from scripts.validation.runner import run_matrix


def _ensure_fixtures(force: bool) -> None:
    build_all(DEFAULT_OUT_ROOT, force=force)


async def _run() -> int:
    os.environ["PERSISTENCE_BACKEND"] = "local"
    os.environ["LOCAL_MODELS_ROOT"] = str(DEFAULT_OUT_ROOT)
    os.environ.setdefault("RESULT_CACHE_ENABLED", "false")

    from fastmcp import Client

    from google_meridian_mcp_server.server import mcp

    async with Client(mcp) as client:
        report = await run_matrix(client)

    print(f"\n{len(report.passed)} passed, {len(report.failed)} failed")
    if report.failed:
        print("FAILURES:")
        for item in report.failed:
            print(f"  - {item}")
        return 1
    print("LIVE VALIDATION PASSED")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="Rebuild fixtures first")
    args = parser.parse_args()
    if not (DEFAULT_OUT_ROOT.exists() and any(DEFAULT_OUT_ROOT.iterdir())) or args.force:
        _ensure_fixtures(args.force)
    sys.exit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Remove the superseded script**

```bash
git rm scripts/live_verify.py
```

- [ ] **Step 6: Run the full live validation (the acceptance gate)**

Run: `uv run python -m scripts.validation.live_validate`
Expected: builds the 7 fixtures (first run only; takes a few minutes), then prints PASS lines per variant×tool and finally `LIVE VALIDATION PASSED` with `0 failed`. If any FAIL lines appear, fix the underlying tool/facade until green. (This is the real-Meridian gate for Tasks 2–10.)

- [ ] **Step 7: Ruff, commit**

Run: `uv run ruff check scripts tests`
```bash
git add scripts/validation/runner.py scripts/validation/live_validate.py tests/unit/test_validation_runner.py
git rm scripts/live_verify.py
git commit -m "feat: add live validation suite over the variant matrix; remove live_verify"
```

---

## Task 13: Parity report + documentation

**Files:**
- Create: `docs/meridian-mcp-showcase-parity.md`
- Modify: `AGENTS.md`, `README.md`
- Test: documentation only (no code test); verify the doc files exist and the tool lists are consistent.

**Interfaces:** none (docs).

- [ ] **Step 1: Write the parity report**

Create `docs/meridian-mcp-showcase-parity.md` with a table mapping each in-scope mmm-showcase chart/data point to its MCP tool and status. Use these rows (one per showcase item; Summary Report, Optimization, and Model Diagnostics pages are excluded):

```markdown
# mmm-showcase ↔ Meridian MCP Parity

Scope: Home, Response Curves, Attribution, Lag Effects, Reach & Frequency, Data
Exploration. Excluded: Summary Report, Budget Optimization, Model Diagnostics.

| Showcase item | Page | Meridian quantity | MCP tool | Status | Notes |
|---|---|---|---|---|---|
| Model metadata (time range, geos, channels, model type) | Home | input_data coords | get_model_overview | Supported | |
| Channel inventory (media/RF/organic/non-media/controls) | Home | input_data coords | get_model_overview | Supported | |
| Geographic markets + population | Home | input_data.geo/population | get_model_overview | Supported | |
| Historical channel spend time-series | Response Curves | input_data.media_spend/rf_spend | get_channel_data | Supported | per-channel long |
| Response/saturation curve (incremental outcome vs spend) | Response Curves | Analyzer.response_curves | get_response_curves | Supported | |
| ROI / CPIK / mROI efficiency at spend points | Response Curves | derived from response curve | get_response_curves (+ agent arithmetic) | Partial | app-side arithmetic; derivable |
| Media summary table (paid + non-paid) | Attribution | Analyzer.summary_metrics | get_channel_summary (paid_summary_metrics) | Supported | |
| % outcome contribution & % spend by channel | Attribution | summary_metrics | get_channel_summary / get_contribution | Supported | |
| ROI / CPIK by channel | Attribution | summary_metrics | get_channel_summary (roi/cpik) | Supported | roi gated to revenue models |
| Contribution waterfall (incl. baseline & non-paid) | Attribution | contribution_metrics | get_contribution | Supported | |
| Response curves (all channels) | Attribution | Analyzer.response_curves | get_response_curves | Supported | |
| Model fit: expected vs actual + baseline + residual | Attribution | Analyzer.expected_vs_actual_data | get_model_fit | Supported (new) | geo-aggregated |
| Adstock decay curves | Lag Effects | Analyzer.adstock_decay | get_adstock_decay | Supported | |
| Optimal frequency / ROI-vs-frequency | Reach & Frequency | Analyzer.optimal_freq | get_reach_frequency | Supported (new) | RF-only |
| Raw input series (impressions, spend, reach, freq, controls, KPI) | Data Exploration | input_data arrays | get_channel_data + get_training_data | Supported | channel-keyed vs raw datasets |
| VIF / multicollinearity | Data Exploration | statsmodels over scaled arrays | — | Out-of-scope | computed app-side, not a Meridian output |
| Per-geo disaggregated metrics | (various) | aggregate_geos=False | — | Future work | dead flag removed; not yet implemented |

New tools `get_model_fit`, `get_reach_frequency`, and `get_channel_data` close
the previously-unsupported items. VIF and the app-side efficiency arithmetic
remain out of scope.
```

- [ ] **Step 2: Update AGENTS.md**

In `AGENTS.md`, under "Current Tool Surface", add the three new tools:

```markdown
- `get_model_fit`
- `get_reach_frequency`
- `get_channel_data`
```

Under "Current Analysis Behavior", add:

```markdown
- `roi` and `marginal_roi` raise `metric_not_supported` for models without revenue (`revenue_per_kpi is None`); `cpik`/`marginal_cpik` are valid for all models.
- `get_model_overview.available_tool_options` is dynamic: it omits `roi`/`marginal_roi` for no-revenue models and lists `get_reach_frequency` only for models with reach & frequency channels.
- The facade resolves `use_kpi` from the model's revenue capability when the caller does not set it (no-revenue models default to KPI mode).
- `get_training_data` applies date/geo/channel filters to the merged rows; the dead `aggregate_geos` filter field has been removed.
- `get_model_fit` returns expected/actual/baseline/residual over time (geo-aggregated). `get_reach_frequency` returns optimal-frequency ROI curves (RF-only, else `metric_not_supported`). `get_channel_data` returns a per-channel long table across all channel types.
```

- [ ] **Step 3: Update README.md**

In `README.md`, under "Tool Surface", add `get_model_fit`, `get_reach_frequency`, and `get_channel_data` to the bullet list, and add a short paragraph describing each (one sentence each) plus a note that `roi`/`marginal_roi` are only available for revenue models. Add a "Live validation" subsection under "Quality Checks":

```markdown
### Live validation

Build dummy models for every variant and validate every tool live against an
in-process MCP client (national vs geo, revenue vs KPI, with adversarial
error-path checks):

```bash
uv run python -m scripts.validation.live_validate
```

This generates gitignored fixtures under `models/_validation/` on first run and
exits non-zero on any mismatch.
```

- [ ] **Step 4: Verify docs and full suite**

Run: `uv run pytest && uv run ruff check src tests scripts`
Expected: all tests PASS, ruff clean.
Run: `test -f docs/meridian-mcp-showcase-parity.md && echo OK`
Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add docs/meridian-mcp-showcase-parity.md AGENTS.md README.md
git commit -m "docs: add showcase parity report and document new tools/behavior"
```

---

## Final verification (after all tasks)

- [ ] Run the full unit/integration suite: `uv run pytest` → all green.
- [ ] Run ruff: `uv run ruff check src tests scripts` → clean.
- [ ] Run the live validation gate: `uv run python -m scripts.validation.live_validate` → `LIVE VALIDATION PASSED`, `0 failed`.
- [ ] Confirm no `scripts/live_verify.py` remains and no committed binaries under `models/`.

# Drift report: `pre-full-funnel` -> `post-full-funnel`

**Verdict: PASS -- exit code 0** (154 ACKNOWLEDGED)

## Legend

- **FAIL** and **REVIEW** both block (exit code 1): FAIL is a structural break (added/removed/type-changed field, changed identity, or a case missing from one label) with no ambiguity; REVIEW is a numeric or ordering finding that needs a human to judge (over tolerance, sign flip, known racy field, broken CI ordering) and MIGHT be legitimate.
- **ACKNOWLEDGED** does not block: pre-registered in `acknowledged.py` with a mandatory reason (spec 7.4). Reported in its own section, never folded into a clean PASS.

## Environment

| Item | pre-full-funnel | post-full-funnel |
| --- | --- | --- |
| transport | inprocess | inprocess |
| Python | 3.13.13 | 3.13.13 |
| fastmcp | 4.0.3 | 4.0.3 |
| google-meridian | 2.1.0 | 2.1.0 |
| jax | 0.11.1 | 0.11.1 |
| jaxlib | 0.11.1 | 0.11.1 |
| pydantic | 2.13.5 | 2.13.5 |
| tensorflow | 2.21.0 | 2.21.0 |
| tensorflow-probability | None | None |
| tfp-nightly | 0.26.0.dev20260130 | 0.26.0.dev20260130 |
| worker MERIDIAN_BACKEND | jax | jax |
| worker MERIDIAN_ENABLE_JAX_X64 | true | true |
| worker TF_CPP_MIN_LOG_LEVEL | 3 | 3 |
| probe backend (`probe_backend`) | jax | jax |
| relative tolerance (REL_TOLERANCE) | 1e-03 | 1e-03 |
| absolute floor (ABS_FLOOR) | 1e-09 | 1e-09 |

> `worker MERIDIAN_BACKEND`/`worker MERIDIAN_ENABLE_JAX_X64` above are recorded verbatim from the environment this capture DRIVER process happened to inherit -- frozen by design (`manifest.build_manifest`'s docstring) so existing snapshots never go stale, and may not reflect what the tools actually ran on (`None` above is normal, not a bug). `probe backend` is the CORRECTED value instead: the backend the fixture probe was explicitly forced to run under, mirroring how the real analysis/optimization workers resolve it -- this is what the Fixture provenance section's `current_backend` below actually measures. `None` there means an older manifest predating this field, not that no backend was used.

## Fixture provenance

**pre-full-funnel**

- `geo-kpi-only`: trained TENSORFLOW/FLOAT32 (v1.7.0) vs current JAX/FLOAT64 -- backend mismatch: True, precision mismatch: True
- `geo-kpi-rpk`: trained TENSORFLOW/FLOAT32 (v1.7.0) vs current JAX/FLOAT64 -- backend mismatch: True, precision mismatch: True
- `geo-revenue`: trained TENSORFLOW/FLOAT32 (v1.7.0) vs current JAX/FLOAT64 -- backend mismatch: True, precision mismatch: True
- `geo-revenue-media-only`: trained TENSORFLOW/FLOAT32 (v1.7.0) vs current JAX/FLOAT64 -- backend mismatch: True, precision mismatch: True
- `national-kpi-only`: trained TENSORFLOW/FLOAT32 (v1.7.0) vs current JAX/FLOAT64 -- backend mismatch: True, precision mismatch: True
- `national-kpi-rpk`: trained TENSORFLOW/FLOAT32 (v1.7.0) vs current JAX/FLOAT64 -- backend mismatch: True, precision mismatch: True
- `national-revenue`: trained TENSORFLOW/FLOAT32 (v1.7.0) vs current JAX/FLOAT64 -- backend mismatch: True, precision mismatch: True

**post-full-funnel**

- `geo-kpi-only`: trained TENSORFLOW/FLOAT32 (v1.7.0) vs current JAX/FLOAT64 -- backend mismatch: True, precision mismatch: True
- `geo-kpi-rpk`: trained TENSORFLOW/FLOAT32 (v1.7.0) vs current JAX/FLOAT64 -- backend mismatch: True, precision mismatch: True
- `geo-revenue`: trained TENSORFLOW/FLOAT32 (v1.7.0) vs current JAX/FLOAT64 -- backend mismatch: True, precision mismatch: True
- `geo-revenue-media-only`: trained TENSORFLOW/FLOAT32 (v1.7.0) vs current JAX/FLOAT64 -- backend mismatch: True, precision mismatch: True
- `national-kpi-only`: trained TENSORFLOW/FLOAT32 (v1.7.0) vs current JAX/FLOAT64 -- backend mismatch: True, precision mismatch: True
- `national-kpi-rpk`: trained TENSORFLOW/FLOAT32 (v1.7.0) vs current JAX/FLOAT64 -- backend mismatch: True, precision mismatch: True
- `national-revenue`: trained TENSORFLOW/FLOAT32 (v1.7.0) vs current JAX/FLOAT64 -- backend mismatch: True, precision mismatch: True

## Summary

- Cases compared: 293 (279 clean -- no findings in any section)
- Case-set differences (case present in only one label): 0
- FAIL (including case-set differences above): 0
- REVIEW: 0
- ACKNOWLEDGED: 154

## Per-case environment provenance (for cases with findings)

- pre-full-funnel vs post-full-funnel capture_env differs the same way for every case below: src_hash: `1b7569e2e3a4ba7d854e29b6f171152f80fad4fb9302bd7823f2963e0e2c84ef` vs `a842346d4ebf6e31a1ffcc3d29b451b393f1c77fda5c49866b53d51909d9fb85`

## FAIL - structural

_none_

## REVIEW -- needs a human

_none_

## ACKNOWLEDGED - pre-registered in acknowledged.py

| Case | Pointer | Detail |
| --- | --- | --- |
| `geo-kpi-only/get_model_overview__default.json` | `/funnel` | key added: 'funnel' -- v0.4.0: get_model_overview reports funnel='single' on single models. |
| `geo-kpi-only/list_models__default.json` | `/0/funnel` | key added: 'funnel' -- v0.4.0: list_models reports each model's funnel type (single/full_funnel). |
| `geo-kpi-only/list_models__default.json` | `/0/mediators` | key added: 'mediators' -- v0.4.0: list_models lists brand-mediator names (empty for single models). |
| `geo-kpi-only/list_models__default.json` | `/0/model_version` | key added: 'model_version' -- v0.4.0: list_models exposes the stage-file version token that now keys result caching and optimization reuse. |
| `geo-kpi-only/list_models__default.json` | `/1/funnel` | key added: 'funnel' -- v0.4.0: list_models reports each model's funnel type (single/full_funnel). |
| `geo-kpi-only/list_models__default.json` | `/1/mediators` | key added: 'mediators' -- v0.4.0: list_models lists brand-mediator names (empty for single models). |
| `geo-kpi-only/list_models__default.json` | `/1/model_version` | key added: 'model_version' -- v0.4.0: list_models exposes the stage-file version token that now keys result caching and optimization reuse. |
| `geo-kpi-only/list_models__default.json` | `/2/funnel` | key added: 'funnel' -- v0.4.0: list_models reports each model's funnel type (single/full_funnel). |
| `geo-kpi-only/list_models__default.json` | `/2/mediators` | key added: 'mediators' -- v0.4.0: list_models lists brand-mediator names (empty for single models). |
| `geo-kpi-only/list_models__default.json` | `/2/model_version` | key added: 'model_version' -- v0.4.0: list_models exposes the stage-file version token that now keys result caching and optimization reuse. |
| `geo-kpi-only/list_models__default.json` | `/3/funnel` | key added: 'funnel' -- v0.4.0: list_models reports each model's funnel type (single/full_funnel). |
| `geo-kpi-only/list_models__default.json` | `/3/mediators` | key added: 'mediators' -- v0.4.0: list_models lists brand-mediator names (empty for single models). |
| `geo-kpi-only/list_models__default.json` | `/3/model_version` | key added: 'model_version' -- v0.4.0: list_models exposes the stage-file version token that now keys result caching and optimization reuse. |
| `geo-kpi-only/list_models__default.json` | `/4/funnel` | key added: 'funnel' -- v0.4.0: list_models reports each model's funnel type (single/full_funnel). |
| `geo-kpi-only/list_models__default.json` | `/4/mediators` | key added: 'mediators' -- v0.4.0: list_models lists brand-mediator names (empty for single models). |
| `geo-kpi-only/list_models__default.json` | `/4/model_version` | key added: 'model_version' -- v0.4.0: list_models exposes the stage-file version token that now keys result caching and optimization reuse. |
| `geo-kpi-only/list_models__default.json` | `/5/funnel` | key added: 'funnel' -- v0.4.0: list_models reports each model's funnel type (single/full_funnel). |
| `geo-kpi-only/list_models__default.json` | `/5/mediators` | key added: 'mediators' -- v0.4.0: list_models lists brand-mediator names (empty for single models). |
| `geo-kpi-only/list_models__default.json` | `/5/model_version` | key added: 'model_version' -- v0.4.0: list_models exposes the stage-file version token that now keys result caching and optimization reuse. |
| `geo-kpi-only/list_models__default.json` | `/6/funnel` | key added: 'funnel' -- v0.4.0: list_models reports each model's funnel type (single/full_funnel). |
| `geo-kpi-only/list_models__default.json` | `/6/mediators` | key added: 'mediators' -- v0.4.0: list_models lists brand-mediator names (empty for single models). |
| `geo-kpi-only/list_models__default.json` | `/6/model_version` | key added: 'model_version' -- v0.4.0: list_models exposes the stage-file version token that now keys result caching and optimization reuse. |
| `geo-kpi-rpk/get_model_overview__default.json` | `/funnel` | key added: 'funnel' -- v0.4.0: get_model_overview reports funnel='single' on single models. |
| `geo-kpi-rpk/list_models__default.json` | `/0/funnel` | key added: 'funnel' -- v0.4.0: list_models reports each model's funnel type (single/full_funnel). |
| `geo-kpi-rpk/list_models__default.json` | `/0/mediators` | key added: 'mediators' -- v0.4.0: list_models lists brand-mediator names (empty for single models). |
| `geo-kpi-rpk/list_models__default.json` | `/0/model_version` | key added: 'model_version' -- v0.4.0: list_models exposes the stage-file version token that now keys result caching and optimization reuse. |
| `geo-kpi-rpk/list_models__default.json` | `/1/funnel` | key added: 'funnel' -- v0.4.0: list_models reports each model's funnel type (single/full_funnel). |
| `geo-kpi-rpk/list_models__default.json` | `/1/mediators` | key added: 'mediators' -- v0.4.0: list_models lists brand-mediator names (empty for single models). |
| `geo-kpi-rpk/list_models__default.json` | `/1/model_version` | key added: 'model_version' -- v0.4.0: list_models exposes the stage-file version token that now keys result caching and optimization reuse. |
| `geo-kpi-rpk/list_models__default.json` | `/2/funnel` | key added: 'funnel' -- v0.4.0: list_models reports each model's funnel type (single/full_funnel). |
| `geo-kpi-rpk/list_models__default.json` | `/2/mediators` | key added: 'mediators' -- v0.4.0: list_models lists brand-mediator names (empty for single models). |
| `geo-kpi-rpk/list_models__default.json` | `/2/model_version` | key added: 'model_version' -- v0.4.0: list_models exposes the stage-file version token that now keys result caching and optimization reuse. |
| `geo-kpi-rpk/list_models__default.json` | `/3/funnel` | key added: 'funnel' -- v0.4.0: list_models reports each model's funnel type (single/full_funnel). |
| `geo-kpi-rpk/list_models__default.json` | `/3/mediators` | key added: 'mediators' -- v0.4.0: list_models lists brand-mediator names (empty for single models). |
| `geo-kpi-rpk/list_models__default.json` | `/3/model_version` | key added: 'model_version' -- v0.4.0: list_models exposes the stage-file version token that now keys result caching and optimization reuse. |
| `geo-kpi-rpk/list_models__default.json` | `/4/funnel` | key added: 'funnel' -- v0.4.0: list_models reports each model's funnel type (single/full_funnel). |
| `geo-kpi-rpk/list_models__default.json` | `/4/mediators` | key added: 'mediators' -- v0.4.0: list_models lists brand-mediator names (empty for single models). |
| `geo-kpi-rpk/list_models__default.json` | `/4/model_version` | key added: 'model_version' -- v0.4.0: list_models exposes the stage-file version token that now keys result caching and optimization reuse. |
| `geo-kpi-rpk/list_models__default.json` | `/5/funnel` | key added: 'funnel' -- v0.4.0: list_models reports each model's funnel type (single/full_funnel). |
| `geo-kpi-rpk/list_models__default.json` | `/5/mediators` | key added: 'mediators' -- v0.4.0: list_models lists brand-mediator names (empty for single models). |
| `geo-kpi-rpk/list_models__default.json` | `/5/model_version` | key added: 'model_version' -- v0.4.0: list_models exposes the stage-file version token that now keys result caching and optimization reuse. |
| `geo-kpi-rpk/list_models__default.json` | `/6/funnel` | key added: 'funnel' -- v0.4.0: list_models reports each model's funnel type (single/full_funnel). |
| `geo-kpi-rpk/list_models__default.json` | `/6/mediators` | key added: 'mediators' -- v0.4.0: list_models lists brand-mediator names (empty for single models). |
| `geo-kpi-rpk/list_models__default.json` | `/6/model_version` | key added: 'model_version' -- v0.4.0: list_models exposes the stage-file version token that now keys result caching and optimization reuse. |
| `geo-revenue-media-only/get_model_overview__default.json` | `/funnel` | key added: 'funnel' -- v0.4.0: get_model_overview reports funnel='single' on single models. |
| `geo-revenue-media-only/list_models__default.json` | `/0/funnel` | key added: 'funnel' -- v0.4.0: list_models reports each model's funnel type (single/full_funnel). |
| `geo-revenue-media-only/list_models__default.json` | `/0/mediators` | key added: 'mediators' -- v0.4.0: list_models lists brand-mediator names (empty for single models). |
| `geo-revenue-media-only/list_models__default.json` | `/0/model_version` | key added: 'model_version' -- v0.4.0: list_models exposes the stage-file version token that now keys result caching and optimization reuse. |
| `geo-revenue-media-only/list_models__default.json` | `/1/funnel` | key added: 'funnel' -- v0.4.0: list_models reports each model's funnel type (single/full_funnel). |
| `geo-revenue-media-only/list_models__default.json` | `/1/mediators` | key added: 'mediators' -- v0.4.0: list_models lists brand-mediator names (empty for single models). |
| `geo-revenue-media-only/list_models__default.json` | `/1/model_version` | key added: 'model_version' -- v0.4.0: list_models exposes the stage-file version token that now keys result caching and optimization reuse. |
| `geo-revenue-media-only/list_models__default.json` | `/2/funnel` | key added: 'funnel' -- v0.4.0: list_models reports each model's funnel type (single/full_funnel). |
| `geo-revenue-media-only/list_models__default.json` | `/2/mediators` | key added: 'mediators' -- v0.4.0: list_models lists brand-mediator names (empty for single models). |
| `geo-revenue-media-only/list_models__default.json` | `/2/model_version` | key added: 'model_version' -- v0.4.0: list_models exposes the stage-file version token that now keys result caching and optimization reuse. |
| `geo-revenue-media-only/list_models__default.json` | `/3/funnel` | key added: 'funnel' -- v0.4.0: list_models reports each model's funnel type (single/full_funnel). |
| `geo-revenue-media-only/list_models__default.json` | `/3/mediators` | key added: 'mediators' -- v0.4.0: list_models lists brand-mediator names (empty for single models). |
| `geo-revenue-media-only/list_models__default.json` | `/3/model_version` | key added: 'model_version' -- v0.4.0: list_models exposes the stage-file version token that now keys result caching and optimization reuse. |
| `geo-revenue-media-only/list_models__default.json` | `/4/funnel` | key added: 'funnel' -- v0.4.0: list_models reports each model's funnel type (single/full_funnel). |
| `geo-revenue-media-only/list_models__default.json` | `/4/mediators` | key added: 'mediators' -- v0.4.0: list_models lists brand-mediator names (empty for single models). |
| `geo-revenue-media-only/list_models__default.json` | `/4/model_version` | key added: 'model_version' -- v0.4.0: list_models exposes the stage-file version token that now keys result caching and optimization reuse. |
| `geo-revenue-media-only/list_models__default.json` | `/5/funnel` | key added: 'funnel' -- v0.4.0: list_models reports each model's funnel type (single/full_funnel). |
| `geo-revenue-media-only/list_models__default.json` | `/5/mediators` | key added: 'mediators' -- v0.4.0: list_models lists brand-mediator names (empty for single models). |
| `geo-revenue-media-only/list_models__default.json` | `/5/model_version` | key added: 'model_version' -- v0.4.0: list_models exposes the stage-file version token that now keys result caching and optimization reuse. |
| `geo-revenue-media-only/list_models__default.json` | `/6/funnel` | key added: 'funnel' -- v0.4.0: list_models reports each model's funnel type (single/full_funnel). |
| `geo-revenue-media-only/list_models__default.json` | `/6/mediators` | key added: 'mediators' -- v0.4.0: list_models lists brand-mediator names (empty for single models). |
| `geo-revenue-media-only/list_models__default.json` | `/6/model_version` | key added: 'model_version' -- v0.4.0: list_models exposes the stage-file version token that now keys result caching and optimization reuse. |
| `geo-revenue/get_model_overview__default.json` | `/funnel` | key added: 'funnel' -- v0.4.0: get_model_overview reports funnel='single' on single models. |
| `geo-revenue/list_models__default.json` | `/0/funnel` | key added: 'funnel' -- v0.4.0: list_models reports each model's funnel type (single/full_funnel). |
| `geo-revenue/list_models__default.json` | `/0/mediators` | key added: 'mediators' -- v0.4.0: list_models lists brand-mediator names (empty for single models). |
| `geo-revenue/list_models__default.json` | `/0/model_version` | key added: 'model_version' -- v0.4.0: list_models exposes the stage-file version token that now keys result caching and optimization reuse. |
| `geo-revenue/list_models__default.json` | `/1/funnel` | key added: 'funnel' -- v0.4.0: list_models reports each model's funnel type (single/full_funnel). |
| `geo-revenue/list_models__default.json` | `/1/mediators` | key added: 'mediators' -- v0.4.0: list_models lists brand-mediator names (empty for single models). |
| `geo-revenue/list_models__default.json` | `/1/model_version` | key added: 'model_version' -- v0.4.0: list_models exposes the stage-file version token that now keys result caching and optimization reuse. |
| `geo-revenue/list_models__default.json` | `/2/funnel` | key added: 'funnel' -- v0.4.0: list_models reports each model's funnel type (single/full_funnel). |
| `geo-revenue/list_models__default.json` | `/2/mediators` | key added: 'mediators' -- v0.4.0: list_models lists brand-mediator names (empty for single models). |
| `geo-revenue/list_models__default.json` | `/2/model_version` | key added: 'model_version' -- v0.4.0: list_models exposes the stage-file version token that now keys result caching and optimization reuse. |
| `geo-revenue/list_models__default.json` | `/3/funnel` | key added: 'funnel' -- v0.4.0: list_models reports each model's funnel type (single/full_funnel). |
| `geo-revenue/list_models__default.json` | `/3/mediators` | key added: 'mediators' -- v0.4.0: list_models lists brand-mediator names (empty for single models). |
| `geo-revenue/list_models__default.json` | `/3/model_version` | key added: 'model_version' -- v0.4.0: list_models exposes the stage-file version token that now keys result caching and optimization reuse. |
| `geo-revenue/list_models__default.json` | `/4/funnel` | key added: 'funnel' -- v0.4.0: list_models reports each model's funnel type (single/full_funnel). |
| `geo-revenue/list_models__default.json` | `/4/mediators` | key added: 'mediators' -- v0.4.0: list_models lists brand-mediator names (empty for single models). |
| `geo-revenue/list_models__default.json` | `/4/model_version` | key added: 'model_version' -- v0.4.0: list_models exposes the stage-file version token that now keys result caching and optimization reuse. |
| `geo-revenue/list_models__default.json` | `/5/funnel` | key added: 'funnel' -- v0.4.0: list_models reports each model's funnel type (single/full_funnel). |
| `geo-revenue/list_models__default.json` | `/5/mediators` | key added: 'mediators' -- v0.4.0: list_models lists brand-mediator names (empty for single models). |
| `geo-revenue/list_models__default.json` | `/5/model_version` | key added: 'model_version' -- v0.4.0: list_models exposes the stage-file version token that now keys result caching and optimization reuse. |
| `geo-revenue/list_models__default.json` | `/6/funnel` | key added: 'funnel' -- v0.4.0: list_models reports each model's funnel type (single/full_funnel). |
| `geo-revenue/list_models__default.json` | `/6/mediators` | key added: 'mediators' -- v0.4.0: list_models lists brand-mediator names (empty for single models). |
| `geo-revenue/list_models__default.json` | `/6/model_version` | key added: 'model_version' -- v0.4.0: list_models exposes the stage-file version token that now keys result caching and optimization reuse. |
| `national-kpi-only/get_model_overview__default.json` | `/funnel` | key added: 'funnel' -- v0.4.0: get_model_overview reports funnel='single' on single models. |
| `national-kpi-only/list_models__default.json` | `/0/funnel` | key added: 'funnel' -- v0.4.0: list_models reports each model's funnel type (single/full_funnel). |
| `national-kpi-only/list_models__default.json` | `/0/mediators` | key added: 'mediators' -- v0.4.0: list_models lists brand-mediator names (empty for single models). |
| `national-kpi-only/list_models__default.json` | `/0/model_version` | key added: 'model_version' -- v0.4.0: list_models exposes the stage-file version token that now keys result caching and optimization reuse. |
| `national-kpi-only/list_models__default.json` | `/1/funnel` | key added: 'funnel' -- v0.4.0: list_models reports each model's funnel type (single/full_funnel). |
| `national-kpi-only/list_models__default.json` | `/1/mediators` | key added: 'mediators' -- v0.4.0: list_models lists brand-mediator names (empty for single models). |
| `national-kpi-only/list_models__default.json` | `/1/model_version` | key added: 'model_version' -- v0.4.0: list_models exposes the stage-file version token that now keys result caching and optimization reuse. |
| `national-kpi-only/list_models__default.json` | `/2/funnel` | key added: 'funnel' -- v0.4.0: list_models reports each model's funnel type (single/full_funnel). |
| `national-kpi-only/list_models__default.json` | `/2/mediators` | key added: 'mediators' -- v0.4.0: list_models lists brand-mediator names (empty for single models). |
| `national-kpi-only/list_models__default.json` | `/2/model_version` | key added: 'model_version' -- v0.4.0: list_models exposes the stage-file version token that now keys result caching and optimization reuse. |
| `national-kpi-only/list_models__default.json` | `/3/funnel` | key added: 'funnel' -- v0.4.0: list_models reports each model's funnel type (single/full_funnel). |
| `national-kpi-only/list_models__default.json` | `/3/mediators` | key added: 'mediators' -- v0.4.0: list_models lists brand-mediator names (empty for single models). |
| `national-kpi-only/list_models__default.json` | `/3/model_version` | key added: 'model_version' -- v0.4.0: list_models exposes the stage-file version token that now keys result caching and optimization reuse. |
| `national-kpi-only/list_models__default.json` | `/4/funnel` | key added: 'funnel' -- v0.4.0: list_models reports each model's funnel type (single/full_funnel). |
| `national-kpi-only/list_models__default.json` | `/4/mediators` | key added: 'mediators' -- v0.4.0: list_models lists brand-mediator names (empty for single models). |
| `national-kpi-only/list_models__default.json` | `/4/model_version` | key added: 'model_version' -- v0.4.0: list_models exposes the stage-file version token that now keys result caching and optimization reuse. |
| `national-kpi-only/list_models__default.json` | `/5/funnel` | key added: 'funnel' -- v0.4.0: list_models reports each model's funnel type (single/full_funnel). |
| `national-kpi-only/list_models__default.json` | `/5/mediators` | key added: 'mediators' -- v0.4.0: list_models lists brand-mediator names (empty for single models). |
| `national-kpi-only/list_models__default.json` | `/5/model_version` | key added: 'model_version' -- v0.4.0: list_models exposes the stage-file version token that now keys result caching and optimization reuse. |
| `national-kpi-only/list_models__default.json` | `/6/funnel` | key added: 'funnel' -- v0.4.0: list_models reports each model's funnel type (single/full_funnel). |
| `national-kpi-only/list_models__default.json` | `/6/mediators` | key added: 'mediators' -- v0.4.0: list_models lists brand-mediator names (empty for single models). |
| `national-kpi-only/list_models__default.json` | `/6/model_version` | key added: 'model_version' -- v0.4.0: list_models exposes the stage-file version token that now keys result caching and optimization reuse. |
| `national-kpi-rpk/get_model_overview__default.json` | `/funnel` | key added: 'funnel' -- v0.4.0: get_model_overview reports funnel='single' on single models. |
| `national-kpi-rpk/list_models__default.json` | `/0/funnel` | key added: 'funnel' -- v0.4.0: list_models reports each model's funnel type (single/full_funnel). |
| `national-kpi-rpk/list_models__default.json` | `/0/mediators` | key added: 'mediators' -- v0.4.0: list_models lists brand-mediator names (empty for single models). |
| `national-kpi-rpk/list_models__default.json` | `/0/model_version` | key added: 'model_version' -- v0.4.0: list_models exposes the stage-file version token that now keys result caching and optimization reuse. |
| `national-kpi-rpk/list_models__default.json` | `/1/funnel` | key added: 'funnel' -- v0.4.0: list_models reports each model's funnel type (single/full_funnel). |
| `national-kpi-rpk/list_models__default.json` | `/1/mediators` | key added: 'mediators' -- v0.4.0: list_models lists brand-mediator names (empty for single models). |
| `national-kpi-rpk/list_models__default.json` | `/1/model_version` | key added: 'model_version' -- v0.4.0: list_models exposes the stage-file version token that now keys result caching and optimization reuse. |
| `national-kpi-rpk/list_models__default.json` | `/2/funnel` | key added: 'funnel' -- v0.4.0: list_models reports each model's funnel type (single/full_funnel). |
| `national-kpi-rpk/list_models__default.json` | `/2/mediators` | key added: 'mediators' -- v0.4.0: list_models lists brand-mediator names (empty for single models). |
| `national-kpi-rpk/list_models__default.json` | `/2/model_version` | key added: 'model_version' -- v0.4.0: list_models exposes the stage-file version token that now keys result caching and optimization reuse. |
| `national-kpi-rpk/list_models__default.json` | `/3/funnel` | key added: 'funnel' -- v0.4.0: list_models reports each model's funnel type (single/full_funnel). |
| `national-kpi-rpk/list_models__default.json` | `/3/mediators` | key added: 'mediators' -- v0.4.0: list_models lists brand-mediator names (empty for single models). |
| `national-kpi-rpk/list_models__default.json` | `/3/model_version` | key added: 'model_version' -- v0.4.0: list_models exposes the stage-file version token that now keys result caching and optimization reuse. |
| `national-kpi-rpk/list_models__default.json` | `/4/funnel` | key added: 'funnel' -- v0.4.0: list_models reports each model's funnel type (single/full_funnel). |
| `national-kpi-rpk/list_models__default.json` | `/4/mediators` | key added: 'mediators' -- v0.4.0: list_models lists brand-mediator names (empty for single models). |
| `national-kpi-rpk/list_models__default.json` | `/4/model_version` | key added: 'model_version' -- v0.4.0: list_models exposes the stage-file version token that now keys result caching and optimization reuse. |
| `national-kpi-rpk/list_models__default.json` | `/5/funnel` | key added: 'funnel' -- v0.4.0: list_models reports each model's funnel type (single/full_funnel). |
| `national-kpi-rpk/list_models__default.json` | `/5/mediators` | key added: 'mediators' -- v0.4.0: list_models lists brand-mediator names (empty for single models). |
| `national-kpi-rpk/list_models__default.json` | `/5/model_version` | key added: 'model_version' -- v0.4.0: list_models exposes the stage-file version token that now keys result caching and optimization reuse. |
| `national-kpi-rpk/list_models__default.json` | `/6/funnel` | key added: 'funnel' -- v0.4.0: list_models reports each model's funnel type (single/full_funnel). |
| `national-kpi-rpk/list_models__default.json` | `/6/mediators` | key added: 'mediators' -- v0.4.0: list_models lists brand-mediator names (empty for single models). |
| `national-kpi-rpk/list_models__default.json` | `/6/model_version` | key added: 'model_version' -- v0.4.0: list_models exposes the stage-file version token that now keys result caching and optimization reuse. |
| `national-revenue/get_model_overview__default.json` | `/funnel` | key added: 'funnel' -- v0.4.0: get_model_overview reports funnel='single' on single models. |
| `national-revenue/list_models__default.json` | `/0/funnel` | key added: 'funnel' -- v0.4.0: list_models reports each model's funnel type (single/full_funnel). |
| `national-revenue/list_models__default.json` | `/0/mediators` | key added: 'mediators' -- v0.4.0: list_models lists brand-mediator names (empty for single models). |
| `national-revenue/list_models__default.json` | `/0/model_version` | key added: 'model_version' -- v0.4.0: list_models exposes the stage-file version token that now keys result caching and optimization reuse. |
| `national-revenue/list_models__default.json` | `/1/funnel` | key added: 'funnel' -- v0.4.0: list_models reports each model's funnel type (single/full_funnel). |
| `national-revenue/list_models__default.json` | `/1/mediators` | key added: 'mediators' -- v0.4.0: list_models lists brand-mediator names (empty for single models). |
| `national-revenue/list_models__default.json` | `/1/model_version` | key added: 'model_version' -- v0.4.0: list_models exposes the stage-file version token that now keys result caching and optimization reuse. |
| `national-revenue/list_models__default.json` | `/2/funnel` | key added: 'funnel' -- v0.4.0: list_models reports each model's funnel type (single/full_funnel). |
| `national-revenue/list_models__default.json` | `/2/mediators` | key added: 'mediators' -- v0.4.0: list_models lists brand-mediator names (empty for single models). |
| `national-revenue/list_models__default.json` | `/2/model_version` | key added: 'model_version' -- v0.4.0: list_models exposes the stage-file version token that now keys result caching and optimization reuse. |
| `national-revenue/list_models__default.json` | `/3/funnel` | key added: 'funnel' -- v0.4.0: list_models reports each model's funnel type (single/full_funnel). |
| `national-revenue/list_models__default.json` | `/3/mediators` | key added: 'mediators' -- v0.4.0: list_models lists brand-mediator names (empty for single models). |
| `national-revenue/list_models__default.json` | `/3/model_version` | key added: 'model_version' -- v0.4.0: list_models exposes the stage-file version token that now keys result caching and optimization reuse. |
| `national-revenue/list_models__default.json` | `/4/funnel` | key added: 'funnel' -- v0.4.0: list_models reports each model's funnel type (single/full_funnel). |
| `national-revenue/list_models__default.json` | `/4/mediators` | key added: 'mediators' -- v0.4.0: list_models lists brand-mediator names (empty for single models). |
| `national-revenue/list_models__default.json` | `/4/model_version` | key added: 'model_version' -- v0.4.0: list_models exposes the stage-file version token that now keys result caching and optimization reuse. |
| `national-revenue/list_models__default.json` | `/5/funnel` | key added: 'funnel' -- v0.4.0: list_models reports each model's funnel type (single/full_funnel). |
| `national-revenue/list_models__default.json` | `/5/mediators` | key added: 'mediators' -- v0.4.0: list_models lists brand-mediator names (empty for single models). |
| `national-revenue/list_models__default.json` | `/5/model_version` | key added: 'model_version' -- v0.4.0: list_models exposes the stage-file version token that now keys result caching and optimization reuse. |
| `national-revenue/list_models__default.json` | `/6/funnel` | key added: 'funnel' -- v0.4.0: list_models reports each model's funnel type (single/full_funnel). |
| `national-revenue/list_models__default.json` | `/6/mediators` | key added: 'mediators' -- v0.4.0: list_models lists brand-mediator names (empty for single models). |
| `national-revenue/list_models__default.json` | `/6/model_version` | key added: 'model_version' -- v0.4.0: list_models exposes the stage-file version token that now keys result caching and optimization reuse. |

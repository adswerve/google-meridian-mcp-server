# Drift report: `v1.7-engine` -> `v2.0-tf`

**Verdict: PASS -- exit code 0** (nothing to report)

## Legend

- **FAIL** and **REVIEW** both block (exit code 1): FAIL is a structural break (added/removed/type-changed field, changed identity, or a case missing from one label) with no ambiguity; REVIEW is a numeric or ordering finding that needs a human to judge (over tolerance, sign flip, known racy field, broken CI ordering) and MIGHT be legitimate.
- **ACKNOWLEDGED** does not block: pre-registered in `acknowledged.py` with a mandatory reason (spec 7.4). Reported in its own section, never folded into a clean PASS.

## Environment

| Item | v1.7-engine | v2.0-tf |
| --- | --- | --- |
| transport | inprocess | inprocess |
| Python | 3.13.13 | 3.13.13 |
| fastmcp | 4.0.3 | 4.0.3 |
| google-meridian | 1.7.0 | 2.0.0 |
| jax | None | 0.11.1 |
| jaxlib | None | 0.11.1 |
| pydantic | 2.13.5 | 2.13.5 |
| tensorflow | 2.20.0 | 2.21.0 |
| tensorflow-probability | None | None |
| tfp-nightly | 0.26.0.dev20260130 | 0.26.0.dev20260130 |
| worker MERIDIAN_BACKEND | None | None |
| worker MERIDIAN_ENABLE_JAX_X64 | None | None |
| worker TF_CPP_MIN_LOG_LEVEL | 3 | 3 |
| relative tolerance (REL_TOLERANCE) | 1e-03 | 1e-03 |
| absolute floor (ABS_FLOOR) | 1e-09 | 1e-09 |

## Fixture provenance

**v1.7-engine**

- `geo-kpi-only`: trained TENSORFLOW/FLOAT32 (v1.7.0) vs current TENSORFLOW/FLOAT32 -- backend mismatch: False, precision mismatch: False
- `geo-kpi-rpk`: trained TENSORFLOW/FLOAT32 (v1.7.0) vs current TENSORFLOW/FLOAT32 -- backend mismatch: False, precision mismatch: False
- `geo-revenue`: trained TENSORFLOW/FLOAT32 (v1.7.0) vs current TENSORFLOW/FLOAT32 -- backend mismatch: False, precision mismatch: False
- `geo-revenue-media-only`: trained TENSORFLOW/FLOAT32 (v1.7.0) vs current TENSORFLOW/FLOAT32 -- backend mismatch: False, precision mismatch: False
- `national-kpi-only`: trained TENSORFLOW/FLOAT32 (v1.7.0) vs current TENSORFLOW/FLOAT32 -- backend mismatch: False, precision mismatch: False
- `national-kpi-rpk`: trained TENSORFLOW/FLOAT32 (v1.7.0) vs current TENSORFLOW/FLOAT32 -- backend mismatch: False, precision mismatch: False
- `national-revenue`: trained TENSORFLOW/FLOAT32 (v1.7.0) vs current TENSORFLOW/FLOAT32 -- backend mismatch: False, precision mismatch: False

**v2.0-tf**

- `geo-kpi-only`: trained TENSORFLOW/FLOAT32 (v1.7.0) vs current JAX/FLOAT64 -- backend mismatch: True, precision mismatch: True
- `geo-kpi-rpk`: trained TENSORFLOW/FLOAT32 (v1.7.0) vs current JAX/FLOAT64 -- backend mismatch: True, precision mismatch: True
- `geo-revenue`: trained TENSORFLOW/FLOAT32 (v1.7.0) vs current JAX/FLOAT64 -- backend mismatch: True, precision mismatch: True
- `geo-revenue-media-only`: trained TENSORFLOW/FLOAT32 (v1.7.0) vs current JAX/FLOAT64 -- backend mismatch: True, precision mismatch: True
- `national-kpi-only`: trained TENSORFLOW/FLOAT32 (v1.7.0) vs current JAX/FLOAT64 -- backend mismatch: True, precision mismatch: True
- `national-kpi-rpk`: trained TENSORFLOW/FLOAT32 (v1.7.0) vs current JAX/FLOAT64 -- backend mismatch: True, precision mismatch: True
- `national-revenue`: trained TENSORFLOW/FLOAT32 (v1.7.0) vs current JAX/FLOAT64 -- backend mismatch: True, precision mismatch: True

## Summary

- Cases compared: 330 (330 clean -- no findings in any section)
- Case-set differences (case present in only one label): 0
- FAIL (including case-set differences above): 0
- REVIEW: 0
- ACKNOWLEDGED: 0

## FAIL - structural

_none_

## REVIEW -- needs a human

_none_

## ACKNOWLEDGED - pre-registered in acknowledged.py

_none_

---

## Measured drift distribution (added after the final whole-branch review)

The final review made a fair methodological point: `REL_TOLERANCE = 1e-3` sits
well above the payloads' own quantization (`optimizer_facade` rounds to 6
significant figures, ~5e-7 relative), so a clean PASS establishes "no leaf
moved more than 0.1%", **not** "nothing changed". Rather than argue the point,
the drift was measured directly, ignoring the tolerance entirely:

```
files compared            : 330
payload-identical         : 300  (90.9%)
numeric leaves compared   : 16452
numeric leaves DIFFERING  :   838
non-numeric differences   :     0
relative delta   p50=1.807e-06   p90=6.171e-06   p99=9.941e-06   max=1.267e-04
leaves exceeding 1e-05    :     8
leaves exceeding 1e-04    :     3
leaves exceeding 1e-03    :     0
```

**What this supports.** Upgrading Meridian 1.7 -> 2.0 on the TensorFlow
backend produced **no structural change at all** (zero non-numeric
differences across 16,452 numeric leaves), left **91% of payloads
byte-identical**, and where numbers moved they moved by at most **1.27e-4
(0.013%)** with a median of 1.8e-6.

**What it does not support.** The earlier phrasing "byte-identical output
across all 330 tool cases" was too strong and is corrected here: 300 of 330
were byte-identical; the remaining 30 differ only in low-order float digits.

The tolerance was not concealing anything meaningful — the maximum observed
drift is an order of magnitude below the 1e-3 threshold. That is now measured
rather than assumed, which is the stronger claim.

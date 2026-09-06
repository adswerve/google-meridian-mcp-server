> **Scope note.** This is diff #2 re-run across only the SUPPORTED surface — the seven
> `.binpb` fixtures. The full run (`02-backend-precision.md`) also covered
> `national-revenue-pkl` and produced 237 FAILs, every one of them on that fixture and all
> from a single root cause: Meridian 2.0 on JAX cannot run inference on a pickle saved under
> TensorFlow. `.pkl` support was consequently removed (see `../pkl-format-removed.md`), so
> this report describes the surface the server actually serves.
>
> Labels are copies of `v2.0-tf` / `v2.0-jax` with the pickle fixture excluded; payloads are
> untouched.

# Drift report: `v2.0-tf-sup` -> `v2.0-jax-sup`

**Verdict: FAIL -- exit code 1** (7 REVIEW, 112 ACKNOWLEDGED)

## Legend

- **FAIL** and **REVIEW** both block (exit code 1): FAIL is a structural break (added/removed/type-changed field, changed identity, or a case missing from one label) with no ambiguity; REVIEW is a numeric or ordering finding that needs a human to judge (over tolerance, sign flip, known racy field, broken CI ordering) and MIGHT be legitimate.
- **ACKNOWLEDGED** does not block: pre-registered in `acknowledged.py` with a mandatory reason (spec 7.4). Reported in its own section, never folded into a clean PASS.

## Environment

| Item | v2.0-tf-sup | v2.0-jax-sup |
| --- | --- | --- |
| transport | inprocess | inprocess |
| Python | 3.13.13 | 3.13.13 |
| fastmcp | 4.0.3 | 4.0.3 |
| google-meridian | 2.0.0 | 2.0.0 |
| jax | 0.11.1 | 0.11.1 |
| jaxlib | 0.11.1 | 0.11.1 |
| pydantic | 2.13.5 | 2.13.5 |
| tensorflow | 2.21.0 | 2.21.0 |
| tensorflow-probability | None | None |
| tfp-nightly | 0.26.0.dev20260130 | 0.26.0.dev20260130 |
| worker MERIDIAN_BACKEND | None | jax |
| worker MERIDIAN_ENABLE_JAX_X64 | None | true |
| worker TF_CPP_MIN_LOG_LEVEL | 3 | 3 |
| relative tolerance (REL_TOLERANCE) | 1e-03 | 1e-03 |
| absolute floor (ABS_FLOOR) | 1e-09 | 1e-09 |

## Fixture provenance

**v2.0-tf-sup**

- `geo-kpi-only`: trained TENSORFLOW/FLOAT32 (v1.7.0) vs current TENSORFLOW/FLOAT32 -- backend mismatch: False, precision mismatch: False
- `geo-kpi-rpk`: trained TENSORFLOW/FLOAT32 (v1.7.0) vs current TENSORFLOW/FLOAT32 -- backend mismatch: False, precision mismatch: False
- `geo-revenue`: trained TENSORFLOW/FLOAT32 (v1.7.0) vs current TENSORFLOW/FLOAT32 -- backend mismatch: False, precision mismatch: False
- `geo-revenue-media-only`: trained TENSORFLOW/FLOAT32 (v1.7.0) vs current TENSORFLOW/FLOAT32 -- backend mismatch: False, precision mismatch: False
- `national-kpi-only`: trained TENSORFLOW/FLOAT32 (v1.7.0) vs current TENSORFLOW/FLOAT32 -- backend mismatch: False, precision mismatch: False
- `national-kpi-rpk`: trained TENSORFLOW/FLOAT32 (v1.7.0) vs current TENSORFLOW/FLOAT32 -- backend mismatch: False, precision mismatch: False
- `national-revenue`: trained TENSORFLOW/FLOAT32 (v1.7.0) vs current TENSORFLOW/FLOAT32 -- backend mismatch: False, precision mismatch: False

**v2.0-jax-sup**

- `geo-kpi-only`: trained TENSORFLOW/FLOAT32 (v1.7.0) vs current JAX/FLOAT64 -- backend mismatch: True, precision mismatch: True
- `geo-kpi-rpk`: trained TENSORFLOW/FLOAT32 (v1.7.0) vs current JAX/FLOAT64 -- backend mismatch: True, precision mismatch: True
- `geo-revenue`: trained TENSORFLOW/FLOAT32 (v1.7.0) vs current JAX/FLOAT64 -- backend mismatch: True, precision mismatch: True
- `geo-revenue-media-only`: trained TENSORFLOW/FLOAT32 (v1.7.0) vs current JAX/FLOAT64 -- backend mismatch: True, precision mismatch: True
- `national-kpi-only`: trained TENSORFLOW/FLOAT32 (v1.7.0) vs current JAX/FLOAT64 -- backend mismatch: True, precision mismatch: True
- `national-kpi-rpk`: trained TENSORFLOW/FLOAT32 (v1.7.0) vs current JAX/FLOAT64 -- backend mismatch: True, precision mismatch: True
- `national-revenue`: trained TENSORFLOW/FLOAT32 (v1.7.0) vs current JAX/FLOAT64 -- backend mismatch: True, precision mismatch: True

## Summary

- Cases compared: 293 (263 clean -- no findings in any section)
- Case-set differences (case present in only one label): 0
- FAIL (including case-set differences above): 0
- REVIEW: 7
- ACKNOWLEDGED: 112

## Per-case environment provenance (for cases with findings)

- v2.0-tf-sup vs v2.0-jax-sup capture_env differs the same way for every case below: src_hash: `b3854dbfd1a571aaff65c0258119010da8ed630a740e9b70a19299be4a758f39` vs `8cf4a6d36339ee8e66e9d3454a92d0005914db33b14a0176d5fb7573697f37bd`

## FAIL - structural

_none_

## REVIEW -- needs a human

| Case | Pointer | Reason | Detail |
| --- | --- | --- | --- |
| `geo-revenue/run_optimization__geo_subset.json` | `/result/response_curves/201/incremental_outcome` | over tolerance | relative delta 1.092e-03 > 1e-03: 0.0236069 -> 0.0236327 |
| `national-kpi-only/get_contribution__contribution_metrics_by_time.json` | `/rows/134/2` | over tolerance | relative delta 1.000e+00 > 1e-03: -6.1281e-07 -> -1.38084e-15 |
| `national-kpi-only/get_contribution__contribution_metrics_by_time.json` | `/rows/134/3` | over tolerance | relative delta 1.000e+00 > 1e-03: -2.49936e-08 -> -5.63178e-17 |
| `national-kpi-rpk/get_contribution__contribution_metrics_by_time.json` | `/rows/134/2` | over tolerance | relative delta 1.000e+00 > 1e-03: -1.92422e-06 -> -4.33584e-15 |
| `national-kpi-rpk/get_contribution__contribution_metrics_by_time.json` | `/rows/134/3` | over tolerance | relative delta 1.000e+00 > 1e-03: -2.63509e-08 -> -5.93762e-17 |
| `national-revenue/get_contribution__contribution_metrics_by_time.json` | `/rows/134/2` | over tolerance | relative delta 1.000e+00 > 1e-03: -6.1281e-07 -> -1.38084e-15 |
| `national-revenue/get_contribution__contribution_metrics_by_time.json` | `/rows/134/3` | over tolerance | relative delta 1.000e+00 > 1e-03: -2.49891e-08 -> -5.63076e-17 |

## ACKNOWLEDGED - pre-registered in acknowledged.py

| Case | Pointer | Detail |
| --- | --- | --- |
| `geo-revenue/lifecycle__cancel.json` | `/status/backend` | key removed: 'backend' -- Spec 6.2/6.3: the backend knob is removed in Phase 4. JAX is the only supported backend, so a per-run `backend` field carries no information. |
| `geo-revenue/lifecycle__cancel.json` | `/status/meridian_version` | key added: 'meridian_version' -- Spec 6.3: replaces `backend` as agent-visible provenance. It was already written to the run manifest and returned by nothing, so removing `backend` alone would have been a net LOSS of provenance. |
| `geo-revenue/lifecycle__cancel.json` | `/submit/backend` | key removed: 'backend' -- Spec 6.2/6.3: the backend knob is removed in Phase 4. JAX is the only supported backend, so a per-run `backend` field carries no information. |
| `geo-revenue/lifecycle__cancel.json` | `/submit/meridian_version` | key added: 'meridian_version' -- Spec 6.3: replaces `backend` as agent-visible provenance. It was already written to the run manifest and returned by nothing, so removing `backend` alone would have been a net LOSS of provenance. |
| `geo-revenue/lifecycle__status_result_reuse_delete.json` | `/reused/backend` | key removed: 'backend' -- Spec 6.2/6.3: the backend knob is removed in Phase 4. JAX is the only supported backend, so a per-run `backend` field carries no information. |
| `geo-revenue/lifecycle__status_result_reuse_delete.json` | `/reused/meridian_version` | key added: 'meridian_version' -- Spec 6.3: replaces `backend` as agent-visible provenance. It was already written to the run manifest and returned by nothing, so removing `backend` alone would have been a net LOSS of provenance. |
| `geo-revenue/lifecycle__status_result_reuse_delete.json` | `/status/backend` | key removed: 'backend' -- Spec 6.2/6.3: the backend knob is removed in Phase 4. JAX is the only supported backend, so a per-run `backend` field carries no information. |
| `geo-revenue/lifecycle__status_result_reuse_delete.json` | `/status/meridian_version` | key added: 'meridian_version' -- Spec 6.3: replaces `backend` as agent-visible provenance. It was already written to the run manifest and returned by nothing, so removing `backend` alone would have been a net LOSS of provenance. |
| `geo-revenue/lifecycle__status_result_reuse_delete.json` | `/submit/backend` | key removed: 'backend' -- Spec 6.2/6.3: the backend knob is removed in Phase 4. JAX is the only supported backend, so a per-run `backend` field carries no information. |
| `geo-revenue/lifecycle__status_result_reuse_delete.json` | `/submit/meridian_version` | key added: 'meridian_version' -- Spec 6.3: replaces `backend` as agent-visible provenance. It was already written to the run manifest and returned by nothing, so removing `backend` alone would have been a net LOSS of provenance. |
| `geo-revenue/run_future_optimization__cost_multipliers.json` | `/status/backend` | key removed: 'backend' -- Spec 6.2/6.3: the backend knob is removed in Phase 4. JAX is the only supported backend, so a per-run `backend` field carries no information. |
| `geo-revenue/run_future_optimization__cost_multipliers.json` | `/status/meridian_version` | key added: 'meridian_version' -- Spec 6.3: replaces `backend` as agent-visible provenance. It was already written to the run manifest and returned by nothing, so removing `backend` alone would have been a net LOSS of provenance. |
| `geo-revenue/run_future_optimization__cost_multipliers.json` | `/submit/backend` | key removed: 'backend' -- Spec 6.2/6.3: the backend knob is removed in Phase 4. JAX is the only supported backend, so a per-run `backend` field carries no information. |
| `geo-revenue/run_future_optimization__cost_multipliers.json` | `/submit/meridian_version` | key added: 'meridian_version' -- Spec 6.3: replaces `backend` as agent-visible provenance. It was already written to the run manifest and returned by nothing, so removing `backend` alone would have been a net LOSS of provenance. |
| `geo-revenue/run_future_optimization__planned_allocation_long_horizon.json` | `/status/backend` | key removed: 'backend' -- Spec 6.2/6.3: the backend knob is removed in Phase 4. JAX is the only supported backend, so a per-run `backend` field carries no information. |
| `geo-revenue/run_future_optimization__planned_allocation_long_horizon.json` | `/status/meridian_version` | key added: 'meridian_version' -- Spec 6.3: replaces `backend` as agent-visible provenance. It was already written to the run manifest and returned by nothing, so removing `backend` alone would have been a net LOSS of provenance. |
| `geo-revenue/run_future_optimization__planned_allocation_long_horizon.json` | `/submit/backend` | key removed: 'backend' -- Spec 6.2/6.3: the backend knob is removed in Phase 4. JAX is the only supported backend, so a per-run `backend` field carries no information. |
| `geo-revenue/run_future_optimization__planned_allocation_long_horizon.json` | `/submit/meridian_version` | key added: 'meridian_version' -- Spec 6.3: replaces `backend` as agent-visible provenance. It was already written to the run manifest and returned by nothing, so removing `backend` alone would have been a net LOSS of provenance. |
| `geo-revenue/run_future_optimization__reference_full_history_average.json` | `/status/backend` | key removed: 'backend' -- Spec 6.2/6.3: the backend knob is removed in Phase 4. JAX is the only supported backend, so a per-run `backend` field carries no information. |
| `geo-revenue/run_future_optimization__reference_full_history_average.json` | `/status/meridian_version` | key added: 'meridian_version' -- Spec 6.3: replaces `backend` as agent-visible provenance. It was already written to the run manifest and returned by nothing, so removing `backend` alone would have been a net LOSS of provenance. |
| `geo-revenue/run_future_optimization__reference_full_history_average.json` | `/submit/backend` | key removed: 'backend' -- Spec 6.2/6.3: the backend knob is removed in Phase 4. JAX is the only supported backend, so a per-run `backend` field carries no information. |
| `geo-revenue/run_future_optimization__reference_full_history_average.json` | `/submit/meridian_version` | key added: 'meridian_version' -- Spec 6.3: replaces `backend` as agent-visible provenance. It was already written to the run manifest and returned by nothing, so removing `backend` alone would have been a net LOSS of provenance. |
| `geo-revenue/run_future_optimization__reference_same_period_last_year.json` | `/status/backend` | key removed: 'backend' -- Spec 6.2/6.3: the backend knob is removed in Phase 4. JAX is the only supported backend, so a per-run `backend` field carries no information. |
| `geo-revenue/run_future_optimization__reference_same_period_last_year.json` | `/status/meridian_version` | key added: 'meridian_version' -- Spec 6.3: replaces `backend` as agent-visible provenance. It was already written to the run manifest and returned by nothing, so removing `backend` alone would have been a net LOSS of provenance. |
| `geo-revenue/run_future_optimization__reference_same_period_last_year.json` | `/submit/backend` | key removed: 'backend' -- Spec 6.2/6.3: the backend knob is removed in Phase 4. JAX is the only supported backend, so a per-run `backend` field carries no information. |
| `geo-revenue/run_future_optimization__reference_same_period_last_year.json` | `/submit/meridian_version` | key added: 'meridian_version' -- Spec 6.3: replaces `backend` as agent-visible provenance. It was already written to the run manifest and returned by nothing, so removing `backend` alone would have been a net LOSS of provenance. |
| `geo-revenue/run_future_optimization__reference_trailing.json` | `/status/backend` | key removed: 'backend' -- Spec 6.2/6.3: the backend knob is removed in Phase 4. JAX is the only supported backend, so a per-run `backend` field carries no information. |
| `geo-revenue/run_future_optimization__reference_trailing.json` | `/status/meridian_version` | key added: 'meridian_version' -- Spec 6.3: replaces `backend` as agent-visible provenance. It was already written to the run manifest and returned by nothing, so removing `backend` alone would have been a net LOSS of provenance. |
| `geo-revenue/run_future_optimization__reference_trailing.json` | `/submit/backend` | key removed: 'backend' -- Spec 6.2/6.3: the backend knob is removed in Phase 4. JAX is the only supported backend, so a per-run `backend` field carries no information. |
| `geo-revenue/run_future_optimization__reference_trailing.json` | `/submit/meridian_version` | key added: 'meridian_version' -- Spec 6.3: replaces `backend` as agent-visible provenance. It was already written to the run manifest and returned by nothing, so removing `backend` alone would have been a net LOSS of provenance. |
| `geo-revenue/run_future_optimization__revenue_per_kpi_multiplier.json` | `/status/backend` | key removed: 'backend' -- Spec 6.2/6.3: the backend knob is removed in Phase 4. JAX is the only supported backend, so a per-run `backend` field carries no information. |
| `geo-revenue/run_future_optimization__revenue_per_kpi_multiplier.json` | `/status/meridian_version` | key added: 'meridian_version' -- Spec 6.3: replaces `backend` as agent-visible provenance. It was already written to the run manifest and returned by nothing, so removing `backend` alone would have been a net LOSS of provenance. |
| `geo-revenue/run_future_optimization__revenue_per_kpi_multiplier.json` | `/submit/backend` | key removed: 'backend' -- Spec 6.2/6.3: the backend knob is removed in Phase 4. JAX is the only supported backend, so a per-run `backend` field carries no information. |
| `geo-revenue/run_future_optimization__revenue_per_kpi_multiplier.json` | `/submit/meridian_version` | key added: 'meridian_version' -- Spec 6.3: replaces `backend` as agent-visible provenance. It was already written to the run manifest and returned by nothing, so removing `backend` alone would have been a net LOSS of provenance. |
| `geo-revenue/run_optimization__date_window.json` | `/status/backend` | key removed: 'backend' -- Spec 6.2/6.3: the backend knob is removed in Phase 4. JAX is the only supported backend, so a per-run `backend` field carries no information. |
| `geo-revenue/run_optimization__date_window.json` | `/status/meridian_version` | key added: 'meridian_version' -- Spec 6.3: replaces `backend` as agent-visible provenance. It was already written to the run manifest and returned by nothing, so removing `backend` alone would have been a net LOSS of provenance. |
| `geo-revenue/run_optimization__date_window.json` | `/submit/backend` | key removed: 'backend' -- Spec 6.2/6.3: the backend knob is removed in Phase 4. JAX is the only supported backend, so a per-run `backend` field carries no information. |
| `geo-revenue/run_optimization__date_window.json` | `/submit/meridian_version` | key added: 'meridian_version' -- Spec 6.3: replaces `backend` as agent-visible provenance. It was already written to the run manifest and returned by nothing, so removing `backend` alone would have been a net LOSS of provenance. |
| `geo-revenue/run_optimization__fixed_budget.json` | `/status/backend` | key removed: 'backend' -- Spec 6.2/6.3: the backend knob is removed in Phase 4. JAX is the only supported backend, so a per-run `backend` field carries no information. |
| `geo-revenue/run_optimization__fixed_budget.json` | `/status/meridian_version` | key added: 'meridian_version' -- Spec 6.3: replaces `backend` as agent-visible provenance. It was already written to the run manifest and returned by nothing, so removing `backend` alone would have been a net LOSS of provenance. |
| `geo-revenue/run_optimization__fixed_budget.json` | `/submit/backend` | key removed: 'backend' -- Spec 6.2/6.3: the backend knob is removed in Phase 4. JAX is the only supported backend, so a per-run `backend` field carries no information. |
| `geo-revenue/run_optimization__fixed_budget.json` | `/submit/meridian_version` | key added: 'meridian_version' -- Spec 6.3: replaces `backend` as agent-visible provenance. It was already written to the run manifest and returned by nothing, so removing `backend` alone would have been a net LOSS of provenance. |
| `geo-revenue/run_optimization__geo_subset.json` | `/status/backend` | key removed: 'backend' -- Spec 6.2/6.3: the backend knob is removed in Phase 4. JAX is the only supported backend, so a per-run `backend` field carries no information. |
| `geo-revenue/run_optimization__geo_subset.json` | `/status/meridian_version` | key added: 'meridian_version' -- Spec 6.3: replaces `backend` as agent-visible provenance. It was already written to the run manifest and returned by nothing, so removing `backend` alone would have been a net LOSS of provenance. |
| `geo-revenue/run_optimization__geo_subset.json` | `/submit/backend` | key removed: 'backend' -- Spec 6.2/6.3: the backend knob is removed in Phase 4. JAX is the only supported backend, so a per-run `backend` field carries no information. |
| `geo-revenue/run_optimization__geo_subset.json` | `/submit/meridian_version` | key added: 'meridian_version' -- Spec 6.3: replaces `backend` as agent-visible provenance. It was already written to the run manifest and returned by nothing, so removing `backend` alone would have been a net LOSS of provenance. |
| `geo-revenue/run_optimization__spend_constraints.json` | `/status/backend` | key removed: 'backend' -- Spec 6.2/6.3: the backend knob is removed in Phase 4. JAX is the only supported backend, so a per-run `backend` field carries no information. |
| `geo-revenue/run_optimization__spend_constraints.json` | `/status/meridian_version` | key added: 'meridian_version' -- Spec 6.3: replaces `backend` as agent-visible provenance. It was already written to the run manifest and returned by nothing, so removing `backend` alone would have been a net LOSS of provenance. |
| `geo-revenue/run_optimization__spend_constraints.json` | `/submit/backend` | key removed: 'backend' -- Spec 6.2/6.3: the backend knob is removed in Phase 4. JAX is the only supported backend, so a per-run `backend` field carries no information. |
| `geo-revenue/run_optimization__spend_constraints.json` | `/submit/meridian_version` | key added: 'meridian_version' -- Spec 6.3: replaces `backend` as agent-visible provenance. It was already written to the run manifest and returned by nothing, so removing `backend` alone would have been a net LOSS of provenance. |
| `geo-revenue/run_optimization__target_mroas.json` | `/status/backend` | key removed: 'backend' -- Spec 6.2/6.3: the backend knob is removed in Phase 4. JAX is the only supported backend, so a per-run `backend` field carries no information. |
| `geo-revenue/run_optimization__target_mroas.json` | `/status/meridian_version` | key added: 'meridian_version' -- Spec 6.3: replaces `backend` as agent-visible provenance. It was already written to the run manifest and returned by nothing, so removing `backend` alone would have been a net LOSS of provenance. |
| `geo-revenue/run_optimization__target_mroas.json` | `/submit/backend` | key removed: 'backend' -- Spec 6.2/6.3: the backend knob is removed in Phase 4. JAX is the only supported backend, so a per-run `backend` field carries no information. |
| `geo-revenue/run_optimization__target_mroas.json` | `/submit/meridian_version` | key added: 'meridian_version' -- Spec 6.3: replaces `backend` as agent-visible provenance. It was already written to the run manifest and returned by nothing, so removing `backend` alone would have been a net LOSS of provenance. |
| `geo-revenue/run_optimization__target_roas.json` | `/status/backend` | key removed: 'backend' -- Spec 6.2/6.3: the backend knob is removed in Phase 4. JAX is the only supported backend, so a per-run `backend` field carries no information. |
| `geo-revenue/run_optimization__target_roas.json` | `/status/meridian_version` | key added: 'meridian_version' -- Spec 6.3: replaces `backend` as agent-visible provenance. It was already written to the run manifest and returned by nothing, so removing `backend` alone would have been a net LOSS of provenance. |
| `geo-revenue/run_optimization__target_roas.json` | `/submit/backend` | key removed: 'backend' -- Spec 6.2/6.3: the backend knob is removed in Phase 4. JAX is the only supported backend, so a per-run `backend` field carries no information. |
| `geo-revenue/run_optimization__target_roas.json` | `/submit/meridian_version` | key added: 'meridian_version' -- Spec 6.3: replaces `backend` as agent-visible provenance. It was already written to the run manifest and returned by nothing, so removing `backend` alone would have been a net LOSS of provenance. |
| `national-revenue/lifecycle__cancel.json` | `/status/backend` | key removed: 'backend' -- Spec 6.2/6.3: the backend knob is removed in Phase 4. JAX is the only supported backend, so a per-run `backend` field carries no information. |
| `national-revenue/lifecycle__cancel.json` | `/status/meridian_version` | key added: 'meridian_version' -- Spec 6.3: replaces `backend` as agent-visible provenance. It was already written to the run manifest and returned by nothing, so removing `backend` alone would have been a net LOSS of provenance. |
| `national-revenue/lifecycle__cancel.json` | `/submit/backend` | key removed: 'backend' -- Spec 6.2/6.3: the backend knob is removed in Phase 4. JAX is the only supported backend, so a per-run `backend` field carries no information. |
| `national-revenue/lifecycle__cancel.json` | `/submit/meridian_version` | key added: 'meridian_version' -- Spec 6.3: replaces `backend` as agent-visible provenance. It was already written to the run manifest and returned by nothing, so removing `backend` alone would have been a net LOSS of provenance. |
| `national-revenue/lifecycle__status_result_reuse_delete.json` | `/reused/backend` | key removed: 'backend' -- Spec 6.2/6.3: the backend knob is removed in Phase 4. JAX is the only supported backend, so a per-run `backend` field carries no information. |
| `national-revenue/lifecycle__status_result_reuse_delete.json` | `/reused/meridian_version` | key added: 'meridian_version' -- Spec 6.3: replaces `backend` as agent-visible provenance. It was already written to the run manifest and returned by nothing, so removing `backend` alone would have been a net LOSS of provenance. |
| `national-revenue/lifecycle__status_result_reuse_delete.json` | `/status/backend` | key removed: 'backend' -- Spec 6.2/6.3: the backend knob is removed in Phase 4. JAX is the only supported backend, so a per-run `backend` field carries no information. |
| `national-revenue/lifecycle__status_result_reuse_delete.json` | `/status/meridian_version` | key added: 'meridian_version' -- Spec 6.3: replaces `backend` as agent-visible provenance. It was already written to the run manifest and returned by nothing, so removing `backend` alone would have been a net LOSS of provenance. |
| `national-revenue/lifecycle__status_result_reuse_delete.json` | `/submit/backend` | key removed: 'backend' -- Spec 6.2/6.3: the backend knob is removed in Phase 4. JAX is the only supported backend, so a per-run `backend` field carries no information. |
| `national-revenue/lifecycle__status_result_reuse_delete.json` | `/submit/meridian_version` | key added: 'meridian_version' -- Spec 6.3: replaces `backend` as agent-visible provenance. It was already written to the run manifest and returned by nothing, so removing `backend` alone would have been a net LOSS of provenance. |
| `national-revenue/run_future_optimization__cost_multipliers.json` | `/status/backend` | key removed: 'backend' -- Spec 6.2/6.3: the backend knob is removed in Phase 4. JAX is the only supported backend, so a per-run `backend` field carries no information. |
| `national-revenue/run_future_optimization__cost_multipliers.json` | `/status/meridian_version` | key added: 'meridian_version' -- Spec 6.3: replaces `backend` as agent-visible provenance. It was already written to the run manifest and returned by nothing, so removing `backend` alone would have been a net LOSS of provenance. |
| `national-revenue/run_future_optimization__cost_multipliers.json` | `/submit/backend` | key removed: 'backend' -- Spec 6.2/6.3: the backend knob is removed in Phase 4. JAX is the only supported backend, so a per-run `backend` field carries no information. |
| `national-revenue/run_future_optimization__cost_multipliers.json` | `/submit/meridian_version` | key added: 'meridian_version' -- Spec 6.3: replaces `backend` as agent-visible provenance. It was already written to the run manifest and returned by nothing, so removing `backend` alone would have been a net LOSS of provenance. |
| `national-revenue/run_future_optimization__planned_allocation_long_horizon.json` | `/status/backend` | key removed: 'backend' -- Spec 6.2/6.3: the backend knob is removed in Phase 4. JAX is the only supported backend, so a per-run `backend` field carries no information. |
| `national-revenue/run_future_optimization__planned_allocation_long_horizon.json` | `/status/meridian_version` | key added: 'meridian_version' -- Spec 6.3: replaces `backend` as agent-visible provenance. It was already written to the run manifest and returned by nothing, so removing `backend` alone would have been a net LOSS of provenance. |
| `national-revenue/run_future_optimization__planned_allocation_long_horizon.json` | `/submit/backend` | key removed: 'backend' -- Spec 6.2/6.3: the backend knob is removed in Phase 4. JAX is the only supported backend, so a per-run `backend` field carries no information. |
| `national-revenue/run_future_optimization__planned_allocation_long_horizon.json` | `/submit/meridian_version` | key added: 'meridian_version' -- Spec 6.3: replaces `backend` as agent-visible provenance. It was already written to the run manifest and returned by nothing, so removing `backend` alone would have been a net LOSS of provenance. |
| `national-revenue/run_future_optimization__reference_full_history_average.json` | `/status/backend` | key removed: 'backend' -- Spec 6.2/6.3: the backend knob is removed in Phase 4. JAX is the only supported backend, so a per-run `backend` field carries no information. |
| `national-revenue/run_future_optimization__reference_full_history_average.json` | `/status/meridian_version` | key added: 'meridian_version' -- Spec 6.3: replaces `backend` as agent-visible provenance. It was already written to the run manifest and returned by nothing, so removing `backend` alone would have been a net LOSS of provenance. |
| `national-revenue/run_future_optimization__reference_full_history_average.json` | `/submit/backend` | key removed: 'backend' -- Spec 6.2/6.3: the backend knob is removed in Phase 4. JAX is the only supported backend, so a per-run `backend` field carries no information. |
| `national-revenue/run_future_optimization__reference_full_history_average.json` | `/submit/meridian_version` | key added: 'meridian_version' -- Spec 6.3: replaces `backend` as agent-visible provenance. It was already written to the run manifest and returned by nothing, so removing `backend` alone would have been a net LOSS of provenance. |
| `national-revenue/run_future_optimization__reference_same_period_last_year.json` | `/status/backend` | key removed: 'backend' -- Spec 6.2/6.3: the backend knob is removed in Phase 4. JAX is the only supported backend, so a per-run `backend` field carries no information. |
| `national-revenue/run_future_optimization__reference_same_period_last_year.json` | `/status/meridian_version` | key added: 'meridian_version' -- Spec 6.3: replaces `backend` as agent-visible provenance. It was already written to the run manifest and returned by nothing, so removing `backend` alone would have been a net LOSS of provenance. |
| `national-revenue/run_future_optimization__reference_same_period_last_year.json` | `/submit/backend` | key removed: 'backend' -- Spec 6.2/6.3: the backend knob is removed in Phase 4. JAX is the only supported backend, so a per-run `backend` field carries no information. |
| `national-revenue/run_future_optimization__reference_same_period_last_year.json` | `/submit/meridian_version` | key added: 'meridian_version' -- Spec 6.3: replaces `backend` as agent-visible provenance. It was already written to the run manifest and returned by nothing, so removing `backend` alone would have been a net LOSS of provenance. |
| `national-revenue/run_future_optimization__reference_trailing.json` | `/status/backend` | key removed: 'backend' -- Spec 6.2/6.3: the backend knob is removed in Phase 4. JAX is the only supported backend, so a per-run `backend` field carries no information. |
| `national-revenue/run_future_optimization__reference_trailing.json` | `/status/meridian_version` | key added: 'meridian_version' -- Spec 6.3: replaces `backend` as agent-visible provenance. It was already written to the run manifest and returned by nothing, so removing `backend` alone would have been a net LOSS of provenance. |
| `national-revenue/run_future_optimization__reference_trailing.json` | `/submit/backend` | key removed: 'backend' -- Spec 6.2/6.3: the backend knob is removed in Phase 4. JAX is the only supported backend, so a per-run `backend` field carries no information. |
| `national-revenue/run_future_optimization__reference_trailing.json` | `/submit/meridian_version` | key added: 'meridian_version' -- Spec 6.3: replaces `backend` as agent-visible provenance. It was already written to the run manifest and returned by nothing, so removing `backend` alone would have been a net LOSS of provenance. |
| `national-revenue/run_future_optimization__revenue_per_kpi_multiplier.json` | `/status/backend` | key removed: 'backend' -- Spec 6.2/6.3: the backend knob is removed in Phase 4. JAX is the only supported backend, so a per-run `backend` field carries no information. |
| `national-revenue/run_future_optimization__revenue_per_kpi_multiplier.json` | `/status/meridian_version` | key added: 'meridian_version' -- Spec 6.3: replaces `backend` as agent-visible provenance. It was already written to the run manifest and returned by nothing, so removing `backend` alone would have been a net LOSS of provenance. |
| `national-revenue/run_future_optimization__revenue_per_kpi_multiplier.json` | `/submit/backend` | key removed: 'backend' -- Spec 6.2/6.3: the backend knob is removed in Phase 4. JAX is the only supported backend, so a per-run `backend` field carries no information. |
| `national-revenue/run_future_optimization__revenue_per_kpi_multiplier.json` | `/submit/meridian_version` | key added: 'meridian_version' -- Spec 6.3: replaces `backend` as agent-visible provenance. It was already written to the run manifest and returned by nothing, so removing `backend` alone would have been a net LOSS of provenance. |
| `national-revenue/run_optimization__date_window.json` | `/status/backend` | key removed: 'backend' -- Spec 6.2/6.3: the backend knob is removed in Phase 4. JAX is the only supported backend, so a per-run `backend` field carries no information. |
| `national-revenue/run_optimization__date_window.json` | `/status/meridian_version` | key added: 'meridian_version' -- Spec 6.3: replaces `backend` as agent-visible provenance. It was already written to the run manifest and returned by nothing, so removing `backend` alone would have been a net LOSS of provenance. |
| `national-revenue/run_optimization__date_window.json` | `/submit/backend` | key removed: 'backend' -- Spec 6.2/6.3: the backend knob is removed in Phase 4. JAX is the only supported backend, so a per-run `backend` field carries no information. |
| `national-revenue/run_optimization__date_window.json` | `/submit/meridian_version` | key added: 'meridian_version' -- Spec 6.3: replaces `backend` as agent-visible provenance. It was already written to the run manifest and returned by nothing, so removing `backend` alone would have been a net LOSS of provenance. |
| `national-revenue/run_optimization__fixed_budget.json` | `/status/backend` | key removed: 'backend' -- Spec 6.2/6.3: the backend knob is removed in Phase 4. JAX is the only supported backend, so a per-run `backend` field carries no information. |
| `national-revenue/run_optimization__fixed_budget.json` | `/status/meridian_version` | key added: 'meridian_version' -- Spec 6.3: replaces `backend` as agent-visible provenance. It was already written to the run manifest and returned by nothing, so removing `backend` alone would have been a net LOSS of provenance. |
| `national-revenue/run_optimization__fixed_budget.json` | `/submit/backend` | key removed: 'backend' -- Spec 6.2/6.3: the backend knob is removed in Phase 4. JAX is the only supported backend, so a per-run `backend` field carries no information. |
| `national-revenue/run_optimization__fixed_budget.json` | `/submit/meridian_version` | key added: 'meridian_version' -- Spec 6.3: replaces `backend` as agent-visible provenance. It was already written to the run manifest and returned by nothing, so removing `backend` alone would have been a net LOSS of provenance. |
| `national-revenue/run_optimization__spend_constraints.json` | `/status/backend` | key removed: 'backend' -- Spec 6.2/6.3: the backend knob is removed in Phase 4. JAX is the only supported backend, so a per-run `backend` field carries no information. |
| `national-revenue/run_optimization__spend_constraints.json` | `/status/meridian_version` | key added: 'meridian_version' -- Spec 6.3: replaces `backend` as agent-visible provenance. It was already written to the run manifest and returned by nothing, so removing `backend` alone would have been a net LOSS of provenance. |
| `national-revenue/run_optimization__spend_constraints.json` | `/submit/backend` | key removed: 'backend' -- Spec 6.2/6.3: the backend knob is removed in Phase 4. JAX is the only supported backend, so a per-run `backend` field carries no information. |
| `national-revenue/run_optimization__spend_constraints.json` | `/submit/meridian_version` | key added: 'meridian_version' -- Spec 6.3: replaces `backend` as agent-visible provenance. It was already written to the run manifest and returned by nothing, so removing `backend` alone would have been a net LOSS of provenance. |
| `national-revenue/run_optimization__target_mroas.json` | `/status/backend` | key removed: 'backend' -- Spec 6.2/6.3: the backend knob is removed in Phase 4. JAX is the only supported backend, so a per-run `backend` field carries no information. |
| `national-revenue/run_optimization__target_mroas.json` | `/status/meridian_version` | key added: 'meridian_version' -- Spec 6.3: replaces `backend` as agent-visible provenance. It was already written to the run manifest and returned by nothing, so removing `backend` alone would have been a net LOSS of provenance. |
| `national-revenue/run_optimization__target_mroas.json` | `/submit/backend` | key removed: 'backend' -- Spec 6.2/6.3: the backend knob is removed in Phase 4. JAX is the only supported backend, so a per-run `backend` field carries no information. |
| `national-revenue/run_optimization__target_mroas.json` | `/submit/meridian_version` | key added: 'meridian_version' -- Spec 6.3: replaces `backend` as agent-visible provenance. It was already written to the run manifest and returned by nothing, so removing `backend` alone would have been a net LOSS of provenance. |
| `national-revenue/run_optimization__target_roas.json` | `/status/backend` | key removed: 'backend' -- Spec 6.2/6.3: the backend knob is removed in Phase 4. JAX is the only supported backend, so a per-run `backend` field carries no information. |
| `national-revenue/run_optimization__target_roas.json` | `/status/meridian_version` | key added: 'meridian_version' -- Spec 6.3: replaces `backend` as agent-visible provenance. It was already written to the run manifest and returned by nothing, so removing `backend` alone would have been a net LOSS of provenance. |
| `national-revenue/run_optimization__target_roas.json` | `/submit/backend` | key removed: 'backend' -- Spec 6.2/6.3: the backend knob is removed in Phase 4. JAX is the only supported backend, so a per-run `backend` field carries no information. |
| `national-revenue/run_optimization__target_roas.json` | `/submit/meridian_version` | key added: 'meridian_version' -- Spec 6.3: replaces `backend` as agent-visible provenance. It was already written to the run manifest and returned by nothing, so removing `backend` alone would have been a net LOSS of provenance. |

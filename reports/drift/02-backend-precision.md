# Drift report: `v2.0-tf` -> `v2.0-jax`

**Verdict: FAIL -- exit code 1** (237 FAIL, 7 REVIEW, 112 ACKNOWLEDGED)

## Legend

- **FAIL** and **REVIEW** both block (exit code 1): FAIL is a structural break (added/removed/type-changed field, changed identity, or a case missing from one label) with no ambiguity; REVIEW is a numeric or ordering finding that needs a human to judge (over tolerance, sign flip, known racy field, broken CI ordering) and MIGHT be legitimate.
- **ACKNOWLEDGED** does not block: pre-registered in `acknowledged.py` with a mandatory reason (spec 7.4). Reported in its own section, never folded into a clean PASS.

## Environment

| Item | v2.0-tf | v2.0-jax |
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

> **Environment note (added after the final whole-branch review).** This
> report predates the `probe backend` row (see `01-meridian-version.md`) and
> cannot be re-rendered to add it -- the label copies it was captured from no
> longer exist. `worker MERIDIAN_BACKEND` above is the *inherited* ambient
> environment (frozen by design; `None` is normal, not a bug), not
> necessarily what the tools ran under. The Fixture provenance section
> immediately below reflects the backend the probe actually ran under, which
> is what this report's payloads measure.

## Fixture provenance

**v2.0-tf**

- `geo-kpi-only`: trained TENSORFLOW/FLOAT32 (v1.7.0) vs current TENSORFLOW/FLOAT32 -- backend mismatch: False, precision mismatch: False
- `geo-kpi-rpk`: trained TENSORFLOW/FLOAT32 (v1.7.0) vs current TENSORFLOW/FLOAT32 -- backend mismatch: False, precision mismatch: False
- `geo-revenue`: trained TENSORFLOW/FLOAT32 (v1.7.0) vs current TENSORFLOW/FLOAT32 -- backend mismatch: False, precision mismatch: False
- `geo-revenue-media-only`: trained TENSORFLOW/FLOAT32 (v1.7.0) vs current TENSORFLOW/FLOAT32 -- backend mismatch: False, precision mismatch: False
- `national-kpi-only`: trained TENSORFLOW/FLOAT32 (v1.7.0) vs current TENSORFLOW/FLOAT32 -- backend mismatch: False, precision mismatch: False
- `national-kpi-rpk`: trained TENSORFLOW/FLOAT32 (v1.7.0) vs current TENSORFLOW/FLOAT32 -- backend mismatch: False, precision mismatch: False
- `national-revenue`: trained TENSORFLOW/FLOAT32 (v1.7.0) vs current TENSORFLOW/FLOAT32 -- backend mismatch: False, precision mismatch: False

**v2.0-jax**

- `geo-kpi-only`: trained TENSORFLOW/FLOAT32 (v1.7.0) vs current JAX/FLOAT64 -- backend mismatch: True, precision mismatch: True
- `geo-kpi-rpk`: trained TENSORFLOW/FLOAT32 (v1.7.0) vs current JAX/FLOAT64 -- backend mismatch: True, precision mismatch: True
- `geo-revenue`: trained TENSORFLOW/FLOAT32 (v1.7.0) vs current JAX/FLOAT64 -- backend mismatch: True, precision mismatch: True
- `geo-revenue-media-only`: trained TENSORFLOW/FLOAT32 (v1.7.0) vs current JAX/FLOAT64 -- backend mismatch: True, precision mismatch: True
- `national-kpi-only`: trained TENSORFLOW/FLOAT32 (v1.7.0) vs current JAX/FLOAT64 -- backend mismatch: True, precision mismatch: True
- `national-kpi-rpk`: trained TENSORFLOW/FLOAT32 (v1.7.0) vs current JAX/FLOAT64 -- backend mismatch: True, precision mismatch: True
- `national-revenue`: trained TENSORFLOW/FLOAT32 (v1.7.0) vs current JAX/FLOAT64 -- backend mismatch: True, precision mismatch: True

## Summary

- Cases compared: 330 (276 clean -- no findings in any section)
- Case-set differences (case present in only one label): 0
- FAIL (including case-set differences above): 237
- REVIEW: 7
- ACKNOWLEDGED: 112

## Per-case environment provenance (for cases with findings)

- v2.0-tf vs v2.0-jax capture_env differs the same way for every case below: src_hash: `b3854dbfd1a571aaff65c0258119010da8ed630a740e9b70a19299be4a758f39` vs `8cf4a6d36339ee8e66e9d3454a92d0005914db33b14a0176d5fb7573697f37bd`

## FAIL - structural

| Case | Pointer | Detail |
| --- | --- | --- |
| `national-revenue-pkl/get_channel_summary__baseline_summary_metrics.json` | `/columns` | key removed: 'columns' |
| `national-revenue-pkl/get_channel_summary__baseline_summary_metrics.json` | `/model_id` | key removed: 'model_id' |
| `national-revenue-pkl/get_channel_summary__baseline_summary_metrics.json` | `/output_type` | key removed: 'output_type' |
| `national-revenue-pkl/get_channel_summary__baseline_summary_metrics.json` | `/row_count` | key removed: 'row_count' |
| `national-revenue-pkl/get_channel_summary__baseline_summary_metrics.json` | `/rows` | key removed: 'rows' |
| `national-revenue-pkl/get_channel_summary__baseline_summary_metrics.json` | `/details` | key added: 'details' |
| `national-revenue-pkl/get_channel_summary__baseline_summary_metrics.json` | `/error_code` | key added: 'error_code' |
| `national-revenue-pkl/get_channel_summary__baseline_summary_metrics.json` | `/message` | key added: 'message' |
| `national-revenue-pkl/get_channel_summary__cpik.json` | `/columns` | key removed: 'columns' |
| `national-revenue-pkl/get_channel_summary__cpik.json` | `/model_id` | key removed: 'model_id' |
| `national-revenue-pkl/get_channel_summary__cpik.json` | `/output_type` | key removed: 'output_type' |
| `national-revenue-pkl/get_channel_summary__cpik.json` | `/row_count` | key removed: 'row_count' |
| `national-revenue-pkl/get_channel_summary__cpik.json` | `/rows` | key removed: 'rows' |
| `national-revenue-pkl/get_channel_summary__cpik.json` | `/details` | key added: 'details' |
| `national-revenue-pkl/get_channel_summary__cpik.json` | `/error_code` | key added: 'error_code' |
| `national-revenue-pkl/get_channel_summary__cpik.json` | `/message` | key added: 'message' |
| `national-revenue-pkl/get_channel_summary__cpik_channel_subset.json` | `/columns` | key removed: 'columns' |
| `national-revenue-pkl/get_channel_summary__cpik_channel_subset.json` | `/model_id` | key removed: 'model_id' |
| `national-revenue-pkl/get_channel_summary__cpik_channel_subset.json` | `/output_type` | key removed: 'output_type' |
| `national-revenue-pkl/get_channel_summary__cpik_channel_subset.json` | `/row_count` | key removed: 'row_count' |
| `national-revenue-pkl/get_channel_summary__cpik_channel_subset.json` | `/rows` | key removed: 'rows' |
| `national-revenue-pkl/get_channel_summary__cpik_channel_subset.json` | `/details` | key added: 'details' |
| `national-revenue-pkl/get_channel_summary__cpik_channel_subset.json` | `/error_code` | key added: 'error_code' |
| `national-revenue-pkl/get_channel_summary__cpik_channel_subset.json` | `/message` | key added: 'message' |
| `national-revenue-pkl/get_channel_summary__cpik_date_window.json` | `/columns` | key removed: 'columns' |
| `national-revenue-pkl/get_channel_summary__cpik_date_window.json` | `/model_id` | key removed: 'model_id' |
| `national-revenue-pkl/get_channel_summary__cpik_date_window.json` | `/output_type` | key removed: 'output_type' |
| `national-revenue-pkl/get_channel_summary__cpik_date_window.json` | `/row_count` | key removed: 'row_count' |
| `national-revenue-pkl/get_channel_summary__cpik_date_window.json` | `/rows` | key removed: 'rows' |
| `national-revenue-pkl/get_channel_summary__cpik_date_window.json` | `/details` | key added: 'details' |
| `national-revenue-pkl/get_channel_summary__cpik_date_window.json` | `/error_code` | key added: 'error_code' |
| `national-revenue-pkl/get_channel_summary__cpik_date_window.json` | `/message` | key added: 'message' |
| `national-revenue-pkl/get_channel_summary__marginal_cpik.json` | `/columns` | key removed: 'columns' |
| `national-revenue-pkl/get_channel_summary__marginal_cpik.json` | `/model_id` | key removed: 'model_id' |
| `national-revenue-pkl/get_channel_summary__marginal_cpik.json` | `/output_type` | key removed: 'output_type' |
| `national-revenue-pkl/get_channel_summary__marginal_cpik.json` | `/row_count` | key removed: 'row_count' |
| `national-revenue-pkl/get_channel_summary__marginal_cpik.json` | `/rows` | key removed: 'rows' |
| `national-revenue-pkl/get_channel_summary__marginal_cpik.json` | `/details` | key added: 'details' |
| `national-revenue-pkl/get_channel_summary__marginal_cpik.json` | `/error_code` | key added: 'error_code' |
| `national-revenue-pkl/get_channel_summary__marginal_cpik.json` | `/message` | key added: 'message' |
| `national-revenue-pkl/get_channel_summary__marginal_roi.json` | `/columns` | key removed: 'columns' |
| `national-revenue-pkl/get_channel_summary__marginal_roi.json` | `/model_id` | key removed: 'model_id' |
| `national-revenue-pkl/get_channel_summary__marginal_roi.json` | `/output_type` | key removed: 'output_type' |
| `national-revenue-pkl/get_channel_summary__marginal_roi.json` | `/row_count` | key removed: 'row_count' |
| `national-revenue-pkl/get_channel_summary__marginal_roi.json` | `/rows` | key removed: 'rows' |
| `national-revenue-pkl/get_channel_summary__marginal_roi.json` | `/details` | key added: 'details' |
| `national-revenue-pkl/get_channel_summary__marginal_roi.json` | `/error_code` | key added: 'error_code' |
| `national-revenue-pkl/get_channel_summary__marginal_roi.json` | `/message` | key added: 'message' |
| `national-revenue-pkl/get_channel_summary__paid_summary_metrics.json` | `/columns` | key removed: 'columns' |
| `national-revenue-pkl/get_channel_summary__paid_summary_metrics.json` | `/model_id` | key removed: 'model_id' |
| `national-revenue-pkl/get_channel_summary__paid_summary_metrics.json` | `/output_type` | key removed: 'output_type' |
| `national-revenue-pkl/get_channel_summary__paid_summary_metrics.json` | `/row_count` | key removed: 'row_count' |
| `national-revenue-pkl/get_channel_summary__paid_summary_metrics.json` | `/rows` | key removed: 'rows' |
| `national-revenue-pkl/get_channel_summary__paid_summary_metrics.json` | `/details` | key added: 'details' |
| `national-revenue-pkl/get_channel_summary__paid_summary_metrics.json` | `/error_code` | key added: 'error_code' |
| `national-revenue-pkl/get_channel_summary__paid_summary_metrics.json` | `/message` | key added: 'message' |
| `national-revenue-pkl/get_channel_summary__roi.json` | `/columns` | key removed: 'columns' |
| `national-revenue-pkl/get_channel_summary__roi.json` | `/model_id` | key removed: 'model_id' |
| `national-revenue-pkl/get_channel_summary__roi.json` | `/output_type` | key removed: 'output_type' |
| `national-revenue-pkl/get_channel_summary__roi.json` | `/row_count` | key removed: 'row_count' |
| `national-revenue-pkl/get_channel_summary__roi.json` | `/rows` | key removed: 'rows' |
| `national-revenue-pkl/get_channel_summary__roi.json` | `/details` | key added: 'details' |
| `national-revenue-pkl/get_channel_summary__roi.json` | `/error_code` | key added: 'error_code' |
| `national-revenue-pkl/get_channel_summary__roi.json` | `/message` | key added: 'message' |
| `national-revenue-pkl/get_contribution__by_time_date_window.json` | `/columns` | key removed: 'columns' |
| `national-revenue-pkl/get_contribution__by_time_date_window.json` | `/model_id` | key removed: 'model_id' |
| `national-revenue-pkl/get_contribution__by_time_date_window.json` | `/output_type` | key removed: 'output_type' |
| `national-revenue-pkl/get_contribution__by_time_date_window.json` | `/row_count` | key removed: 'row_count' |
| `national-revenue-pkl/get_contribution__by_time_date_window.json` | `/rows` | key removed: 'rows' |
| `national-revenue-pkl/get_contribution__by_time_date_window.json` | `/details` | key added: 'details' |
| `national-revenue-pkl/get_contribution__by_time_date_window.json` | `/error_code` | key added: 'error_code' |
| `national-revenue-pkl/get_contribution__by_time_date_window.json` | `/message` | key added: 'message' |
| `national-revenue-pkl/get_contribution__contribution_metrics.json` | `/columns` | key removed: 'columns' |
| `national-revenue-pkl/get_contribution__contribution_metrics.json` | `/model_id` | key removed: 'model_id' |
| `national-revenue-pkl/get_contribution__contribution_metrics.json` | `/output_type` | key removed: 'output_type' |
| `national-revenue-pkl/get_contribution__contribution_metrics.json` | `/row_count` | key removed: 'row_count' |
| `national-revenue-pkl/get_contribution__contribution_metrics.json` | `/rows` | key removed: 'rows' |
| `national-revenue-pkl/get_contribution__contribution_metrics.json` | `/details` | key added: 'details' |
| `national-revenue-pkl/get_contribution__contribution_metrics.json` | `/error_code` | key added: 'error_code' |
| `national-revenue-pkl/get_contribution__contribution_metrics.json` | `/message` | key added: 'message' |
| `national-revenue-pkl/get_contribution__contribution_metrics_by_time.json` | `/columns` | key removed: 'columns' |
| `national-revenue-pkl/get_contribution__contribution_metrics_by_time.json` | `/model_id` | key removed: 'model_id' |
| `national-revenue-pkl/get_contribution__contribution_metrics_by_time.json` | `/output_type` | key removed: 'output_type' |
| `national-revenue-pkl/get_contribution__contribution_metrics_by_time.json` | `/row_count` | key removed: 'row_count' |
| `national-revenue-pkl/get_contribution__contribution_metrics_by_time.json` | `/rows` | key removed: 'rows' |
| `national-revenue-pkl/get_contribution__contribution_metrics_by_time.json` | `/details` | key added: 'details' |
| `national-revenue-pkl/get_contribution__contribution_metrics_by_time.json` | `/error_code` | key added: 'error_code' |
| `national-revenue-pkl/get_contribution__contribution_metrics_by_time.json` | `/message` | key added: 'message' |
| `national-revenue-pkl/get_model_fit__date_window.json` | `/columns` | key removed: 'columns' |
| `national-revenue-pkl/get_model_fit__date_window.json` | `/model_id` | key removed: 'model_id' |
| `national-revenue-pkl/get_model_fit__date_window.json` | `/row_count` | key removed: 'row_count' |
| `national-revenue-pkl/get_model_fit__date_window.json` | `/rows` | key removed: 'rows' |
| `national-revenue-pkl/get_model_fit__date_window.json` | `/details` | key added: 'details' |
| `national-revenue-pkl/get_model_fit__date_window.json` | `/error_code` | key added: 'error_code' |
| `national-revenue-pkl/get_model_fit__date_window.json` | `/message` | key added: 'message' |
| `national-revenue-pkl/get_model_fit__default.json` | `/columns` | key removed: 'columns' |
| `national-revenue-pkl/get_model_fit__default.json` | `/model_id` | key removed: 'model_id' |
| `national-revenue-pkl/get_model_fit__default.json` | `/row_count` | key removed: 'row_count' |
| `national-revenue-pkl/get_model_fit__default.json` | `/rows` | key removed: 'rows' |
| `national-revenue-pkl/get_model_fit__default.json` | `/details` | key added: 'details' |
| `national-revenue-pkl/get_model_fit__default.json` | `/error_code` | key added: 'error_code' |
| `national-revenue-pkl/get_model_fit__default.json` | `/message` | key added: 'message' |
| `national-revenue-pkl/get_model_fit__use_kpi.json` | `/columns` | key removed: 'columns' |
| `national-revenue-pkl/get_model_fit__use_kpi.json` | `/model_id` | key removed: 'model_id' |
| `national-revenue-pkl/get_model_fit__use_kpi.json` | `/row_count` | key removed: 'row_count' |
| `national-revenue-pkl/get_model_fit__use_kpi.json` | `/rows` | key removed: 'rows' |
| `national-revenue-pkl/get_model_fit__use_kpi.json` | `/details` | key added: 'details' |
| `national-revenue-pkl/get_model_fit__use_kpi.json` | `/error_code` | key added: 'error_code' |
| `national-revenue-pkl/get_model_fit__use_kpi.json` | `/message` | key added: 'message' |
| `national-revenue-pkl/get_reach_frequency__date_window.json` | `/columns` | key removed: 'columns' |
| `national-revenue-pkl/get_reach_frequency__date_window.json` | `/model_id` | key removed: 'model_id' |
| `national-revenue-pkl/get_reach_frequency__date_window.json` | `/row_count` | key removed: 'row_count' |
| `national-revenue-pkl/get_reach_frequency__date_window.json` | `/rows` | key removed: 'rows' |
| `national-revenue-pkl/get_reach_frequency__date_window.json` | `/details` | key added: 'details' |
| `national-revenue-pkl/get_reach_frequency__date_window.json` | `/error_code` | key added: 'error_code' |
| `national-revenue-pkl/get_reach_frequency__date_window.json` | `/message` | key added: 'message' |
| `national-revenue-pkl/get_reach_frequency__default.json` | `/columns` | key removed: 'columns' |
| `national-revenue-pkl/get_reach_frequency__default.json` | `/model_id` | key removed: 'model_id' |
| `national-revenue-pkl/get_reach_frequency__default.json` | `/row_count` | key removed: 'row_count' |
| `national-revenue-pkl/get_reach_frequency__default.json` | `/rows` | key removed: 'rows' |
| `national-revenue-pkl/get_reach_frequency__default.json` | `/details` | key added: 'details' |
| `national-revenue-pkl/get_reach_frequency__default.json` | `/error_code` | key added: 'error_code' |
| `national-revenue-pkl/get_reach_frequency__default.json` | `/message` | key added: 'message' |
| `national-revenue-pkl/get_response_curves__curves_channel_subset.json` | `/columns` | key removed: 'columns' |
| `national-revenue-pkl/get_response_curves__curves_channel_subset.json` | `/model_id` | key removed: 'model_id' |
| `national-revenue-pkl/get_response_curves__curves_channel_subset.json` | `/output_type` | key removed: 'output_type' |
| `national-revenue-pkl/get_response_curves__curves_channel_subset.json` | `/row_count` | key removed: 'row_count' |
| `national-revenue-pkl/get_response_curves__curves_channel_subset.json` | `/rows` | key removed: 'rows' |
| `national-revenue-pkl/get_response_curves__curves_channel_subset.json` | `/details` | key added: 'details' |
| `national-revenue-pkl/get_response_curves__curves_channel_subset.json` | `/error_code` | key added: 'error_code' |
| `national-revenue-pkl/get_response_curves__curves_channel_subset.json` | `/message` | key added: 'message' |
| `national-revenue-pkl/get_response_curves__response_curve_summary.json` | `/columns` | key removed: 'columns' |
| `national-revenue-pkl/get_response_curves__response_curve_summary.json` | `/model_id` | key removed: 'model_id' |
| `national-revenue-pkl/get_response_curves__response_curve_summary.json` | `/output_type` | key removed: 'output_type' |
| `national-revenue-pkl/get_response_curves__response_curve_summary.json` | `/row_count` | key removed: 'row_count' |
| `national-revenue-pkl/get_response_curves__response_curve_summary.json` | `/rows` | key removed: 'rows' |
| `national-revenue-pkl/get_response_curves__response_curve_summary.json` | `/details` | key added: 'details' |
| `national-revenue-pkl/get_response_curves__response_curve_summary.json` | `/error_code` | key added: 'error_code' |
| `national-revenue-pkl/get_response_curves__response_curve_summary.json` | `/message` | key added: 'message' |
| `national-revenue-pkl/get_response_curves__response_curves.json` | `/columns` | key removed: 'columns' |
| `national-revenue-pkl/get_response_curves__response_curves.json` | `/model_id` | key removed: 'model_id' |
| `national-revenue-pkl/get_response_curves__response_curves.json` | `/output_type` | key removed: 'output_type' |
| `national-revenue-pkl/get_response_curves__response_curves.json` | `/row_count` | key removed: 'row_count' |
| `national-revenue-pkl/get_response_curves__response_curves.json` | `/rows` | key removed: 'rows' |
| `national-revenue-pkl/get_response_curves__response_curves.json` | `/details` | key added: 'details' |
| `national-revenue-pkl/get_response_curves__response_curves.json` | `/error_code` | key added: 'error_code' |
| `national-revenue-pkl/get_response_curves__response_curves.json` | `/message` | key added: 'message' |
| `national-revenue-pkl/get_spend_scenario__default_base.json` | `/base_outcome` | key removed: 'base_outcome' |
| `national-revenue-pkl/get_spend_scenario__default_base.json` | `/base_spend` | key removed: 'base_spend' |
| `national-revenue-pkl/get_spend_scenario__default_base.json` | `/channel` | key removed: 'channel' |
| `national-revenue-pkl/get_spend_scenario__default_base.json` | `/channel_type` | key removed: 'channel_type' |
| `national-revenue-pkl/get_spend_scenario__default_base.json` | `/efficiency` | key removed: 'efficiency' |
| `national-revenue-pkl/get_spend_scenario__default_base.json` | `/efficiency_at_new` | key removed: 'efficiency_at_new' |
| `national-revenue-pkl/get_spend_scenario__default_base.json` | `/expected_outcome_increase` | key removed: 'expected_outcome_increase' |
| `national-revenue-pkl/get_spend_scenario__default_base.json` | `/expected_outcome_increase_pct` | key removed: 'expected_outcome_increase_pct' |
| `national-revenue-pkl/get_spend_scenario__default_base.json` | `/marginal_efficiency` | key removed: 'marginal_efficiency' |
| `national-revenue-pkl/get_spend_scenario__default_base.json` | `/model_id` | key removed: 'model_id' |
| `national-revenue-pkl/get_spend_scenario__default_base.json` | `/new_outcome` | key removed: 'new_outcome' |
| `national-revenue-pkl/get_spend_scenario__default_base.json` | `/new_spend` | key removed: 'new_spend' |
| `national-revenue-pkl/get_spend_scenario__default_base.json` | `/outcome_mode` | key removed: 'outcome_mode' |
| `national-revenue-pkl/get_spend_scenario__default_base.json` | `/spend_increase` | key removed: 'spend_increase' |
| `national-revenue-pkl/get_spend_scenario__default_base.json` | `/spend_increase_pct` | key removed: 'spend_increase_pct' |
| `national-revenue-pkl/get_spend_scenario__default_base.json` | `/details` | key added: 'details' |
| `national-revenue-pkl/get_spend_scenario__default_base.json` | `/error_code` | key added: 'error_code' |
| `national-revenue-pkl/get_spend_scenario__default_base.json` | `/message` | key added: 'message' |
| `national-revenue-pkl/get_spend_scenario__explicit_base_spend.json` | `/base_outcome` | key removed: 'base_outcome' |
| `national-revenue-pkl/get_spend_scenario__explicit_base_spend.json` | `/base_spend` | key removed: 'base_spend' |
| `national-revenue-pkl/get_spend_scenario__explicit_base_spend.json` | `/channel` | key removed: 'channel' |
| `national-revenue-pkl/get_spend_scenario__explicit_base_spend.json` | `/channel_type` | key removed: 'channel_type' |
| `national-revenue-pkl/get_spend_scenario__explicit_base_spend.json` | `/efficiency` | key removed: 'efficiency' |
| `national-revenue-pkl/get_spend_scenario__explicit_base_spend.json` | `/efficiency_at_new` | key removed: 'efficiency_at_new' |
| `national-revenue-pkl/get_spend_scenario__explicit_base_spend.json` | `/expected_outcome_increase` | key removed: 'expected_outcome_increase' |
| `national-revenue-pkl/get_spend_scenario__explicit_base_spend.json` | `/expected_outcome_increase_pct` | key removed: 'expected_outcome_increase_pct' |
| `national-revenue-pkl/get_spend_scenario__explicit_base_spend.json` | `/marginal_efficiency` | key removed: 'marginal_efficiency' |
| `national-revenue-pkl/get_spend_scenario__explicit_base_spend.json` | `/model_id` | key removed: 'model_id' |
| `national-revenue-pkl/get_spend_scenario__explicit_base_spend.json` | `/new_outcome` | key removed: 'new_outcome' |
| `national-revenue-pkl/get_spend_scenario__explicit_base_spend.json` | `/new_spend` | key removed: 'new_spend' |
| `national-revenue-pkl/get_spend_scenario__explicit_base_spend.json` | `/outcome_mode` | key removed: 'outcome_mode' |
| `national-revenue-pkl/get_spend_scenario__explicit_base_spend.json` | `/spend_increase` | key removed: 'spend_increase' |
| `national-revenue-pkl/get_spend_scenario__explicit_base_spend.json` | `/spend_increase_pct` | key removed: 'spend_increase_pct' |
| `national-revenue-pkl/get_spend_scenario__explicit_base_spend.json` | `/details` | key added: 'details' |
| `national-revenue-pkl/get_spend_scenario__explicit_base_spend.json` | `/error_code` | key added: 'error_code' |
| `national-revenue-pkl/get_spend_scenario__explicit_base_spend.json` | `/message` | key added: 'message' |
| `national-revenue-pkl/get_spend_scenario__large_increase.json` | `/base_outcome` | key removed: 'base_outcome' |
| `national-revenue-pkl/get_spend_scenario__large_increase.json` | `/base_spend` | key removed: 'base_spend' |
| `national-revenue-pkl/get_spend_scenario__large_increase.json` | `/channel` | key removed: 'channel' |
| `national-revenue-pkl/get_spend_scenario__large_increase.json` | `/channel_type` | key removed: 'channel_type' |
| `national-revenue-pkl/get_spend_scenario__large_increase.json` | `/efficiency` | key removed: 'efficiency' |
| `national-revenue-pkl/get_spend_scenario__large_increase.json` | `/efficiency_at_new` | key removed: 'efficiency_at_new' |
| `national-revenue-pkl/get_spend_scenario__large_increase.json` | `/expected_outcome_increase` | key removed: 'expected_outcome_increase' |
| `national-revenue-pkl/get_spend_scenario__large_increase.json` | `/expected_outcome_increase_pct` | key removed: 'expected_outcome_increase_pct' |
| `national-revenue-pkl/get_spend_scenario__large_increase.json` | `/marginal_efficiency` | key removed: 'marginal_efficiency' |
| `national-revenue-pkl/get_spend_scenario__large_increase.json` | `/model_id` | key removed: 'model_id' |
| `national-revenue-pkl/get_spend_scenario__large_increase.json` | `/new_outcome` | key removed: 'new_outcome' |
| `national-revenue-pkl/get_spend_scenario__large_increase.json` | `/new_spend` | key removed: 'new_spend' |
| `national-revenue-pkl/get_spend_scenario__large_increase.json` | `/outcome_mode` | key removed: 'outcome_mode' |
| `national-revenue-pkl/get_spend_scenario__large_increase.json` | `/spend_increase` | key removed: 'spend_increase' |
| `national-revenue-pkl/get_spend_scenario__large_increase.json` | `/spend_increase_pct` | key removed: 'spend_increase_pct' |
| `national-revenue-pkl/get_spend_scenario__large_increase.json` | `/details` | key added: 'details' |
| `national-revenue-pkl/get_spend_scenario__large_increase.json` | `/error_code` | key added: 'error_code' |
| `national-revenue-pkl/get_spend_scenario__large_increase.json` | `/message` | key added: 'message' |
| `national-revenue-pkl/get_spend_scenario__tiny_increase.json` | `/base_outcome` | key removed: 'base_outcome' |
| `national-revenue-pkl/get_spend_scenario__tiny_increase.json` | `/base_spend` | key removed: 'base_spend' |
| `national-revenue-pkl/get_spend_scenario__tiny_increase.json` | `/channel` | key removed: 'channel' |
| `national-revenue-pkl/get_spend_scenario__tiny_increase.json` | `/channel_type` | key removed: 'channel_type' |
| `national-revenue-pkl/get_spend_scenario__tiny_increase.json` | `/efficiency` | key removed: 'efficiency' |
| `national-revenue-pkl/get_spend_scenario__tiny_increase.json` | `/efficiency_at_new` | key removed: 'efficiency_at_new' |
| `national-revenue-pkl/get_spend_scenario__tiny_increase.json` | `/expected_outcome_increase` | key removed: 'expected_outcome_increase' |
| `national-revenue-pkl/get_spend_scenario__tiny_increase.json` | `/expected_outcome_increase_pct` | key removed: 'expected_outcome_increase_pct' |
| `national-revenue-pkl/get_spend_scenario__tiny_increase.json` | `/marginal_efficiency` | key removed: 'marginal_efficiency' |
| `national-revenue-pkl/get_spend_scenario__tiny_increase.json` | `/model_id` | key removed: 'model_id' |
| `national-revenue-pkl/get_spend_scenario__tiny_increase.json` | `/new_outcome` | key removed: 'new_outcome' |
| `national-revenue-pkl/get_spend_scenario__tiny_increase.json` | `/new_spend` | key removed: 'new_spend' |
| `national-revenue-pkl/get_spend_scenario__tiny_increase.json` | `/outcome_mode` | key removed: 'outcome_mode' |
| `national-revenue-pkl/get_spend_scenario__tiny_increase.json` | `/spend_increase` | key removed: 'spend_increase' |
| `national-revenue-pkl/get_spend_scenario__tiny_increase.json` | `/spend_increase_pct` | key removed: 'spend_increase_pct' |
| `national-revenue-pkl/get_spend_scenario__tiny_increase.json` | `/details` | key added: 'details' |
| `national-revenue-pkl/get_spend_scenario__tiny_increase.json` | `/error_code` | key added: 'error_code' |
| `national-revenue-pkl/get_spend_scenario__tiny_increase.json` | `/message` | key added: 'message' |
| `national-revenue-pkl/get_spend_scenario__zero_denominator.json` | `/base_outcome` | key removed: 'base_outcome' |
| `national-revenue-pkl/get_spend_scenario__zero_denominator.json` | `/base_spend` | key removed: 'base_spend' |
| `national-revenue-pkl/get_spend_scenario__zero_denominator.json` | `/channel` | key removed: 'channel' |
| `national-revenue-pkl/get_spend_scenario__zero_denominator.json` | `/channel_type` | key removed: 'channel_type' |
| `national-revenue-pkl/get_spend_scenario__zero_denominator.json` | `/efficiency` | key removed: 'efficiency' |
| `national-revenue-pkl/get_spend_scenario__zero_denominator.json` | `/efficiency_at_new` | key removed: 'efficiency_at_new' |
| `national-revenue-pkl/get_spend_scenario__zero_denominator.json` | `/expected_outcome_increase` | key removed: 'expected_outcome_increase' |
| `national-revenue-pkl/get_spend_scenario__zero_denominator.json` | `/expected_outcome_increase_pct` | key removed: 'expected_outcome_increase_pct' |
| `national-revenue-pkl/get_spend_scenario__zero_denominator.json` | `/marginal_efficiency` | key removed: 'marginal_efficiency' |
| `national-revenue-pkl/get_spend_scenario__zero_denominator.json` | `/model_id` | key removed: 'model_id' |
| `national-revenue-pkl/get_spend_scenario__zero_denominator.json` | `/new_outcome` | key removed: 'new_outcome' |
| `national-revenue-pkl/get_spend_scenario__zero_denominator.json` | `/new_spend` | key removed: 'new_spend' |
| `national-revenue-pkl/get_spend_scenario__zero_denominator.json` | `/outcome_mode` | key removed: 'outcome_mode' |
| `national-revenue-pkl/get_spend_scenario__zero_denominator.json` | `/spend_increase` | key removed: 'spend_increase' |
| `national-revenue-pkl/get_spend_scenario__zero_denominator.json` | `/spend_increase_pct` | key removed: 'spend_increase_pct' |
| `national-revenue-pkl/get_spend_scenario__zero_denominator.json` | `/details` | key added: 'details' |
| `national-revenue-pkl/get_spend_scenario__zero_denominator.json` | `/error_code` | key added: 'error_code' |
| `national-revenue-pkl/get_spend_scenario__zero_denominator.json` | `/message` | key added: 'message' |

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

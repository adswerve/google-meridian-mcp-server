# Drift report: `v2.0-jax-sup` -> `v2.0-refit`

**Verdict: FAIL -- exit code 1** (4209 FAIL, 54962 REVIEW)

## Legend

- **FAIL** and **REVIEW** both block (exit code 1): FAIL is a structural break (added/removed/type-changed field, changed identity, or a case missing from one label) with no ambiguity; REVIEW is a numeric or ordering finding that needs a human to judge (over tolerance, sign flip, known racy field, broken CI ordering) and MIGHT be legitimate.
- **ACKNOWLEDGED** does not block: pre-registered in `acknowledged.py` with a mandatory reason (spec 7.4). Reported in its own section, never folded into a clean PASS.

## Environment

| Item | v2.0-jax-sup | v2.0-refit |
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
| worker MERIDIAN_BACKEND | jax | jax |
| worker MERIDIAN_ENABLE_JAX_X64 | true | true |
| worker TF_CPP_MIN_LOG_LEVEL | 3 | 3 |
| relative tolerance (REL_TOLERANCE) | 1e-03 | 1e-03 |
| absolute floor (ABS_FLOOR) | 1e-09 | 1e-09 |

> **Environment note (added after the final whole-branch review).** This
> report predates the `probe backend` row (see `01-meridian-version.md`) and
> cannot be re-rendered to add it -- the `/tmp/d3` scratch copies it was
> captured from no longer exist. `worker MERIDIAN_BACKEND` above is the
> *inherited* ambient environment (frozen by design), not necessarily what
> the tools ran under; here it happens to agree with the Fixture provenance
> section immediately below, which reflects the backend the probe actually
> ran under and is what this report's payloads measure.

## Fixture provenance

**v2.0-jax-sup**

- `geo-kpi-only`: trained TENSORFLOW/FLOAT32 (v1.7.0) vs current JAX/FLOAT64 -- backend mismatch: True, precision mismatch: True
- `geo-kpi-rpk`: trained TENSORFLOW/FLOAT32 (v1.7.0) vs current JAX/FLOAT64 -- backend mismatch: True, precision mismatch: True
- `geo-revenue`: trained TENSORFLOW/FLOAT32 (v1.7.0) vs current JAX/FLOAT64 -- backend mismatch: True, precision mismatch: True
- `geo-revenue-media-only`: trained TENSORFLOW/FLOAT32 (v1.7.0) vs current JAX/FLOAT64 -- backend mismatch: True, precision mismatch: True
- `national-kpi-only`: trained TENSORFLOW/FLOAT32 (v1.7.0) vs current JAX/FLOAT64 -- backend mismatch: True, precision mismatch: True
- `national-kpi-rpk`: trained TENSORFLOW/FLOAT32 (v1.7.0) vs current JAX/FLOAT64 -- backend mismatch: True, precision mismatch: True
- `national-revenue`: trained TENSORFLOW/FLOAT32 (v1.7.0) vs current JAX/FLOAT64 -- backend mismatch: True, precision mismatch: True

**v2.0-refit**

- `geo-kpi-only`: trained JAX/FLOAT64 (v2.0.0) vs current JAX/FLOAT64 -- backend mismatch: False, precision mismatch: False
- `geo-kpi-rpk`: trained JAX/FLOAT64 (v2.0.0) vs current JAX/FLOAT64 -- backend mismatch: False, precision mismatch: False
- `geo-revenue`: trained JAX/FLOAT64 (v2.0.0) vs current JAX/FLOAT64 -- backend mismatch: False, precision mismatch: False
- `geo-revenue-media-only`: trained JAX/FLOAT64 (v2.0.0) vs current JAX/FLOAT64 -- backend mismatch: False, precision mismatch: False
- `national-kpi-only`: trained JAX/FLOAT64 (v2.0.0) vs current JAX/FLOAT64 -- backend mismatch: False, precision mismatch: False
- `national-kpi-rpk`: trained JAX/FLOAT64 (v2.0.0) vs current JAX/FLOAT64 -- backend mismatch: False, precision mismatch: False
- `national-revenue`: trained JAX/FLOAT64 (v2.0.0) vs current JAX/FLOAT64 -- backend mismatch: False, precision mismatch: False

## Summary

- Cases compared: 293 (81 clean -- no findings in any section)
- Case-set differences (case present in only one label): 0
- FAIL (including case-set differences above): 4209
- REVIEW: 54962
- ACKNOWLEDGED: 0

## Per-case environment provenance (for cases with findings)

- v2.0-jax-sup vs v2.0-refit capture_env differs the same way for every case below: src_hash: `8cf4a6d36339ee8e66e9d3454a92d0005914db33b14a0176d5fb7573697f37bd` vs `7ea7e10bb41a91ee5ed20d4ca0a5478eed4307c96ba04dd806cca1d8f04cc242`
- `geo-kpi-only/get_adstock_decay__adstock_decay.json` -- fixture_hash: `c8f19ddaf8e0955594d898149735a60c4836a7ccd057eb6af2f9a6b7e690ce30` vs `cda11a9cf2a04daac8f19d67b1e8ffb9ac4bd48018da38647a6de36230c48503`
- `geo-kpi-only/get_adstock_decay__alpha_summary.json` -- fixture_hash: `c8f19ddaf8e0955594d898149735a60c4836a7ccd057eb6af2f9a6b7e690ce30` vs `cda11a9cf2a04daac8f19d67b1e8ffb9ac4bd48018da38647a6de36230c48503`
- `geo-kpi-only/get_channel_summary__baseline_summary_metrics.json` -- fixture_hash: `c8f19ddaf8e0955594d898149735a60c4836a7ccd057eb6af2f9a6b7e690ce30` vs `cda11a9cf2a04daac8f19d67b1e8ffb9ac4bd48018da38647a6de36230c48503`
- `geo-kpi-only/get_channel_summary__cpik.json` -- fixture_hash: `c8f19ddaf8e0955594d898149735a60c4836a7ccd057eb6af2f9a6b7e690ce30` vs `cda11a9cf2a04daac8f19d67b1e8ffb9ac4bd48018da38647a6de36230c48503`
- `geo-kpi-only/get_channel_summary__cpik_channel_subset.json` -- fixture_hash: `c8f19ddaf8e0955594d898149735a60c4836a7ccd057eb6af2f9a6b7e690ce30` vs `cda11a9cf2a04daac8f19d67b1e8ffb9ac4bd48018da38647a6de36230c48503`
- `geo-kpi-only/get_channel_summary__cpik_date_window.json` -- fixture_hash: `c8f19ddaf8e0955594d898149735a60c4836a7ccd057eb6af2f9a6b7e690ce30` vs `cda11a9cf2a04daac8f19d67b1e8ffb9ac4bd48018da38647a6de36230c48503`
- `geo-kpi-only/get_channel_summary__marginal_cpik.json` -- fixture_hash: `c8f19ddaf8e0955594d898149735a60c4836a7ccd057eb6af2f9a6b7e690ce30` vs `cda11a9cf2a04daac8f19d67b1e8ffb9ac4bd48018da38647a6de36230c48503`
- `geo-kpi-only/get_channel_summary__paid_summary_metrics.json` -- fixture_hash: `c8f19ddaf8e0955594d898149735a60c4836a7ccd057eb6af2f9a6b7e690ce30` vs `cda11a9cf2a04daac8f19d67b1e8ffb9ac4bd48018da38647a6de36230c48503`
- `geo-kpi-only/get_contribution__by_time_date_window.json` -- fixture_hash: `c8f19ddaf8e0955594d898149735a60c4836a7ccd057eb6af2f9a6b7e690ce30` vs `cda11a9cf2a04daac8f19d67b1e8ffb9ac4bd48018da38647a6de36230c48503`
- `geo-kpi-only/get_contribution__contribution_metrics.json` -- fixture_hash: `c8f19ddaf8e0955594d898149735a60c4836a7ccd057eb6af2f9a6b7e690ce30` vs `cda11a9cf2a04daac8f19d67b1e8ffb9ac4bd48018da38647a6de36230c48503`
- `geo-kpi-only/get_contribution__contribution_metrics_by_time.json` -- fixture_hash: `c8f19ddaf8e0955594d898149735a60c4836a7ccd057eb6af2f9a6b7e690ce30` vs `cda11a9cf2a04daac8f19d67b1e8ffb9ac4bd48018da38647a6de36230c48503`
- `geo-kpi-only/get_model_fit__date_window.json` -- fixture_hash: `c8f19ddaf8e0955594d898149735a60c4836a7ccd057eb6af2f9a6b7e690ce30` vs `cda11a9cf2a04daac8f19d67b1e8ffb9ac4bd48018da38647a6de36230c48503`
- `geo-kpi-only/get_model_fit__default.json` -- fixture_hash: `c8f19ddaf8e0955594d898149735a60c4836a7ccd057eb6af2f9a6b7e690ce30` vs `cda11a9cf2a04daac8f19d67b1e8ffb9ac4bd48018da38647a6de36230c48503`
- `geo-kpi-only/get_model_fit__geo_filter.json` -- fixture_hash: `c8f19ddaf8e0955594d898149735a60c4836a7ccd057eb6af2f9a6b7e690ce30` vs `cda11a9cf2a04daac8f19d67b1e8ffb9ac4bd48018da38647a6de36230c48503`
- `geo-kpi-only/get_model_fit__use_kpi.json` -- fixture_hash: `c8f19ddaf8e0955594d898149735a60c4836a7ccd057eb6af2f9a6b7e690ce30` vs `cda11a9cf2a04daac8f19d67b1e8ffb9ac4bd48018da38647a6de36230c48503`
- `geo-kpi-only/get_reach_frequency__date_window.json` -- fixture_hash: `c8f19ddaf8e0955594d898149735a60c4836a7ccd057eb6af2f9a6b7e690ce30` vs `cda11a9cf2a04daac8f19d67b1e8ffb9ac4bd48018da38647a6de36230c48503`
- `geo-kpi-only/get_reach_frequency__default.json` -- fixture_hash: `c8f19ddaf8e0955594d898149735a60c4836a7ccd057eb6af2f9a6b7e690ce30` vs `cda11a9cf2a04daac8f19d67b1e8ffb9ac4bd48018da38647a6de36230c48503`
- `geo-kpi-only/get_response_curves__curves_channel_subset.json` -- fixture_hash: `c8f19ddaf8e0955594d898149735a60c4836a7ccd057eb6af2f9a6b7e690ce30` vs `cda11a9cf2a04daac8f19d67b1e8ffb9ac4bd48018da38647a6de36230c48503`
- `geo-kpi-only/get_response_curves__response_curve_summary.json` -- fixture_hash: `c8f19ddaf8e0955594d898149735a60c4836a7ccd057eb6af2f9a6b7e690ce30` vs `cda11a9cf2a04daac8f19d67b1e8ffb9ac4bd48018da38647a6de36230c48503`
- `geo-kpi-only/get_response_curves__response_curves.json` -- fixture_hash: `c8f19ddaf8e0955594d898149735a60c4836a7ccd057eb6af2f9a6b7e690ce30` vs `cda11a9cf2a04daac8f19d67b1e8ffb9ac4bd48018da38647a6de36230c48503`
- `geo-kpi-only/get_spend_scenario__default_base.json` -- fixture_hash: `c8f19ddaf8e0955594d898149735a60c4836a7ccd057eb6af2f9a6b7e690ce30` vs `cda11a9cf2a04daac8f19d67b1e8ffb9ac4bd48018da38647a6de36230c48503`
- `geo-kpi-only/get_spend_scenario__explicit_base_spend.json` -- fixture_hash: `c8f19ddaf8e0955594d898149735a60c4836a7ccd057eb6af2f9a6b7e690ce30` vs `cda11a9cf2a04daac8f19d67b1e8ffb9ac4bd48018da38647a6de36230c48503`
- `geo-kpi-only/get_spend_scenario__large_increase.json` -- fixture_hash: `c8f19ddaf8e0955594d898149735a60c4836a7ccd057eb6af2f9a6b7e690ce30` vs `cda11a9cf2a04daac8f19d67b1e8ffb9ac4bd48018da38647a6de36230c48503`
- `geo-kpi-only/get_spend_scenario__tiny_increase.json` -- fixture_hash: `c8f19ddaf8e0955594d898149735a60c4836a7ccd057eb6af2f9a6b7e690ce30` vs `cda11a9cf2a04daac8f19d67b1e8ffb9ac4bd48018da38647a6de36230c48503`
- `geo-kpi-only/get_spend_scenario__zero_denominator.json` -- fixture_hash: `c8f19ddaf8e0955594d898149735a60c4836a7ccd057eb6af2f9a6b7e690ce30` vs `cda11a9cf2a04daac8f19d67b1e8ffb9ac4bd48018da38647a6de36230c48503`
- `geo-kpi-only/list_models__default.json` -- fixture_hash: `c8f19ddaf8e0955594d898149735a60c4836a7ccd057eb6af2f9a6b7e690ce30` vs `cda11a9cf2a04daac8f19d67b1e8ffb9ac4bd48018da38647a6de36230c48503`
- `geo-kpi-rpk/get_adstock_decay__adstock_decay.json` -- fixture_hash: `75b511317c7620c00a037834083656d54e1058e1efc1686048469dfbaf218eae` vs `903744b023a9b2fccc2ca136018a6ef4bd875eba55f03541b03902883c9304b3`
- `geo-kpi-rpk/get_adstock_decay__alpha_summary.json` -- fixture_hash: `75b511317c7620c00a037834083656d54e1058e1efc1686048469dfbaf218eae` vs `903744b023a9b2fccc2ca136018a6ef4bd875eba55f03541b03902883c9304b3`
- `geo-kpi-rpk/get_channel_summary__baseline_summary_metrics.json` -- fixture_hash: `75b511317c7620c00a037834083656d54e1058e1efc1686048469dfbaf218eae` vs `903744b023a9b2fccc2ca136018a6ef4bd875eba55f03541b03902883c9304b3`
- `geo-kpi-rpk/get_channel_summary__cpik.json` -- fixture_hash: `75b511317c7620c00a037834083656d54e1058e1efc1686048469dfbaf218eae` vs `903744b023a9b2fccc2ca136018a6ef4bd875eba55f03541b03902883c9304b3`
- `geo-kpi-rpk/get_channel_summary__cpik_channel_subset.json` -- fixture_hash: `75b511317c7620c00a037834083656d54e1058e1efc1686048469dfbaf218eae` vs `903744b023a9b2fccc2ca136018a6ef4bd875eba55f03541b03902883c9304b3`
- `geo-kpi-rpk/get_channel_summary__cpik_date_window.json` -- fixture_hash: `75b511317c7620c00a037834083656d54e1058e1efc1686048469dfbaf218eae` vs `903744b023a9b2fccc2ca136018a6ef4bd875eba55f03541b03902883c9304b3`
- `geo-kpi-rpk/get_channel_summary__marginal_cpik.json` -- fixture_hash: `75b511317c7620c00a037834083656d54e1058e1efc1686048469dfbaf218eae` vs `903744b023a9b2fccc2ca136018a6ef4bd875eba55f03541b03902883c9304b3`
- `geo-kpi-rpk/get_channel_summary__marginal_roi.json` -- fixture_hash: `75b511317c7620c00a037834083656d54e1058e1efc1686048469dfbaf218eae` vs `903744b023a9b2fccc2ca136018a6ef4bd875eba55f03541b03902883c9304b3`
- `geo-kpi-rpk/get_channel_summary__paid_summary_metrics.json` -- fixture_hash: `75b511317c7620c00a037834083656d54e1058e1efc1686048469dfbaf218eae` vs `903744b023a9b2fccc2ca136018a6ef4bd875eba55f03541b03902883c9304b3`
- `geo-kpi-rpk/get_channel_summary__roi.json` -- fixture_hash: `75b511317c7620c00a037834083656d54e1058e1efc1686048469dfbaf218eae` vs `903744b023a9b2fccc2ca136018a6ef4bd875eba55f03541b03902883c9304b3`
- `geo-kpi-rpk/get_contribution__by_time_date_window.json` -- fixture_hash: `75b511317c7620c00a037834083656d54e1058e1efc1686048469dfbaf218eae` vs `903744b023a9b2fccc2ca136018a6ef4bd875eba55f03541b03902883c9304b3`
- `geo-kpi-rpk/get_contribution__contribution_metrics.json` -- fixture_hash: `75b511317c7620c00a037834083656d54e1058e1efc1686048469dfbaf218eae` vs `903744b023a9b2fccc2ca136018a6ef4bd875eba55f03541b03902883c9304b3`
- `geo-kpi-rpk/get_contribution__contribution_metrics_by_time.json` -- fixture_hash: `75b511317c7620c00a037834083656d54e1058e1efc1686048469dfbaf218eae` vs `903744b023a9b2fccc2ca136018a6ef4bd875eba55f03541b03902883c9304b3`
- `geo-kpi-rpk/get_model_fit__date_window.json` -- fixture_hash: `75b511317c7620c00a037834083656d54e1058e1efc1686048469dfbaf218eae` vs `903744b023a9b2fccc2ca136018a6ef4bd875eba55f03541b03902883c9304b3`
- `geo-kpi-rpk/get_model_fit__default.json` -- fixture_hash: `75b511317c7620c00a037834083656d54e1058e1efc1686048469dfbaf218eae` vs `903744b023a9b2fccc2ca136018a6ef4bd875eba55f03541b03902883c9304b3`
- `geo-kpi-rpk/get_model_fit__geo_filter.json` -- fixture_hash: `75b511317c7620c00a037834083656d54e1058e1efc1686048469dfbaf218eae` vs `903744b023a9b2fccc2ca136018a6ef4bd875eba55f03541b03902883c9304b3`
- `geo-kpi-rpk/get_model_fit__use_kpi.json` -- fixture_hash: `75b511317c7620c00a037834083656d54e1058e1efc1686048469dfbaf218eae` vs `903744b023a9b2fccc2ca136018a6ef4bd875eba55f03541b03902883c9304b3`
- `geo-kpi-rpk/get_reach_frequency__date_window.json` -- fixture_hash: `75b511317c7620c00a037834083656d54e1058e1efc1686048469dfbaf218eae` vs `903744b023a9b2fccc2ca136018a6ef4bd875eba55f03541b03902883c9304b3`
- `geo-kpi-rpk/get_reach_frequency__default.json` -- fixture_hash: `75b511317c7620c00a037834083656d54e1058e1efc1686048469dfbaf218eae` vs `903744b023a9b2fccc2ca136018a6ef4bd875eba55f03541b03902883c9304b3`
- `geo-kpi-rpk/get_response_curves__curves_channel_subset.json` -- fixture_hash: `75b511317c7620c00a037834083656d54e1058e1efc1686048469dfbaf218eae` vs `903744b023a9b2fccc2ca136018a6ef4bd875eba55f03541b03902883c9304b3`
- `geo-kpi-rpk/get_response_curves__response_curve_summary.json` -- fixture_hash: `75b511317c7620c00a037834083656d54e1058e1efc1686048469dfbaf218eae` vs `903744b023a9b2fccc2ca136018a6ef4bd875eba55f03541b03902883c9304b3`
- `geo-kpi-rpk/get_response_curves__response_curves.json` -- fixture_hash: `75b511317c7620c00a037834083656d54e1058e1efc1686048469dfbaf218eae` vs `903744b023a9b2fccc2ca136018a6ef4bd875eba55f03541b03902883c9304b3`
- `geo-kpi-rpk/get_spend_scenario__default_base.json` -- fixture_hash: `75b511317c7620c00a037834083656d54e1058e1efc1686048469dfbaf218eae` vs `903744b023a9b2fccc2ca136018a6ef4bd875eba55f03541b03902883c9304b3`
- `geo-kpi-rpk/get_spend_scenario__explicit_base_spend.json` -- fixture_hash: `75b511317c7620c00a037834083656d54e1058e1efc1686048469dfbaf218eae` vs `903744b023a9b2fccc2ca136018a6ef4bd875eba55f03541b03902883c9304b3`
- `geo-kpi-rpk/get_spend_scenario__large_increase.json` -- fixture_hash: `75b511317c7620c00a037834083656d54e1058e1efc1686048469dfbaf218eae` vs `903744b023a9b2fccc2ca136018a6ef4bd875eba55f03541b03902883c9304b3`
- `geo-kpi-rpk/get_spend_scenario__tiny_increase.json` -- fixture_hash: `75b511317c7620c00a037834083656d54e1058e1efc1686048469dfbaf218eae` vs `903744b023a9b2fccc2ca136018a6ef4bd875eba55f03541b03902883c9304b3`
- `geo-kpi-rpk/get_spend_scenario__zero_denominator.json` -- fixture_hash: `75b511317c7620c00a037834083656d54e1058e1efc1686048469dfbaf218eae` vs `903744b023a9b2fccc2ca136018a6ef4bd875eba55f03541b03902883c9304b3`
- `geo-kpi-rpk/list_models__default.json` -- fixture_hash: `75b511317c7620c00a037834083656d54e1058e1efc1686048469dfbaf218eae` vs `903744b023a9b2fccc2ca136018a6ef4bd875eba55f03541b03902883c9304b3`
- `geo-revenue-media-only/get_adstock_decay__adstock_decay.json` -- fixture_hash: `7ef7d1c1ccdcdfed512c01e63e85a4d91f6a4ed00c85dbeb5e712c9b36494233` vs `2b2cb2b21dad6665d936b679211688c71e5228db98cd9a147405deb3bd556934`
- `geo-revenue-media-only/get_adstock_decay__alpha_summary.json` -- fixture_hash: `7ef7d1c1ccdcdfed512c01e63e85a4d91f6a4ed00c85dbeb5e712c9b36494233` vs `2b2cb2b21dad6665d936b679211688c71e5228db98cd9a147405deb3bd556934`
- `geo-revenue-media-only/get_channel_summary__baseline_summary_metrics.json` -- fixture_hash: `7ef7d1c1ccdcdfed512c01e63e85a4d91f6a4ed00c85dbeb5e712c9b36494233` vs `2b2cb2b21dad6665d936b679211688c71e5228db98cd9a147405deb3bd556934`
- `geo-revenue-media-only/get_channel_summary__cpik.json` -- fixture_hash: `7ef7d1c1ccdcdfed512c01e63e85a4d91f6a4ed00c85dbeb5e712c9b36494233` vs `2b2cb2b21dad6665d936b679211688c71e5228db98cd9a147405deb3bd556934`
- `geo-revenue-media-only/get_channel_summary__cpik_channel_subset.json` -- fixture_hash: `7ef7d1c1ccdcdfed512c01e63e85a4d91f6a4ed00c85dbeb5e712c9b36494233` vs `2b2cb2b21dad6665d936b679211688c71e5228db98cd9a147405deb3bd556934`
- `geo-revenue-media-only/get_channel_summary__cpik_date_window.json` -- fixture_hash: `7ef7d1c1ccdcdfed512c01e63e85a4d91f6a4ed00c85dbeb5e712c9b36494233` vs `2b2cb2b21dad6665d936b679211688c71e5228db98cd9a147405deb3bd556934`
- `geo-revenue-media-only/get_channel_summary__marginal_cpik.json` -- fixture_hash: `7ef7d1c1ccdcdfed512c01e63e85a4d91f6a4ed00c85dbeb5e712c9b36494233` vs `2b2cb2b21dad6665d936b679211688c71e5228db98cd9a147405deb3bd556934`
- `geo-revenue-media-only/get_channel_summary__marginal_roi.json` -- fixture_hash: `7ef7d1c1ccdcdfed512c01e63e85a4d91f6a4ed00c85dbeb5e712c9b36494233` vs `2b2cb2b21dad6665d936b679211688c71e5228db98cd9a147405deb3bd556934`
- `geo-revenue-media-only/get_channel_summary__paid_summary_metrics.json` -- fixture_hash: `7ef7d1c1ccdcdfed512c01e63e85a4d91f6a4ed00c85dbeb5e712c9b36494233` vs `2b2cb2b21dad6665d936b679211688c71e5228db98cd9a147405deb3bd556934`
- `geo-revenue-media-only/get_channel_summary__roi.json` -- fixture_hash: `7ef7d1c1ccdcdfed512c01e63e85a4d91f6a4ed00c85dbeb5e712c9b36494233` vs `2b2cb2b21dad6665d936b679211688c71e5228db98cd9a147405deb3bd556934`
- `geo-revenue-media-only/get_contribution__by_time_date_window.json` -- fixture_hash: `7ef7d1c1ccdcdfed512c01e63e85a4d91f6a4ed00c85dbeb5e712c9b36494233` vs `2b2cb2b21dad6665d936b679211688c71e5228db98cd9a147405deb3bd556934`
- `geo-revenue-media-only/get_contribution__contribution_metrics.json` -- fixture_hash: `7ef7d1c1ccdcdfed512c01e63e85a4d91f6a4ed00c85dbeb5e712c9b36494233` vs `2b2cb2b21dad6665d936b679211688c71e5228db98cd9a147405deb3bd556934`
- `geo-revenue-media-only/get_contribution__contribution_metrics_by_time.json` -- fixture_hash: `7ef7d1c1ccdcdfed512c01e63e85a4d91f6a4ed00c85dbeb5e712c9b36494233` vs `2b2cb2b21dad6665d936b679211688c71e5228db98cd9a147405deb3bd556934`
- `geo-revenue-media-only/get_model_fit__date_window.json` -- fixture_hash: `7ef7d1c1ccdcdfed512c01e63e85a4d91f6a4ed00c85dbeb5e712c9b36494233` vs `2b2cb2b21dad6665d936b679211688c71e5228db98cd9a147405deb3bd556934`
- `geo-revenue-media-only/get_model_fit__default.json` -- fixture_hash: `7ef7d1c1ccdcdfed512c01e63e85a4d91f6a4ed00c85dbeb5e712c9b36494233` vs `2b2cb2b21dad6665d936b679211688c71e5228db98cd9a147405deb3bd556934`
- `geo-revenue-media-only/get_model_fit__geo_filter.json` -- fixture_hash: `7ef7d1c1ccdcdfed512c01e63e85a4d91f6a4ed00c85dbeb5e712c9b36494233` vs `2b2cb2b21dad6665d936b679211688c71e5228db98cd9a147405deb3bd556934`
- `geo-revenue-media-only/get_model_fit__use_kpi.json` -- fixture_hash: `7ef7d1c1ccdcdfed512c01e63e85a4d91f6a4ed00c85dbeb5e712c9b36494233` vs `2b2cb2b21dad6665d936b679211688c71e5228db98cd9a147405deb3bd556934`
- `geo-revenue-media-only/get_response_curves__curves_channel_subset.json` -- fixture_hash: `7ef7d1c1ccdcdfed512c01e63e85a4d91f6a4ed00c85dbeb5e712c9b36494233` vs `2b2cb2b21dad6665d936b679211688c71e5228db98cd9a147405deb3bd556934`
- `geo-revenue-media-only/get_response_curves__response_curve_summary.json` -- fixture_hash: `7ef7d1c1ccdcdfed512c01e63e85a4d91f6a4ed00c85dbeb5e712c9b36494233` vs `2b2cb2b21dad6665d936b679211688c71e5228db98cd9a147405deb3bd556934`
- `geo-revenue-media-only/get_response_curves__response_curves.json` -- fixture_hash: `7ef7d1c1ccdcdfed512c01e63e85a4d91f6a4ed00c85dbeb5e712c9b36494233` vs `2b2cb2b21dad6665d936b679211688c71e5228db98cd9a147405deb3bd556934`
- `geo-revenue-media-only/get_spend_scenario__default_base.json` -- fixture_hash: `7ef7d1c1ccdcdfed512c01e63e85a4d91f6a4ed00c85dbeb5e712c9b36494233` vs `2b2cb2b21dad6665d936b679211688c71e5228db98cd9a147405deb3bd556934`
- `geo-revenue-media-only/get_spend_scenario__explicit_base_spend.json` -- fixture_hash: `7ef7d1c1ccdcdfed512c01e63e85a4d91f6a4ed00c85dbeb5e712c9b36494233` vs `2b2cb2b21dad6665d936b679211688c71e5228db98cd9a147405deb3bd556934`
- `geo-revenue-media-only/get_spend_scenario__large_increase.json` -- fixture_hash: `7ef7d1c1ccdcdfed512c01e63e85a4d91f6a4ed00c85dbeb5e712c9b36494233` vs `2b2cb2b21dad6665d936b679211688c71e5228db98cd9a147405deb3bd556934`
- `geo-revenue-media-only/get_spend_scenario__tiny_increase.json` -- fixture_hash: `7ef7d1c1ccdcdfed512c01e63e85a4d91f6a4ed00c85dbeb5e712c9b36494233` vs `2b2cb2b21dad6665d936b679211688c71e5228db98cd9a147405deb3bd556934`
- `geo-revenue-media-only/get_spend_scenario__zero_denominator.json` -- fixture_hash: `7ef7d1c1ccdcdfed512c01e63e85a4d91f6a4ed00c85dbeb5e712c9b36494233` vs `2b2cb2b21dad6665d936b679211688c71e5228db98cd9a147405deb3bd556934`
- `geo-revenue-media-only/list_models__default.json` -- fixture_hash: `7ef7d1c1ccdcdfed512c01e63e85a4d91f6a4ed00c85dbeb5e712c9b36494233` vs `2b2cb2b21dad6665d936b679211688c71e5228db98cd9a147405deb3bd556934`
- `geo-revenue/get_adstock_decay__adstock_decay.json` -- fixture_hash: `3e45f6b150e0d0bab59dd2e52a182e1a229363ec156593fb21444dd6d8ff19e7` vs `c01684c12a30853fdbde93b8a77f4ec4f85e433fe314fe30b968d3d5350f97c3`
- `geo-revenue/get_adstock_decay__alpha_summary.json` -- fixture_hash: `3e45f6b150e0d0bab59dd2e52a182e1a229363ec156593fb21444dd6d8ff19e7` vs `c01684c12a30853fdbde93b8a77f4ec4f85e433fe314fe30b968d3d5350f97c3`
- `geo-revenue/get_channel_summary__baseline_summary_metrics.json` -- fixture_hash: `3e45f6b150e0d0bab59dd2e52a182e1a229363ec156593fb21444dd6d8ff19e7` vs `c01684c12a30853fdbde93b8a77f4ec4f85e433fe314fe30b968d3d5350f97c3`
- `geo-revenue/get_channel_summary__cpik.json` -- fixture_hash: `3e45f6b150e0d0bab59dd2e52a182e1a229363ec156593fb21444dd6d8ff19e7` vs `c01684c12a30853fdbde93b8a77f4ec4f85e433fe314fe30b968d3d5350f97c3`
- `geo-revenue/get_channel_summary__cpik_channel_subset.json` -- fixture_hash: `3e45f6b150e0d0bab59dd2e52a182e1a229363ec156593fb21444dd6d8ff19e7` vs `c01684c12a30853fdbde93b8a77f4ec4f85e433fe314fe30b968d3d5350f97c3`
- `geo-revenue/get_channel_summary__cpik_date_window.json` -- fixture_hash: `3e45f6b150e0d0bab59dd2e52a182e1a229363ec156593fb21444dd6d8ff19e7` vs `c01684c12a30853fdbde93b8a77f4ec4f85e433fe314fe30b968d3d5350f97c3`
- `geo-revenue/get_channel_summary__marginal_cpik.json` -- fixture_hash: `3e45f6b150e0d0bab59dd2e52a182e1a229363ec156593fb21444dd6d8ff19e7` vs `c01684c12a30853fdbde93b8a77f4ec4f85e433fe314fe30b968d3d5350f97c3`
- `geo-revenue/get_channel_summary__marginal_roi.json` -- fixture_hash: `3e45f6b150e0d0bab59dd2e52a182e1a229363ec156593fb21444dd6d8ff19e7` vs `c01684c12a30853fdbde93b8a77f4ec4f85e433fe314fe30b968d3d5350f97c3`
- `geo-revenue/get_channel_summary__paid_summary_metrics.json` -- fixture_hash: `3e45f6b150e0d0bab59dd2e52a182e1a229363ec156593fb21444dd6d8ff19e7` vs `c01684c12a30853fdbde93b8a77f4ec4f85e433fe314fe30b968d3d5350f97c3`
- `geo-revenue/get_channel_summary__roi.json` -- fixture_hash: `3e45f6b150e0d0bab59dd2e52a182e1a229363ec156593fb21444dd6d8ff19e7` vs `c01684c12a30853fdbde93b8a77f4ec4f85e433fe314fe30b968d3d5350f97c3`
- `geo-revenue/get_contribution__by_time_date_window.json` -- fixture_hash: `3e45f6b150e0d0bab59dd2e52a182e1a229363ec156593fb21444dd6d8ff19e7` vs `c01684c12a30853fdbde93b8a77f4ec4f85e433fe314fe30b968d3d5350f97c3`
- `geo-revenue/get_contribution__contribution_metrics.json` -- fixture_hash: `3e45f6b150e0d0bab59dd2e52a182e1a229363ec156593fb21444dd6d8ff19e7` vs `c01684c12a30853fdbde93b8a77f4ec4f85e433fe314fe30b968d3d5350f97c3`
- `geo-revenue/get_contribution__contribution_metrics_by_time.json` -- fixture_hash: `3e45f6b150e0d0bab59dd2e52a182e1a229363ec156593fb21444dd6d8ff19e7` vs `c01684c12a30853fdbde93b8a77f4ec4f85e433fe314fe30b968d3d5350f97c3`
- `geo-revenue/get_model_fit__date_window.json` -- fixture_hash: `3e45f6b150e0d0bab59dd2e52a182e1a229363ec156593fb21444dd6d8ff19e7` vs `c01684c12a30853fdbde93b8a77f4ec4f85e433fe314fe30b968d3d5350f97c3`
- `geo-revenue/get_model_fit__default.json` -- fixture_hash: `3e45f6b150e0d0bab59dd2e52a182e1a229363ec156593fb21444dd6d8ff19e7` vs `c01684c12a30853fdbde93b8a77f4ec4f85e433fe314fe30b968d3d5350f97c3`
- `geo-revenue/get_model_fit__geo_filter.json` -- fixture_hash: `3e45f6b150e0d0bab59dd2e52a182e1a229363ec156593fb21444dd6d8ff19e7` vs `c01684c12a30853fdbde93b8a77f4ec4f85e433fe314fe30b968d3d5350f97c3`
- `geo-revenue/get_model_fit__use_kpi.json` -- fixture_hash: `3e45f6b150e0d0bab59dd2e52a182e1a229363ec156593fb21444dd6d8ff19e7` vs `c01684c12a30853fdbde93b8a77f4ec4f85e433fe314fe30b968d3d5350f97c3`
- `geo-revenue/get_reach_frequency__date_window.json` -- fixture_hash: `3e45f6b150e0d0bab59dd2e52a182e1a229363ec156593fb21444dd6d8ff19e7` vs `c01684c12a30853fdbde93b8a77f4ec4f85e433fe314fe30b968d3d5350f97c3`
- `geo-revenue/get_reach_frequency__default.json` -- fixture_hash: `3e45f6b150e0d0bab59dd2e52a182e1a229363ec156593fb21444dd6d8ff19e7` vs `c01684c12a30853fdbde93b8a77f4ec4f85e433fe314fe30b968d3d5350f97c3`
- `geo-revenue/get_response_curves__curves_channel_subset.json` -- fixture_hash: `3e45f6b150e0d0bab59dd2e52a182e1a229363ec156593fb21444dd6d8ff19e7` vs `c01684c12a30853fdbde93b8a77f4ec4f85e433fe314fe30b968d3d5350f97c3`
- `geo-revenue/get_response_curves__response_curve_summary.json` -- fixture_hash: `3e45f6b150e0d0bab59dd2e52a182e1a229363ec156593fb21444dd6d8ff19e7` vs `c01684c12a30853fdbde93b8a77f4ec4f85e433fe314fe30b968d3d5350f97c3`
- `geo-revenue/get_response_curves__response_curves.json` -- fixture_hash: `3e45f6b150e0d0bab59dd2e52a182e1a229363ec156593fb21444dd6d8ff19e7` vs `c01684c12a30853fdbde93b8a77f4ec4f85e433fe314fe30b968d3d5350f97c3`
- `geo-revenue/get_spend_scenario__default_base.json` -- fixture_hash: `3e45f6b150e0d0bab59dd2e52a182e1a229363ec156593fb21444dd6d8ff19e7` vs `c01684c12a30853fdbde93b8a77f4ec4f85e433fe314fe30b968d3d5350f97c3`
- `geo-revenue/get_spend_scenario__explicit_base_spend.json` -- fixture_hash: `3e45f6b150e0d0bab59dd2e52a182e1a229363ec156593fb21444dd6d8ff19e7` vs `c01684c12a30853fdbde93b8a77f4ec4f85e433fe314fe30b968d3d5350f97c3`
- `geo-revenue/get_spend_scenario__large_increase.json` -- fixture_hash: `3e45f6b150e0d0bab59dd2e52a182e1a229363ec156593fb21444dd6d8ff19e7` vs `c01684c12a30853fdbde93b8a77f4ec4f85e433fe314fe30b968d3d5350f97c3`
- `geo-revenue/get_spend_scenario__tiny_increase.json` -- fixture_hash: `3e45f6b150e0d0bab59dd2e52a182e1a229363ec156593fb21444dd6d8ff19e7` vs `c01684c12a30853fdbde93b8a77f4ec4f85e433fe314fe30b968d3d5350f97c3`
- `geo-revenue/get_spend_scenario__zero_denominator.json` -- fixture_hash: `3e45f6b150e0d0bab59dd2e52a182e1a229363ec156593fb21444dd6d8ff19e7` vs `c01684c12a30853fdbde93b8a77f4ec4f85e433fe314fe30b968d3d5350f97c3`
- `geo-revenue/lifecycle__status_result_reuse_delete.json` -- fixture_hash: `3e45f6b150e0d0bab59dd2e52a182e1a229363ec156593fb21444dd6d8ff19e7` vs `c01684c12a30853fdbde93b8a77f4ec4f85e433fe314fe30b968d3d5350f97c3`
- `geo-revenue/list_models__default.json` -- fixture_hash: `3e45f6b150e0d0bab59dd2e52a182e1a229363ec156593fb21444dd6d8ff19e7` vs `c01684c12a30853fdbde93b8a77f4ec4f85e433fe314fe30b968d3d5350f97c3`
- `geo-revenue/run_future_optimization__cost_multipliers.json` -- fixture_hash: `3e45f6b150e0d0bab59dd2e52a182e1a229363ec156593fb21444dd6d8ff19e7` vs `c01684c12a30853fdbde93b8a77f4ec4f85e433fe314fe30b968d3d5350f97c3`
- `geo-revenue/run_future_optimization__planned_allocation_long_horizon.json` -- fixture_hash: `3e45f6b150e0d0bab59dd2e52a182e1a229363ec156593fb21444dd6d8ff19e7` vs `c01684c12a30853fdbde93b8a77f4ec4f85e433fe314fe30b968d3d5350f97c3`
- `geo-revenue/run_future_optimization__reference_full_history_average.json` -- fixture_hash: `3e45f6b150e0d0bab59dd2e52a182e1a229363ec156593fb21444dd6d8ff19e7` vs `c01684c12a30853fdbde93b8a77f4ec4f85e433fe314fe30b968d3d5350f97c3`
- `geo-revenue/run_future_optimization__reference_same_period_last_year.json` -- fixture_hash: `3e45f6b150e0d0bab59dd2e52a182e1a229363ec156593fb21444dd6d8ff19e7` vs `c01684c12a30853fdbde93b8a77f4ec4f85e433fe314fe30b968d3d5350f97c3`
- `geo-revenue/run_future_optimization__reference_trailing.json` -- fixture_hash: `3e45f6b150e0d0bab59dd2e52a182e1a229363ec156593fb21444dd6d8ff19e7` vs `c01684c12a30853fdbde93b8a77f4ec4f85e433fe314fe30b968d3d5350f97c3`
- `geo-revenue/run_future_optimization__revenue_per_kpi_multiplier.json` -- fixture_hash: `3e45f6b150e0d0bab59dd2e52a182e1a229363ec156593fb21444dd6d8ff19e7` vs `c01684c12a30853fdbde93b8a77f4ec4f85e433fe314fe30b968d3d5350f97c3`
- `geo-revenue/run_optimization__date_window.json` -- fixture_hash: `3e45f6b150e0d0bab59dd2e52a182e1a229363ec156593fb21444dd6d8ff19e7` vs `c01684c12a30853fdbde93b8a77f4ec4f85e433fe314fe30b968d3d5350f97c3`
- `geo-revenue/run_optimization__fixed_budget.json` -- fixture_hash: `3e45f6b150e0d0bab59dd2e52a182e1a229363ec156593fb21444dd6d8ff19e7` vs `c01684c12a30853fdbde93b8a77f4ec4f85e433fe314fe30b968d3d5350f97c3`
- `geo-revenue/run_optimization__geo_subset.json` -- fixture_hash: `3e45f6b150e0d0bab59dd2e52a182e1a229363ec156593fb21444dd6d8ff19e7` vs `c01684c12a30853fdbde93b8a77f4ec4f85e433fe314fe30b968d3d5350f97c3`
- `geo-revenue/run_optimization__spend_constraints.json` -- fixture_hash: `3e45f6b150e0d0bab59dd2e52a182e1a229363ec156593fb21444dd6d8ff19e7` vs `c01684c12a30853fdbde93b8a77f4ec4f85e433fe314fe30b968d3d5350f97c3`
- `geo-revenue/run_optimization__target_mroas.json` -- fixture_hash: `3e45f6b150e0d0bab59dd2e52a182e1a229363ec156593fb21444dd6d8ff19e7` vs `c01684c12a30853fdbde93b8a77f4ec4f85e433fe314fe30b968d3d5350f97c3`
- `geo-revenue/run_optimization__target_roas.json` -- fixture_hash: `3e45f6b150e0d0bab59dd2e52a182e1a229363ec156593fb21444dd6d8ff19e7` vs `c01684c12a30853fdbde93b8a77f4ec4f85e433fe314fe30b968d3d5350f97c3`
- `national-kpi-only/get_adstock_decay__adstock_decay.json` -- fixture_hash: `2e8c9119be566ccdc29769b7266c6d4b6a5e2608281fbbf094a914bf6344f81b` vs `6b02c9b7e3ed5bdd189f073327b600b550a34eadd2fdc872b31fd5e3fdece564`
- `national-kpi-only/get_adstock_decay__alpha_summary.json` -- fixture_hash: `2e8c9119be566ccdc29769b7266c6d4b6a5e2608281fbbf094a914bf6344f81b` vs `6b02c9b7e3ed5bdd189f073327b600b550a34eadd2fdc872b31fd5e3fdece564`
- `national-kpi-only/get_channel_summary__baseline_summary_metrics.json` -- fixture_hash: `2e8c9119be566ccdc29769b7266c6d4b6a5e2608281fbbf094a914bf6344f81b` vs `6b02c9b7e3ed5bdd189f073327b600b550a34eadd2fdc872b31fd5e3fdece564`
- `national-kpi-only/get_channel_summary__cpik.json` -- fixture_hash: `2e8c9119be566ccdc29769b7266c6d4b6a5e2608281fbbf094a914bf6344f81b` vs `6b02c9b7e3ed5bdd189f073327b600b550a34eadd2fdc872b31fd5e3fdece564`
- `national-kpi-only/get_channel_summary__cpik_channel_subset.json` -- fixture_hash: `2e8c9119be566ccdc29769b7266c6d4b6a5e2608281fbbf094a914bf6344f81b` vs `6b02c9b7e3ed5bdd189f073327b600b550a34eadd2fdc872b31fd5e3fdece564`
- `national-kpi-only/get_channel_summary__cpik_date_window.json` -- fixture_hash: `2e8c9119be566ccdc29769b7266c6d4b6a5e2608281fbbf094a914bf6344f81b` vs `6b02c9b7e3ed5bdd189f073327b600b550a34eadd2fdc872b31fd5e3fdece564`
- `national-kpi-only/get_channel_summary__marginal_cpik.json` -- fixture_hash: `2e8c9119be566ccdc29769b7266c6d4b6a5e2608281fbbf094a914bf6344f81b` vs `6b02c9b7e3ed5bdd189f073327b600b550a34eadd2fdc872b31fd5e3fdece564`
- `national-kpi-only/get_channel_summary__paid_summary_metrics.json` -- fixture_hash: `2e8c9119be566ccdc29769b7266c6d4b6a5e2608281fbbf094a914bf6344f81b` vs `6b02c9b7e3ed5bdd189f073327b600b550a34eadd2fdc872b31fd5e3fdece564`
- `national-kpi-only/get_contribution__by_time_date_window.json` -- fixture_hash: `2e8c9119be566ccdc29769b7266c6d4b6a5e2608281fbbf094a914bf6344f81b` vs `6b02c9b7e3ed5bdd189f073327b600b550a34eadd2fdc872b31fd5e3fdece564`
- `national-kpi-only/get_contribution__contribution_metrics.json` -- fixture_hash: `2e8c9119be566ccdc29769b7266c6d4b6a5e2608281fbbf094a914bf6344f81b` vs `6b02c9b7e3ed5bdd189f073327b600b550a34eadd2fdc872b31fd5e3fdece564`
- `national-kpi-only/get_contribution__contribution_metrics_by_time.json` -- fixture_hash: `2e8c9119be566ccdc29769b7266c6d4b6a5e2608281fbbf094a914bf6344f81b` vs `6b02c9b7e3ed5bdd189f073327b600b550a34eadd2fdc872b31fd5e3fdece564`
- `national-kpi-only/get_model_fit__date_window.json` -- fixture_hash: `2e8c9119be566ccdc29769b7266c6d4b6a5e2608281fbbf094a914bf6344f81b` vs `6b02c9b7e3ed5bdd189f073327b600b550a34eadd2fdc872b31fd5e3fdece564`
- `national-kpi-only/get_model_fit__default.json` -- fixture_hash: `2e8c9119be566ccdc29769b7266c6d4b6a5e2608281fbbf094a914bf6344f81b` vs `6b02c9b7e3ed5bdd189f073327b600b550a34eadd2fdc872b31fd5e3fdece564`
- `national-kpi-only/get_model_fit__use_kpi.json` -- fixture_hash: `2e8c9119be566ccdc29769b7266c6d4b6a5e2608281fbbf094a914bf6344f81b` vs `6b02c9b7e3ed5bdd189f073327b600b550a34eadd2fdc872b31fd5e3fdece564`
- `national-kpi-only/get_reach_frequency__date_window.json` -- fixture_hash: `2e8c9119be566ccdc29769b7266c6d4b6a5e2608281fbbf094a914bf6344f81b` vs `6b02c9b7e3ed5bdd189f073327b600b550a34eadd2fdc872b31fd5e3fdece564`
- `national-kpi-only/get_reach_frequency__default.json` -- fixture_hash: `2e8c9119be566ccdc29769b7266c6d4b6a5e2608281fbbf094a914bf6344f81b` vs `6b02c9b7e3ed5bdd189f073327b600b550a34eadd2fdc872b31fd5e3fdece564`
- `national-kpi-only/get_response_curves__curves_channel_subset.json` -- fixture_hash: `2e8c9119be566ccdc29769b7266c6d4b6a5e2608281fbbf094a914bf6344f81b` vs `6b02c9b7e3ed5bdd189f073327b600b550a34eadd2fdc872b31fd5e3fdece564`
- `national-kpi-only/get_response_curves__response_curve_summary.json` -- fixture_hash: `2e8c9119be566ccdc29769b7266c6d4b6a5e2608281fbbf094a914bf6344f81b` vs `6b02c9b7e3ed5bdd189f073327b600b550a34eadd2fdc872b31fd5e3fdece564`
- `national-kpi-only/get_response_curves__response_curves.json` -- fixture_hash: `2e8c9119be566ccdc29769b7266c6d4b6a5e2608281fbbf094a914bf6344f81b` vs `6b02c9b7e3ed5bdd189f073327b600b550a34eadd2fdc872b31fd5e3fdece564`
- `national-kpi-only/get_spend_scenario__default_base.json` -- fixture_hash: `2e8c9119be566ccdc29769b7266c6d4b6a5e2608281fbbf094a914bf6344f81b` vs `6b02c9b7e3ed5bdd189f073327b600b550a34eadd2fdc872b31fd5e3fdece564`
- `national-kpi-only/get_spend_scenario__explicit_base_spend.json` -- fixture_hash: `2e8c9119be566ccdc29769b7266c6d4b6a5e2608281fbbf094a914bf6344f81b` vs `6b02c9b7e3ed5bdd189f073327b600b550a34eadd2fdc872b31fd5e3fdece564`
- `national-kpi-only/get_spend_scenario__large_increase.json` -- fixture_hash: `2e8c9119be566ccdc29769b7266c6d4b6a5e2608281fbbf094a914bf6344f81b` vs `6b02c9b7e3ed5bdd189f073327b600b550a34eadd2fdc872b31fd5e3fdece564`
- `national-kpi-only/get_spend_scenario__tiny_increase.json` -- fixture_hash: `2e8c9119be566ccdc29769b7266c6d4b6a5e2608281fbbf094a914bf6344f81b` vs `6b02c9b7e3ed5bdd189f073327b600b550a34eadd2fdc872b31fd5e3fdece564`
- `national-kpi-only/get_spend_scenario__zero_denominator.json` -- fixture_hash: `2e8c9119be566ccdc29769b7266c6d4b6a5e2608281fbbf094a914bf6344f81b` vs `6b02c9b7e3ed5bdd189f073327b600b550a34eadd2fdc872b31fd5e3fdece564`
- `national-kpi-only/list_models__default.json` -- fixture_hash: `2e8c9119be566ccdc29769b7266c6d4b6a5e2608281fbbf094a914bf6344f81b` vs `6b02c9b7e3ed5bdd189f073327b600b550a34eadd2fdc872b31fd5e3fdece564`
- `national-kpi-rpk/get_adstock_decay__adstock_decay.json` -- fixture_hash: `fb552a059d560168558c717a52082ba9c5ca1524f642e5d1837e4cb18b4f4c5b` vs `b498805c6e3ca2bebd8c543f8327f71a5c41fd2949924c60dcfacd5998d05e7e`
- `national-kpi-rpk/get_adstock_decay__alpha_summary.json` -- fixture_hash: `fb552a059d560168558c717a52082ba9c5ca1524f642e5d1837e4cb18b4f4c5b` vs `b498805c6e3ca2bebd8c543f8327f71a5c41fd2949924c60dcfacd5998d05e7e`
- `national-kpi-rpk/get_channel_summary__baseline_summary_metrics.json` -- fixture_hash: `fb552a059d560168558c717a52082ba9c5ca1524f642e5d1837e4cb18b4f4c5b` vs `b498805c6e3ca2bebd8c543f8327f71a5c41fd2949924c60dcfacd5998d05e7e`
- `national-kpi-rpk/get_channel_summary__cpik.json` -- fixture_hash: `fb552a059d560168558c717a52082ba9c5ca1524f642e5d1837e4cb18b4f4c5b` vs `b498805c6e3ca2bebd8c543f8327f71a5c41fd2949924c60dcfacd5998d05e7e`
- `national-kpi-rpk/get_channel_summary__cpik_channel_subset.json` -- fixture_hash: `fb552a059d560168558c717a52082ba9c5ca1524f642e5d1837e4cb18b4f4c5b` vs `b498805c6e3ca2bebd8c543f8327f71a5c41fd2949924c60dcfacd5998d05e7e`
- `national-kpi-rpk/get_channel_summary__cpik_date_window.json` -- fixture_hash: `fb552a059d560168558c717a52082ba9c5ca1524f642e5d1837e4cb18b4f4c5b` vs `b498805c6e3ca2bebd8c543f8327f71a5c41fd2949924c60dcfacd5998d05e7e`
- `national-kpi-rpk/get_channel_summary__marginal_cpik.json` -- fixture_hash: `fb552a059d560168558c717a52082ba9c5ca1524f642e5d1837e4cb18b4f4c5b` vs `b498805c6e3ca2bebd8c543f8327f71a5c41fd2949924c60dcfacd5998d05e7e`
- `national-kpi-rpk/get_channel_summary__marginal_roi.json` -- fixture_hash: `fb552a059d560168558c717a52082ba9c5ca1524f642e5d1837e4cb18b4f4c5b` vs `b498805c6e3ca2bebd8c543f8327f71a5c41fd2949924c60dcfacd5998d05e7e`
- `national-kpi-rpk/get_channel_summary__paid_summary_metrics.json` -- fixture_hash: `fb552a059d560168558c717a52082ba9c5ca1524f642e5d1837e4cb18b4f4c5b` vs `b498805c6e3ca2bebd8c543f8327f71a5c41fd2949924c60dcfacd5998d05e7e`
- `national-kpi-rpk/get_channel_summary__roi.json` -- fixture_hash: `fb552a059d560168558c717a52082ba9c5ca1524f642e5d1837e4cb18b4f4c5b` vs `b498805c6e3ca2bebd8c543f8327f71a5c41fd2949924c60dcfacd5998d05e7e`
- `national-kpi-rpk/get_contribution__by_time_date_window.json` -- fixture_hash: `fb552a059d560168558c717a52082ba9c5ca1524f642e5d1837e4cb18b4f4c5b` vs `b498805c6e3ca2bebd8c543f8327f71a5c41fd2949924c60dcfacd5998d05e7e`
- `national-kpi-rpk/get_contribution__contribution_metrics.json` -- fixture_hash: `fb552a059d560168558c717a52082ba9c5ca1524f642e5d1837e4cb18b4f4c5b` vs `b498805c6e3ca2bebd8c543f8327f71a5c41fd2949924c60dcfacd5998d05e7e`
- `national-kpi-rpk/get_contribution__contribution_metrics_by_time.json` -- fixture_hash: `fb552a059d560168558c717a52082ba9c5ca1524f642e5d1837e4cb18b4f4c5b` vs `b498805c6e3ca2bebd8c543f8327f71a5c41fd2949924c60dcfacd5998d05e7e`
- `national-kpi-rpk/get_model_fit__date_window.json` -- fixture_hash: `fb552a059d560168558c717a52082ba9c5ca1524f642e5d1837e4cb18b4f4c5b` vs `b498805c6e3ca2bebd8c543f8327f71a5c41fd2949924c60dcfacd5998d05e7e`
- `national-kpi-rpk/get_model_fit__default.json` -- fixture_hash: `fb552a059d560168558c717a52082ba9c5ca1524f642e5d1837e4cb18b4f4c5b` vs `b498805c6e3ca2bebd8c543f8327f71a5c41fd2949924c60dcfacd5998d05e7e`
- `national-kpi-rpk/get_model_fit__use_kpi.json` -- fixture_hash: `fb552a059d560168558c717a52082ba9c5ca1524f642e5d1837e4cb18b4f4c5b` vs `b498805c6e3ca2bebd8c543f8327f71a5c41fd2949924c60dcfacd5998d05e7e`
- `national-kpi-rpk/get_reach_frequency__date_window.json` -- fixture_hash: `fb552a059d560168558c717a52082ba9c5ca1524f642e5d1837e4cb18b4f4c5b` vs `b498805c6e3ca2bebd8c543f8327f71a5c41fd2949924c60dcfacd5998d05e7e`
- `national-kpi-rpk/get_reach_frequency__default.json` -- fixture_hash: `fb552a059d560168558c717a52082ba9c5ca1524f642e5d1837e4cb18b4f4c5b` vs `b498805c6e3ca2bebd8c543f8327f71a5c41fd2949924c60dcfacd5998d05e7e`
- `national-kpi-rpk/get_response_curves__curves_channel_subset.json` -- fixture_hash: `fb552a059d560168558c717a52082ba9c5ca1524f642e5d1837e4cb18b4f4c5b` vs `b498805c6e3ca2bebd8c543f8327f71a5c41fd2949924c60dcfacd5998d05e7e`
- `national-kpi-rpk/get_response_curves__response_curve_summary.json` -- fixture_hash: `fb552a059d560168558c717a52082ba9c5ca1524f642e5d1837e4cb18b4f4c5b` vs `b498805c6e3ca2bebd8c543f8327f71a5c41fd2949924c60dcfacd5998d05e7e`
- `national-kpi-rpk/get_response_curves__response_curves.json` -- fixture_hash: `fb552a059d560168558c717a52082ba9c5ca1524f642e5d1837e4cb18b4f4c5b` vs `b498805c6e3ca2bebd8c543f8327f71a5c41fd2949924c60dcfacd5998d05e7e`
- `national-kpi-rpk/get_spend_scenario__default_base.json` -- fixture_hash: `fb552a059d560168558c717a52082ba9c5ca1524f642e5d1837e4cb18b4f4c5b` vs `b498805c6e3ca2bebd8c543f8327f71a5c41fd2949924c60dcfacd5998d05e7e`
- `national-kpi-rpk/get_spend_scenario__explicit_base_spend.json` -- fixture_hash: `fb552a059d560168558c717a52082ba9c5ca1524f642e5d1837e4cb18b4f4c5b` vs `b498805c6e3ca2bebd8c543f8327f71a5c41fd2949924c60dcfacd5998d05e7e`
- `national-kpi-rpk/get_spend_scenario__large_increase.json` -- fixture_hash: `fb552a059d560168558c717a52082ba9c5ca1524f642e5d1837e4cb18b4f4c5b` vs `b498805c6e3ca2bebd8c543f8327f71a5c41fd2949924c60dcfacd5998d05e7e`
- `national-kpi-rpk/get_spend_scenario__tiny_increase.json` -- fixture_hash: `fb552a059d560168558c717a52082ba9c5ca1524f642e5d1837e4cb18b4f4c5b` vs `b498805c6e3ca2bebd8c543f8327f71a5c41fd2949924c60dcfacd5998d05e7e`
- `national-kpi-rpk/get_spend_scenario__zero_denominator.json` -- fixture_hash: `fb552a059d560168558c717a52082ba9c5ca1524f642e5d1837e4cb18b4f4c5b` vs `b498805c6e3ca2bebd8c543f8327f71a5c41fd2949924c60dcfacd5998d05e7e`
- `national-kpi-rpk/list_models__default.json` -- fixture_hash: `fb552a059d560168558c717a52082ba9c5ca1524f642e5d1837e4cb18b4f4c5b` vs `b498805c6e3ca2bebd8c543f8327f71a5c41fd2949924c60dcfacd5998d05e7e`
- `national-revenue/get_adstock_decay__adstock_decay.json` -- fixture_hash: `038f9fa42f86599f0e8d272ab194c277a2d243e9682b0d74d2217e2ed0b4604c` vs `3010feb75b88cf76558b2e9a2109cf5931c682fbb798b3cc2cadb453fa91a373`
- `national-revenue/get_adstock_decay__alpha_summary.json` -- fixture_hash: `038f9fa42f86599f0e8d272ab194c277a2d243e9682b0d74d2217e2ed0b4604c` vs `3010feb75b88cf76558b2e9a2109cf5931c682fbb798b3cc2cadb453fa91a373`
- `national-revenue/get_channel_summary__baseline_summary_metrics.json` -- fixture_hash: `038f9fa42f86599f0e8d272ab194c277a2d243e9682b0d74d2217e2ed0b4604c` vs `3010feb75b88cf76558b2e9a2109cf5931c682fbb798b3cc2cadb453fa91a373`
- `national-revenue/get_channel_summary__cpik.json` -- fixture_hash: `038f9fa42f86599f0e8d272ab194c277a2d243e9682b0d74d2217e2ed0b4604c` vs `3010feb75b88cf76558b2e9a2109cf5931c682fbb798b3cc2cadb453fa91a373`
- `national-revenue/get_channel_summary__cpik_channel_subset.json` -- fixture_hash: `038f9fa42f86599f0e8d272ab194c277a2d243e9682b0d74d2217e2ed0b4604c` vs `3010feb75b88cf76558b2e9a2109cf5931c682fbb798b3cc2cadb453fa91a373`
- `national-revenue/get_channel_summary__cpik_date_window.json` -- fixture_hash: `038f9fa42f86599f0e8d272ab194c277a2d243e9682b0d74d2217e2ed0b4604c` vs `3010feb75b88cf76558b2e9a2109cf5931c682fbb798b3cc2cadb453fa91a373`
- `national-revenue/get_channel_summary__marginal_cpik.json` -- fixture_hash: `038f9fa42f86599f0e8d272ab194c277a2d243e9682b0d74d2217e2ed0b4604c` vs `3010feb75b88cf76558b2e9a2109cf5931c682fbb798b3cc2cadb453fa91a373`
- `national-revenue/get_channel_summary__marginal_roi.json` -- fixture_hash: `038f9fa42f86599f0e8d272ab194c277a2d243e9682b0d74d2217e2ed0b4604c` vs `3010feb75b88cf76558b2e9a2109cf5931c682fbb798b3cc2cadb453fa91a373`
- `national-revenue/get_channel_summary__paid_summary_metrics.json` -- fixture_hash: `038f9fa42f86599f0e8d272ab194c277a2d243e9682b0d74d2217e2ed0b4604c` vs `3010feb75b88cf76558b2e9a2109cf5931c682fbb798b3cc2cadb453fa91a373`
- `national-revenue/get_channel_summary__roi.json` -- fixture_hash: `038f9fa42f86599f0e8d272ab194c277a2d243e9682b0d74d2217e2ed0b4604c` vs `3010feb75b88cf76558b2e9a2109cf5931c682fbb798b3cc2cadb453fa91a373`
- `national-revenue/get_contribution__by_time_date_window.json` -- fixture_hash: `038f9fa42f86599f0e8d272ab194c277a2d243e9682b0d74d2217e2ed0b4604c` vs `3010feb75b88cf76558b2e9a2109cf5931c682fbb798b3cc2cadb453fa91a373`
- `national-revenue/get_contribution__contribution_metrics.json` -- fixture_hash: `038f9fa42f86599f0e8d272ab194c277a2d243e9682b0d74d2217e2ed0b4604c` vs `3010feb75b88cf76558b2e9a2109cf5931c682fbb798b3cc2cadb453fa91a373`
- `national-revenue/get_contribution__contribution_metrics_by_time.json` -- fixture_hash: `038f9fa42f86599f0e8d272ab194c277a2d243e9682b0d74d2217e2ed0b4604c` vs `3010feb75b88cf76558b2e9a2109cf5931c682fbb798b3cc2cadb453fa91a373`
- `national-revenue/get_model_fit__date_window.json` -- fixture_hash: `038f9fa42f86599f0e8d272ab194c277a2d243e9682b0d74d2217e2ed0b4604c` vs `3010feb75b88cf76558b2e9a2109cf5931c682fbb798b3cc2cadb453fa91a373`
- `national-revenue/get_model_fit__default.json` -- fixture_hash: `038f9fa42f86599f0e8d272ab194c277a2d243e9682b0d74d2217e2ed0b4604c` vs `3010feb75b88cf76558b2e9a2109cf5931c682fbb798b3cc2cadb453fa91a373`
- `national-revenue/get_model_fit__use_kpi.json` -- fixture_hash: `038f9fa42f86599f0e8d272ab194c277a2d243e9682b0d74d2217e2ed0b4604c` vs `3010feb75b88cf76558b2e9a2109cf5931c682fbb798b3cc2cadb453fa91a373`
- `national-revenue/get_reach_frequency__date_window.json` -- fixture_hash: `038f9fa42f86599f0e8d272ab194c277a2d243e9682b0d74d2217e2ed0b4604c` vs `3010feb75b88cf76558b2e9a2109cf5931c682fbb798b3cc2cadb453fa91a373`
- `national-revenue/get_reach_frequency__default.json` -- fixture_hash: `038f9fa42f86599f0e8d272ab194c277a2d243e9682b0d74d2217e2ed0b4604c` vs `3010feb75b88cf76558b2e9a2109cf5931c682fbb798b3cc2cadb453fa91a373`
- `national-revenue/get_response_curves__curves_channel_subset.json` -- fixture_hash: `038f9fa42f86599f0e8d272ab194c277a2d243e9682b0d74d2217e2ed0b4604c` vs `3010feb75b88cf76558b2e9a2109cf5931c682fbb798b3cc2cadb453fa91a373`
- `national-revenue/get_response_curves__response_curve_summary.json` -- fixture_hash: `038f9fa42f86599f0e8d272ab194c277a2d243e9682b0d74d2217e2ed0b4604c` vs `3010feb75b88cf76558b2e9a2109cf5931c682fbb798b3cc2cadb453fa91a373`
- `national-revenue/get_response_curves__response_curves.json` -- fixture_hash: `038f9fa42f86599f0e8d272ab194c277a2d243e9682b0d74d2217e2ed0b4604c` vs `3010feb75b88cf76558b2e9a2109cf5931c682fbb798b3cc2cadb453fa91a373`
- `national-revenue/get_spend_scenario__default_base.json` -- fixture_hash: `038f9fa42f86599f0e8d272ab194c277a2d243e9682b0d74d2217e2ed0b4604c` vs `3010feb75b88cf76558b2e9a2109cf5931c682fbb798b3cc2cadb453fa91a373`
- `national-revenue/get_spend_scenario__explicit_base_spend.json` -- fixture_hash: `038f9fa42f86599f0e8d272ab194c277a2d243e9682b0d74d2217e2ed0b4604c` vs `3010feb75b88cf76558b2e9a2109cf5931c682fbb798b3cc2cadb453fa91a373`
- `national-revenue/get_spend_scenario__large_increase.json` -- fixture_hash: `038f9fa42f86599f0e8d272ab194c277a2d243e9682b0d74d2217e2ed0b4604c` vs `3010feb75b88cf76558b2e9a2109cf5931c682fbb798b3cc2cadb453fa91a373`
- `national-revenue/get_spend_scenario__tiny_increase.json` -- fixture_hash: `038f9fa42f86599f0e8d272ab194c277a2d243e9682b0d74d2217e2ed0b4604c` vs `3010feb75b88cf76558b2e9a2109cf5931c682fbb798b3cc2cadb453fa91a373`
- `national-revenue/get_spend_scenario__zero_denominator.json` -- fixture_hash: `038f9fa42f86599f0e8d272ab194c277a2d243e9682b0d74d2217e2ed0b4604c` vs `3010feb75b88cf76558b2e9a2109cf5931c682fbb798b3cc2cadb453fa91a373`
- `national-revenue/lifecycle__status_result_reuse_delete.json` -- fixture_hash: `038f9fa42f86599f0e8d272ab194c277a2d243e9682b0d74d2217e2ed0b4604c` vs `3010feb75b88cf76558b2e9a2109cf5931c682fbb798b3cc2cadb453fa91a373`
- `national-revenue/list_models__default.json` -- fixture_hash: `038f9fa42f86599f0e8d272ab194c277a2d243e9682b0d74d2217e2ed0b4604c` vs `3010feb75b88cf76558b2e9a2109cf5931c682fbb798b3cc2cadb453fa91a373`
- `national-revenue/run_future_optimization__cost_multipliers.json` -- fixture_hash: `038f9fa42f86599f0e8d272ab194c277a2d243e9682b0d74d2217e2ed0b4604c` vs `3010feb75b88cf76558b2e9a2109cf5931c682fbb798b3cc2cadb453fa91a373`
- `national-revenue/run_future_optimization__planned_allocation_long_horizon.json` -- fixture_hash: `038f9fa42f86599f0e8d272ab194c277a2d243e9682b0d74d2217e2ed0b4604c` vs `3010feb75b88cf76558b2e9a2109cf5931c682fbb798b3cc2cadb453fa91a373`
- `national-revenue/run_future_optimization__reference_full_history_average.json` -- fixture_hash: `038f9fa42f86599f0e8d272ab194c277a2d243e9682b0d74d2217e2ed0b4604c` vs `3010feb75b88cf76558b2e9a2109cf5931c682fbb798b3cc2cadb453fa91a373`
- `national-revenue/run_future_optimization__reference_same_period_last_year.json` -- fixture_hash: `038f9fa42f86599f0e8d272ab194c277a2d243e9682b0d74d2217e2ed0b4604c` vs `3010feb75b88cf76558b2e9a2109cf5931c682fbb798b3cc2cadb453fa91a373`
- `national-revenue/run_future_optimization__reference_trailing.json` -- fixture_hash: `038f9fa42f86599f0e8d272ab194c277a2d243e9682b0d74d2217e2ed0b4604c` vs `3010feb75b88cf76558b2e9a2109cf5931c682fbb798b3cc2cadb453fa91a373`
- `national-revenue/run_future_optimization__revenue_per_kpi_multiplier.json` -- fixture_hash: `038f9fa42f86599f0e8d272ab194c277a2d243e9682b0d74d2217e2ed0b4604c` vs `3010feb75b88cf76558b2e9a2109cf5931c682fbb798b3cc2cadb453fa91a373`
- `national-revenue/run_optimization__date_window.json` -- fixture_hash: `038f9fa42f86599f0e8d272ab194c277a2d243e9682b0d74d2217e2ed0b4604c` vs `3010feb75b88cf76558b2e9a2109cf5931c682fbb798b3cc2cadb453fa91a373`
- `national-revenue/run_optimization__fixed_budget.json` -- fixture_hash: `038f9fa42f86599f0e8d272ab194c277a2d243e9682b0d74d2217e2ed0b4604c` vs `3010feb75b88cf76558b2e9a2109cf5931c682fbb798b3cc2cadb453fa91a373`
- `national-revenue/run_optimization__spend_constraints.json` -- fixture_hash: `038f9fa42f86599f0e8d272ab194c277a2d243e9682b0d74d2217e2ed0b4604c` vs `3010feb75b88cf76558b2e9a2109cf5931c682fbb798b3cc2cadb453fa91a373`
- `national-revenue/run_optimization__target_mroas.json` -- fixture_hash: `038f9fa42f86599f0e8d272ab194c277a2d243e9682b0d74d2217e2ed0b4604c` vs `3010feb75b88cf76558b2e9a2109cf5931c682fbb798b3cc2cadb453fa91a373`
- `national-revenue/run_optimization__target_roas.json` -- fixture_hash: `038f9fa42f86599f0e8d272ab194c277a2d243e9682b0d74d2217e2ed0b4604c` vs `3010feb75b88cf76558b2e9a2109cf5931c682fbb798b3cc2cadb453fa91a373`

## FAIL - structural, and REVIEW - needs a human

> **Truncated for publication.** The full per-leaf triage tables for the 4,209
> FAIL and 54,962 REVIEW findings ran to roughly 59,000 rows and 8.8 MB —
> more than the rest of this repository combined — and were machine-generated
> from scratch directories (`/tmp/d3`) that no longer exist. Every finding was
> decomposed and accounted for in the Triage section below, which is what the
> tables supported; the rows themselves carried no conclusion the Triage does
> not state. They were removed rather than kept as repository weight.

## ACKNOWLEDGED - pre-registered in acknowledged.py

_none_

## Triage

### FAIL decomposition (4209 total)

| Category | Count | Verdict |
| --- | --- | --- |
| `get_contribution` (`by_time_date_window`, `contribution_metrics`, `contribution_metrics_by_time`) | 4108 | Benign -- value-sorted list reordering |
| `run_optimization` (`/result/spend_delta/N/channel`) | 41 | Benign -- value-sorted list reordering |
| `run_future_optimization` (`/result/spend_delta/N/channel`) | 45 | Benign -- value-sorted list reordering |
| `lifecycle` (`/result/spend_delta/N/channel`, reuses `run_optimization`) | 8 | Benign -- value-sorted list reordering |
| `list_models` (`list length 8 -> 7`) | 7 | Expected -- `.pkl` removal, `national-revenue-pkl` no longer discovered |
| **Total** | **4209** | matches report verdict exactly (4108+41+45+8+7) |

**`get_contribution` (4108).** Verified directly against the fixtures under
`/tmp/d3/`: `national-revenue/get_contribution__contribution_metrics.json`
rows are strictly descending by `incremental_outcome` in both labels (e.g.
`v2.0-jax-sup`: `baseline` 1228.47 -> `ch_0` 234.831 -> `rf_ch_1` 99.1363 ->
... -> `non_media_0` -78.2609; `v2.0-refit`: `baseline` 458.819 -> `rf_ch_0`
413.991 -> `ch_1` 322.397 -> ...). `row_count` is 9 in both, and the row
*set* is identical -- only rank order moved because the refit posterior
changed each channel's incremental_outcome magnitude. The differ compares
list positions with no notion of a value-sorted list, so every reordered
row reports as a structural FAIL on `/rows/N/1` (the channel-name cell).
This is expected behaviour under resampling, confirmed for `by_time` too.

**`run_optimization` / `run_future_optimization` / `lifecycle` (94
combined).** All 94 of these FAILs land on exactly one pointer shape:
`/result/spend_delta/N/channel` -- confirmed by grepping every FAIL row for
these three tools and normalizing the numeric index (94/94 match). This is
the same phenomenon as `get_contribution`: `OptimizerFacade._spend_delta`
(`src/google_meridian_mcp_server/meridian/optimizer_facade.py:519-534`)
sorts channels by spend delta (negative ascending, then positive
descending), not by channel identity. Verified directly against three
sampled fixtures (`geo-revenue/run_optimization__date_window.json`,
`geo-revenue/run_future_optimization__cost_multipliers.json`,
`geo-revenue/lifecycle__status_result_reuse_delete.json`): in every case
the channel *set* is identical between labels, both orderings independently
satisfy the sort invariant (negatives ascending then non-negatives
descending), and only the ranking changed because the refit posterior moved
the deltas. `lifecycle` FAILs are the same finding surfacing through a
`run_optimization` call embedded in a lifecycle scenario, not a separate
bug. **None of the 94 is a genuine structural break** -- no case-set
difference, no type change, no missing case.

**`list_models` (7).** `list length 8 -> 7` on every geo/national variant
that still exists. This is `.pkl` support removal working as designed --
see `reports/drift/07-refit-notes.md` ("Seven fixtures were refitted, not
eight... `national-revenue-pkl` variant" removed after commit `c7c4f0d`
proved Meridian 2.0/JAX cannot run inference on a TF-pickled model). Expected
and correct, not a regression.

**Cross-reference to `07-refit-notes.md`, §2.2 (channel coordinate
reordering).** That report explicitly checked for and did **not observe**
coordinate-order changes: `geo-kpi-only__get_channel_data.json`, a
2185-row ordered array taken directly from tensor/coordinate order (not a
value-sorted output), diffed byte-identical in position and value against
the refit fixture. That rules out the concerning failure mode this task was
watching for -- the reordering seen in `get_contribution` /
`run_optimization` / `run_future_optimization` / `lifecycle` above is
value-sort reordering in this server's own facade code (by design, to show
"biggest mover first"), entirely independent of and consistent with
"channel coordinate order unchanged" from §2.2. **No structural break was
found among the 4209 FAILs.**

### Tolerance calibration

Extracted per Task 20 Step 5 (`grep -o "relative delta ..." | awk`), over
all 54962 REVIEWs (52688 numeric "over tolerance" + 2274 "sign flip",
which together account for the total exactly):

```
n=52688 min=1.010e-03 p01=1.382e-02 p10=1.542e-01 p25=4.000e-01
        p50=6.291e-01 p75=9.060e-01 p90=9.697e-01 p95=9.800e-01
        p99=9.995e-01 max=1.000e+00
```

`min` sits just above `REL_TOLERANCE` (1e-3) by construction -- everything
below it already passed silently. The distribution has **no pileup near the
current threshold**: p01 is already 13.8x the tolerance, and the median
relative delta is 0.629 -- i.e. half of all flagged values differ by more
than 63% of their own magnitude between an original-seed fit and a
refit. These are not small values inflating a ratio: sampled `over
tolerance` rows for `get_adstock_decay` show genuine curve-shape changes at
substantive magnitudes (e.g. `0.886065 -> 0.499055`, `0.616398 ->
0.0634727`), and sampled `sign flip` rows for `get_model_fit` show large
residual reversals (`-123.024 -> 0.710635`, `45.5411 -> -35.3828`) -- the
opposite of the diff #2 near-zero-noise pattern.

To rule out contamination from the sort-order FAILs above (comparing
`/rows/N/2` for a channel at position N in one label against a *different*
channel at the same position in the other), the same percentiles were
recomputed excluding `get_contribution` / `run_optimization` /
`run_future_optimization` / `lifecycle` entirely -- i.e. only tools with
stable row order (`get_reach_frequency`, `get_model_fit`,
`get_adstock_decay`, `get_response_curves`, `get_spend_scenario`,
`get_channel_summary`, 30812 REVIEWs):

```
n=29560 min=1.010e-03 p10=1.004e-01 p25=3.218e-01 p50=5.652e-01
        p75=8.671e-01 p90=9.694e-01 p95=9.863e-01 p99=9.999e-01 max=1.0
```

Materially the same shape (p50=0.565, p90=0.969). The huge REVIEW volume is
**not** an artifact of row misalignment -- it is a real property of a
10-draw, 1-chain fixture posterior: individual leaf values (decay curves,
reach-frequency curves, model-fit residuals, response curves) genuinely
swing by 50-100% of their own scale when refit from a different seed.

**Recommendation: keep `REL_TOLERANCE = 1e-3` and `ABS_FLOOR = 1e-9`
unchanged.** The brief's decision rule ("if p90 >= 1e-3, raise to the next
round value above p90") does not apply as written here, because p90 is
0.97 -- raising the tolerance to cover it would mean setting
`REL_TOLERANCE` to roughly 1.0, which does not calibrate anything; it
disables the numeric check almost entirely. Concretely: even a 10x
loosening to `1e-2` would silence only 367 of 52688 numeric REVIEWs
(0.7%, consistent with p01=1.382e-02) -- 99.3% of the report is exactly as
noisy as today. A 100x loosening to `1e-1` silences 3508 of 52688 (6.7%),
leaving 93.3% flagged (consistent with p10 = 0.100-0.154 depending on
subset). No
tolerance value in a defensible range materially reduces what a human has
to read; the only value that would is one so loose it stops catching real
regressions on the axes that matter (diff #1: 330/330 clean at 1e-3; diff
#2: FAIL 0, 7 REVIEWs, at 1e-3 -- both already comfortably within the
current tolerance, so there is no evidence 1e-3 is "too tight" for
deterministic-path comparisons). This diff's noise is a property of an
undersampled posterior (10 draws, 1 chain), not of the tolerance -- no
global float tolerance can distinguish "expected resampling variance" from
"a real regression" at this sample size, which is exactly why every one of
these findings is triaged in writing above rather than filtered away.
Per the explicit constraint on this task, tolerance is not tuned to make
this report look green. **No change made to `scripts/validation/diff_baseline.py`;
diffs #1 and #2 do not need to be rerun.**

### Verdict

FAIL 4209 / REVIEW 54962, both fully accounted for and triaged above.
**No structural regression.** The `--allow-fixture-change` exit-1 result is
expected and correct for this diff.

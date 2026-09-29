# Full-funnel performance on Google's simulated data

**Date:** 2026-09-29. **Verdict:** every analysis call finished in 5.2-7.7 s, far under the
escalation threshold; both optimizers finished in about 30 s. No mitigation was needed or attempted.

This is the spec section 8 performance check for full-funnel support: one call per tool,
plus one historical and one future optimization, on a realistic model rather than the tiny
validation fixtures.

## Setup

- **Machine:** Apple M4 Max, 14 CPU cores, 36 GiB RAM (38,654,705,664 bytes), macOS 26.7.
- **Stack:** google-meridian 2.1.0, JAX 0.11.1 on the CPU backend.
- **Data:** Google's `geo_full_funnel_data.csv` at Meridian commit `675da46`: 20 geos x 156
  weeks (3,120 rows). Paid channels `PerformanceNative`, `PerformanceDisplay`,
  `AwarenessVideo`; one brand mediator, `BrandedGQV_control`, driven by `AwarenessVideo`.
- **Fit:** two stages (mediator model, then KPI model with the mediator as organic media),
  `knots=36`, 2 chains, 300 adapt, 300 burn-in, 200 keep. The fit ran once, in about 1-2
  minutes, into the gitignored `models/_perf/`.
- **Server:** in-process FastMCP client over `create_server()`, with
  `PERSISTENCE_BACKEND=local`, `OPTIMIZATION_TIER=local`, `RESULT_CACHE_ENABLED=false`
  (so no call is served from a result cache) and a scratch `OPTIMIZATION_RUNS_ROOT`.
- **Command:** `OPTIMIZATION_TIER=local uv run python scripts/qa/full_funnel_perf.py`
  (script: `scripts/qa/full_funnel_perf.py`).
- **Escalation rule:** stop and report if any analysis call exceeds
  0.5 x `ANALYSIS_WORKER_TIMEOUT`. The configured timeout is 300 s, so the threshold is
  150 s. Slowest analysis call: 7.7 s (5% of the threshold).

## Results

Analysis calls (each is a fresh worker load of the model; caching is off):

| Call | Seconds | Response |
|---|---:|---:|
| get_model_overview | 5.4 | 6.0 KB |
| get_channel_summary paid_summary_metrics | 7.1 | 3.0 KB |
| get_channel_summary roi | 6.9 | 1.0 KB |
| get_channel_summary baseline_summary_metrics | 6.2 | 0.3 KB |
| get_contribution contribution_metrics | 6.6 | 0.5 KB |
| get_contribution contribution_metrics_by_time | 6.6 | 57.3 KB |
| get_adstock_decay adstock_decay | 5.2 | 10.9 KB |
| get_response_curves response_curves | 5.7 | 5.9 KB |
| get_spend_scenario (AwarenessVideo, +1000) | 7.5 | 0.5 KB |
| get_model_fit | 5.8 | 14.4 KB |
| get_channel_data | 5.3 | 1253.9 KB |
| get_training_data (kpi, 2021-01-25 to 2021-03-29) | 5.2 | 7.2 KB |
| get_funnel_breakdown channel_breakdown | 5.7 | 0.7 KB |
| get_funnel_breakdown mediator_lift | 6.1 | 0.3 KB |

Optimizations (submit to `completed`, polled every 2 s, `force_rerun=true`):

| Call | Seconds | Result |
|---|---:|---:|
| run_optimization fixed_budget (full history) | 31.5 | 54.9 KB |
| run_future_optimization fixed_budget, trailing reference, 13-week horizon from 2024-01-22 | 25.7 | 2.7 KB |

**Peak RSS** (this measurement run started from the already-fitted model, so it excludes
the fit): the server process peaked at 110 MB; the largest single worker subprocess
(`RUSAGE_CHILDREN`, the maximum over analysis and optimizer workers, not attributed to a
call) peaked at 1,671 MB.

## Observations

- Every analysis call costs 5-8 s regardless of payload, and the two full-funnel-specific
  calls (5.7 s, 6.1 s) sit in the same band as the single-model tools. Full-funnel adds no
  measurable analysis cost on this model.
- The fixed 5 s floor (even a 7 KB `get_training_data` slice takes 5.2 s) points at per-call
  worker start-up and model load rather than computation; that split was not profiled.
- `get_channel_data` with no filters returned 1.25 MB of JSON. It was fast, but the tool
  description already warns that unfiltered pulls are large.
- The future optimization is not slower than the historical one here (25.7 s vs 31.5 s).
- Not covered: `get_reach_frequency` (this model has no reach-and-frequency channels), the
  cloud optimization tiers, and models larger than 20 geos x 156 weeks x 3 paid channels.

## Raw output

The first invocation included the fit in the same process (all calls were within 0.5 s of
the numbers below, and none exceeded the threshold), so its RSS was inflated by the fit and
is not reported. The output below is the second invocation, from the fitted model.

```json
{
  "analysis_worker_timeout": 300.0,
  "escalation_threshold_seconds": 150.0,
  "over_threshold": [],
  "rows": [
    {
      "call": "get_model_overview {}",
      "kind": "analysis",
      "seconds": 5.4,
      "response_kb": 6.0,
      "over_threshold": false
    },
    {
      "call": "get_channel_summary {\"output_type\": \"paid_summary_metrics\"}",
      "kind": "analysis",
      "seconds": 7.1,
      "response_kb": 3.0,
      "over_threshold": false
    },
    {
      "call": "get_channel_summary {\"output_type\": \"roi\"}",
      "kind": "analysis",
      "seconds": 6.9,
      "response_kb": 1.0,
      "over_threshold": false
    },
    {
      "call": "get_channel_summary {\"output_type\": \"baseline_summary_metrics\"}",
      "kind": "analysis",
      "seconds": 6.2,
      "response_kb": 0.3,
      "over_threshold": false
    },
    {
      "call": "get_contribution {\"output_type\": \"contribution_metrics\"}",
      "kind": "analysis",
      "seconds": 6.6,
      "response_kb": 0.5,
      "over_threshold": false
    },
    {
      "call": "get_contribution {\"output_type\": \"contribution_metrics_by_time\"}",
      "kind": "analysis",
      "seconds": 6.6,
      "response_kb": 57.3,
      "over_threshold": false
    },
    {
      "call": "get_adstock_decay {\"output_type\": \"adstock_decay\"}",
      "kind": "analysis",
      "seconds": 5.2,
      "response_kb": 10.9,
      "over_threshold": false
    },
    {
      "call": "get_response_curves {\"output_type\": \"response_curves\"}",
      "kind": "analysis",
      "seconds": 5.7,
      "response_kb": 5.9,
      "over_threshold": false
    },
    {
      "call": "get_spend_scenario {\"channel\": \"AwarenessVideo\", \"spend_increase\": 1000.0}",
      "kind": "analysis",
      "seconds": 7.5,
      "response_kb": 0.5,
      "over_threshold": false
    },
    {
      "call": "get_model_fit {}",
      "kind": "analysis",
      "seconds": 5.8,
      "response_kb": 14.4,
      "over_threshold": false
    },
    {
      "call": "get_channel_data {}",
      "kind": "analysis",
      "seconds": 5.3,
      "response_kb": 1253.9,
      "over_threshold": false
    },
    {
      "call": "get_training_data {\"dataset\": [\"kpi\"], \"filters\": {\"start_date\": \"2021-01-25\", \"end_date\": \"2021-03-29\"}}",
      "kind": "analysis",
      "seconds": 5.2,
      "response_kb": 7.2,
      "over_threshold": false
    },
    {
      "call": "get_funnel_breakdown {\"output_type\": \"channel_breakdown\"}",
      "kind": "analysis",
      "seconds": 5.7,
      "response_kb": 0.7,
      "over_threshold": false
    },
    {
      "call": "get_funnel_breakdown {\"output_type\": \"mediator_lift\"}",
      "kind": "analysis",
      "seconds": 6.1,
      "response_kb": 0.3,
      "over_threshold": false
    },
    {
      "call": "run_optimization {\"scenario\": {\"type\": \"fixed_budget\"}}",
      "kind": "optimization",
      "seconds": 31.5,
      "status": "completed",
      "result_kb": 54.9
    },
    {
      "call": "run_future_optimization {\"scenario\": {\"type\": \"fixed_budget\"}, \"future\": {\"start_date\": \"2024-01-22\", \"horizon\": 13, \"reference\": {\"mode\": \"trailing\"}}}",
      "kind": "optimization",
      "seconds": 25.7,
      "status": "completed",
      "result_kb": 2.7
    }
  ],
  "peak_server_process_rss_mb": 110,
  "peak_largest_child_rss_mb": 1671
}
```

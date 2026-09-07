# What this upgrade's verification did *not* cover

The Meridian 2.0 upgrade is backed by four drift reports, a green test suite,
live validation and a live cloud deployment. This file records the gaps in that
evidence, so nobody infers more coverage than exists. It is written from the
final whole-branch review's findings.

## 1. The two environment moves were never drift-baselined

`fastmcp 3.4.7 -> 4.0.3` and `Python 3.12.8 -> 3.13.13` have **no drift
measurement at all**. Every label records the post-move environment:

```
v1.7-engine : fastmcp 4.0.3   python 3.13.13
v2.0-tf     : fastmcp 4.0.3   python 3.13.13
v2.0-jax    : fastmcp 4.0.3   python 3.13.13
v2.0-refit  : fastmcp 4.0.3   python 3.13.13
```

This is a deliberate and somewhat ironic consequence of the phase ordering. An
earlier draft of the plan put the Python bump *between* the Meridian bump and
the first baseline, which would have made diff #1 measure two variables while
claiming to isolate one. Moving both ahead of the first capture fixed that —
and in doing so removed any possibility of measuring them.

**What does back them:** the full suite green at each step (`458 passed` on
1.7+fastmcp4+py3.13 during the canary, `662 passed` after both moves),
`LIVE VALIDATION PASSED 147/0`, `FUTURE-OPT QA 11/11`, and a review of the
Python 3.13 stdlib removal list. Also verified by reading fastmcp 4's own
source that the payload-extraction precedence (`structured_content` -> `.data`
-> `content[0]`) is unchanged, which is what would otherwise have silently
altered every capture.

**What does not:** there is no leaf-by-leaf comparison of tool output across
either move. If either changed a payload subtly, this project would not have
detected it.

## 2. `excluded_channels` is absent from the drift matrix

`scripts/validation/matrix.py` contains **zero** cases exercising
`excluded_channels` or per-channel future-optimization behaviour, despite that
being the subject of the two most recent PRs before this upgrade (#5 and #6,
"Fix/future optimization exclusion cost basis"). So the most recently changed
optimization behaviour is not drift-diffed.

**Mitigating, and it matters:** `scripts/qa/future_optimization_qa.py`
references `excluded_channels` in 4 places and passes **11/11** at every gate
in this upgrade, including after the Phase 5 refit. The behaviour is therefore
live-tested; it is only absent from the *drift* comparison.

**Consequence:** a subtle change in exclusion cost-basis output between 1.7 and
2.0 would have been caught by the QA gate's assertions but not measured by a
diff. Adding the cases now would require re-capturing labels, one of which
(`v1.7-engine`) cannot be regenerated.

## 3. The cloud comparison bypassed the fixture-fingerprint gate

Drift report 4 compares 286 cases by calling `diff_baseline.diff_case`
directly, because the cloud label has no manifest (7 cases were uncapturable —
see that report). `diff_baseline`'s manifest gate, which normally refuses to
diff labels whose fixture fingerprints differ, therefore did not run.

The fixtures were not rebuilt between the local and cloud captures, and the
cloud service loads the same bytes from GCS that were uploaded from
`models/_validation/`, so the risk is low. It is nonetheless an assumption
rather than a verified fingerprint match.

## 4. `known_racy_fields` under-covers the cancel race

The `lifecycle__cancel` case declares one racy leaf (`status.status`), but the
cancel-versus-complete race can move more than one leaf in that payload. Only
the declared one is downgraded to REVIEW; another moving leaf would present as
a FAIL. No such failure was observed across five captures, but the declaration
is narrower than the race.

## 5. GPU performance is unmeasured

Both Cloud Run tiers were verified to *work* (CPU 50s compute, GPU 65s compute
on one fixture). No performance comparison was made, and the
`WeeklyOptimizationGrid` spike explicitly could not measure GPU. No
infrastructure sizing decision should be drawn from this upgrade.

## 6. `get_training_data` with no filter cannot traverse the HTTP surface

Drift report 4 (`reports/drift/04-cloud-vs-local.md`, Limitation section)
found that `get_training_data__all_datasets` -- the unfiltered call, no
`dataset` or date window -- fails against the deployed Cloud Run service in
every one of the 7 fixtures it was tried on: the server returns `200 OK` and
the SSE stream is dropped before the payload reaches the client, in ~85s,
well inside the 300s request timeout and far under both Cloud Run's 32 MiB
response ceiling and the server's own 64 MB `ANALYSIS_MAX_RESPONSE_BYTES`.
Payload sizes are 3.2 MB (`national-revenue`) to 16.3 MB (`geo-revenue`).

**Pre-existing, not caused by this upgrade:** the payload is byte-identical
across all four labels including `v1.7-engine`. It was never observed before
because this is the first time the full tool matrix was captured over HTTP
against a deployed Cloud Run service.

**Consequence:** any caller of a deployed server that invokes
`get_training_data` without a `dataset` or date filter on a model with a
large training set will see the call fail with no payload, even though the
tool works correctly in-process and over HTTP when filtered. Until the
transport path is fixed (chunking/paginating large analysis responses
instead of one SSE event), callers should always pass a `dataset` or date
filter to `get_training_data` against a deployed server. The tool description
in `src/google_meridian_mcp_server/transport/tools.py` now says this at the
point of use.

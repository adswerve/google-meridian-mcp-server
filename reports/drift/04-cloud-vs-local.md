# Drift report: local `v2.0-refit` -> deployed `v2.0-cloud-cpu`

**Verdict: PASS with two environment-inherent differences and one pre-existing
limitation. No behavioural drift attributable to the Meridian 2.0 upgrade.**

- Cases compared: **286** (**252 clean** -- byte-identical payloads)
- Findings: **84**, all in two fully-explained categories (below)
- Unexplained findings: **0**. REVIEW: **0**.
- Cases present locally but absent from the cloud capture: **7** (see Limitation)

Deployed service: `https://<service>.run.app`
(Cloud Run, `us-central1`, a development project), Meridian 2.0.0 on JAX with
64-bit precision, Python 3.13, `RESULT_CACHE_ENABLED=false` so the service
cannot serve one capture's results to the next.

## Method

`capture_baseline --transport http` drove every matrix case against the
deployed service with an identity token, writing `v2.0-cloud-cpu`. Because 7
cases could not be captured (see Limitation) the label has no `manifest.json`
-- written last, deliberately, so an incomplete capture cannot present itself
as complete -- and `diff_baseline` therefore refuses it by design.

Rather than fabricate a manifest or loosen the differ, this comparison reuses
`diff_baseline.diff_case` -- the differ's own classification logic, including
the relative-`1e-3`/absolute-`1e-9` tolerance and the `known_racy_fields`
handling -- over the 286 cases both labels hold.

## Finding 1 -- `compute_tier`: `local` -> `cloud_cpu` (56 findings)

Expected by construction. Locally `resolve_tier` returns `local`; on the
deployed service the same request resolves to `cloud_cpu`. Pointers:
`/status/compute_tier`, `/submit/compute_tier_resolved`,
`/reused/compute_tier_resolved`. This is the deployment working, not drift.

## Finding 2 -- `list_models` ordering (28 findings)

The model list holds the same set in a different order. Locally the catalog
enumerates a filesystem directory; on the service it enumerates GCS object
keys, and the two orders differ. Findings appear as positional value changes,
e.g. `/2/model_id`: `geo-revenue` -> `geo-revenue-media-only`, with the
displaced entry reappearing at another index.

The volatile-field normalization already neutralises `source_backend`
(`local`->`gcs`), `source_path`, `last_modified` and `etag_or_fingerprint`,
which is why those produce no findings at all. Ordering is not normalized
because the differ compares list positions and has no notion of an unordered
collection -- the same mechanism that surfaced value-sorted reordering in
drift report 3.

## Limitation -- `get_training_data__all_datasets` cannot traverse the HTTP surface

All 7 fixtures fail this one case over HTTP with
`MCPError: SSE stream ended without a response`. Characterised:

- **Reproducible in isolation** with a freshly minted identity token, so it is
  not token expiry.
- **Fails in ~85s**, well inside Cloud Run's 300s request timeout.
- **Payload is 3.2 MB** (`national-revenue`) to 16.3 MB (`geo-revenue`), far
  under Cloud Run's 32 MiB response ceiling, and under the server's own
  `ANALYSIS_MAX_RESPONSE_BYTES` default of 64 MB as it stood at the time of
  this capture (the shipped default has since been lowered -- see the Update
  section below).
- **The server returns `200 OK`** -- Cloud Run request logs confirm it -- and
  the stream is then dropped before the payload reaches the client. The
  failure is in the transport path, not the application.
- The three smaller `get_training_data` variants (`date_window`,
  `single_dataset`, `geo_and_channel_filter`) all capture successfully, so the
  tool itself works over HTTP; only its largest response does not.

**This is pre-existing and unrelated to the upgrade.** The payload is
byte-identical at 3260 KB across all four labels including `v1.7-engine`, so
nothing about Meridian 2.0, JAX or 64-bit precision caused it. It had simply
never been observed, because this is the first time the full tool matrix has
been captured over HTTP against Cloud Run.

Worth fixing as separate work -- at the time of this report the plausible
direction looked like chunking or paginating large analysis responses rather
than returning them as one SSE event. **A later controlled experiment
superseded this guess -- see the Update section below**, which found the
server already transmits every byte of the response successfully, so chunking
or paginating the response targets the wrong layer. Until a transport-level
fix lands, `get_training_data` over a deployed server should be called with a
dataset or date filter; unfiltered calls on large geo models will fail.

## Update -- controlled experiment supersedes the fix-direction guess

A later, dedicated two-arm Cloud Run experiment characterised this failure
directly (it was not part of the original drift-matrix capture above). It
confirms every finding in the Limitation section -- the failure is
reproducible, well inside the request timeout, and the server returns
`200 OK` -- and adds detail that changes where a fix should look:

- **The server is not at fault.** Cloud Run request logs show `200` and the
  full payload leaving: 16,408,410 B for the geo-revenue fixture, 3,279,412 B
  for the national-revenue fixture. The client receives **zero bytes**. The
  payload is lost *after* Cloud Run has already accounted for sending it --
  ruling out the response-generation layer, which is exactly why chunking or
  paginating the response (the original guess above) would not have helped.
- **It is not a timeout.** A 1-dataset call succeeded at 56.51s. The "~85s"
  figure above is analysis *compute* time on 2 vCPU, not a timer expiring --
  it scales with model size (62.8s national, 86.1s geo).
- **It is a delivery size ceiling**, not a payload-size or timeout ceiling.
  Bracketed between 46,635 B (delivered) and 3,279,412 B (not delivered) in
  one pair of trials; a finer pair of trials bracketed it between 17,870 B
  (delivered) and 5,073,288 B (not delivered). The true ceiling sits
  somewhere under ~3.3 MB.
- **Response de-duplication does not fix it**, verified against a control
  that reproduced this report's documented failure at 85.98s. De-duplication
  cuts latency by roughly a third and doubles the usable payload per unit of
  wire budget, but the ceiling is far too low for that to rescue these calls.

**Consequence for `ANALYSIS_MAX_RESPONSE_BYTES`:** the shipped default has
since been lowered from 64 MiB to 4 MiB (4194304 bytes) specifically because
of this finding, but the two are not the same thing -- the measured delivery
ceiling (under ~3.3 MB) sits below even the new 4 MiB guard, so a response
can still pass the size check and then be silently dropped in transit. See
`README.md`'s Reference section for the current, honest framing of that
guard.

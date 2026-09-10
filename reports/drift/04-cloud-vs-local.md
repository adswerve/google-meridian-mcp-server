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

## Limitation -- `get_training_data__all_datasets` over the HTTP harness (RESOLVED)

All 7 fixtures failed this one case over HTTP with
`MCPError: SSE stream ended without a response`: the server returned `200 OK` and the
full byte count, and the client received zero bytes.

**Measured cause.** Two independent conditions were both required. The server commits a
reply to a single SSE frame when the handler outlives `_SSE_PING_INTERVAL` (15s); the
Python client applies httpx2's 1 MiB per-event cap on that branch only, raising an
`SSEError` that the SDK reports as the misleading message above. Neither condition alone
fails: 32 MB succeeds when fast, and a 100s call succeeds when small. Boundaries measured
sharply at 14s/15s and at 918,069 B / 1,049,141 B (1 MiB).

The failure was never Cloud Run's, never a timeout, and never a payload-size limit. It
reproduces on localhost with no network in the path, and TypeScript clients — including
Claude Desktop and Claude Code — were never affected.

**Fixed** by `json_response=True` in `server.py`, which keeps `tools/call` replies on the
uncapped `application/json` branch. Cloud Run's 32 MiB non-streaming response limit now
applies where the SSE path was exempt; the largest real payload is ~16 MB.

> **Correction history.** Earlier revisions of this report attributed the failure to a
> Cloud Run delivery ceiling (~46 KB–3.3 MB) and then to a size ceiling, and recommended
> lowering `ANALYSIS_MAX_RESPONSE_BYTES` accordingly. Both diagnoses were disproved by
> measurement on 2026-09-09; the size and latency of every failing call were perfectly
> confounded, so the original data could not have distinguished them. The response cap
> has since been removed entirely.

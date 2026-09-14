# Durable queue / restart / cancel — live verification

**Result: PASSED.** Every phase of the `QUEUE_SMOKE=1` acceptance gate ran to
completion against a real Cloud Run deployment.

| | |
|---|---|
| Project / region | `as-dev-anze` / `us-central1` |
| Service | `meridian-mcp-server` (`https://meridian-mcp-server-px6atnevbq-uc.a.run.app`) |
| Job | `meridian-opt-cpu` |
| Model | `national-revenue` (`.binpb`) |
| Bucket | `gs://as-dev-anze-meridian-opt`, prefix `optimizations/` |
| Tier / cap | `OPTIMIZATION_TIER=cloud_cpu`, `OPTIMIZATION_MAX_PARALLEL=2` |
| Started / finished | `2026-09-11T20:18:24.814895+00:00` → `2026-09-11T20:56:07.015641+00:00` (37m42s) |
| Gate commit | `f8fa8c7` |
| `max_instance_count` | pinned **1** for the run; original value **2**, restored afterwards |

Every line quoted below is console output from that run. Nothing is
reconstructed or paraphrased.

## Prerequisites

```
prereq OK: national-revenue has a .binpb model at gs://as-dev-anze-meridian-opt/models/national-revenue/
prereq OK: no pre-existing QUEUED or RUNNING runs in this bucket
prereq OK: meridian-mcp-server max_instance_count=1 (pinned)
```

The single-instance pin matters: with `max_instance_count=2` and no session
affinity, three submissions can spread across two instances, each admitting
two, and nothing queues at all — the gate would pass while measuring nothing.

## Phase 0 — the queue on the deployed service, over HTTP, across a real revision swap

```
phase0: submitted ['national-revenue-20260911T202059-d25711', 'national-revenue-20260911T202059-e1b2a9', 'national-revenue-20260911T202156-1cc6c0'] (first two concurrently)
phase0: running=2 queued=1
phase0: non-ASCII label round-tripped byte-identical: '予算最適化 🎯'
revision confirmed swapped: '.../revisions/meridian-mcp-server-00009-x5z' -> '.../revisions/meridian-mcp-server-00010-9jf'
phase0: post-swap national-revenue-20260911T202156-1cc6c0 status=completed
```

Two runs executing, one waiting, then the serving container replaced and the
waiting run carried through to completion by a revision that never saw it
submitted. The swap is an env-var template change with the revision id
asserted before and after — not a `--update-labels` call, which is
metadata-only and would not have replaced the container.

Exercises `eab662a`, `6b28b6e`, `54ea6fd`, and folds in the free check of
`371a831` + `fa06339` (the non-ASCII label round trip).

## Phase 1 — the cap holds and the third really waits

```
phase1: PASSED peak=2 third_dispatched_while={...757ecc: 'completed', ...3a5847: 'completed', ...2901-255f2c: 'queued'}
```

`peak=2` is accumulated across the whole poll loop against *live* executions —
Cloud Run retains completed executions forever, so a cumulative count could
never distinguish "two at a time" from "three in total". The third run was
still `queued` at the moment both predecessors had completed, i.e. it was
dispatched only once a slot actually freed.

## Phase 2 — restart: rebuild the object graph over the same bucket

```
phase2: adoption confirmed by exact execution name for ['national-revenue-20260911T203638-3b8e64']; not holding a slot: [...77924a, ...b8f49e]
phase2: third run dispatched once a slot freed; new executions total=3
phase2: all three runs completed with valid results
```

**Three runs, three executions.** The rebuilt executor adopted in-flight work
by its persisted `execution_name` instead of relaunching it; a relaunch would
have produced more than three.

**This phase proved less than it was designed to.** It was meant to catch two
runs adopted simultaneously with the third held back. Only one adoption was
observed, because both slot holders finished mid-reconcile — see "Limits"
below. The adoption-by-persisted-identity property did hold, and is the core
of what the phase exists to prove.

## Phase 2b — the same proof against an actually redeployed revision

```
phase2b: slots held by [...204500-8f1d83, ...204500-d2b6ec]; ...204608-d5d58f is waiting
revision confirmed swapped: '...-00010-9jf' -> '...-00011-xbs'
phase2b: post-swap national-revenue-20260911T204608-d5d58f status=completed
phase2b: real revision swap survived; new executions={...meridian-opt-cpu-mvgl8, ...meridian-opt-cpu-hjw88, ...meridian-opt-cpu-5x2bw}
```

This is the strongest form of the claim, and the one the original requirement
asked for literally: **two runs genuinely executing and one genuinely queued at
the moment the serving container was destroyed.** The replacement revision
recovered all three from GCS alone — exactly three executions for three runs,
so nothing was relaunched and nothing was stranded.

Phase 2 proves the reconciliation logic; only Phase 2b proves that nothing
else in the deployment depends on process memory.

## Phase 3 — cancel actually cancels

```
phase3: state=running
phase3: projects/.../jobs/meridian-opt-cpu/executions/meridian-opt-cpu-stzt2 confirmed terminal after cancel-by-name
```

The run was allowed to reach a real, running Cloud Run execution before being
cancelled, and the *remote execution* was then confirmed terminal — not merely
the local record marked `canceled`. Exercises `3528ba8` (cancel by recorded
name, even when this process did not launch it).

## The two near-free checks

```
near-free: every blob for reused run national-revenue-20260911T202901-255f2c (incl. dispatch.json) confirmed deleted from GCS
near-free: national-revenue-20260911T205602-2a3346 correctly classified as cloud_job_not_found:
  {'code': 'cloud_job_not_found', 'message': "failed to launch worker: 404 Resource
   'queue-smoke-deliberately-nonexistent-job' of kind 'JOB' in region 'us-central1'
   in project 'as-dev-anze' does not exist."}
```

The first reuses a run Phase 1 already paid for rather than submitting a
fresh one. The second confirms `34e4a56`: a missing Cloud Run Job surfaces as
`cloud_job_not_found` at dispatch, with the job name in the message — not as a
late, opaque `worker_lost`.

## Terraform gating (Task 3), captured against the live project

Two checks that `terraform validate` cannot make, run against this project:

```
$ terraform show -json tfplan | grep -c CLOUD_RUN_JOB_GPU
0

$ terraform plan -var 'optimization_tier=cloud_gpu' -var 'enable_gpu_job=false'
Error: Resource precondition failed
```

The deployed service's environment contains no `CLOUD_RUN_JOB_GPU` at all, so
the server is never handed a job name that was never provisioned; and the
misconfiguration is rejected at plan time with an actionable message rather
than failing later as a runtime 404.

## Per-phase execution counts

```
phase0: {'submitted': 3, 'running_at_cap': 2, 'queued': 1, 'new_executions': 2, 'non_ascii_label_roundtrip': True, 'survived_revision_swap': True}
phase1: {'submitted': 3, 'peak_concurrent': 2, 'run_ids': [...]}
phase2: {'submitted': 3, 'adopted': 2, 'new_executions_total': 3}
phase2b: {'submitted': 3, 'new_executions_total': 3}
phase3: {'submitted': 1, 'canceled': 1}
dispatch_json_deletion: True
cloud_job_not_found: True
max_instance_count: before=1 after=1
```

13 optimizer executions in the passing run. Note `phase2`'s reported
`adopted: 2` is the phase's static descriptor, not a measurement; the run
actually observed one adoption, as the phase output above shows.

## Limits of this verification

**Phase 2 observed one adoption, not two.** Both slot holders completed while
`reconcile_orphans()` was still scanning. Working back from the 2-second
detection poll, reconciliation took roughly **70 seconds over a bucket holding
80 runs** — longer than a run lasts (~63s of execution). The gate now prints
this duration and the bucket size directly (`ee26812`, added after this run).

**`reconcile_orphans()` cost scales with bucket history, not with work in
flight.** It scans the whole prefix on every server start, so startup latency
grows with every run ever recorded. At 80 runs it already exceeds a minute.
This is pre-existing behaviour, not introduced by this branch, and is left
unfixed here — but an operator accumulating thousands of runs will see
multi-minute cold starts, and a fix (scan only non-terminal runs, or index
them) is real follow-up work.

**Three concurrent submissions OOM the service.** `Memory limit of 2048 MiB
exceeded with 2078 MiB used` — each `run_optimization` loads the model to
fingerprint it. Also pre-existing and unrelated to the queue, but it is a real
capacity limit of the deployed configuration.

**The `allUsers` invoker binding was refused** by a domain-restricted-sharing
org policy, so the service requires an identity token despite
`allow_unauthenticated = true` in Terraform. Unauthenticated requests return
`403`; the gate authenticates and re-mints the token every 45 minutes.

**The runs are short.** `national-revenue` optimizes in ~63s against Cloud Run
boot times of 2–4 minutes that vary by more than a minute between executions.
The gate now dispatches slot holders concurrently so overlap is structural
rather than probabilistic, but a larger model would make every restart phase
more comfortably provable.

## What it took to get here

The gate was written, reviewed and committed without ever being executed. Its
first live run failed, and so did the next eight. Nine defects surfaced, all
in the gate, none in the product code under test:

| Commit | Defect |
|---|---|
| `11c6cde` | Identity token pinned into a static header; expires an hour into a multi-hour gate |
| `011d5d7` | Kept using an MCP session the revision swap had destroyed |
| `f1733e9` | One transient `503` discarded every execution paid for so far |
| `a602ea5` | Read `queued` as `completed`, voiding five seconds after submitting |
| `b3e6f8f` | Assumed submission order decided which runs held slots |
| `6d7a530` | Three concurrent submits OOM'd the service |
| `f07b3e4` | Staggered dispatch left the two slot holders never concurrent |
| `ab86c9b` | Concurrent calls on one MCP session tore down each other's stream |
| `f8fa8c7` | Hard-coded *which* runs hold slots; reported correct adoption as "max_parallel is not enforced" |

The last is the one worth remembering. It failed with `the two in-flight
executions were not adopted; max_parallel is not enforced after a restart`
while its own output showed two runs correctly adopted by execution name — a
slot holder had simply finished mid-restart and handed its slot to the waiter.
Read quickly, that message accuses the durable queue of exactly the defect
this branch fixes.

The product code passed every assertion it was given, on every run.

# Research on the `meridian-analyst` skill and the skills mechanism

This is a synthesis, not a source. Follow the pointers to `reports/` for the
actual evidence. Like `references/research-deferred-work.md`, this file lives
in a gitignored directory (`.gitignore:22`) — it is local scratch material,
not a committed artifact. The durable record is `reports/skills-audit.md`.

---

## 1. The audit method — and why it's worth repeating as-is

`reports/skills-audit.md` (Task 22, Phase 6b) inventoried **35 behavioural
claims** made across `skills/meridian-analyst/SKILL.md` and its
`references/*.md` files, then verified **every one of the 35 against a real
tool call** on a running server, against the refit Meridian 2.0 fixtures —
not by reading the claim and judging whether it sounded plausible. The
mechanism: `fastmcp.Client` driving the server in-process, `uv run python -c
"..."`, across seven different fixture models chosen to exercise every axis
the skill claims matters (geo vs. national, revenue-capable vs. KPI-only,
with/without reach-and-frequency channels). Optimization calls used an
isolated scratch `OPTIMIZATION_RUNS_ROOT` so they couldn't collide with the
concurrently-running `live_validate` process.

**Result: 34 of 35 confirmed true, 1 factually wrong.** That ratio is itself
useful data — a skill this close to accurate, after a version bump the skill
was never rewritten for, means the skill's claims track the server's actual
behavior tightly enough that "read the docs and trust them" would have been
almost right, but not quite — and the one place it was wrong was
consequential enough (see below) that "almost right" was not good enough to
skip verification. That is the argument for repeating this exact method
(inventory every behavioural claim, verify each with a live call) at the next
version bump, rather than doing a lighter-touch read-through.

## 2. The one wrong claim — and why it's instructive

`skills/meridian-analyst/references/channel-performance.md` claimed that
`get_contribution`'s baseline (the organic/non-media portion of the outcome)
"is not a channel here," and that seeing it required a **second** call to
`get_channel_summary`. A real call
(`get_contribution(geo-revenue, "contribution_metrics")`, default filters)
showed this was wrong: the response already contains a `baseline` row by
default, and its value matched `get_channel_summary`'s baseline `mean`
exactly (1915.73 in the verification run). The row is present even with
`include_non_paid: false` (organic/non-media contributions simply fold into
it rather than disappearing).

Why it's instructive beyond "the docs had a bug": the stale text wasn't just
wrong, it was **actively sending every agent that followed it on an
unnecessary extra tool call** — and worse, implying the two numbers were
unrelated when they're the same quantity viewed two ways. A behavioural claim
in a skill isn't just documentation risk; it's a standing tax on every future
agent invocation until it's caught. This is the clearest argument in the
audit for why claims need re-verification against the running server, not
just a read for internal consistency.

The fix (already applied, per the audit's "Corrections made" section):
reworded both spots to say `get_contribution` already returns `baseline` by
default in one call, and that `get_channel_summary`'s baseline view is for
when you need the baseline's own credible interval, not its point share.

## 3. The one claim that could not be verified live — and why

The exact **content** of `get_optimization_status`'s `error` payload on a
genuinely **failed** optimization run was not exercised. Producing one on
purpose would have meant crafting a config designed to fail while another
process (`live_validate`) was mid-run against shared fixture paths — noise
indistinguishable from a real regression was judged not worth the risk. The
audit confirmed the `error` field's **presence** and the `failed`/`canceled`
terminal states structurally (schema + source code), just not by a live
failure round-trip. Two smaller items share this same "confirmed in source,
not round-tripped live" status: two of three `budget_source` enum values
(`explicit`, `determined_by_target` — only `derived_from_reference` was
produced by an actual run) and two of three `reference` modes
(`same_period_last_year`, `full_history_average` — only `trailing` was
exercised).

## 4. How the skill is delivered

The skill ships bundled as an Agent Skill. When a connecting MCP client
supports the skills-provider mechanism, it's discoverable at the resource URI
`skill://meridian-analyst/SKILL.md` (confirmed in `README.md`,
`src/google_meridian_mcp_server/server.py`, and exercised directly in
`tests/contract/test_skill_provider.py`, which also checks for a
`skill://meridian-analyst/_manifest` resource). For a client that doesn't
support MCP skill resources, the fallback is a manual folder-drop: copy
`skills/meridian-analyst/` into wherever the client looks for skills (e.g.
`.claude/skills/`).

This is exactly why `skills/meridian-analyst/references/` carries an explicit
un-ignore exception in `.gitignore` (line just above it: "Skill references
are served as MCP resources and must be tracked") even though the
project-root `references/` (this file's own directory) is gitignored — the
two directories with the same basename serve opposite purposes. The skill's
own `references/` must ship with the repo because clients fetch it as MCP
resources at runtime; this synthesis file's `references/` is scratch
material for humans/agents working in this worktree, never shipped anywhere.

## 5. The marketer-friendly-language rule (commit `c6e2b67`)

Commit `c6e2b67`, `docs(skill): enforce marketer-friendly language in all
user-facing output`, added a cardinal rule to the skill: never leak internal
vocabulary — MCP enum values, field names, tool names — into anything the
end user sees (questions, offers, next-step suggestions, result summaries,
caveats). The example given in the commit message: say "a
profitability-first plan," not "a `target_mroas` scenario." It touched
`SKILL.md` (new cardinal rule), `consultation.md` (broadened the in-question
jargon ban to *all* output, added a reverse "internal concept → marketer
phrasing" table covering every scenario/constraint/reference/budget_source/
async-status the model can surface), and `budget-optimization.md` /
`glossary.md` (reworded "state assumptions verbatim" so the model reads real
assumed values but presents them in plain terms). Technical metrics the
target audience already knows — ROAS, marginal ROI, CPIK — are explicitly
preserved as exceptions; enum names remain fine in the skill's own internal
teaching/routing material, just not in anything shown to the user.

Why this rule exists for this audience: the skill's target user is a
marketer making budget decisions, not an engineer inspecting the MCP
protocol. An MCP enum name or a raw field like `budget_source:
"derived_from_reference"` is meaningless to that audience and actively
undermines trust in the tool's answers. This rule is repo-wide policy for
all user-facing output from the skill, not a one-off wording fix — anyone
extending the skill with new reference material should check new text
against it.

## 6. Comparison against Meridian 2.0's own official skills

`reports/skills-audit.md` compared `meridian-analyst` against the five
official skills shipped in Meridian 2.0.0 itself
(`.agents/skills.json` and `skills/meridian_*/SKILL.md` in the upstream
`google/meridian` clone). The core conclusion: **the two are a different
genre, with no overlap in mechanism but real overlap in topic.** Meridian's
own skills guide an agent through **writing and running Meridian Python
scripts** end-to-end (load data, configure a `ModelSpec`, fit, save,
visualize, optimize, export). `meridian-analyst` guides an agent through
**calling this MCP server's tools** against a model that's already fitted —
it never builds or touches a model directly.

Topic-level mapping the audit drew:

| Meridian's skill | Nearest analogue here |
| --- | --- |
| `meridian_doc_consultant` (RAG lookup into Meridian's docs) | `glossary.md` + `taxonomy.md`, but defined inline since our tools are read-only/pre-fitted |
| `meridian_model_building` | **No analogue — out of scope for us entirely**; this server never builds or fits a model |
| `meridian_result_visualization` | `channel-performance.md`'s routing — same information, delivered as structured tool output instead of a rendered report |
| `meridian_budget_optimization` | `budget-optimization.md` — closest analogue; theirs is "write the optimizer call," ours is "call `run_optimization`, poll, read the result" |
| `meridian_scenario_planner` (Colab/Looker Studio export) | No analogue — this server has no export/downstream-deliverable concept |

One more structural difference worth carrying forward: all five of
Meridian's own skills enforce a mandatory "Interactivity Checkpoint Rule" —
structured multiple-choice check-ins at every step, via a dedicated
`ask_question`-style tool. `consultation.md` covers similar ground
(elicit → propose → confirm before a decision-heavy run) but is calibrated to
ambiguity/stakes rather than mandatory at every step, and speaks in plain
business language rather than presenting tool-call choices to the user. If
this server's client surface ever exposes an equivalent structured-checkpoint
tool, that gate design is worth revisiting — the audit flagged it as a design
decision, not a finding to act on now.

## 7. Useful for whoever extends the skill next

- The audit's own recommendations (not yet done, explicitly out of scope for
  the audit itself): add a short "what this skill is not" note pointing
  newcomers at Meridian's own `meridian_model_building` /
  `meridian_result_visualization` skills if they actually need to fit or
  re-fit a model — this skill's entire premise assumes a model already
  exists. Also: the `get_contribution` baseline-row behavior (§2 above) is
  still under-documented in the tool's own docstring in
  `src/google_meridian_mcp_server/transport/tools.py` ("Get how much each
  media channel contributed to the KPI" — silent on the baseline row) —
  worth a tool-description tweak independent of the skill text.
- The audit confirmed (by `grep -rniE
  "backend|\.pkl|MERIDIAN_BACKEND|OPTIMIZATION_BACKEND" skills/`, zero hits)
  that none of the skill's text ever referenced the backend-selection knobs
  or `.pkl` format that this upgrade removed (see
  `reports/pkl-format-removed.md`, `reports/cross-backend-gate-removed.md`)
  — so no skill-text correction was needed on that front. Worth knowing this
  was checked, not assumed.
- `taxonomy.md`'s "future optimization: two independent axes" framing
  (allocation vs. cost-structure) was verified only structurally — a
  follow-up could exercise the two untested reference modes
  (`same_period_last_year`, `full_history_average`) end-to-end for full
  symmetry with `trailing`, which was the only one actually run.
- The skill's `references/` files are the only gitignore exception in the
  whole repo — a reminder that any new skill reference file added under
  `skills/meridian-analyst/references/` is committed and shipped as an MCP
  resource, unlike everything else placed under a bare `references/` at the
  repo root.

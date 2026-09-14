"""Structural changes pre-registered as intentional (spec section 7.4).

Empty by default and shipping with exactly three entries, each carrying a
MANDATORY reason. Acknowledged findings are reported in their own section of
the diff report -- never folded into PASS -- so a reader always sees what was
waved through and why. ``reason`` is the intent (why the change is
acceptable) -- kept short, since it is rendered verbatim in the report's
Detail column; the ANCHORING mechanism (why this pointer, and not others) is
documented as a code comment below instead of inflating that text.

Fix wave 2 -- ANCHORING, corrected: neither entry's field is ever diffed at
the payload root. ``capture_baseline.py`` never snapshots a raw submit/status
envelope; every case is a COMPOSITE dict of named sub-responses:

  - ``run_optimization_case`` (tool ``run_optimization``/``run_future_optimization``):
    ``{"submit", "status", "result"}``.
  - ``run_lifecycle_case``, non-cancel scenario:
    ``{"submit", "status", "result", "reused", "listing", "deleted",
    "status_after_delete"}``.
  - ``run_lifecycle_case``, cancel scenario:
    ``{"submit", "canceled", "status", "deleted"}``.

``optimization_service.py`` puts ``backend``/``meridian_version`` as a direct
top-level key of the dict returned by ``_submit_envelope`` (~line 258) and by
``get_status`` (~line 283) -- both of which land, UNCHANGED, one level down
inside the composite case dict above. ``/backend`` at the bare root is never
actually produced by anything ``capture_baseline.py`` diffs; ``match()``
still accepts it (per this module's own documented design: the case root is
a valid parent), it is just never hit in practice.

Fix wave 3 -- NARROWED to what was actually derived, not merely enumerated.
Fix wave 2 built ``_KNOWN_ENVELOPE_KEYS`` from EVERY key any case dict uses,
which included ``result``/``listing``/``deleted``/``canceled`` even though
none of them structurally carries ``backend``/``meridian_version`` today --
an over-match by inclusion rather than derivation (if a future worker result
payload ever gained an unrelated ``backend`` key, its removal would be
silently ACKNOWLEDGED). The set below is restricted to the four keys where
the field is actually reachable, one call site at a time:

  - ``submit`` -- ``run_optimization_case``'s and ``run_lifecycle_case``'s
    (both scenarios) ``submit`` is the raw ``_submit_envelope`` result
    (optimization_service.py:258).
  - ``status`` -- both functions' ``status`` is the raw ``get_status`` result
    (optimization_service.py:283).
  - ``reused`` -- ``run_lifecycle_case``'s ``reused`` is a SECOND raw
    ``run_optimization`` tool call (a second ``_submit_envelope``,
    optimization_service.py:258 again).
  - ``status_after_delete`` -- ``run_lifecycle_case``'s (non-cancel) ``gone``
    is the SAME ``get_optimization_status`` call as ``status``, made again
    after deletion (optimization_service.py:283 again; the call normally
    errors post-delete, so this pointer is rarely hit in practice, but the
    match must not depend on that).

Excluded, and why: ``result`` is ``get_result``'s ``{run_id, **result}``
(optimization_service.py:287-289) -- the analysis result payload, no
``backend`` field. ``listing`` is ``list_optimizations``'s
``OptimizationRunSummary`` entries (:290-303) -- no ``backend`` field.
``deleted`` is ``delete_optimization``'s ``{run_id, deleted}`` (:309-316) --
no ``backend`` field. ``canceled`` is ``cancel_optimization``'s
``{run_id, status}`` (:304-308) -- no ``backend`` field. A field appearing
under any of these four is therefore an unrelated, real drift and must FAIL.

``match()`` accepts the field at the case root OR nested exactly one level
under one of the four keys above -- NOT a depth-crossing glob (which is what
over-matched in the first place: ``fnmatch``'s ``*`` crosses ``/`` and would
also reach ``domain/errors.py``'s unrelated ``backend`` key inside an error
envelope's ``details``, e.g. ``/details/backend`` -- a different,
agent-visible contract from the one these two entries describe, and that
must still FAIL). A hard-coded key set is chosen over a fully generic
depth-limited walk because these are the ONLY places the real field can
appear today; if ``capture_baseline.py``'s case shapes change, this set must
be updated deliberately, which is the point -- a silent widening is exactly
the failure mode being avoided.
"""

from __future__ import annotations

import dataclasses

# The ONLY case-dict keys where backend/meridian_version are structurally
# reachable today (see the "Fix wave 3" section of the module docstring for
# the call-site-by-call-site derivation of each). A field is acknowledged
# when it sits at the case root, or one level under one of these four --
# nowhere else, and in particular not under `result`/`listing`/`deleted`/
# `canceled`, which never carry it.
#
# `/scope`, by contrast, is root-only: it is written directly into the
# `get_adstock_decay` case payload (both output types) by the service
# itself, not nested inside a submit/status-shaped sub-envelope the way
# `backend`/`meridian_version` are. It therefore never needs -- and never
# gets -- a lookup against this set; `match()` catches it via the bare
# `pointer == entry.pointer` branch alone, before this frozenset is even
# consulted.
_KNOWN_ENVELOPE_KEYS = frozenset(
    {
        "submit",
        "status",
        "reused",
        "status_after_delete",
    }
)


@dataclasses.dataclass(frozen=True)
class Acknowledged:
    pointer: str  # the field's CANONICAL (case-root) pointer, e.g. "/backend"
    change: str  # "removed" | "added" | "changed"
    reason: str


ACKNOWLEDGED: tuple[Acknowledged, ...] = (
    Acknowledged(
        pointer="/backend",
        change="removed",
        reason=(
            "Spec 6.2/6.3: the backend knob is removed in Phase 4. JAX is the "
            "only supported backend, so a per-run `backend` field carries no "
            "information."
        ),
    ),
    Acknowledged(
        pointer="/meridian_version",
        change="added",
        reason=(
            "Spec 6.3: replaces `backend` as agent-visible provenance. It was "
            "already written to the run manifest and returned by nothing, so "
            "removing `backend` alone would have been a net LOSS of provenance."
        ),
    ),
    Acknowledged(
        pointer="/scope",
        change="added",
        reason=(
            "adstock_decay and alpha_summary now declare their scope "
            "unconditionally: the alpha posterior has dims (chain, draw, "
            "channel), so both outputs are national and time-invariant "
            "regardless of any filter, and the key is a constant per output "
            "type."
        ),
    ),
)


def match(pointer: str, change: str) -> Acknowledged | None:
    for entry in ACKNOWLEDGED:
        if entry.change != change:
            continue
        if pointer == entry.pointer:
            return entry
        if any(pointer == f"/{key}{entry.pointer}" for key in _KNOWN_ENVELOPE_KEYS):
            return entry
    return None

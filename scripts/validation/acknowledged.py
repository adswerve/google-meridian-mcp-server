"""Structural changes pre-registered as intentional (spec section 7.4).

Empty by default and shipping with exactly two entries, each carrying a
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
inside the composite case dict above. So the real pointers are
``/submit/backend``, ``/status/backend`` and ``/reused/backend`` (``reused``
wraps a second ``run_optimization`` submit call) -- and, structurally, also
``/status_after_delete/backend`` (it wraps the SAME ``get_optimization_status``
call as ``status``, just made after deletion; that call normally errors
post-delete, so this pointer is rarely hit in practice, but the match must
not depend on that). ``/backend`` at the bare root is never actually
produced by anything ``capture_baseline.py`` diffs.

``_KNOWN_ENVELOPE_KEYS`` is the closed set of composite-case keys above,
enumerated from ``capture_baseline.py``'s own case construction rather than
assumed. ``match()`` accepts the field at the case root OR nested exactly
one level under one of these keys -- NOT a depth-crossing glob (which is
what over-matched in the first place: ``fnmatch``'s ``*`` crosses ``/`` and
would also reach ``domain/errors.py``'s unrelated ``backend`` key inside an
error envelope's ``details``, e.g. ``/details/backend`` -- a different,
agent-visible contract from the one these two entries describe, and that
must still FAIL). A hard-coded key set is chosen over a fully generic
depth-limited walk because these are the ONLY places the real field can
appear today; if ``capture_baseline.py``'s case shapes change, this set must
be updated deliberately, which is the point -- a silent widening is exactly
the failure mode being avoided.
"""

from __future__ import annotations

import dataclasses

# Every top-level key any case dict in capture_baseline.py actually uses
# (see the module docstring above for the full derivation from
# run_optimization_case / run_lifecycle_case). A field is acknowledged when
# it sits at the case root, or one level under one of these -- nowhere else.
_KNOWN_ENVELOPE_KEYS = frozenset(
    {
        "submit",
        "status",
        "result",
        "reused",
        "listing",
        "deleted",
        "status_after_delete",
        "canceled",
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

"""Structural changes pre-registered as intentional (spec section 7.4).

Empty by default and shipping with exactly two entries, each carrying a
MANDATORY reason. Acknowledged findings are reported in their own section of
the diff report -- never folded into PASS -- so a reader always sees what was
waved through and why.

``pointer`` is matched with :mod:`fnmatch`, whose ``*`` crosses ``/`` (it has
no directory-boundary semantics, unlike a shell glob) -- so a pattern like
``"*/backend"`` matches ``backend`` at ANY depth, not just the one real
field it was written for. ``domain/errors.py`` happens to put an unrelated
``backend`` key inside several error envelopes' ``details`` (e.g. pointer
``/details/backend`` for a bare error payload, or nested further for one
carried in a ``get_status`` response's ``error`` field) -- a depth-crossing
glob silently ACKNOWLEDGEs that unrelated field too, which is a silent-PASS
vector for a real drift. The two entries below are therefore anchored to the
EXACT pointer of the one real field each describes: ``optimization_service.py``
returns ``backend``/``meridian_version`` as a direct top-level key of both the
submit envelope (``_submit_envelope``, ~line 258) and the ``get_status``
envelope (~line 283) -- i.e. at pointer ``/backend`` when a case's own
payload is the pointer root, which is how every case is diffed
(``diff_case`` starts each case at pointer ``""``). No entry needs a glob
character at all; if a future field genuinely needs one, add it deliberately
and narrowly, not by widening these two.
"""

from __future__ import annotations

import dataclasses
import fnmatch


@dataclasses.dataclass(frozen=True)
class Acknowledged:
    pointer: str  # fnmatch pattern over the JSON pointer -- exact, not a glob
    change: str  # "removed" | "added" | "changed"
    reason: str


ACKNOWLEDGED: tuple[Acknowledged, ...] = (
    Acknowledged(
        pointer="/backend",
        change="removed",
        reason=(
            "Spec 6.2/6.3: the backend knob is removed in Phase 4. JAX is the only "
            "supported backend, so a per-run `backend` field carries no information. "
            "Anchored to optimization_service.py's submit/get_status envelopes "
            "(the field sits at the case's own root, pointer `/backend`), NOT a "
            "glob -- a `backend` key elsewhere (e.g. an error envelope's `details`) "
            "is an unrelated field and must still FAIL."
        ),
    ),
    Acknowledged(
        pointer="/meridian_version",
        change="added",
        reason=(
            "Spec 6.3: replaces `backend` as agent-visible provenance, at the same "
            "root position (`/meridian_version`) in the same two envelopes. It was "
            "already written to the run manifest and returned by nothing, so "
            "removing `backend` alone would have been a net LOSS of provenance."
        ),
    ),
)


def match(pointer: str, change: str) -> Acknowledged | None:
    for entry in ACKNOWLEDGED:
        if entry.change == change and fnmatch.fnmatch(pointer, entry.pointer):
            return entry
    return None

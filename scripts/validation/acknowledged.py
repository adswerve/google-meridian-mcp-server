"""Structural changes pre-registered as intentional (spec section 7.4).

Empty by default and shipping with exactly two entries, each carrying a
MANDATORY reason. Acknowledged findings are reported in their own section of
the diff report -- never folded into PASS -- so a reader always sees what was
waved through and why.
"""

from __future__ import annotations

import dataclasses
import fnmatch


@dataclasses.dataclass(frozen=True)
class Acknowledged:
    pointer: str  # fnmatch glob over the JSON pointer
    change: str  # "removed" | "added" | "changed"
    reason: str


ACKNOWLEDGED: tuple[Acknowledged, ...] = (
    Acknowledged(
        pointer="*/backend",
        change="removed",
        reason=(
            "Spec 6.2/6.3: the backend knob is removed in Phase 4. JAX is the only "
            "supported backend, so a per-run `backend` field carries no information."
        ),
    ),
    Acknowledged(
        pointer="*/meridian_version",
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
        if entry.change == change and fnmatch.fnmatch(pointer, entry.pointer):
            return entry
    return None

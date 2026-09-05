"""Normalize values that are non-deterministic by construction.

Spec sections 7.3 and 7.3.1. Two families:

1. Optimization payloads carry a fresh ``run_id`` (a UTC timestamp plus
   ``secrets.token_hex(3)``), fresh timestamps, and an ``elapsed_seconds``
   float that depends on how busy the machine was.
2. ``list_models`` returns ``asdict(ModelCatalogEntry)``, which carries the
   file mtime, the source path, the source backend and an etag. The Phase 5
   refit changes every mtime; a Cloud Run capture changes the backend and
   path. Both would FAIL list_models on every variant for no real reason.

The policy is not "drop these keys". Each volatile value is replaced by a
token that encodes its TYPE, PRESENCE and NULLABILITY, so a field that
disappears, turns null, or changes type is still caught -- only its value is
waived. Deliberately NOT normalized: ``model_format``, ``status``,
``display_name`` and ``model_id``, which are stable properties of the model
and whose change would be a real finding.

Phase 0 gate (Task 8) surfaced a THIRD family the key-based scheme above
cannot reach: a volatile ``run_id`` embedded INSIDE a human-readable message
string, not carried as its own key. ``persistence/optimization_run_registry.py``
builds ``RunNotFoundError`` (:24) and ``ResultNotReadyError`` (:33) as
``f"Optimization run '{run_id}' was not found."`` /
``f"Optimization run '{run_id}' has no result yet (status={status})."`` --
the same ``run_id`` that the standalone-key branch already tokenizes when it
appears as a dict value under the literal key ``"run_id"``, but baked here
into a sentence, where a key-name check never looks. Two otherwise-identical
captures of ``get_optimization_status`` called after ``delete_optimization``
therefore FAILed on ``/status_after_delete/message`` alone (verified: Task 8
self-diff, ``smoke-a`` vs ``smoke-b``, 2026-09-05).

``run_id`` is built in ``services/optimization_service.py`` (:227) as
``f"{_slug(model_id)}-{utcnow:%Y%m%dT%H%M%S}-{secrets.token_hex(3)}"`` --
``_slug`` only replaces ``/`` with ``-`` (:44-45), so the model-id prefix is
deterministic; only the trailing ``YYYYMMDDTHHMMSS-hex6`` suffix is volatile.
``_RUN_ID_SUFFIX_RE`` below matches exactly that suffix (8 digits, literal
``T``, 6 digits, hyphen, 6 lowercase hex digits) and nothing looser: no other
string this server emits has that 14-character shape (ISO 8601 timestamps
elsewhere use dashes inside the date, e.g. ``2026-09-04T10:15:00+00:00``, so
they never contain 8 consecutive digits before a bare ``T``). The
substitution therefore does NOT waive the field wholesale -- unlike adding
``message`` to ``VOLATILE_FIELDS`` would, which would also hide a genuinely
CHANGED error message (a real finding this harness exists to catch). It
targets only the specific volatile substring, leaves the surrounding
sentence (and the deterministic model-id prefix) intact, and is applied to
every string leaf in the payload, not just ones under a ``"message"`` key --
the id could in principle be embedded in any free-text field, not only the
two known error messages above.
"""

from __future__ import annotations

import re
from typing import Any

# See the "Phase 0 gate" section of this module's docstring for the
# construction this is anchored to and why it cannot false-positive on
# anything else this server emits.
_RUN_ID_SUFFIX_RE = re.compile(r"\d{8}T\d{6}-[0-9a-f]{6}")
_RUN_ID_SUFFIX_TOKEN = "<run_id>"  # same token as the standalone `run_id` key

VOLATILE_FIELDS: dict[str, str] = {
    # Optimization payloads.
    "run_id": "<run_id>",
    "created_at": "<iso8601>",
    "heartbeat_at": "<iso8601>",
    "started_at": "<iso8601>",
    "finished_at": "<iso8601>",
    "elapsed_seconds": "<float>",
    # list_models catalog entries.
    "last_modified": "<iso8601>",
    "source_path": "<path>",
    "source_backend": "<backend>",
    "etag_or_fingerprint": "<fingerprint>",
}

_EXPECTED_TYPES: dict[str, tuple[type, ...]] = {
    "<run_id>": (str,),
    "<iso8601>": (str,),
    "<float>": (int, float),
    "<path>": (str,),
    "<backend>": (str,),
    "<fingerprint>": (str,),
}


def _token_for(key: str, value: Any) -> str:
    token = VOLATILE_FIELDS[key]
    if value is None:
        return f"{token}|null"
    # bool is a subclass of int; a boolean elapsed_seconds is a type change,
    # not a number.
    if isinstance(value, bool) or not isinstance(value, _EXPECTED_TYPES[token]):
        return f"{token}|unexpected-type:{type(value).__name__}"
    return token


def normalize(payload: Any) -> Any:
    """Return a copy of ``payload`` with volatile values tokenized."""
    if isinstance(payload, dict):
        return {
            key: (
                _token_for(key, value) if key in VOLATILE_FIELDS else normalize(value)
            )
            for key, value in payload.items()
        }
    if isinstance(payload, list):
        return [normalize(item) for item in payload]
    if isinstance(payload, str):
        # A key literally named "run_id" is already fully tokenized above
        # (and never reaches here, since the dict branch short-circuits to
        # `_token_for` for it). This catches the SAME volatile id embedded
        # inside an arbitrary string value instead -- see the module
        # docstring's "Phase 0 gate" section.
        return _RUN_ID_SUFFIX_RE.sub(_RUN_ID_SUFFIX_TOKEN, payload)
    return payload

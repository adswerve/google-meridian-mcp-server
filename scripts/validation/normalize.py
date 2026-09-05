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
"""

from __future__ import annotations

from typing import Any

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
    return payload

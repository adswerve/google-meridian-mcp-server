"""The one MCP payload extractor.

Every driver that reads a FastMCP ``CallToolResult`` in this repository calls
``extract`` -- the in-process live-validation runner, the deployed-server
smoke test, and the baseline capture harness over both transports.

This matters more than it looks. The baseline harness diffs cloud captures
against local ones and attributes any disagreement to an environment bug
(spec section 8, step 3). If the two transports parsed results differently,
a parser divergence would present identically to a real environment bug.
"""

from __future__ import annotations

import json
from typing import Any


def content_to_obj(result: Any) -> Any:
    """Return the structured payload carried by a FastMCP CallToolResult.

    Order matters. ``structured_content`` is the JSON object the server
    actually returned; ``.data`` is FastMCP's deserialized view of it, which
    may be a pydantic model rather than a plain dict. Prefer the raw object
    so snapshots stay JSON-shaped regardless of transport.
    """
    structured = getattr(result, "structured_content", None)
    if structured is not None:
        return structured
    data = getattr(result, "data", None)
    if data is not None:
        return data
    content = getattr(result, "content", None)
    if not content:
        return result
    block = content[0]
    text = getattr(block, "text", block)
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return text


def unwrap(obj: Any) -> Any:
    """Collapse FastMCP's ``{"result": ...}`` wrapper around non-object returns.

    ``list_models`` returns a list, which FastMCP wraps; every other tool
    returns an object, which it does not.
    """
    if isinstance(obj, dict) and set(obj.keys()) == {"result"}:
        return obj["result"]
    return obj


def extract(result: Any) -> Any:
    """The function callers should use: parse, then unwrap."""
    return unwrap(content_to_obj(result))

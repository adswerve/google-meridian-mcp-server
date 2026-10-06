"""A client holding a session id the server no longer knows must still be served.

In stateful mode the server keeps each client's session id in process memory.
After a restart the new process has never heard of the id the client still
sends, so the legacy (2025-era) streamable-HTTP path answers every call 404
"Session not found" and the client never recovers by itself. Stateless mode
keeps no session table, so any request -- stale id, other client, no id -- is
answered.

The requests here are raw HTTP on purpose. fastmcp 4's own Client negotiates a
modern protocol version, which the mcp session manager routes AROUND the
session table entirely, so a Client-based test would pass even against a
stateful server and prove nothing. A 2025-era protocol-version header is what
deployed clients (for example an ADK agent on an older mcp client) still send,
and it is the path that strands them.

The control arm is load-bearing, as in test_json_response_mode.py: it shows
that the same request IS refused by a stateful server, so the treatment arm's
pass is due to stateless mode and not to a request the server never checks.
"""

from __future__ import annotations

import json
import socket
import threading
import time
import urllib.error
import urllib.request
from contextlib import asynccontextmanager, contextmanager

import pytest
import uvicorn
from fastmcp import FastMCP
from mcp_types.version import HANDSHAKE_PROTOCOL_VERSIONS

from google_meridian_mcp_server import server as meridian_server

LEGACY_PROTOCOL_VERSION = "2025-06-18"
STALE_SESSION_ID = "0123456789abcdef0123456789abcdef"  # issued by a dead process


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _build_app(lifespan_entries: list[int], **http_options):
    @asynccontextmanager
    async def lifespan(_server):
        lifespan_entries.append(1)
        yield {}

    app_server = FastMCP("stateless-http-test", lifespan=lifespan)

    @app_server.tool
    def echo(text: str) -> str:
        return text

    return app_server.http_app(**http_options)


@contextmanager
def _running(app):
    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    srv = uvicorn.Server(config)
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    deadline = time.time() + 15
    while not srv.started and time.time() < deadline:
        time.sleep(0.05)
    if not srv.started:
        raise RuntimeError("test server failed to start")
    try:
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        srv.should_exit = True
        thread.join(timeout=15)


def _call_echo(url: str, text: str, session_id: str | None) -> tuple[int, dict]:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": LEGACY_PROTOCOL_VERSION,
    }
    if session_id is not None:
        headers["Mcp-Session-Id"] = session_id
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "echo", "arguments": {"text": text}},
    }
    request = urllib.request.Request(
        url, data=json.dumps(body).encode(), headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read())


@pytest.fixture
def no_stateless_override(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.delenv("FASTMCP_STATELESS_HTTP", raising=False)
    monkeypatch.chdir(tmp_path)


def test_legacy_protocol_version_takes_the_session_path():
    """Guard: if this version stops being a handshake version, the session
    manager routes it to the modern handler and both arms below go vacuous."""
    assert LEGACY_PROTOCOL_VERSION in HANDSHAKE_PROTOCOL_VERSIONS


@pytest.mark.usefixtures("no_stateless_override")
def test_stale_session_and_other_clients_are_answered_by_default():
    lifespan_entries: list[int] = []
    app = _build_app(lifespan_entries, **meridian_server.http_app_options())

    with _running(app) as url:
        results = [
            _call_echo(url, "stale", STALE_SESSION_ID),
            _call_echo(url, "other-client", "ffffffffffffffffffffffffffffffff"),
            _call_echo(url, "no-session", None),
        ]

    for (status, payload), text in zip(
        results, ["stale", "other-client", "no-session"], strict=True
    ):
        assert status == 200, payload
        assert "error" not in payload, payload
        assert payload["result"]["structuredContent"] == {"result": text}
    # Stateless builds a transport per request, but the server lifespan --
    # discovery, poller, subprocess runner in production -- must still be
    # entered once per process, not once per request.
    assert lifespan_entries == [1]


def test_control_stateful_server_strands_a_stale_session():
    """Control arm: the identical request against a stateful server is 404'd.
    If this ever passes, the upstream mechanism changed and the test above no
    longer means what it says."""
    app = _build_app([], json_response=True, stateless_http=False)

    with _running(app) as url:
        status, payload = _call_echo(url, "stale", STALE_SESSION_ID)

    assert status == 404
    assert payload["error"]["message"] == "Session not found"

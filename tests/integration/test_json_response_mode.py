"""The server must not put a large, slow reply on the SSE branch.

Two conditions together produced the historical failure, and BOTH are needed
to reproduce it: the handler must outlive the server's mode-switch window
(which commits the reply to one SSE frame), and the payload must exceed
httpx2's 1 MiB per-event cap. A big-but-fast reply takes the uncapped JSON
branch and proves nothing, which is why this fixture sleeps.

This test reads a PRIVATE upstream constant, mcp.server._streamable_http_modern
._SSE_PING_INTERVAL. If an upstream rename breaks it, that is the intended
signal: the mechanism moved and this guarantee needs re-checking.

Spec: docs/superpowers/specs/2026-09-09-large-payload-root-cause-and-cap-removal-design.md
"""

import socket
import threading
import time
from contextlib import contextmanager

import mcp.client.streamable_http as client_sh
import mcp.server._streamable_http_modern as modern
import mcp.server.streamable_http_manager as manager
import pytest
import uvicorn
from fastmcp import Client, FastMCP
from fastmcp.client.transports import StreamableHttpTransport

PAYLOAD_BYTES = 2 * 1024 * 1024  # over httpx2's 1 MiB DEFAULT_MAX_EVENT_SIZE_BYTES
HANDLER_DELAY = 0.2  # must exceed the patched window below
PATCHED_WINDOW = 0.05  # stands in for the production 15.0s


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _build_app(json_response: bool):
    server = FastMCP("json-response-mode-test")

    @server.tool
    async def slow_big() -> dict:
        import anyio

        await anyio.sleep(HANDLER_DELAY)
        return {"blob": "x" * PAYLOAD_BYTES}

    return server.http_app(json_response=json_response)


@contextmanager
def _running(app, port):
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


@pytest.fixture
def record_branch(monkeypatch):
    """Record which client handler ran, so we assert WHICH branch, not just
    that the call succeeded."""
    seen: dict[str, str] = {}
    json_orig = client_sh.StreamableHTTPTransport._handle_json_response
    sse_orig = client_sh.StreamableHTTPTransport._handle_sse_response

    async def json_spy(self, response, *a, **kw):
        seen["content_type"] = response.headers.get("content-type", "")
        return await json_orig(self, response, *a, **kw)

    async def sse_spy(self, response, *a, **kw):
        seen["content_type"] = response.headers.get("content-type", "")
        return await sse_orig(self, response, *a, **kw)

    monkeypatch.setattr(
        client_sh.StreamableHTTPTransport, "_handle_json_response", json_spy
    )
    monkeypatch.setattr(
        client_sh.StreamableHTTPTransport, "_handle_sse_response", sse_spy
    )
    return seen


@pytest.fixture(autouse=True)
def shrink_mode_switch_window(monkeypatch):
    """Production waits 15s before committing to SSE. Shrink it so the
    fixture's handler can outlive it in 0.2s instead of 15s."""
    monkeypatch.setattr(modern, "_SSE_PING_INTERVAL", PATCHED_WINDOW)


@pytest.fixture(autouse=True)
def require_modern_handler(monkeypatch):
    """Guard against a silent false pass.

    streamable_http_manager routes to the LEGACY transport when the client's
    MCP-Protocol-Version is in HANDSHAKE_PROTOCOL_VERSIONS, and the legacy
    transport honours json_response too -- so the application/json assertion
    below would pass without ever exercising the modern short-circuit this
    test exists to pin. fastmcp 4.0.3's Client negotiates a modern version
    today; this fixture fails loudly if that ever stops being true.

    The manager imports the symbol into its own namespace
    (streamable_http_manager.py:19), so this MUST patch the manager, not
    mcp.server._streamable_http_modern -- patching the latter has no effect.
    """
    calls = {"n": 0}
    original = manager.handle_modern_request

    async def spy(*args, **kwargs):
        calls["n"] += 1
        return await original(*args, **kwargs)

    monkeypatch.setattr(manager, "handle_modern_request", spy)
    return calls


async def test_slow_large_reply_uses_json_branch(record_branch, require_modern_handler):
    """With json_response=True the reply must arrive as application/json and
    carry the full payload."""
    port = _free_port()
    with _running(_build_app(json_response=True), port) as url:
        async with Client(StreamableHttpTransport(url)) as client:
            result = await client.call_tool("slow_big", {})

    assert result.structured_content is not None
    assert len(result.structured_content["blob"]) == PAYLOAD_BYTES
    assert "application/json" in record_branch["content_type"], (
        "reply must take the JSON branch; text/event-stream means the mode "
        "switch fired and the 1 MiB client cap is live again"
    )
    assert require_modern_handler["n"] > 0, (
        "request never reached handle_modern_request -- it took the legacy "
        "transport, so this test proved nothing about the modern short-circuit"
    )


async def test_control_slow_large_reply_fails_on_sse_branch(
    record_branch, require_modern_handler
):
    """Control arm. Without json_response the identical call takes the SSE
    branch and is lost. If this ever starts passing, the upstream mechanism
    changed and the guarantee above no longer means what it says."""
    port = _free_port()
    with _running(_build_app(json_response=False), port) as url:
        with pytest.raises(Exception) as excinfo:
            async with Client(StreamableHttpTransport(url)) as client:
                await client.call_tool("slow_big", {})

    assert "SSE stream ended without a response" in str(excinfo.value)
    assert "text/event-stream" in record_branch["content_type"]

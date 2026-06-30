"""Unit tests for server setup and transport selection."""

from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest

from google_meridian_mcp_server import server


class _FakeFastMCP:
    def __init__(self, name, lifespan):
        self.name = name
        self.lifespan = lifespan


def _runtime_config(backend: str) -> SimpleNamespace:
    return SimpleNamespace(
        transport="streamable-http",
        persistence_backend=backend,
        local_models_root="/models",
        gcs_bucket="bucket",
        gcs_models_prefix="models",
        discovery_ttl_seconds=60,
        model_cache_root="/tmp/cache",
        result_cache_enabled=True,
        result_cache_ttl_seconds=30,
        resolved_registry_backend="local",
        optimization_runs_root="/tmp/optimizations",
        optimization_max_parallel=2,
        optimization_heartbeat_stale_seconds=120,
        optimization_backend_local="subprocess",
        optimization_allowed_tiers=("local",),
    )


def test_create_server_registers_tools(monkeypatch: pytest.MonkeyPatch):
    register_tools = mock.Mock()
    monkeypatch.setattr(server, "FastMCP", _FakeFastMCP)
    monkeypatch.setattr(server, "register_tools", register_tools)

    mcp = server.create_server()

    assert isinstance(mcp, _FakeFastMCP)
    assert mcp.name == "Google Meridian MCP Server"
    register_tools.assert_called_once_with(mcp)


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["local", "gcs"])
async def test_lifespan_selects_expected_provider(
    monkeypatch: pytest.MonkeyPatch,
    backend: str,
):
    model_catalog = object()
    result_cache = object()

    monkeypatch.setattr(server, "load_config", lambda: _runtime_config(backend))
    monkeypatch.setattr(
        server, "build_model_catalog", mock.Mock(return_value=model_catalog)
    )
    monkeypatch.setattr(server, "ResultCache", mock.Mock(return_value=result_cache))

    async with server._lifespan(SimpleNamespace()) as state:
        assert state["model_catalog"] is model_catalog
        assert state["result_cache"] is result_cache

    server.build_model_catalog.assert_called_once()
    server.ResultCache.assert_called_once_with(enabled=True, ttl_seconds=30)


def test_run_server_uses_stdio_transport(monkeypatch: pytest.MonkeyPatch):
    run = mock.Mock()
    monkeypatch.setattr(
        server, "load_config", lambda: SimpleNamespace(transport="stdio")
    )
    monkeypatch.setattr(server.mcp, "run", run)

    server.run_server()

    run.assert_called_once_with(transport="stdio")


def test_run_server_uses_http_transport_and_env_host_port(
    monkeypatch: pytest.MonkeyPatch,
):
    run = mock.Mock()
    monkeypatch.setattr(
        server, "load_config", lambda: SimpleNamespace(transport="streamable-http")
    )
    monkeypatch.setattr(server.mcp, "run", run)
    monkeypatch.setenv("MCP_HOST", "127.0.0.1")
    monkeypatch.setenv("PORT", "9000")
    monkeypatch.delenv("MCP_PORT", raising=False)

    server.run_server()

    run.assert_called_once_with(transport="http", host="127.0.0.1", port=9000)

"""Unit tests for server setup and transport selection."""

from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest

from google_meridian_mcp_server import server
from google_meridian_mcp_server.persistence.optimization_run_registry import (
    GcsOptimizationRunRegistry,
)


class _FakeFastMCP:
    def __init__(self, name, instructions=None, lifespan=None):
        self.name = name
        self.instructions = instructions
        self.lifespan = lifespan
        self.providers = []

    def add_provider(self, provider, *, namespace=""):
        self.providers.append(provider)


def _runtime_config(backend: str) -> SimpleNamespace:
    return SimpleNamespace(
        transport="streamable-http",
        persistence_backend=backend,
        local_models_root="/models",
        gcs_bucket="bucket",
        gcs_models_prefix="models",
        model_cache_root="/tmp/cache",
        result_cache_enabled=True,
        result_cache_ttl_seconds=30,
        optimization_runs_root="/tmp/optimizations",
        optimization_gcs_prefix="optimizations/",
        optimization_max_parallel=2,
        optimization_tier="local",
        analysis_worker_timeout=300.0,
    )


def test_create_server_registers_tools(monkeypatch: pytest.MonkeyPatch):
    register_tools = mock.Mock()
    monkeypatch.setattr(server, "FastMCP", _FakeFastMCP)
    monkeypatch.setattr(server, "register_tools", register_tools)

    mcp = server.create_server()

    assert isinstance(mcp, _FakeFastMCP)
    assert mcp.name == "Google Meridian MCP Server"
    assert mcp.instructions == server._SERVER_INSTRUCTIONS
    assert len(mcp.providers) == 1
    register_tools.assert_called_once_with(mcp)


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["local", "gcs"])
async def test_lifespan_selects_expected_provider(
    monkeypatch: pytest.MonkeyPatch,
    backend: str,
):
    discovery_cache = object()
    result_cache = object()

    monkeypatch.setattr(server, "load_config", lambda: _runtime_config(backend))
    monkeypatch.setattr(
        server, "build_discovery_cache", mock.Mock(return_value=discovery_cache)
    )
    monkeypatch.setattr(server, "ResultCache", mock.Mock(return_value=result_cache))
    monkeypatch.setattr(
        GcsOptimizationRunRegistry,
        "_default_client",
        staticmethod(lambda: object()),
    )

    async with server._lifespan(SimpleNamespace()) as state:
        assert state["discovery_cache"] is discovery_cache
        assert state["result_cache"] is result_cache
        # Task 11: the server drops the full model_catalog entirely -- the
        # subprocess runner is the only thing that ever touches Meridian.
        analysis_runner = state["analysis_runner"]
        assert isinstance(analysis_runner, server.SyncSubprocessExecutor)
        expected = (
            "GcsOptimizationRunRegistry"
            if backend == "gcs"
            else "LocalOptimizationRunRegistry"
        )
        assert type(state["optimization_registry"]).__name__ == expected

    server.build_discovery_cache.assert_called_once()
    server.ResultCache.assert_called_once_with(enabled=True, ttl_seconds=30)


@pytest.mark.asyncio
async def test_lifespan_shuts_down_analysis_runner_on_exit(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(server, "load_config", lambda: _runtime_config("local"))
    monkeypatch.setattr(
        server, "build_discovery_cache", mock.Mock(return_value=object())
    )
    monkeypatch.setattr(server, "ResultCache", mock.Mock(return_value=object()))

    shutdown = mock.AsyncMock()
    monkeypatch.setattr(
        server.SyncSubprocessExecutor, "shutdown", shutdown, raising=True
    )

    async with server._lifespan(SimpleNamespace()):
        shutdown.assert_not_called()

    shutdown.assert_awaited_once()


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

    server.run_server()

    run.assert_called_once_with(
        transport="http", host="127.0.0.1", port=9000, json_response=True
    )

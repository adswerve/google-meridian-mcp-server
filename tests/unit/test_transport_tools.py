"""Unit tests for FastMCP tool registration wrappers."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from google_meridian_mcp_server.domain.errors import (
    BackendUnavailableError,
    ModelNotFoundError,
)
from google_meridian_mcp_server.domain.filters import AnalysisFilters
from google_meridian_mcp_server.transport import tools as tools_module


class _FakeFastMCP:
    def __init__(self):
        self.tools = {}

    def tool(self, annotations=None):
        def _decorator(fn):
            self.tools[fn.__name__] = fn
            return fn

        return _decorator


def _async(fn):
    """Wrap a sync callable as an async function (service methods are now async)."""

    async def _wrapped(*args, **kwargs):
        return fn(*args, **kwargs)

    return _wrapped


def _async_raise(exc):
    async def _wrapped(*args, **kwargs):
        raise exc

    return _wrapped


@pytest.mark.asyncio
async def test_register_tools_exposes_successful_handlers(
    monkeypatch: pytest.MonkeyPatch,
):
    mcp = _FakeFastMCP()
    catalog_service = SimpleNamespace(list_models=lambda: [{"model_id": "m1"}])
    analysis_service = SimpleNamespace(
        get_model_overview=_async(
            lambda model_id: {"model_id": model_id, "model_type": "geo"}
        ),
        get_training_data=_async(
            lambda model_id, dataset, filters: {
                "model_id": model_id,
                "datasets": dataset,
                "filters": filters,
            }
        ),
        get_channel_summary=_async(
            lambda model_id, output_type, filters: {
                "model_id": model_id,
                "output_type": output_type,
                "filters": filters,
            }
        ),
        get_contribution=_async(
            lambda model_id, output_type, filters: {
                "model_id": model_id,
                "output_type": output_type,
            }
        ),
        get_adstock_decay=_async(
            lambda model_id, output_type, filters: {
                "model_id": model_id,
                "output_type": output_type,
            }
        ),
        get_response_curves=_async(
            lambda model_id, output_type, filters: {
                "model_id": model_id,
                "output_type": output_type,
            }
        ),
    )
    monkeypatch.setattr(tools_module, "_catalog_service", lambda ctx: catalog_service)
    monkeypatch.setattr(tools_module, "_analysis_service", lambda ctx: analysis_service)

    tools_module.register_tools(mcp)
    ctx = SimpleNamespace(lifespan_context={})

    assert await mcp.tools["list_models"](ctx) == [{"model_id": "m1"}]
    assert await mcp.tools["get_model_overview"]("m1", ctx) == {
        "model_id": "m1",
        "model_type": "geo",
    }
    # No filters passed -> the transport layer no longer normalizes; it passes
    # the raw (default None) value straight through to the service, which is
    # now responsible for normalization.
    assert await mcp.tools["get_training_data"]("m1", ["kpi"], ctx) == {
        "model_id": "m1",
        "datasets": ["kpi"],
        "filters": None,
    }
    assert (await mcp.tools["get_channel_summary"]("m1", "roi", ctx))[
        "output_type"
    ] == "roi"
    assert (await mcp.tools["get_contribution"]("m1", "contribution_metrics", ctx))[
        "output_type"
    ] == "contribution_metrics"
    assert (await mcp.tools["get_adstock_decay"]("m1", "alpha_summary", ctx))[
        "output_type"
    ] == "alpha_summary"
    assert (
        await mcp.tools["get_response_curves"]("m1", "response_curve_summary", ctx)
    )["output_type"] == "response_curve_summary"


@pytest.mark.asyncio
async def test_register_tools_passes_through_provided_filters(
    monkeypatch: pytest.MonkeyPatch,
):
    """When a caller does supply filters, the transport passes the exact
    (already-pydantic-validated-by-FastMCP) AnalysisFilters instance through
    unchanged -- no redundant re-normalization at the transport layer."""
    mcp = _FakeFastMCP()
    captured = {}

    analysis_service = SimpleNamespace(
        get_channel_summary=_async(
            lambda model_id, output_type, filters: captured.update(
                {"filters": filters}
            )
            or {"model_id": model_id, "output_type": output_type}
        ),
    )
    monkeypatch.setattr(tools_module, "_analysis_service", lambda ctx: analysis_service)

    tools_module.register_tools(mcp)
    ctx = SimpleNamespace(lifespan_context={})

    provided = AnalysisFilters(channels=["tv"])
    await mcp.tools["get_channel_summary"]("m1", "roi", ctx, filters=provided)

    assert captured["filters"] is provided


@pytest.mark.asyncio
async def test_tool_wrappers_return_standard_error_payloads(
    monkeypatch: pytest.MonkeyPatch,
):
    mcp = _FakeFastMCP()
    analysis_service = SimpleNamespace(
        get_model_overview=_async_raise(ModelNotFoundError("missing")),
    )
    monkeypatch.setattr(
        tools_module,
        "_catalog_service",
        lambda ctx: SimpleNamespace(
            list_models=lambda: (_ for _ in ()).throw(
                BackendUnavailableError("local", "disk full")
            )
        ),
    )
    monkeypatch.setattr(tools_module, "_analysis_service", lambda ctx: analysis_service)

    tools_module.register_tools(mcp)
    ctx = SimpleNamespace(lifespan_context={})

    assert await mcp.tools["list_models"](ctx) == {
        "error_code": "backend_unavailable",
        "message": "Backend 'local' is not available: disk full",
        "details": {"backend": "local"},
    }
    assert await mcp.tools["get_model_overview"]("missing", ctx) == {
        "error_code": "model_not_found",
        "message": "Model 'missing' is not available in the configured backend.",
        "details": {"model_id": "missing", "backend": "unknown"},
    }


def test_aggregate_geos_is_no_longer_accepted():
    with pytest.raises(ValidationError):
        AnalysisFilters(aggregate_geos=False)


@pytest.mark.asyncio
async def test_register_tools_exposes_get_spend_scenario(
    monkeypatch: pytest.MonkeyPatch,
):
    mcp = _FakeFastMCP()
    captured = {}

    def _get_spend_scenario(model_id, channel, spend_increase, base_spend, filters):
        captured["args"] = (model_id, channel, spend_increase, base_spend)
        captured["filters"] = filters
        return {"model_id": model_id, "channel": channel, "outcome_mode": "revenue"}

    analysis_service = SimpleNamespace(
        get_spend_scenario=_async(_get_spend_scenario)
    )
    monkeypatch.setattr(tools_module, "_analysis_service", lambda ctx: analysis_service)

    tools_module.register_tools(mcp)
    ctx = SimpleNamespace(lifespan_context={})

    result = await mcp.tools["get_spend_scenario"]("m1", "search", 1000.0, ctx)

    assert result == {
        "model_id": "m1",
        "channel": "search",
        "outcome_mode": "revenue",
    }
    assert captured["args"] == ("m1", "search", 1000.0, None)
    # No filters were passed by the caller, and the transport layer no longer
    # normalizes -- the raw (default None) value reaches the service.
    assert captured["filters"] is None

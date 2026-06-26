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


@pytest.mark.asyncio
async def test_register_tools_exposes_successful_handlers(
    monkeypatch: pytest.MonkeyPatch,
):
    mcp = _FakeFastMCP()
    catalog_service = SimpleNamespace(list_models=lambda: [{"model_id": "m1"}])
    analysis_service = SimpleNamespace(
        get_model_overview=lambda model_id: {"model_id": model_id, "model_type": "geo"},
        get_training_data=lambda model_id, dataset, filters: {
            "model_id": model_id,
            "datasets": dataset,
            "filters": filters.model_dump(mode="json"),
        },
        get_channel_summary=lambda model_id, output_type, filters: {
            "model_id": model_id,
            "output_type": output_type,
            "filters": filters.model_dump(mode="json"),
        },
        get_contribution=lambda model_id, output_type, filters: {
            "model_id": model_id,
            "output_type": output_type,
        },
        get_adstock_decay=lambda model_id, output_type, filters: {
            "model_id": model_id,
            "output_type": output_type,
        },
        get_response_curves=lambda model_id, output_type, filters: {
            "model_id": model_id,
            "output_type": output_type,
        },
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
    assert await mcp.tools["get_training_data"]("m1", ["kpi"], ctx) == {
        "model_id": "m1",
        "datasets": ["kpi"],
        "filters": {
            "start_date": None,
            "end_date": None,
            "geos": [],
            "channels": [],
            "aggregate_times": True,
            "include_non_paid": None,
            "use_kpi": None,
        },
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
async def test_tool_wrappers_return_standard_error_payloads(
    monkeypatch: pytest.MonkeyPatch,
):
    mcp = _FakeFastMCP()
    analysis_service = SimpleNamespace(
        get_model_overview=lambda model_id: (_ for _ in ()).throw(
            ModelNotFoundError(model_id)
        ),
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

    analysis_service = SimpleNamespace(get_spend_scenario=_get_spend_scenario)
    monkeypatch.setattr(
        tools_module, "_analysis_service", lambda ctx: analysis_service
    )

    tools_module.register_tools(mcp)
    ctx = SimpleNamespace(lifespan_context={})

    result = await mcp.tools["get_spend_scenario"]("m1", "search", 1000.0, ctx)

    assert result == {
        "model_id": "m1",
        "channel": "search",
        "outcome_mode": "revenue",
    }
    assert captured["args"] == ("m1", "search", 1000.0, None)
    assert isinstance(captured["filters"], AnalysisFilters)

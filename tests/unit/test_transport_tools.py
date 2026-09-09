"""Unit tests for FastMCP tool registration wrappers."""

from __future__ import annotations

import json
import threading
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

    def tool(self, fn=None, *, annotations=None):
        """Supports both ``@mcp.tool`` (bare) and ``@mcp.tool(annotations=...)``
        -- the real handlers use both forms."""

        def _decorator(f):
            self.tools[f.__name__] = f
            return f

        if fn is not None:
            return _decorator(fn)
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
async def test_envelope_carries_payload_exactly_once(monkeypatch):
    """De-dup rule: data lives in structured_content; content is a short note.
    Reverting the fix makes content[0].text the full JSON and fails here."""
    mcp = _FakeFastMCP()
    analysis_service = SimpleNamespace(
        get_model_overview=_async(lambda model_id: {"model_id": model_id}),
    )
    monkeypatch.setattr(tools_module, "_analysis_service", lambda ctx: analysis_service)
    tools_module.register_tools(mcp)
    ctx = SimpleNamespace(lifespan_context={})

    result = await mcp.tools["get_model_overview"]("m1", ctx)

    assert result.structured_content == {"model_id": "m1"}
    assert len(result.content) == 1
    note = result.content[0].text
    assert note == "See structuredContent."
    with pytest.raises(json.JSONDecodeError):
        json.loads(note)


@pytest.mark.asyncio
async def test_envelope_content_note_reports_rows_through_a_real_handler(monkeypatch):
    """Branch 1 of `_content_note` (row_count/columns) as exercised THROUGH
    `_guarded`, not just as a direct unit call. `AnalysisService._build_result`
    stamps row_count/columns onto every analysis payload, so this is the
    common production path, not the exotic one -- a hardcoded
    'See structuredContent.' at the ToolResult call site would still pass
    every other test in this file."""
    mcp = _FakeFastMCP()
    analysis_service = SimpleNamespace(
        get_model_overview=_async(
            lambda model_id: {
                "row_count": 2,
                "columns": ["a", "b"],
                "rows": [[1, 2], [3, 4]],
            }
        ),
    )
    monkeypatch.setattr(tools_module, "_analysis_service", lambda ctx: analysis_service)
    tools_module.register_tools(mcp)
    ctx = SimpleNamespace(lifespan_context={})

    result = await mcp.tools["get_model_overview"]("m1", ctx)

    assert result.content[0].text == "2 rows x 2 columns in structuredContent"


def test_content_note_reports_rows_and_columns():
    """§4.2 branch 1. Collapsing every tool to the constant string fails here."""
    assert (
        tools_module._content_note({"row_count": 85800, "columns": ["a", "b"]})
        == "85800 rows x 2 columns in structuredContent"
    )


def test_content_note_falls_back_for_rowless_payloads():
    """§4.2 branch 2. Always emitting counts fails here."""
    assert tools_module._content_note({"model_id": "m1"}) == "See structuredContent."
    assert tools_module._content_note([{"model_id": "m1"}]) == "See structuredContent."


@pytest.mark.asyncio
async def test_list_models_success_envelope_is_wrapped(monkeypatch):
    mcp = _FakeFastMCP()
    monkeypatch.setattr(
        tools_module,
        "_catalog_service",
        lambda ctx: SimpleNamespace(list_models=lambda: [{"model_id": "m1"}]),
    )
    tools_module.register_tools(mcp)
    ctx = SimpleNamespace(lifespan_context={})

    result = await mcp.tools["list_models"](ctx)

    assert result.structured_content == {"result": [{"model_id": "m1"}]}


@pytest.mark.asyncio
async def test_list_models_error_envelope_is_also_wrapped(monkeypatch):
    """The path a naive `isinstance(payload, list)` check leaves unwrapped, which
    every client then rejects with `'result' is a required property`."""
    mcp = _FakeFastMCP()
    monkeypatch.setattr(
        tools_module,
        "_catalog_service",
        lambda ctx: SimpleNamespace(
            list_models=lambda: (_ for _ in ()).throw(
                BackendUnavailableError("local", "disk full")
            )
        ),
    )
    tools_module.register_tools(mcp)
    ctx = SimpleNamespace(lifespan_context={})

    result = await mcp.tools["list_models"](ctx)

    assert result.structured_content == {
        "result": {
            "error_code": "backend_unavailable",
            "message": "Backend 'local' is not available: disk full",
            "details": {"backend": "local"},
        }
    }


@pytest.mark.asyncio
async def test_list_models_survives_the_payloads_extractor_round_trip(monkeypatch):
    """scripts/validation/payloads.extract must return the SAME object as
    before this change -- the bare list, not the {"result": ...} wrapper.
    This is what keeps every drift baseline byte-identical (spec §4.5)."""
    from fastmcp import Client, FastMCP

    from scripts.validation.payloads import extract

    mcp = FastMCP("roundtrip")
    tools_module.register_tools(mcp)
    monkeypatch.setattr(
        tools_module,
        "_catalog_service",
        lambda ctx: SimpleNamespace(list_models=lambda: [{"model_id": "m1"}]),
    )
    async with Client(mcp) as client:
        result = await client.call_tool("list_models", {})

    assert extract(result) == [{"model_id": "m1"}]


@pytest.mark.asyncio
async def test_list_models_error_survives_the_payloads_extractor_round_trip(
    monkeypatch,
):
    """Error-path half of the round trip above -- spec §9.2 is explicit that a
    success-only test passes while the error path is broken, and the error
    path is the one a naive implementation breaks."""
    from fastmcp import Client, FastMCP

    from scripts.validation.payloads import extract

    mcp = FastMCP("roundtrip")
    tools_module.register_tools(mcp)
    monkeypatch.setattr(
        tools_module,
        "_catalog_service",
        lambda ctx: SimpleNamespace(
            list_models=lambda: (_ for _ in ()).throw(
                BackendUnavailableError("local", "disk full")
            )
        ),
    )
    async with Client(mcp) as client:
        result = await client.call_tool("list_models", {})

    assert extract(result) == {
        "error_code": "backend_unavailable",
        "message": "Backend 'local' is not available: disk full",
        "details": {"backend": "local"},
    }


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

    assert (await mcp.tools["list_models"](ctx)).structured_content == {
        "result": [{"model_id": "m1"}]
    }
    assert (await mcp.tools["get_model_overview"]("m1", ctx)).structured_content == {
        "model_id": "m1",
        "model_type": "geo",
    }
    # No filters passed -> the transport layer no longer normalizes; it passes
    # the raw (default None) value straight through to the service, which is
    # now responsible for normalization.
    assert (
        await mcp.tools["get_training_data"]("m1", ["kpi"], ctx)
    ).structured_content == {
        "model_id": "m1",
        "datasets": ["kpi"],
        "filters": None,
    }
    assert (
        await mcp.tools["get_channel_summary"]("m1", "roi", ctx)
    ).structured_content["output_type"] == "roi"
    assert (
        await mcp.tools["get_contribution"]("m1", "contribution_metrics", ctx)
    ).structured_content["output_type"] == "contribution_metrics"
    assert (
        await mcp.tools["get_adstock_decay"]("m1", "alpha_summary", ctx)
    ).structured_content["output_type"] == "alpha_summary"
    assert (
        await mcp.tools["get_response_curves"]("m1", "response_curve_summary", ctx)
    ).structured_content["output_type"] == "response_curve_summary"


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
            lambda model_id, output_type, filters: (
                captured.update({"filters": filters})
                or {"model_id": model_id, "output_type": output_type}
            )
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

    assert (await mcp.tools["list_models"](ctx)).structured_content == {
        "result": {
            "error_code": "backend_unavailable",
            "message": "Backend 'local' is not available: disk full",
            "details": {"backend": "local"},
        }
    }
    assert (
        await mcp.tools["get_model_overview"]("missing", ctx)
    ).structured_content == {
        "error_code": "model_not_found",
        "message": "Model 'missing' is not available in the configured backend.",
        "details": {"model_id": "missing", "backend": "unknown"},
    }


@pytest.mark.asyncio
async def test_tool_surface_catches_non_meridian_exceptions(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    """F4(b): a plain (non-MeridianMcpError) exception raised by a service
    method must never escape a tool handler as a raw exception -- the
    @_guarded decorator wraps every handler so it always returns a clean
    internal_error envelope instead."""
    mcp = _FakeFastMCP()
    analysis_service = SimpleNamespace(
        get_model_overview=_async_raise(ValueError("boom: disk exploded")),
    )
    monkeypatch.setattr(tools_module, "_analysis_service", lambda ctx: analysis_service)

    tools_module.register_tools(mcp)
    ctx = SimpleNamespace(lifespan_context={})

    with caplog.at_level("ERROR", logger=tools_module.__name__):
        result = (await mcp.tools["get_model_overview"]("m1", ctx)).structured_content

    assert result["error_code"] == "internal_error"
    assert "ValueError" in result["message"]
    assert "boom: disk exploded" in result["message"]
    assert result["details"] == {}
    # R2: an unexpected exception must be logged server-side, not just
    # converted to an envelope, so operators get some signal.
    assert any(
        "unhandled error in tool handler" in rec.getMessage() for rec in caplog.records
    )


@pytest.mark.asyncio
async def test_guarded_does_not_log_meridian_mcp_errors(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    """R2: a MeridianMcpError is a normal domain outcome, not an unexpected
    failure -- @_guarded must NOT log it (only the except Exception branch
    logs)."""
    mcp = _FakeFastMCP()
    analysis_service = SimpleNamespace(
        get_model_overview=_async_raise(ModelNotFoundError("missing")),
    )
    monkeypatch.setattr(tools_module, "_analysis_service", lambda ctx: analysis_service)

    tools_module.register_tools(mcp)
    ctx = SimpleNamespace(lifespan_context={})

    with caplog.at_level("ERROR", logger=tools_module.__name__):
        await mcp.tools["get_model_overview"]("missing", ctx)

    assert not any(
        "unhandled error in tool handler" in rec.getMessage() for rec in caplog.records
    )


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

    analysis_service = SimpleNamespace(get_spend_scenario=_async(_get_spend_scenario))
    monkeypatch.setattr(tools_module, "_analysis_service", lambda ctx: analysis_service)

    tools_module.register_tools(mcp)
    ctx = SimpleNamespace(lifespan_context={})

    result = await mcp.tools["get_spend_scenario"]("m1", "search", 1000.0, ctx)

    assert result.structured_content == {
        "model_id": "m1",
        "channel": "search",
        "outcome_mode": "revenue",
    }
    assert captured["args"] == ("m1", "search", 1000.0, None)
    # No filters were passed by the caller, and the transport layer no longer
    # normalizes -- the raw (default None) value reaches the service.
    assert captured["filters"] is None


@pytest.mark.asyncio
async def test_list_models_is_offloaded_to_a_worker_thread(
    monkeypatch: pytest.MonkeyPatch,
):
    """F6(a): list_models does discovery I/O (local fs walk or GCS list)
    synchronously; the handler must run it via asyncio.to_thread so a slow
    backend can't stall the event loop for every other in-flight tool call."""
    mcp = _FakeFastMCP()
    main_thread = threading.current_thread()
    seen = {}

    def _list_models():
        seen["thread"] = threading.current_thread()
        return [{"model_id": "m1"}]

    monkeypatch.setattr(
        tools_module,
        "_catalog_service",
        lambda ctx: SimpleNamespace(list_models=_list_models),
    )

    tools_module.register_tools(mcp)
    ctx = SimpleNamespace(lifespan_context={})

    result = await mcp.tools["list_models"](ctx)

    assert result.structured_content == {"result": [{"model_id": "m1"}]}
    assert seen["thread"] is not main_thread


@pytest.mark.asyncio
async def test_optimization_status_result_list_delete_cancel_are_offloaded_to_thread(
    monkeypatch: pytest.MonkeyPatch,
):
    """F6(a): get_optimization_status/get_optimization_result/list_optimizations/
    delete_optimization/cancel_optimization wrap SYNC service calls (registry
    I/O, and for get_status a Cloud Run liveness RPC per live handle via
    executor.pump()); each handler must offload via asyncio.to_thread."""
    mcp = _FakeFastMCP()
    main_thread = threading.current_thread()
    calls: dict[str, threading.Thread] = {}

    def _record(name):
        def _fn(*args, **kwargs):
            calls[name] = threading.current_thread()
            return {"ok": name, "args": args, "kwargs": kwargs}

        return _fn

    optimization_service = SimpleNamespace(
        get_status=_record("get_status"),
        get_result=_record("get_result"),
        list_runs=_record("list_runs"),
        delete=_record("delete"),
        cancel=_record("cancel"),
    )
    monkeypatch.setattr(
        tools_module, "_optimization_service", lambda ctx: optimization_service
    )

    tools_module.register_tools(mcp)
    ctx = SimpleNamespace(lifespan_context={})

    assert (await mcp.tools["get_optimization_status"]("r1", ctx)).structured_content[
        "ok"
    ] == "get_status"
    assert (await mcp.tools["get_optimization_result"]("r1", ctx)).structured_content[
        "ok"
    ] == "get_result"
    assert (await mcp.tools["list_optimizations"](ctx)).structured_content[
        "ok"
    ] == "list_runs"
    assert (await mcp.tools["delete_optimization"]("r1", ctx)).structured_content[
        "ok"
    ] == "delete"
    assert (await mcp.tools["cancel_optimization"]("r1", ctx)).structured_content[
        "ok"
    ] == "cancel"

    assert set(calls) == {
        "get_status",
        "get_result",
        "list_runs",
        "delete",
        "cancel",
    }
    for name, thread in calls.items():
        assert thread is not main_thread, f"{name} ran on the event-loop thread"

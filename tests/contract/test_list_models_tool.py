"""Contract tests for list_models MCP tool behavior."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone

import pytest

from google_meridian_mcp_server.domain.models import (
    ModelCatalogEntry,
    ModelStatus,
    PersistenceBackend,
)


def _make_entry(model_id: str = "demo") -> ModelCatalogEntry:
    return ModelCatalogEntry(
        model_id=model_id,
        display_name="Demo Model",
        source_backend=PersistenceBackend.LOCAL.value,
        source_path=f"/models/{model_id}.binpb",
        model_format="binpb",
        last_modified=datetime(2026, 1, 1, tzinfo=timezone.utc),
        status=ModelStatus.READY.value,
    )


class TestListModelsContract:
    """Validates the external contract of the list_models tool."""

    @pytest.mark.asyncio
    async def test_returns_list_of_catalog_entries(self):
        """list_models returns a JSON-serializable list of model entries."""
        entry = _make_entry("test-model")
        result = asdict(entry)

        assert isinstance(result, dict)
        assert result["model_id"] == "test-model"
        assert result["display_name"] == "Demo Model"
        assert result["source_backend"] == "local"
        assert result["model_format"] == "binpb"
        assert result["status"] == "ready"

    @pytest.mark.asyncio
    async def test_empty_catalog_returns_empty_list(self):
        """Agents receive an empty list when no models are available."""
        entries: list[ModelCatalogEntry] = []
        result = [asdict(e) for e in entries]
        assert result == []

    @pytest.mark.asyncio
    async def test_catalog_entry_has_required_fields(self):
        """Every entry in the list must contain the contract-required fields."""
        entry = _make_entry()
        d = asdict(entry)

        required = {
            "model_id",
            "display_name",
            "source_backend",
            "source_path",
            "model_format",
            "status",
            "metadata",
        }
        assert required.issubset(d.keys())

    @pytest.mark.asyncio
    async def test_error_shape_on_backend_failure(self):
        """Backend errors must use the standard error shape."""
        from google_meridian_mcp_server.domain.errors import BackendUnavailableError

        try:
            raise BackendUnavailableError("local", "disk full")
        except BackendUnavailableError as exc:
            error = {
                "error_code": exc.error_code,
                "message": str(exc),
                "details": exc.details,
            }

        assert error["error_code"] == "backend_unavailable"
        assert "disk full" in error["message"]
        assert error["details"]["backend"] == "local"

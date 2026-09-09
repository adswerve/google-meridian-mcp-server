"""Tests for MeridianMcpError payload rehydration and new error classes."""

import pytest

from google_meridian_mcp_server.domain.errors import (
    InternalError,
    MeridianMcpError,
    MissingModelDataError,
    OptimizationFailedError,
    ResponseTooLargeError,
    ServerBusyError,
    WorkerFailedError,
    WorkerLostError,
    WorkerTimeoutError,
)


def test_roundtrip_via_base_class():
    original = MissingModelDataError("m1", "no data")
    revived = MeridianMcpError.from_payload(original.to_payload())
    assert type(revived) is MeridianMcpError  # base, not subclass
    assert revived.error_code == "missing_model_data"
    assert str(revived) == str(original)
    assert revived.details == original.details


def test_from_payload_defaults():
    revived = MeridianMcpError.from_payload({"message": "boom"})
    assert revived.error_code == "internal_error" and revived.details == {}


@pytest.mark.parametrize(
    "cls,code",
    [
        (WorkerFailedError, "worker_failed"),
        (WorkerTimeoutError, "worker_timeout"),
        (ServerBusyError, "server_busy"),
        (InternalError, "internal_error"),
    ],
)
def test_infra_codes(cls, code):
    assert cls("x").error_code == code


def test_execution_error_codes_match_serialized_registry_dicts():
    """Documents that the hierarchy's codes match the ad-hoc dict codes
    written to the registry by execution/worker.py and
    execution/base_executor.py, without changing the serialized JSON shape.
    """
    assert OptimizationFailedError("x").error_code == "optimization_failed"
    assert WorkerLostError().error_code == "worker_lost"


def test_response_too_large_reports_shape_when_known():
    err = ResponseTooLargeError(
        nbytes=7774957, limit_bytes=4194304, total_rows=85800, total_columns=10
    )
    assert err.error_code == "response_too_large"
    assert "85800 rows x 10 columns" in str(err)
    assert "filters.geos" in str(err)
    assert err.details == {
        "bytes": 7774957,
        "limit_bytes": 4194304,
        "total_rows": 85800,
        "total_columns": 10,
    }


def test_response_too_large_omits_shape_when_unknown():
    """GUARD 2 has no row/column counts; the message must not say 'None rows'."""
    err = ResponseTooLargeError(nbytes=99, limit_bytes=8)
    assert "None" not in str(err)
    assert "rows x" not in str(err)
    assert err.details["total_rows"] is None

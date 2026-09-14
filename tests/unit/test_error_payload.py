"""Tests for MeridianMcpError payload rehydration and new error classes."""

import pytest

from google_meridian_mcp_server.domain.errors import (
    InternalError,
    MeridianMcpError,
    MissingModelDataError,
    OptimizationFailedError,
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

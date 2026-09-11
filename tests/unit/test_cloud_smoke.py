"""Transient-error handling in the QUEUE_SMOKE live acceptance gate.

The gate polls the Cloud Run Admin API every few seconds for hours, so a
transient failure is a certainty rather than a risk. A live run died on
`503 ... DNS query cancelled` and discarded six already-paid-for optimizer
executions, which is what these tests exist to prevent recurring.
"""

import pytest
from google.api_core import exceptions as gexc

from scripts.validation import cloud_smoke


@pytest.fixture(autouse=True)
def _no_real_sleeping(monkeypatch):
    """Backoff is real seconds in production and must not be paid here."""
    monkeypatch.setattr(cloud_smoke.time, "sleep", lambda _s: None)


def test_returns_the_value_when_the_call_succeeds():
    assert cloud_smoke._call_with_retries("x", lambda: "ok") == "ok"


def test_arguments_are_forwarded_unchanged():
    seen = {}

    def fn(*args, **kwargs):
        seen["args"], seen["kwargs"] = args, kwargs
        return "ok"

    assert cloud_smoke._call_with_retries("x", fn, 1, parent="p") == "ok"
    assert seen == {"args": (1,), "kwargs": {"parent": "p"}}


@pytest.mark.parametrize(
    "exc",
    [
        gexc.ServiceUnavailable("503"),
        gexc.DeadlineExceeded("504"),
        gexc.InternalServerError("500"),
        gexc.TooManyRequests("429"),
        gexc.GatewayTimeout("504"),
    ],
)
def test_transient_failures_are_retried_until_one_succeeds(exc, capsys):
    calls = []

    def fn():
        calls.append(1)
        if len(calls) < 3:
            raise exc
        return "ok"

    assert cloud_smoke._call_with_retries("list_executions", fn) == "ok"
    assert len(calls) == 3
    assert "failed transiently" in capsys.readouterr().out


def test_transient_failures_give_up_after_the_cap_and_reraise():
    calls = []

    def fn():
        calls.append(1)
        raise gexc.ServiceUnavailable("503 DNS query cancelled")

    with pytest.raises(gexc.ServiceUnavailable):
        cloud_smoke._call_with_retries("list_executions", fn)
    assert len(calls) == cloud_smoke._TRANSIENT_RETRIES


@pytest.mark.parametrize(
    "exc",
    [
        gexc.PermissionDenied("403"),
        gexc.NotFound("404"),
        gexc.Unauthenticated("401"),
    ],
)
def test_non_transient_failures_are_not_retried(exc):
    """Asking again does not fix a revoked credential or a deleted job, and
    retrying would only delay an actionable prerequisite failure."""
    calls = []

    def fn():
        calls.append(1)
        raise exc

    with pytest.raises(type(exc)):
        cloud_smoke._call_with_retries("get_service", fn)
    assert len(calls) == 1


def test_backoff_grows_between_attempts(monkeypatch):
    delays = []
    monkeypatch.setattr(cloud_smoke.time, "sleep", delays.append)

    def fn():
        raise gexc.ServiceUnavailable("503")

    with pytest.raises(gexc.ServiceUnavailable):
        cloud_smoke._call_with_retries("list_executions", fn)
    assert delays == sorted(delays) and delays[0] < delays[-1]

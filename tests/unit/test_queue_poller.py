"""Unit tests for the background queue poller.

The optimization queue only advances inside ``BaseExecutor.pump()``, and until
this poller existed ``pump()`` ran only from ``submit`` and
``get_optimization_status``. A run queued past ``OPTIMIZATION_MAX_PARALLEL``
therefore sat QUEUED on an idle instance until some unrelated request arrived.
"""

from __future__ import annotations

import asyncio
import logging

import pytest
from google.api_core.exceptions import ServiceUnavailable

from google_meridian_mcp_server import bootstrap
from google_meridian_mcp_server.domain.errors import MeridianMcpError
from google_meridian_mcp_server.persistence.optimization_run_registry import (
    RunNotFoundError,
)
from tests.fakes.fake_executor import CountingExecutor, wait_for_calls

# Short enough to keep the suite fast, long enough to be a real sleep between
# ticks rather than a busy loop.
TICK = 0.01

_LOGGER = "google_meridian_mcp_server.bootstrap"


async def test_the_poller_pumps_on_every_tick():
    """Three pumps from one start proves a loop, not a single startup pump."""
    executor = CountingExecutor()
    task = bootstrap.start_queue_poller(executor, interval_seconds=TICK)
    try:
        await wait_for_calls(executor, 3)
    finally:
        await bootstrap.stop_queue_poller(task)
    assert executor.calls >= 3


async def test_stopping_the_poller_ends_the_pumping():
    executor = CountingExecutor()
    task = bootstrap.start_queue_poller(executor, interval_seconds=TICK)
    await wait_for_calls(executor, 2)

    await bootstrap.stop_queue_poller(task)

    assert task.cancelled()
    await asyncio.sleep(TICK * 5)  # let any in-flight pump thread finish
    settled = executor.calls
    await asyncio.sleep(TICK * 20)
    assert executor.calls == settled


async def test_a_registry_error_is_logged_and_the_loop_keeps_going(caplog):
    """RunNotFoundError is reachable from pump(): _fail_if_unfinished writes
    state for a run another instance may have deleted since the read."""
    caplog.set_level(logging.WARNING, logger=_LOGGER)
    executor = CountingExecutor(errors=[RunNotFoundError("run-1")])

    task = bootstrap.start_queue_poller(executor, interval_seconds=TICK)
    try:
        await wait_for_calls(executor, 3)
    finally:
        await bootstrap.stop_queue_poller(task)

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "run-1" in warnings[0].getMessage()
    assert warnings[0].exc_info is not None


async def test_a_cloud_api_error_is_logged_and_the_loop_keeps_going(caplog):
    """The cloud tier reaches the Cloud Run and GCS clients from inside pump()
    (_reap -> _is_alive, _claim -> claim_dispatch); a transient 503 there must
    not end the timer for the life of the instance."""
    caplog.set_level(logging.WARNING, logger=_LOGGER)
    executor = CountingExecutor(errors=[ServiceUnavailable("backend busy")])

    task = bootstrap.start_queue_poller(executor, interval_seconds=TICK)
    try:
        await wait_for_calls(executor, 3)
    finally:
        await bootstrap.stop_queue_poller(task)

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "backend busy" in warnings[0].getMessage()


async def test_an_unrecognised_error_ends_the_poller_loudly(caplog):
    """The catch is deliberately narrow, so an error outside the named families
    still ends the loop -- but never silently: a dead poller means queued runs
    are back to advancing only on request arrival, and that must be in the log."""
    caplog.set_level(logging.ERROR, logger=_LOGGER)
    executor = CountingExecutor(errors=[ValueError("not a pump failure")])

    task = bootstrap.start_queue_poller(executor, interval_seconds=TICK)
    # Bounded: a poller that never raises must fail this test, not hang it.
    with pytest.raises(ValueError, match="not a pump failure"):
        await asyncio.wait_for(task, timeout=2)
    await asyncio.sleep(0)  # let the done-callback run

    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert "poller" in errors[0].getMessage()


class _AnotherDomainError(MeridianMcpError):
    """A domain error pump() does not raise today -- the shape a future bug would take."""


async def test_a_domain_error_other_than_run_not_found_ends_the_poller_loudly(caplog):
    """The catch names ``RunNotFoundError``, not its base class: a different domain
    error escaping ``pump()`` is a bug nobody has seen, and logging past it would
    turn a loud failure into a queue that quietly stops draining."""
    caplog.set_level(logging.ERROR, logger=_LOGGER)
    executor = CountingExecutor(
        errors=[_AnotherDomainError("other_error", "unexpected")]
    )

    task = bootstrap.start_queue_poller(executor, interval_seconds=TICK)
    with pytest.raises(_AnotherDomainError, match="unexpected"):
        await asyncio.wait_for(task, timeout=2)
    await asyncio.sleep(0)  # let the done-callback run

    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert "poller" in errors[0].getMessage()

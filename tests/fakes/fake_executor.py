"""Counting executor stand-in for the background queue-poller tests."""

from __future__ import annotations

import asyncio
import threading


class CountingExecutor:
    """Executor stand-in that records every ``pump()`` call.

    Guarantees: ``calls`` is safe to read from the event loop while ``pump()``
    runs on a worker thread (the poller calls it through
    ``asyncio.to_thread``), and each scripted error is raised exactly once, in
    order, on the earliest pumps.
    """

    def __init__(self, errors: list[BaseException] | None = None) -> None:
        self._lock = threading.Lock()
        self._errors = list(errors or [])
        self.calls = 0

    def pump(self) -> None:
        """Record this pump and raise the next scripted error, if any."""
        with self._lock:
            self.calls += 1
            error = self._errors.pop(0) if self._errors else None
        if error is not None:
            raise error

    def reconcile_orphans(self) -> None:
        """No-op: the lifespan reconciles at startup, before the poller runs."""


async def wait_for_calls(
    executor: CountingExecutor, count: int, *, timeout: float = 5.0
) -> None:
    """Wait until *executor* has recorded *count* pumps, else fail the test.

    Polled rather than event-driven on purpose: ``pump()`` runs on a worker
    thread, where setting an ``asyncio.Event`` would not be loop-safe.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while executor.calls < count:
        if loop.time() > deadline:
            raise AssertionError(
                f"expected at least {count} pump() calls within {timeout}s; "
                f"saw {executor.calls}"
            )
        await asyncio.sleep(0.005)

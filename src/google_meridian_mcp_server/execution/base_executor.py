"""Executor template: concurrency gate, launch lifecycle, crash reconciliation."""

from __future__ import annotations

import abc
import collections
from datetime import datetime, timezone
from typing import Any

from google_meridian_mcp_server.domain.optimization import (
    OptimizationRun,
    OptimizationRunState,
    RunStatus,
)
from google_meridian_mcp_server.persistence.optimization_run_registry import (
    OptimizationRunRegistry,
)


class BaseExecutor(abc.ABC):
    def __init__(
        self,
        registry: OptimizationRunRegistry,
        *,
        max_parallel: int,
        heartbeat_stale_seconds: int,
    ) -> None:
        self._registry = registry
        self._max_parallel = max_parallel
        self._stale_seconds = heartbeat_stale_seconds
        self._handles: dict[str, Any] = {}
        self._queue: collections.deque[str] = collections.deque()

    @abc.abstractmethod
    def _launch(self, run: OptimizationRun) -> Any: ...
    @abc.abstractmethod
    def _is_alive(self, handle: Any) -> bool: ...

    def submit(self, run: OptimizationRun) -> None:
        self._registry.write_state(
            OptimizationRunState(run_id=run.run_id, status=RunStatus.QUEUED)
        )
        self._queue.append(run.run_id)
        self.pump()

    def pump(self) -> None:
        self._reap()
        while self._queue and len(self._handles) < self._max_parallel:
            run_id = self._queue.popleft()
            run = self._registry.get_record(run_id)
            self._handles[run_id] = self._launch(run)

    def _reap(self) -> None:
        for run_id, handle in list(self._handles.items()):
            if self._is_alive(handle):
                # Handle is alive: liveness is authoritative for the local subprocess
                # tier, so we leave it tracked without touching the registry.
                # _reconcile_stale is reserved for the Phase-2 cloud tier, where
                # process liveness is not locally observable and stale heartbeats are
                # the only crash signal.  It is intentionally NOT invoked here.
                continue
            del self._handles[run_id]
            state = self._registry.get_state(run_id)
            if state.status in (RunStatus.RUNNING, RunStatus.QUEUED):
                # process exited without writing a terminal state -> crashed.
                self._registry.write_state(
                    OptimizationRunState(
                        run_id=run_id,
                        status=RunStatus.FAILED,
                        error={
                            "code": "worker_lost",
                            "message": "worker exited without writing a result",
                        },
                    )
                )

    def _reconcile_stale(self, run_id: str) -> None:
        """Phase-2 cloud-tier crash reconciliation (not invoked for locally-tracked alive processes).

        For the local subprocess tier, _is_alive(handle) is authoritative.  This
        method is kept for the Phase-2 cloud tier where a remote process's liveness
        cannot be determined locally and a stale heartbeat is the only crash signal.
        """
        state = self._registry.get_state(run_id)
        if state.status != RunStatus.RUNNING or not state.heartbeat_at:
            return
        last = datetime.fromisoformat(state.heartbeat_at)
        age = (datetime.now(timezone.utc) - last).total_seconds()
        if age > self._stale_seconds:
            self._registry.write_state(
                OptimizationRunState(
                    run_id=run_id,
                    status=RunStatus.FAILED,
                    error={
                        "code": "worker_lost",
                        "message": f"heartbeat stale ({int(age)}s)",
                    },
                )
            )
            self._handles.pop(run_id, None)

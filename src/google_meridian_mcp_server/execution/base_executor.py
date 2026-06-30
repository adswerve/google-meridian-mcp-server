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
                self._on_alive(run_id)
                continue
            del self._handles[run_id]
            self._fail_if_unfinished(run_id, "worker exited without writing a result")

    def _on_alive(self, run_id: str) -> None:
        """Hook: local tier no-ops; cloud tier checks stale heartbeats."""
        return

    def _fail_if_unfinished(self, run_id: str, message: str) -> None:
        state = self._registry.get_state(run_id)
        if state.status in (RunStatus.RUNNING, RunStatus.QUEUED):
            self._registry.write_state(
                OptimizationRunState(
                    run_id=run_id,
                    status=RunStatus.FAILED,
                    error={"code": "worker_lost", "message": message},
                )
            )

    def _reconcile_stale(self, run_id: str) -> None:
        """Cloud-tier crash reconciliation via stale heartbeat detection.

        For the local subprocess tier, _is_alive(handle) is authoritative and
        _on_alive is a no-op so this is never called.  For the cloud tier,
        _on_alive delegates here because remote process liveness is coarse and
        a stale heartbeat is the authoritative crash signal.
        Uses expected_generation so a live heartbeat written between our read
        and write rejects the false failure.
        """
        gen = self._registry.get_state_generation(run_id)
        state = self._registry.get_state(run_id)
        if state.status != RunStatus.RUNNING or not state.heartbeat_at:
            return
        last = datetime.fromisoformat(state.heartbeat_at)
        age = (datetime.now(timezone.utc) - last).total_seconds()
        if age > self._stale_seconds:
            try:
                self._registry.write_state(
                    OptimizationRunState(
                        run_id=run_id,
                        status=RunStatus.FAILED,
                        error={
                            "code": "worker_lost",
                            "message": f"heartbeat stale ({int(age)}s)",
                        },
                    ),
                    expected_generation=gen,
                )
            except Exception:  # noqa: BLE001 - precondition failed => worker still alive
                return
            self._handles.pop(run_id, None)

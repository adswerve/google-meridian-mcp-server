"""Executor template: concurrency gate, launch lifecycle, crash reconciliation."""

from __future__ import annotations

import abc
import collections
import threading
from datetime import datetime, timezone
from typing import Any

from google_meridian_mcp_server.domain.optimization import (
    OptimizationRun,
    OptimizationRunState,
    RunStatus,
)
from google_meridian_mcp_server.persistence.optimization_run_registry import (
    OptimizationRunRegistry,
    ResultNotReadyError,
    RunNotFoundError,
)

# A RUNNING run whose heartbeat is older than this is reconciled as crashed.
# Was OPTIMIZATION_HEARTBEAT_STALE_SECONDS: an internal liveness detail, not a
# deployment input.
DEFAULT_HEARTBEAT_STALE_SECONDS = 60

# A dispatch claim older than this with no execution name recorded means the
# claiming process died between claiming and calling run_job.
#
# Deliberately far larger than the heartbeat window. The state read in
# _fail_stale_dispatch is not sufficient protection on its own: it only helps
# once the worker has written RUNNING, and an execution still cold-starting (a
# multi-gigabyte GPU image pull, an L4 capacity wait) leaves state.json at
# QUEUED while the execution is live and billing. Failing that would produce a
# wrongly-failed run AND a leaked execution -- the exact pair this rule exists
# to prevent. A module constant, not an env var: the env-var simplification
# demoted these deliberately.
DEFAULT_DISPATCH_STALE_SECONDS = 1800


class BaseExecutor(abc.ABC):
    def __init__(
        self,
        registry: OptimizationRunRegistry,
        *,
        max_parallel: int,
        heartbeat_stale_seconds: int = DEFAULT_HEARTBEAT_STALE_SECONDS,
    ) -> None:
        self._registry = registry
        self._max_parallel = max_parallel
        self._stale_seconds = heartbeat_stale_seconds
        self._handles: dict[str, Any] = {}
        self._queue: collections.deque[str] = collections.deque()
        # F6(b): `_handles`/`_queue` are mutated by submit/pump/cancel/_reap and
        # the reconcile paths. Once the MCP tool handlers offload their sync
        # service calls onto worker threads (asyncio.to_thread), those methods
        # can run concurrently from multiple threads -> a data race on plain
        # dict/deque mutation. A single coarse RLock (reentrant so a locked
        # method can call another locked method on the same thread, e.g.
        # submit -> pump -> _reap) guards every method touching either
        # structure. These ops are infrequent and cheap, so holding the lock
        # across the (usually local-registry) I/O inside them is an acceptable
        # trade for correctness over minimizing hold time.
        self._lock = threading.RLock()

    @abc.abstractmethod
    def _launch(self, run: OptimizationRun) -> Any: ...
    @abc.abstractmethod
    def _is_alive(self, handle: Any) -> bool: ...
    @abc.abstractmethod
    def _terminate(self, handle: Any) -> None: ...

    def cancel(self, run_id: str) -> None:
        with self._lock:
            handle = self._handles.pop(run_id, None)
            if handle is not None:
                self._terminate(handle)
            try:
                self._queue.remove(run_id)
            except ValueError:
                pass
            state = self._registry.get_state(run_id)
            if state.status in (RunStatus.QUEUED, RunStatus.RUNNING):
                self._registry.write_state(
                    OptimizationRunState(run_id=run_id, status=RunStatus.CANCELED)
                )

    def reconcile_orphans(self) -> None:
        """Startup crash reconciliation for runs left over by a stopped server.

        Default: only RUNNING runs are considered, via stale-heartbeat
        detection -- a cloud worker CAN outlive the server process, so a
        fresh heartbeat may mean the run is still legitimately in flight.
        CloudRunJobExecutor overrides this to also rebuild the in-memory
        queue from QUEUED runs and re-adopt in-flight executions by their
        recorded execution_name -- see CloudRunJobExecutor.reconcile_orphans.
        The local subprocess tier overrides this with an unconditional fail
        -- see AsyncSubprocessExecutor.reconcile_orphans.
        """
        with self._lock:
            for summary in self._registry.list(status=RunStatus.RUNNING):
                self._reconcile_stale(summary.run_id)

    def submit(self, run: OptimizationRun) -> None:
        with self._lock:
            self._registry.write_state(
                OptimizationRunState(run_id=run.run_id, status=RunStatus.QUEUED)
            )
            self._queue.append(run.run_id)
            self.pump()

    def pump(self) -> None:
        with self._lock:
            self._reap()
            while self._queue and len(self._handles) < self._max_parallel:
                run_id = self._queue.popleft()
                try:
                    run = self._registry.get_record(run_id)
                except RunNotFoundError:
                    # Deleted while still queued (OptimizationService.delete
                    # dequeues via cancel(), but tolerate a stale id regardless):
                    # nothing to launch, and this must not escape into whatever
                    # unrelated tool call happened to trigger this pump().
                    continue
                claim = self._claim(run_id)
                if claim is None:
                    # Another instance owns this run. Do NOT re-enqueue and do
                    # NOT fail it -- the winner is dispatching it.
                    continue
                try:
                    handle = self._launch(run)
                except Exception as exc:  # noqa: BLE001 - launch failures must not escape pump()
                    self._fail_if_unfinished(
                        run_id,
                        f"failed to launch worker: {exc}",
                        code=self._launch_error_code(exc),
                    )
                    continue
                self._handles[run_id] = handle
                self._record_dispatch(claim, handle)

    def _reap(self) -> None:
        with self._lock:
            for run_id, handle in list(self._handles.items()):
                if self._is_alive(handle):
                    self._on_alive(run_id)
                    continue
                del self._handles[run_id]
                self._fail_if_unfinished(
                    run_id, "worker exited without writing a result"
                )

    def _on_alive(self, run_id: str) -> None:
        """Hook: local tier no-ops; cloud tier checks stale heartbeats."""
        return

    def _claim(self, run_id: str) -> Any | None:
        """Hook: claim the right to dispatch, returning an opaque token.

        Returns None when another instance owns the run. The local tier is
        single-process, so the run_id itself is a sufficient truthy token.
        """
        return run_id

    def _record_dispatch(self, claim: Any, handle: Any) -> None:
        """Hook: persist the dispatch handle. Local tier keeps none."""
        return

    def _launch_error_code(self, exc: Exception) -> str:
        """Hook: classify a launch failure. Local tier has nothing to classify."""
        return "worker_lost"

    def _fail_if_unfinished(
        self, run_id: str, message: str, *, code: str = "worker_lost"
    ) -> None:
        try:
            state = self._registry.get_state(run_id)
        except RunNotFoundError:
            # The run was deleted while its handle was still pending reap; a
            # deleted run is not "unfinished", so there is nothing to fail.
            return
        if state.status not in (RunStatus.RUNNING, RunStatus.QUEUED):
            return
        try:
            # The worker writes result.json (worker.py:231) BEFORE its terminal
            # write_state (:243). A container killed in between leaves a valid
            # result under a RUNNING state, and failing it destroys a completed
            # run. Adoption is what makes this window reachable across
            # processes, but a local worker has it too.
            self._registry.get_result(run_id)
        except (ResultNotReadyError, RunNotFoundError):
            pass
        else:
            return
        self._registry.write_state(
            OptimizationRunState(
                run_id=run_id,
                status=RunStatus.FAILED,
                error={"code": code, "message": message},
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
        with self._lock:
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

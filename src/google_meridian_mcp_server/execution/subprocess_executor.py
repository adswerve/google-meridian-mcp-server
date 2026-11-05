"""Local-tier optimization executor: async job lifecycle + subprocess mechanics."""

from __future__ import annotations

import contextlib
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from google_meridian_mcp_server.domain.optimization import (
    OptimizationRun,
    OptimizationRunDispatch,
    RunStatus,
)
from google_meridian_mcp_server.execution.base_executor import (
    DEFAULT_HEARTBEAT_STALE_SECONDS,
    BaseExecutor,
)
from google_meridian_mcp_server.execution.base_subprocess import BaseSubprocessExecutor
from google_meridian_mcp_server.persistence.optimization_run_registry import (
    OptimizationRunRegistry,
    RunNotFoundError,
)

# F10b: exported so bootstrap/server code can sweep the same directory a
# default-constructed executor writes worker logs to, without hardcoding the
# path twice.
DEFAULT_LOG_ROOT = "/tmp/mmm-worker-logs"


class AsyncSubprocessExecutor(BaseExecutor, BaseSubprocessExecutor):
    def __init__(
        self,
        registry: OptimizationRunRegistry,
        *,
        max_parallel: int,
        heartbeat_stale_seconds: int = DEFAULT_HEARTBEAT_STALE_SECONDS,
        log_root: str | Path = DEFAULT_LOG_ROOT,
        python_executable: str | None = None,
    ) -> None:
        BaseExecutor.__init__(
            self,
            registry,
            max_parallel=max_parallel,
            heartbeat_stale_seconds=heartbeat_stale_seconds,
        )
        prefix = (
            [python_executable, "-m", BaseSubprocessExecutor.WORKER_MODULE]
            if python_executable
            else None
        )
        # child_env() supplies MERIDIAN_BACKEND and MERIDIAN_ENABLE_JAX_X64.
        BaseSubprocessExecutor.__init__(self, worker_argv_prefix=prefix)
        self._log_root = Path(log_root)
        # Launched runs found at startup that may be a live peer's; re-checked
        # by every pump() until they end or go silent. See reconcile_orphans.
        self._watched: set[str] = set()

    def _launch(self, run: OptimizationRun) -> Any:
        self._log_root.mkdir(parents=True, exist_ok=True)
        log_file = open(self._log_root / f"{run.run_id}.log", "w")  # noqa: SIM115
        try:
            return subprocess.Popen(
                self.worker_argv(),  # optimization mode: no argv; env carries run id
                env=self.child_env({"OPTIMIZATION_RUN_ID": run.run_id}),
                **self.popen_redirect_kwargs(log_file),
            )
        finally:
            log_file.close()  # child holds its own dup'd fd

    def _is_alive(self, handle: Any) -> bool:
        return handle.poll() is None

    def _terminate(self, handle: Any) -> None:
        self.kill_group(handle.pid)
        # Reap the child after SIGKILL so it doesn't linger as a zombie: a
        # bounded wait (the process is already dead or dying from the
        # SIGKILL above, so this should return almost immediately).
        with contextlib.suppress(Exception):
            handle.wait(timeout=5)

    def _claim(self, run_id: str) -> Any | None:
        # The same create-if-absent claim as the cloud tier. Reconcile adopts
        # QUEUED runs, so a run queued on a live peer can sit in two instances'
        # queues at once; only the claim winner launches it.
        dispatch = OptimizationRunDispatch(
            run_id=run_id, claimed_at=datetime.now(timezone.utc).isoformat()
        )
        return dispatch if self._registry.claim_dispatch(dispatch) else None

    def reconcile_orphans(self) -> None:
        """Local tier: recover queued runs, fail runs whose worker went silent.

        A fresh server process starts with empty `_handles`/`_queue`. The
        registry may be shared with other live instances (a GCS registry
        behind an autoscaled service), so a run that is QUEUED or RUNNING in
        it may belong to a live peer, not to a dead predecessor:

        - QUEUED and never claimed: nobody launched it. Adopt it into this
          queue, as the cloud tier does. If a live peer also holds it, the
          dispatch claim (`_claim`) lets exactly one instance launch it.
        - RUNNING, or QUEUED and claimed (launched, worker not yet RUNNING):
          judged by the worker's last sign of life -- its heartbeat (written
          every 8 s by worker.py) or the claim time. Silent for longer than
          the heartbeat window: failed now, as before (the PID-1 parent-death
          guard in worker.py means a local worker cannot outlive its server).
          Otherwise it may be a live peer's, so it is watched and failed by a
          later pump() only if it goes silent (`_reap`).

        What this does not make safe across instances: cancel only stops a
        worker on the instance that launched it, and a peer whose CPU is
        throttled long enough to miss the heartbeat window is treated as dead.
        """
        with self._lock:
            for summary in self._registry.list(status=RunStatus.RUNNING):
                if not self._fail_if_silent(summary.run_id):
                    self._watched.add(summary.run_id)
            # list() sorts created_at descending; adopt oldest-first.
            for summary in sorted(
                self._registry.list(status=RunStatus.QUEUED),
                key=lambda s: s.created_at,
            ):
                if self._registry.get_dispatch(summary.run_id) is None:
                    self._queue.append(summary.run_id)
                elif not self._fail_if_silent(summary.run_id):
                    self._watched.add(summary.run_id)
            self.pump()

    def _reap(self) -> None:
        with self._lock:
            super()._reap()
            self._watched = {
                run_id for run_id in self._watched if not self._fail_if_silent(run_id)
            }

    def _fail_if_silent(self, run_id: str) -> bool:
        """Fail a launched run whose worker has gone silent.

        Returns True when nothing is left to watch: the run has ended, is
        gone, or was failed here. False while its worker still shows life.
        """
        try:
            state = self._registry.get_state(run_id)
        except RunNotFoundError:
            return True
        if state.status is RunStatus.RUNNING:
            # Heartbeat age, result guard and generation-guarded write.
            self._reconcile_stale(run_id)
            return self._registry.get_state(run_id).status is not RunStatus.RUNNING
        if state.status is not RunStatus.QUEUED:
            return True
        dispatch = self._registry.get_dispatch(run_id)
        if dispatch is not None:
            claimed = datetime.fromisoformat(dispatch.claimed_at)
            age = (datetime.now(timezone.utc) - claimed).total_seconds()
            if age <= self._stale_seconds:
                return False
        self._fail_if_unfinished(run_id, "worker never reported running")
        return True

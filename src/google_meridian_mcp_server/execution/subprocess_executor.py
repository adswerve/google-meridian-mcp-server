"""Local-tier optimization executor: async job lifecycle + subprocess mechanics."""

from __future__ import annotations

import contextlib
import subprocess
from pathlib import Path
from typing import Any

from google_meridian_mcp_server.domain.optimization import OptimizationRun, RunStatus
from google_meridian_mcp_server.execution.base_executor import (
    DEFAULT_HEARTBEAT_STALE_SECONDS,
    BaseExecutor,
)
from google_meridian_mcp_server.execution.base_subprocess import BaseSubprocessExecutor
from google_meridian_mcp_server.persistence.optimization_run_registry import (
    OptimizationRunRegistry,
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

    def reconcile_orphans(self) -> None:
        """Local tier: unconditionally fail every RUNNING/QUEUED run at startup.

        `_handles`/`_queue` are in-memory, so a fresh server process starts
        with neither -- there is no live handle to reap and no heartbeat
        staleness window to wait out. The PID-1 parent-death guard in
        worker.py guarantees a local worker subprocess CANNOT survive its
        parent server process, so any run still RUNNING or QUEUED when this
        (new) server starts is provably dead / was never launched. Unlike the
        cloud tier (BaseExecutor.reconcile_orphans), this also covers QUEUED:
        a queued-but-never-launched run has no chance of being picked up by
        anything else.

        Single-instance-only by contract: this is only correct for a lone
        server instance against its registry. Under a SHARED registry with
        multiple concurrent server instances, one instance's startup
        reconcile could transiently FAIL another instance's live run (see
        spec: "local tier is single-instance-only"); multi-instance
        deployment requires the cloud tier instead.
        """
        with self._lock:
            for status in (RunStatus.RUNNING, RunStatus.QUEUED):
                for summary in self._registry.list(status=status):
                    run_id = summary.run_id
                    self._handles.pop(run_id, None)
                    try:
                        self._queue.remove(run_id)
                    except ValueError:
                        pass
                    self._fail_if_unfinished(
                        run_id, "server restarted; local worker cannot survive"
                    )

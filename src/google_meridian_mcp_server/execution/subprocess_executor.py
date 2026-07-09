"""Local-tier optimization executor: async job lifecycle + subprocess mechanics."""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from google_meridian_mcp_server.domain.optimization import OptimizationRun
from google_meridian_mcp_server.execution.base_executor import BaseExecutor
from google_meridian_mcp_server.execution.base_subprocess import BaseSubprocessExecutor
from google_meridian_mcp_server.persistence.optimization_run_registry import (
    OptimizationRunRegistry,
)


class AsyncSubprocessExecutor(BaseExecutor, BaseSubprocessExecutor):
    def __init__(
        self,
        registry: OptimizationRunRegistry,
        *,
        max_parallel: int,
        heartbeat_stale_seconds: int,
        backend: str,
        log_root: str | Path = "/tmp/mmm-worker-logs",
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
        BaseSubprocessExecutor.__init__(
            self, worker_argv_prefix=prefix, env_base={"MERIDIAN_BACKEND": backend}
        )
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

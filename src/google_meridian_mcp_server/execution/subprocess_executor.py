"""Executor that runs the worker as a local subprocess."""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Any

from google_meridian_mcp_server.domain.optimization import OptimizationRun
from google_meridian_mcp_server.execution.base_executor import BaseExecutor
from google_meridian_mcp_server.persistence.optimization_run_registry import (
    OptimizationRunRegistry,
)


class SubprocessExecutor(BaseExecutor):
    def __init__(
        self,
        registry: OptimizationRunRegistry,
        *,
        max_parallel: int,
        heartbeat_stale_seconds: int,
        backend: str,
        python_executable: str = sys.executable,
    ) -> None:
        super().__init__(
            registry,
            max_parallel=max_parallel,
            heartbeat_stale_seconds=heartbeat_stale_seconds,
        )
        self._backend = backend
        self._python = python_executable

    def _launch(self, run: OptimizationRun) -> Any:
        env = dict(os.environ)
        env["OPTIMIZATION_RUN_ID"] = run.run_id
        env["MERIDIAN_BACKEND"] = self._backend
        return subprocess.Popen(
            [self._python, "-m", "google_meridian_mcp_server.execution.worker"],
            env=env,
        )

    def _is_alive(self, handle: Any) -> bool:
        return handle.poll() is None

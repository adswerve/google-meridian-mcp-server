"""Shared launch mechanics for Meridian worker subprocesses (no Meridian import)."""
from __future__ import annotations

import contextlib
import os
import signal
import sys
from typing import IO

WORKER_MODULE = "google_meridian_mcp_server.execution.worker"
DEFAULT_WORKER_ARGV_PREFIX = [sys.executable, "-m", WORKER_MODULE]
_HYGIENE_DEFAULTS = {
    "TF_CPP_MIN_LOG_LEVEL": "3",
    "TF_NUM_INTRAOP_THREADS": "2",
    "TF_NUM_INTEROP_THREADS": "2",
}


class BaseSubprocessExecutor:
    WORKER_MODULE = WORKER_MODULE

    def __init__(self, *, worker_argv_prefix=None, env_base=None) -> None:
        self._argv_prefix = list(worker_argv_prefix or DEFAULT_WORKER_ARGV_PREFIX)
        self._env_base = dict(env_base or {})

    def worker_argv(self, *args: str) -> list[str]:
        return [*self._argv_prefix, *args]

    def child_env(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        env = dict(os.environ)
        for k, v in _HYGIENE_DEFAULTS.items():
            env.setdefault(k, v)          # never clobber operator-set values
        env.update(self._env_base)
        if extra:
            env.update(extra)
        return env

    def popen_redirect_kwargs(self, log_file: IO) -> dict:
        return {"stdout": log_file, "stderr": log_file, "start_new_session": True}

    @staticmethod
    def kill_group(pid: int) -> None:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(os.getpgid(pid), signal.SIGKILL)

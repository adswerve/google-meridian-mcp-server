"""Shared launch mechanics for Meridian worker subprocesses (no Meridian import)."""

from __future__ import annotations

import contextlib
import os
import signal
import sys
from typing import IO

WORKER_MODULE = "google_meridian_mcp_server.execution.worker"
DEFAULT_WORKER_ARGV_PREFIX = [sys.executable, "-m", WORKER_MODULE]
# D2: JAX is the only supported backend. Set EXPLICITLY as a module constant
# rather than relying on Meridian's own default -- that default has already
# flipped once (TensorFlow -> JAX in 2.0.0) and could flip again.
MERIDIAN_BACKEND = "jax"
# Applied with setdefault: these carry OPERATOR intent, so an operator-set
# value wins.
_HYGIENE_DEFAULTS = {
    "TF_NUM_INTRAOP_THREADS": "2",
    "TF_NUM_INTEROP_THREADS": "2",
}
# Applied unconditionally. os.environ stopped being a proxy for operator
# intent the moment a library began writing to it: jax/__init__.py line 17
# runs os.environ.setdefault('TF_CPP_MIN_LOG_LEVEL', '1') at import time, and
# jax is a CORE dependency of Meridian 2.0. Both tables are applied BEFORE
# env_base and extra, so explicit executor configuration still wins.
_HYGIENE_OVERRIDES = {
    "TF_CPP_MIN_LOG_LEVEL": "3",
    "MERIDIAN_BACKEND": MERIDIAN_BACKEND,
    # D3: 64-bit precision is pinned, never inherited from a library default.
    "MERIDIAN_ENABLE_JAX_X64": "true",
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
            env.setdefault(k, v)  # never clobber operator-set values
        env.update(_HYGIENE_OVERRIDES)  # library-set values are not operator intent
        env.update(self._env_base)
        # Captured HERE, in the parent, at spawn time -- before fork+exec and
        # therefore before the child's own import chain (0.5-2s) can run. The
        # child's PID-1 parent-death guard (worker.py) reads this instead of
        # calling os.getppid() itself: if it self-captured "original" ppid only
        # after that import window, a parent death DURING the window would
        # already have reparented the child, and it would wrongly record the
        # reaper as its "original" parent -- masking the orphan forever.
        env["MERIDIAN_PARENT_PID"] = str(os.getpid())
        if extra:
            env.update(extra)
        return env

    def popen_redirect_kwargs(self, log_file: IO) -> dict:
        return {"stdout": log_file, "stderr": log_file, "start_new_session": True}

    @staticmethod
    def kill_group(pid: int) -> None:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(os.getpgid(pid), signal.SIGKILL)

"""Synchronous (await-inline) analysis runner over a throwaway worker subprocess."""
from __future__ import annotations

import asyncio
import contextlib
import json
import shutil
import tempfile
from pathlib import Path

from google_meridian_mcp_server.domain.errors import (
    MeridianMcpError,
    ServerBusyError,
    WorkerFailedError,
    WorkerTimeoutError,
)
from google_meridian_mcp_server.execution.base_subprocess import BaseSubprocessExecutor

_LOG_TAIL = 4096


class SyncSubprocessExecutor(BaseSubprocessExecutor):
    def __init__(self, *, semaphore, run_timeout, queue_wait_timeout,
                 max_response_bytes, workdir_root, worker_argv_prefix=None, env_base=None):
        super().__init__(worker_argv_prefix=worker_argv_prefix, env_base=env_base)
        self._sem = semaphore
        self._run_timeout = run_timeout
        self._queue_wait_timeout = queue_wait_timeout
        self._max_bytes = max_response_bytes
        self._root = Path(workdir_root)
        self._live: set[int] = set()

    async def run(self, operation, model_id, params) -> dict:
        try:
            await asyncio.wait_for(self._sem.acquire(), timeout=self._queue_wait_timeout)
        except asyncio.TimeoutError:
            raise ServerBusyError() from None
        try:
            return await self._run_locked(operation, model_id, params)
        finally:
            self._sem.release()

    async def _run_locked(self, operation, model_id, params) -> dict:
        self._root.mkdir(parents=True, exist_ok=True)
        workdir = Path(tempfile.mkdtemp(dir=self._root))
        req, resp, logp = workdir/"req.json", workdir/"resp.json", workdir/"log"
        req.write_text(json.dumps({"operation": operation, "model_id": model_id, "params": params}))
        proc, keep = None, False
        log_file = open(logp, "w")  # noqa: SIM115
        try:
            # Shield the spawn itself: create_subprocess_exec can fork+exec the
            # child and then be cancelled while still awaiting its internal
            # connection-made waiter, which would raise CancelledError to us
            # *before* we ever get the Process back — orphaning the child with
            # no pid in self._live for kill_group/shutdown() to find. Shielding
            # lets the spawn finish so the done-callback can still kill it.
            spawn_task = asyncio.ensure_future(asyncio.create_subprocess_exec(
                *self.worker_argv("analysis", str(req), str(resp)),
                env=self.child_env(None), **self.popen_redirect_kwargs(log_file)))
            try:
                proc = await asyncio.shield(spawn_task)
            except asyncio.CancelledError:
                spawn_task.add_done_callback(self._kill_if_spawned)
                raise
            self._live.add(proc.pid)
            try:
                rc = await asyncio.wait_for(proc.wait(), timeout=self._run_timeout)
            except asyncio.TimeoutError:
                self.kill_group(proc.pid)
                with contextlib.suppress(Exception):
                    await proc.wait()
                keep = True
                raise WorkerTimeoutError(f"exceeded {self._run_timeout}s",
                                         {"log_tail": self._tail(logp)}) from None
            except asyncio.CancelledError:
                self.kill_group(proc.pid)
                with contextlib.suppress(Exception):
                    await proc.wait()
                raise
            result, keep = self._decode(rc, resp, logp)
            return result
        finally:
            if proc is not None:
                self._live.discard(proc.pid)
            log_file.close()
            if not keep:
                shutil.rmtree(workdir, ignore_errors=True)

    def _decode(self, rc, resp, logp):
        # returns (result, keep_workdir). keep=True only on infra failure.
        if not resp.exists():
            raise WorkerFailedError(f"no response (exit {rc})", {"log_tail": self._tail(logp)})
        if resp.stat().st_size > self._max_bytes:
            raise WorkerFailedError("response too large", {"bytes": resp.stat().st_size})
        try:
            payload = json.loads(resp.read_text())
        except json.JSONDecodeError:
            raise WorkerFailedError(f"bad response (exit {rc})", {"log_tail": self._tail(logp)}) from None
        if payload.get("ok"):
            return payload["result"], False
        if "error" in payload:
            raise MeridianMcpError.from_payload(payload["error"])   # domain error: keep=False (workdir removed)
        raise WorkerFailedError(f"malformed response (exit {rc})", {"log_tail": self._tail(logp)})

    def _kill_if_spawned(self, spawn_task: "asyncio.Task") -> None:
        """Done-callback for a shielded spawn cancelled from above: if the
        child actually came into being, kill its process group even though
        we never got to add its pid to self._live."""
        if spawn_task.cancelled() or spawn_task.exception() is not None:
            return
        self.kill_group(spawn_task.result().pid)

    @staticmethod
    def _tail(logp) -> str:
        with contextlib.suppress(Exception):
            return Path(logp).read_bytes()[-_LOG_TAIL:].decode("utf-8", "replace")
        return ""

    async def shutdown(self) -> None:
        for pid in list(self._live):
            self.kill_group(pid)
        self._live.clear()

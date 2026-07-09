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
    def __init__(
        self,
        *,
        semaphore,
        run_timeout,
        queue_wait_timeout,
        max_response_bytes,
        workdir_root,
        worker_argv_prefix=None,
        env_base=None,
    ):
        super().__init__(worker_argv_prefix=worker_argv_prefix, env_base=env_base)
        self._sem = semaphore
        self._run_timeout = run_timeout
        self._queue_wait_timeout = queue_wait_timeout
        self._max_bytes = max_response_bytes
        self._root = Path(workdir_root)
        self._live: set[int] = set()
        self._cleanup_tasks: set = set()

    async def run(self, operation, model_id, params) -> dict:
        try:
            await asyncio.wait_for(
                self._sem.acquire(), timeout=self._queue_wait_timeout
            )
        except asyncio.TimeoutError:
            raise ServerBusyError() from None
        try:
            return await self._run_locked(operation, model_id, params)
        finally:
            self._sem.release()

    async def _run_locked(self, operation, model_id, params) -> dict:
        self._root.mkdir(parents=True, exist_ok=True)
        workdir = Path(tempfile.mkdtemp(dir=self._root))
        req, resp, logp = workdir / "req.json", workdir / "resp.json", workdir / "log"
        proc, keep, deferred_cleanup = None, False, False
        log_file = None
        try:
            try:
                req.write_text(
                    json.dumps(
                        {"operation": operation, "model_id": model_id, "params": params}
                    )
                )
                log_file = open(logp, "w")  # noqa: SIM115
                # Shield the spawn itself: create_subprocess_exec can fork+exec the
                # child and then be cancelled while still awaiting its internal
                # connection-made waiter, which would raise CancelledError to us
                # *before* we ever get the Process back — orphaning the child with
                # no pid in self._live for kill_group/shutdown() to find. Shielding
                # lets the spawn finish so a deferred teardown can kill+reap it and
                # only THEN remove the workdir (never before the child is dead).
                spawn_task = asyncio.ensure_future(
                    asyncio.create_subprocess_exec(
                        *self.worker_argv("analysis", str(req), str(resp)),
                        env=self.child_env(None),
                        **self.popen_redirect_kwargs(log_file),
                    )
                )
                try:
                    proc = await asyncio.shield(spawn_task)
                except asyncio.CancelledError:
                    # Hand ownership of workdir + log_file to a deferred teardown
                    # that fires once the shielded spawn resolves; the sync finally
                    # must NOT rmtree/close underneath the still-launching child.
                    deferred_cleanup = True
                    spawn_task.add_done_callback(
                        lambda t: self._on_spawn_after_cancel(t, workdir, log_file)
                    )
                    raise
            except Exception as exc:  # noqa: BLE001 - translate spawn/setup failures
                # EMFILE, disk full, etc: don't let a raw OSError escape past
                # tool handlers' `except MeridianMcpError` -- translate to a
                # worker_failed envelope instead of a bare protocol error.
                raise WorkerFailedError(f"failed to spawn worker: {exc}") from exc
            self._live.add(proc.pid)
            try:
                rc = await asyncio.wait_for(proc.wait(), timeout=self._run_timeout)
            except asyncio.TimeoutError:
                self.kill_group(proc.pid)
                with contextlib.suppress(Exception):
                    await proc.wait()
                keep = True
                raise WorkerTimeoutError(
                    f"exceeded {self._run_timeout}s", {"log_tail": self._tail(logp)}
                ) from None
            except asyncio.CancelledError:
                self.kill_group(proc.pid)
                with contextlib.suppress(Exception):
                    await proc.wait()
                raise
            try:
                result, keep = self._decode(rc, resp, logp)
            except WorkerFailedError:
                keep = True  # infra failure: retain workdir for postmortem
                raise
            except MeridianMcpError:
                # Well-formed error payload (e.g. internal_error) but the
                # worker still exited non-zero -> infra/internal failure, not
                # a normal domain outcome. Retain the workdir+log so the
                # traceback (child-log-only per spec) survives. A domain
                # error with rc == 0 keeps the existing keep=False behavior.
                if rc != 0:
                    keep = True
                raise
            return result
        finally:
            if proc is not None:
                self._live.discard(proc.pid)
            if not deferred_cleanup:
                if log_file is not None:
                    log_file.close()
                if not keep:
                    shutil.rmtree(workdir, ignore_errors=True)

    def _decode(self, rc, resp, logp):
        # returns (result, keep_workdir). keep=True only on infra failure.
        if not resp.exists():
            raise WorkerFailedError(
                f"no response (exit {rc})", {"log_tail": self._tail(logp)}
            )
        if resp.stat().st_size > self._max_bytes:
            size = resp.stat().st_size
            # Unlink the oversized file itself before raising: the workdir is
            # retained for postmortem (WorkerFailedError -> keep=True), but
            # retaining the exact multi-hundred-MiB file the size ceiling was
            # meant to guard against would defeat the point. The log tail is
            # what matters for debugging; that stays.
            with contextlib.suppress(OSError):
                resp.unlink()
            raise WorkerFailedError("response too large", {"bytes": size})
        try:
            payload = json.loads(resp.read_text())
        except json.JSONDecodeError:
            raise WorkerFailedError(
                f"bad response (exit {rc})", {"log_tail": self._tail(logp)}
            ) from None
        if payload.get("ok"):
            return payload["result"], False
        if "error" in payload:
            raise MeridianMcpError.from_payload(
                payload["error"]
            )  # domain error: keep=False (workdir removed)
        raise WorkerFailedError(
            f"malformed response (exit {rc})", {"log_tail": self._tail(logp)}
        )

    def _on_spawn_after_cancel(self, spawn_task, workdir, log_file) -> None:
        """Sync done-callback for a shielded spawn whose caller was cancelled.
        A done-callback can't await, so it schedules the async teardown that
        kills+reaps any child that came into being, then removes the workdir
        and closes the log file (ownership handed over by the cancel branch)."""
        proc = None
        if not spawn_task.cancelled() and spawn_task.exception() is None:
            proc = spawn_task.result()
        task = asyncio.ensure_future(self._deferred_teardown(proc, workdir, log_file))
        self._cleanup_tasks.add(task)
        task.add_done_callback(self._cleanup_tasks.discard)

    async def _deferred_teardown(self, proc, workdir, log_file) -> None:
        """Kill+reap the child (if any), THEN drop the workdir and log fd.
        Runs exactly once per spawn-cancel; the sync finally skipped both."""
        try:
            if proc is not None:
                self._live.discard(proc.pid)
                self.kill_group(proc.pid)
                with contextlib.suppress(Exception):
                    await proc.wait()
        finally:
            log_file.close()
            shutil.rmtree(workdir, ignore_errors=True)

    @staticmethod
    def _tail(logp) -> str:
        with contextlib.suppress(Exception):
            return Path(logp).read_bytes()[-_LOG_TAIL:].decode("utf-8", "replace")
        return ""

    async def shutdown(self) -> None:
        for pid in list(self._live):
            self.kill_group(pid)
        self._live.clear()
        # Drain any in-flight deferred teardowns (spawn-cancel path) so their
        # child is killed+reaped and their workdir/log fd released before the
        # loop stops — otherwise a pending spawn's callback would never fire.
        if self._cleanup_tasks:
            await asyncio.gather(*list(self._cleanup_tasks), return_exceptions=True)

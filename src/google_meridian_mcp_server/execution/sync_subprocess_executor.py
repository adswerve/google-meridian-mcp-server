"""Synchronous (await-inline) analysis runner over a throwaway worker subprocess."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shutil
import tempfile
import time
from pathlib import Path

from google_meridian_mcp_server.domain.errors import (
    MeridianMcpError,
    ResponseTooLargeError,
    WorkerFailedError,
    WorkerTimeoutError,
)
from google_meridian_mcp_server.execution.base_subprocess import BaseSubprocessExecutor

_LOG_TAIL = 4096

log = logging.getLogger(__name__)


def sweep_stale_entries(root: str | Path, ttl_seconds: float) -> None:
    """Remove files/dirs directly under *root* whose mtime is older than *ttl_seconds*.

    F10b: retained analysis workdirs (one per timeout/spawn-failure/
    non-domain-rc!=0 run) and optimization worker log files (one per
    run, forever) otherwise accumulate unboundedly on disk. Called once at
    server startup -- NOT on every spawn -- so this is a bounded, best-effort
    hygiene pass: a missing root is a no-op, and a failure removing any single
    entry (permissions, a concurrent deletion, ...) is swallowed so it can't
    abort the sweep of the rest.
    """
    root_path = Path(root)
    if not root_path.is_dir():
        return
    cutoff = time.time() - ttl_seconds
    for entry in root_path.iterdir():
        try:
            if entry.stat().st_mtime >= cutoff:
                continue
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink(missing_ok=True)
        except OSError:
            log.warning(
                "sweep_stale_entries: failed to remove %s", entry, exc_info=True
            )
            continue


class SyncSubprocessExecutor(BaseSubprocessExecutor):
    def __init__(
        self,
        *,
        run_timeout,
        max_response_bytes,
        workdir_root,
        worker_argv_prefix=None,
        env_base=None,
    ):
        super().__init__(worker_argv_prefix=worker_argv_prefix, env_base=env_base)
        self._run_timeout = run_timeout
        self._max_bytes = max_response_bytes
        self._root = Path(workdir_root)
        self._live: set[int] = set()
        self._cleanup_tasks: set = set()
        # F11: tracks every shielded spawn from creation until it resolves
        # (success, exception, or cancellation) -- independent of whether the
        # caller's own await was ever cancelled. shutdown() needs this because
        # a spawn genuinely in flight (caller not cancelled) has no pid in
        # `_live` yet and no entry in `_cleanup_tasks` yet either; without
        # tracking it, shutdown() would have nothing to wait for or cancel.
        self._pending_spawns: set = set()

    async def run(self, operation, model_id, params) -> dict:
        proc, keep, deferred_cleanup = None, False, False
        log_file = None
        workdir: Path | None = None
        try:
            try:
                # mkdir/mkdtemp are inside this wrapped try (not before it):
                # ENOSPC/EMFILE/permission errors here must translate to a
                # WorkerFailedError envelope too, same as a spawn failure --
                # not escape as a raw OSError past the tool handlers.
                self._root.mkdir(parents=True, exist_ok=True)
                workdir = Path(tempfile.mkdtemp(dir=self._root))
                req = workdir / "req.json"
                resp = workdir / "resp.json"
                logp = workdir / "log"
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
                # F11: track from creation to resolution, regardless of why it
                # resolves (normal return, exception, or cancellation) -- this
                # is what lets shutdown() find + drain a spawn that is still
                # pending because the CALLER (this coroutine) was never itself
                # cancelled, only shutdown() was invoked concurrently.
                self._pending_spawns.add(spawn_task)
                spawn_task.add_done_callback(self._pending_spawns.discard)
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
                # ResponseTooLargeError (raised in _decode before json.loads,
                # so it lands here too) takes the same path: on rc == 0 the
                # whole workdir is removed elsewhere (keep=False), but on
                # rc != 0 the workdir -- including the oversized resp.json --
                # is deliberately NOT unlinked and is retained for postmortem,
                # bounded only by ANALYSIS_WORKDIR_TTL_SECONDS. That error
                # also carries no log_tail, since it's raised before
                # self._tail(logp) would run.
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
                # workdir may be None if mkdir/mkdtemp itself is what failed
                # (translated to WorkerFailedError above) -- nothing to remove.
                if not keep and workdir is not None:
                    shutil.rmtree(workdir, ignore_errors=True)

    def _decode(self, rc, resp, logp):
        # returns (result, keep_workdir). keep=True only on infra failure.
        if not resp.exists():
            raise WorkerFailedError(
                f"no response (exit {rc})", {"log_tail": self._tail(logp)}
            )
        if resp.stat().st_size > self._max_bytes:
            raise ResponseTooLargeError(
                nbytes=resp.stat().st_size, limit_bytes=self._max_bytes
            )
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
        # Seek from the end instead of reading the whole file: on the failure
        # path this is meant to help diagnose, a multi-GB TF log must never be
        # fully loaded into memory just to report the last few KB of it.
        with contextlib.suppress(Exception):
            with open(logp, "rb") as f:
                size = f.seek(0, os.SEEK_END)
                f.seek(-min(_LOG_TAIL, size), os.SEEK_END)
                return f.read().decode("utf-8", "replace")
        return ""

    async def shutdown(self) -> None:
        # F11: if shutdown() runs while a shielded spawn is still unresolved
        # (the caller's own `run()` was never cancelled -- shutdown() is
        # racing a genuinely in-flight call), that spawn's pid isn't in
        # `_live` yet and its teardown isn't in `_cleanup_tasks` yet either --
        # nothing above would find or kill it. Cancelling the tracked spawn
        # task directly (not just the caller's shielded await) forces it to
        # resolve now; `run`'s own `except CancelledError` branch then
        # registers the existing deferred-teardown path exactly as it does for
        # a caller-cancelled run, so the same kill+reap+rmtree logic applies
        # with no new code path and no risk of double-kill/double-rmtree.
        #
        # Loop (bounded: no new work arrives during shutdown) until BOTH
        # `_pending_spawns` and `_cleanup_tasks` drain -- the continuation
        # that moves a settled spawn into `_cleanup_tasks` (or, if a race let
        # it spawn cleanly, into `_live`) runs as a separately scheduled
        # callback, not synchronously with our own cancel/gather.
        for _ in range(100):
            for pid in list(self._live):
                self.kill_group(pid)
            self._live.clear()

            pending = [t for t in self._pending_spawns if not t.done()]
            for t in pending:
                t.cancel()
            if self._pending_spawns:
                await asyncio.gather(
                    *list(self._pending_spawns), return_exceptions=True
                )

            # Drain any in-flight deferred teardowns (spawn-cancel path) so their
            # child is killed+reaped and their workdir/log fd released before the
            # loop stops — otherwise a pending spawn's callback would never fire.
            if self._cleanup_tasks:
                await asyncio.gather(*list(self._cleanup_tasks), return_exceptions=True)

            await asyncio.sleep(0)  # let scheduled continuations/callbacks run
            if not self._pending_spawns and not self._cleanup_tasks:
                break
        else:
            # R3: the cap was hit without both collections draining -- make a
            # stuck teardown visible instead of exiting silently.
            log.warning(
                "shutdown: drain loop hit its iteration cap with %d pending "
                "spawn(s) and %d cleanup task(s) still outstanding",
                len(self._pending_spawns),
                len(self._cleanup_tasks),
            )

        # D2: a spawn can resolve cleanly mid-drain -- landing its pid in
        # `_live` -- AFTER the last top-of-iteration kill pass but before the
        # loop's break check runs. Do one final kill pass over whatever is in
        # `_live` now so shutdown() can never exit leaving such a child alive.
        # kill_group already suppresses ProcessLookupError, so re-killing an
        # already-dead pid from an earlier pass is harmless.
        for pid in list(self._live):
            self.kill_group(pid)
        self._live.clear()

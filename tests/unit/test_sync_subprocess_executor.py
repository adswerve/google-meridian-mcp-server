import asyncio
import os
import subprocess
import sys
import textwrap
import time

import pytest

from google_meridian_mcp_server.domain.errors import MeridianMcpError
from google_meridian_mcp_server.execution.sync_subprocess_executor import (
    SyncSubprocessExecutor,
)

OK = textwrap.dedent("""
    import json,sys,os; req,resp=sys.argv[-2],sys.argv[-1]
    d=json.load(open(req)); tmp=resp+'.tmp'
    json.dump({'ok':True,'result':{'echo':d}}, open(tmp,'w')); os.replace(tmp,resp)
""")
ERR = OK.replace(
    "{'ok':True,'result':{'echo':d}}",
    "{'ok':False,'error':{'error_code':'missing_model_data','message':'no','details':{}}}",
)

# Mirrors worker.run_analysis's unexpected-exception branch: a well-formed
# internal_error payload, but the worker process exits non-zero (rc=1).
INTERNAL_ERROR_RC1 = textwrap.dedent("""
    import json,sys,os; req,resp=sys.argv[-2],sys.argv[-1]
    d=json.load(open(req)); tmp=resp+'.tmp'
    print("some traceback text", file=sys.stderr)
    json.dump(
        {'ok': False, 'error': {'error_code': 'internal_error', 'message': 'RuntimeError', 'details': {}}},
        open(tmp, 'w'),
    )
    os.replace(tmp, resp)
    sys.exit(1)
""")


def mk(tmp, script, **o):
    kw = dict(
        semaphore=asyncio.Semaphore(2),
        run_timeout=10.0,
        queue_wait_timeout=5.0,
        max_response_bytes=10_000_000,
        workdir_root=str(tmp),
        worker_argv_prefix=[sys.executable, "-c", script],
    )
    kw.update(o)
    return SyncSubprocessExecutor(**kw)


async def test_ok(tmp_path):
    out = await mk(tmp_path, OK).run("get_contribution", "m1", {"x": 1})
    assert out == {
        "echo": {"operation": "get_contribution", "model_id": "m1", "params": {"x": 1}}
    }


async def test_domain_error(tmp_path):
    with pytest.raises(MeridianMcpError) as e:
        await mk(tmp_path, ERR).run("op", "m1", {})
    assert e.value.error_code == "missing_model_data"


async def test_nonzero_exit_worker_failed(tmp_path):
    with pytest.raises(MeridianMcpError) as e:
        await mk(tmp_path, "import sys; sys.exit(3)").run("op", "m1", {})
    assert e.value.error_code == "worker_failed"


async def test_unparseable_response(tmp_path):
    bad = "import sys,os; resp=sys.argv[-1]; open(resp,'w').write('{not json')"
    with pytest.raises(MeridianMcpError) as e:
        await mk(tmp_path, bad).run("op", "m1", {})
    assert e.value.error_code == "worker_failed"


async def test_response_too_large(tmp_path):
    big = "import sys,os,json; resp=sys.argv[-1]; open(resp,'w').write('{\"ok\":true,\"result\":\"'+ 'x'*20 +'\"}')"
    with pytest.raises(MeridianMcpError):
        await mk(tmp_path, big, max_response_bytes=8).run("op", "m1", {})


async def test_timeout_kills_fast(tmp_path):
    t0 = time.time()
    with pytest.raises(MeridianMcpError) as e:
        await mk(tmp_path, "import time; time.sleep(600)", run_timeout=1.0).run(
            "op", "m1", {}
        )
    assert e.value.error_code == "worker_timeout" and time.time() - t0 < 30


async def test_server_busy_when_no_slot(tmp_path):
    sem = asyncio.Semaphore(0)  # no slots
    r = mk(tmp_path, OK, semaphore=sem, queue_wait_timeout=0.2)
    with pytest.raises(MeridianMcpError) as e:
        await r.run("op", "m1", {})
    assert e.value.error_code == "server_busy"


async def test_cancellation_kills_child(tmp_path):
    r = mk(tmp_path, "import time; time.sleep(600)")
    task = asyncio.create_task(r.run("op", "m1", {}))
    await asyncio.sleep(0.5)
    pid = next(iter(r._live))  # capture before cancel
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.3)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)  # child gone


async def test_shutdown_drains_a_still_pending_spawn(tmp_path, monkeypatch):
    """F11: if shutdown() runs while a shielded spawn is still unresolved --
    because the CALLER's own run() was never itself cancelled, shutdown() is
    just racing a genuinely in-flight call -- the spawn's pid isn't in `_live`
    yet and its teardown isn't in `_cleanup_tasks` yet either; nothing would
    find or kill it without `_pending_spawns` tracking. shutdown() must drain
    it (cancelling the tracked spawn task directly, which routes through the
    existing deferred-teardown path) and end with all three tracking
    collections empty, with no exception raised anywhere."""
    release = asyncio.Event()

    class _FakeProc:
        pid = 999999

        async def wait(self):
            return 0

    async def _delayed_create(*args, **kwargs):
        await release.wait()  # never set in this test: stays pending until cancelled
        return _FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _delayed_create)

    r = mk(tmp_path, OK)
    run_task = asyncio.create_task(r.run("op", "m1", {}))

    for _ in range(50):
        await asyncio.sleep(0.01)
        if r._pending_spawns:
            break
    assert r._pending_spawns, "spawn must be tracked while genuinely still pending"

    await r.shutdown()  # must not hang or raise with the spawn still pending

    with pytest.raises(asyncio.CancelledError):
        await run_task

    assert r._live == set()
    assert r._pending_spawns == set()
    assert r._cleanup_tasks == set()


async def test_shutdown_final_pass_kills_a_pid_left_in_live(tmp_path):
    """D2: the drain loop's kill pass over `_live` runs at the TOP of each
    iteration, but the break condition only checks `_pending_spawns`/
    `_cleanup_tasks` -- a spawn resolving cleanly mid-drain (see the
    shutdown() docstring: "or, if a race let it spawn cleanly, into `_live`")
    can add its pid to `_live` AFTER the loop's last kill pass but BEFORE the
    break. shutdown() must therefore do one final kill pass over `_live`
    after the loop exits, so no pid left there -- regardless of when it
    arrived -- survives shutdown()."""
    r = mk(tmp_path, OK)
    # Stand-in for a spawn that resolved into `_live` after the drain loop's
    # own kill passes: a real, live child process whose pid we place directly
    # into `_live`, with nothing else pending so the loop would otherwise
    # break on its very first iteration.
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        start_new_session=True,
    )
    r._live.add(proc.pid)

    await r.shutdown()

    assert r._live == set()
    proc.wait(timeout=5)  # reap the SIGKILL'd child before checking liveness
    with pytest.raises(ProcessLookupError):
        os.kill(proc.pid, 0)  # child gone (not just a zombie)


async def test_shutdown_warns_when_drain_loop_cap_exhausted(
    tmp_path, monkeypatch, caplog
):
    """R3: if the drain loop's ~100-iteration bound is hit without both
    `_pending_spawns` and `_cleanup_tasks` draining, shutdown() must log a
    warning so a stuck teardown is visible instead of exiting silently."""
    import logging

    from google_meridian_mcp_server.execution import (
        sync_subprocess_executor as sse_module,
    )

    r = mk(tmp_path, OK)

    class _NeverDoneTask:
        def done(self):
            return False

        def cancel(self):
            return True

        def __await__(self):
            return iter(())

    # A permanently-pending "task" that is never removed from
    # `_pending_spawns` -- forces every iteration to fail the break check.
    r._pending_spawns.add(_NeverDoneTask())

    async def _fake_gather(*args, **kwargs):
        return []

    monkeypatch.setattr(sse_module.asyncio, "gather", _fake_gather)

    with caplog.at_level(logging.WARNING, logger=sse_module.__name__):
        await r.shutdown()

    assert any("iteration cap" in rec.getMessage() for rec in caplog.records)


def _entries(root):
    return sorted(os.listdir(root)) if os.path.isdir(root) else []


async def test_internal_error_rc1_retains_workdir_and_log(tmp_path):
    """Fable finding 2: an unexpected worker exception prints its traceback to
    the child log and returns a well-formed internal_error payload with rc 1.
    That is a well-formed error payload (raises plain MeridianMcpError, not
    WorkerFailedError), but the worker exit code is non-zero -- an
    infra/internal failure, not a normal domain outcome -- so the workdir
    (and the log holding the traceback) must be RETAINED, not rmtree'd."""
    root = tmp_path / "internal_error"
    with pytest.raises(MeridianMcpError) as exc_info:
        await mk(root, INTERNAL_ERROR_RC1).run("op", "m1", {})
    assert exc_info.value.error_code == "internal_error"

    entries = _entries(root)
    assert len(entries) == 1, "workdir must be retained for postmortem"
    workdir = root / entries[0]
    assert (workdir / "log").exists()
    log_text = (workdir / "log").read_text()
    assert "some traceback text" in log_text


async def test_response_too_large_unlinks_resp_but_keeps_workdir(tmp_path):
    """Fable finding 4: the too-large response is itself the disk-fill risk
    the size ceiling exists to prevent, so resp.json must be unlinked even
    though the workdir (with the log) is retained for postmortem."""
    root = tmp_path / "too_large"
    big = "import sys,os,json; resp=sys.argv[-1]; open(resp,'w').write('{\"ok\":true,\"result\":\"'+ 'x'*20 +'\"}')"
    with pytest.raises(MeridianMcpError) as exc_info:
        await mk(root, big, max_response_bytes=8).run("op", "m1", {})
    assert exc_info.value.error_code == "worker_failed"

    entries = _entries(root)
    assert len(entries) == 1, "workdir must still be retained"
    workdir = root / entries[0]
    assert not (workdir / "resp.json").exists(), "oversized resp.json must be unlinked"
    assert (workdir / "log").exists()


async def test_workdir_retention_semantics(tmp_path):
    # success -> workdir removed
    ok_root = tmp_path / "ok"
    await mk(ok_root, OK).run("op", "m1", {})
    assert _entries(ok_root) == []

    # ordinary domain error ({ok:false,error:...}) -> workdir removed
    err_root = tmp_path / "err"
    with pytest.raises(MeridianMcpError) as e1:
        await mk(err_root, ERR).run("op", "m1", {})
    assert e1.value.error_code == "missing_model_data"
    assert _entries(err_root) == []

    # worker_failed (nonzero exit, no response) -> workdir RETAINED for postmortem
    wf_root = tmp_path / "wf"
    with pytest.raises(MeridianMcpError) as e2:
        await mk(wf_root, "import sys; sys.exit(3)").run("op", "m1", {})
    assert e2.value.error_code == "worker_failed"
    assert len(_entries(wf_root)) == 1

    # worker_failed (unparseable response) -> workdir RETAINED
    up_root = tmp_path / "up"
    bad = "import sys,os; resp=sys.argv[-1]; open(resp,'w').write('{not json')"
    with pytest.raises(MeridianMcpError) as e3:
        await mk(up_root, bad).run("op", "m1", {})
    assert e3.value.error_code == "worker_failed"
    assert len(_entries(up_root)) == 1

    # worker_timeout -> workdir RETAINED
    to_root = tmp_path / "to"
    with pytest.raises(MeridianMcpError) as e4:
        await mk(to_root, "import time; time.sleep(600)", run_timeout=1.0).run(
            "op", "m1", {}
        )
    assert e4.value.error_code == "worker_timeout"
    assert len(_entries(to_root)) == 1


async def test_workdir_setup_failure_translates_to_worker_failed(tmp_path, monkeypatch):
    """F4(a): self._root.mkdir/tempfile.mkdtemp must be INSIDE the wrapped
    try in _run_locked (not before it) so ENOSPC/EMFILE/permission failures
    at workdir setup translate to a clean WorkerFailedError envelope, same as
    a spawn failure -- not escape as a raw OSError past the tool handlers'
    `except MeridianMcpError`."""
    from google_meridian_mcp_server.execution import (
        sync_subprocess_executor as sse_module,
    )

    def _boom(dir=None):
        raise OSError("EMFILE: too many open files")

    monkeypatch.setattr(sse_module.tempfile, "mkdtemp", _boom)

    with pytest.raises(MeridianMcpError) as exc_info:
        await mk(tmp_path, OK).run("op", "m1", {})

    assert exc_info.value.error_code == "worker_failed"
    assert "EMFILE" in str(exc_info.value)


async def test_tail_reads_only_the_end_of_a_large_log(tmp_path):
    """F10a: _tail must never read a multi-GB log fully into memory -- it
    seeks from the end and reads only the last _LOG_TAIL bytes (approx),
    even for a log much larger than that."""
    from google_meridian_mcp_server.execution.sync_subprocess_executor import (
        _LOG_TAIL,
        SyncSubprocessExecutor,
    )

    logp = tmp_path / "big.log"
    content = ("x" * 100 + "\n") * (
        _LOG_TAIL // 10
    )  # several times larger than the tail
    logp.write_text(content)
    assert logp.stat().st_size > _LOG_TAIL * 5  # sanity: genuinely large

    tail = SyncSubprocessExecutor._tail(str(logp))

    assert len(tail.encode("utf-8", "replace")) <= _LOG_TAIL
    assert tail == content[-_LOG_TAIL:]


async def test_tail_handles_file_smaller_than_tail_limit(tmp_path):
    """F10a regression: a log smaller than _LOG_TAIL must return in full, not
    raise (seek(-_LOG_TAIL, SEEK_END) on a small file must be guarded)."""
    from google_meridian_mcp_server.execution.sync_subprocess_executor import (
        SyncSubprocessExecutor,
    )

    logp = tmp_path / "small.log"
    logp.write_text("hello world")

    assert SyncSubprocessExecutor._tail(str(logp)) == "hello world"


def test_sweep_stale_entries_removes_old_keeps_new(tmp_path):
    """F10b: retained analysis workdirs and worker log files otherwise
    accumulate forever. sweep_stale_entries removes only entries directly
    under root whose mtime is older than the TTL, leaving fresh ones alone."""
    import os

    from google_meridian_mcp_server.execution.sync_subprocess_executor import (
        sweep_stale_entries,
    )

    old_dir = tmp_path / "old_workdir"
    old_dir.mkdir()
    (old_dir / "req.json").write_text("{}")
    old_file = tmp_path / "old-run.log"
    old_file.write_text("stale log")

    new_dir = tmp_path / "new_workdir"
    new_dir.mkdir()
    new_file = tmp_path / "new-run.log"
    new_file.write_text("fresh log")

    old_ts = time.time() - 1_000_000  # far older than any sane TTL
    for p in (old_dir, old_file):
        os.utime(p, (old_ts, old_ts))

    sweep_stale_entries(tmp_path, ttl_seconds=3600)

    remaining = {p.name for p in tmp_path.iterdir()}
    assert remaining == {"new_workdir", "new-run.log"}


def test_sweep_stale_entries_missing_root_is_a_noop(tmp_path):
    """F10b: a not-yet-created root (e.g. no runs yet) must not raise."""
    from google_meridian_mcp_server.execution.sync_subprocess_executor import (
        sweep_stale_entries,
    )

    sweep_stale_entries(tmp_path / "does-not-exist", ttl_seconds=3600)


def test_sweep_stale_entries_ignores_per_entry_errors(tmp_path, monkeypatch):
    """F10b: one bad entry (permission error, concurrent deletion, ...) must
    not abort the sweep of the rest."""
    from google_meridian_mcp_server.execution import (
        sync_subprocess_executor as sse_module,
    )

    old_dir = tmp_path / "boom"
    old_dir.mkdir()
    old_ts = time.time() - 1_000_000
    os.utime(old_dir, (old_ts, old_ts))

    good_file = tmp_path / "old.log"
    good_file.write_text("x")
    os.utime(good_file, (old_ts, old_ts))

    real_rmtree = sse_module.shutil.rmtree

    def _boom(path, ignore_errors=False):
        if str(path) == str(old_dir):
            raise OSError("permission denied")
        return real_rmtree(path, ignore_errors=ignore_errors)

    monkeypatch.setattr(sse_module.shutil, "rmtree", _boom)

    sse_module.sweep_stale_entries(tmp_path, ttl_seconds=3600)  # must not raise

    assert old_dir.exists()  # the "bad" entry survives
    assert not good_file.exists()  # the good entry is still swept

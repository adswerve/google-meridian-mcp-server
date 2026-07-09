import asyncio
import os
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
ERR = OK.replace("{'ok':True,'result':{'echo':d}}",
                 "{'ok':False,'error':{'error_code':'missing_model_data','message':'no','details':{}}}")

def mk(tmp, script, **o):
    kw = dict(semaphore=asyncio.Semaphore(2), run_timeout=10.0, queue_wait_timeout=5.0,
              max_response_bytes=10_000_000, workdir_root=str(tmp),
              worker_argv_prefix=[sys.executable, "-c", script])
    kw.update(o)
    return SyncSubprocessExecutor(**kw)

async def test_ok(tmp_path):
    out = await mk(tmp_path, OK).run("get_contribution", "m1", {"x": 1})
    assert out == {"echo": {"operation": "get_contribution", "model_id": "m1", "params": {"x": 1}}}

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
        await mk(tmp_path, "import time; time.sleep(600)", run_timeout=1.0).run("op", "m1", {})
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
    pid = next(iter(r._live))            # capture before cancel
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.3)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)                  # child gone


def _entries(root):
    return sorted(os.listdir(root)) if os.path.isdir(root) else []

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
        await mk(to_root, "import time; time.sleep(600)", run_timeout=1.0).run("op", "m1", {})
    assert e4.value.error_code == "worker_timeout"
    assert len(_entries(to_root)) == 1

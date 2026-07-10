"""Non-vacuous guard: booting the server + driving real tool calls must never
import meridian/TF/JAX (or the project's own meridian subpackage) into the
server process. A meta_path finder raises if any banned module is imported
while the lifespan boots and tools are dispatched.

This complements the static ruff TID251 banned-api rule (pyproject.toml):
TID251 catches banned imports anywhere in a server-path file at lint time
(including the exact dotted string "google_meridian_mcp_server.meridian");
this test catches anything that would actually execute in the running
server process, in a fresh interpreter with no modules pre-imported.

Two tool calls are driven (Fable-reviewed, see task-12-report.md):
  - list_models: boots the lifespan and the discovery-only dispatch path.
  - get_channel_data (with the subprocess runner stubbed out): forces
    call-time execution of the routing layer, AnalysisService, and result
    caching -- code that only runs at dispatch time and that list_models
    alone never touches. The runner is stubbed so this stays a fast,
    fixture-free unit-style check of the SERVER side only; the real worker
    subprocess (a separate interpreter, invisible to this meta_path finder)
    is exercised by tests/contract/test_analysis_tools.py and friends.

The runtime blocker bans both top-level module names (name.split(".")[0])
and, explicitly, the google_meridian_mcp_server.meridian subpackage prefix
-- a bare top-level check would miss it, since its own top-level name
(google_meridian_mcp_server) is not itself banned.
"""

import subprocess
import sys
import textwrap

SCRIPT = textwrap.dedent("""
    import sys, asyncio, os, tempfile
    BANNED_TOP = {"meridian","tensorflow","tensorflow_probability","jax","keras","tf_keras"}
    BANNED_PREFIX = "google_meridian_mcp_server.meridian"
    class B:
        def find_spec(self, name, path=None, target=None):
            top = name.split(".")[0]
            if top in BANNED_TOP or name == BANNED_PREFIX or name.startswith(BANNED_PREFIX + "."):
                raise AssertionError("server imported banned module: " + name)
            return None
    sys.meta_path.insert(0, B())
    os.environ["PERSISTENCE_BACKEND"]="local"
    os.environ["LOCAL_MODELS_ROOT"]=tempfile.mkdtemp()   # empty → list_models returns []
    from fastmcp import Client
    from google_meridian_mcp_server.server import mcp
    from google_meridian_mcp_server.execution.sync_subprocess_executor import (
        SyncSubprocessExecutor,
    )

    # Stub the subprocess runner so the analysis dispatch path (routing,
    # AnalysisService, result cache) runs for real on the server side,
    # without spawning the real worker subprocess or needing a fitted model.
    async def _stub_run(self, operation, model_id, params):
        return {"model_id": model_id, "columns": [], "rows": [], "row_count": 0}
    SyncSubprocessExecutor.run = _stub_run

    async def main():
        async with Client(mcp) as c:
            await c.call_tool("list_models", {})                      # lifespan + discovery dispatch
            await c.call_tool("get_channel_data", {"model_id": "x"})  # analysis dispatch, runner stubbed
    asyncio.run(main())
    print("OK")
""")


def test_server_never_imports_meridian_through_a_tool_call():
    res = subprocess.run([sys.executable, "-c", SCRIPT], capture_output=True, text=True)
    assert res.returncode == 0 and "OK" in res.stdout, res.stderr

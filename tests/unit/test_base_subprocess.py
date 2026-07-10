import os
import subprocess
import sys

from google_meridian_mcp_server.execution.base_subprocess import BaseSubprocessExecutor


def test_worker_argv_default_prefix():
    argv = BaseSubprocessExecutor().worker_argv("analysis", "req", "resp")
    assert argv == [
        sys.executable,
        "-m",
        BaseSubprocessExecutor.WORKER_MODULE,
        "analysis",
        "req",
        "resp",
    ]


def test_child_env_backend_and_hygiene_respect_operator(monkeypatch):
    monkeypatch.setenv("TF_NUM_INTRAOP_THREADS", "9")  # operator-set → preserved
    env = BaseSubprocessExecutor(env_base={"MERIDIAN_BACKEND": "jax"}).child_env(
        {"FOO": "bar"}
    )
    assert env["MERIDIAN_BACKEND"] == "jax"
    assert env["TF_CPP_MIN_LOG_LEVEL"] == "3"  # hygiene default applied
    assert env["TF_NUM_INTRAOP_THREADS"] == "9"  # operator value NOT clobbered
    assert env["FOO"] == "bar" and env["PATH"] == os.environ["PATH"]


def test_child_env_includes_parent_pid_for_orphan_guard():
    """F3: child_env() must inject MERIDIAN_PARENT_PID = str(os.getpid())
    (the PARENT's own pid, captured here at spawn time -- BEFORE the child's
    fork+exec and therefore before its import-chain TOCTOU window) so the
    worker's guard (execution/worker.py) can detect a parent death during
    that window instead of self-capturing getppid() only after it."""
    env = BaseSubprocessExecutor().child_env()
    assert env["MERIDIAN_PARENT_PID"] == str(os.getpid())


def test_popen_redirect_never_inherits_stdout(tmp_path):
    with open(tmp_path / "log", "w") as log:
        kw = BaseSubprocessExecutor().popen_redirect_kwargs(log)
    assert (
        kw["stdout"] is log and kw["stderr"] is log and kw["start_new_session"] is True
    )


def test_kill_group_reaps_session_leader():
    p = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True
    )
    BaseSubprocessExecutor.kill_group(p.pid)
    p.wait(timeout=5)
    assert p.returncode is not None

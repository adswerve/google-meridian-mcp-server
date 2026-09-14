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


def test_tf_cpp_log_level_overrides_a_library_polluted_value(monkeypatch):
    """jax/__init__.py line 17 runs os.environ.setdefault('TF_CPP_MIN_LOG_LEVEL',
    '1') at import time, and jax is a CORE dependency of Meridian 2.0. pytest
    collects tests/contract and tests/integration before tests/unit, so by the
    time this module runs the parent environment may already carry '1'. That
    value is a library's, not an operator's, so child_env must override it."""
    monkeypatch.setenv("TF_CPP_MIN_LOG_LEVEL", "1")
    env = BaseSubprocessExecutor().child_env()
    assert env["TF_CPP_MIN_LOG_LEVEL"] == "3"


def test_explicit_executor_configuration_still_beats_the_override(monkeypatch):
    """Hygiene applies BEFORE env_base and extra, so a deliberate executor
    setting still wins."""
    monkeypatch.setenv("TF_CPP_MIN_LOG_LEVEL", "1")
    executor = BaseSubprocessExecutor(env_base={"TF_CPP_MIN_LOG_LEVEL": "0"})
    assert executor.child_env()["TF_CPP_MIN_LOG_LEVEL"] == "0"
    assert (
        executor.child_env({"TF_CPP_MIN_LOG_LEVEL": "2"})["TF_CPP_MIN_LOG_LEVEL"] == "2"
    )


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


def test_child_env_forces_jax_and_x64_regardless_of_ambient_environment(monkeypatch):
    """D2/D3: JAX everywhere, x64 explicitly on. Relying on an upstream default
    that has already flipped once (TensorFlow -> JAX in Meridian 2.0) would be
    careless, and MERIDIAN_ENABLE_JAX_X64 must be pinned rather than inherited
    from a library default."""
    from google_meridian_mcp_server.execution.base_subprocess import MERIDIAN_BACKEND

    monkeypatch.setenv("MERIDIAN_BACKEND", "tensorflow")
    monkeypatch.delenv("MERIDIAN_ENABLE_JAX_X64", raising=False)
    env = BaseSubprocessExecutor().child_env()
    assert MERIDIAN_BACKEND == "jax"
    assert env["MERIDIAN_BACKEND"] == "jax"
    assert env["MERIDIAN_ENABLE_JAX_X64"] == "true"

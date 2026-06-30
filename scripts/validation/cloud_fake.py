"""In-process fake of Cloud Run jobs.run that launches the real worker locally.

Exercises the CloudRunJobExecutor launch contract end-to-end (env overrides,
RUN_ID, worker, registry writes, heartbeat, reconcile, cancel) with ONLY the
GCP RPC (jobs.run / executions.get / executions.cancel) stubbed. The worker
runs as a real local subprocess against a local-dir registry.
"""

from __future__ import annotations

import os
import subprocess
import sys
from types import SimpleNamespace


class FakeJobsClient:
    """Stands in for run_v2.JobsClient: run_job launches the real worker locally."""

    def __init__(self, *, base_env: dict):
        self._base_env = base_env
        self.procs: dict[str, subprocess.Popen] = {}
        self._by_exec: dict[str, subprocess.Popen] = {}

    def run_job(self, request):
        overrides = request.overrides.container_overrides[0]
        env = dict(os.environ)
        env.update(self._base_env)
        for var in overrides.env:
            env[var.name] = var.value
        run_id = env["OPTIMIZATION_RUN_ID"]
        proc = subprocess.Popen(
            [sys.executable, "-m", "google_meridian_mcp_server.execution.worker"],
            env=env,
        )
        self.procs[run_id] = proc
        name = f"exec-{run_id}"
        self._by_exec[name] = proc
        return SimpleNamespace(metadata=SimpleNamespace(name=name))


class FakeExecutionsClient:
    """Stands in for run_v2.ExecutionsClient: liveness/cancel by polling the proc."""

    def __init__(self, jobs: FakeJobsClient):
        self._jobs = jobs

    def get_execution(self, *, name):
        proc = self._jobs._by_exec.get(name)
        alive = proc is not None and proc.poll() is None
        return SimpleNamespace(completion_time=None if alive else "done")

    def cancel_execution(self, *, name):
        proc = self._jobs._by_exec.get(name)
        if proc and proc.poll() is None:
            proc.terminate()

"""Durable registry for optimization runs (interface + local provider)."""

from __future__ import annotations

import abc
import json
import os
import tempfile
from pathlib import Path

from google_meridian_mcp_server.domain.errors import MeridianMcpError
from google_meridian_mcp_server.domain.optimization import (
    OptimizationRun,
    OptimizationRunState,
    OptimizationRunSummary,
    RunStatus,
)


class RunNotFoundError(MeridianMcpError):
    def __init__(self, run_id: str):
        super().__init__(
            error_code="optimization_run_not_found",
            message=f"Optimization run '{run_id}' was not found.",
            details={"run_id": run_id},
        )


class ResultNotReadyError(MeridianMcpError):
    def __init__(self, run_id: str, status: str):
        super().__init__(
            error_code="optimization_not_ready",
            message=f"Optimization run '{run_id}' has no result yet (status={status}).",
            details={"run_id": run_id, "status": status},
        )


def build_config_summary(run: OptimizationRun) -> str:
    cfg = run.config
    scenario = cfg.scenario.type
    dates = f"{cfg.start_date or 'start'}..{cfg.end_date or 'end'}"
    geos = "all geos" if not cfg.selected_geos else f"{len(cfg.selected_geos)} geos"
    objective = "KPI" if cfg.use_kpi else "ROAS"
    constraint = (
        f"+/-{int(cfg.constraint.pct * 100)}%"
        if cfg.constraint.mode == "global"
        else "per-channel"
    )
    return f"{scenario} . {dates} . {geos} . {objective} . {constraint}"


class OptimizationRunRegistry(abc.ABC):
    @abc.abstractmethod
    def create(self, run: OptimizationRun) -> None: ...
    @abc.abstractmethod
    def write_state(
        self, state: OptimizationRunState, *, expected_generation: int | None = None
    ) -> None: ...
    @abc.abstractmethod
    def get_state_generation(self, run_id: str) -> int | None: ...
    @abc.abstractmethod
    def write_result(self, run_id: str, result: dict) -> None: ...
    @abc.abstractmethod
    def get_record(self, run_id: str) -> OptimizationRun: ...
    @abc.abstractmethod
    def get_state(self, run_id: str) -> OptimizationRunState: ...
    @abc.abstractmethod
    def get_result(self, run_id: str) -> dict: ...
    @abc.abstractmethod
    def list(
        self,
        *,
        model_id: str | None = None,
        status: RunStatus | None = None,
        limit: int | None = None,
    ) -> list[OptimizationRunSummary]: ...
    @abc.abstractmethod
    def delete(self, run_id: str) -> None: ...
    @abc.abstractmethod
    def find_by_fingerprint(self, fingerprint: str) -> str | None: ...
    @abc.abstractmethod
    def put_fingerprint(self, fingerprint: str, run_id: str) -> None: ...


def _atomic_write(path: Path, text: str) -> None:
    """Write *text* to *path* atomically via a same-directory temp file + os.replace."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    fd_closed = False
    try:
        os.write(fd, text.encode())
        os.fsync(fd)
        os.close(fd)
        fd_closed = True
        os.replace(tmp, path)
    except Exception:
        if not fd_closed:
            os.close(fd)
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class LocalOptimizationRunRegistry(OptimizationRunRegistry):
    def __init__(self, root: str) -> None:
        self._root = Path(root)
        self._runs = self._root / "runs"
        self._index = self._root / "index" / "by_fingerprint"

    def _run_dir(self, run_id: str) -> Path:
        return self._runs / run_id

    def create(self, run: OptimizationRun) -> None:
        d = self._run_dir(run.run_id)
        d.mkdir(parents=True, exist_ok=True)
        _atomic_write(d / "record.json", run.model_dump_json(indent=2))

    def write_state(
        self, state: OptimizationRunState, *, expected_generation=None
    ) -> None:
        d = self._run_dir(state.run_id)
        if not d.is_dir():
            raise RunNotFoundError(state.run_id)
        _atomic_write(d / "state.json", state.model_dump_json(indent=2))

    def get_state_generation(self, run_id: str) -> int | None:
        return None  # local fs has no generation/precondition concept

    def write_result(self, run_id: str, result: dict) -> None:
        d = self._run_dir(run_id)
        if not d.is_dir():
            raise RunNotFoundError(run_id)
        _atomic_write(d / "result.json", json.dumps(result, indent=2))

    def get_record(self, run_id: str) -> OptimizationRun:
        path = self._run_dir(run_id) / "record.json"
        if not path.is_file():
            raise RunNotFoundError(run_id)
        return OptimizationRun.model_validate_json(path.read_text())

    def get_state(self, run_id: str) -> OptimizationRunState:
        path = self._run_dir(run_id) / "state.json"
        if not path.is_file():
            if not self._run_dir(run_id).is_dir():
                raise RunNotFoundError(run_id)
            return OptimizationRunState(run_id=run_id, status=RunStatus.QUEUED)
        return OptimizationRunState.model_validate_json(path.read_text())

    def get_result(self, run_id: str) -> dict:
        state = self.get_state(run_id)
        path = self._run_dir(run_id) / "result.json"
        if not path.is_file():
            raise ResultNotReadyError(run_id, state.status.value)
        return json.loads(path.read_text())

    def list(self, *, model_id=None, status=None, limit=None):
        if not self._runs.is_dir():
            return []
        summaries: list[OptimizationRunSummary] = []
        for d in sorted(self._runs.iterdir()):
            record_path = d / "record.json"
            if not record_path.is_file():
                continue
            run = OptimizationRun.model_validate_json(record_path.read_text())
            if model_id is not None and run.model_id != model_id:
                continue
            state = self.get_state(run.run_id)
            if status is not None and state.status != status:
                continue
            summaries.append(
                OptimizationRunSummary(
                    run_id=run.run_id,
                    label=run.label,
                    model_id=run.model_id,
                    config_summary=build_config_summary(run),
                    status=state.status,
                    created_at=run.created_at,
                    finished_at=state.finished_at,
                    headline=state.headline,
                )
            )
        summaries.sort(key=lambda s: s.created_at, reverse=True)
        return summaries[:limit] if limit else summaries

    def delete(self, run_id: str) -> None:
        d = self._run_dir(run_id)
        if not d.is_dir():
            raise RunNotFoundError(run_id)
        record_path = d / "record.json"
        if record_path.is_file():
            fp = OptimizationRun.model_validate_json(
                record_path.read_text()
            ).config_fingerprint
            pointer = self._index / fp
            if pointer.is_file() and pointer.read_text().strip() == run_id:
                pointer.unlink()
        for child in d.iterdir():
            child.unlink()
        d.rmdir()

    def find_by_fingerprint(self, fingerprint: str) -> str | None:
        pointer = self._index / fingerprint
        return pointer.read_text().strip() if pointer.is_file() else None

    def put_fingerprint(self, fingerprint: str, run_id: str) -> None:
        self._index.mkdir(parents=True, exist_ok=True)
        _atomic_write(self._index / fingerprint, run_id)


class GcsOptimizationRunRegistry(OptimizationRunRegistry):
    def __init__(self, bucket: str, prefix: str, *, client_factory=None) -> None:
        self._bucket_name = bucket
        self._prefix = prefix.rstrip("/")
        self._client_factory = client_factory or self._default_client
        self.client = self._client_factory()

    @staticmethod
    def _default_client():
        from google.cloud import storage  # lazy import

        return storage.Client()

    def _bucket(self):
        return self.client.bucket(self._bucket_name)

    def _run_prefix(self, run_id: str) -> str:
        return f"{self._prefix}/runs/{run_id}"

    def _blob(self, path: str):
        return self._bucket().blob(path)

    def create(self, run: OptimizationRun) -> None:
        self._blob(f"{self._run_prefix(run.run_id)}/record.json").upload_from_string(
            run.model_dump_json(indent=2)
        )

    def write_state(
        self, state: OptimizationRunState, *, expected_generation=None
    ) -> None:
        blob = self._blob(f"{self._run_prefix(state.run_id)}/state.json")
        kwargs = {}
        if expected_generation is not None:
            kwargs["if_generation_match"] = expected_generation
        blob.upload_from_string(state.model_dump_json(indent=2), **kwargs)

    def get_state_generation(self, run_id: str) -> int | None:
        blob = self._blob(f"{self._run_prefix(run_id)}/state.json")
        return blob.generation if blob.exists() else None

    def write_result(self, run_id: str, result: dict) -> None:
        self._blob(f"{self._run_prefix(run_id)}/result.json").upload_from_string(
            json.dumps(result, indent=2)
        )

    def get_record(self, run_id: str) -> OptimizationRun:
        blob = self._blob(f"{self._run_prefix(run_id)}/record.json")
        if not blob.exists():
            raise RunNotFoundError(run_id)
        return OptimizationRun.model_validate_json(blob.download_as_text())

    def get_state(self, run_id: str) -> OptimizationRunState:
        blob = self._blob(f"{self._run_prefix(run_id)}/state.json")
        if not blob.exists():
            if not self._blob(f"{self._run_prefix(run_id)}/record.json").exists():
                raise RunNotFoundError(run_id)
            return OptimizationRunState(run_id=run_id, status=RunStatus.QUEUED)
        return OptimizationRunState.model_validate_json(blob.download_as_text())

    def get_result(self, run_id: str) -> dict:
        state = self.get_state(run_id)
        blob = self._blob(f"{self._run_prefix(run_id)}/result.json")
        if not blob.exists():
            raise ResultNotReadyError(run_id, state.status.value)
        return json.loads(blob.download_as_text())

    def list(self, *, model_id=None, status=None, limit=None):
        prefix = f"{self._prefix}/runs/"
        record_blobs = [
            b
            for b in self._bucket().list_blobs(prefix=prefix)
            if b.name.endswith("/record.json")
        ]
        summaries: list[OptimizationRunSummary] = []
        for b in record_blobs:
            run = OptimizationRun.model_validate_json(b.download_as_text())
            if model_id is not None and run.model_id != model_id:
                continue
            state = self.get_state(run.run_id)
            if status is not None and state.status != status:
                continue
            summaries.append(
                OptimizationRunSummary(
                    run_id=run.run_id,
                    label=run.label,
                    model_id=run.model_id,
                    config_summary=build_config_summary(run),
                    status=state.status,
                    created_at=run.created_at,
                    finished_at=state.finished_at,
                    headline=state.headline,
                )
            )
        summaries.sort(key=lambda s: s.created_at, reverse=True)
        return summaries[:limit] if limit else summaries

    def delete(self, run_id: str) -> None:
        prefix = self._run_prefix(run_id)
        record = self._blob(f"{prefix}/record.json")
        if not record.exists():
            raise RunNotFoundError(run_id)
        fp = OptimizationRun.model_validate_json(
            record.download_as_text()
        ).config_fingerprint
        pointer = self._blob(f"{self._prefix}/index/by_fingerprint/{fp}")
        if pointer.exists() and pointer.download_as_text().strip() == run_id:
            pointer.delete()
        for name in (
            f"{prefix}/record.json",
            f"{prefix}/state.json",
            f"{prefix}/result.json",
        ):
            blob = self._blob(name)
            if blob.exists():
                blob.delete()

    def find_by_fingerprint(self, fingerprint: str) -> str | None:
        blob = self._blob(f"{self._prefix}/index/by_fingerprint/{fingerprint}")
        return blob.download_as_text().strip() if blob.exists() else None

    def put_fingerprint(self, fingerprint: str, run_id: str) -> None:
        self._blob(
            f"{self._prefix}/index/by_fingerprint/{fingerprint}"
        ).upload_from_string(run_id)

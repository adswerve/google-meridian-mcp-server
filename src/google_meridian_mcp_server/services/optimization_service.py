"""Service orchestrating optimization submission, reuse, and registry reads."""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Any

from google_meridian_mcp_server.domain.errors import MeridianMcpError
from google_meridian_mcp_server.domain.models import RuntimeConfig
from google_meridian_mcp_server.domain.optimization import (
    OptimizationConfig,
    OptimizationRun,
    RunStatus,
    config_fingerprint,
    to_optimize_kwargs,
)
from google_meridian_mcp_server.execution.routing import (
    model_size_features,
    resolve_tier,
    size_score,
)
from google_meridian_mcp_server.persistence.optimization_run_registry import (
    OptimizationRunRegistry,
)

_MERIDIAN_VERSION = "1.7.0"
_SERVER_VERSION = "0.1.0"


class InvalidOptimizationConfigError(MeridianMcpError):
    def __init__(self, reason: str):
        super().__init__(
            error_code="invalid_optimization_config",
            message=f"Invalid optimization config: {reason}",
        )


def _slug(model_id: str) -> str:
    return model_id.replace("/", "-")


def _default_label(model_id: str, config: OptimizationConfig) -> str:
    return f"{_slug(model_id)} {config.scenario.type}"


class OptimizationService:
    def __init__(
        self,
        catalog: Any,
        registry: OptimizationRunRegistry,
        executor: Any,
        cfg: RuntimeConfig,
    ) -> None:
        self._catalog = catalog
        self._registry = registry
        self._executor = executor
        self._cfg = cfg

    def run_optimization(
        self,
        model_id: str,
        config_dict: dict,
        *,
        label: str | None = None,
        note: str | None = None,
        compute_tier: str = "auto",
        force_rerun: bool = False,
    ) -> dict[str, Any]:
        facade = self._catalog.get_optimizer_facade(
            model_id
        )  # raises ModelNotFoundError
        try:
            config = OptimizationConfig.model_validate(config_dict)
        except Exception as exc:  # pydantic ValidationError
            raise InvalidOptimizationConfigError(str(exc)) from exc

        use_kpi = facade.resolve_use_kpi(config)
        try:
            to_optimize_kwargs(
                config, channel_order=facade.channel_order(), use_kpi=use_kpi
            )
        except ValueError as exc:
            raise InvalidOptimizationConfigError(str(exc)) from exc

        fingerprint = config_fingerprint(model_id, config)
        if not force_rerun:
            existing_id = self._registry.find_by_fingerprint(fingerprint)
            if existing_id is not None:
                state = self._registry.get_state(existing_id)
                if state.status in (
                    RunStatus.COMPLETED,
                    RunStatus.RUNNING,
                    RunStatus.QUEUED,
                ):
                    record = self._registry.get_record(existing_id)
                    return self._submit_envelope(
                        record, reused=True, status=state.status.value
                    )

        features = model_size_features(facade)
        score = size_score(features)
        try:
            resolved = resolve_tier(
                score,
                requested=compute_tier,
                allowed=self._cfg.optimization_allowed_tiers,
                thresholds=self._cfg.optimization_size_thresholds,
            )
        except ValueError as exc:
            raise InvalidOptimizationConfigError(str(exc)) from exc
        backend = self._cfg.optimization_backend_local  # local-only this phase

        run_id = f"{_slug(model_id)}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}-{secrets.token_hex(3)}"
        record = OptimizationRun(
            run_id=run_id,
            label=label or _default_label(model_id, config),
            note=note,
            model_id=model_id,
            config=config,
            config_fingerprint=fingerprint,
            compute_tier_requested=compute_tier,
            compute_tier_resolved=resolved,
            backend=backend,
            size_score=score,
            created_at=datetime.now(timezone.utc).isoformat(),
            meridian_version=_MERIDIAN_VERSION,
            server_version=_SERVER_VERSION,
        )
        self._registry.create(record)
        self._registry.put_fingerprint(fingerprint, run_id)
        self._executor.submit(record)
        return self._submit_envelope(
            record, reused=False, status=RunStatus.QUEUED.value
        )

    @staticmethod
    def _submit_envelope(
        record: OptimizationRun, *, reused: bool, status: str
    ) -> dict[str, Any]:
        return {
            "run_id": record.run_id,
            "status": status,
            "compute_tier_resolved": record.compute_tier_resolved,
            "backend": record.backend,
            "size_score": record.size_score,
            "reused": reused,
        }

    def get_status(self, run_id: str) -> dict[str, Any]:
        self._executor.pump()
        record = self._registry.get_record(run_id)
        state = self._registry.get_state(run_id)
        elapsed = None
        if state.started_at:
            end = state.finished_at or datetime.now(timezone.utc).isoformat()
            elapsed = (
                datetime.fromisoformat(end) - datetime.fromisoformat(state.started_at)
            ).total_seconds()
        return {
            "run_id": run_id,
            "status": state.status.value,
            "phase": state.phase.value if state.phase else None,
            "progress_fraction": state.progress_fraction,
            "heartbeat_at": state.heartbeat_at,
            "started_at": state.started_at,
            "finished_at": state.finished_at,
            "elapsed_seconds": elapsed,
            "compute_tier": record.compute_tier_resolved,
            "backend": record.backend,
            "error": state.error,
        }

    def get_result(self, run_id: str) -> dict[str, Any]:
        result = self._registry.get_result(run_id)  # raises ResultNotReadyError
        return {"run_id": run_id, **result}

    def list_runs(self, model_id=None, status=None, limit=None) -> dict[str, Any]:
        try:
            status_enum = RunStatus(status) if status else None
        except ValueError as exc:
            raise InvalidOptimizationConfigError(str(exc)) from exc
        summaries = self._registry.list(
            model_id=model_id, status=status_enum, limit=limit
        )
        return {
            "runs": [s.model_dump(mode="json") for s in summaries],
            "count": len(summaries),
        }

    def cancel(self, run_id: str) -> dict[str, Any]:
        self._registry.get_record(run_id)  # raises RunNotFoundError if unknown
        self._executor.cancel(run_id)
        return {"run_id": run_id, "status": RunStatus.CANCELED.value}

    def delete(self, run_id: str) -> dict[str, Any]:
        self._registry.delete(run_id)
        return {"run_id": run_id, "deleted": True}

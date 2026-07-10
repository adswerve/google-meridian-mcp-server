"""Service orchestrating optimization submission, reuse, and registry reads.

Task 11: every model-derived value (channel_order, use_kpi, size_features,
config validation) now comes from the worker-side ``preflight_optimization``
op (Task 7) via ``runner.run(...)``. This service never imports or touches
Meridian directly -- the SERVER process is Meridian-free.
"""

from __future__ import annotations

import asyncio
import secrets
from datetime import datetime, timezone
from typing import Any

from google_meridian_mcp_server.domain.errors import MeridianMcpError
from google_meridian_mcp_server.domain.models import RuntimeConfig
from google_meridian_mcp_server.domain.optimization import (
    BaseOptimizationConfig,
    FutureOptimizationConfig,
    OptimizationConfig,
    OptimizationRun,
    RunStatus,
    config_fingerprint,
)
from google_meridian_mcp_server.execution.routing import resolve_tier, size_score
from google_meridian_mcp_server.persistence.cache import ResultCache
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


def _default_label(model_id: str, config: BaseOptimizationConfig) -> str:
    return f"{_slug(model_id)} {config.scenario.type}"


def _raise_from_validation_error(payload: dict[str, Any]) -> None:
    """Reconstruct a typed error from preflight's ``validation_error`` payload.

    The worker-side op only ever populates this with error_code
    "invalid_optimization_config" today (validate_future/to_optimize_kwargs
    raise plain ValueError, pydantic raises ValidationError -- both land in
    the generic except branch of ``_preflight_optimization``). Branch on the
    error_code defensively rather than hardcoding the subclass: if a future
    change makes the worker surface a *different* typed MeridianMcpError here
    (caught via its own `except MeridianMcpError` branch, whose payload
    message may already carry its own prefix), reconstructing it as
    InvalidOptimizationConfigError would double-prefix the message.
    """
    if payload.get("error_code") == "invalid_optimization_config":
        raise InvalidOptimizationConfigError(payload["message"])
    raise MeridianMcpError.from_payload(payload)


class OptimizationService:
    def __init__(
        self,
        runner: Any,
        registry: OptimizationRunRegistry,
        executor: Any,
        cfg: RuntimeConfig,
        result_cache: ResultCache | None = None,
    ) -> None:
        self._runner = runner
        self._registry = registry
        self._executor = executor
        self._cfg = cfg
        # Preflight results are config-dependent (use_kpi/validation_error vary
        # per config, not just per model_id), so this MUST be keyed on the
        # config fingerprint -- never on bare model_id. See Task 11 brief.
        #
        # A fresh OptimizationService is constructed per tool call (see
        # transport/tools.py:_optimization_service), so a bespoke
        # instance-local dict here would never survive past a single call --
        # it would cache nothing, ever. Cache in the lifespan-scoped
        # ResultCache instead, which outlives individual tool calls.
        self._result_cache = result_cache

    async def _preflight(
        self, model_id: str, config_dict: dict, fingerprint: str
    ) -> dict[str, Any]:
        cache_params = {"fingerprint": fingerprint}
        if self._result_cache is not None:
            cached = self._result_cache.get(
                "preflight_optimization", model_id, cache_params
            )
            if cached is not None:
                return cached
        result = await self._runner.run(
            "preflight_optimization", model_id, {"config": config_dict}
        )
        if self._result_cache is not None:
            self._result_cache.put(
                "preflight_optimization", model_id, cache_params, result
            )
        return result

    async def run_optimization(
        self,
        model_id: str,
        config_dict: dict,
        *,
        label: str | None = None,
        note: str | None = None,
        compute_tier: str = "auto",
        force_rerun: bool = False,
    ) -> dict[str, Any]:
        try:
            config = OptimizationConfig.model_validate(config_dict)
        except Exception as exc:  # pydantic ValidationError
            raise InvalidOptimizationConfigError(str(exc)) from exc

        fingerprint = config_fingerprint(model_id, config)
        preflight = await self._preflight(
            model_id, config.model_dump(mode="json"), fingerprint
        )
        if preflight["validation_error"]:
            _raise_from_validation_error(preflight["validation_error"])

        # D1: _submit is sync and acquires the executor's RLock (registry I/O
        # + launch); get_status/cancel/etc. are offloaded via to_thread and can
        # hold that same lock across GCS RPCs / _terminate's handle.wait(5) --
        # a concurrent submit running on the event-loop thread would block the
        # WHOLE loop waiting for it. Offload the sync tail to a worker thread
        # too, so it can block on the lock without freezing the loop.
        return await asyncio.to_thread(
            self._submit,
            model_id,
            config,
            fingerprint=fingerprint,
            size_features=preflight["size_features"],
            label=label,
            note=note,
            compute_tier=compute_tier,
            force_rerun=force_rerun,
        )

    async def run_future_optimization(
        self,
        model_id: str,
        config_dict: dict,
        *,
        label: str | None = None,
        note: str | None = None,
        compute_tier: str = "auto",
        force_rerun: bool = False,
    ) -> dict[str, Any]:
        try:
            config = FutureOptimizationConfig.model_validate(config_dict)
        except Exception as exc:  # pydantic ValidationError
            raise InvalidOptimizationConfigError(str(exc)) from exc

        fingerprint = config_fingerprint(model_id, config)
        preflight = await self._preflight(
            model_id, config.model_dump(mode="json"), fingerprint
        )
        if preflight["validation_error"]:
            _raise_from_validation_error(preflight["validation_error"])

        # D1: see the matching comment in run_optimization -- offload the sync
        # submit tail so it can't block the event loop on the executor lock.
        return await asyncio.to_thread(
            self._submit,
            model_id,
            config,
            fingerprint=fingerprint,
            size_features=preflight["size_features"],
            label=label,
            note=note,
            compute_tier=compute_tier,
            force_rerun=force_rerun,
        )

    def _submit(
        self,
        model_id: str,
        config: BaseOptimizationConfig,
        *,
        fingerprint: str,
        size_features: dict[str, int],
        label: str | None,
        note: str | None,
        compute_tier: str,
        force_rerun: bool,
    ) -> dict[str, Any]:
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

        score = size_score(size_features)
        try:
            resolved = resolve_tier(
                score,
                requested=compute_tier,
                allowed=self._cfg.optimization_allowed_tiers,
                thresholds=self._cfg.optimization_size_thresholds,
            )
        except ValueError as exc:
            raise InvalidOptimizationConfigError(str(exc)) from exc
        backend = self._cfg.backend_for_tier(resolved)

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
        # cancel() BEFORE delete(): it dequeues/terminates any QUEUED or
        # RUNNING executor-side entry first (like cancel_optimization does).
        # Without this a QUEUED run's id would linger in the executor's
        # internal queue after its registry record is gone, and a later
        # pump() would pop it and hit a RunNotFoundError trying to launch it.
        self._executor.cancel(run_id)
        self._registry.delete(run_id)
        return {"run_id": run_id, "deleted": True}

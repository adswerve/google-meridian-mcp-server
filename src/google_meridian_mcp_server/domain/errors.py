"""Domain-specific error types."""

from __future__ import annotations


class MeridianMcpError(Exception):
    """Base error for all MCP server errors."""

    def __init__(self, error_code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.error_code = error_code
        self.details = details or {}

    def to_payload(self) -> dict:
        return {
            "error_code": self.error_code,
            "message": str(self),
            "details": self.details,
        }

    @classmethod
    def from_payload(cls, payload: dict) -> "MeridianMcpError":
        # Always build the BASE class - subclass __init__ signatures differ.
        return MeridianMcpError(
            error_code=payload.get("error_code", "internal_error"),
            message=payload.get("message", ""),
            details=payload.get("details") or {},
        )


class ModelNotFoundError(MeridianMcpError):
    def __init__(self, model_id: str, backend: str | None = None):
        super().__init__(
            error_code="model_not_found",
            message=f"Model '{model_id}' is not available in the configured backend.",
            details={"model_id": model_id, "backend": backend or "unknown"},
        )


class BackendUnavailableError(MeridianMcpError):
    def __init__(self, backend: str, reason: str = ""):
        super().__init__(
            error_code="backend_unavailable",
            message=f"Backend '{backend}' is not available: {reason}",
            details={"backend": backend},
        )


class AuthenticationFailedError(MeridianMcpError):
    def __init__(self, backend: str, reason: str = ""):
        super().__init__(
            error_code="authentication_failed",
            message=f"Authentication failed for backend '{backend}': {reason}",
            details={"backend": backend, "next_action": "Verify ADC access."},
        )


class InvalidFiltersError(MeridianMcpError):
    def __init__(self, reason: str):
        super().__init__(
            error_code="invalid_filters",
            message=f"Invalid filters: {reason}",
        )


class InvalidOutputTypeError(MeridianMcpError):
    def __init__(self, output_type: str, valid_types: list[str]):
        super().__init__(
            error_code="invalid_output_type",
            message=f"Unsupported output_type '{output_type}'. Valid: {valid_types}",
            details={"valid_types": valid_types},
        )


class MissingModelDataError(MeridianMcpError):
    def __init__(self, model_id: str, reason: str):
        super().__init__(
            error_code="missing_model_data",
            message=f"Model '{model_id}' is missing required data: {reason}",
            details={"model_id": model_id},
        )


class DatasetNotAvailableError(MeridianMcpError):
    def __init__(self, model_id: str, dataset: str):
        super().__init__(
            error_code="dataset_not_available",
            message=f"Dataset '{dataset}' is not available for model '{model_id}'.",
            details={"model_id": model_id, "dataset": dataset},
        )


class MetricNotSupportedError(MeridianMcpError):
    def __init__(self, model_id: str, output_type: str, reason: str):
        super().__init__(
            error_code="metric_not_supported",
            message=(
                f"Metric '{output_type}' is not supported for model "
                f"'{model_id}': {reason}"
            ),
            details={
                "model_id": model_id,
                "output_type": output_type,
                "reason": reason,
            },
        )


class UnsupportedModelFormatError(MeridianMcpError):
    """Raised when a model file cannot be loaded because of its extension.

    Meridian 2.0 deprecated the pickle persistence API (``save_mmm`` /
    ``load_mmm``) with no backward-compatibility guarantee, and a pickle
    checkpoint saved under the TensorFlow backend restores raw TF
    ``EagerTensor``s regardless of the active backend -- which crashes with
    a ``TracerArrayConversionError`` under JAX. This server therefore only
    loads Meridian's proto format (``.binpb``); a ``.pkl`` model must be
    re-exported to ``.binpb`` before it can be loaded here.
    """

    def __init__(self, path: str, extension: str):
        if extension == ".pkl":
            message = (
                f"Model file '{path}' is a pickle (.pkl) checkpoint. Pickle "
                "models are no longer supported: Meridian 2.0 deprecated "
                "save_mmm/load_mmm, and loading a pickle checkpoint under the "
                "JAX backend fails at inference time. Re-export this model to "
                "Meridian's proto format (.binpb) and load that file instead."
            )
        else:
            message = (
                f"Unsupported model format '{extension}' for '{path}'. Only "
                "Meridian's proto format (.binpb) is supported."
            )
        super().__init__(
            error_code="unsupported_model_format",
            message=message,
            details={
                "path": path,
                "extension": extension,
                "expected_extension": ".binpb",
            },
        )


class WorkerFailedError(MeridianMcpError):
    def __init__(
        self, message: str = "worker process failed", details: dict | None = None
    ):
        super().__init__("worker_failed", message, details)


class WorkerTimeoutError(MeridianMcpError):
    def __init__(
        self, message: str = "worker process timed out", details: dict | None = None
    ):
        super().__init__("worker_timeout", message, details)


class ServerBusyError(MeridianMcpError):
    def __init__(self, message: str = "server is busy", details: dict | None = None):
        super().__init__("server_busy", message, details)


class InternalError(MeridianMcpError):
    def __init__(self, message: str = "internal error", details: dict | None = None):
        super().__init__("internal_error", message, details)


class OptimizationFailedError(MeridianMcpError):
    def __init__(self, message: str, details: dict | None = None):
        super().__init__("optimization_failed", message, details)


class WorkerLostError(MeridianMcpError):
    def __init__(
        self, message: str = "worker process was lost", details: dict | None = None
    ):
        super().__init__("worker_lost", message, details)


class ResponseTooLargeError(MeridianMcpError):
    """A response too large to cross the wire. User-correctable: narrow it.

    Row/column counts are optional: the executor backstop measures a file and
    has no shape information, and three worker operations return no rows.
    """

    def __init__(
        self,
        *,
        nbytes: int,
        limit_bytes: int,
        total_rows: int | None = None,
        total_columns: int | None = None,
    ):
        shape = (
            f" ({total_rows} rows x {total_columns} columns)"
            if total_rows is not None and total_columns is not None
            else ""
        )
        super().__init__(
            error_code="response_too_large",
            message=(
                f"Response is {nbytes / 1024 / 1024:.2f} MiB{shape}, over the "
                f"{limit_bytes / 1024 / 1024:.2f} MiB limit. Narrow the request "
                f"with filters.start_date/filters.end_date, filters.geos, or "
                f"filters.channels, or request fewer datasets."
            ),
            details={
                "bytes": nbytes,
                "limit_bytes": limit_bytes,
                "total_rows": total_rows,
                "total_columns": total_columns,
            },
        )

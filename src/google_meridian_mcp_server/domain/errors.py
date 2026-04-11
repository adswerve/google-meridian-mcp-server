"""Domain-specific error types."""

from __future__ import annotations


class MeridianMcpError(Exception):
    """Base error for all MCP server errors."""

    def __init__(self, error_code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.error_code = error_code
        self.details = details or {}


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

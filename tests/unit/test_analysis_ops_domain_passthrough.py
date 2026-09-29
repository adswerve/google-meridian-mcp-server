"""Every analysis_ops worker-boundary catch-all lets MeridianMcpError through as-is.

The error is raised from the call INSIDE each function's try block (the facade method, or
catalog.resolve for channel/training data), so the test fails if the passthrough guard is
reverted (the catch-all would rewrap it as missing_model_data).
"""

from __future__ import annotations

import pytest

from google_meridian_mcp_server.domain.errors import (
    InvalidFullFunnelModelError,
    ModelNotFoundError,
)
from google_meridian_mcp_server.execution.analysis_ops import run_operation


class _Raising:
    def __init__(self, err):
        self._err = err

    def has_rf_channels(self):
        return True

    def has_revenue_per_kpi(self):
        return True

    def get_data_inputs(self):
        return {"media": ["A"], "rf_media": []}

    def resolve_use_kpi(self, filters):
        return False

    def geo_names(self):
        return []

    def __getattr__(self, name):
        def _boom(*args, **kwargs):
            raise self._err

        return _boom


class _Catalog:
    def __init__(self, err):
        self._err = err
        self._obj = _Raising(err)

    def get_facade(self, model_id):
        return self._obj

    def get_interrogator(self, model_id):
        return self._obj

    def resolve(self, model_id):
        raise self._err


CASES = [
    ("get_channel_summary", {"output_type": "paid_summary_metrics", "filters": {}}),
    ("get_contribution", {"output_type": "contribution_metrics", "filters": {}}),
    ("get_reach_frequency", {"filters": {}}),
    ("get_model_fit", {"filters": {}}),
    ("get_model_overview", {}),
    ("get_channel_data", {"filters": {}}),
    ("get_training_data", {"filters": {}, "datasets": ["kpi"]}),
    (
        "get_spend_scenario",
        {"filters": {}, "channel": "A", "spend_increase": 1.0, "base_spend": None},
    ),
]


@pytest.mark.parametrize(("operation", "params"), CASES, ids=[c[0] for c in CASES])
def test_domain_error_passes_through_every_wrapper(operation, params):
    err = InvalidFullFunnelModelError("mediators/m1.binpb does not match (found: M1)")
    with pytest.raises(InvalidFullFunnelModelError):
        run_operation(_Catalog(err), operation, "exp", params)


@pytest.mark.parametrize("operation", ["get_channel_data", "get_training_data"])
def test_unknown_model_on_resolve_is_model_not_found(operation):
    params = {"filters": {}, "datasets": ["kpi"]}
    with pytest.raises(ModelNotFoundError):
        run_operation(_Catalog(ModelNotFoundError("nope")), operation, "nope", params)

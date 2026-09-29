"""FullFunnelModelFit mirrors ModelFit.__init__ from Meridian 2.1.x; fail on upgrade."""

import inspect
from types import SimpleNamespace

import meridian
from meridian.analysis import visualizer

from google_meridian_mcp_server.meridian.full_funnel.visualizers import (
    FullFunnelModelFit,
)


def test_model_fit_init_matches_the_mirrored_version():
    assert meridian.__version__.startswith("2.1."), (
        "Re-check FullFunnelModelFit.__init__ against visualizer.ModelFit.__init__, "
        "then update this guard."
    )
    params = list(inspect.signature(visualizer.ModelFit.__init__).parameters)
    assert params == ["self", "meridian", "use_kpi", "confidence_level"]
    source = inspect.getsource(visualizer.ModelFit.__init__)
    for needle in ("expected_vs_actual_data", "_use_kpi(", "currency_module"):
        assert needle in source, needle


def test_full_funnel_model_fit_uses_the_injected_analyzer():
    class _Analyzer:
        def _use_kpi(self, use_kpi):
            return use_kpi

        def expected_vs_actual_data(self, use_kpi, confidence_level):
            return ("ff-data", use_kpi, confidence_level)

    meridian_stub = SimpleNamespace(input_data=SimpleNamespace(currency_code=None))
    fit = FullFunnelModelFit(
        meridian_stub, _Analyzer(), use_kpi=True, confidence_level=0.8
    )
    assert fit._model_fit_data == ("ff-data", True, 0.8)

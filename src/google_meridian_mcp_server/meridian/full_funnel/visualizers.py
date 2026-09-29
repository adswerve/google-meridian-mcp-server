"""Per-instance full-funnel injection into Meridian visualizers.

ModelFit builds its own plain Analyzer AND computes expected_vs_actual_data inside
__init__, so the analyzer must be in place before that call. This mirrors
meridian 2.1.x visualizer.ModelFit.__init__ (visualizer.py:399-424); only the analyzer
construction differs. tests/unit/test_full_funnel_visualizers_guard.py fails on upgrade.
"""

from __future__ import annotations

from typing import Any

from meridian import constants as c
from meridian.analysis import visualizer
from meridian.common import currency as currency_module


class FullFunnelModelFit(visualizer.ModelFit):
    def __init__(
        self,
        meridian: Any,
        analyzer: Any,
        use_kpi: bool = False,
        confidence_level: float = c.DEFAULT_CONFIDENCE_LEVEL,
    ):  # noqa: D107 - deliberately does not call super().__init__
        self._meridian = meridian
        self._analyzer = analyzer
        self._use_kpi = self._analyzer._use_kpi(use_kpi)
        self._model_fit_data = self._analyzer.expected_vs_actual_data(
            use_kpi=self._use_kpi, confidence_level=confidence_level
        )
        currency_code = getattr(self._meridian.input_data, "currency_code", None)
        self._currency = currency_module.get_currency_symbol(currency_code)

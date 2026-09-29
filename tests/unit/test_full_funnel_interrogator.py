"""MeridianInterrogator full-funnel wiring (fakes; no real Meridian math)."""

from __future__ import annotations

import warnings
from types import SimpleNamespace

import numpy as np
import pytest

from google_meridian_mcp_server.domain.errors import InvalidFullFunnelModelError
from google_meridian_mcp_server.meridian.full_funnel import analyzer as ff_mod
from google_meridian_mcp_server.meridian.interrogator import MeridianInterrogator


def _model(paid):
    return SimpleNamespace(
        input_data=SimpleNamespace(get_all_paid_channels=lambda: np.asarray(paid)),
        model_context=object(),
        inference_data=object(),
    )


class _Recorder:
    calls: list = []

    def __init__(self, *, meridian, mediator_models):
        type(self).calls.append((meridian, dict(mediator_models)))


@pytest.fixture()
def recorder(monkeypatch):
    _Recorder.calls = []
    monkeypatch.setattr(ff_mod, "AnalyzerFullFunnel", _Recorder)
    return _Recorder


def test_single_model_is_not_full_funnel():
    mmm = MeridianInterrogator(_model(["A"]))
    assert mmm.is_full_funnel is False
    assert mmm.mediator_names == []


def test_full_funnel_properties_are_sorted_and_labelled():
    s2 = _model(["A", "B"])
    mmm = MeridianInterrogator(s2, {"M2": _model(["A", "B"]), "M1": _model(["A"])})
    assert mmm.is_full_funnel is True
    assert mmm.mediator_names == ["M1", "M2"]
    assert mmm.mediator_channels("M2") == ["A", "B"]
    assert mmm.rest_labels == {
        "M1": "M1 (brand equity, rest)",
        "M2": "M2 (brand equity, rest)",
    }


def test_analyzer_is_full_funnel_over_all_mediators_and_cached(recorder):
    s2, m1 = _model(["A"]), _model(["A"])
    mmm = MeridianInterrogator(s2, {"M1": m1})
    first = mmm._get_analyzer()
    assert mmm._get_analyzer() is first
    assert recorder.calls == [(s2, {"M1": m1})]


def test_single_mediator_analyzer_reuses_full_analyzer_when_n_is_1(recorder):
    mmm = MeridianInterrogator(_model(["A"]), {"M1": _model(["A"])})
    assert mmm._get_single_mediator_analyzer("M1") is mmm._get_analyzer()
    assert len(recorder.calls) == 1


def test_single_mediator_analyzers_are_built_per_mediator_when_n_is_2(recorder):
    m1, m2 = _model(["A"]), _model(["A", "B"])
    mmm = MeridianInterrogator(_model(["A", "B"]), {"M1": m1, "M2": m2})
    mmm._get_single_mediator_analyzer("M2")
    assert recorder.calls[-1][1] == {"M2": m2}


def test_google_value_error_becomes_domain_error(monkeypatch):
    def _boom(**kwargs):
        raise ValueError("Geos mismatch. Stage 2 has 5 geos")

    monkeypatch.setattr(ff_mod, "AnalyzerFullFunnel", _boom)
    mmm = MeridianInterrogator(_model(["A"]), {"M1": _model(["A"])})
    with pytest.raises(InvalidFullFunnelModelError, match="Geos mismatch"):
        mmm._get_analyzer()


def test_direct_and_stage1_analyzers_are_plain(monkeypatch):
    built = []
    import meridian.analysis.analyzer as analyzer_mod

    def fake_analyzer(*, model_context, inference_data):
        built.append((model_context, inference_data))
        return "plain"

    monkeypatch.setattr(analyzer_mod, "Analyzer", fake_analyzer)
    s2 = _model(["A"])
    m1 = _model(["A"])
    mmm = MeridianInterrogator(s2, {"M1": m1})
    assert mmm._get_direct_analyzer() == "plain"
    assert mmm._get_stage1_analyzer("M1") == "plain"
    assert built == [
        (s2.model_context, s2.inference_data),
        (m1.model_context, m1.inference_data),
    ]


def test_mediator_channels_follow_stage2_paid_order():
    # Mediator model lists its channels in a different order than stage 2.
    mmm = MeridianInterrogator(
        _model(["A", "B", "C"]), {"M1": _model(["C", "A"]), "M2": _model(["B"])}
    )
    assert mmm.mediator_channels("M1") == ["A", "C"]
    assert mmm.mediator_channels("M2") == ["B"]


def test_google_deprecation_warning_is_suppressed_at_construction(monkeypatch):
    def _noisy(**kwargs):
        warnings.warn(
            "The `meridian` argument is deprecated and will be removed",
            DeprecationWarning,
            stacklevel=2,
        )
        return "ff"

    monkeypatch.setattr(ff_mod, "AnalyzerFullFunnel", _noisy)
    mmm = MeridianInterrogator(_model(["A"]), {"M1": _model(["A"])})
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert mmm._get_analyzer() == "ff"


def test_other_deprecation_warnings_are_not_suppressed(monkeypatch):
    def _other(**kwargs):
        warnings.warn("something else is deprecated", DeprecationWarning, stacklevel=2)
        return "ff"

    monkeypatch.setattr(ff_mod, "AnalyzerFullFunnel", _other)
    mmm = MeridianInterrogator(_model(["A"]), {"M1": _model(["A"])})
    with pytest.warns(DeprecationWarning, match="something else"):
        mmm._get_analyzer()

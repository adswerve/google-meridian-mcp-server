"""ModelCatalog resolves full-funnel entries and builds the analyzer eagerly."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from google_meridian_mcp_server.domain.errors import InvalidFullFunnelModelError
from google_meridian_mcp_server.domain.models import MediatorFile, ModelCatalogEntry
from google_meridian_mcp_server.meridian import catalog as catalog_mod
from google_meridian_mcp_server.meridian.full_funnel import analyzer as ff_mod


def _model():
    return SimpleNamespace(
        input_data=SimpleNamespace(get_all_paid_channels=lambda: np.asarray(["A"]))
    )


ENTRY = ModelCatalogEntry(
    model_id="exp",
    display_name="Exp",
    source_backend="local",
    source_path="/r/exp/model.binpb",
    model_format="binpb",
    mediators=(MediatorFile("M1", "/r/exp/mediators/M1.binpb", "e"),),
    model_version="v",
)


class _Discovery:
    def get_model(self, model_id):
        return ENTRY if model_id == "exp" else None


class _Materialization:
    def get_local_paths(self, entry):
        return Path("/r/exp/model.binpb"), {"M1": Path("/r/exp/mediators/M1.binpb")}

    def get_local_path(self, entry):  # pragma: no cover - single-model path
        raise AssertionError("full-funnel entries must use get_local_paths")


@pytest.fixture()
def catalog(monkeypatch):
    s2, m1 = _model(), _model()
    monkeypatch.setattr(
        catalog_mod, "load_full_funnel", lambda p, meds: (s2, {"M1": m1})
    )
    return catalog_mod.ModelCatalog(_Discovery(), _Materialization()), s2, m1


def test_resolve_returns_stage2_and_mediators_separately(catalog):
    cat, s2, m1 = catalog
    assert cat.resolve("exp") is s2
    assert cat.resolve_mediators("exp") == {"M1": m1}


def test_get_facade_builds_full_funnel_analyzer_eagerly(catalog, monkeypatch):
    cat, s2, m1 = catalog
    built = []
    monkeypatch.setattr(
        ff_mod, "AnalyzerFullFunnel", lambda **kw: built.append(kw) or "ff"
    )
    facade = cat.get_facade("exp")
    assert built == [{"meridian": s2, "mediator_models": {"M1": m1}}]
    assert facade.is_full_funnel and facade._get_analyzer() == "ff"


def test_get_facade_surfaces_google_validation_as_domain_error(catalog, monkeypatch):
    cat, _, _ = catalog

    def _boom(**kw):
        raise ValueError("Timepoints mismatch")

    monkeypatch.setattr(ff_mod, "AnalyzerFullFunnel", _boom)
    with pytest.raises(InvalidFullFunnelModelError, match="Timepoints mismatch"):
        cat.get_facade("exp")

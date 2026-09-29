"""The full-funnel fixture spec, its build-completeness rule, and fixture_probe."""

from __future__ import annotations

from pathlib import Path

import pytest

import scripts.generate_validation_models as gen
from scripts.validation import fixture_probe, fixtures
from scripts.validation.matrix import fixture_specs


def test_full_funnel_variant_is_separate_from_drift_variants():
    assert [v.key for v in gen.FULL_FUNNEL_VARIANTS] == ["geo-full-funnel"]
    assert "geo-full-funnel" not in {v.key for v in fixture_specs()}
    assert len(gen.VARIANTS) == 7


def test_full_funnel_frame_has_two_mediators_and_three_paid_channels():
    spec = gen.FULL_FUNNEL_VARIANTS[0]
    df = gen._full_funnel_frame(spec)
    assert len(df) == spec.n_geos * gen.N_TIMES
    for col in ("kpi", "revenue_per_kpi", "control", "M1", "M2", "population"):
        assert col in df.columns
    for c in gen.FF_PAID:
        assert f"{c}_impression" in df.columns and f"{c}_spend" in df.columns
    assert (df[["M1", "M2", "kpi"]] > 0).all().all()


def test_incomplete_build_is_rebuilt(tmp_path, monkeypatch):
    spec = gen.FULL_FUNNEL_VARIANTS[0]
    target = tmp_path / spec.key
    target.mkdir()
    (target / "model.binpb").write_bytes(b"stale")  # mediators missing
    saved: list[str] = []
    monkeypatch.setattr(gen, "_full_funnel_frame", lambda s: "frame")
    monkeypatch.setattr(gen, "_ff_input_data", lambda df, **kw: kw["kpi"])
    monkeypatch.setattr(gen, "_fit_with_spec", lambda data, model_spec: data)
    monkeypatch.setattr(gen, "_ff_model_spec", lambda **kw: None)
    monkeypatch.setattr(gen, "_save", lambda mmm, path: saved.append(Path(path).name))

    gen.build_full_funnel_variant(spec, tmp_path)

    # mediators first, model.binpb LAST
    assert saved == ["M1.binpb", "M2.binpb", "model.binpb"]


def test_complete_build_is_skipped(tmp_path, monkeypatch):
    spec = gen.FULL_FUNNEL_VARIANTS[0]
    target = tmp_path / spec.key
    (target / "mediators").mkdir(parents=True)
    for name in ("model.binpb", "mediators/M1.binpb", "mediators/M2.binpb"):
        (target / name).write_bytes(b"x")
    monkeypatch.setattr(gen, "_save", lambda *a: (_ for _ in ()).throw(AssertionError))
    assert gen.build_full_funnel_variant(spec, tmp_path) == target / "model.binpb"


def test_fixture_probe_reads_model_binpb_not_first_sorted_file(tmp_path, monkeypatch):
    (tmp_path / "mediators").mkdir()
    (tmp_path / "mediators" / "M1.binpb").write_bytes(b"s1")
    (tmp_path / "model.binpb").write_bytes(b"s2")
    monkeypatch.setattr(fixture_probe, "_read_provenance", lambda p: {"read": p.name})
    assert fixture_probe.probe(tmp_path)["read"] == "model.binpb"


def test_ensure_fixture_model_refuses_full_funnel_before_building(monkeypatch):
    def _no_build(*args, **kwargs):
        raise AssertionError("must refuse before building fixtures")

    monkeypatch.setattr(fixtures, "build_all", _no_build)
    with pytest.raises(ValueError, match="ensure_full_funnel_fixture"):
        fixtures.ensure_fixture_model("geo-full-funnel")

"""Domain additions for full-funnel models (pure, no Meridian)."""

from __future__ import annotations

import dataclasses

from google_meridian_mcp_server.domain.errors import InvalidFullFunnelModelError
from google_meridian_mcp_server.domain.models import MediatorFile, ModelCatalogEntry
from google_meridian_mcp_server.domain.optimization import (
    OptimizationConfig,
    OptimizationRun,
    config_fingerprint,
)

_CFG = OptimizationConfig.model_validate({"scenario": {"type": "fixed_budget"}})


def test_catalog_entry_defaults_keep_existing_constructors_working():
    entry = ModelCatalogEntry(
        model_id="m",
        display_name="M",
        source_backend="local",
        source_path="/x/m/model.binpb",
        model_format="binpb",
    )
    assert entry.mediators == ()
    assert entry.model_version is None


def test_mediator_file_is_frozen():
    m = MediatorFile(
        name="M1", source_path="/x/mediators/M1.binpb", etag_or_fingerprint="e"
    )
    assert dataclasses.is_dataclass(m)
    try:
        m.name = "other"  # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        return
    raise AssertionError("MediatorFile must be frozen")


def test_invalid_full_funnel_error_payload():
    err = InvalidFullFunnelModelError("mediators/m1.binpb does not match ...")
    assert err.error_code == "invalid_full_funnel_model"
    assert err.to_payload()["message"] == "mediators/m1.binpb does not match ..."


def test_fingerprint_changes_with_model_version():
    a = config_fingerprint("m", _CFG, meridian_version="2.1.0", model_version="aaa")
    b = config_fingerprint("m", _CFG, meridian_version="2.1.0", model_version="bbb")
    assert a != b


def test_fingerprint_pinned_hex():
    # Pins the exact hashed payload shape: a silent change to what enters the
    # fingerprint would orphan every stored run's reuse.
    got = config_fingerprint("m", _CFG, meridian_version="2.1.0", model_version="abc")
    assert got == "b721a415f5afa960c7604d03ba223cf52058e50d2ac15e397abd0d5e5101cf2d"


def test_optimization_run_legacy_json_without_new_fields_loads():
    legacy = {
        "run_id": "r",
        "label": "l",
        "model_id": "m",
        "config": {"scenario": {"type": "fixed_budget"}},
        "config_fingerprint": "f",
        "compute_tier_requested": "auto",
        "compute_tier_resolved": "local",
        "size_score": 1,
        "created_at": "2026-01-01T00:00:00+00:00",
        "meridian_version": "2.1.0",
        "server_version": "0.3.2",
    }
    run = OptimizationRun.model_validate(legacy)
    assert run.model_version is None
    assert run.funnel == "single"

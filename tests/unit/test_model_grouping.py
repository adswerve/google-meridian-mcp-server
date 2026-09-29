"""group_model_files: folding mediators/, orphans, ordering, model_version."""

from __future__ import annotations

import logging
from pathlib import PurePosixPath

from google_meridian_mcp_server.domain.models import MediatorFile
from google_meridian_mcp_server.persistence.base import (
    ListedFile,
    compute_model_version,
    group_model_files,
)


def _f(path: str, etag: str | None = "e") -> ListedFile:
    return ListedFile(
        relative_path=PurePosixPath(path),
        source_path=f"/root/{path}",
        etag_or_fingerprint=etag if etag is None else f"{etag}:{path}",
        last_modified=None,
    )


def test_mediators_fold_into_their_experiment():
    grouped = group_model_files(
        [
            _f("exp/mediators/M2.binpb"),
            _f("exp/model.binpb"),
            _f("exp/mediators/M1.binpb"),
        ]
    )
    assert [g.model_id for g in grouped] == ["exp"]
    assert [m.name for m in grouped[0].mediators] == ["M1", "M2"]
    assert grouped[0].mediators[0].source_path == "/root/exp/mediators/M1.binpb"


def test_orphan_mediators_are_skipped_with_warning(caplog):
    with caplog.at_level(logging.WARNING):
        grouped = group_model_files(
            [_f("lonely/mediators/M1.binpb"), _f("other.binpb")]
        )
    assert [g.model_id for g in grouped] == ["other"]
    assert "lonely/mediators/M1.binpb" in caplog.text


def test_mediators_model_binpb_is_never_its_own_model():
    grouped = group_model_files([_f("exp/mediators/model.binpb")])
    assert grouped == []  # orphan: no exp/model.binpb


def test_single_models_are_unchanged_and_sorted_by_model_id():
    grouped = group_model_files(
        [_f("team/a/model.binpb"), _f("team-b/model.binpb"), _f("flat.binpb")]
    )
    assert [g.model_id for g in grouped] == ["flat", "team-b", "team/a"]
    assert all(g.mediators == () for g in grouped)


def test_model_version_is_model_first_then_mediators_by_name():
    m1 = MediatorFile("M1", "/p1", "e1")
    m2 = MediatorFile("M2", "/p2", "e2")
    assert compute_model_version("s", [m2, m1]) == compute_model_version("s", [m1, m2])
    assert compute_model_version("s", []) != compute_model_version("s", [m1])
    assert compute_model_version(None, []) is None
    assert compute_model_version("s", [MediatorFile("M1", "/p", None)]) is None
    assert len(compute_model_version("s", [])) == 16


def test_model_version_changes_when_a_mediator_etag_changes():
    before = group_model_files(
        [_f("exp/model.binpb"), _f("exp/mediators/M1.binpb", "v1")]
    )
    after = group_model_files(
        [_f("exp/model.binpb"), _f("exp/mediators/M1.binpb", "v2")]
    )
    assert before[0].model_version != after[0].model_version

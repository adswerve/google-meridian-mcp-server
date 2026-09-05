"""Volatile values must lose their value but keep their type and nullability."""

from scripts.validation.normalize import VOLATILE_FIELDS, normalize


def test_all_ten_spec_fields_are_normalized():
    assert set(VOLATILE_FIELDS) == {
        # optimization payloads
        "run_id",
        "created_at",
        "heartbeat_at",
        "started_at",
        "finished_at",
        "elapsed_seconds",
        # list_models entries (spec 7.3.1)
        "last_modified",
        "source_path",
        "source_backend",
        "etag_or_fingerprint",
    }


def test_run_id_value_is_replaced_but_key_survives():
    out = normalize({"run_id": "national-revenue-20260904T101500-a1b2c3"})
    assert out == {"run_id": "<run_id>"}


def test_two_different_run_ids_normalize_identically():
    a = normalize({"run_id": "m-20260904T101500-a1b2c3"})
    b = normalize({"run_id": "m-20260905T230101-ffeedd"})
    assert a == b


def test_null_is_distinguished_from_present():
    assert normalize({"finished_at": None}) == {"finished_at": "<iso8601>|null"}
    assert normalize({"finished_at": "2026-09-04T10:15:00+00:00"}) == {
        "finished_at": "<iso8601>"
    }


def test_elapsed_seconds_accepts_int_and_float_identically():
    assert normalize({"elapsed_seconds": 3}) == normalize({"elapsed_seconds": 3.5})


def test_type_change_is_still_visible():
    """A float turning into a string must NOT normalize to the same token."""
    good = normalize({"elapsed_seconds": 3.5})
    bad = normalize({"elapsed_seconds": "3.5"})
    assert good != bad
    assert bad == {"elapsed_seconds": "<float>|unexpected-type:str"}


def test_booleans_are_not_accepted_as_floats():
    assert normalize({"elapsed_seconds": True}) == {
        "elapsed_seconds": "<float>|unexpected-type:bool"
    }


def test_a_refit_changes_mtimes_and_that_must_not_read_as_drift():
    """Phase 5 rebuilds every fixture, so every last_modified changes."""
    before = normalize(
        {"model_id": "geo-revenue", "last_modified": "2026-01-01T00:00:00"}
    )
    after = normalize(
        {"model_id": "geo-revenue", "last_modified": "2026-09-04T12:00:00"}
    )
    assert before == after == {"model_id": "geo-revenue", "last_modified": "<iso8601>"}


def test_local_and_gcs_catalog_entries_normalize_identically():
    """Phase 7 captures against Cloud Run, where the backend and path differ."""
    local = normalize(
        {
            "model_id": "geo-revenue",
            "source_backend": "local",
            "source_path": "models/_validation/geo-revenue/model.binpb",
            "etag_or_fingerprint": None,
        }
    )
    cloud = normalize(
        {
            "model_id": "geo-revenue",
            "source_backend": "gcs",
            "source_path": "rover/geo-revenue/model.binpb",
            "etag_or_fingerprint": "CJmZ2vTx",
        }
    )
    assert local["source_backend"] == cloud["source_backend"] == "<backend>"
    assert local["source_path"] == cloud["source_path"] == "<path>"
    # Nullability is still diffed: absent on local, present on GCS.
    assert local["etag_or_fingerprint"] == "<fingerprint>|null"
    assert cloud["etag_or_fingerprint"] == "<fingerprint>"


def test_model_format_and_status_are_NOT_normalized():
    """Only environment-dependent catalog fields are waived; a model that
    silently changed format or status is a real finding."""
    payload = {
        "model_format": "binpb",
        "status": "ready",
        "display_name": "geo-revenue",
    }
    assert normalize(payload) == payload


def test_nested_and_listed_occurrences_are_normalized():
    payload = {
        "runs": [
            {"run_id": "a", "label": "keep me"},
            {"run_id": "b", "created_at": "2026-01-01T00:00:00+00:00"},
        ]
    }
    assert normalize(payload) == {
        "runs": [
            {"run_id": "<run_id>", "label": "keep me"},
            {"run_id": "<run_id>", "created_at": "<iso8601>"},
        ]
    }


def test_non_volatile_values_are_untouched():
    payload = {"columns": ["a", "b"], "rows": [[1.0, None]], "row_count": 1}
    assert normalize(payload) == payload


def test_input_is_not_mutated():
    payload = {"run_id": "a"}
    normalize(payload)
    assert payload == {"run_id": "a"}


def test_volatile_key_nested_inside_a_dict_of_dicts_is_normalized():
    """A volatile key can appear as the value of another dict, not just
    inside a list -- e.g. a run object keyed by run_id, or a status
    envelope nesting the optimization result under a fixed key."""
    payload = {
        "status": {
            "run_id": "national-revenue-20260904T101500-a1b2c3",
            "heartbeat_at": None,
            "meta": {
                "finished_at": "2026-09-04T10:15:00+00:00",
                "elapsed_seconds": 12.5,
            },
        }
    }
    assert normalize(payload) == {
        "status": {
            "run_id": "<run_id>",
            "heartbeat_at": "<iso8601>|null",
            "meta": {"finished_at": "<iso8601>", "elapsed_seconds": "<float>"},
        }
    }


def test_volatile_key_nested_inside_list_of_lists_is_normalized():
    """A run object can appear inside a list nested inside another list
    (e.g. a batch of optimization runs grouped by scenario)."""
    payload = {
        "scenarios": [
            [
                {"run_id": "a", "created_at": "2026-01-01T00:00:00+00:00"},
                {"run_id": "b"},
            ]
        ]
    }
    assert normalize(payload) == {
        "scenarios": [
            [
                {"run_id": "<run_id>", "created_at": "<iso8601>"},
                {"run_id": "<run_id>"},
            ]
        ]
    }

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
            "source_path": "models/geo-revenue/model.binpb",
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


def test_embedded_run_id_in_a_message_string_is_replaced_wherever_it_appears():
    """RunNotFoundError bakes run_id into a sentence
    (persistence/optimization_run_registry.py:24) -- a key-name check can
    never reach it there. This must be neutralized at any nesting depth."""
    payload = {
        "status_after_delete": {
            "error_code": "optimization_run_not_found",
            "message": "Optimization run 'geo-revenue-20260905T044640-80b9c7' "
            "was not found.",
            "details": {"run_id": "geo-revenue-20260905T044640-80b9c7"},
        }
    }
    out = normalize(payload)
    assert out["status_after_delete"]["message"] == (
        "Optimization run 'geo-revenue-<run_id>' was not found."
    )
    assert out["status_after_delete"]["details"] == {"run_id": "<run_id>"}


def test_two_messages_differing_only_by_run_id_normalize_identically():
    a = normalize(
        {"message": "Optimization run 'm-20260904T101500-a1b2c3' was not found."}
    )
    b = normalize(
        {"message": "Optimization run 'm-20260905T230101-ffeedd' was not found."}
    )
    assert a == b == {"message": "Optimization run 'm-<run_id>' was not found."}


def test_two_messages_differing_in_actual_wording_still_differ():
    """The substring substitution must not mask an unrelated real change --
    only the run-id-shaped span is touched, never the rest of the string."""
    a = normalize(
        {"message": "Optimization run 'm-20260904T101500-a1b2c3' was not found."}
    )
    b = normalize(
        {
            "message": "Optimization run 'm-20260904T101500-a1b2c3' has no "
            "result yet (status=queued)."
        }
    )
    assert a != b


def test_result_not_ready_message_run_id_is_also_neutralized():
    """The second known template (optimization_run_registry.py:33), same
    treatment."""
    a = normalize(
        {
            "message": "Optimization run 'm-20260904T101500-a1b2c3' has no "
            "result yet (status=queued)."
        }
    )
    b = normalize(
        {
            "message": "Optimization run 'm-20260905T230101-ffeedd' has no "
            "result yet (status=queued)."
        }
    )
    assert a == b


def test_string_merely_resembling_the_pattern_but_not_a_run_id_is_still_scrubbed():
    """The regex is anchored purely on SHAPE (8 digits, literal T, 6 digits,
    hyphen, 6 lowercase hex), not on any surrounding context -- it cannot
    tell a real run id from coincidental text of the identical shape, and
    does not try to. Documented, deliberate behaviour: nothing this server
    emits produces that 14-character shape except an actual run id (see the
    module docstring), so treating any occurrence of it as volatile is safe
    in practice, not merely permissive by accident."""
    out = normalize({"note": "ref 20260904T101500-a1b2c3 unrelated"})
    assert out == {"note": "ref <run_id> unrelated"}


def test_iso8601_timestamps_are_not_mistaken_for_a_run_id():
    """A real ISO 8601 string (dashes inside the date, colons inside the
    time) never contains 8 consecutive digits followed by a bare `T`, so it
    must pass through the new substring scrub untouched."""
    out = normalize({"note": "seen at 2026-09-04T10:15:00+00:00"})
    assert out == {"note": "seen at 2026-09-04T10:15:00+00:00"}


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

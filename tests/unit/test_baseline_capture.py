"""Snapshot envelope, atomic writes, resumability and failure accounting."""

import json
import os
import time
import types

import httpx2
import pytest

from scripts.validation import capture_baseline as cb
from scripts.validation.matrix import ToolCase

CASE = ToolCase("get_channel_summary", "roi", {}, frozenset(), "service")


class _Result:
    def __init__(self, payload):
        self.structured_content = payload
        self.data = None
        self.content = []


class _FakeCaptureClient:
    """Fake FastMCP client for ``capture()``-level tests.

    ``responses`` maps tool name -> payload (or a callable(args) -> payload).
    ``raise_on`` names tools whose call raises instead of returning, so
    ``call_tool`` turns them into ``CaptureFailure`` the same way a real
    transport blip would.
    """

    def __init__(self, responses=None, raise_on=None):
        self.responses = dict(responses or {})
        self.raise_on = set(raise_on or ())
        self.calls: list[tuple[str, dict]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def call_tool(self, name, args):
        self.calls.append((name, dict(args)))
        if name in self.raise_on:
            raise TimeoutError(f"{name} blew up")
        value = self.responses.get(name, {"ok": True})
        if callable(value):
            value = value(args)
        return _Result(value)


def _patch_matrix(monkeypatch, keys, cases):
    """Make ``matrix.fixture_specs()`` return one fake variant per key, each
    exposing exactly the ``cases`` list regardless of overview content."""
    variants = [types.SimpleNamespace(key=key) for key in keys]
    monkeypatch.setattr(cb.matrix, "fixture_specs", lambda: variants)
    monkeypatch.setattr(cb.matrix, "tool_cases", lambda variant, overview: list(cases))
    monkeypatch.setattr(cb.matrix, "adversarial_tool_cases", lambda variant: [])


def _patch_client(monkeypatch, client):
    monkeypatch.setattr(cb, "build_client", lambda transport, url: client)


def _env(worker_environment, variant_key="v1"):
    """Build a capture_env exactly the way `capture()` itself does for one
    variant, so hand-crafted test envelopes match real `capture()` output
    byte for byte. `variant_key` need not be a real fixture -- a
    nonexistent fixture dir hashes to a fixed (still-comparable) sentinel."""
    return cb.capture_environment(
        "inprocess",
        worker_environment,
        fixture_hash=cb.fixture_content_hash(cb.DEFAULT_OUT_ROOT / variant_key),
        src_hash=cb.src_tree_hash(),
    )


async def _no_sleep(*_args, **_kwargs):
    return None


_OVERVIEW_OK = {"time": ["2024-01-01"]}


def test_snapshot_path_is_label_variant_tool_case(tmp_path):
    path = cb.case_path(tmp_path, "v1.7-engine", "geo-revenue", CASE)
    assert (
        path
        == tmp_path / "v1.7-engine" / "geo-revenue" / "get_channel_summary__roi.json"
    )


# ---------------------------------------------------------------------------
# Fix wave 4 -- the snapshot envelope: {"capture_env": ..., "payload": ...},
# with an optional "known_racy_fields". Replaces the bare-payload file.
# ---------------------------------------------------------------------------


def test_write_snapshot_wraps_the_payload_in_a_self_describing_envelope(tmp_path):
    path = tmp_path / "a" / "b.json"
    env = {"transport": "inprocess", "python": "3.12.8"}
    cb.write_snapshot(
        path, {"run_id": "m-20260904T101500-a1b2c3", "rows": [[1.0]]}, capture_env=env
    )
    on_disk = json.loads(path.read_text())
    assert on_disk == {
        "capture_env": env,
        "payload": {"run_id": "<run_id>", "rows": [[1.0]]},
    }


def test_write_snapshot_includes_known_racy_fields_only_when_given(tmp_path):
    with_racy = tmp_path / "with_racy.json"
    cb.write_snapshot(
        with_racy,
        {"status": {"status": "completed"}},
        capture_env={},
        racy_fields=["status.status"],
    )
    on_disk = json.loads(with_racy.read_text())
    assert on_disk["known_racy_fields"] == ["status.status"]
    assert "known_racy_fields" not in on_disk["payload"]  # moved OUT of the payload

    without_racy = tmp_path / "without_racy.json"
    cb.write_snapshot(without_racy, {"ok": True}, capture_env={})
    assert "known_racy_fields" not in json.loads(without_racy.read_text())


def test_write_snapshot_leaves_no_temp_file_behind(tmp_path):
    path = tmp_path / "b.json"
    cb.write_snapshot(path, {"ok": True}, capture_env={})
    assert sorted(p.name for p in tmp_path.iterdir()) == ["b.json"]


def test_write_snapshot_overwrites_in_place(tmp_path):
    path = tmp_path / "b.json"
    cb.write_snapshot(path, {"v": 1}, capture_env={})
    cb.write_snapshot(path, {"v": 2}, capture_env={})
    assert json.loads(path.read_text())["payload"] == {"v": 2}


def test_read_snapshot_returns_none_for_missing_empty_corrupt_or_non_object(tmp_path):
    assert cb.read_snapshot(tmp_path / "missing.json") is None

    empty = tmp_path / "empty.json"
    empty.write_text("")
    assert cb.read_snapshot(empty) is None

    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{oops")
    assert cb.read_snapshot(corrupt) is None

    not_an_object = tmp_path / "list.json"
    not_an_object.write_text("[1, 2, 3]")
    assert cb.read_snapshot(not_an_object) is None


def test_read_snapshot_rejects_an_object_with_no_payload_key(tmp_path):
    """MINOR 1: a JSON object with a `capture_env` but no `payload` is the
    exact failure class CRITICAL 1 closed for the bare-payload file --
    nothing writes such a file today, but it must not read as valid."""
    path = tmp_path / "no_payload.json"
    path.write_text(json.dumps({"capture_env": {"e": "x"}}))
    assert cb.read_snapshot(path) is None


def test_read_snapshot_returns_the_envelope_for_a_valid_file(tmp_path):
    path = tmp_path / "ok.json"
    cb.write_snapshot(path, {"a": 1}, capture_env={"e": "x"})
    assert cb.read_snapshot(path) == {"capture_env": {"e": "x"}, "payload": {"a": 1}}


def test_prepare_env_isolates_the_registry(tmp_path, monkeypatch):
    monkeypatch.setenv("RESULT_CACHE_ENABLED", "true")
    monkeypatch.setenv("OPTIMIZATION_RUNS_ROOT", "/somewhere/shared")
    cb.prepare_env(str(tmp_path / "runs"))
    assert os.environ["OPTIMIZATION_RUNS_ROOT"] == str(tmp_path / "runs")
    assert os.environ["RESULT_CACHE_ENABLED"] == "false"
    assert os.environ["PERSISTENCE_BACKEND"] == "local"
    assert os.environ["OPTIMIZATION_TIER"] == "local"


def test_worker_env_reports_what_the_workers_actually_get(monkeypatch):
    """Previously ``"MERIDIAN_BACKEND" in env or "TF_CPP_MIN_LOG_LEVEL" in env``,
    which passes on any dict containing either key -- and TF_CPP_MIN_LOG_LEVEL
    is unconditionally set by ``child_env``'s hygiene defaults, so the check
    could never fail regardless of what ``worker_env`` actually did with
    MERIDIAN_BACKEND. Assert the ambient value flows through untouched
    instead, which fails if ``worker_env`` stops delegating to
    ``BaseSubprocessExecutor.child_env``."""
    monkeypatch.setenv("MERIDIAN_BACKEND", "jax")
    env = cb.worker_env()
    assert env["MERIDIAN_BACKEND"] == "jax"
    # child_env's own hygiene default -- a real, checkable value, not just presence.
    assert env["TF_CPP_MIN_LOG_LEVEL"] == "3"
    assert "PATH" in env


def test_probe_backend_returns_the_module_constant_regardless_of_env(monkeypatch):
    """Task 16: Phase 4 removed the ``MERIDIAN_BACKEND`` env-var knob
    entirely; ``probe_backend()`` now imports
    ``base_subprocess.MERIDIAN_BACKEND`` directly instead of reading an env
    var or guessing a "tensorflow" default, so it follows the code instead
    of guessing. With no ambient override it must report "jax"."""
    monkeypatch.delenv("MERIDIAN_BACKEND", raising=False)
    assert cb.probe_backend() == "jax"


def test_probe_backend_ignores_an_ambient_env_override(monkeypatch):
    """An operator-set MERIDIAN_BACKEND no longer has any effect: with the
    knob removed there is only one backend, and probe_backend() must follow
    the code (the module constant) rather than any environment variable --
    reading one back would silently misreport provenance the moment an
    operator's ambient env happened to disagree with what the workers
    actually ran on."""
    monkeypatch.setenv("MERIDIAN_BACKEND", "tensorflow")
    assert cb.probe_backend() == "jax"


def test_probe_backend_agrees_with_worker_env_now(monkeypatch):
    """The class of bug this whole fix exists to close: probing must not be
    left to derive its backend from ``worker_env()`` -- but now that Task 16
    imports the same module constant ``worker_env()``/``child_env()`` force
    (Task 15, D2), both report "jax" and genuinely agree, closing the
    mismatch the earlier regression guard existed to catch."""
    monkeypatch.delenv("MERIDIAN_BACKEND", raising=False)
    inherited = cb.worker_env()
    assert inherited.get("MERIDIAN_BACKEND") == "jax"
    assert cb.probe_backend() == "jax"


async def test_capture_writes_the_manifest_with_an_explicit_probe_backend(
    tmp_path, monkeypatch
):
    """End-to-end guard: ``capture()`` must actually pass ``probe_backend()``'s
    value through to ``build_manifest``, not just define the function and
    never wire it up -- the exact way this bug could reappear silently."""
    _patch_matrix(monkeypatch, ["v1"], [CASE])
    _patch_client(
        monkeypatch,
        _FakeCaptureClient(
            {"get_model_overview": _OVERVIEW_OK, "get_channel_summary": {"ok": True}}
        ),
    )
    captured_kwargs = {}

    def _fake_build_manifest(**kwargs):
        captured_kwargs.update(kwargs)
        return {"stub": True}

    monkeypatch.setattr(cb, "build_manifest", _fake_build_manifest)
    monkeypatch.setattr(cb, "probe_backend", lambda: "tensorflow")

    rc = await cb.capture(
        label="L",
        transport="inprocess",
        url=None,
        variant_keys=["v1"],
        tools=None,
        cases_filter=None,
        compute_tier=None,
        force=False,
        out_root=tmp_path,
        variants_selected=True,
    )

    assert rc == 0
    assert captured_kwargs.get("probe_backend") == "tensorflow"


def test_selected_cases_honours_tool_and_case_filters():
    cases = [
        ToolCase("get_model_fit", "default", {}, frozenset(), "service"),
        ToolCase("get_model_fit", "geo_filter", {}, frozenset(), "service"),
        ToolCase("get_channel_data", "all", {}, frozenset(), "service"),
    ]
    assert [
        c.name
        for c in cb.selected_cases(cases, tools=["get_model_fit"], cases_filter=None)
    ] == ["default", "geo_filter"]
    assert [
        c.tool for c in cb.selected_cases(cases, tools=None, cases_filter=["all"])
    ] == ["get_channel_data"]
    assert len(cb.selected_cases(cases, tools=None, cases_filter=None)) == 3


async def test_call_tool_returns_error_envelopes_as_data():
    """A typed error envelope IS the result. metric_not_supported silently
    becoming a different code is a breaking change for every consuming agent."""

    class _Client:
        async def call_tool(self, name, args):
            return _Result({"error_code": "metric_not_supported"})

    payload = await cb.call_tool(_Client(), "get_channel_summary", {})
    assert payload == {"error_code": "metric_not_supported"}


async def test_call_tool_raises_on_a_transport_error_instead_of_snapshotting_it():
    """A blip must NOT be written to disk: the file's existence would make the
    resumed rerun skip the case, permanently."""

    class _Client:
        async def call_tool(self, name, args):
            raise TimeoutError("read timed out")

    with pytest.raises(cb.CaptureFailure, match="TimeoutError"):
        await cb.call_tool(_Client(), "get_model_fit", {})


async def test_optimization_case_passes_force_rerun_and_the_requested_tier():
    calls = []

    class _Client:
        async def call_tool(self, name, args):
            calls.append((name, args))
            if name in ("run_optimization", "run_future_optimization"):
                return _Result({"run_id": "r1", "compute_tier_resolved": "cloud_gpu"})
            if name == "get_optimization_status":
                return _Result({"status": "completed"})
            return _Result({"ok": True})

    out = await cb.run_optimization_case(
        _Client(), "run_optimization", {"model_id": "m"}, compute_tier="cloud_gpu"
    )
    submit_args = calls[0][1]
    assert submit_args["force_rerun"] is True
    assert submit_args["compute_tier"] == "cloud_gpu"
    assert out["submit"]["compute_tier_resolved"] == "cloud_gpu"
    assert ("delete_optimization", {"run_id": "r1"}) in calls


async def test_optimization_case_omits_compute_tier_when_not_requested():
    calls = []

    class _Client:
        async def call_tool(self, name, args):
            calls.append((name, args))
            if name == "run_optimization":
                return _Result({"run_id": "r1"})
            if name == "get_optimization_status":
                return _Result({"status": "completed"})
            return _Result({"ok": True})

    await cb.run_optimization_case(
        _Client(), "run_optimization", {"model_id": "m"}, compute_tier=None
    )
    assert "compute_tier" not in calls[0][1]


async def test_lifecycle_case_survives_a_submit_that_returns_an_error_envelope():
    class _Client:
        async def call_tool(self, name, args):
            return _Result({"error_code": "invalid_optimization_config"})

    out = await cb.run_lifecycle_case(_Client(), "m", "cancel", compute_tier=None)
    assert out == {"submit": {"error_code": "invalid_optimization_config"}}


# ---------------------------------------------------------------------------
# MINOR 7 -- the reap in run_optimization_case must never mask the real
# outcome or the real error.
# ---------------------------------------------------------------------------


async def test_optimization_case_reap_failure_does_not_mask_a_successful_outcome():
    class _Client:
        async def call_tool(self, name, args):
            if name == "run_optimization":
                return _Result({"run_id": "r1"})
            if name == "get_optimization_status":
                return _Result({"status": "completed"})
            if name == "get_optimization_result":
                return _Result({"answer": 42})
            if name == "delete_optimization":
                raise TimeoutError("reap blew up")
            raise AssertionError(name)

    out = await cb.run_optimization_case(
        _Client(), "run_optimization", {"model_id": "m"}, compute_tier=None
    )
    assert out == {
        "submit": {"run_id": "r1"},
        "status": {"status": "completed"},
        "result": {"answer": 42},
    }


async def test_optimization_case_preserves_the_primary_error_when_the_reap_also_fails():
    class _Client:
        async def call_tool(self, name, args):
            if name == "run_optimization":
                return _Result({"run_id": "r1"})
            if name == "get_optimization_status":
                raise TimeoutError("status blew up")
            if name == "delete_optimization":
                raise RuntimeError("reap also blew up")
            raise AssertionError(name)

    with pytest.raises(cb.CaptureFailure, match="status blew up"):
        await cb.run_optimization_case(
            _Client(),
            "run_optimization",
            {"model_id": "m"},
            compute_tier=None,
            poll_timeout=0.5,
        )


# ---------------------------------------------------------------------------
# IMPORTANT 4 -- list_optimizations must be scoped to this run, not collapsed
# to a registry-wide count.
# ---------------------------------------------------------------------------


async def test_lifecycle_case_scopes_listing_to_this_runs_own_entry():
    other_run = {"run_id": "other-run", "label": "x", "status": "completed"}
    this_run = {"run_id": "r1", "label": "mine", "status": "completed"}

    class _Client:
        def __init__(self):
            self.submits = 0

        async def call_tool(self, name, args):
            if name == "run_optimization":
                self.submits += 1
                return _Result({"run_id": "r1" if self.submits == 1 else "r2"})
            if name == "get_optimization_status":
                return _Result({"status": "completed"})
            if name == "get_optimization_result":
                return _Result({"ok": True})
            if name == "list_optimizations":
                # A shared registry: an unrelated run plus this run's own.
                return _Result({"runs": [other_run, this_run], "count": 57})
            return _Result({"ok": True})

    out = await cb.run_lifecycle_case(
        _Client(), "m", "status_result_reuse_delete", compute_tier=None
    )
    assert out["listing"]["runs"] == [this_run]
    assert out["listing"]["count"] == 1


async def test_lifecycle_case_scoped_listing_is_invariant_to_unrelated_runs():
    """Changing only the unrelated-run entries or the raw registry count must
    NOT change this case's snapshot -- that is the whole point of scoping."""

    def _client_with_registry(other_runs, raw_count):
        this_run = {"run_id": "r1", "label": "mine", "status": "completed"}

        class _Client:
            async def call_tool(self, name, args):
                if name == "run_optimization":
                    return _Result({"run_id": "r1"})
                if name == "get_optimization_status":
                    return _Result({"status": "completed"})
                if name == "get_optimization_result":
                    return _Result({"ok": True})
                if name == "list_optimizations":
                    return _Result(
                        {"runs": [*other_runs, this_run], "count": raw_count}
                    )
                return _Result({"ok": True})

        return _Client()

    out_a = await cb.run_lifecycle_case(
        _client_with_registry([{"run_id": "a", "status": "completed"}], 12),
        "m",
        "status_result_reuse_delete",
        compute_tier=None,
    )
    out_b = await cb.run_lifecycle_case(
        _client_with_registry(
            [
                {"run_id": "a", "status": "completed"},
                {"run_id": "b", "status": "failed"},
                {"run_id": "c", "status": "queued"},
            ],
            999,
        ),
        "m",
        "status_result_reuse_delete",
        compute_tier=None,
    )
    assert out_a["listing"] == out_b["listing"]


# ---------------------------------------------------------------------------
# IMPORTANT 5 -- cancel must poll to a terminal state and flag the race.
# ---------------------------------------------------------------------------


async def test_lifecycle_cancel_polls_to_terminal_and_flags_the_racy_field(monkeypatch):
    monkeypatch.setattr(cb.asyncio, "sleep", _no_sleep)
    polls = {"n": 0}

    class _Client:
        async def call_tool(self, name, args):
            if name == "run_optimization":
                return _Result({"run_id": "r1"})
            if name == "cancel_optimization":
                return _Result({"run_id": "r1", "status": "canceled"})
            if name == "get_optimization_status":
                polls["n"] += 1
                # First read catches the race mid-flight; only the second is terminal.
                status = "running" if polls["n"] == 1 else "completed"
                return _Result({"run_id": "r1", "status": status})
            return _Result({"ok": True})

    out = await cb.run_lifecycle_case(
        _Client(), "m", "cancel", compute_tier=None, poll_timeout=1.0
    )
    assert out["status"]["status"] == "completed"
    assert polls["n"] == 2  # actually polled, not a single arbitrary read
    assert out[cb.KNOWN_RACY_FIELDS_KEY] == ["status.status"]


# ---------------------------------------------------------------------------
# IMPORTANT 6 -- the poll ceiling must be a parameter, not a hard-coded 120s.
# ---------------------------------------------------------------------------


def test_cli_poll_timeout_defaults_to_the_module_constant_and_reaches_poll():
    """Asserts the actual CLI wiring: --poll-timeout's default resolves from
    the same constant _poll itself defaults to, so changing one without the
    other would be caught (not just asserting the constant's own value)."""
    args = cb._parse_args(["--label", "x"])
    assert args.poll_timeout == cb._DEFAULT_POLL_TIMEOUT

    import inspect

    assert (
        inspect.signature(cb._poll).parameters["timeout"].default
        == cb._DEFAULT_POLL_TIMEOUT
    )


async def test_poll_respects_a_custom_timeout(monkeypatch):
    monkeypatch.setattr(cb.asyncio, "sleep", _no_sleep)

    class _Client:
        async def call_tool(self, name, args):
            return _Result({"status": "running"})

    with pytest.raises(cb.CaptureFailure, match=r"within 0\.5s"):
        await cb._poll(_Client(), "r1", timeout=0.5)


# ---------------------------------------------------------------------------
# MINOR 8 / New-4 -- stale .tmp files are swept, but only genuinely old ones.
# ---------------------------------------------------------------------------


def test_sweep_stale_tmp_files_removes_only_old_orphans(tmp_path):
    """MINOR New-4: an unconditional sweep would delete a SECOND capture()'s
    in-flight .tmp when both write into the same label directory (the
    module's own documented "re-run one case" resume workflow), making its
    os.replace raise FileNotFoundError. Only files older than the staleness
    threshold may be removed."""
    label_dir = tmp_path / "L"
    (label_dir / "v1").mkdir(parents=True)

    stale = label_dir / "v1" / "old__default.json.tmp"
    stale.write_text("{}")
    old_time = time.time() - cb._STALE_TMP_AGE_SECONDS - 10
    os.utime(stale, (old_time, old_time))

    live = label_dir / "v1" / "in_flight__default.json.tmp"
    live.write_text("{}")  # e.g. another capture() writing right now

    kept = label_dir / "v1" / "get_model_fit__default.json"
    kept.write_text("{}")

    cb.sweep_stale_tmp_files(label_dir)

    assert not stale.exists()
    assert live.exists()
    assert kept.exists()


# ---------------------------------------------------------------------------
# MINOR New-3 / IMPORTANT B -- the cancel branch reaps unconditionally, but a
# failed reap FAILS the case (deleted is payload here, unlike
# run_optimization_case's purely-cleanup reap).
# ---------------------------------------------------------------------------


async def test_lifecycle_cancel_reaps_even_when_the_poll_times_out(monkeypatch):
    monkeypatch.setattr(cb.asyncio, "sleep", _no_sleep)
    deleted_run_ids = []

    class _Client:
        async def call_tool(self, name, args):
            if name == "run_optimization":
                return _Result({"run_id": "r1"})
            if name == "cancel_optimization":
                return _Result({"run_id": "r1", "status": "canceled"})
            if name == "get_optimization_status":
                return _Result({"run_id": "r1", "status": "running"})  # never terminal
            if name == "delete_optimization":
                deleted_run_ids.append(args["run_id"])
                return _Result({"run_id": "r1", "deleted": True})
            raise AssertionError(name)

    with pytest.raises(cb.CaptureFailure, match="did not reach a terminal status"):
        await cb.run_lifecycle_case(
            _Client(), "m", "cancel", compute_tier=None, poll_timeout=0.5
        )

    assert deleted_run_ids == ["r1"]  # reaped despite the poll timeout


async def test_lifecycle_cancel_fails_the_case_when_the_reap_fails_after_a_successful_poll():
    """IMPORTANT B: `deleted` is part of THIS case's snapshotted payload
    (unlike run_optimization_case's purely-cleanup reap), so a failed
    delete_optimization must fail the case -- never be recorded as
    `deleted: null`."""

    class _Client:
        async def call_tool(self, name, args):
            if name == "run_optimization":
                return _Result({"run_id": "r1"})
            if name == "cancel_optimization":
                return _Result({"run_id": "r1", "status": "canceled"})
            if name == "get_optimization_status":
                return _Result({"run_id": "r1", "status": "completed"})
            if name == "delete_optimization":
                raise TimeoutError("reap blew up")
            raise AssertionError(name)

    with pytest.raises(cb.CaptureFailure, match="delete_optimization"):
        await cb.run_lifecycle_case(_Client(), "m", "cancel", compute_tier=None)


async def test_lifecycle_cancel_reap_failure_does_not_replace_the_primary_poll_error(
    monkeypatch,
):
    """The Minor 7 non-masking property, preserved for IMPORTANT B: when the
    poll ALSO failed, that original error must remain the reported cause --
    the reap's own failure must not overwrite it."""
    monkeypatch.setattr(cb.asyncio, "sleep", _no_sleep)

    class _Client:
        async def call_tool(self, name, args):
            if name == "run_optimization":
                return _Result({"run_id": "r1"})
            if name == "cancel_optimization":
                return _Result({"run_id": "r1", "status": "canceled"})
            if name == "get_optimization_status":
                raise TimeoutError("status blew up")
            if name == "delete_optimization":
                raise RuntimeError("reap also blew up")
            raise AssertionError(name)

    with pytest.raises(cb.CaptureFailure, match="status blew up"):
        await cb.run_lifecycle_case(
            _Client(), "m", "cancel", compute_tier=None, poll_timeout=0.5
        )


# ---------------------------------------------------------------------------
# CRITICAL, defence in depth -- --label must not escape out_root.
# ---------------------------------------------------------------------------


def test_validate_label_accepts_a_normal_label(tmp_path):
    assert cb.validate_label("v2.0-jax", tmp_path) == tmp_path / "v2.0-jax"


def test_validate_label_rejects_empty_slash_backslash_and_dotdot(tmp_path):
    for bad in ["", "a/b", "a\\b", "..", "../x", "x/..", "/abs/path"]:
        with pytest.raises(SystemExit, match="invalid --label"):
            cb.validate_label(bad, tmp_path)


def test_validate_label_rejects_a_single_dot_cleanly(tmp_path):
    """MINOR 2: "." passes the substring filter (it contains neither "/",
    "\\", nor ".."), so only the structural resolve() check catches it --
    and it used to be a bare `assert`, which (a) surfaces as an ugly
    AssertionError instead of a clean SystemExit/exit 1, and (b) vanishes
    entirely under `python -O`. This must raise SystemExit specifically,
    not AssertionError."""
    with pytest.raises(SystemExit, match="invalid --label"):
        cb.validate_label(".", tmp_path)


def test_validate_label_rejects_an_absolute_path(tmp_path):
    with pytest.raises(SystemExit, match="invalid --label"):
        cb.validate_label("/etc/passwd", tmp_path)


async def test_capture_rejects_an_empty_label(tmp_path):
    with pytest.raises(SystemExit, match="invalid --label"):
        await cb.capture(
            label="",
            transport="inprocess",
            url=None,
            variant_keys=["v1"],
            tools=None,
            cases_filter=None,
            compute_tier=None,
            force=False,
            out_root=tmp_path,
        )
    assert list(tmp_path.iterdir()) == []  # nothing touched


async def test_capture_rejects_a_dotdot_label(tmp_path):
    with pytest.raises(SystemExit, match="invalid --label"):
        await cb.capture(
            label="..",
            transport="inprocess",
            url=None,
            variant_keys=["v1"],
            tools=None,
            cases_filter=None,
            compute_tier=None,
            force=False,
            out_root=tmp_path,
        )


# ---------------------------------------------------------------------------
# capture() itself.
# ---------------------------------------------------------------------------


async def test_capture_recaptures_a_corrupt_or_empty_snapshot_instead_of_skipping(
    tmp_path, monkeypatch
):
    _patch_matrix(monkeypatch, ["v1"], [CASE])
    _patch_client(
        monkeypatch,
        _FakeCaptureClient(
            {"get_model_overview": _OVERVIEW_OK, "get_channel_summary": {"value": 42}}
        ),
    )
    path = cb.case_path(tmp_path, "L", "v1", CASE)
    path.parent.mkdir(parents=True)
    path.write_text("")  # truncated to zero bytes, as in the empirical repro
    stray_tmp = path.parent / "leftover.tmp"
    stray_tmp.write_text("junk")
    old_time = time.time() - cb._STALE_TMP_AGE_SECONDS - 10
    os.utime(stray_tmp, (old_time, old_time))  # old enough for the sweep (MINOR New-4)

    rc = await cb.capture(
        label="L",
        transport="inprocess",
        url=None,
        variant_keys=["v1"],
        tools=None,
        cases_filter=None,
        compute_tier=None,
        force=False,
        out_root=tmp_path,
        variants_selected=True,
    )

    assert rc == 0
    assert json.loads(path.read_text())["payload"] == {"value": 42}
    assert not stray_tmp.exists()  # MINOR 8, exercised through capture()


async def test_capture_recaptures_an_envelope_with_matching_env_but_no_payload(
    tmp_path, monkeypatch
):
    """MINOR 1: `0 recaptured, 2 skipped` was the reviewer's repro -- an
    envelope whose `capture_env` matches but has no `payload` key must not
    be a valid skip. Same failure class as CRITICAL 1."""
    _patch_matrix(monkeypatch, ["v1"], [CASE])
    monkeypatch.setattr(cb, "worker_env", lambda: {"MERIDIAN_BACKEND": "jax"})
    current_env = _env(cb.worker_env())
    path = cb.case_path(tmp_path, "L", "v1", CASE)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"capture_env": current_env}))  # no "payload" key

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

        async def call_tool(self, name, args):
            if name == "get_model_overview":
                return _Result(_OVERVIEW_OK)
            return _Result({"value": 42})

    _patch_client(monkeypatch, _Client())

    rc = await cb.capture(
        label="L",
        transport="inprocess",
        url=None,
        variant_keys=["v1"],
        tools=None,
        cases_filter=None,
        compute_tier=None,
        force=False,
        out_root=tmp_path,
        variants_selected=True,
    )
    assert rc == 0
    assert json.loads(path.read_text())["payload"] == {"value": 42}


async def test_capture_writes_no_manifest_for_a_genuine_variants_subset(
    tmp_path, monkeypatch
):
    _patch_matrix(monkeypatch, ["v1", "v2"], [CASE])
    _patch_client(
        monkeypatch,
        _FakeCaptureClient(
            {"get_model_overview": _OVERVIEW_OK, "get_channel_summary": {"ok": True}}
        ),
    )

    rc = await cb.capture(
        label="L",
        transport="inprocess",
        url=None,
        variant_keys=["v1"],  # a genuine subset of the known ["v1", "v2"]
        tools=None,
        cases_filter=None,
        compute_tier=None,
        force=False,
        out_root=tmp_path,
        variants_selected=True,
    )

    assert rc == 0
    assert not (tmp_path / "L" / "manifest.json").exists()


async def test_capture_writes_a_manifest_when_variants_names_every_fixture(
    tmp_path, monkeypatch
):
    """MINOR C, carried into Fix wave 4: --variants naming ALL known fixtures
    is a full-label run in substance -- unlike a genuine subset, it must NOT
    suppress the manifest."""
    _patch_matrix(monkeypatch, ["v1", "v2"], [CASE])
    _patch_client(
        monkeypatch,
        _FakeCaptureClient(
            {"get_model_overview": _OVERVIEW_OK, "get_channel_summary": {"ok": True}}
        ),
    )
    monkeypatch.setattr(cb, "build_manifest", lambda **kwargs: {"stub": True})

    rc = await cb.capture(
        label="L",
        transport="inprocess",
        url=None,
        variant_keys=["v1", "v2"],  # every known fixture, named explicitly
        tools=None,
        cases_filter=None,
        compute_tier=None,
        force=False,
        out_root=tmp_path,
        variants_selected=True,
    )

    assert rc == 0
    assert (tmp_path / "L" / "manifest.json").exists()


async def test_capture_fails_loudly_on_an_unknown_variant_name(tmp_path, monkeypatch):
    _patch_matrix(monkeypatch, ["national-revenue"], [CASE])

    with pytest.raises(SystemExit, match="TYPO-DOES-NOT-EXIST"):
        await cb.capture(
            label="L",
            transport="inprocess",
            url=None,
            variant_keys=["national-revenue", "TYPO-DOES-NOT-EXIST"],
            tools=None,
            cases_filter=None,
            compute_tier=None,
            force=False,
            out_root=tmp_path,
            variants_selected=True,
        )
    assert not (tmp_path / "L").exists()  # nothing narrowed, nothing written


async def test_capture_counts_failures_and_exits_nonzero(tmp_path, monkeypatch):
    _patch_matrix(monkeypatch, ["v1"], [CASE])
    _patch_client(
        monkeypatch,
        _FakeCaptureClient(
            {"get_model_overview": _OVERVIEW_OK}, raise_on={"get_channel_summary"}
        ),
    )

    rc = await cb.capture(
        label="L",
        transport="inprocess",
        url=None,
        variant_keys=["v1"],
        tools=None,
        cases_filter=None,
        compute_tier=None,
        force=False,
        out_root=tmp_path,
        variants_selected=True,
    )

    assert rc == 1
    assert not cb.case_path(tmp_path, "L", "v1", CASE).exists()
    assert not (tmp_path / "L" / "manifest.json").exists()


# ---------------------------------------------------------------------------
# Fix wave 4's central invariant -- environment identity is per-snapshot: a
# stale-environment snapshot is recaptured INDIVIDUALLY, disturbing nothing
# else in the label. No sidecar, no three-branch --force rule, no rmtree.
# ---------------------------------------------------------------------------


async def test_capture_skips_a_snapshot_whose_environment_still_matches(
    tmp_path, monkeypatch
):
    _patch_matrix(monkeypatch, ["v1"], [CASE])
    monkeypatch.setattr(cb, "worker_env", lambda: {"MERIDIAN_BACKEND": "jax"})
    current_env = _env(cb.worker_env())
    path = cb.case_path(tmp_path, "L", "v1", CASE)
    cb.write_snapshot(path, {"value": "original"}, capture_env=current_env)

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

        async def call_tool(self, name, args):
            if name == "get_model_overview":
                return _Result(_OVERVIEW_OK)
            raise AssertionError(f"case tool should not be called: {name}")

    _patch_client(monkeypatch, _Client())

    rc = await cb.capture(
        label="L",
        transport="inprocess",
        url=None,
        variant_keys=["v1"],
        tools=None,
        cases_filter=None,
        compute_tier=None,
        force=False,
        out_root=tmp_path,
        variants_selected=True,
    )
    assert rc == 0
    assert json.loads(path.read_text())["payload"] == {"value": "original"}


async def test_capture_recaptures_only_the_case_whose_snapshot_environment_is_stale(
    tmp_path, monkeypatch
):
    """The new invariant, directly: capture a case, change MERIDIAN_BACKEND,
    re-run WITHOUT --force -- that specific snapshot is recaptured, and NO
    OTHER case is disturbed. This is the per-file replacement for the old
    label-wide sidecar/three-branch-force/rmtree machinery."""
    case_a = ToolCase(
        "get_channel_summary", "roi", {"output_type": "roi"}, frozenset(), "service"
    )
    case_b = ToolCase(
        "get_channel_summary", "cpik", {"output_type": "cpik"}, frozenset(), "service"
    )
    _patch_matrix(monkeypatch, ["v1"], [case_a, case_b])

    calls = []

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

        async def call_tool(self, name, args):
            if name == "get_model_overview":
                return _Result(_OVERVIEW_OK)
            calls.append(args.get("output_type"))
            return _Result({"output_type": args.get("output_type"), "fresh": True})

    _patch_client(monkeypatch, _Client())

    a_path = cb.case_path(tmp_path, "L", "v1", case_a)
    b_path = cb.case_path(tmp_path, "L", "v1", case_b)

    stale_env = _env({"MERIDIAN_BACKEND": "tensorflow"})
    monkeypatch.setattr(cb, "worker_env", lambda: {"MERIDIAN_BACKEND": "jax"})
    current_env = _env(cb.worker_env())

    cb.write_snapshot(
        a_path, {"output_type": "roi", "stale": True}, capture_env=stale_env
    )
    cb.write_snapshot(
        b_path, {"output_type": "cpik", "current": True}, capture_env=current_env
    )

    rc = await cb.capture(
        label="L",
        transport="inprocess",
        url=None,
        variant_keys=["v1"],
        tools=None,
        cases_filter=None,
        compute_tier=None,
        force=False,
        out_root=tmp_path,
        variants_selected=True,
        allow_stale_recapture=True,  # CRITICAL C1: required now that a_path is stale_env
    )

    assert rc == 0
    # ONLY case_a's tool was actually called again -- case_b was never touched.
    assert calls == ["roi"]
    assert json.loads(a_path.read_text())["payload"]["fresh"] is True
    assert json.loads(b_path.read_text())["payload"] == {
        "output_type": "cpik",
        "current": True,
    }


# ---------------------------------------------------------------------------
# CRITICAL C1 (whole-branch review): recapturing a stale-environment snapshot
# permanently destroys the only record of how the OLD environment behaved
# (the v1.7-engine scenario: 330 snapshots recording google-meridian 1.7.0,
# unregenerable now that 1.7 is no longer installed). This must require an
# explicit, distinct opt-in -- --force is NOT enough, because --force's own
# meaning ("recapture regardless of validity/currency") predates this guard
# and a label can go stale with nobody ever typing --force at all.
# ---------------------------------------------------------------------------


def _stale_env_with_package_diff(fixture_root_variant: str = "v1") -> dict:
    """A hand-crafted capture_env whose ``packages`` disagrees with reality
    -- exactly the shape the real v1.7-engine snapshots have today (recorded
    google-meridian 1.7.0 / jax null against whatever this test machine
    actually has installed), so the guard's package-diff message has
    something real to report."""
    return {
        "transport": "inprocess",
        "python": cb.platform.python_version(),
        "packages": {"google-meridian": "1.7.0", "jax": None},
        "worker_env": {"MERIDIAN_BACKEND": "jax"},
        "fixture_hash": cb.fixture_content_hash(
            cb.DEFAULT_OUT_ROOT / fixture_root_variant
        ),
        "src_hash": cb.src_tree_hash(),
    }


async def test_capture_refuses_stale_env_recapture_without_explicit_opt_in(
    tmp_path, monkeypatch, capsys
):
    """The guard, directly: a stale-environment snapshot is left completely
    untouched and the run exits nonzero unless --allow-stale-recapture was
    given. The refusal message must name the label, the stale count, and
    which packages differ -- an operator staring at this output must be able
    to tell "I meant to do this" from "I nearly destroyed something"."""
    _patch_matrix(monkeypatch, ["v1"], [CASE])
    monkeypatch.setattr(cb, "worker_env", lambda: {"MERIDIAN_BACKEND": "jax"})
    path = cb.case_path(tmp_path, "v1.7-engine", "v1", CASE)
    cb.write_snapshot(
        path,
        {"value": "irreplaceable"},
        capture_env=_stale_env_with_package_diff(),
    )

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

        async def call_tool(self, name, args):
            if name == "get_model_overview":
                return _Result(_OVERVIEW_OK)
            raise AssertionError("the guard must refuse BEFORE any case tool is called")

    _patch_client(monkeypatch, _Client())

    rc = await cb.capture(
        label="v1.7-engine",
        transport="inprocess",
        url=None,
        variant_keys=["v1"],
        tools=None,
        cases_filter=None,
        compute_tier=None,
        force=False,
        out_root=tmp_path,
        variants_selected=True,
    )

    assert rc == 1
    # Nothing was touched: the on-disk snapshot is byte-for-byte the original.
    assert json.loads(path.read_text())["payload"] == {"value": "irreplaceable"}
    out = capsys.readouterr().out
    assert "REFUSED" in out
    assert "'v1.7-engine'" in out  # names the label
    assert "1 snapshot(s)" in out  # says how many are stale
    assert "google-meridian" in out and "1.7.0" in out  # says what changed
    assert "--allow-stale-recapture" in out


async def test_capture_force_alone_does_not_bypass_the_stale_env_guard(
    tmp_path, monkeypatch, capsys
):
    """--force means "redo everything, valid or not" (Fix wave 4) -- it is
    NOT an acknowledgement that irreplaceable old-environment evidence is
    about to be overwritten. The guard fires exactly the same whether
    --force was passed or not."""
    _patch_matrix(monkeypatch, ["v1"], [CASE])
    monkeypatch.setattr(cb, "worker_env", lambda: {"MERIDIAN_BACKEND": "jax"})
    path = cb.case_path(tmp_path, "v1.7-engine", "v1", CASE)
    cb.write_snapshot(
        path,
        {"value": "irreplaceable"},
        capture_env=_stale_env_with_package_diff(),
    )

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

        async def call_tool(self, name, args):
            if name == "get_model_overview":
                return _Result(_OVERVIEW_OK)
            raise AssertionError("the guard must refuse BEFORE any case tool is called")

    _patch_client(monkeypatch, _Client())

    rc = await cb.capture(
        label="v1.7-engine",
        transport="inprocess",
        url=None,
        variant_keys=["v1"],
        tools=None,
        cases_filter=None,
        compute_tier=None,
        force=True,  # <-- --force alone, no --allow-stale-recapture
        out_root=tmp_path,
        variants_selected=True,
    )

    assert rc == 1
    assert json.loads(path.read_text())["payload"] == {"value": "irreplaceable"}
    assert "REFUSED" in capsys.readouterr().out


async def test_capture_allow_stale_recapture_permits_the_guarded_recapture(
    tmp_path, monkeypatch
):
    """The explicit opt-in, given, lets the run through -- proving the
    refusal above is a real gate and not a permanent block."""
    _patch_matrix(monkeypatch, ["v1"], [CASE])
    monkeypatch.setattr(cb, "worker_env", lambda: {"MERIDIAN_BACKEND": "jax"})
    path = cb.case_path(tmp_path, "v1.7-engine", "v1", CASE)
    cb.write_snapshot(
        path,
        {"value": "old"},
        capture_env=_stale_env_with_package_diff(),
    )

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

        async def call_tool(self, name, args):
            if name == "get_model_overview":
                return _Result(_OVERVIEW_OK)
            return _Result({"value": "new"})

    _patch_client(monkeypatch, _Client())

    rc = await cb.capture(
        label="v1.7-engine",
        transport="inprocess",
        url=None,
        variant_keys=["v1"],
        tools=None,
        cases_filter=None,
        compute_tier=None,
        force=False,
        out_root=tmp_path,
        variants_selected=True,
        allow_stale_recapture=True,
    )

    assert rc == 0
    assert json.loads(path.read_text())["payload"] == {"value": "new"}


def test_describe_stale_env_names_the_differing_packages():
    old_env = {
        "transport": "inprocess",
        "python": "3.13.13",
        "packages": {"google-meridian": "1.7.0", "jax": None},
        "worker_env": {"MERIDIAN_BACKEND": "jax"},
        "fixture_hash": "h1",
        "src_hash": "s1",
    }
    new_env = {
        **old_env,
        "packages": {"google-meridian": "2.0.0", "jax": "0.11.1"},
    }
    description = cb.describe_stale_env(old_env, new_env)
    assert "packages differ" in description
    assert "google-meridian" in description
    assert "'1.7.0'" in description and "'2.0.0'" in description
    assert "jax" in description
    assert "None" in description and "'0.11.1'" in description


def test_describe_stale_env_falls_back_when_packages_match():
    """A rebuilt fixture or an edited src/ can make a snapshot stale_env
    with IDENTICAL packages -- the message must not claim packages differ
    when they didn't."""
    old_env = {
        "transport": "inprocess",
        "python": "3.13.13",
        "packages": {"google-meridian": "2.0.0"},
        "worker_env": {"MERIDIAN_BACKEND": "jax"},
        "fixture_hash": "h1",
        "src_hash": "s1",
    }
    new_env = {**old_env, "fixture_hash": "h2"}
    description = cb.describe_stale_env(old_env, new_env)
    assert "packages differ" not in description
    assert "fixture_hash changed" in description


def test_cli_parses_allow_stale_recapture_flag_default_false():
    args = cb._parse_args(["--label", "L"])
    assert args.allow_stale_recapture is False


def test_cli_parses_allow_stale_recapture_flag_when_given():
    args = cb._parse_args(["--label", "L", "--allow-stale-recapture"])
    assert args.allow_stale_recapture is True


async def test_capture_failure_in_one_case_does_not_disturb_an_unrelated_snapshot(
    tmp_path, monkeypatch
):
    """No wipe/rmtree exists any more: a failure in one case can only ever
    fail to write ITS OWN file. An existing, valid, current snapshot for a
    different case is left completely alone."""
    case_a = ToolCase(
        "get_channel_summary", "roi", {"output_type": "roi"}, frozenset(), "service"
    )
    case_b = ToolCase(
        "get_channel_summary", "cpik", {"output_type": "cpik"}, frozenset(), "service"
    )
    _patch_matrix(monkeypatch, ["v1"], [case_a, case_b])
    monkeypatch.setattr(cb, "worker_env", lambda: {"MERIDIAN_BACKEND": "jax"})
    current_env = _env(cb.worker_env())

    a_path = cb.case_path(tmp_path, "L", "v1", case_a)
    b_path = cb.case_path(tmp_path, "L", "v1", case_b)
    cb.write_snapshot(a_path, {"safe": True}, capture_env=current_env)

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

        async def call_tool(self, name, args):
            if name == "get_model_overview":
                return _Result(_OVERVIEW_OK)
            raise TimeoutError("case_b's tool blew up")

    _patch_client(monkeypatch, _Client())

    rc = await cb.capture(
        label="L",
        transport="inprocess",
        url=None,
        variant_keys=["v1"],
        tools=None,
        cases_filter=None,
        compute_tier=None,
        force=False,
        out_root=tmp_path,
        variants_selected=True,
    )

    assert rc == 1
    assert not b_path.exists()
    assert json.loads(a_path.read_text())["payload"] == {"safe": True}  # untouched


async def test_force_recaptures_a_snapshot_even_when_its_environment_still_matches(
    tmp_path, monkeypatch
):
    """--force is back to its plain meaning: recapture regardless of whether
    the existing snapshot is valid or current."""
    _patch_matrix(monkeypatch, ["v1"], [CASE])
    monkeypatch.setattr(cb, "worker_env", lambda: {"MERIDIAN_BACKEND": "jax"})
    current_env = _env(cb.worker_env())
    path = cb.case_path(tmp_path, "L", "v1", CASE)
    cb.write_snapshot(path, {"value": "first"}, capture_env=current_env)

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

        async def call_tool(self, name, args):
            if name == "get_model_overview":
                return _Result(_OVERVIEW_OK)
            return _Result({"value": "second"})

    _patch_client(monkeypatch, _Client())

    rc = await cb.capture(
        label="L",
        transport="inprocess",
        url=None,
        variant_keys=["v1"],
        tools=None,
        cases_filter=None,
        compute_tier=None,
        force=True,
        out_root=tmp_path,
        variants_selected=True,
    )
    assert rc == 0
    assert json.loads(path.read_text())["payload"] == {"value": "second"}


# ---------------------------------------------------------------------------
# MINOR 3 -- capture_env also carries a fixture content hash and a src/ tree
# hash, so a rebuilt fixture or an edited src/ mid-label invalidates exactly
# the snapshots affected, which the once-at-the-end manifest cannot see.
# ---------------------------------------------------------------------------


def test_tree_hash_changes_when_a_files_content_changes(tmp_path):
    (tmp_path / "a.txt").write_text("v1")
    h1 = cb._tree_hash(tmp_path)
    (tmp_path / "a.txt").write_text("v2")
    assert cb._tree_hash(tmp_path) != h1


def test_tree_hash_excludes_pycache(tmp_path):
    (tmp_path / "a.py").write_text("x = 1")
    pycache = tmp_path / "__pycache__"
    pycache.mkdir()
    (pycache / "a.cpython-312.pyc").write_bytes(b"whatever")
    h1 = cb._tree_hash(tmp_path, glob="*.py")
    (pycache / "a.cpython-312.pyc").write_bytes(b"totally different bytes")
    assert cb._tree_hash(tmp_path, glob="*.py") == h1  # pycache change ignored
    (tmp_path / "a.py").write_text("x = 2")
    assert cb._tree_hash(tmp_path, glob="*.py") != h1  # real source change caught


def test_tree_hash_missing_root_is_a_stable_sentinel(tmp_path):
    missing = tmp_path / "does-not-exist"
    assert cb._tree_hash(missing) == cb._tree_hash(missing)
    assert "missing" in cb._tree_hash(missing)


def test_fixture_content_hash_reuses_file_fingerprint_not_a_second_scheme(
    tmp_path, monkeypatch
):
    (tmp_path / "model.binpb").write_bytes(b"data")
    calls = []
    real_fingerprint = cb.file_fingerprint

    def _spy(path):
        calls.append(path)
        return real_fingerprint(path)

    monkeypatch.setattr(cb, "file_fingerprint", _spy)
    cb.fixture_content_hash(tmp_path)
    assert calls == [tmp_path / "model.binpb"]


def test_src_tree_hash_only_looks_at_py_files_under_the_server_package():
    """Deliberately not asserting a specific value (the real src/ tree
    changes over time) -- just that it is a real, non-sentinel hash, since
    the real google_meridian_mcp_server package exists in this repo."""
    h = cb.src_tree_hash()
    assert isinstance(h, str)
    assert "missing" not in h


async def test_capture_recaptures_when_the_fixture_content_changes(
    tmp_path, monkeypatch
):
    """Minor 3, directly: rebuilding a fixture (e.g. a re-fit mid-Phase-4)
    changes its content hash, which invalidates exactly the snapshots
    captured against it -- even though MERIDIAN_BACKEND and every tracked
    package are unchanged."""
    fixture_root = tmp_path / "fixtures"
    (fixture_root / "v1").mkdir(parents=True)
    model_file = fixture_root / "v1" / "model.binpb"
    model_file.write_bytes(b"original")
    monkeypatch.setattr(cb, "DEFAULT_OUT_ROOT", fixture_root)
    monkeypatch.setattr(cb, "src_tree_hash", lambda: "fixed-src-hash")
    monkeypatch.setattr(cb, "build_manifest", lambda **kwargs: {"stub": True})
    _patch_matrix(monkeypatch, ["v1"], [CASE])

    calls = []

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

        async def call_tool(self, name, args):
            if name == "get_model_overview":
                return _Result(_OVERVIEW_OK)
            calls.append(name)
            return _Result({"fresh": True})

    _patch_client(monkeypatch, _Client())
    label_root = tmp_path / "out"

    rc0 = await cb.capture(
        label="L",
        transport="inprocess",
        url=None,
        variant_keys=["v1"],
        tools=None,
        cases_filter=None,
        compute_tier=None,
        force=False,
        out_root=label_root,
        variants_selected=True,
    )
    assert rc0 == 0
    path = cb.case_path(label_root, "L", "v1", CASE)
    assert json.loads(path.read_text())["payload"] == {"fresh": True}

    # Rebuild the fixture: same bytes... no, DIFFERENT bytes, same path.
    model_file.write_bytes(b"rebuilt")
    calls.clear()

    rc1 = await cb.capture(
        label="L",
        transport="inprocess",
        url=None,
        variant_keys=["v1"],
        tools=None,
        cases_filter=None,
        compute_tier=None,
        force=False,
        out_root=label_root,
        variants_selected=True,
        allow_stale_recapture=True,  # CRITICAL C1: rebuilt fixture -> stale_env
    )
    assert rc1 == 0
    assert calls == ["get_channel_summary"]  # genuinely recaptured, not skipped


# ---------------------------------------------------------------------------
# MINOR 4 -- pre-flight visibility before an expensive capture, no gate.
# ---------------------------------------------------------------------------


async def test_capture_prints_a_preflight_summary_before_executing(
    tmp_path, monkeypatch, capsys
):
    case_a = ToolCase(
        "get_channel_summary", "roi", {"output_type": "roi"}, frozenset(), "service"
    )
    case_b = ToolCase(
        "get_channel_summary", "cpik", {"output_type": "cpik"}, frozenset(), "service"
    )
    _patch_matrix(monkeypatch, ["v1"], [case_a, case_b])
    monkeypatch.setattr(cb, "worker_env", lambda: {"MERIDIAN_BACKEND": "jax"})
    current_env = _env(cb.worker_env())

    a_path = cb.case_path(tmp_path, "L", "v1", case_a)
    cb.write_snapshot(a_path, {"ok": True}, capture_env=current_env)  # current
    # case_b has no file at all -> missing.

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

        async def call_tool(self, name, args):
            if name == "get_model_overview":
                return _Result(_OVERVIEW_OK)
            return _Result({"fresh": True})

    _patch_client(monkeypatch, _Client())

    rc = await cb.capture(
        label="L",
        transport="inprocess",
        url=None,
        variant_keys=["v1"],
        tools=None,
        cases_filter=None,
        compute_tier=None,
        force=False,
        out_root=tmp_path,
        variants_selected=True,
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert (
        "Pre-flight: 1 current, 0 stale, 1 missing -> 1 case(s) will be executed" in out
    )


async def test_capture_preflight_summary_notes_force_executes_everything(
    tmp_path, monkeypatch, capsys
):
    _patch_matrix(monkeypatch, ["v1"], [CASE])
    monkeypatch.setattr(cb, "worker_env", lambda: {"MERIDIAN_BACKEND": "jax"})
    current_env = _env(cb.worker_env())
    path = cb.case_path(tmp_path, "L", "v1", CASE)
    cb.write_snapshot(path, {"ok": True}, capture_env=current_env)

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

        async def call_tool(self, name, args):
            if name == "get_model_overview":
                return _Result(_OVERVIEW_OK)
            return _Result({"fresh": True})

    _patch_client(monkeypatch, _Client())

    rc = await cb.capture(
        label="L",
        transport="inprocess",
        url=None,
        variant_keys=["v1"],
        tools=None,
        cases_filter=None,
        compute_tier=None,
        force=True,
        out_root=tmp_path,
        variants_selected=True,
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert (
        "Pre-flight: 1 current, 0 stale, 0 missing -> 1 case(s) will be executed "
        "(--force: every case, regardless of currency)" in out
    )


class TestRefreshingBearerAuth:
    """Google-signed identity tokens expire after an hour; QUEUE_SMOKE budgets
    4-6 and opens its second HTTP client past that mark. These tests pin the
    three behaviors that keep a multi-hour gate alive."""

    @staticmethod
    def _header(auth):
        request = httpx2.Request("POST", "https://example.invalid/mcp")
        return next(auth.auth_flow(request)).headers["Authorization"]

    def test_fresh_token_is_sent_without_reminting(self):
        auth = cb.RefreshingBearerAuth(
            "initial", ttl_seconds=10_000, refresh_command="printf refreshed"
        )
        assert self._header(auth) == "Bearer initial"

    def test_stale_token_is_reminted_from_the_refresh_command(self):
        auth = cb.RefreshingBearerAuth(
            "initial", ttl_seconds=0, refresh_command="printf refreshed"
        )
        assert self._header(auth) == "Bearer refreshed"

    def test_failed_refresh_keeps_the_existing_token(self, capsys):
        auth = cb.RefreshingBearerAuth(
            "initial", ttl_seconds=0, refresh_command="exit 3"
        )
        assert self._header(auth) == "Bearer initial"
        assert "auth token refresh failed" in capsys.readouterr().out

    def test_empty_refresh_output_keeps_the_existing_token(self, capsys):
        auth = cb.RefreshingBearerAuth("initial", ttl_seconds=0, refresh_command="true")
        assert self._header(auth) == "Bearer initial"
        assert "produced no token" in capsys.readouterr().out

    def test_failed_refresh_does_not_reshell_on_every_request(self, monkeypatch):
        calls = []
        real_run = cb.subprocess.run

        def counting_run(*args, **kwargs):
            calls.append(args)
            return real_run(*args, **kwargs)

        monkeypatch.setattr(cb.subprocess, "run", counting_run)
        auth = cb.RefreshingBearerAuth(
            "initial", ttl_seconds=10_000, refresh_command="exit 3"
        )
        auth._minted_at = time.monotonic() - 20_000
        assert self._header(auth) == "Bearer initial"
        assert self._header(auth) == "Bearer initial"
        assert len(calls) == 1

    def test_http_client_uses_refreshing_auth_not_a_static_header(self, monkeypatch):
        monkeypatch.setenv("MCP_AUTH_TOKEN", "initial")
        client = cb.build_client("http", "https://example.invalid")
        transport = client.transport
        assert isinstance(transport.auth, cb.RefreshingBearerAuth)
        assert "Authorization" not in (transport.headers or {})

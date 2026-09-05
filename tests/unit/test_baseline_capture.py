"""Snapshot layout, atomic writes, resumability and failure accounting."""

import json
import os
import time
import types

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


async def _no_sleep(*_args, **_kwargs):
    return None


_OVERVIEW_OK = {"time": ["2024-01-01"]}


def test_snapshot_path_is_label_variant_tool_case(tmp_path):
    path = cb.case_path(tmp_path, "v1.7-engine", "geo-revenue", CASE)
    assert (
        path
        == tmp_path / "v1.7-engine" / "geo-revenue" / "get_channel_summary__roi.json"
    )


def test_write_snapshot_normalizes_volatile_fields(tmp_path):
    path = tmp_path / "a" / "b.json"
    cb.write_snapshot(path, {"run_id": "m-20260904T101500-a1b2c3", "rows": [[1.0]]})
    assert json.loads(path.read_text()) == {"run_id": "<run_id>", "rows": [[1.0]]}


def test_write_snapshot_leaves_no_temp_file_behind(tmp_path):
    path = tmp_path / "b.json"
    cb.write_snapshot(path, {"ok": True})
    assert sorted(p.name for p in tmp_path.iterdir()) == ["b.json"]


def test_write_snapshot_overwrites_in_place(tmp_path):
    path = tmp_path / "b.json"
    cb.write_snapshot(path, {"v": 1})
    cb.write_snapshot(path, {"v": 2})
    assert json.loads(path.read_text()) == {"v": 2}


def test_prepare_env_isolates_the_registry(tmp_path, monkeypatch):
    monkeypatch.setenv("RESULT_CACHE_ENABLED", "true")
    monkeypatch.setenv("OPTIMIZATION_RUNS_ROOT", "/somewhere/shared")
    cb.prepare_env(str(tmp_path / "runs"))
    assert os.environ["OPTIMIZATION_RUNS_ROOT"] == str(tmp_path / "runs")
    assert os.environ["RESULT_CACHE_ENABLED"] == "false"
    assert os.environ["PERSISTENCE_BACKEND"] == "local"
    assert os.environ["REGISTRY_BACKEND"] == "local"
    assert os.environ["OPTIMIZATION_ALLOWED_TIERS"] == "local"


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
    """Replaces a bare constant echo (asserted nothing behavioural; only
    failed pre-fix because the symbol was renamed). Asserts the actual CLI
    wiring: --poll-timeout's default resolves from the same constant _poll
    itself defaults to, so changing one without the other would be caught."""
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
# MINOR 8 -- stale .tmp files must be swept at the start of a label capture.
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
# capture() itself -- untested before this change (the TEST GAP finding).
# Each test below is written to FAIL against the pre-fix code.
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
    assert json.loads(path.read_text()) == {"value": 42}
    assert not stray_tmp.exists()  # MINOR 8, exercised through capture()


async def test_capture_writes_no_manifest_when_variants_selector_is_used(
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


async def test_capture_refuses_to_resume_a_label_after_the_environment_changed(
    tmp_path, monkeypatch
):
    # A second known fixture ("v2") that is never requested -- so
    # variant_keys=["v1"] below is a genuine PROPER SUBSET, not "--variants
    # naming every fixture" (MINOR C), which is intentionally exempt from
    # this guard and covered by its own dedicated test.
    _patch_matrix(monkeypatch, ["v1", "v2"], [CASE])
    _patch_client(
        monkeypatch,
        _FakeCaptureClient(
            {"get_model_overview": _OVERVIEW_OK, "get_channel_summary": {"ok": True}}
        ),
    )

    monkeypatch.setattr(cb, "worker_env", lambda: {"MERIDIAN_BACKEND": "tensorflow"})
    rc1 = await cb.capture(
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
    assert rc1 == 0
    assert (tmp_path / "L" / cb._ENV_SIDECAR_NAME).exists()

    # Same label, a second (different) case, but the backend changed underneath us.
    case2 = ToolCase("get_channel_summary", "cpik", {}, frozenset(), "service")
    _patch_matrix(monkeypatch, ["v1", "v2"], [case2])
    monkeypatch.setattr(cb, "worker_env", lambda: {"MERIDIAN_BACKEND": "jax"})

    rc2 = await cb.capture(
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
    assert rc2 == 1
    assert not cb.case_path(tmp_path, "L", "v1", case2).exists()

    # IMPORTANT New-1: --force alone does NOT override a mismatch when this
    # run is narrowed by a selector (here, variants_selected=True) -- see the
    # dedicated three-branch test below for the full rule.
    rc3 = await cb.capture(
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
    assert rc3 == 1
    assert not cb.case_path(tmp_path, "L", "v1", case2).exists()


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
# IMPORTANT New-1 -- --force must not launder a mixed-environment label
# through a selector; only a full, unfiltered --force may recapture it.
# ---------------------------------------------------------------------------


async def test_force_environment_override_requires_no_selector(tmp_path, monkeypatch):
    """Reproduces the reviewer's finding: force=True + cases_filter under a
    changed backend used to return rc 0 and rewrite the sidecar while an
    existing snapshot stayed captured under the OLD backend -- permanently
    laundering a mixed-environment label. Covers all three situations from
    the fix: force+selector (still refused), force+no selector (legitimate
    full recapture), and plain no-force (still refused afterwards, i.e. the
    full recapture did not leave the guard permanently open)."""
    _patch_matrix(monkeypatch, ["v1"], [CASE])
    _patch_client(
        monkeypatch,
        _FakeCaptureClient(
            {"get_model_overview": _OVERVIEW_OK, "get_channel_summary": {"ok": True}}
        ),
    )
    # A full (no-selector) run reaches the real manifest-building code, which
    # needs real fixtures on disk; stub it out since this test is only about
    # the environment guard, not manifest content.
    monkeypatch.setattr(cb, "build_manifest", lambda **kwargs: {"stub": True})
    monkeypatch.setattr(
        cb, "write_manifest", lambda label_dir, manifest: label_dir / "manifest.json"
    )

    monkeypatch.setattr(cb, "worker_env", lambda: {"MERIDIAN_BACKEND": "tensorflow"})
    rc0 = await cb.capture(
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
    assert rc0 == 0
    sidecar = tmp_path / "L" / cb._ENV_SIDECAR_NAME
    original_env = json.loads(sidecar.read_text())

    case2 = ToolCase("get_channel_summary", "cpik", {}, frozenset(), "service")
    _patch_matrix(monkeypatch, ["v1"], [case2])
    monkeypatch.setattr(cb, "worker_env", lambda: {"MERIDIAN_BACKEND": "jax"})

    # (1) --force WITH a selector (--cases): must still refuse, not launder.
    rc_selector = await cb.capture(
        label="L",
        transport="inprocess",
        url=None,
        variant_keys=["v1"],
        tools=None,
        cases_filter=["cpik"],
        compute_tier=None,
        force=True,
        out_root=tmp_path,
        variants_selected=True,
    )
    assert rc_selector == 1
    assert not cb.case_path(tmp_path, "L", "v1", case2).exists()
    assert json.loads(sidecar.read_text()) == original_env  # NOT laundered

    # (2) --force with NO selector at all: a legitimate whole-label recapture.
    rc_full = await cb.capture(
        label="L",
        transport="inprocess",
        url=None,
        variant_keys=["v1"],
        tools=None,
        cases_filter=None,
        compute_tier=None,
        force=True,
        out_root=tmp_path,
        variants_selected=False,
    )
    assert rc_full == 0
    assert cb.case_path(tmp_path, "L", "v1", case2).exists()
    new_env = json.loads(sidecar.read_text())
    assert new_env != original_env
    assert new_env["worker_env"]["MERIDIAN_BACKEND"] == "jax"

    # (3) No --force at all, under yet another environment: still refused --
    # the full recapture in (2) did not leave this guard permanently open.
    case3 = ToolCase("get_channel_summary", "marginal_roi", {}, frozenset(), "service")
    _patch_matrix(monkeypatch, ["v1"], [case3])
    monkeypatch.setattr(cb, "worker_env", lambda: {"MERIDIAN_BACKEND": "tensorflow"})
    rc_no_force = await cb.capture(
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
    assert rc_no_force == 1
    assert not cb.case_path(tmp_path, "L", "v1", case3).exists()


# ---------------------------------------------------------------------------
# MINOR New-2 -- a corrupt sidecar must refuse cleanly, not crash.
# ---------------------------------------------------------------------------


async def test_capture_refuses_on_a_corrupt_sidecar_instead_of_crashing(
    tmp_path, monkeypatch
):
    _patch_matrix(monkeypatch, ["v1"], [CASE])
    _patch_client(
        monkeypatch,
        _FakeCaptureClient(
            {"get_model_overview": _OVERVIEW_OK, "get_channel_summary": {"ok": True}}
        ),
    )
    label_dir = tmp_path / "L"
    label_dir.mkdir(parents=True)
    (label_dir / cb._ENV_SIDECAR_NAME).write_text("{oops")

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


async def test_force_with_no_selector_recovers_from_a_corrupt_sidecar(
    tmp_path, monkeypatch
):
    _patch_matrix(monkeypatch, ["v1"], [CASE])
    _patch_client(
        monkeypatch,
        _FakeCaptureClient(
            {"get_model_overview": _OVERVIEW_OK, "get_channel_summary": {"ok": True}}
        ),
    )
    monkeypatch.setattr(cb, "build_manifest", lambda **kwargs: {"stub": True})
    monkeypatch.setattr(
        cb, "write_manifest", lambda label_dir, manifest: label_dir / "manifest.json"
    )
    label_dir = tmp_path / "L"
    label_dir.mkdir(parents=True)
    (label_dir / cb._ENV_SIDECAR_NAME).write_text("{oops")

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
        variants_selected=False,
    )
    assert rc == 0
    assert cb.case_path(tmp_path, "L", "v1", CASE).exists()
    json.loads((label_dir / cb._ENV_SIDECAR_NAME).read_text())  # valid again


# ---------------------------------------------------------------------------
# MINOR New-3 -- the cancel branch must reap unconditionally, even when the
# post-cancel poll never reaches a terminal state.
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
    `deleted: null`. (Replaces a test that asserted exactly that null,
    encoding the defect as intended behaviour.)"""

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
# IMPORTANT A -- a forced full recapture that partially fails must not leave
# a complete-looking label mixing two environments.
# ---------------------------------------------------------------------------


async def test_force_full_recapture_wipes_the_label_so_partial_failure_cannot_mix_environments(
    tmp_path, monkeypatch
):
    """End-to-end reproduction of the reviewer's sequence: a tensorflow
    label, then a forced full recapture under jax where ONE case's tool call
    fails. Before the fix, the sidecar was rewritten to jax on the first
    successful snapshot of pass 1 while the failing case's tensorflow-era
    file stayed on disk (still valid JSON) -- so a later plain resume saw a
    matching sidecar, skipped the stale file as "already captured", and
    completed with a manifest: a complete-looking label secretly containing
    one tensorflow snapshot and one jax snapshot."""
    case_a = ToolCase(
        "get_channel_summary", "roi", {"output_type": "roi"}, frozenset(), "service"
    )
    case_b = ToolCase(
        "get_channel_summary", "cpik", {"output_type": "cpik"}, frozenset(), "service"
    )

    class _Client:
        def __init__(self, fail_output_type=None):
            self.fail_output_type = fail_output_type

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

        async def call_tool(self, name, args):
            if name == "get_model_overview":
                return _Result(_OVERVIEW_OK)
            if (
                name == "get_channel_summary"
                and args.get("output_type") == self.fail_output_type
            ):
                raise TimeoutError("blew up")
            return _Result({"output_type": args.get("output_type")})

    monkeypatch.setattr(cb, "build_manifest", lambda **kwargs: {"stub": True})

    _patch_matrix(monkeypatch, ["v1"], [case_a, case_b])
    a_path = cb.case_path(tmp_path, "L", "v1", case_a)
    b_path = cb.case_path(tmp_path, "L", "v1", case_b)
    manifest_path = tmp_path / "L" / "manifest.json"

    # Pass 0: establish the label under tensorflow -- both cases succeed.
    monkeypatch.setattr(cb, "worker_env", lambda: {"MERIDIAN_BACKEND": "tensorflow"})
    monkeypatch.setattr(cb, "build_client", lambda transport, url: _Client())
    rc0 = await cb.capture(
        label="L",
        transport="inprocess",
        url=None,
        variant_keys=["v1"],
        tools=None,
        cases_filter=None,
        compute_tier=None,
        force=False,
        out_root=tmp_path,
        variants_selected=False,
    )
    assert rc0 == 0
    assert a_path.exists() and b_path.exists()
    assert manifest_path.exists()

    # Pass 1: --force, NO selector, under jax -- case_b's tool call fails.
    monkeypatch.setattr(cb, "worker_env", lambda: {"MERIDIAN_BACKEND": "jax"})
    monkeypatch.setattr(
        cb, "build_client", lambda transport, url: _Client(fail_output_type="cpik")
    )
    rc1 = await cb.capture(
        label="L",
        transport="inprocess",
        url=None,
        variant_keys=["v1"],
        tools=None,
        cases_filter=None,
        compute_tier=None,
        force=True,
        out_root=tmp_path,
        variants_selected=False,
    )
    assert rc1 == 1
    # case_b's stale TENSORFLOW-era snapshot must NOT survive this pass.
    assert not b_path.exists()
    assert a_path.exists()  # the case that succeeded is genuinely jax-era
    sidecar = json.loads((tmp_path / "L" / cb._ENV_SIDECAR_NAME).read_text())
    assert sidecar["worker_env"]["MERIDIAN_BACKEND"] == "jax"
    assert not manifest_path.exists()  # incomplete: no manifest yet

    # Pass 2: plain resume (no --force). Must genuinely RECAPTURE case_b
    # under jax rather than skip a stale tensorflow file, and finish clean.
    monkeypatch.setattr(
        cb, "build_client", lambda transport, url: _Client(fail_output_type=None)
    )
    rc2 = await cb.capture(
        label="L",
        transport="inprocess",
        url=None,
        variant_keys=["v1"],
        tools=None,
        cases_filter=None,
        compute_tier=None,
        force=False,
        out_root=tmp_path,
        variants_selected=False,
    )
    assert rc2 == 0
    # b_path did not exist after pass 1 (asserted above) and exists now, so
    # this write is a genuine fresh capture under jax, never a leftover.
    assert b_path.exists()
    assert manifest_path.exists()


# ---------------------------------------------------------------------------
# MINOR C -- --variants naming every known fixture is not a narrowing
# selector for the --force override rule.
# ---------------------------------------------------------------------------


async def test_variants_naming_every_fixture_is_not_treated_as_a_selector(
    tmp_path, monkeypatch
):
    _patch_matrix(monkeypatch, ["v1", "v2"], [CASE])
    _patch_client(
        monkeypatch,
        _FakeCaptureClient(
            {"get_model_overview": _OVERVIEW_OK, "get_channel_summary": {"ok": True}}
        ),
    )
    monkeypatch.setattr(cb, "build_manifest", lambda **kwargs: {"stub": True})

    monkeypatch.setattr(cb, "worker_env", lambda: {"MERIDIAN_BACKEND": "tensorflow"})
    rc0 = await cb.capture(
        label="L",
        transport="inprocess",
        url=None,
        variant_keys=["v1", "v2"],  # --variants naming BOTH known fixtures
        tools=None,
        cases_filter=None,
        compute_tier=None,
        force=False,
        out_root=tmp_path,
        variants_selected=True,
    )
    assert rc0 == 0
    sidecar_path = tmp_path / "L" / cb._ENV_SIDECAR_NAME
    assert json.loads(sidecar_path.read_text())["worker_env"]["MERIDIAN_BACKEND"] == (
        "tensorflow"
    )

    # --force, backend changed, --variants STILL naming every known fixture:
    # in substance a full-label recapture, so --force must override, unlike
    # a genuine subset (covered by test_force_environment_override_requires_no_selector).
    case2 = ToolCase("get_channel_summary", "cpik", {}, frozenset(), "service")
    _patch_matrix(monkeypatch, ["v1", "v2"], [case2])
    monkeypatch.setattr(cb, "worker_env", lambda: {"MERIDIAN_BACKEND": "jax"})
    rc1 = await cb.capture(
        label="L",
        transport="inprocess",
        url=None,
        variant_keys=["v1", "v2"],
        tools=None,
        cases_filter=None,
        compute_tier=None,
        force=True,
        out_root=tmp_path,
        variants_selected=True,
    )
    assert rc1 == 0
    assert cb.case_path(tmp_path, "L", "v1", case2).exists()
    assert (
        json.loads(sidecar_path.read_text())["worker_env"]["MERIDIAN_BACKEND"] == "jax"
    )

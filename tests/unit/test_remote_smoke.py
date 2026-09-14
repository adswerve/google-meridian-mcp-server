from scripts.validation.remote_smoke import normalize_mcp_url


def test_appends_mcp_path_when_missing():
    assert normalize_mcp_url("https://x.run.app") == "https://x.run.app/mcp"


def test_strips_trailing_slash_from_existing_mcp_path():
    # A trailing slash triggers an insecure http:// redirect behind Cloud Run.
    assert normalize_mcp_url("https://x.run.app/mcp/") == "https://x.run.app/mcp"


def test_preserves_bare_mcp_path():
    assert normalize_mcp_url("https://x.run.app/mcp") == "https://x.run.app/mcp"


def test_strips_trailing_slash_before_appending():
    assert normalize_mcp_url("https://x.run.app/") == "https://x.run.app/mcp"


class _FakeTool:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeResult:
    """Mimics a FastMCP CallToolResult well enough for payloads.extract."""

    def __init__(self, payload):
        self.structured_content = payload


class _FakeClient:
    """Returns a canned run_optimization submit payload; every other tool
    returns the minimum shape remote_smoke needs to reach the submit."""

    def __init__(self, submit: dict) -> None:
        self._submit = submit
        self.calls: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def list_tools(self):
        return [
            _FakeTool(n)
            for n in (
                "list_models",
                "get_model_overview",
                "run_optimization",
                "get_optimization_status",
                "get_optimization_result",
            )
        ]

    async def call_tool(self, name: str, args: dict):
        self.calls.append(name)
        if name == "list_models":
            return _FakeResult([{"model_id": "national-revenue"}])
        if name == "get_model_overview":
            return _FakeResult({"model_id": "national-revenue"})
        if name == "run_optimization":
            return _FakeResult(self._submit)
        if name == "get_optimization_status":
            return _FakeResult({"status": "completed"})
        if name == "get_optimization_result":
            return _FakeResult({"summary": {}})
        raise AssertionError(f"unexpected tool {name}")


def _run_smoke(monkeypatch, submit: dict, compute_tier: str = "cloud_gpu"):
    import asyncio

    from scripts.validation import capture_baseline, remote_smoke

    client = _FakeClient(submit)
    monkeypatch.setattr(capture_baseline, "build_client", lambda *a, **k: client)
    rc = asyncio.run(
        remote_smoke._run(
            "https://x.run.app",
            "national-revenue",
            True,
            60,
            compute_tier,
            True,
        )
    )
    return rc, client


def test_fails_when_server_resolves_a_different_tier(monkeypatch):
    # config_fingerprint excludes compute_tier, so a cloud_gpu request can be
    # served by an existing cloud_cpu run. That must fail, not report PASS.
    rc, client = _run_smoke(
        monkeypatch, {"run_id": "r1", "compute_tier_resolved": "cloud_cpu"}
    )
    assert rc == 1
    assert "get_optimization_status" not in client.calls


def test_fails_when_the_run_was_reused(monkeypatch):
    rc, client = _run_smoke(
        monkeypatch,
        {"run_id": "r1", "compute_tier_resolved": "cloud_gpu", "reused": True},
    )
    assert rc == 1
    assert "get_optimization_status" not in client.calls


def test_passes_when_tier_matches_and_run_is_fresh(monkeypatch):
    rc, client = _run_smoke(
        monkeypatch,
        {"run_id": "r1", "compute_tier_resolved": "cloud_gpu", "reused": False},
    )
    assert rc == 0
    assert "get_optimization_result" in client.calls

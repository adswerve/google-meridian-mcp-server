from scripts.validation.remote_smoke import normalize_mcp_url


def test_appends_mcp_path_when_missing():
    assert normalize_mcp_url("https://x.run.app") == "https://x.run.app/mcp/"


def test_preserves_existing_mcp_path():
    assert normalize_mcp_url("https://x.run.app/mcp/") == "https://x.run.app/mcp/"


def test_strips_trailing_slash_before_appending():
    assert normalize_mcp_url("https://x.run.app/") == "https://x.run.app/mcp/"

"""Contract test: the bundled meridian-analyst skill is served and readable."""

from __future__ import annotations

import pytest
from fastmcp import Client

from google_meridian_mcp_server.server import create_server

SKILL_URI = "skill://meridian-analyst/SKILL.md"


@pytest.mark.asyncio
async def test_skill_resource_is_served_and_readable():
    mcp = create_server()
    async with Client(mcp) as client:
        uris = {str(r.uri) for r in await client.list_resources()}
        assert SKILL_URI in uris
        assert "skill://meridian-analyst/_manifest" in uris

        contents = await client.read_resource(SKILL_URI)
        text = contents[0].text
        assert "name: meridian-analyst" in text
        assert "description:" in text

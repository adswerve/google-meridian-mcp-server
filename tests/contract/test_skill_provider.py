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


FULL_FUNNEL_URI = "skill://meridian-analyst/references/full-funnel.md"


@pytest.mark.asyncio
async def test_full_funnel_reference_is_served_and_states_the_double_counting_rule():
    mcp = create_server()
    async with Client(mcp) as client:
        contents = await client.read_resource(FULL_FUNNEL_URI)
        flat = " ".join(contents[0].text.split())  # markdown wraps lines
        assert (
            "Never add the brand signal's full effect on top of the paid channels"
            in flat
        )
        assert "it belongs in that sum" in flat  # the rest row IS in the identity
        skill = (await client.read_resource(SKILL_URI))[0].text
        assert "references/full-funnel.md" in skill

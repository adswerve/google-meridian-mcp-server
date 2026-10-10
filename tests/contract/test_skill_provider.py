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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("uri", "kept"),
    [
        (
            "skill://meridian-analyst/references/budget-optimization.md",
            "the shares you name are kept exactly as given",
        ),
        (
            "skill://meridian-analyst/references/glossary.md",
            "the named shares are kept as given",
        ),
    ],
)
async def test_skill_states_the_planned_allocation_rule(uri, kept):
    """The skill must state the current planned-mix rule, never the old
    fill-with-spend-and-renormalise one."""
    mcp = create_server()
    async with Client(mcp) as client:
        contents = await client.read_resource(uri)
        flat = " ".join(contents[0].text.split())
        assert kept in flat
        assert "split among the channels left out in proportion to their" in flat
        assert "a left-out channel with no spend there gets 0" in flat
        assert "when its shares sum to 1 or more" in flat
        assert "every channel left out had no" in flat
        assert "renormalized" not in flat

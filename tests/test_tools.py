"""Tool surface: the live-format injection, and the search limit.

The injection reaches into two private SDK attributes (`_mcp_server`,
`_tool_manager`). It works because ToolManager.list_tools() hands back the live
Tool objects rather than copies — which is also why it can go wrong: appending to
a description that already carries the block would compound on every connect.
That is the regression these tests exist for, and the first thing to re-run when
the `mcp` pin in requirements.txt is bumped.
"""
import asyncio
import json

import httpx
import pytest
import respx
from conftest import ETAPI

FORMAT_TOOLS = ("create_note", "update_note")


@pytest.fixture(autouse=True)
def restore_tool_descriptions(server):
    """Descriptions live on the shared Tool objects, so leave them as found."""
    tools = server.mcp._tool_manager.list_tools()
    original = {t.name: t.description for t in tools}
    yield
    for tool in server.mcp._tool_manager.list_tools():
        tool.description = original[tool.name]


def list_tools(server):
    return asyncio.run(server._list_tools_with_live_format())


def described(tools, name):
    return next(t.description for t in tools if t.name == name)


def test_format_is_injected_into_the_authoring_tools(server, slide_type):
    tools = list_tools(server)

    for name in FORMAT_TOOLS:
        assert "One H1, then bullets." in described(tools, name), name


def test_other_tools_are_left_alone(server, slide_type):
    tools = list_tools(server)

    for name in ("get_note", "delete_note", "search_notes", "list_note_types"):
        assert "One H1, then bullets." not in described(tools, name), name


def test_repeated_list_tools_does_not_compound_the_block(server, slide_type):
    """Every client connect calls this; the block must not stack up."""
    first = described(list_tools(server), "create_note")
    latest = [described(list_tools(server), "create_note") for _ in range(4)][-1]

    assert latest == first
    assert latest.count("Note formats (live from Trilium") == 1


def test_an_unavailable_type_reaches_the_model_as_a_stop_notice(server, trilium):
    trilium.add_type("dupA", "Letter", "letter", "# A")
    trilium.add_type("dupB", "Letter", "letter", "# B")

    description = described(list_tools(server), "create_note")

    assert "AMBIGUOUS" in description
    assert "# A" not in description and "# B" not in description


def test_all_tools_are_still_listed(server, trilium):
    names = {t.name for t in list_tools(server)}

    assert names == {
        "list_note_types", "create_note", "get_note", "update_note",
        "attach_image", "get_note_info", "list_children", "clone_node",
        "move_node", "delete_note", "search_notes",
    }


def test_every_tool_has_a_description_and_a_schema(server, trilium):
    """A tool with no description is invisible to the model in practice."""
    for tool in list_tools(server):
        assert tool.description and tool.description.strip(), tool.name
        assert tool.inputSchema, tool.name


# ── search limit ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("requested,expected", [
    (20, 20),
    (1, 1),
    (200, 200),
    (201, 200),
    (99999, 200),      # an unbounded value would pull an arbitrary slice into context
    (0, 1),
    (-5, 1),
    ("garbage", 20),   # falls back to the default rather than raising
    (None, 20),
])
def test_search_limit_is_clamped(server, requested, expected):
    with respx.mock as mock:
        route = mock.get(f"{ETAPI}/notes").mock(
            return_value=httpx.Response(200, json={"results": []}))
        server._search("anything", limit=requested)
        assert route.calls.last.request.url.params["limit"] == str(expected)


def test_search_notes_returns_ids_and_titles(server, trilium):
    trilium.add_type("n1", "Slide Format", "slide", "# Slide")

    result = json.loads(server.search_notes("#notecastType"))

    assert result == [{"noteId": "n1", "title": "Slide Format"}]

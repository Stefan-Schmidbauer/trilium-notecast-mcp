"""Tool surface: the live-format injection, and the search limit.

The injection is a `ServerMiddleware` on `tools/list`. It mutates the serialised
result — the `{"tools": [...]}` dict the SDK builds fresh per request — not the
registered tools, so the block cannot compound across connections the way it
could when this reached into `_tool_manager` and edited live Tool objects.

These tests pin both halves: that the right tools carry the block (through the
middleware directly, for speed), and that the middleware is actually *in* the
request chain and fires on the real method name (end to end, through a client).
The second is what a bump of the `mcp` pin would break silently — a middleware
that never runs injects nothing, and every tool still lists fine.
"""
import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
import respx
from conftest import ETAPI

FORMAT_TOOLS = ("create_note", "update_note")


def list_tools(server):
    """Drive the middleware the way the dispatcher does, and hand back what a
    client would receive: the tools as wire-format dicts."""
    async def run():
        tools = await server.mcp.list_tools()
        payload = {"tools": [t.model_dump(by_alias=True, exclude_none=True) for t in tools]}

        async def call_next(_ctx):
            return payload

        ctx = SimpleNamespace(method="tools/list")
        return (await server._live_format_middleware(ctx, call_next))["tools"]

    return asyncio.run(run())


def described(tools, name):
    return next(t["description"] for t in tools if t["name"] == name)


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
    names = {t["name"] for t in list_tools(server)}

    assert names == {
        "list_note_types", "create_note", "get_note", "update_note",
        "attach_image", "get_note_info", "list_children", "clone_node",
        "move_node", "move_to_parent", "unlink_branch", "set_labels",
        "remove_label", "delete_note", "search_notes",
    }


def test_every_tool_has_a_description_and_a_schema(server, trilium):
    """A tool with no description is invisible to the model in practice."""
    for tool in list_tools(server):
        assert tool["description"] and tool["description"].strip(), tool["name"]
        assert tool["inputSchema"], tool["name"]


# ── the middleware is actually wired in ──────────────────────────────────────

def test_the_block_reaches_a_real_client(server, slide_type):
    """End to end, through the SDK's own dispatcher and a real ClientSession.

    The tests above call `_live_format_middleware` directly, so they would all
    still pass if `mcp.middleware.append(...)` were dropped or the SDK renamed
    the method to something other than "tools/list" — the injection would simply
    never run, and every tool would still list correctly. This is the test that
    fails in that case, which makes it the one to re-run when the `mcp` pin
    moves.
    """
    import anyio
    from mcp.client.session import ClientSession
    from mcp.shared.memory import create_client_server_memory_streams

    # `_lowlevel_server` is private, and deliberately used only here: the SDK
    # exposes no public way to run a server over a pair of streams (the public
    # entry points own a transport). Confining it to the test keeps the *server*
    # free of private API, which is the point of the middleware rewrite.
    lowlevel = server.mcp._lowlevel_server

    async def run():
        async with create_client_server_memory_streams() as ((cr, cw), (sr, sw)):
            async with anyio.create_task_group() as tg:
                tg.start_soon(
                    lambda: lowlevel.run(sr, sw, lowlevel.create_initialization_options()))
                async with ClientSession(cr, cw) as session:
                    await session.initialize()
                    first = await session.list_tools()
                    second = await session.list_tools()
                tg.cancel_scope.cancel()
        return first.tools, second.tools

    first, second = anyio.run(run)
    by_name = {t.name: t.description for t in first}

    assert "One H1, then bullets." in by_name["create_note"]
    assert "One H1, then bullets." in by_name["update_note"]
    assert "One H1, then bullets." not in by_name["get_note"]
    # A second connect must not stack a second copy on the first.
    assert {t.name: t.description for t in second} == by_name


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

    assert result == [{"noteId": "n1", "title": "Slide Format",
                       "type": "code", "dateModified": None}]

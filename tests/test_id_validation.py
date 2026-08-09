"""`_id()` — the guard between model-supplied arguments and the ETAPI URL path.

Every ETAPI request carries the API key, so an ID that alters the request path
reaches endpoints the tool never meant to touch. The README states this as a
property of the server; these tests are what make it a checked one.

The tests come in two layers on purpose:

* the unit layer pins the accepted alphabet;
* the HTTP layer asserts that no request is ever *sent* for a rejected ID —
  which is the actual promise, and the one that a refactor moving a call site
  past the guard would break while the unit layer still passed.
"""
import httpx
import pytest
import respx
from conftest import ETAPI

REJECTED = {
    "parent traversal": "../options",
    "traversal mid-path": "abc/../..",
    "path separator": "abc/def",
    "query truncation": "abc?limit=1",
    "fragment truncation": "abc#frag",
    "CR": "abc\r",
    # \Z rather than $ in the pattern: $ also matches before a trailing newline.
    "trailing LF": "abc\n",
    "header injection": "abc\r\nX-Injected: 1",
    "null byte": "abc\x00",
    "space": "abc def",
    "empty": "",
    "single dot": ".",
    "percent encoded": "abc%2f..",
    "non-ascii": "abcä",
    "backslash": "abc\\def",
    "absolute url": "http://evil.example/x",
}

ACCEPTED = [
    "root",          # the default parent
    "_hidden",       # built-in subtree roots use underscores
    "_options",
    "abc123XYZ",
    "AbC-123_xyz",   # branch ids join two note ids with "_"
    "a",
]


@pytest.mark.parametrize("value", REJECTED.values(), ids=list(REJECTED))
def test_rejects(server, value):
    with pytest.raises(ValueError):
        server._id(value)


@pytest.mark.parametrize("value", ACCEPTED)
def test_accepts(server, value):
    assert server._id(value) == value


def test_rejects_non_string(server):
    for value in (None, 12345, ["abc"]):
        with pytest.raises(ValueError):
            server._id(value)


# ── the promise, at the HTTP layer ───────────────────────────────────────────

# Every tool that puts an ID into a URL path, with a call that should never
# produce a request.
GUARDED_CALLS = {
    "get_note": lambda s, bad: s.get_note(bad),
    "get_note_info": lambda s, bad: s.get_note_info(bad),
    "list_children": lambda s, bad: s.list_children(bad),
    "delete_note": lambda s, bad: s.delete_note(bad),
    "update_note content": lambda s, bad: s.update_note(bad, content="x"),
    "update_note title": lambda s, bad: s.update_note(bad, title="x"),
    "move_node": lambda s, bad: s.move_node(bad, 1),
}


@pytest.mark.parametrize("call", GUARDED_CALLS.values(), ids=list(GUARDED_CALLS))
@pytest.mark.parametrize("bad", ["../options", "abc?x=1", "abc#f", "abc\r\nX: y"])
def test_no_request_is_sent_for_a_rejected_id(server, call, bad):
    with respx.mock(assert_all_called=False) as mock:
        catch_all = mock.route().mock(return_value=httpx.Response(200, json={}))
        with pytest.raises(ValueError):
            call(server, bad)
        assert not catch_all.called, f"a request escaped the guard for {bad!r}"


def test_valid_id_reaches_the_expected_path_with_the_api_key(server):
    with respx.mock as mock:
        route = mock.get(f"{ETAPI}/notes/abc123/content").mock(
            return_value=httpx.Response(200, text="the body"))
        assert server.get_note("abc123") == "the body"
        assert route.called
        assert route.calls.last.request.headers["authorization"] == "test-token"


def test_trailing_slash_in_trilium_url_does_not_double_up(server):
    assert not server.TRILIUM_URL.endswith("/")
    with respx.mock as mock:
        route = mock.get(f"{ETAPI}/notes/abc123").mock(
            return_value=httpx.Response(200, json={"noteId": "abc123"}))
        server._get("notes/abc123")
        assert "//etapi" not in str(route.calls.last.request.url).replace("http://", "")

"""Shared fixtures.

`server.py` reads every setting from the environment at module scope and raises
`EnvironmentError` without a token, so the environment has to be set up *before*
the import — not inside a fixture. That is why the assignments below sit at
module level and the import follows them.
"""
import json
import os
import pathlib
import sys

os.environ.setdefault("TRILIUM_API_KEY", "test-token")
os.environ.setdefault("TRILIUM_URL", "http://trilium.test:8080")

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402
import pytest  # noqa: E402
import respx  # noqa: E402

import server as _server  # noqa: E402

BASE = "http://trilium.test:8080"
ETAPI = f"{BASE}/etapi"


@pytest.fixture
def server():
    """The server module with its type caches cleared.

    Both caches are module-level and TTL-based, so without this a test that
    resolves a type would decide the outcome of the next one.

    `_base_descriptions` is deliberately *not* cleared: it holds the original
    tool descriptions, and clearing it while the Tool objects still carry a
    mutated description would make the mutated text the new baseline — the exact
    double-append that `test_tools.py` guards against.
    """
    _server._type_cache.clear()
    _server._type_block_cache.update(text=None, ts=0.0)
    return _server


class FakeTrilium:
    """A minimal in-memory ETAPI: enough notes, labels and content to drive the
    type resolution, and a call log so tests can assert on request *counts*.

    Routed through respx rather than by monkeypatching `_get` / `_search`, so
    the URL construction and the `Authorization` header are exercised too — the
    part that carries the security-relevant promises.
    """

    def __init__(self):
        self.notes: dict[str, dict] = {}
        self.content: dict[str, str] = {}
        self.search_calls: list[tuple[str, int]] = []
        self.note_calls: list[str] = []
        self.content_calls: list[str] = []
        self.created: list[dict] = []
        self.attributes_set: list[dict] = []
        self.attributes_patched: list[dict] = []
        # Attachments keyed by attachmentId. `content_writes` records the raw
        # body *and* the Content-Type of every write, because the Content-Type is
        # the part that decides whether Trilium stores the bytes or mangles them.
        self.attachments: dict[str, dict] = {}
        self.attachment_content: dict[str, bytes] = {}
        self.content_writes: list[tuple[str, str, bytes]] = []
        # Older Trilium answers a search with bare note stubs; flip this to
        # exercise the per-note fallback in `_type_notes`.
        self.search_returns_attributes = True

    def add_type(self, note_id: str, title: str, type_id: str, content: str, **mechanics: str):
        attributes = [{"type": "label", "name": "notecastType", "value": type_id}]
        attributes += [
            {"type": "label", "name": name, "value": value}
            for name, value in mechanics.items()
        ]
        self.notes[note_id] = {
            "noteId": note_id, "title": title, "type": "code",
            "mime": "text/x-markdown", "attributes": attributes, "childNoteIds": [],
        }
        self.content[note_id] = content
        return note_id

    def _matches(self, query: str) -> list[dict]:
        if query.startswith("#notecastType="):
            # Trilium reads a quoted value as a literal string; the server quotes
            # every type id because a bare `note` would be parsed as a search
            # identifier and match almost everything. Strip them the way the real
            # search does, so the mock accepts the query the server really sends.
            want = query.split("=", 1)[1].strip('"')
            return [
                n for n in self.notes.values()
                if any(a["name"] == "notecastType" and a["value"] == want for a in n["attributes"])
            ]
        if query == "#notecastType":
            return [
                n for n in self.notes.values()
                if any(a["name"] == "notecastType" for a in n["attributes"])
            ]
        return []

    def install(self, respx_mock):
        # Kept so a test can replace a route to simulate an ETAPI failure.
        self.router = respx_mock
        # Most specific first — respx returns the first matching route.
        respx_mock.get(path__regex=r"^/etapi/notes/(?P<nid>[^/]+)/content$").mock(
            side_effect=self._content_route)
        respx_mock.get(path__regex=r"^/etapi/notes/(?P<nid>[^/]+)/attachments$").mock(
            side_effect=self._note_attachments_route)
        respx_mock.get(path__regex=r"^/etapi/notes/(?P<nid>[^/]+)$").mock(
            side_effect=self._note_route)
        respx_mock.get(path="/etapi/notes").mock(side_effect=self._search_route)
        respx_mock.post(path="/etapi/create-note").mock(side_effect=self._create_route)
        respx_mock.post(path="/etapi/attributes").mock(side_effect=self._attribute_route)
        respx_mock.patch(path__regex=r"^/etapi/attributes/(?P<aid>[^/]+)$").mock(
            side_effect=self._attribute_patch_route)
        respx_mock.post(path="/etapi/attachments").mock(side_effect=self._attachment_create_route)
        respx_mock.put(path__regex=r"^/etapi/attachments/(?P<aid>[^/]+)/content$").mock(
            side_effect=self._attachment_content_route)
        return self

    def add_note(self, note_id: str, title: str = "A note", note_type: str = "code", **fields):
        """A plain note to hang attachments off — not a type definition."""
        self.notes[note_id] = {
            "noteId": note_id, "title": title, "type": note_type,
            "attributes": [], "childNoteIds": [], **fields,
        }
        self.content.setdefault(note_id, "")
        return note_id

    def _attachment_create_route(self, request):
        body = json.loads(request.content)
        attachment_id = f"att{len(self.attachments) + 1:02d}"
        self.attachments[attachment_id] = {"attachmentId": attachment_id, **body}
        return httpx.Response(201, json=self.attachments[attachment_id])

    def _attachment_content_route(self, request, aid):
        self.content_writes.append(
            (aid, request.headers.get("Content-Type", ""), request.content))
        self.attachment_content[aid] = request.content
        return httpx.Response(204)

    def _note_attachments_route(self, request, nid):
        return httpx.Response(200, json=[
            a for a in self.attachments.values() if a.get("ownerId") == nid
        ])

    def _create_route(self, request):
        body = json.loads(request.content)
        self.created.append(body)
        note_id = f"created{len(self.created):02d}"
        self.notes[note_id] = {
            "noteId": note_id, "title": body.get("title", ""),
            "type": body.get("type", "text"), "attributes": [], "childNoteIds": [],
        }
        self.content[note_id] = body.get("content", "")
        return httpx.Response(201, json={"note": {"noteId": note_id}, "branch": {}})

    def _attribute_route(self, request):
        body = json.loads(request.content)
        self.attributes_set.append(body)
        self.notes[body["noteId"]]["attributes"].append(
            {"type": "label", "name": body["name"], "value": body.get("value", ""),
             "attributeId": f"attr{len(self.attributes_set):02d}"})
        return httpx.Response(201, json={"attributeId": f"attr{len(self.attributes_set):02d}"})

    def _attribute_patch_route(self, request, aid):
        """ETAPI updates a label's value in place; the attributeId survives.

        `_set_attribute` takes this branch whenever the label already exists,
        which is how `create_note(labels=…)` overrides a type's
        #notecastApplyLabels default without leaving two labels of the same name
        behind. Without this route the double answered 404 and the override path
        was never exercised.
        """
        body = json.loads(request.content)
        for note in self.notes.values():
            for attr in note["attributes"]:
                if attr.get("attributeId") == aid:
                    attr["value"] = body.get("value", "")
                    self.attributes_patched.append({"attributeId": aid, **body})
                    return httpx.Response(200, json=attr)
        return httpx.Response(404, json={"message": f"no attribute {aid}"})

    def attributes_named(self, note_id: str, name: str) -> list[dict]:
        """Every label with this name — a dict view would hide a duplicate."""
        return [a for a in self.notes[note_id]["attributes"] if a["name"] == name]

    def labels_on(self, note_id: str) -> dict[str, str]:
        return {a["name"]: a["value"] for a in self.notes[note_id]["attributes"]}

    def _search_route(self, request):
        query = request.url.params.get("search", "")
        limit = int(request.url.params.get("limit", 20))
        self.search_calls.append((query, limit))
        hits = self._matches(query)[:limit]
        if self.search_returns_attributes:
            results = [dict(n) for n in hits]
        else:
            results = [{"noteId": n["noteId"], "title": n["title"]} for n in hits]
        return httpx.Response(200, json={"results": results})

    def _note_route(self, request, nid):
        self.note_calls.append(nid)
        if nid not in self.notes:
            return httpx.Response(404, json={"message": "not found"})
        return httpx.Response(200, json=self.notes[nid])

    def _content_route(self, request, nid):
        self.content_calls.append(nid)
        if nid not in self.content:
            return httpx.Response(404, text="")
        return httpx.Response(200, text=self.content[nid])


@pytest.fixture
def trilium():
    """A FakeTrilium wired into respx for the duration of the test."""
    with respx.mock(assert_all_called=False) as respx_mock:
        yield FakeTrilium().install(respx_mock)


@pytest.fixture
def slide_type(trilium):
    """One well-formed type definition — the happy path most tests build on."""
    trilium.add_type(
        "slideNote01", "Slide Format", "slide", "# Slide format\nOne H1, then bullets.",
        notecastTargetType="code", notecastMime="text/x-markdown",
        notecastPrefix="Folie", notecastApplyLabels="slideType=content",
    )
    return trilium

"""Note-type resolution — the part that decides what the model is told to write.

A `#notecastType` note's content becomes tool-description text, so it reaches the
model as *instructions*. The design accepts that anyone who can tag a note
defines a type; what it does not accept is a type resolving to the wrong note, or
to nothing while pretending otherwise. These tests pin the three outcomes:

* exactly one definition  → the live format, plus its mechanics;
* none                    → a loud "do not guess" notice, and nothing created;
* more than one           → a loud "ambiguous" notice, and nothing created.

The second half pins the request *count*. Discovery runs on every client
connect, and it used to fan out into a search plus two fetches per type.
"""
import json

import httpx
import pytest

# ── the three outcomes ───────────────────────────────────────────────────────

def test_unique_type_resolves_with_its_mechanics(server, slide_type):
    fmt, mechanics, source = server._fetch_type_definition("slide")

    assert source == "slideNote01"
    assert "One H1, then bullets." in fmt
    assert mechanics["target_type"] == "code"
    assert mechanics["mime"] == "text/x-markdown"
    assert mechanics["prefix"] == "Folie"
    assert mechanics["apply_labels"] == ["slideType=content"]


def test_missing_type_refuses_loudly(server, trilium):
    fmt, mechanics, source = server._fetch_type_definition("noSuchType")

    assert source is None and mechanics is None
    assert "UNAVAILABLE" in fmt and "DO NOT GUESS" in fmt


def test_duplicated_type_refuses_rather_than_picking_one(server, trilium):
    trilium.add_type("letterA", "Letter", "letter", "# Letter\nformat A")
    trilium.add_type("letterB", "Letter (copy)", "letter", "# Letter\nformat B")

    fmt, mechanics, source = server._fetch_type_definition("letter")

    assert source is None and mechanics is None
    assert "AMBIGUOUS" in fmt
    # Neither candidate's content may leak — picking silently is the failure mode.
    assert "format A" not in fmt and "format B" not in fmt


def test_type_with_empty_content_is_treated_as_unavailable(server, trilium):
    trilium.add_type("blankNote1", "Blank", "blank", "   \n  ")

    fmt, _mechanics, source = server._fetch_type_definition("blank")

    assert source is None
    assert "UNAVAILABLE" in fmt


@pytest.mark.parametrize("bad", ["../etc/passwd", "a b", "#label", "", "a=b", "x'y"])
def test_injection_shaped_type_id_never_reaches_a_search(server, trilium, bad):
    fmt, _mechanics, source = server._fetch_type_definition(bad)

    assert source is None and "UNAVAILABLE" in fmt
    assert trilium.search_calls == [], "a rejected type id must not be searched for"


def test_unreachable_etapi_degrades_to_the_notice(server, slide_type):
    """A type lookup that cannot reach Trilium must not raise out of the tool.

    The type exists here — so a passing test really does mean the *transport*
    failure was handled, not that the note happened to be missing.
    """
    slide_type.router.get(path="/etapi/notes").mock(
        side_effect=httpx.ConnectError("connection refused"))

    fmt, mechanics, source = server._fetch_type_definition("slide")

    assert source is None and mechanics is None
    assert "UNAVAILABLE" in fmt


def test_unreachable_etapi_does_not_cache_a_wrong_no_types(server, slide_type):
    slide_type.router.get(path="/etapi/notes").mock(
        side_effect=httpx.ConnectError("connection refused"))
    assert "No note types are defined yet" in server._build_type_block()

    # Trilium comes back — the next call must reflect reality, not the cache.
    slide_type.install(slide_type.router)
    assert "`slide`" in server._build_type_block()


# ── request economy ──────────────────────────────────────────────────────────

def test_type_block_uses_one_search_and_no_redundant_fetches(server, trilium):
    trilium.add_type("n1", "Slide", "slide", "# Slide")
    trilium.add_type("n2", "Letter", "letter", "# Letter")
    trilium.add_type("n3", "Note", "note", "# Note")

    block = server._build_type_block()

    assert trilium.search_calls == [("#notecastType", 100)]
    assert trilium.note_calls == [], "search carried attributes; no note refetch needed"
    assert sorted(trilium.content_calls) == ["n1", "n2", "n3"]
    for type_id in ("slide", "letter", "note"):
        assert f"`{type_id}`" in block


def test_search_without_attributes_falls_back_to_a_per_note_fetch(server, trilium):
    """Older Trilium answers a search with bare stubs — the fallback keeps it working."""
    trilium.add_type("n1", "Slide", "slide", "# Slide")
    trilium.search_returns_attributes = False

    block = server._build_type_block()

    assert trilium.note_calls == ["n1"]
    assert "`slide`" in block


def test_type_block_isolates_a_duplicate_from_the_healthy_types(server, trilium):
    trilium.add_type("n1", "Slide", "slide", "# Slide\nthe real format")
    trilium.add_type("dupA", "Letter", "letter", "# Letter A")
    trilium.add_type("dupB", "Letter", "letter", "# Letter B")

    block = server._build_type_block()

    assert "AMBIGUOUS" in block
    assert "the real format" in block, "one bad type must not suppress the good ones"
    assert "Letter A" not in block and "Letter B" not in block


def test_truncated_discovery_reverifies_each_type(server, trilium, monkeypatch):
    """A full result page means uniqueness cannot be concluded from the set.

    `_build_type_block` then falls back to the per-type targeted search — slower,
    but it is the lookup the ambiguity guard actually rests on.
    """
    monkeypatch.setattr(server, "_TYPE_SEARCH_LIMIT", 2)
    trilium.add_type("n1", "Slide", "slide", "# Slide")
    trilium.add_type("n2", "Letter", "letter", "# Letter")

    server._build_type_block()

    queries = [q for q, _limit in trilium.search_calls]
    assert queries[0] == "#notecastType"
    assert '#notecastType="slide"' in queries
    assert '#notecastType="letter"' in queries


def test_the_type_id_is_quoted_in_the_search_query(server, trilium):
    """Unquoted, Trilium parses the value as part of its search grammar.

    `#notecastType=note` reads `note` as an identifier and matches almost every
    note in the instance — so the registered type id `note` came back as more
    than one hit and was permanently "ambiguous", i.e. impossible to create.
    Measured against a live instance: 20 hits unquoted, 1 quoted.
    """
    trilium.add_type("n1", "Note", "note", "# Note")

    server._fetch_type_definition("note")

    assert [q for q, _limit in trilium.search_calls] == ['#notecastType="note"']


def test_untruncated_discovery_does_not_reverify(server, trilium):
    trilium.add_type("n1", "Slide", "slide", "# Slide")

    server._build_type_block()

    assert [q for q, _limit in trilium.search_calls] == ["#notecastType"]


def test_no_types_yields_the_setup_notice(server, trilium):
    block = server._build_type_block()
    assert "No note types are defined yet" in block


# ── caching ──────────────────────────────────────────────────────────────────

def test_successful_lookup_is_cached(server, slide_type):
    server._fetch_type_definition("slide")
    before = len(slide_type.search_calls) + len(slide_type.content_calls)
    server._fetch_type_definition("slide")
    assert len(slide_type.search_calls) + len(slide_type.content_calls) == before


def test_failed_lookup_is_not_cached(server, trilium):
    """A transient failure must recover on the next call, not stick for the TTL."""
    fmt, _m, source = server._fetch_type_definition("slide")
    assert source is None

    trilium.add_type("slideNote01", "Slide Format", "slide", "# Slide format")

    fmt, _m, source = server._fetch_type_definition("slide")
    assert source == "slideNote01" and "Slide format" in fmt


# ── the tools that sit on top ────────────────────────────────────────────────

def test_list_note_types_does_not_refetch_notes(server, trilium):
    trilium.add_type("n1", "Slide", "slide", "# Slide")
    trilium.add_type("n2", "Letter", "letter", "# Letter")

    result = json.loads(server.list_note_types())

    assert [r["noteType"] for r in result] == ["slide", "letter"]
    assert [r["noteId"] for r in result] == ["n1", "n2"]
    assert trilium.note_calls == []


def test_create_note_applies_the_type_mechanics(server, slide_type):
    result = json.loads(server.create_note("slide", "Folie 1", "# Hello"))

    body = slide_type.created[0]
    assert body["type"] == "code"
    assert body["mime"] == "text/x-markdown"
    assert body["prefix"] == "Folie"
    assert body["title"] == "Folie 1"

    labels = slide_type.labels_on(result["noteId"])
    # The instance marker is a *different* label than the type marker on purpose:
    # tagging instances with #notecastType would collide with the type lookup.
    assert labels["notecastInstance"] == "slide"
    assert labels["slideType"] == "content"
    assert "notecastType" not in labels


def test_create_note_honours_the_types_default_parent(server, trilium):
    trilium.add_type("n1", "Report", "report", "# Report", notecastParent="parent123")

    server.create_note("report", "Q3", "# Q3")

    assert trilium.created[0]["parentNoteId"] == "parent123"


def test_explicit_parent_wins_over_the_type_default(server, trilium):
    trilium.add_type("n1", "Report", "report", "# Report", notecastParent="parent123")

    server.create_note("report", "Q3", "# Q3", parent_note_id="explicit9")

    assert trilium.created[0]["parentNoteId"] == "explicit9"


@pytest.mark.parametrize("setup,marker", [
    ("missing", "UNAVAILABLE"),
    ("duplicate", "AMBIGUOUS"),
])
def test_create_note_creates_nothing_when_the_type_does_not_resolve(
    server, trilium, setup, marker
):
    if setup == "duplicate":
        trilium.add_type("a", "Letter", "letter", "# A")
        trilium.add_type("b", "Letter", "letter", "# B")

    out = server.create_note("letter", "Title", "body")

    assert marker in out
    assert trilium.created == [], "a STOP notice must not be accompanied by a note"
    assert trilium.attributes_set == []


# ── create_note(labels=…) ────────────────────────────────────────────────────
# The type's #notecastApplyLabels is one fixed value per type, so a type whose
# id does not distinguish its variants was unreachable through the MCP: `slide`
# defines title / content / chapter, the type stamps `content`, and there was no
# way to author the other two. `labels` is that way, and it has to win over the
# type default rather than sit beside it — two `slideType` labels on one note
# would make the rendered layout depend on attribute order.

def test_labels_override_the_types_apply_labels(server, slide_type):
    result = json.loads(
        server.create_note("slide", "Titel", "# Titel", labels={"slideType": "title"}))

    assert slide_type.labels_on(result["noteId"])["slideType"] == "title"
    # Patched in place rather than added beside the type's default: two
    # `slideType` labels would make the layout depend on attribute order.
    assert len(slide_type.attributes_named(result["noteId"], "slideType")) == 1
    assert slide_type.attributes_patched != []


def test_labels_add_alongside_the_type_defaults(server, slide_type):
    result = json.loads(
        server.create_note("slide", "Folie", "# Folie", labels={"notecastIgnore": ""}))

    labels = slide_type.labels_on(result["noteId"])
    assert labels["notecastIgnore"] == ""
    assert labels["slideType"] == "content"     # untouched
    assert labels["notecastInstance"] == "slide"


def test_omitting_labels_changes_nothing(server, slide_type):
    result = json.loads(server.create_note("slide", "Folie", "# Folie"))

    assert slide_type.labels_on(result["noteId"])["slideType"] == "content"


@pytest.mark.parametrize("name", ["notecastInstance", "notecastType"])
def test_reserved_labels_are_refused_and_nothing_is_created(server, slide_type, name):
    out = server.create_note("slide", "Folie", "# Folie", labels={name: "letter"})

    assert "RESERVED" in out
    assert slide_type.created == []


@pytest.mark.parametrize("name", ["slide:type", "slide type", "slide-type", "", "a.b"])
def test_invalid_label_names_are_refused_and_nothing_is_created(server, slide_type, name):
    out = server.create_note("slide", "Folie", "# Folie", labels={name: "x"})

    assert "INVALID LABEL NAME" in out
    assert slide_type.created == []


def test_a_non_string_value_is_refused(server, slide_type):
    out = server.create_note("slide", "Folie", "# Folie", labels={"slideType": 3})

    assert "NON-STRING VALUE" in out
    assert slide_type.created == []


def test_labels_are_validated_before_the_type_is_looked_up(server, trilium):
    """A bad mapping must not cost an ETAPI round trip — and must not half-create."""
    out = trilium and server.create_note(
        "nosuchtype", "X", "# X", labels={"bad name": "x"})

    assert "INVALID LABEL NAME" in out
    assert trilium.created == []

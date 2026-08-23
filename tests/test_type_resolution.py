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


# ── move_node ────────────────────────────────────────────────────────────────
# `new_position` promised an index and delivered `index * 10` written straight
# onto the branch. Since Trilium spaces siblings ten apart, every reachable
# value collided with a sibling already sitting there, and what came out of the
# tie was Trilium's choice. Measured on a live deck: 25 — meant as "between 20
# and 30" — became 250 and moved the note to the end. These tests pin the index
# down as an index.

@pytest.fixture
def deck(trilium):
    """Seven siblings at 10, 20 … 70 — Trilium's own spacing."""
    trilium.add_children("deck", "a", "b", "c", "d", "e", "f", "g")
    return trilium


@pytest.mark.parametrize("index,expected", [
    (0, ["g", "a", "b", "c", "d", "e", "f"]),
    (2, ["a", "b", "g", "c", "d", "e", "f"]),
    (6, ["a", "b", "c", "d", "e", "f", "g"]),
])
def test_move_node_puts_the_note_at_that_index(server, deck, index, expected):
    server.move_node("deck_g", index)

    assert deck.order_under("deck") == expected


def test_move_node_inserts_between_two_neighbours(server, deck):
    """The case the old arithmetic could not express at all."""
    server.move_node("deck_f", 1)

    assert deck.order_under("deck") == ["a", "f", "b", "c", "d", "e", "g"]


def test_positions_stay_ten_apart_after_a_move(server, deck):
    server.move_node("deck_e", 0)

    assert deck.positions_under("deck") == [10, 20, 30, 40, 50, 60, 70]


@pytest.mark.parametrize("index,expected_index", [(-5, 0), (99, 6)])
def test_out_of_range_indexes_clamp(server, deck, index, expected_index):
    result = json.loads(server.move_node("deck_c", index))

    assert result["newPosition"] == expected_index
    assert deck.order_under("deck").index("c") == expected_index


def test_a_move_that_changes_nothing_writes_nothing(server, deck):
    """`c` is already third; renumbering it to the same places must not PATCH."""
    result = json.loads(server.move_node("deck_c", 2))

    assert result["renumbered"] == 0
    assert deck.branches_patched == []


def test_only_the_branches_that_shift_are_written(server, deck):
    """Moving the last note to the front shifts every sibling — but no more."""
    result = json.loads(server.move_node("deck_g", 0))

    assert result["renumbered"] == 7
    assert {p["branchId"] for p in deck.branches_patched} == {
        f"deck_{n}" for n in "abcdefg"}


# ── list_children: naming the branch ─────────────────────────────────────────
# Reordering and unlinking both address a *placement*, not a note, so they need
# a branchId. Getting one used to mean a `get_note_info` per child on top of the
# listing. The id is derivable instead: parent and child each report the branches
# they sit on, and the one they agree on is the branch joining them.

def test_list_children_names_the_branch_of_each_child(server, trilium):
    trilium.add_children("deck", "a", "b", "c")

    children = json.loads(server.list_children("deck"))

    assert [c["branchId"] for c in children] == ["deck_a", "deck_b", "deck_c"]


def test_a_cloned_note_reports_the_branch_of_the_parent_being_listed(server, trilium):
    """The case a note id alone cannot express: one slide in two decks.

    Both listings are of the same note; handing back the same branch for both
    would make `move_node` reorder whichever deck Trilium happened to name first.
    """
    trilium.add_children("master", "slide")
    trilium.add_children("training", "slide")

    under_master = json.loads(server.list_children("master"))
    under_training = json.loads(server.list_children("training"))

    assert under_master[0]["branchId"] == "master_slide"
    assert under_training[0]["branchId"] == "training_slide"


def test_naming_the_branch_costs_no_extra_request(server, trilium):
    """One GET for the parent, one per child — the count before this field existed."""
    trilium.add_children("deck", "a", "b", "c")
    trilium.note_calls.clear()

    server.list_children("deck")

    assert trilium.note_calls == ["deck", "a", "b", "c"]


def test_children_still_list_when_the_instance_omits_branch_ids(server, trilium):
    """An ETAPI that reports no branch fields must not cost us the listing."""
    trilium.add_children("deck", "a")
    trilium.notes["deck"].pop("childBranchIds")
    trilium.notes["a"].pop("parentBranchIds")

    children = json.loads(server.list_children("deck"))

    assert children[0]["branchId"] is None
    assert children[0]["title"] == "a"


# ── search_notes: type and age ───────────────────────────────────────────────

def test_search_reports_type_and_date_modified(server, trilium):
    trilium.add_type("s1", "A slide", "slide", "# A")
    trilium.notes["s1"]["dateModified"] = "2026-03-31 11:36:58.293+0000"

    hits = json.loads(server.search_notes("#notecastType=slide"))

    assert hits[0]["type"] == "code"
    assert hits[0]["dateModified"] == "2026-03-31 11:36:58.293+0000"


def test_search_tolerates_an_instance_that_answers_with_bare_stubs(server, trilium):
    """Older Trilium returns noteId and title only; the extra fields go null,
    and the caller still gets its hits."""
    trilium.add_type("s1", "A slide", "slide", "# A")
    trilium.search_returns_attributes = False

    hits = json.loads(server.search_notes("#notecastType=slide"))

    assert hits == [{"noteId": "s1", "title": "A slide",
                     "type": None, "dateModified": None}]


# ── move_to_parent ───────────────────────────────────────────────────────────
# Trilium has no "change the parent" call: placement lives on a branch and
# `PATCH /branches` cannot rewrite parentNoteId. So a move is a create plus a
# delete, and the guards below all exist because the delete half is destructive
# when it lands on the wrong branch — or on the only one.

def test_move_to_parent_refiles_the_note(server, trilium):
    trilium.add_children("master", "slide")
    trilium.add_children("training")

    result = json.loads(server.move_to_parent("slide", "master", "training"))

    assert trilium.order_under("master") == []
    assert trilium.order_under("training") == ["slide"]
    assert result["removedBranchId"] == "master_slide"


def test_the_new_placement_is_written_before_the_old_one_is_removed(server, trilium):
    """The reverse order can leave the note with no placement at all — which is
    how Trilium spells "deleted"."""
    trilium.add_children("master", "slide")
    trilium.add_children("training")

    server.move_to_parent("slide", "master", "training")

    assert trilium.branches_created and trilium.branches_deleted
    assert trilium.branches_deleted == ["master_slide"]


def test_other_placements_of_a_clone_survive_the_move(server, trilium):
    """One slide in three places; moving one of them must not disturb the rest."""
    trilium.add_children("master", "slide")
    trilium.add_children("courseA", "slide")
    trilium.add_children("courseB")

    server.move_to_parent("slide", "courseA", "courseB")

    assert trilium.order_under("master") == ["slide"]
    assert trilium.order_under("courseA") == []
    assert trilium.order_under("courseB") == ["slide"]


def test_position_places_the_note_among_its_new_siblings(server, trilium):
    trilium.add_children("master", "slide")
    trilium.add_children("training", "a", "b", "c")

    server.move_to_parent("slide", "master", "training", position=1)

    assert trilium.order_under("training") == ["a", "slide", "b", "c"]


def test_a_move_from_a_parent_the_note_is_not_under_is_refused(server, trilium):
    trilium.add_children("master", "slide")
    trilium.add_children("elsewhere")
    trilium.add_children("training")

    result = server.move_to_parent("slide", "elsewhere", "training")

    assert "NOT PLACED THERE" in result
    assert trilium.branches_deleted == []
    assert trilium.order_under("master") == ["slide"]


def test_a_move_onto_the_same_parent_is_refused(server, trilium):
    """The measured upsert makes this the dangerous one: creating the branch
    returns the existing id, so deleting "the old one" would delete the only
    placement the note has."""
    trilium.add_children("master", "slide")

    result = server.move_to_parent("slide", "master", "master")

    assert "SAME PARENT" in result
    assert trilium.branches_deleted == []
    assert trilium.order_under("master") == ["slide"]


def test_a_move_into_the_notes_own_subtree_is_refused(server, trilium):
    """Would detach the whole subtree from the root while leaving it alive."""
    trilium.add_children("root", "chapter")
    trilium.add_children("chapter", "section")

    result = server.move_to_parent("chapter", "root", "section")

    assert "OWN SUBTREE" in result
    assert trilium.branches_deleted == []


def test_a_move_onto_itself_is_refused(server, trilium):
    trilium.add_children("root", "chapter")

    result = server.move_to_parent("chapter", "root", "chapter")

    assert "OWN SUBTREE" in result
    assert trilium.branches_deleted == []


# ── unlink_branch ────────────────────────────────────────────────────────────

def test_unlink_removes_one_placement_and_keeps_the_others(server, trilium):
    trilium.add_children("master", "slide")
    trilium.add_children("training", "slide")

    result = json.loads(server.unlink_branch("training_slide"))

    assert trilium.order_under("training") == []
    assert trilium.order_under("master") == ["slide"]
    assert result["remainingPlacements"] == 1
    assert "slide" in trilium.notes


def test_unlinking_the_last_placement_is_refused(server, trilium):
    """Trilium deletes a note that has no placement left. Without this guard the
    same call unfiles sometimes and destroys other times, and nothing at the
    call site tells the two apart."""
    trilium.add_children("master", "slide")

    result = server.unlink_branch("master_slide")

    assert "ONLY PLACEMENT" in result
    assert "delete_note" in result
    assert trilium.branches_deleted == []
    assert trilium.order_under("master") == ["slide"]


# ── set_labels / remove_label ────────────────────────────────────────────────

def test_set_labels_adds_a_label_to_an_existing_note(server, trilium):
    trilium.add_note("slide", title="A slide")

    server.set_labels("slide", {"reviewStatus": "outdated"})

    assert trilium.attributes_set[-1]["name"] == "reviewStatus"
    assert trilium.attributes_set[-1]["value"] == "outdated"


def test_setting_a_label_that_exists_updates_it_instead_of_duplicating(server, trilium):
    trilium.add_note("slide", title="A slide")
    server.set_labels("slide", {"reviewStatus": "outdated"})

    server.set_labels("slide", {"reviewStatus": "current"})

    labels = [a for a in trilium.notes["slide"]["attributes"] if a["name"] == "reviewStatus"]
    assert len(labels) == 1
    assert labels[0]["value"] == "current"


def test_reserved_labels_are_refused_here_too(server, trilium):
    trilium.add_note("slide", title="A slide")

    result = server.set_labels("slide", {"notecastType": "slide"})

    assert "RESERVED" in result
    assert trilium.attributes_set == []


def test_an_inherited_label_is_not_edited_where_it_shows_up(server, trilium):
    """A note reports what it inherits alongside what it owns. Patching the
    inherited one would rewrite it on the note it comes from — for every note
    inheriting it, silently."""
    trilium.add_note("slide", title="A slide")
    trilium.notes["slide"]["attributes"].append({
        "type": "label", "name": "reviewStatus", "value": "current",
        "noteId": "template", "attributeId": "inherited01",
    })

    server.set_labels("slide", {"reviewStatus": "outdated"})

    assert trilium.attributes_patched == []
    assert trilium.attributes_set[-1]["noteId"] == "slide"


def test_remove_label_deletes_the_notes_own_label(server, trilium):
    trilium.add_note("slide", title="A slide")
    server.set_labels("slide", {"reviewStatus": "outdated"})

    result = json.loads(server.remove_label("slide", "reviewStatus"))

    assert result["removed"] == 1
    assert trilium.notes["slide"]["attributes"] == []


def test_removing_a_label_that_is_not_there_is_not_an_error(server, trilium):
    trilium.add_note("slide", title="A slide")

    result = json.loads(server.remove_label("slide", "reviewStatus"))

    assert result["removed"] == 0
    assert trilium.attributes_deleted == []


def test_remove_label_leaves_an_inherited_label_alone(server, trilium):
    """It belongs to the note it comes from; deleting it there strips it from
    every note that inherits it."""
    trilium.add_note("slide", title="A slide")
    trilium.notes["slide"]["attributes"].append({
        "type": "label", "name": "reviewStatus", "value": "current",
        "noteId": "template", "attributeId": "inherited01",
    })

    result = json.loads(server.remove_label("slide", "reviewStatus"))

    assert result["removed"] == 0
    assert trilium.attributes_deleted == []

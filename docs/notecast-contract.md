# Notecast — Shared Label Contract

Status: **implemented on all three sides** — the binding agreement between the
repos below. The `trilium-notecast-mcp` server (this repo) implements its half,
the Slide Format note in Trilium carries the full `#notecast…` type definition,
and `trilium-notecast-render` reads `#notecastInstance` / `#notecastTheme` /
`#notecastIgnore` and ships the seven document types. What is left is
publication, not implementation — see Migration.

## What Notecast is

Notecast is a family of three Trilium tools that all read and write the **same
notes** but each does exactly one job:

| Repo | Role | Reads | Writes |
| --- | --- | --- | --- |
| `trilium-notecast-mcp` (this repo) | **Authoring** — an AI assistant creates/modifies typed notes via ETAPI | type definitions | notes of a type |
| `trilium-presenter-plugin` (public) | **Presenting** — renders a subtree as an on-screen slide deck | slide notes + themes | — |
| `trilium-notecast-render` | **Rendering** — renders a marked note to print/PDF in a chosen theme | notes + themes | — |

The glue between them is a set of Trilium labels sharing the `notecast` prefix.
`notecast` is **only a label namespace and a family name** — it is not required
to appear in every repo's name. The public presenter repo keeps its established
name (it *is* the presentation specialist) and simply reads `#notecast…` labels.

### A note on generality

The MCP server started life as a slide/presentation tool. Notecast generalises
that: a "type" is no longer only "slide" — it can be `expenseReport`,
`wikiEntry`, `meetingNote`, anything. The author defines a new type by creating
one tagged note; no code change, no redeploy.

## The `notecast` namespace

Trilium attribute names are `[A-Za-z0-9_]` only (no `:` — that is reserved by
Trilium for promoted-attribute definitions like `label:foo`), so every label is
camelCase under the `notecast` prefix.

Generic single words (`type`, `theme`, `css`, `template`, `print`, `node`) are
**avoided on purpose**: Trilium ships built-ins such as `#appTheme`, `#appCss`,
`#cssClass`, `#template`, `#titleTemplate`, `#viewType`, and other plugins on
the public plugin page could grab any bare word. A distinctive prefix is the
only real collision protection. Verified against the TriliumNext attributes doc:
none of the labels below collide with a built-in.

### Always quote a label value in a search query

`#notecastType=note` does **not** search for the value `note`. Trilium's search
grammar reads a bare `note` as one of its own identifiers, and the query then
matches almost every note in the instance — measured against a live instance:
**20 hits unquoted, 1 quoted**. `#notecastType="note"` is correct.

This is not a corner case: `note` is a registered type id (below), so every repo
that looks a type or theme up by value is affected. It shipped in two of them —
`create_note(note_type="note")` always answered "AMBIGUOUS" and could never
create anything, and the renderer offered a dropdown of ordinary notes instead
of that type's themes. Both now quote.

The value must be a plain token (`[A-Za-z0-9_-]+`) before it is quoted — quoting
is not an escape mechanism, and a query assembled from arbitrary label text is
not made safe by wrapping it. The MCP validates ids against exactly that shape
already; the renderer falls back to "all themes" for anything else.

### Content-type definition — read by the MCP only

A **type definition** is one note that tells the MCP how to create notes of a
given type.

| Label | Meaning | Required |
| --- | --- | --- |
| `#notecastType=<id>` | Marks the note as a type definition. `<id>` is the value passed to `create_note(note_type=…)`, e.g. `slide`, `expenseReport`. | yes |
| `#notecastTargetType=text\|code` | Trilium note type of created notes. Default `text`. | no |
| `#notecastMime=<mime>` | MIME for `code` notes, e.g. `text/x-markdown`. Ignored for `text`. | no |
| `#notecastApplyLabels=<name>` or `<name>=<value>` | A label to stamp on every created note. May appear multiple times (one per label). e.g. `#notecastApplyLabels=slideType=content`. | no |
| `#notecastParent=<noteId>` | Default parent for created notes of this type. Falls back to the caller's argument / `TRILIUM_DEFAULT_PARENT`. | no |
| `#notecastPrefix=<branchPrefix>` | Branch prefix for created notes (the old slides used `Folie`). | no |

#### Per-note labels

`#notecastApplyLabels` is **one fixed value per type**, which leaves a type whose
id does not distinguish its variants unreachable: `slide` has three of them —
`title`, `content`, `chapter` — the definition stamps `slideType=content`, and
before `labels` there was no way to author the other two. Every deck created
through the MCP therefore had no title slide until someone edited the label by
hand in Trilium, and the MCP had no tool to do that either.

`create_note(..., labels={"slideType": "title"})` closes that. The mapping is
applied **after** `#notecastApplyLabels`, so a name given there replaces the
type's default rather than sitting beside it — two `slideType` labels on one
note would make the rendered layout depend on attribute order.

Note what this is *not*: the presenter's position-based fallback (first slide in
a deck becomes `title`) is deliberately dead, because the same note then rendered
as a title slide in a deck and as a content slide on its own. Naming the type on
every note is the fix for that; `labels` is what lets the MCP name it too.

Two labels are refused, and nothing is created when they appear:

| Label | Why |
| --- | --- |
| `#notecastInstance` | the renderer resolves a note's type from it; a wrong value silently offers the wrong themes |
| `#notecastType` | would make the created note a *second* definition of that type, and the ambiguity guard then refuses every later `create_note` for it |

Names are validated against `[A-Za-z0-9_]+` — Trilium's own attribute grammar.

The note's **content** is the authoring format — the prose the model must
follow (structure, conventions, voice). It is injected live into the
`create_note` / `update_note` tool descriptions, exactly as the old
`#presenterSlideFormat` content was injected into `create_slide`.

### Theme definition — read by the renderer, NOT by the MCP or the presenter

A **theme** is one note carrying a CSS stylesheet for a type.

| Element | Meaning |
| --- | --- |
| `#notecastTheme=<typeId>` | Marks the note as a theme and binds it to a type. |
| note **title** | The theme's name, shown in the renderer's picker — e.g. `A4 Note`, `US Letter Note`. |
| note **content** | The CSS. |

**Themes are not shared between the presenter and the renderer — each keeps its
own label namespace:**

| Label | Owner | Shape |
| --- | --- | --- |
| `#notecastTheme=<typeId>` | `trilium-notecast-render` | **one** note; its content is the print CSS |
| `#presenterTheme` | `trilium-presenter-plugin` | a **container** note holding several CSS notes (Base / Title / Content / Handout / Chapter) plus SVG backgrounds |

The two shapes genuinely differ, and forcing the presenter's multi-CSS structure
into a single note would lose the split it depends on. The presenter predates
Notecast and keeps its established namespace; `notecast` was introduced to give
the *new* pieces a common family name, not to rename what already worked. So
`#notecastTheme` is in practice the **print/render** label, and there is no
medium marker on it — a type simply has a set of named themes and the medium
lives in the name.

Consequence, accepted deliberately: the presenter's themes are invisible to the
renderer and vice versa. That is the intended separation, not a gap to close.

The MCP **does not touch themes at all** — neither namespace, not creating, not
reading. (It may of course set an ordinary `#notecastApplyLabels` if a type wants
a default theme stamped, but it has no theme-specific logic.)

### Presenter-only labels — read by no other repo

`#presenterTheme` above is one of these; the other is `#slideIgnore`.

| Label | Owner | Meaning |
| --- | --- | --- |
| `#slideIgnore` | `trilium-presenter-plugin` | keep a note out of the presentation — bare on the note itself, or `#slideIgnore=subtree` for its whole branch |

Both sit outside the `notecast` namespace because they predate it, and neither
the MCP nor the renderer reads either one.

For `#slideIgnore` that has a visible consequence, so it is written down rather
than left implicit: **a branch hidden from the presentation is still printed.**
The renderer's subtree print is deliberately type-agnostic — one code path for
every Notecast type — and it reads none of the presenter's labels. A "Handouts"
or "Notes" folder parked next to the slides therefore stays off screen and lands
on paper.

The expectation that it would not comes from the presenter's own handout, which
did honour the label. That handout is gone; printing belongs to the renderer
now. That left the renderer with no way to exclude anything at all, which has
since been decided — and **not** by teaching it this label: print exclusion is
`#notecastIgnore`, below.

### Print exclusion — read by the renderer

| Label | Owner | Shape |
| --- | --- | --- |
| `#notecastIgnore` | `trilium-notecast-render` | keep a note out of a subtree print — bare on the note itself, or `#notecastIgnore=subtree` for its whole branch |

Shape-identical to `#slideIgnore` on purpose, and deliberately **not the same
label**. Sharing one would get the paradigm case backwards: the canonical
`#slideIgnore` note is a "Handouts" or "Notes" folder parked beside the slides,
kept off screen precisely *because* it belongs on paper. A renderer honouring
`#slideIgnore` would suppress exactly the branch the print job exists for.
"Not a slide" and "not for print" are different statements about a note, so they
get different labels — and a note that means both simply carries both.

Three rules govern the walk:

- **Bare removes the note, not its descendants.** Its children are still walked
  and printed. That is what makes the label usable on a container: a folder can
  be kept off paper without hiding what it holds.
- **`=subtree` removes the branch**, the note included. Any other value reads as
  bare, matching the presenter.
- **The note Print was pressed on is exempt.** Its own `#notecastIgnore` does not
  apply to itself, only to what hangs below it. The renderer's root is chosen by
  hand — a note selected, a button pressed — where the presenter's is whatever
  deck it was opened on. An explicit act beats a passive marker, and the
  alternative is answering a deliberate Print with "Nothing to print", which
  reads as a broken plugin rather than as a label doing its job. For the same
  reason a single-note print never consults the label at all.

The MCP does not read it. It can of course stamp it through `#notecastApplyLabels`
like any other label, but it has no logic of its own for it.

### Instance marker — written by the MCP, read by the renderer

`#notecastType=<id>` marks a type **definition**. `#notecastInstance=<id>` marks
an **instance** — a note *of* that type. `create_note` stamps
`#notecastInstance=<note_type>` on every note it creates, so instance-side
consumers (the render plugin) can resolve a note's type: select a note → its
`#notecastInstance` fixes the type → offer that type's `#notecastTheme=<id>`
themes.

It is deliberately a **different label** than `#notecastType`: if instances
carried `#notecastType`, the MCP's definition lookup (`#notecastType=<id>`, which
must resolve to exactly one note) would collide with every instance and always
report "ambiguous". Notes not created by the MCP (hand-authored) simply lack the
marker; the renderer then falls back to offering all themes.

### Images — written by the MCP, resolved by the output plugins

An image belongs to the note that shows it: the MCP attaches it to that note as
a Trilium **attachment** with `role=image`, and the attachment's **title is the
file name**. Nothing about this is carried by a `#notecast…` label — the binding
is the title, and that is what makes it a shared agreement rather than an
implementation detail of one repo.

**The resolution rule, binding on every consumer of a markdown-typed note:** a
reference target that is a bare file name — no scheme, no slash — is looked up
against the titles of the note's own attachments and replaced with that
attachment's URL. Both output plugins implement it: `trilium-presenter-plugin`
in `processSlideContent`, `trilium-notecast-render` in `attachmentUrls` /
`resolveImageSrc`.

Both of them render into a window opened from a `blob:` URL, which does not
share Trilium's origin, so **the resolved URL has to be made absolute**. A
relative `api/attachments/…` survives every test that inspects the HTML and then
404s in the actual output — including in `text` notes, where the URL was never
built by the plugin at all but came from Trilium.

Two consequences the MCP enforces when attaching, both to keep the mapping
decidable:

- **File names carry no path.** A target containing a slash is not resolved by
  the presenter, so a path-shaped name would attach without error and then
  silently never render. `attach_image` refuses it.
- **A file name is unique within its note.** Two attachments with the same title
  make "which image does this reference mean" undecidable, and the answer would
  depend on enumeration order. `attach_image` refuses the second one — the same
  clarity guard as the duplicate-type refusal, for the same reason.

**The reference form depends on `#notecastTargetType`**, and the two are not
interchangeable:

| Target type | Reference in the note content | Resolved by |
| --- | --- | --- |
| `code` (markdown) | `![alt](filename.png)` | the output plugin, against attachment titles |
| `text` (HTML) | `<img src="api/attachments/<attachmentId>/image/<title>">` | nobody — Trilium renders the URL as-is |

`attach_image` returns the correct form for the note it attached to, so the
authoring model does not have to know this table. It does mean the reference in
a `text` note contains a concrete attachment ID and does **not** survive the
image being replaced, whereas the markdown form does.

Themes are unaffected: images are content, and no `#notecastTheme` or
`#presenterTheme` note is involved.

## Resolution rules (MCP)

Generalised from the current `_fetch_slide_format` machinery. To resolve a type
`X` the server searches `#notecastType=X`:

- **exactly one hit** → use its content as the format and read its mechanics
  labels. Cache with a short TTL to avoid hammering ETAPI during a `tools/list`
  burst.
- **zero hits / ETAPI unreachable** → a loud **"DO NOT GUESS"** notice, never a
  built-in fallback format. A missing type must never silently masquerade as a
  real one.
- **more than one hit** → **refuse** for that type with a loud "type `X` defined
  more than once" error.

That last rule is **not** a trust boundary — by decision there is none. Anyone
who can tag a note defines a type; the author is responsible for what they tag.
It is a *clarity* guard: two notes claiming the same type id make the format
undecidable, and a clear error beats silently picking the wrong instructions.
There is no per-type pinning env var (the old `TRILIUM_SLIDE_FORMAT_NOTE_ID` is
removed) — pinning every type by ID would defeat the "just tag a note" workflow.

## MCP tool surface

The presenter-specific tools collapse into generic, type-driven CRUD. The MCP
**authors** notes; it does not present or render, so the presenter-mirroring
traversal is gone.

| New | Replaces | Notes |
| --- | --- | --- |
| `create_note(note_type, title, content, parent?, labels?)` | `create_slide` / `create_presentation` | Reads the type definition, sets `#notecastApplyLabels`, target type, mime, prefix. `labels` overrides those defaults for one note — see "Per-note labels" below. |
| `update_note(note_id, content?, title?)` | `update_slide` | |
| `attach_image(note_id, filename, data_base64, alt?, mime?)` | — (new) | Attaches an image to an existing note and returns the reference to embed. See "Images" above. |
| `get_note(note_id)` | `get_slide` | |
| `list_children(parent_note_id)` | `list_presentations` | Plain navigation for authoring — **not** the presenter traversal. |
| `clone_node(note_id, target_parent_id)` | `clone_slide` | Kept generic; mostly the user's job, but cheap and useful. |
| `move_node(branch_id, new_position)` | `move_slide` | Kept generic (reordering after authoring). |
| `delete_note(note_id)` | `delete_note` | Unchanged. |
| `get_note_info(note_id)` | `get_note_info` | Unchanged. |
| `search_notes(query, limit)` | `search_notes` | Unchanged. |
| `list_note_types()` | — (new) | Discovery: enumerates `#notecastType` ids + titles so the assistant knows valid `note_type` values. |

**Removed:** `list_slides` and the whole `collectSlides` traversal
(`_collect_slides`, `_sorted_child_branches`) — presenting is the presenter's
job. `slideType` stops being special: for the `slide` type it is just one
`#notecastApplyLabels=slideType=…` entry.

### Format injection

`create_note` / `update_note` descriptions carry the available type ids and
their formats, fetched live at `tools/list` time (same mechanism as today).
Scaling caveat: with many types this description grows. If it ever becomes a
problem, the fallback is per-type tools (`create_expenseReport`, …) generated
dynamically — deliberately deferred; the single generic tool is the default.

## Seed ownership

The importable seed notes (type definitions + themes) are **not** shipped by the
MCP. Ownership follows the output:

- **The output plugin that renders a type owns and ships that type's full seed**
  — its single `#notecastType` format note *and* the theme notes it renders with
  — as that plugin's own import zip. The presenter ships `#notecastType=slide`
  plus its `#presenterTheme` screen themes and is thus self-sufficient
  standalone; the renderer ships `#notecastTheme=<typeId>` print themes.
  Each plugin seeds the namespace it reads (see "Theme definition" above).
- **The MCP ships no definitions.** It is a pure engine; with no type notes,
  `create_note` correctly refuses ("do not guess"). A user gets types by
  importing an output plugin's zip or by tagging their own note.
- **The presenter presents, full stop. All printing lives in the renderer** as
  print themes — including a print theme for `slide` (a deck-as-handout), if
  wanted. Screen themes = presenter, print themes = renderer. In principle one
  *could* build a print theme "for" the presenter, but by this split any such
  print output is a renderer concern, not the presenter's.
- The ambiguity guard enforces the clean division: the **format note per type
  must be unique** → exactly one owning plugin per type; **themes are additive**
  → many `#notecastTheme=<sameType>` notes may coexist across repos and media.

### Reserved type-id vocabulary

Because two repos must never claim the same id as a `#notecastType` format note,
type ids are a shared, reserved vocabulary. Register each canonical id here as it
is introduced:

| Type id | Owning plugin | Target | Notes |
| --- | --- | --- | --- |
| `slide` | `trilium-presenter-plugin` | `code` / `text/x-markdown` | screen themes in the presenter under `#presenterTheme`; print theme in the renderer under `#notecastTheme=slide` |
| `note` | `trilium-notecast-render` | `text` (HTML) | a short captured thought |
| `kbEntry` | `trilium-notecast-render` | `code` / `text/x-markdown` | a durable reference article |
| `meetingNote` | `trilium-notecast-render` | `code` / `text/x-markdown` | minutes of one meeting |
| `checklist` | `trilium-notecast-render` | `code` / `text/x-markdown` | steps to tick off on paper |
| `itTip` | `trilium-notecast-render` | `code` / `text/x-markdown` | one problem, one fix, one page |
| `letter` | `trilium-notecast-render` | `text` (HTML) | formal letter, window-envelope geometry |
| `handout` | `trilium-notecast-render` | `code` / `text/x-markdown` | course material to take home; the one type written to run past a single sheet |

The renderer owns the general-purpose document types because it is the general-
purpose output: anything can be printed, whereas `slide` only means something to
a presenter. A type belongs to whichever plugin gives it a visible form.

## Migration

- **Done:** the Slide Format note carries `#notecastType=slide` plus the mechanics
  labels (`#notecastTargetType=code`, `#notecastMime=text/x-markdown`,
  `#notecastApplyLabels=slideType=content`, `#notecastPrefix=Folie`), applied by
  `trilium-presenter-plugin/migrate-slide-type-to-notecast.py`. The migration is
  additive: the legacy `#presenterSlideFormat` label is still on the note and can
  be dropped once the MCP is verified end to end.
- **Done:** the presenter's import zip carries the new labels. The exported
  `meta.json` that would have had to be refreshed by hand is gone from both
  plugin repos — `build-zip.py` now declares the note tree and emits the archive
  and its metadata together, so the labels cannot drift out of a release again.
- **Themes: deliberately not migrated, and never will be.** The presenter's
  on-screen themes stay on `#presenterTheme`; `#notecastTheme` belongs to the
  renderer. This is settled — see "Theme definition" above for the reasoning.
- **Done locally, not yet published:** the legacy mini MCP server (no Docker)
  that used to sit in `trilium-presenter-plugin/mcp/` has been removed there and
  its docs point at this repo — but those commits are unpushed. The *public*
  presenter repo is still on its 2026-06-27 state and does bundle the mini
  server. It resolves itself when the presenter repo is pushed, which is planned
  for the same round in which this repo and `trilium-notecast-render` go public.

## Env vars (MCP)

Unchanged: `TRILIUM_URL`, `TRILIUM_API_KEY`, `TRILIUM_DEFAULT_PARENT`, and all
transport/auth vars. **Removed:** `TRILIUM_SLIDE_FORMAT_NOTE_ID` (no pinning).

"""Trilium Notes MCP Server — generic typed-note authoring via ETAPI (Notecast)."""

import base64
import binascii
import json
import logging
import os
import re
import time
from hmac import compare_digest
from typing import TypedDict
from urllib.parse import quote

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.types import TextContent

# rstrip: a trailing slash here would produce "…//etapi/…" in every request path.
TRILIUM_URL = os.getenv("TRILIUM_URL", "http://localhost:8080").rstrip("/")
TRILIUM_API_KEY = os.getenv("TRILIUM_API_KEY", "")
DEFAULT_PARENT = os.getenv("TRILIUM_DEFAULT_PARENT", "root")

# Transport: "stdio" (default, for Claude Desktop) or "streamable-http" (for the
# containerised server reached over the tailnet).
MCP_TRANSPORT = os.getenv("MCP_TRANSPORT", "stdio")
MCP_HOST = os.getenv("MCP_HOST", "127.0.0.1")
MCP_PORT = int(os.getenv("MCP_PORT", "8000"))
MCP_PATH = os.getenv("MCP_PATH", "/mcp")
# Optional second layer on top of "only reachable inside the tailnet".
MCP_AUTH_TOKEN = os.getenv("MCP_AUTH_TOKEN", "")
# Hosts/origins accepted by the SDK's DNS-rebinding protection. Comma-separated;
# needed because we are reached through a reverse proxy under its own hostname.
MCP_ALLOWED_HOSTS = [h.strip() for h in os.getenv("MCP_ALLOWED_HOSTS", "").split(",") if h.strip()]
MCP_ALLOWED_ORIGINS = [o.strip() for o in os.getenv("MCP_ALLOWED_ORIGINS", "").split(",") if o.strip()]

if not TRILIUM_API_KEY:
    raise OSError(
        "TRILIUM_API_KEY is not set.\n"
        "Generate a token in Trilium: Options → ETAPI → Create new token.\n"
        "Then set it in the MCP server env block in claude_desktop_config.json."
    )

logger = logging.getLogger("trilium-notecast-mcp")

mcp = FastMCP("trilium-notecast")


# Trilium IDs are random alphanumerics; branch IDs join two of them with "_",
# and the built-in subtree roots ("root", "_hidden", "_options", …) use "_" too.
# \Z, not $: $ also matches before a trailing newline, which would let a CR/LF
# through into the URL path.
#
# The same pattern guards two different things, which is why it is one constant
# and not two: entity IDs interpolated into an ETAPI *path* (see `_id`), and
# note-type ids interpolated into a Trilium *search query* (see
# `_fetch_type_definition`), where plain tokens cannot inject search operators.
_ID_RE = re.compile(r"^[A-Za-z0-9_-]+\Z")

# Trilium attribute names are [A-Za-z0-9_] only — a `:` is reserved for its own
# promoted-attribute definitions (`label:foo`). Validating here rather than
# letting ETAPI reject it keeps the failure legible: the caller is a model, and
# "invalid label name" beats a 400 with Trilium's internal wording.
_LABEL_NAME_RE = re.compile(r"^[A-Za-z0-9_]+\Z")

# One pooled client for the process. Every ETAPI call went through a fresh
# connection before, which meant a TCP (and TLS) handshake per request — and a
# cold tools/list makes dozens of them. httpx.Client is thread-safe, which
# matters because FastMCP runs sync tools in a threadpool under the HTTP
# transport. It lives for the process; there is nothing to close.
_client = httpx.Client(timeout=10, headers={"Authorization": TRILIUM_API_KEY})

# Upper bound on any search. `limit` reaches `search_notes` from the model, and
# an unbounded value would pull an arbitrary slice of the instance into context.
_MAX_SEARCH_LIMIT = 200

# Images arrive base64-encoded in a tool argument, so the practical ceiling is
# the model's context, not this — the limit is here to turn a runaway payload
# into a clear error instead of a 10-minute upload.
_MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024

# Keyed by extension because the extension is what the author writes in the note
# body; the value goes into the attachment's `mime` field. SVG is included on
# purpose (the slide templates use it) — it reaches the page as <img src>, which
# does not execute script.
_IMAGE_MIMES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
}

# The filename becomes the attachment *title*, which consumers match against the
# `![alt](name)` target in the note body. The presenter only resolves targets
# without a slash (widget.js), so a path-shaped name would attach fine and then
# silently never render.
_FILENAME_RE = re.compile(r"^[^/\\\x00-\x1F]{1,120}\Z")


def _id(value: str) -> str:
    """Validate an entity ID before it is interpolated into an ETAPI URL path.

    Tool arguments come from the model, and every ETAPI request carries the API
    key — so an unchecked ID is a way to reach endpoints the tool never meant to
    touch (`?`/`#` truncate the rest of the path, `..` segments are normalised
    away by the URL parser). Trilium IDs are alphanumeric, so rejecting anything
    else costs nothing and closes the whole class.
    """
    if not isinstance(value, str) or not _ID_RE.match(value):
        raise ValueError(
            f"Invalid Trilium ID {value!r} — expected alphanumeric characters only."
        )
    return value


_JSON = {"Content-Type": "application/json"}


def _get(path: str) -> dict:
    r = _client.get(f"{TRILIUM_URL}/etapi/{path}")
    r.raise_for_status()
    return r.json()


def _post(path: str, body: dict) -> dict:
    r = _client.post(f"{TRILIUM_URL}/etapi/{path}", headers=_JSON, json=body)
    r.raise_for_status()
    return r.json()


def _patch(path: str, body: dict) -> dict:
    r = _client.patch(f"{TRILIUM_URL}/etapi/{path}", headers=_JSON, json=body)
    r.raise_for_status()
    return r.json() if r.content else {}


def _put_content(note_id: str, content: str) -> None:
    r = _client.put(
        f"{TRILIUM_URL}/etapi/notes/{_id(note_id)}/content",
        headers={"Content-Type": "text/plain"},
        content=content.encode(),
    )
    r.raise_for_status()


def _put_attachment_content(attachment_id: str, data: bytes) -> None:
    """Write raw bytes into an attachment.

    Deliberately *not* a flag on `_put_content`: the Content-Type here is
    hard-coded to application/octet-stream and has to stay that way. Trilium
    installs a body parser for that type only —

    - sending the image's real MIME (`image/png`) answers **500**;
    - sending `text/plain`, which is what `_put_content` uses, decodes the body
      as UTF-8 and replaces every byte >= 0x80 with U+FFFD, silently returning a
      corrupt image roughly twice the size.

    Both verified against a live instance by tools/probe-attachment-binary.py.
    A shared codepath with a `content_type` argument would make that difference
    look like a caller's choice, when in fact one of the options destroys data.
    """
    r = _client.put(
        f"{TRILIUM_URL}/etapi/attachments/{_id(attachment_id)}/content",
        headers={"Content-Type": "application/octet-stream"},
        content=data,
    )
    r.raise_for_status()


def _delete(path: str) -> None:
    r = _client.delete(f"{TRILIUM_URL}/etapi/{path}")
    r.raise_for_status()


def _get_content(note_id: str) -> str:
    r = _client.get(f"{TRILIUM_URL}/etapi/notes/{_id(note_id)}/content")
    r.raise_for_status()
    return r.text


def _search(query: str, limit: int = 20) -> list[dict]:
    try:
        limit = max(1, min(int(limit), _MAX_SEARCH_LIMIT))
    except (TypeError, ValueError):
        limit = 20
    r = _client.get(
        f"{TRILIUM_URL}/etapi/notes",
        params={"search": query, "limit": limit},
    )
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, list) else data.get("results", [])


def _set_attribute(note_id: str, name: str, value: str) -> dict:
    # Check if attribute exists first
    note = _get(f"notes/{_id(note_id)}")
    for attr in note.get("attributes", []):
        if attr["name"] == name and attr["type"] == "label":
            return _patch(f"attributes/{_id(attr['attributeId'])}", {"value": value})
    # Create new attribute
    return _post("attributes", {
        "noteId": note_id,
        "type": "label",
        "name": name,
        "value": value,
    })


# ── Note-type definitions (Notecast) ─────────────────────────────────────────
# A "type" is defined by data in Trilium, not by code here: a note labelled
# #notecastType=<id> whose *content* is the authoring format (the prose the model
# must follow) and whose *labels* carry the mechanics — target note type, mime,
# labels to stamp, default parent, branch prefix. Adding a type is tagging a
# note; no code change. The output plugin that renders a type owns and ships its
# definition. See docs/notecast-contract.md.

TYPE_LABEL = "notecastType"
INSTANCE_LABEL = "notecastInstance"       # stamped on every created note = its type id
MECH_TARGET_TYPE = "notecastTargetType"   # text | code  (default: text)
MECH_MIME = "notecastMime"                # e.g. text/x-markdown (code notes)
MECH_APPLY_LABEL = "notecastApplyLabels"  # "name" or "name=value"; repeatable
MECH_PARENT = "notecastParent"            # default parent note id
MECH_PREFIX = "notecastPrefix"            # branch prefix for created notes

# Type ids come from the model and are interpolated into a Trilium search query,
# so keep them to plain tokens — they cannot then inject search operators. Same
# shape as an entity ID, so the same compiled pattern; see _ID_RE.
_TYPE_ID_RE = _ID_RE


# Labels a caller may never set through `labels`. Both are the MCP's own
# bookkeeping and breaking either is silent, not loud:
#   notecastInstance — the render plugin resolves a note's type from it, so a
#     wrong value offers the wrong themes and a missing one falls back to "all".
#   notecastType     — would turn the created note into a *second* definition of
#     that type, and the ambiguity guard then refuses every later create_note
#     for it. One stray label would take the type offline.
_RESERVED_LABELS = (INSTANCE_LABEL, TYPE_LABEL)


def _validate_labels(labels: dict[str, str] | None) -> str | None:
    """Reject a bad `labels` mapping, or return None if it is usable.

    Returns the message to hand back to the caller; nothing is created when it
    is not None. Refusing beats silently dropping a label — an author who asked
    for `slideType=title` and got a content slide has no way to tell why.
    """
    if not labels:
        return None
    if not isinstance(labels, dict):
        return "⚠️ labels must be an object mapping label name to value."
    for name, value in labels.items():
        if not isinstance(name, str) or not _LABEL_NAME_RE.match(name):
            return (
                f"⚠️ INVALID LABEL NAME '{name}' — nothing was created.\n"
                "Trilium attribute names may contain only letters, digits and "
                "underscores."
            )
        if name in _RESERVED_LABELS:
            return (
                f"⚠️ LABEL '{name}' IS RESERVED — nothing was created.\n"
                f"#{INSTANCE_LABEL} is set from `note_type` and is how the render "
                f"plugin resolves a note's type; #{TYPE_LABEL} marks a type "
                "*definition* and would make this note a duplicate one, taking the "
                "type offline for every later create_note."
            )
        if not isinstance(value, str):
            return (
                f"⚠️ LABEL '{name}' HAS A NON-STRING VALUE — nothing was created.\n"
                "Use \"\" for a bare label."
            )
    return None


def _type_unavailable_notice(type_id: str) -> str:
    return (
        f"⚠️ NOTE TYPE '{type_id}' UNAVAILABLE — DO NOT GUESS\n"
        f"No note is labelled #{TYPE_LABEL}={type_id} in Trilium (missing or ETAPI\n"
        "unreachable). A note type ships with the output plugin that renders it and\n"
        "is the single source of truth for structure, conventions and voice. Do NOT\n"
        "invent a format or rely on remembered rules. Stop, tell the author the type\n"
        "definition is unreachable, and let them fix it before any note of this type\n"
        "is created or updated."
    )


def _type_ambiguous_notice(type_id: str) -> str:
    return (
        f"⚠️ NOTE TYPE '{type_id}' AMBIGUOUS — DO NOT GUESS\n"
        f"More than one note carries #{TYPE_LABEL}={type_id}, so which one is the\n"
        "authoritative definition cannot be decided — and the wrong one would\n"
        "silently become the instructions for every note of this type. Do NOT invent\n"
        "or pick one. Stop, tell the author to remove the duplicate label, and let\n"
        "them fix it before any note of this type is written."
    )


_NO_TYPES_NOTICE = (
    "No note types are defined yet.\n"
    f"Create a Trilium note labelled #{TYPE_LABEL}=<id> whose content is the\n"
    'authoring format for that type; then create_note(note_type="<id>", …) can\n'
    "use it. Note types ship with the output plugin that renders them (e.g. the\n"
    "presenter ships the `slide` type)."
)


def _parse_mechanics(note: dict) -> dict:
    """Read the operational labels off a #notecastType definition note."""
    m = {"target_type": "text", "mime": None, "parent": None, "prefix": None, "apply_labels": []}
    for a in note.get("attributes", []):
        if a.get("type") != "label":
            continue
        name, value = a.get("name"), a.get("value", "")
        if name == MECH_TARGET_TYPE and value:
            m["target_type"] = value
        elif name == MECH_MIME and value:
            m["mime"] = value
        elif name == MECH_PARENT and value:
            m["parent"] = value
        elif name == MECH_PREFIX and value:
            m["prefix"] = value
        elif name == MECH_APPLY_LABEL and value:
            m["apply_labels"].append(value)
    return m


_TYPE_CACHE_TTL = 60.0  # seconds — avoid hammering ETAPI during a tools/list burst
_type_cache: dict[str, dict] = {}


_TYPE_SEARCH_LIMIT = 100


def _type_notes() -> tuple[list[dict], bool]:
    """Every #notecastType definition note, plus whether the search was truncated.

    One search instead of a search plus a GET per note: current Trilium returns
    full notes in the search response, so the per-note fetch is only paid for
    the ones that came back without attributes. That fallback is what keeps this
    working against a server that answers with bare stubs.

    The truncation flag matters because callers use the *set* of notes to decide
    whether a type id is unique. A full result page means there may be more that
    were not seen, so that conclusion is no longer safe — see `_build_type_block`.
    """
    hits = _search(f"#{TYPE_LABEL}", limit=_TYPE_SEARCH_LIMIT)
    notes = [
        n if n.get("attributes") else _get(f"notes/{_id(n['noteId'])}")
        for n in hits
    ]
    return notes, len(hits) >= _TYPE_SEARCH_LIMIT


def _type_id_of(note: dict) -> str:
    """The value of this note's #notecastType label, or "" if it carries none."""
    for a in note.get("attributes", []):
        if a.get("type") == "label" and a.get("name") == TYPE_LABEL:
            return a.get("value", "")
    return ""


def _fetch_type_definition(
    type_id: str, note: dict | None = None
) -> tuple[str, dict | None, str | None]:
    """Resolve a type id to (format_text, mechanics, source_note_id).

    `note` is an already-loaded definition note for this id. Callers that have
    just enumerated the type notes pass it so the lookup below is skipped; the
    caller is then responsible for having established that the id is unique
    (see `_build_type_block`).

    Generalises the old #presenterSlideFormat lookup. On success the mechanics
    dict and the source note id are returned. On failure the first element is a
    loud do-not-guess / ambiguous notice and the other two are None — never a
    built-in fallback, so a missing or duplicated type can never masquerade as a
    real one. Only successful loads are cached; failures recover on the next call.

    No pinning by ID: by design there is no trust boundary — anyone who can tag a
    note defines a type. The ambiguity refusal is only a clarity guard: two notes
    claiming the same id make the format undecidable, so we refuse rather than
    silently pick the wrong instructions.
    """
    if not _TYPE_ID_RE.match(type_id or ""):
        return _type_unavailable_notice(type_id), None, None
    now = time.monotonic()
    cached = _type_cache.get(type_id)
    if cached and now - cached["ts"] < _TYPE_CACHE_TTL:
        return cached["text"], cached["mechanics"], cached["source"]
    try:
        if note is None:
            # The value is quoted because Trilium's search grammar reads a bare
            # `note` as an identifier, not as a string: `#notecastType=note`
            # matches ~every note in the instance, so the registered type id
            # `note` resolved to "ambiguous" and could never be created. The id
            # passed _TYPE_ID_RE above, so it cannot contain a quote itself.
            found = _search(f'#{TYPE_LABEL}="{type_id}"', limit=2)
            if len(found) > 1:
                logger.warning(
                    "Multiple notes carry #%s=%s — refusing to pick one", TYPE_LABEL, type_id
                )
                return _type_ambiguous_notice(type_id), None, None
            if found:
                note_id = found[0]["noteId"]
                note = found[0] if found[0].get("attributes") else _get(f"notes/{_id(note_id)}")
        if note is not None:
            note_id = note["noteId"]
            content = _get_content(note_id).strip()
            if content:
                mechanics = _parse_mechanics(note)
                _type_cache[type_id] = {
                    "text": content, "mechanics": mechanics, "source": note_id, "ts": now,
                }
                return content, mechanics, note_id
        logger.warning("#%s=%s note not found — returning unavailable notice", TYPE_LABEL, type_id)
    except Exception as exc:  # never let type lookup break the tool
        logger.warning("Could not load #%s=%s: %s", TYPE_LABEL, type_id, exc)
    return _type_unavailable_notice(type_id), None, None


def _group_types_by_id(notes: list[dict]) -> dict[str, list[dict]]:
    """Definition notes keyed by their #notecastType id, in note order.

    A list per id rather than a single note, because the count is the ambiguity
    check: more than one note claiming an id means the format is undecidable.
    """
    groups: dict[str, list[dict]] = {}
    for n in notes:
        type_id = _type_id_of(n)
        if type_id:
            groups.setdefault(type_id, []).append(n)
    return groups


# ── Generic note tools ───────────────────────────────────────────────────────

@mcp.tool()
def list_note_types() -> str:
    """List the note types available to create_note.

    Each type is a Trilium note labelled #notecastType=<id> whose content is the
    authoring format. Types ship with the output plugin that renders them.

    Returns:
        JSON array of {noteType, title, noteId}.
    """
    notes, _truncated = _type_notes()
    return json.dumps([
        {"noteType": _type_id_of(n), "title": n["title"], "noteId": n["noteId"]}
        for n in notes
    ])


@mcp.tool()
def create_note(
    note_type: str,
    title: str,
    content: str,
    parent_note_id: str | None = None,
    labels: dict[str, str] | None = None,
) -> str:
    """Create a note of a given type.

    The type is a Trilium note labelled #notecastType=<note_type>; its content is
    the authoring format you must follow when writing `content` — that format is
    appended to this description below, loaded live from Trilium. Call
    list_note_types() to see the available types.

    If `note_type` does not resolve to exactly one definition this refuses and
    returns a STOP notice instead of creating anything — never guess a format.

    Args:
        note_type: The type id (e.g. 'slide', 'expenseReport').
        title: Note title (shown in the Trilium tree).
        content: The note body, in the type's format.
        parent_note_id: Parent note ID. Defaults to the type's #notecastParent,
                        else the configured default parent.
        labels: Extra labels for this one note, as {name: value}; "" for a bare
                label. Applied after the type's #notecastApplyLabels, so a name
                given here overrides the type's default for that label. Use it
                where a type has variants the type id does not distinguish —
                a `slide` deck needs slideType=title on its first slide and
                slideType=chapter on a section break, while the type stamps
                slideType=content on every note it creates.
    Returns:
        JSON with noteId, or a STOP notice if the type is unavailable/ambiguous
        or `labels` is not usable.
    """
    # Validated before the type lookup: a bad mapping must not cost an ETAPI
    # round trip, and must never leave a half-labelled note behind.
    if (refusal := _validate_labels(labels)) is not None:
        return refusal
    fmt, mechanics, source_id = _fetch_type_definition(note_type)
    if source_id is None or mechanics is None:
        return fmt  # loud do-not-guess / ambiguous notice — nothing created
    parent = parent_note_id or mechanics["parent"] or DEFAULT_PARENT
    body = {
        "parentNoteId": parent,
        "title": title,
        "type": mechanics["target_type"],
        "content": content,
    }
    if mechanics["target_type"] == "code" and mechanics["mime"]:
        body["mime"] = mechanics["mime"]
    if mechanics["prefix"]:
        body["prefix"] = mechanics["prefix"]
    result = _post("create-note", body)
    note_id = result["note"]["noteId"]
    # Mark the note with its type so instance-side consumers (e.g. the render
    # plugin) can resolve it. Deliberately a different label than TYPE_LABEL —
    # tagging instances with #notecastType would collide with the type lookup.
    _set_attribute(note_id, INSTANCE_LABEL, note_type)
    for spec in mechanics["apply_labels"]:
        name, _sep, value = spec.partition("=")
        if name:
            _set_attribute(note_id, name, value)
    # Last, so the caller's value replaces the type's default for the same name
    # — _set_attribute patches an existing label rather than adding a second.
    for name, value in (labels or {}).items():
        _set_attribute(note_id, name, value)
    return json.dumps({"noteId": note_id, "title": title, "noteType": note_type})


@mcp.tool()
def get_note(note_id: str) -> str:
    """Get the raw content of a note.

    Args:
        note_id: The note ID.
    Returns:
        The raw note content.
    """
    return _get_content(note_id)


@mcp.tool()
def update_note(
    note_id: str,
    content: str | None = None,
    title: str | None = None,
) -> str:
    """Update a note's content and/or title.

    When rewriting content, follow the note type's format (see the create_note
    description, loaded live from Trilium).

    Args:
        note_id: The note ID.
        content: New content (optional).
        title: New title (optional).
    Returns:
        JSON confirming the update.
    """
    if content is not None:
        _put_content(note_id, content)
    if title is not None:
        _patch(f"notes/{_id(note_id)}", {"title": title})
    return json.dumps({"noteId": note_id, "updated": True})


def _image_reference(note_type: str, attachment_id: str, filename: str, alt: str) -> str:
    """The snippet that makes an attached image show up in a note of this type.

    The two target types resolve images differently and neither form works in
    the other. Markdown notes are rendered by a consumer (the presenter) that
    maps the `![alt](name)` target onto the note's attachment titles, so the
    reference stays a bare filename and survives the attachment being replaced.
    `text` notes are HTML rendered by Trilium itself, which resolves nothing —
    the attachment's own URL has to be in the content.
    """
    if note_type == "code":
        return f"![{alt}]({filename})"
    return (f'<img src="api/attachments/{attachment_id}/image/{quote(filename)}"'
            f' alt="{alt}">')


@mcp.tool()
def attach_image(
    note_id: str,
    filename: str,
    data_base64: str,
    alt: str = "",
    mime: str | None = None,
) -> str:
    """Attach an image to an existing note and get back the reference to embed.

    Images are attachments of the note that shows them — attach first, then put
    the returned `reference` into the note body with update_note. The note must
    already exist, so the order is create_note → attach_image → update_note.

    The filename is the attachment title and is how the image is referenced, so
    keep it short and unique within the note; attaching a second image under a
    name the note already uses is refused rather than resolved arbitrarily.

    Args:
        note_id: The note to attach to — the one whose content shows the image.
        filename: File name incl. extension, e.g. 'architecture.png'. Must be a
                  plain name: no slashes. Allowed: png, jpg, jpeg, gif, webp, svg.
        data_base64: The image file, base64-encoded.
        alt: Alt text describing the image.
        mime: Override the MIME type. Defaults to the one implied by the extension.
    Returns:
        JSON with attachmentId, filename, bytes and `reference` — the snippet to
        paste into the note content, already in the right form for the note type.
    """
    if not _FILENAME_RE.match(filename or ""):
        raise ValueError(
            f"Invalid filename {filename!r} — expected a plain file name with no "
            "slashes, e.g. 'diagram.png'."
        )
    extension = os.path.splitext(filename)[1].lower()
    if extension not in _IMAGE_MIMES:
        raise ValueError(
            f"Unsupported image extension {extension or '(none)'!r} — "
            f"expected one of {', '.join(sorted(_IMAGE_MIMES))}."
        )
    try:
        data = base64.b64decode(data_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"data_base64 is not valid base64: {exc}") from exc
    if not data:
        raise ValueError("data_base64 decoded to zero bytes — nothing to attach.")
    if len(data) > _MAX_ATTACHMENT_BYTES:
        raise ValueError(
            f"Image is {len(data)} bytes, over the {_MAX_ATTACHMENT_BYTES}-byte limit."
        )

    note = _get(f"notes/{_id(note_id)}")
    existing = _get(f"notes/{_id(note_id)}/attachments")
    if any(a.get("title") == filename for a in existing):
        raise ValueError(
            f"Note {note_id} already has an attachment titled {filename!r}. Consumers "
            "match the reference in the note body against attachment titles, so a "
            "duplicate makes which image renders undecidable — pick another filename."
        )

    attachment = _post("attachments", {
        "ownerId": note_id,
        "role": "image",
        "mime": mime or _IMAGE_MIMES[extension],
        "title": filename,
    })
    attachment_id = attachment["attachmentId"]
    _put_attachment_content(attachment_id, data)
    return json.dumps({
        "attachmentId": attachment_id,
        "filename": filename,
        "bytes": len(data),
        "reference": _image_reference(note.get("type", "text"), attachment_id, filename, alt),
    })


@mcp.tool()
def get_note_info(note_id: str) -> str:
    """Get metadata for a note (title, type, children, attributes).

    Args:
        note_id: The Trilium note ID.
    Returns:
        JSON with note metadata.
    """
    return json.dumps(_get(f"notes/{_id(note_id)}"))


@mcp.tool()
def list_children(parent_note_id: str = DEFAULT_PARENT) -> str:
    """List the direct children of a note, for navigation while authoring.

    This is plain tree navigation — it does not resolve a presentation the way
    the presenter does. Use it to drill down and find where to create notes.

    Args:
        parent_note_id: Parent note ID to list children from.
    Returns:
        JSON array of {noteId, title, type, childCount, hasChildren}.
    """
    note = _get(f"notes/{_id(parent_note_id)}")
    results = []
    for child_id in note.get("childNoteIds", []):
        child = _get(f"notes/{_id(child_id)}")
        child_ids = child.get("childNoteIds", [])
        results.append({
            "noteId": child_id,
            "title": child["title"],
            "type": child["type"],
            "childCount": len(child_ids),
            "hasChildren": bool(child_ids),
        })
    return json.dumps(results)


@mcp.tool()
def clone_node(note_id: str, target_parent_id: str, prefix: str | None = None) -> str:
    """Clone a note into another parent (a Trilium branch, not a copy).

    The note then appears under both parents; editing it updates it everywhere.

    Args:
        note_id: The note ID to clone.
        target_parent_id: The parent note ID to clone into.
        prefix: Optional branch prefix.
    Returns:
        JSON with the new branchId.
    """
    body = {"noteId": note_id, "parentNoteId": target_parent_id}
    if prefix:
        body["prefix"] = prefix
    result = _post("branches", body)
    return json.dumps({"branchId": result["branchId"], "noteId": note_id})


@mcp.tool()
def move_node(branch_id: str, new_position: int) -> str:
    """Move a note to the n-th place among its siblings.

    `new_position` is a **0-based index**, not a Trilium notePosition: 0 makes
    the note first, 1 second, and any value at or past the end makes it last.
    Negative values clamp to 0.

    Why this renumbers the whole sibling set rather than writing one number:
    Trilium keeps order as an integer on the *branch* and spaces siblings ten
    apart (10, 20, 30 …). This used to write `new_position * 10`, which meant
    every reachable value landed exactly on a sibling that was already there —
    "insert between these two" was not expressible at all, and the order that
    came out of the tie was Trilium's to decide, not the caller's. Measured on a
    live instance: moving a note to index 2 in a seven-slide deck put it third
    only because the tie happened to break that way, and passing 25 in the hope
    of landing between 20 and 30 wrote 250 and moved the note to the end.

    Rewriting every sibling costs one PATCH per note that actually shifts, on an
    operation that is rare and small (the children of one note). Positions that
    already match are left alone, so a no-op move writes nothing.

    Use list_children / get_note_info to find the branchId.

    Args:
        branch_id: The branch ID of the note to move.
        new_position: 0-based index among the siblings after the move.
    Returns:
        JSON with the index actually used and how many branches were renumbered.
    """
    branch = _get(f"branches/{_id(branch_id)}")
    parent = _get(f"notes/{_id(branch['parentNoteId'])}")

    # Read every sibling's position: childBranchIds carries no order of its own,
    # and the order is the thing being changed, so it cannot be assumed.
    positions: dict[str, int] = {}
    for sibling_id in parent.get("childBranchIds", []):
        sibling = branch if sibling_id == branch_id else _get(f"branches/{_id(sibling_id)}")
        positions[sibling_id] = sibling.get("notePosition", 0)
    positions.setdefault(branch_id, branch.get("notePosition", 0))

    order = sorted((b for b in positions if b != branch_id), key=lambda b: positions[b])
    index = max(0, min(int(new_position), len(order)))
    order.insert(index, branch_id)

    renumbered = 0
    for i, sibling_id in enumerate(order, start=1):
        want = i * 10
        if positions.get(sibling_id) != want:
            _patch(f"branches/{_id(sibling_id)}", {"notePosition": want})
            renumbered += 1
    return json.dumps({"branchId": branch_id, "newPosition": index,
                       "renumbered": renumbered})


@mcp.tool()
def delete_note(note_id: str) -> str:
    """Delete a note.

    Args:
        note_id: The note ID to delete.
    Returns:
        JSON confirming deletion.
    """
    _delete(f"notes/{_id(note_id)}")
    return json.dumps({"noteId": note_id, "deleted": True})


@mcp.tool()
def search_notes(query: str, limit: int = 20) -> str:
    """Search Trilium notes by title or content.

    Supports Trilium's search syntax, e.g.:
    - Plain text: "introduction"
    - By label: "#notecastType"
    - By label value: "#notecastType=slide"

    Args:
        query: Search query string.
        limit: Maximum number of results (default 20).
    Returns:
        JSON array of matching notes with noteId and title.
    """
    notes = _search(query, limit)
    return json.dumps([{"noteId": n["noteId"], "title": n["title"]} for n in notes])


# ── Live note formats in prompt + tool descriptions ──────────────────────────
# MCP *prompts* are not reliably injected into the model's context — only tool
# names, descriptions and schemas are guaranteed to arrive. So the authoring
# formats of every #notecastType are embedded directly into the create_note /
# update_note descriptions at tools/list time. The Trilium notes stay the single
# source of truth; editing one changes what the model sees on the next connection.

_FORMAT_TOOLS = ("create_note", "update_note")
_TYPE_BLOCK_TTL = 60.0


class _TypeBlockCache(TypedDict):
    """Two values of different types in one dict — spelled out so the TTL
    comparison below reads `ts` as a float rather than as a bare object."""

    text: str | None
    ts: float


_type_block_cache: _TypeBlockCache = {"text": None, "ts": 0.0}
_base_descriptions: dict[str, str] = {}

_TOOL_WORKFLOW_HEADER = f"""# Notecast — Note Creation

## Tool workflow
1. `list_note_types()` → discover the available note types.
2. `create_note(note_type, title, content, parent_note_id)` for each note,
   following that type's format below.
3. `update_note` / `get_note` to edit; `list_children` to navigate;
   `clone_node` / `move_node` to reuse and reorder.
4. `attach_image(note_id, filename, data_base64)` to add an image to a note that
   already exists, then put the `reference` it returns into the note content.

The formats below are loaded live from the Trilium notes labelled
#{TYPE_LABEL}=<id>, so their authors maintain them in one place.
"""


def _build_type_block() -> str:
    """Assemble the per-type format block injected into descriptions and the prompt.

    Enumerates every #notecastType and appends its live format. Missing content or
    a duplicated id surfaces as the per-type STOP notice. Cached for a short TTL
    to survive a tools/list burst; a transient discovery failure is not cached
    (so it recovers next call).

    The enumeration is done once here and the resulting notes are handed to
    `_fetch_type_definition`, which would otherwise search and re-fetch each one
    — this runs on every client connect, and it used to mean a request storm
    against ETAPI before a single tool could be listed.
    """
    now = time.monotonic()
    cached = _type_block_cache["text"]
    if isinstance(cached, str) and now - _type_block_cache["ts"] < _TYPE_BLOCK_TTL:
        return cached
    try:
        all_notes, truncated = _type_notes()
        groups = _group_types_by_id(all_notes)
    except Exception as exc:  # transient — do not cache a wrong "no types"
        logger.warning("Could not discover note types: %s", exc)
        return _NO_TYPES_NOTICE
    if truncated:
        # We may not have seen every definition note, so "this id appears once in
        # what we fetched" no longer proves it is unique. Fall back to the
        # per-type lookup, which does its own targeted search — slower, but it is
        # the check the ambiguity guard actually rests on.
        logger.warning(
            "More than %d notes carry #%s — falling back to per-type lookup",
            _TYPE_SEARCH_LIMIT, TYPE_LABEL,
        )
    if not groups:
        block = _NO_TYPES_NOTICE
    else:
        sections = []
        for type_id, notes in groups.items():
            if len(notes) > 1:
                logger.warning(
                    "Multiple notes carry #%s=%s — refusing to pick one", TYPE_LABEL, type_id
                )
                sections.append(f"### Note type `{type_id}`\n{_type_ambiguous_notice(type_id)}")
                continue
            fmt, _mech, source = _fetch_type_definition(
                type_id, note=None if truncated else notes[0]
            )
            head = f"### Note type `{type_id}`"
            if source:
                head += f" (Trilium note {source})"
            sections.append(f"{head}\n{fmt}")
        block = (
            f"--- Note formats (live from Trilium #{TYPE_LABEL} notes) ---\n"
            + "\n\n".join(sections)
        )
    _type_block_cache.update(text=block, ts=now)
    return block


@mcp.prompt()
def note_creation_guide() -> list[TextContent]:
    """Workflow + the live authoring formats for every defined note type.

    Loads each #notecastType note's format from Trilium (the single source of
    truth), prefixed with the tool workflow. Missing/duplicated types surface as
    loud STOP notices — never a silent built-in substitute.
    """
    return [TextContent(type="text", text=f"{_TOOL_WORKFLOW_HEADER}\n{_build_type_block()}")]


@mcp._mcp_server.list_tools()
async def _list_tools_with_live_format():
    """list_tools handler that embeds the live note formats into the create_note
    / update_note descriptions (overrides FastMCP's default).

    Uses two private SDK attributes — `mcp._mcp_server` to register a handler
    ahead of FastMCP's own, and `mcp._tool_manager` to reach the registered Tool
    objects. The mutation works because ToolManager.list_tools() hands back the
    live objects rather than copies, and `_base_descriptions` keeps the block
    from being appended twice. Both are unsupported surface: this is a reason the
    `mcp` pin in requirements.txt is exact, and the first thing to re-verify when
    that pin is bumped.
    """
    block = _build_type_block()
    for tool in mcp._tool_manager.list_tools():
        if tool.name in _FORMAT_TOOLS:
            base = _base_descriptions.setdefault(tool.name, tool.description)
            tool.description = f"{base}\n\n{block}"
    return await mcp.list_tools()


class HealthzMiddleware:
    """Answer /healthz, always and without authentication.

    Deliberately its own layer *outside* the bearer check rather than a branch
    inside it: a health probe carries no token, and when it lived in
    BearerAuthMiddleware the endpoint simply did not exist unless MCP_AUTH_TOKEN
    happened to be set — so deploy.sh, which polls it, reported a healthy server
    as a failed deployment.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope.get("path") == "/healthz":
            await _plain_response(send, 200, b"ok")
            return
        await self.app(scope, receive, send)


class BearerAuthMiddleware:
    """Pure-ASGI bearer check.

    Deliberately not a BaseHTTPMiddleware subclass: that one buffers the
    response and would break the streaming/SSE responses of the MCP transport.
    """

    def __init__(self, app, token: str):
        self.app = app
        # Compared as bytes, exactly as the header arrives: decoding first means
        # a non-UTF-8 header raises UnicodeDecodeError, and compare_digest on str
        # rejects non-ASCII with a TypeError — either turns a request anyone can
        # send into a 500 with a traceback instead of a clean 401.
        self.expected = f"Bearer {token}".encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        provided = headers.get(b"authorization", b"")
        if not compare_digest(provided, self.expected):
            logger.warning("Rejected unauthenticated request to %s", scope.get("path"))
            await _plain_response(send, 401, b"unauthorized")
            return
        await self.app(scope, receive, send)


async def _plain_response(send, status: int, body: bytes) -> None:
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [
            (b"content-type", b"text/plain; charset=utf-8"),
            (b"content-length", str(len(body)).encode()),
        ],
    })
    await send({"type": "http.response.body", "body": body})


def _run_http() -> None:
    import uvicorn
    from mcp.server.transport_security import TransportSecuritySettings

    mcp.settings.streamable_http_path = MCP_PATH
    if MCP_ALLOWED_HOSTS or MCP_ALLOWED_ORIGINS:
        mcp.settings.transport_security = TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=MCP_ALLOWED_HOSTS,
            allowed_origins=MCP_ALLOWED_ORIGINS,
        )
        logger.info("DNS-rebinding protection allows hosts: %s", MCP_ALLOWED_HOSTS)
    app = mcp.streamable_http_app()
    if MCP_AUTH_TOKEN:
        app = BearerAuthMiddleware(app, MCP_AUTH_TOKEN)
    else:
        logger.warning(
            "MCP_AUTH_TOKEN is not set — the endpoint is unauthenticated. "
            "Only expose it inside a trusted network."
        )
    # Outermost, so the probe answers with or without a bearer token.
    app = HealthzMiddleware(app)
    logger.info("Serving MCP over HTTP on %s:%s%s", MCP_HOST, MCP_PORT, MCP_PATH)
    uvicorn.run(app, host=MCP_HOST, port=MCP_PORT, log_level="info")


if __name__ == "__main__":
    if MCP_TRANSPORT == "streamable-http":
        logging.basicConfig(level=logging.INFO)
        _run_http()
    else:
        mcp.run()

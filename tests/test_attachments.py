"""Image attachments — the server's only binary path.

The rules asserted here are not style choices, they were measured against a live
Trilium by tools/probe-attachment-binary.py:

- `application/octet-stream` is the only Content-Type that stores bytes intact;
- the image's real MIME (`image/png`) makes the content endpoint answer 500;
- `text/plain` — what `_put_content` sends — decodes the body as UTF-8 and turns
  every byte >= 0x80 into U+FFFD, returning a corrupt image about twice the size.

The last one is why `test_bytes_above_7_bit_survive` uses all 256 byte values:
an ASCII-only payload passes even through the broken path.
"""
import base64
import json

import pytest

ALL_BYTES = bytes(range(256))
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + bytes(range(64))


def attach(server, note_id="note01", filename="diagram.png", data=PNG_BYTES, **kwargs):
    return json.loads(server.attach_image(
        note_id, filename, base64.b64encode(data).decode(), **kwargs))


@pytest.fixture
def note(trilium):
    """A markdown note (target type `code`) — the common case."""
    trilium.add_note("note01", "Slide 1", "code", mime="text/x-markdown")
    return trilium


def test_the_attachment_carries_title_role_and_mime(server, note):
    result = attach(server)

    stored = note.attachments[result["attachmentId"]]
    assert stored["ownerId"] == "note01"
    assert stored["role"] == "image"
    assert stored["mime"] == "image/png"
    # The title is the filename because that is what consumers match the
    # reference in the note body against.
    assert stored["title"] == "diagram.png"


def test_content_goes_out_as_octet_stream(server, note):
    """The image's own MIME here is a 500 from Trilium — see the module docstring."""
    attach(server)

    _aid, content_type, _body = note.content_writes[0]
    assert content_type == "application/octet-stream"


def test_bytes_above_7_bit_survive(server, note):
    """The regression for the UTF-8 mangling: ASCII-only payloads hide it."""
    result = attach(server, data=ALL_BYTES)

    assert note.attachment_content[result["attachmentId"]] == ALL_BYTES
    assert result["bytes"] == 256


def test_a_markdown_note_gets_a_filename_reference(server, note):
    """Bare filename: the presenter maps it onto the note's attachment titles."""
    result = attach(server, alt="The architecture")

    assert result["reference"] == "![The architecture](diagram.png)"


def test_a_text_note_gets_an_html_reference(server, trilium):
    """`text` notes are HTML rendered by Trilium, which resolves no filenames."""
    trilium.add_note("html01", "A letter", "text")

    result = attach(server, note_id="html01", filename="logo.png", alt="Logo")

    assert result["reference"] == (
        '<img src="api/attachments/att01/image/logo.png" alt="Logo">')


def test_a_filename_needing_escaping_is_escaped_in_the_url(server, trilium):
    trilium.add_note("html01", "A letter", "text")

    result = attach(server, note_id="html01", filename="my diagram.png")

    assert "my%20diagram.png" in result["reference"]


def test_the_mime_can_be_overridden(server, note):
    result = attach(server, filename="chart.svg", mime="image/svg+xml")

    assert note.attachments[result["attachmentId"]]["mime"] == "image/svg+xml"


def test_svg_is_allowed_by_extension_alone(server, note):
    """The slide templates use .svg; it reaches the page as <img src>."""
    result = attach(server, filename="chart.svg")

    assert note.attachments[result["attachmentId"]]["mime"] == "image/svg+xml"


def test_a_duplicate_filename_is_refused(server, note):
    attach(server, filename="diagram.png")

    with pytest.raises(ValueError, match="already has an attachment"):
        attach(server, filename="diagram.png")

    assert len(note.attachments) == 1


def test_a_second_image_under_a_different_name_is_fine(server, note):
    attach(server, filename="one.png")
    attach(server, filename="two.png")

    assert len(note.attachments) == 2


@pytest.mark.parametrize("filename", [
    "sub/diagram.png",      # the presenter never resolves a slash-shaped target
    "..\\diagram.png",
    "",
    "diagram\n.png",
])
def test_path_shaped_filenames_are_refused(server, note, filename):
    with pytest.raises(ValueError, match="Invalid filename"):
        attach(server, filename=filename)

    assert not note.attachments


@pytest.mark.parametrize("filename", ["notes.pdf", "script.svgz", "diagram"])
def test_non_image_extensions_are_refused(server, note, filename):
    with pytest.raises(ValueError, match="Unsupported image extension"):
        attach(server, filename=filename)


def test_invalid_base64_is_refused(server, note):
    with pytest.raises(ValueError, match="not valid base64"):
        server.attach_image("note01", "diagram.png", "not base64 !!")

    assert not note.attachments


def test_an_empty_payload_is_refused(server, note):
    with pytest.raises(ValueError, match="zero bytes"):
        attach(server, data=b"")


def test_an_oversized_payload_is_refused(server, note, monkeypatch):
    monkeypatch.setattr(server, "_MAX_ATTACHMENT_BYTES", 128)

    with pytest.raises(ValueError, match="over the 128-byte limit"):
        attach(server, data=ALL_BYTES)

    assert not note.attachments


def test_the_note_id_is_validated_before_it_reaches_a_url(server, note):
    """Same promise as every other tool: IDs come from the model."""
    with pytest.raises(ValueError, match="Invalid Trilium ID"):
        attach(server, note_id="note01/../../etapi/app-info")

    assert not note.attachments

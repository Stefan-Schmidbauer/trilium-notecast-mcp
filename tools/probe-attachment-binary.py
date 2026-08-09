#!/usr/bin/env python3
"""Check that ETAPI still carries raw bytes through an attachment roundtrip.

`attach_image` is the only binary path in this server, and the way it has to
write content is not what the ETAPI spec suggests: the spec declares
`PUT /attachments/{id}/content` as `text/plain` + `string`, with no binary form
at all. What a live instance actually does was measured with this script:

    Content-Type: application/octet-stream   256 B in, 256 B out, hash equal
    Content-Type: image/png                  HTTP 500
    Content-Type: text/plain                 256 B in, 512 B out, corrupt
    content inline in POST /attachments      corrupt

`text/plain` is the interesting failure: the body is decoded as UTF-8, so every
byte >= 0x80 becomes U+FFFD (EF BF BD) and the payload roughly doubles — an
image written that way is silently destroyed rather than rejected. The 500 on
`image/png` is a Trilium bug (an unhandled Content-Type should be a 415), and it
is the trap anyone reaches for first when uploading a PNG.

Run this after a Trilium upgrade. If `application/octet-stream` ever stops
passing, `_put_attachment_content` in server.py needs to change with it.

    export TRILIUM_URL=... TRILIUM_API_KEY=...
    python3 tools/probe-attachment-binary.py

It creates one throwaway note under `root`, attaches its probes there, and
deletes the note again in a `finally` block. Exit code is 0 only if a real PNG
survives the roundtrip.
"""
import hashlib
import os
import struct
import sys
import zlib

import httpx

URL = os.environ.get("TRILIUM_URL", "http://localhost:8080").rstrip("/")
KEY = os.environ.get("TRILIUM_API_KEY", "")

if not KEY:
    sys.exit("TRILIUM_API_KEY is not set.")

client = httpx.Client(headers={"Authorization": KEY}, timeout=30.0)

# Every byte value, so the payload contains sequences that are invalid UTF-8.
# A PNG alone is a weaker probe: it might survive a lossy path by luck.
ALL_BYTES = bytes(range(256))


def make_png() -> bytes:
    """A real 2x2 PNG, built here so its bytes are known exactly."""
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0)  # 2x2, 8-bit RGB
    raw = b"\x00\xff\x00\x00\x00\x00\xff\x00\x00\x00\xff\x00\xff\xff\xff"
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


PNG = make_png()
results: dict[str, bool] = {}


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()[:16]


def report(label: str, sent: bytes, got: bytes | None, err: str | None = None) -> bool:
    if err:
        print(f"  {label:<34} ERROR  {err}")
        return False
    ok = sent == got
    print(f"  {label:<34} {'OK  ' if ok else 'FAIL'}   "
          f"sent {len(sent)}B/{sha(sent)}  got {len(got)}B/{sha(got)}")
    if not ok:
        # strict=False on purpose: a length mismatch is the corruption we are
        # reporting, so it must not raise before the divergence is printed.
        for i, (a, b) in enumerate(zip(sent, got, strict=False)):
            if a != b:
                print(f"       first divergence at byte {i}: sent 0x{a:02x} got 0x{b:02x}")
                break
        else:
            print(f"       prefix identical; length differs by {len(got) - len(sent)}")
    return ok


def make_attachment(note_id: str, title: str, mime: str) -> str:
    r = client.post(f"{URL}/etapi/attachments",
                    headers={"Content-Type": "application/json"},
                    json={"ownerId": note_id, "role": "image", "mime": mime, "title": title})
    r.raise_for_status()
    return r.json()["attachmentId"]


def put_and_get(att_id: str, payload: bytes, content_type: str) -> tuple[bytes | None, str | None]:
    try:
        r = client.put(f"{URL}/etapi/attachments/{att_id}/content",
                       headers={"Content-Type": content_type}, content=payload)
        r.raise_for_status()
    except Exception as e:  # noqa: BLE001 - any failure is itself a result
        return None, f"PUT {type(e).__name__}: {e}"
    try:
        g = client.get(f"{URL}/etapi/attachments/{att_id}/content")
        g.raise_for_status()
        return g.content, None
    except Exception as e:  # noqa: BLE001
        return None, f"GET {type(e).__name__}: {e}"


note_id = None
try:
    print(f"Trilium at {URL}\n")
    r = client.post(f"{URL}/etapi/create-note",
                    headers={"Content-Type": "application/json"},
                    json={"parentNoteId": "root", "title": "ZZ TEMP notecast attachment probe",
                          "type": "text", "content": "throwaway - deleted by the probe script"})
    r.raise_for_status()
    note_id = r.json()["note"]["noteId"]
    print(f"probe note {note_id}\n")

    print("A. PUT /attachments/{id}/content, by Content-Type")
    for content_type in ("application/octet-stream", "image/png", "text/plain"):
        aid = make_attachment(note_id, f"probe-{content_type.replace('/', '-')}.bin", "image/png")
        got, err = put_and_get(aid, ALL_BYTES, content_type)
        results[f"all bytes via {content_type}"] = report(
            f"ALL_BYTES  CT={content_type}", ALL_BYTES, got, err)

    print("\nB. A real PNG over the supported Content-Type")
    aid = make_attachment(note_id, "probe.png", "image/png")
    got, err = put_and_get(aid, PNG, "application/octet-stream")
    results["png"] = report("PNG        CT=octet-stream", PNG, got, err)

    print("\nC. Inline content in POST /attachments (the spec's 'string')")
    try:
        r = client.post(f"{URL}/etapi/attachments",
                        headers={"Content-Type": "application/json"},
                        json={"ownerId": note_id, "role": "image", "mime": "image/png",
                              "title": "probe-inline.png", "content": PNG.decode("latin-1")})
        r.raise_for_status()
        g = client.get(f"{URL}/etapi/attachments/{r.json()['attachmentId']}/content")
        g.raise_for_status()
        results["inline"] = report("PNG inline in POST body", PNG, g.content)
    except Exception as e:  # noqa: BLE001
        results["inline"] = report("PNG inline in POST body", PNG, None,
                                   f"{type(e).__name__}: {e}")

    print("\nD. The attachment hangs off the note with role=image")
    try:
        g = client.get(f"{URL}/etapi/notes/{note_id}/attachments")
        g.raise_for_status()
        atts = g.json()
        print(f"  {len(atts)} attachment(s), roles={sorted({a.get('role') for a in atts})}")
        results["listing"] = bool(atts) and {a.get("role") for a in atts} == {"image"}
    except Exception as e:  # noqa: BLE001
        print(f"  listing ERROR {type(e).__name__}: {e}")
        results["listing"] = False

finally:
    if note_id:
        try:
            client.delete(f"{URL}/etapi/notes/{note_id}").raise_for_status()
            print(f"\ncleaned up probe note {note_id}")
        except Exception as e:  # noqa: BLE001
            print(f"\n!! CLEANUP FAILED for note {note_id}: {e} — delete it by hand")

print("\n=== verdict ===")
for label, passed in results.items():
    print(f"  {'pass' if passed else 'FAIL'}  {label}")
print("\nExpected: only application/octet-stream and the PNG and listing pass;\n"
      "the other three are the documented failure modes, not regressions.")
sys.exit(0 if results.get("png") and results.get("listing") else 1)

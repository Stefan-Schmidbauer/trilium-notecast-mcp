#!/usr/bin/env python3
"""Create (or remove) one throwaway note type, to exercise the engine end to end.

This server ships no type definitions — it is a pure engine, and with no type
notes `create_note` correctly refuses. That makes it awkward to check a fresh
deployment without first installing an output plugin, which is what this script
is for: it tags one note, so `list_note_types` has something to find and
`create_note` has something to write.

The real types live with the plugin that renders them — the `slide` type in
trilium-presenter-plugin, the document types (note, kbEntry, meetingNote,
checklist, letter) in trilium-notecast-render. This is not a substitute for
either; it is a smoke test you delete again.

    export TRILIUM_URL=... TRILIUM_API_KEY=...
    python3 tools/seed-demo-type.py            # create it
    python3 tools/seed-demo-type.py --remove   # delete it again

Creating it twice on purpose is a useful test too: two notes carrying the same
#notecastType make the server refuse as ambiguous, which is the behaviour the
clarity guard exists for.
"""
import os
import sys

import httpx

URL = os.environ.get("TRILIUM_URL", "http://localhost:8080").rstrip("/")
KEY = os.environ.get("TRILIUM_API_KEY", "")
PARENT = os.environ.get("TRILIUM_DEFAULT_PARENT", "root")

TYPE_ID = "demoNote"
TITLE = "Demo Note — Notecast type (safe to delete)"

ATTRIBUTES = {
    "notecastType": TYPE_ID,
    "notecastTargetType": "code",
    "notecastMime": "text/x-markdown",
    "notecastApplyLabels": "demoMarker=yes",
    "notecastPrefix": "Demo",
}

FORMAT = """# Demo Note

A throwaway type for checking that the engine resolves types and applies
mechanics. Created as a Trilium **code** note with mime `text/x-markdown`.

## Format
- One `# H1` matching the note title.
- Two or three sentences. Nothing else.

## Conventions & Voice
Language and tone are NOT fixed here. If the author has not said which to use,
ask — do not guess.

## Attributes

The labels this definition carries — the contract's convention, so a definition
says in its content what makes it one.

| Label | Value |
|---|---|
""" + "\n".join(f"| `#{name}` | `{value}` |" for name, value in ATTRIBUTES.items()) + "\n"
# The table is rendered from ATTRIBUTES rather than typed beside it: the plugins
# parse their table to get the labels, and this script has no file to parse — so
# it generates the other way round, from the dict it stamps. Same single source,
# opposite direction.


def headers() -> dict:
    return {"Authorization": KEY, "Content-Type": "application/json"}


def find(client: httpx.Client) -> list[str]:
    r = client.get(f"{URL}/etapi/notes", headers=headers(),
                   params={"search": f"#notecastType={TYPE_ID}"})
    r.raise_for_status()
    return [n["noteId"] for n in r.json().get("results", [])]


def main() -> None:
    if not KEY:
        sys.exit("TRILIUM_API_KEY is not set.")
    remove = "--remove" in sys.argv

    with httpx.Client(timeout=10) as c:
        existing = find(c)

        if remove:
            if not existing:
                print(f"Nothing to remove — no note carries #notecastType={TYPE_ID}.")
                return
            for note_id in existing:
                c.delete(f"{URL}/etapi/notes/{note_id}", headers=headers()).raise_for_status()
                print(f"removed {note_id}")
            print("\nNote: notes *created* from this type are left alone — they are\n"
                  f"tagged #notecastInstance={TYPE_ID} if you want to find them.")
            return

        if existing:
            print(f"Careful: {len(existing)} note(s) already carry "
                  f"#notecastType={TYPE_ID}: {', '.join(existing)}")
            print("Adding another makes the type ambiguous and the server will "
                  "refuse it.\nRun with --remove first, or continue deliberately.")

        r = c.post(f"{URL}/etapi/create-note", headers=headers(), json={
            "parentNoteId": PARENT,
            "title": TITLE,
            "type": "code",
            "mime": "text/x-markdown",
            "content": FORMAT,
        })
        r.raise_for_status()
        note_id = r.json()["note"]["noteId"]

        for name, value in ATTRIBUTES.items():
            c.post(f"{URL}/etapi/attributes", headers=headers(), json={
                "noteId": note_id, "type": "label", "name": name, "value": value,
            }).raise_for_status()

        print(f"created {TYPE_ID} -> {note_id}")
        print("\nReconnect the MCP client, then:\n"
              "  list_note_types()\n"
              f'  create_note(note_type="{TYPE_ID}", title="…", content="…")\n'
              "\nClean up with: python3 tools/seed-demo-type.py --remove")


if __name__ == "__main__":
    main()

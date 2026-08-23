#!/usr/bin/env python3
"""Check what `DELETE /branches/{id}` does to the note behind the branch.

`unlink_branch` and `move_to_parent` both delete a branch, and both are built on
one assumption the ETAPI spec does not state: removing a note's *last* branch
does not merely unfile the note, it deletes the note. Everything about the
guards follows from that — `unlink_branch` refuses the last branch, and
`move_to_parent` writes the new placement before removing the old one, so a
failure between the two leaves the note in two places rather than in none.

This script measures three things:

    1. delete a branch while another one remains  → note must survive
    2. delete the only remaining branch           → does the note survive?
    3. POST /branches for an existing note/parent → new branch, or the old id?

(3) is already measured — a live instance answered with the *existing* branchId
rather than creating a second placement, which is why `move_to_parent` refuses a
move whose source and target parent are the same: the create would hand back the
branch that is about to be deleted, and the note would lose its only placement.
It is re-measured here because the two behaviours together are what make the
delete half safe.

Run this after a Trilium upgrade. If (2) ever reports that the note survives,
the `unlink_branch` guard is merely tidy rather than load-bearing — and if (3)
ever starts creating a second branch, the same-parent refusal can be relaxed
into a plain reorder.

    export TRILIUM_URL=... TRILIUM_API_KEY=...
    python3 tools/probe-branch-deletion.py

It creates one throwaway note and two throwaway folders under `root` and removes
whatever is left of them in a `finally` block. Exit code is 0 when the measured
behaviour still matches what server.py assumes.
"""
import os
import sys

import httpx

URL = os.environ.get("TRILIUM_URL", "http://localhost:8080").rstrip("/")
KEY = os.environ.get("TRILIUM_API_KEY", "")

if not KEY:
    sys.exit("TRILIUM_API_KEY is not set.")

client = httpx.Client(headers={"Authorization": KEY}, timeout=30.0)
JSON = {"Content-Type": "application/json"}


def create_note(parent_id: str, title: str) -> str:
    r = client.post(f"{URL}/etapi/create-note", headers=JSON, json={
        "parentNoteId": parent_id, "title": title,
        "type": "text", "content": "probe",
    })
    r.raise_for_status()
    return r.json()["note"]["noteId"]


def note_exists(note_id: str) -> bool:
    return client.get(f"{URL}/etapi/notes/{note_id}").status_code == 200


def placements(note_id: str) -> list[str]:
    r = client.get(f"{URL}/etapi/notes/{note_id}")
    r.raise_for_status()
    return r.json().get("parentBranchIds", [])


def main() -> int:
    created: list[str] = []
    failures: list[str] = []
    try:
        folder_a = create_note("root", "ZZ probe folder A")
        created.append(folder_a)
        folder_b = create_note("root", "ZZ probe folder B")
        created.append(folder_b)

        note_id = create_note(folder_a, "ZZ probe note")
        created.append(note_id)

        # (3) A second placement, then the same one again.
        r = client.post(f"{URL}/etapi/branches", headers=JSON,
                        json={"noteId": note_id, "parentNoteId": folder_b})
        r.raise_for_status()
        branch_b = r.json()["branchId"]

        r = client.post(f"{URL}/etapi/branches", headers=JSON,
                        json={"noteId": note_id, "parentNoteId": folder_b})
        r.raise_for_status()
        again = r.json()["branchId"]
        upserts = again == branch_b
        print(f"(3) create for an existing note/parent pair → "
              f"{'the existing branch id (upsert)' if upserts else 'a second branch'}")
        if not upserts:
            failures.append(
                "POST /branches now creates a second placement; the same-parent "
                "refusal in move_to_parent can become a reorder instead."
            )

        held = placements(note_id)
        print(f"    the note now has {len(held)} placement(s)")

        # (1) Delete one of two.
        r = client.delete(f"{URL}/etapi/branches/{branch_b}")
        r.raise_for_status()
        survived = note_exists(note_id)
        print(f"(1) deleting one of two placements → note "
              f"{'survives' if survived else 'IS GONE'}")
        if not survived:
            failures.append(
                "Deleting a non-last branch deleted the note. unlink_branch and "
                "move_to_parent are both unsafe until this is understood."
            )
            return 1

        # (2) Delete the last one.
        last = placements(note_id)[0]
        r = client.delete(f"{URL}/etapi/branches/{last}")
        r.raise_for_status()
        survived = note_exists(note_id)
        print(f"(2) deleting the last placement → note "
              f"{'survives (!)' if survived else 'is deleted, as assumed'}")
        if survived:
            created.append(note_id)
            failures.append(
                "The last branch can now be removed without deleting the note. "
                "The unlink_branch guard is no longer load-bearing — keep it or "
                "relax it deliberately, but stop citing this behaviour for it."
            )
    finally:
        for note_id in reversed(created):
            client.delete(f"{URL}/etapi/notes/{note_id}")

    for line in failures:
        print(f"\n⚠️  {line}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

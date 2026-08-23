# Concept: tree editing and note marking

Status: implemented. Kept as the record of why this surface looks the way it
does — the measurements behind the guards are in `tools/probe-branch-deletion.py`.

## Why

The server can build a tree but not reshape one. That gap showed up while
reviewing a real slide library (~110 master slides, three trainings assembled
from clones of them):

- Slides and handouts had accumulated in the same folder. Separating them was
  impossible — there was no way to move a note to a different parent, so the
  workaround was a `Themenblatt:` title prefix.
- Marking a slide's review state (current / outdated / rework) had no home
  either. Labels could only be stamped at creation time, through
  `create_note(labels=…)`.
- Reordering a deck needs a `branchId`, and `list_children` did not return one.
  Every reorder cost N+1 calls: list the children, then `get_note_info` each one
  to find its branch.

The asymmetry was the sharp edge: the only destructive operation exposed was
`delete_note`, which removes the note everywhere. "Take this slide out of this
training" — the safe, common operation — could not be expressed at all, so the
model's nearest reachable tool was the one that destroys the master.

## What ETAPI gives us

Trilium models placement as a *branch* (noteId + parentNoteId + notePosition +
prefix). The relevant endpoints:

| Endpoint | Use |
|---|---|
| `POST /branches` | place a note under a parent (already used by `clone_node`) |
| `PATCH /branches/{id}` | `notePosition`, `prefix` — **not** `parentNoteId` |
| `DELETE /branches/{id}` | remove one placement |

Three measurements drive the design:

1. A move is **create new branch + delete old branch**, not a patch.
2. Deleting a note's *last* branch deletes the note; deleting any other branch
   leaves the note alone. That is the single most important thing for the guards
   below to get right. **Measured on a live instance** — the same way the
   attachment content-type behaviour was measured rather than read out of the
   spec, which declares none of this.

3. `POST /branches` for a note/parent pair that already has a branch answers
   with the *existing* branchId rather than creating a second placement.
   **Measured on a live instance.** It is what makes a same-parent move
   dangerous rather than merely pointless: the create hands back the branch the
   delete is about to remove, so the note would lose its only placement.

## Proposed tools

### 1. `move_to_parent(note_id, source_parent_id, target_parent_id, position=None)` — *implemented*

The missing core operation. `source_parent_id` is required, not inferred: a note
that has been cloned has several parents, and guessing which placement to move
would silently reshape the wrong training.

Order of operations: create the new branch first, then delete the old one. If
the delete fails the note is temporarily in two places — visible and repairable.
The reverse order can leave a note with no branch at all.

Guards:
- refuse if `note_id` has no branch under `source_parent_id`
- refuse if `target_parent_id` is `note_id` or lies inside its subtree (cycle)
- refuse if source and target parent are the same (that is a `move_node`
  reorder, not a move — and see measurement 3 for why it is not harmless)

### 2. `unlink_branch(branch_id)` — *implemented*

Remove one placement without touching the note. This is "take this slide out of
this training".

The guard is the whole point: **refuse when it is the last branch.** The message
should name the alternative explicitly — that `delete_note` is the tool that
removes a note for good — so the refusal teaches the distinction instead of
looking like a bug to route around.

### 3. `set_labels(note_id, labels)` — *implemented*

Add or update labels on an existing note. `_set_attribute()` already does the
work; it is simply not reachable from any tool except through `create_note`.

Reuses `_validate_labels()` unchanged, so `notecastInstance` and `notecastType`
stay refused for the same reasons they are refused at creation.

Enables the review workflow that prompted this document: `#reviewStatus=outdated`,
`#material=handout` — queryable through `search_notes`, unlike a title prefix.

### 4. `remove_label(note_id, name)` — *implemented*

Counterpart to 3. `DELETE /attributes/{attributeId}` after looking the label up
on the note. Without it a wrong mark is permanent, which makes people reluctant
to mark anything.

### 5. `list_children`: return `branchId` — *implemented*

Not a new tool — one field on an existing one. It removes the N+1 that every
reordering pass pays today, and it is what makes `unlink_branch` usable: the
caller sees the branch it wants to remove in the listing it already made.

`notePosition` and `prefix` were dropped from this item during implementation.
Both live on the branch, so reporting them means a GET per child on top of the
one the listing already makes — doubling the requests to restate what the array
order already says, for fields no tool takes as an argument. `branchId` costs
nothing by comparison: parent and child each report the branches they sit on,
and the single id they agree on is the branch joining them.

### 6. `search_notes`: return `type` and `dateModified` — *implemented*

Reviewing a library means asking "what is old here". That is one search plus one
`get_note_info` per hit today. Two extra fields per result make it one call.
Both are null when the instance answers with bare note stubs.

## What shipped

All six are implemented, with tests in `test_type_resolution.py`.

One thing implementation added that the concept did not foresee: a note's
`attributes` include the ones it *inherits*, each tagged with its owner's
noteId. `_own_labels()` filters on that, and `_set_attribute()` was changed to
use it — before, a label matched by name alone could be patched on the template
it came from rather than on the note being edited.

Nothing is left assumed. `tools/probe-branch-deletion.py` ran against a live
instance on 2026-08-23 and confirmed all three measurements:

    (3) create for an existing note/parent pair → the existing branch id (upsert)
    (1) deleting one of two placements         → note survives
    (2) deleting the last placement            → note is deleted, as assumed

So `unlink_branch`'s refusal is a safety property rather than tidiness, and the
write-then-delete order in `move_to_parent` is load-bearing rather than
defensive. Re-run the probe after a Trilium upgrade; if (2) ever reports that
the note survives, both pieces of reasoning need correcting in the code
comments, not just relaxing.

## Non-goals

- **No bulk or recursive tree operations.** No "move this whole folder", no
  "reorganise by label". Primitives compose; a bulk tool that is wrong is wrong
  across a hundred notes at once.
- **No undo.** Trilium has its own recycle bin; duplicating that here would be a
  second source of truth about what still exists.
- **No type definitions.** Unchanged: this server stays a pure engine.

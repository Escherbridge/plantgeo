---
type: retrospective
status: historical
date: 2026-09-11
---

# Session and Conductor maintenance — 2026-09-11

This archive preserves superseded session instructions while the
[current registry](../../tracks.md) and [current runbook](../../RUNBOOK.md)
continue to own unfinished work. The user requested project session pruning,
track/runbook reconciliation, retrospectives, and archival of completed PlantGeo
tasks in the Codex sidebar. This pass changes documentation and session state.

## Preserved records

| Record | Outcome |
| --- | --- |
| [13 layer briefs and their index](layer-sessions/README.md) | August 25–26 measurements preserved; old entry points now redirect to current owners. |
| [Brief generator](layer-sessions/gen_prompts.py.txt) and [fact table](layer-sessions/lane_facts.py.txt) | Exact source retained as text. The generator had a hard-coded output path that could overwrite current briefs with the August census; it has no callers outside this historical bundle. |
| [August 5 handoff](handoff-2026-08-05.md) | Historical scheduler, serving and package decisions retained with original body and date. |
| [July 27 session log](session-log-2026-07-27.md) | Frozen-export checksum and failed squad-attempt lessons retained. |
| [Archive manifest](archive-manifest.json) | SHA-256 and byte counts for all 18 original files; 200,879 bytes copied and compared before entry-point replacement. |

Archived source files keep their original text, including status labels and
instructions that were current when written. Those labels are historical; this
index and the current registry establish archival status. Old file-and-line
citations refer to the corresponding archived original, whose lines are unchanged.

## Local session state

The August 25 rolling/ultrawork markers and September 4 ultrapilot marker are now
inactive historical pointers. Their exact previous bytes, including the
pre-existing uncommitted ultrapilot state, are recoverable under
`.omc/state/archive/session-hygiene-20260911/`; that local archive is ignored by Git.
No process or running task was stopped by changing these stale markers.

The local `.conductor_session_log` kept its 10 entries since September 4;
the full 99-entry original is retained under
`conductor/.runtime/archive/2026-09-11/conductor_session_log`. Its checksum is in
the local state archive's `manifest.json`. Historical log paths are events,
not a current file inventory.

The three uncommitted QA plans and retirement learnings remain in place because
they contain current work or useful findings. No worktree, source data, backup,
or global Codex session store was deleted.

## Lessons carried forward

- A session prompt ages independently of its parent track. Archive an obsolete
  prompt after handing remaining work to a current owner; do not mark the parent
  complete merely because the old session ended.
- A deployed implementation receipt can complete a bounded slice while
  schedule burn-in, recovery parity, or production acceptance remains open.
  Record the slice and its remaining gates separately.
- Keep one current operational entry point. Historical `LIVE`, `START HERE`,
  worker-running labels and hard-coded regeneration scripts otherwise compete
  with newer evidence after compaction.
- Preserve exact bytes before pruning local records, and keep a dated index
  and stable redirects for shared documentation. Sidebar archival is reversible
  and does not remove a task's worktree.

## Sidebar audit

The independent [sidebar audit](sidebar-audit.json) records all 49 inspected tasks
with exact titles, IDs, final-handoff evidence and successor references. Forty
scoped-complete or explicitly superseded tasks were archived; all 40 were found
in the archived-task listing and absent from the refreshed sidebar. All nine
retained tasks remained visible. Archival is reversible through the task ID and
does not delete a worktree.

The retained set is this maintenance task, the pinned QA and repair tasks, the
earlier data-task coordinator, and five data-loading tasks without final closure:
soil wetness, precipitation, dew point, burn severity and drought. Some original
full-source horizons exceed the later canonical snapshot's scope; keep these
open until their remaining obligations are reconciled.

The app exposes at most 50 unarchived tasks per listing without pagination.
Two archive-and-refresh passes revealed no further PlantGeo candidates in the
final visible window; this audit does not claim to inspect inaccessible older
tasks. The machine ledger retains the listing limitation and exact outcomes.

The [maintenance workflow](../../workflow.md#session-and-archive-maintenance)
applies these rules at subsequent handoffs. The independent
[verification receipt](verification.json) records documentation and archive
checks for the complete edit batch; it is not an application or release receipt.

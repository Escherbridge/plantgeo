---
type: workflow
---

# Conductor workflow

Follow the repository `AGENTS.md` for coding, testing, security, and Git
requirements. This document governs only Conductor state.

1. Choose work from [`tracks.md`](./tracks.md), not a historical track file.
2. Read the track metadata, specification, plan, linked evidence, and the
   applicable runtime/data contract before changing code or data.
3. Record the smallest truthful status change in both the registry and track
   metadata. Do not turn an external dependency into an `active` task.
4. Keep evidence immutable and dated. Add a current-state note or successor
   track instead of editing historical findings into a new conclusion.
5. Apply changes in one bounded writer lane; obtain an independent review for
   governance, statistical, security, or release-boundary changes.
6. After all edits, run one final verification sweep scoped to the affected
   boundaries by default. Use a full integrated sweep for cross-cutting changes
   or when release gates require it. Follow [`docs/testing.md`](../docs/testing.md)
   for commands, and identify the tested scope in the receipt: a scoped pass is
   not a full-suite or release acceptance result.

For a release, [`release-governance.md`](./release-governance.md) takes
precedence over every track. For data/forecast/ML work, an evaluation result is
not a publication or an intervention-effect claim.

## Session and archive maintenance

At each meaningful checkpoint or handoff, reconcile the work just completed:

1. Update the registry, metadata and current plan together using dated commits,
   receipts or review evidence. Separate code completion, deployment, measured
   production behavior and sustained schedule acceptance.
2. Write a short retrospective for a completed track or bounded slice: what
   shipped, evidence, lessons and the owner of each remaining gate. Archive a
   whole track only when all its deliverables are complete or explicitly
   superseded. Keep unfinished parent tracks in the current registry.
3. Keep `RUNBOOK.md` focused on current state and next actions. Move superseded
   handoffs into a dated archive, retain their original text, and link them from
   the current entry point. Preserve old paths as redirects where other records
   cite them; original line numbers refer to the archived source.
4. Prune project-local session state only after preserving an exact recovery
   copy and a dated manifest. Age alone does not establish completion. Keep
   active work, uncommitted plans and unresolved findings; a stale `active`
   marker may be retired as historical without marking its work complete.
   After rotation, retain at least the latest seven days of local session-log
   entries, with the full original in the ignored local runtime archive.
5. When sidebar cleanup is authorized, inspect each task's final handoff before
   archiving. Idle or unloaded is not a completion verdict. Preserve unresolved
   tasks and running tasks; record the exact title, task ID and reason for each
   archive so it can be restored. Archiving does not delete worktrees.
6. After the complete edit batch, obtain independent review and run the checks
   for the actual surface. A documentation pass checks metadata, local links,
   archive integrity and whitespace; it does not produce an application-test,
   Python quality-receipt or production acceptance claim.

The [September 11 maintenance retrospective](retros/session_hygiene_20260911/README.md)
records the first reconciliation under this procedure. Maintenance happens with
the work; this workflow does not schedule background cleanup.

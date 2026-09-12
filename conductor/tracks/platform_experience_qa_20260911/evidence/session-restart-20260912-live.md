---
type: evidence-receipt
track: platform_experience_qa_20260911
recorded_on: 2026-09-12
observed_at: 2026-09-12T10:43:10Z
status: restarted_read_only_owners
---

# Live restart and hang-handling receipt

This receipt records the current lifecycle state after restarting the two
unresolved PlantGeo owners. It is a session record only. No source, database,
object-store, writer, scheduler, deployment or remote publication action was
performed.

## Forecast owner

The paused forecast task `01a09323-6f59-7822-9e6f-146892776749` was restarted
with a read-only ownership and rebase audit. Its restart turn remained active
through the bounded wait and was then interrupted/stopped; the task is now
`idle` without changing files, refs, stashes or external state. No new
completed restart receipt was produced. Earlier completed audit evidence
remains retained in its historical custody record and confirms that
`codex/weather-forecast-20260911` remains superseded, diverges from current
`main`, and is not a safe merge or cherry-pick source. The preserved
future-forecast stash remains audit-only and unopened.

## Ingestion owner

The unresolved ingestion task `01a08b00-2a50-73c2-b39b-39523c74ceb2` was
restarted with a bounded read-only reconciliation prompt. Its turn remained
`inProgress` for the full 60-second wait with no assistant message, tool marker
or state revision. A stop prompt then returned it to `idle`; no command or tool
ran during the stopped turn, and no worktree, data or external state changed.
The historical candidate `3a5f3902e6f56b0878eab1d72b34789f6653058f` remains
unintegrated and unresolved under its existing custody record.

## Fresh queued work

Two fresh current-main, read-only tasks were requested so the hung owners are
not retried in place:

| Scope | Client queue identifier | Boundary | Materialized status at observation |
| --- | --- | --- | --- |
| Forecast replan and ownership audit | `client-new-thread:bfb15326-7832-47bc-a0cf-2415b0127c45` | Current-main forecast contract/rebase audit; no edits, provider, data, DB, Railway, deploy or push | Not present in `list_threads`; retain as a queue alias only |
| Ingestion candidate reconciliation | `client-new-thread:5a83705b-98c6-4717-b9bd-519af214817d` | Current-main candidate status review; no edits, ingestion, data, DB, Railway, deploy or push | Not present in `list_threads`; retain as a queue alias only |

The queue aliases are not task IDs or commit identities. Do not archive or
report them as completed until a materialized task returns an immutable receipt.

## Botanical candidate decision

The dedicated read-only botanical candidate audit returned **HOLD** for both
`edc6afd` and `3135d6b`. The former is a broad non-ancestor runtime and local
Parquet publication candidate with source-admission and production gates open;
the latter is superseded documentation whose equivalent evidence is already on
current `main`. Neither is integrated. The current exact-UUID transitional
lookup and newer census custody remain the canonical state.

No Python data-writer process was observed during the restart check. The safe
next action is to wait for the two fresh tasks to materialize, then review their
current-main receipts independently before any local integration. Keep the
historical owners idle and retained for custody; do not archive unresolved
ingestion or source-admission work.

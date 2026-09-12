---
type: evidence
recorded_on: 2026-09-11
observed_at: 2026-09-12T03:54:00Z
---

# Session restart and custody receipt

This receipt records the safe restart after the PlantGeo task group stopped
making progress. It is a lifecycle record only; it does not promote any data
candidate, source, or deployment.

## Weather visual lane

The former **Build PlantGeo weather forecast** task
(`01a09323-6f59-7822-9e6f-146892776749`) was archived after its worktree was
clean and its five-commit forecast reversion chain had been retained. Its
superseded shared diff remains in the named local stash
`superseded future forecast shared integration before historical-weather
correction`. No stash was applied, deleted, or pushed.

A new isolated owner was started for the screenshot-driven historical weather
visual repair:

| Field | Value |
| --- | --- |
| Task | `01a093b8-e328-7b52-ae2a-a06257d6aed5` — Implement historical weather visual… |
| Checkout | `C:/Users/atooz/.codex/worktrees/15a4/plantgeo` |
| Starting source | `main` at the QA checkout's accepted documentation baseline |
| Scope | Historical Wind & Weather presentation, selected-day stale-frame clearing, readable temperature/wind/precipitation summaries, and map/panel/legend parity |
| Guardrails | No ingestion, data-writer changes, Railway or production access, remote publication, deployment, or inferred continuous field from sparse samples |
| Current state | Active authoring; exact commit, tree, focused receipts, and independent review are pending |

The integration task is holding shared-file custody until this owner provides
an immutable handoff. This prevents a UI branch from interrupting a running
data load.

## Botanical census lane

The resumed **Build botanical species profiles** task
(`01a092bd-c71d-7bb3-bc46-e0dac684f751`) was archived after its preserved
worktree no longer contained a Git checkout and its resumed turn produced no
tool event. Its verified local implementation remains preserved at commit
`edc6afdeb23f339b40049ec0828551c2fe1a4d45`, tree
`01ad55b6220a13604e8fbf8a4ceda773e35d5658`.

A clean read-only census owner was then started:

| Field | Value |
| --- | --- |
| Task | `01a093be-a6b1-7670-bf41-5e999cb0a2c9` — Census botanical production tables |
| Checkout | `C:/Users/atooz/.codex/worktrees/38cc/plantgeo` |
| Scope | Verify the preserved candidate, inspect current Railway target/config availability, and query botanical table metadata/counts/review-state/provenance only if an authenticated read-only DSN is available |
| Guardrails | No download, ingest, publish, migrate, delete, mutation, or deployment; WCVP remains an unaccepted candidate |
| Current state | Active read-only census; receipt and blocker/result are pending |

## Integration and QA custody

The canonical integration task
(`01a0919f-40dd-7ae2-a5a3-cd8cf7e13489`) remains active on
`codex/active-track-integration-20260911` and is not modifying either owner
checkout while authoring is in progress. The root QA task remains the sole
owner of this evidence directory and will bind exact commit/tree identities,
receipts, and independent verdicts before accepting a candidate or archiving
the owner task.

No PostgreSQL or Railway writer, scheduled load, remote publication, or
deployment was interrupted by this restart sequence.

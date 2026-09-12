---
type: evidence-receipt
track: platform_experience_qa_20260911
recorded_on: 2026-09-12
observed_at: 2026-09-12T12:08:10Z
status: restart_reconciliation_read_only
---

# Latest restart reconciliation

This receipt records the latest bounded restart of the unresolved ingestion
owner and the preserved botanical profile task. Both lanes were instructed to
remain read-only. No database, `pgt`, Railway, object-store, provider,
download, writer, scheduler, deployment, or push operation occurred.

## Botanical profile task

Task `01a092bd-c71d-7bb3-bc46-e0dac684f751` was reopened from archive and
completed a read-only comparison against current `main`
(`356630ea6506d9c0989a00b6b44d26de14158e1b`, tree
`f30f71c1ad9515e5b325c1210801fc0cc281d81a`). It confirmed that current main
serves the exact-lowercase internal Species UUID transitional lookup through
the API, agent graph and MCP surface, with explicit missingness and an
unpublished profile release. The historical WCVP implementation remains
branch-only custody and is not a current serving or release-parity candidate.

The task found one concrete P2 agent-parity defect. `graph.py:311-320` binds
`allowed_species_id` inside the warehouse `run_context`, but
`graph.py:423-425` re-exposes `WAREHOUSE_TOOLS` after that context exits;
`_run_pass` at `graph.py:253` supplies those tools directly. Because
`tools.py:310-313` defaults the constraint to `None`,
`tools.py:538` can accept a syntactically valid UUID that was not supplied by
the caller, and the injected session provider is reset. The later web pass has
no regression covering this path.

The smallest safe follow-up is a local synthetic graph regression that enters
the web pass with (a) a UUID different from the caller's and (b) no caller
UUID, using an injected session provider, and proves refusal before lookup.
The agent owner should then keep the constraint alive for the later pass or
exclude `species_information` from that pass. This requires no live data or
credentials.

The follow-up omission design was implemented locally at `9d895dc`: the web
pass now derives `WAREHOUSE_TOOLS_FOR_WEB` from the common registry while
excluding `species_information`. The affected graph test, Ruff check and mypy
check pass (`28 passed, 1 skipped` for `test_agent_graph.py`). This closes the
post-context re-exposure path for the current graph, but it does not satisfy
the final release-pinned botanical agent/API/MCP parity gates.

The source, census, non-Herbaria, Herbaria, publication, occurrence-plane and
recommendation gates remain HOLD. The task made no file or ref change and is
archived after this receipt; the parent botanical and Herbaria tracks remain
active.

## Ingestion task

Task `01a08b00-2a50-73c2-b39b-39523c74ceb2` was restarted with the same
read-only preflight. Its turn completed without an assistant message, tool
event, command, or revision. That is terminal task state but not completion
evidence. It remains idle, unresolved, and open; it must not be archived.

## Coordination disposition

The historical weather presentation approval remains bounded and unchanged.
The visual presentation candidate is already integrated locally at `e54d091`;
no second visual task is created. The superseded forecast implementation stays
archived custody. The current orchestration task retains ownership of the P2
agent finding, the ingestion hold, and the remaining production and populated
experience gates.

The stale completed QA task `01a08af2-569e-7532-a16c-79823255e487` was also
archived from the `PlantGeo Pending Reconcile` section after its last completed
candidate receipt was rechecked. No active worktree or running turn was
associated with that task.

## Coordinator continuation poll — 2026-09-12T12:27:26Z

The coordinator restarted ingestion task
`01a08b00-2a50-73c2-b39b-39523c74ceb2` once more with a strict read-only
reconciliation prompt. Its turn
`01a09596-3c03-79c0-9099-c24464715adc` completed at
`2026-09-12T12:30:38Z` and returned idle, but contained no assistant message,
tool event, command, or revision. This is verified terminal task state without
completion evidence, so the owner remains unresolved and intentionally open.
No file, ref, database, provider, object-store, writer, scheduler,
deployment, or push operation occurred.

## Dedicated weather QA lane dispatch — 2026-09-12T12:31Z

The coordinator dispatched a dedicated read-only weather QA task as queue
alias `client-new-thread:8eb12603-ce98-42b1-ab04-539779e56f9c` from the current
PlantGeo project default branch. Its brief is limited to reconciling the
approved historical presentation against service-backed populated-data,
selected-day, mobile/touch and accessibility evidence. It must not edit,
commit, push, deploy, access Railway or production data, run writers, or reopen
the separate forecast implementation. The alias had not materialized in the
sidebar at the time of this receipt, so no completion or runtime evidence is
claimed yet.

## Weather retry affordance follow-up — 2026-09-12T12:45Z

The coordinator applied a bounded local presentation follow-up to the
historical weather report. `WeatherHistoryReport` now offers a keyboard-
accessible `Retry weather` action for query transport errors and typed
`upstream_unavailable` responses, disables it during refetch and labels the
in-flight state `Retrying…`. The focused test exercises the retry action. The
change is presentation-only: it does not touch the governed reader, data
plane, forecast contract, database, writer, scheduler, deployment or push.

The final integrated frontend sweep passed with 150 test files passed and 2
skipped, 2,233 tests passed and 13 skipped. The data-boundary check and
type-check passed; ESLint reported zero errors and eleven pre-existing React
hook warnings in the report component. The bounded historical-weather
approval remains in force, while populated-data, mobile/touch, hover,
selected-day and forecast gates remain open. The dedicated weather QA queue
alias remains unmaterialized and therefore has no completion evidence.

The independent final review returned PASS after the transport-error branch
was covered directly. It confirmed both retry paths use the existing query
refetch, preserve the no-fallback behavior and remain outside the reader,
forecast, ingestion, database, writer and deployment boundaries.

## Coordinator ingestion restart — 2026-09-12T12:59Z

The coordinator restarted ingestion task
`01a08b00-2a50-73c2-b39b-39523c74ceb2` with a bounded read-only checkpoint.
Turn `01a095b3-4944-7d60-841b-5dfe04f4a9ed` ran from
`2026-09-12T12:59:10Z` through `2026-09-12T12:59:49Z`, completed idle and
emitted no assistant message, tool event, command or revision. This is
terminal task state without completion evidence; the owner remains unresolved,
idle and intentionally open. No file, ref, database, provider, object-store,
writer, scheduler, deployment or push operation occurred.

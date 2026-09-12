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

## Lane-aware botanical and visual dispatch — 2026-09-12T13:02Z

The coordinator dispatched two new read-only tasks from the current PlantGeo
project default branch. Botanical source admission and agent parity was
queued as `client-new-thread:871b3483-2577-4ff3-9caa-d45ecf509731` using
`gpt-5.6-terra` at medium effort. Its brief covers the active species-profile
P0/P1 source, licence, growth-requirement, water/oil/fuel-composition,
agricultural-role and immutable-profile gates, with no database or external
source access.

Visual M3 acceptance was queued as
`client-new-thread:ca4a204f-51a2-4b53-b096-800862d65054` using
`gpt-5.6-luna` at low effort. Its brief covers the active multiscale visual
gates and one bounded local presentation or synthetic-evidence slice, with no
reader, API, forecast, ingestion, database, writer, scheduler, deployment or
push access. Both aliases were absent from `list_threads` at observation, so
no completion or runtime evidence is claimed yet.

## Coordinator owner restart — 2026-09-12T13:14Z

The coordinator reopened the archived botanical task
`01a092bd-c71d-7bb3-bc46-e0dac684f751` and sent the bounded P2 UUID-binding
implementation brief. The task returned idle after reporting that its
directory `C:\Users\atooz\.codex\worktrees\0caf\plantgeo` has no
usable `.git` repository; no HEAD, branch or tree could be verified. It made
no edits, tests, commits, data access or external requests. Because the owner
is unresolved, it remains retained open rather than archived.

The coordinator also reopened the archived scalar-rendering task
`01a04997-04fb-7a31-9628-b46ace71f415` and sent the bounded M3 fixture brief.
Its turn completed idle without an assistant message, tool event, command or
revision, so there is no visual evidence or candidate to integrate. The task
remains retained open pending a real worktree and an evidence-bearing restart.

A replacement botanical implementation task was requested from the verified
PlantGeo Git project using `gpt-5.6-terra` at medium effort as queue alias
`client-new-thread:5348c172-4406-49c9-8479-1b66ff81e6f7`. The alias was not
present in the subsequent `list_threads` observation, so it is dispatch
evidence only and has no completion claim. An attempt to create a local
isolated branch `codex/botanical-uuid-binding` failed before mutation because
the sandbox could not lock the repository ref (`Permission denied`); no branch
or worktree was created.

The ingestion owner remains idle and unresolved after its verified restart;
the weather bounded approval and all production, source-admission, database,
writer, scheduler, deployment and push gates remain unchanged.

## Visual owner continuation poll — 2026-09-12T13:17Z

The scalar-rendering task was re-steered to use the authoritative checkout
`C:\Users\atooz\Programming\plantgeo` explicitly because its registered
worktree is unavailable. The new turn is still active, but no assistant
message, tool marker, command or revision has appeared yet. This is an
in-progress observation rather than a completion or terminal-state claim, so
the task remains open and no archive, candidate integration or stop action is
authorized.

## Visual owner pause — 2026-09-12T13:21Z

The visual task remained active through repeated thirty-second polls with no
tool marker, assistant message, command or revision. A stop-at-boundary prompt
also produced no response. The app handoff interruption could not run because
the task had pending composer state, so the coordinator archived the task as a
reversible session pause. This is not a completion, owner closure or M3
approval: the visual track remains active, no candidate exists, and the task
must be reopened only when a valid worktree and evidence-bearing restart are
available.

## Botanical candidate handoff — 2026-09-12T13:29Z

The registered detached worktree
`C:\Users\atooz\.codex\worktrees\920f\plantgeo` now contains candidate
`0cd943036990023b6d237d3514b2ba124dba87a0`, parent `0ee4f5b`.
`services/agri-data-service/src/agri_data_service/agent/graph.py` keeps the
caller-bound species context active for the web pass, while the existing web
tool list continues to omit `species_information`. The candidate adds a
parametrized synthetic regression for mismatched and omitted caller UUIDs and
the injected session provider; it contains no database, source, writer,
deployment or production changes.

Candidate validation was rerun with the candidate source directory explicitly
on `PYTHONPATH`: `30 passed, 1 skipped` for `test_agent_graph.py`, Ruff passed,
mypy passed, and the candidate worktree is clean. An initial invocation that
reported two failures loaded the root checkout's editable package instead of
the candidate; the corrected source-path run is the authoritative result.
Independent review is pending. No cherry-pick, integration, push or release
approval has been performed.

## Botanical candidate local integration — 2026-09-12T13:32Z

The independent review returned PASS. The root files matched the candidate
parent blobs exactly before integration, so the two reviewed candidate files
were copied into the root checkout without touching any other worktree or
data path. Root now contains the candidate graph/test blobs as uncommitted
changes; no cherry-pick or commit was attempted because repository ref locking
is denied in this sandbox.

The candidate focused run with its source directory forced on `PYTHONPATH`
returned `30 passed, 1 skipped` with exit code zero; Ruff and mypy passed using
writable cache locations. A separate reviewer observed a teardown stall on a
parallel rerun and terminated that process after the same test bodies passed,
so this remains focused implementation evidence rather than a broad release
receipt. No push or release approval is recorded.

## 2026-09-12 owner restart and visual replacement dispatch

The botanical owner `01a092bd-c71d-7bb3-bc46-e0dac684f751` was restarted once
more with a single read-only reconciliation checkpoint. Its checkout
`C:\Users\atooz\.codex\worktrees\0caf\plantgeo` still has no usable `.git`
directory; `git rev-parse --show-toplevel` and `git rev-parse HEAD` therefore
failed before any receipt or candidate inspection. The owner stopped cleanly
and remains open/unresolved. No edits, tests, data access, downloads, Railway,
database, object-store, writer, deployment or push operation occurred. The
next action is a rebind to a valid registered repository worktree before any
further botanical work.

The ingestion owner `01a08b00-2a50-73c2-b39b-39523c74ceb2` also completed its
single bounded read-only preflight and returned idle with no assistant message,
tool marker, command or revision. It remains open/unresolved; no candidate,
archive or integration is authorized, and no data, database, provider,
object-store, writer, scheduler, deployment or push operation occurred.

A replacement dedicated visual task was queued from the verified PlantGeo Git
project as `client-new-thread:f19ad11e-fca1-49e9-a510-63824b2dfd54` with a
presentation-only brief and low-effort model routing. Its brief requires a
read-only preflight, a concrete traditional-weather presentation or fixture
improvement only when justified, one final affected-check sweep, and no
Railway, production, ingestion, writer, deployment or push operation. The
queue alias had not yet materialized in `list_threads` at this observation, so
it is dispatch evidence only and carries no completion or approval claim.

## Weather selected-day stale-frame candidate — 2026-09-12T13:47Z

A read-only visual audit found that `WeatherHistoryReport` and
`LayerManager` treated a mismatched `keepPreviousData` placeholder as drawable
weather. The coordinator applied a bounded local correction in
`src/components/panels/WeatherHistoryReport.tsx` and
`src/components/map/LayerManager.tsx`: the report and map now withhold a prior
weather day during a new selection, while the drawn-day registry reports the
requested day as loading. Focused report and map regressions were added for the
placeholder and delayed day-A/day-B transition. The candidate remains
uncommitted pending the one final integrated check sweep and independent code
review; no data, database, provider, writer, scheduler, deployment, push or
forecast operation occurred.

## Weather stale-frame review and integrated verification — 2026-09-12T13:52Z

Independent review returned PASS for the selected-day correction. The report
and map now share the requested-day match rule; a mismatched placeholder is
withheld from rows and `WeatherLayer`, and the drawn-day registry reports the
requested day as loading. The delayed day-A/day-B report and map regressions
passed. The one integrated sweep passed data-boundary, type-check, ESLint with
zero errors, tooling tests and the full frontend suite: 150 files passed and 2
were skipped; 2,235 tests passed and 13 were skipped. Existing warnings and
real-data/mobile/forecast gates remain open. The candidate is locally accepted
but uncommitted because repository ref locking is denied; no data, database,
writer, scheduler, deployment or push operation occurred.

## 2026-09-12 custody recovery and lane replacement dispatch

The botanical owner `01a092bd-c71d-7bb3-bc46-e0dac684f751` and ingestion owner
`01a08b00-2a50-73c2-b39b-39523c74ceb2` were each stopped at a read-only
boundary after their registered directories failed `git rev-parse`; both were
archived as reversible pauses, not as completed work. No source download,
ingestion, database, Railway, writer, object-store, deployment or push action
occurred. The preserved botanical candidate and ingestion evidence remain under
their existing custody records.

Replacement tasks were dispatched from the verified PlantGeo Git project with
isolated worktrees and right-sized low-effort models:

* botanical reconciliation and bounded agent/API/MCP work:
  `client-new-thread:8b8ed7ef-46e5-4700-a503-18ceabcb1a5a`;
* non-Herbaria ingestion/agent-wiring audit:
  `client-new-thread:ce5c8b4e-7320-4f9c-82ad-7c88d07c1768`;
* traditional weather and visual-layer presentation work:
  `client-new-thread:ace3c375-e668-420d-85a3-501c56027c1a`.

Each brief requires a read-only preflight, no data-load interruption, no local
PostgreSQL or `pgt`, no Railway or external source access, no writer or
deployment, and one affected-check sweep only after any justified local edit.
These aliases are dispatch evidence until the task list reports a materialized
thread and an evidence-bearing result.

## 2026-09-12 lane audit findings and visual label candidate

The replacement lane audits completed read-only. Botanical evidence confirms
that the exact-UUID transitional profile lookup and HTTP/agent/MCP wiring are
working locally, but immutable publication is blocked on an admitted
non-Herbaria release, canonical taxon mapping and assertion-level provenance.
The WCVP bundle remains an unaccepted local candidate and the Railway census
still requires an operator-authorized read-only target. WTU and UBC Herbaria
admissions remain blocked; the occurrence Parquet plane remains planned.

The non-Herbaria ingestion audit confirms that VPD, GloFAS, CAMS and ensemble
work is still scaffolded or planned: no approved Parquet-native writer,
serving/layer registration or publication evidence exists. Growth requirements,
water/oil/tissue composition, fuel analysis and agricultural-role fields remain
blocked on source admission, rights, identity, normalization and immutable
publication. No database, Railway, source, writer or ingestion operation was
performed.

The visual audit confirmed that the existing report already provides a
forecast-like local summary, while aggregate support is intentionally drawn as
declared cells. A bounded local candidate now makes map wind labels font-safe
by using explicit ASCII `from N` compass wording plus measured speed; the
Unicode arrow helper remains available for compatibility. The candidate touches only
`WeatherLayer`, its focused test and the map directory guidance. It does not
invent data or close the populated-data, mobile, accessibility, hover or
forecast gates; the independent review and final affected-check sweep are
recorded in the dated section below.

## 2026-09-12 font-safe label review and final sweep

Independent visual review returned PASS after the label was corrected to
explicit meteorological `from <cardinal> <speed>` wording. The legend and map
guidance agree, and `directionToArrow` remains available for non-map consumers.
The final single sweep is recorded in the [root integrated weather-label
receipt](root-integrated-checks-20260912-weather-labels.md): data-boundary,
type-check, lint, tooling and full Vitest all passed (150 files passed, 2
skipped; 2,240 tests passed, 13 skipped). No data-load or external service
operation occurred; broader populated-data, mobile, accessibility, hover and
forecast gates remain open.

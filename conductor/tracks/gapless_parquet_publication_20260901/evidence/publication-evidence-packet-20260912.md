---
type: track-evidence
track: gapless_parquet_publication_20260901
artifact: publication-evidence-and-blocker-packet
audited_on: 2026-09-12
status: local_reconciliation_complete_operational_gates_open
source_commit: 843b4b313e03447594b23a67f75c3062b2b1a024
source_tree: 9533bb9e5423240630935df0cd012cd8ead15504
---

# Publication evidence and exact remaining gates

This packet is a repository-only reconciliation of committed `main` at the
commit/tree above. No database (including local PostgreSQL or `pgt`), Railway,
object storage, writer, scheduler or deployment was accessed. It records no new
publication, absence, recovery or operational approval. The parent track stays
`active`; P3 recovery and P4 burn-in stay open.

The [earlier local blocker](local-publication-blockers-20260912.md) correctly
keeps runtime gates open, but its statement that exact older horizons are all
unknown is too broad. Retained sessions and receipts establish some requested
windows and narrower completed subsets. They do not establish today's coverage.
The [September 2 census](product-ownership-census.md) remains dated history:
its missing eleven registrations, CDS-blocked direct ownership and 28
time-bearing products must not be copied into a current activation manifest.

In source anchors below, `pipeline/`, `execution/`, `jobs/`, `sql/`, `foundation/`
and `parquet_ops/` are relative to
`services/agri-data-service/src/agri_data_service/`. Code was read, not imported
or executed. A **declared ceiling** is a settlement rule; a **retained ceiling**
is a dated receipt fact; a **published edge** is the last verified terminal
partition. None substitutes for another. A forward ownership floor is not a
provider's earliest available date or proof that earlier history is complete.

## Scope census and current ownership declarations

Static constructor inspection gives 32 lane registrations: 27 time-bearing
streams (11 NASA POWER, eight ERA5-Land and eight other streams) and five static
lookups. This is a repository inventory, not a measured active set. The current
effective active/required set is missing. The following matrix therefore covers
all registered product identities, including retained activation candidates;
the executor owner must attach a disposition to every row and expand family
rows into individual products when recording burn-in.

The 32 registrations are not a count of executor responsibilities. The historical
handoff also includes the terminal `soil-moisture-parquet-backfill` one-shot and
`soilgrids-cache-warm` static database cache responsibility; these must receive
an explicit terminal/retired/replacement disposition in the effective-set
readback. Operational maintenance and superseded `postgres-*` jobs likewise
remain visible in that readback without being counted as new Parquet products.

`D` below means the audit/receipt's explicitly recorded UTC date, not the local
wall clock substituted into a historical claim. Daily fields use their declared
lag before source-response settlement checks. Every time-bearing stream owes
the ordered rung set `(0, 5, 9, 13)` and its availability evidence.

| Product identities | Declared floor, ceiling/lag and cadence | Declared publication owner; unresolved scope |
| --- | --- | --- |
| `climate-field-air-temperature-max`, `climate-field-air-temperature-mean`, `climate-field-air-temperature-min` | NASA POWER direct floor 2026-08-07; daily candidates through D−5. | `climate-nasa-power-direct-forward`, hourly :40 UTC. Retained September 10 receipt closes 1,560 days each, 2022-04-30..2026-08-06. Its source ceiling 2026-09-05 leaves 2026-08-07..2026-09-05 unclosed by that receipt; later status is unknown. |
| `climate-field-dew-point`, `climate-field-precipitation`, `climate-field-relative-humidity`, `climate-field-wind-speed` | NASA POWER direct floor 2026-08-07; daily candidates through D−5. | Same climate executor; four distinct products, not four successes inferred from one family tick. Older requested history needs its own artifact identity. Relative-humidity 1981–2017 availability recovery remains explicitly open in offline export. |
| `soil-wetness-surface`, `soil-wetness-root-zone`, `soil-wetness-profile` | NASA POWER GWETTOP/GWETROOT/GWETPROF; direct floor 2026-08-07, daily through D−5. | Same climate executor. Fraction of saturation, distinct from ERA5 volumetric moisture. All three now have direct registrations; the September 2 missing-writer claim is superseded locally. |
| `climate-field-shortwave-radiation` | NASA POWER direct floor 2026-06-01; daily through D−75. The 75-day lag is explicitly conservative and unmeasured against the source edge. | Same climate executor. Obtain a source-specific ceiling receipt; the family's meteorology lag cannot settle solar. Historical snapshot edge 2026-05-31 is not a measured current provider ceiling. |
| `soil-field-moisture-0-7cm`, `soil-field-moisture-7-28cm`, `soil-field-moisture-28-100cm` | Open-Meteo ERA5-Land archive; direct floor 2026-08-03, daily through D−9. | `soil-era5-land-direct-forward`, hourly :50. Historical terminal one-shot reports 1,556 days through 2026-08-02; it is not recurring ownership or current all-rung availability proof. |
| `soil-temperature-0-to-7cm`, `soil-temperature-7-to-28cm`, `soil-temperature-28-to-100cm`, `soil-temperature-100-to-255cm`, `soil-field-vpd` | Same archive, direct floor 2026-08-03, daily through D−9; lag inherited from the dated redistributor measurement. | Same soil executor. The current direct source is the archive, so the old CDS-credential blocker does not establish its current feasibility. The September 7 physical census was sparse: VPD 448 days within 2022-04-30..2026-08-29; soil temperatures two days each, August 28–29. This is neither a current census nor full-history closure. |
| `burn-severity` | Historical release floor 2020-11-24, lag seven, irregular release dates; current snapshots use capture date +1 UTC day, no additional lag. | `burn-severity-direct-forward`, daily 08:55 eligibility check and weekly capture. Historical cohorts and the 2018–2026 replacement population require separate ledgers; see below. |
| `drought` | USDM registered floor 2022-08-09, Tuesday release candidates every seven days, lag four. | `drought-direct-forward`, hourly :45. Scheduled scan covers at most 60 settled weeks; source-direct backfill covers the registered floor, not the older 2000 archive request. |
| `fire-detections` | FIRMS/MODIS_SP history floor 2000-11-01; lag two, daily. Generic writer ceiling 2026-08-24; direct starts 2026-08-25. | `fire-detections-direct-forward`, hourly :15, five settled-day scan. `parquet-fire-detections` and old archive commands cannot supply new source-direct history merely by being registered. Retained August archive totals require current receipt/ladder reconciliation. |
| `water-gauges` | NWIS registered dense-record floor 2026-05-24; lag two remains unverified; daily. Generic ceiling 2026-09-01; direct starts 2026-09-02. | `water-gauges-direct-forward`, hourly :15, current publisher-day poll. Older 2025-10-18..2025-11-17 archive failure is unresolved by forward freshness; the old archive command stages environmental data in PostgreSQL. |
| `vegetation` | Sentinel-2 registered history floor 2022-08-05; lag seven, daily candidates. Generic/backfill boundary 2026-09-05 inclusive; direct starts 2026-09-06. | `vegetation-direct-forward`, hourly :05, 400-day lookback clipped to that boundary. Historical `backfill.py` still uses the database adapter; a current preserved-Parquet/source-direct recovery owner is required. |
| `sensors` | NWS registered floor 2026-07-29, lag one, daily; source retains only about six days. | `sensors-direct-forward`, hourly :20, at most seven day buckets. Older lost source history cannot be reacquired by this poll; September 5–6 positive-data/absence conflict and preserved candidate admission remain open in retirement. |
| `weather-observations` | Current Open-Meteo poll. Registered 2026-08-01 floor and two-day lag remain explicitly uncited fallbacks. | `weather-observations-direct-forward`, hourly :30; at most two touched day buckets. Freeze source/history/settlement contract and actual retained coverage. Current conditions do not acquire forecast or archive history. |
| `signal` | Transitional combined plane: registered floor 2022-04-30, daily through D−9 (larger source lag). | Retained `parquet-signal` exports environmental PostgreSQL and is not a new source-history owner. Retirement owns the 222 prepared legacy-schema day candidates and missing local admission inputs. |
| `fire-perimeters`, `evacuation-zones`, `watersheds` | Static source-version sets, lag zero; registered floors 2025-07-28, 2025-04-14, 2026-08-07 are inert for scheduling. Ceiling is the source watermark/content version, not a daily history obligation. | Declared direct replacements for WFIGS, Oregon OEM and NHDPlus_HR. A current poll cannot recreate uncaptured versions. Perimeter generic exporter still has database dependencies; supplied September 10 active set is dated, not current activation proof. |
| `soil-survey`, `calendar` | Static lookups; soil survey is vintage/watermark driven (inert floor 2025-08-26), calendar has a computed floor. | `parquet-soil-survey` retains a database dependency; static soil restoration has a separate point-reader contract. `parquet-calendar` has no environmental DB source. Do not turn an unchanged version into artificial daily data/absences. |

Source anchors: `pipeline/direct/climate/products.py:78`, `:143`, `:214`;
`pipeline/direct/soil/products.py:64`, `:134`; `pipeline/parquet/lane_registry.py:783`,
`:833`, `:881`, `:900`, `:923`, `:977`, `:994`, `:1009`, `:1025`, `:1065`,
`:1099`, `:1133`, `:1230`, `:1261`; `execution/job_executor_service.py:633`,
`:683`, `:704`, `:726`, `:755`, `:779`, `:841`, `:869`, `:902`, `:933`;
`foundation/parquet/zoom.py:29`. The
[local scope audit](local-ownership-audit-20260912.md) binds the bounded lookbacks
to source/test definitions. Inspected publication/recovery implementation paths
are unchanged between its `8c14ea0` base and this packet's base.

The [temperature generation receipt](../../environmental_postgres_retirement_20260904/evidence/temperature-bootstrap-receipts-20260910.json),
[September 7 physical census](../../environmental_postgres_retirement_20260904/evidence/snapshot-products-vs-availability-20260907.md),
[terminal one-shot handoff](scheduler-handoff-20260902.md),
[runtime repair receipt](../../environmental_postgres_retirement_20260904/evidence/parquet-runtime-repair-20260910.md)
and [current offline plan](../../offline_export_service_20260908/plan.md) retain
the dates and limits above. All other current per-product floor-to-ceiling gap
intervals are unmeasured in this packet; no empty list of gaps is inferred.

## Five older requests: retained facts and unresolved intervals

The [archived sidebar audit](../../../retros/session_hygiene_20260911/sidebar-audit.json)
classifies the older soil-wetness, precipitation, dew-point, burn-severity and
drought tasks as `ambiguous_incomplete` (lines 665, 680, 695, 710 and 725).
Those summaries retain claims and unfinished gates; they are not the underlying
data, manifest or final exhaustive audit. A reported load and a completed audit
are separate evidence states.

| Request | Retained evidence | Exact remaining reconciliation |
| --- | --- | --- |
| Soil wetness | Older summary reports 1981-01-01..2026-08-20, 16,668 daily partitions at four tiers, 6,617,196 base rows and 397 source receipts; overlap/parity was still running. It does not identify which depth(s) those totals describe. Separate canonical subset closes 2022-04-30..2026-08-06: each depth 592,110 physical = 580,806 selected + 11,304 superseded rows; zero rejected. Later promotion records 1,560 days per depth with marker repair; later availability note records 6,240 rows per depth completed. | Recover the older source/manifest/inventory and bind it to each product/depth and actual root. Reconcile 1981-01-01..2022-04-29 and 2026-08-07..2026-08-20 against that claim and the narrower subset. These are comparison intervals, not measured current missing days. Do not multiply the older totals across three depths or close its original parity gate. |
| Precipitation | Older target is 6,611,638 z13 rows over 16,654 days with 2026-08-06 cutover ceiling, without final completion. An 1981-01-01 start is only an arithmetic inference if those days are contiguous; the excerpt does not state it. Canonical subset explicitly closes 2022-04-30..2026-08-06: 1,560 days, 1,166,676 physical rows to 619,320 cell-days, 547,356 duplicates, zero excluded and all four tiers. | Recover the original floor and source-bound inventory/audit for the older target. Earlier/final summaries report 12,536 versus 12,538 objects for the abbreviated output manifest; preserve that discrepancy until exact inventories explain it. The canonical subset cannot discharge the older target or prove the later tail. |
| Dew point | September 7 physical live-prefix census reports 16,654 contiguous days, 1981-01-01..2026-08-06, all four rungs. It calls the result bootstrappable; it is not itself an availability publication receipt. Canonical audit separately binds manifest `c2972ea61ebfb66a86fa1e834625fae163e5d0a0abfd39f8c701edca3e59b71a` and 691 objects. | Bind the complete physical history to the exact bootstrap, generation and pointer receipts, and recover the older exhaustive-audit conclusion. Its `RequestCanceled` interruption is a transport failure, not absence or a demonstrated data mismatch. Current tail and current receipt identity remain unmeasured here. |
| Drought | Registered floor 2022-08-09 is a retained warehouse extent; source archive capability starts 2000-01-04. August 25/28 summaries retain 209 complete release ladders through 2026-08-18 plus two governed skipped Tuesdays. The older task stopped during API repair deployment. | Name the two skipped Tuesdays and bind each source/absence receipt at every rung; do not replace this with prose saying 209/209 continuous. Resolve whether the older request requires the archive-capability interval before 2022-08-09, and assign a reviewed owner if it does. Current 60-week forward scanning does not discharge that decision or the registered older window. Source-direct full registered-window backfill exists but is manual/unregistered. |
| Burn severity | August 25 records four data days, 2,089 z13 absences, two missing base days and one unfinished day. Later August 28 summary reports all five known release dates complete at four rungs, superseding the old data-day count. September 11 separately closes the 747-fire 2018–2026 current snapshot; 2023–2026 stay partial. Retirement retains 3,077 inventoried fires from 1984–2017 outside that replacement. Historical serving preserved 540 rows with truncation. | Reconcile historical absence dates and all-rung receipts; the older absence markers were z13-only. Do not carry the two missing days forward as current after the five-release proof, or infer all absence/unfinished states closed. Recover and admit the separate 3,077-fire inventory with explicit release/availability semantics. Neither 540 preserved truncated rows nor 747 current fires proves full history. |

Additional retained anchors: sidebar audit lines 363 (canonical soil), 480/512
(precipitation count discrepancy), 180 (later drought/MTBS census);
`conductor/RUNBOOK-archive-2026-09.md:1464`, `:1490`, `:1493` (actual product roots
and soil promotion); `conductor/tracks/offline_export_service_20260908/RUNBOOK.md:264`
(soil availability); `conductor/tracks/postgres_shrink_ingest_repoint_20260825/plan.md:74`
(dew-point canonical hash and 691-object audit); archived
[drought session](../../../retros/session_hygiene_20260911/layer-sessions/drought.md)
at lines 31/41/52 and
[burn session](../../../retros/session_hygiene_20260911/layer-sessions/burn-severity.md)
at lines 31/41/52/117. The
[MTBS rollout receipt](../../environmental_postgres_retirement_20260904/evidence/mtbs-live-rollout-20260911.md)
and [retirement plan](../../environmental_postgres_retirement_20260904/plan.md)
own the newer current/older split. Native provider floors for other products
are not frozen by these load summaries; the source owner still owes the
receipt-bound source/horizon ledger.

## Terminal and absence categories

| Category | Evidence required and treatment |
| --- | --- |
| Published data | Exact source identity, all data parts and completion receipts at every required rung; immutable availability generation and conditional pointer update. Preserve row/count/aggregation conservation and selected-day identity. A valid NASA mixed real/fill response remains data with explicit fill-cell counts; it is not whole-day absence. |
| Published empty derived rung | `derived_empty=True`, zero rows/parts and a completion receipt. This is published output, not provider absence. An unmarked legacy empty rung remains a repair obligation. |
| Settled provider absence | Immutable source response/inventory plus product, day, support and reason. Climate/soil all-fill results require complete support and proof the source moved past the day; drought needs its requested Tuesday URL and mirror-moved-past proof. A missing result alone cannot author absence. |
| No release on a calendar day | Existing historical markers require the explicit release calendar/source receipt. Weekly USDM release days and irregular MTBS cohorts retain their own semantics. Current MTBS paths do not author new nonrelease daily absences that could shadow a snapshot. A carried release's readable date is not a new observation or provider-ceiling advance. |
| Unsettled or deferred | Provider quota, request-budget exhaustion, incomplete acquisition, latest mirror not advanced or all-fill response without settlement proof remains pending/deferred. No invented absence and no terminal partial day. Valid mixed real/fill data is distinguished above. |
| Failed, truncated, unindexed or conflicted | Keep exact missing intervals/rungs and repair work visible; a dead letter is not source exhaustion. Sensor positive-poll/absence conflicts need governed correction; missing pointer with bootstrap present needs recovery, not empty bootstrap. |
| Source exhausted / older evidence unavailable | Name the source limit and retained receipt. A failed historical job or absent local artifact cannot prove that the provider had no data. Source-capability floor, requested floor and currently published floor stay distinct. |
| Static unchanged / source empty | An unchanged source version requires no invented publication. A watermark claiming a populated version is not satisfied by an absence marker. `source_empty` requires a successful source watermark read; unread watermark remains unknown. |

Anchors: `pipeline/direct/climate/adapter.py:136`,
`pipeline/direct/soil/adapter.py:137`, `pipeline/direct/drought/adapter.py:110`,
`pipeline/parquet/derivation.py:265`, `pipeline/parquet/availability_extension.py:825`,
`:843`, `foundation/parquet/lane_contract.py:271`; the
[runtime repair receipt](../../environmental_postgres_retirement_20260904/evidence/parquet-runtime-repair-20260910.md)
separates quota/budget deferral from terminal publication. The historical database
absence-table wording in `docs/layer-lane-standard.md` is not permission to add
environmental PostgreSQL reads or writes: current AGENTS and this track require
governed immutable Parquet absence evidence.

## Effective executor, checkpoints and stale-worker fencing

| Dated state | What it proves and what it does not |
| --- | --- |
| September 2 handoff | 37 active executable lanes plus one terminal snapshot responsibility at release `e4490c3c2f2e23f75cc9d6e297f4be646e0e00a1`, deployment `b1f35a20-6e05-48ff-9801-5235c9753a01`. This is not today's activation manifest. |
| September 10 cutoff configuration | Retained receipt records 28 running active lanes to a configured 20-lane list at `2026-09-10T13:48:23.7340624Z`, `skipDeploys: true`, explicitly `configured_pending_deployment`. Four source/promotion lanes (`jobs-firms-archive`, `jobs-streamflow-archive`, `mtbs-forward`, `vegetation-catch-up`) and four FIRMS/streamflow plan-gap/reconcile lanes were in the pause scope. The two materialized-view lanes were not among those eight. |
| September 11 MTBS definition | One persisted v2 definition was reconciled from the old weekly schedule to daily 08:55 UTC, 2,130-second worker budget and 2,220-second lease, with weekly capture. Readback reports enabled, matching specification and no active work. It explicitly says future scheduled execution had not occurred. This does not prove other definitions or the full cutoff. |

These facts are retained in the [scheduler handoff](scheduler-handoff-20260902.md),
[runtime repair](../../environmental_postgres_retirement_20260904/evidence/parquet-runtime-repair-20260910.md)
at line 284, the [active-lane boundary](../../environmental_postgres_retirement_20260904/evidence/active-lane-database-boundary-20260910.md)
and [MTBS rollout](../../environmental_postgres_retirement_20260904/evidence/mtbs-live-rollout-20260911.md)
at lines 39/49. Local existence checks found neither
`.omc/research/executor-ingestion-cutoff-20260910.json` nor
`.omc/research/mtbs-lane-post-rollout-inspection-20260911.json`. Their checked-in
summaries retain those conclusions; the raw readbacks cannot be independently
replayed from this checkout.

The current source defaults activation to shadow and validates lane conflicts
and handoff tokens. Those tokens are operator assertions, not evidence of
quiescence. Registration preserves paused/existing definitions; it does not
automatically rewrite their stored schedules and budgets. Existing compatible
prior-version work may resume using its stored definition; live leases wait,
and disabled/incompatible versions need reconciliation. Pause prevents future
cooperating dispatch, not existing child or arbitrary manual-writer execution.
Anchors: `execution/job_executor_service.py:1071`, `:1334`, `:1619`;
`execution/AGENTS.md:21`, `:1729`, `:1752`, `:1780`, `:1790`.

| Boundary | Implemented contract and remaining proof |
| --- | --- |
| Leader and work claim | Pinned physical leader connection; connection loss invalidates the tick. Claim uses `FOR UPDATE SKIP LOCKED` and increments the work-item fence. Retain actual leader/process identities, old/new claims and absence of overlap. |
| Durable checkpoint/cursor | Updates compare work item, owner and live fence. Outer `ready` cursor means launch resumption, not domain/source progress. Retain both outer cursor and actual publication/source checkpoint before and after restart. |
| Attempt close | Success/failure/defer compare the live work-item fence, not only the attempt's immutable token. Show rejected stale attempt/checkpoint writes and preserved successor state. |
| Expired lease | Reaper is scoped by definition, runs before claim, clears ownership into retry/dead letter without resetting/bumping the fence. The successor claim increments it. Show reaper, new claim and rejected stale actions separately. |
| Child termination | Parent monitors shutdown, deadline and heartbeat loss, then terminates/bounded-kills the child. Observe stop/reap timings and absence of orphans; a paused definition alone is insufficient. |
| Publication | Cooperating writers hold a shared lane publication barrier plus exclusive day lock; availability holds the exclusive barrier through receipt verification and conditional pointer CAS. The initial barrier rollout must drain old writers that cannot join it. DB ledger fencing alone does not fence object bytes. |
| MTBS lock connection | Publisher checks its pinned driver connection across async boundaries. The documented contract explicitly does not claim object-store fencing after an undetectable network partition. Retain that limit and require process/quiescence and independent output evidence rather than claiming a DB reclaim proves absolute storage fencing. |

Anchors: `sql/jobs/claim_work_item.sql:138`, `:156`, `:161`;
`sql/jobs/advance_checkpoint_sequence.sql:64`;
`sql/jobs/close_attempt_succeeded.sql:71`; `jobs/lease.py:437`, `:501`;
`jobs/worker.py:1023`; `sql/jobs/reclaim_expired_leases.sql:112`;
`execution/job_executor_service.py:1895`, `:1920`, `:1955`, `:1981`;
`pipeline/parquet/AGENTS.md:602`; `pipeline/parquet/publication_barrier.py:27`;
`pipeline/direct/burn_severity/publish_snapshot.py:268`, `:401`;
`pipeline/direct/AGENTS.md:1509`.

The declared outer retry policy is five attempts, initial 30-second exponential
backoff, multiplier two and maximum 3,600 seconds; lease is command timeout +120
seconds and worker budget is timeout +30 (`execution/job_executor_service.py:195`).
Compare these to every stored definition. Failed coalescing checkpoints may
clock-release below the three-failure breaker; replay-oldest holds on the first
failure. A held lane requires exact incident/supersession evidence; resume alone
does not clear it (`execution/job_executor_service.py:1424`). The historical P3
dead-letter list is not authority to restart retired environmental DB commands.

## Acceptance packet still required for every activated product

Three logical duties and three scheduled advances are different gates.
`docs/layer-lane-standard.md:178` requires source-cadence forward refresh, daily
gap authorship/reconciliation and daily coverage status in the sole executor.
`spec.md` and the production acceptance plan separately require at least three
consecutive scheduled advances per activated product. One climate or soil
invocation must account for all eleven or eight products respectively; a green
family process cannot stand in for products it did not advance.

The executor/publication owner must retain one manifest of the effective active
set, expanded by product, and one receipt row for each of the three due buckets:

- Deployed commit/tree, deployment identity and observation time; persisted
  definition ID/version, exact command/arguments, enabled/required status,
  cadence/phase, catch-up policy, command deadline, stored work budget and lease.
- Source floor and receipt-derived settled ceiling, declared lag, requested
  interval/support, governing owner and conflict/legacy-owner disposition.
- Scheduled due bucket, run/work-item/attempt IDs, worker/leader identity, lease
  expiry and fence token, checkpoint/cursor before and after, retry/dead-letter
  disposition, and any supersession evidence.
- Per-product prior/new published edge or eligible release, source and terminal
  receipt identities, all-rung part/completion digests and conservation results,
  prior/new availability generation/checksum and pointer conditional-write result.
- Collapsed unresolved intervals by product/rung/reason, receipt-backed absence
  intervals, unindexed physical days, and idempotently authored repair work with
  its bounded source-direct or preserved-Parquet owner.

Keep source-unchanged MTBS/static checks as checks; three green no-op ticks do not
prove three publication advances. If no eligible source update exists, record the
source receipt and keep the advancement gate open for an explicit acceptance
disposition. Do not fabricate releases to clear it. The current MTBS contract
needs daily eligibility checks and the weekly capture evidence separately.

Retry, process restart and expired-lease recovery need observed outcomes on the
exact deployed definition: transient failure to successful terminal publication;
restart with retained cursor and bounded catch-up; expired lease reclaimed to a
new fence, followed by rejected stale-worker heartbeat/acknowledgement/publication
attempt and one authoritative output. Current unit definitions prove local
contracts only. The handoff must include physical inventory, absence/coverage,
all-rung conservation and availability reconciliation after those events.

## Owners, missing artifacts and next actions

These are required future evidence actions, not actions executed or authorized
by this local task. Responsibility is assigned at track/role level; a currently
authorized named runtime operator is not established by the retained packet.

| Missing owner / dependency | Missing artifact | Exact next action |
| --- | --- | --- |
| Gapless source-family owner for each older interval; named assignee absent | Interval ledger joining requested horizon to source receipt, physical root/manifest, terminal/absence/index coverage and current bounded recovery command | Reconcile the five historical requests above; assign each remaining product/rung interval to one source-direct or preserved-Parquet owner. Do not revive the dated PostgreSQL archive queue. |
| Executor operator under retirement; current operator/readback absent | Effective deployment/definition/active/required manifest, cutoff time, old-process drain and current checkpoint/cursor/lease inventory | Supply a separately authorized runtime readback that reconciles the September 10 configuration with actual processes and leases. Repository registration or later deployment success cannot prove effective cutoff. |
| Publication/recovery owner with executor operator | Retry, restart and expired-lease receipts including stale-worker refusal at both ledger and publication boundaries | Collect exact event and output identities on the deployed definition; prove no overlapping authoritative writer and preserved immutable history. |
| Gapless duty owners and production acceptance verifier | Three consecutive scheduled per-product advance rows, three-duty mapping, post-advance inventory/availability/absence reconciliation | Expand the measured active set into product rows and submit the exact acceptance handoff; keep omitted, unchanged-source and failed products explicit. |
| Custodian of retained but missing raw artifacts | Re-hashable immutable inputs and exact raw operational readbacks referenced by summaries | Recover the named artifacts without treating checked-in hashes or session summaries as the original bytes. Revalidate under a separately authorized custody scope. |

No operational checkbox closes from this packet. Its completed scope is the
local evidence reconciliation and a concrete handoff of missing owners,
artifacts and next actions.

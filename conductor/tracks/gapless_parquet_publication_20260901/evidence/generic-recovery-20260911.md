---
type: track-evidence
track: gapless_parquet_publication_20260901
observed_on: 2026-09-11
status: local-verified-production-gated
---

# Generic publication recovery and current ownership

This packet supersedes September 1–3 operational predictions for this track. It
does not supersede their receipts. Source revision at preflight was
`fa202230958fb55521963e886eb031be5fc266c4`; the working tree already contained the
September 11 Conductor reconciliation. Only this track and generic Python
publication/executor code are changed by this work.

No production deployment, configuration change, schedule activation, retry,
supersession, data publication/retraction, or service removal was performed.
The running scheduler continued its previously authorized work during these
read-only probes. No local database was created or used.

## Read-only preflight

[Machine inventory](live-reinventory-20260911.json) records the bounded physical
listing, ordinary checksum-bound availability reads, effective executor tick,
operational definitions/runs/attempts/leases, and remaining legacy configurations.
Object and database reads ran from **18:00:57 to 18:03:33 UTC**. The canonical
`plantgeo` database explicitly returned `transaction_read_only=on`.

The current registry has **32 physical registrations: 27 time-bearing products
and five static/reference registrations**, with required rungs 0, 5, 9 and 13.
All 128 rung listings completed without exceeding their 250-page bounds. These
are name-level candidate inventories: a completion filename is not proof of its
body, all-rung agreement, source completeness, or aggregation conservation.
The listings were not one atomic publication snapshot. Days incomplete at every
rung remain visible in per-rung states even when `partial_ladder_days` is zero.

| Product group | Indexed days | All-rung name-complete candidates | Remaining coverage fact |
| --- | ---: | ---: | --- |
| Air temperature max/mean/min, precipitation, wind speed, three soil-wetness products | 1,577 each | 1,577 each | Latest September 6; August 7–20 remains missing. The three temperature histories had 1,560 indexed days at the prior checkpoint. |
| Dew point | 15,576 | 16,671 | Physical history begins in 1981; indexed history begins in 1984. The cardinality difference is 1,095 days; August 7–20 also remains open. |
| Relative humidity | 3,101 | 16,615 | Physical history begins in 1981; indexed history begins in 2018. The 13,514-day cardinality difference belongs to the offline availability gate. Additional internal physical gaps are preserved in the machine receipt. |
| Shortwave radiation | 1,493 | 1,493 | Latest May 31, index source ceiling June 24. June 1–24 is not closed. The declared 75-day lag would project June 28 on this date, but no fresh upstream edge measurement establishes that date. |
| Eight ERA5-Land soil products | 1,587 each | 1,587 each | Latest September 2; no internal name-level gaps in the observed physical range. Eighteen product-days received the separate physical verification below. |
| Burn severity | 2,109 | 2,109 | Latest September 11. This includes the current snapshot; partial 2023–2026 mapping and pre-2018 recovery remain separate. |
| Drought | 213 | 213 | Latest release September 1; index source ceiling September 3. Daily calendar gaps cannot be inferred for this release series. |
| Fire detections | 9,444 | 9,444 | Latest September 9; full source conservation was not repeated. |
| Sensors | 18 | 18 | 25 additional partial ladders; current executor lane remains held after failures. Product-specific correction remains with environmental retirement. |
| Signal | unavailable | 1,344 | 241 partial ladders; missing availability and coordinate correction remain with environmental retirement. |
| Vegetation | 1,488 | 1,489 | Index ends August 31, physical names reach September 1. New direct ownership begins September 6 and was not yet settled. |
| Water gauges | 1,537 | 1,538 | Latest indexed day September 11; a cardinality delta during live mutable publication needs exact reconciliation. |
| Weather observations | 1,467 | 1,467 | September 6 is missing in the current window. Recent polls report no writable observations. |
| Fire perimeters | no availability index | 56 | 288 partial ladders; direct replacement remains shadow. |
| Evacuation zones / watersheds / calendar | static/reference | 5 / 1 / 1 | No time-series availability index is inferred for these reference lanes. Evacuation zones also has eight incomplete base dates. |
| Soil survey | no availability index | 0 | Two incomplete base dates; dark-layer restoration is separately owned. |

Availability source ceilings are claims from the current verified index; they
are not a new independent measurement of every provider's maximum published day.
Every legacy source floor/lag in `product-ownership-census.md` therefore remains
dated declaration or evidence, not blanket authorization to fill a window.

## Effective scheduler ownership and the cutoff

Railway reported **11 services and zero cron schedules**. The executor is running
`main` revision `fa202230958fb55521963e886eb031be5fc266c4`, deployment
`95304e24-16ed-4705-a615-c60b0e368a08`, status `SUCCESS`, start command
`agri-service ops jobs-executor`. The captured configuration has no staged patch.
OAuth exposes variable names, not their values; effective activation is established
by the deployed process's tick tables, not a claim to have read the environment value.

The capture contains 98 explicit executor tick tables, from 16:30:04 through
17:55:20 UTC. The last table lists **20 effective active lanes**. The eight paused
lanes are all shadow: `jobs-firms-archive`, `jobs-streamflow-archive`, `mtbs-forward`,
`vegetation-catch-up`, and the FIRMS/streamflow archive plan-gaps and reconcile
duties. This advances the September 10 cutoff from `configured_pending_deployment`
to **effective for this deployed process and ledger snapshot**.

Active here means participating in the executor's configured lane set. A lane
may be waiting, held or already settled; activation is not an in-flight invocation
or evidence of new publication.

All 50 stored job definitions remain enabled. Enabled historical definitions are
not proof of active ownership: the executor allowlist and operator controls still
govern dispatch. The read-only ledger has no owned lease for the cutoff group.
There is one owned lease for `parquet-calendar`, so this is not a global drain
certificate. Twenty-one inner streamflow archive shards remain `retry_wait`, with
no owned lease; they must not be resumed through the retired PostgreSQL ingestion
path. There are also 207 retained strategy-refresh retry-wait shards outside the
eight-lane pause. No ninth pause is inferred.

The attempt query reached its 1,000-row limit and covers only its returned
interval, not all of the requested three days. Run history is the newest five
runs per definition within seven days. Source-specific command success and full
publication advancement are distinct measurements.

That bounded capture contains 73 attempts numbered above one: 24 succeeded, 44
failed and five deferred. Successful retries include two soil and 11 water
executor attempts; the others belong to historical inner archive work. This is
evidence of ordinary retries in the retained interval, not restart, expired-lease
or disable verification of this work's undeployed changes, nor permission to
resume retired archive duties.

Three former services (`plantgeo-ingest-cron`, `plantgeo-cron-mtbs`, and
`plantgeo-cron-soilgrids`) are absent from the current inventory. This work did not
remove them and does not invent removal dates, credential-transfer receipts, or
replacement success receipts. Three remain with no schedules, no-op start
commands and `NEVER` restart:

| Remaining object | Service ID | Required proof before any later removal |
| --- | --- | --- |
| `plantgeo-fire-detections-forward` | `f4ad61fe-e71a-4776-b9d5-0b153c9ee5b7` | Fresh no-in-flight and exact direct-publication responsibility receipt. |
| `plantgeo-water-gauges-forward` | `40cb252b-e21c-4140-8d94-5db77eb2398d` | Exact direct-owned day coverage from September 2, source/publication and boundary proof, plus no-in-flight. |
| `plantgeo-soil-moisture-parquet-load` | `4a1413f1-5f96-44ea-853c-6a379c7673c4` | Revalidate the retained one-shot immutable artifact receipt and no active invocation. |

Removal remains gated by the exact post-main handoff authority in
`conductor/release-governance.md`; this feature work supplies no new removal or
deployment permission. Rollback uses supported executor lane disable, preserves
objects/receipts and waits for the old invocation to settle. Railway cron
ownership must never be restored.

## Three scheduled advancements: bounded proof

[Physical scheduled-publication receipt](scheduled-soil-advances-20260911.json)
binds three distinct backlog days—August 5, August 4 and August 3—to consecutive
September 11 executor buckets **15:50, 16:50 and 17:50 UTC** for six products:
soil moisture 28–100 cm, all four soil-temperature depths, and soil-field VPD.
The ordinary read-only availability verifier checked the source evidence,
terminal receipts and bound physical data/completion objects for all **18
product-days / 72 rungs**. Scheduler association uses the unique successful run
whose completion matches each structured log event within 15 seconds. Every rung's
publication time and the logged provider fetch fall within that run's start/end
window. All four rungs share one source receipt, whose retained generic export
binds the exact writer run ID, product root and day. The additional 36 source
objects were read and digest-checked without mutation.

The generic export receipt does not contain the provider response SHA recorded
in the structured log. That logged SHA remains evidence of the writer's source
report, not a cryptographic cross-binding to a retained upstream payload. This
limit is explicit in the machine receipt.

This is backlog coverage advancement, not a claim that the newest selectable
date advanced three times. It does not establish whole-history aggregation
conservation or the gate for every activated product. The first two moisture
depths had no new day in these three turns. Weather returned
`no_writable_observations`, vegetation returned `not_yet_settled`, and evacuation
zones reported unchanged versions. Those are observed schedules, not new
publication. The burn-severity run history is September 8, 10 and 11, not three
consecutive daily buckets; only September 11 is a scheduled run after `fa20223`.
The captured climate summary strings are malformed at log chunk boundaries and
are not accepted as machine-readable three-advance receipts.

## Generic implementation and remaining production work

The local publication repair preserves source proof in absence receipts, avoids a
post-write GET before the durable retry claim, repairs incomplete absence ladders
from retained source evidence, and rechecks a ladder under the day lock before
changing it. Compatible absence rechecks retain original bytes; conflicting
evidence refuses. A warehouse export returning zero rows now leaves an explicit
blocked gap rather than manufacturing source absence. Historical query-zero
markers remain preserved and require product-owner reconciliation.

The local executor repair lets failures of its pinned database connection escape
before worker rollback/reconnection can mask lost leadership. An unconfirmed
child exit now aborts execution while retaining the attempt and lease, and exits
the service before another command can start. This includes non-timeout signal,
wait and cancellation failures and a resolved wait lacking a process return
code. Confirmed exit preserves the original diagnostic; a fatal outcome does not
start another cleanup pass. Ordinary engine-bound domain handler retries retain
their prior behavior.

The generic registry and direct writer registration were re-inventoried; no
product ownership or source clock change was justified. Signal/sensor/static
soil/soil survey/MTBS product repairs, offline builders, frontend readers,
retirement migrations and project-wide Conductor files remain outside this lane.

The authorized local deliverable has passed integrated Python verification and
independent review. A later production slice must bind the reviewed commit to the
exact successful main deployment before testing retry/restart/expired-lease and
rollback-by-disable live. It must re-read ownership and coverage, use supported
controls, record exact before/after identities, and retain every valid immutable
day. Product gap repair and service removal each need their own exact authorized
scope; this packet is preparation, not permission.

## Final validation

The final full sweep passed at **19:10:04 UTC**: **6,016 passed, 149 skipped,
one xfailed, zero failures and zero errors**. Formatting, lint and mypy across
`src` and `scripts` passed. The repository's client URL, restricted-import and
fabricated-observation boundary guards also passed. The independent verifier
approved this scoped local commit with no remaining actionable findings.

[Validation receipt](validation-20260911.json) records exact tool versions,
durations, JUnit totals and capture digests. The
[Python quality receipt](../../../../services/agri-data-service/QUALITY_RECEIPT.json)
matches both independently recomputed staged and working-tree digests over 1,309
inputs: `sha256:d2c093db5ae5e396ce64f2c11e7e78dacfbac43b36092d0e6a5464e69482ba5b`.
No source or test file changed after that sweep.

The first integrated sweep completed after the whole implementation batch:
format and mypy passed; lint found the new exception's missing `Error` suffix and
one test import scope issue. Pytest reported **5,995 passed, three failed, 149
skipped and one xfailed**. The three failures exposed integration fixtures tied
to the retired empty-export fallback, an irrelevant coarse-rung ordering
assumption, and a transient-read fault enabled before marker setup. All five
findings were corrected together, with no product source repair or lint
suppression. The corrected full sweep passed all four gates at 18:45 UTC, but
independent review found one further cleanup gap: non-timeout signal/wait errors
could release an attempt without confirmed child exit. That reviewed correction
was applied as one batch. Its static gates passed, but six new synthetic cleanup
cases raced one-millisecond wait deadlines: **6,010 tests passed**, while the six
failed assertions saw a legitimate timeout before their intended injected error.
Unknown child exit still took the fatal path. The mock timing was made
deterministic and independently reviewed; the single focused harness check passed
**114 tests in 1.27 seconds** under the repository's exception for mock-infrastructure
fixes. The final full sweep then passed against the frozen staged tree. The intermediate
green receipt is not the final release evidence. No production
recovery exercise or complete-track acceptance is claimed.

The offline recovery evidence is exercised by the full Python gate:

| Contract | Regression surface |
| --- | --- |
| Durable absence repair, claim-write interruption, physical-write interruption, preserved source bytes and no fabricated source absence | `tests/parquet/test_publication_recovery.py`, `test_gap_fill.py`, `test_governed_absence.py`, `test_availability_extension.py` |
| Connection loss, retained unconfirmed work, no peer dispatch, terminate/kill fallback and service exit | `tests/test_jobs_worker.py`, `tests/test_job_executor_service.py` |
| Retry backoff fairness, restart of the exact logical bucket and expired-lease reaping | Existing worker/executor regressions in the same full sweep |
| Supported lane disable, audit/idempotence, unchanged work, pause inheritance and refused trigger | `tests/test_job_lane_control.py`, `tests/test_jobs_dispatch.py` and executor pause tests |
| Consumers preserve their own source-evidence and bounded-read contracts | `tests/direct/test_watersheds_direct_adapter.py`, `tests/parquet/test_vegetation_absence.py` |

The final command is `uv run --no-sync python scripts/check.py --write-receipt`
from `services/agri-data-service`, using the repository's existing Python 3.12
development environment and this checkout's source path. Database test DSNs are
unset. Skipped database integration cases must be reported separately; no
production database or bucket is a test target. The quality receipt binds staged
and working-tree source digests, rather than certifying uncommitted mutable bytes.

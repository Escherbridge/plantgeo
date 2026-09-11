---
type: track-evidence
status: active
recorded_on: 2026-09-11
---

# Runtime ownership and retirement readback

The September 11 repair investigation confirms the deployed cutoff configuration
and several remaining product failures. It does not establish retirement or
authorize a production mutation. The [machine checkpoint](retirement-runtime-checkpoint-20260911.json)
preserves the selected catalogue, foreign keys, job definitions, operational lease
census, object receipts and bounded agent replies, with hashes of the original
read-only captures. All observations below are dated facts, not a future lease or
quiescence guarantee.

## Deployed ownership

Railway readback at 17:56–17:57 UTC showed the main app, data API, executor and Martin
successfully deployed at `fa202230958fb55521963e886eb031be5fc266c4`, with no staged
changes. The executor process read at 18:02:11 UTC was deployment
`95304e24-16ed-4705-a615-c60b0e368a08`; its effective allowlist contained the expected
20 lanes. The eleven configured handoff acknowledgements were also read from that
process at 18:22:18 UTC. An acknowledgement is an operator assertion, not independent
proof that every possible standalone or manual writer is stopped.

The first queue sample reached its 301-row bound and is explicitly **not** a
complete lease census. A separate query at 18:07:55 UTC examined all work in
`leased`/`running` state or with an unexpired lease; it returned zero rows within a
201-row cap. The query and the grouped queue counts are preserved in the machine
checkpoint. No leases, queues, definitions or retry states were changed.

The archive runners, archive reconcilers/planners, vegetation catch-up and
`soilgrids-cache-warm` remain enabled in their durable definitions but are absent
from the effective allowlist. Their definitions were not deleted or disabled by
this task. The old FIRMS work still includes 1,757 queued items and one deferred
item; streamflow includes 14 queued, 21 retry-wait, two deferred and one
dead-lettered item. Those states are preserved recovery evidence, not permission
to restart the PostgreSQL-writing handlers.

The September 11 08:55 MTBS run
`558248db-8bd8-44f9-b437-b05cfa48c226` succeeded at 08:55:30.347203 UTC. The previous
scheduled September 10 catch-up also succeeded on September 11 at 05:57:30 UTC.
These are later scheduled-execution observations beyond the earlier rollout
receipt. They do not prove three new publication advances, weekly capture, older
MTBS admission, or the production acceptance matrix.

## Surviving database

The read-only repeatable-read transaction reported PostgreSQL 18.4 and
**1,789,712,063 database bytes**. The September 9 reduction to 30 MB remains a
historical rebuild checkpoint; it is not the present size or evidence that all
environmental writers were permanently retired.

The bounded catalogue query completed with 150 relations across `agri`, `geo` and
`public`: 121 tables, one partitioned table, 13 materialized views and 15 views.
The foreign-key query returned 171 rows, also below its cap. The machine receipt
retains every catalogue row and the exact queries. `reltuples` values are planner
estimates, including unknown `-1` values; none is a counted population or parity
measurement.

| Surviving relation | Total relation bytes | Implication |
| --- | ---: | --- |
| `geo.features` | 1,341,440,000 | Shared environmental/community storage remains; the table is not a whole-table drop candidate. |
| `geo.geometry` | 408,420,352 | Geometry dependencies and historical identity still require separate proof. |
| `agri.spatial_cell` | 1,073,152 | Direct source support and remaining callers still reference this relation. |
| `public.soil_grid_cache` | 712,704 | Static-soil replacement has not yet cleared the cache reader and warmer. |
| `agri.signal_observation` | 40,960 | Presence and size do not prove conserved signal values or authorize deletion. |

The existing D1 packet builder was rerun locally for all 27 non-KEEP rows in the
historical ledger; the [final re-inventory](retirement-reinventory-20260911.json)
binds the exact proof-tool source hashes and each full packet digest. Twenty-five
relations are present and two absent from the captured catalogue. Four have no
lexical consumers in the expanded scan, but every packet remains blocked. No new exact day/row parity or
archived `pg_dump` was produced, and no destructive statement was executed.
Relations absent from this complete bounded catalogue are identified as absent,
not as newly dropped by this task. Relations outside the historical ledger are
surfaced for classification rather than silently ignored; an old KEEP label is
not a newly reviewed permanent exemption from the track's broader charter.

The investigation also found a concrete zero-reader proof defect: repository-root
operator scripts were outside `retirement/readers.py`'s scan surfaces. Live
references include `scripts/data-quality-report.mjs`,
`scripts/warm-soilgrids.mjs`, `scripts/export-ndvi-grid-tiles.py`, and the geometry
maintenance SQL scripts. The repair expands the proof surface and adds regression
coverage. It neither deletes these callers nor treats a newly found reference as
retirement evidence. The final inventory includes the expanded scan and the
reviewed conservative shell-comment handling: quoted SQL JSON operators cannot
hide a relation behind a `#` character.

## Product state from actual readers

The deployed API process was queried directly at 18:09:58 UTC through the same
bounded tool functions used by the agent, without an LLM call or database fallback.
The machine checkpoint records exact arguments, replies, elapsed times and deployed
API revision. These are **before-repair baselines**, not acceptance of the new local
code. No browser or public HTTP validation was performed in this readback.

| Product and selected day | Exact observed result |
| --- | --- |
| Sensors, September 5 and 6 | Both availability and point answers still report governed absence from a zero-row warehouse export. Temporal neighbours identify September 4 and 7 with actual signed day distances. The preserved 5,935 positive source rows contradict those absence claims. |
| Signal, August 6, temporal neighbours within three days and 25 km | `parquet_serving_refused`, code `lane_columns_absent`; the selected Parquet files lack `cell_latitude` and `cell_longitude`. This is not evidence of an empty source day. |
| Soil-survey, September 11 | Coverage refuses with `parquet_availability_withheld`; a physical file listing cannot substitute for an admitted snapshot. |
| Burn severity, September 11 | Coverage reports 747 published fires. The 25 km bounding-box point query returns five features and explicitly reports truncation; centroid distance can exceed the circular radius. No nearest covered day occurs within the seven-day query window. |

Both signal availability `_LATEST` and `_BOOTSTRAPPED` objects were absent at
18:07:55 UTC. All eight September 5–6 sensor absence markers were still present,
with exact hashes in the checkpoint. Their presence preserves the correction
target; it does not make the warehouse-only absence claim true.

## Soil preservation boundary

A complete bounded GET/LIST census at 18:17:40 UTC found exactly 959 soil-survey
Parquet parts and no JSON completion or availability marker:

| Native partition | Parts | Compressed bytes |
| --- | ---: | ---: |
| August 10, z13 | 478 | 478,154,389 |
| August 28, z13 | 481 | 479,302,736 |

Two bounded full-object reads pinned `part-0.parquet` in each partition. Both
contain 500 rows in one row group and the same 15-column native schema. Their
SHA-256 values and schema are preserved in the checkpoint. No full-partition row
count, source population, true release date, or generalized rung is inferred
from those samples. An actual release/completeness receipt must precede recovery
of either snapshot; dark preserved bytes are neither an admitted release nor an
empty source.

The proposed `static-soil/_LATEST.json` pointer was `NoSuchKey` at 18:22:18 UTC.
That is the dated precondition for a future first admission, not proof that the
existing raster/SoilGrids HTTP paths have switched. The static candidate and
native-grid point checks are recorded in their separate soil evidence packet.

## Remaining source-direct archive boundary

The old archive command still binds `bind_feature_writer` in
`ingest/commands.py::run_archive_definition_slice`, places it in
`ArchiveWalkContext`, and reaches `run_source_backfill` through
`ingest/archive_walk.py`. FIRMS and streamflow historical work must not resume
through that path. Existing direct forward functions provide reusable source
normalization, but their September cutoffs are intentional ownership gates:
`fire_detections.py` rejects a moved `forward_start_day`, and
`water_gauges_forward.py::_owned_publisher_tables` filters earlier days.
Removing those guards or merely re-enabling the old archive definitions would
not be a source-direct recovery implementation.

Product-specific recovery must preserve raw source response and population
limits, exact publisher day and identity, all-rung derivation and known-byte
retry semantics. The generic owner must then register the historical ownership,
gap-authored work, bounded retries and source-receipt admission. That work remains
open until its actual candidate and selected-day receipts exist. No generic
registry, scheduler, executor, agent or frontend reader was edited here.

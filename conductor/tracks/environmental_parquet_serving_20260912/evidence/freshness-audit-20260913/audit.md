---
type: audit-report
status: complete
date: 2026-09-13
scope: repository and read-only production inspection
---

# Environmental freshness and publication audit

The reported problem is confirmed, with several distinct causes: disconnected static soil
serving, disabled perimeter refresh, a held sensor job, measured historical gaps, and missing
repair ownership. A successful deployment or scheduler bucket is not proof of current publication.
No application code, production configuration, job state, or data was changed by this audit.

## Evidence and limits

- [Manifest](manifest.json) binds captured artifacts by SHA-256, project/environment and repository HEAD.
- [Coverage JSON](coverage.json) is the public Parquet coverage response generated at
  **2026-09-13 14:18:02 UTC**; [CSV](coverage.csv) retains every returned lane/rung with gap and
  governed-absence ranges. The JSON also retains published ranges.
- [Executor tick](executor-tick.json) is observed at **2026-09-13 14:14:48 UTC**;
  [activation](activation.json) records the 11 selected lanes.
- [Deployments](deployments.json): main is SUCCESS at `d6fcbc5`; executor and Parquet API are
  SUCCESS at `0c72d3d`. All three inspected services have null Railway cron schedules.
- Code findings were read from the working tree without modifying its in-flight botanical or
  intervention changes. Deployment identities are separate from working-tree evidence.
- This is a coverage-index/runtime audit, not a fresh inventory of physical Parquet objects,
  upstream availability, spatial completeness, governed-absence validity, or browser rendering.
  Coverage dates and zero reported gaps do not prove those other properties.
- September 12 acceptance remains RED. This evidence supplements it and does not certify a release.

## Measured product status

The following dates and temporal gap ranges agree across the returned rungs. Static release ages
are not daily lag failures. A stored `source_ceiling_day` is a publication/index value, not a fresh
provider measurement; it can freeze along with publication.

| Product | Latest published/covered day | Audit result |
| --- | --- | --- |
| SSURGO soil survey | None | Null coverage dates; no admitted publisher; map route unconditionally refuses publication. |
| Fire perimeters | 2026-09-04 | Direct refresh is explicitly shadow: absent from active allow-list. |
| Fire detections | 2026-09-11 | Matches configured two-day lag; no reported temporal gaps from 2000-11-01 at z0/5/9/13. Older-gap repair remains unsupported. |
| Sensors | 2026-09-09 | Lane held after three consecutive unsuccessful buckets; no automatic release. |
| Vegetation NDVI | 2026-09-06 | Matches seven-day lag, but September 1–5 is a reported gap outside the active writer's ownership floor. |
| Shortwave radiation | 2026-05-31 | June 1–24 reported missing; stored ceiling June 24. Configured 75-day lag gives June 30 as the September 13 planning ceiling, requiring fresh upstream evidence. |
| ERA5-Land soil moisture, VPD and temperature | 2026-09-04 | Matches configured nine-day lag; no reported temporal gaps. These are different from static SSURGO and SoilGrids. |
| NASA POWER meteorology and soil wetness | 2026-09-08 | Matches configured five-day lag. Relative humidity additionally reports two older gap ranges. |
| Water gauges | 2026-09-13 | Current edge present; 37 reported gap ranges, including September 6. UI-supported floor must be applied before defining repairs. |
| Weather observations | 2026-09-13 | Current edge present; two reported gap ranges, including September 6. Observation coverage does not admit a forecast. |
| Burn severity | 2026-09-11 | Reported gaps April 2, 2015–November 23, 2020 and September 1–10, 2026; release/event semantics require source reconciliation. |
| Drought | Selector covered through 2026-09-13 | Recorded source ceiling September 8; weekly release carry, not a September 13 source observation. |
| Watersheds | 2026-08-07 | Static reference; age alone is not a freshness defect. |
| Evacuation zones | 2026-09-11 | Static lookup with active refresh; capture/source version must be checked before calling it stale. |

## Findings and resolution ownership

**F1 — P1: static soil was cut over to unavailable before replacement publication.**
`src/lib/server/trpc/routers/environmental.ts:554` always returns
`soil_survey_parquet_lane_not_published`. `pipeline/parquet/lane_registry.py:308` states that no
source-direct soil-survey publisher is admitted. Commit `fd60290` replaced the previous serving
call on September 12. This proves disconnection, not deletion of historical storage objects.
The surviving `planes/soil_survey.py` is a release/point reader, not the missing wired viewport API.
SoilGrids is a separate regression: the point route (`environmental.ts:515`) always throws and
raster discovery (`:583`) always returns an empty list. The registry (`layer-registry.ts:365`)
still suggests a point lookup that cannot succeed. The old `scripts/backfill-soil-survey.mjs:17`
imports a deleted service and is not a recovery command. Owner: environmental serving.

**F2 — P1: fresh perimeter publication is disabled in production.**
The current tick explicitly reports `fire-perimeters-direct-forward` as shadow because it is not
in the active allow-list. The September 4 publication date is consistent with missing forward work.
Resolve why it was withheld, validate its direct writer and source capture, then activate that exact
owner without disturbing the other 11 lanes. The mutable WFIGS current feed cannot reconstruct
uncaptured old outlines. Owner: gapless publication and environmental serving.

**F3 — P1: sensors are operationally held.**
Run `def58693-a0b6-4d97-90f2-3127bfc9b418` is held after three failed buckets; the tick calls for
recorded operator supersession. Root cause is not established by the bounded logs: they show the
breaker, not the original failure. Obtain attempt/error receipts, fix the cause, and only then use
the governed supersession path. Do not blindly release the breaker. The sensor source's six-day
rolling replay window makes recovery time-sensitive. Owner: gapless publication.

**F4 — P1: several measured gaps have no active source repair owner.**
The executor retired generic gap-fill/drain schedules (`execution/job_executor_service.py:304`).
NDVI's direct writer starts September 6 (`pipeline/direct/vegetation/products.py:49`); registry
prose assigns earlier dates to a nonexistent backfill module. That excludes the measured September
1–5 gap. Water/weather also retain September 6 gaps despite a current edge. Define supported
horizons and author durable work from actual gaps, with terminal receipts across all required rungs.
Fire detection's five-day forward window and August 25 ownership floor create the same resilience
hole, although this audit found no current detection temporal gap. Owner: gapless publication.

Weather uses a current-conditions feed with no historical-date query and a three-hour freshness
gate (`pipeline/direct/weather_observations/source.py:3`). Missing live observations require retained
source captures or an explicitly admitted alternative; later current polls cannot recreate them.
Archive climate products must not silently substitute for the original weather observation product.

**F5 — P1: successful object writes can leave availability unpublished without a scheduled repair.**
`pipeline/parquet/gap_fill.py:1752` preserves the terminal write result when availability extension
fails. `availability_extension.py:349` clears its claim when an index is not bootstrapped.
Only climate, soil and vegetation direct drivers invoke `retry_pending_availability`; the other
direct families lack an equivalent scheduled drain after generic scheduler retirement. Add a
publication reconciliation owner and distinguish object completion from serving completion in job
reporting. This is a code-level failure path; it is not proven to be the cause of each live gap.
Owner: gapless publication; shared serving integration remains with environmental serving.

**F6 — P2: source ceiling and newest-first polling can conceal or prolong a stale tail.**
Shortwave's stored ceiling is June 24 while the current configured planning ceiling is June 30.
`parquet_ops/availability_coverage.py:347,497` uses the stored pointer ceiling. The climate writer
defaults to one newest day per product; a source-unsettled outcome is non-failing and can postpone
older missing days. Measure the actual provider response and per-product outcomes before changing
lag policy. Add an independent freshness clock and bounded older-gap work; never convert unsettled
input to an invented absence. Owner: gapless publication and layer status presentation.

**F7 — P2: burn-severity scope remains incomplete, but its fresh capture owner is active.**
Do not confuse disabled `mtbs-forward` with stopped current capture. Active
`burn-severity-direct-forward` passes `--current-snapshots`; `pipeline/direct/burn_severity/daily.py`
owns daily stage publication and weekly capture, and may walk historical work on capture days.
The current cohort is bounded to 2018–2026 and the declared PNW footprint. Reconcile the measured
older gap and September 1–10 interval against source/release receipts; do not manufacture daily
burn observations or enable an overlapping owner. Owner: gapless publication and fire validation.

## Protected work

The Herbaria admission, botanical occurrence publication/experience, species profile work, and
intervention workspace remain with their existing owners. No specimen republish, taxonomy recipe
change, intervention migration, store/UI change, or deployment occurred here. In-flight shared
files include `environmental.ts`, `layer-registry.ts`, map components and their tests; future
restoration patches require an explicit narrow handoff with those owners. The resolution plan
belongs to the existing environmental and gapless tracks, not a competing replacement track.

## Verification

Repository inspection and read-only production capture completed. No application tests were run:
this change contains audit/plan documentation and evidence only. Independent reviewer `soil_trace`
approved the audit/planning scope after two wording corrections: distinguish a recorded ceiling
from a measured provider ceiling, and name precisely which ranges the CSV includes. The reviewer
corroborated the runtime conclusions, all five artifact hashes, all 120 CSV/JSON rows and cross-rung
temporal consistency. Local checks passed artifact checksums, row counts, metadata/OKF frontmatter,
local links and whitespace. This is neither implementation acceptance nor production recovery.

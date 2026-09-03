---
type: report
supersedes: docs/reports/data-lane-execution-ownership-2026-08-28.md
observed_at: 2026-09-02
---

# PlantGeo data-lane execution ownership — 2026-09-02

**Supersedes `docs/reports/data-lane-execution-ownership-2026-08-28.md`.** That report described a
branch-only, not-yet-merged executor proposal against a 13-stream Parquet registry. As of `main`
production release `e4490c3` and repository `HEAD` `9052998` (wave 1, commit `2b4cfef`, not yet
pushed), `plantgeo-job-executor` is the sole production scheduler, Railway cron scheduling is
rejected, and the registry has grown to 21 `LANE_REGISTRATIONS` / 47 `LANE_SPECS`. See "What the
2026-08-28 report said that is no longer true" below for the itemized corrections, and the
companion evidence file
`conductor/tracks/gapless_parquet_publication_20260901/evidence/product-ownership-census.md` for the
per-product detail this report does not repeat.

## Executive conclusion

The transition the 2026-08-28 report proposed has happened. `plantgeo-job-executor` (service
`565ecaad-9946-48f1-8a0b-28fa60494a16`) is the sole scheduler; Railway's environment schedule census
returns `scheduled=[]`, and all six legacy scheduled/one-shot writer objects are fenced
(`cronSchedule: null`, a no-op start command, `restartPolicyType: NEVER`, zero retries) rather than
deleted (`evidence/scheduler-handoff-20260902.md:17-21`). Nothing has been retired: PostgreSQL, R2
data, source adapters, ingestion commands, manifests, checkpoints and durable ledgers are all
untouched; rollback disables an executor lane in place (`evidence/scheduler-handoff-20260902.md:23-25`).

What is new since the handoff was written (`e4490c3`, still the production release) and captured by
wave 1 (`2b4cfef`, committed to `main` locally but **not pushed, nothing deployed**) is nine
additional registered responsibilities — the eight NASA POWER `parquet-climate-field-*` generic
lanes plus the dedicated `climate-nasa-power-direct-forward` direct writer — none of which is in
`PLANTGEO_JOB_EXECUTOR_ACTIVE_LANES` (`conductor/RUNBOOK.md:151,204`). Production today still runs
at the 38-responsibility surface the handoff measured; the repository on disk carries 47.

## Executor responsibility matrix, as the tree stands at `HEAD` `9052998`

Counted directly from `LANE_SPECS` (`execution/job_executor_service.py:590`) and
`LANE_REGISTRATIONS` (`pipeline/parquet/lane_registry.py:1018-1023`), not copied from any prior
document. `git diff e4490c3c..HEAD -- execution/job_executor_service.py pipeline/parquet/lane_registry.py`
confirms every line of the delta below lands in commit `2b4cfef` and nowhere else.

| group | spec count | executable | source |
|---|---:|---:|---|
| `_POSTGRES_SPECS` (source-family Postgres ingestion + two independent repair lanes) | 11 | 11 | `job_executor_service.py:363-406` |
| `_JOBS_SPECS` (4 durable job schedules + 4 archive-maintenance verbs + 1 stream-validation lane) | 9 | 9 | `job_executor_service.py:409-490` |
| `_PARQUET_SPECS` (one `parquet-<slug>` per `LANE_REGISTRATIONS` entry) | 21 | 21 | `job_executor_service.py:502`, `lane_registry.py:1018-1023` |
| `_MIGRATION_INPUT_SPECS` (direct writers, MTBS, SoilGrids warm, climate direct, one terminal snapshot) | 6 | 5 | `job_executor_service.py:506-591` |
| **Total** | **47** | **46** | — |

The one non-executable spec is `soil-moisture-parquet-backfill` (`command=None`,
`cadence_seconds=None`, `job_executor_service.py:577-591`): a completed one-shot snapshot load with a
terminal disposition, deliberately never a recurring lane
(`LEGACY_RAILWAY_RESPONSIBILITIES[SOIL_MOISTURE_SNAPSHOT_OWNER].terminal_disposition`,
`job_executor_service.py:640-646`).

Of the 21 `_PARQUET_SPECS`, the 21 `LANE_REGISTRATIONS` entries break down as **12 database-backed +
8 source-direct NASA POWER + 1 calendar** (`lane_registry.py:7-8`, verified against the registration
tuples at `:723,977,1018`). This is up from **13 (12 database-backed + 1 calendar)** at production
release `e4490c3`, confirmed by `git diff e4490c3c..HEAD -- pipeline/parquet/lane_registry.py`
showing `_SOURCE_DIRECT_REGISTRATIONS` (8 entries) added wholesale in `2b4cfef`.

### Active vs shadow

`conductor/RUNBOOK.md:203-206` and `evidence/scheduler-handoff-20260902.md:40` both record: at
release `e4490c3`, **38 registered responsibilities, 37 active executable lanes plus the terminal
soil-moisture snapshot**. `conductor/RUNBOOK.md:204-206` records the delta directly: "the repository
at `2b4cfef` registers 47 (eight `parquet-climate-field-*` generic lanes plus
`climate-nasa-power-direct-forward`), none of the nine new ones is active." That means:

- **37 lanes ACTIVE** (unchanged from `e4490c3`; all 11 `_POSTGRES_SPECS`, all 9 `_JOBS_SPECS`, the
  original 13 `_PARQUET_SPECS`, and 4 of the original 6 `_MIGRATION_INPUT_SPECS` —
  `fire-detections-direct-forward`, `water-gauges-direct-forward`, `mtbs-forward`,
  `soilgrids-cache-warm`).
- **9 lanes SHADOW** (all new in `2b4cfef`, not activated): `parquet-climate-field-air-temperature-max`,
  `-mean`, `-min`, `parquet-climate-field-dew-point`, `parquet-climate-field-precipitation`,
  `parquet-climate-field-relative-humidity`, `parquet-climate-field-shortwave-radiation`,
  `parquet-climate-field-wind-speed`, and `climate-nasa-power-direct-forward`.
- **1 lane TERMINAL** (`soil-moisture-parquet-backfill`; never active, never activatable).

"Shadow" here is the runtime `LaneTickState` a spec receives whenever it is executable but absent
from `PLANTGEO_JOB_EXECUTOR_ACTIVE_LANES` — `_plan_active_lanes` assigns it explicitly
(`job_executor_service.py:1184`: `state: LaneTickState = "shadow" if spec.executable else
"source_specific"`), and it predicts the next cadence bucket without reading or writing any ledger
or source-watermark state (`job_executor_service.py:1206-1211`).

## The eight climate generic lanes and the climate direct lane: mutual exclusion

Two owners exist for the same eight NASA POWER calendars, and the executor refuses either to
activate beside the other:

- `CLIMATE_DIRECT_LANE_ID = "climate-nasa-power-direct-forward"` (`job_executor_service.py:334`) is
  one lane covering all eight products in a single point-per-cell request
  (`pipeline/direct/climate/forward.py`, invoked as `python -m agri_data_service.pipeline.direct.climate`).
  Its spec sets `conflicts_with=CLIMATE_GENERIC_LANE_IDS` (`job_executor_service.py:559`) — the tuple
  of all eight `parquet-climate-field-*` ids (`job_executor_service.py:335-337`).
- Each generic `parquet-climate-field-<product>` spec is built by `_parquet_spec`, which detects
  `source_direct = slug in _SOURCE_DIRECT_SLUGS` and sets `conflicts=(CLIMATE_DIRECT_LANE_ID,)` from
  the *other* side (`job_executor_service.py:329-333`).
- `parse_activation` enforces both directions symmetrically: for every lane in the requested active
  set, `conflicts = sorted(set(spec.conflicts_with) & active)` and it raises
  `ExecutorConfigurationError` if the intersection is non-empty (`job_executor_service.py:717-720`).
  Activating the direct writer and any of its eight generic siblings in the same
  `PLANTGEO_JOB_EXECUTOR_ACTIVE_LANES` value is therefore a hard configuration error, not an
  operational choice — "The direct climate writer and the eight generic `parquet-climate-field-*`
  specs are two owners of one calendar, so `parse_activation` refuses the pairing from either side"
  (`job_executor_service.py:332-333`).

Both sides are also legacy-owner-free (`legacy_owners=()`): neither requires a
`plantgeo-ingest-cron`-disable acknowledgement to activate, because `plantgeo-ingest-cron` never
produced a day of any of the eight streams (`job_executor_service.py:319-321`).

## The climate/soil direct lanes and the atomic Postgres-cron owner group

Separately from the climate mutual exclusion above, every database-backed `parquet-<slug>` spec
(the original 12, unchanged) still carries `legacy_owners=(INGEST_CRON_OWNER,)`
(`job_executor_service.py:333`, the `else` branch), which means it belongs to the atomic owner group
`_require_atomic_owner_cutovers` enforces for `plantgeo-ingest-cron`
(`job_executor_service.py:263-276`): all 11 `_POSTGRES_SPECS`, the 12 database-backed
`parquet-<slug>` lanes, four `_JOBS_SPECS` durable definitions, the archive-maintenance verbs and
`maintenance-validate-streams` must activate together or not at all
(`evidence/scheduler-handoff-20260902.md:64`, "Atomic owner group" column). The 8 new source-direct
climate `parquet-<slug>` specs are **not** part of this group (`legacy_owners=()`), so activating
them never requires touching the `plantgeo-ingest-cron` cutover.

## What the 2026-08-28 report said that is no longer true

1. **Stream count.** "`pipeline/parquet/lane_registry.py:711-941` registers 13 live Parquet storage
   streams: 12 database-backed streams plus the computed calendar"
   (`data-lane-execution-ownership-2026-08-28.md:106-107`). Now **21**: the same 12 plus 8
   source-direct NASA POWER climate streams plus the calendar.
2. **`climate-field-dew-point` had no dedicated product.** The 2026-08-28 report stated it "is an
   additional gap: it is served through the generic signal read model, but no dedicated physical
   product schema or builder exists" (line 170). This is now false: `warehouse/schemas/climate_field_dew_point.py`
   defines `STREAM = "climate-field-dew-point"`, it has its own `LANE_REGISTRATIONS` entry
   (`lane_registry.py:979-989`, one per `CLIMATE_FIELD_PRODUCTS` member), and its own
   `SNAPSHOT_PRODUCTS` entry with a pinned `expected_manifest_sha256`
   (`snapshot_products.py:196-206`).
3. **"Fifteen registered snapshot product streams" is now fourteen, and the membership changed.**
   The 2026-08-28 table (lines 148-157) listed `climate-field-precipitation` and
   `climate-field-shortwave-radiation` as registered snapshot streams with named builder scripts.
   Neither appears in the current `SNAPSHOT_PRODUCTS` tuple (`snapshot_products.py:168-303`, 14
   entries counted directly). Both are climate products with real, if shadow, forward writers
   (Table 2 of the companion census), but they are served only through `coverage.py:52-58`'s
   `DEDICATED_SLIDER_PRODUCT_LAYERS` raw-prefix fallback — a weaker, unverified-manifest path,
   explicitly commented as "physical warehouse prefixes even though they are not direct-ingest
   registrations. Missing expected prefixes remain in the census with null bounds"
   (`coverage.py:50-52`). Whether this is a deliberate design choice or an incomplete migration step
   is not resolved by any file read for this report or the companion census; it is recorded as an
   open item, not silently corrected.
4. **"None has a proven active forward executor lane."** The 2026-08-28 climate/soil section said
   flatly that no dedicated climate/soil product has a forward owner. Eight of them now do — shadow,
   not active, but a real registered spec exists (`climate-nasa-power-direct-forward` plus the eight
   generic lanes), which is a different and better-evidenced state than "no owner at all."
5. **Water's writer-ceiling gap is closed.** The 2026-08-28 report's activation blocker read:
   "Activation blocker: unlike fire, water has no writer ceiling. A lock prevents simultaneous
   mutation, not alternating ownership" (line 132). `WATER_GAUGES_STREAM`'s registration now declares
   `writer_ceiling=WATER_GAUGES_DIRECT_WRITER_START_DAY - timedelta(days=1)`
   (`lane_registry.py:884`, resolving to `2026-09-01`), with the direct writer's floor fixed at
   `2026-09-02` (`pipeline/lanes/water_gauges.py:39`). The generic and direct lanes now have a
   disjoint, code-enforced date boundary, matching fire's existing pattern.
6. **Watersheds now has a scheduled owner.** The 2026-08-28 report said "Watersheds: this branch adds
   an independently activatable daily current-version fetch with no fabricated legacy owner.
   Production still has no scheduled WBD refresh until that replacement lane is deliberately
   activated and observed" (lines 240-242). `postgres-watersheds` is now an ACTIVE spec
   (`job_executor_service.py:391-408`; part of the 37 active lanes at release `e4490c3`, per
   `conductor/RUNBOOK.md:203-204`), on cadence `86400`, phase offset `7200`, schedule `0 2 * * *`.
7. **The executor itself moved from proposal to sole scheduler.** The entire premise of the
   2026-08-28 report was "the safely deployable transition is therefore a shadow-first dedicated
   executor" as a not-yet-merged branch (lines 25-31). It is now `main`, deployed, and the sole
   scheduler with Railway cron scheduling explicitly rejected
   (`evidence/scheduler-handoff-20260902.md:11-15`).

What is still true and unchanged from the 2026-08-28 report: `signal` still has no registered
`postgres-signal` forward-ingestion spec (`_POSTGRES_SPECS` has no such entry today, confirmed by
direct enumeration above); `soil-survey` still has no scheduled SSURGO source-refresh spec; the
eleven ERA5-Land/NASA-POWER-derived dedicated products in the companion census's Table 3 remain
snapshot-only with no forward owner; and `interventions` remains explicitly out of scope for
Parquet.

## Sources read

Same file set as the companion evidence document
(`conductor/tracks/gapless_parquet_publication_20260901/evidence/product-ownership-census.md`,
"Sources read" section), plus `docs/reports/data-lane-execution-ownership-2026-08-28.md` in full for
the corrections above and `git diff e4490c3c..HEAD` for the two key files to confirm every claimed
delta lands in commit `2b4cfef` and only that commit.

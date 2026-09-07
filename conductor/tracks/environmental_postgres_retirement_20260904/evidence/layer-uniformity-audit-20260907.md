---
type: evidence
---

# Layer uniformity audit — where each layer deviates, and whether the deviation is principled

Traced 2026-09-07 from the working tree. No database, no object store, no network. Every deviation is
classified **PRINCIPLED** (the layer genuinely differs, with the reason cited), **ACCIDENTAL** (built
at a different time by a different pass), or **UNKNOWN** (not establishable from the repo, with the
command that would settle it).

Counts reconcile: 32 lane registrations = 12 hand-written (incl. `signal`) + 19 generated
climate/soil + `calendar`. 16 census lanes (`parquet_ops/coverage.py:97-122`). 12 own an availability
index; 4 are `static_lookup` and never get one (`availability_coverage.py:516-525`); 14 are snapshot
products structurally excluded; 2 excluded by name (`coverage.py:60`). 23 Parquet serving rows +
`interventions` = the 24 the slider reports as 14 served / 10 withheld.

## The two findings that were acted on immediately

### 1. evacuation-zones was cached for 365 days with no revalidation — FIXED

`DEFAULT_REFRESH_MODE` argues `static_lookup: "manual"` from watersheds — *"published exactly one
version in its entire history"*. True of watersheds and soil-survey. **False of evacuation zones**,
whose whole lane exists because Oregon OEM's set changes: its watermark is a CONTENT DIGEST
recomputed on an hourly poll, and its published population moved **677 → 718 → 116 rows inside three
weeks**.

Left on the nature default, `resolveCacheTtlMs` returns `MANUAL_TTL_MS` (365 days) for every day
including the live edge, so a reader who opened the map before a fire could be served that snapshot
from disk for a year — on the one layer where a stale answer is a life-safety answer.

Fixed as a declared per-layer exception (`src/lib/cache/layer-cache-policy.ts`,
`REFRESH_MODE_EXCEPTIONS`) rather than by changing its nature: it genuinely *is* a `static_lookup`,
and its retention default is still right. The exception touches one field, sits beside the table it
departs from, and a user can still override it in either direction. A test asserts every entry in
that table actually departs from its nature default, so an exception equal to the default cannot
sit there pretending to be a decision.

### 2. The "two burn-severity writers" alarm — REFUTED

Both `mtbs-forward` and `burn-severity-direct-forward` are active (confirmed against
`PLANTGEO_JOB_EXECUTOR_ACTIVE_LANES`, 28 lanes). That is **not** a double-write of one stream:
`mtbs-forward` runs `ingest-mtbs`, which writes `geo.features` (`ingest/mtbs.py:947`), while the
direct lane writes Parquet. Different stores.

What it *is*: `mtbs-forward` now feeds a dead end, because `parquet-burn-severity` — the exporter that
read those rows — is retired. It is Postgres work whose consumer no longer exists.

## The ranked accidental deviations

| # | Deviation | Fix | Blast radius |
|---|---|---|---|
| 1 | `strategy-recommendations` is a live toggle bound to a Martin source Martin does not publish (`StrategyLayer.tsx:19-26`) | Set `permanentlyUnavailableReason`, as `soil` already does | UI only |
| 2 | Three `wildfire` procedures return `unpublishedRisk()` unconditionally (`wildfire.ts:111-142`) | Delete them | none |
| 3 | ~~evacuation-zones 365-day cache~~ | **DONE, above** | life-safety |
| 4 | **Four registrations still carry Postgres-reading adapters whose own comments say to swap them "in the same push" that activated the now-active direct lane** | Route each to `_source_direct_refusal(...)` — the factory is at `lane_registry.py:730-758` and already serves watersheds/evacuation-zones — and delete the expired paragraph | **unblocks the `geo.features` drop** |
| 5 | The agent's burn-severity read goes to live ArcGIS (`regional-context.ts:1053` → `services/mtbs.ts:21-22`) while the map reads Parquet | Swap for `getParquetBurnSeverity` | agent/map disagreement |
| 6 | `getGeoFeatureSliderCapabilities()` runs on every slider call and 23 of 24 rows are discarded (`parquet-slider-capabilities.ts:293-297`) | Scope it to `interventions` | largest per-request Postgres read left |
| 7 | `soil-wetness-*` lane slugs lack the `climate-field-` prefix their serving row has | Rename the three while they hold zero objects | naming |
| 8 | `weather-observations` floor 2026-08-01 and lag 2 are admitted, uncited fallbacks (`lane_registry.py:1155-1163`) | Measure `min(observed_day)` and replace both | phantom gap-days |
| 9 | `soil-survey` renders `aggregate_cell` where every geometry sibling renders `native_polygon` | Move to `native_polygon` once its base export exists | visual fidelity |
| 10 | `signal` derives three coarse rungs and has no renderer contract | Decide: contract, or stop deriving | wasted derivation |
| 11 | Three time-bearing lanes name no forecaster with no recorded reason | Add the module or record `horizon: none` | contract honesty |
| 12 | 14 snapshot products have no route to an availability index | Owner call on which prefix the index describes, then move group A | 9 permanently-withheld rows |
| 13 | `vegetation-catch-up` is a one-layer bespoke executor lane | Delete once its 1,026-day backlog discharges | tidiness |

### #4 in detail, because it is the one that blocks the drop

| lane | its own stated condition | state today |
|---|---|---|
| `drought` (`:838-861`) | *"swap it … in the same owner-confirmed push that adds this lane to the active set, never earlier"* | direct lane active, `parquet-drought` retired — **condition expired, swap not done** |
| `burn-severity` (`:801-816`) | gated on a parity proof and the owner stopping `mtbs-forward` | direct lane active; `mtbs-forward` still active |
| `sensors` (`:992-1007`) | held because NWS keeps ~6 days, so `geo.features` was the only path to older days | direct lane active; `postgres-sensors` **deleted**, so the table cannot grow |
| `weather-observations` (`:1152-1163`) | held for want of a cited ownership-boundary day | direct lane active |
| `vegetation` (`:1055-1061`) | **PRINCIPLED** — `backfill.py:149-155` republishes pre-ceiling days *through this adapter* for D2 parity | correctly unswapped |

Consequence is not a live outage — the generic lanes are retired for four of five, so the adapters are
dormant. It is three other things: `geo.features` cannot be dropped while five adapters read it;
re-activating any of those lanes would export a frozen Postgres over days the direct writer owns, with
no ceiling to stop it; and four registrations now document a condition that is false, which is worse
than documenting nothing.

## Deviations that are PRINCIPLED and must not be flattened

- **Static lanes carry a watermark and no `writer_ceiling`; the rest carry a ceiling and no
  watermark.** `LaneRegistration.__post_init__` (`:223-243`) enforces both directions — the strongest
  uniformity guarantee in the system, because the invariant is executable rather than documented.
- **`drought` alone has `cadence_days: 7`** — USDM publishes weekly on a Tuesday and its floor is a
  Tuesday. `burn-severity` is also a `release_series` and deliberately stays at cadence 1 because
  MTBS's five releases sit on no fixed step. Two `release_series` lanes with different cadences is the
  correct answer.
- **Geometry lanes use `GeometrySimplification`, point lanes `GridAggregation`, `calendar`
  `TierPassthrough`.** Only geometry lanes open DuckDB — which is why the 2026-09-02 `LOAD spatial`
  regression hit exactly those six and no point lane.
- **`watersheds` alone adds `HierarchicalDissolve` HUC12→10→8→6** — the only lane whose source
  publishes a real containment hierarchy.
- **`soil-survey` is the one row still declaring `servingReader: "postgresql"`, and it is correct** —
  the lane has never written an object, and `usda-soil.ts` both reads and *writes* `geo.features`.
- **`POSTGRES_CAPABILITY_PASSTHROUGH_NAMES` was deleted rather than emptied**: *"a reader cannot tell
  a live exception with no current members apart from a retired one."*

## RUNBOOK claims already stale against this tree

Recorded so nobody redoes finished work:

1. `RUNBOOK.md:1302-1304` — burn-severity's `servingReader` **is** `parquet` and the passthrough set
   **is** deleted (`parquet-slider-capabilities.ts:140`, `:271-292`).
2. `RUNBOOK.md:1305` — `regional-context` fire-perimeters **is** on `getParquetFirePerimeters`
   (`:1043`).
3. `RUNBOOK.md:1195-1199` — *"only two active lanes write Parquet"* is two waves out of date.

## What could not be established

- **Ladder completeness for the six geometry lanes and the 19 climate/soil products.** Every figure in
  `rung-coverage-census.md` predates both the DuckDB derivation outage and its fix.
  `parquet-drain --selection ladder --dry-run` per lane, against production.
- **Whether `climate-field-relative-humidity`'s historical breakdown exists.** No pinned checksum, no
  receipt. `scripts/build_relative_humidity_from_canonical_snapshot.py census`.
- **Whether the five `geo.*_tiles` functions and their Martin registrations still exist in
  production.** `drizzle/0039` is unjournalled by design. Martin `/catalog`; `\df geo.*_tiles`.
- **Whether `sensors`' 2026-07-29 floor still holds** now its Postgres producer is deleted and the
  table is frozen. `min(observed_day)` for the sensors layer, before that table is dropped.
- **The "39 files touch geo.*" figure quoted earlier.** A different predicate yields 67 and still
  misses ORM-only readers (`regional-context.ts`, `interventions.ts`, `visualization.ts`,
  `places.ts`, `contributions.ts`, `layer-ids.ts`) that never spell `geo.`. **Neither number should be
  quoted without stating its predicate.**

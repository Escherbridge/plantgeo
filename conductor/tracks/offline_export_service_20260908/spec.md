---
type: spec
track: offline_export_service_20260908
status: chartered
created: 2026-09-08
---

# Offline export service — stage locally, transform locally, upload in bulk

## Why this track exists

Every slow step in the Parquet cutover on 2026-09-07 was the same shape: **one network round trip per
object, issued serially.** Nothing was CPU-bound and nothing was short of data.

Measured that night:

| step | cost | cause |
|---|---|---|
| bootstrap evidence compile | ~150 objects/min | serial GET per marker |
| `availability-bootstrap --apply` (relative-humidity) | **hung after 3h08m** | serial verify holding an open DB transaction |
| soil-wetness promotion | 37,440 copies | server-side, survivable only because copies are cheap |
| marker repair | 18,720 GET+PUT | serial-ish, 64 workers |

The bucket is **14.00 GiB across 1,047,868 objects** (measured 2026-09-08). Serving prefixes alone are
**998,486 objects for 9.49 GiB — a ~10 KB average object.** The inefficiency is object COUNT, not
volume. A million tiny objects is a million round trips.

## The premise that was corrected before this track was written

The originating idea was to pull down a **database backup** and export from that. Measurement refutes
it for the remaining work:

* The eight lanes still withheld (`climate-field-air-temperature-{mean,max,min}`, `soil-field-vpd`,
  `soil-temperature-{0-to-7cm,7-to-28cm,28-to-100cm,100-to-255cm}`) need day-grain rows that already
  live in the object store at `raw-canonical/signal-observation/snapshot=prod-20260826-full-signal-v1/`
  — **8,796 objects, 1.96 GiB total.**
* The four proven builders (`scripts/build_{soil_moisture,precipitation,shortwave_radiation,relative_humidity}_from_canonical_snapshot.py`)
  contain **zero** references to `psycopg2`, `asyncpg` or `DATABASE_URL`. They read the object store.

So a 40 GB DB dump would be downloaded and then not used. **The staging target is `raw-canonical`,
~2 GiB.** The instinct — stop iterating over the network, work against a local copy — is correct and
is what this track builds. Only the source was wrong.

Postgres cost is a real and SEPARATE problem (see `## Non-goals`), not this track's lever.

## Goal

A **cutaway service**, living outside the application repo, that:

1. **Stages** the pinned canonical snapshot to local disk once, verified by manifest SHA-256.
2. **Transforms** locally with DuckDB/Polars at disk speed — no network in the inner loop.
3. **Uploads** in bulk with high concurrency, then verifies what landed.
4. **Emits the availability bootstrap input** from local state, so the compile step stops paying
   ~150 objects/min to re-read what it just wrote.

## Success criteria

- [ ] All eight monthly lanes hold a complete four-rung day-grain ladder at their live prefix, with
      real `PartitionCompletion` markers — not breakdown sidecars (see `## Known traps`).
- [ ] Each lane's availability generation publishes and `_LATEST` advances.
- [ ] `getSliderCapabilities` serves 23 of 24 layers (`soil-survey` is out of scope, below).
- [ ] Wall-clock for a full eight-lane build is recorded and is **hours, not days**.
- [ ] The service is reproducible from its own README by someone who has not read this file.

## Non-goals, stated so they are not silently adopted

- **`soil-survey`.** Its blocker is a job time budget (1,230 s against a 3h20m run) plus a deliberate
  `servingReader: "postgresql"` pin. Different problem, different fix.
- **Postgres cost reduction.** Real — `layers` has 33,273,668 sequential scans against a 48 kB table
  and `pg_stat_statements` is not installed, so nothing can attribute query cost today — but it is a
  serving-path problem, not an export problem. Chart it separately.
- **Re-partitioning the serving layout.** The ~10 KB average object is worth revisiting, but changing
  the serving contract mid-cutover would invalidate every reader. Note it; do not do it here.
- **Retiring Postgres tables.** Gated on the D1 three-part proof; unchanged by this track.

## Known traps this service must handle by construction

1. **`layout` is the discriminator.** `SnapshotProduct.layout` is `daily` or `monthly`. A month-grain
   root copied to a live prefix puts month files where `classify_partition_day` looks for `day=`. This
   produced 1,908 unreadable objects on 2026-09-07, reverted the same day.
2. **`data_root` has two trees.** `layer=<slug>/snapshot=<id>` and
   `derived-canonical/signal-observation/lane=<slug>/snapshot=<id>`. A census of only the first
   reported three fully-populated lanes as empty.
3. **`_complete.json` is two different documents.** The derived tree holds a product-breakdown SIDECAR
   (`contract_version`, `part_key`, `part_sha256`, `tier`); a live prefix needs a `PartitionCompletion`
   (`completed_at`, `part_count`, `row_count`, `run_id`, `schema_version`). Copying the first where the
   second belongs yields a lane with every part present that advertises nothing.
4. **`MAX_INPUT_BYTES` is 64 MiB.** Relative-humidity's full 16,598-day input compiled to 69.12 MiB and
   was refused. Trim the window; do not raise the cap.
5. **`kind=physical` is an audit-only byte-identical copy.** No reader opens it. It must never be
   uploaded to a live prefix.
6. **Never emit a digest not computed from the object it describes.** Cross-check against a second
   independent source and refuse on disagreement.

## Constraints

- The service lives **outside** this repo. It may READ the repo's contracts; it must not be imported
  by it.
- Every production write is `--apply`-gated and owner-confirmed. Dry-run is the default.
- Never run PlantGeo locally. Test against prod and live Martin.
- The object-store and database credentials live in the gitignored `services/agri-data-service/.env`.
  They are never echoed, never committed, never placed in a fixture.

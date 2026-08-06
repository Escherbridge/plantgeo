---
name: agri-pipelines
description: >
  Run, backfill, and debug the PlantGeo agri-data-service ingestion pipelines
  and the Railway ingest cron. Covers the CLI verb inventory, the non-obvious
  DSN contract every command needs, which pipelines are credential-blocked
  versus plan-blocked, backfill chunk economics with measured cost-per-row,
  how to verify row counts without psql, and the failure modes whose real
  cause is not what the error says. Use when running any `agri-cli ingest-*`
  or `historical-*` verb, backfilling a source, wiring a new producer,
  diagnosing an empty map layer, or touching `plantgeo-ingest-cron`.
---

# agri-data-service pipelines

Service root: `services/agri-data-service/`. Run everything from there.

## The DSN contract — read this before any command

Every `agri-cli` verb that writes needs **two** variables, and they must differ:

```bash
DATABASE_URL="postgresql+asyncpg://unused:unused@127.0.0.1:1/unused" \
LOCAL_SOURCE_LOADER_DATABASE_URL="postgresql+asyncpg://<user>:<pw>@<host>:<port>/plantgeo" \
  uv run agri-cli <verb>
```

The dummy `DATABASE_URL` is required **locally only**. `Settings` reads
`services/agri-data-service/.env`, which supplies a real `DATABASE_URL`, and the
validator rejects a loader URL equal to it
(`"LOCAL_SOURCE_LOADER_DATABASE_URL must not reuse DATABASE_URL"`). On Railway there
is no `.env` in the image, so `DATABASE_URL` must simply be **absent** there.

## Verifying results — there is no psql on this machine

Use asyncpg, and note the scheme differs from the loader URL: **`postgresql://`**, not
`postgresql+asyncpg://`.

```bash
uv run python -c "
import asyncio, asyncpg
async def main():
    conn = await asyncpg.connect('postgresql://<user>:<pw>@<host>:<port>/plantgeo')
    for row in await conn.fetch('''
        select layer.name, count(feature.id) as features
        from geo.layers layer
        left join geo.features feature on feature.layer_id = layer.id
        group by 1 order by 2 desc'''):
        print(dict(row))
    await conn.close()
asyncio.run(main())"
```

`geo.features` has **no `observed_at` column** — the observation instant lives in
`properties->>'observedAt'` (camelCase). `agri.signal_observation` *does* have
`observed_at`.

## Verb inventory

**Forward ingestion** (`ingest/commands.py`, all registered by `register_ingest_commands`):
`ingest-firms`, `ingest-streamflow`, `ingest-weather`, `ingest-fire-perimeters`,
`ingest-drought`, `ingest-ndvi`, `ingest-sensors`, `ingest-evacuation-zones`,
`ingest-mtbs`, `ingest-drought-history`, `ingest-backfill`, `ingest-geometry-repair`,
`ingest-all`.

**Historical / warehouse** (`cli.py`): `historical-nasa-*`, `historical-era5-*`,
`historical-usdm-*`, `historical-promotion-*`, `source-ingest`, `forecast-*`,
`strategy-train`, `db-status`, `db-upgrade`, `pipeline-status`.

`ingest-all` runs the eight forward jobs plus `ingest-geometry-repair` as the last
job. **MTBS is deliberately not in it** — it is a quarterly source against an hourly
runner and wants its own schedule.

## Backfill

Only sources declaring a usable `HistoryCapability` are backfillable. Today that is
exactly **`nws-sensors`** and **`sentinel2-ndvi`** (`_build_backfillable_sources`).

```bash
uv run agri-cli ingest-backfill --source sentinel2-ndvi \
  --years 4 --chunk-days 5 --bbox "-125,42,-111,49"
```

- Writes through the **same** path as forward ingestion (`select_writes` →
  `write_features`), so Type-2 versioning, geometry linking and identity minting are
  identical. There is no separate backfill write path.
- **Resumable.** Chunks are anchored at window start, so the same window always cuts
  the same chunks. Each logs `backfill_chunk_complete` with its bounds — resume a
  died run with `--since <last completed chunk end>`.
- **Idempotent.** Keys are `{cell_key}:{observedAt}`, so re-running rewrites rather
  than duplicating.
- **Safe alongside a concurrent `ingest-all`.** `lock_feature_event_keys` takes the
  feature lock before the geometry lock, matching `writer._ingest_resolved_batch`;
  the ordering exists specifically to prevent that deadlock.

### Chunk economics (measured, not estimated)

Each NDVI chunk starts with a full pending grid and stops when full, so cost is
**linear in chunk count** — it does not get cheaper as it goes. Each cell gets **at
most one observation per chunk**, so tighter chunks are what buy temporal density.

A completed reference run: `--years 4 --chunk-days 5` over `-125,42,-111,49`
(1,568 cells) → **293 chunks, 184,409 rows, 1,195 distinct dates, ~4 min/chunk,
~19 h wall clock, +251 MB**. Sentinel-2 revisit is ~5 days, so `--chunk-days 5` is
about the tightest that reliably finds a clear scene.

Expect seasonal gaps: `MAX_SCENE_CLOUD_COVER_PERCENT = 20` means many PNW Nov–Feb
chunks legitimately return nothing. That is honest, not a bug — but anything
downstream assuming regular cadence must handle it.

### Storage cost per row (measured in prod)

| Table / layer | bytes per row |
|---|---|
| `geo.drought_areas` | **490,038** |
| burn-severity | 150,253 |
| fire-perimeters | 130,583 |
| evacuation-zones | 8,032 |
| vegetation | **730** |
| `agri.signal_observation` | **535** |

One drought polygon costs as much as ~915 signal observations. Narrow fact tables are
essentially free; multipolygon layers are not.

## Credentials — one pipeline requires any, one accepts an optional one

Two secrets are **required**, both by the CDS ERA5-Land lane: **`CDSAPI_URL`** and
**`CDSAPI_KEY`**.

One is **optional**: **`OPEN_METEO_API_KEY`**, read by
`ingest/open_meteo.py::resolve_open_meteo_api_key` for the
`historical-open-meteo-backfill` archive lane. Absent is the supported default
(free host, free quota); present switches the request to
`customer-archive-api.open-meteo.com` and buys quota, not access. It is not in
any plan, so it does not change `plan_checksum`, and it is never persisted.

Everything else is keyless and open: Sentinel-2 (Earth Search), NASA POWER,
USGS NWIS, USDM, MTBS (USDA ArcGIS), USDA Soil Data Access, HydroSHEDS,
ISRIC SoilGrids. `NASA_FIRMS_KEY` is used by the FIRMS path only.

**The CDS gotcha:** `_require_cds_credentials()` reads `os.environ` **directly**.
`Settings` uses pydantic-settings' `env_file`, which populates the settings object and
**not** `os.environ`, and there is no CDS field in `Settings`. Listing them in `.env`
is therefore *inert on its own* — export them:

```bash
set -a; . ./.env; set +a
```

## Blocked pipelines — know which kind of blocked

| Pipeline | Credential | Other prerequisite |
|---|---|---|
| ERA5-Land (soil moisture, soil temp) | `CDSAPI_*` **and** licence `cc-by` rev 1 accepted in a browser | a NASA lattice **run** first |
| NASA historical lattice | none | a reviewed plan artifact |
| MTBS | none | the `burn-severity` row in `geo.layers` |
| USDM history | none | none — runnable as-is |
| Forecast lane, `strategy-train` | none | enough rows in `agri.*` |

**ERA5's real prerequisite is not the checksum.** `nasa_lattice_plan_checksum` is
unenforced metadata (one occurrence, nothing validates it). The enforced gate is
`historical_writer._require_era5_spatial_cells`: an `agri.spatial_cell` row must exist
for every ERA5 `cell_key`, and those are created only by a NASA lattice run. So the
order is **NASA plan → NASA run → ERA5 plan → ERA5 run**. Never hand-write a
checksum — derive it with `historical_nasa_plan_checksum()`.

ERA5's window is forced to exactly four calendar years, so there is no small probe
run: the minimum is 49 monthly CDS retrievals. Those are **per-period, not per-cell** —
requesting 110 cells costs the same 49 retrievals as 4, so widen the lattice *before*
running rather than after.

## Gaps are data — never fill them

- USDM `ingest-drought-history` records an unpublished week as an explicit **gap**. A
  reported gap is correct behaviour.
- An NDVI cell whose lattice was entirely cloud/snow/shadow is written as nothing, not
  interpolated or carried forward.
- MTBS `HistoryCapability(supported=False)` is deliberate: a `fetch_history` returning
  `[]` is the "gap certified as complete" failure `source.py` exists to prevent.
- MTBS `observed_at` is the cohort's **publication** date, never `ig_date` — dating by
  ignition would backdate the geometry version by the ~18-month mapping lag and let a
  time-slider position show fires nobody could have known about. `ignitionDate` is
  carried separately in properties.

## The Railway ingest cron

`plantgeo-ingest-cron` builds the **repository root** context. Two dashboard settings
are required and **changing one at a time fails**:

| Setting | Value |
|---|---|
| Root Directory | `/` |
| Config-as-code path | `infra/cron-ingest/railway.json` |

`RAILWAY_DOCKERFILE_PATH` as an environment variable **can never work** — the repo-root
`railway.json` declares the Next.js Dockerfile and config-as-code overrides the env
var. `RAILWAY_CONFIG_PATH` as a variable is not honoured either. Neither setting is
reachable from the CLI (`railway service` has no `update`).

Symptom → cause:

- `"/services/agri-data-service/src": not found` → Root Directory still `/infra/cron-ingest`
- dies on `NEXT_PUBLIC_PMTILES_URL must be a reviewed production URL` → building the
  **root** Dockerfile; config-as-code path unset

The cron image deliberately carries **no migration machinery** (`alembic/`, `db/`,
`alembic.ini` are not COPYed), so it cannot run a migration by construction.

## Failure modes whose cause is not what the error says

| Error | Real cause |
|---|---|
| `Sentinel-2 NDVI sampling requires an upstream client on the fetch request` | fixed — `_sample_grid` now owns a client when the caller supplies none |
| `configured ingestion layer does not exist: <name>` | `geo.layers` has no such row; add it as a migration, do not invent one |
| `ERA5-Land operation failed (HTTPError)` | usually the unaccepted `cc-by` licence — the CLI collapses non-`ValueError` to a class name |
| `realtime_publish_unavailable … WinError 1225` | local Redis unreachable; **benign**, the write still lands |
| `PytestCacheWarning … Access is denied` | ACL-poisoned `.pytest_cache`; benign |

## Before claiming a layer is broken

Establish which of four gates it is behind, in order:

1. **no rows** — count in `geo.features` by layer name
2. **rows but no read-model reader** — check `environmental-read-model.ts`
3. **reader but a governance stub** — grep `unavailableCollection` in
   `trpc/routers/environmental.ts`
4. **served but no tile template** — `getEnvironmentalTileTemplate()` returns `""`
   under `ENVIRONMENTAL_TILES_CONFIGURED = false`

Only gate 1 is an ingestion problem. Also check the migration state: a tile function
authored in `drizzle/` is not live until applied (`drizzle.__drizzle_migrations`).

## Testing

One sweep at the end, never test→fix→test loops:

```bash
uv run ruff check src/ tests/ && uv run mypy src && uv run pytest tests/ -q
```

Route the approval pass to `quality-reviewer`; the author never self-approves.

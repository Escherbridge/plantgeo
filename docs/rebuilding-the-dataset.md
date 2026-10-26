# Rebuilding the dataset from a clone

> **STATUS — 2026-08-22, body below untouched.** The architecture pivot
> (`conductor/RUNBOOK.md` §0.23/§0.24) moves every data plane out of Postgres
> into day-partitioned Parquet on Railway object storage, read by DuckDB+Polars.
> The "local warehouse" Postgres bootstrap (port 5442) and the ingestion verbs
> below describe the pre-pivot path. New or rebuilt layers should follow the lane
> structure in `conductor/code_styleguides/layer-lanes.md` instead; the
> Parquet-era equivalent of this rebuild guide has not been written yet. Read
> RUNBOOK §0.23/§0.24 first.

This page is for someone who has just cloned the repository and wants the map
layers populated with real data on their own machine. It assumes the application
already runs — see the cold start in the [repository README](../README.md) — and
it never asks you to touch a hosted database.

Two planes stay separate throughout:

- the **application database**, managed by Drizzle, which the Next.js app uses;
- the **local warehouse**, a dedicated PostgreSQL container on loopback port
  `5442`, managed by Alembic, which holds every governed environmental source.

Ingestion writes only to the warehouse. **Stale as of 2026-08-08:** the loader role
and the "must differ from `DATABASE_URL`" distinctness check described here were
retired in revision `20260808_0019` — `require_local_source_loader_database_url()`
now simply falls back to `DATABASE_URL` and raises only when both are unset (see
`services/agri-data-service/README.md` §3.1 for the full story).

## Credentials by source

Most sources need nothing. Establish this before you start, because the failure
modes differ: a keyless source that fails is a network or plan problem, while a
keyed source that fails without its variable set says so explicitly.

| Source | Keyed? | Environment variable | Where to get it | What breaks without it |
| --- | --- | --- | --- | --- |
| NASA POWER daily (live lane `climate-nasa-power-direct-forward`) | No | — | Public API, no registration | Nothing; served by `pipeline/direct/climate` lane |
| U.S. Drought Monitor (live lane `drought-direct-forward`; operator repair `python -m agri_data_service.pipeline.direct.drought.backfill`) | No | — | Public shapefile/JSON endpoints | Nothing; served by `pipeline/direct/drought/` |
| MTBS burned area (live lane `mtbs-forward`; forward capture `burn-severity-direct-forward`) | No | — | Public USDA/USGS ArcGIS service | Nothing; served by `pipeline/direct/burn_severity` |
| Open-Meteo weather (live lane `weather-observations-direct-forward`) | No | — | Public API, no registration | Nothing; served by `pipeline/direct/weather_observations` |
| USGS NWIS streamflow (live lane `water-gauges-direct-forward`) | No | — | Public water services API | Nothing; served by `pipeline/parquet/water_gauges_forward` |
| NWS observations (live lane `sensors-direct-forward`) | No | `NWS_API_USER_AGENT` *(optional)* | Not a key — a contact string | Nothing; defaults to `plantgeo-agri-data-service`. Set a real contact before running at volume |
| Sentinel-2 / NDVI (live lane `vegetation-sentinel2-ndvi-direct-forward`) | No | — | Public Earth Search STAC API | Nothing; served by `pipeline/direct/vegetation` |
| NIFC fire perimeters (live lane `fire-perimeters-direct-forward`) | No | — | Public ArcGIS feature service | Nothing; served by `pipeline/direct/fire_perimeters` |
| Evacuation zones (live lane `evacuation-zones-direct-forward`) | No | `EVACUATION_ZONES_LAYER_ID` *(optional)* | Not a key — a layer label override | Nothing; uses a default layer name; served by `pipeline/direct/evacuation_zones` |
| **NASA FIRMS active fire** (live lane `fire-detections-direct-forward`) | **Yes** | `NASA_FIRMS_KEY` | Free MAP_KEY from [firms.modaps.eosdis.nasa.gov](https://firms.modaps.eosdis.nasa.gov/api/area/) | The writer raises `NASA_FIRMS_KEY environment variable is not set` and exits; served by `pipeline/direct/fire_detections` |
| **ERA5-Land** (live lane `soil-era5-land-direct-forward`) | **No** | — | Published by NASA data pool, no registration | Served by `pipeline/direct/soil` lane; CDS CLI backfill removed 2026-10-26 |
| Open-Meteo weather (config-driven lanes via `pipeline/runner`) | Optional | `OPEN_METEO_API_KEY` *(optional)* | A paid Open-Meteo subscription for keyed endpoints; keyless endpoints work free | Absent, calls the free public host; keyed endpoints with empty key error (exit 78). Config runner reads `OPEN_METEO_API_KEY` directly from shell export, not `.env` |

So: two credentials are *required* by a lane, and only one of them needs a
hosted account with a browser step. `OPEN_METEO_API_KEY` is the only optional
credential — it buys quota, not access. Everything else on the list is keyless.

One key fact about the surviving variable:

- **`OPEN_METEO_API_KEY` is exported, never stored, and read only by the config
  runner.** It is not part of any plan and does not enter `plan_checksum`. The
  warehouse records the host that answered, never the key:

- **The FIRMS recent-days API is keyed; the archive is a separate product.**
  `NASA_FIRMS_KEY` covers the bounded recent-detections endpoint that the
  `fire-detections-direct-forward` lane (`pipeline/direct/fire_detections`) calls.
  Longer fire history comes from MTBS (`mtbs-forward`, `pipeline/direct/burn_severity`),
  which needs no key.

`NASA_FIRMS_KEY` is already present in the root `.env.example`, and both it and
`NWS_API_USER_AGENT` are described in [env-vars.md](./env-vars.md).

## Bootstrap the local warehouse

The database owner performs these gates by hand. Neither the application nor a
scheduler may enable extensions or create privileged roles. Full detail,
including backup and restore, is in
[infra/local-warehouse/README.md](../infra/local-warehouse/README.md).

1. **Start the container.** Copy `infra/local-warehouse/.env.example` to
   `infra/local-warehouse/.env`, set a unique password, then bring up the
   Compose project defined by `infra/local-warehouse/compose.yaml`. After it
   exists, `infra/local-warehouse/start-dw-dev.ps1` is the safe day-to-day
   launcher — it starts the retained container and refuses to silently create a
   blank one.
2. **Enable extensions** by running `infra/local-warehouse/enable-extensions.sql`
   as the owner. Verify `postgis`, `vector`, and `pgcrypto` are installed.
   Alembic fails before creating any `agri` object if they are not. (Note:
   TimescaleDB was removed on 2026-08-25.)
3. **Apply the `agri` migrations:**

   ```powershell
   Set-Location services/agri-data-service
   uv run agri-service ops db-upgrade
   uv run agri-service ops db-status
   ```

4. **Set the warehouse DSN** in your operator environment. There is no role to
   create first — the 2026-08-08 role teardown (`20260808_0019`) deleted
   `create-loader-role.sql` and every DSN assertion with it:

   ```text
   DATABASE_URL=postgresql+asyncpg://plantgeo_owner:<owner-password>@127.0.0.1:5442/plantgeo
   ```

   `LOCAL_SOURCE_LOADER_DATABASE_URL` is an optional override that falls back to
   the value above. Nothing is validated, so confirm the target yourself.

Confirm the wiring without starting any work. `ops pipeline-status` no longer exists
(removed with the rest of the pre-Parquet ingestion CLI); the current equivalent is
the lane inventory the job executor itself prints:

```powershell
uv run agri-service ops jobs-executor --inventory
```

It prints the code-owned lane table (every `LANE_SPECS` entry, active or quarantined)
and exits without running a tick — the fastest local way to catch a missing loader DSN
or an allow-list typo before `jobs-executor` is left running continuously.

## Retired PostgreSQL-era replay plans

The service-local `plans/` tree and its plan generators were retired after the
environmental serving plane moved to governed Parquet lanes. Do not regenerate
or run those historical PostgreSQL replay plans. The current rebuild path is the
lane-specific forward/history/gap workflow documented below and in
[`layer-lane-standard.md`](layer-lane-standard.md).

## Ingest

### Set a bounding box first

**`INGEST_BBOX` has no default, and an unset box makes every source skip
silently rather than fail.** `resolve_bounded_bbox` returns `None` when neither
`--bbox` nor `INGEST_BBOX` is set, and the job returns a *skipped* result with
`UNCONFIGURED_BBOX_REASON`. A run that ingested nothing therefore looks like a
clean run. `INGEST_BBOX=` ships empty in `.env.example`, so set it before your
first ingestion:

```powershell
$env:INGEST_BBOX = '-125,42,-111,49'   # the canonical Pacific Northwest box
```

The value is `west,south,east,north`. `-125,42,-111,49` is
`PACIFIC_NORTHWEST_COVERAGE_BBOX`, the box the existing data and the default
sensor station rosters (`WA,OR,ID,MT`) were built around; widen
`SENSOR_STATION_STATES` if you move the box. Any verb also takes `--bbox` to
override per run.

### Current-conditions layers

There is no `data ingest-*` CLI family any more — it was deleted in the "Complete
greenfield Parquet cutover" (`fd602901`, 2026-09-12). Every current-conditions layer
is now a direct-to-Parquet writer registered as a lane in
`execution/lane_specs.py`/`execution/lane_ids.py`. In production, the continuous
`agri-service ops jobs-executor` service dispatches each lane on its own cadence,
gated by the `PLANTGEO_JOB_EXECUTOR_ACTIVE_LANES` allow-list. Locally, run one lane's
writer module directly:

```powershell
Set-Location services/agri-data-service

uv run python -m agri_data_service.pipeline.direct.weather_observations
uv run python -m agri_data_service.pipeline.direct.drought
uv run python -m agri_data_service.pipeline.parquet.water_gauges_forward
uv run python -m agri_data_service.pipeline.direct.sensors
uv run python -m agri_data_service.pipeline.direct.vegetation
uv run python -m agri_data_service.pipeline.direct.fire_perimeters
uv run python -m agri_data_service.pipeline.direct.evacuation_zones
uv run python -m agri_data_service.pipeline.direct.fire_detections   # needs NASA_FIRMS_KEY
```

There is no composite "run every lane" verb any more — each module above is its own
failure domain; run the ones you need.

MTBS (`mtbs-forward`) publishes a weekly historical-cohort census; the same package
also runs the daily current-snapshot capture with `--current-snapshots` (this is what
the `burn-severity-direct-forward` lane passes — see `execution/lane_specs.py`):

```powershell
uv run python -m agri_data_service.pipeline.direct.burn_severity
uv run python -m agri_data_service.pipeline.direct.burn_severity --current-snapshots --time-budget-seconds 1800
```

Per-lane backfill/repair modules exist alongside the forward writers where a source
needs one, e.g. `pipeline/direct/drought/backfill.py`, `pipeline/direct/burn_severity/backfill.py`,
`pipeline/direct/vegetation/backfill.py` — run them the same way, as
`python -m agri_data_service.pipeline.direct.<lane>.backfill`. There is no single
generic `ingest-backfill --source` verb any more; the old PostgreSQL-era
`ingest-geometry-repair` pass has no Parquet-era equivalent because nothing here
writes `geo.geometry` today.

Order matters for ERA5. The enforced prerequisite is not the checksum but the
spatial cells: `_require_era5_spatial_cells` raises "ERA5 persistence requires
the complete matching NASA sampling lattice in the warehouse" unless an
`agri.spatial_cell` row exists for every `cell_key` in the ERA5 plan, and those
rows are created only by the NASA POWER persist path. **Run the NASA lattice
backfill before ERA5**, or you fail at the warehouse rather than at validation.

Reviewed plans for the national scope live in
`infra/local-warehouse/plans/`. Progress is checkpointed under
`.agri-local-runs/`, and a retry reuses the validated raw cache rather than
re-requesting the provider.

### One-off reviewed GeoJSON

**Gone.** `data source-ingest` / `data source-ingest-status` were deleted in the same
2026-09-12 cutover as the rest of this section. `execution/source_ingestion.py` still
holds the plan/payload primitives those verbs called, but no CLI command and no
registered HTTP route invokes it any more — there is no current path to publish a
one-off reviewed GeoJSON release through this service.

## What you cannot reproduce from a clone

Honest limits, and the local substitute for each:

- **The Railway project and its services.** Deployment topology, service
  variables, and the cron schedule are account-owned. Substitute: run everything
  locally as above; nothing in this guide needs Railway.
- **The Cloudflare R2 bucket and its custom tile domain.** Basemap PMTiles are
  served from a bucket behind a domain that only the account owner controls.
  Substitute: point the map at your own PMTiles or a public basemap style, and
  serve dynamic tiles from local Martin (`npm run tiles:serve`).
- **A Copernicus account.** ERA5-Land is now published by the live lane
  `soil-era5-land-direct-forward` and does not require credentials locally.
  The CLI backfill verb was removed 2026-10-26. The NASA POWER soil-wetness
  parameters (`GWETTOP`, `GWETROOT`, `GWETPROF`) from `climate-nasa-power-direct-forward`
  are a keyless stream but measure saturation, not volumetric water content.
- **Data already persisted in the maintainer's production database.** Row counts
  you may see referenced in docs or reports reflect completed runs there.
  Substitute: rebuild locally; the plans and verbs above are the whole recipe.
- **The uncommitted soil-wetness plan** noted above.

Everything else — the application, the warehouse schema, the plan files, and
every keyless source — rebuilds from this repository.

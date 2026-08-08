# agri-data-service — operator guide

This is the from-zero guide for running this service's own data ingestion. It assumes you can
read `SELECT ... WHERE` SQL and run shell commands, and nothing more. Every command below is
copy-pasteable; every trap is called out in a warning block **before** the step that triggers it.

If you only read one thing before touching anything: read [§3, the DSN contract](#3-the-dsn-contract)
and [§3.2, the grant gap](#32-the-grant-gap--read-this-before-you-run-anything). They are the two
ways a first run goes sideways.

## Table of contents

1. [What this service is](#1-what-this-service-is)
2. [Prerequisites](#2-prerequisites)
3. [The DSN contract](#3-the-dsn-contract)
4. [Environment variables](#4-environment-variables)
5. [Credentials](#5-credentials)
6. [First run](#6-first-run)
7. [The durable-lane workflow](#7-the-durable-lane-workflow)
8. [Full CLI verb inventory](#8-full-cli-verb-inventory)
9. [Known failure modes](#9-known-failure-modes)
10. [Where things live](#10-where-things-live)

---

## 1. What this service is

`agri-data-service` is a Python CLI (`agri-cli`) plus a small Sanic HTTP service that pulls
environmental data — fire detections, streamflow, weather, drought, vegetation, sensors — from
public upstream APIs and lands it in PostgreSQL so the PlantGeo map can serve it. It is not a
web app you "start" to ingest data; ingestion happens by running individual `agri-cli` commands
(by hand, from a scheduled task, or from a Railway cron container).

```
source APIs (NASA FIRMS, USGS NWIS, Open-Meteo, WFIGS, USDM, Sentinel-2, ...)
        |
        v
  agri-cli ingest-*  /  jobs-*  (this service)
        |
        v
  PostgreSQL: geo.* tables (forward/current data) + agri.* tables (lineage, job ledger)
        |
        v
  Martin tile server + the Next.js tRPC API
        |
        v
  PlantGeo map (src/components/map/)
```

Two schemas, two migration owners, one physical database: the Next.js app's Drizzle migrations
own `geo.*` (the tables the map reads), and this service's Alembic migrations own `agri.*` (source
lineage, job ledger, forecasting). The `ingest-*` verbs write `geo.*` directly
(`src/agri_data_service/ingest/writer.py:88,111,122`); the plan-based `historical-*` verbs write
`agri.*` lineage tables.

Related docs, so this file doesn't duplicate them:
- [`docs/rebuilding-the-dataset.md`](../../docs/rebuilding-the-dataset.md) — a from-a-clone
  recipe for the *current-conditions* verbs. It predates the durable-lane runtime in §7 of this
  document, so use this README for anything involving `jobs-*`.
- [`docs/runbooks/durable-backfill-lanes.md`](../../docs/runbooks/durable-backfill-lanes.md) —
  the deep operator reference for the `jobs-*` ledger: why it exists, the full Railway deployment
  story, and how to requeue a dead letter. §7 here is the short version.
- [`infra/local-warehouse/README.md`](../../infra/local-warehouse/README.md) and
  [`infra/local-warehouse/AGENTS.md`](../../infra/local-warehouse/AGENTS.md) — standing up the
  local warehouse this service ingests into.
- [`src/agri_data_service/ingest/AGENTS.md`](src/agri_data_service/ingest/AGENTS.md) — the
  per-producer design rationale (550 lines), i.e. *why* each source is built the way it is.

---

## 2. Prerequisites

**Python / uv**

- Requires Python `>=3.12` (`pyproject.toml:9`).
- Install [uv](https://docs.astral.sh/uv/) if you don't have it.
- From `services/agri-data-service`:

  ```bash
  uv sync --locked --all-extras
  ```

  ```powershell
  uv sync --locked --all-extras
  ```

  Expected: a `.venv/` appears in `services/agri-data-service/` and the command exits 0 with a
  "Resolved N packages" / "Installed N packages" summary. `--all-extras` also pulls the `dev`
  extra (`ruff`, `mypy`, `pytest`, ...) — it is a project extra, not a dependency group, so
  `uv sync` without `--all-extras` (or with `--no-dev`) will silently skip it.
- Every command in this document is `uv run agri-cli <verb>` — the console script
  `agri-cli = "agri_data_service.cli:cli"` (`pyproject.toml:49`) only resolves inside that venv.

**PostgreSQL — read this before you run `docker compose up`**

> [!WARNING]
> **Do not use `services/agri-data-service/docker-compose.yml`.** It looks like the obvious
> thing to run from this directory, and it is the *only* `docker-compose.yml` here — but it
> stands up a database this service's own config validator refuses for ingestion. Verified,
> line by line:
>
> | | `docker-compose.yml`'s `db` service | What `config.py` requires for ingestion |
> |---|---|---|
> | Port | `5433` | `5442` (`config.py:14`) |
> | Database | `agri_data` | `plantgeo` or `plantgeo_*` (`config.py:15,173-176`) |
> | Role | `plantgeo` | `plantgeo_loader` (`config.py:16,201-203`) |
> | Image | `postgis/postgis:16-3.4` | needs TimescaleDB too |
> | Init script | `infra/db/init-extensions.sql` — installs `postgis`, `vector`, `uuid-ossp`, **no `timescaledb`** | `postgis` + `timescaledb` + `vector` + `pgcrypto`, all four, or the Alembic foundation migration refuses to create the `agri` schema (`infra/local-warehouse/AGENTS.md`) |
>
> Every mismatched field above raises a distinct, named error out of
> `Settings.require_local_source_loader_database_url()` (`config.py:158-204`) the first time you
> try to ingest anything against it. It cannot even complete a migration. Treat
> `services/agri-data-service/docker-compose.yml` as the HTTP-service-only dev stack (it does
> still work for `make dev` against a toy database) and never for ingestion.

The supported local target is **`infra/local-warehouse`**, two directories up from here, at the
repo root. It is a dedicated Podman-based Postgres 16 + TimescaleDB + PostGIS + pgvector
container, isolated from every other local database on your machine, listening only on
**`127.0.0.1:5442`**, database **`plantgeo`** (`infra/local-warehouse/compose.yaml:11-16`).

```powershell
# from the repo root
Copy-Item infra/local-warehouse/.env.example infra/local-warehouse/.env
notepad infra/local-warehouse/.env   # set PLANTGEO_LOCAL_DB_PASSWORD to a unique local value

powershell.exe -NoProfile -File infra/local-warehouse/start-dw-dev.ps1 -CreateIfMissing
```

```bash
# bash (WSL/macOS/Linux); PowerShell is the primary path this repo documents for local-warehouse,
# but the underlying commands are plain podman compose and work the same in bash:
cp infra/local-warehouse/.env.example infra/local-warehouse/.env
$EDITOR infra/local-warehouse/.env   # set PLANTGEO_LOCAL_DB_PASSWORD

podman --connection podman-machine-default-root compose \
  --project-name plantgeo-warehouse --env-file infra/local-warehouse/.env \
  -f infra/local-warehouse/compose.yaml up -d
```

Expected: `podman ps` shows a `plantgeo-warehouse_plantgeo-warehouse_1` container `Up` and
`(healthy)` within about a minute. On every later day, use
`start-dw-dev.ps1` **without** `-CreateIfMissing` — it deliberately refuses to create a fresh,
blank warehouse if it can't find the existing container, so you don't accidentally lose your
local data by fat-fingering the launch.

**Extensions gate — manual, owner-run, never automatic**

> [!WARNING]
> `enable-extensions.sql`'s own header forbids copying it into an image init directory, and the
> Alembic foundation migration refuses to create the `agri` schema until `postgis`,
> `timescaledb`, `vector`, and `pgcrypto` are **all four** already installed
> (`infra/local-warehouse/AGENTS.md`). This is a step you run once, by hand, as the bootstrap
> owner — not something `db-upgrade` does for you.

```powershell
$env:PGPASSWORD = '<the PLANTGEO_LOCAL_DB_PASSWORD you set above>'
& 'C:\Program Files\PostgreSQL\16\bin\psql.exe' -h 127.0.0.1 -p 5442 -U plantgeo_owner -d plantgeo `
  -f infra/local-warehouse/enable-extensions.sql
```

```bash
PGPASSWORD='<the PLANTGEO_LOCAL_DB_PASSWORD you set above>' \
  psql -h 127.0.0.1 -p 5442 -U plantgeo_owner -d plantgeo \
  -f infra/local-warehouse/enable-extensions.sql
```

Expected: four `CREATE EXTENSION` (or `NOTICE: extension "..." already exists, skipping`) lines,
no errors. It is safe to re-run.

> [!NOTE]
> There is a *second*, unrelated extensions file at
> `services/agri-data-service/infra/db/init-extensions.sql`. That one belongs to the unsupported
> `docker-compose.yml` above (`postgis`, `vector`, `uuid-ossp`, **no `timescaledb`**). Do not use
> it here — use `infra/local-warehouse/enable-extensions.sql`.

**Migrate the `agri` schema**

```bash
cd services/agri-data-service
DATABASE_URL_SYNC='postgresql://plantgeo_owner:<password>@127.0.0.1:5442/plantgeo' \
  uv run agri-cli db-upgrade
```

```powershell
Set-Location services/agri-data-service
$env:DATABASE_URL_SYNC = 'postgresql://plantgeo_owner:<password>@127.0.0.1:5442/plantgeo'
uv run agri-cli db-upgrade
```

Expected: a stream of `INFO  [alembic.runtime.migration] Running upgrade ... -> ...` lines ending
without an error and returning to the prompt. `uv run agri-cli db-status` afterwards prints the
current revision (Alembic's normal `command.current(verbose=True)` text block, not JSON).

> [!WARNING]
> **`services/agri-data-service/.env.example`'s own default for `DATABASE_URL_SYNC` is
> `postgresql://geo:<password>@localhost:5432/plantgeo` — port 5432, role `geo`. That is not the
> local warehouse.** (`config.py:130`; the local warehouse is `127.0.0.1:5442`, role
> `plantgeo_owner`.) If you copy `.env.example` to `.env` verbatim and run `db-upgrade` without
> overriding `DATABASE_URL_SYNC`, you migrate whatever database *is* listening on `5432` locally
> (if anything), not the warehouse you just built. Set `DATABASE_URL_SYNC` explicitly, every
> time, until you've confirmed your `.env` has the right value. See §3 for the full story on why
> this variable is so easy to get wrong silently.

---

## 3. The DSN contract

This is the biggest trap in the whole service. **Ingestion, migrations, and the HTTP API each
read a different connection-string environment variable, and none of them falls back to another
one.** Getting this wrong doesn't usually error loudly — it silently targets the wrong database.

| What you're doing | Variable it reads | Falls back to `DATABASE_URL`? |
|---|---|---|
| `ingest-*` / `jobs-*` / `validate-streams` (any ingestion verb) | `LOCAL_SOURCE_LOADER_DATABASE_URL` | **Never** (`config.py:161-165`) |
| `alembic upgrade` / `db-upgrade` / `db-status` (migrations) | `DATABASE_URL_SYNC` | **Never** (`alembic/env.py:14`) |
| The Sanic HTTP API in `combined_local` profile | `DATABASE_URL` | n/a — this is its own variable |

### 3.1 The loader DSN: `LOCAL_SOURCE_LOADER_DATABASE_URL`

`ingest_session()`, the only session every `ingest-*`/`jobs-*` verb opens, calls
`settings.require_local_source_loader_database_url()` and nothing else
(`src/agri_data_service/db/engine.py:133-138`). That validator (`config.py:158-204`) is strict:

- Scheme must be exactly `postgresql+asyncpg`, no query string, no fragment.
- Database name must be `plantgeo` or `plantgeo_<anything>`.
- Host/port/role must match one specific allowlisted triple:
  `(127.0.0.1, 5442, plantgeo_loader)` — the local warehouse — **or**
  `(switchback.proxy.rlwy.net, 37967, postgres)` — the Railway production proxy.
- `plantgeo_owner` (the bootstrap role) is refused by name even at an allowed host/port.
- **It must not equal `DATABASE_URL`, character for character** — if you set both to the same
  string, ingestion refuses to start.

> [!WARNING]
> The error message you'll see if `LOCAL_SOURCE_LOADER_DATABASE_URL` is unset says
> `"source-ingest requires LOCAL_SOURCE_LOADER_DATABASE_URL; DATABASE_URL is never a loader
> fallback"` (`config.py:163-165`). It names `source-ingest`, but it is raised for **every**
> ingest verb — `ingest-weather`, `jobs-plan-lane`, all of them, not just the literal
> `source-ingest` command. Don't go looking for a `source-ingest`-specific bug; set the variable.

```bash
export LOCAL_SOURCE_LOADER_DATABASE_URL='postgresql+asyncpg://plantgeo_loader:<loader-password>@127.0.0.1:5442/plantgeo'
unset DATABASE_URL   # or the "must not reuse DATABASE_URL" guard rejects you
```

```powershell
$env:LOCAL_SOURCE_LOADER_DATABASE_URL = 'postgresql+asyncpg://plantgeo_loader:<loader-password>@127.0.0.1:5442/plantgeo'
Remove-Item Env:\DATABASE_URL -ErrorAction SilentlyContinue
```

### 3.2 The grant gap — read this before you run anything

> [!WARNING]
> **Critical, verified, and currently unresolved:** the documented `plantgeo_loader` role
> (created by `infra/local-warehouse/create-loader-role.sql`, the file the setup docs point you
> to) **cannot execute a single `ingest-*` or `jobs-*` verb against your local warehouse.**
>
> `create-loader-role.sql:26-49` grants `plantgeo_loader` exactly: `CONNECT` on the database,
> `USAGE` on schema `agri`, and `SELECT, INSERT` on eleven named `agri.*` lineage tables. There is
> **no grant on schema `geo`** and **no grant on any `agri.job_*` table.** But every `ingest-*`
> verb writes `geo.features`/`geo.geometry`/`geo.drought_areas`
> (`src/agri_data_service/ingest/writer.py:88,111,122`), and every `jobs-*` verb reads and writes
> `agri.job_definition`/`job_run`/`job_work_item`/`job_attempt`/`job_checkpoint`
> (`src/agri_data_service/ingest/commands.py:545-548`). `grant-resolution-aware-loader.sql`, the
> one follow-up grant script that exists, adds only three more `agri.*` tables — still nothing on
> `geo` or `job_*`.
>
> **The symptom looks like an application bug, not a permissions problem:** you will see
> `permission denied for schema geo` on your first `ingest-weather`, and `permission denied for
> table job_definition` (or similar) on your first `jobs-plan-lane`. There is no code path that
> explains this to you.
>
> **The current working path, until a grants migration lands:** `run-backfill.sh`, the script the
> repo's own historical-backfill workflow uses, does not point the loader at your local
> warehouse at all — it sources `services/agri-data-service/.env`, takes whatever `DATABASE_URL`
> is defined there, and passes *that* as `LOCAL_SOURCE_LOADER_DATABASE_URL`
> (`run-backfill.sh:24-25`). Per that script's own header comment, "*the real prod DSN is passed
> as the loader*" (`run-backfill.sh:4-5`) — i.e. the DSN in this service's checked-in-locally
> `.env` is the **production** Railway database, reached over the allowlisted proxy target
> `(switchback.proxy.rlwy.net, 37967, postgres)`. **Running `run-backfill.sh` writes to
> production, not to your local warehouse.** This is why the two durable lanes already show real
> completed windows in production (§7) despite the local loader role being unusable — nobody has
> actually been running ingestion against `infra/local-warehouse` day to day.
>
> **What this means for you, practically:**
> - `db-status`, `db-upgrade`, and `pipeline-status` are unaffected — they use a different DSN
>   variable (`DATABASE_URL_SYNC`) or, for `pipeline-status`, only validate the DSN *string* and
>   never open a connection (§6). Those are safe to run locally today.
> - A genuine local, isolated ingestion write is **not currently a supported, reviewed path**.
>   If you want one anyway for local development, you can grant the missing privileges yourself
>   against your own local warehouse — you already hold `plantgeo_owner` there — but treat this
>   as an unreviewed, personal-sandbox workaround, not the documented practice:
>   ```sql
>   -- run as plantgeo_owner against your own 5442 warehouse only — never against production
>   GRANT USAGE ON SCHEMA geo TO plantgeo_loader;
>   GRANT SELECT ON geo.layers TO plantgeo_loader;
>   GRANT SELECT, INSERT, UPDATE ON geo.features, geo.geometry, geo.drought_areas TO plantgeo_loader;
>   GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA geo TO plantgeo_loader;
>   GRANT SELECT, INSERT, UPDATE ON agri.job_definition, agri.job_run, agri.job_work_item,
>     agri.job_attempt, agri.job_checkpoint TO plantgeo_loader;
>   ```
> - If you run any ingestion verb through `run-backfill.sh`, remember every window it plans, runs,
>   or reconciles lands in the **production** ledger. Read `jobs-reconcile-lane`'s dry-run output
>   (§7) carefully before ever passing `--apply`.

### 3.3 The migration DSN: `DATABASE_URL_SYNC`, and why overriding `DATABASE_URL` does nothing

`alembic/env.py:14` sets Alembic's target straight from `settings.database_url_sync` — never from
`DATABASE_URL`. `database_url_sync` has a **non-empty default**,
`postgresql://geo:plantgeo@localhost:5432/plantgeo` (`config.py:130`), so an unset variable is
not a loud failure — it quietly targets that default instead. And Pydantic Settings loads `.env`
**relative to your current working directory** (`config.py:47`,
`SettingsConfigDict(env_file=".env", ...)`), so which `.env` gets read depends on where you ran
`agri-cli` from.

> [!WARNING]
> **`services/agri-data-service/.env` exists in this working tree and defines
> `DATABASE_URL_SYNC`.** If you `export`/`$env:` a scratch `DATABASE_URL` to point somewhere safe
> and then run `make migrate` (or `uv run agri-cli db-upgrade`) from
> `services/agri-data-service/`, **you migrate whatever `DATABASE_URL_SYNC` names in that `.env`
> file — not the database `DATABASE_URL` points at.** There is no cross-check between the two
> variables anywhere in the code, and no verb prints the resolved target before migrating. If that
> `.env`'s `DATABASE_URL_SYNC` happens to be a production DSN, you have just silently migrated
> production. **Always set `DATABASE_URL_SYNC` explicitly** in the same command, and double-check
> it before running `db-upgrade`:
>
> ```bash
> echo "$DATABASE_URL_SYNC"   # read it back before you upgrade anything
> ```
> ```powershell
> $env:DATABASE_URL_SYNC   # read it back before you upgrade anything
> ```

### 3.4 Other DSN-shaped settings, for completeness

All of these refuse to inherit `DATABASE_URL` the same way the loader DSN does, and are unrelated
to day-to-day ingestion:

- `FORECAST_MV_REFRESH_DATABASE_URL` — a dedicated forecast materialized-view refresh login;
  must not share a username with `DATABASE_URL` (`config.py:206-235`).
- `FORECAST_ITERATION_DATABASE_URL` — evaluation-only forecast iteration writes; allowlisted to
  `(127.0.0.1, 5442, plantgeo_local_developer)` or the production proxy (`config.py:237-280`).
- `RECEIVER_WRITER_DATABASE_URL` / `PUBLISHED_READER_DATABASE_URL` — only valid under the
  matching `SERVICE_PROFILE` (`config.py:79-101`); irrelevant unless you're standing up a
  production HTTP profile.
- Leave `SERVICE_PROFILE` unset locally. Its default, `combined_local`, is the only profile that
  tolerates an incidental `DATABASE_URL` alongside `LOCAL_SOURCE_LOADER_DATABASE_URL`
  (`config.py:425-426`).

---

## 4. Environment variables

Every variable below is read **at call time**, not at process start — you can change one on a
long-running container without restarting it (`src/agri_data_service/ingest/AGENTS.md:41`).

| Variable | Required? | Default | What breaks without it |
|---|---|---|---|
| `LOCAL_SOURCE_LOADER_DATABASE_URL` | **Required** for every `ingest-*`/`jobs-*`/`validate-streams` verb | none | Every ingestion verb refuses to start (§3.1) |
| `DATABASE_URL` | Must be **absent** during ingestion; required for the `combined_local` HTTP profile | none | Set alongside the loader DSN → "must not reuse DATABASE_URL" |
| `DATABASE_URL_SYNC` | Required for migrations | `postgresql://geo:plantgeo@localhost:5432/plantgeo` (not the local warehouse!) | Silently migrates the wrong database (§3.2) |
| `INGEST_BBOX` | **Effectively required** | none | Unset ⇒ every job reports `skipped`, not `failed` — looks clean, writes nothing (§9) |
| `NASA_FIRMS_KEY` | Required for `ingest-firms`, `ingest-all`, `jobs-run --lane firms-archive` | none | FIRMS jobs fail; the archive lane permanently dead-letters every window it claims (§5, §9) |
| `CDSAPI_URL`, `CDSAPI_KEY` | Required for `historical-era5-backfill` only, and must be `export`ed (see §5) | none | ERA5-Land backfill refuses with a clear message |
| `INGEST_MAX_SOURCE_RECORDS` | Optional — **never set on an archive lane** | `10_000`, clamped `[1_000, 50_000]` | Silently drops the *oldest* days of an over-cap chunk while still reporting success (§9) |
| `FIRMS_DAY_RANGE` | Optional | `2`, clamped ≤ `5` | Values above 5 get a `400 Invalid day range` from FIRMS |
| `WEATHER_SAMPLE_SPACING_DEGREES` | Optional | `1.0`, clamped `[0.25, 5.0]`; grid capped at 150 points | Controls Open-Meteo fan-out density |
| `SENSOR_STATION_STATES` | Optional (comma list) | `WA,OR,ID,MT` | Which states' NWS sensor stations are pulled |
| `SENSOR_STATION_NETWORKS` | Optional | `ASOS,ASOS-HFM,RAWS,NonFedAWOS` | Which sensor networks are pulled |
| `SENSOR_MAX_STATIONS` | Optional | `750` | Caps sensor fan-out |
| `NWS_API_USER_AGENT` | Optional (a contact string, not a key) | `plantgeo-agri-data-service` | NWS asks for an identifiable UA; harmless to leave default |
| `DROUGHT_RETAINED_RELEASES` | Optional | `8` (~19 MB each) | How many USDM weekly releases stay cached |
| `OPEN_METEO_API_KEY` | Optional — buys quota, not access | none | Nothing breaks; you just hit the free tier's rate limit sooner |
| `REDIS_URL` | Optional | `redis://localhost:6379` | Absent is safe — realtime publish is best-effort and degrades quietly |
| `FIRMS_LAYER_ID` | Optional | `fire-detections` | Which `geo.layers` row FIRMS writes to |
| `WATER_GAUGES_LAYER_ID` | Optional | `water-gauges` | Which layer USGS streamflow writes to |
| `WEATHER_LAYER_ID` | Optional | `weather-observations` | Which layer Open-Meteo writes to |
| `SENSORS_LAYER_ID` | Optional | `sensors` | Which layer NWS sensors write to |
| `FIRE_PERIMETERS_LAYER_ID` | Optional — **see the trap below** | `fire-perimeters` | Which layer WFIGS perimeters write to |
| `VEGETATION_LAYER_ID` | Optional | `vegetation` | Which layer Sentinel-2 NDVI writes to |
| `WATERSHEDS_LAYER_ID` | Optional | `watersheds` | Which layer USGS WBD writes to |
| `BURN_SEVERITY_LAYER_ID` | Optional | `burn-severity` | Which layer MTBS writes to |
| `EVACUATION_ZONES_LAYER_ID` | Optional | `evacuation-zones` | Which layer evacuation zones write to |
| `RAILWAY_REPLICA_ID` | Optional | none; falls back to `jobs-run:<uuid4>` | Names the lease owner for `jobs-run` |
| `AGRI_ALEMBIC_CONFIG` | Optional | `<service>/alembic.ini` | Path override for Alembic's config file |
| `AGRI_TEST_DATABASE_URL` | Test-only | none | Real-DB tests skip silently if unset |

> [!WARNING]
> **`FIRES_LAYER_ID` vs `FIRE_PERIMETERS_LAYER_ID` — setting the wrong one is a silent no-op.**
> The repo-root `.env.example` documents `FIRES_LAYER_ID` (`.env.example:202`, read by the
> Next.js push route at `src/app/api/ingest/fires/route.ts`). This Python service's
> `ingest-fire-perimeters` verb reads a **different** variable,
> `FIRE_PERIMETERS_LAYER_ID` (`src/agri_data_service/ingest/wfigs.py:50-51,93`), which appears in
> **neither** `.env.example` file. If you rename the fire-perimeters layer and only set
> `FIRES_LAYER_ID` (the documented one), the TypeScript push route follows the rename but this
> service's `ingest-fire-perimeters` keeps writing to the old `fire-perimeters` layer — silently
> splitting one logical layer across two IDs. Set both, kept equal, if you ever touch this.

**Twelve of the optional variables above appear in neither `.env.example` file** (root or
service): `INGEST_MAX_SOURCE_RECORDS`, `WEATHER_SAMPLE_SPACING_DEGREES`,
`SENSOR_STATION_STATES`, `SENSOR_STATION_NETWORKS`, `SENSOR_MAX_STATIONS`, `NWS_API_USER_AGENT`,
`DROUGHT_RETAINED_RELEASES`, `VEGETATION_LAYER_ID`, `WATERSHEDS_LAYER_ID`,
`BURN_SEVERITY_LAYER_ID`, `EVACUATION_ZONES_LAYER_ID`, `OPEN_METEO_API_KEY`. Also,
`INGEST_BBOX` and `NASA_FIRMS_KEY` exist only in the **repo-root** `.env.example`, not the
service's own — even though the code that reads them lives entirely in this service. Don't
assume "not in `.env.example`" means "not real"; this table is the complete list, cross-checked
against `config.py` and every `os.environ`/`getenv` call under `src/`.

---

## 5. Credentials

| Credential | Needed for | Where it comes from |
|---|---|---|
| `NASA_FIRMS_KEY` | `ingest-firms`, `ingest-all`, `jobs-run --lane firms-archive` | Free MAP_KEY from `firms.modaps.eosdis.nasa.gov/api/area/`. **Lives only on the cron/production service, not in any local `.env`** (`src/agri_data_service/ingest/lanes.py:209-210`). |
| `CDSAPI_URL` + `CDSAPI_KEY` | `historical-era5-backfill` only | A Copernicus Climate Data Store account, **plus accepting the CDS web licence terms in a browser** before the key works. |

> [!WARNING]
> **`jobs-run --lane firms-archive` without `NASA_FIRMS_KEY` permanently dead-letters every
> window it claims — before you ever run it, set the key.** The lane deliberately dead-letters on
> a missing credential rather than parking gracefully
> (`src/agri_data_service/ingest/archive_walk.py:643-651`, comment: "it burns the attempt budget
> and dead-letters rather than parking"). Eight attempts, doubling backoff, all burned, for every
> window `jobs-run` touches, and requeuing needs a manual SQL `UPDATE` (§7). Set the key *before*
> `jobs-plan-lane --lane firms-archive`, not after you notice windows dead-lettering.

**Optional, buys convenience not access:** `OPEN_METEO_API_KEY` (raises your Open-Meteo quota),
`NWS_API_USER_AGENT` (a contact string NWS asks for, not a real credential).

**Needs no credential at all:** USGS NWIS (streamflow), USDM (drought), MTBS (burn severity),
Open-Meteo (weather), Sentinel-2/Earth Search STAC (NDVI), WFIGS/NIFC ArcGIS (fire perimeters),
Oregon OEM (evacuation zones), USGS WBD (watersheds), NASA POWER (historical weather). If you're
doing a first local smoke test, pick one of these — see §6.

> [!WARNING]
> Never let `NASA_FIRMS_KEY` reach a log. FIRMS interpolates the key straight into the request
> URL, so the HTTP layer degrades every `httpx.HTTPError` to a generic
> `UpstreamTransportError` carrying only the exception class name, specifically so a raw
> exception with the URL in it never gets logged (`src/agri_data_service/ingest/AGENTS.md:82`).
> `CDSAPI_KEY` must be `export`ed into the shell environment, not just left in `.env` — the CDS
> client reads `os.environ` directly and pydantic-settings' `env_file` loading never populates
> `os.environ` (`src/agri_data_service/execution/historical_era5.py:683-684`).

---

## 6. First run

This proves the plumbing — config, DSN, migrations — without needing a credential. Given the
grant gap in §3.1, expect the last step to either succeed (if you've applied the local grants
yourself) or fail with a `permission denied for schema geo` error (if you haven't) — both
outcomes are informative, and the failure is explained in §3.1 and §9, not a sign something else
is broken.

```bash
cd services/agri-data-service
export INGEST_BBOX='-125,42,-111,49'    # Pacific Northwest coverage box, ingest/policy.py:17
export LOCAL_SOURCE_LOADER_DATABASE_URL='postgresql+asyncpg://plantgeo_loader:<loader-password>@127.0.0.1:5442/plantgeo'
unset DATABASE_URL
export DATABASE_URL_SYNC='postgresql://plantgeo_owner:<owner-password>@127.0.0.1:5442/plantgeo'

uv run agri-cli db-status          # proves DATABASE_URL_SYNC points where you think
uv run agri-cli pipeline-status    # proves the loader DSN string parses and is allowlisted
uv run agri-cli ingest-weather     # no credential needed; ~150 points; the fastest real write
uv run agri-cli validate-streams --format markdown --output /tmp/streams.md
```

```powershell
Set-Location services/agri-data-service
$env:INGEST_BBOX = '-125,42,-111,49'
$env:LOCAL_SOURCE_LOADER_DATABASE_URL = 'postgresql+asyncpg://plantgeo_loader:<loader-password>@127.0.0.1:5442/plantgeo'
Remove-Item Env:\DATABASE_URL -ErrorAction SilentlyContinue
$env:DATABASE_URL_SYNC = 'postgresql://plantgeo_owner:<owner-password>@127.0.0.1:5442/plantgeo'

uv run agri-cli db-status
uv run agri-cli pipeline-status
uv run agri-cli ingest-weather
uv run agri-cli validate-streams --format markdown --output $env:TEMP\streams.md
```

Expected output, in order:
1. `db-status` — Alembic's normal revision report, e.g. a line like
   `Current revision(s) for postgresql://...: <hash> (head)`. No hash means you haven't migrated
   yet; go back to §2.
2. `pipeline-status` — a JSON object with `"local_bulk_ingestion": "runnable with a reviewed plan
   and payload"` if the DSN string is well-formed, or `"blocked: <the specific config error>"` if
   not.
3. `ingest-weather` — one JSON line, `IngestionJobResult.to_summary()`, with
   `"records_seen"`/`"records_written"` counts. If it errors with `permission denied for schema
   geo`, that's the grant gap from §3.1, not a broken setup — see the options there.
4. `validate-streams` — writes a markdown report to the given path; the command itself prints one
   JSON summary line to stdout.

> [!WARNING]
> `pipeline-status` **only validates the loader DSN string** — it never opens a connection
> (`src/agri_data_service/cli.py:2844-2849`). Its other fields (`"state": "inactive"`,
> `"active_jobs": 0`, `"preaggregation_forecasts_training": "blocked pending separate
> implementation and evaluation"`) are **hardcoded literals**, not live checks
> (`cli.py:2850-2859`). A clean-looking `pipeline-status` output tells you nothing about whether
> your bbox is set, your credentials are valid, your migrations are current, or the database is
> even reachable. Don't treat it as a general health check.

If another agent has added an `/ops/backfill` dashboard to the Next.js app by the time you read
this, it gives a friendlier view of the same underlying data as `jobs-status`/`validate-streams`
— check for it, but the CLI verbs above always work and don't depend on the web app being up.

---

## 7. The durable-lane workflow

Two sources — NASA FIRMS fire archive and USGS streamflow archive — have a **durable job ledger**
behind their backfills, so a killed or interrupted walk resumes exactly where it left off instead
of silently losing windows. This replaced a bash driver that skipped 169 of 298 windows and still
reported success (full story:
[`docs/runbooks/durable-backfill-lanes.md`](../../docs/runbooks/durable-backfill-lanes.md)).

The model: **one lane → one `JobRun` → one work item per time window → one handler call per
chunk.** A lane (`src/agri_data_service/ingest/lanes.py`) declares a source, a floor date, a
window size, and a chunk size:

| Lane | Source | Chunk size | Window size | Credential |
|---|---|---|---|---|
| `firms-archive` | NASA FIRMS | 1 day | 5 days | `NASA_FIRMS_KEY` |
| `streamflow-archive` | USGS daily values | 10 days | 30 days | none |

**Use `streamflow-archive` for any first attempt at this workflow — it needs no credential.**

> [!WARNING]
> Everything in this section writes real rows. Per §3.1, the local loader role currently cannot
> run these verbs, so in practice you are most likely running them through `run-backfill.sh`
> against **production**. Read every `jobs-reconcile-lane` dry-run output before you ever pass
> `--apply`, and never plan `firms-archive` before `NASA_FIRMS_KEY` is set (§5).

```bash
uv run agri-cli jobs-plan-lane      --lane streamflow-archive
uv run agri-cli jobs-reconcile-lane --lane streamflow-archive          # dry run — read the span
uv run agri-cli jobs-reconcile-lane --lane streamflow-archive --apply  # settles already-landed windows
uv run agri-cli jobs-run            --lane streamflow-archive          # one bounded slice
uv run agri-cli jobs-status
```

```powershell
uv run agri-cli jobs-plan-lane      --lane streamflow-archive
uv run agri-cli jobs-reconcile-lane --lane streamflow-archive
uv run agri-cli jobs-reconcile-lane --lane streamflow-archive --apply
uv run agri-cli jobs-run            --lane streamflow-archive
uv run agri-cli jobs-status
```

**Or, via the repo's own launcher** (sources `.env`, wires the DSNs per §3.1's warning, exports
CDS credentials): `./run-backfill.sh jobs-status`, `./run-backfill.sh jobs-plan-lane --lane
streamflow-archive`, etc.

What each verb actually does to the ledger:

| Verb | What it does | Exit code |
|---|---|---|
| `jobs-plan-lane --lane <token> [--floor DATE] [--until DATE]` | Fans the lane's date range out into work-item rows. **Idempotent — safe to re-run daily**; `ON CONFLICT DO NOTHING` on both the run and each window. `--floor` mints a *second* run rather than editing the first. | Always `0` |
| `jobs-run --lane <token> [--budget-seconds N] [--worker-id S]` | Claims one window, walks one bounded slice, checkpoints, exits. **This is what a cron tick invokes.** | `0` unless a window dead-lettered this slice or the process raised |
| `jobs-status [--lane <token>]` | Per-state window counts, oldest outstanding window, dead-lettered shard keys with their failure class. Your primary "is this healthy" check. | Always `0` |
| `jobs-reconcile-lane --lane <token> [--apply]` | Checks what days already landed in the warehouse (via `geo.feature_observation_day`) and marks fully-covered windows `succeeded` without re-walking them. **Dry run by default** — `--apply` is required to write. | Always `0` |
| `validate-streams [--format json\|markdown] [--output PATH]` | The cross-stream completeness/validity report — the only verb that reports row counts per layer. | `1` only if a stream is `invalid` |

**Why `jobs-run` exits `0` while thousands of windows are still queued:** a healthy multi-week
backfill is incomplete by definition — FIRMS' full archive is ~1,900 windows. The only thing that
fails a slice is a window exhausting all 8 attempts and going `dead_letter` — a real loss that
needs a human. Everything else (`retried`, `deferred`, `yielded`, `abandoned`) is a normal park
that the next tick resumes.

**A dead-lettered window doesn't retry itself.** Requeuing is a deliberate manual step — read
`last_error_class` from `jobs-status` first, because requeuing blind just burns another 8
attempts:

| `last_error_class` | Action |
|---|---|
| `upstream_unavailable` | Requeue; it usually succeeds on retry. |
| `all_records_rejected` | Investigate first — usually an upstream schema/format change. |
| `record_cap_truncation` | You hit `INGEST_MAX_SOURCE_RECORDS`. Narrow the lane's `chunk_days`, then requeue. |
| `walk_skipped` | `INGEST_BBOX` was unset. Set it, then requeue. |
| `missing_credential` | The lane's credential variable (e.g. `NASA_FIRMS_KEY`) is unset. Set it, then requeue. |

```sql
-- requeue one dead-lettered window (there is no CLI verb for this yet — deliberately manual)
UPDATE agri.job_work_item
SET status = 'queued', completed_at = NULL, next_attempt_at = now(),
    attempt_count = 0, last_error_class = NULL, last_error_summary = NULL
WHERE job_run_id = (SELECT id FROM agri.job_run WHERE logical_run_key = 'archive-walk:<lane>:<floor>')
  AND shard_key  = '<shard-key-from-jobs-status>';
```

Full detail — the Railway deployment topology, the 30-minute cadence rationale, the cutover from
the old bash drivers — lives in
[`docs/runbooks/durable-backfill-lanes.md`](../../docs/runbooks/durable-backfill-lanes.md).

> [!NOTE]
> **`GloFAS`, `CAMS`, and the ensemble forecast lane are separate, plan-based, and fetch-only —
> see §8D and §9.** They are not part of this ledger; they use `--plan <file>.json` arguments and
> currently have no persist step at all.

---

## 8. Full CLI verb inventory

All commands are `uv run agri-cli <verb>` from `services/agri-data-service`. Every `ingest-*`/
`jobs-*` verb prints **one JSON summary line per job to stdout only**; operational logging goes to
stderr, so a cron log parser can read stdout as a clean JSON-lines stream.

### A. Forward ingest (hourly/daily-shaped; take `--bbox WEST,SOUTH,EAST,NORTH` unless noted)

| Verb | Source | Layer written | Credential |
|---|---|---|---|
| `ingest-firms` | NASA FIRMS | `fire-detections` | `NASA_FIRMS_KEY` |
| `ingest-streamflow` | USGS NWIS | `water-gauges` | none |
| `ingest-weather` | Open-Meteo | `weather-observations` | none |
| `ingest-fire-perimeters` | WFIGS/NIFC | `fire-perimeters` | none |
| `ingest-drought [--valid-date] [--replace]` (no `--bbox`) | USDM | `geo.drought_areas` | none |
| `ingest-ndvi` | Sentinel-2 | `vegetation` | none |
| `ingest-sensors` | NWS | `sensors` | none |
| `ingest-evacuation-zones` | Oregon OEM | `evacuation-zones` | none |
| `ingest-watersheds` | USGS WBD HUC12 | `watersheds` | none — run once, no scheduled cadence |
| `ingest-mtbs [--release-year N ...]` | MTBS | `burn-severity` | none — no schedule, deliberately excluded from `ingest-all` |
| `ingest-geometry-repair [--batch-size 200] [--max-features N]` | — | geometry repair pass | none |
| `ingest-all` | all of the above except watersheds/MTBS | — | needs `NASA_FIRMS_KEY` for its FIRMS leg |

`ingest-all` runs FIRMS → streamflow → weather → WFIGS → USDM → NDVI → sensors →
evacuation-zones, then geometry-repair **last**, sequentially (not concurrently) so one source's
failure can never mask another's (`src/agri_data_service/ingest/runner.py:43-53`).

### B. Non-durable backfill

| Verb | What it does |
|---|---|
| `ingest-backfill --source TOKEN [--since ISO] [--until ISO] [--years 2] [--chunk-days 7] [--bbox]` | Walks one source across a date range with no ledger — a bad `--source` prints the valid token list. Accepted tokens: `nws-sensors`, `sentinel2-ndvi`, `nasa-firms-archive`, `usgs-streamflow-archive`. |
| `ingest-drought-history [--years 2] [--replace]` | Walks USDM release history without the ledger. |

### C. Durable archive lanes (`jobs-*`) — see §7 for the full workflow

`jobs-plan-lane`, `jobs-run`, `jobs-status`, `jobs-reconcile-lane`, `validate-streams`. Registered
lanes: `firms-archive`, `streamflow-archive` only. **Always pass `--lane`, never `--definition`**
— a hand-spelled definition name joins to nothing while still exiting `0`
(`src/agri_data_service/ingest/commands.py:610-622`).

### D. Plan-based historical lanes (all take `--plan PATH`, a checksum-governed JSON file under `plans/`)

| Family | Verbs | Persists to the warehouse? |
|---|---|---|
| NASA POWER | `historical-nasa-backfill`, `-status`, `-materialize-parquet`, `-finalize` | Yes, via `-finalize` |
| ERA5-Land (CDS) | `historical-era5-backfill`, `-persist`, `-materialize-parquet`, `-finalize` | Yes |
| Open-Meteo archive | `historical-open-meteo-status`, `-backfill [--max-chunks] [--concurrency 1-4]`, `-persist` | Yes |
| USDM | `historical-usdm-backfill`, `-finalize`, `-status` | Yes, via `-finalize` |
| GloFAS | `historical-glofas-status`, `-backfill [--max-chunks] [--concurrency]` | **No — fetch-only, no persist verb exists** |
| CAMS | `historical-cams-status`, `-backfill [--max-chunks] [--concurrency]` | **No — fetch-only, no persist verb exists** |
| Ensemble forecast | `forecast-ensemble-status`, `forecast-ensemble-fetch [--max-chunks] [--concurrency]` | **No — hardcoded `"blocked_forecast_method_check"`** |

Plus `historical-promotion-spool --release-set-key --minimum-target-revision` and
`historical-promotion-upload --spool-directory` for pushing a finalized release to the promotion
receiver.

> [!WARNING]
> **GloFAS, CAMS, and the ensemble lane will run for hours, cost real upstream quota, and produce
> nothing a query can read.** Confirmed against `cli.py`: ERA5 and Open-Meteo each have a
> `-persist` verb; GloFAS and CAMS do not; the ensemble lane's own release manifest hardcodes
> `"warehouse_persistence": "blocked_forecast_method_check"`
> (`src/agri_data_service/execution/ensemble_forecast.py:92,601`). Don't run these three
> expecting rows to land.

### E. Migrations, seeding, and everything else

| Verb | What it does |
|---|---|
| `db-status` / `db-upgrade [REVISION=head]` | Alembic status / migrate, reading `DATABASE_URL_SYNC` (§3.2) |
| `seed` | Seeds strategy rows |
| `pipeline-status [--checkpoint PATH]` | DSN-string validation only — see the warning in §6 |
| `source-ingest --plan --payload` / `source-ingest-status CHECKPOINT` | Lower-level plan/payload ingestion primitive that the `historical-*` verbs build on |
| `job-logs-maintain [--retention-days 30] [--future-days 7]` | Prunes old job log rows |
| `local init\|status\|checkpoint\|interrupt\|resume\|register-output\|finalize\|publish` | The local-execution-run lifecycle for phase-one ETL/model runs |
| `forecast-refresh-ml-daily`, `forecast-run-iteration`, `forecast-reconcile-actuals` | Forecasting/ML evaluation loop — evaluation-only, no publication path |
| `forecast-vegetation-register\|-simulate\|-evaluate` | NDVI forecast evaluation harness |
| `strategy-label-map-preflight --mapping-manifest` / `strategy-train --label-bundle --output-artifact` | Strategy-selection ML training |

---

## 9. Known failure modes

| Symptom | What it actually means | Fix |
|---|---|---|
| Every job reports `skipped`, exit 0, zero rows, nothing looks wrong | `INGEST_BBOX` is unset. `resolve_bounded_bbox()` returns `None` and the job is a **typed skip, not a failure**, deliberately, so an unconfigured deployment doesn't go red (`src/agri_data_service/ingest/policy.py:39,86`) | Set `INGEST_BBOX` or pass `--bbox` |
| `permission denied for schema geo` / `for table job_definition` | The grant gap (§3.1) — the documented local loader role has no `geo` or `agri.job_*` privileges | See §3.1's options |
| `source-ingest requires LOCAL_SOURCE_LOADER_DATABASE_URL...` on any ingest verb, not just `source-ingest` | The message names `source-ingest` but is raised for every ingest verb (`config.py:163-165`) | Set the loader DSN (§3.1) |
| `LOCAL_SOURCE_LOADER_DATABASE_URL must not reuse DATABASE_URL` | Both env vars are the same string | Unset `DATABASE_URL`, or use `run-backfill.sh`'s dummy-DSN trick |
| `alembic upgrade` migrated a database you didn't expect | It read `DATABASE_URL_SYNC` from the CWD's `.env`, not the `DATABASE_URL` you set (§3.2) | Set `DATABASE_URL_SYNC` explicitly and echo it back before upgrading |
| `ERA5-Land requires accepted CDS web terms plus CDSAPI_URL and CDSAPI_KEY...` even though your `.env` has them | Credentials are in `.env` but not `os.environ` — the CDS client reads `os.environ` directly, and pydantic-settings never populates it (`execution/historical_era5.py:683-684`) | `export`/`$env:` the two variables |
| `400 Invalid day range. Expects [1..5]` from FIRMS | `FIRMS_DAY_RANGE` was set above 5 | Leave it unset (default `2`) |
| `jobs-run --lane firms-archive` dead-letters every window | `NASA_FIRMS_KEY` unset; the lane dead-letters on purpose rather than parking (§5) | Set the key *before* planning/running the lane |
| `jobs-run` exits `0` with thousands of windows still `queued` | Correct and healthy — a multi-week backfill is incomplete by definition (§7) | Not an incident |
| `validate-streams` exits `1` | At least one stream is `invalid` — rows that exist are *wrong* (null geometry, unlinked geometry, a duplicate identity, USGS's `-999999` sentinel served as a real reading). `incomplete` never fails the run. | Read that stream's evidence lines in the report |
| A chunk reports `records_seen=21000, records_written=0` and still "succeeds" | `INGEST_MAX_SOURCE_RECORDS` got bitten; the writer keeps the *newest* records and drops whole *oldest* days | Never set `INGEST_MAX_SOURCE_RECORDS` on an archive lane; the lane pins its own 50,000 ceiling |
| Redis connection refused, but the run continues | Realtime publish is best-effort; the publisher marks itself unavailable on the first `OSError` and just counts drops | Safe to ignore locally |
| GloFAS/CAMS/ensemble backfills run for hours and produce zero rows | These three lanes are fetch-only — no persist verb exists yet (§8D) | Expected; don't wait for rows |
| A weekly or 5-day-cadence stream (e.g. `drought_areas`, `vegetation`) reports a gap whose `days` is far larger than its `missing_day_count` | Not a defect: since 2026-08-08 the gap walk runs on the stream's declared `publication_cadence_days`, so `days` is the calendar silence (what the verdict compares against the cadence) while `missing_day_count` counts the releases actually owed inside it — one skipped weekly release reads as 13 days and 1 missed publication (`src/agri_data_service/ingest/validation/completeness.py`) | Read `missing_day_count`, not `days`, when asking what the stream failed to publish |
| `validate-streams`'s bbox-related checks silently do nothing | `INGEST_BBOX` isn't set in the environment `validate-streams` runs in — that check is unevaluated, not passing | Set `INGEST_BBOX` locally too, not just on ingest verbs |

---

## 10. Where things live

Under `src/agri_data_service/`: **`ingest/`** holds every `ingest-*`/`jobs-*` forward and
archive-lane producer plus the writer that lands rows in `geo.*` (`ingest/AGENTS.md` is the
550-line design rationale for *why* each producer works the way it does); **`execution/`** holds
the plan-based `historical-*`/`forecast-*` machinery (ERA5, NASA POWER, Open-Meteo, GloFAS, CAMS,
ensemble, and the ML/forecast evaluation loop) that writes `agri.*` lineage tables;
**`jobs/`** is the durable ledger runtime behind `jobs-*` (`jobs/AGENTS.md` covers the design
intent beyond the operator view in §7); **`db/`** is the SQLAlchemy engine/session plumbing and
DSN validators (`config.py` lives one level up, at `src/agri_data_service/`); **`models/`** are
the SQLAlchemy ORM models Alembic's autogenerate reads; **`routes/`** is the small Sanic HTTP
surface (local publication receiver, historical promotion receiver); **`seed/`** is one-time
strategy seed data; **`schemas/`** holds request/response schemas for the HTTP routes.

Three separate things all named some variant of "sql" or "db", easy to conflate:

- **`alembic/`** (service root) — the applier of record for the `agri` schema. Alembic owns
  migration ordering and the immutable/forward-only governance described in `alembic/AGENTS.md`.
  This is what `db-upgrade` runs.
- **`db/`** (service root, i.e. `services/agri-data-service/db/`, distinct from
  `src/agri_data_service/db/`) — a declarative, human-readable mirror of every object in the
  `agri` schema (tables, functions, views, triggers, one per file) plus `manifest.sql` to rebuild
  it in dependency order. It does **not** replace Alembic and is never applied directly — read
  `db/AGENTS.md` before touching it.
- **`src/agri_data_service/sql/`** — runtime query files, organized by the package that uses them
  (`cli/`, `db/`, `execution/`, `ingest/`, `jobs/`, `routes/`), being introduced now as this
  service moves hand-written SQL out of Python string literals. See `sql/AGENTS.md`.

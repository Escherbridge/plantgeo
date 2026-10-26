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
7. [The job executor](#7-the-job-executor)
8. [Full CLI verb inventory](#8-full-cli-verb-inventory)
9. [Known failure modes](#9-known-failure-modes)
10. [Where things live](#10-where-things-live)

---

## 1. What this service is

`agri-data-service` is a Python CLI (`agri-service`) plus a small Sanic HTTP service that pulls
environmental data — fire detections, streamflow, weather, drought, vegetation, sensors — from
public upstream APIs and lands it in PostgreSQL so the PlantGeo map can serve it. It is not a
web app you "start" to ingest data; ingestion happens by running individual `agri-service` commands
(by hand, from a scheduled task, or from a Railway cron container).

```
source APIs (NASA FIRMS, USGS NWIS, Open-Meteo, WFIGS, USDM, Sentinel-2, ...)
        |
        v
  agri-service ops jobs-executor  ->  python -m agri_data_service.pipeline.direct.<lane>  (this service)
        |
        v
  Governed Parquet on R2 (object store) + PostgreSQL agri.* tables (job ledger, lineage)
        |
        v
  parquet-api / `agri-service data parquet *` reads + the Next.js tRPC API
        |
        v
  PlantGeo map (src/components/map/)
```

**CORRECTED 2026-10-26: this diagram and paragraph described the pre-cutover architecture.**
"Complete greenfield Parquet cutover" (`fd602901`, 2026-09-12) deleted the entire `ingest-*`/
`jobs-plan-lane`/`jobs-run`/`jobs-status`/`jobs-reconcile-lane`/`validate-streams`/`pipeline-status`
CLI family and the `geo.*`-writing `ingest/writer.py` it called — there is no CLI verb or module at
that path any more. Every source today is a direct-to-Parquet writer module
(`pipeline/direct/<lane>/forward.py`), registered as a lane in `execution/lane_specs.py` and
`execution/lane_ids.py`, dispatched by the continuous `agri-service ops jobs-executor` service under
the `PLANTGEO_JOB_EXECUTOR_ACTIVE_LANES` allow-list (`execution/AGENTS.md` "Lane activation"). `agri.*`
is Postgres-only now — the job ledger, lineage and lane-audit state the executor itself reads via
`LOCAL_SOURCE_LOADER_DATABASE_URL` (§3.1) — nothing in this service writes `geo.*` any more.

Related docs, so this file doesn't duplicate them:
- [`docs/rebuilding-the-dataset.md`](../../docs/rebuilding-the-dataset.md) — a from-a-clone
  recipe, now updated for the direct-to-Parquet writers (§7/§8A here). Still predates the
  2026-08-22 architecture pivot's own written-up guide, which does not exist yet.
- [`docs/runbooks/durable-backfill-lanes.md`](../../docs/runbooks/durable-backfill-lanes.md) —
  **historical.** It documents the per-window `jobs-*` ledger deleted 2026-09-12; read it for why a
  durable ledger exists at all, not for working commands. `execution/AGENTS.md` is the current
  design reference for `ops jobs-executor`; §7 here is the short operator version.
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
- Every command in this document is `uv run agri-service <group> <verb>` — the console script
  `agri-service = "agri_data_service.interface.cli:cli"` (`pyproject.toml:49`) only resolves inside that venv.

**PostgreSQL — read this before you run `docker compose up`**

> [!WARNING]
> **Do not use `services/agri-data-service/docker-compose.yml`.** It looks like the obvious
> thing to run from this directory, and it is the *only* `docker-compose.yml` here — but it
> stands up a database this service's own config validator refuses for ingestion. Verified,
> line by line:
>
> | | `docker-compose.yml`'s `db` service | What `config.py` requires for ingestion |
> |---|---|---|
> | Port | `5433` | `5442` — the local warehouse |
> | Database | `agri_data` | a migrated `plantgeo`/`plantgeo_*` database |
> | Role | `plantgeo` | any login — nothing is asserted since 2026-08-08 (§3.1) |
> | Image | `postgis/postgis:16-3.4` | needs TimescaleDB too |
> | Init script | `infra/db/init-extensions.sql` — installs `postgis`, `vector`, `uuid-ossp`, **no `timescaledb`** | `postgis` + `timescaledb` + `vector` + `pgcrypto`, all four, or the Alembic foundation migration refuses to create the `agri` schema (`infra/local-warehouse/AGENTS.md`) |
>
> Since 2026-08-08 config no longer rejects any of the mismatches above (§3.1), so pointing a
> DSN at this service now fails *later* — on a missing extension, a missing schema, or a
> migration that will not complete — instead of on a named validator error. Treat
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
  uv run agri-service ops db-upgrade
```

```powershell
Set-Location services/agri-data-service
$env:DATABASE_URL_SYNC = 'postgresql://plantgeo_owner:<password>@127.0.0.1:5442/plantgeo'
uv run agri-service ops db-upgrade
```

Expected: a stream of `INFO  [alembic.runtime.migration] Running upgrade ... -> ...` lines ending
without an error and returning to the prompt. `uv run agri-service ops db-status` afterwards prints the
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
| Any direct writer module (`python -m agri_data_service.pipeline.direct.<lane>`) / `ops jobs-executor` / `ops lane-audit` / `ops jobs-usage-report` | `LOCAL_SOURCE_LOADER_DATABASE_URL` | **Never** (`config.py:161-165`) |
| `alembic upgrade` / `db-upgrade` / `db-status` (migrations) | `DATABASE_URL_SYNC` | **Never** (`alembic/env.py:14`) |
| The Sanic HTTP API in `combined_local` profile | `DATABASE_URL` | n/a — this is its own variable |

### 3.1 The loader DSN: `LOCAL_SOURCE_LOADER_DATABASE_URL` (optional since 2026-08-08)

`ingest_session()` (`db/engine.py`) is the session the job ledger side (`ops jobs-executor`,
`ops lane-audit`, `ops jobs-usage-report`, `ops jobs-supersede-run`, `ops jobs-plan-gap-repair`,
`ops jobs-set-lane-enabled`) opens; every direct writer module
(`pipeline/direct/<lane>/forward.py`) calls the same resolver directly. Both paths call
`settings.require_local_source_loader_database_url()` and nothing else. That resolver is now
two lines: return `LOCAL_SOURCE_LOADER_DATABASE_URL` if set, otherwise `DATABASE_URL`, and
raise only when neither is set.

Everything that used to be enforced here is gone — the `postgresql+asyncpg`-only scheme check,
the `plantgeo`/`plantgeo_*` database-name rule, the `127.0.0.1:5442` / Railway-proxy host
allowlist, the required login, and the "must not equal `DATABASE_URL`" distinctness rule. The
owner ruling recorded in `20260808_0019` is that every application path connects with the
single owner credential, and the follow-up change removed the validators built around role
separation. **Nothing in config will catch a wrong target database now**; that is the
operator's responsibility.

Practically: `DATABASE_URL` in `.env` is enough. Set the loader variable only to point one
command somewhere else.

```bash
# Nothing to export — .env's DATABASE_URL is used.
uv run python -m agri_data_service.pipeline.direct.sensors

# Or aim one command at a different database:
export LOCAL_SOURCE_LOADER_DATABASE_URL='postgresql+asyncpg://plantgeo_owner:<password>@127.0.0.1:5442/plantgeo_scratch'
```

### 3.2 The grant gap — closed by deleting the path, not by granting

> [!NOTE]
> **Resolved 2026-08-08 — the local loader role is retired, not fixed.** The warning that used to
> live here was accurate: the documented `plantgeo_loader` role could not execute a single
> `ingest-*` or `jobs-*` verb locally. It had `CONNECT`, `USAGE` on schema `agri`, and
> `SELECT, INSERT` on eleven `agri.*` lineage tables — **no grant on schema `geo`** and **none on
> any `agri.job_*` table** — while every `ingest-*` verb writes
> `geo.features`/`geo.geometry`/`geo.drought_areas` (`src/agri_data_service/ingest/writer.py:88,111,122`)
> and every `jobs-*` verb reads and writes
> `agri.job_definition`/`job_run`/`job_work_item`/`job_attempt`/`job_checkpoint`
> (`src/agri_data_service/ingest/commands.py:545-548`). The symptom looked like an application
> bug: `permission denied for schema geo`, then `permission denied for table job_definition`.
>
> The owner's answer was to remove the path rather than write the grants.
> `infra/local-warehouse/create-loader-role.sql` is deleted, revision `20260808_0019` retired the
> forecast capability-role family, and the loader DSN asserts nothing at all any more (§3.1).
> **Local work targets production through the proxy DSN**, which is what was actually happening
> anyway: `run-backfill.sh` sources `services/agri-data-service/.env` and pins
> `LOCAL_SOURCE_LOADER_DATABASE_URL` to whatever `DATABASE_URL` is defined there. That is why the
> durable lanes of that era showed real completed windows in production — see
> [`docs/runbooks/durable-backfill-lanes.md`](../../docs/runbooks/durable-backfill-lanes.md), now
> historical — while the local role was unusable: nobody was running ingestion against
> `infra/local-warehouse` day to day.
>
> **What this means for you, practically:**
> - `db-status` and `db-upgrade` are unaffected — they read `DATABASE_URL_SYNC`, a different
>   variable, never `LOCAL_SOURCE_LOADER_DATABASE_URL` (§3.3). `pipeline-status`, the DSN-string-only
>   health check this bullet used to cite, no longer exists (deleted 2026-09-12); see §6 for the
>   current equivalent.
> - A local isolated ingestion write is still not a reviewed path, but it is no longer blocked by
>   a role: point `DATABASE_URL` (or the loader override) at your own 5442 warehouse with the
>   owner credential and it is accepted.
> - **`plantgeo_loader` still existed in production at the time of this ruling**; the `jobs-plan-lane`/
>   `jobs-reconcile-lane --apply` workflow this bullet used to warn about is itself deleted along
>   with the rest of the per-window ledger (§7, 2026-09-12). Any direct writer module you run locally
>   still writes to whatever `DATABASE_URL`/`LOCAL_SOURCE_LOADER_DATABASE_URL` names — check it before
>   running one, the same caution this bullet always meant.

### 3.3 The migration DSN: `DATABASE_URL_SYNC`, and why overriding `DATABASE_URL` does nothing

`alembic/env.py:14` sets Alembic's target straight from `settings.database_url_sync` — never from
`DATABASE_URL`. `database_url_sync` has a **non-empty default**,
`postgresql://geo:plantgeo@localhost:5432/plantgeo` (`config.py:130`), so an unset variable is
not a loud failure — it quietly targets that default instead. And Pydantic Settings loads `.env`
**relative to your current working directory** (`config.py:47`,
`SettingsConfigDict(env_file=".env", ...)`), so which `.env` gets read depends on where you ran
`agri-service` from.

> [!WARNING]
> **`services/agri-data-service/.env` exists in this working tree and defines
> `DATABASE_URL_SYNC`.** If you `export`/`$env:` a scratch `DATABASE_URL` to point somewhere safe
> and then run `make migrate` (or `uv run agri-service ops db-upgrade`) from
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
| `LOCAL_SOURCE_LOADER_DATABASE_URL` | Optional override for any direct writer module / `ops jobs-executor` and its sibling ledger verbs; falls back to `DATABASE_URL` (§3.1) | `DATABASE_URL` | Nothing, unless `DATABASE_URL` is also unset |
| `DATABASE_URL` | Required unless every command carries its own override; also the `combined_local` HTTP profile's DSN | none | Every direct writer refuses to start (§3.1) |
| `DATABASE_URL_SYNC` | Required for migrations | `postgresql://geo:plantgeo@localhost:5432/plantgeo` (not the local warehouse!) | Silently migrates the wrong database (§3.2) |
| `INGEST_BBOX` | **Effectively required** | none | Unset ⇒ every job reports `skipped`, not `failed` — looks clean, writes nothing (§9) |
| `NASA_FIRMS_KEY` | Required for the direct fire-detections writer (`python -m agri_data_service.pipeline.direct.fire_detections`, lane `fire-detections-direct-forward`) | none | The writer fails; the lane cannot land a fire-detections row without it (§5, §9) |
| `CDSAPI_URL`, `CDSAPI_KEY` | **CORRECTED 2026-10-26: no longer read by anything.** `historical-era5-backfill` and `historical_era5.py` are deleted; `cdsapi` was dropped from the dependency lock with them. ERA5-Land is now served keylessly by the `soil-era5-land-direct-forward` lane. | none | Nothing — these variables are inert |
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
| `RAILWAY_REPLICA_ID` | Optional | none; falls back to `<hostname>:<pid>` | Part of the worker identity `job-executor:<identity>:<lane_id>` the executor uses per tick (`_worker_id`, `execution/job_executor_service.py`) |
| `AGRI_ALEMBIC_CONFIG` | Optional | `<service>/alembic.ini` | Path override for Alembic's config file |
| `AGRI_TEST_DATABASE_URL` | Test-only | none | Real-DB tests skip silently if unset. A libpq DSN, not a SQLAlchemy URL, and never a database named `plantgeo` — see `db/AGENTS.md` §Provisioning the disposable database for the full recipe |

> [!WARNING]
> **`FIRES_LAYER_ID` vs `FIRE_PERIMETERS_LAYER_ID` — setting the wrong one is a silent no-op.**
> The repo-root `.env.example` documents `FIRES_LAYER_ID` (`.env.example:202`, read by the
> Next.js push route at `src/app/api/ingest/fires/route.ts`). This Python service's
> `fire-perimeters-direct-forward` lane (`python -m agri_data_service.pipeline.direct.fire_perimeters`)
> reads a **different** variable, `FIRE_PERIMETERS_LAYER_ID` (`src/agri_data_service/ingest/wfigs.py:48,144`),
> which appears in **neither** `.env.example` file. If you rename the fire-perimeters layer and only
> set `FIRES_LAYER_ID` (the documented one), the TypeScript push route follows the rename but this
> service's direct writer keeps writing to the old `fire-perimeters` layer — silently splitting one
> logical layer across two IDs. Set both, kept equal, if you ever touch this.

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
| `NASA_FIRMS_KEY` | `python -m agri_data_service.pipeline.direct.fire_detections` (lane `fire-detections-direct-forward`) | Free MAP_KEY from `firms.modaps.eosdis.nasa.gov/api/area/`. **Lives only on the production job-executor service, not in any local `.env`.** |
| `CDSAPI_URL` + `CDSAPI_KEY` | **CORRECTED 2026-10-26: nothing.** `historical-era5-backfill` is deleted; no verb reads these any more (§4). | N/A |

> [!WARNING]
> **The old `jobs-run --lane firms-archive` ledger and its dead-letter/requeue workflow are gone**
> (deleted in the 2026-09-12 Parquet cutover, §7). Today a missing `NASA_FIRMS_KEY` raises
> `NASA_FIRMS_KEY environment variable is not set` straight out of the `fire-detections-direct-forward`
> lane's command, every tick, until the key is set. A repeated failure streak opens an
> `agri.job_incident` row and, past the breaker threshold, the executor withholds the lane until an
> operator clears it with `ops jobs-supersede-run` or pauses it with `ops jobs-set-lane-enabled
> --disabled` (`execution/AGENTS.md` "Soft failure"). Set the key before the lane is ever activated.

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
> `CDSAPI_KEY` is resolved through `Settings` as of 2026-08-08, so leaving it in `.env` is
> enough; a real `CDSAPI_KEY` export still takes precedence. Neither value is logged,
> checkpointed, or included in the refusal message.

---

## 6. First run

**CORRECTED 2026-10-26: `pipeline-status`, `ingest-sensors`, and `validate-streams` below are
all deleted (2026-09-12 Parquet cutover).** This section now proves the two pieces that survived
the cutover — the ledger DSN and the lane inventory — without needing a credential. A real
Parquet write additionally needs object-store credentials (`OBJECT_STORE_ENDPOINT_URL`,
`OBJECT_STORE_BUCKET`, `OBJECT_STORE_ACCESS_KEY_ID`, `OBJECT_STORE_SECRET_ACCESS_KEY`,
`OBJECT_STORE_REGION`; `config.py::require_object_store`) that the local-warehouse bootstrap in
§2 does not provision — that part is intentionally outside this smoke test.

```bash
cd services/agri-data-service
export INGEST_BBOX='-125,42,-111,49'    # Pacific Northwest coverage box, ingest/policy.py:17
export DATABASE_URL='postgresql+asyncpg://plantgeo_owner:<owner-password>@127.0.0.1:5442/plantgeo'
export DATABASE_URL_SYNC='postgresql://plantgeo_owner:<owner-password>@127.0.0.1:5442/plantgeo'

uv run agri-service ops db-status              # proves DATABASE_URL_SYNC points where you think
uv run agri-service ops jobs-executor --inventory   # proves the lane table loads and the allow-list parses
```

```powershell
Set-Location services/agri-data-service
$env:INGEST_BBOX = '-125,42,-111,49'
$env:DATABASE_URL = 'postgresql+asyncpg://plantgeo_owner:<owner-password>@127.0.0.1:5442/plantgeo'
$env:DATABASE_URL_SYNC = 'postgresql://plantgeo_owner:<owner-password>@127.0.0.1:5442/plantgeo'

uv run agri-service ops db-status
uv run agri-service ops jobs-executor --inventory
```

Expected output, in order:
1. `db-status` — Alembic's normal revision report, e.g. a line like
   `Current revision(s) for postgresql://...: <hash> (head)`. No hash means you haven't migrated
   yet; go back to §2.
2. `jobs-executor --inventory` — one JSON object naming every registered lane and whether it is
   active or quarantined under `PLANTGEO_JOB_EXECUTOR_ACTIVE_LANES`; it prints and exits before
   opening any DSN or object-store connection, so it is the fastest "does the code even load" check.

To prove an actual write locally, once object-store credentials are set, run one direct writer
module directly — the NWS sensors lane needs no API key:

```bash
uv run python -m agri_data_service.pipeline.direct.sensors
```

A `permission denied for schema geo`-style error cannot happen here any more — these writers don't
touch `geo.*`; a credential problem on this path surfaces as an `S3`/boto error against
`OBJECT_STORE_*`, or a `require_local_source_loader_database_url` `ValueError` if neither DSN
variable is set.

---

## 7. The job executor

**CORRECTED 2026-10-26: the per-window ledger this section used to describe (`ingest/lanes.py`,
`jobs-plan-lane`, `jobs-run`, `jobs-status`, `jobs-reconcile-lane`, `firms-archive`/
`streamflow-archive`) is gone.** It was deleted in the 2026-09-12 "Complete greenfield Parquet
cutover" (`fd602901`), the same push that deleted the `ingest-*` verbs in §8A/§8B.
[`docs/runbooks/durable-backfill-lanes.md`](../../docs/runbooks/durable-backfill-lanes.md) is now a
historical record of that deleted system — read it for the "why a durable ledger exists at all"
story, not for working commands.

What replaced it: **one continuous scheduler process, `agri-service ops jobs-executor`, dispatches
every registered lane on its own cadence.** A lane (`execution/lane_specs.py`, ids in
`execution/lane_ids.py`) declares a shell command — almost always
`python -m agri_data_service.pipeline.direct.<name>` — plus a cadence, phase offset, timeout, and
catch-up policy. Deployment activation is one env var, `PLANTGEO_JOB_EXECUTOR_ACTIVE_LANES` (an
allow-list; an unlisted lane stays in shadow mode and never dispatches), plus the kill-switch
`PLANTGEO_JOB_EXECUTOR_STOPPED_LANES`. Full design: `execution/AGENTS.md`.

```bash
uv run agri-service ops jobs-executor --inventory    # print the lane table and exit; no DSN opened
uv run agri-service ops jobs-executor --once          # run one leader-elected tick and exit
uv run agri-service ops jobs-executor                 # run continuously (what Railway's cron-free `plantgeo-job-executor` service does)
```

To run one lane's writer directly, bypassing the scheduler entirely — the sensors lane needs no
credential:

```bash
uv run python -m agri_data_service.pipeline.direct.sensors
```

Operator verbs over the executor's own state (all under `ops`, all reading
`LOCAL_SOURCE_LOADER_DATABASE_URL`, §3.1):

| Verb | What it does |
|---|---|
| `lane-audit` | Read-only per-lane audit: declared limits, coverage, last turn, usage, open incidents. Your primary "is this healthy" check. |
| `jobs-usage-report` | Month-to-date spend, per-lane × host rates, open incidents. |
| `jobs-set-lane-enabled --definition <name> --enabled/--disabled --operator <who> --reason <why> [--apply]` | Pause or resume one executor definition; preview by default, `--apply` to write. |
| `jobs-supersede-run --lane <id> --run-id <id> ...` | Clears a lane's failed-checkpoint hold so the executor resumes dispatching it. |
| `jobs-plan-gap-repair [--lane <id> ...] [--max-days N]` | Hands each named lane's own gap-repair binding a bounded re-walk of missing days. |

**There is no per-window dead-letter ledger any more.** A lane that keeps failing opens an
`agri.job_incident` row every tick and, past a breaker threshold, the executor withholds it —
visible in `lane-audit` — until an operator runs `jobs-supersede-run` or `jobs-set-lane-enabled
--disabled` (`execution/AGENTS.md` "Soft failure"). There is no SQL `UPDATE` requeue step; the
breaker and the lane state are the whole mechanism.

> [!NOTE]
> **GloFAS, CAMS, and the plan-based `historical-*`/ensemble-forecast family no longer exist at
> all** — see §8D for what replaced ERA5-Land, NASA POWER, Open-Meteo and USDM.

---

## 8. Full CLI verb inventory

**CORRECTED 2026-10-26: every `data ingest-*`, `ops jobs-plan-lane`/`jobs-run`/`jobs-status`/
`jobs-reconcile-lane`, `ops validate-streams`, `ops pipeline-status`, `data historical-*`,
`data source-ingest*`, `ops local *`, and `forecast *` verb this section used to list was deleted
in the 2026-09-12 "Complete greenfield Parquet cutover" (`fd602901`) or the later
2026-10-26 cleanup. None of them are registered — confirmed against every `@click.command` and
`register_commands(...)` call under `interface/cli/` and `execution/`.** The current CLI surface is
exactly three groups, `uv run agri-service <group> <verb>` from `services/agri-data-service`:

- **`data`** — `data parquet day|window|release|coverage` (read governed Parquet; see
  `interface/cli/parquet.py`) and `data availability-bootstrap|availability-publish|
  availability-reconcile-physical|availability-digest-trusted|availability-verify-days|
  availability-release-verified-days` (operate the availability index; `interface/cli/availability.py`).
- **`ops`** — `ops seed`, `ops db-status`, `ops db-upgrade [REVISION=head]`,
  `ops job-logs-maintain [--retention-days 30] [--future-days 7]`, `ops jobs-executor [--once]
  [--inventory]` (§7), `ops jobs-supersede-run`, `ops jobs-plan-gap-repair`,
  `ops jobs-set-lane-enabled`, `ops jobs-usage-report`, `ops lane-audit`, `ops export-expert-labels`
  (`interface/cli/ops.py`).
- **`agent`** — `agent list-tools`, `agent probe`, `agent call-tool`, `agent ask`, `agent mcp-serve`
  (the location-analysis MCP tool surface; `interface/cli/agent.py`).

### A. The direct writers (what actually lands rows today)

There is no `data ingest-*` verb family. Every layer below is a lane in `execution/lane_specs.py`,
run either by the continuous `ops jobs-executor` in production or by hand locally as
`python -m agri_data_service.pipeline.direct.<module>` (§7):

| Layer | Live lane id | Module | Credential |
|---|---|---|---|
| Fire detections | `fire-detections-direct-forward` | `pipeline.direct.fire_detections` | `NASA_FIRMS_KEY` |
| Water gauges | `water-gauges-direct-forward` | `pipeline.parquet.water_gauges_forward` | none |
| MTBS historical census | `mtbs-forward` | `pipeline.direct.burn_severity` (no flags) | none |
| MTBS current capture | `burn-severity-direct-forward` | `pipeline.direct.burn_severity --current-snapshots --time-budget-seconds 1800` | none |
| Climate (NASA POWER) | `climate-nasa-power-direct-forward` | `pipeline.direct.climate` | none |
| Soil (ERA5-Land) | `soil-era5-land-direct-forward` | `pipeline.direct.soil` | none |
| Vegetation (Sentinel-2 NDVI) | `vegetation-sentinel2-ndvi-direct-forward` | `pipeline.direct.vegetation` | none |
| Weather observations | `weather-observations-direct-forward` | `pipeline.direct.weather_observations` | none |
| Drought (USDM) | `drought-direct-forward` | `pipeline.direct.drought` | none |
| Fire perimeters | `fire-perimeters-direct-forward` | `pipeline.direct.fire_perimeters` | none |
| Sensors (NWS) | `sensors-direct-forward` | `pipeline.direct.sensors` | none |
| Watersheds | `watersheds-direct-forward` | `pipeline.direct.watersheds` | none |
| Evacuation zones | `evacuation-zones-direct-forward` | `pipeline.direct.evacuation_zones` | none |
| Land context (BLM) | `land-context-blm-forward`/`-reconcile`/`-backfill` | `pipeline.direct.land_context --mode refresh\|reconcile\|backfill` | none |
| Crop cover (USDA) | `crop-cover-usda-maintain` | `pipeline.direct.crop_cover` | none |

There is no composite "run every lane" verb — each module is its own failure domain, dispatched
independently by the executor.

### B. Backfill / repair modules

There is no generic `data ingest-backfill --source TOKEN` verb and no `data ingest-geometry-repair`
(nothing here writes `geo.geometry` any more). Where a lane needs a bounded historical walk or
repair pass, it ships its own module, run the same way:
`python -m agri_data_service.pipeline.direct.<lane>.backfill` — confirmed present for
`drought`, `burn_severity`, and `vegetation` (`pipeline/direct/<lane>/backfill.py`).

### C. The job executor — see §7 for the full workflow

`ops jobs-executor [--once] [--inventory]`, plus the operator verbs over its state:
`ops lane-audit`, `ops jobs-usage-report`, `ops jobs-set-lane-enabled`, `ops jobs-supersede-run`,
`ops jobs-plan-gap-repair`. There is no `--lane`/`--definition` ambiguity to worry about any more —
`jobs-set-lane-enabled` takes `--definition`, the others take `--lane`, and both are validated
against the code-owned registry (`execution/lane_catalogue.py`), not joined loosely.

### D. Historical backfill families — all gone

**NASA POWER, ERA5-Land (CDS), Open-Meteo archive, USDM, GloFAS, CAMS, and the ensemble forecast
lane's plan-based `data historical-*` verbs no longer exist.** Confirmed: no `historical` token
appears in any `@click.command` name under `interface/cli/` or `execution/`. ERA5-Land is now
served keylessly by the `soil-era5-land-direct-forward` lane (§8A); NASA POWER by
`climate-nasa-power-direct-forward`; there is no current replacement producer for the Open-Meteo
archive, USDM historical backfill, GloFAS, CAMS, or the ensemble forecast lane — if you need one of
those, it does not exist on `HEAD` today. `data historical-promotion-spool`/`-upload` are gone with
the rest of this family.

### E. Migrations, seeding, and everything else

| Verb | What it does |
|---|---|
| `ops db-status` / `ops db-upgrade [REVISION=head]` | Alembic status / migrate, reading `DATABASE_URL_SYNC` (§3.3) |
| `ops seed` | Seeds strategy rows |
| `ops job-logs-maintain [--retention-days 30] [--future-days 7]` | Prunes old job log rows and maintains their UTC partitions |
| `ops export-expert-labels` | One-time export of the reviewed label plane to the ML service's bucket prefix (operational verb, no schedule) |
| `agent list-tools` / `agent probe` / `agent call-tool` / `agent ask` / `agent mcp-serve` | The location-analysis MCP tool surface — see `interface/cli/agent.py` |

`ops pipeline-status`, `data source-ingest`/`source-ingest-status`, `ops local init|status|...`,
`forecast refresh-ml-daily|run-iteration|reconcile-actuals`, and `forecast vegetation-register|
vegetation-simulate|vegetation-evaluate` are all gone — none of these groups/verbs are registered
on `HEAD`. The `ml strategy-label-map-preflight`/`ml strategy-train` move to
`services/plantgeo-ml-service` (noted here historically as "removed 2026-09-18") is the one claim in
this old table that is still accurate.

---

## 9. Known failure modes

| Symptom | What it actually means | Fix |
|---|---|---|
| Every job reports `skipped`, exit 0, zero rows, nothing looks wrong | `INGEST_BBOX` is unset. `resolve_bounded_bbox()` returns `None` and the job is a **typed skip, not a failure**, deliberately, so an unconfigured deployment doesn't go red (`src/agri_data_service/ingest/policy.py:39,86`) | Set `INGEST_BBOX` or pass `--bbox` |
| `set LOCAL_SOURCE_LOADER_DATABASE_URL or DATABASE_URL` on any verb | Neither variable is set; the loader DSN falls back to `DATABASE_URL` and there is nothing to fall back to | Set `DATABASE_URL` in `.env` (§3.1) |
| `alembic upgrade` migrated a database you didn't expect | It read `DATABASE_URL_SYNC` from the CWD's `.env`, not the `DATABASE_URL` you set (§3.3) | Set `DATABASE_URL_SYNC` explicitly and echo it back before upgrading |
| `CDSAPI_URL`/`CDSAPI_KEY`-related error messages anywhere | **HISTORICAL — these variables are no longer read at all (2026-10-26).** `historical-era5-backfill` and `cdsapi` are deleted; ERA5-Land is served by `soil-era5-land-direct-forward` with no credential | N/A — neither variable does anything today |
| `400 Invalid day range. Expects [1..5]` from FIRMS | `FIRMS_DAY_RANGE` was set above 5 | Leave it unset (default `2`) |
| `NASA_FIRMS_KEY environment variable is not set`, every tick | The `fire-detections-direct-forward` lane has no key; it opens an `agri.job_incident` row every failing tick (§7) | Set the key before activating the lane |
| A lane's `lane-audit` row shows a withheld/breaker-tripped state | Past-threshold repeated failures; the executor withholds the lane until an operator clears it (§7, `execution/AGENTS.md` "Soft failure") | `ops jobs-supersede-run` or `ops jobs-set-lane-enabled --disabled` to investigate, then re-enable |
| A chunk reports `records_seen=21000, records_written=0` and still "succeeds" | `INGEST_MAX_SOURCE_RECORDS` got bitten; the writer keeps the *newest* records and drops whole *oldest* days | Never set `INGEST_MAX_SOURCE_RECORDS` on an archive lane |
| Redis connection refused, but the run continues | Realtime publish is best-effort; the publisher marks itself unavailable on the first `OSError` and just counts drops | Safe to ignore locally |
| A weekly or 5-day-cadence stream (e.g. `drought_areas`, `vegetation`) reported a gap whose `days` was far larger than its `missing_day_count` | HISTORICAL (2026-08-08 to 2026-10-26): the gap walk ran on the stream's declared `publication_cadence_days` in `ingest/validation/completeness.py`, which has since been deleted as zero-caller dead code along with the rest of the validity-report stack (`ingest/AGENTS.md`'s deletion table). No gap walk reads a stream's cadence today. | N/A — kept as history only |

`validate-streams`, `pipeline-status`, and the old `jobs-run`/`jobs-plan-lane` dead-letter rows that
used to appear here described verbs deleted in the 2026-09-12 cutover; see §7 and §8 for what
replaced them.

---

## 10. Where things live

Under `src/agri_data_service/`: **`ingest/`** no longer holds a `geo.*` writer or a CLI-registered
producer — it is now a library of per-source fetch/parse helpers (`firms.py`, `sensors.py`,
`wfigs.py`, `mtbs.py`, `usdm.py`, `policy.py` for bbox resolution, …) that the `pipeline/direct/*`
writers import (`ingest/AGENTS.md` is the design rationale for *why* each source parser works the
way it does, including the pre-cutover history); **`pipeline/direct/`** holds the current
Parquet-writing lane modules (§8A), one package per layer, each with a `forward.py` and (where one
exists) a `backfill.py`; **`execution/`** is the job executor runtime — `job_executor_service.py`,
`lane_specs.py`, `lane_ids.py`, the operator verbs in §8C-E — plus the surviving forecast/ML
evaluation modules; it does not write `agri.*` lineage for a plan-based `historical-*` family any
more, since that family is deleted. **`jobs/`** (`dispatch.py`, `lease.py`, `registry.py`,
`scheduler.py`, `worker.py`) is runtime plumbing the current executor still uses, not the deleted
per-window ledger (`jobs/AGENTS.md` covers the design intent); **`db/`** is the SQLAlchemy
engine/session plumbing and DSN validators (`config.py` lives one level up, at
`src/agri_data_service/`); **`models/`** are the SQLAlchemy ORM models Alembic's autogenerate
reads; **`routes/`** is the small Sanic HTTP surface — `agent_analysis.py`, `agent_tools.py`,
`ops.py`, `strategies.py` today, not the "local publication receiver, historical promotion
receiver" this paragraph used to describe (those routes are gone with the plan-based ingestion
system); **`seed/`** is one-time strategy seed data; **`schemas/`** holds request/response schemas
for the HTTP routes.

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

# Dedicated local PlantGeo warehouse

This is separate from the native PostgreSQL service at `127.0.0.1:5432` and
from every other application database. It uses the plain `postgis/postgis:16-3.4`
image family (TimescaleDB was dropped repo-wide 2026-08-25: production's only
hypertable, `tracking.positions`, was always empty with zero chunks and no
continuous aggregate ever existed, so the extension earned nothing and this
warehouse now matches production's extension set), a dedicated named volume,
and loopback-only port `5442`. Its semantic lineage bundles record the source
PostgreSQL major and require an equal-or-newer target during promotion
preflight, including the planned PostgreSQL 18 Railway target.

## Start it

```powershell
Copy-Item infra/local-warehouse/.env.example infra/local-warehouse/.env
# Edit infra/local-warehouse/.env locally with a unique password.
podman --connection podman-machine-default-root compose --project-name plantgeo-warehouse --env-file infra/local-warehouse/.env -f infra/local-warehouse/compose.yaml build
podman --connection podman-machine-default-root compose --project-name plantgeo-warehouse --env-file infra/local-warehouse/.env -f infra/local-warehouse/compose.yaml up -d
podman --connection podman-machine-default-root compose --project-name plantgeo-warehouse --env-file infra/local-warehouse/.env -f infra/local-warehouse/compose.yaml ps
```

For ordinary development startup after the warehouse already exists, use the
safe launcher. It starts the retained container and waits for PostgreSQL; it
refuses to create a new blank warehouse unless that is explicitly requested:

```powershell
powershell.exe -NoProfile -File infra/local-warehouse/start-dw-dev.ps1
powershell.exe -NoProfile -File infra/local-warehouse/start-dw-dev.ps1 -OpenPsql
```

For an intentional first-time setup only, create the ignored `.env` as above
and add `-CreateIfMissing`. The launcher prints a session-read-only owner DSN and points to
the reviewed warehouse inspection queries.

The derived image retains the upstream PostGIS package but removes its
automatic `template_postgis`/extension-creation init script. It does
not install or enable any PostgreSQL extension.

## Persistence, checkpoints, and recovery

`plantgeo_warehouse_pgdata` is a named Podman volume, so ordinary
`podman --connection podman-machine-default-root compose --project-name plantgeo-warehouse ... down` preserves the database. Do not use `down -v` and do
not remove that named volume unless a verified external backup exists.

Use PowerShell to write an independent, compressed restore point after
migration, after each completed historical source, after Parquet
materialization, before Railway promotion, and after each successful weekly
ingestion. The script retains eight completed archives by default; choose an
output directory outside this repository and include it in the normal machine
backup policy.

Resolve the container once per shell — the id differs on every machine and every
recreate, so it must never be pasted from a runbook:

```powershell
$env:PLANTGEO_WAREHOUSE_CONTAINER = (podman ps --filter name=plantgeo-warehouse --format '{{.ID}}')
```

```powershell
pwsh -File infra/local-warehouse/backup.ps1 `
  -Container $env:PLANTGEO_WAREHOUSE_CONTAINER `
  -OutputDirectory $env:PLANTGEO_BACKUP_ROOT
```

Every `.dump` has an adjacent checksum manifest. A restore verifies that
manifest before replacing the `plantgeo` target; it is deliberately explicit
and never invoked by the ingestion commands:

```powershell
pwsh -File infra/local-warehouse/restore.ps1 `
  -Container $env:PLANTGEO_WAREHOUSE_CONTAINER `
  -ArchivePath $env:PLANTGEO_BACKUP_ROOT\plantgeo-<timestamp>.dump `
  -IUnderstandThisReplacesData
```

After a restore, run the normal migration/readiness checks and a pinned release
read before treating the warehouse as recovered.

## Explicit extension gate

After the container reports healthy, verify capability first (replace the
placeholder with the local password without placing it in source control):

```powershell
$env:PGPASSWORD = '<local password>'
& 'C:\Program Files\PostgreSQL\16\bin\psql.exe' -h 127.0.0.1 -p 5442 -U plantgeo_owner -d plantgeo -Atc "SELECT name, default_version, installed_version FROM pg_available_extensions WHERE name IN ('postgis','vector','pgcrypto') ORDER BY name;"
```

Then the operator, not an automated startup script, runs the reviewed manual
gate (PostGIS, pgvector, pgcrypto — TimescaleDB is gone repo-wide, see above).
It is safe to re-run and stops at the first error:

```powershell
& 'C:\Program Files\PostgreSQL\16\bin\psql.exe' -h 127.0.0.1 -p 5442 -U plantgeo_owner -d plantgeo -f infra/local-warehouse/enable-extensions.sql
```

The Alembic foundation verifies all three are already installed and fails before
it creates any `agri` object. Only after that approved gate should reviewed
Drizzle/Alembic migrations create schemas and `agri-service data source-ingest` loads local source releases. The future
promotion archive comes from this database; it never comes from `pgt`,
`postgres`, or another application database.

## Role management is retired (2026-08-08)

This directory no longer provisions any role. `create-loader-role.sql`,
`create-forecast-roles.sql`, `create-forecast-refresh-operator.sql`,
`create-local-access-roles.sql`, and `grant-resolution-aware-loader.sql` are all
deleted, and revision `20260808_0019` dropped the forecast capability-role
family. Every application path connects with the single owner credential. See
`docs/reports/migration-decision-packet-2026-08-08.md` § Resolution.

`plantgeo_loader` and the `plantgeo_local_developer`/`plantgeo_local_viewer`
convenience logins still exist wherever they were created — the deployed Railway
cron ingest and the in-flight archive walks authenticate as `plantgeo_loader`
right now — and DSNs naming them keep working. No repository code manages,
provisions, or requires them.

`LOCAL_SOURCE_LOADER_DATABASE_URL` is now an optional override rather than a
custody gate: when it is unset, `agri-service data` ingest and loader commands use
`DATABASE_URL`, and setting both to the same DSN is accepted. No host, port,
database name, or login is asserted. The same holds for
`FORECAST_MV_REFRESH_DATABASE_URL` and `FORECAST_ITERATION_DATABASE_URL`.

## Connection forms

```text
postgresql://plantgeo_owner:<password>@127.0.0.1:5442/plantgeo
postgresql+asyncpg://plantgeo_owner:<password>@127.0.0.1:5442/plantgeo
```

## PgAdmin

Register a server with host `127.0.0.1`, port `5442`, maintenance database
`plantgeo`, and the owner login. Keep SSL mode at `Prefer` or `Disable` for this
loopback-only container. Real local credentials belong in the ignored
`infra/local-warehouse/.env`, never in tracked docs.

The Boise intervention release and the latest evaluation-only v2 forecast
currently live only in the guarded disposable proof database
`plantgeo_geospatial_test_20260723_1421`; the persistent `plantgeo` database
remains deliberately unmigrated. Inspect the proof without writes by opening
`read-pilot-evidence.sql` in PgAdmin's Query Tool against that database or by
running:

```powershell
& 'C:\Program Files\PostgreSQL\16\bin\psql.exe' `
  -h 127.0.0.1 -p 5442 -U plantgeo_owner `
  -d plantgeo_geospatial_test_20260723_1421 `
  -X -f infra/local-warehouse/read-pilot-evidence.sql
```

After a separately authorized migration/promotion, change only `-d` to the
approved destination database.

The explicit ML materialized-view refresh runs on
`FORECAST_MV_REFRESH_DATABASE_URL`, or on `DATABASE_URL` when that is unset.
Since `20260808_0019` retired `plantgeo_forecast_mv_refresher`, `agri-service forecast
refresh-ml-daily` no longer probes a role or issues `SET LOCAL ROLE`:
the matview and its `SECURITY DEFINER` refresher belong to the owner credential,
and a non-concurrent `REFRESH` needs exactly that ownership. The repository
defines no refresh schedule.

## Forecasting framework gate

Alembic revision `20260722_0005` defines the additive PostgreSQL forecasting,
backtest, receipt, local-ML lineage, and serving contracts. Revision
`20260722_0006` adds a separate historical hindcast plane: simulated cutoffs
are never written as operational issue times, finalization recomputes every SQL
point and actual lineage, and forecast-versus-actual signals remain unavailable
before their server-recorded finalization time. Revision `20260722_0007`
requires the full calibration horizon to end by the simulated cutoff. Applying
these migrations seeds
no forecast/model/strategy rows and materializes no serving data. Run the
contract tests and disposable PostgreSQL execution test before an operator
applies them here; then take a verified backup before migration.

Validated source coverage, passing backtests, immutable job-output receipts, and
a publication pointer are required before anything from this framework is
published. The same migration is intended to roll forward to the future private
Railway database, but no Railway schedule or model worker is authorized by it.
Hindcast outcomes are local evaluation/ML feature evidence and do not enter
`v_forecast_series_serving` or bypass publication quality gates.

Revision `20260723_0010` adds the generic evaluation loop requested for the
next forecast phase:

- `v_forecast_timeseries_contract` describes provider, licence, entity, metric,
  unit, spatial support, and desired temporal grain;
- a compact server-written high-water ledger plus the release/as-of contract
  preserve when contract, release-set, and source-release content became known;
- the daily date spine preserves gaps/imputation, source-release IDs, and
  observation checksums;
- `forecast_daily_bootstrap` deterministically samples historical daily
  increments and returns low/p10, median/p50, and high/p90 values;
- `materialize_forecast_iteration` persists a default 30-day immutable
  evaluation receipt; and
- `reconcile_forecast_iteration_actuals` appends only complete daily actuals;
  digest v2 preserves their release, observation, and license snapshots so
  residual, error, and interval-coverage series can feed future evaluation.

The iteration plane is hard-coded `evaluation_only` and has no publication
foreign key or promotion function. It does not validate a life-safety forecast.
Its initial independent-increment bootstrap ignores seasonality and serial
correlation; later algorithms should add new method versions instead of
rewriting these receipts.

### Run and read one iteration

Point the iteration DSN at an already migrated database, or leave it unset to use
`DATABASE_URL`:

```powershell
$env:FORECAST_ITERATION_DATABASE_URL = 'postgresql+asyncpg://plantgeo_owner:<password>@127.0.0.1:5442/<plantgeo-database>'
```

From `services/agri-data-service`, run the default 30-day deterministic
bootstrap. The included example uses the retained coarse NASA POWER wind series
and a retrospective cutoff so the next 30 actual days exist; it is evaluation
evidence, not Boise/property or current-weather evidence:

```powershell
$forecastAsOf = (Get-Date).ToUniversalTime().ToString('o')
$iterationKey = 'nasa-power-ws2m-20260331-bootstrap-manual-' + `
  (Get-Date).ToUniversalTime().ToString('yyyyMMddHHmmssfff')
uv run agri-service forecast run-iteration `
  --iteration-key $iterationKey `
  --series-id ee98ea66-e9e9-4997-85da-d5e79d443a23 `
  --release-set-id 10f6933b-c048-4dbc-9c33-68e00d2e6d87 `
  --as-of-time $forecastAsOf `
  --cutoff-time 2026-03-31T00:00:00Z `
  --history-start 2022-04-30T00:00:00Z `
  --horizon-days 30 `
  --simulation-count 1000 `
  --seed 42 `
  --gap-policy strict `
  --lower-bound 0
```

The same operation is available in PgAdmin/psql through
`run-forecast-iteration.sql`; its defaults create a timestamped iteration key
and use PostgreSQL `now` as the first-run as-of, while `-v name=value` overrides
defaults. Migration 0010 conservatively records
pre-existing inputs at its migration boundary, so an as-of earlier than that
boundary intentionally returns no history. Preserve the exact first-run
`as_of_time` when rerunning an existing iteration key.
After the iteration exists, append actuals:

```powershell
$actualAsOf = (Get-Date).ToUniversalTime().ToString('o')
uv run agri-service forecast reconcile-actuals `
  --iteration-id <iteration-id> `
  --actual-release-set-id <validated-release-set-containing-actuals> `
  --as-of-time $actualAsOf
```

Both CLI procedures and the SQL runbook apply a transaction-local 120-second
statement timeout. Direct SQL callers must set an equivalent local timeout.

Inspect every contract, receipt, low/median/high value, actual, residual, and
aggregate metric; the script keeps its own transaction read-only:

```powershell
& 'C:\Program Files\PostgreSQL\16\bin\psql.exe' `
  -h 127.0.0.1 -p 5442 -U plantgeo_owner `
  -d <plantgeo-database> -X `
  -f infra/local-warehouse/read-forecast-iterations.sql
```

In PgAdmin, the main relations are:

- `agri.v_forecast_timeseries_contract`;
- `agri.forecast_iteration` and `agri.forecast_iteration_value`;
- `agri.forecast_iteration_actual` and
  `agri.forecast_iteration_actual_input`; and
- `agri.v_forecast_iteration_outcome`.

### Low-cost upstream PostgreSQL transfer

For fewer than 100 concurrent users, the cheapest first transfer is a compressed
PostgreSQL custom-format archive, not a continuously running ETL service. Use
`backup.ps1` to create a checksummed local `.dump`, inspect it with
`pg_restore --list`, and restore it only into a separately authorized empty
target with the reviewed migration/owner identity:

```powershell
pwsh -File infra/local-warehouse/backup.ps1 `
  -Container <local-warehouse-container> `
  -OutputDirectory $env:PLANTGEO_BACKUP_ROOT

& 'C:\Program Files\PostgreSQL\16\bin\pg_restore.exe' `
  --list $env:PLANTGEO_BACKUP_ROOT\plantgeo-<timestamp>.dump

& 'C:\Program Files\PostgreSQL\16\bin\pg_restore.exe' `
  --dbname '<authorized-target-admin-dsn>' `
  --no-owner --no-acl --single-transaction --exit-on-error `
  $env:PLANTGEO_BACKUP_ROOT\plantgeo-<timestamp>.dump
```

The restore target must already have the required extensions. A full archive is
simple and cheap for the first cut; later refreshes
should use typed promotion receipts or logical replication rather than repeated
full restores. No upstream or Railway database is modified by these local
commands or this workstream.

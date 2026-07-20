# Dedicated local PlantGeo warehouse

This is separate from the native PostgreSQL service at `127.0.0.1:5433` and
from every other application database. It uses the PostgreSQL 16 Timescale HA
image family already supported by this repository, a dedicated named volume,
and loopback-only port `5442`. Its semantic lineage bundles record the source
PostgreSQL major and require an equal-or-newer target during promotion
preflight, including the planned PostgreSQL 18 Railway target.

## Start it

```powershell
Copy-Item infra/local-warehouse/.env.example infra/local-warehouse/.env
# Edit infra/local-warehouse/.env locally with a unique password.
podman compose --env-file infra/local-warehouse/.env -f infra/local-warehouse/compose.yaml build
podman compose --env-file infra/local-warehouse/.env -f infra/local-warehouse/compose.yaml up -d
podman compose --env-file infra/local-warehouse/.env -f infra/local-warehouse/compose.yaml ps
```

The derived image retains the upstream extension packages but removes its
automatic TimescaleDB/toolkit creation and host-sized tuning scripts. It does
not install or enable any PostgreSQL extension.

## Explicit extension gate

After the container reports healthy, verify capability first (replace the
placeholder with the local password without placing it in source control):

```powershell
$env:PGPASSWORD = '<local password>'
& 'C:\Program Files\PostgreSQL\16\bin\psql.exe' -h 127.0.0.1 -p 5442 -U plantgeo_owner -d plantgeo -Atc "SELECT name, default_version, installed_version FROM pg_available_extensions WHERE name IN ('postgis','timescaledb','vector','pgcrypto') ORDER BY name;"
```

Then the operator, not an automated startup script, runs the reviewed manual
gate. It is safe to re-run and stops at the first error:

```powershell
& 'C:\Program Files\PostgreSQL\16\bin\psql.exe' -h 127.0.0.1 -p 5442 -U plantgeo_owner -d plantgeo -f infra/local-warehouse/enable-extensions.sql
```

The Alembic foundation verifies all four are already installed and fails before
it creates any `agri` object. Only after that approved gate should reviewed
Drizzle/Alembic migrations create schemas and `agri-cli source-ingest` load local source releases. The future
promotion archive comes from this database; it never comes from `pgt`,
`postgres`, or another application database.

## Explicit local-loader role gate

After the reviewed migrations create the `agri` schema, run the user-controlled
role script as the bootstrap owner. It creates a new `plantgeo_loader` role and
fails closed if that role already exists, so an unaudited existing role cannot
retain unexpected memberships. It never runs from container startup and it does
not grant schema DDL, ownership, or access outside the source-lineage tables.

```powershell
$env:PGPASSWORD = '<local owner password>'
& 'C:\Program Files\PostgreSQL\16\bin\psql.exe' -h 127.0.0.1 -p 5442 -U plantgeo_owner -d plantgeo -v loader_password='<unique loader password>' -f infra/local-warehouse/create-loader-role.sql
```

Set `LOCAL_SOURCE_LOADER_DATABASE_URL` to the `plantgeo_loader` async DSN in
the data-service operator environment. `agri-cli source-ingest` fails closed
for an unset target, a different host/port/database, `DATABASE_URL`, or the
`plantgeo_owner` role.

## Connection forms

```text
# Bootstrap/migration only; never use for normal source loads.
postgresql://plantgeo_owner:<password>@127.0.0.1:5442/plantgeo
postgresql+asyncpg://plantgeo_owner:<password>@127.0.0.1:5442/plantgeo

# Normal `source-ingest` connection after the manual role gate.
postgresql+asyncpg://plantgeo_loader:<loader-password>@127.0.0.1:5442/plantgeo
```

Do not point long-lived application containers at the owner role. Create the
reviewed least-privilege runtime and migration roles after schema initialization.

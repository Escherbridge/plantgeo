---
type: local-runbook
---

# Local warehouse access

## Scope and safety

This is the loopback-only local PlantGeo warehouse in Podman, not Railway and
not production. It listens on `127.0.0.1:5442`, database `plantgeo`.

Use a read-only transaction for exploration. Prefer the local viewer login; the
owner login is a bootstrap/admin path, not a normal inspection identity.

## Connection parameters

| Setting | Value |
| --- | --- |
| Host | `127.0.0.1` |
| Port | `5442` |
| Database | `plantgeo` |
| Read-only user | `plantgeo_local_viewer` |
| Admin/bootstrap user | `plantgeo_owner` |
| TLS | `sslmode=disable` for this local loopback container |

Keep the password out of source control. Let `psql` prompt rather than placing
it in shell history:

```powershell
& 'C:\Program Files\PostgreSQL\16\bin\psql.exe' `
  'postgresql://plantgeo_local_viewer@127.0.0.1:5442/plantgeo?sslmode=disable'
```

Then begin with:

```sql
BEGIN READ ONLY;
SET LOCAL statement_timeout = '30s';
SET LOCAL search_path = agri, public;
```

For pgAdmin or DBeaver use the same host, port, database, and user. Enable a
read-only connection mode if the client offers it. End with `ROLLBACK;`.

## Start or check the local warehouse

```powershell
podman --connection podman-machine-default-root start plantgeo-warehouse_plantgeo-warehouse_1
podman --connection podman-machine-default-root ps --all --filter name=plantgeo-warehouse
```

Do not recreate the container or named volume merely to inspect data; that risks
the retained local evidence.

## What to inspect

Run [`queries.sql`](./queries.sql) in order. They show source/release lineage,
retained artifacts, signal coverage, and the forecast evidence plane. They avoid
an unbounded dump of the 34.8-million-row signal table.

Inspection can prove timestamps, coverage, and lineage. It cannot repair the
historical availability clock: a row recorded after a simulated forecast origin
was unavailable to that origin, even if its observed date is earlier.

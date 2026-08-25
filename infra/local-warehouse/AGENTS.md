# Dedicated local warehouse

This compose project is the only supported local source for a future PlantGeo promotion. It deliberately does not share PostgreSQL ports, volumes, users, or databases with other local applications. The service listens only on `127.0.0.1:5442` and uses an isolated named volume.

The local source intentionally uses a tiny derivative of upstream `postgis/postgis:16-3.4`, which matches the repository's supported PostgreSQL major. Semantic lineage promotion records the source major and requires an equal-or-newer private target, including the planned PostgreSQL 18 Railway target. TimescaleDB was dropped repo-wide 2026-08-25 — production's only hypertable, `tracking.positions`, was always empty (0 rows, 0 chunks) and no continuous aggregate ever existed, so the extension earned nothing and this warehouse no longer carries it either. The base image packages the PostGIS extension, but its vendor init script (`10_postgis.sh`) automatically creates a `template_postgis` database and loads extensions into it; `Dockerfile` removes only that script, retaining the packaged binaries while requiring the operator's explicit extension gate. Running the entrypoint as root allows it to prepare the mounted volume and then drop to the PostgreSQL user, matching the documented Railway remediation.

**Role management is retired as of 2026-08-08, and this directory no longer provisions any role.** Revision `20260808_0019` dropped the `plantgeo_forecast_writer`/`_publisher`/`_reader`/`_mv_refresher`/`_mv_refresh_owner` family after verifying it had zero members, no DSN, and no `USAGE` on schema `agri`; every application path connects with the single owner credential. The five bootstrap/grant scripts that belonged to that world — `create-loader-role.sql`, `create-forecast-roles.sql`, `create-forecast-refresh-operator.sql`, `create-local-access-roles.sql`, `grant-resolution-aware-loader.sql` — are deleted, along with the DSN validators that asserted a login. Do not reintroduce one to "restore separation of duties": that is the decision, not an oversight. `plantgeo_loader` and the `plantgeo_local_*` convenience logins still exist in the clusters where they were created, and DSNs naming them keep working, but no repository code manages, provisions, or requires them. See `docs/reports/migration-decision-packet-2026-08-08.md` § Resolution.

`enable-extensions.sql` is a manual pre-migration gate, never an init script. It contains the exact owner-run commands for PostGIS, pgvector, and pgcrypto after the operator has checked package availability. The Alembic foundation preflight refuses to create `agri` until all three are installed.

`first-metric-forecast.sql` is a one-time, fail-closed, evaluation-only fixture over the exact validated NASA POWER release and the `WS2M` point sample at `(-105, 40)`. It reuses the existing exact-source series identity and creates new candidate/model/policy/run identities. Failure retains a rejected run and immutable hindcasts; even a passing evaluation returns before forecast receipts, values, publication, or pointer advancement. It is stale-input historical SQL-baseline evidence, not operational weather, ML, or strategy evidence.

`read-pilot-evidence.sql` is the read-only operator view of the Boise evidence release and latest v2 forecast evaluation. Keep its transaction explicitly read-only and add new inspection queries there rather than mixing diagnostic reads into ingestion or publication fixtures.

`run-forecast-iteration.sql` is the manual, evaluation-only stored-procedure
entry point for revision `0010`; its default retained wind-series fixture is
retrospective and coarse. `read-forecast-iterations.sql` is the corresponding
read-only contract/receipt/outcome inspection surface, including contract and
training-license snapshots plus v2 actual release/observation/license lineage.
The server-recorded input ledger remains release-granular and its DML is denied
to local developer/viewer/loader capabilities. Neither script may create a
publication, schedule, recommendation, or Railway mutation.

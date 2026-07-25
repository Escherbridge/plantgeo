# Dedicated local warehouse

This compose project is the only supported local source for a future PlantGeo promotion. It deliberately does not share PostgreSQL ports, volumes, users, or databases with other local applications. The service listens only on `127.0.0.1:5442` and uses an isolated named volume.

The local source intentionally uses a tiny derivative of upstream `timescale/timescaledb-ha:pg16`, which matches the repository's supported PostgreSQL major. Semantic lineage promotion records the source major and requires an equal-or-newer private target, including the planned PostgreSQL 18 Railway target. The base image packages every required extension, but its vendor init scripts automatically create TimescaleDB and tune the server; `Dockerfile` removes only those scripts, retaining the packaged binaries while requiring the operator's explicit extension gate. Running the entrypoint as root allows it to prepare the mounted volume and then drop to the PostgreSQL user, matching the documented Railway remediation.

Local convenience access stays loopback-only. `plantgeo_local_developer` may have broad non-superuser access for interactive warehouse work; `plantgeo_local_viewer` is the PgAdmin/read-only identity. These convenience logins must not be copied to Railway. Railway keeps the reviewed writer, publisher, reader, and MV-refresh capabilities separate.

`enable-extensions.sql` is a manual pre-migration gate, never an init script. It contains the exact owner-run commands for PostGIS, TimescaleDB, pgvector, and pgcrypto after the operator has checked package availability. The Alembic foundation preflight refuses to create `agri` until all four are installed.

`create-loader-role.sql` is a manual post-migration gate, never an init script. It creates the constrained `plantgeo_loader` login only when that role is absent, then grants only the lineage writes needed by `source-ingest`; normal loads must never use the `plantgeo_owner` bootstrap role.

After `20260723_0009`, existing warehouses apply `grant-resolution-aware-loader.sql` once as the owner. It adds `SELECT`/`INSERT` only on the immutable geospatial evidence plane and `SELECT` on its existing lineage parents; it does not recreate the loader role, grant generic job-ledger writes, or apply automatically.

`create-forecast-roles.sql` is the reviewed post-0005 capability-role gate. Its four NOLOGIN roles keep draft computation, immutable finalization/publication, serving reads, and security-definer MV refresh separate. Application/operator logins receive one reviewed membership outside this script; the API reader must never inherit writer, publisher, or refresh capability. Re-run a privilege audit after every forecast migration instead of relying on default privileges.

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

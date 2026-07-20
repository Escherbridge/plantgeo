# Dedicated local warehouse

This compose project is the only supported local source for a future PlantGeo promotion. It deliberately does not share PostgreSQL ports, volumes, users, or databases with other local applications. The service listens only on `127.0.0.1:5442` and uses an isolated named volume.

The local source intentionally uses a tiny derivative of upstream `timescale/timescaledb-ha:pg16`, which matches the repository's supported PostgreSQL major. Semantic lineage promotion records the source major and requires an equal-or-newer private target, including the planned PostgreSQL 18 Railway target. The base image packages every required extension, but its vendor init scripts automatically create TimescaleDB and tune the server; `Dockerfile` removes only those scripts, retaining the packaged binaries while requiring the operator's explicit extension gate. Running the entrypoint as root allows it to prepare the mounted volume and then drop to the PostgreSQL user, matching the documented Railway remediation.

`enable-extensions.sql` is a manual pre-migration gate, never an init script. It contains the exact owner-run commands for PostGIS, TimescaleDB, pgvector, and pgcrypto after the operator has checked package availability. The Alembic foundation preflight refuses to create `agri` until all four are installed.

`create-loader-role.sql` is a manual post-migration gate, never an init script. It creates the constrained `plantgeo_loader` login only when that role is absent, then grants only the lineage writes needed by `source-ingest`; normal loads must never use the `plantgeo_owner` bootstrap role.

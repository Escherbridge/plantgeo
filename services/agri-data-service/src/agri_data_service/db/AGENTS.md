# Database runtime boundary

Runtime connections never create extensions, schemas, or application tables. Alembic owns structural migrations; narrowly scoped operational maintenance may only manage objects that a migration explicitly created for that purpose.

Production HTTP database pools are profile-bound. `receiver_writer_session` accepts only `SERVICE_PROFILE=receiver_writer` plus `RECEIVER_WRITER_DATABASE_URL`; `published_reader_session` accepts only `SERVICE_PROFILE=published_reader` plus `PUBLISHED_READER_DATABASE_URL`. Neither production profile may receive or retain `DATABASE_URL` or the opposite profile's DSN. The legacy pool is created lazily only for `combined_local`, which is local compatibility and never rollout-ready.

`local_source_loader_session` is intentionally separate from the service-wide `async_session`: `source-ingest` receives an explicitly approved local Compose DSN and uses a one-connection pool. It must never fall back to `DATABASE_URL` or serve as a production/Railway connection path.

`forecast_mv_refresh_session` is likewise isolated from the API pool. The operator CLI accepts only `FORECAST_MV_REFRESH_DATABASE_URL`, verifies that its NOINHERIT login is non-elevated, owns neither the database, `agri` schema, nor any `agri` relation/function, has no direct `agri` grants, and has exactly one non-admin, non-inherited membership: the reviewed NOLOGIN `plantgeo_forecast_mv_refresher` capability role. It then uses `SET LOCAL ROLE` before invoking the migration-owned refresh function. Operator login creation, credential custody, and membership are environment-specific manual steps. The command reports the resulting row count and disposes the one-connection pool; the application reader never inherits that capability, and no scheduler calls it.

`forecast_iteration_session` is a separate one-connection, loopback-only path for
the evaluation iteration procedures. It requires
`FORECAST_ITERATION_DATABASE_URL`, the `plantgeo_local_developer` login, port
5442, and an explicitly named `plantgeo*` database; it never falls back to an API
DSN. The resulting rows are ML/evaluation evidence and have no publication path.
This convenience identity and command must not be copied into Railway or invoked
by a scheduler.

`job_event` uses UTC daily partitions. `maintain_job_event_partitions` takes a transaction-scoped advisory lock, covers the complete hot-retention window plus a short future window, moves matching rows out of the loss-prevention default partition before attachment, and drops only date-named partitions older than the configured hot window. Run it from the operator machine or another approved control plane; it is not a Railway forecast worker. A failure must alert the operator and leave the default partition in place so events are not silently lost.

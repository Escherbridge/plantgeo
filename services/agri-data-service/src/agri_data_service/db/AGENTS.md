# Database runtime boundary

Runtime connections never create extensions, schemas, or application tables. Alembic owns structural migrations; narrowly scoped operational maintenance may only manage objects that a migration explicitly created for that purpose.

Production HTTP database pools are profile-bound. `receiver_writer_session` accepts only `SERVICE_PROFILE=receiver_writer` plus `RECEIVER_WRITER_DATABASE_URL`; `published_reader_session` accepts only `SERVICE_PROFILE=published_reader` plus `PUBLISHED_READER_DATABASE_URL`. Neither production profile may receive or retain `DATABASE_URL` or the opposite profile's DSN. The legacy pool is created lazily only for `combined_local`, which is local compatibility and never rollout-ready.

**Identity/role enforcement in `config.py` is gone as of 2026-08-08** (owner ruling, recorded in `20260808_0019`): every application path connects with the single owner credential, so no DSN validator asserts a login, host, port, role, or service identity. `LOCAL_SOURCE_LOADER_DATABASE_URL`, `FORECAST_MV_REFRESH_DATABASE_URL`, and `FORECAST_ITERATION_DATABASE_URL` are now portable optional *overrides* resolved by one shared helper, `Settings._require_command_database_url`: each returns its own value when set and `DATABASE_URL` otherwise, and setting an override equal to `DATABASE_URL` is legal. A blank or whitespace-only override is unset, not a value — `""` used to fall through while `"  "` was returned verbatim and died inside SQLAlchemy's URL parser. Two failures remain for command/local DSNs: neither variable set, and a DSN that is not a complete `postgresql+asyncpg://` URL. That last check is `Settings._require_complete_database_url`, deliberately the **only** DSN parser in the file: the profile DSNs (`RECEIVER_WRITER_`/`PUBLISHED_READER_`) route through the same one, so shape rules cannot drift between the two families. The two production HTTP profile resolvers add one deployment-identity invariant after that shared shape check: their URL path must name the canonical `plantgeo` database. This catches an accidental reference to the legacy database named `railway` before rollout healthchecking. Do not add login, host, port, distinctness, role, or service allowlists; command and local DSNs remain database-name portable.

What survives is *pooling* separation, which is about connection budget rather than privilege. `local_source_loader_session` (used by `source-ingest`, every `ingest-*`/`jobs-*` verb through `ingest_session`) and `forecast_mv_refresh_session` each open a dedicated one-connection pool and dispose it, so a long ingest or a non-concurrent `REFRESH` never consumes the API pool.

`local_source_loader_pool` exposes that same dedicated pool when connection identity is part of the
correctness contract. The unified executor checks out one `AsyncConnection` for a complete leader-elected
tick and binds its `AsyncSession` to that connection. PostgreSQL session advisory locks belong to a
physical backend, not to an ORM session or transaction; pool size one does not itself pin a checkout, and
transaction boundaries may otherwise return it before the matching unlock.

The forecast MV refresh also lost its role ceremony in `20260808_0019`. It used to verify that the login was NOINHERIT, non-elevated, owner of nothing in `agri`, holder of no direct `agri` grant, and a member of exactly one role — the NOLOGIN `plantgeo_forecast_mv_refresher` — via `sql/cli/forecast_mv_refresh_eligibility.sql`, then `SET LOCAL ROLE` into that capability before invoking the refresh function. That role was retired with the rest of the family, and the matview plus its `SECURITY DEFINER` refresher now belong to the owner credential, so the refresh is an ordinary owner statement: the eligibility probe and the `SET LOCAL ROLE` are deleted. The command still reports the resulting row count and disposes the one-connection pool, and no scheduler calls it.

`forecast_iteration_session` is a separate one-connection path for the evaluation
iteration procedures; point `FORECAST_ITERATION_DATABASE_URL` at a migrated
database or leave it unset to use `DATABASE_URL`. The resulting rows are
ML/evaluation evidence and have no publication path. The command must not be
invoked by a scheduler.

`job_event` uses UTC daily partitions. `maintain_job_event_partitions` takes a transaction-scoped advisory lock, covers the complete hot-retention window plus a short future window, moves matching rows out of the loss-prevention default partition before attachment, and drops only date-named partitions older than the configured hot window. Run it from the operator machine or another approved control plane; it is not a Railway forecast worker. A failure must alert the operator and leave the default partition in place so events are not silently lost.

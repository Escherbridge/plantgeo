# Database runtime boundary

Runtime connections never create extensions, schemas, or application tables. Alembic owns structural migrations; narrowly scoped operational maintenance may only manage objects that a migration explicitly created for that purpose.

`local_source_loader_session` is intentionally separate from the service-wide `async_session`: `source-ingest` receives an explicitly approved local Compose DSN and uses a one-connection pool. It must never fall back to `DATABASE_URL` or serve as a production/Railway connection path.

`job_event` uses UTC daily partitions. `maintain_job_event_partitions` takes a transaction-scoped advisory lock, covers the complete hot-retention window plus a short future window, moves matching rows out of the loss-prevention default partition before attachment, and drops only date-named partitions older than the configured hot window. Run it from the operator machine or another approved control plane; it is not a Railway forecast worker. A failure must alert the operator and leave the default partition in place so events are not silently lost.

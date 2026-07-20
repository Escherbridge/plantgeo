# Agri model boundary

All tables in this directory are owned by Alembic and live in the `agri` schema. Foreign keys stay schema-qualified so connection `search_path` cannot redirect writes into application tables.

The durable ledger uses PostgreSQL as the source of truth for received artifacts and publications. Phase-one ETL, forecast, and model compute use the local manifest contract in `execution/` and do not dispatch through Celery. Work is at-least-once: `logical_run_key` and shard keys prevent duplicate intent; local checkpoints are append-only and checksummed; the database lease/fencing fields remain the recovery contract for bounded publication-side work; immutable outputs are exposed only by advancing `publication_pointer` in a transaction.

`release_set.manifest_checksum` is part of local run identity and is rechecked at stage and commit. After a set leaves `draft`, database triggers freeze its state, identity, validation timestamps, and membership. Withdrawal is a new publication-pointer rollback or tombstone, never mutation of pinned lineage.

Bounded local artifacts may use `artifact.storage_class=database_inline`; their bytes, declared size, and SHA-256 digest must agree. A local publication pointer remains `artifact_only` until a typed loader validates and promotes its rows into a serving table. Never query generic artifact bytes from a browser-facing route.

`job_event.detail` is for redacted structured diagnostics, not source payloads, credentials, private locations, or model training rows. A partition manager must create dated partitions and remove event partitions after 30 days; the default partition only prevents event loss before that manager exists.

Strategy and companion records default to `draft`. Strategy approval requires a reviewer, timestamp, citation, source URL, jurisdiction, and explicit limitations. The first strategy seeds contain only four USDA NRCS practice identities and definitions; all unsupported climate, soil, slope, labor, timing, and impact values are `NULL`. National standards remain definition/discovery sources until a reviewer verifies the exact version, applicability, and current local guidance.

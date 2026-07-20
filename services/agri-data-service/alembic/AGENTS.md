# Migration boundary

Alembic is the only component allowed to create or alter the `agri` schema. Runtime API and worker processes must never call `create_all`, `drop_all`, or extension DDL. The foundation revision is also forbidden from enabling extensions: it begins with an installed-extension preflight, so an operator must run the reviewed manual extension gate before Alembic creates `agri`.

The foundation revision is intentionally forward-only because downgrading would destroy source lineage, checkpoints, and published model evidence. Roll back by restoring a verified backup into a fresh database and deploying the prior application version. PostgreSQL extensions and the `agri` schema are shared infrastructure and are never removed by a downgrade. Once this revision has run outside disposable environments, do not change its preflight again: Alembic will not replay it for an already-versioned database, so later database-level changes need a new revision.

Keep future revisions explicit and deterministic. Do not call current ORM `metadata.create_all()` from a historical revision because replaying that revision would otherwise change as models evolve.

Release-set membership is mutable only in `draft`. The foundation triggers serialize draft finalization against membership writes, reject item inserts, updates, or deletes after validation, and freeze state, identity, and validation timestamps after the set first leaves draft. Future migrations must preserve that database-level invariant.

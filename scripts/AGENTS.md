# Validation routing

`test-surface.mjs` selects local frontend test batches; see `docs/testing.md` for commands.
Use Vitest's installed `related` command for dependency traversal, then a separate batch for
filesystem contracts because those references do not appear in the import graph. Keep the
source-only batch first so unrelated contracts cannot hide a no-tests result.
Removed, shared, or unclassified runtime paths select the full suite. A missing base ref
is an error; a scoped pass never represents a full release check.

Keep selection tests focused on missed-change risks: staged/unstaged/untracked files,
renames/deletions, cross-stack fixtures, full-suite fallback, and invalid options.
Do not add tests whose only purpose is pinning an obsolete implementation or archive text.

The Docker build runs the same tooling tests before Vitest. Its context explicitly
includes `test-surface.mjs`, `migrate-database.mjs` and their tooling tests, and its build stage
installs Git for the temporary-repository integration test. The application runtime
does not install Git; `.git` remains excluded from the build context. Keep these
build requirements aligned when adding executable tooling to the full test command.

# Migration session boundaries

`migrate-database.mjs` is the shared runner used by bootstrap, Railway pre-deploy and
`npm run db:migrate`. It sets the connection search path to `public` before migration,
after each pending migration file, and after successful completion. This also covers
an already-current database and bootstrap's subsequent reference seed phase. Do not
use `RESET search_path`: an ambient `"$user", public` path can start resolving an
unqualified table into a newly created schema matching the database user's name.

The pg_dump baseline intentionally sets a session-level empty search path. Drizzle
runs all pending files in one transaction, so without a boundary the baseline's
setting leaks into later unqualified public table definitions. A session-level
`SET search_path TO public` is required; `SET LOCAL` does not replace the baseline's
session setting after commit. This boundary governs the migration runner only and
does not rewrite stored function-specific search paths or historical SQL files.

Read migrations with the installed `readMigrationFiles` before adding the separate
boundary statement to each in-memory statement list. Preserve the original hash,
timestamp, journal and statement order. Delegate to the same `db.dialect.migrate`
and `db.session` seam used by the installed postgres-js migrator, retaining its
single pending-batch transaction, ledger writes and timestamp-based skip behavior.
That driver seam is internal: when upgrading Drizzle, keep the contract tests against
its actual driver/dialect and the fresh-database/no-op validation. On a migration
error, Drizzle rolls back the batch and restores the pre-transaction public path;
the helper propagates the error without another SQL call masking it.

`tests/migrate-database.test.mjs` uses the actual installed Drizzle reader, driver and
dialect with a controlled SQL transport to check boundaries, original file hashes,
one transaction, skip/no-op behavior and rollback/error propagation. It is not proof
of PostgreSQL DDL execution; bootstrap on an isolated database supplies that evidence.
Include the helper in both Docker build context and runtime COPY. Historical migrations
and the readiness hash pin are not changed to repair session handling.

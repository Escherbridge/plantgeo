# Application database schema

`drizzle/0000_baseline.sql` is the complete greenfield definition for the
application-owned `public`, `geo`, and `tracking` schemas. The Drizzle journal
opens with exactly that baseline and then lists each forward migration in order;
`migration-contract.ts` pins the **newest** journal entry's timestamp and
SHA-256 digest, which is what `/api/ready` requires to be present in
`drizzle.__drizzle_migrations` before a release is considered ready. Re-pin it in
the same commit as the migration, or readiness 503s until the ledger catches up.

The `geo.features` and `geo.geometry` tables are reserved for transactional
features such as interventions. Environmental observations and rendered layer
payloads are served from governed Parquet and must not gain PostgreSQL fallback
queries.

## feature-social

`geo.feature_likes` and `geo.feature_comments` (migration `0001_feature_social`)
are the platform's only like/comment store. They key on a bare
`geo.features.id`, not an intervention-specific id, so the map detail panel,
`/feed`, and any future content type read one backend instead of growing a
second schema each.

Two rules the tables encode rather than leave to callers:

- A like is a `(feature_id, user_id)` row, unique. There is no counter column;
  the count is always `count(*)`. That is what makes "un-like" and "did *I*
  like this" expressible, and what stops one account inflating a total.
- A comment is soft-deleted (`deleted_at`, plus `deleted_by_user_id`), never
  removed. Author-or-moderator deletion is a moderation decision, and a
  moderation decision that leaves no trace is not auditable. Every read filters
  `deleted_at IS NULL`.

Neither table participates in the review lifecycle. `features.status` and
`features.review_note` stay owned by `contributions.publishContribution` /
`rejectContribution`; the social router reads `status` only as a visibility
predicate and issues no `UPDATE` against `geo.features` at all.

For a future relational change, add one forward Drizzle migration and update
the migration contract in the same review. The migration must land with the
application code that requires it.

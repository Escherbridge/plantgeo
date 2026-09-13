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

## strategy-requests

`strategy_requests` and `priority_zones` no longer exist (migration
`0004_public_strategy_requests`). A strategy request is a published
`geo.features` row carrying `properties.kind = 'request'`, written by
`interventions.submitRequest` — the same table, layer, geometry validator and
properties bag an intervention recommendation uses, minus the review queue. The
three production rows were copied across by
`scripts/backfill-strategy-requests.mjs` before the drop; that script must run
before the migration, never after, and is idempotent through
`properties.migratedFromStrategyRequestId`.

`request_votes` survived the drop with its foreign key moved from
`strategy_requests.id` to `geo.features.id` (column renamed `request_id` →
`feature_id`). Votes and likes stay distinct concepts by product decision: a
like is `geo.feature_likes`'s per-user toggle counted with `count(*)`, a vote is
one-way support. Nothing writes the table today — its only writer,
`community.voteOnRequest`, went with the retired router — so it is deliberately
dormant infrastructure, not a wired feature. Wire it by inserting
`(feature_id, user_id)` and deriving the count; the denormalized counter it used
to increment no longer exists to drift.

`geo.intervention_tiles()` projects `properties ->> 'kind'` as of
`0005_intervention_tiles_kind`, which is what lets the published tile source
paint a request its own colour. **Restart Martin after applying it** — the same
caveat `0002_intervention_tiles_category` carries.

For a future relational change, add one forward Drizzle migration and update
the migration contract in the same review. The migration must land with the
application code that requires it.

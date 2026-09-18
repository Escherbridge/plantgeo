# Server contracts

PostgreSQL is limited to application, community, tracking, operational-control, intervention, and
bounded lookup data. Environmental observations and derived layer products are read from governed
Parquet through the data service. Do not add an environmental PostgreSQL query, writer, cache, or
fallback.

`geo.features` is available only for user-authored interventions and community contributions.
Every generic feature query must use an explicit application-layer allowlist.

Parquet responses follow the selected-day contract: report the requested day, resolved publication
day, coverage, provenance, governed absences, and temporal/spatial neighbours. Missing data stays
unavailable until a publication or validation job repairs it.

Keep cross-cutting rationale in this file and code comments short. Run all edits before the final
type, lint, boundary, and test sweep.

## Canonical contribution review

Publish and reject are pending-review actions, including for administrators. The final UPDATE
must atomically match feature ID, layer partition and `status = pending_review`; a preceding
SELECT alone cannot protect against a stale second reviewer. A zero-row conditional UPDATE
returns CONFLICT, never success, and leaves the first committed status and review note intact.
Missing IDs return NOT_FOUND before the update. No administrator override is implied by these
actions; any future override needs a separate explicit operation and contract.

Router regressions compile the actual Drizzle UPDATE predicate and cover refused zero-row
writes. They do not replace a database-backed stale-review check or prove simultaneous database
scheduling. The queue refreshes on refusal and retains an explanation even when no rows remain.

## trpc / db / auth

`trpc/routers/teams.ts` composes five procedure groups from `trpc/routers/teams/*`
(organizations, membership, invitations, join-links, directory); each group shares the
DB/auth helpers in `teams/shared.ts` (`loadMembership`, `requireTeamAccess`, `loadOrganization`,
`adoptActiveTeam`, `loadRedeemingIdentity`) rather than re-querying `teamMembers`/`teams`
directly — every membership or role check reads from the database inside the acting
transaction, never from the session/JWT, because the JWT's role claim is minted at sign-in and
never refreshed. Split the file this way (not by CRUD verb) because the original router already
grouped procedures with `// ─── … ───` banner comments along exactly this seam; splitting on it
kept a one-file-to-one-file move with no re-grouping judgment calls.

Every BullMQ job registration (`jobs/*.ts`) parses `REDIS_URL` through the single
`getRedisConnection()` in `redis.ts`, not a local re-implementation — three jobs had grown their
own copy with slightly different fallback shapes before this pass; `alert-dispatcher.ts` was
always the correct example to copy.

`db/schema/land-context/shared.ts`'s `PNW_STATE_CODES`/`PnwStateCode` moved behind
`getRegion().adminCodes` in the `federation.md` §5 step 2 push; both names stay as deprecated
aliases, and new code should read `RegionAdminCode` (`src/lib/region/region.ts`) directly.

# `src/lib/server/db` — module notes

Rationale and constraints that the code's one-line doc comments deliberately omit.
Add a section per module as it grows; sections are independent.

## §geometry-dimension

The one conformed geometry dimension every fact joins to. Files: `schema.ts`
(`geometry`, and `features.geometryId`), migration
`drizzle/0008_geometry_dimension.sql`, backfill `scripts/backfill-geometry.sql`,
guard test `src/__tests__/lib/geometry-migration.test.ts`, and the identity
contract it stores,
`services/agri-data-service/src/agri_data_service/ingest/identity.py` (read-only
from here — lane A owns it). Full design, including the change-detection rule
table, is `plans/ingestion-warehouse-consolidation-2026-08-03.md` §2.0.

### `natural_key` is the place; `geometry_id` is one version of it

Identity used to be defined in six places — each ingestion job built its own
`properties->>'id'` string and the only database-side guard was a partial unique
index scoped to a single layer. `geo.geometry.natural_key` replaces all six. It is
always `'<producer>:<producer-local id>'`, minted only by
`identity.FeatureIdentity.natural_key`, and it names the **place across its whole
life**. `geometry_id` names **one version** of that place, and every fact carries a
`geometry_id`, never a `natural_key`.

The namespace is a correctness requirement, not hygiene. Under Type-2 the column no
longer carries a bare global UNIQUE — uniqueness moved to
`(natural_key, version_valid_from)` plus a partial unique on `natural_key` where
`version_valid_to IS NULL`. So two producers colliding on an unnamespaced id are not
merged into one row; they are **interleaved into one version chain**, and the result
is a fabricated history that looks entirely plausible and is strictly harder to
detect than a duplicate. `ck_geometry_natural_key_namespaced` makes that
structurally unrepresentable rather than something a periodic audit query might
notice.

The separator is the **first** colon, never the last. Three of the four live
producers put further colons inside the producer-local id, and two of those embed a
full ISO-8601 timestamp whose `HH:MM:SS` adds more. Do not split the key at all —
read the `producer` column, which exists so that "which ingest owns this row" is
answerable without parsing. One consequence of the `LIKE producer || ':%'` check:
producer tokens must contain no `_` and no `%`, which
`identity.PRODUCER_TOKEN_PATTERN` already guarantees.

There is deliberately **no `first_seen_at` column**. `min(version_valid_from)` over a
`natural_key` is first-seen by construction, and it cannot be silently rewritten,
because closing a version writes `version_valid_to` and never touches
`version_valid_from`. That is the honest replacement for `geo.features.created_at`,
which is "last touched" rather than "first seen" and must never be used to derive a
first-observation time, a `data_available_at`, a retention window or the slider's
history depth.

That equivalence now holds for every producer. **Corrected 2026-08-04:** this paragraph
used to say the dimension could not answer when gauge `14181500` was first seen, and to
suggest `min(version_valid_from) WHERE natural_key LIKE 'usgs-nwis:14181500:%'`. Both
statements are stale. `scripts/rekey-geometry-to-entity.sql` re-keyed `geo.geometry` from
the OBSERVATION key onto the ENTITY key — `identity.FeatureIdentity.entity_key`, "the
enduring place this observation was taken at, not the observation" — so the table now
holds one row per place (7,424 rows) rather than one per reading. The key is exactly
`usgs-nwis:14181500`, that `LIKE` pattern matches nothing at all, and the question is a
plain equality:

```sql
SELECT version_valid_from FROM geo.geometry WHERE natural_key = 'usgs-nwis:14181500';
```

`usgs-nwis` and `open-meteo` are the two producers that set `entity_local_id` (the site
number, and the 4dp `lat:lon` sample point). Everywhere else `entity_local_id` is None, so
`entity_key == natural_key` by construction and nothing changed.

### Three producers are v1-only, and that is the identity contract, not a bug

`natural_key` is `'<producer>:<producer-local id>'`, and the producer-local id is
byte-identical to the `featureId` the TypeScript ingest already writes into
`properties->>'id'`. For three of the six producers that id embeds the observation
itself: `usgs-nwis` is `siteNo:updatedAt` (`ingestion-jobs.ts:189`), `open-meteo` is
`lat:lon:observedAt` (`:294`), and `firms` is
`satellite:acqDate:acqTime:lat:lon` (`:100-115`). Measured on production 2026-08-04:

| producer | keys | distinct geometries |
| --- | --- | --- |
| `firms` | 6297 | 6201 |
| `usgs-nwis` | 10723 | 899 |
| `open-meteo` | 1981 | 116 |
| `wfigs` | 112 | 112 |

So `uq_geometry_current` can never conflict for those three, no version of theirs is
ever closed, and `geometry_current` returns one row per reading rather than one per
site. That is the *stated* rule of the change-detection subsection below — never
version a producer whose location is part of its identity, because a "moved" record
there is a different record — and it mirrors `geo.features`, which already holds
10,723 gauge rows for 899 sites. The dimension is 1:1 with `geo.features` here; it
introduces no fan-out of its own.

Two costs are real and should not be discovered later. `geo.geometry` grows with
`geo.features` and, unlike facts, is never pruned, so the never-closing rows accumulate
permanently. And the Type-2 apparatus — `version_valid_to`, `superseded_by`,
`ck_geometry_version_order`, `ix_geometry_asof` — carries no information for 96 % of the
rows. The backfill therefore prints a per-producer fan-out `NOTICE` rather than an
assertion, so the ratio stays visible without blocking a run.

Making a gauge's key its site (`usgs-nwis:14181500`) is **not** a change to make here.
It would break the byte-identity between `identity.py` and the live TypeScript, and
because `features_layer_external_id_unique` is on `(layer_id, properties->>'id')` it
would collapse the gauge layer from an accumulating series into last-reading-wins —
a data-model decision that belongs to `services/ingestion-jobs.ts` and to whoever owns
retention, not to a dimension migration.

### `centroid` is a plain column and must never become `GENERATED … STORED`

It is written at insert time by whoever writes the row. A stored generated column
calling a PostGIS function is exactly the mechanism that made the agri warehouse
unrestorable from its own `pg_dump`
(`services/agri-data-service/plans/checksum-layer-audit-2026-08-03.md:176-186`); two
of those are being deleted this quarter, and this is not a third. The convenience of
never having to remember the column is not worth an unrestorable dump.

### Dimension rows are never deleted, so every fact FK is `ON DELETE RESTRICT`

A fact points at one *version*, permanently — that is what makes the slider correct
with no as-of join on the read path. `CASCADE` would therefore let a dimension
cleanup silently delete history that facts still cite. Prune facts; never prune
geometry. A superseded polygon costs a few KB and stays forever. This applies to
`geo.features.geometry_id`, to `geo.metric_daily` when it lands, and to the `agri`
columns lane C repoints. `superseded_by` is `RESTRICT` for the same reason, and its
paired `ck_geometry_supersede` makes a half-closed version — closed, with no
successor — unrepresentable.

### Closing a version has exactly one legal statement order

Three constraints interlock, and only one sequence satisfies all three:

1. Generate the successor's `geometry_id` in the writer, before touching the database.
2. `UPDATE` v1, setting `version_valid_to` **and** `superseded_by` in the same statement.
3. `INSERT` v2 with that pre-generated `geometry_id`, open-ended.
4. Repoint every `geo.features` row that cited v1 (see the next subsection).
5. `COMMIT`, which is where the deferred self-FK is finally validated.

Every other order fails, and the failures are not obvious from reading the DDL.
Inserting v2 first violates `uq_geometry_current`, because v1 is still open — and that
is a partial **index**, not a constraint, so it can never be deferred. Closing v1
without naming a successor violates `ck_geometry_supersede`, and a `CHECK` in PostgreSQL
is always immediate. Doing both halves inside one data-modifying CTE still violates
`uq_geometry_current`, because the sub-statements of a CTE cannot see each other's
effects. That leaves step 2 first, which is why `superseded_by` is declared
`DEFERRABLE INITIALLY DEFERRED` in `drizzle/0008_geometry_dimension.sql` — without it,
step 2 fails its FK at end of statement and the dimension can only ever hold v1 rows.

Do not work around this by pointing a row at itself for a moment. Nothing forbids
`superseded_by = geometry_id`, so a failure between that write and the repoint leaves a
self-superseding row that satisfies every constraint and is wrong. `ON DELETE RESTRICT`
is unaffected by the deferral: PostgreSQL checks `RESTRICT` immediately even on a
deferrable constraint, which is the behaviour this table wants anyway.

### `geo.features.geometry_id` is current state and must be repointed on version close

`geo.features` is refreshed in place; it holds what is true now, not history. Its
`geometry_id` therefore always points at the **current** version, and whatever closes
a version is responsible for repointing every feature that referenced the closed one
in the same transaction. `scripts/backfill-geometry.sql` establishes the invariant
once; keeping it is the ingest path's job. Rendering a *feature* layer at a past date
consequently needs the as-of predicate
(`version_valid_from <= $ts AND (version_valid_to IS NULL OR version_valid_to > $ts)`),
which is one predicate used in one place, not a per-layer branch. `geo.geometry_current`
is the convenience view for the common case.

`geo.features.geom` stays denormalised. The dimension is the identity authority, not
the render source: Martin's four function sources still select `f.geom` and need no
edit for this migration.

### Change detection: never compare geometry floats

Summary of the rule; the full per-producer table is plan §2.0. In priority order:
version on the producer's own revision signal where one exists (WFIGS
`polygonDateTime`, USDM `valid_date`, MTBS release identifier); **never** version a
producer whose location is part of its identity (FIRMS detections, weather grid
points, stream gauges — a "moved" record there is a different record); and only as a
last resort, where neither applies, `NOT ST_Equals(old, new)` **and** a
per-producer Hausdorff tolerance, both conditions, never either alone.

The reason is measured, not theoretical. The refresh-in-place diff already excludes
`geometry`/`geometry_repaired` because the `drizzle/0004` trigger rewrites
`properties.geometry` through `ST_AsGeoJSON` on every write, so a whole-payload
compare rewrites every row every run. Under Type-2 the same mechanism has a worse
consequence: one new version per feature per run, forever, silently, while the map
keeps rendering correctly.

A circuit breaker is mandatory: if a single run would open new versions for more than
a stated fraction of a layer, it aborts, writes nothing and exits non-zero. **The
threshold is still TBD and belongs to the ingest modules, not here** — 5 % of the
~110-row `fire-perimeters` layer is 6 rows, which is probably too tight to be useful.
This table's contribution is only to make the question cheap to ask:
`ix_geometry_asof` on `(natural_key, version_valid_from DESC)` answers "how many
versions opened in this window" without a scan.

`version_valid_from` deliberately carries **no** not-write-time CHECK. A guard such
as `version_valid_from < now() - interval '60 seconds'` would reject legitimate
near-real-time observations from FIRMS and Open-Meteo. The equivalent assertion
belongs in the batch runner, next to the one that already rejects a `source_release`
whose `data_available_at` is within 60 s of `now()`.

### Drizzle migrates before Alembic on a fresh database

`geo.geometry` is Drizzle-owned on purpose: `preDeployCommand` runs
`scripts/migrate.mjs` automatically on every Railway deploy, while Alembic is run by
hand. If the dimension were Alembic-owned, a fresh environment would deploy a map
with no geometry table. The consequence, once `agri.*` columns carry a cross-schema
FK to `geo.geometry`, is that **Drizzle must run first on a fresh database** — an
ordering that is invisible until someone builds a new environment. The Alembic
revision that adds the FK opens with `SELECT to_regclass('geo.geometry')` and raises
a readable error rather than failing on a bare FK violation. Cross-schema FKs already
exist in the other direction: `geo.layers.team_id → public.teams.id`.

The other half of this coupling: a Drizzle migration must update
`migration-contract.ts` in the **same commit**. `/api/ready` matches both `created_at`
and `hash` against `drizzle.__drizzle_migrations`, and the Railway healthcheck kills
a release whose contract is stale. Two further mechanics that are easy to get wrong —
the journal's `when` must be strictly **greater** than the previous entry's, because
`drizzle-orm`'s migrator applies only entries whose `when` exceeds the newest
`created_at` already recorded; and the recorded hash is the SHA-256 of the `.sql`
file's bytes, so it has to be recomputed after the last edit to that file, not before.

### Version-sensitive details

Production is PostgreSQL 18.4 with PostGIS 3.6; the local rehearsal container is
PostgreSQL 16. Two things depend on that floor. `scripts/backfill-geometry.sql` uses
`pg_input_is_valid(text, 'timestamptz')` for its null-safe timestamp cast, which is
PostgreSQL 16 or newer. The backfill also runs at `REPEATABLE READ` so that the census
it asserts against is a true baseline while ingestion keeps running on its cron.

`REPEATABLE READ` on its own is **not** sufficient there, and an earlier version of that
script claimed it was. The isolation level converts a write-write collision into a
serialization failure, but rows another transaction `INSERT`s after the snapshot are
simply invisible — no error is raised. The census, the insert, the repoint and the
closing `unresolved_eligible` assertion all read that one snapshot, so an hourly ingest
landing mid-run would be missed by all four and the script would print
`all assertions passed` over features left holding a null `geometry_id`. It therefore
also takes `LOCK TABLE geo.features, geo.geometry IN SHARE MODE`, which blocks writers
while still allowing readers, under a `lock_timeout`. The lock has to come first, and it
can: `SET` and `LOCK TABLE` are the two utility statements that take no snapshot, so the
`REPEATABLE READ` snapshot is not established until the first statement after them, by
which point the lock is already held.

One naming hazard worth knowing about. This table is called `geometry`, so `geo` now
contains a composite type of that name, and any session whose `search_path` puts
`geo` ahead of `public` will resolve an unqualified `geometry` type reference to it
rather than to PostGIS's type. The migration writes `public.geometry(...)`
throughout for that reason. Production connects as `postgres`, and no `postgres`
schema exists, so its effective `search_path` is `public` and nothing is shadowed —
but `docker-compose.yml` connects as `geo`, where `"$user"` does resolve, and the
`geo.*` plpgsql functions declare locals as bare `geometry`.

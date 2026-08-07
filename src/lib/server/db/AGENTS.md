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

## §tile-observation-day

The day a baked tile layer is filtered on. Files: `drizzle/0015_tile_observation_day.sql`
(`geo.feature_observation_day` and the four Martin function sources that emit it),
`src/lib/server/services/environmental-read-model.ts` (`PUBLISHER_NAMED_DAY_RULE`,
`OBSERVATION_DAY`, `resolveRequestedObservationDay`), and the agreement test
`src/__tests__/lib/observation-day-contract.test.ts`.

### The day is an attribute, not a request parameter

`fire-perimeters`, `evacuation-zones`, `burn-severity` and `sensors` are Martin **function
sources baked into the map style**, not React-mounted readers. Nothing in that path had ever
taken a date: each function selected every published row in the tile envelope, so scrubbing
the slider to 2023 left August-2026 fire perimeters and evacuation zones on the map with
nothing saying so. Those four layers hold 388 + 119 + 478 + 15,769 published rows in
production, and every one of them was drawn at every date on a four-year axis.

Two alternatives were rejected:

- **A `query json` parameter per function, filtered server-side.** Postgres cannot add a
  parameter with `CREATE OR REPLACE`, so that is a `DROP` + `CREATE` on four live functions
  plus a Martin restart to re-read the catalog — and a missing function 404s the **whole**
  composite: `martin-dynamic` carries six sources in one MapLibre source, so one bad
  signature blanks every dynamic layer on the map.
- **Splitting the composite** so each layer carries its own dated URL. Same blast radius, and
  Martin cannot emit `vector_layers` for function sources (see `src/lib/map/sources.ts`).

Emitting the day keeps every signature identical, so the migration is `CREATE OR REPLACE`
with no catalog change and no restart; Martin's own `tile_expiry: 5m` ages cached tiles out.
The client then filters with a MapLibre expression, which means scrubbing **re-filters
already-downloaded tiles instead of refetching them** — a day-granular scrub over these
layers costs zero requests. At this row count, shipping every feature and filtering in the
browser is the cheaper design, not a compromise. The attribute is emitted `::text` so a
MapLibre expression can compare it lexicographically: ISO-8601 dates sort correctly as
strings, so `["<=", ["get","observed_day"], "2024-03-01"]` is exactly the "as the record
stood on that day" test, with no date parsing in the style.

### One rule, and it is the publisher-named day

The tile filter and the slider's axis MUST bucket a feature onto the same calendar day. If
they disagree the slider advertises a day as published and the tiles draw nothing for it,
which renders as a bug in the map rather than as a gap in the data — the hardest class to
diagnose. Agreement means the SAME rule, not merely the same source keys: both sides read
`COALESCE(observedAt, updatedAt, polygonDateTime)` and take the **stored ISO string's own
first ten characters**.

Never an instant-based conversion. Measured against production, 6,279 of the 16,743
water-gauge rows carry a `-07:00` offset and bucket to the day **after** the one they name
once converted: `2026-08-03T23:50:00.000-07:00` got axis day 2026-08-03 and tile day
2026-08-04, so `observed_day <= '2026-08-03'` hid the very feature the slider advertised.
`PUBLISHER_NAMED_DAY_RULE.forbiddenInstantConversions` names the two spellings, and the
contract test fails if either side grows one.

### `IMMUTABLE` here is a deliberate promotion over two STABLE callees

`geo.feature_observation_day` is declared `IMMUTABLE` and its body calls `to_date(text, text)`
and `pg_input_is_valid(text, text)`, both of which PostgreSQL catalogues as **STABLE**
(`SELECT provolatile FROM pg_proc WHERE proname IN ('to_date','pg_input_is_valid')` returns
`s` for both, verified on 16.9). That is not an oversight, and it is not a misdeclaration:

- Both are catalogued STABLE because the general case reads session state — `DateStyle`
  decides whether `03-05-2026` is March or May. This function pins a **fixed `'YYYY-MM-DD'`
  pattern over a `^\d{4}-\d{2}-\d{2}$`-shaped input**, where the four-digit year is
  unambiguous, so neither callee's result can move with the session. Verified: under
  `SET datestyle='DMY'` the guard and the parse return the same values as under ISO.
- The promotion is **required for the intended use**. PostgreSQL checks the *declared*
  volatility of the top-level function in an index expression, so
  `CREATE INDEX ... (geo.feature_observation_day(properties))` is accepted, while the same
  logic inlined — `CREATE INDEX ... (to_date(substring(properties->>'observedAt',1,10),
  'YYYY-MM-DD'))` — is rejected with `functions in index expression must be marked
  IMMUTABLE`. Both halves were run on 16.9. The wrapper is what buys the future index.

What must **not** be done is reach the same declaration through a cast. `text::timestamptz`
and `text::date` route through input functions that genuinely read `TimeZone` and `DateStyle`
for these inputs, so `IMMUTABLE` over a cast would be a lie *and* would re-bucket the 37.5%
of gauge rows above.

### The guard fails closed, because one raise blanks a whole tile

A shape check alone is not enough: `^\d{4}-\d{2}-\d{2}$` admits `2026-02-31`, and
`to_date('2026-02-31','YYYY-MM-DD')` **raises** `date/time field value out of range` on
PostgreSQL 16 and later. A raise inside `ST_AsMVT` fails the entire tile — every feature in
it, not just the bad row — which is precisely the failure the guard exists to prevent, and
the failure mode 0012 called out for bare numeric casts.

So the guard is two conditions, and `to_date` never sees a day that does not exist:
the anchored regex proves the **shape**, and `pg_input_is_valid(day, 'date')` proves the day
**exists** (false for `2026-02-31`, `2026-13-01` and `2023-02-29`; true for `2024-02-29`).
`pg_input_is_valid` is non-raising by construction — it is the soft-error probe, PostgreSQL
16+, already relied on by `scripts/backfill-geometry.sql` — so no `EXCEPTION` block and no
per-row subtransaction is needed, which matters on a 15,769-row layer. A `plpgsql` wrapper
with `EXCEPTION WHEN others THEN NULL` would work but costs a subtransaction per row and
swallows unrelated errors.

This is the SQL twin of `resolveRequestedObservationDay`, which pairs
`CALENDAR_DATE_PATTERN` with `Number.isNaN(Date.parse(...))` for the same reason: a
well-shaped impossible day must resolve to "nothing is observed here", never to a raise and
never to a neighbouring day. A row that cannot be dated yields NULL and is treated as undated
by the client filter, which shows it at every date rather than hiding it.

## §soil-field-view

The ERA5-Land serving surface for soil moisture **and** soil temperature. Files:
`drizzle/0016_soil_field.sql` (`geo.soil_field_observation`, `geo.soil_field`), read by
`getPublishedSoilField` in
`src/lib/server/services/environmental-read-model.ts`. Supersedes 0014's
`geo.soil_moisture_observation` / `geo.soil_moisture_field`, which covered moisture only.

### The reviewed (signal, measure, unit) triples are stated exactly once

The view's row gate, its `measure` label and its accepted unit all come from **one** joined
`VALUES` list, `governed(signal_name, measure, normalized_unit)`. They used to be two
enumerations — a `CASE` for the label and an `OR`-of-`IN` for the `WHERE` — which meant
widening one without the other served rows with `measure` NULL, and the reader has no way to
tell that apart from a genuinely unlabelled measurement. Joining the list makes the three
structurally unable to disagree: widening the list is the whole edit, and there is no second
place to forget.

The list is enumerated rather than prefix-matched on purpose. A signal arriving in an
unexpected unit — Kelvin instead of Celsius — matches nothing and is invisible here, rather
than served beside comparable values and coloured as if it were one of them. A signal absent
from the list is likewise absent: this same ERA5-Land lane also carries
`vapour_pressure_deficit`, so a fallback `ELSE 'temperature'` would hand the first person who
widens the gate a silently mislabelled signal drawn on the temperature ramp. `is_observed`
and `quality_flag = 'accepted'` are applied HERE rather than at each call site, so no reader
can serve a rejected or imputed value as a measurement.

Verified against a stand-in schema on 16.9: the joined form returns exactly the row set the
double-enumerated form did (3 moisture + 4 temperature signals kept; wrong-unit,
unenumerated, unobserved, rejected and null-valued rows dropped), with no fan-out and with
`measure` never NULL.

### The function is renamed, the view is replaced

`geo.soil_moisture_field(...)` was already measure-agnostic — it takes `target_signal` and
`target_support_key` as parameters and reads `agri.signal_observation` directly, so it
aggregates temperature correctly with no change to its body. It is **renamed**, never
rewritten: `ALTER FUNCTION … RENAME TO` preserves the body byte-for-byte, whereas a
`DROP` + `CREATE` would fork the Gaussian-blur definition into a second copy that could drift
from the one 0014 reviewed. The rename is wrapped in a `DO` block that checks `pg_proc`
first, so a database already carrying the new name is a no-op rather than an error.

The view, by contrast, gains a column (`measure`) and changes name, and PostgreSQL permits
neither with `CREATE OR REPLACE` — hence `DROP VIEW` + `CREATE VIEW`. Nothing but
`environmental-read-model.ts` read the old view (grep across `src/`, `docs/`, `infra/`), so
the drop is safe.

### Still not a tile function, and still no new index

`infra/martin/martin.yaml` sets `auto_publish: false` and names its function sources
explicitly, so nothing here can join a composite source id and no tile server needs
restarting — the restart hazard is `geo.*_tiles` functions only.

No index is added, for 0014's reason: the bbox resolves the cell list first, and the day is
then one index search per cell on the existing
`ix_signal_observation_cell_time_signal (cell_id, observed_at, signal_name)`. That index is
keyed on `signal_name` without regard to *which* signal, so the temperature signals ride it
exactly as the moisture ones do. Building an index here would lock a table the live backfill
is writing to, for no measured gain.

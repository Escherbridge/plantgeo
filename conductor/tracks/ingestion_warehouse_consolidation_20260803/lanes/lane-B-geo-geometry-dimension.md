---
type: lane-brief
track: ingestion_warehouse_consolidation_20260803
lane: B
status: in-progress
depends_on: A
started_at: 2026-08-03
---

# Lane B — `geo.geometry` Type-2 dimension + backfill (Drizzle)

Phase 3's Drizzle half. Governing detail:
[`plans/ingestion-warehouse-consolidation-2026-08-03.md`](../../../../plans/ingestion-warehouse-consolidation-2026-08-03.md)
§2.0 (DDL sketch, change-detection rule, backfill) and §8 risks 1, 1b, 2c, 5, 7b, 8.
Settled decisions D1 and D6: [`../spec.md`](../spec.md). Shared rules for every lane:
[`README.md`](./README.md) — **read it before this file**.

## 1. Goal

When this lane is done, `geo.geometry` exists as the one conformed, Type-2 geometry
dimension in the `plantgeo` database, created by a hand-authored Drizzle migration whose
hash is recorded in `migration-contract.ts` in the same commit. Every existing
`geo.features` row that has a natural key and a geometry has been backfilled as a v1
(`version_valid_to IS NULL`) row, dated by its producer's own observation timestamp and
never by `created_at` or `now()`, inside a single transaction whose assertion compares
against a census captured in that same transaction. `geo.features` carries a nullable
`geometry_id` pointing at the **current** version of each place. `geo.geometry_current`
exists as the one-line convenience view. No Martin tile function, no Alembic revision and
no ingest module has been touched — lanes C, D and I own those.

## 2. Prerequisites

| # | Must have landed | Verify | Expected |
|---|---|---|---|
| 1 | **Lane A** — the identity contract. You need its exact `natural_key` string format and its producer namespace tokens; you do not need its code. | `Select-String -Path services/agri-data-service/src/agri_data_service/ingest/identity.py -Pattern 'PRODUCER_BY_LAYER_NAME','def natural_key','class FeatureIdentity'` | three matches. Fewer than three, or a missing file, means lane A has not landed — **stop**, it is a hard blocking prerequisite (`README.md` §"Wave plan", wave 0). |
| 2 | Drizzle head is still `0007`. | `Get-Content src/lib/server/db/migration-contract.ts` | `tag: "0007_governed_environmental_ingestion"`, `createdAt: 1_785_900_000_000`. Verified at `src/lib/server/db/migration-contract.ts:1-5`. If it reads `0008`, another lane already added a migration — stop and report. |
| 3 | A local `plantgeo` PostGIS to rehearse against (containers are all stopped). | `podman compose up -d postgis` then `& "C:\Program Files\PostgreSQL\16\bin\psql.exe" -c "SHOW server_version" "postgresql://geo:$env:POSTGRES_PASSWORD@127.0.0.1:5434/plantgeo"` | A version string. **`podman`, not `docker`** — `npm run docker:up` is `podman compose up -d` (`package.json`). The compose port is `${POSTGRES_HOST_PORT:-5432}` (`docker-compose.yml:12`), so **confirm the port before trusting it** — host mappings on this machine are crossed (`README.md` §"Environment"). |

**Phase 2 (Alembic `0018`) is not a prerequisite for this lane.** It is queued for owner
apply (`../plan.md:24`) and touches only `agri`. Lane C is what waits on both.

## 3. Files you own

Exactly this list, from `README.md` §"File boundaries", lane B row:

- `src/lib/server/db/schema.ts`
- `src/lib/server/db/migration-contract.ts`
- `src/lib/server/db/AGENTS.md` (new — see step 9)
- `drizzle/**`
- `scripts/backfill-geometry.*`
- `src/__tests__/lib/geometry-migration.test.ts` (new — see step 10)

**Other sessions are running concurrently. Do not touch anything outside this list.**
Specifically forbidden for lane B: `services/agri-data-service/**` (lanes A, C, D, E, I),
`src/lib/server/services/ingestion-jobs.ts` (lane D), and `scripts/raster/**` +
`scripts/deploy-pmtiles.sh` (lane F — `scripts/` is *not* yours wholesale, only
`scripts/backfill-geometry.*`). `src/__tests__/` is shared: you own exactly the one new
file named above; `src/__tests__/stores/**` and `src/__tests__/components/**` are lane G's
and the two ingestion test files are lane D's deletions. If you need a change outside your
list, stop and report it rather than reaching across.

Two consequences worth stating plainly:

- The Drizzle-before-Alembic note the plan asks for in
  `services/agri-data-service/db/AGENTS.md` (plan `:240`, `:756`) is **lane C's**, not
  yours — that path is inside lane C's tree. Put your half of the rationale in
  `src/lib/server/db/AGENTS.md` and say so in your handoff.
- Repointing `agri.signal_observation.cell_id`, `agri.forecast_series.spatial_cell_id`
  and `agri.cell_source_crosswalk.cell_id` is **lane C**. You create the target; you do
  not create the FKs pointing at it.

## 4. The work

### Step 1 — Lift the key format from lane A, do not re-derive it

Open `services/agri-data-service/src/agri_data_service/ingest/identity.py` (read-only)
and copy, verbatim into a comment in your migration SQL:

1. the exact `natural_key` template, and
2. the producer token for each of the four live layers.

This is the single hazard the wave plan exists to manage (`README.md` §"The one real
hazard"). Under
Type-2 a mismatched key does not duplicate a row — it **interleaves two producers into
one version chain**, fabricating a plausible history (plan `:785`, risk 1, Critical).

> **The plan's backfill sketch uses `l.name` as the namespace** (plan `:266`, `:270`).
> That is a placeholder. Layer names are `fire-detections`, `water-gauges`,
> `weather-observations`, `fire-perimeters` (`src/lib/server/services/ingestion-jobs.ts:15-18`,
> resolved by name at `src/lib/server/services/ingest.ts:17-33`). If lane A's producer
> token is `firms` rather than `fire-detections`, **lane A wins** and you map layer name →
> producer token explicitly in the backfill. Write the mapping as a literal `VALUES` list
> in the backfill SQL so it is reviewable, not as a string transform.

### Step 2 — Add the table to `src/lib/server/db/schema.ts`

The spatial `customType` helpers already exist and are the ones to reuse:
`spatialGeometry` → `geometry(GEOMETRY,4326)` (`schema.ts:24-26`) and `spatialPoint` →
`geometry(POINT,4326)` (`schema.ts:28-30`).

House pattern in this file: columns and indexes are declared in TypeScript; **CHECK
constraints live only in the hand-written `.sql`** — `geo.drought_areas` has three CHECKs
in `drizzle/0007_governed_environmental_ingestion.sql:16-17` and none in its schema.ts
declaration (`schema.ts:300-319`). Follow that. Partial unique indexes *are* expressible
in TypeScript — `features_layer_external_id_unique` uses `.where(sql\`…\`)`
(`schema.ts:180-183`) — so declare those in both places.

```ts
/** Type-2 conformed geometry dimension: one row per version of a place. See `src/lib/server/db/AGENTS.md` §geometry-dimension. */
export const geometry = geoSchema.table(
  "geometry",
  {
    geometryId: uuid("geometry_id").defaultRandom().primaryKey(),
    naturalKey: varchar("natural_key", { length: 255 }).notNull(),
    versionValidFrom: timestamp("version_valid_from", { withTimezone: true }).notNull(),
    versionValidTo: timestamp("version_valid_to", { withTimezone: true }),
    geomKind: varchar("geom_kind", { length: 16 }).notNull(),
    geom: spatialGeometry("geom").notNull(),
    centroid: spatialPoint("centroid").notNull(),
    gridName: varchar("grid_name", { length: 100 }),
    cellKey: varchar("cell_key", { length: 180 }),
    resolutionMeters: integer("resolution_m"),
    producer: varchar("producer", { length: 100 }).notNull(),
    supersededBy: uuid("superseded_by"),
    lastConfirmedAt: timestamp("last_confirmed_at", { withTimezone: true })
      .notNull()
      .defaultNow(),
  },
  (table) => [ /* uq_geometry_version, uq_geometry_current, uq_geometry_grid_cell, the four read indexes */ ]
);
```

`supersededBy` is a self-reference; declare it without `.references()` and add the FK in
the SQL, mirroring the forward-reference note already in this file at `schema.ts:54-56`.

### Step 3 — Hand-author `drizzle/0008_geometry_dimension.sql`

**Do not run `npm run db:generate` / `drizzle-kit generate`.** `drizzle/meta/` holds
snapshots for `0000`–`0004` and `0006` only — there is no `0005_snapshot.json` and no
`0007_snapshot.json`. `0005` and `0007` were hand-authored (`0007` landed in `208d056`
with commentary and `IF NOT EXISTS` throughout, `drizzle/0007_governed_environmental_ingestion.sql:1-30`).
Generating would diff against a stale snapshot and emit a destructive migration.

Write the DDL from plan §2.0 (`plans/…:125-162`) with these deltas, each load-bearing:

| Delta | Why |
|---|---|
| `centroid` is a **plain** `geometry(Point,4326) NOT NULL`, written at insert | A stored generated column calling a PostGIS function is exactly what made the warehouse unrestorable from its own `pg_dump` (`services/agri-data-service/plans/checksum-layer-audit-2026-08-03.md:176-186`). Two are being deleted this quarter. Do not add a third. |
| `ck_geometry_kind` allows `'line'`, and the backfill maps to it with an explicit `CASE`, not `replace(GeometryType(...), 'MULTI', '')` | The plan's sketch (`:269`) produces `'linestring'` for a LINESTRING/MULTILINESTRING, which the CHECK (`:146`) rejects. Latent today (all four live layers are points and polygons), a hard failure the first time a line source lands. |
| Add `ck_geometry_natural_key_namespaced CHECK (natural_key LIKE producer \|\| ':%')` | Risk 1 asks for "a standing assertion that every `natural_key` in a version chain shares one `producer`". A single-row CHECK on the prefix makes cross-producer interleaving structurally unrepresentable, which is cheaper and stronger than a periodic query. |

Everything else is the sketch verbatim. The parts that are not negotiable:

```sql
-- identity: natural_key is the PLACE across time; geometry_id is ONE VERSION.
CONSTRAINT uq_geometry_version UNIQUE (natural_key, version_valid_from),
CONSTRAINT ck_geometry_version_order CHECK (
  version_valid_to IS NULL OR version_valid_to > version_valid_from),
-- a half-closed version (closed, no successor) is unrepresentable
CONSTRAINT ck_geometry_supersede CHECK (
  (version_valid_to IS NULL AND superseded_by IS NULL)
  OR (version_valid_to IS NOT NULL AND superseded_by IS NOT NULL)),
CONSTRAINT ck_geometry_cell_fields CHECK (
  (geom_kind <> 'grid_cell' AND grid_name IS NULL AND cell_key IS NULL AND resolution_m IS NULL)
  OR (geom_kind = 'grid_cell' AND grid_name IS NOT NULL AND cell_key IS NOT NULL AND resolution_m > 0));

-- exactly one current version per place
CREATE UNIQUE INDEX uq_geometry_current
  ON geo.geometry (natural_key) WHERE version_valid_to IS NULL;
CREATE UNIQUE INDEX uq_geometry_grid_cell
  ON geo.geometry (grid_name, cell_key) WHERE version_valid_to IS NULL;
```

**There is no `first_seen_at` column and you must not add one.** `min(version_valid_from)`
over a `natural_key` *is* first-seen by construction, and it cannot be silently rewritten
because closing a version writes `version_valid_to`, never `version_valid_from`
(plan `:213`).

Also in this migration:

```sql
CREATE VIEW geo.geometry_current AS
  SELECT * FROM geo.geometry WHERE version_valid_to IS NULL;

-- geo.features holds CURRENT state (refreshed in place), so this points at the
-- current version and must be repointed whenever a version closes.
ALTER TABLE geo.features
  ADD COLUMN IF NOT EXISTS geometry_id uuid
    REFERENCES geo.geometry(geometry_id) ON DELETE RESTRICT;
```

`ON DELETE RESTRICT`, never `CASCADE`, on every fact FK into this table: a fact pins one
*version* permanently, so dimension rows are never deleted. Prune facts, never geometry
(plan `:227`, `:312-314`).

### Step 4 — Journal entry

Append to `drizzle/meta/_journal.json` by hand, matching the shape of the existing
entries (`drizzle/meta/_journal.json`, entry idx 7):

```json
{ "idx": 8, "version": "7", "when": <epoch-ms>, "breakpoints": true, "tag": "0008_geometry_dimension" }
```

Do not add a `0008_snapshot.json`; `0005` and `0007` have none.

### Step 5 — `migration-contract.ts`, same commit

```
tag       = "0008_geometry_dimension"
createdAt = the journal entry's `when`, exactly
sha256    = sha256 of drizzle/0008_geometry_dimension.sql
```

`/api/ready` matches `created_at` **and** `hash` against `drizzle.__drizzle_migrations`
(`src/app/api/ready/route.ts:44-49`) and Railway health-checks it, so a stale contract
kills the release. `src/__tests__/security/readiness-migration-contract.test.ts:12-30`
recomputes the digest from the file and compares the journal's last entry — it will catch
you locally.

Digest command:

```powershell
(Get-FileHash -Algorithm SHA256 drizzle/0008_geometry_dimension.sql).Hash.ToLower()
```

**Recompute it after any edit to the `.sql`.** This is the most common way this lane
breaks.

### Step 6 — Backfill: `scripts/backfill-geometry.sql`

Ship it as a `.sql` script run through `psql`, not as application code — it runs once, by
hand, against a database the owner chooses.

Structure, all three steps in **one transaction**, per plan `:247-278`:

```sql
BEGIN;

-- 1. census FIRST, in this transaction. It is the assertion's left-hand side,
--    not a preflight you run and eyeball.
CREATE TEMP TABLE backfill_census ON COMMIT DROP AS
SELECT count(*) FILTER (WHERE properties ? 'id' AND geom IS NOT NULL) AS eligible,
       count(*) FILTER (WHERE NOT (properties ? 'id'))                AS no_natural_key,
       count(*) FILTER (WHERE geom IS NULL)                           AS no_geometry,
       count(*)                                                       AS total
FROM geo.features;

-- 2. every eligible row as v1: current, open-ended.
INSERT INTO geo.geometry (natural_key, version_valid_from, version_valid_to,
                          geom_kind, geom, centroid, producer)
SELECT p.producer || ':' || (f.properties ->> 'id'),
       <first-observed expression, table below>,
       NULL,
       CASE GeometryType(f.geom)
         WHEN 'POINT' THEN 'point' WHEN 'MULTIPOINT' THEN 'point'
         WHEN 'POLYGON' THEN 'polygon' WHEN 'MULTIPOLYGON' THEN 'polygon'
         WHEN 'LINESTRING' THEN 'line' WHEN 'MULTILINESTRING' THEN 'line'
       END,
       f.geom, ST_Centroid(f.geom), p.producer
FROM geo.features f
JOIN geo.layers l ON l.id = f.layer_id
JOIN (VALUES ('fire-detections','<lane A token>'), …) AS p(layer_name, producer)
  ON p.layer_name = l.name
WHERE f.properties ? 'id' AND f.geom IS NOT NULL;

-- 3. assert against the census captured above, then COMMIT.
--    eligible == inserted; every inserted row has version_valid_to IS NULL;
--    every eligible feature resolved a geometry_id. RAISE on mismatch → ROLLBACK.
COMMIT;
```

**`version_valid_from` — read the property, do not parse the id.** All four producers
already store their observation timestamp as a `properties` key, which is both simpler and
safer than splitting the id on `:` (the gauge and weather ids embed ISO timestamps that
themselves contain colons):

| Layer | `version_valid_from` source | Verified at |
|---|---|---|
| `fire-detections` | `properties ->> 'observedAt'` | `src/lib/server/services/ingestion-jobs.ts:152` |
| `water-gauges` | `properties ->> 'updatedAt'` (spread from the gauge record) | `src/lib/server/services/ingestion-jobs.ts:189-191` |
| `weather-observations` | `properties ->> 'observedAt'` (spread from the observation) | `src/lib/server/services/ingestion-jobs.ts:294-298` |
| `fire-perimeters` | `properties ->> 'polygonDateTime'` | `src/lib/server/services/ingestion-jobs.ts:340` |
| anything else, or the key absent/unparseable | `'-infinity'::timestamptz` | plan `:287-290` |

Wrap each in a null-safe cast so a malformed value falls back to `'-infinity'` rather than
aborting the transaction.

**Never `created_at`. Never `now()`.**
`created_at` is "last touched", not "first seen" — measured, all rows read as created today
(plan `:298`, risk 2c at `:789`).
`now()` is worse: it would date every v1 to backfill day, so scrubbing the slider one day
backwards would make **every geometry on the map vanish at once** — indistinguishable from
"the layer is broken" (risk 7b, plan `:796`).

**Never hardcode a row count**, including the `15 016` written in the plan. Ingestion runs
on a cron; the four layers moved ~2.7 k rows during the drafting of that document
(plan `:292`, risk 2b at `:788`). The census is the only admissible baseline.

Rehearse the whole script against the local container, `ROLLBACK` instead of `COMMIT`, and
read the census output before you run it for real.

### Step 7 — Point `geo.features.geometry_id` at the current version

In the same backfill transaction, after step 2:

```sql
UPDATE geo.features f
SET    geometry_id = g.geometry_id
FROM   geo.geometry g
WHERE  g.natural_key = <producer> || ':' || (f.properties ->> 'id')
  AND  g.version_valid_to IS NULL;
```

Then assert `count(*) FILTER (WHERE geometry_id IS NULL)` equals
`census.total - census.eligible`. Record in `AGENTS.md` that this column tracks **current**
state and must be repointed whenever a version closes — that repoint is lane D's ingest
work, not yours, but the invariant is documented here.

### Step 8 — Confirm Martin needs nothing, and write that down

**Existing MVT tile functions require zero edits in this lane or any other.** Verified:
Martin publishes exactly four function sources — `fire_risk_tiles`, `sensor_tiles`,
`intervention_tiles`, `building_tiles` (`infra/martin/martin.yaml:27-39`) — plus two table
sources. All four read `FROM geo.features f JOIN geo.layers l` and select `f.geom`
(`drizzle/0001_handy_riptide.sql:376,398`, `:412,434`, `:448,471`, `:485`; the
`intervention_tiles` redefinition at `drizzle/0005_intervention_priority_tiles.sql:6,30`).
`geo.features.geom` stays denormalised; the dimension is the identity authority, not the
render source (plan `:364`).

Do not go looking for tile functions to change. The slider layer that *does* need a date
parameter is served as GeoJSON through tRPC, not MVT, and that is lane J (plan `:365`).

> The prompt for this lane cited `infra/martin/martin.yaml:29-40`. **Correction:** the
> function-source block is `infra/martin/martin.yaml:27-39`.

### Step 9 — `src/lib/server/db/AGENTS.md` (new)

`src/lib/server/AGENTS.md:1` exists and is organised as `## §section` blocks
(`§regional-intelligence`, `§drought-ingestion`, `§soil-evidence`, `§vegetation-tiles`).
There is no `src/lib/server/db/AGENTS.md` yet — create one, same style, with a
`§geometry-dimension` section carrying the rationale that must not live in code comments:

- why Type-2 and what `natural_key` vs `geometry_id` mean;
- why `centroid` is plain and must never become `GENERATED … STORED`;
- why every fact FK is `ON DELETE RESTRICT` and dimension rows are never deleted;
- why `geo.features.geometry_id` is current-state and must be repointed on version close;
- the change-detection rule summary and a pointer to plan §2.0 for the full table;
- **Drizzle migrates before Alembic on a fresh database**, because `agri.* → geo.geometry`
  is a cross-schema FK and `preDeployCommand` runs Drizzle automatically while Alembic is
  manual (risk 5, plan `:793`). Precedent for cross-schema FKs already exists in the other
  direction: `geo.layers.team_id → public.teams.id` (`src/lib/server/db/schema.ts:159`).

Add the one-line pointer comment in `schema.ts` (step 2 shows it). Keep code comments to
one line; rationale goes here.

### Step 10 — Test

Write **exactly one new file**: `src/__tests__/lib/geometry-migration.test.ts`. That path
is your whole claim on `src/__tests__/` — do not add a second, and do not edit any existing
test there (`src/__tests__/stores/**` and `src/__tests__/components/**` are lane G's;
`src/__tests__/api/cron-ingest.test.ts` and `src/__tests__/services/ingestion-jobs.test.ts`
are lane D's to delete). Assert, without a database:

1. `drizzle/0008_geometry_dimension.sql` contains no `GENERATED ALWAYS` and no
   `ON DELETE CASCADE` — a cheap regex guard against the two mistakes that are expensive
   to discover later.

Do **not** duplicate the digest/journal assertion: the existing
`src/__tests__/security/readiness-migration-contract.test.ts:12-30` already reads
`drizzle/${EXPECTED_DRIZZLE_MIGRATION.tag}.sql`, recomputes its sha256 and compares the
journal's last entry generically. Confirm it passes; leave it alone.

## 5. Traps specific to this lane

| # | Trap | Where |
|---|---|---|
| 1 | **`.env.local` points `DATABASE_URL` at production** (`switchback.proxy.rlwy.net:37967/plantgeo`, `.env.local:11`), and `drizzle.config.ts:8` reads `process.env.DATABASE_URL`. `npm run db:migrate` or `node scripts/migrate.mjs` picked up from a shell that loaded it goes straight at prod. Set `$env:DATABASE_URL` explicitly in the *same* PowerShell statement as every command; `VAR=x cmd` does not work in PowerShell. | `.env.local:11`, `drizzle.config.ts:1-10` |
| 2 | **Do not run `drizzle-kit generate`.** Snapshots stop at `0006`; `0005` and `0007` are hand-authored with no snapshot. Generating diffs against `0004`/`0006` and emits destructive DDL. | `drizzle/meta/` |
| 3 | **The `geom_kind` expression in the plan's sketch is wrong for lines.** `lower(replace(GeometryType(g),'MULTI',''))` yields `'linestring'`, which `ck_geometry_kind` rejects. Use an explicit `CASE`. | plan `:146`, `:269` |
| 4 | **`natural_key` no longer carries a bare global UNIQUE.** Its meaning changed from "this row is unique" to "these rows are the same place over time". An unnamespaced collision interleaves two producers into one version chain — a fabricated history, strictly harder to detect than a duplicate. Namespace is a *correctness* requirement now, not hygiene. | plan `:785` (risk 1, Critical) |
| 5 | **Do not compare geometry floats anywhere.** Not in the backfill, not in any helper you leave behind. The codebase already learned this: the refresh-in-place diff deliberately excludes `geometry`/`geometry_repaired` because the `0004` trigger rewrites `properties.geometry` through `ST_AsGeoJSON` on every write, so a whole-payload compare rewrites every row every run. Same mechanism, worse consequence under Type-2 — unbounded silent version growth (risk 1b, Critical). | `src/lib/server/services/ingest.ts:100-118`, `drizzle/0004_repair_ingested_geometries.sql:45-51`, plan `:786` |
| 6 | **`properties ->> 'id'` is unique only *within a layer*** — the only DB-side guard is a partial unique index on `(layer_id, properties->>'id')`. There is no global uniqueness to inherit. | `src/lib/server/db/schema.ts:180-183` |
| 7 | **Assume a governed constraint does not work until you have watched it reject the bad case.** Two constraints in this repo silently covered nothing while passing every format check. Before committing, `INSERT` a deliberately half-closed version (`version_valid_to` set, `superseded_by` NULL) and confirm `ck_geometry_supersede` rejects it. Do the same for the namespace CHECK. | `../plan.md:66-82`, `README.md` §"Rules every lane inherits" |
| 8 | **`geo.features.created_at` is not first-seen** — and the mechanism is *not* what the plan says. The refresh `UPDATE` sets `updatedAt`, not `createdAt` (`src/lib/server/services/ingest.ts:109-119`), and nothing in `drizzle/**` writes `created_at` on `geo.features`. The measurement (all rows dated today) stands; the explanation does not. Treat the column as untrusted for any temporal purpose and do not try to "fix" it in this lane. | see Open questions #1 |

## 6. Definition of done

Run once, at the end — not test→fix→test.

```powershell
# 1. contract + guard tests, type-check, lint, data-boundary
npm run test
npm run type-check
npm run lint
npm run check:data-boundary
```

Proof: `readiness-migration-contract` passes (it recomputes the sha256 of
`drizzle/0008_geometry_dimension.sql` and compares the journal's last entry —
`src/__tests__/security/readiness-migration-contract.test.ts:12-30`); `tsc` exits 0; eslint
reports 0 errors.

```powershell
# 2. migration applies to a clean local plantgeo, and the backfill is idempotent
$env:DATABASE_URL = "postgresql://geo:$env:POSTGRES_PASSWORD@127.0.0.1:5434/plantgeo"
node scripts/migrate.mjs
& "C:\Program Files\PostgreSQL\16\bin\psql.exe" -v ON_ERROR_STOP=1 -f scripts/backfill-geometry.sql $env:DATABASE_URL
```

Proof: `migrate: drizzle migrations are up to date` (`scripts/migrate.mjs:36`), and the
backfill prints its census and commits without raising.

```sql
-- 3. invariants, run against the rehearsal database
SELECT count(*) FILTER (WHERE version_valid_to IS NULL) AS current_versions,
       count(*)                                          AS total_versions
FROM geo.geometry;                       -- must be equal: every backfilled row is v1

SELECT count(*) FROM geo.geometry g
WHERE g.natural_key NOT LIKE g.producer || ':%';          -- must be 0

SELECT count(DISTINCT producer) FROM geo.geometry
GROUP BY natural_key HAVING count(DISTINCT producer) > 1; -- must return no rows

SELECT count(*) FROM geo.features
WHERE properties ? 'id' AND geom IS NOT NULL AND geometry_id IS NULL;  -- must be 0

-- the constraint actually rejects the bad case (expect an ERROR, not a row)
INSERT INTO geo.geometry (natural_key, version_valid_from, version_valid_to, geom_kind,
                          geom, centroid, producer)
VALUES ('probe:1', '-infinity', now(), 'point',
        ST_SetSRID(ST_MakePoint(0,0),4326), ST_SetSRID(ST_MakePoint(0,0),4326), 'probe');
-- expected: new row for relation "geometry" violates check constraint "ck_geometry_supersede"

-- Martin still serves: unchanged tile functions still resolve
SELECT geo.fire_risk_tiles(6, 10, 22) IS NOT NULL;
```

```powershell
# 4. re-running the backfill inserts nothing new (idempotence, no literal counts)
```
Capture `count(*) FROM geo.geometry` immediately before and after a second run in the same
session; the delta must be exactly 0.

Commit contents — all in one commit, and `git status --short` must show these six paths and
nothing else: `src/lib/server/db/schema.ts`, `drizzle/0008_geometry_dimension.sql`,
`drizzle/meta/_journal.json`, `src/lib/server/db/migration-contract.ts`,
`scripts/backfill-geometry.sql`, `src/lib/server/db/AGENTS.md`,
`src/__tests__/lib/geometry-migration.test.ts`.

## 7. Open questions

| # | Question | Recommendation |
|---|---|---|
| 1 | **What actually rewrites `geo.features.created_at`?** The plan attributes it to `src/lib/server/services/ingest.ts:107-122` (risk 2c, plan `:789`), but that `UPDATE` sets `updatedAt` only (`ingest.ts:109-119`) and no trigger in `drizzle/**` touches `created_at`. So either the measurement has a different cause (a delete+reinsert path, or a one-off backfill) or the rows genuinely were all created today. | **Do not investigate in this lane** — it changes nothing about what you build, because the rule ("never derive a first-observation time from `created_at`") is unaffected and is now structurally satisfied by having no `first_seen_at` column. Record the discrepancy in your handoff so the plan's risk 2c wording gets corrected, and so lane J does not inherit a false mechanism when it audits `created_at` readers before phase 6. |
| 2 | **Does `version_valid_from` need a NOT-`now()` guard in the DDL?** A future ingest module defaulting it to `now()` reproduces risk 7b one producer at a time rather than all at once. | Add `CONSTRAINT ck_geometry_valid_from_not_write_time CHECK (version_valid_from < now() - interval '60 seconds' OR version_valid_from = '-infinity')` only if you can confirm no near-real-time producer legitimately observes within 60 s. FIRMS and Open-Meteo plausibly can. **Recommendation: do not add it here.** The equivalent assertion belongs in the batch runner, where it already exists for `data_available_at` (plan `:792`, risk 4). Note it for lane D. |
| 3 | **Circuit-breaker threshold for version churn.** The rule is settled (>5 % of a layer versioning in one run aborts and exits non-zero); the number is not, and 5 % of the 110-row `fire-perimeters` layer is 6 rows (`../plan.md:235-237`). | Lane D owns the enforcement. **Your job is only to leave the dimension able to answer the question**: `ix_geometry_asof` on `(natural_key, version_valid_from DESC)` makes "how many versions opened in this window" a cheap query. Document the threshold as TBD in `AGENTS.md` and hand the number to lane D. |
| 4 | **Does any surviving `agri` table beyond `forecast_series` need `geometry_id`?** Explicitly left open (`../plan.md:238`). | Not yours to answer — lane C decides it. Do not pre-emptively widen anything to accommodate it; `geometry_id` is a plain uuid PK and any number of FKs can point at it later. |

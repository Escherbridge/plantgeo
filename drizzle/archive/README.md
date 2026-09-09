# Archived Drizzle migration chain (0000–0040)

These 40 files are **applied history and superseded source**. They are retained unedited as the
record of how the schema was built; nothing reads them at runtime, and they are not on the
migration path. `drizzle/0000_baseline.sql` replaces them.

Collapsed 2026-09-08. This mirrors what the Alembic side did on 2026-08-25
(`services/agri-data-service/alembic/archive/`), for the same reason and with the same shape:
one forward-only baseline generated from the real schema rather than a chain that no fresh
database can replay.

## Why the chain was collapsed

**1. It could not be replayed onto a fresh database — by construction.**

`drizzle-orm`'s migrator applies *every pending migration in one transaction*
(`node_modules/drizzle-orm/pg-core/dialect.js`). On a fresh database all entries are pending at
once, so there is no point at which a step can run *between* two migrations. But four steps had to:

| Gap | Out-of-band step | Why it cannot be a migration |
|---|---|---|
| before 0029 | `node scripts/apply-pre-aggregation.mjs --phase=a` | `CREATE INDEX CONCURRENTLY` raises 25001 inside a transaction |
| before 0030 | build `geo.ix_features_layer_geom` | same |
| before 0032 | `REFRESH MATERIALIZED VIEW geo.mv_feature_observation_day_axis` | 0031's matview must be populated first |
| before 0036 | `node scripts/partition-features.mjs` (8 phases) | an online table swap, and it orphans 7 matviews that must then be recreated |

`drizzle/0030`'s own header states the conclusion plainly:

> That assertion also means a FRESH database cannot replay this tree from 0000 unattended.

**2. Seven of the last ten migrations had never been applied to production.**

Measured 2026-09-08 by probing production directly, not by reading the journal:

| Migration | In production? | Evidence |
|---|---|---|
| 0030 | **no** | its `COMMENT ON INDEX` is absent (the index itself exists, built by hand) |
| 0031 | **no** | `geo.mv_feature_observation_day_axis` does not exist |
| 0032 | **no** | `geo.v_observation_day_census` does not reference the axis matview |
| 0033 | **no** | `geo.watershed_tiles` has no `target_layer_id` — it is the pre-0033 body |
| 0034 | yes | `geo.mv_signal_cell_daily` (created by 0029) is gone |
| 0035 | **no** | `geo.mv_soil_survey_union` definition differs from the 0035 form |
| 0036 | **no** | `geo.features` is `relkind='r'` with 0 partitions, not the partitioned parent 0036 asserts |
| 0038 | yes | `geo.intervention_tiles` is byte-identical to the 0038 body |
| 0039 | **no** | all five tile functions it drops are still present |
| 0040 | yes | `ai_conversations.geohash` is `varchar(24)` |

The journal recorded only 0000–0029, so those ten were invisible to it. **Repairing the journal
without checking production would have broken deploys**: `preDeployCommand` would have tried to
apply all seven, and 0036 fails outright, rolling back the whole transaction.

Those seven are **superseded by the Parquet/PMTiles architecture** (owner decision 2026-09-08).
They are archived, not applied. 0039 in particular must never be run against production as-is —
dropping the tile functions Martin serves 404s the whole composite tile request and hides every
layer.

**3. One migration asserted a state nothing in the tree produced.**

`0033` deliberately left `geo.intervention_tiles` without a `SET search_path` pin ("that is how
`drizzle/0005` left it"), while `0038` asserts all five tile functions carry one. Production only
got past 0038 because the pin had been applied there by hand. `0037_intervention_tiles_search_path.sql`
was written on 2026-09-08 to close that gap and is archived here as the record of it; the baseline
carries the pin because production does.

## Reading these files

They remain the best explanation of *why* the schema looks the way it does — several headers carry
traps that cost real time. Two worth knowing before editing anything here:

- Writing the statement-separator literal inside a comment **cuts the file in half** and the
  migration dies with a 42601 syntax error. `drizzle/0030`'s header explains this; it was first hit
  in 0029.
- `pg_dump` cannot dump a server newer than itself, and production is PostgreSQL 18.

---
type: evidence
slug: repository_conformity_hardening_20260901
wave: c4
date: 2026-09-02
---

# Dormant Drizzle migrations — typed state manifest

**No file in `drizzle/` was edited, moved or registered by this track.** Ownership stays with
shrink `s6`; any registration or movement requires an `s6` handoff. This manifest exists because
the proof-before-delete contract's item 5 requires a typed state, a reason and a production
fingerprint for every migration whose absence from an import graph is irrelevant — which is all
of them.

## The one fact that governs the whole table

`drizzle/meta/_journal.json` **ends at `idx 29` (`0029_pre_aggregation_layer`)**. Files `0030`
through `0038` exist on disk and **none of them is journalled**. `scripts/migrate.mjs` enumerates
from the journal, not from the directory, so an unregistered file is skipped **and the deploy
still reports success** (RUNBOOK §0.13 table, line 853). Applied-state therefore cannot be
inferred from the journal in either direction — `0030`'s index and `0038`'s function bodies are
live in production while `0031`'s relation is not, and the journal says the same thing about all
three.

Two consequences that any registration must respect:

1. **A contract bump rides along.** `src/__tests__/security/readiness-migration-contract.test.ts`
   pins the **last** journal entry's tag, `when` and file sha256 against
   `src/lib/server/db/migration-contract.ts`, and `Dockerfile:67` runs `npm test` as a hard build
   gate. Registering anything without bumping the contract in the same commit fails the build.
2. **Ordering is a live hazard.** The migrator runs *every* registered file, and `0033`–`0036`
   sit ahead of `0038` (RUNBOOK §0.21.6). Registering the earlier files replays all six tile
   functions before `0038`'s ceilings land; a single bad tile function 404s the whole composite
   and hides every layer.

**Gap numbers are real, not lost files.** `idx 26` has neither a journal entry nor a file, and no
`drizzle/0037*` has ever existed in git history (`git log --all --diff-filter=A -- 'drizzle/0037*'`
returns nothing). Precedent, not anomaly: the journal already skips `idx 26` and production is
current, which is what makes registering a later file while earlier ones stay dormant a proven
move (RUNBOOK §0.19.0 step 10).

## The rebuild hazard this manifest exists to keep loud

A rebuild from migration history alone would **silently restore the old 14 MB `sensor_tiles`
body** and **resurrect `geo.mv_signal_cell_daily`** (6,349 MB / 24,958,092 rows) — because
`drizzle/0029_pre_aggregation_layer.sql:533` still contains its `CREATE MATERIALIZED VIEW IF NOT
EXISTS`, and the out-of-band `DROP` of 2026-08-18 is recorded only in the unregistered `0034`.
Divergence is acceptable; silent divergence is what nearly undid that drop once already.

## Per-file state

| File | State | Reason | Production fingerprint |
| --- | --- | --- | --- |
| `0030_features_layer_geom_tile_index.sql` | **hand-applied** | Composite layer+geometry GiST index for the tile path. Applied out of band; never journalled. §0.18.8 item 1 recommends shelving it with the rest of the partition work, which is an owner decision (§0.19.7). | `ix_features_layer_geom` **exists in production** despite `0030` being unregistered — hand-applied (RUNBOOK line 801: *"Do not infer applied-state from `_journal.json`"*). The file's own header records the measurement that motivated it: `geo.burn_severity_tiles(6,10,22)` at **45,574 ms** before the index. |
| `0031_observation_day_axis.sql` | **dormant** | Adds the feature observation-day axis as a relation nothing reads yet. Deliberately split from `0032` as a safety property, not tidiness. | **Not applied.** `geo.mv_feature_observation_day_axis` reports `skipped_missing` in `agri.matview_refresh_state` — *"`drizzle/0031` unregistered, exactly as designed"* (RUNBOOK line 1491). |
| `0032_observation_day_census_repoint.sql` | **dormant** | Repoints `geo.v_observation_day_census`'s feature leg onto `0031`'s axis. This is the half with blast radius, which is why it is a separate file. | **Not applied**, and gated: its own `DO $$` precondition fails the migration unless `mv_feature_observation_day_axis` has already been **refreshed**. Querying an unpopulated matview errors outright (`ERROR: materialized view "mv_soil_survey_union" has not been populated`, measured on production). |
| `0033_tile_function_partition_pruning.sql` | **shelved** | Resolves each layer NAME to a `layer_id` constant inside six of the seven Martin-registered tile functions. *"Shipped dormant"* (RUNBOOK line 1081); each function diffed against its live body — exactly three hunks each, signatures/volatility/`PARALLEL SAFE`/`search_path`/predicates preserved. §0.18.8 item 1 recommends shelving `0030`–`0033` alongside `scripts/partition-features.mjs`. | **No production application evidenced.** Registration is an owner decision with two preconditions beyond the contract bump: restart Martin (nothing in the pipeline does), then fetch one tile per rewritten source **with an `Origin` header** (RUNBOOK §0.13 item 1). |
| `0034_record_signal_cell_daily_drop.sql` | **dormant** | Makes the out-of-band `DROP MATERIALIZED VIEW geo.mv_signal_cell_daily` replayable and idempotent. Its own header opens `-- DORMANT. Not registered in drizzle/meta/_journal.json; do not register it as part of this task.` **Never edit `0029`.** | The **DROP itself is applied to production** (2026-08-18; the relation was 6,349 MB / 24,958,092 rows, last full rebuild 1,729 s against a 2 GB-capped container). The **migration** is not. A caveat on two of its lines must be fixed before registration (RUNBOOK lines 1112, 1535, §0.19.6 item 43). |
| `0035_soil_survey_union_collection_extract.sql` | **dormant** | Moves `ST_CollectionExtract(…,3)` into the `delineation` CTE so a repaired `GeometryCollection` cannot reach `ST_Union`; `0029`'s two extract calls only wrap the union's *output*, which is too late. | **Not applied.** `geo.mv_soil_survey_union` has **never produced a row** — 4 consecutive failed refreshes in `agri.matview_refresh_state`, `relispopulated = false` since `0029` created it. **Hard deploy hazard:** the file's `DO $$` guard `RAISE EXCEPTION`s if that view is populated (`:94-96`), so a successful hourly `matview-refresh` between merge and apply blocks `preDeployCommand` for a reason nothing in the commit explains. Priority LOW — it repairs a relation with **zero readers**. |
| `0036_features_partitioned_precondition.sql` | **dormant** | Four catalog-read-only `DO $$` asserts for the partitioned `geo.features` swap: `relkind='p'`; `features_layer_external_id_unique` present and `indisvalid`/`indisready`; composite PK `(id, layer_id)` in that order; `geo.features_default` registered as the DEFAULT partition. No DDL, no data touched. Exists to be registered **in the same commit the swap is declared complete**. | **Not applied**, by construction — `scripts/partition-features.mjs` performs the swap by hand, out of band, and nothing in `preDeployCommand` runs it. If the swap is shelved (§0.18.8 item 1 / §0.19.7), this file becomes **permanently dormant dead code** and should be retired with the swap, not before it. |
| `0037` | **does not exist** | Number never allocated. `git log --all --diff-filter=A -- 'drizzle/0037*'` returns nothing; there is no journal entry either. | n/a — same class as the missing `idx 26`. |
| `0038_tile_low_zoom_routing.sql` | **hand-applied** | Hard row ceiling on all five `geo.features`-backed tile functions plus the `DISTINCT ON` that `geo.sensor_tiles` needed. Same names, argument names, types, `bytea` return, `STABLE`, `PARALLEL SAFE`, `search_path`, MVT layer tag and attribute order — any of those changing would blank a layer while reporting success. | **Applied to production 2026-08-21** (RUNBOOK line 3417): `sensor_tiles` **14,258,826 → 745,755 bytes (19.1×)**; z6/11/22 **3,920,849 → 222,682 bytes (17.6×)**, read straight from Postgres so Martin's 5-minute cache could not flatter the number. |

## Sources

- `drizzle/meta/_journal.json` (machine source of truth for the journalled set; 29 entries, last
  `0029_pre_aggregation_layer`).
- Each file's own header block in `drizzle/00*.sql` — `0034` and `0036` declare their dormancy in
  the first line, `0030`/`0035`/`0038` carry the production measurements quoted above.
- `conductor/RUNBOOK.md` §0.21.6 (*"seven migrations applied to production but unregistered"* —
  note the count is prose from that pass; the file range `0030`–`0038` it names is **eight**
  files, and their states are not uniform, which is what this table exists to record), plus
  §0.13, §0.18.8, §0.19.0 step 10, §0.19.6 item 43 and §0.19.7.

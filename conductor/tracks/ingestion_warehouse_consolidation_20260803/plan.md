---
type: track-plan
track: ingestion_warehouse_consolidation_20260803
status: active
---

# Plan

Phase detail, DDL sketches and effort estimates are in
[`plans/ingestion-warehouse-consolidation-2026-08-03.md`](../../../plans/ingestion-warehouse-consolidation-2026-08-03.md) §7.
This file tracks execution order and state only.

**Ordering constraint:** phases 2 and 3 are destructive to `agri` and are free
only while it holds 0 rows. Phase 4 writes the first row. Do not reorder 2, 3, 4.

Phase 2 runs before phase 3 (revised 2026-08-03, superseding the session
handoff's geometry-first ordering): phase 3 ALTERs `forecast_series`,
`signal_observation` and `cell_source_crosswalk`, and doing that after the
guards are gone removes the need for a `DISABLE TRIGGER` bracket — which is one
of phase 2's stated benefits.

| Phase | Scope | Status |
|---|---|---|
| 2 | Alembic `0018`: cut the enforcement layer, remove the eval-only lock, preserve the nine ML tables (Option 4 scope — see spec) | authored and verified; awaiting owner apply to production |
| 3 | `geo.geometry` Type-2 conformed dimension; backfill; repoint the facts | pending |
| 1 | Port the six ingestion jobs to the Python CLI; swap the cron container off `curlimages/curl` | pending |
| 4 | Provenance on the operational lane — every fetch writes `source_release` | pending |
| 5 | Five new in-house sources + NDVI (soil, terrain, NLCD, LANDFIRE, MTBS) | pending |
| 6 | Narrow fact `geo.metric_daily` + covariates v2 + Monte Carlo + the time slider | pending |
| 7 | ML variant on the slider toggle, including the CLI publisher | pending |

## Phase 2 checklist

- [ ] Author `20260803_0018_*` — drop `finalize_*`, `verify_*`, `enforce_*`,
      `guard_*` (except `guard_forecast_immutable_rows` and
      `guard_strategy_review_change`), `protect_*`/`reject_*`/`prevent_*`; drop the
      hindcast plane and its two checksum functions; drop **three** owner roles;
      convert **one** `GENERATED ALWAYS … STORED` `value_checksum` column to a plain
      column; drop `ck_forecast_iteration_method` and `ck_forecast_iteration_purpose`
      and flip `purpose`'s default to `'serving'`.
- [ ] Keep all nine ML-serving tables, every checksum column and its format CHECK,
      `ck_forecast_receipt_finalized_evidence`, both `forecast_iteration_*_checksum`
      functions, `materialize_forecast_iteration`'s idempotency block, the
      `record_*` writers, and the strategy/intervention planes.

### Two rules discovered while shipping `0018` — they apply to every future teardown

**1. A revision that drops an object must first inline the DDL into every earlier revision
that loads it.** `db/agri/**` is regenerated from a `pg_dump` of *head*, but earlier
migrations read from that tree via `load_object_sql`. The moment a later revision drops
an object, its canonical file stops existing and the chain can no longer replay from
scratch — `alembic upgrade head` dies with `FileNotFoundError: no declarative object at …`.
Thirteen objects hit this; their bodies now live as module constants in `0012`, `0013` and
`0014`. Find them with an AST scan that resolves `load_object_sql` arguments through
list/tuple constants in **both** module and function scope — a plain grep for string
literals misses the loop-driven calls in `0013` and `0014`.

**2. Teardown drops of triggers and constraints must be `IF EXISTS`.** Editing a canonical
`.sql` file retroactively changes what an earlier revision creates. `0018` edited
`triggers/strategy_label_episode.sql`, so on a fresh replay `0013` no longer created
`strategy_label_episode_parent_state` and `0018`'s unconditional `DROP TRIGGER` failed.
`DROP FUNCTION` deliberately stays strict — a signature that does not resolve is a real
error, not a replay artefact — and no drop ever uses `CASCADE`.

### `ck_forecast_receipt_finalized_evidence` was broken, and `0018` repairs it

The CHECK everything was documented to rely on did not work. As written it read
`status <> 'finalized' OR (receipt_checksum ~ '^[0-9a-f]{64}$' AND finalized_at IS NOT NULL)`.
Under a NULL `receipt_checksum` the regex is NULL, `NULL AND TRUE` is NULL, `FALSE OR NULL`
is NULL — and a CHECK rejects only FALSE. A receipt could reach `status='finalized'` with no
digest at all, and `v_forecast_series_serving` trusts that status, so it would have been
admitted to the ML serving lane with NULL provenance.

This was survivable only while `verify_forecast_receipt_finalization` and
`finalize_forecast_receipt` still raised on a NULL digest. `0018` drops both, which would
have left the hole as the *only* guard. The constraint is rebuilt with an explicit
`receipt_checksum IS NOT NULL` conjunct. Verified on both majors: the predicate now
evaluates FALSE for a NULL digest.

It is the same failure shape as the `value_checksum` conversion — a governed column that
silently stops covering anything while every format check still passes. Worth assuming a
third instance exists somewhere and looking for it.

### Plan corrections found while writing the lane briefs, 2026-08-03

Verified against the code and against production. The plan's *rules* survive; two of its
stated *mechanisms* do not, and a third is a latent bug.

1. **Risk 2c's mechanism is wrong.** The plan says
   `src/lib/server/services/ingest.ts:107-122` rewrites `geo.features.created_at`. It does
   not — that UPDATE sets `properties` and `updatedAt` only (`:109-119`), and no trigger in
   `drizzle/**` writes `created_at`. **The measurement still holds**: production `created_at`
   spans just 1–2 distinct days per layer, none earlier than 2026-08-03, so it cannot date a
   v1 row honestly. The operative rule is unchanged — date `version_valid_from` from the
   producer's observation timestamp, fall back to `'-infinity'`, never `created_at`, never
   `now()` — but do not repeat the false mechanism, and lane J must not audit "`created_at`
   readers" looking for a rewrite that isn't there.
2. **The backfill sketch has a latent CHECK violation.** `plans/…:269` maps geometry type via
   `lower(replace(GeometryType(f.geom),'MULTI',''))`, which yields `'linestring'` — but
   `ck_geometry_kind` (`plans/…:146`) allows only `point|polygon|line|grid_cell`. Harmless
   today (all four live layers are points and polygons), a hard failure the first time a line
   source lands. Use an explicit `CASE` mapping.
3. **The `natural_key` namespace contradicts itself.** The backfill SQL (`plans/…:266`) uses
   `l.name` (`fire-detections`, `water-gauges`, …) while `:210` and `:474` specify producer
   tokens (`firms:`, `mtbs:`). **Lane A's producer tokens win**; lane A exports the mapping and
   lane B must substitute it rather than reading the plan's SQL literally. Under Type-2 a
   namespace disagreement does not merely duplicate — it interleaves two producers into one
   version chain.
4. **Boundary gap.** The plan requires the Drizzle-before-Alembic note in
   `services/agri-data-service/db/AGENTS.md` in the Phase 3 commit, but lane B is forbidden
   from `services/agri-data-service/**`. Lane C owns that note.

### Corrections found by adversarial verification, 2026-08-03

The drop set as first surveyed was refuted with high confidence. Six repairs are
folded into `0018`; each is load-bearing.

1. **`plantgeo_forecast_mv_refresh_owner` must NOT be dropped.** It owns both
   `agri.mv_forecast_ml_daily_serving` and `agri.refresh_forecast_ml_daily_serving()`
   (`20260802_0015:197-200`). A bare `DROP ROLE` errors `2BP01`; the `DROP OWNED BY`
   recipe used for its siblings would delete the ML matview outright. Three roles, not four.
   Non-concurrent `REFRESH` requires matview ownership and the refresh function is
   `SECURITY DEFINER`, so owner and definer must stay the same role.
2. **Converting the generated column silently guts the Monte Carlo receipt.**
   `materialize_forecast_iteration.sql:271-280` omits `value_checksum` from its INSERT
   and relies on the `GENERATED` expression. After `DROP EXPRESSION` every row is NULL;
   `forecast_iteration_receipt_checksum.sql:44` does `string_agg` over those NULLs,
   which returns NULL, and `concat_ws` drops NULL args — yielding a valid 64-hex digest
   that covers nothing and passes every retained format CHECK. `0018` **must** amend the
   procedure to call `agri.forecast_iteration_value_checksum(...)` explicitly.
3. **`record_*` is retained.** Those seven writers feed `forecast_input_recorded_at`,
   which `v_forecast_timeseries_contract.sql:45-46` INNER JOINs and
   `forecast_daily_bootstrap.sql:67-72` hard-`RAISE`s on. Dropping them breaks
   `materialize_forecast_iteration`, `reconcile_forecast_iteration_actuals` and
   `forecast_aligned_daily_series` — all of which are kept.
4. **`guard_strategy_review_change` is retained.** It is the sole caller of the
   strategy checksum functions Option 4 keeps (`:35-36`, `:53-54`); dropping it would
   leave `definition_checksum` and `policy_checksum` permanently NULL.
5. **`strategy_selection_quality_evidence` is dropped**, not kept: it INNER JOINs the
   hindcast view being removed (`:16`, `:29`) and its only caller
   (`finalize_strategy_selection_receipt`) is going too. SQL bodies are not tracked
   dependencies, so this would have failed silently at runtime.
6. **`health.py` must be pruned in the same change.** Its readiness inventories name
   dropped objects at roughly fifteen sites; `railway.json` health-checks `/ready`, so a
   stale list is a failing deploy.

Also corrected: only **one** generated column needs conversion
(`forecast_iteration_value.sql:17`). `forecast_hindcast_value` carries five, but the
whole table is dropped. And `materialize_forecast_iteration` /
`reconcile_forecast_iteration_actuals` are PROCEDURES — `DROP FUNCTION` fails on them.

**`finalize_forecast_receipt` is the only object in the schema that writes
`forecast_receipt.receipt_checksum` and sets `status='finalized'`.** Dropping it is
correct, but it means phase 7's Python publisher must byte-exactly reproduce that
digest under the `0017` determinism pins. Recorded here so phase 7 does not rediscover it.

### Environment: the regeneration path

`db/tools/regenerate.py` refuses any server that is not PostgreSQL 16
(`dump_schema.CANONICAL_SERVER_MAJOR = 16`), and the existing pg16 warehouse container is
**not reachable from the Windows host** — the TCP port answers but every connection dies
with `server closed the connection unexpectedly`. It works only via `podman exec`. Host
port mappings on this machine are also crossed: `127.0.0.1:5443` lands on the pg18
rehearsal server, not the pg16 sweep DB. **Always run `SHOW server_version` before
trusting a port.**

Resolved 2026-08-03 without touching the running containers, by standing up a clean pg16
from the same image on a free port:

```
podman run -d --name plantgeo-pg16-regen -p 127.0.0.1:5456:5432 \
  -e POSTGRES_PASSWORD=526152 -e POSTGRES_USER=postgres -e POSTGRES_DB=postgres \
  localhost/plantgeo-spatiotemporal:pg16
```

Verified: PostgreSQL 16.14, host-reachable, with `postgis`, `timescaledb`, `vector` and
`pgcrypto` all available. Use it as `--admin-dsn` for `regenerate.py` and as
`AGRI_TEST_DATABASE_URL` for the byte-parity gate.

That container was **removed** after use — it is disposable, so recreate it from the command
above when needed. Note that the app roles must be created **before** migrating: the grants in
`0012`/`0015` are skipped for a role that does not yet exist, and two role-boundary tests then
fail with `role "plantgeo_forecast_reader" does not exist`. Create
`plantgeo_owner`, `plantgeo_loader`, `plantgeo_forecast_{reader,writer,publisher}`,
`plantgeo_forecast_mv_refresher`, `plantgeo_forecast_refresh_operator`,
`plantgeo_local_{developer,viewer}` as `NOLOGIN`, then `CREATE DATABASE`, run
`infra/local-warehouse/enable-extensions.sql`, then `alembic upgrade head`.

**All podman containers were stopped at the end of this session** to free the machine; nothing
was removed except the disposable regen container, so every volume is intact — including the
precious `plantgeo_boise_completion_20260725` on the warehouse container. Restart only what a
task needs: `plantgeo-warehouse_plantgeo-warehouse_1` (pg16 warehouse, `:5442`, unreachable
from the host — `podman exec` only), `plantgeo-pg18-rehearsal` (`:5445`, the PG18 verification
target), and the `plantgeo` compose trio (postgis `:5434`, redis `:6379`, martin `:3100`) for
running the Next.js app.

**Never run `regenerate.py` while anything is editing `db/agri/`** — it wipes the tree and
rewrites it from the migrations, so concurrent hand edits are lost. Regenerate only after
the revision and all declarative edits are final.

Other verification facts worth keeping:

- `dump_schema.DUMP_ARGS` is `--schema-only --schema=agri --no-owner --no-privileges`, so
  roles, ownership and every `GRANT`/`REVOKE` are **absent from `db/agri/**`**. Dropping a
  role produces no tree diff, and a forgotten `REVOKE` is not caught by the parity test.
- Pointing `AGRI_TEST_DATABASE_URL` at a pg18 server makes
  `test_declarative_tree_matches_migrations` **hard-fail**, not skip. Byte parity is only
  ever asserted against pg16; the pg18 comparison is the separate opt-in
  `AGRI_CROSS_MAJOR_DATABASE_URL` path.
- `conftest.py` gates db-backed tests twice: it refuses if `current_database()` is
  `plantgeo`, and it fails if `alembic_version` differs from `EXPECTED_ALEMBIC_HEAD` — so
  the constant bump and the migration are one atomic change.
- `pytest_sessionfinish` sets `exitstatus = 1` if **any** `agri_db` test *skips* while
  `AGRI_TEST_DATABASE_URL` is set, even when everything that ran passed. Test deletions must
  not leave a db-backed test skipping.
- CI runs **no tests**. The Dockerfile `checks` stage runs `ruff format --check`,
  `ruff check` and `mypy` only. Nothing automated catches a parity or contract regression —
  pytest must be run by hand.
- [x] Regenerate the declarative tree (`db/tools/regenerate.py`) — 204 files, byte-parity passes.
- [x] Bump `EXPECTED_ALEMBIC_REVISION` (`routes/health.py`) and `EXPECTED_ALEMBIC_HEAD`
      (`tests/conftest.py`) to `20260803_0018`.
- [x] Prune `health.py`'s readiness inventories of every dropped object — `railway.json`
      health-checks `/ready`, so a stale list is a failing deploy.
- [x] Reconcile the ORM: `models/forecasting.py` (hindcast classes, five stale CHECKs,
      the `purpose` default, `value_checksum` no longer `Computed`), `models/__init__.py`
      re-exports, `models/strategy_selection.py`.
- [x] Triage the tests — 6 files deleted, 15 edited, no `agri_db` test left skipping.
- [x] Update the governance narrative in `db/AGENTS.md` and `alembic/AGENTS.md`.
- [x] Verified on a **fresh** pg18 database built from scratch (`agri_final_0018` on
      `127.0.0.1:5445`): head lands, 9/9 ML tables, `REFRESH MATERIALIZED VIEW` succeeds,
      `value_checksum` plain, only `plantgeo_forecast_mv_refresh_owner` owns objects.
- [x] Full sweep green: agri **268 passed / 2 skipped**, byte-parity passes, `ruff` and
      `mypy` clean; Next.js **299 passed**, type-check, lint (0 errors) and
      `check:data-boundary` clean.
- [ ] **Owner applies to production.** Agents are not permitted to; `.env` in that directory
      points at production, so every command needs an explicit `$env:DATABASE_URL_SYNC` override.

Note on role retirement: `DROP ROLE` is cluster-wide while `REASSIGN OWNED` / `DROP OWNED`
are database-local, so a role still holding objects in a database that has not replayed
`0018` survives with a NOTICE by design. On the rehearsal cluster `plantgeo_boise_pg18`
(at `0016`) is that holder. In each migrated database the three retiring roles own zero objects.

**The seven `record_*` writers become `SECURITY INVOKER`** (owner decision, 2026-08-03). They
were `SECURITY DEFINER` only so a restricted role could append to
`agri.forecast_input_recorded_at` without holding INSERT on it, owned by the locked NOLOGIN
`plantgeo_forecast_input_recorder_owner`. Retiring that role reassigns them to whoever runs
the migration, so leaving the definer bit set would have *widened* their privileges rather
than preserved them. With the four-role model rejected and every caller connecting with the
same credentials, the bit buys nothing. `agri.refresh_forecast_ml_daily_serving` is now the
only `SECURITY DEFINER` function in the schema, and it must stay one — a non-concurrent
`REFRESH` requires matview ownership. Pinned by
`tests/test_security_definer_lockdown_postgresql.py`.

## Out-of-band work carried in this track

Two UI defects the owner found in the browser on 2026-08-03, in code shipped by
`9ce5178` and its neighbours:

- [ ] Community features do not work on the map.
- [ ] Navigation can trap the user in the organization settings page.

## Tune during implementation — no owner decision needed

- Circuit-breaker thresholds for geometry change detection. The rule is settled;
  the numbers are not. 5 % of a 110-row layer is 6 rows, so a per-layer floor is
  needed.
- Whether any surviving ML table needs `geometry_id` beyond `forecast_series`.
- MTBS licensing under Esri AGOL terms — persist now, record the terms in
  `data_source`, revisit only if distribution changes.

---

## Out-of-band: geometry dimension repaired against production, 2026-08-04

Applied by the owner's direction to run against prod (overriding this track's
never-touch-prod rule; recorded here so the override is auditable, not silent).

**Before:** 3,669 of 23,690 features had `geometry_id IS NULL`; `geo.geometry` held 20,021
rows. The orphan count was *growing* — 3,505 → 3,669 in roughly one hour — because the
Python ingest path never writes the dimension.

**Applied:** one transaction. 3,669 geometry versions inserted, 3,669 features linked.
Guarded by a `DO` block that aborts if any orphan lacks an observation time or geometry.

**After:** `0` orphans; 23,690 features = 23,690 geometry rows = 23,690 open versions.

Every repaired row was dated from its **own payload** (`updatedAt` for `usgs-nwis`,
`observedAt` for `open-meteo`) — zero from a clock, zero at `-infinity`, zero key
collisions, zero pre-existing open versions for those keys (rehearsed under `ROLLBACK`
first, which left the row count unchanged at 20,021).

Key formula confirmed empirically against all 20,021 previously-linked rows:
`natural_key = producer || ':' || (properties->>'id')` — resolving §7 Q1 in favour of
producer tokens, as lane A recommended.

**This is a repair, not a fix.** `ingest/writer.py` still inserts `(layer_id, properties)`
only, so orphans regrow every cron tick until the versioned warehouse adapter lands. That
adapter is in flight.

### Lane A closed out

`ingest/identity.py` shipped and was validated the strong way: it reproduces **all 23,526**
stored production keys byte-identically — every row, not a sample — via §4.2's *preferred*
route (reading `properties->>'id'` back out of `geo.features`), which closes the
carry-forward that the golden fixtures were only TypeScript-derived.

## Out-of-band: geometry dimension re-keyed to entities, 2026-08-04

The dimension was keyed by OBSERVATION, not place: 15,936 gauge rows for 899 real gauges (17x),
2,983 weather rows for 116 sample points (25x). Every key held exactly one version and
`superseded_by` was NULL everywhere — the Type-2 machinery was inert.

`identity.py` gained an additive split (observation key unchanged, so TypeScript parity holds):
`natural_key` = the observation; `entity_key` = the enduring place. `geometry.py`'s
`geometry_key_for()` returns `entity_key` — it originally returned `natural_key` and no test
caught it, because no fixture set `entity_local_id`. Now covered.

**Rebuild applied to production:** 17,281 observation-keyed rows deleted; 1,015 entity rows
inserted; every feature re-linked.

| | Before | After |
|---|---|---|
| `geo.geometry` rows | 23,690 | **7,424** |
| Orphaned features | 1,638 (regrown) | **0** |
| `usgs-nwis` slider depth | 1990-10-01 | **1990-10-01** (preserved) |

Each entity's first version is dated from its **earliest** observation and shaped by its
**latest** — dating from the latest would have silently discarded 36 years of slider range.
Cross-validated two ways: the SQL derivation and `identity.py` in Python both produced 7,424.

Python sweep after the spine landed: **853 passed, 26 skipped** (baseline was 645).

**Not fixed by this:** `geo.features` still grows ~13,200 rows/day at ~5.4 KB (~71 MB/day).
Geometry growth is now ~zero. Owner decision: no `geo.metric_daily` and no new migration —
serve the slider from queries over existing tables, driven by layer toggles.

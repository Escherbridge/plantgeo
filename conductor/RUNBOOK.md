---
type: runbook
---

# PlantGeo — Runbook

**Current status (2026-08-27): the targeted pre-API Parquet data and code integration is complete on
`main` at `949e20ee38405781a3e2a8978b2fc769bb7659d6`. Start with the LIVE section immediately
below.** The older 2026-08-25 header and session notes are retained as historical evidence; where
they describe missing Parquet data, an unbuilt serving API, or unintegrated lane artifacts, the
2026-08-27 LIVE section supersedes them.

**Historical header last updated:** 2026-08-25. **§0.41 IS THE FIRST ANALYTICAL USE OF THE DRAINED WAREHOUSE (2026-08-24) — it makes NO infrastructure claims and supersedes nothing, but read §0.41.6 before running any local DuckDB: a cross-join against CONUS-wide USDM polygons CONSUMED THE HOST, and spilling is now disabled by guard, not by tuning. §0.41.4 records THREE claims refuted by their own evidence, and §0.41.9 names the one blocker — the 23-year `fire-detections` hole, which is already chartered as pivot item B, not new work.** **READ §0.42 FIRST — the orchestration packet: three concurrent lanes (data/ingestion, API, UI) behind a contract freeze, a per-layer hard cutover to Parquet, the four layers that are NOT Parquet lanes at all, and the correction that §0.41.9's blocker (the `fire-detections` hole) CLEARED at 19:42 UTC on 2026-08-24 — that track is runnable, not blocked.** **READ §0.40 FIRST — it corrects §0.39: the runners were NOT stopped (every push redeploys them; taken down 02:05 UTC), "backlog 0" was z13 only with no coarse rung anywhere, the pivot verified on cold-path and resource grounds, and the four repoint decisions are recorded there.** **THE PARQUET BACKLOG IS DRAINED (12,365 -> 0 lane-days) - READ §0.39 NEXT; it supersedes §0.38 and records why the DB shrink is now gated on an unbuilt read API, and why nothing is ingesting.** **ARCHITECTURE PIVOT — READ §0.23 FIRST: Postgres becomes a community-features database; every data plane moves to day-partitioned Parquet read by DuckDB+Polars, with Martin serving PMTiles. §0.16–§0.22 optimise a Postgres this project is leaving.** **§0.25 is the CURRENT HEAD OF THE PROGRAMME — wave 1 shipped green, the by-domain layering question is answered, and it retires `agri_sdk_layering` phases 4–8.** **§0.24 is the concurrent stream plan that executes it — 21 streams in 5 waves, each with a disjoint file boundary, governed by the new `conductor/code_styleguides/layer-lanes.md`.** **THE PARQUET PATH HAS STARTED — §0.22 carries the signal-plane grain decision and the traps for the export job.** **THE MAP IS FIXED — READ §0.21 FIRST; IT SUPERSEDES §0.17 AND §0.16.7.** **Branch:** `main` · **Last commit:** `2b38c66 layers` · **Working tree clean, level with origin.** **§0.21 records the three changes that fixed the map (composite split, cache-first service worker, and `sensor_tiles` DISTINCT ON — 14.26 MB → 745 KB, applied to production); the correction that `EXPLAIN` cost is MEANINGLESS for these tile functions, since it prices a 0-row layer identically to a 186,904-row one, which invalidates every cost-based conclusion above it; the seven migrations applied-but-unregistered (§0.21.6); and the owner directive to STOP RUNNING WORKFLOWS and work in small steps (§0.21.8).**

**What changed 2026-08-21 — four new sections, and they supersede earlier ones where they disagree.** **§0.16** is the data-quality and QA assessment: the census per layer and per observation plane, freshness and rot, the job ledger and matview refresh state, the storage/index/bloat profile, the partitionwise probe, and what the QA gate does and does not prove — every number labelled with how it was obtained, CONFIRMED separated from UNVERIFIED, and **two headline claims REFUTED outright (§0.16.9)**. **§0.17** is why the map is broken *right now*, ranked by rendering unblocked per unit of work; **read it first if you are here to fix the outage.** **§0.18** is the target architecture — entity/observation split with sealed months on R2 — with the losing designs' grafts folded in and **17 recorded rejections so they stop being re-litigated (§0.18.8)**. **§0.19** is the merged programme plan with a gate class, precondition and reversal cost per item.

**THREE THINGS THAT INVALIDATE EARLIER SECTIONS.** **(1) The partition swap's justification did not survive measurement.** A controlled probe (§0.16.7) found **byte-identical plan text** for all three census matviews with `enable_partitionwise_aggregate`/`_join` both off and on, because each joins the never-co-partitionable 11-row `geo.layers` before aggregating — so partitioning `geo.features` delivers **zero** relief to the workload blowing the cap, and all six tile functions already plan as a cheap composite-index `Nested Loop` **at z10 — the zoom at which tiles already return in 0.2-0.4 s. The z5/z6 plans, where tiles actually hang, were never captured** (correction 2026-08-21; see §0.16.7). §0.18.8 item 1 recommends **shelving** `drizzle/0030`-`0033` and `scripts/partition-features.mjs`; **that is an owner decision and the largest single call in the programme (§0.19.7).** §0.1 step 1, §0.8 and §10 should be read through that lens. **(2) The acute outage is NOT D0 and NOT the bandwidth cap.** D0 is measured fixed. The current cause is a Martin `statement_timeout` of **0** plus no cancel-on-disconnect leaking pool slots permanently (an abandoned tile query measured still `active` at **1,142 s**), stacked on unbounded tile functions — and the composite **times out entirely once gzip is requested, which every browser does**, so every prior latency figure in §9 understates what a browser sees. The client request budget **never sees a tile request** (§0.17.6). **(3) `mv_signal_cell_daily`'s degraded consumers are FOUR, not three** — the section carrying the stale count of three is **§0** (the HANDOFF list), **not §0.10**. §0.10 already names all four and flags the drift itself. *(Corrected 2026-08-21: an earlier draft of this line said "§0.10's count is wrong", which reproduced the very count-drift failure mode the paragraph exists to fix.)* **And those four consumers do NOT crash** — each returns a typed `pre_aggregated_plane_unbuilt` refusal via the `to_regclass` probe at `agent/tools.py:473`/`:901`. See §0.16.4, which carried the wrong verdict until 2026-08-21.

**A FOURTH THING, found 2026-08-21 while closing a completeness review.** **Every `pg_stat_*` counter in this runbook is measured over an UNKNOWN window, and the database restarts often.** `pg_stat_database.stats_reset` is **NULL** on production, which does *not* mean "since forever": a crash/unclean restart discards cumulative stats and leaves `stats_reset` NULL again. That a discard already happened is provable from inside this file — §5 records **37.5 billion** lifetime reads with `stats_reset` NULL; §0.16.9 measured **1.06 billion** later. Counters cannot fall. Separately, a read at **2026-08-21 07:47:45 UTC caught the instance 0.56 s after a restart** (`pg_postmaster_start_time()`), and a re-read 20 s later showed counters *higher* than the 2026-08-20 pass — so counters **do** survive a clean restart and uptime is **not** a bound on the stats window. Net: treat "`idx_scan = 0`" as "zero over an unbounded-below window", never as "never read". This directly gates §0.19.3 item 11's `DROP INDEX`.

**Older header context, still true:** the memory cap is **2 GB, not 3 GB** — everything below §0 assumes otherwise. `geo.mv_signal_cell_daily` was **DROPPED** 2026-08-18. §0.4's BLOCKER (runtime layer creation ⇒ a DEFAULT partition is mandatory) invalidates §0.1 step 1 as written. **A PRODUCTION CHANGE WAS APPLIED 2026-08-17** — `TILE_CORS_ORIGIN` on `plantgeo-martin` (§2). Correction to that entry: the fix is real and verified live, but §2/§9's framing of it as "the root-cause outage" no longer holds for the *current* outage — see §0.17.

---

## LIVE — targeted pre-API Parquet integration complete, 2026-08-27. START HERE.

This is the current operational handoff. It supersedes the previous session-12 LIVE entry below,
which is retained as a historical checkpoint.

### Completion evidence

- `main` and `origin/main` are integrated through
  `949e20ee38405781a3e2a8978b2fc769bb7659d6`. The integration recovered and normalized the
  completed canonical snapshot, climate, soil, fire, water, weather, soil-wetness, and vegetation
  artifacts. No API/client/browser cutover was performed by that integration.
- The canonical signal snapshot is immutable at
  `raw-canonical/signal-observation/snapshot=prod-20260826-full-signal-v1/`: 46,146,568 physical
  facts, 8,364 fact parts, zero rejects, and manifest SHA-256
  `465abc4e813bf28c78acd7f97a4da9d19ad959e525de3eb1f422ca2f6e73e94f` bound by `_COMPLETE`.
- Snapshot-derived lanes are complete for precipitation, wind speed, relative humidity, air
  temperature (mean/max/min), shortwave radiation, VPD, ERA5-Land soil moisture (three depths),
  soil temperature (four depths), and NASA soil wetness (surface/root-zone/profile). Each published
  lane completed exact source reconciliation and wrote manifest/`_COMPLETE` last.
- Fire detections are exact across 9,428 calendar days: 8,359 data days, 1,069 governed absences,
  and 3,039,749 detections. Water gauges are exact across 1,521 days and 1,448,754 z13 rows.
- Weather's independent 2026-08-01..25 audit reported zero findings. Vegetation repaired exactly
  five stale days, then independently reconciled 1,208 days and 185,245 source/Parquet rows with
  matching SHA-256 `0f13a768816db63a2640277f361de3681b193b7624365af444ede5267e7c9e64`.
- NASA soil wetness independently recomputed all 159 lane-months. Bundle reconciliation is
  1,776,330 physical = 1,742,418 selected + 33,912 superseded + 0 rejected; bundle manifest
  SHA-256 is `75b9e825816369d7ffcca943f45a66fb411acbeaa0f0af983f50df443e33c78c`.
- The single consolidated validation sweep on exact commit `949e20e` passed data-boundary,
  TypeScript typecheck, frontend lint, Python format, Ruff, Mypy, 1,384 frontend tests, and 4,334
  Python tests. Thirteen frontend tests and 136 Python tests remained environment-gated; the
  independent integrated-tree review approved the result.

### Production state

- PostgreSQL data and ingestion remain intact. No table/row deletion, writer disablement, or
  retirement migration was performed. Keep that fallback until each forward writer and reader
  cutover passes its own gate.
- The private Sanic Parquet surface and the bounded TypeScript client exist, but the Next.js tRPC
  procedures have not been repointed and no browser acceptance pass has run.
- Vegetation's production publication deployment succeeded, and the forward cron configuration is
  present. Its scheduled runtime cadence still needs observation before retirement. Separately, the
  API service has a pre-existing `/ready` healthcheck timeout observed across multiple commits. Treat
  that as the first API-cutover blocker; it is separate from Parquet data correctness.

### Outstanding work, in order

1. Diagnose and clear the API service `/ready` timeout without weakening the readiness contract.
2. Deploy and directly probe the private `/api/v1/parquet` day, window, release, and coverage routes
   with production R2 configuration and bounded DuckDB memory/concurrency.
3. Repoint the eligible Next.js tRPC readers and slider capability census to
   `parquet-plane-client.ts`, one lane at a time, with no silent PostgreSQL fallback.
4. Run production browser acceptance at the default camera and z10 for every repointed layer,
   including day changes, zoom-rung routing, empty/governed-absence states, and stale-cache checks.
5. Prove the forward source-to-Parquet path for every lane that still relies on a snapshot builder
   or PostgreSQL bridge. Historical completeness alone does not authorize writer retirement.
6. Complete shrink-track `s2a`: extract the shared Parquet operations core into top-level
   `parquet_ops/`, then split `interface/cli/` and perform the coordinated `agri-service` hard rename.
7. Complete shrink-track `s7`: repoint the agent/MCP data tools off obsolete Postgres views onto the
   shared Parquet core, following §0.42.30's exact keep/rewrite/delete census.
8. Run the environment-gated PostGIS/reader tests against disposable DSNs on the exact cutover tree.
9. Only after steps 1-8: retire PostgreSQL producers/readers per lane, then author reviewed migrations
   for index/table/data removal and record before/after storage plus rollback evidence.

---

## HISTORICAL — session 12, 2026-08-25. ROLLING ARMED.

**This entry is edited in place, not appended to.** It is the legible head of a 8,578-line file:
`Done` and `Reviewed` append, `In flight` and `Next` are rewritten so they are never stale. Detail
lives in §0.42.14–§0.42.25; this says only where things stand. Marker: `.omc/state/rolling.json`.

### Goal

**DATA COMPLETENESS** — every layer that can render real data does so at every zoom rung, reading
Parquet. Code completeness across the three lanes is the means, not the end. Owner decisions
§0.42.5, gate answers §0.42.14.

**Measured 2026-08-25: the warehouse has not moved in 24.6 hours** (§0.42.31). 95,048 objects,
supervisor not running, and the ladder gap readable straight off the completion marks —
z13 11,510 vs 10,473 at each coarse rung = **1,037 lane-days**. Every hour of engineering this wave
moved zero bytes of warehouse data. That is not a failure of the work; it is that **all three
things which would close a gap need an operator to authorize them**, and none has been.

### Done — 15 commits, tree clean, **NOTHING PUSHED**

| commit | what |
|---|---|
| `876c011` | two sessions' uncommitted work landed together with provenance |
| `80ac72a` | **the wire freeze** — 9 goldens, asserted from both languages, cross-language `WIRE` parse |
| `19cef02` | both partition sets re-verified; `d5` moved lane C → A (it wrote inside `s5`'s directory) |
| `4e0961a` | `s0` — ingest `cronSchedule` restored; the Dockerfile header was lying |
| `8bd856b` `88ff1de` | `u4` — four non-lane surfaces state their real blocker instead of a false history claim |
| `290a6f2` → `4224842` | `s1` — 26 alembic revisions → one baseline, then six review fixes |
| `273828b` → `4a53deb` | `d3`/`b1` — the four Sanic routes, then eight review fixes |
| `239a079` → `549346f` | `d1` — ladder census + guards, then five review fixes |
| `369a810` | corrected the coverage golden (found by lane B measuring the real bucket) |
| `da1cef9` | mypy green across 259 files, red since `440d9b5` |
| `1eb7995` `539c23e` `1fe2f72` | s2 rescoped for the CLI split; `s7` added; Sanic + models decisions |

### Reviewed — the ledger, and it is not clean

- `s1` · quality-reviewer, refute-prompted · **CHANGES-REQUIRED** (6 findings) · fixed at `4224842` · **the fix is UNREVIEWED**
- `d3`/`b1` · quality-reviewer, refute-prompted · **CHANGES-REQUIRED** (8 findings) · fixed at `4a53deb` · **the fix is UNREVIEWED**
- `d1` · quality-reviewer, refute-prompted · **CHANGES-REQUIRED** (5 findings + 5 minors) · fixed at `549346f` · **the fix is UNREVIEWED**
- `s0` · `[x] (unreviewed)` — config only, but it changes a production schedule
- `u4` · `[x] (unreviewed)` — no reviewer ran
- **Three fix passes carry no verdict. Per the gate, they are in flight however finished they look.**
- **Phase (whole wave) · monitor-architect, separate lane, ran every suite · PASS-with-deferred** ·
  7 gates green (4,218 py / 1,384 ts / mypy / ruff / tsc / eslint / next build), **1 real defect: HEAD
  did not build** (three unstaged modules, fixed) · 9 findings ranked, 3 of them live defects ·
  product: aligned · §0.42.26–§0.42.30

### In flight

- Nothing running. All eight agents complete.

### Next — in dependency order. THE FIRST THREE ARE THE ONLY ONES THAT MOVE DATA.

1. **Run the ladder repair** — `parquet-drain --selection ladder`, reachable since `549346f`.
   Closes the 1,037. ~1-2 h, resumable, no source queries, writes ~3 objects/day. Dry-run first
   (`--dry-run --selection ladder`) and confirm it still reports 1,037.
2. **Deploy** to arm `s0`'s restored `cronSchedule`, so a forward writer exists at all.
   `sensors` upstream keeps ~6 days - days after **2026-08-31** are unrecoverable. The stamp
   (§0.42.21) should precede it; owner has said a broken live deployment is acceptable, so the
   ordering is a preference now, not a gate.
3. **The legacy sweep**, report-only first: `parquet-retire-legacy-layout --layer <slug>`. Numbers
   can only have moved in the safe direction since `549346f`.
4. `s2a` - extract the Parquet core using **§0.42.27's corrected classification, not §0.42.23's**,
   into top-level `parquet_ops/`, moving admission control into the core in the SAME change
   (§0.42.28). Then the CLI split + `agri-service` rename.
5. Two live unowned defects: `execution/historical_parquet.py:151` missing
   `max_temp_directory_size`; `planes/drought.py:247` opening a wholly unguarded session.
6. `s7` - reshaped by §0.42.30: 4 re-authored, 1 deleted, 1 probe rewritten, **5 left alone**.
7. Re-review the three fix passes, or accept them explicitly as unreviewed.
8. Measure the serving container's real memory limit - the ceiling has **zero headroom**.

### Retros — what the diff cannot show

- **Assumption falsified:** §0.40.2's "no coarse rung exists for any lane" and "the signal base
  lacks positions". Both dead. The ~1,560-day re-export I briefed as the longest pole **was already
  done** — a background loop kept working after the listing behind §0.40.2 was taken. *Before
  planning Parquet work, list the bucket.*
- **Dead end:** `s1`'s first stamp gate used a byte-exact `pg_dump` diff. Run against the real
  chain-built database it **failed on all 74 reparse lines** — a gate that could never have cleared
  production. Replaced with one shared canonical scoring rule.
- **Surprise:** `union_by_name=true` in a `LIMIT 0` probe makes the column set the UNION across
  objects, so a mid-re-export bbox read answered `published, rows: [], truncated: false` for days
  that hold rows. The four-state design's own worst failure, hiding inside a correctness check.
- **Surprise:** `DUCKDB_EXTENSION_DIRECTORY` as an env var is silently ignored by DuckDB 1.5.4 — it
  must arrive as `SET extension_directory`. Every request would have failed on a cold container
  while every local test passed on a warm cache.
- **Process:** a per-agent lint run in a shared tree reports *other agents'* half-written files.
  Owner deferred all ruff to one end sweep, then moved verification out of the authoring lane
  entirely. See `plantgeo-authoring-and-verification-are-separate-agents`.

---

## 0. HANDOFF — 2026-08-18. START HERE.

**Supersedes the 3 GB memory-cap premise that every section below this one assumes.**

### Goal

Get PlantGeo's database under its Railway memory cap without a materialized view, a continuous aggregate, or
TimescaleDB anywhere in the design. Owner's words: *"the main goal is to make it so that no materialized views or
continuous aggregates + timescale db memory hogging features are needed."* The engine-migration path was
investigated exhaustively and refuted — see `docs/research/timescale-pivot-2026-08-17/report.md`.

### THE CAP IS 2 GB. IT WAS 1 GB THIS MORNING. NOTHING BELOW THIS SECTION KNOWS THAT.

Every prior document — this runbook's §5, the memory notes, the whole research corpus — reasons about a **3 GB**
cap. That was never re-verified. Measured and confirmed by the owner 2026-08-18:

- The cap had been set to **1 GB**, which is **below the measured working set of a single query** (one census sort
  measured ~1.4 GiB). At 1 GB those statements cannot succeed at any speed — they spill until they time out. The
  Railway graph showed memory pinned flat at the ceiling with CPU near idle: a box waiting on I/O, not working.
- **Uncapped it reached ~50 GB.** That is page cache, not working set — Postgres fills whatever it is given from a
  43 GB data dir and Railway bills it via cgroup. This is the same mechanism §5's "the 3 GB reading is page cache"
  finding describes, at a larger number.
- **Owner raised it to 2 GB on 2026-08-18 as an explicit bridge, not a permanent setting.** The plan is to do the
  structural work until no statement's working set exceeds 1 GB, then come back down.

`effective_cache_size` is 2 GB, which was a 2× lie at a 1 GB cap and is now merely optimistic (100% of container;
~1.25 GB would be textbook). **Deliberately left alone** — do not "fix" it without measuring, changing planner cost
parameters on a degraded box is how you get a surprise.

### State

**Applied to production and verified:**

| change | evidence |
|---|---|
| `autovacuum_max_workers` 10 → 3 | live, `context=sighup`, no restart. Was a **1.28 GB** worst-case floor against a **1 GB** cap — it exceeded the whole container. Confirmed still 3 on 2026-08-18. |
| `hypopg` 1.4.3 + `pg_buffercache` installed | `CREATE EXTENSION` succeeded; `ALTER SYSTEM` **is** permitted on Railway managed PG |
| alembic `20260817_0025` | hand-applied; `alembic_version` verified |
| **`geo.mv_signal_cell_daily` DROPPED** | 2026-08-18. **Database 43 GB → 37 GB.** Was 6,349 MB (heap 3,796 + indexes 2,553), 24,958,092 rows, measured **1,729 s** rebuild. Zero in-database dependents confirmed before dropping. |
| refresh backoff working | ledger shows `consecutive_failures` incrementing (soil_union 3, signal_observation_day 3, soil_survey_grid 2) instead of churning |
| `mv_feature_observation_day` **now succeeds** | 2026-08-18 19:04 — the 300→900 s timeout raise fixed it. It is no longer one of the failing four. |

**Commits on `main`:** `160388f` (backoff/preflight/unshard) · `44c2133` (drizzle 0030-0032, dormant) ·
`5354df7` (runbook) · `e71e1cd` (the 42P08 fix + stale readiness pin).

**Believed-correct but NOT verified:** whether the 2 GB cap survives a Railway-initiated restart; whether
`ALTER SYSTEM autovacuum_max_workers` survives one. Both should be re-checked after the next deploy.

**Broken / degraded right now:**
- The four agent tools that read `mv_signal_cell_daily` — `sql/agent/signal_value_on_day.sql`,
  `signal_neighbors_in_time.sql`, `signals_near_point.sql`, `nearest_signal_cells.sql` — **are degraded by design** until the Parquet path
  exists. Owner accepted this explicitly.
- Three views still fail every attempt, now backing off correctly: `mv_soil_survey_union` (never once succeeded —
  real bug, see below), `mv_signal_observation_day` (300 s timeout), `mv_soil_survey_grid`.
- `mv_feature_observation_day_axis` reports `skipped_missing`, `refreshed=NEVER` — because `drizzle/0031` shipped
  **dormant**.

**Review ledger:** the research corpus had an ACH crucible + contradiction mapping + adversarial personas
(14 agents). The deploy batch `160388f` was reviewed by audit but **shipped a regression anyway** — see below. The
`e71e1cd` fix was self-verified against production with live evidence. **The partitioning work below has no review
verdict yet.**

### Key context — the non-obvious material

**1. A green test sweep is not evidence on this repo unless `AGRI_TEST_DATABASE_URL` was set.** `160388f` shipped
with 3,062 Python + 1,320 JS tests passing and took **both** refresh lanes down in production. The test that catches
it — `tests/test_strategy_mv_refresh_postgresql.py` — **already existed and was silently skipped**. With the gate
set: 3,170 passed. Check the gate before trusting a pass count. Real-DB recipe: local `agri_sweep` on port **5442**.

**2. The bug class, worth internalising: a named parameter used TWICE in one statement.** In
`sql/jobs/upsert_matview_refresh_state.sql`, `:outcome` is read as the column value and again in the
`consecutive_failures` CASE that 0025 added. **SQLAlchemy renders a repeated named parameter as ONE placeholder**,
so PostgreSQL deduced `$6` from both sites: `ERROR 42P08: inconsistent types deduced for parameter $6 — text versus
character varying`. Raised at **parse** time; the statement never ran once. Fix was
`bindparam("outcome", type_=String)` at `jobs/matview_refresh.py:605`. **`check_relations_exist.sql` was innocent —
an early diagnosis blamed it; do not "fix" it.**

**3. Alembic is NOT run by the deploy pipeline.** No `preDeployCommand`, no migration step in
`services/agri-data-service/railway.json` or its Dockerfile. **Hand-apply against `DATABASE_URL_SYNC` before
pushing.** Drizzle migrations *are* automatic on `plantgeo-main` — but the migrator only executes files listed in
`drizzle/meta/_journal.json`, so `0030`-`0032` shipped inert. Registering them is queued work with preconditions.

**4. No index can rescue the census aggregates. Measured, closed.** `hypopg` showed `status = 'published'` matches
**5,029,620 of ~5.03M rows — over 99.9%**. That predicate has no selectivity, so nothing beats a sequential scan of
the 3,723 MB heap. `ix_features_layer_observation_day` **already exists in production** and the planner correctly
rejects it; forcing it is 1.76% *worse*. `drizzle/0031`'s "next lever" note is **CLOSED — the lever does not
exist.** The 166-row fire-perimeters guard was three orders of magnitude too small to matter.

**5. `hypopg` cannot test GiST** (`access method "gist" is not supported`). So `drizzle/0030`'s composite index is
**build-measure-drop**, not preview-then-build. Reversible but not free.

**6. The jsonb→native-column fix is NOT a speed fix.** `drizzle/0031` measured **286,800 ms vs 283,049 ms** —
marginally slower. It buys an **8.5× cut in peak allocation** (33 B/tuple vs 511) and reliability. On this box the
binding constraint is **peak allocation, never latency.** No fix here makes it quick.

**7. Geometry is stored TWICE.** `geo.features` carries an inline `geom` **and** a `geometry_id` FK into
`geo.geometry`, which has its own `geom`. **All 5,029,850 rows carry both.** 5.03M features point at 3,255,832
dimension rows. And the dimension's forward path is not maintained, so the duplicate is not reliably in sync.

**8. `geo.features` has ZERO inbound foreign keys.** Verified. This is what unblocks partitioning — the constraint
that made the hypertable conversion illegal (PK on `id` carrying FKs) does not apply here.

**9. `water-gauges` is 1,392,454 rows over 953 distinct geometries** — a USGS reading log — and is served by **no
tile function**. It owns **27.7%** of `idx_features_geom`, which every tile query's `&&` leg walks. fire-detections
owns 59.9%, also with no tile function. **87.6% of the shared spatial index belongs to two layers that never use
it**; the five style-baked tile layers own 0.21% of the tree they are forced to walk.

**10. `mv_soil_survey_union`'s real bug** is a missing `ST_CollectionExtract(…, 3)` in the `delineation` CTE at
`drizzle/0029_pre_aggregation_layer.sql:918` — the extract runs *after* the unions, too late to stop a MakeValid'd
GeometryCollection reaching `ST_Union`. **Not** an SFCGAL problem; a backend swap is the wrong lever.

### Decisions (2026-08-18, owner)

- **Do not migrate engines.** Materialize/RisingWave have no geometry type; ClickHouse has no spatial index and
  Martin has no ClickHouse support; VictoriaMetrics is float64-only; DuckDB is single-writer; `pg_ivm` is not among
  Railway's 102 available extensions. Full reasoning and citations: `docs/research/timescale-pivot-2026-08-17/`.
- **Raise the cap to 2 GB as a bridge**, not a destination. Come back to 1 GB after the structural work.
- **Drop `mv_signal_cell_daily` now**, rebuild as Parquet later — chosen over export-first because the export
  itself must read 6.3 GB on a constrained box.
- **Execute partitioning, not just spec it.** Owner chose execution knowing this session already shipped one
  regression from a green sweep.
- **DuckDB over Polars** if/when the Parquet path is built — the consumers are SQL files, and DuckDB has real
  spatial. **Skip Iceberg/DuckLake/R2 Data Catalog** — 15+ months in open beta, no GA date.
- **No schema-per-layer.** Partitions give physical separation; schemas add `search_path` surface for no gain.

### Assumptions

- **The 2 GB cap holds and is enough for the partition-copy batches** · default taken: proceed · to reverse: the
  copy of `fire-detections` (3,012,005 rows) is the one batch that could exceed it; chunk it by day if it does.
- **The three degraded agent tools are not user-visible** · default taken: accept the gap · to reverse: rebuild
  `mv_signal_cell_daily` from `drizzle/0029`, ~1,729 s.
- **`geo.features.geom` is authoritative and the dimension is the stale copy** · default taken: partition on the
  inline `geom` and leave the dimension alone · to reverse: cheap now, expensive after partitioning.
- **~1 concurrent user** · default taken: treat this as a single-query working-set problem, not a concurrency one ·
  **never actually measured** — Railway `http_requests` on `plantgeo-martin`/`plantgeo-main` would settle it.

### Relevant files

- `docs/research/timescale-pivot-2026-08-17/report.md` — the full verdict, 7 sections
- `docs/research/timescale-pivot-2026-08-17/FACTS.md` — **669+ lines of measured ground truth and 9 corrections.**
  Later corrections reverse earlier claims in the same file; the later one always wins.
- `services/agri-data-service/src/agri_data_service/jobs/matview_refresh.py:605` — the `bindparam` fix; `:256`,
  `:267-455` the twelve specs and their staleness ceilings
- `drizzle/0029_pre_aggregation_layer.sql:918` — the soil-union `ST_CollectionExtract` bug
- `drizzle/0030_features_layer_geom_tile_index.sql:5-13` — the 45.6 s BitmapAnd EXPLAIN, and `:32-40` the sound
  rejection of per-layer partial GiST indexes
- `drizzle/0031_observation_day_axis.sql:18-51` — the not-faster measurement; its "next lever" note is now CLOSED
- `docs/research/timescale-pivot-2026-08-17/evidence/hypopg-covering-index-2026-08-17.md` — the plans that closed it

### Environment

- Branch `main`, level with origin at `e71e1cd`. Working tree clean except untracked `docs/research/`.
- Prod: PostgreSQL **18.4**, PostGIS 3.6.4, TimescaleDB 2.29.0 (idle, drop authorized). Database **37 GB** after
  the drop. Railway project **Aevani** `6faaf3ea-ac46-4c8b-bbfe-1351dbb9d990`, env
  `b7cfa813-8a5c-4fcd-80f2-cab736d840a7`. Railway MCP authenticated as owner.
- Prod DSN: `DATABASE_URL_SYNC` in `services/agri-data-service/.env` (gitignored). **Values live there only.**
  Query with `services/agri-data-service/.venv/Scripts/python.exe` — **psycopg2, not psycopg3**: `conn.cursor()`,
  never `conn.execute()`. `SET statement_timeout` on every statement.
- Real-DB tests: gate is `AGRI_TEST_DATABASE_URL`; local `agri_sweep` on port **5442**.
- Installed and unused on prod, all free to try: `h3`, `h3_postgis`, `pg_repack`, `hll`, `roaringbitmap`,
  `postgis_sfcgal`. `pg_stat_statements`/`pg_qualstats`/`pg_stat_kcache` need the **same restart** as the
  TimescaleDB drop — bundle them.

### 0.1 Continuation plan

1. **Execute the `geo.features` LIST partitioning on `layer_id`.** Owner-approved for execution. `geo.features` is
   7,703 MB / 5,025,009 rows across 11 layers sharing one heap, one 311 MB GiST index over **5,028,934** entries,
   one 1,467 MB jsonb TOAST relation, and an **819 MB** jsonb expression index
   (`features_layer_external_id_unique`, larger than the PK and 2.6× the spatial index).
   - **Never `ALTER` in place** — 7.7 GB on a 2 GB box is the same trap as `create_hypertable(migrate_data=>true)`.
     Create new partitioned table → copy **per layer** → swap. Per-layer batching is natural: fire-perimeters
     (166), burn-severity (541), evacuation-zones (643), watersheds (9,396) are instant; only fire-detections
     (3,012,005) and water-gauges (1,392,454) are real batches.
   - PK becomes `(id, layer_id)` — free, because there are zero inbound FKs.
   - **Remodel `water-gauges` during the copy** — 1,392,454 rows over 953 geometries. This alone removes 27.7% of
     the GiST tree.
   - **Fix partition pruning:** SIX Martin-registered tile functions restrict on `l.name` through a join (§0.6 — five of them byte-identical),
     and per `0030:32-40` equivalence classes propagate `f.layer_id = l.id` but never the restriction on `l.name`.
     Resolve `name → layer_id` into a constant first so pruning happens at plan time.
   - **Add a real-DB test with `AGRI_TEST_DATABASE_URL` set before shipping.** This is the specific lesson of
     2026-08-17; do not repeat it.
2. **Fix `mv_soil_survey_union`** — add `ST_CollectionExtract(…, 3)` in the `delineation` CTE, new migration. It has
   never once produced a row.
3. **Register `drizzle/0030`-`0032` in `_journal.json`**, in dependency order, honouring each file's hand-applied
   precondition. `0031` is why `mv_feature_observation_day_axis` reports `skipped_missing`.
4. **Build the Parquet path** for the dropped rollup and repoint the three agent SQL files. Hive-partition by
   **month** (52 files, not 1,560). Spatially sort before writing if geometry ever moves — Parquet has no spatial
   index, only bbox row-group pruning.
5. **Convert the eleven small matviews to incrementally-written tables** — `DELETE WHERE day = :day` + `INSERT`,
   watermark last, one transaction. **Not `ON CONFLICT DO UPDATE`** — upsert cannot delete, so a backfill that
   retracts an observation silently orphans rows.
6. **Drop TimescaleDB + `timescaledb_toolkit`** last, so each earlier change's relief stays measurable. Needs
   `tracking.positions` un-hypertabled, a `shared_preload_libraries` edit, and a restart — bundle the three
   diagnostic extensions into that same restart.
7. **Then lower the cap back toward 1 GB** and confirm no statement's working set exceeds it.

### 0.2 Open questions

- **Does the 2 GB cap survive a Railway restart?** Trigger: the next deploy. Same for `ALTER SYSTEM`.
- **Is `geo.features.geom` droppable in favour of the geometry dimension?** Trigger: before partitioning locks the
  column layout in. Worth answering *now* — it is 311 MB of GiST plus the column's heap share.
- **Real concurrency.** Trigger: if partitioning does not deliver, this premise is the next thing to doubt.
- **Peak memory during a 1,729 s refresh** — now unmeasurable, the relation is dropped. If a rebuild is ever run,
  capture it.


### 0.3 Measured schema of `geo.features` (prod, 2026-08-20)

Total **7,872 MB** = heap 3,790 + indexes 2,563 + TOAST 1,518. Live per-layer counts, which are the
copy batches:

| layer | rows | layer | rows |
|---|---:|---|---:|
| fire-detections | 3,019,709 | watersheds | 9,396 |
| water-gauges | 1,413,932 | evacuation-zones | 648 |
| soil-survey | 238,986 | burn-severity | 541 |
| vegetation | 185,031 | fire-perimeters | 172 |
| sensors | 180,654 | interventions | 2 (0 published) |
| weather-observations | 31,569 | **total** | **5,080,640** |

Columns: `id` uuid NOT NULL DEFAULT `gen_random_uuid()`; `layer_id` uuid NOT NULL; `properties` jsonb
NOT NULL DEFAULT `'{}'`; `status` varchar(20) DEFAULT `'published'`; `review_note` text; `created_at`
timestamptz DEFAULT `now()`; `updated_at` timestamptz DEFAULT `now()`; `geom` geometry(Geometry,4326);
`geometry_id` uuid; `data_available_at` timestamptz.

**Two OUTBOUND FKs** — §0's "zero inbound FKs" is right but was only half the picture. Both must be
re-created on the new parent: `features_layer_id_layers_id_fk (layer_id) -> geo.layers(id) ON DELETE
CASCADE` and `features_geometry_id_fkey (geometry_id) -> geo.geometry(geometry_id) ON DELETE RESTRICT`.
The CASCADE is partition-aware, so layer deletion needs no change.

**One row trigger the plan never mentioned:** `geo_features_sync_geom` BEFORE INSERT OR UPDATE OF
`properties` FOR EACH ROW -> `geo.sync_feature_geom_from_properties()` (`drizzle/0001_handy_riptide.sql:185-187`,
redefined `drizzle/0004_repair_ingested_geometries.sql:4`, `search_path` pinned
`drizzle/0008_geometry_dimension.sql:113`). BEFORE-row triggers are legal on a partitioned parent and
must be re-created there. The write path is not a plain INSERT.

All 11 indexes, by size — every one must be re-created by **exact name**:
`features_layer_external_id_unique` UNIQUE (layer_id, (properties->>'id')) WHERE (properties ? 'id') **830 MB** ·
`ix_features_layer_geom` GIST (layer_id, geom) WHERE published AND geom NOT NULL **449 MB** ·
`idx_features_geom` GIST (geom) **313 MB** · `ix_features_layer_observation_day` **292 MB** ·
`features_pkey` **211 MB** · `ix_features_geometry_id` **158 MB** · `idx_features_layer_updated_at` **75 MB** ·
`idx_features_layer_created_at` **74 MB** · `idx_features_layer_status` **64 MB** · `idx_features_layer` **61 MB** ·
`ix_features_updated_at` **36 MB**.

`ix_features_layer_geom` **exists in production** even though `drizzle/0030` is unregistered — it was
hand-applied. Do not infer applied-state from `_journal.json`.

**Grants: only `postgres`.** No application-role grants to replicate at swap, and RLS is disabled
(`relrowsecurity=false`). One less swap-day trap than expected — verified, not assumed.

**Four indexes become partly redundant once partitioned**, because `layer_id` is constant within every
partition: `idx_features_layer` (61 MB, pure waste), and the leading `layer_id` column of
`idx_features_layer_status`, `idx_features_layer_created_at`, `idx_features_layer_updated_at` (213 MB
combined). Rebuild those three without the leading column and drop the first outright. Separately, per
§0's note, `fire-detections` and `water-gauges` own 87.6% of `idx_features_geom` and are served by no
tile function — per-partition indexing means simply **not** creating the geom index on those two.

### 0.4 BLOCKER — layers are created at runtime, so a DEFAULT partition is mandatory

**This is the single thing that would have taken production down, and no prior document contains it.**

`layersRouter.create` is a `contributorProcedure` — team-editor, **not** admin-only — and mints a
`geo.layers` row at request time: `const [layer] = await ctx.db.insert(layers).values(input).returning();`
(`src/lib/server/trpc/routers/layers.ts:140-149`, insert at `:147`; gate `assertCanCreateInTeam` at `:143`).
`geo.layers.id` is server-minted (`src/lib/server/db/schema.ts:162-164`) — no allowlist, no enum, no
partition registry. Two live UI surfaces call it (`src/components/panels/LayerUpload.tsx:98,109-113`;
`src/components/tools/DrawingToolbar.tsx:201,220` — **CORRECTED 2026-08-20: the path was wrong (`tools/`,
not `map/`) and slice A has since DELETED that file, so `LayerUpload.tsx` is now the only UI surface. The
blocker is unchanged: `layersRouter.create` is a `contributorProcedure` reachable over tRPC with no UI at
all**), and the new id can immediately receive features —
`layerId: z.string().uuid()` is accepted straight from the client (`src/lib/server/trpc/routers/contributions.ts:7-24`).

Without a DEFAULT partition the first feature write into a contributor-created layer fails with
`no partition of relation "features" found for row`: a 500 on `contributions.submitObservation`, and in
ingestion a per-job hard failure that flips the cron exit code
(`services/agri-data-service/src/agri_data_service/ingest/results.py:86-94`). Loud, not silent — but every run.

Python ingestion never creates a layer; it resolves or raises `MissingIngestionLayerError`
(`.../ingest/writer.py:96-98,164-170`). A second layer-creation helper with zero importers exists at
`src/lib/server/services/layers.ts:19-22` — dead today, same hazard if revived.

**Decision: create `geo.features_default` regardless**, plus a periodic drain into real per-layer
partitions. Prior art for the drain exists in this repo for `agri.job_event` (RANGE/day, with an existing
`job_event_default`): `services/agri-data-service/src/agri_data_service/db/maintenance.py`. Optionally
also create the partition synchronously inside `layersRouter.create`'s transaction — but that needs the
app role to hold `CREATE` on schema `geo`, which is **unverified**. The DEFAULT partition is not optional
either way: without it, every future layer-creating code path is one merge away from the same outage.

### 0.5 Write path — the ingestion moves

`[lane]` = subagent-reported, not re-verified.

| file:line | what it does | what must change |
|---|---|---|
| `src/lib/server/trpc/routers/layers.ts:140-149` | mints a layer with no matching partition | **BLOCKER** — see §0.4 |
| `src/lib/server/db/schema.ts:221` | `id: uuid("id").defaultRandom().primaryKey()` — **single-column PK** | **BLOCKER.** -> `.notNull()` + `primaryKey({ columns: [table.id, table.layerId] })` in the trailing config array (`:240-250`). Idiom already used at `schema.ts:94,102,136,395`. Must land in the same PR as the DDL or the next `db:generate` proposes reverting the live PK. |
| `drizzle/meta/_journal.json` | ends at `idx 29`; `0030`/`0031`/`0032` exist on disk, unregistered; `idx 26` has neither entry nor file | **BLOCKER.** `scripts/migrate.mjs:1-45` enumerates from the journal, not the directory — an unregistered file is skipped and **the deploy still reports success**. Also `src/__tests__/security/readiness-migration-contract.test.ts` pins the **last** journal entry's tag + sha256 against `src/lib/server/db/migration-contract.ts` (currently `0029_pre_aggregation_layer`), so any registration forces a contract bump in the same commit. |
| `contributions.ts:37-45` `publishContribution`; `:48-65` `rejectContribution`; `interventions.ts:380-406` `transitionLifecycleState` | `.update(features).where(eq(features.id, ...))` — no `layer_id` in scope | Add a preceding `SELECT layer_id`, then `eq(features.layerId, ...)` in the WHERE. Otherwise every publish probes all N partitions. |
| `interventions.ts:338-374` `castModerationVote` | selects the whole row at `:353-356` (so `feature.layerId` **is** in scope), updates at `:365-371` on `id` only | Cheapest fix on the list — add `eq(features.layerId, feature.layerId)`. Router already has `resolveInterventionsLayerId` (`:128-143`). |
| `scripts/apply-pre-aggregation.mjs:87-90,98-99,216` | `CREATE INDEX CONCURRENTLY ... ON geo.features ...`, then `ANALYZE geo.features` | **Now known broken post-swap — see §0.7.** Must be rewritten for the parent/child topology. `drizzle/0029:72-89` hard-asserts both indexes exist. Note autovacuum does **not** analyze a partitioned parent, so the explicit `ANALYZE` at `:216` becomes load-bearing, not a tidy-up. |
| `scripts/backfill-geometry.sql:31,199-209`; `scripts/rekey-geometry-to-entity.sql:37,152-158` | `LOCK TABLE geo.features ... IN SHARE MODE` then whole-table UPDATE | `LOCK TABLE` on a partitioned parent recurses to every partition — N+1 locks in one transaction against `max_locks_per_transaction`. Both are already-run one-shots still on disk and runnable. Re-scope or retire. |
| `.../sql/ingest/link_feature_geometry.sql:113-118` | `UPDATE geo.features SET geometry_id = ...` | Confirm a `layer_id` predicate is present; add if not. |

**Zero** `SET layer_id =`, **zero** `DELETE FROM geo.features` in `src/`, and **zero**
`TRUNCATE`/`VACUUM FULL`/`CLUSTER`/`REINDEX`/`ctid` against the table. No cross-partition row MOVE exists
today; if a `layer_id` UPDATE is ever introduced it executes as DELETE+INSERT and re-fires the trigger.

### 0.6 Read path — application querying and partition pruning

The decisive question at every site: does it constrain `layer_id` directly (**prunes**), or join
`geo.layers` and filter on `l.name` (**does not prune at plan time**)? Equivalence classes propagate
`f.layer_id = l.id` but never the restriction on `l.name`.

**Six of the seven Martin-registered tile functions read `geo.features`, and all six fail to prune.**
(§0.1 said five — it undercounts.) Registry verified at `infra/martin/martin.yaml:43-67`, `auto_publish: false`:
`geo.burn_severity_tiles` (`drizzle/0015:63-104`, WHERE `:103`) · `geo.evacuation_zone_tiles` (`0015:116-155`, `:146`) ·
`geo.fire_risk_tiles` (`0015:159-197`, `:185`) · `geo.sensor_tiles` (`0015:198-237`, `:225`) ·
`geo.intervention_tiles` (`drizzle/0005:6-38`, `:32` — predates the observation-day column, so not byte-identical to the 0015 four) ·
`geo.watershed_tiles` **detail branch only** (`drizzle/0023:131-207`, `:178`; coarser branches read `watershed_rollup`).
`geo.building_tiles` reads `osm_buildings` and is unaffected. `geo.strategy_recommendations_tiles` exists
in the DB but is **not registered in martin.yaml** — never served.

`CREATE OR REPLACE FUNCTION` keeps names and signatures so `martin.yaml` needs no edit, but **Martin must
be restarted** and its config is baked in by `Dockerfile.martin`, read once at container start. A missing
or broken tile function 404s the *whole* composite and hides **every** layer.

Highest-frequency application readers, all `l.name`-join, all needing the same mechanical fix:

| file:line | why it matters |
|---|---|
| `src/lib/server/services/environmental-read-model.ts:4068-4100` `getMetricAtDate` | fires on **every date-slider scrub** — highest-frequency reader found |
| `...:1373-1402` + `:1430-1451` `getPublishedVegetationIndex` | deepest layer (4 years, 184,409 rows), so the unpruned fan-out is proportionally worst |
| `...:623-638` `getPublishedWeatherForPoint` | KNN `ORDER BY geom <-> ... LIMIT n` over an unpruned Append needs a MergeAppend of per-partition GiST-KNN scans — extra scrutiny |
| `...:324-347`, `:378-397`, `:458-488`, `:510-525`, `:696-726`, `:787-802` | fire-detections / water-gauges / weather readers |
| `src/lib/server/services/usda-soil.ts:974-1006`, `:1059-1099`, `:1159-1199` | needs a module-level resolved-id cache |
| `src/lib/server/services/regional-context.ts:359-383` | fires on point-click |
| `src/lib/server/trpc/routers/wildfire.ts:113-132`; `visualization.ts:106-131`, `:133-160`, `:162-192` | `visualization.*` take a **client-supplied** `layerName` — resolve via cached lookup before touching features |
| `.../sql/agent/feature_value_near_point.sql:104-125`; `fire_history_near_point.sql:103-126` | `:surface_name` is a **bind parameter** — strictly worse for the planner than a literal |
| `.../sql/execution/{load_observations,insert_spatial_cells,select_candidate_cell_keys,corpus_digest}.sql` | `layer.name = :layer_name` join |
| `.../sql/jobs/matview_refresh_watermark_watershed_features.sql:12-14` | the refresh gate for `watershed_rollup` — every tick pays a full fan-out |
| `scripts/export-ndvi-grid-tiles.py:41-48` | `DISTINCT ON` over an unpruned Append — the worst shape on the list |
| `drizzle/0029:807-865` `mv_soil_survey_grid`; `:910-960` `mv_soil_survey_union`; `drizzle/0023:20-98` `watershed_rollup` | matviews carrying the same defect — do not ship it into a new relation |

**The four reference implementations** — every fix above is a mechanical port of these:
`interventions.ts:209-235` `listMySubmissions`, `:253-294` `listProposed`, `regional-context.ts:428-459`
`readCommunityProposals`, `services/ingest.ts:56-64`. All resolve name -> `layer_id` constant first.

**No-layer-predicate reads** (become cross-partition scans; some are correct by design):
`src/app/api/v1/features/route.ts:41-90` pushes `eq(features.layerId, ...)` **only `if (layerId)`** (`:52`) —
public paginated browse, unscoped `ORDER BY id LIMIT/OFFSET` becomes a cross-partition merge sort; either
require `layer_id` or require bbox + cap. `contributions.ts:82-97` `listPendingReview` is deliberately
all-layer (comment `:78-80`) — cannot prune without changing the product requirement; note prod's
`idx_features_layer_status` leads with `layer_id` and will **not** serve a bare `status` filter, so add a
per-partition partial index `WHERE status <> 'published'` or accept the scan.
`.../sql/jobs/matview_refresh_watermark_features_updated_at{,_hourly}.sql` do `max(updated_at)` with no
layer predicate — today an O(1) backwards walk on `ix_features_updated_at`, post-swap a MergeAppend over N
per-partition indexes. **Re-measure**: this gate runs for *every* `geo.features`-backed matview.

The census matviews (`drizzle/0029:140-306`, `:720-731`, `:757-767`; `drizzle/0031:134-154`) are all-layer
**by design** — nothing to fix. Confirm `enable_partitionwise_aggregate` post-cutover.
`geo.mv_layer_feature_stats` is the cheapest post-swap smoke test: refresh it and diff the 11 counts.

**Readiness probe coupling:** `src/app/api/ready/route.ts:55,60` does `to_regclass('geo.features')` and
`to_regclass('geo.features_layer_external_id_unique')`. If the unique index is not re-created at the parent
under that **exact** name, readiness fails and the deploy is marked unhealthy.

`sql/agent/observation_coverage_on_day.sql` and `observation_temporal_neighbors.sql` do **not** read
`geo.features` (they read `geo.v_observation_day_census`); `src/lib/server/services/analytics.ts` does not
either. Recorded so they are not re-audited.

### 0.7 Resolved unknowns — tested against prod 2026-08-20

| question | verdict |
|---|---|
| Is a **partial** UNIQUE index legal on a partitioned parent? (`features_layer_external_id_unique`, 830 MB — the plan is invalid as written if not) | **YES — VERIFIED.** `CREATE UNIQUE INDEX ... (layer_id, ((properties->>'id'))) WHERE (properties ? 'id')` succeeded on a PG 18.4 LIST-partitioned probe table. Global uniqueness holds because one `layer_id` implies exactly one partition. |
| Is `PRIMARY KEY (id, layer_id)` legal? | **YES — VERIFIED** on the same probe. |
| Does `CREATE INDEX CONCURRENTLY` work on a partitioned parent? (lanes disagreed; `drizzle/0030` comments claim "PG14+ supports this") | **NO — VERIFIED FALSE.** `ERROR: cannot create index on partitioned table "..." concurrently`. **The `0030` comment is wrong and `scripts/apply-pre-aggregation.mjs:87,98` is broken post-swap.** Verified workaround: `CREATE INDEX ON ONLY <parent>` (lands `indisvalid=false`) -> per-partition `CREATE INDEX CONCURRENTLY` -> `ALTER INDEX ... ATTACH PARTITION`; the parent flips valid only once **every** partition index is attached **and valid**. |
| Are there grants or RLS to replicate at swap? | **NO — VERIFIED.** Sole grantee `postgres`; `relrowsecurity=false`. |
| Peak memory of a 1,729 s refresh | Still unmeasurable — relation dropped. |
| Does the app role hold `CREATE` on schema `geo`? | **STILL UNKNOWN** — only needed if synchronous partition creation is chosen over the DEFAULT partition. |
| Does execution-time pruning rescue the `l.name`-join sites? | **NOT DERIVED, deliberately.** Plan-time pruning is what the fix guarantees; execution-time pruning is not a substitute inside a plpgsql tile function whose plan is cached across a long-lived Martin session. `EXPLAIN (ANALYZE, BUFFERS)` post-swap before deprioritising any site. |

**Ingestion is live right now.** `pg_stat_activity` showed an active ingest backend mid-batch during this
session, and it is what made the probe `CREATE INDEX CONCURRENTLY` time out at 30 s. The same contention
will queue the swap's `ACCESS EXCLUSIVE` rename — and a queued `ACCESS EXCLUSIVE` blocks **every reader
behind it while it waits**. Set a short `lock_timeout` on the rename and retry rather than letting it pile up.

### 0.8 Swap window

1. **Quiesce writers** — three Railway crons plus the app. Ingest commits per 100-row batch
   (`writer.py:38,300-306`) and per 200-row repair page (`backfill.py:69`), so a stopped cron leaves no
   partial batch; a running one holds `RowExclusiveLock`.
2. **Copy per layer, verify, then rename.** Per-layer `INSERT ... SELECT` must run **outside** the migrator
   transaction — every drizzle `.sql` runs in one transaction (stated at `drizzle/0031:27-28`), which is
   also why no `CONCURRENTLY` can appear inside one. Follow the established pattern: expensive work
   out-of-band, then ship a migration whose only executed DDL is a `DO $$` precondition assert
   (`drizzle/0030:196-224`, `drizzle/0032:41-51`). Assert per-layer counts match before the rename.
3. **Re-create on the new parent by exact name:** both outbound FKs, the `geo_features_sync_geom`
   BEFORE-row trigger, and all 11 indexes (`features_layer_external_id_unique` and `ix_features_layer_geom`
   especially — the readiness probe names the first).
4. **`ANALYZE` the parent and every partition.** Autovacuum does not analyze a partitioned parent; the
   expression index has no statistics until analyzed, and without them the planner reverts to the
   sequential scan the index exists to replace.
5. **Restart Martin; bounce `plantgeo-main`.** Martin's config is read once at start and a stale plpgsql
   plan against the pre-swap OID would serve from the orphaned heap. The app pool is `max: 20` postgres-js
   with server-side prepared statements (`src/lib/server/db/index.ts:7-9`) — bounce rather than reason
   about plan invalidation across a two-step rename.
6. **Un-quiesce ingestion last and watch the first cron exit code** — a missing partition surfaces there.
   Then refresh `geo.mv_layer_feature_stats` and diff the 11 per-layer counts against the pre-swap census.

### 0.9 Parquet bucket — provisioned 2026-08-20

Railway object-storage bucket **`plantgeo-parquet`**, id `79d5b0c0-059a-40a9-a90a-ef8d15bb5828`, region
**`sjc`** (US West), project Aevani, env production. Created for the rollup that replaces the dropped
`geo.mv_signal_cell_daily`. Nothing is written to it yet.

Still to wire: S3 credentials as service variables on the writer + reader services (prefer Railway
reference variables over copied secrets); the export job (Hive-partition by **month**, 52 files, not 1,560);
and the readiness reporting below.

**Readiness surfaces to update — owner chose BOTH:**
- `services/agri-data-service/scripts/readiness.py` — add a Parquet/bucket freshness check as another
  fault-isolated section (every check there is already fault-isolated; a failed query reports and the rest
  of the report still runs).
- `src/components/panels/JobRunnerDashboard.tsx` — surface Parquet rollup coverage in the existing
  `gaps` tab (tabs are `lanes | history | gaps`, `:70`). This is the admin UI at `/admin/jobs`.

Note `layers.getIngestionCoverage` (`src/lib/server/trpc/routers/layers.ts:247`) is **not** a readiness
surface — it is the spatial `INGEST_BBOX` badge. Don't extend it for this.

### 0.10 Deprecation removal plan

Summary of the cleanup triage: of 27 candidates, **13 delete now**, **9 repoint, not delete**, **5 need an
owner call**, **8 rejected** (two for mis-scoped greps).

**Delete now** — reachability proven zero across import graph, barrel files, dynamic-import path strings,
and App-Router convention: `package.json:25` (`routing:serve`, targets a commented-out compose service) ·
`db_check.ts` · the drawing/annotation/measure cluster (`src/components/tools/{AnnotationLayer,DrawingToolbar,VertexEditor,MeasureTool}.tsx`,
`src/hooks/{useDrawing,useMeasurement}.ts`) · the fleet-tracking cluster
(`src/components/tracking/{FleetPanel,GeofenceEditor,RouteHistory,VehicleMarker}.tsx`) · the imagery cluster
(`src/components/imagery/{SplitView,PanoViewer,StreetCoverage}.tsx` **plus `src/stores/imagery-store.ts`**,
which the original hunt missed) · the SSE live-flash subsystem (`src/components/map/LiveIndicator.tsx`,
`src/hooks/useLiveLayer.ts`, `src/stores/realtime-store.ts`) · stores `drawing-store.ts`, `tracking-store.ts` ·
stranded tests `src/__tests__/stores/drawing-store.test.ts`, `src/__tests__/hooks/use-live-layer.test.ts`.

**Do NOT touch:** `src/components/tools/ServiceAreaDrawTool.tsx` — live, rendered at
`src/components/panels/TeamDetails.tsx:6,410`. `src/hooks/useSSE.ts` — live via `MetricsBar.tsx`.

**Order matters:** `src/components/tracking/GeofenceEditor.tsx:5` imports `@/stores/drawing-store`, coupling
the tracking and drawing clusters. Leaves before stores, tests in the same commit as their subject, then
**one** type-check/lint/test/build sweep at the end.

**Repoint, do not delete** — headed by the `mv_signal_cell_daily` consumers. There are **FOUR**, not the
three §0 names: `sql/agent/{signal_value_on_day,signal_neighbors_in_time,signals_near_point,nearest_signal_cells}.sql`.
`nearest_signal_cells.sql:20,118` reads the identical relation and is guarded identically at
`agent/tools.py:901` — a repoint driven off §0's list would miss it. None of them crash: each is guarded by
a `to_regclass` probe (`tools.py:473`, `matview_refresh.py:640`) returning a typed
`pre_aggregated_plane_unbuilt` refusal. **They are the only surviving specification of the rollup's grain and
column set — deleting them destroys the spec for the Parquet replacement.** Delete them last, after Parquet lands.

**One genuinely broken path, fix independently and first:** `scripts/apply-pre-aggregation.mjs:133` lists
`geo.mv_signal_cell_daily` in `POPULATE_ORDER` and `runPhaseB()` issues `REFRESH MATERIALIZED VIEW` with **no
existence guard**, unlike `matview_refresh.py:736`. On a fresh/DR database phase B refreshes eight real views,
then raises 42P01 and exits 1. Add the same `to_regclass` guard, or drop the entry and fix the "nine matviews"
comment at `:117`.

**`drizzle/0029:533` still creates `geo.mv_signal_cell_daily`** and no DROP is recorded in any migration —
the 2026-08-18 drop was out-of-band. A rebuild from migration history silently resurrects a 6,349 MB /
1,729 s relation. **Corrected 2026-08-21:** this originally said "record the drop in a new `drizzle/0033_*`". **`0033` is taken** by the tile-function migration (§0.13 item 2), and the file has since been written as
**`drizzle/0034_record_signal_cell_daily_drop.sql`** — on disk, dormant, unregistered (§0.19.1, §0.19.6 item 43). **Never edit `0029`**.

**Most dangerous rejected deletion:** `src/lib/server/trpc/routers/contributions.ts` + `ContributionQueue.tsx`.
The justification that `ModerationPanel` is a functional equivalent is **false** — `contributions.listPendingReview`
(`:82`) queries features across all layers, `interventions.listProposed` (`:253`) is narrower, and
`submitObservation` (`:7`) has no equivalent at all. `conductor/tracks/community_engagement_completion_20260805/plan.md:14`
still carries Phase 1 as **pending**. That is unfinished scope, not dead code.

**Also stale, rewrite rather than delete:** `infra/railway/README.md:12,15,129-312` describes services that no
longer exist and calls the sole live production DB an unproven "replacement candidate"; `:212` cites
`infra/local-warehouse/create-forecast-roles.sql`, deleted in `3fb9acf`. Its Martin CORS guidance (`:96`) and
`## Deployment order` (`:313`) are still accurate.


---

### 0.11 CRITICAL — the seven matviews follow the OID, not the name

**Found 2026-08-20 while writing the swap driver. It is not in any prior version of this plan, and it is
a silent-wrong-answer bug, not a loud one.**

A materialized view's query is stored as a rewrite rule that references the source relation by **OID**.
`ALTER TABLE ... RENAME` does not rewrite that reference. So after the two-step swap
(`features` -> `features_legacy`, `features_new` -> `features`), every matview built on the old heap
keeps reading **`geo.features_legacy`** — successfully, silently, and forever. No error, no missing
relation, just permanently frozen data diverging from the live table.

Confirmed by `pg_depend`/`pg_rewrite` against production; the live dependents are:

`geo.mv_feature_observation_day` (`drizzle/0029:166`) · `geo.mv_layer_feature_stats` (`:728`) ·
`geo.mv_layer_hourly_activity` (`:763`) · `geo.mv_soil_survey_grid` (`:816`) ·
`geo.mv_soil_survey_union` (`:919`) · `geo.watershed_rollup` (`drizzle/0023:36,176`) ·
plus `geo.mv_feature_observation_day_axis` (`drizzle/0031:142`) once 0031 lands — seven in total.

**Each must be dropped and re-created from its migration after the swap.** There is no swap without it,
so the driver cannot refuse on it; `scripts/partition-features.mjs --phase=plan` enumerates the live list
from the catalog and `--phase=swap` prints it as a `!!` block. **This needs an owner decision on
sequencing**, because re-creating them is a full refresh of each — and `mv_soil_survey_union` has never
once produced a row (§0.10), so it will re-create empty and that is expected, not a regression.

Note this also means the post-swap smoke test in §0.8 step 6 is *invalid as written*: refreshing
`geo.mv_layer_feature_stats` and diffing the 11 counts would read the **legacy** heap and match
trivially. Re-create it first, then diff.

### 0.12 What landed 2026-08-20 (code only — nothing applied to production)

Six parallel slices with pre-declared exclusive file ownership; no collisions.

| slice | result |
|---|---|
| **A — deletions** | 21 files deleted + the `routing:serve` script removed from `package.json`. Every basename and exported symbol grepped repo-wide before removal; all surviving hits were documentation, never live code. `ServiceAreaDrawTool.tsx` and `useSSE.ts` verified live and kept. Post-delete dangling-import sweep: zero matches. |
| **B — service reads** | One shared `resolveCachedLayerId()` (module-level cache, caches hits only, never caches a miss — so a runtime-created layer per §0.4 is still found next call) + `clearLayerIdCache()`. Applied at 10 sites in `environmental-read-model.ts`, 3 in `usda-soil.ts`, 1 in `regional-context.ts`. Each resolver miss short-circuits to the exact empty terminal state the old zero-row join produced. |
| **C — routers** | Read-path pruning in `wildfire.ts` + the three `visualization.ts` readers; write-path `layer_id` scoping in `publishContribution`, `rejectContribution`, `castModerationVote`, `transitionLifecycleState`. Kept the `geo.layers` join where `featureVisibilityCondition` genuinely reads `isPublic`/`teamId`, but moved its predicate from `l.name` to the resolved `l.id` — pruning fixed, authorization unchanged. |
| **D — schema + DDL** | `schema.ts` composite PK `(id, layer_id)`. New `scripts/partition-features.mjs` (10 idempotent phases: plan/create/copy/index/trigger/verify/swap/analyze/adopt/rollback) and `docs/pending-migrations/0033-features-partitioning.md`. |
| **E — agri SQL** | `apply-pre-aggregation.mjs` phase-B existence guard (the 42P01 crash) **and** topology-aware index builds using the verified `ON ONLY` -> per-partition CIC -> `ATTACH` path. Pruning fixes in 4 `sql/execution/*`, 1 `sql/jobs/*`, 2 `sql/agent/*`, and `export-ndvi-grid-tiles.py`. |
| **F — tile functions** | `drizzle/0033_tile_function_partition_pruning.sql`, **shipped dormant**. Each of the six functions diffed against its live body: exactly three hunks each, nothing else. Signatures, volatility, `PARALLEL SAFE`, `search_path`, and every predicate preserved. |

**Non-obvious decisions made inside the slices, recorded so they are not re-litigated:**

- **`idx_features_geom` ceases to exist as a name.** A parent index requires a matching child on *every*
  partition, but `fire-detections` and `water-gauges` deliberately get no geom index. So it becomes
  per-partition `features_<slug>_geom_idx` on the other ten. Grep confirms nothing probes that name — but
  stale prose references remain in `src/lib/server/AGENTS.md:1032`,
  `services/agri-data-service/.../agent/AGENTS.md:220`, `agent/tools.py:248`,
  `sql/agent/feature_value_near_point.sql:33`, `fire_history_near_point.sql:23`,
  `tests/test_agent_graph.py:589`. Comments only, no functional break.
- **Index names collide, constraint names do not.** Index names are unique per *schema*, so incoming
  indexes are built with a `_swap` suffix and renamed inside the swap transaction. A failed rename rolls
  the whole transaction back — a failed swap, never a corrupt one.
- **The trigger is created after the copy, not before.** `sync_feature_geom_from_properties()` runs
  `ST_MakeValid` since `drizzle/0004`, so copying with it attached costs a parse per row *and silently
  rewrites* geometries stored before that repair landed.
- **Copy chunking walks `created_at` boundaries via `ORDER BY ... OFFSET n LIMIT 1`** off
  `idx_features_layer_created_at` — an index-only seek with flat memory. `ntile`/`percentile_disc` would
  tuplestore the whole layer, which is exactly what a 2 GB cap cannot afford.
- **Tile functions resolve the layer id at call time, not baked into DDL** — respects `0030`'s explicit
  rejection of environment-specific ids in replayed DDL, and a 12th layer works with no new DDL.
- The three `layer_id`-leading composite indexes (§0.3, ~213 MB of redundancy) are re-created
  **unchanged** for now — narrowing them changes plan shapes on read paths rewritten in this same batch.
  Recorded as a measured follow-up; the index list is a data structure, so it is a one-line change later.
- `water-gauges` is **not** remodelled during the copy (§0.1 proposes it). The copy is byte-for-byte on
  purpose; remodelling during a swap mixes two failure domains.

### 0.13 Gated, not done — read before deploying anything

> **STATUS AS OF 2026-08-21 (§0.19.1 has the evidence).** Item **2 is RESOLVED** —
> `drizzle/0034_record_signal_cell_daily_drop.sql` is written and dormant. **Caveat on `0034`, found
> 2026-08-21:** its header comment (lines 21-22) asserts the four agent tools "throw a hard database error
> (relation does not exist)". **That is false** — they return a typed refusal (§0.16.4, `tools.py:473`/`:901`).
> The file's *instruction* ("do NOT wrap these four callers in an existence guard") is still right, but for
> the opposite reason: **the guard already exists.** **Correct those two comment lines before registering
> `0034`** (§0.19.6 item 43) — as written they invite a future agent to "fix" a guard that is already there,
> or to remove it, which would create the very hard error the header claims. The DDL itself is correct and
> unaffected. Item **3 is WRITTEN, NOT RESOLVED** —
> `drizzle/0036_features_partitioned_precondition.sql` is written and dormant, and it asserts more than
> this item asked for (composite PK column order, and that `geo.features_default` is the *registered*
> DEFAULT partition, not merely a same-named table). **But it asserts `relkind='p'` on `geo.features` and
> RAISEs otherwise, so it is registrable ONLY if the partition swap actually proceeds** — and §0.18.8 item 1
> / §0.19.7 recommend **shelving** the swap. Shelve the swap and `0036` becomes permanently dormant dead
> code, never registrable. That consequence is now carried in §0.19.7's decision row. Item **4 has been measured and the answer inverts
> it**: turning the two GUCs on changes nothing at all until the three census matviews are rewritten to
> aggregate before joining `geo.layers` (§0.16.7) — flipping them at cutover, as this item says, would
> prove nothing. Items **1, 5, 6, 7 remain open** exactly as written. **Newly gated since:**
> **(8)** `drizzle/0035_soil_survey_union_collection_extract.sql` is written and dormant and needs its
> *refresh outcome* watched, not just a clean DDL apply. **(9)** `scripts/recreate-features-matviews.mjs`
> and `scripts/data-quality-report.mjs` are written and have **never been run against production**.
> **(10)** Every registered migration must re-pin `src/lib/server/db/migration-contract.ts` in the same
> commit or `Dockerfile:67`'s `npm test` gate fails the build — true of item 1 and of every migration
> above. **(11)** Martin reports `v0.7.0` in `application_name` against `Dockerfile.martin:1`'s
> `martin:1.10.1`; settle it with `railway logs -s plantgeo-martin | head` before trusting any
> `martin.yaml` block other than `cors` and `postgres.functions`. **(12)** `plantgeo-ingest-cron` is
> **CRASHED** and `plantgeo-cron-soilgrids` **FAILED** on the current deploy — every "ingestion is live"
> premise in this runbook currently rests on a crashed service. **(13)** The whole partitioning premise is
> now an owner decision, not a scheduling one (§0.18.8 item 1, §0.19.7).

1. **`drizzle/0033` is NOT registered in `_journal.json`, deliberately.** `0030`-`0032` are already
   dormant and this repo ships migrations dormant on purpose (commit `44c2133`). Registering `0033`
   means the next deploy replaces all six tile functions, and **a single bad tile function 404s the whole
   composite and hides every layer**. Registration is an owner decision with two preconditions: register
   it *and* bump `src/lib/server/db/migration-contract.ts` in the same commit (the readiness test pins the
   last journal entry's tag + sha256), then **restart Martin** and fetch one tile per rewritten source
   **with an `Origin` header** before calling it done. Nothing in the pipeline restarts Martin.
2. **`0033` is claimed by the tile-function migration**, so the migration recording the out-of-band
   `DROP MATERIALIZED VIEW geo.mv_signal_cell_daily` (§0.10) must be **`0034`**. `drizzle/0029:533` still
   creates that relation and no DROP is recorded anywhere; a rebuild from migration history silently
   resurrects a 6,349 MB / 1,729 s view.
3. **The precondition-assert migration for the partitioned table is not written.** It should assert
   `relkind='p'` on `geo.features`, plus `features_layer_external_id_unique` existing and `indisvalid`,
   following `drizzle/0030:196-224`. It was in no slice's scope.
4. **`enable_partitionwise_aggregate` / `_join` are OFF** (§0.7). Without them the censuses gain nothing
   from partitioning. Turn on and re-measure as part of the cutover, not after.
5. **`max_locks_per_transaction` is 128** and the swap touches ~145 relations — index creation must be
   chunked across transactions, and the two ops scripts' `LOCK TABLE geo.features` now takes 13 locks.
6. **Still unpruned, out of every slice's scope:** `usda-soil.ts` `persistCell` (~`:884-926`) joins
   `geo.layers` by name in three places on the **write** path — not in §0.5's table, found during slice B.
   And `scripts/backfill-geometry.sql:31,199-209` + `scripts/rekey-geometry-to-entity.sql:37,152-158`
   still `LOCK TABLE geo.features IN SHARE MODE`.
7. **Not started:** the Parquet export job, the DuckDB repoint of the four agent SQL tools, and the
   readiness wiring in `readiness.py` + `JobRunnerDashboard`'s `gaps` tab (§0.9). The bucket exists and is
   empty; no credentials have been wired to any service yet.


---

### 0.14 Review verdict + sweep evidence, 2026-08-20

**Sweep — GREEN, and gated. This is the number to trust.**

| suite | result |
|---|---|
| Python, `AGRI_TEST_DATABASE_URL` **and** `PGBIN` set | **3,170 passed, 3 skipped**, exit 0 |
| TypeScript `tsc --noEmit` | exit 0 |
| `eslint .` | exit 0 (warnings only, all in the vendored minified `static/datastar.js`) |
| `vitest run` | **1,305 passed, 13 skipped**, exit 0 |
| `next build` | exit 0 |

3,170/3 matches the documented fully-gated baseline exactly; an ungated run is ~3,062. **Two near-misses
worth keeping**, both of which would have produced a fake green:
- The gate looked *unavailable* at first — `agri-sweep-db` binds IPv4-only, so `localhost` resolves to
  `::1` and refuses, and its credentials are container-local, not the ones in CLAUDE.md. See
  [[agri-real-db-testing-gap]].
- A first sweep attempt reported `exit code 0` while running **zero** tests: pytest rejected an
  unrecognised `--timeout` flag and the exit status came from the `tail` at the end of the pipe.
  **Never read an exit code through a pipeline.**

**Adversarial review: `/code-review high`, separate context, CHANGES-REQUIRED — 7 findings.** Six were
dispatched for fix; the seventh is an owner decision recorded below. What the reviewer independently
verified clean is itself useful: no dangling imports to the 19 deleted modules; `layers.name` is UNIQUE,
so every scalar-subquery rewrite is row-for-row equivalent to the join it replaced; no FK and no
`onConflict` targets `features.id`, so the PK change is safe at the ORM layer; `drizzle/0033`'s six tile
functions reproduce the original predicates and MVT attribute lists exactly; and `partition-features.mjs`'s
chunk predicates are disjoint and exhaustive **including** the `created_at IS NULL` bucket.

| # | severity | finding |
|---|---|---|
| 1 | high | `visualization.ts:10` caches a **null miss** for the process lifetime — a runtime-created layer renders permanently empty. Inconsistent with the sibling resolver, which caches hits only. |
| 2 | high | `environmental-read-model.ts:300` `layerIdByName` has **no invalidation**, but `layersRouter.delete` (`layers.ts:178`) falsifies the "an id never changes once minted" premise: delete + recreate under the same name silently empties ~10 readers until restart. |
| 3 | medium | `apply-pre-aggregation.mjs:282` post-swap builds a **second** child index per partition then fails on `ATTACH`, because `partition-features.mjs` already attached them — aborting before `ANALYZE` and leaving duplicates. |
| 4 | medium | `wildfire.ts:136` `getInterventions` is a public read that now **throws** where it previously returned `[]`. |
| 5 | medium | `interventions.ts:394` dropped its post-update NOT_FOUND throw; a delete racing the new pre-flight SELECT resolves to `undefined`. Same shape in `castModerationVote` and both `contributions` mutations. |
| 6 | low | `features/route.ts:52` enforces a 10k offset ceiling while `parsePageValue` still advertises 1,000,000. |

**All six FIXED and re-verified 2026-08-20** — `tsc --noEmit` exit 0, `vitest run src/__tests__` 1,305
passed / 13 skipped. Notes worth keeping:
- **#1 + #2 collapsed into one resolver.** `visualization.ts`'s second cache was deleted outright rather
  than patched, so there is now one resolver, one miss policy, one expiry, one invalidation path. Its
  200-entry bound moved into the shared resolver, which is where every client-supplied name now lands.
- **#2's strategy is TTL-first (60 s), explicit-evict-second, and that ordering is the point.** An
  explicit evict alone cannot be correct here: the cache is per-process, so a delete handled by one Node
  process leaves every other process stale until restart — and `geo.layers` rows also vanish out-of-band
  via `features_layer_id_layers_id_fk ON DELETE CASCADE`, psql, and the ops scripts, none of which pass
  through any call site. The TTL heals all of those with no call site at all. The evict is kept because
  it makes the common in-process delete heal instantly instead of serving up to 60 s of empty reads.
- **A rename was found that the review did not name.** `layersRouter.update` (`layers.ts:151-172`) can
  rename a layer (`layerUpdateSchema` is `layerCreateSchema.partial()`), stranding the OLD name in the
  cache pointing at a live id. The old name is not in scope there. TTL covers it; an explicit-evict-only
  design would not have. Both mutation paths are now wired: `delete` returns the name and calls
  `invalidateLayerId`; `update` calls `clearLayerIdCache()` when `input.name` is present.
- **#3 detects by ATTACHMENT, not by name** — `partition-features.mjs` creates parent indexes with a
  plain `CREATE INDEX`, so PostgreSQL generates and attaches the children itself under names this script
  could never guess. An attached-but-invalid child is reported loudly rather than worked around, because
  an attached child cannot be dropped while its parent requires it.
- **#5 keeps a deliberate asymmetry in `contributions.ts`**: a *pre-flight* miss still returns `undefined`
  (what this router has always answered for a feature that is simply gone), while passing the pre-flight
  and then matching nothing — the actual race — now throws. `ContributionQueue.tsx:76-88` only wires
  `onSuccess`, so throwing on the former would leave a deleted row on screen.

**FINDING 7 — NOT FIXED, NEEDS AN OWNER DECISION.** `schema.ts:244` now declares the composite PK
`(id, layer_id)` while the partition swap is deliberately **not applied**, so `schema.ts` and production
disagree. The hazard is real in both directions and there is no configuration that is safe in both:

- **Leave it composite (current state):** the next `npm run db:generate` emits a `DROP`/`ADD PRIMARY KEY`
  against the live **5.08M-row unpartitioned heap**, inside a single-transaction migration that the deploy
  pipeline runs automatically. On a 2 GB box that is not survivable.
- **Revert it to single-column:** safe today, but the moment the swap runs, `db:generate` proposes
  reverting production's PK back — the failure mode `slice D` warned about.

The coupling is unavoidable: **`schema.ts` must flip in the same commit as the swap.** Until the swap is
scheduled, treat `db:generate` as blocked. `migration-contract.ts` still pins `0029`, which is correct and
intentional — `drizzle/0033` is dormant (§0.13).

**RESOLVED 2026-08-20 — REVERTED to single-column `.primaryKey()`, `tsc --noEmit` exit 0.** The deciding
argument is that the swap is **not imminent**: it is gated behind the `enable_partitionwise_aggregate`
re-measurement (step 4 of the plan, which may refute the premise before 7.7 GB is moved), behind quiescing
three crons, and behind the §0.11 owner decision on matview sequencing. Both hazards are real, but they are
not symmetric in *when* they fire. Composite-while-prod-is-single-column arms a routine, unattended action
— any `db:generate` by any developer or agent emits `DROP`/`ADD PRIMARY KEY` against the live 5.08M-row
heap in an auto-deployed single-transaction migration. Single-column arms nothing today; its hazard fires
only *after* the swap, at the exact moment the runbook already requires `schema.ts` to flip anyway. So the
revert restores the invariant that makes `db:generate` safe — **`schema.ts` describes production** — and
moves the remaining risk onto a step that is already documented as mandatory. The composite PK now lives as
a comment at `schema.ts:242-245` naming the swap as its trigger; `docs/pending-migrations/0033-features-partitioning.md`
is unchanged and still specifies `(id, layer_id)` as the target. `db:generate` is **no longer blocked**.


---

### 0.15 First executable step taken — `--phase=plan` against production, 2026-08-20

Committed as `79ae3be` (code) + `67a2f06` (runbook); working tree clean. Then
`node scripts/partition-features.mjs --phase=plan` — **read-only, exit 0, no column drift**. It
connects via `PARTITION_DATABASE_URL`, which takes precedence over `DATABASE_URL` precisely so an
operator can point it at prod without exporting the DSN the app would pick up.

**Swap state: not started.** `geo.features_new` absent, `geo.features_legacy` absent — a clean slate,
so no earlier partial attempt has to be reconciled.

**The census has MOVED, and that is the most useful thing this run produced.** Live total is
**5,092,022** rows against the 5,080,640 recorded on 2026-08-20 — **+11,382 in hours**:

| layer | recorded 2026-08-20 | live now | chunks |
|---|---|---|---|
| fire-detections | 3,019,709 | **3,022,314** | 14 |
| water-gauges | 1,413,932 | **1,417,935** | 7 |
| sensors | 180,654 | **184,733** | 1 |
| vegetation | 185,031 | 185,064 | 1 |
| weather-observations | 31,569 | 32,223 | 1 |
| evacuation-zones | 648 | 651 | 1 |
| fire-perimeters | 172 | 177 | 1 |
| soil-survey / watersheds / burn-severity / interventions | unchanged | 238,986 / 9,396 / 541 / 2 | 1 each |

This is §0.7's "ingestion is live right now" made concrete: **seven of eleven layers grew**. Two
consequences. The copy is a moving target, which is what `--phase=copy --catchup` exists for — the
quiesce in §0.8 is not optional politeness. And **`--phase=verify`'s per-layer count equality can only
be trusted against a quiesced database**; run against a live one it will fail on the two big layers and
that failure is correct, not a bug in the driver.

Also note `interventions` now has **2 rows** — it was the last provably empty layer
([[plantgeo-empty-layers-have-no-producer]]). It no longer is.

**Dependents: the catalog reports SIX, not seven.** `mv_feature_observation_day` ·
`mv_layer_feature_stats` · `mv_layer_hourly_activity` · `mv_soil_survey_grid` · `mv_soil_survey_union` ·
`watershed_rollup`. This **confirms** §0.11 rather than contradicting it — the seventh,
`mv_feature_observation_day_axis`, exists only once `drizzle/0031` is registered, and it is still
dormant. **The count is a function of registration order**: register `0031` before the swap and the
re-create list becomes seven. Re-run `--phase=plan` immediately before `--phase=swap` and use *that*
list, never this one.

`geo.layers` holds 11 layers; the DEFAULT partition covers anything minted after (§0.4).


---

### 0.16 Data-quality and QA assessment, 2026-08-20/21

Twelve read-only probes and audits ran against production between 2026-08-20 and 2026-08-21. Every number
below carries the command or catalog view that produced it. **Two adversarial lenses re-ran a sample of the
claims against live production; findings that survived both are marked CONFIRMED, findings measured only
once are marked UNVERIFIED, and two headline claims were REFUTED outright and are corrected here rather
than deleted.** §0.11's convention applies: a silent wrong answer is worse than a loud one, so an honest
uncertainty marker beats a confident sentence.

#### 0.16.1 The census — three different totals, all correct

| source | total | how obtained |
|---|---|---|
| `pg_class.reltuples` | **5,025,009** | catalog estimate, no scan. Stale by definition — `last_autoanalyze` is NULL on `geo.features`. |
| `geo.mv_layer_feature_stats` | **5,091,902** | matview, refreshed 2026-08-21 00:41 UTC. Published rows only. |
| exact per-layer `count(*)` | **5,092,112** | eleven `SELECT count(*) WHERE layer_id=$1`, index-assisted via `idx_features_layer`, run 2026-08-20 |

The three differ because ingestion is live (§0.15 measured +11,382 rows in hours) and because the matview
counts `status='published'` while the per-layer scan does not. **Do not reconcile them — cite which one you
used.** A single unfiltered `GROUP BY layer_id` over the whole table times out at 30 s on this box and has
now failed for three separate agents; `readiness.py`'s own `geo_features_by_layer` check fails the same
way. Use `mv_layer_feature_stats` when a snapshot is acceptable, and per-layer `count(*)` with a `layer_id`
predicate when it is not.

Published rows per layer, from `geo.mv_layer_feature_stats` (2026-08-21 00:41 UTC) — CONFIRMED, and within
0.01% of the independent per-layer exact counts:

| layer | published rows | share |
|---|---|---|
| fire-detections | 3,022,196 | 59.4% |
| water-gauges | 1,417,935 | 27.8% |
| soil-survey | 238,986 | 4.7% |
| vegetation | 185,064 | 3.6% |
| sensors | 184,733 | 3.6% |
| weather-observations | 32,223 | 0.6% |
| watersheds | 9,396 | 0.2% |
| evacuation-zones | 651 | — |
| burn-severity | 541 | — |
| fire-perimeters | 177 | — |
| interventions | **0 published** (2 rows, both `status='approved'`) | — |

**The number that reorganises everything: 4,842,151 of 5,091,902 published rows — 95.1% — are time-series
observations, not features.** Fire-detections, water-gauges, vegetation, sensors and weather-observations
are all append-only reading logs. Only 249,751 rows (4.9%) are genuine entities. CONFIRMED. §0.18 is built
on this one fact.

`pg_stats` on the same table: `properties` avg_width **479 bytes (61% of the row)**, `geom` 198 (25%), the
remaining seven columns 8-16 bytes each — ≈780 bytes/row heap. `layer_id` correlation **0.235** with
n_distinct 10; `geom` correlation 0.044.

#### 0.16.2 The three observation planes

Checking one plane and concluding a lane is dead is a documented bug class here
([[agri-zero-landing-bug-class]]). All three, measured 2026-08-20:

| plane | rows | how |
|---|---|---|
| `agri.signal_observation` | ~46,068,872 | `pg_class.reltuples` — an exact count of a 46M-row / 26 GB heap is not affordable on this box |
| `agri.forecast_observation` | 184,409 | exact `count(*)` (116 MB relation) |
| `agri.normalized_source_feature` | **0** | exact `count(*)` — the plane is completely empty in production |

`normalized_source_feature` being empty is not a bug found; it is the accurate current state, recorded so a
future audit does not re-derive it. UNVERIFIED whether anything ever wrote to it.

Canonical landing census (`seasonal_source_landing_census.sql`, run verbatim, 30 s cap) by `data_source`:

| source | releases | landed | plane |
|---|---|---|---|
| `nasa-power-daily` | 1,600 | 1,600 | signal |
| `open-meteo-era5-land-archive` | 202 | **194** | signal — **8 releases land nowhere** |
| `open-meteo-era5-archive` | 8 | 8 | signal |
| `sentinel2-ndvi-l2a` | 1 | 1 | **forecast, 0 signal** |
| `kaggle-ghisaconus-mirror` | 1 | **0** | static mirror, by design |

`sentinel2-ndvi-l2a` landing in `forecast_observation` and not `signal_observation` is exactly the
false-negative shape the bug class warns about: a signal-only check calls the live NDVI lane dead. The 8
zero-landing ERA5-Land releases were not drilled to `release_id`; that is cheap and unstarted.

Per-source freshness via `agri.source_release` (avoids scanning `signal_observation`):
`nasa-power-daily` observed_to 2026-08-06 · `open-meteo-era5-archive` 2026-08-02 ·
`open-meteo-era5-land-archive` 2026-08-02 · `sentinel2-ndvi-l2a` 2026-08-05 ·
`kaggle-ghisaconus-mirror` 2015-12-31 (static). **Climate/weather ingestion is stalled ~11 days across all
three Open-Meteo/NASA sources** — a live stall, not a design gap, and every `climate-field-*` signal in
`geo.v_observation_day_census` is frozen at 2026-08-06 (one at 2026-05-31) because of it.

#### 0.16.3 Freshness and rot — which layers are moving

Per-day `created_at` counts over a 90-day window, per layer, `layer_id`-filtered (all completed under
13 s). CONFIRMED.

| layer | verdict |
|---|---|
| fire-detections | **healthy** — 08-03 ramp, 08-09 backfill peak 871,843, steady 1,357-4,278/day since 08-13 |
| water-gauges | **healthy** — 08-08 backfill peak 660,932, steady 7,977-17,674/day since 08-10 |
| sensors | **healthy, declining** — peak ~14k/day mid-month, now ~7-9k/day. Worth watching. |
| weather-observations | **healthy** — steady 630-2,528/day. **This is the NWS `geo.layers` layer, NOT the Open-Meteo/NASA climate signals of §0.16.2, which are STALLED ~11 days.** The same word names both; they are different lanes with different producers, and reading this row as "climate ingestion is healthy" is wrong. |
| vegetation | **healthy** — 08-05 backfill 182,903, then 18-93/day (correct for a 5-day revisit) |
| fire-perimeters | **healthy** — scattered but continuous, 1-111/day |
| evacuation-zones | **healthy** — thin but continuous, 7-381/day |
| burn-severity | **10 days static** — rows only on 08-05 (478) and 08-10 (63) |
| soil-survey | **10 days static** — 08-05/06/08/10 only |
| watersheds | **13 days static, exactly ONE load day** — all 9,396 rows on 08-07 |
| interventions | **dead** — 2 rows on 08-06, nothing since; both stuck `approved` |

For three of those the staleness is arguably correct — HUC12 boundaries and soil surveys do not change
daily — but **that intent is written down nowhere**, and `validation/models.py:143-146` declares
soil-survey, watersheds, burn-severity and interventions `kind="reference"` with **no
`publication_cadence_days`**, so `validate-streams` applies *zero* staleness check to exactly those four.
The layers that most need an alarm are structurally incapable of raising one. And `watersheds` has a
complete, working producer — `ingest/watersheds.py`, CLI verb registered at `ingest/commands.py:264-267` —
that **no cron and no lane ever invokes**: it is absent from `ingest-all`'s job list (`:484-497`) and there
is no `infra/cron-watersheds` directory.

Two corrections to claims held elsewhere in this runbook and in memory:

- **"Only vegetation goes back 4 years" is true for `created_at`, false for `observed_at`.** Sampled
  `properties->>'observedAt'` spans: fire-detections **2000-11-02 → 2026-08-20** (6× vegetation's depth),
  vegetation 2022-08-05 → 2026-08-20, burn-severity 2020-11-24 → 2024-08-22, evacuation-zones 2025-04-14 →
  2026-08-20. The old claim holds only for "when PlantGeo first held a row", never for event depth. Any UI
  or agent-tool logic assuming only vegetation supports a multi-year slider is wrong.
- **`fire-perimeters` is missing `observedAt` on 100% of its 177 rows** (exact, full-layer scan). Its own
  tile function reads that key, so every MVT feature Martin serves for it carries `observed_at=NULL`, and
  `geo.feature_observation_day(properties)` almost certainly returns NULL for every row — meaning
  fire-perimeters likely never matches a slider-selected date at all. Plausible root cause of the unowned
  §9 D11 "abrupt drop". UNVERIFIED as causation; the confirming query is
  `SELECT count(*) FROM geo.features WHERE layer_id=<fire-perimeters> AND geo.feature_observation_day(properties) IS NULL`.

Also: **`data_available_at` is 100% NULL on all eight layers measured so far — 467,040 rows, 9.2% of the
table. The other three layers hold 90.8% of the rows and are UNMEASURED.** *(Rescoped 2026-08-21. The
original sentence read "100% NULL on every layer that could be scanned … across the board", which
generalised from 3.8% of the rows to all of them. It is corrected rather than deleted.)*

| status | layers | rows | how |
|---|---|---|---|
| **100% NULL, CONFIRMED** | burn-severity 541/541 · evacuation-zones 651/651 · fire-perimeters 177/177 · interventions 2/2 · vegetation 185,064/185,064 · watersheds 9,396/9,396 | 195,831 (3.8%) | full-layer scan, 2026-08-20 |
| **no non-NULL row exists, CONFIRMED** | weather-observations (32,223) · soil-survey (238,986) | 271,209 (5.3%) | existence probe, 2026-08-21 — scan **completed** inside 10 s and returned no row |
| **UNMEASURED** | fire-detections (3,022,196) · water-gauges (1,417,935) · sensors (184,733) | **4,624,864 (90.8%)** | existence probe **timed out at 10 s** on all three; there is no supporting index |

The exact probe, which is cheap because it stops at the first hit rather than counting (a positive answer
returns fast; only a negative answer has to scan):

```sql
SET statement_timeout = '10s';
SELECT 1 FROM geo.features WHERE layer_id = <id> AND data_available_at IS NOT NULL LIMIT 1;
```

A timeout here is **not** evidence of NULL — it is evidence that no non-NULL row was found in the first 10 s
of an index-ordered scan. The three unmeasured layers are precisely the high-volume ingestion lanes most
likely to have been wired to set the column, so this is the wrong 90% to be guessing about.

`drizzle/0025` added it as the ML leakage boundary. **On the 9.2% measured, any backtest filtering on it is
a no-op.** Whether that holds for the remaining 90.8% — and therefore whether `0025`'s leakage boundary is
as dead as this runbook has been recording — is **open**. Closing it needs either the probe above run
without a timeout in a maintenance window, or a partial index. Carried in §0.19.8.

**Geometry quality is good, and it is the one cheerful result in the audit.** `TABLESAMPLE SYSTEM(0.2)`
across the whole table joined to layers: **0 invalid geometries, 0 NULL `geom`, 0 NULL `geometry_id`, 1
distinct SRID** in every sampled bucket, and exact full-layer censuses on the small layers confirm it.
`geometry_columns` reports `geometry(GEOMETRY,4326)`, `coord_dimension=2` — enforced by column typmod for
every row, not just the sample. Duplicate `properties->>'id'` is **structurally impossible** wherever the
key exists: `features_layer_external_id_unique` is `indisvalid`, `indisready`, `indisunique`. The one
exception is `interventions`, whose 2 rows have **NULL `geometry_id`** and **no `id` key at all** —
confirming they are ungoverned seed rows, not producer output.

Tile-function key coverage for the six layers Martin reads: burn-severity 0/541 missing a required key,
evacuation-zones 0/651, sensors 0/184,733, watersheds 0/9,396 — clean. `interventions` 2/2 missing,
`fire-perimeters` 177/177 missing (the `observedAt` gap above).

Sampling note, itself evidence about the box: `TABLESAMPLE SYSTEM` at **3-5% reliably times out at 30 s**
on `geo.features` regardless of target layer, while **0.2-0.5% completes in 1-11 s**. Calibrate accordingly.

#### 0.16.4 Matview refresh state — three views have never once worked

All 12 `MATVIEW_REFRESH_SPECS` (`jobs/matview_refresh.py`) cross-referenced against
`agri.matview_refresh_state` and `pg_class`, 2026-08-20. CONFIRMED. The three
`mv_strategy_recommendations_*` rows in the same table belong to the separate `strategy-mv-refresh` lane
and are excluded from the 12.

| state | views |
|---|---|
| **healthy** (6) | `mv_layer_feature_stats` (41,865 ms, 11 rows) · `mv_layer_hourly_activity` (5,069 ms, 446 rows) · `mv_drought_release_index` · `mv_feature_observation_day` (**428,066 ms = 7.1 min**, 11,225 rows) · `mv_drought_observation_day` · `agri.mv_forecast_ml_daily_serving` (0 rows — empty but not failing) |
| **standing failure** (3) | `mv_signal_observation_day` — 5 consecutive failures, **never once succeeded**, 301 s against a 300 s cap · `mv_soil_survey_grid` — 4 failures at 300,555 ms each, **holding** 2,746 rows from a prior success (**not "serving" — nothing reads it**, see below), watermark frozen at 2026-08-10 · `mv_soil_survey_union` — 4 failures, **`relispopulated=false`, has never produced a single row** |
| **skipped/missing** (2) | `mv_feature_observation_day_axis` (`skipped_missing` — `drizzle/0031` unregistered, exactly as designed) · `mv_signal_cell_daily` (`skipped_missing` — genuinely DROPPED 2026-08-18, `refreshed_at` frozen at 2026-08-16 16:40:15) |
| **gated, not failing** (1) | `geo.watershed_rollup` — last success 2026-08-16 16:11:25 (230,207 ms, 2,162 rows); `last_attempt_at` NULL because its upstream watermark has not moved since 08-07. Not a failure. |

The backoff machinery works — `consecutive_failures` increments instead of churning. But the perpetual
`matview-refresh:pulse` logical run has been `status='running'` since 2026-08-16 02:35:33 with **74 work
items: 2 succeeded, 70 failed, 2 unaccounted** — the states of the remaining 2 were not captured in the
probe and are **UNMEASURED**, so quote the pair as "74 items, 70 failed" rather than implying the set is
closed. Closing query:
`SELECT status, count(*) FROM agri.job_work_item WHERE run_id = <the pulse run> GROUP BY status;`
`agri.job_incident` count is **0**, so nothing escalates.

**Neither soil matview has a reader.** *(Added 2026-08-21.)* A grep across `src/` and the agri service
finds exactly two references outside the refresh specs, and both are comments recording a **deliberate
non-repoint**: `src/lib/server/services/usda-soil.ts:1049` ("NOT REPOINTED at `geo.mv_soil_survey_union` in
the 2026-08-15 pre-aggregation pass") and `:1151` (the same for `_grid`). Each gives a grain-mismatch
reason — `_union` is grained `(zoom_tier, drainage_class)` against a viewport-scoped union; `_grid` is
three fixed tiers against an unbounded doubling ladder — so both readers still run their own `GROUP BY` on
purpose. **`mv_soil_survey_grid` is therefore not more urgent than `_union`; both have zero consumers.**
This inverts the ranking §0.19.8 carried, and it means `drizzle/0035` repairs a relation nothing reads.
The honest open question is whether either should be **repaired at all or dropped** alongside the §0.19.6
item 46 matview retirement — they are exactly the "matviews serving nothing" pattern §0.18.8 item 4 rejects.

Downstream consequence — **CORRECTED 2026-08-21, and the original verdict here was wrong.** The four agent
SQL tools reading the dropped `geo.mv_signal_cell_daily` (`sql/agent/signal_value_on_day.sql`,
`signal_neighbors_in_time.sql`, `signals_near_point.sql`, `nearest_signal_cells.sql`) **degrade gracefully;
they do not error.** Each is guarded by a `to_regclass` plane probe *before any SQL runs*:
`_unbuilt_planes()` at `agent/tools.py:473` and `_plane_refusal()` at `:496`, called at `:532` / `:752` /
`:830` / `:901` respectively. The module comment above `:473` states the design intent outright — catching
the raise and returning an empty list "would be the single most damaging thing this module could do:
'no drought here' and 'the drought plane has never been built' would become the same answer."

So the **user-visible symptom today is a typed `pre_aggregated_plane_unbuilt` refusal that names the
missing plane**, not an exception and not a wrong answer. That is degraded service, and it still blocks
point-in-time signal values, temporal signal neighbours, signals near a point and nearest signal cells —
but it is **not a live incident**, and it does not carry the urgency the earlier "CRITICAL and current"
label gave it. §0.10 always had this right; this section did not. The count of **four** (not the three
named in §0) stands and is still the thing to correct when repointing.

**This error escaped into a shipped artifact and must be fixed there too.**
`drizzle/0034_record_signal_cell_daily_drop.sql` **lines 21-22** read: "each of these throws a hard
database error (relation does not exist) rather than returning stale or empty data". **That is false.**
The file's instruction two lines later — do NOT wrap these four callers in an existence guard — is
**correct and must be kept**, but its stated reason is backwards: the guard is not missing, it is
*already there*, and removing it is what would produce the hard error the comment describes. **Correct
those two lines before registering `0034`** (§0.19.6 item 43). *(Not corrected in this pass: that file is
outside this document's write scope. Recorded here as the action item.)*

`agri.job_definition` holds exactly **4 rows**: `agri.ingest.archive_walk.firms-archive`,
`agri.ingest.archive_walk.streamflow-archive` (both enabled, no cron schedule), `matview-refresh`
(`0 * * * *`), `strategy-mv-refresh` (every 15 min). The climate/weather ingesters have **no
`job_definition` row at all** — they run outside this ledger on Railway crons.

#### 0.16.5 The gap-to-work loop covers 2 of 11 layers

`ingest/lanes.py:228-230` — `BACKFILL_LANES` has exactly two members, `firms-archive` and
`streamflow-archive`, and `resolve_lane` raises for anything else. `jobs_pulse_command.py`'s durable pass
walks that registry; its maintenance pass (`jobs-reconcile-lane` + `jobs-plan-gaps`) is scoped to whatever
pass 2 discovered — the same two. Confirmed live: `readiness.py`'s `job_ledger` section returns exactly two
`archive_walk` rows. Pass 1's dispatchable lanes are only `matview-refresh` and `strategy-mv-refresh`
(`register_dispatchable_lane` appears at `jobs/matview_refresh.py:1274` and
`jobs/strategy_mv_refresh.py:502` and nowhere else) — **no ingestion lane is dispatchable.**

`validate-streams` detects and reports gaps for all registered streams every hour. For the other **nine**
layers nothing converts a detected hole into a claimable `job_work_item`. The loop in
`docs/layer-lane-standard.md` §6 is **structurally absent** for them, not merely unscheduled.

Two layers declare a history horizon they cannot walk: `weather-observations`
(`HistoryCapability(supported=True)`, rolling ~92-day floor) and `fire-perimeters` (floor 2020-01-01), both
declared in the 2026-08-10 wave (`ingest/AGENTS.md:706-741`) with — the file says so itself — no fetcher,
lane or `FunctionSource` wired. `commands.py:321-344`'s `_build_backfillable_sources()` returns only
`nws-sensors`, `sentinel2-ndvi`, `firms-archive-source`, `usgs-streamflow-archive-source`. An operator
reading `supported=True` would reasonably assume a backfill path exists. It does not. `evacuation-zones`
by contrast declares `supported=False` — an honest refusal, since Oregon's feed is current-state-only.

`vegetation` and `sensors` are ingest-backfill-capable but are **not** registered lanes: closing a reported
gap requires a human to notice the report and run the CLI. `drought_areas` (USDM, its own store, not
`geo.layers`) is the same — `ingest-drought-history` exists, no lane, no schedule.

Governed absences (`layer-lane-standard.md` §7) are structurally unimplemented for all 11 layers plus
`drought_areas`. USDM's `not_published` weeks (2026-02-17, 02-24, 08-04) and MTBS's un-mapped 2023/2024
fire years are each a one-time job-result string, never a persisted row a completeness engine can read as
"certified absent". So nothing stops `validate-streams` re-reporting a legitimate absence as an open gap
forever. USDM already suffered a real incident from this class — 26 of 29 release weeks silently missing,
found and fixed 2026-08-05; nothing added since closes the structural gap, only that one bug.

Three documentation traps worth naming, all live: **(a)** `infra/cron-soilgrids` warms
`public.soil_grid_cache` (ISRIC point rasters for the soil-field popup), **not** the `soil-survey` layer —
whose only producer is `src/lib/server/services/usda-soil.ts:11-62`, a lazy read-through cell warmer
triggered by viewport pans, with a `backfillSoilSurvey()` that exists and is unscheduled. **(b)**
`mtbs.py`'s own AGENTS.md says the module "emits rather than persists", which read in isolation would lead
an auditor to conclude burn-severity has no forward producer to `geo.features`; the weekly
`plantgeo-cron-mtbs` is real and does write real rows (478 landed 2026-08-05). **(c)** the stream catalog's
`cadence_basis` citations reference six cron directories deleted in the 2026-08-14 consolidation — the
values are still correct against the all-hourly reality, but the citations are dead.

`interventions` has **no ingestion producer anywhere in the stack** — the only write path is the legacy
TypeScript `POST /api/ingest/interventions`, which needs an authenticated external caller and is
unscheduled. Recorded as the ingestion-architecture confirmation, not merely "zero rows today".

#### 0.16.6 Storage, index and bloat profile

Relations over 100 MB in `geo`/`agri`, by total size (catalog-only, no scan):

| relation | heap | index | toast | total | reltuples |
|---|---|---|---|---|---|
| `agri.signal_observation` | 11 GB | **15 GB** | 0 | **26 GB** | 46,068,872 |
| `geo.features` | 3,808 MB | 2,574 MB | 1,502 MB | **7,912 MB** | 5,025,009 |
| `geo.geometry` | 1,173 MB | 1,361 MB | 437 MB | **2,977 MB** | 3,255,832 |
| `geo.drought_areas` | 640 kB | 216 kB | 493 MB | 500 MB | 995 |
| `agri.artifact` | 1,272 kB | 1,064 kB | 168 MB | 173 MB | 1,632 |
| `agri.forecast_observation` | 76 MB | 40 MB | 0 | 116 MB | 184,409 |

`agri.signal_observation` having **more index than heap**, with one six-column natural-key unique index
(`uq_signal_observation_release_cell_signal_time`) at **11 GB by itself**, is the most consequential
cautionary datum in this audit. It is what a generic observation table costs, sitting in this same
database. §0.18 cites it as the reason not to build a second one.

`agri.signal_observation` is also, at 26 GB, **~70% of the whole 37 GB database** — and §0.18's target
architecture **does not cover it**. That is a deliberate scope boundary, stated in §0.18.1, not an
oversight; read it before quoting any "the database shrinks to ~1.5 GB" figure.

**READ THIS BEFORE QUOTING ANY NUMBER IN THE NEXT TABLE — the measurement window is UNKNOWN.**
*(Added 2026-08-21 after a completeness review; the table below was originally published as if the counts
were lifetime properties.)*

| window fact | value | how |
|---|---|---|
| `pg_stat_database.stats_reset` | **NULL** | `SELECT stats_reset FROM pg_stat_database WHERE datname = current_database()` |
| `pg_postmaster_start_time()` | **2026-08-21 07:47:45 UTC** | read at 07:47:45.855 — the instance was **0.56 s old**; re-read at 07:48:52 confirmed the same start time |
| uptime at the §0.16.9 read | ~2 h 20 m | §0.16.9 |

Three things follow, and they matter more than the counts:

1. **`stats_reset = NULL` does not mean "since forever."** An unclean/crash restart discards cumulative
   stats and leaves `stats_reset` NULL again. **A discard demonstrably already happened**: §5 records a
   lifetime **37.5 billion** reads with `stats_reset` NULL; §0.16.9 measured **1.06 billion**
   (848,950,016 + 207,723,551) later. Counters do not fall. So the window is bounded above by "since the
   last crash restart" and its actual length is **UNMEASURED**.
2. **Uptime is NOT the window.** Counters survive a *clean* restart (PG persists them at shutdown and
   reloads them). Proof: the 2026-08-21 07:48 re-read below was taken **~20 s after a restart** and every
   counter is *higher* than the 2026-08-20 pass. So the "these numbers are only ~2 h 20 m old" reading of
   §0.16.9 is itself wrong — the true window is longer than uptime and shorter than lifetime, and nothing
   measured pins it.
3. **This box restarts, and often.** Two independent reads caught it at ~2 h 20 m old and at **0.56 s**
   old. Combined with §0.18.5's 7-day memory max of **3.0 GB against a 2 GB cap**, OOM-restart is the
   obvious hypothesis and it is **UNVERIFIED**. Settling it is one command against Railway:
   `railway logs -s plantgeo-spatiotemporal-db | grep -i "out of memory\|received fast shutdown\|database system was not properly shut down"`.

**The honest reading of every `idx_scan = 0` below is therefore "zero over a window of unknown length",
never "never been read."** A weekly or monthly reader — an ops query, a backfill script, `readiness.py` —
is invisible in a window this poorly bounded.

All eleven `geo.features` indexes with cumulative scan counts (`pg_stat_user_indexes`; `geo.features` is
confirmed **not** partitioned — no `pg_partitioned_table` row). **Two independent reads are shown**, the
second taken 2026-08-21 07:48 UTC, ~20 s after a restart, expressly to test whether the zeros hold:

| index | size | idx_scan (08-20) | idx_tup_read (08-20) | idx_scan (08-21) | idx_tup_read (08-21) |
|---|---|---|---|---|---|
| `features_layer_external_id_unique` | **832 MB** (833 on re-read) | **0** | 0 | **0** | **0** |
| `ix_features_layer_geom` | 453 MB | 1,066 | 11,842,823 | 1,196 | 41,277,144 |
| `idx_features_geom` | **314 MB** | **0** | 0 | **0** | **0** |
| `ix_features_layer_observation_day` | 294 MB | 45 | 112,098,086 | 49 | 116,571,854 |
| `features_pkey` | 212 MB | 394,097 | 404,892 | 404,860 | 415,697 |
| `ix_features_geometry_id` | 158 MB | 84 | 59,473 | 86 | 62,431 |
| `idx_features_layer_updated_at` | 75 MB | 1,879 | 1,091,699 | 2,204 | 1,092,028 |
| `idx_features_layer_created_at` | 74 MB | 415 | 31,798,581 | 431 | 32,134,194 |
| `idx_features_layer_status` | 64 MB | 14 | 1,449,178 | 17 | 1,715,673 |
| `idx_features_layer` | 61 MB | 4,096 | **5,525,831,267** | 4,226 | **5,696,414,102** |
| `ix_features_updated_at` | 36 MB | 476 | 11,687 | 490 | 16,724 |

Table-level, same two reads: `seq_scan` 874 → **957**, `seq_tup_read` 3,356,554,005 → **3,530,839,202**,
`n_tup_ins` 58,509 → 61,468, `n_tup_upd` 59,762 → 62,730, `last_autovacuum` and `last_autoanalyze` **both
still NULL**.

Three readings — **CONFIRMED as measurements over the unbounded window described above, NOT as lifetime
properties.** The distinction is load-bearing for reading 1.

1. **1,202,151,424 bytes (~1.15 GB) of index recorded zero read-scans across both samples.**
   `features_layer_external_id_unique`'s zero read-scans is *expected* — it enforces `ON CONFLICT`
   uniqueness on the write path, so it earns its keep there. `idx_features_geom` (314 MB) has zero scans
   **and** no write-side rationale: it is fully covered by `ix_features_layer_geom`, and `pg_stats` MCV
   puts **87.43%** of its content in fire-detections (59.96%) plus water-gauges (27.47%) — two layers no
   tile function reads by geometry at all. Every INSERT, UPDATE and VACUUM pays GiST maintenance on ~4.4M
   rows whose geometries are never read back through it.

   **Strength of the evidence, stated precisely.** The second read is a genuinely independent sample and it
   is not weak: every *other* index advanced between the two reads (`ix_features_layer_geom`'s
   `idx_tup_read` more than tripled) while these two stayed at exactly 0/0. That rules out "the counters
   were frozen." What it does **not** rule out is a reader whose period exceeds the window — and the window
   length is unmeasured (see the box above), so a weekly or monthly ops query, a backfill script or a
   `readiness.py` path would look identical to this.

   **Verdict: droppable, pending a lifetime-window confirmation — NOT "droppable today."** *(Downgraded
   2026-08-21; the original text read "Droppable today", which asserted a lifetime property from a window
   of unknown length.)* The confirmation is cheap and is now a precondition on §0.19.3 item 11: re-read
   `pg_stat_user_indexes` after **≥7 days of uninterrupted uptime** (check `pg_postmaster_start_time()` in
   the same statement — this box restarts, so the clock will likely need restarting too), or install
   `pg_stat_statements` in item 49's restart window and read the query set directly. `hypopg` is already
   installed and can model the drop's planner effect without touching the index.
2. **The ~213 MB redundancy figure is exact to the megabyte**: `idx_features_layer_status` 64 +
   `idx_features_layer_created_at` 74 + `idx_features_layer_updated_at` 75 = 213 MB, all
   `btree(layer_id, X)`. `idx_features_layer` itself (61 MB) is **not** part of that set — it is the
   heaviest-used index on the table and is load-bearing. All three redundant ones do get some real scans,
   so this is a consolidation candidate for the moment every index has to be re-created anyway, not an
   urgent drop.
3. **`idx_features_layer` reads ~1.35 million index tuples per scan.** *(Corrected 2026-08-21 — twice.)*
   The arithmetic: 5,525,831,267 / 4,096 = **1,349,080**, and on the second read 5,696,414,102 / 4,226 =
   **1,347,946**. The original text said **1,369,300**, which is simply wrong division, and then inferred
   from it that the figure "happens to equal the water-gauges row count." **It does not** — water-gauges is
   **1,417,935** published rows (§0.16.1), 5% away from the real quotient. **That clause is deleted; there
   was no coincidence to reason from.**

   The conclusion survives on magnitude alone and does not need the coincidence: **~1.35M tuples per scan
   against a table whose largest layer is 3.0M rows means this index is serving whole-layer scans, not
   lookups.** It is also stable across two independent reads, which is the stronger form of the same claim.
   `seq_scan` on the table is 874 → 957 with `seq_tup_read` 3,356,554,005 → 3,530,839,202.

   **One inconsistency is left standing rather than resolved, deliberately.** A completeness review noted
   that 5.53 billion tuples over the ~2 h 20 m uptime of §0.16.9 implies ~657k tuples/sec sustained, which
   is irreconcilable with §0.18.5's measured idle profile (`plantgeo-main` CPU avg 0.0001,
   `plantgeo-martin` CPU avg 0.0031 over 10,081 samples). **The resolution is that the premise is wrong,
   not the numbers**: uptime is not the counter window (counters survive clean restarts — see the box
   above), so no rate can be derived from these figures at all. **Do not compute a throughput from any
   `pg_stat_*` counter in this file until the window is pinned.**

**`geo.features` shows NO measurable bloat.** The standard ioguix `pg_stats`-derived estimator gives actual
5,368,324,096 B against an expected minimum of 5,437,612,032 B — actual is *smaller* than the naive model,
so `pct_bloat` clamps to 0.00%. This **contradicts** the "`features_layer_external_id_unique` is ~2×
bloated" inference that appears elsewhere in the evidence, which was arithmetic from key length, not a
measurement. The per-index btree bloat estimator was **not** run; if that 832 MB matters, run it before
acting on it.

`geo.geometry` maintains Type-2 version history for roughly **108 closed versions**: `version_valid_to` and
`superseded_by` both have `null_frac` 0.9999667 over 3,255,832 rows. `uq_geometry_version` is 402 MB with
**0 scans**; `uq_geometry_grid_cell` 33 MB with 0 scans; `ix_geometry_asof` 400 MB / 216,206 scans;
`uq_geometry_current` 356 MB / 53,604. Its write mix is `n_tup_ins` 10,149 vs `n_tup_upd` 203,271 —
**20:1** — with `n_dead_tup` 39,006 against `n_live_tup` 10,149, and `last_autovacuum` NULL.
`geo.features`'s own mix is 58,509 inserts vs 59,762 updates, roughly 1:1, also `last_autovacuum` NULL. A
conformed dimension holding one row per *reading* is a second copy of the fact table with four extra
indexes.

**Extensions — this corrects the runbook and memory.** Only **9** extensions are actually
`CREATE EXTENSION`'d: `btree_gist` 1.8, `hypopg` 1.4.3, `pg_buffercache` 1.6, `pgcrypto` 1.4, `plpgsql`,
`postgis` 3.6.4, `timescaledb` 2.29.0, `timescaledb_toolkit` 1.24.0, `vector` 0.8.5. **`h3`, `h3_postgis`,
`hll`, `pg_repack`, `postgis_sfcgal` and `roaringbitmap` are NOT installed** — merely *available* in the
image (`pg_available_extensions.installed_version IS NULL`). Earlier phrasing said "installed and unused".
Anyone planning `h3` spatial indexing or a `pg_repack` online repack hits "function does not exist" until
`CREATE EXTENSION` runs first. Only `hypopg` and `pg_buffercache` are genuinely installed-and-idle.

TimescaleDB re-confirmed: exactly one hypertable, `tracking.positions`, `num_chunks=0`,
`compression_enabled=false`. Structurally idle, as §12.7 says.

Settings, all `source=configuration file`: `shared_buffers` 256 MB · `work_mem` 16 MB ·
`maintenance_work_mem` 128 MB · `effective_cache_size` 2 GB · `max_locks_per_transaction` 128 ·
`autovacuum_max_workers` **3** (the 10→3 change is applied and persisted) · `random_page_cost` 1.1 ·
`effective_io_concurrency` 256 · `max_parallel_workers_per_gather` 2 ·
`enable_partitionwise_aggregate` **off** · `enable_partitionwise_join` **off**.

#### 0.16.7 The partitionwise probe — partitioning buys the census matviews NOTHING

A scratch schema (`partitionwise_probe_scratch`, 8,000 sampled rows in an 11-partition LIST table plus a
default, dropped `CASCADE` and verified gone) isolated the question the whole partitioning plan rests on.
CONFIRMED.

- Against the **real** tables, `EXPLAIN` of all three census matview shapes produced **byte-identical plan
  text** with `enable_partitionwise_aggregate`/`_join` off and on.
- On the probe table, `mv_layer_feature_stats`'s shape as written (JOIN then GROUP BY) produced the same
  global `Append → Hash Right Join → single HashAggregate` in both settings.
- **Test A** (bare `GROUP BY layer_id`, no join): off = one global HashAggregate; on = **11 separate
  per-partition HashAggregate nodes** under an Append. The GUCs do work.
- **Test B** (control — join two *identically partitioned* tables on the partition key): on = per-partition
  Hash Join + HashAggregate. Partitionwise join works when both sides match.
- **Test C** (rewritten shape — aggregate on `layer_id` first, join `geo.layers` last): on reproduces Test
  A's per-partition shape.

**The blocker is join topology, not partitioning.** All three matviews join the 11-row `geo.layers` before
aggregating, and `geo.layers` can never reasonably be co-partitioned with `geo.features` — it is minted at
request time by `layersRouter.create` (§0.4). This is fixable at the SQL level with no schema change and no
new index: defer the aggregate past the join. **Until that rewrite happens, partitioning `geo.features`
delivers zero relief to the exact workload that is blowing the memory cap.** This is the literal trigger
condition §0.13 item 4 named for doubting the premise, and it has now fired.

Scope caveat, stated honestly: this is evidence about **plan shape** at 8,000-row/11-partition scale. Plan
shape generalises — the planner's partitionwise decision is driven by partition compatibility, not row
count. What does **not** generalise is whether the resulting per-partition working set at 5.09M real rows
fits under the 1 GB-per-statement structural goal. That needs a real partitioned copy or a much larger
bounded sample, and was out of scope for a fully reversible probe.

Separately: all six composite tile functions already plan as `Nested Loop → Index Scan using
ix_features_layer_geom`, cost 0.41..56.17, identical shape across all six **at z10** (source pulled from
`pg_proc`, so this is the live body, not the dormant `0033`).

**SCOPE CORRECTION, 2026-08-21 — this claim was over-generalised and is now bounded.** The original text
concluded "tile latency is therefore not a justification for partitioning" without qualification. **The
EXPLAIN was taken only at z10, which is a zoom where tiles already work** — §0.17.2 measures z10/181/373
at 93,860 B in 0.40 s. The zooms that are actually broken are **z5 and z6**, and §0.17.2 measures them in
the same run: z5/5/11 status **000 after 25 s**; z5/4/10 284 B after **102.10 s**; z6/11/22 **36.9 s** in
one probe and a gzip timeout in another; `sensor_tiles` z6/11/22 = **3,886,077 B raw**. A total cost of
**56.17 corresponds to a handful of rows — it cannot be the plan for a tile returning 3.9 MB.**

**Corrected claim: tile latency *at z10* is not a partitioning justification. The z5/z6 plans were never
captured, so nothing here is evidence either way about the zooms that hang.** Capturing them is read-only
and cheap, and the plan is the whole point:

```sql
SET statement_timeout = '30s';
EXPLAIN (COSTS, BUFFERS) SELECT geo.sensor_tiles(5,5,11);
EXPLAIN (COSTS, BUFFERS) SELECT geo.sensor_tiles(6,11,22);
```

(no `ANALYZE` — actually executing a z5 tile is the thing measured at 102 s.) Run the same for
`fire_risk_tiles`, `burn_severity_tiles`, `evacuation_zone_tiles`, `intervention_tiles`,
`watershed_tiles`. Until that runs, **§0.18.8 item 1(b) rests on a z10 measurement and says so.**

The sole remaining justification for the swap is the census-aggregate memory problem, and per the probe
above that justification only exists *after* the matview SQL is rewritten. **That argument is unaffected by
this correction** — item 1's grounds (a) and (c) stand on their own measurements.

#### 0.16.8 What the QA gate actually proves

| surface | count | what it proves |
|---|---|---|
| pytest files under `services/agri-data-service/tests/` | 141 | agri pipeline logic |
| of those, Postgres-backed (`*_postgresql.py`) | 16 | real SQL, **only when `AGRI_TEST_DATABASE_URL` is set** |
| using the shared `agri_db_*` fixture | 23 | policed by the no-silent-skip gate |
| vitest files under `src/__tests__` | 106 | the Next.js app |
| Playwright e2e specs | 3 | **100% network-mocked** |
| CI workflow files | **0** | `.github` does not exist |

Gated pytest baseline is **3,170 passed / 3 skipped**; ungated ~3,062. A ~3,062 result means the gate was
not set.

**What the gate cannot catch, ranked by the outages this project has actually had:**

1. **The D0 CORS outage — the one that blanked every dynamic layer — would go undetected today.** Nothing
   in this repo makes a real HTTP request to the deployed app or Martin and asserts on the response. The
   Playwright suite hardcodes a permissive CORS header in its own mock, so it is structurally incapable of
   detecting a CORS misconfiguration, a composite-source 404 (which `sources.ts:28-32` fans into every
   dynamic layer), or a malformed/empty MVT — the three failure modes that matter most here. It also
   requires `npm run dev` via its `webServer` config, violating the never-run-locally rule, so in practice
   it is neither run automatically nor safely runnable by hand.
2. **The only real-PostGIS coverage of the tile functions is frozen at migration ceiling 12.**
   `postgis-spatial.test.ts`'s `MIGRATION_CEILING` is 12 against a registered journal head of 0029, and 21
   behind the dormant `0033` that rewrites all six tile-function bodies. The one test that could catch a
   syntax or semantic regression in those exact bodies before deploy structurally cannot, because it never
   applies them.
3. **No no-silent-skip gate exists on the vitest side.** `climate-field-sql-contract.test.ts` and
   `postgis-spatial.test.ts` are the only two files in the whole vitest suite that parse reader SQL and
   tile-function SQL against a real PostgreSQL/PostGIS parser — the file's own docstring says "a bind whose
   type PostgreSQL cannot resolve (SQLSTATE 42846) is invisible without them", which is literally how the
   climate-field bug shipped. Both `describe.skipIf` away on an unset env var with only a `console.warn`,
   unconditionally, inside the enforced Docker build gate.
4. **The pytest no-silent-skip gate only polices the `agri_db` marker family.** At least one real test —
   `test_live_report_synthesis_returns_the_declared_schema`, the only end-to-end check of the Claude
   structured-outputs round trip the location-analysis feature depends on — skips on every run where
   `ANTHROPIC_API_KEY` is unset, and the sweep reports full green. Same failure shape as the incident that
   motivated building the gate, keyed on a different variable.
5. **No contract test ties `martin.yaml`'s published functions to `DYNAMIC_TILE_SOURCE_IDS`.** Editing
   exactly one of the two files produces a working build, a passing suite, and a production-only 404 that
   blanks the whole composite. `building_tiles` was already removed from the client list once.
6. **`scripts/partition-features.mjs` has zero automated coverage of any of its ten phases.** The script
   that will rewrite `geo.features`'s physical layout in production runs with no regression protection
   beyond whatever the operator manually re-verifies each time.
7. **`npm test` passes `--configLoader`, a flag with zero matches in vitest 3.2.7's own `dist/cli.js`** —
   and `Dockerfile:67` runs `npm test` as a hard build gate. Either it fails every build (contradicting
   "deploys green") or the flag is swallowed and the gate is not gating what it claims. **Verify directly
   before citing `npm test` as evidence of anything.** UNVERIFIED which of the two it is.
8. **The agri-data-service has zero automated enforcement on deploy.** The 141-file sweep, ruff and the
   mypy baseline (679 errors, never green — diff against baseline, never gate on it) run only when a human
   remembers `python scripts/check.py` with the correct `AGRI_TEST_DATABASE_URL` and `PGBIN`. The code that
   most directly touches the 2 GB cap and the live database ships with the weakest enforcement in the
   programme. Configured coverage thresholds (60%) are enforced by nothing.
9. **The client request-budget test unit-tests the concurrency primitive, never its effect on layer load
   time** — and the owner's #1 named complaint is literally about that mechanism.
10. **Nothing anywhere exercises the *compressed* tile path** — which is the one every real browser uses,
    and the one §0.17.2 measured hanging at 0 bytes while the identical uncompressed request returned 4 MB
    in under a second. *(Added 2026-08-21; this was missing from the list.)* Every probe, every mock and
    every latency figure in this repo's history was taken without `Accept-Encoding: gzip`. The smoke script
    below closes it, and that is precisely why its `--compressed` flag is not optional.

**The single highest-value QA artifact this programme could add is ~80 lines**: an un-mocked production
smoke script — composite tile with an `Origin` header **and** `--compressed` at three zooms, `/api/ready`,
`/catalog` membership diffed against `DYNAMIC_TILE_SOURCE_IDS` — run by the existing weekly cron. It would
have caught D0. Scheduled as a numbered deliverable in §0.19, not left as a good intention.

#### 0.16.9 REFUTED — two headline claims that did not survive

Recorded in full, because a silent correction is worse than a loud one.

**REFUTED: "`geo.features` runs a 5.6% heap hit ratio, therefore cache eviction is the direct mechanism
behind the owner's rendering complaint."** The *measurement* reproduces and stands: heap
826,240,292 read / 48,876,140 hit = **5.57%** in one pass; 856,985,458 / 55,365,003 = **6.1%** in an
independent re-measure hours later; index 44.6%; TOAST 91.2%; database-wide 19.63% (848,950,016 /
207,723,551). What does not survive is the causal step, on three grounds. **(a)** The "memory pinned at
ceiling, CPU idle" observation it cites as corroboration is **already spoken for** by §0's verified
per-query sort-spill diagnosis, and the acute rendering complaint is already attributed to D0 plus several
named open defects. **(b)** The window-dating is wrong: `pg_postmaster_start_time()` showed the instance up
only ~2 h 20 m when counters showing billions of block accesses were read — the "roughly one to two days of
ingest" framing cannot be derived, and if that boot was an OOM restart the counters reset then too.
**(c)** "Shrink it and it fits cache" ignores that `geo.features` shares a 2 GB container with
`agri.signal_observation` (26 GB) and the rest of a 37 GB database. Also, `heap_blks_read` is a
shared-buffer-miss counter, not a physical-disk counter; in this specific 256 MB / 2 GB / 37 GB environment
the distinction narrows but does not vanish. **Treat the hit ratio as a real, striking measurement and a
plausible contributing factor. Do not treat it as a proven root cause, and do not repeat "6.76 TB read from
disk" as fact.** One thing the refutation *overstated* in turn: "no amount of partitioning changes total
resident bytes" is true of disk footprint but understates partition pruning's effect on the per-query
working set, which is what actually governs hit behaviour.

**REFUTED: "`geo.sensor_tiles` never returns at the default zoom and is therefore THE broken composite
member."** The composite failure is real and reproduces — it times out at both 5/5/11 and 6/11/22 with an
`Origin` header, and the single-composite-source mechanism at `sources.ts:28-32` is exactly as described.
The *attribution* is wrong. Re-probing live: `sensor_tiles/6/11/22` returned **200 / 3.9 MB / ~0.9 s on 4
of 4 tries**, while `evacuation_zone_tiles/6/11/22` — which the original finding called healthy at 3.32 s —
**timed out 4 of 4**. At the truer default-zoom tile 5/5/11, **five of six** members (sensor,
evacuation_zone, burn_severity, intervention, watershed) independently hang; only `fire_risk` succeeds.
Dropping only `sensor_tiles` from `DYNAMIC_TILE_SOURCE_IDS` would very likely not restore the other five.
**This is a shared low-zoom cost/contention problem across most Martin function sources, not a
`sensor_tiles`-specific defect** — root-cause it as such in §0.17.

---

### 0.17 Why the map is broken, 2026-08-20/21 — the acute section

The owner's words: *"at the moment plantgeo is also basically non functional most of the layers do not
render if they do they update very slowly because of the bandwith limitations i set as a cost control."*
**The bandwidth limitation is not the cause.** This section says what is, in the order a fix should be
attempted. Everything here was measured against live production between 2026-08-20 and 2026-08-21.

#### 0.17.1 The one-paragraph answer

Six of the eleven layers render through **one** MapLibre vector source built from **one** comma-joined
Martin composite URL, and a composite 404s or hangs entirely if any single member does
(`src/lib/map/sources.ts:28-32`). At the default camera that composite **does not complete**. Two
mechanisms stack: **(1) the Martin role has `statement_timeout = 0` and Martin does not cancel on client
disconnect**, so an abandoned tile query leaks a Postgres backend and a pool slot *permanently*, and eight
slots is one page load's worth; **(2) the tile functions carry no day bound and no `LIMIT`**, so tile cost
grows with history depth × feature count every hour ingestion runs, and several sources have crossed the
point where a low-zoom tile cannot be built inside any reasonable time. D0 (the CORS blanking) is
genuinely fixed and is *not* the current cause. The client request budget never sees a tile request at all.

**PROVENANCE OF THIS WHOLE SECTION, AND IT IS A REAL LIMITATION.** *(Added 2026-08-21.)* Everything in
§0.17 was derived from `curl`, `pg_stat_activity`, `EXPLAIN` and source reading. **The owner's actual
symptom — "most of the layers do not render" — has never been observed by anyone working this programme.**
No foregrounded browser session against production exists in the record; the one attempt was made in a
backgrounded automation tab, where rAF is suspended and zero tile requests fire, producing a known false
negative (§5). The default camera geometry driving the z5/z6 analysis is likewise **INFERRED** from
viewport arithmetic and source `tileSize`, not observed. §0.17.9's `de3139e` defect statuses are
**code-traced and spot-checked, never seen working.**

**Consequence to hold onto: there is no baseline for the symptom the programme is organised around.**
§0.17.7 predicts the five non-Martin layers (fire-detections, water-gauges, weather-observations,
vegetation, soil-survey) are unaffected — that is the section's key triage claim and it is **untested**.
If they are also blank, the diagnosis here is incomplete and Tier 1 will not clear the complaint; if they
render fine, "most layers" really means "the six composite layers" and the triage is confirmed. **Post-fix
is the worst possible moment to discover the baseline was wrong**, which is why §0.19.2 now opens with a
browser baseline pass **ordered before item 1**.

#### 0.17.2 Martin live probe results — the raw matrix

Every probe below sent `-H "Origin: https://plantgeo-main-production.up.railway.app"` unless the row says
otherwise. Without an `Origin` header a broken CORS config returns a clean 200 to curl while every browser
is blocked, which is how D0 hid.

**CORS is healthy.** `/catalog` lists **9** sources and exactly matches `martin.yaml` — no drift between
config and what Martin serves. Three-way check on `/health` and on the composite: no `Origin` → 200 with no
ACAO; correct origin → 200 **with** `access-control-allow-origin` echoing it; wrong origin → 200 with no
ACAO; `OPTIONS` preflight → 200 for the correct origin, **400 for a wrong one**. The two-origin
`martin.yaml:27-29` list is live. **D0 is fixed. Stop attributing the current outage to it.**

**The composite, at the camera users actually get.** Default camera derives from
`getClientCoverageBbox()`'s PNW bbox fitted to 1024×512 with 40 px padding — zoom **≈5.56-5.92**, centre
≈(−118, 45.6), **6-9 composite tiles on first paint** (INFERRED from viewport geometry and source
`tileSize`, not observed in a browser).

| tile | uncompressed | **with `Accept-Encoding: gzip`** |
|---|---|---|
| z5/5/11 | status **000 after 25 s** | — |
| z5/4/10 | 284 B after **102.10 s** | — |
| z6/11/22 | 200, **4,063,189 B**, 0.87-0.98 s in one probe / **36.9 s** in another | **TIMED OUT, 0 bytes, at both 30 s and 60 s, reproduced twice** |
| z8/43/93 | 203,311 B, **26.75 s cold** | 84,634 B |
| z8/43/93 (immediate repeat) | 203,311 B, **0.307 s warm** | — |
| z10/181/373 | 93,860 B, 0.40 s | 41,252 B, **8.78 s** |
| z14/2903/5981 | — | 278 B, 0.72 s |

**Two things in that table are the whole story.**

**(i) The 87× cold/warm ratio on byte-identical output** (26.75 s → 0.307 s for the same 203,311 B) proves
the cost is Postgres page-cache misses, not payload size. **No amount of compression or byte-shaving fixes
this. Never asking again does.** CONFIRMED.

**(ii) The composite hangs *specifically when gzip is requested*, which every real browser does by
default.** Uncompressed z6/11/22 returned 4 MB in under a second in one probe; `--compressed` timed out at
30 s and 60 s, twice. **Every prior latency figure in this runbook — including §9's 117 s cold
`fire_risk_tiles` — was measured without `Accept-Encoding` and therefore understates what a browser sees.**
This is very likely the dominant, previously-unmeasured mechanism behind "most layers do not render".
CONFIRMED as reproduced twice; UNVERIFIED as to mechanism (compression buffering in Martin, in Railway's
proxy, or an interaction with a large response — not root-caused).

**Per-source matrix, 36 fetches (9 sources × 4 tiles).** Full CSV at
`…/scratchpad/tileprobe/results.csv`.

- **200 with bytes:** `watershed_tiles` 4/4 · `fire_risk_tiles` 3/4 (z14 empty) · `sensor_tiles` 3/4 (z14
  empty) · `burn_severity_tiles` 2/4 · `evacuation_zone_tiles` 2/4
- **204 everywhere, at every zoom and location tested:** `building_tiles`, `intervention_tiles`,
  `osm_roads`, `osm_waterways` — **structurally empty**

Latency is *not stable per source across probes*, which is itself the finding: `evacuation_zone_tiles`
measured 3.32 s in one pass and timed out 4/4 in another; `sensor_tiles` measured "never returns at 60 s
and 180 s" in one pass and 200/3.9 MB/0.9 s across 4/4 tries in another. **Do not build a fix around one
source being the bad one** (§0.16.9). `fire_risk_tiles` at boise z6/11/23 stalled >60 s twice cold, then
returned in 0.26-0.29 s three times warm — while the never-before-hit Seattle z6/10/22 returned
57,583 B in 1.19 s on first try. Cold cost is tile-specific, not source-specific.

**Payload sizes that are on their own a problem:** `sensor_tiles` z6/11/23 = **2,622,412 B raw /
946,836 B gzip**; z6/11/22 = **3,886,077 B raw**. There is no zoom-based simplification and no feature cap
anywhere in these functions. ~950 KB compressed for one z6 tile is several times a normal vector-tile
budget, on every pan that crosses a sensor-dense low-zoom tile.

#### 0.17.3 The leak — measured, and it is the reason the outage survives a reload

`SELECT * FROM pg_db_role_setting` returns **zero rows** on production, and `infra/martin/martin.yaml` sets
no timeout. So Martin runs with `statement_timeout = 0`. Martin also does not cancel on client disconnect.

Measured directly: after a curl aborted a `sensor_tiles` request at 60 s, `pg_stat_activity` showed
`SELECT "geo"."sensor_tiles"($1::integer,$2::integer,$3::integer) AS tile` still `active` in
`IO/DataFileRead` at 793 s, then 848 s, then 986 s, then **1,142 s**. A second ran to 575 s. A z5 composite
aborted at 25 s left `burn_severity_tiles` running at 70 s.

**Contagion, measured directly.** With only **two** stuck queries against `pool_size: ${MARTIN_POOL_SIZE:8}`
(`infra/martin/martin.yaml:39`): `burn_severity_tiles/6/11/22` went **0.38 s → status 000 at 30 s**, and
`fire_risk_tiles/6/11/22` went **0.29 s → 5.02 s**. Under a separate 6-way concurrency test,
`intervention_tiles/10/181/373` (an empty 204!) took **6.60 s** and `sensor_tiles/10/181/373` **6.86 s**,
against 0.21-0.30 s solo immediately after.

**The arithmetic that makes this total.** `src/components/map/MapView.tsx:80-89` constructs the map with
**no `maxParallelImageRequests` override**, so MapLibre uses its default of **16** concurrent tile
requests. The default view needs 6-9 composite tiles, each spawning six member queries. Pool is 8. One
page load can exhaust the pool with queries that never complete, and from then on **every** tile request —
including the fast ones — queues behind stuck backends. That is why the outage is both total and
self-sustaining, and why it survives page reloads until Martin restarts.

A later `pg_stat_activity` snapshot taken during a quiet window showed the healthy shape for contrast: 8
Martin backends, **all `state='idle'`** on `ClientRead`, idle 462-1,011 s, plus the TimescaleDB background
worker. No agri/ingestion connection present at all in that snapshot.

**Note for the operator:** pid 105 was still running an abandoned `sensor_tiles` query at 1,142 s when the
probe ended. Cancelling leaked backends is `pg_cancel_backend(<pid>)` and was outside the read-only briefs.

#### 0.17.4 The one hazard every proposed fix got wrong

Every design in the panel offered `ALTER ROLE <martin_role> SET statement_timeout = '20s'` as an
alternative to the connection-string form. **Do not do that.** A read-only production query settled it:
there is exactly **one login role — `postgres`, `rolsuper=true`** — and all 8 Martin backends run as it
(`usename=postgres`, `application_name='Martin v0.7.0 - pid=1'`). There is no Martin role to alter.
Applying a 20 s ceiling to `postgres` would impose it on ingestion, `scripts/migrate.mjs`, the readiness
probe, and the 428 s / 900 s matview refreshes.

**The only safe form is appending `?options=-c%20statement_timeout%3D20000` to `DATABASE_URL` on the
`plantgeo-martin` Railway service.** That is a runtime variable change — no rebuild — unlike `martin.yaml`,
which is baked in at build time (`Dockerfile.martin:3`) so a `railway service restart` reuses the old image
and appears to do nothing. A dedicated least-privilege Martin role is separate, unstarted work.

Related: `martin.yaml:42`'s `max_feature_count: 10000` is **structurally inert**. Every dynamic layer is
registered under `functions:` (`:46-67`), not `tables:`, and `pg_stat_activity` shows Martin issuing a bare
`SELECT "geo"."sensor_tiles"($1,$2,$3) AS tile` — there is no generated query for Martin to inject a LIMIT
into. The cap applies only to `osm_roads`/`osm_waterways`, which have **0 rows**. The single configured
guard against a runaway tile does nothing for any layer that draws.

And `martin.yaml`'s `cache:` block is Martin's **internal tile cache** (`size_mb` / `tile_size_mb` /
`tile_expiry` / `maxzoom`). **There is no HTTP `Cache-Control` knob in Martin's config.** Any plan step
reading "add `Cache-Control` to Martin" is unimplementable as written and needs a proxy or CDN in front —
see §0.19's staging of that, and its CORS hazard.

#### 0.17.5 Caching — Martin honours revalidation and advertises nothing

Martin sends a strong `ETag` and honours `If-None-Match`: **304, 0 bytes, 0.37 s**, verified. It sends
**no `Cache-Control` and no `Last-Modified`**. Browser heuristic freshness is therefore **zero**, so every
tile must be revalidated on every single page load — best case one full round trip per tile per load, worst
case the 26-102 s cold path again. Martin's own internal cache expires at 5 minutes and caches only to
z14 while the composite advertises maxzoom 22, so every tile above z14 is an uncached database query.

The PMTiles basemap has ideal headers and is **not being edge-cached**: 1,411,574,646 B,
`Cache-Control: public, max-age=31536000, immutable`, strong ETag, `Accept-Ranges` honoured (a 16 KB header
range fetch takes 0.25-0.28 s), served via Cloudflare — and **`cf-cache-status: DYNAMIC`, not HIT**, on
both ranges tested, with wildcard `ACAO: *`. This is not a "download 1.4 GB" problem; range requests work
correctly. It *is* an every-range-round-trips-to-R2 problem on an immutable object shared by every user.
UNVERIFIED whether a Cache Rule fixes it; that is one rule and two curls to find out, and **§0.18's entire
serverless story depends on the answer.**

#### 0.17.6 The request budget is aimed at the wrong path

`grep -rn "transformRequest\|maxParallelImageRequests" src/components/map src/lib/map` returns **zero
hits**. MapLibre is constructed at `MapView.tsx:80-89` with no request hooks, so it uses its own fetch at
its default 16-way tile concurrency. `createBudgetedFetch` has exactly four call sites:
`src/lib/trpc/client.ts:21` (lane `trpc`), `src/hooks/useFireData.ts:13` (`fires`),
`src/hooks/useOfflineSync.ts:20` (`offline-sync`), `src/lib/offline/tile-cache.ts:12` (`tile-prefetch`).
**Not one of them is a tile.**

Cold-load arithmetic under `MAX_CONCURRENT_REQUESTS = 4` (`src/lib/net/request-budget.ts:24`),
`SUSTAINED_REQUESTS_PER_SECOND = 5` (`:27`), `BURST_CAPACITY = 8` (`:30`): `activeLayers` defaults to `[]`
(`src/stores/map-store.ts:91`), so first paint issues the headless capabilities query plus a small number
of tRPC batches — and `httpBatchLink` collapses concurrent queries into single HTTP calls, so even ~20
layer queries form a handful of fetches, all clearing the 8-token burst instantly. **The budget adds no
measurable delay to a cold load.** Meanwhile the unmetered path carries a 105,636 B burn-severity tile, a
793,110 B `/api/fires` response, and 16 concurrent tile requests against an 8-slot pool.

**Conclusion, and it is the direct answer to the owner's diagnosis: the bandwidth ceiling set as a cost
control does not reduce tile bandwidth and cannot — it never sees a tile request. Removing it would not fix
the outage; tightening it would not have prevented it.** The one place concurrency *is* unbounded is
exactly the place that overruns the server. The fix is to re-aim it, not remove it: pass `transformRequest`
into the map constructor, route Martin-origin URLs onto a `"tiles"` lane, and set
**`maxParallelImageRequests: 6`** — strictly *below* `pool_size: 8`, so the client can never queue more
tile work than the server can hold. (6, not 8: setting it equal to the pool saturates it with a single tab
and a second tab re-creates the measured contagion.)

Secondary, low today but real later: all tRPC layers share a single budget lane, so the round-robin
fairness the module was built for is inert. It becomes real the moment a user turns on several layers —
background prefetch can head-of-line-block a foreground paint. And `query-persister.ts`'s private
`MAX_CONCURRENT_REVALIDATIONS = 2` semaphore (`:801-832`) sits outside the shared budget, which its own
`src/lib/net/AGENTS.md` names as the one deliberate gap.

#### 0.17.7 Every registration mismatch that hides a layer

Five places must agree for a dynamic layer to draw: a `geo.layers` row → published rows with
`status='published'` → a `geo.*_tiles` function → a `martin.yaml` `functions:` entry → membership in
`DYNAMIC_TILE_SOURCE_IDS`. The live counts: **8** tile functions in `geo`, **7** in `martin.yaml`, **6** in
the client composite.

| mismatch | consequence |
|---|---|
| **`interventions`: 2 rows, both `status='approved'`, 0 published.** Every tile function in the codebase gates on `'published'`. All five other links are correct — `martin.yaml` publishes it, `layers.ts` wires three style layers, `geo.layers` carries the row with `is_public=true`, so the slider catalogue lists it. | The layer serves a **valid, well-formed, permanently empty MVT** forever, with no error anywhere. Looks identical to "no data yet"; is actually a stuck publish workflow. |
| **`geo.strategy_recommendations_tiles` exists (`drizzle/0027:123`) but is absent from `martin.yaml`**, and Martin runs `auto_publish: false` (`:43`). Meanwhile `StrategyLayer.tsx:15` sets `SOURCE_ID = "martin-dynamic"` and `:36`/`:64` add layers with `"source-layer": "strategy_recommendations"` — a name in no composite member. | Flipping the ML Strategy Recommendations switch does **nothing**, silently, with no console error and no capability row. Doubly dark: all three `mv_strategy_recommendations_*` are also empty. |
| **`building_tiles` is the 7th `martin.yaml` function and the answer to the six-vs-seven discrepancy.** It touches neither `geo.features` nor `geo.layers`; `geo.osm_buildings` has **0 rows**; the client never requests it. | Registered-but-orphaned — the inverse of a hiding-layer bug. Documented so nobody re-investigates it. |
| **`geo.osm_roads` / `osm_waterways` / `osm_buildings` are all exactly 0 rows** (exact `count(*)`, all three at their empty-table minimum size). | Three of nine published sources are permanently dead weight. An ingestion gap, not a serving defect, but it does contribute to "most layers do not render". |
| **`fire_risk_tiles`' function name, source id and emitted MVT layer tag (`fire_risk`) have never matched the `geo.layers.name` / `LayerToggleId` they serve (`fire-perimeters`).** | No impact today — every consumer uses the same literal. But renaming either string in isolation breaks the layer with no compiler or schema error to catch it. |
| **Capability catalogue publishes 24 layers; `layer-registry.ts` declares 27 toggles.** Missing: `soil` and `demand-heatmap` (both `warehouseLayerName` null — expected) and `strategy-recommendations` (claims a name the server never publishes, per `src/types/time-slider.ts:136`). `interventions` and `soil-survey` publish with `earliest=latest=null`. | The registry and the server disagree about what exists. |

**Which layers a Martin incident actually takes down: 6 of 11, not 11 of 11.** fire-perimeters, sensors,
evacuation-zones, burn-severity, interventions and watersheds ride the composite. fire-detections,
water-gauges, weather-observations, vegetation and soil-survey render via component-mounted tRPC/API reads
and are unaffected by Martin. That triage distinction is written down nowhere else.

**Production is still running the pre-0033 (join-based) tile bodies** — confirmed by
`pg_get_functiondef(geo.fire_risk_tiles)`, which still contains `JOIN geo.layers l ON f.layer_id = l.id`
and has no `target_layer_id` variable. That is a second, independent source of truth for "0033 is dormant"
beyond `_journal.json`, and it matters because journal registration and actual function replacement are
two different failure points.

#### 0.17.8 The non-tile read paths that are also slow

- **`/api/fires`: 793,110 B uncompressed, 7.80-20.02 s cold / 0.59 s warm, `x-fire-count: 2000`, no bbox
  filter accepted or sent, refetched every 120 s** by `useFireData`. That is **23.8 MB/hour per open tab**.
  No `Content-Encoding` on the response even when `Accept-Encoding: gzip` is sent; GeoJSON compresses ~10:1.
- **`getSliderCapabilities`: 45,807 B uncompressed, 6.75-10.69 s cold / 0.23-0.41 s warm**, 5-minute server
  memo, no ETag, no `Cache-Control`. It gates **every** layer's day, so for ~11 s after each deploy and
  after each cache expiry that lands cold, the whole map has no dates and every warehouse-backed layer
  renders blank — indistinguishable from the tile outage, which is part of why the outage has been hard to
  attribute. It is also **not persisted client-side**, so a cold offline start has no `serverCurrentDate`.
- **`/api/ready` flaps.** One probe sequence: 503 `database:false`, then three consecutive 200s within
  ~25 s; another later probe: clean 200 with all three checks true. `/api/health` 200 in 0.22-0.33 s
  throughout. The probe timeout is 2,000 ms, and Railway's healthcheck reads it — a flap can pull the
  instance out of rotation or restart it mid-session. Same I/O starvation root cause as the tiles.
- **`/api/v1/features` has no ETag, no `If-None-Match`, no `updated-since`** (confirmed by source), and
  paginates by `OFFSET` ordered by `asc(features.id)` — a **random uuid** — with `MAX_OFFSET_WITH_LAYER_ID
  = 1_000_000`. A deep page re-scans and discards, and a random-uuid order is not stable under concurrent
  insert, so a paging client can miss or repeat rows. It could not be measured with real data from outside:
  **401, "Invalid or missing API key", 0.22 s** — a provisioned read-only test key is needed for any future
  payload measurement.
- **The dated fire-detections reader plans as a `Parallel Seq Scan` of the whole table.**
  `EXPLAIN (COSTS)` on the exact statement at `environmental-read-model.ts:377-400`:
  `Parallel Seq Scan on features f (cost=0.00..540780.44 rows=6437 width=527)`, `Workers Planned: 2`,
  feeding a Sort on `properties->>'acqTime'`. The structurally identical water-gauges statement (`:518-548`)
  returns `Index Scan using ix_features_layer_observation_day (cost=0.56..7792.72)`, total 8,112 — **66.7×
  cheaper**. The comment at `:359-366` explains why: `geo.feature_observation_day()` COALESCEs
  `observedAt`/`updatedAt`/`polygonDateTime` and knows nothing about FIRMS's `acqDate`. The doc-comment
  calls it "a bounded-by-LIMIT scan"; **LIMIT bounds output, not the scan**, because the day expression is
  unindexed and there is an `ORDER BY` above it. This is the mechanism behind the unowned §9 D11
  live/historical asymmetry.

#### 0.17.9 §9 defect status, re-verified in the current tree

| defect | status |
|---|---|
| **D0** Martin CORS blanking every dynamic layer | **FIXED** — measured, §0.17.2 |
| C2, D1, D3/D4/D5/D7/D10/D12 | shipped in `de3139e`, spot-checked present (`query-persister.ts` stamps `layerId`/`day`) |
| C5 | looks fixed though unnamed — `api/fires/route.ts`'s ETag is now content-fingerprint-based |
| **C1** 30-day IndexedDB TTL | **OPEN.** `query-persister.ts:309` returns `HISTORICAL_TTL_MS` (30 days, `:68`) whenever the selected day < `serverCurrentDate` — and every layer opens on `latestObservedDate`, which is by definition strictly before today. The 30-day class applies **universally**. Retuning the constant does not fix it; the predicate is what is wrong. |
| **D6** 6-decimal bbox | **OPEN.** `viewport-bbox.ts:30` still `toFixed(6)` — a 10 cm pan mints a cold cache key for every layer. |
| **D9** 5-minute capability poll | **OPEN.** `TimeSliderCapabilitiesLoader.tsx:8` still polls on a 5-minute `refetchInterval` that re-stamps the date mid-session. |
| **D11** fire live/historical query-*shape* asymmetry | **OPEN, unowned.** Now has two candidate mechanisms: the seq-scan plan divergence above, and fire-perimeters' 100%-missing `observedAt` (§0.16.3). |
| §10 join-free tile relations | **proposal only** — confirmed no `tile_*` relations exist in `drizzle/`. |
| `mv_signal_observation_day` | **still fails every pulse** (301 s vs 300 s cap). Only its sibling `mv_feature_observation_day` was fixed by the 300→900 s raise. |

#### 0.17.10 Ranked: user-visible rendering unblocked per unit of work

**Do these in this order.** Everything in tier 1 is a variable, a header, or a one-line constant. Nothing
in tier 1 touches a schema.

**TIER 1 — hours, no schema, high confidence.**

1. **`?options=-c%20statement_timeout%3D20000` on `plantgeo-martin`'s `DATABASE_URL`.** Converts a runaway
   tile from a permanent pool leak into a fast, attributable failure. **Must be first — every subsequent
   measurement is contaminated by leaked backends.** Also `pg_cancel_backend()` the currently leaked pids.
   Reversal: delete the parameter. Risk: a legitimately slow tile now 500s instead of hanging, which is the
   point and is visible.
2. **`maxParallelImageRequests: 6` + `transformRequest` at `MapView.tsx:80-89`.** The client can never
   again queue more tile work than the pool can hold. Reversal: one constructor option.
3. **Un-crash `plantgeo-ingest-cron` (CRASHED) and `plantgeo-cron-soilgrids` (FAILED)** on the current
   deploy, found incidentally 2026-08-21. Every "ingestion is live" premise in this programme — including
   this runbook's own safety rules — currently rests on a crashed service.
4. **Root-cause the missing `Content-Encoding`, then fix the layer that is actually broken.**
   `next.config.ts` has **no `compress` key**, so Next's default (`true`) is already in force — "enable
   gzip in next.config" is a **no-op** that will be reported as done. `/_next/static` chunks *are* gzipped
   on the wire; route handlers are not. Diagnose with one `curl -I` against a static chunk versus a route
   handler before changing anything.
5. **Verify the Martin version.** Every `pg_stat_activity` row carries
   `application_name = 'Martin v0.7.0 - pid=1'` against `Dockerfile.martin:1`'s `martin:1.10.1`. The
   two-origin CORS list from `martin.yaml` *is* live, which argues the deployed image reads the current
   config — so this may be a hardcoded string rather than a real mismatch. One command settles it:
   `railway logs -s plantgeo-martin | head`. **Do it before relying on any `martin.yaml` block other than
   `cors` and `postgres.functions`**, both verified working live.

**TIER 2 — days, one migration each, deploy one function at a time.**

6. **`LIMIT` inside each tile function's inner subquery before `ST_AsMVT`** — the only place the cap can be
   enforced, since `max_feature_count` is inert (§0.17.4). **The `LIMIT` must carry an `ORDER BY` inside
   the subquery** (e.g. `ORDER BY f.id`, or a zoom-aware rank), or tile contents become nondeterministic
   across requests and any content fingerprint over them changes — the same tie-cut trap
   `environmental-read-model.ts:367-372` documents for `acqTime`. Ship as a new `drizzle/00NN`, **one
   function per deploy**, each fetched standalone with an `Origin` header before the composite sees it.
   This converts the `sensor_tiles` hang into a truncated-but-rendering tile immediately.
7. **A server-side day bound on the tile functions.** `src/lib/map/tile-layer-date-filter.ts:12-14` states
   the current design outright: "Filtering in the style rather than in the tile query is what makes a scrub
   free." That is a *good* property and should survive — but the live `geo.sensor_tiles` body
   (`drizzle/0015:198-237`) has predicates only on layer name, `is_public`, status, `geom NOT NULL` and the
   envelope, and the dormant `drizzle/0033:372-415` preserves that exactly ("same predicates, same tile
   bytes"). Meanwhile `ix_features_layer_observation_day` (294 MB) exists and the tile path never touches
   it. `sensors` spans 17 days with an estimated 26,734 published rows inside one z6 envelope. Cheapest
   sufficient form: emit only the latest row per station inside the function
   (`DISTINCT ON (properties->>'sensor_id') … ORDER BY … observed_day DESC`).
8. **`CREATE INDEX CONCURRENTLY ix_features_fire_detection_day ON geo.features (layer_id, (COALESCE(substring(properties->>'observedAt',1,10), properties->>'acqDate'))) WHERE status = 'published'`.**
   `->>`, `substring(text,int,int)` and `COALESCE` are all immutable, so **no `IMMUTABLE` wrapper function
   is needed** — simpler than `drizzle/0015`'s `to_date` problem because it stays in text. ~200 MB, built
   out of band with `lock_timeout='20min'` per `drizzle/0030`'s apply procedure, then a `DO $$` assert
   migration. **This must happen before any partitioning** — `CREATE INDEX CONCURRENTLY` on a partitioned
   parent **fails** on PG 18.4 (verified live; `drizzle/0030`'s "PG14+ supports this" comment is wrong).
9. **`DROP INDEX CONCURRENTLY idx_features_geom`** — 314 MB, 0 scans, fully covered, 87.4% of it belonging
   to two layers no tile function reads by geometry (§0.16.6).
10. **Bbox cache keys quantized** (D6) — `viewport-bbox.ts:30`'s `toFixed(6)` to a z8-tile-envelope key or
    2 decimals. Turns a near-zero cache hit rate into a near-one, and is the precondition for any CDN hit
    rate later.
11. **Stop the 5-minute capability poll re-stamping the date mid-session** (D9).

**TIER 3 — needs a decision or a proxy, staged deliberately later.**

12. **A CDN in front of Martin** to supply the `Cache-Control` Martin cannot. **Hazard, and it is severe:**
    Cloudflare ignores `Vary` except `Accept-Encoding`, and Martin echoes a **per-origin** ACAO from
    `martin.yaml:27-29`'s two-entry list — so a shared edge cache will hand origin A's cached response,
    carrying A's ACAO, to origin B, blocking it in every browser behind a clean `curl 200`. **That is D0
    wearing a CDN costume.** Either collapse tiles to a single `Access-Control-Allow-Origin: *` (they are
    public) or set the header at the edge with a Transform Rule, and prove it with the wrong-`Origin` probe
    before trusting it. Also note `NEXT_PUBLIC_DYNAMIC_TILES_URL` is inlined at build
    (`src/lib/map/sources.ts:18`) — repointing it is a **full rebuild** through `Dockerfile`'s
    check-data-boundary / type-check / lint / `npm test` gate, not a variable change.
13. **A Cloudflare Cache Rule for `*.pmtiles` with range caching**, to turn `cf-cache-status: DYNAMIC` into
    HIT. Free, and §0.18's whole serverless story is gated on whether it works.
14. **Fix the `interventions` publish workflow** — find whatever should move `status` from `approved` to
    `published` (likely under `src/lib/server/services/`), or decide `approved` should render and adjust
    `intervention_tiles`' `WHERE` (and `0033`'s dormant copy). **A workflow bug, not an architecture one —
    it must not gate the map.**
15. **Register `strategy_recommendations_tiles`** in `martin.yaml` and `DYNAMIC_TILE_SOURCE_IDS`. Needs a
    Martin **rebuild**, not a restart. Adding a composite member is the exact change that 404s all members,
    and all three backing matviews are empty, so it would render nothing anyway. **Deferred until after
    everything above, and only once it answers standalone.**

**What NOT to do, so it stops being re-proposed:** do not remove or loosen the request budget (§0.17.6);
do not drop only `sensor_tiles` from the composite (§0.16.9); do not raise `shared_buffers` (256 MB against
a 7.9 GB table sharing a 2 GB container with a 26 GB relation — the working set does not fit at any
setting); do not register `drizzle/0033` as part of the acute fix (six functions replaced at once, and its
constant-`layer_id` pruning is moot if partitioning is deferred).

---

### 0.18 Target architecture — entity/observation split with sealed months on R2

Three independent designs were built against the evidence in §0.16-§0.17 and judged by three lenses
(feasibility-against-this-repo, scale-and-sync-correctness, operational-durability). **Two of three lenses
picked the same design: the entity/observation split.** The scale lens preferred a manifest-addressed
"cold ledger" and its best ideas are grafted in below, attributed. The rejected designs and the reasons are
recorded in §0.18.8 — this runbook's convention is that a recorded rejection stops work being
re-litigated.

**The thesis in one sentence.** 95.1% of `geo.features` is the wrong shape (§0.16.1); split it by SHAPE —
entities keep polymorphic `jsonb` because that is genuinely correct for them, observations move to narrow
typed tables RANGE-partitioned by month — and every other goal the owner named stops being a separate
project: **the monthly partition boundary IS the Parquet file boundary**, so "serverless" becomes an export
rather than a new system; an append-only typed table has a natural monotone cursor, so "pull only what's
new" becomes a `WHERE` clause instead of `OFFSET` over a random uuid; a sealed month is immutable, so it
gets `Cache-Control: immutable` — the exact pattern the 1.41 GB PMTiles archive already proves works.

**Why this design and not the cold-ledger one.** The cold ledger scored highest on scale (its manifest and
z8-keyed live edge give genuinely O(1)-in-users origin load) and had the best falsification discipline in
the packet. It lost on durability and feasibility for one reason: it **DETACHes and DROPs Postgres
partitions past two months, leaving R2 as the only copy of the corpus with no named restore path**, and it
retires Martin for six layers in favour of a tippecanoe pipeline that does not exist in this repo. The
winning design keeps the full corpus in Postgres at ~1,456 MB, so a failure of Parquet, hyparquet, DuckDB,
R2 or Cloudflare degrades to "slow" rather than "data unavailable". **The serverless tier is optional at
runtime.** For a one-maintainer system with no CI, that is worth more than the better asymptote.

#### 0.18.1 Data model — three tiers

**SCOPE OF THIS ARCHITECTURE — read before quoting any size figure.** *(Added 2026-08-21.)* §0.18 covers
the **`geo` schema only**. It does **not** cover `agri.signal_observation` — **26 GB (11 GB heap + 15 GB
index), 46,068,872 rows, roughly 70% of the entire 37 GB database** (§0.16.6). That is deliberate, and the
reasons are: it is not a map-rendering plane, so it is outside the acute outage; it is not read by any
`geo` serving path; and its single largest object is one six-column natural-key unique index
(`uq_signal_observation_release_cell_signal_time`, **11 GB alone**) whose necessity is a separate
question from the entity/observation split.

**The consequence must not be glossed.** Completing every item in §0.19 leaves the database at roughly
**27 GB, not ~1.5 GB.** Any sentence of the form "the extraction shrinks the database so the cap can come
back down" is about the `geo` schema alone. `agri.signal_observation` now has its own numbered items —
§0.19.3 item 11a (audit the 11 GB index for redundancy) and §0.19.5 item 37a (its own Parquet export
stream, which is also what item 41 actually depends on).

**Tier 1 — `geo.features` stays and shrinks to entities (~249,751 rows).** soil-survey 238,986 ·
watersheds 9,396 · evacuation-zones 651 · burn-severity 541 · fire-perimeters 177 · interventions 0-2.
Polymorphic `properties jsonb` is **correct** here: heterogeneous, few, large per row, read whole, never
aggregated. Three changes:

- **`properties->'geometry'` is deleted from stored payloads.** Measured duplication: a watersheds row
  carries `geom` **21,572 B** of WKB *and* `properties->'geometry'` **56,780 B** of GeoJSON inside a
  16,237 B compressed blob; soil-survey 2,625 / 7,980 / 3,684; a water-gauges point is stored **four
  times** (`geom`, `properties.geometry`, `properties.lat`+`lon`, and a `geo.geometry` row). **No read path
  reads it** — `src/app/api/v1/features/route.ts:111-114` derives geometry from `ST_AsGeoJSON(features.geom)`
  and `:133-134` then spreads `row.properties` verbatim, so the duplicate ships on the wire anyway. Reduce
  `drizzle/0004_repair_ingested_geometries.sql:4-56`'s BEFORE trigger to validation-only (raise on invalid,
  never write back); the writer supplies `geom` via `ST_GeomFromGeoJSON`. **Budget this honestly:**
  `sql/ingest/refresh_features.sql`'s header documents its strip-asymmetry as load-bearing (the stored side
  drops `geometry` AND `geometry_repaired`, the candidate side drops only `geometry`) and names the
  inherited limit that a shape-only change goes undetected — **today the trigger's write-back is what makes
  that change-detection test work at all.** Removing it forces a re-derivation of the change-detection
  predicate, not just a key deletion.
- **Two native columns replace two expression indexes.** `external_id text` and `observed_day date`.
  `features_layer_external_id_unique` (832 MB, `(layer_id, (properties->>'id')) WHERE properties ? 'id'`,
  **0 read scans**) becomes `UNIQUE (layer_id, external_id)` — a plain btree, ~15-30 MB at entity scale;
  `ix_features_layer_observation_day` (294 MB) becomes `btree (layer_id, observed_day)`. With **no index
  over `properties` left**, a measurement-only UPDATE becomes HOT-eligible — today `n_tup_upd` 59,762 ≈
  `n_tup_ins` 58,509 means half the write traffic rewrites all eleven index entries across 2,573 MB of
  index on a table where `last_autovacuum` is NULL.
- **`idx_features_geom` is dropped** (314 MB, 0 scans, covered — §0.16.6).

**One day semantic per stream, decided once at extraction and never `COALESCE`d at read time.** This is
what removes the 66.7× plan divergence in §0.17.8. Measured basis for the mapping (§0.16.3): fire-detections
carries `observedAt` on 100% of sampled rows; water-gauges carries **no** `observedAt` but `updatedAt` on
all sampled rows; soil-survey carries **neither** (correctly — it is static); fire-perimeters carries it on
**0 of 177**. Pick per stream, write it as a column, stop guessing.

**Tier 2 — `geo.station`, the entity dimension the reading logs never had (~2,200 rows, <20 MB).**

```sql
CREATE TABLE geo.station (
  station_id  int4 GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  stream      text NOT NULL,              -- 'streamflow' | 'sensor' | 'weather'
  external_id text NOT NULL,              -- USGS siteNo, NWS station id
  name        text,
  geom        geometry(Point,4326) NOT NULL,
  geometry_id uuid REFERENCES geo.geometry,
  first_seen  date, last_seen date,
  attrs       jsonb NOT NULL DEFAULT '{}',
  UNIQUE (stream, external_id));
CREATE INDEX ON geo.station USING gist (geom);
```

953 gauges carry 1,417,935 readings — **1,488 readings per station**, each re-storing `siteNo`, a
46-character `siteName` ("SF COEUR D ALENE R ABV PLACER CR AT WALLACE ID"), `lat`, `lon`, `source` and a
full GeoJSON Point in a 438-byte blob. This table stores each of those once. Sensors are worse per row:
1,064 B, with the nested `readings` object repeating `unitCode` and `qualityControl` for all seven measures
on every reading.

**Tier 3 — one narrow typed table per stream, RANGE-partitioned by month.**

```sql
CREATE TABLE geo.streamflow_reading (
  station_id  int4 NOT NULL REFERENCES geo.station,
  observed_at timestamptz NOT NULL,
  flow_cfs    real, gauge_height_ft real,
  percentile  smallint, trend smallint, condition smallint,
  PRIMARY KEY (station_id, observed_at)
) PARTITION BY RANGE (observed_at);          -- ≈48 B/row
```

plus `geo.sensor_reading` (WIDE not tall — the seven NWS measures are always co-observed, so one 56 B row
beats seven 20 B rows), `geo.weather_observation` on the same pattern,
`geo.vegetation_observation(cell_id, observed_day, ndvi, cloud_pct, scene_id)` ≈32 B (grid-cell keyed, not
station keyed), and `geo.fire_detection(detection_id int8 identity, observed_at, acq_day date, geom
geometry(Point,4326), brightness, bright_t31, frp, confidence, satellite, daynight, scan, track)` ≈104 B
including the point — fire detections have no station, each detection *is* the event.

**Deliberately NOT one polymorphic `geo.observation` table.** Fire detections (brightness, bright_t31, frp,
confidence, satellite) and gauge readings (flow_cfs, percentile, trend, condition) share **no** columns and
differ 2× in cardinality. The cautionary template sits in this same database: `agri.signal_observation` is
46,068,872 rows, 11 GB heap and **15 GB of index**, one six-column natural-key unique index at 11 GB by
itself (§0.16.6). **Standing review rule: two streams may share a table only if they share every column.**
The pressure to unify these five will be constant and must be resisted with that measurement.

**Modelled size delta.** Row-width arithmetic from §0.16.1's ≈780 B/row heap and the per-layer census; the
target widths are from the DDL above. INFERRED, not measured — the falsification is one `COPY` and an
`ls -l` (§0.19).

| relation | today | target |
|---|---|---|
| streamflow (1,417,935 rows) | ~1,509 MB | ~145 MB — **10.4×** |
| fire detections (3,022,196) | ~4,696 MB | ~615 MB — **7.6×** |
| sensors (184,733) | ~320 MB | ~22 MB — **14.5×** |
| vegetation (185,064) | ~290 MB | ~15 MB |
| weather (32,223) | ~55 MB | ~4 MB |
| entity `geo.features` | ~1,000 MB | ~400 MB |
| `geo.geometry` (3,255,832 rows for ~108 closed versions) | 2,977 MB | ~250 MB at ~255k entity rows |
| **`geo.drought_areas`** — 995 rows, **493 MB of it TOAST** (compressed polygon payload) | **500 MB** | **500 MB — UNCHANGED. No design item touches it.** |
| **subtotal, rows above** | 11,347 MB | **1,951 MB** |
| **`geo` schema, measured relations >100 MB** | **11,389 MB** | **~1,951 MB** |

**Two corrections to this table, both made 2026-08-21 after a completeness review.** *(Recorded rather
than silently patched, per this file's convention.)* **(1)** The `geo.drought_areas` row was **missing
entirely**. It is 500 MB — the third-largest `geo` relation — and it is a **rendered map layer with a live
hourly producer**, so it is not a defensible omission. The design says nothing about its 493 MB of TOAST,
so its target is recorded as **unchanged**, honestly, rather than assumed away. **(2)** The old totals did
not add up: the today-column rows summed to 10,847 MB against a stated 10,882, and the target column to
1,451 against a stated 1,456. Both are corrected above. The 42 MB gap between the 11,347 subtotal and the
11,389 measured figure is row-width rounding across the six `geo.features` decomposition rows, and is left
visible on purpose.

*(Graft, from the cold-ledger design's judge: publish this table per-relation rather than as a single
"~2 GB" assertion. It is the falsifiable artifact that makes "return the cap from 2 GB to 1 GB" checkable
rather than aspirational, and it is the single most useful number for the owner's cost goal.)* That is
exactly why the missing row mattered: **this table is the one number the owner is meant to check the
programme against.**

**The corrected number changes the conclusion, and the old sentence has to go.** It read: "Against
`shared_buffers` 256 MB and `effective_cache_size` 2 GB, ~1.46 GB is a working set that plausibly fits."
The real post-extraction `geo` schema is **~1,951 MB — 95% of the 2 GB `effective_cache_size`**, with
nothing left over. And `effective_cache_size` is not a private allocation: it describes the whole
container's page cache, which `agri.signal_observation` (26 GB, out of scope per the note above) competes
for continuously.

**Honest restatement: the extraction cuts the `geo` schema ~5.8× (11,389 → ~1,951 MB), which is a large
and worthwhile win, but it does NOT by itself produce a working set that fits cache, and it is not on its
own sufficient to justify lowering the cap back to 1 GB.** Two levers are available if that is the goal
and neither is currently planned: `geo.drought_areas`' 493 MB TOAST, and `agri.signal_observation`'s 15 GB
of index. §0.19.6 item 50 (lower the cap) should be read against ~1,951 MB, not ~1,456 MB. The §0.16.9
caveat still applies on top: cache-fit is a *plausible* mechanism for the 5.57% heap hit ratio improving,
never a proven one.

#### 0.18.2 Sync protocol

**Watermark: a single global `geo.revision_seq`, stamped on INSERT and re-stamped by a `BEFORE UPDATE`
trigger.** A wall-clock or `observed_at` cursor is wrong here and the code says why:
`sql/ingest/reopen_gap_windows.sql` republishes historical windows and caps at five generations *because it
recurs*, and USDM/ERA5 backfill. A monotone revision is the only cursor that survives history being
rewritten.

**Known hazard, and it must be designed around rather than argued away.** Sequence values are allocated at
*statement* time, not commit time. `writer.py:38` sets `INSERT_BATCH_SIZE = 100` and commits one bounded
batch per transaction, and the hourly forward pass runs concurrently with two durable archive walks. **A
transaction that allocated revision 1000 can commit after a client has advanced past 1005; those rows
become permanently invisible to that client.** The winning design originally cited non-transactionality as
a *benefit* and examined only the benign gap class. That reasoning is rejected. Mitigations, in order of
preference:

1. **Publish a lagging high-watermark.** The response's `high_watermark` is `min(revision)` over
   in-flight transactions minus one, not the sequence's `last_value`. Clients never advance past a cursor
   that could still be overtaken. This is cheap and it is the correct primary answer.
2. **A nightly drift assertion** (graft, durability lens): any row whose `updated_at` exceeds the last
   published watermark but whose `revision` does not, fails a check. This catches the real long-term
   failure — a sixth reading table added in six months **without** the trigger, or a maintenance UPDATE
   under `session_replication_role = replica`, making rows permanently invisible with no error anywhere.
   That is the §0.11 matview-OID shape reproduced one level down, and it needs an alarm, not a comment.

**Wire shape.**

```
GET /api/v1/sync/{stream}?since=<int8>&scope=<hash>&bbox=&from=&to=&limit=5000
→ 200 { rows:[...], tombstones:[...], next_cursor:<int8>, high_watermark:<int8>,
        sealed_through:"2026-07", retention_floor:<int8>, complete:true|false }
     ETag: W/"<stream>:<high_watermark>"    Cache-Control: private, max-age=0
```

`WHERE revision > $since AND station_id = ANY($scope_stations) ORDER BY revision LIMIT $n` — an index scan
on `(revision)` within the current partition, stable under concurrent insert. This replaces
`ORDER BY features.id` + `OFFSET` over a random uuid (§0.17.8). `next_cursor` is the last row's revision,
never a row count.

**Tombstones.** `geo.tombstone(revision int8 PRIMARY KEY DEFAULT nextval('geo.revision_seq'), stream text,
entity_key text, observed_at timestamptz, deleted_at timestamptz DEFAULT now())`, written by an AFTER
DELETE trigger, retained 180 days, **with the retention floor published in every response** (graft, scale
lens) so a client can tell whether it is inside the guarantee rather than discovering it via a reset. A
client whose `since` predates the floor gets `410 Gone` and re-seeds from the sealed-month Parquet — cheap,
because those bytes are on the edge. There is no tombstone mechanism anywhere today, so any local store
monotonically accumulates rows the server no longer has.

**ORDERING HAZARD — this bit the winning design's own migration and must not be repeated.** The AFTER
DELETE trigger must be installed **after** the mass extraction completes, or gated on a session GUC the
migration sets. Phase B5 alone deletes 1,417,935 water-gauges rows from `geo.features`; the full extraction
deletes ~4.84M. With the trigger live, that emits ~4.84M tombstones instructing every synced client to
delete data that merely **moved tables** — silent, client-side, no server error, no test.
`geo.tombstone` also needs a named **pruner**, not only a writer; a retention policy with no pruner is the
`interventions` pattern (registered everywhere, produced by nothing).

**Scope is a durable subscription, not a request argument.** Today bbox and date are arguments and nothing
persists what matters to a user, so there is nothing to diff a watermark against and eviction is
relevance-blind LRU. Persist `{scope_id, bbox, streams[], day_from, day_to, station_ids[]}` in IndexedDB
and send a hash. The server needs it only to resolve `station_ids` (a GiST lookup over ~2,200 rows,
sub-millisecond) and to name sealed months; it does **not** persist per-client sync state — that is O(users)
storage and the first thing to break at scale. Scope resolution is its own endpoint,
`GET /api/v1/scope/resolve?bbox=`, and its answer is cacheable by bbox.

**Bbox quantizes to the z8 tile envelope(s) covering the viewport** (graft, scale lens), not `toFixed(6)`.
This kills D6 *and* is the precondition for any CDN hit rate: a z8 key is stable across any pan inside
~150 km, and the key space over the PNW extent is ~70 keys rather than unbounded. It is probably the single
largest perceived-speed win available on the client.

**Capabilities become the watermark index.** `getSliderCapabilities` is already a server-computed,
server-memoized, day-granularity per-layer watermark — 45,807 B, 6.75-10.69 s cold, no ETag, and **not
persisted**. Extend it with `{stream: {high_watermark, sealed_through, earliest, latest, retention_floor}}`,
gzip it (~6 KB), give it an ETag, and persist it. It is the single object a client fetches first and the
only one it must have.

**How a client offline for a month recovers.** (1) Fetch capabilities; compare `retention_floor` against
its stored cursor. (2) If inside the floor: one or more `?since=` pages, plus tombstones, done. (3) If
outside: `410 Gone` → discard the hot-window store, re-seed the sealed months from Parquet by content hash
(most are already in `cold-artifacts` and are immutable, so this is usually a *diff*, not a download), then
one `?since=<start of the current open month>` page. (4) **`410`/`resetRequired` must be a counted metric,
not merely a response field** (graft, scale lens): if clients start re-seeding in the wild, the 180-day
floor is wrong and nothing else in the system will say so.

**Conflicts.** Reads are immutable artifacts or server-authoritative rows, so read conflicts do not exist.
Writes exist only for contributions/interventions, where `src/lib/offline/sync-queue.ts` (`plantgeo-offline`
v2, stores `sync-queue` + `sync-conflicts`, `SyncOperation`, `isSnapshotStale`, the 409 backstop, the
`OfflinePanel` resolution UI) is **already fully built and has no producer** — nothing ever enqueues, so
`pendingCount` is structurally always 0 and `SyncIndicator` reports a state that cannot occur. Wire the two
mutation call sites to enqueue with the `updatedAt` they read and replay it as a precondition; the server
returns 409 on mismatch. **Do not design a new mechanism, and do not build a CRDT/OT/merge layer** —
last-writer-wins is not acceptable for a moderation queue and the server-side precondition is the whole
answer, at roughly 40 lines because everything downstream exists.

**Steady-state motion of the entire warehouse**, from the 90-day per-day census (§0.16.3): streamflow
7,977-17,674 rows/day · sensors ~7-9k · fire-detections 1,357-4,278 · weather 630-2,528 · vegetation 18-93.
At target widths that is **~1.4 MB/day raw, ~350 KB/day gzipped, unscoped, all streams combined.** A client
polling once per 60 s pulls ~120 streamflow rows ≈ 1.7 KB raw / ~600 B gzip.

#### 0.18.3 Client storage — extend `query-persister.ts`, do not rebuild it

`src/lib/cache/query-persister.ts` is already a real local-first store, not an HTTP cache: two-store split
(payload + ~100-byte metadata row, so a sweep never deserializes a ~98 KB payload), running byte totals,
serialized capacity passes, a quota ceiling **learned from a refused write** rather than guessed per
browser, a `navigator.storage.estimate()` backstop, LRU eviction, SWR revalidation with a generation guard
and a 60 s/entry floor, and layer/day attribution stamped at write with a `JSON.parse(cacheKey)` fallback
verified against 504 live production entries. Its 24 KB AGENTS.md carries the production profile behind
every constant (504 entries / 49.57 MB / ~98 KB per entry; 235 of 504 expired-but-resident before the sweep
existed; `getSoilField` alone 137 of 504). **A blank-page design rebuilds all of that and lands somewhere
worse.** Three additions, zero replacements.

**1. A third object store, `cold-artifacts`** (IndexedDB v3, an in-place upgrade of exactly the kind v2
already performed). Key = content hash (`sha256:off:len`), value = the raw `ArrayBuffer` of a Parquet row
group or a PMTiles byte range, plus a metadata row in the **existing** `StoredEntryMetadata` shape so the
**existing** sweep evicts it with no new eviction code. Immutable-by-hash means **no TTL at all** — evicted
by relevance and LRU, never by expiry.

**2. One quota arbiter — this closes a live self-destruct bug.** Today `MAX_TOTAL_CACHE_BYTES = 512 MB` in
`query-persister.ts` and `CACHE_SIZE_LIMIT_BYTES = 500 MB` in `sw.js:5` are two independent budgets against
**one** origin quota. Once IndexedDB alone passes 500 MB — which its own budget explicitly permits, and
which `src/lib/cache/AGENTS.md` justifies as "roughly a year of daily entries" — `sw.js:107` deletes 10% of
the tile cache on **every subsequent tile write, forever**, while never reclaiming a byte of what actually
filled the quota. The offline tile cache silently self-destructs and nothing observes it. Fix: a new
`src/lib/cache/storage-budget.ts` owning `navigator.storage.estimate()` and the learned ceiling, handing
out shares — **cold artifacts 40% / query cache 30% / tiles 30%**, tunable. `MAX_TOTAL_CACHE_BYTES` becomes
a share, not a constant; keep the 16 MB `MIN_CACHE_BUDGET_BYTES` floor.

**3. Freshness keyed on revision, which retires the TTL classes** and with them open defect C1. Today
`resolveCacheTtlMs` (`:303-310`) picks `HISTORICAL_TTL_MS` (30 days, `:68`) vs `LIVE_TTL_MS` (5 min) by
`date < serverCurrentDate` — and every layer opens on its *latest observed* day, which is by definition
before today, so the 30-day class applies universally and the name means nothing. Replace the predicate: an
entry is fresh iff `entry.revision >= lastSeenHighWatermark[stream]`, and the watermark arrives with the
persisted capability payload on every cold start. Immutable artifacts: no TTL. Live edge (no `date` in the
input): 5 min. Background revalidation stays — its own AGENTS.md correctly calls it a correctness
mechanism, not a nicety — but becomes a no-op whenever the watermark has not moved.

**Eviction should be relevance-ordered, not LRU** (graft, cold-ledger design). LRU is relevance-blind,
which is precisely wrong for "only data that matters to the user". Score by scope membership × layer
active × decay by day-distance from the slider, and never evict an in-scope immutable artifact before an
out-of-scope mutable one. Every input already exists in the metadata store (`layerId`, `day`,
`approxByteSize`, `lastAccessedAt`). **Caveat carried from the durability lens: a scoring bug evicts
in-scope data and presents as a cache miss, never an error.** Keep LRU as the tiebreaker and log evictions
of in-scope entries.

**Offline guarantee, stated as a testable claim.** Today the honest answer to "can it serve a map view
offline" is an unambiguous **no**: the client persists only 9 tRPC procedures
(`CACHEABLE_LAYER_QUERIES` — streamflow, groundwater, vegetation index, drought, soil field, climate field,
soil survey, watersheds, weather-for-bbox), no basemap, no vector tiles, no fires, no time axis — and
"Download this area for offline" downloads **only Esri satellite imagery** (`tile-cache.ts`'s
`TILE_TEMPLATES` has exactly one entry; a 500-tile walk is ~7 MB). A user who explicitly asks for offline
coverage gets aerial photographs with no cartography and no data on them.

After this design, following one online session with a scope set, a **region pack** yields: basemap PMTiles
byte ranges for the scope bbox (~80 MB for PNW z0-12), per-layer geometry for the entity layers (~20 MB;
measured z6 tiles: burn_severity 105,636 B, watershed 31,274 B, evacuation_zone 15,153 B), station
registries (~2 MB), sealed months for the scope window (~25 MB/stream/month, **INFERRED**), the hot window
as of last sync, and the persisted capabilities so the slider has a real domain. **≈160 MB for a 30-day PNW
pack.** It does **not** yield live-only sources (`fire_risk_tiles`, `sensor_tiles`) — and **the UI must say
so rather than render a silently empty map.**

**Reading Parquet in the browser: hyparquet, not DuckDB-WASM.** hyparquet is pure JS at tens of KB and does
the one thing needed — range-read specified row groups with a column projection. DuckDB-WASM is ~30-35 MB
of wasm on a cold load, inside a deliberate bandwidth cost control; that is self-defeating. DuckDB stays
where it already lives (`services/agri-data-service/pyproject.toml:28`, `duckdb>=1.1,<2`), server-side and
build-side, where the consumers genuinely are `.sql` files. **hyparquet is the one unproven library bet in
this design** and it is gated accordingly in §0.19, with a pre-chosen fallback: a Cloudflare Worker that
slices the row group server-side and returns JSON — still serverless, still edge-cached, one Worker, no
wasm.

**Budget integration.** Every artifact fetch goes through `createBudgetedFetch` on named lanes — `"tiles"`,
`"sync"`, `"scrub"` (foreground, user waiting), `"prefetch"`, `"revalidate"`. Merging
`query-persister.ts`'s private `MAX_CONCURRENT_REVALIDATIONS = 2` semaphore (`:801-832`) onto the
`"revalidate"` lane closes the gap its own `src/lib/net/AGENTS.md` names, and makes background
revalidation visible in `getRequestBudgetSnapshot` so it can never head-of-line-block a foreground paint.
**Budget by origin class** (graft): R2/Cloudflare fetches cost the 2 GB box nothing and belong on an
`"r2"` lane with a ceiling of ~12; origin fetches (Martin, Next) keep the tight ceiling. Raise the global
cap 4 → 8 only because lanes now mean something. The SW-mediated tile prefetch — currently a 500-request
serial walk with no rate limit, no backoff, no abort, and invisible to `getRequestBudgetSnapshot` — moves
onto a lane too.

#### 0.18.4 What moves to Parquet on R2, and what must not

**The monthly RANGE partition IS the Parquet file.** `geo.streamflow_reading_2026_07` exports to
`r2://plantgeo-parquet/observations/stream=streamflow/month=2026-07/part-<sha>.parquet`. Nothing is
transformed, reshaped or re-aggregated — **export a whole partition, not the result of a SELECT** (graft,
scale lens): it eliminates the class of bug where the exporter's projection drifts from the table schema,
and makes verify-then-detach a single coherent operation. This is exactly why the model fix must come
first; exporting today's jsonb heap would produce files whose dominant content is duplicated station
metadata and a second copy of every geometry.

**Sealed vs live is the cold/warm boundary.** A month is *sealed* when `now()` passes its end plus a
**7-day reopen grace** (the gap-reopen lane caps at five generations, so 7 days covers the observed
republish window; a late republish bumps a generation suffix and rewrites the one file). Sealed months get
`Cache-Control: public, max-age=31536000, immutable` — the exact header
`tiles.aevani.com/pnw-2026-08-02.pmtiles` already carries. The current month is served only from Postgres,
via `/api/v1/sync`.

**Layout**, with `manifest/v1/latest.json` as the **only** mutable object (~20 KB, `max-age=60`, ETag);
every data object carries its sha256 in the key. Split the row-group index into per-`(layer,month)` side
files the moment `latest.json` exceeds ~100 KB gzipped — 11 layers × ~50 months × ~30 row groups × ~80 B is
~1.3 MB, which would make the manifest larger than the 45,807 B capabilities payload it exists to replace.
Publish atomically by copying `scripts/raster/publish-soil-rasters.py`'s discipline: immutable `<sha>.json`
first, mutable pointer **last**, superseded objects retained 30 days so a client mid-sync against an older
manifest keeps working.

**What leaves Postgres.** All historical observation reads (the `MAX_ROWS = 2000`-bounded `DISTINCT ON`
readers in `environmental-read-model.ts` collapse to "latest row per station in the current month", a
partition-local index scan — today `readStreamflowGaugesOnDay` does `DISTINCT ON (f.properties->>'siteNo')`
and `readWeatherOnDay` does `DISTINCT ON (ST_X(f.geom), ST_Y(f.geom))`, **undoing the modelling error on
every single request, sorting on an unindexed jsonb extraction**). The four broken agent SQL tools
(§0.16.4) repoint at DuckDB over `r2://` — DuckDB was chosen over Polars precisely because these consumers
are `.sql` files with real spatial needs, and that reasoning holds here and nowhere else. **Delete those
four files last**: they are the only surviving specification of the dropped rollup's grain and column set.
And the census/rollup work the matviews serve moves off the 2 GB box entirely.

**What must stay in Postgres, permanently — hand-waving this is how serverless designs fail.**

1. **All writes.** Ingest upserts, `ON CONFLICT`, the `agri.job_*` durable ledger, transactions. DuckDB is
   single-writer; Parquet is not a database.
2. **The live edge.** A month still accumulating has no stable content hash. Definitionally un-artifactable.
3. **Real spatial predicates over the hot window.** Point-in-polygon for soil-survey and watershed
   containment, `ST_Intersects` for bbox scoping on `ix_features_layer_geom` and the `geo.station` GiST.
   Parquet has no spatial index; a bbox/geohash column prunes row groups and nothing finer. After
   extraction these run against ~250k rows, not 5.09M. Deep-history spatial questions route to DuckDB with
   a **mandatory bbox + date bound enforced in the tool signature, not by convention**.
4. **Martin MVT for the six entity-backed tile layers.** Retained — this design does not retire Martin.
5. **Identity and mutable configuration.** Auth, teams, API keys, `geo.layers` (11 rows, mintable at
   request time by `layersRouter.create`), contributions, moderation. `contributions.listPendingReview`
   (`:82`) deliberately queries features across all layers and `submitObservation` (`:7`) has no equivalent
   anywhere — write-heavy, latency-sensitive, unfinished scope. Not dead code, not a candidate.
6. **Anything needing a serializable cross-stream read.** There is nothing like this today. Recorded so it
   stays that way — introducing one punctures the design.

**Registering the export lane is not optional** (graft, durability lens, and no design supplied it).
`export-parquet` must appear **both** in `BACKFILL_LANES` (`ingest/lanes.py:228-230`) **and** as a
`StreamDefinition` with a cadence. Without the first, `jobs-pulse`'s durable pass never discovers it and
`jobs-plan-gaps` can never turn a missed stream-month into a claimable work item; without the second,
`validate-streams` never reports the hole. A job-ledger work item alone gives a producer with no gap
detection — two-thirds of the rot pattern that already afflicts 9 of 11 layers (§0.16.5).

**And the freshness contract must reach past the stream catalog's blind spot** (graft, promoted to
non-negotiable). The manifest carries `built_from_max_updated_at` per stream/layer, and `readiness.py`'s
new Parquet section **fails** — not warns — when Postgres `max(updated_at)` exceeds it by more than that
stream's declared cadence. But `validation/models.py:143-146` gives soil-survey, watersheds, burn-severity
and interventions `kind="reference"` with **no** `publication_cadence_days`, so `validate-streams` applies
zero staleness check to exactly those four. **Add an explicit `expected_refresh_days` to those four
`StreamDefinition`s in the same change**, or every artifact built from them inherits the identical blind
spot that has left watersheds frozen since 2026-08-07 with a complete producer and no invoker.

**R2 write is not new capability.** `scripts/deploy-pmtiles.sh` exists and already takes
`R2_BUCKET`/`R2_ENDPOINT`/`R2_ACCESS_KEY_ID`/`R2_SECRET_ACCESS_KEY`; `scripts/raster/publish-soil-rasters.py`
already publishes six SoilGrids COGs under exactly that contract; `tiles.aevani.com` already serves a
1.41 GB archive with correct `immutable` headers and working ranges. What is missing is credentials on the
cron service and an export job. **Open risk:** `plantgeo-parquet` (id `79d5b0c0-059a-40a9-a90a-ef8d15bb5828`,
region `sjc`) is a **Railway** bucket, not obviously the Cloudflare-fronted R2 that serves
`tiles.aevani.com`. If it is not CDN-fronted, every "edge-cached" claim degrades to origin latency. First
`curl -I -H Range -H Origin` against a published object settles it; the design does not depend on which
bucket, so publish to the account already serving `tiles.aevani.com` if needed.

**Also gate on the CDN itself.** `cf-cache-status` is **DYNAMIC, not HIT**, on both PMTiles ranges tested
despite `max-age=31536000, immutable` and a strong ETag (§0.17.5). If a Cache Rule cannot change that, the
serverless story is "R2 origin fetches with extra steps" and the byte model below is wrong. **Prove HIT
before writing a single Parquet byte.**

#### 0.18.5 Bandwidth arithmetic — today vs target

**COLD, today, default camera** (all measured, §0.17):

| item | bytes | latency |
|---|---|---|
| app shell (HTML 3,648 gz + 16 JS/CSS chunks 287,108 gz + 6 woff2 187,824) | **478,580** | 0.20 s TTFB |
| terrain DEM, 8-12 × 76,714 B | ~770,000 | 0.59 s each |
| Martin composite z6/11/22 | **4,063,189** | **36.9 s cold** / 1.0 s warm — **and it does not return at all with gzip** |
| `/api/fires` (no bbox, no compression) | **793,110** | 7.80-20.02 s |
| `getSliderCapabilities` (no compression, no ETag) | **45,807** | 6.75-10.69 s |
| PMTiles ranges | ~200,000-400,000 | 0.25-0.28 s, `cf-cache-status: DYNAMIC` |
| **total** | **6,550,686 B high / 2,287,497 B low** — **≈2.3-6.6 MB** | **26 s to never** |

*(Sums shown 2026-08-21; the row previously read "≈2.5-6.4 MB" with no derivation.* **High bound** =
478,580 + 770,000 + 4,063,189 + 793,110 + 45,807 + 400,000, i.e. the composite **returns**. **Low bound**
= 478,580 + 770,000 + **0** + 793,110 + 45,807 + 200,000, i.e. the composite **returns nothing** — which
is the case today for most cameras, and is the outage. Note what that means: **the low bound is the broken
state, not a better one.**)

**COLD, target, same camera and layer set:**

| item | bytes | note |
|---|---|---|
| app shell | 478,580 | unchanged; already `immutable` + content-hashed |
| terrain DEM | ~770,000 | unchanged (third-party, already cached well) |
| Martin composite, day-bounded + `LIMIT` + gzip | ~85,000 | measured proxy: the same composite at z8/43/93 is 203,311 raw / **84,634 gzip** |
| `/api/fires` → bbox-scoped + gzip | ~70,000 | INFERRED at ~10:1 GeoJSON compression, scoped to viewport |
| capabilities + watermarks, gzipped + ETag'd | ~6,000 | from 45,807 raw |
| PMTiles ranges | ~200,000-400,000 | same bytes, edge-cached once the Cache Rule lands |
| **total** | **1,809,580 B high / 1,609,580 B low** — **≈1.6-1.8 MB** | **target < 3 s to useful paint** |

*(Sums shown 2026-08-21. The two bounds differ only in the PMTiles range figure, 400,000 vs 200,000.)*

**−30% to −72% bytes, depending on whether the composite returns today.** *(Corrected 2026-08-21; this
read "roughly −70%", which is true only against the upper bound.* 1,809,580 vs 6,550,686 = **−72.4%**;
1,609,580 vs 2,287,497 = **−29.6%**. *The single figure was the one most likely to be quoted onward — it
is the headline the owner would hear about the bandwidth goal — so it gets the range, not the flattering
end of it.)* And note the honest reading of the low end: the −30% case is measured against a **broken**
today in which the composite returns nothing, so the target ships *more rendered layers* for 30% fewer
bytes, which is a better result than the number looks.

But **the byte cut is not the point and should not be sold as such**: the 87×
cold/warm ratio on identical bytes (§0.17.2) proves the pathological tail is Postgres page-cache misses. The
tail is removed by the `statement_timeout` + `LIMIT` + day-bound work in §0.17.10, not by compression.

**WARM (second visit, same device):** app shell 0 (immutable), tiles 0 within Martin's 5-minute expiry then
304, sealed observations 0 (immutable in IndexedDB), capabilities ~200 B via 304. **≈20-80 KB, first paint
from disk.** Today a warm load must revalidate *every* tile on *every* load because Martin sends an ETag
and no `Cache-Control`, and tRPC responses carry neither (and `revision` is hardcoded null at every
producer), so every background revalidation is a full re-fetch and full re-serialize of a ~98 KB payload.

**STEADY STATE, one open tab, one hour:** today `/api/fires` alone is 793,110 × 30 = **23.8 MB/hour =
571 MB/day/tab**, plus a 5-minute 45,807 B capabilities poll, plus every pan re-minting cold cache keys via
`toFixed(6)`. Target: one delta poll per 60 s per active stream — streamflow at ~12,000 rows/day steady is
~120 rows/min ≈ 1.7 KB raw / **~600 B gzip**; all five streams unscoped ≈ **350 KB/day gzipped**. Against
571 MB/day that is roughly **three orders of magnitude**, achieved by not re-sending data the client already
holds rather than by refusing to send data at all.

**Scrubbing the slider** — the interaction the whole time-slider programme exists to serve: a day already
in IndexedDB costs **0 bytes, 0 ms**; a new day costs one range read per active layer, ~40-80 KB, with no
footer round trip because the row-group index is inlined in the manifest. Today the same scrub is a full
tRPC re-fetch at ~98 KB/entry against a 5.57%-hit-ratio heap.

**Server-side cost at scale.** Today tile cost is O(N) Postgres queries against 8 pool slots at 0.3-102 s
each, so the system effectively saturates at roughly **one user** — 6-9 composite tiles on first paint
against an 8-slot pool. That has been hidden by the measured reality that both services sit essentially
idle across a full 7-day window (`plantgeo-main` CPU avg 0.0001 / mem avg 111.0 MB; `plantgeo-martin` CPU
avg 0.0031 / mem avg 177.5 MB, 10,081 sample points each), which independently settles the "~1 concurrent
user" assumption. Target: Postgres serves the live edge and writes only; sealed reads are CDN. **Honest
limit of this design: the sync endpoint is `private, max-age=0`, so it is O(N) in users, and its stated
ceiling is ~100 concurrent, not 100k.** If that ceiling is ever approached, the cold-ledger design's fix is
the graft to take: **drop the per-client cursor from the live-edge URL** and return the full bbox-scoped
window keyed on the z8 tile alone, letting the client dedupe locally — that collapses the key space to
~350 URLs refreshed once per 60 s, constant in N.

One more measured item worth carrying: **the real database is `plantgeo-spatiotemporal-db`**, not the
similarly-named near-idle `Aevani-Postgress` — querying the wrong one silently produces reassuring
numbers. Its 7-day memory is **avg 1.91 GB, max 3.0 GB** against a stated 2 GB cap. Either the cap was not
in force for the whole window (it was raised 2026-08-18, mid-window) or Railway's sampled metric reads
above the enforced cgroup ceiling just before an OOM restart. **UNVERIFIED which.** Worth settling before
claiming the cap holds.

#### 0.18.6 What this design does NOT claim

- It does not claim the byte cut fixes the outage. §0.17.10 fixes the outage; this fixes the model.
- It does not claim the ~1,456 MB target makes the working set "fit cache" — see §0.16.9's refutation.
- The Parquet size arithmetic (~25 MB/stream/month, ~60-150 MB for the whole observation corpus) is
  **INFERRED from row widths, never measured.** One `COPY (SELECT … ) TO 'x.parquet' (FORMAT PARQUET,
  COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)` and an `ls -l` falsifies or confirms it in minutes. Threshold:
  **>60 MB/month kills the cheap per-day range read**, and the fallback is smaller row groups (25k),
  int16-scaled floats, and `(observed_day date, minute_of_day int16)` instead of `timestamptz`.
- It does not claim hyparquet works here. That is a 30-line spike, and it is gated.
- It does not claim R2 is edge-cached. That is one Cache Rule and two curls, and it is gated.

#### 0.18.7 What the panel agreed on across all three designs

Worth recording, because unanimity across independently-constructed designs is itself evidence:

- **Split `geo.features` by shape.** All three. The 95.1% figure is the pivot.
- **`properties.geometry` dies; `geom` survives.** All three.
- **Native `external_id` + `observed_day`; the two expression indexes go.** All three.
- **Drop `idx_features_geom`.** All three. **Caveat added 2026-08-21: all three designs inherited the same
  premise — `idx_scan = 0` read as "never used" — from §0.16.6, and that premise is now known to describe a
  window of unmeasured length, not a lifetime. Unanimity across three designs is not independent evidence
  when all three read the same table.** The drop is still the right call on the balance of evidence (two
  independent samples at 0/0 while every other index advanced; full coverage by `ix_features_layer_geom`;
  87.4% of its content in two layers no tile function reads by geometry) — but it now carries the
  ≥7-day-uptime precondition on §0.19.3 item 11 rather than shipping on the strength of the consensus.
- **Per-stream narrow tables, never one polymorphic observation table**, with
  `agri.signal_observation`'s 15 GB of index as the proof. All three.
- **Keep and extend `query-persister.ts`; one quota arbiter; retire the TTL classes.** All three.
- **Re-aim the request budget at tiles; do not remove it.** All three.
- **Martin `statement_timeout` first, before any measurement.** All three.
- **Do not raise `shared_buffers`.** All three.
- **The offline write queue needs a producer, not a merge algorithm.** All three.

#### 0.18.8 Recorded rejections — do not re-litigate

1. **The `geo.features` LIST-partition swap on `layer_id` (`drizzle/0030`-`0033`,
   `scripts/partition-features.mjs`, `docs/pending-migrations/0033-features-partitioning.md`). SHELVED, not
   deleted — and this is the largest deliberate reversal in the programme.** Three independent reasons:
   **(a)** the partitionwise probe measured **byte-identical plan text** for all three census matview shapes
   with both GUCs on and off, because each joins the never-co-partitionable 11-row `geo.layers` before
   aggregating (§0.16.7) — the exact workload blowing the cap gets zero relief; **(b) — WEAKENED 2026-08-21,
   do not lean on this one:** all six tile functions already plan as a cheap composite-index `Nested Loop`
   **at z10**, so tile latency *at z10* is not a justification either. **The EXPLAIN was never taken at z5
   or z6, which are the zooms that actually hang** (§0.17.2: z5/5/11 status 000 at 25 s; z5/4/10 at 102 s;
   `sensor_tiles` z6/11/22 = 3.9 MB raw, against a z10 plan whose total cost of 56.17 implies a handful of
   rows). **Ground (b) is therefore not evidence for shelving as stated** — the shelve recommendation rests
   on (a) and (c), which are unaffected. The z5/z6 `EXPLAIN` is read-only and named in §0.16.7; run it
   before anyone re-opens this; **(c)** the extraction deletes 95.1% of the rows the swap exists to organise, leaving a ~250k-row
   table for which partitioning is pure overhead. Against that: a 5M-row copy, ~6 GB of new relation, a
   queued `ACCESS EXCLUSIVE` rename that blocks every reader behind it while it waits, six matviews to drop
   and rebuild because their rewrite rules bind **by OID** (§0.11), a mandatory DEFAULT partition and drain
   (§0.4), a PK change that must land in the same commit, `max_locks_per_transaction` 128 against ~145
   relations, and the **permanent** loss of `CREATE INDEX CONCURRENTLY` on the parent. **What survives:**
   `drizzle/0033`'s constant-`layer_id` pruning is still worth registering on its own merits as a plain
   function rewrite; RANGE-by-month partitioning **is** used, on the new observation tables, where it is
   free (create-empty, no copy) and maps 1:1 onto the Parquet layout; `partition-features.mjs` is the right
   driver for those. Revisit `geo.features` partitioning only if extraction slips badly — and then it is
   cheap, because the table is small.
2. **Any engine migration.** Refuted across 14 agents (`docs/research/timescale-pivot-2026-08-17/report.md`):
   Martin has no ClickHouse support, VictoriaMetrics is float64-only, and `pg_ivm`/`pg_partman`/`pg_duckdb`/
   `citus` are not installable on Railway managed PG. A fixed constraint, and correct.
3. **Iceberg / DuckLake / R2 Data Catalog.** 15+ months of open beta with no GA date, and the catalog is
   the thing you would have to operate — which is the thing being removed. A Hive-partitioned prefix plus a
   hand-written manifest gives atomic publish (write the pointer last), content addressing and time travel
   in ~200 lines.
4. **Materialized views, continuous aggregates and TimescaleDB in the target.** The owner's stated goal,
   and the evidence agrees: `mv_signal_observation_day` has never once succeeded, `mv_soil_survey_union` has
   never produced a row, `mv_soil_survey_grid` has been failing 10+ days **and neither soil matview has a
   single reader** (§0.16.4, 2026-08-21 — both `usda-soil.ts` readers are documented as deliberately NOT
   repointed, with grain-mismatch reasons), and the perpetual refresh run is 74 items / **70 failed** /
   2 succeeded / 2 unaccounted (§0.16.4). Narrow reading tables make the census a native
   `GROUP BY observed_at::date` over 48-byte rows; there is nothing left to pre-aggregate. TimescaleDB has
   one hypertable at 0 chunks / 40 kB, so removing it is bookkeeping, not relief — sequence it last and
   separately so any relief stays attributable (§12.7).
5. **A polymorphic `geo.observation` table.** See §0.18.1 and the standing review rule.
6. **A generic sync engine (ElectricSQL / PowerSync / Zero).** They solve bidirectional row-level sync over
   a *live* Postgres — the opposite problem. Here 95% of the data is immutable append-only history that
   wants CDN economics, and each adds a stateful server, which is what is being removed.
7. **A CRDT / OT / merge-conflict layer.** The offline mutation queue has no producer; `pendingCount` is
   structurally always 0. Server precondition + 409, using the machinery that already exists.
8. **DuckDB-WASM in the browser.** ~30-35 MB of wasm inside a bandwidth cost control, to read a file whose
   own footer already carries the index.
9. **Removing or loosening the request budget as the fix for slowness.** Provably never sees a tile request
   (§0.17.6). Re-aim it.
10. **Raising `shared_buffers`.** 256 MB against a 7.9 GB table on a 2 GB container that also hosts a 26 GB
    relation. Shrink the working set. `effective_cache_size` = 2 GB stays deliberately untouched.
11. **`CLUSTER geo.features USING ix_features_layer_geom`.** Would buy most of the locality the 0.235
    `layer_id` correlation is missing, but takes `ACCESS EXCLUSIVE` on 7.9 GB under live ingestion — and
    extraction makes it moot by removing the scattered rows.
12. **Rebuilding the client cache layer.** §0.18.3.
13. **Fixing C1 by retuning `HISTORICAL_TTL_MS`.** The predicate is broken, not the constant.
14. **Registering `drizzle/0033` as part of the acute fix.** Six functions replaced at once; one bad
    function 404s the whole composite.
15. **Deleting the four `mv_signal_cell_daily` consumer SQL files now.** They are the only surviving
    specification of the dropped rollup's grain. Deleted **last**, after the replacement serves.
16. **Real tombstones on the *cold* lane.** A month file is the truth for its month and entity layers ship
    full snapshots, so absence means deletion for free. Tombstones exist only for the hot window.
    Breaking point, stated so it can be detected: an entity layer above ~1M rows, or a month file large
    enough that rewriting it for one deletion is wasteful.
17. **Server-side per-user sync state.** No `sync_state` table, no per-client cursor storage, no
    change-tracking triggers beyond the revision stamp. O(users) storage is the first thing to break.

---

### 0.19 Programme plan and gates

One ordered plan, merging §0.13's outstanding list with the §0.18 architecture. Gate classes:
**code-safe** (edits files, nothing applied) · **read-only** (queries production, no writes) ·
**prod-mutating** (changes production state) · **deploy-gated** (needs a deploy or a rebuild) ·
**owner-decision** (needs a human call).

**NUMBERING CONVENTION.** Items **1-50** are the original programme and are cited by number from other
sections, so they are **never renumbered**. Items added later carry a **letter suffix** on the item they
sort after — `2a`, `11a`, `37a` — plus **item 0**, which sorts before item 1. Eight such items were added
2026-08-21 to close scope gaps a completeness review found: **0** (browser baseline), **2a** (the gzip
hang), **4a-4d** (ingestion — the owner's first stated goal, which had zero numbered items), **11a** and
**37a** (`agri.signal_observation`, 70% of the database and previously in no item), **13a** (C1), and
**43a** (`drizzle/0035`). Where a gap was closed by a *precondition* rather than a new item, the change is
in the existing row.

#### 0.19.0 READ THIS FIRST — the production cutover was deliberately NOT executed

**This programme executed no production cutover.** Specifically, **none** of the following happened:

- the `geo.features` partition swap (`scripts/partition-features.mjs --phase=create/copy/index/trigger/verify/swap/analyze/adopt`)
- the post-swap matview re-creation
- any migration registration in `drizzle/meta/_journal.json` (journal head is still **idx 29**;
  `0030`-`0036` exist on disk and are all dormant)
- any `src/lib/server/db/migration-contract.ts` re-pin
- any deploy of `plantgeo-main` or rebuild of `plantgeo-martin`
- any Martin restart
- any cron quiesce window
- any `ALTER SYSTEM`, `ALTER ROLE`, DDL, DML, `VACUUM`, `REINDEX` or `REFRESH` against production

Production is untouched by this programme except that read-only probes ran and **left leaked Martin
backends behind** (§0.17.3) — those need `pg_cancel_backend()`.

**What an operator runs, in order, when the owner opens a window.** This list assumes the owner has decided
to proceed with the *partition swap*, which §0.18.8 item 1 recommends **shelving**. If the swap is shelved,
steps 3-9 drop out entirely and the window is only steps 1-2 and 10-12.

1. **Quiesce.** Stop `plantgeo-ingest-cron`, `plantgeo-cron-mtbs`, `plantgeo-cron-soilgrids`, and pause
   `plantgeo-main`. §0.15 measured **+11,382 rows in hours across seven of eleven layers** — the quiesce is
   not optional politeness, and `--phase=verify`'s per-layer count equality can only be trusted against a
   quiesced database.
2. **`pg_cancel_backend()` any leaked Martin backends**, then confirm `pg_stat_activity` is quiet.
3. **RE-RUN `node scripts/partition-features.mjs --phase=plan`. This is mandatory and it is not a
   formality.** Two things move under you: (a) the census (see step 1), and (b) **the dependent-matview
   list is a function of migration registration order** — the catalog reported **six** dependents on
   2026-08-20, but registering `drizzle/0031` first makes it **seven** (`mv_feature_observation_day_axis`
   appears only once 0031 lands). §0.11's OID trap is a silent-wrong-answer bug: a matview left pointing at
   `geo.features_legacy` reads stale data forever with no error. **Use the list this run prints, never the
   one written down in §0.11 or §0.15.**
4. `--phase=create`, `--phase=copy` (with `--catchup`), `--phase=index`, `--phase=trigger`.
   **Chunk index creation across transactions** — `max_locks_per_transaction` is 128 and the swap touches
   ~145 relations (§0.13 item 5).
5. `--phase=verify`. Per-layer counts must match exactly. They will not if step 1 was skipped.
6. `--phase=swap`, then `--phase=analyze`.
7. **`node scripts/recreate-features-matviews.mjs --phase=plan`, then `--phase=recreate`, then
   `--phase=verify`.** Built this run (§0.19.1). It walks `pg_depend`/`pg_rewrite` outward from
   `geo.features_legacy` at any depth, so it catches plain views too — notably
   `geo.v_observation_day_census` (`drizzle/0032`), which `DROP MATERIALIZED VIEW … CASCADE` would destroy
   silently. `--phase=verify` passes only when **no relation anywhere still resolves to the legacy heap**.
   Note `mv_soil_survey_union` re-creating empty is **expected, not a regression** (§0.16.4) — do not
   roll back a good swap over it.
8. **The §0.8 step-6 smoke test is invalid as written** and must be run in this order: re-create
   `mv_layer_feature_stats` **first**, *then* diff the 11 counts. Diffing before re-creation reads the
   legacy heap and matches trivially.
9. `--phase=adopt`. `--phase=rollback` exists if any step fails.
10. **Register migrations.** Each registered migration must update `drizzle/meta/_journal.json` **and**
    `src/lib/server/db/migration-contract.ts` (tag, `createdAt`, sha256) **in the same commit** —
    `src/__tests__/security/readiness-migration-contract.test.ts` asserts the last journal entry's tag +
    `when` + file sha256, and `Dockerfile:67` runs `npm test` as a hard build gate. **Miss this and the
    deploy never ships.** Useful precedent that de-risks the whole ordering: `_journal.json` **already
    skips idx 26** (no `0026` file exists) and production is current — so registering a new `0034`+ while
    `0030`-`0033` stay dormant is proven safe, not novel.
11. **Deploy `plantgeo-main`**, then **restart or rebuild `plantgeo-martin`.** Nothing in the pipeline
    restarts Martin. `martin.yaml` is baked at **build** time (`Dockerfile.martin:3`), so a config change
    needs a **rebuild**; a `DATABASE_URL` change needs only a restart.
12. **Un-quiesce ingestion**, then fetch **one tile per rewritten source with an `Origin` header AND
    `--compressed`** before calling anything done.

#### 0.19.1 DONE this run

| item | class | note |
|---|---|---|
| Twelve read-only production probes and audits | read-only | §0.16, §0.17. No writes. |
| Two adversarial verification lenses over the findings | read-only | Two headline claims REFUTED (§0.16.9) |
| Partitionwise probe with a scratch schema | prod-mutating (scratch only, fully reversed) | Created `partitionwise_probe_scratch`, `DROP SCHEMA … CASCADE`, verified 0 rows in `pg_namespace` after |
| Three design proposals + three-lens judge panel | — | §0.18 |
| **`drizzle/0034_record_signal_cell_daily_drop.sql`** | code-safe, **dormant** | `DROP MATERIALIZED VIEW IF EXISTS geo.mv_signal_cell_daily` + a `to_regclass IS NULL` assert in `drizzle/0030`'s `DO $$` style. Closes §0.13 item 2. |
| **`drizzle/0035_soil_survey_union_collection_extract.sql`** | code-safe, **dormant** | Moves `ST_CollectionExtract(…,3)` into the `delineation` CTE so a repaired `GeometryCollection` cannot reach `ST_Union`. `0029`'s two existing extract calls only wrap the union's *output* — too late. Preconditions asserted in a `DO $$` block. |
| **`drizzle/0036_features_partitioned_precondition.sql`** | code-safe, **dormant** | Four catalog-read-only `DO $$` asserts: `relkind='p'`; `features_layer_external_id_unique` exists **and** `indisvalid`/`indisready`; the PK is composite `(id, layer_id)` in that order; `geo.features_default` exists **and** is the registered DEFAULT partition via `pg_partitioned_table.partdefid`. Closes §0.13 item 3. |
| **`scripts/recreate-features-matviews.mjs`** | code-safe, never run | Catalog-driven OID-trap repair, three phases. See §0.19.0 step 7. |
| **`scripts/data-quality-report.mjs`** | code-safe, never run against prod | Repeatable read-only harness: session `statement_timeout=25000`, never full-scans `geo.features`, checks all three observation planes, reads `agri.matview_refresh_state`, and derives tile reachability by extracting each function's `l.name` literal from `pg_get_functiondef` and diffing against parsed `martin.yaml` + parsed `DYNAMIC_TILE_SOURCE_IDS`. 14 named thresholds gate the exit code. |

**Nothing above is registered, applied, deployed or committed.** `_journal.json` is untouched.

**Where each of these is actually scheduled** *(added 2026-08-21 — `0035` and `0036` were listed as DONE
deliverables here while appearing in no tier at all, which reads as "handled" and is not)*: **`0034`** →
§0.19.6 item **43**, with a required header correction. **`0035`** → §0.19.6 item **43a**, newly added,
low priority, and carrying a deploy-block hazard. **`0036`** → **nowhere, and possibly never**: it asserts
`relkind='p'` on `geo.features` and RAISEs otherwise, so it is registrable **only if the partition swap
proceeds** — which §0.18.8 item 1 and §0.19.7 recommend **shelving**. Shelve the swap and `0036` is
permanently dormant dead code. §0.13's status note has been corrected from "RESOLVED" accordingly.

Three notes on the new files. `0035`'s DDL applying cleanly proves only that the SQL parsed — **watch
`agri.matview_refresh_state` for the next `matview-refresh` tick's outcome** to confirm rows actually land.
**And note what `0035` is for: `geo.mv_soil_survey_union` has ZERO readers** (§0.16.4 — `usda-soil.ts:1049`
documents the deliberate non-repoint), so this repairs a relation nothing consumes. That is a reason to
rank it low, and a reason to ask whether it should be dropped rather than fixed.
`0036` couples to `scripts/partition-features.mjs`'s current rename targets (parent → bare `geo.features`,
PK → `features_pkey`, default → `geo.features_default`); if that script's names change, `0036` must change
in the same commit or it fails against a swap that actually succeeded.

#### 0.19.2 TIER 1 — the acute fix. Do this before anything else.

Full detail in §0.17.10. Nothing here touches a schema.

| # | item | class | precondition | reversal cost |
|---|---|---|---|---|
| **0** | **BASELINE THE SYMPTOM IN A REAL FOREGROUNDED BROWSER — before touching anything.** Load production, assert `document.visibilityState === "visible"` **first** (§5's rAF trap has already produced one false negative here), then record **per layer** whether it draws, at the default camera **and** at z10. Capture the Network panel's per-request timing for the composite, `/api/fires` and `getSliderCapabilities`. **Explicitly cover the five non-Martin layers** — fire-detections, water-gauges, weather-observations, vegetation, soil-survey — which §0.17.7 predicts are unaffected; **that prediction is the section's key triage claim and is completely untested** | read-only | **none. Ordered before item 1.** The owner's actual symptom ("most of the layers do not render") has never been observed by this programme — §0.17 is entirely `curl` + `EXPLAIN` + source reading (§0.17.1) | n/a |
| 1 | **`?options=-c%20statement_timeout%3D20000` on `plantgeo-martin`'s `DATABASE_URL`** | prod-mutating (a variable) | **(0)** — capture the baseline first, then this is the first *change*. Every later measurement is contaminated by leaked backends. **Do NOT use `ALTER ROLE`** — there is one login role, `postgres`, superuser (§0.17.4) | delete the parameter; restart |
| 2 | `pg_cancel_backend()` the leaked pids | prod-mutating | (1) applied, else they re-accumulate | none (they are already abandoned) |
| **2a** | **ROOT-CAUSE THE GZIP-SPECIFIC COMPOSITE HANG.** §0.17.2 measured z6/11/22 returning 4,063,189 B in **<1 s uncompressed** and **timing out at 0 bytes with `--compressed`, at both 30 s and 60 s, reproduced twice** — and every browser requests gzip by default. Three probes, in order: **(i)** the same tile via Martin's **Railway service URL** vs. the **public domain**, to separate Martin from the Railway edge proxy; **(ii)** `Accept-Encoding: identity` vs `gzip` vs `br` at z6/11/22, same tile, same origin; **(iii)** dump Martin's **own response headers** on the succeeding uncompressed request (`Content-Encoding`, `Transfer-Encoding`, `Content-Length`, `Vary`) | read-only | (1)+(2), so the pool is not leaking underneath the measurement. **This is a PRECONDITION of the Tier 1 hard gate** — the gate tests `--compressed` and no other item addresses this path | n/a |
| 3 | **`maxParallelImageRequests: 6` + `transformRequest`** at `MapView.tsx:80-89` | code-safe → deploy-gated | none | one constructor option |
| 4 | **Un-crash `plantgeo-ingest-cron` (CRASHED); fix `plantgeo-cron-soilgrids` (FAILED)** | prod-mutating | none. **Every "ingestion is live" premise currently rests on a crashed service** — including this runbook's own safety rules and §0.19.0 step 1's quiesce. **Expected symptom of the crash: the ~11-day climate/weather stall in §0.16.2.** Item (4a) states what should recover | n/a — restoring intended state |
| **4a** | **After (4), verify the stall actually clears** — `agri.source_release.observed_to` must advance past **2026-08-06** for `nasa-power-daily` and past **2026-08-02** for `open-meteo-era5-archive` and `open-meteo-era5-land-archive` (§0.16.2). `SELECT data_source, observed_to FROM agri.source_release ...`. **If it does not advance, the crash was a symptom and not the cause**, and the stall is a separate live incident needing its own diagnosis | read-only | (4). Without this, an operator restarts a cron with **no stated expectation of what should recover** | n/a |
| **4b** | **Register the `watersheds` producer that already exists and is called by nothing.** `ingest/watersheds.py` is complete and working, with a CLI verb at `ingest/commands.py:264-267` — but it is absent from `ingest-all`'s job list (`commands.py:484-497`) and there is no `infra/cron-watersheds` (§0.16.5). All 9,396 rows landed on a single day, 2026-08-07. Add it to `ingest-all` or give it a cron | code-safe → deploy-gated | none. This is a finished producer nobody calls — the cheapest ingestion item in the programme | remove the entry |
| **4c** | **Add `expected_refresh_days` to the four `kind="reference"` streams** in `validation/models.py:143-146` (soil-survey, watersheds, burn-severity, interventions). Today they declare `kind="reference"` with **no `publication_cadence_days`**, so `validate-streams` applies **zero** staleness check to exactly the four layers that are 10-13 days static — **the layers that most need an alarm are structurally incapable of raising one** (§0.16.3) | code-safe → deploy-gated | none. **Pulled forward from item 38's Tier 4 cell** — it is independent of Parquet and does not belong behind it | revert the constants |
| **4d** | **OWNER DECISION: does `BACKFILL_LANES` grow to cover the nine unlooped layers, or is that accepted debt?** `ingest/lanes.py:228-230` has exactly two members (`firms-archive`, `streamflow-archive`). `validate-streams` **detects** gaps hourly for every stream, but for the other **nine** layers nothing converts a detected hole into a claimable `job_work_item` — the `docs/layer-lane-standard.md` §6 loop is **structurally absent**, not merely unscheduled (§0.16.5). Same call covers the **governed-absence** plane (§7 of the same standard), unimplemented for all 11 layers plus `drought_areas`, which is why `validate-streams` will re-report a legitimate absence as an open gap forever | owner-decision | (4a), so the decision is made against a working baseline rather than a crashed one. **This is the owner's first stated goal — "a strong performant data model *with ingestion*"** | n/a |
| 5 | **Root-cause the missing `Content-Encoding`**, then fix the real layer | read-only → deploy-gated | `next.config.ts` has no `compress` key, so "enable gzip in next.config" is a **no-op** | n/a |
| 6 | **`railway logs -s plantgeo-martin \| head`** to settle v0.7.0 vs 1.10.1 | read-only | none. Do before trusting any `martin.yaml` block but `cors` and `postgres.functions` | none |
| 7 | **The production smoke script** — composite with `Origin` **and** `--compressed` at three zooms, `/api/ready`, `/catalog` diffed against `DYNAMIC_TILE_SOURCE_IDS` — on the existing weekly cron | code-safe → deploy-gated | none. ~80 lines. **It would have caught D0**, and it is the only standing alarm on the read path (§0.16.8) | delete the cron entry |

**HARD GATE after Tier 1.** Composite at z5/5/11 **and** z6/11/22, with `-H "Origin: …"` **and**
`--compressed`, under 2 s, non-empty. Verify in a **real foregrounded browser** and assert
`document.visibilityState === "visible"` first — a backgrounded tab suspends rAF, fires zero tile requests,
and looks exactly like a total outage. That trap has already produced one false negative here. **If the
gate fails, stop and re-plan** — Tier 2's day-bound and `LIMIT` work jumps ahead of everything else.
**Compare against item 0's baseline, not against memory.**

**THE `--compressed` HALF OF THIS GATE IS OWNED BY ITEM 2a, AND THAT OWNERSHIP IS NEW.** *(Added
2026-08-21.)* Before 2a existed, this gate tested a defect **no item in the plan addressed**: nothing in
§0.17 argues that the `statement_timeout` and concurrency fixes will incidentally resolve the gzip hang,
and the evidence cuts the other way — the *uncompressed* path returned 4 MB in under a second on the same
tile that timed out with `--compressed`. So a Tier 1 that completed items 1-7 could still fail this gate
and send the operator into "stop and re-plan" with **no diagnostic step queued**. Run **2a** before
declaring the gate failed; a gate failure whose only symptom is the compressed variant is a **2a result**,
not a re-plan trigger.

**STALE-CACHE WARNING FOR EVERY VERIFICATION IN TIERS 1-3 — C1 IS OPEN AND STAYS OPEN.** *(Added
2026-08-21.)* `query-persister.ts:309` returns `HISTORICAL_TTL_MS` (30 days, `:68`) whenever the selected
day < `serverCurrentDate`, and **every layer opens on `latestObservedDate`, which is by definition strictly
before today — so the 30-day TTL applies universally, to every layer, for every returning user** (§0.17.9).
The only scheduled remedy is Tier 4 item 32, behind items 17-31 — the entire extraction, revision sequence,
sync endpoint and tombstone lane. §0.18.8 item 13 explicitly rejects the one-line mitigation (retuning the
constant) because **the predicate, not the constant, is what is wrong.**

Two consequences an operator must hold: **(1)** every post-fix check in Tiers 1-3 must be run with a
**cleared IndexedDB or in a private window**, or a warm cache will serve up-to-30-day-old layer data and
the map will look exactly as broken as before the fix; **(2)** real users are served stale data throughout,
and "the layers are still wrong" reports during this period are ambiguous between the outage and C1. The
interim fix that §0.18.8 item 13 does **not** reject is item **13a**: fix the *predicate* — freshness
relative to the layer's own `latestObservedDate` rather than `serverCurrentDate`.

**Tell the owner before Tier 1 ships: fixing the map will increase bandwidth cost**, because layers that
never rendered will start rendering. The §0.18.5 budget is a budget, not a hope — instrument real
bytes-per-cold-load and fail a check above 2.5 MB.

#### 0.19.3 TIER 2 — bounded tiles and index hygiene

| # | item | class | precondition | reversal cost |
|---|---|---|---|---|
| 8 | **`LIMIT` + `ORDER BY` inside each tile function's inner subquery** (§0.17.10 item 6 — the `ORDER BY` is not optional, or tile bytes become nondeterministic) | code-safe → deploy-gated | Tier 1 gate green. **One function per deploy**, verified standalone with an `Origin` header | `CREATE OR REPLACE` back |
| 9 | **Server-side day bound / `DISTINCT ON` latest-per-station** in `sensor_tiles` and `fire_risk_tiles` | code-safe → deploy-gated | (8) shipped | `CREATE OR REPLACE` back |
| 10 | **`CREATE INDEX CONCURRENTLY ix_features_fire_detection_day`** (§0.17.10 item 8) | prod-mutating | **must precede any partitioning** — CIC on a partitioned parent fails on PG 18.4 | `DROP INDEX CONCURRENTLY`. Temporary by design — dropped when fire-detections extracts |
| 11 | **`DROP INDEX CONCURRENTLY idx_features_geom`** (314 MB, 0 scans across two samples) | prod-mutating | **CHANGED 2026-08-21 — was "none".** Re-read `pg_stat_user_indexes` after **≥7 days of uninterrupted uptime** and confirm `idx_scan = 0` still holds. Read `pg_postmaster_start_time()` in the same statement: **this box restarts often** (caught 0.56 s old on 2026-08-21), so the 7-day clock will likely have to be restarted more than once. Alternatively, install `pg_stat_statements` in item 49's restart window and read the query set directly. **Rationale: `stats_reset` is NULL but that is NOT lifetime — a crash discards stats silently (§0.16.6), and §5's 37.5 B reads vs §0.16.9's 1.06 B proves one already happened. "0 scans" currently means "0 over a window of unmeasured length", and a weekly ops query or backfill script is invisible in it.** Model the drop with `hypopg` first — it is installed and costs nothing | `CREATE INDEX CONCURRENTLY` — **and this is why the precondition exists**: the rebuild is a slow GiST CIC on a 5.09M-row table under a 2 GB cap and live ingest |
| **11a** | **AUDIT `uq_signal_observation_release_cell_signal_time` — 11 GB, one index, on a table in NO other item of this programme.** `agri.signal_observation` is **26 GB (11 GB heap + 15 GB index), 46,068,872 rows, ~70% of the whole database**, and §0.18 covers the `geo` schema only (§0.18.1 scope note). This six-column natural-key unique index is larger than its own heap. Determine: is it redundant against the other indexes on the table, can it be narrowed, and is the uniqueness constraint it enforces still needed by the writer? **Read-only first** — `pg_stat_user_indexes` for the whole table plus `hypopg` to model any drop. **Decide nothing on a single stats window** — the same unbounded-window caveat as item 11 applies, and applies harder to a relation this size | read-only → owner-decision | none. **Independent of everything else in the programme** — it can run in parallel with any tier | n/a while read-only |
| 12 | Bbox cache keys quantized to a z8 envelope (D6) | code-safe → deploy-gated | none | revert one function |
| 13 | Stop the 5-min capability poll re-stamping the date (D9) | code-safe → deploy-gated | none | revert one prop |
| **13a** | **Interim C1 fix — repair the PREDICATE, not the constant.** `query-persister.ts:309` classifies an entry as historical (30-day TTL, `:68`) whenever the selected day < `serverCurrentDate`; since every layer opens on `latestObservedDate`, which is always strictly before today, **every entry for every layer gets the 30-day TTL**. Rekey freshness to the **layer's own `latestObservedDate`**: a day at or after it is live and gets the short TTL; only days genuinely behind the layer's own frontier are historical. **§0.18.8 item 13 rejects retuning `HISTORICAL_TTL_MS` — it does NOT reject fixing the predicate**, which is the actual defect it names | code-safe → deploy-gated | none. **Alternative, if this is judged not worth doing before item 32: record a one-line owner decision that up-to-30-day staleness is ACCEPTED until Tier 4, with the reason.** What is not acceptable is leaving it unstated — the symptom (a warm map showing stale data) is easily misread as the layers still being broken after Tier 1 | revert one predicate |
| 14 | One `storage-budget.ts` arbiter — fixes the SW self-eviction | code-safe → deploy-gated | none | revert; the two private constants return |
| 15 | Persist `getSliderCapabilities` + add its ETag and gzip | code-safe → deploy-gated | none | revert |
| 16 | bbox filter + `Cache-Control` + compression on `/api/fires` | code-safe → deploy-gated | (5) root-caused | revert |

#### 0.19.4 TIER 3 — the extraction. Weeks, one stream at a time, dual-write.

Order: **water-gauges first** (1,417,935 rows over 953 stations, and `readStreamflowGaugesOnDay` already
does `DISTINCT ON (properties->>'siteNo')`, so the target shape is proven), then sensors,
weather-observations, vegetation, **fire-detections last** (largest, trickiest day semantics).

| # | item | class | precondition | reversal cost |
|---|---|---|---|---|
| 17 | `geo.revision_seq`, `geo.station`, `geo.tombstone`, first reading table, created **empty** with monthly partitions +2 months ahead | code-safe → deploy-gated | `schema.ts` + migration + contract re-pin in one commit | `DROP` — nothing reads them |
| 18 | **Backfill script**, `created_at` batches of 50k, out of band, resumable | prod-mutating | (17) | `TRUNCATE` |
| 19 | **Dual-write** in the ingest writer | code-safe → deploy-gated | (18) | feature flag |
| 20 | **Nightly per-station-day count + checksum parity job, as a job-ledger work item that FAILS loudly** | code-safe → deploy-gated | (19). **Do not skip this to save a day** — this codebase has shipped exactly this failure class twice (USDM's 26-of-29 missing weeks; the test gate dark for a whole alembic revision) | n/a |
| 21 | Repoint the reader behind a flag, default off; flip on; watch | code-safe → deploy-gated | (20) green | flag off, instantly |
| 22 | **Delete that layer's rows from `geo.features`, in monthly batches** | prod-mutating | **seven consecutive clean nights of (20)**. Never one statement — ~4.84M deletes on a 2 GB box under live ingest is the shape that pins memory at the ceiling | **first genuinely hard-to-revert step.** Rows survive in the reading table; re-materialisation is one-way in practice |
| 23 | Watch `n_dead_tup` after the first batch; manual `VACUUM` (not FULL) per batch if autovacuum does not keep up | prod-mutating | (22). `last_autovacuum` is NULL on this table and `autovacuum_max_workers` is 3. **`pg_repack` is NOT installed** (§0.16.6) | none |
| 24 | Kill `properties.geometry`: trigger → validation-only, writer supplies `geom`, strip from `insert_features.sql`/`refresh_features.sql`, remove from the `route.ts:133` spread | code-safe → deploy-gated | **re-derive `refresh_features.sql`'s change-detection predicate first** (§0.18.1) — this is real unbudgeted work, not a key deletion | restore the trigger body |
| 25 | Native `external_id` + `observed_day`; drop the two expression indexes for plain btrees | prod-mutating | (24) | re-create the expression indexes |
| 26 | **Rekey `geo.geometry` to entities only** (3,255,832 → ~255k rows) — reuse `scripts/rekey-geometry-to-entity.sql`, which already exists, is idempotent, `REPEATABLE READ` + `LOCK TABLE` disciplined, and pre-measured | prod-mutating | (22) for each stream. **Handle per stream, not afterwards**, or it becomes the largest object in the database | script is one-way; take a snapshot |

#### 0.19.5 TIER 4 — sync and Parquet

| # | item | class | precondition | reversal cost |
|---|---|---|---|---|
| 27 | `revision` columns + `BEFORE UPDATE` trigger + **lagging high-watermark** (§0.18.2) | code-safe → deploy-gated | (17) | drop the column |
| 28 | **Tombstone AFTER DELETE trigger** | code-safe → deploy-gated | **INSTALL ONLY AFTER (22) COMPLETES for every stream**, or gate on a session GUC. Firing it during the extraction emits ~4.84M tombstones telling clients to delete data that merely moved tables | drop the trigger |
| 29 | Nightly revision-drift assertion (`updated_at` moved, `revision` did not) | code-safe → deploy-gated | (27) | n/a |
| 30 | `geo.tombstone` **pruner** at the 180-day floor | code-safe → deploy-gated | (28) | n/a |
| 31 | `GET /api/v1/sync/{stream}` + `GET /api/v1/scope/resolve` | code-safe → deploy-gated | (27). Old endpoints stay | remove the routes |
| 32 | Client: scope record, delta consumer, revision-keyed freshness (retires C1), `cold-artifacts` store, relevance-ordered eviction | code-safe → deploy-gated | (31) | revert; IDB v3 upgrade is additive |
| 33 | **GATE: prove `cf-cache-status: HIT`** on a repeated PMTiles range request via a Cache Rule | prod-mutating (a CF rule) | none. **If this fails, STOP — 34-39 are not worth building** | delete the rule |
| 34 | **GATE: 30-line hyparquet spike** — publish one hand-built Parquet, range-read one row group with a column projection, from a browser, against the bucket's CORS | code-safe | (33). Pre-chosen fallback: a Cloudflare Worker that slices server-side and returns JSON | discard the spike |
| 35 | **GATE: one-command Parquet size check** — `COPY (…) TO 'x.parquet' (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)` then `ls -l`. **Threshold: >60 MB/month kills the cheap per-day range read** | read-only | (17)/(18) for one stream | n/a |
| 36 | Wire `R2_*` credentials as **Railway reference variables** (not copied secrets) on writer + reader; confirm the bucket is CDN-fronted with `curl -I -H Range -H Origin` | prod-mutating | (33)-(35) all green | unset variables |
| 37 | **`export-parquet` job on the existing `agri.job_*` ledger** — one work item per (stream, month), whole-partition export, watermark-gated re-export, atomic publish (immutable `<sha>.json` first, mutable pointer last, 30-day retention of superseded objects) | code-safe → deploy-gated | (36) | stop the job; objects are additive |
| **37a** | **A SECOND `export-parquet` STREAM FOR THE SIGNAL PLANE — this is item 41's real producer, and it did not exist.** Item 37 exports **one work item per (stream, month) for the new `geo` reading tables only** (`geo.streamflow_reading` etc., where the monthly RANGE partition *is* the Parquet file, §0.18.4). The four agent SQL tools of item 41 read a **signal-cell rollup derived from `agri.signal_observation`** — 46M rows, a different schema, a different grain, and **not exported by item 37 under any reading of it**. Either export the signal plane on the same job-ledger pattern, or define the rollup grain the four `sql/agent/*.sql` files still specify (§0.10 — they are its only surviving spec) and export *that*. **The grain decision comes first and is the real work here; the export job is the easy half** | code-safe → deploy-gated | (36). **Note: `agri.signal_observation` is NOT month-partitioned** — it is a plain 11 GB heap, so this export has no partition to lift and must range-scan. Budget it accordingly; it is not item 37's shape | stop the job; objects are additive |
| 38 | **Register `export-parquet` in `BACKFILL_LANES` AND as a `StreamDefinition` with a cadence**; add `expected_refresh_days` to the four `kind="reference"` streams | code-safe → deploy-gated | (37), and (37a) if the signal plane ships. **Without both it has a producer and no gap detection** (§0.18.4). **The `expected_refresh_days` half was pulled forward to Tier 1 item 4c** — it is independent of Parquet and should not wait behind it; leave it here only as the cross-check that it actually landed | revert |
| 39 | `readiness.py` Parquet/bucket freshness section (**fails**, not warns, on `built_from_max_updated_at` drift) + `JobRunnerDashboard.tsx:70`'s `gaps` tab | code-safe → deploy-gated | (37) | revert |
| 40 | Client hyparquet range reader for sealed months | code-safe → deploy-gated | (34) green | feature flag |
| 41 | **Repoint the four agent SQL tools at DuckDB-over-R2, then delete them LAST** | code-safe → deploy-gated | **CHANGED 2026-08-21 — was "(37) serving", which item 37 can never satisfy.** The real precondition is **(37a) serving**: item 37 exports the new `geo` reading tables, and these four tools read a rollup derived from `agri.signal_observation`, which item 37 never touches. **Priority note: this is NOT urgent.** The four tools currently return a typed `pre_aggregated_plane_unbuilt` refusal, not an error (§0.16.4, `tools.py:473`/`:901`) — degraded service, not a live incident. It was mis-ranked against the acute outage while §0.16.4 claimed they crash. They remain the only surviving spec of the dropped rollup's grain (§0.10) | keep the `to_regclass` guard **that already exists** so a DuckDB/R2 failure degrades to today's typed refusal |
| 42 | Wire the two mutation call sites to enqueue into the existing offline queue; 409 precondition on replay | code-safe → deploy-gated | none | revert two call sites |

#### 0.19.6 TIER 5 — matview retirement and the cap

| # | item | class | precondition | reversal cost |
|---|---|---|---|---|
| 43 | **Register `drizzle/0034`** (records the `mv_signal_cell_daily` drop) — **before any other matview work.** `drizzle/0029:533` still creates it; a history replay resurrects a 6,349 MB / 1,729 s view. **Never edit `0029`** | contract re-pin in the same commit. **ALSO, added 2026-08-21: correct the file's header lines 21-22 in that same commit.** They claim the four agent tools "throw a hard database error (relation does not exist)" — **false**; they return a typed refusal (§0.16.4). Keep the "do NOT wrap these four callers in an existence guard" instruction, but state the true reason: **the guard already exists** at `tools.py:473`/`:901`. As written the comment invites a future agent to add a redundant guard or, worse, remove the real one and create the very error it describes | unregister |
| **43a** | **Register `drizzle/0035_soil_survey_union_collection_extract.sql`** — written and dormant since this run, and scheduled in **no tier** until now | deploy-gated | contract re-pin in the same commit (§0.19.0 step 10). **HAZARD, and it is a hard deploy block with a non-obvious cause:** the file's `DO $$` guard does `IF COALESCE(is_populated, false) THEN RAISE EXCEPTION` on `geo.mv_soil_survey_union` (`:94-96`). **If the hourly `matview-refresh` lane ever succeeds on that view between now and the migration applying, the migration RAISEs, `preDeployCommand` fails, and the deploy is blocked by a data state a live job controls — nothing in the commit will explain it.** Mitigation: re-check `relispopulated` **immediately before merging**, or downgrade the assert to `RAISE NOTICE` + skip. **PRIORITY: LOW — `0035` repairs a relation with ZERO readers** (§0.16.4; both `usda-soil.ts` readers are deliberately not repointed). Consider whether it should be **dropped instead** alongside item 46 rather than repaired | unregister |
| 44 | **Rewrite the three census matviews to aggregate before joining `geo.layers`** (§0.16.7 Test C) — SQL-level, no schema change, no new index. **This is the prerequisite that makes partitioning worth anything, and it may make it unnecessary** | code-safe → deploy-gated | none | revert the SQL |
| 45 | Convert the eleven small matviews to incrementally-written tables (`DELETE WHERE day=:day` + `INSERT` on the existing watermark — **not** `ON CONFLICT DO UPDATE`, which cannot retract rows) | code-safe → prod-mutating at cutover | (44) | keep the matviews until the tables are proven |
| 46 | **Delete the `MATVIEW_REFRESH_SPECS` set, the backoff state machine, and the perpetual 74-item/70-failure run** | code-safe → deploy-gated | (43), (45), and (41). **Name and rewrite the live consumers first**: `analytics.ts:43/:77/:120/:139` (`mv_layer_feature_stats`, `mv_layer_hourly_activity` — public dashboard procedures deliberately moved off `COUNT(*)` over 4.97M rows), `environmental-read-model.ts:1515/:4241` (`mv_feature_observation_day`, capability + recency), `usda-soil.ts:1049/:1151` (two soil readers explicitly not yet repointed). **An R2 manifest answers none of those without rewriting them** | re-register the specs |
| 47 | Fix `scripts/apply-pre-aggregation.mjs:133`'s unguarded `REFRESH` — on a fresh or DR database it refreshes eight views then raises 42P01 and exits 1 | code-safe | none | revert |
| 48 | **Turn ON `enable_partitionwise_aggregate` / `_join` and re-measure** | prod-mutating | (44). Per §0.16.7 they change nothing until the matviews are rewritten — **turning them on before (44) proves nothing** | `ALTER SYSTEM RESET` |
| 49 | **Drop TimescaleDB + `timescaledb_toolkit`** — un-hypertable `tracking.positions` first, edit `shared_preload_libraries`, restart; bundle `pg_stat_statements`/`pg_qualstats`/`pg_stat_kcache` into the same restart | prod-mutating, restart window | **AFTER** the §12.2b client fixes and Tier 3, so any relief stays attributable. Measured NOT to be the cause (§12.7) | reinstall; the one hypertable is 0 chunks / 40 kB |
| 50 | **Lower the memory cap from 2 GB toward 1 GB**, re-measure | owner-decision | Tier 3 complete and no statement's working set exceeds 1 GB. Settle the 3.0 GB 7-day max first (§0.18.5) | raise it back |

#### 0.19.7 Owner decisions outstanding

| decision | context |
|---|---|
| **Proceed with the `geo.features` partition swap, or shelve it?** | §0.18.8 item 1 recommends **shelve**. Three lines of evidence say it buys nothing the extraction does not buy more cheaply, and the shelving cost is six landed slices staying dormant. **This is the largest single call in the programme.** **Two corrections to what the owner is deciding against, both 2026-08-21: (i)** ground **(b)** of the recommendation — "tile latency is not a justification" — was measured **only at z10, a zoom where tiles already work**; the z5/z6 plans, where they hang, were never captured (§0.16.7). The shelve still stands on grounds (a) and (c), but (b) is not evidence as stated, and the read-only `EXPLAIN` that would settle it is named in §0.16.7. **(ii)** Shelving makes **`drizzle/0036` permanently dead** — it asserts `relkind='p'` and can only ever be registered on a partitioned table. Shelving does not merely park it; it retires it. |
| Register `drizzle/0033` (six tile functions at once) | §0.13 item 1. Still an owner call; one bad function 404s the whole composite. Its constant-`layer_id` pruning is moot if partitioning is shelved — but the rewrite may still be worth registering **one function at a time** on its own merits. |
| `tile_interventions` day column (§10 decision c) | Default: no. Unanswered since §12.5. |
| Commit the pre-existing pulse batch (~456 lines in `jobs_pulse_command.py`) | Deliberately not swept into `de3139e`. |
| FIRMS `INGEST_MAX_SOURCE_RECORDS` cap | 2,239 records currently drop silently past the 10,000 ceiling ([[agri-firms-record-cap-drops-silently]]). |
| ML Phase B | Hard-blocked until drought covariates exist — a missing covariate index 36-38 makes `build_design_row` return `None` for every row. |
| Does the app role hold `CREATE` on schema `geo`? | read-only; only needed if synchronous partition creation is chosen over the DEFAULT partition (§0.7). Moot if the swap is shelved. |

#### 0.19.8 Carried forward from §0.13 and §7, unchanged in priority

- **Clear the 15 standing dead letters** keeping the hourly pulse red (prod-mutating, operator action). The
  three still-broken matviews will keep re-redding it via `deferred_failing`; that is intended.
- **Re-scope `scripts/backfill-geometry.sql:31,199-209` and `scripts/rekey-geometry-to-entity.sql:37,152-158`**
  off whole-table `LOCK TABLE geo.features IN SHARE MODE` — post-partition that takes 13 locks, not 1.
  Code-safe. **Moot if the swap is shelved.**
- **Prune `usda-soil.ts` `persistCell`'s three write-path joins to `geo.layers` by name (~`:884-926`)**.
  Code-safe, found in slice B, in no slice's scope.
- **§10's join-free tile relations** (11 `geo.tile_*` relations, zero jsonb, zero join, pre-projected to
  3857) — proposal only, no `tile_*` relation exists. **Re-evaluate against §0.18**: Tier 2 items 8-9 may
  deliver most of the benefit for a fraction of the work, and Tier 3 changes what a tile relation would
  even read from.
- **Re-verify the 2 GB cap and `autovacuum_max_workers=3` survive a Railway-initiated restart**, not just a
  manual check. Read-only. `autovacuum_max_workers` was re-confirmed `source=configuration file` on
  2026-08-20; survival across a *Railway-initiated* restart is still unverified.
- **The 12-item layer-lane conformance backlog** (§8, 2026-08-15), highest blast radius first: 454 queued
  `firms-archive` work items since 2026-08-08 · the signal plane on no schedule · `coverage_fill`'s
  non-durable `Path.exists()` idempotence key on an ephemeral cron container · the agent drought tool
  reading the 0-row `drought_polygon_snapshot` while the map serves `geo.drought_areas`.
- **Deferred review findings**: a disposable-DB contract test for `to_regclass` semantics · repoint
  `routes/ops.py` at `jobs/lease.py::failure_condition_name` · an `EXPLAIN` that actually demonstrates the
  index-causation claim · the structurally-unreachable `MAX_OBSERVED_DAY_ROWS` cap.
- **USDM and ERA5-Land as self-healing lanes**; the persistence audit; the 8 zero-landing ERA5-Land
  releases (§0.16.2).
- **A real foregrounded-browser visual verification pass** of everything `de3139e` shipped. Still only
  code-traced plus one automation-tab attempt that produced a false negative. Now folded into §0.19.2's
  hard gate.
- **`drizzle/0035` needs its outcome watched**, not just its DDL applied (§0.19.1). **Now scheduled as
  §0.19.6 item 43a**, with the `relispopulated` deploy-block hazard stated there.
- **`geo.mv_soil_survey_grid`** is in standing failure at the 300 s boundary. **CORRECTED 2026-08-21: this
  bullet previously read "unlike `union`, has a reader". That is FALSE — NEITHER soil matview has a
  reader.** A grep across `src/` and the agri service finds only the two deliberate non-repoint comments,
  `usda-soil.ts:1049` (`_union`) and `:1151` (`_grid`), each recording a real grain mismatch as the reason
  the reader still runs its own `GROUP BY` (§0.16.4). So the one prioritisation statement about these two
  views was **inverted**: `_grid` is not more urgent than `_union`; both have zero consumers, and
  `drizzle/0035` repairs an unread relation. **Re-ranked: the honest question is not which to fix first but
  whether either should be repaired at all, or dropped alongside §0.19.6 item 46's matview retirement.**
  They are exactly the "matviews serving nothing" pattern §0.18.8 item 4 rejects. Still unowned.
- **Close the `data_available_at` question on the three unmeasured layers** — fire-detections (3,022,196),
  water-gauges (1,417,935) and sensors (184,733), **90.8% of the table**, where the existence probe timed
  out (§0.16.3). Eight layers are confirmed 100% NULL; these three are **UNMEASURED**, and they are the
  high-volume ingestion lanes most likely to actually set the column. Until they are read, "backtests
  filtering on `data_available_at` are a no-op" is a claim about 9.2% of the rows, and `drizzle/0025`'s ML
  leakage boundary may not be as dead as recorded. Read-only; needs either a maintenance window without a
  statement timeout, or a partial index.
- **Settle whether this database is OOM-restarting** (§0.16.6). Two reads caught it at ~2 h 20 m and at
  **0.56 s** of uptime, `stats_reset` is NULL, and §0.18.5 measured a 7-day memory **max of 3.0 GB against
  a 2 GB cap**. One command: `railway logs -s plantgeo-spatiotemporal-db | grep -i "out of memory\|database
  system was not properly shut down"`. This gates item 11's ≥7-day-uptime precondition, and it is the
  cheapest available check on whether the cap is actually holding.

#### 0.19.9 Completeness review of §0.16-§0.19, 2026-08-21 — what was corrected, and what was rejected

A completeness critic read §0.16-§0.19 in full against the live source tree and raised fifteen findings.
**Thirteen were correct and are fixed in place above.** Two were correct in their conclusion but wrong in
their reasoning, and one arithmetic correction was itself miscalculated — those are recorded here rather
than silently absorbed, because this file's convention is that a recorded rejection stops work being
re-litigated. **Two new production measurements were taken to close findings rather than guess at them**
(both read-only): the stats-window probe in §0.16.6 and the `data_available_at` existence probe in §0.16.3.

**Corrected in place** (section → what changed): §0/header → the "§0.10's count is wrong" pointer named the
wrong section, and the tile-plan claim was unscoped · §0.10 → the stale "record it in a new `drizzle/0033`"
line, superseded by the written `0034` · §0.13 → item 3 downgraded from RESOLVED, plus the `0034` header
caveat · §0.16.3 → `data_available_at` rescoped from "across the board" to the 9.2% actually measured, and
`weather-observations` disambiguated from the stalled climate signals · §0.16.4 → **the "four agent tools
throw a hard database error" verdict, which was flatly false**, plus the soil-matview reader claim and the
74/2/70 arithmetic · §0.16.6 → the unbounded stats window, a second independent index sample, "Droppable
today" downgraded, and the 1,369,300 division error · §0.16.7 → the z10 scope caveat · §0.16.8 → the
compressed tile path added to the blind-spot list · §0.17.1 → provenance: the symptom was never observed ·
§0.18.1 → the missing `geo.drought_areas` row, two total-arithmetic errors, the `agri` scope statement, and
the "plausibly fits" conclusion re-derived against 1,951 MB · §0.18.5 → sums shown, "−70%" replaced by the
honest range · §0.18.7/§0.18.8 → the inherited premises flagged · §0.19 → eight new numbered items and
four changed preconditions.

**Rejected or amended, with reasoning:**

1. **REJECTED IN MECHANISM, ACCEPTED IN CONCLUSION: "every index number comes from a ~2 h 20 m
   post-restart stats window."** The conclusion — that these are not lifetime figures and cannot support
   "never been read" — is **correct and is now acted on**. The stated mechanism is **wrong**: PostgreSQL
   persists cumulative stats at clean shutdown and reloads them, so counters **survive** restarts and
   uptime is not a bound on the window. Measured directly on 2026-08-21: a read taken **20 s after a
   restart** showed every counter *higher* than the 2026-08-20 pass. **The window is therefore longer than
   uptime and shorter than lifetime, and nothing measured pins it** — which is a weaker claim than the
   critic's but justifies the same precondition on §0.19.3 item 11. **Do not re-derive a throughput rate
   from any `pg_stat_*` counter in this file**; the 657k-tuples/sec inconsistency the critic raised
   dissolves once the premise is dropped, and is recorded as resolved-by-refutation in §0.16.6 reading 3.
2. **AMENDED: the corrected division was also wrong.** The critic gave 5,525,831,267 / 4,096 =
   **1,348,884.6**. The correct quotient is **1,349,080** (the runbook's original 1,369,300 was wrong too).
   Both the original and the correction are recorded in §0.16.6 reading 3. The critic's substantive point —
   that the "happens to equal the water-gauges row count" inference is false — **stands**: water-gauges is
   1,417,935, which matches neither figure.
3. **AMENDED: the `data_available_at` scope was better than the critic assumed, and is now better still.**
   The critic scoped the confirmed set at six layers / 3.8%. Two of the five "timed out" layers
   (`weather-observations`, `soil-survey`) were re-probed on 2026-08-21 with an existence probe and
   **completed**, confirming no non-NULL row. The confirmed set is **eight layers / 467,040 rows / 9.2%**;
   the unmeasured remainder is **three layers / 4,624,864 rows / 90.8%**. The critic's core point — that a
   claim covering the unmeasured 90% was stated without a hedge — **stands and is fixed.**
4. **AMENDED: §0.18.5's low bound was also mis-stated, not just underived.** The critic computed
   2,287,497 B and compared it to the published "≈2.5-6.4 MB". 2,287,497 B is **≈2.3 MB**, so the low bound
   was wrong in the second digit as well as underived; the range is now published as **≈2.3-6.6 MB** with
   both sums shown.

**Not fixed here, and deliberately so: `drizzle/0034`'s header lines 21-22.** The critic is right that the
false "throws a hard database error" claim propagated into that shipped artifact and will mislead whoever
registers it. **That file is outside this document's write scope**, so the correction is recorded as a
required precondition on §0.19.6 item 43 instead of being made silently. Anyone registering `0034` must
fix those two lines in the same commit.

---

### 0.20 Independent codex-led outage diagnosis, 2026-08-21 — corroboration and four things §0.17 does not have

**Provenance.** A second workflow ran concurrently with the one that produced §0.16-§0.19, deliberately
isolated: read-only, forbidden from touching `conductor/RUNBOOK.md` or any file the other run owned.
Four lanes, each driving the **codex CLI v0.145.0** (`codex exec -s read-only`) over a distinct hypothesis
— client, Martin, empty-data, and one deliberately given no hypothesis at all — then every critical/high
claim cross-verified by a Claude agent briefed to refute it. 12 agents, 1.29 M tokens, 76 minutes.
Full document: `scratchpad/codex-outage-diagnosis.md` (615 lines).

**Why this section exists rather than being merged into §0.17.** The two investigations reached the same
mechanism independently, which is the strongest evidence in this runbook for anything about the outage.
But they measured different magnitudes, and §0.17 contains none of the four items in §0.20.3. Keeping them
separate preserves the independence; collapsing them would manufacture a false single narrative.

#### 0.20.1 Where the two runs AGREE — treat these as the highest-confidence findings in §0.16-§0.20

- **The owner's bandwidth cap cannot touch a map tile.** `src/lib/net/request-budget.ts` caps 4 concurrent
  / 5 per second, but a full-tree grep for `transformRequest|RequestParameters|setTransformRequest` returns
  **nothing**, and the only consumers of `createBudgetedFetch|runBudgeted|acquireRequestSlot` are
  `useFireData.ts`, `useOfflineSync.ts`, `lib/offline/tile-cache.ts` and `lib/trpc/client.ts`. MapLibre's
  own tile loader is not among them. Found independently by both runs and by both model families.
  **The owner's stated theory of his own bug is wrong, and that is good news** — the cost control he set is
  not what broke the map, so the fix does not require giving up the cost control.
- **An unbounded statement against an 8-connection pool is the self-sustaining mechanism.** `pool_size: 8`
  (`infra/martin/martin.yaml:39`) with no `statement_timeout` (§5). Both runs named it from the files
  before either measured it.
- **`max_feature_count: 10000` is dead configuration** for all six function-backed sources: they
  `RETURN bytea`, which Martin cannot inspect or truncate. Codex-sourced, corroborated by measured
  multi-MB `sensor_tiles` payloads in both runs.
- **The ML Strategy Recommendations layer can never render**, via two independent defects at once: absent
  from `martin.yaml` and from the live `/catalog`, **and** `StrategyLayer.tsx:36,64` requests source-layer
  `"strategy_recommendations"` while `drizzle/0028:342` emits `'strategy-recommendations'`. Both models
  found this before either saw the other's output. Scope it honestly: **one toggle, not "most layers."**
- **Not causes**, checked and cleared by both: authentication, CSP, SSR/hydration, deck.gl interleaving.

#### 0.20.2 Where they DISAGREE — and why the disagreement is the finding

Cold-tile latency has no single value. §0.17.2 measured z5/5/11 at status 000 after **25 s** and z5/4/10 at
284 B after **102.10 s**; the codex run measured the z5 composite at **300.17 s and 300.18 s** on two
consecutive runs against a 400 s cap; individual Claude lanes inside the codex run reported 14.6 s, 19.4 s,
81 s, 147.3 s, 193 s and >260 s, and one lane's figure was formally refuted by another that could not
reproduce it.

**Adjudication: nobody was wrong, and the variance *is* the result.** With a 5-minute tile cache and a pool
that saturates without draining, the same URL legitimately costs 0.58 s or infinity depending on the minute
it is requested. **Any single-sample latency figure for these endpoints — including the ones in §0.17.2 —
should be read as one draw from a bimodal distribution, not as "the" cost.** Two consequences: no fix should
be validated by a single timing, and the twin 300.17/300.18 s figures are the more interesting datum
precisely because they are *not* variable — two runs terminating 10 ms apart against a 400 s client cap
indicates a **hard 300 s cut in front of Martin** (Railway's edge proxy), not a slow query. A request killed
at the edge never completes, so Martin never caches it, **so retrying never warms it**.

Worth recording as method: codex refused to state any current latency at all, because its sandbox had no
outbound HTTPS. That refusal was **more correct** than the Claude lanes' over-generalisation from single
samples. Where a tool cannot measure, "I cannot measure this" outranks a confident number.

#### 0.20.3 Four things §0.17 does not contain

1. **The `/catalog` control that proves pool starvation rather than inferring it.** In one measured window:
   `watershed_tiles/6/11/23` returned **000 after 60.01 s** having served in **0.30 s** minutes earlier;
   the composite `/6/11/23` returned **000 after 60.03 s** having served in **0.91 s**;
   `intervention_tiles/5/5/11` hung **150 s** despite returning **204 No Content in 0.22 s** at z6 — while
   `GET /catalog`, the one endpoint touching no database, held steady at **0.30 s**. A layer that returns
   *no rows* cannot hang on query cost. Martin's HTTP server is healthy; its Postgres pool is empty and does
   not refill. This is the difference between a diagnosis and a demonstration.

2. **`building_tiles` is empty at every zoom on Earth — undocumented anywhere before now.** `204 No Content`
   at z10/181/373, z6/11/23, z4/2/5, z2/1/1 **and z0/0/0, the whole-world tile**, while five sibling layers
   returned real bytes in the same sweep. A z0 world tile returning 204 admits no bbox explanation. Same for
   `intervention_tiles`, which corroborates the standing note that interventions has no producer — but
   **`building_tiles` appears in no memory note and no prior runbook section.** It is structurally the odd
   one out too: no `geo.layers` join, no `status` predicate, just `b.is_public IS TRUE` plus the bbox test.
   Next probe: row-count `geo.osm_buildings`. Zero rows means the OSM import never committed; rows but none
   passing `b.is_public` means that flag is mis-set at import. Either way it is a data-completion gap, not a
   query bug. **Caveat: this came from the lane that died (§0.20.4) and was never cross-verified.**

3. **The `watersheds` time-slider row filters nothing, and the self-check built to catch that structurally
   cannot.** `watershed_tiles` emits `observed_day` exactly like its four siblings, but both client
   allowlists — `DATE_FILTERABLE_TILE_LAYER_TOGGLE_IDS` in `tile-layer-date-filter.ts` and
   `DATE_FILTERABLE_TOGGLES_WITH_A_DAY_HERE` in `LayerManager.tsx` — contain exactly
   `[fire-perimeters, evacuation-zones, burn-severity, sensors]`. This is **over**-inclusion (boundaries
   drawn at every date), not empty-render, so it does **not** explain the acute symptom. The better finding
   is the meta one: `reportDateFilterableToggleDrift()` exists to catch precisely this drift and compares
   **the two client lists against each other**, never against which SQL functions actually emit the
   attribute — a self-check that can only detect disagreement between two copies of the same mistake. Any
   fix should re-point it at the server's emitted attributes.

4. **Two things already fixed — do not "fix" them again.** The shared default-landing-date defect was
   repaired 2026-08-09: `resolveLayerDate` defaults each layer to its **own** `latestObservedDate`, so a
   layer opens on its own newest published day rather than a global date. And `UNINITIALIZED_DATE` cannot
   reach the style filter — `isCalendarDate()` coerces it to `null` in `useDebouncedLayerDay`, and
   `hasSelectableDay` gates it again; even if it leaked, any ISO date sorts lexicographically before
   `"uninitialized"`, so the filter would show everything rather than hide everything.

**A methodology rule earned the hard way, and it generalises past this outage.** `evacuation_zone_tiles`
and `burn_severity_tiles` return **0 bytes at Boise z10/181/373** but **86,992 B and 22,070 B at z6/11/23**,
and 149,235 / 97,257 B at z4/2/5. They are healthy; the Boise tile simply does not intersect the events.
Event-based layers are geographically sparse and one city is not guaranteed to intersect them. **Probe
`z1/0/0` first** — a cheap "does this table have rows anywhere on Earth" check — before concluding anything
from a narrow high-zoom tile. A zero-byte 200 and a broken layer are indistinguishable without that step.

#### 0.20.4 What this run got wrong, recorded because the failure mode will recur

The `codex:data` lane **died** — it leaked literal tool-call markup into its JSON string fields and blew the
StructuredOutput retry cap (5). `parallel()` resolved it to `null`, the barrier cleared with three lanes,
and **the synthesis in `codex-outage-diagnosis.md` §§0-6 was written without ever seeing it.** Everything in
§0.20.3 items 2-4 above was recovered by hand from the dead agent's transcript afterwards and is
**single-source: it never passed through the cross-verification phase.** Weight it below §0.20.1
accordingly. One thing raises confidence: its live probes ran in the healthy window, and other layers
returned real payloads in the same sweep, so "204 everywhere" was measured against working siblings rather
than against an already-starved server.

A second, unrelated agent died in the §0.16-§0.19 run (`verify:evidence` on the heap-hit-ratio finding,
lost to `API Error: Connection lost mid-response`). That finding survived on one adversarial lens instead of
two — and was **independently refuted anyway** in §0.16.9, which is the system working.

**The operational lesson, which is the durable part:** a killed workflow agent writes **no journal entry at
all**, so "started with no result" is indistinguishable from "still running" — a dead lane read as healthy
for 71 minutes. The only available liveness signal is transcript mtime. Any future monitor over these runs
must treat a quiet transcript with no result as *probably dead*, not as *working*; both failures here were
caught only after adding that check.

#### 0.20.5 The fix order this run recommends, and one ordering trap

Ranked by user-visible rendering unblocked per unit of work — and note this is a **different first move**
than §0.17's tiering, because it optimises for "most map appears soonest" rather than for root-cause depth.

1. **Split the single dynamic composite into six independent MapLibre sources**
   (`src/lib/map/sources.ts:33-40`, plus the `source:` field on each dynamic layer in `layers.ts`).
   Measured individually in one window: `watershed_tiles` 0.30 s, `intervention_tiles` 0.22 s,
   `evacuation_zone_tiles` 0.49 s, `burn_severity_tiles` 4.55 s, `sensor_tiles` 15.32 s — and
   `fire_risk_tiles` did not answer within 120 s. **All six layers ride one MapLibre source, so today the
   five that work wait on the one that does not.** Splitting also permanently retires the "one missing
   source 404s the whole TileJSON" failure class that `sources.ts:29-32` warns about in its own comment.
   Risk: low-medium; six TileJSON fetches instead of one. `sources.ts:20-24` warns never to mix function
   and table sources in one composite — splitting moves away from that hazard, never toward it. No Martin
   redeploy.

2. **ORDERING TRAP — do not take the smallest diff first.** Adding `statement_timeout` to Martin's
   connection is a one-line change and it is the wrong first move. Applied before the z5 query cost is
   understood, **it converts today's slow-but-sometimes-successful tiles into guaranteed 5xx**, turning an
   intermittent outage into a deterministic one. Correct order: split → `EXPLAIN` the z5/z6 cost (§0.16.7
   names this as the read-only measurement still missing, since its own EXPLAIN was taken at z10 where
   tiles already work) → **then** `statement_timeout` → then cache expiry and a source `minzoom` floor.

This ordering trap and §0.16.7's scope correction are the same gap seen from two directions: **nobody has
yet captured a plan at the zooms that actually hang.** Until someone does, every proposed fix to the tile
path is reasoning about a query it has not looked at.

---

### 0.21 THE MAP IS FIXED — shipped and verified 2026-08-21. Supersedes §0.17's ranking and §0.16.7's cost figures.

Owner confirmation, unprompted: *"this is immediately much better almost all layers render with caching
working perfectly."* Three changes did it. Everything else this programme produced was scaffolding.

#### 0.21.1 What shipped, and the measurement for each

| change | where | evidence |
|---|---|---|
| **Composite split** — one MapLibre source per Martin function, replacing the single six-member composite | `src/lib/map/sources.ts`, `layers.ts`, `styles.ts` (`c1c0428`) | Five layers no longer wait on `fire_risk_tiles`, which did not answer in 120 s |
| **Service worker cache-first for Martin tiles**, with consumer-driven refresh | `public/sw.js`, `src/lib/offline/tile-cache.ts`, `LayerRow.tsx` (`2b38c66`) | Cached tiles paint with **zero** network |
| **`sensor_tiles` `DISTINCT ON`** | `drizzle/0038`, **applied to production 2026-08-21** | **14,258,826 → 745,755 bytes, 19.1×**; z6/11/22 3,920,849 → 222,682 B, 17.6× |

`sensor_tiles` sizes were read **directly from Postgres** (`octet_length(geo.sensor_tiles(z,x,y))`), not through
Martin, so its 5-minute `tile_expiry` cannot flatter them. All five function signatures verified unchanged
after apply: `(z,x,y) -> bytea`, `STABLE`, `PARALLEL SAFE`, one overload each. Rollback text for all five
captured to `scratchpad/rollback-0038.sql` (6,763 B) **before** applying.

#### 0.21.2 CORRECTION — `EXPLAIN` cost is meaningless for these tile functions

**This invalidates every cost-based conclusion earlier in this runbook, including §0.16.7's "186,000×
explosion" and the tile-position mechanism in §0.17.**

Plain `EXPLAIN` prices all five feature-backed tile functions **identically — 691,125.21 at z6/11/22, to the
cent.** The layer is selected by NAME through a join, so `f.layer_id` arrives as a join variable and the
planner estimates the whole envelope across all eleven layers. It hands the same 24,370-row estimate to
`intervention_tiles` (**0 published rows**, 0.22 s) and to `sensor_tiles` (**186,904 rows**, no answer).

Published rows, whole layer, measured 2026-08-21: **sensors 186,904 · evacuation-zones 651 ·
burn-severity 541 · fire-perimeters 177 · interventions 0.** A `LIMIT` cannot bound a 177-row layer, so
`0038`'s ceiling is a deliberate no-op on four of the five and the file says so.

**Do not price these functions with `EXPLAIN` again.** Measure `octet_length()` of the actual output and
wall-clock the call. Chasing the cost numbers is most of what produced this session's analysis loop.

#### 0.21.3 The two real mechanisms, now separated

- **`sensor_tiles` was overdraw, not extent.** 625 stations each publish 13–30 same-day readings **at one
  identical coordinate**, so the tile carried 186,904 features. `DISTINCT ON (sensor_id, geom,
  observation_day)` keeps all 23 observation days and drops the duplicates.
- **`fire_risk_tiles` / `burn_severity_tiles` are cold TOAST reads** — 11.7 MB and 37.5 MB of geometry
  against 256 MB `shared_buffers`. Measured **10.9 s / 28.4 s cold, 0.23 s / 0.42 s warm — 40–68×.**
  **There is no SQL fix.** The client cache is the mitigation, which is precisely why cache-first beat
  stale-while-revalidate: automatic revalidation would silently re-pay that 28 s.

#### 0.21.4 Why the tile cache never revalidates itself

Dynamic Martin tiles are **cache-first, and a hit fires no background fetch.** That is the design, not an
omission: revalidating on every hit paints instantly but leaves origin load exactly where it was, which is
the cost the cache exists to remove.

What makes it safe: **a tile's bytes do not vary with the selected date.** No tile function has a date
predicate — day filtering is a client-side MapLibre style filter over the `observed_day` MVT attribute — so
moving the time slider needs **no refetch at all**, and a cached tile already carries every day it will be
asked for. Only an ingest run landing new features changes what a tile should contain.

So refresh is consumer-driven: a **Refresh** button per Martin-backed layer row, and
`refreshDynamicTiles(sourceId)` under it. Dropping the service-worker entry alone is **not enough** —
MapLibre keeps its own in-memory copy of every tile it has drawn, so the button also re-sets the source's
tile template, which is the public API for "reload this source now". Without that it looks broken while
working.

Deliberately **not** folded into "Clear saved days" beside it: that control is destructive, is confirmed as
such, and its copy promises downloaded tiles are left alone. Two intents, two buttons.

**PMTiles stays excluded and must** — Range requests answered with 206, and `cache.put` throws on a partial
response. `/catalog` and `/health` stay uncached. Nine matcher cases were tested before shipping.

#### 0.21.5 Owner decisions, 2026-08-21

- **Next session starts the Parquet path**, not further map polish. The map is good enough to build on.
- **Keep the `sensors` layer for now.** It was the worst offender and is now one of the cheapest, so the
  cost argument for removing it has largely evaporated. Revisit as a product question, not a performance one.
- **Return to a greenfield state if possible.** Owner: *"we may not really want to invest much more in
  something we are migrating to parquet — register now is still viable if needed."* See §0.21.6.
- **Static layers stop revalidating:** soil-survey (SSURGO) and watersheds, **plus historical days for every
  layer** — any day older than a layer's newest published day is settled.
- **Backfills that correct a completed record become a manual admin action.** This is the answer to the
  hazard that blocked the historical-days decision: a retracting backfill can no longer rely on automatic
  revalidation to reach a client, so it must be triggered deliberately.

#### 0.21.6 HAZARD — seven migrations applied to production but unregistered

`drizzle/0030` – `0038` are hand-applied or dormant and **none is in `drizzle/meta/_journal.json`**. A rebuild
from migration history would silently restore the **old 14 MB `sensor_tiles` body** and resurrect
`geo.mv_signal_cell_daily` (6,349 MB, `drizzle/0029:533`), because no DROP is recorded there either.

The owner's greenfield preference makes reconciling this lineage possibly not worth doing — but **the
divergence must not be silent**, which is exactly how the `mv_signal_cell_daily` drop nearly got undone.
Registering remains viable: add the entry **and** bump `src/lib/server/db/migration-contract.ts` in the same
commit, because the readiness test pins the last journal entry's tag and sha256. Note the ordering risk:
the migrator runs **every** registered file, and `0033`–`0036` sit ahead of `0038`.

#### 0.21.7 Still open after this run

- **tRPC-backed layers do not have the tile cache's discipline.** soil-survey, watersheds, water, vegetation,
  drought, and the soil/climate fields go through the IndexedDB persister, which returns a hit in 0 ms **but
  background-revalidates on every access**, and only engages when the query carries a bbox or date
  (`isPersistableQueryKey`). §0.21.5's decision is the fix and it is not built.
- **`interventions` has 0 published rows** — renders nothing because there is nothing to render.
- **`building_tiles` returns 204 at every zoom including z0/0/0** (§0.20.3). Untouched.
- **Strategy Recommendations still cannot render**: `geo.strategy_recommendations_tiles` is absent from
  `infra/martin/martin.yaml` and `auto_publish` is false, so Martin 404s it. The client name mismatch was
  fixed; the registration was deliberately **not** added, because declaring a source Martin 404s would leave
  it permanently unresolved and re-create the all-layer stall the split just removed.

#### 0.21.8 Process — what this session actually cost

Roughly **10M tokens of agent analysis produced three shippable changes.** Each research wave corrected the
previous one — partitioning was the fix until it delivered nothing; the bandwidth cap was the suspect until
it provably could not touch a tile; cost exploded 186,000× until that proved a planner artifact. The
corrections were individually right and collectively a loop, because each fresh agent re-derived from
scratch and found the prior claim over-generalised.

**Owner directive: stop running workflows on this project; work in small bite-sized steps.** The three fixes
that mattered were each a single well-scoped edit. Honour this — it is a working instruction, not a mood.

---

### 0.22 THE SIGNAL-PLANE ROLLUP GRAIN — decided 2026-08-22. This is the Parquet path's first real decision.

Continuation step 3 from §0.21's handoff. §0.19.6 item 37a called this "the real work; the export job is the
easy half" and named the four `sql/agent/*.sql` files as its only surviving spec. They are, and they agree.

**THE GRAIN IS RIGHT AND DOES NOT CHANGE. THE PAYLOAD IS WRONG.**

#### 0.22.1 The grain, confirmed from four independent statements

`(support_key, signal_name, normalized_unit, cell_id, observed_day)` — spelled out identically in
`signal_value_on_day.sql:22`, `signal_neighbors_in_time.sql:21`, `signals_near_point.sql:18` and
`nearest_signal_cells.sql:20`, and matching `drizzle/0029:533`'s `GROUP BY` and its `uq_mv_signal_cell_daily`.
Between them the four reference exactly **13 rollup columns** and no more. Carry it forward unchanged: it is
also the grain `geo.soil_field_observation` and `geo.climate_field_observation` serve, which is the property
that stops the agent and the map disagreeing about the same day.

#### 0.22.2 What was measured, and how

Prod, read-only, `statement_timeout` set on every session. **60 cells across 4 independent blocks
(`ORDER BY id LIMIT n OFFSET {0,400,900,1700}`) — 701,257 grain rows over 1,296,794 raw rows.**

| fact | value | how |
|---|---|---|
| `agri.signal_observation` | 46,068,872 rows · 11 GB heap · **15 GB indexes** | `pg_class.reltuples`, `pg_table_size` |
| grain cardinality | support_key 3 · signal_name 19 · unit 8 · cell 1,867 · day 1,560 | `pg_stats.n_distinct` |
| extent | 2022-04-30 → 2026-08-06 | measured on the probe blocks |
| collapse at grain | **1.81× – 1.88×**, four blocks agreeing | `count(*)` vs `count(DISTINCT grain)` |
| implied full-plane rows | **~21.8M** — an ESTIMATE from a 3.2% cell sample, and an OVER-estimate | extrapolation |
| groups where `min_value <> max_value` | **0 of 701,257** | `count(*) FILTER` |
| group sizes | n=1 16% · n=2 81% · n=3 3% | `GROUP BY n` |

The ~21.8M figure is deliberately labelled an over-estimate: the probes applied the quality filters but **not**
the 19-triple governed join, so they count rows the rollup excludes. `surface_shortwave_radiation` alone
appears under two support keys (`surface` and `era5-0.25deg`) where the contract admits one.

#### 0.22.3 The finding — the rollup never aggregated measurements, it deduplicated releases

**The rollup was designed for sub-daily sampling this data does not have.** Every governed lane delivers
exactly one measurement per cell-day. The 1.85× collapse is **overlapping archive releases republishing the
identical number** — which is why `min_value = max_value` on 100% of 701,257 measured rows.

Consequences, each measured rather than reasoned:

- **`min_value`, `max_value` and `avg_value` are the same number as `normalized_value` on every row.** In
  Parquet this is the *expensive* redundancy: compression works within a column, not across them, so these are
  three additional varying `float64` columns — 3 of the ~4 that dominate the file.
- **`coverage_fraction` is the constant `1.0`; `allowed_client_exposure` has one distinct value.** This is the
  *cheap* redundancy — RLE flattens a constant to nothing. **Keep both**: they cost ~0 and they preserve the
  contract if upstream ever varies.
- **`is_observed AND quality_flag = 'accepted'` removes ZERO rows.** The governed quality gate is currently
  inert. Keep it as a guard; do not mistake it for evidence that rejected rows are being filtered.
- **`observation_count` counts archive releases, not measurements.** This is the defect with teeth:
  `signal_value_on_day.sql`'s neighbourhood mean is
  `sum(avg_value * observation_count) / nullif(sum(observation_count), 0)`, which weights each cell by **how
  many times an archive republished it**. The header at `:104` justifies the weight as "a cell built from one
  reading vs one built from twenty-four" — a population that does not exist here.

**The one anomaly, resolved.** 700 rows at offset 900 had `avg_value <> min_value`. They sit **entirely** in
the `n=3` groups (700 of 4,104) and are float64 rounding: `v+v+v` is not exactly `3v`, so `avg()` drifts in the
last bits while `min` and `max` stay exact. It confirms the finding — all three rows carry the identical value.

#### 0.22.4 Owner decision, 2026-08-22 — export 10 columns, and fix the weight

- **Drop `min_value`, `max_value`, `avg_value`.** The four agent statements' `spread` CTEs re-aggregate from
  `normalized_value` instead: `min(normalized_value)`, `max(normalized_value)`. Provably identical output,
  because the columns are provably identical values.
- **Fix the weighted mean.** The cell-weighted mean becomes an unweighted mean across contributing cells,
  removing the republication-count weighting. This is a correctness fix, not an optimisation.
- **Keep `coverage_fraction` and `allowed_client_exposure`** — free under RLE, and contract-preserving.
- **Keep `observation_count`, and re-document it** as a source-release count. It is the dedup audit trail, not
  a measurement count, and no reader should weight by it.

Net: **13 columns → 10**, and the 3 removed are the 3 most expensive in the file.

#### 0.22.5 Traps for whoever builds the export

- **`agri.signal_observation` is a plain 11 GB heap with no index leading on `observed_at`.** `min(observed_at)`
  alone does not complete in 90 s. A month-scoped export across all cells is therefore a **full heap scan**, not
  a range read. Batch by `cell_id` to ride `ix_signal_observation_cell_time_signal` (2,915 MB, 87,609 scans),
  then sort to the grain in memory before writing — that is what produces the clustering the compression needs.
- **The 11 GB `uq_signal_observation_release_cell_signal_time` index reports `idx_scan = 0`** but is a UNIQUE
  constraint enforced on insert. Read it against §0-note 4: these counters are measured over an unbounded-below
  window. Do **not** read `idx_scan = 0` as "droppable".
- **Dispersed cell sampling flips the planner to a seq scan** and times out where a contiguous `OFFSET` block
  of the same size succeeds. Probe with contiguous blocks at several offsets, not with a spread sample.
- **`relative_humidity`'s unit is `%`.** Inlining the governed 19-triple `VALUES` list into a psycopg2 statement
  that also takes parameters breaks interpolation. Pass the triples as `unnest(%s::text[], ...)` arrays instead.

#### 0.22.6 MEASURED — one month of real Parquet output, 2026-08-22

Continuation step 4. **July 2026, all 1,965 cells, the governed 19-triple join applied, exported at the decided
10-column schema, sorted to the grain before writing.** Files at `C:/tmp/pq/` (scratch, not committed).

| measurement | value |
|---|---|
| grain rows, July 2026 | **487,630** |
| duplicate grain keys in the output | **0** — the grain is a true key, verified by counting |
| **zstd** | **695,338 B — 0.7 MB — `1.43 B/row`** |
| snappy | 874,945 B |
| **13-column variant** (min/max/avg kept) | **2,647,775 B — 3.81× larger** |
| distinct cells / days / signals | 1,867 · 31 · **18 of 19** |

**The decision paid 3.81×, not the ~3× estimated.** Per-column compressed bytes make the reason plain:

```
normalized_value          650,609 B   93.9%
cell_id                    40,007 B    5.8%
newest_observed_at            734 B    0.1%
observed_day                  575 B    0.1%
signal_name / unit / support  645 B    0.0%
observation_count             156 B    0.0%
coverage_fraction             116 B    0.0%
allowed_client_exposure        64 B    0.0%
```

**One column is 93.9% of the file.** Everything else is rounding error, which settles two things: keeping
`coverage_fraction` and `allowed_client_exposure` costs **180 bytes per month** (§0.22.4 was right that they are
free), and any future size work must target `normalized_value` alone — nothing else is worth touching.

**Full-plane projection: ~24.5M rows, ~35 MB zstd.** Three independent routes agree: July's 15,730 grain
rows/day × 1,560 days = 24.5M; 46.07M raw ÷ the 1.85× collapse = 24.9M. **CONFIRMED against the real thing:**
the 2026-08-17 research recorded `geo.mv_signal_cell_daily` at **24,958,092 rows** before it was dropped — the
projection above was derived from one month with no knowledge of that figure and lands **within 2%**. The
~35 MB number can be trusted. Against the dropped
`geo.mv_signal_cell_daily` at **6,349 MB**, that is a **~180× reduction** — and it is the number that makes the
Parquet path worth walking.

**A float32 option exists and is NOT taken.** A float64→float32 round-trip over all 487,630 values loses at most
`5.7e-08` relative — far inside any geophysical tolerance — and would roughly halve the dominant column, so
~35 MB becomes ~19 MB. **This is a data-fidelity decision, not a performance one, and it is left open.**

#### 0.22.7 THREE CORRECTIONS to §0.22.2 and §0.22.3, from the July export

1. **The ~21.8M row estimate was an UNDER-estimate, not the over-estimate §0.22.2 labelled it.** The reasoning
   there was sound — the cell probes omitted the governed join, so they counted rows the rollup excludes — but
   the 60 probe cells turned out to be less dense than average, and that dominated. The measured projection is
   **~24.5M**. Recorded because the error was in the direction the stated reasoning ruled out.
2. **`allowed_client_exposure` is a `boolean`, and its only value is `False`.** §0.22.3 said "one distinct
   value" without naming the type — the four `sql/agent/*.sql` headers describe it as "what exposure the
   governed plane permits" and never say it is a flag. **Every governed row currently says exposure is not
   permitted**, while the map paints this data. That is either a misread of the column's meaning or an unset
   default, and **it is unresolved** — do not build an exposure gate on it without settling that first.
3. **The 1.85× collapse is a HISTORICAL BACKFILL artifact, not an ongoing property.** July 2026's
   `observation_count` histogram is `{1: 487,258, 2: 372}` — mean **1.001**, duplicates on 0.076% of rows.
   The overlapping archive re-releases are concentrated in the backfilled years. This does **not** weaken
   §0.22.3's finding (`min = max` on 0 of 701,257 rows was measured across the full extent), but it does mean
   `observation_count` is ~1 on current data and the deduplication buys almost nothing going forward.

#### 0.22.8 A coverage gap the export surfaced

**`surface_shortwave_radiation` has ZERO rows in July 2026** while every other `nasa-power-daily` signal has
exactly 12,307. It is a governed signal under contract, so this is a live lane gap, not an absence by design —
precisely what [`docs/layer-lane-standard.md`](../docs/layer-lane-standard.md)'s gap detection exists to turn
into a work item. Found incidentally by exporting a month; **not investigated, not fixed.**

Note also that the plane has **two cell populations**, which any export must expect: `nasa-power-daily` signals
cover **397 cells**, the ERA5-Land signals cover **1,470**, and 397 + 1,470 = the 1,867 cells that carry data
(of 1,965 in `agri.spatial_cell`).

---


---

### 0.23 HANDOFF 2026-08-22 — THE ARCHITECTURE PIVOT: Postgres becomes a community-features database

Owner decision this session, and it supersedes the programme above rather than extending it. **Read this
before §0.16–§0.22; those sections optimise a Postgres the project is now leaving.**

#### 0.23.1 Goal

Move every data plane out of Postgres into **day-partitioned Parquet computed at ingestion time**, read by
**DuckDB (spatial extension) + Polars**, with **Martin still serving tiles** — from generated PMTiles instead of
PostGIS functions. **Postgres is retained for community features only.** The target this serves is explicit:
stop paying for a service pinned near 40 GB.

Owner, verbatim: *"postgres will only be used for the community features, everything else is duckdb with the
geo extension + polars + martin for tile serving — this should serve everything we need without keeping a
service at 40 gb ram. parquet files will make gap detection easier as well."* And on sequencing: *"I don't care
if the map is no longer working; we need a better long-term solution focused on efficiency and compute at
ingestion time, with parquet files being used like materialized views and continuous aggregates."*

#### 0.23.2 FOUR RELATIONS ARE THE ENTIRE PROBLEM

Catalog read, 2026-08-22, `pg_total_relation_size` — no scans:

| relation | rows | size | geometry |
|---|---|---|---|
| `agri.signal_observation` | 46,068,872 | **26 GB** | no |
| `geo.features` | 5,025,009 | **7,986 MB** | **yes** |
| `geo.geometry` | 3,277,801 | **2,988 MB** | **yes** |
| `geo.drought_areas` | **995** | **500 MB** | **yes** |
| `agri.artifact` | 1,632 | 173 MB | no |
| `agri.forecast_observation` | 184,409 | 116 MB | no |
| *(31 further relations)* | | **all under 120 MB** | |

**37.5 GB of ~40 GB sits in four relations.** Migrating those four is the whole RAM story; the other 31 are
noise and must not absorb migration effort. Note `geo.drought_areas`: **995 rows occupying 500 MB.** Row count
is worthless as a size proxy anywhere geometry is involved — size the geometry, never the rows.

#### 0.23.3 The refutation this overrides, and why the override is coherent

The 2026-08-17 verdict recorded that **no streaming/OLAP engine can replace PostGIS**. That finding is not
being ignored — it is **out of scope for what is now proposed**, and the distinction matters:

- It evaluated replacing PostGIS **in place**, keeping query-time compute and asking an engine to serve the
  same live workload. This design **moves compute to ingestion time** and treats Parquet as the materialised
  view — there is no live analytical query left to serve.
- It found the rollup problem was **one 6.3 GB matview that has no geometry**. §0.22 measured that exact plane
  at **1.43 B/row, ~35 MB for its whole history** against 6,349 MB — a ~180× reduction, which is the evidence
  this pivot rests on.
- **Martin v1.10.1 serves PMTiles and MBTiles from files**, not only PostGIS functions (`Dockerfile.martin:1`).
  Tile serving therefore survives the removal of the geometry backend. This is the fact that dissolves the
  apparent contradiction between "no Postgres" and "the map works".

**Update `plantgeo-engine-migration-verdict` in memory**: it is superseded as a blocker, and its reasoning
remains correct only for the in-place-replacement question it actually asked.

#### 0.23.4 Decisions — owner, 2026-08-22. Do not re-litigate.

1. **Postgres serves community features only.** Everything else leaves.
2. **DuckDB + spatial extension** is the geo/analytical engine; **Polars** does the transforms.
3. **Martin stays**, serving **generated PMTiles** rather than PostGIS tile functions.
4. **Parquet files are the materialised views and continuous aggregates.** Compute happens **at ingestion**.
5. **One Parquet file per day** for daily pulls, with **year/month/day striation in the path** — chosen by the
   owner specifically because *"striation across month and years makes ingestion and gap checking easier"*.
   Gap detection becomes an object listing, not a query.
6. **Railway object storage is the target** — owner: *"it has to be railway, it's the main storage service."*
   This retires the R2 option for data (R2 keeps serving basemap tiles at `tiles.aevani.com`).
7. **The map may break during the transition, and that is accepted.** Long-term efficiency outranks continuity
   of the fix shipped 2026-08-21.
8. **The signal plane exports 10 columns**, per §0.22.4 — `min_value`/`max_value`/`avg_value` dropped.

#### 0.23.5 State

**Verified this session:** the sensor tile through production Martin — 222,867 B raw / 91,898 B gzipped, warm
0.36–0.44 s, cold 46.8 s (§0.21's fix confirmed live). The signal-plane grain, measured and decided (§0.22).
One month of real Parquet output, verified to have **0 duplicate grain keys** (§0.22.6).

**Not started:** every export. Nothing has been written to object storage. The only Parquet on disk is the
July 2026 proof at `C:/tmp/pq/` — scratch, uncommitted, safe to delete.

**Uncommitted:** `conductor/RUNBOOK.md` carries §0.22 and this section and **is not committed**. Tree was clean
at `70a0299`; this file is the only modification.

**Missing from the toolchain — this blocks step 1:** `polars`, `boto3` and `s3fs` are **not installed** in
`services/agri-data-service/.venv`. `pyarrow 21.0.0` and `duckdb 1.5.4` are present.

#### 0.23.6 Assumptions — none of these were asked; each names its reversal cost

- **Path layout `layer=<name>/year=YYYY/month=MM/day=DD/part-0.parquet`** (Hive-style, so DuckDB and Polars both
  prune on it for free) · default taken · **to reverse:** re-upload under a new prefix — cheap now, expensive
  once ingestion writes against it. **Confirm this before step 3.**
- **zstd, not snappy** · measured 695,338 B vs 874,945 B on the same month, ~20% better · **to reverse:** re-export.
- **float64 retained** · §0.22.6 left this open; float32 loses ≤5.7e-08 relative and would roughly halve the
  dominant column (~35 MB → ~19 MB) · **to reverse:** re-export, but only cheap before the backfill runs.
- ~~**Static layers get one file per layer, no day striation**~~ — **OVERTURNED 2026-08-22 by S0 (§0.24.9).**
  Static layers write one dated partition on the release day, using the identical layout as daily lanes, so
  every generic reader/lister/gap-detector works across all eleven with no special case.
- **Community-feature tables live in `public`** and were not inventoried this session · **to reverse:** cheap
  catalog read — but it decides what actually stays in Postgres, so do it before declaring the migration done.

#### 0.23.7 Relevant files

- [`conductor/RUNBOOK.md`](RUNBOOK.md) §0.22 — the grain decision, the measured Parquet numbers, and five traps
  for the export job. §0.22.5 is the one to read before writing any exporter.
- `services/agri-data-service/src/agri_data_service/sql/agent/{signal_value_on_day,signal_neighbors_in_time,signals_near_point,nearest_signal_cells}.sql`
  — the four statements whose `spread` CTEs still reference the dropped `min_value`/`max_value`/`avg_value` and
  still read `FROM geo.mv_signal_cell_daily`. **Deliberately untouched**: their aggregate edits are settled by
  §0.22.4, but their source relation is not, and two passes over the same four files was not worth it.
- `services/agri-data-service/src/agri_data_service/execution/{historical_parquet,historical_era5_parquet}.py`
  — Parquet export code that already exists. Read before writing a new exporter.
- `infra/martin/martin.yaml` — where PMTiles sources get registered when tile serving moves off PostGIS.
- `services/agri-data-service/.env` — holds `DATABASE_URL_SYNC` (production). **Values stay here; never inline.**

#### 0.23.8 Continuation plan — ordered by dependency, not by discovery

1. **Install the toolchain**: `polars`, `boto3`, `s3fs` into `services/agri-data-service/.venv`. Everything
   below is blocked on this. Verify with an import check, not with pip's exit code.
2. **Wire the Railway bucket credentials as reference variables** (the previous plan's step 2; gates 33/34
   waived). The Railway CLI is authenticated — the MCP plugin is not, and cannot be in a non-interactive
   session. Nothing can be uploaded until this lands.
3. **Confirm the path layout** in §0.23.6, then **export `agri.signal_observation`** day-by-day. Reuse the
   measured query shape from §0.22.6 verbatim — 10 columns, governed 19-triple join passed as
   `unnest(%s::text[],...)` arrays, **batched by `cell_id`** because no index leads on `observed_at`
   (§0.22.5). 1,560 days, ~487k rows/month, ~35 MB total expected.
4. **Export `agri.forecast_observation`** (184,409 rows, 116 MB) — same pattern, second non-geometry plane.
5. **Export the three geometry relations** — `geo.drought_areas` (500 MB), then `geo.geometry` (2,988 MB), then
   `geo.features` (7,986 MB). Geometry goes to Parquet as WKB; DuckDB's spatial extension reads it back.
   **This is the hard half and its tile path is an open question — see §0.23.9.**
6. **Update the runbook for the serverless read path**: how DuckDB+Polars answer what the tRPC readers and the
   four agent SQL tools answer today, and where compute now happens. This is the section that replaces §0.10's
   pre-aggregation design.
7. **Only then** repoint the four `sql/agent/*.sql` files — one pass, both the `spread` CTE fix (§0.22.4) and
   the new source, once the Parquet-backed relation has a name.

#### 0.23.9 Open questions — each with the trigger that makes it live

- **How do PMTiles get generated from Parquet?** Martin serves them; nothing in this repo *produces* them from
  a Parquet geometry store. Tippecanoe is the usual answer and is not currently a dependency. **Live at step 5**,
  and it gates whether the map comes back at all.
- **What is the ingestion-time compute substrate?** Decision 4 says compute moves to ingestion, but the current
  ingestion is a Python CLI on a Railway cron. Whether that stays and writes Parquet, or is replaced, is
  undecided. **Live at step 3**, because it decides whether the exporter is a migration tool or the new
  production writer.
- **What actually remains in Postgres?** "Community features" was not inventoried. **Live before step 6.**
- **`ingest/validation/models.py`'s `cadence_basis` strings cite SIX directories that do not exist** —
  `infra/cron-{firms,streamflow,weather,ndvi,fire-perimeters,evacuation-zones}/railway.json`, all stale since
  the 2026-08-14 cron consolidation. Only `cron-ingest`, `cron-mtbs` and `cron-soilgrids` are real; everything
  else runs hourly from the shared `cron-ingest` via `run_all_ingestion_jobs`. **Two wave-1 agents disagreed
  about this and the one quoting `models.py` was wrong** — the string reads like a citation and is not one.
  Affects every lane's declared cadence, and therefore every gap-detection window built on it.
- **`surface_shortwave_radiation` has zero rows in July 2026** while every sibling NASA-POWER signal has 12,307
  (§0.22.8). A governed signal with a live gap, found incidentally and **not investigated**. **Live whenever
  gap detection is built** — it is a ready-made test case for it.
- **`allowed_client_exposure` is a boolean whose only value is `False`** across every governed row (§0.22.7),
  while the map paints this data. Unresolved. **Live before any exposure gate is built on it.**

---

### 0.24 THE STREAM PLAN — how the pivot in §0.23 gets executed concurrently

Owner instruction 2026-08-22: *"set up the runbook so that each stream is tackled separately in a concurrent
manner."* This section is the execution structure for §0.23. **§0.23 is the decision; §0.24 is the work
breakdown.** Every stream below has a disjoint file boundary so streams in the same wave can run at once
without two agents editing one file.

The lane contract every per-layer stream must satisfy is
[`conductor/code_styleguides/layer-lanes.md`](code_styleguides/layer-lanes.md), written this session and
binding from now on. Read it before opening any lane.

#### 0.24.1 The streams, their boundaries, and their wave

| # | stream | owns (nothing else may write here) | needs | wave |
|---|---|---|---|---|
| **S0** | **Parquet foundation & contract** — path layout, writer, schema registry, Railway object-store client | `…/agri_data_service/parquet/**` | — | **1** |
| S1 | **Geometry export** — `geo.features` (7,986 MB), `geo.geometry` (2,988 MB) to WKB Parquet | `…/parquet/geometry/**` | S0 | 2 |
| S2 | **Drought geometry** — `geo.drought_areas` (995 rows, **500 MB**) | the `drought` slug's five lattice files | S0 | 2 |
| S3 | **Signal plane** — `agri.signal_observation`, 46M rows / 26 GB. **Spec already complete in §0.22** | the `signal` slug's five lattice files | S0 | 2 |
| S4 | **Forecast-observation plane** — `agri.forecast_observation` (184,409 rows) | the `forecast-observation` slug's five lattice files | S0 | 2 |
| **S5–S15** | **The eleven layer lanes — one stream each, fully independent** (§0.24.2) | `method/monte_carlo/<slug>.py`, `pipeline/lanes/<slug>.py`, `pipeline/validation/<slug>.py`, `planes/<slug>.py`, `warehouse/schemas/<slug>.py` — **one file per layer, NOT a `lanes/` package** (§0.24.8) | S0 | 2 |
| S16 | **Ingestion dual-write** — every producer writes Postgres **and** Parquet | `…/execution/**`, `…/ingest/**` | S0 | 2 |
| S17 | **Backfill & source validation** — reconcile written Parquet against source systems | `…/validation/**` | S1–S15 | 3 |
| S18 | **Serving aggregates** — the rollups each serving surface actually reads | `…/aggregates/**` | S5–S15 | 3 |
| S19 | **PMTiles generation + Martin repoint** | `infra/martin/**`, `scripts/tiles/**` | S1, S2 | 3 |
| S20 | **Polars/DuckDB serving** — the read path that replaces tRPC-over-Postgres | `…/serving/**` | S18 | 4 |
| S21 | **Ops, readiness & admin repoint** — `routes/ops.py` (1,874 lines) and the readiness probes | `…/routes/ops.py`, `…/routes/health/**` | S20 | 5 |

**Wave 2 is eleven-plus-five streams running at once.** That is the point of the boundary column: S3 touching the `signal` slug's five files cannot collide with S9 touching the `sensors` slug's five, and neither may touch `parquet/`, which S0
froze in wave 1.

#### 0.24.2 The eleven lanes, and which of them can actually forecast

From `geo.layers`, 2026-08-22. **Each is its own stream.**

**RECONCILED 2026-08-22 at the wave-1 join.** All eleven lane contracts now exist under
[`docs/lanes/`](../docs/lanes/), each written from the repo with `path:line` citations, and **each lane's own
declaration wins over the starting classification below** (lane contract §2). Two changed on contact with the
evidence: **`fire-perimeters` flipped from "yes" to `none`** — measured, only 6 of thousands of Type-2 dimension
entries ever reached a second version, so there is almost no growth history to calibrate against and no honest
way to validate a projection; and **`burn-severity`'s "probably not" resolved to a firm `none`** — its time axis
is 5 discrete release dates across all history, and the layer already declares a typed `HistoryCapability(
supported=False)` refusal that applies symmetrically forward.

**Net: FIVE forecasting lanes, not six.** The `horizon` column below is now the declared value, not a guess.

| lane | source system | 30-day Monte Carlo? |
|---|---|---|
| `weather-observations` | NASA POWER / ERA5-Land | **yes** — the core forecast lane |
| `sensors` | **NOAA NWS `api.weather.gov`**, keyless | **30d**, but its own history is only ~3 weeks (NWS serves a rolling ~6 days) — must borrow a longer prior or gate on minimum history |
| `water-gauges` | USGS NWIS | **30d**, but the dense record starts only 2026-05-24; the declared 2022 floor was borrowed from vegetation, not a source limit |
| `vegetation` | Sentinel-2 NDVI (`sentinel2-ndvi-l2a`) | **yes** — 4 years of history, the deepest record |
| `fire-detections` | FIRMS (**needs `NASA_FIRMS_KEY`**) | **30d — aggregate only** (count / summed FRP per cell-day). Raw per-detection forecasting cannot satisfy the identical-grain rule |
| `fire-perimeters` | WFIGS `_Current` FeatureServer (NIFC), keyless | **none** — flipped; see above |
| `burn-severity` | MTBS (USDA FS EDW), keyless | **none** — 5 discrete release dates, not a series |
| `interventions` | community submissions | **no** — 0 published rows; demand-driven, not projectable |
| `soil-survey` | USDA SSURGO | **no** — static; `horizon: none` |
| `watersheds` | USGS WBD HUC12 | **no** — static; `horizon: none` |
| `evacuation-zones` | **Oregon OEM only** — a real automated feed, but no equivalent exists for WA/ID/MT | **none** — forecasting a policy decision, not a physical process |

**The Monte Carlo forecasts are what serve future dates to users.** That is their purpose, and it is why the
lane standard couples each forecast to its own lane's observed stream at identical grain: the time slider must
be able to cross today without changing shape, and without the user being shown a projection that reads like a
measurement.

#### 0.24.3 What each wave has to be true before the next starts

- **Wave 1 → 2.** S0's path layout and schema registry are frozen. Sixteen streams are about to write against
  it; changing it in wave 2 invalidates everything already written. **This is the one place where getting it
  wrong is expensive** — §0.23.6's layout assumption must be confirmed here, not later.
- **Wave 2 → 3.** Every producer stream has written at least one real partition and had it read back. Not
  "code complete" — a file that a reader opened.
- **Wave 3 → 4.** Aggregates exist for every surface the app actually reads. Enumerate the surfaces from the
  tRPC routers before declaring this, not from memory.
- **Wave 4 → 5.** The serving path answers the same questions as the current readers. Ops repointing against a
  read path that is not yet answering is how a dashboard starts reporting green on an empty warehouse.

#### 0.24.4 Repointing ops, readiness and admin — S21, and why it is last

`routes/ops.py` is **1,874 lines** of Datastar-driven dashboard: lanes, walks, streams, sources and forecast
panels, all reading the `agri.job_*` ledger and the observation planes directly through SQLAlchemy. It is the
surface that tells the owner whether ingestion is alive, so **it must be repointed after the serving path
works, never before** — a readiness panel that reads a half-migrated warehouse reports confidently and wrongly,
and this runbook already records one audit whose citations could not be trusted.

Two things it must keep doing after the move: reporting **zero-landing** per plane, and distinguishing a
**governed absence** from a gap. Both get easier under Parquet — a missing day is a missing object path — but
only if S17's validation writes governed absences as objects rather than leaving holes.

#### 0.24.5 ML is frozen, deliberately

Owner: *"for now leave ml models alone, I'll engage separately on this — we are swapping to mojo executables,
but none of it matters until we have the right data sets and a solid user experience."*

- **No stream in this plan touches model code.** ML is **already at `method/ml/`** (10 modules) and **does
  not move** — an earlier draft of this section said it moves to a new top-level `ml/`, which was wrong
  (§0.24.8). It is expected to leave for a separate **Mojo service**, which is a reason to keep its boundary
  sharp rather than to relocate it first.
- **The `monte_carlo` ↔ `ml` boundary is NOT yet enforced.** Both sit inside `method`, so the existing lattice
  test does not separate them. Adding that rule to `tests/test_layer_import_contract.py` is a **wave-2
  prerequisite** (layer-lanes §5); until then the boundary is convention only.
- **Monte Carlo forecasting is not ML** under this split — it is a per-lane statistical projection with
  declared provenance, and it belongs to the lane it forecasts.

#### 0.24.6 Style-guide enforcement, and what it is protecting against

[`conductor/code_styleguides/layer-lanes.md`](code_styleguides/layer-lanes.md) is binding for every stream
S1–S15. The two rules that matter most while sixteen streams run concurrently:

1. **A lane never imports another lane.** Shared needs go down to the foundation, in their own commit. A
   `from ..lanes.` import inside `lanes/` is the failure mode that quietly re-couples streams the wave plan
   just separated.
2. **A lane writes only under its own `layer=<slug>/` prefix.** This is the only defect in the set that
   corrupts *another agent's* output, and it will not show up as a test failure.

Both belong in review, not in a linter's backlog: add them to the review checklist now, and mechanise later.

#### 0.24.7 Traps carried forward into this plan

Each of these is already measured elsewhere in this runbook and will otherwise be rediscovered per stream:

- **`agri.signal_observation` has no index leading on `observed_at`** — day-partitioned export must batch by
  `cell_id` (§0.22.5). Affects S3 directly, and S16 whenever it backfills.
- **Never size a geometry relation by row count** — `geo.drought_areas` is 995 rows and 500 MB (§0.23.2).
  Affects S1, S2 and S19's tiling budget.
- **`EXPLAIN` cost is meaningless for the tile functions** — it prices a 0-row layer identically to a
  186,904-row one (§0.21.2). Affects S19. Measure `octet_length()` and wall-clock instead.
- **Restart Martin after any tile-source change** — a missing tile function 404s the whole composite and hides
  every layer. Affects S19.
- **FIRMS silently drops beyond 10,000 records** — a "0 written" result is by-design idempotency, not success.
  Affects the `fire-detections` lane and S17's validation.
- **`surface_shortwave_radiation` has zero rows in July 2026** while every sibling NASA-POWER signal has 12,307
  (§0.22.8) — a ready-made first test case for S17.

---

#### 0.24.8 CORRECTION — the first draft of this plan contradicted an enforced contract

**Written and corrected 2026-08-22, before any code was written against it.** Recorded rather than quietly
fixed, because the failure mode will recur: this section was authored without reading
`services/agri-data-service/tests/test_layer_import_contract.py`, which already exists and is enforced.

**The repo has a six-layer dependency lattice**, shipped 2026-08-14/15 under track
`agri_sdk_layering_20260805` (phases 0–3 of 9 — the later phases never landed, and `warehouse/`, `pipeline/`,
`planes/`, `interface/` are still `AGENTS.md`-only stubs):

```
foundation → method → warehouse → pipeline → planes → interface
```

`foundation` may import nothing from `agri_data_service` and no `sqlalchemy`/`httpx`/`asyncpg`/`click`.
**`method` may not import `sqlalchemy` or `httpx`** — it is the pure-computation layer.

**Three defects this created, all now fixed:**

1. **A top-level `lanes/<slug>/` package cannot exist under the lattice.** It would have held `ingest.py`
   (needs `httpx`) beside `forecast.py` (a `method` module, where `httpx` is forbidden) — unclassifiable, and
   violating the lattice by construction. **Corrected:** a lane is a *vertical slice across* the lattice —
   one file per layer, named by slug. See [`code_styleguides/layer-lanes.md`](code_styleguides/layer-lanes.md) §1.
2. **§0.24.5 said ML "moves to `…/agri_data_service/ml/`". It is already at `method/ml/`** (10 modules) and
   does not move. **Corrected below.** `method/monte_carlo/` likewise already exists and already contains
   `vegetation_ndvi_forecast.py` — the `vegetation` lane must bring that into conformance, **not write a
   second vegetation forecaster beside it**.
3. **The `lanes/` ↔ `ml/` import ban was unenforceable as stated.** `monte_carlo` and `ml` are *both* inside
   `method`, so the lattice test does not separate them. **A new rule is required** and is now a wave-2
   prerequisite: `method.monte_carlo` may not import `method.ml`, and vice versa. Until it is added to
   `test_layer_import_contract.py`, that boundary is convention only.

**The generalisable lesson:** this runbook's own §0.21.8 records ~10M tokens of analysis producing three
changes, because each pass re-derived from scratch. This was the same failure at a smaller scale — a plan
written from the conversation rather than from the tree. **Read the enforcement before writing the standard.**

**Also inherited from the same track, and still open:** §0.24's stream table assigns `…/execution/**` and
`…/ingest/**` to S16 using **pre-refactor paths**, while phases 4–8 of `agri_sdk_layering_20260805` intended to
dissolve exactly those into `warehouse/`/`pipeline/`/`planes/`/`interface/`. That track is now marked
**blocked**. **Owner call needed:** does the lattice refactor finish first, or does S16 write against today's
paths and get moved later? Nothing in wave 2 should start on `execution/`/`ingest/` until this is answered.

---

#### 0.24.9 WAVE 1 IS COMPLETE — 2026-08-22. The contract is frozen; wave 2 may start.

Twelve agents ran concurrently: S0 (the foundation) plus one per layer writing its lane contract. **Independent
sweep at the join, run by the orchestrator rather than the authoring context: 3,149 passed, 110 skipped, 0
failed; `ruff` clean; `mypy --strict` clean.** No agent wrote a sibling's file; nothing was committed.

**The frozen interface lives in code, not here** — read the signatures directly, they are the contract:
`foundation/parquet/paths.py` (170 lines, pure stdlib), `warehouse/parquet/schema.py` (131),
`pipeline/parquet/objectstore.py` (284), plus `AGENTS.md` in each package and six `OBJECT_STORE_*` settings in
`config.py`. 88 new tests under `tests/parquet/`.

**Three design calls S0 froze that wave 2 must not re-open:**

1. **The layer slug is ONE identifier for three things** — registry key, schema module
   (`fire-detections` → `fire_detections.py`), and the `layer=<slug>/` write prefix. They cannot drift.
2. **The schema registry autoloads instead of using a central dict.** A central file would serialise the
   16-stream wave-2 fan-out onto a single edit point — the exact collision the wave plan exists to avoid.
3. **Zero-row writes are REFUSED.** An empty Parquet file reads to gap detection as a *present* day, silently
   converting a real hole into coverage. This is the single most important safety property in the foundation.

**§0.23.6's static-layer assumption is OVERTURNED — by measurement, and the new answer is better.** That
assumption said static layers (SSURGO, watersheds) get "one file per layer, no day striation". They instead
write **one dated partition on the release day**, using the identical layout as daily lanes. The reason is
decisive: every generic reader, lister and gap-detector then works across all eleven lanes with no special
case. The `watersheds` lane contract independently reached the same place from the other direction — its
measured production reality is a single load day across the whole set.

**Deliberately NOT built: governed-absence markers.** `missing_partition_days` currently reads a governed
absence as a gap. That convention must be **one decision for all sixteen streams** and is owned by S17;
inventing it per-lane is how eleven incompatible absence conventions get created. Flagged in
`foundation/parquet/AGENTS.md`.

**All eleven lane contracts now exist** under [`docs/lanes/`](../docs/lanes/), each written from the repo with
`path:line` citations. They are the wave-2 briefs. **Read the lane's contract before implementing the lane** —
six of them corrected a claim this runbook or a briefing asserted, and §0.24.10 records why that matters.

---

### 0.25 HANDOFF 2026-08-22 (second) — wave 1 shipped, and the layering question is answered

Supersedes §0.24.8's open owner call. **Wave 2 is unblocked.**

#### 0.25.1 The structural decision, and what it retires

Owner: *"the goal is separate parquets for each layer with dedicated ingest and execution layered in by domain
while sharing used primitives from an ingest and execute path."*

Grounding that changed the question: **`ingest/` is already 90% of that shape.** 44 modules — **12 domain
producers** (`firms`, `mtbs`, `wfigs`, `sensors`, `watersheds`, `usgs_nwis`, `evacuation_zones`, `ndvi`,
`vegetation`, `usdm`, `open_meteo*`) over **shared primitives** (`http`, `arcgis`, `geometry`, `identity`,
`writer`, `policy`, `runner`, `source`, `upstream_retry`, `archive_walk`). **`execution/` is not**: 62 modules,
**29,662 lines**, flat, mixing per-source `historical_*` with coverage, forecast and ML CLI wrappers.

**Four decisions, 2026-08-22:**

| # | decision | consequence |
|---|---|---|
| 1 | **`ingest/` producers move under per-domain packages** — `ingest/<domain>/` holds the producer plus domain-only helpers; shared primitives stay at `ingest/` root | a rename/move sweep across imports and tests; the largest mechanical diff in wave 2 |
| 2 | **`execution/` splits the same way** — `execution/<domain>/` for that domain's `historical_*`, backfill, promotion; `contracts.py`, `coverage_*` and job plumbing stay at root as the shared execute path | the 29,662-line restructure; mirrors what `ingest/` already proves works |
| 3 | **Governed absence is a MARKER OBJECT at the day's partition path** (§0.25.3) | gap detection stays a pure listing operation |
| 4 | **`cli.py` is NOT dissolved now.** Wave 2 adds Parquet verbs to it as-is; the 52 existing command strings keep working | dissolving a 3,723-line file while 16 streams write into it is the exact collision the wave plan exists to prevent |

**`agri_sdk_layering_20260805` phases 4–8 are SUPERSEDED, not deferred.** They intended to dissolve `db/`,
`models/`, `ingest/`, `historical_*` and `cli.py` **into** the lattice's `warehouse/`/`pipeline/`/`planes/`/
`interface/`. Decisions 1, 2 and 4 keep `ingest/` and `execution/` as first-class homes and layer them
internally by domain instead. **Phases 0–3 stay — the six-layer lattice and its import contract remain in
force.** Update that track's status accordingly; it is currently `blocked` pending exactly this call.

#### 0.25.2 THE ENFORCEMENT GAP THIS OPENS — read before starting wave 2

`tests/test_layer_import_contract.py` polices **only the six lattice directories**. **`ingest/` and
`execution/` are not among them**, and never have been. So decisions 1 and 2 create a domain/primitive
boundary with **no enforcement whatsoever** — nothing stops `execution/sensors/` importing
`execution/vegetation/`, which is precisely the cross-lane coupling the whole wave plan exists to prevent.

The lane contract's "a lane never imports another lane" rule is, in these two packages, **convention only**.
The `method.monte_carlo` ↔ `method.ml` rule added this session (`SUBPACKAGE_FORBIDDEN_IMPORTS`) is the pattern
to extend: add a rule asserting no `ingest.<domain>` imports another `ingest.<domain>`, and likewise for
`execution`. **Do this in the same commit as the first domain package**, not after eleven of them exist.

#### 0.25.3 Governed absence — the contract S0 refused to invent

S0 deliberately left this out because it must be **one decision across all sixteen streams**; inventing it
per-lane produces eleven incompatible absence semantics. Owner chose the marker object. The binding points:

- **A marker object is written at the day's partition path** where the data partition would have gone. Gap
  detection therefore stays a **pure listing operation** with no second lookup — the property the
  `year=/month=/day=` striation was chosen for in the first place.
- **It must be distinguishable from a data partition by key alone**, so a lister can classify without opening
  anything.
- **It carries its evidence**: the reason, the upstream response or status that justifies it, when it was
  recorded, and by which run. An absence without evidence is indistinguishable from a silent failure.
- **`missing_partition_days` must treat a marked day as covered-by-absence, NOT as a gap** — it currently
  reads it as a gap (`foundation/parquet/AGENTS.md` flags this).
- **S0's zero-row refusal STAYS.** An empty Parquet file must never be the absence mechanism; that is why the
  refusal exists — an empty file reads to a lister as coverage.
- Owner rule already recorded (§0.21.5): **a backfill that corrects a completed record is a manual admin
  action.** Retracting an absence is therefore deliberate, never automatic.

#### 0.25.4 State at handoff

**Wave 1 complete and independently verified at the join** (orchestrator, not the authoring context):
**3,149 passed · 110 skipped · 0 failed · `ruff` clean · `mypy --strict` clean.**

Shipped: the frozen Parquet contract (585 lines across three lattice modules + 88 tests, §0.24.9); all
**eleven lane contracts** under [`docs/lanes/`](../docs/lanes/); §0.22–§0.24 of this runbook;
[`code_styleguides/layer-lanes.md`](code_styleguides/layer-lanes.md); the `monte_carlo`↔`ml` import rule;
`polars`/`boto3`/`s3fs` in `pyproject.toml`; conductor-track and docs audits.

**42 files changed. NOTHING COMMITTED. HEAD is still `70a0299`.**

**Six lane contracts corrected a claim this runbook or a briefing asserted** — most consequentially that
FIRMS **does** require `NASA_FIRMS_KEY`, that `geo.streamflow_reading` **does not exist**, and that
`ingest/validation/models.py` cites **six `infra/cron-*` directories that are not there** (§0.24.7). Read the
lane contract before implementing its lane.

#### 0.25.5 Continuation plan

1. **Wire the Railway bucket credentials** as reference variables and populate the six `OBJECT_STORE_*`
   settings S0 added to `config.py`. The CLI is authenticated; the MCP plugin is not. Everything downstream
   is blocked on this — the foundation is built but has nowhere to write.
2. **Implement the absence marker** per §0.25.3 in `foundation/parquet/` and teach `missing_partition_days`
   about it. **Before any lane writes**, so no lane invents its own.
3. **Add the domain-isolation import rules** (§0.25.2) for `ingest` and `execution`, in the same commit as the
   first domain package.
4. **Move ONE domain end to end as the template** — recommend `weather-observations`: it is the core forecast
   lane, its §0.22 export path is already measured, and its lane contract is the most complete. Prove the
   `ingest/<domain>/` + `execution/<domain>/` shape once before repeating it ten times.
5. **Then fan out the remaining ten lanes** against the proven template, per §0.24's wave 2.
6. **`method/monte_carlo/` and `execution/` hold two near-identical copies of the NDVI forecaster** — the
   `execution/` one is wired, the `method/` one is not. Converge them when `vegetation` moves; do not add
   provenance columns to whichever one is opened first (§0.24.2, vegetation lane contract).

---

### 0.26 WAVE 2 IN PROGRESS — 2026-08-22 (third session). Steps 1–3 done, step 4 half done.

**HEAD moved.** `70a0299` → **`fa523df`** (wave 1's 42 files, committed verbatim) → **`185b704`**
(absence marker) → **`00b1fc1`** (shared-type extraction) → the domain package. §0.25.4's "42 files
changed, NOTHING COMMITTED" is now historical; the tree is clean.

#### 0.26.1 The Postgres boundary is answered — owner call, 2026-08-22

Supersedes the first bullet of §0.25.6. Owner: *"postgres does not need to stay only the community
intervention feed features rather then the analytics data for the social features is fine but
analytics happens on and with parquet if possible."* Recorded as the **classification rule**, so
the inventory can be mechanical rather than another decision:

> **Postgres keeps every community/social feature table and its operational data** — interventions,
> users, engagement, comments, activity. **Analytics COMPUTE moves to Parquet + DuckDB** wherever it
> can. **Postgres is never queried analytically**, including for social features.

The `interventions` lane contract's recommendation (that lane stays, §8 of `docs/lanes/interventions.md`)
is consistent with this and needs no revision.

#### 0.26.2 Steps 1–3, and what each actually took

1. **Bucket credentials — DONE and round-tripped.** Railway bucket `plantgeo-parquet`
   (`79d5b0c0-059a-40a9-a90a-ef8d15bb5828`), region **`sjc`**, endpoint **`https://t3.storageapi.dev`**,
   real bucket name **`plantgeo-parquet-9ymvp7gv`** (Railway suffixes it — the display name is not the
   S3 name, and `OBJECT_STORE_BUCKET` needs the suffixed one). `OBJECT_STORE_REGION=auto`, which is
   what the credentials endpoint returns, **not** `sjc`.
   - **CORRECTED — the Railway MCP is authenticated and works.** An earlier draft of this section
     said it was not, repeating the harness's startup reminder without testing it. `whoami`,
     `list_variables`, `set_variables`, `create_bucket` and `remove_bucket` all answer.
     **Test an MCP before recording it as unavailable**; a stale startup reminder is not evidence.
   - **The real, narrower limit: the MCP exposes NO bucket-credentials tool.** It can create and
     remove buckets but cannot hand back the S3 access keys, so `railway bucket credentials
     --bucket <name> --json` (CLI) remains the only way to retrieve them. There are also no
     `${{...}}` reference variables for buckets — the five values are set literally.
     (Separately and still true: the MCP cannot delete *services* — `remove_service`'s confirm
     flag cannot be sent from this harness.)
   - Wired into the local gitignored `.env` **and onto all three cron services** (`plantgeo-ingest-cron`,
     `plantgeo-cron-mtbs`, `plantgeo-cron-soilgrids`) with `--skip-deploys`, since no deployed code reads
     them yet. Secret set via `--set-from-stdin` so it never lands in shell history.
   - **Verified by a real round trip against the live bucket**: put → `size_of` 28 → list → delete →
     `size_of` None. The foundation now has somewhere to write.
   - **Trap:** `uv sync` alone **removes pytest and ruff** — dev deps are an *extra*, not a dependency
     group. Use `uv sync --extra dev`, or the next `pytest` run fails with `ModuleNotFoundError:
     agri_data_service` and looks like a broken install.

2. **Governed absence — DONE** (`185b704`), per §0.25.3. `absent.json` at the day's partition path;
   `foundation/parquet/absence.py` owns the evidence payload (reason, upstream response, recorded-at,
   run id — all mandatory, schema-versioned, UTC-normalised). `partition_day_statuses` classifies each
   day `data` / `absent` / `conflict` / `missing`; `missing_partition_days` now reports only `missing`.
   `write_partition` and `write_absence` **refuse each other in both directions** — retraction stays a
   manual admin action. The zero-row refusal is untouched.

3. **Domain isolation — DONE, and it is default-deny.** Every subpackage of `ingest/` or `execution/`
   counts as a domain unless declared shared in `DOMAIN_PARENT_SHARED_SUBPACKAGES` (currently
   `execution/historical_writer`, `ingest/validation`). A domain added later is policed the day it lands.
   Relative imports are resolved before matching, and a synthetic two-domain fixture proves the rule
   fires — with one real domain it could otherwise only ever pass vacuously.

#### 0.26.3 Step 4 — the extraction nobody had costed

**`historical_backfill.py` was the blocker, and it was not on any list.** It owned four value types —
`AnalysisGridCell`, `HistoricalBackfillWindow`, `HistoricalSignalObservation`, `HistoricalCoverageAudit` —
that **five sibling domains** (CAMS, GloFAS, CEMS, AgERA5, ERA5) imported from it. Moving NASA into
`execution/weather_observations/` would therefore have made all five import the weather domain: the exact
cross-domain coupling step 3's new rule forbids, created by step 4 itself.

**Resolution: the shared half moves down, the dependents never move sideways.** The four types now live at
`execution/backfill_types.py` (`00b1fc1`). `coverage_fill` and `plan_continuation` import shared types and
NASA specifics from separate modules, so the domain dependency is visible in the import block rather than
hidden behind a re-export. **Generalise this before each of the remaining ten moves: grep what the target
module exports to its siblings first — the move is blocked until the shared half leaves.**

**Landed:** `execution/weather_observations/` holding `nasa_power.py` (was `historical_backfill.py`) and
`era5_land.py` (was `historical_open_meteo.py`), 17 files' imports rewritten, plus `AGENTS.md`.

**Deliberately still at root:** `historical_writer/nasa.py` and `historical_writer/open_meteo.py`. That
package is already organised per source over shared internals (`_shared`, `_results`, `_release_sets`) used
by CAMS, GloFAS and USDM; splitting it would either duplicate them or export private modules across
packages. Revisit when a second domain moves and the shared surface is measured.

**NOT done — step 4 is half complete.** `ingest/weather_observations/` does not exist yet. `ingest/open_meteo.py`
mixes the current-conditions `WEATHER_LAYER` producer with Open-Meteo client primitives that
`open_meteo_air_quality`, `open_meteo_ensemble` and `open_meteo_flood` all use — **the same shared/domain
entanglement as `historical_backfill.py`, and it needs the same extraction first.** Note §0.24.2's trap 3:
that producer writes `geo.features`, not `agri.signal_observation`, so confirm which of the two
`weather-observations` producers any given change is aimed at.

#### 0.26.4 A runbook claim that does not survive checking

**§0.24.9 and §0.25.4 both say wave 1 ended `mypy --strict` clean. It does not, over the full `src` tree.**
`jobs/matview_refresh.py:667` carries two errors (`no-any-return`, `call-overload`), last touched in
`e71e1cd` and untouched by any commit this session — so the wave-1 sweep was run at a narrower scope than
the claim implies. **Verified state after this session: 3,175 passed · 110 skipped · `ruff` clean over
`src tests plans` · `mypy --strict src` = 2 pre-existing errors in that one file.** Fix or waive it
explicitly rather than letting the next session rediscover it.

#### 0.26.5 THE WAVE 2 → 3 GATE IS PASSED — real partitions, read back, 2026-08-22

Four real signal-plane partitions are in the bucket, written from production and **downloaded back
out and validated**, not merely written. The gate was "a file a reader opened," and this is it.

| day | rows | bytes | B/row | support keys |
|---|---|---|---|---|
| 2026-08-01 | **15,730** | 83,271 | 5.29 | `era5-land-0.1deg` 11,760 + `surface` 3,970 |
| 2026-08-02 | **15,730** | 83,013 | 5.28 | `era5-land-0.1deg` 11,760 + `surface` 3,970 |
| 2026-08-05 | 3,970 | 26,126 | 6.58 | `surface` only |
| 2026-08-06 | 3,970 | 26,163 | 6.59 | `surface` only |

**Three independent corroborations that the exporter is correct**, each a number it was not given:

1. **15,730 grain rows/day is EXACTLY §0.22.6's measured July figure**, arrived at by a different
   code path against different days. That is the projection's own input reproduced.
2. **397 cells × 10 signals = 3,970 exactly** — a lane contracted for **11** NASA signals.
   `surface_shortwave_radiation` is missing, which is §0.22.8's radiation gap **rediscovered
   independently**. It remains unexplained and is still the designated first validation case.
3. **1,470 ERA5-Land cells × 8 signals = 11,760**, matching the lane contract's measured
   1,470-of-1,568 (the 98 absent cells are Pacific-edge water and are not a gap).
   ERA5-Land stops after 2026-08-02 while NASA continues to 08-06 — exactly what the 9-day vs
   5-day publication lags predict.

Verified on readback: schema equals the registry, **0 duplicate grain keys**, rows sorted to the
grain, and `partition_day_statuses` over the real listing returns
`08-01 data · 08-02 data · 08-03 missing · 08-04 missing · 08-05 data · 08-06 data`. The absence
machinery, the lister and the writer are proven against the live bucket, not a fake.

**The exporter is `pipeline/lanes/signal.py` + `sql/pipeline/signal_plane_day_export.sql`.** Its
governed CTE is transcribed **verbatim** from `drizzle/0029:534-614`, the defining query of the
dropped `geo.mv_signal_cell_daily`, with only two deliberate differences (day/cell scoping, and the
three columns §0.22.3 measured equal to `normalized_value`). **Exporting a different population
than that rollup served would silently change what every downstream reader sees** — that is why it
is a transcription and not a new query, and why the next ten lanes copy its shape.

**Measured cost:** 8.1 s for 1,965 cells on a NASA-only day, 18.0 s cold / 3.0 s warm on a
both-producer day, batching 250 cells per statement.

#### 0.26.6 ALL TEN REMAINING LANES LANDED — 2026-08-22, ten concurrent agents

Eleven streams now register and autoload. `interventions` is deliberately absent — its contract
establishes that lane stays in Postgres, consistent with §0.26.1.

| stream | cols | sort key | geom |
|---|---|---|---|
| `signal` | 10 | support_key, signal_name, normalized_unit, cell_id, observed_day | |
| `weather-observations` | 13 | latitude, longitude, observed_at | |
| `vegetation` | 10 | cell_id, observed_day | |
| `fire-detections` | 8 | cell_longitude, cell_latitude, observed_day | |
| `sensors` | 11 | sensor_id, observed_day, measurement_name | |
| `water-gauges` | 14 | site_number, observed_at | |
| `fire-perimeters` | 17 | observed_day, unique_fire_identifier | WKB |
| `burn-severity` | 23 | observed_day, fire_id | WKB |
| `soil-survey` | 15 | mupolygonkey | WKB |
| `watersheds` | 12 | huc12 | WKB |
| `evacuation-zones` | 23 | snapshot_day, natural_key | WKB |

**A SECOND real partition set is written and read back — the geometry shape, not just the scalar
one.** `watersheds` release 2026-08-07: **10 parts, 9,396 rows, 162,626,113 B**, 374.6 s over the
public proxy. On readback: schema matches the registry, rows sorted by `huc12`, WKB header
`0103000000` (little-endian Polygon), and **`partition_day_statuses` reads all ten parts as ONE
present day** — the part-N spillage design and gap detection agree, which was the open question
the multi-part design raised.

**THE SIZE PICTURE JUST CHANGED, AND IT INVERTS §0.22.6's HEADLINE.** `watersheds` alone is
**162 MB at 17.3 KB/row** against the entire 24.5M-row signal plane's projected **~35 MB**. The
~180× reduction that justified the Parquet path was measured on a plane with **no geometry**;
it does not transfer to the five WKB lanes, and nothing has yet costed `soil-survey` (the lane
doc measures the PNW envelope alone at **1,507,623 delineations** — roughly 160× watersheds' row
count). **Do not carry the ~35 MB figure into a whole-warehouse storage estimate.** Cost the
geometry lanes before sizing the bucket or promising a Railway volume reduction.

**Where the lanes deviated from the template, with evidence — these are the useful findings:**
- **`geo.features` lanes need no cell-batching.** `ix_features_layer_observation_day` on
  `(layer_id, geo.feature_observation_day(properties))` already exists in production (verified
  directly). `signal`'s `CELL_BATCH_SIZE` loop exists *only* because `agri.signal_observation` is
  an 11 GB heap with no index leading on `observed_at`. Copying it would have been cargo-cult.
- **`sensors` exports all 16 captured measurement fields**, not the 4 served, on a **tall** grain
  rather than ~48 mostly-null wide columns — because NWS serves a rolling ~6-day window, so a
  field not captured is gone permanently within a week. **This answers §0.25.6's sensors question.**
- **`fire-perimeters` scopes on `geo.feature_observation_day(properties)`, not the job's run
  date** — the naive alternative replays one snapshot every day it runs, forever.
- **`evacuation-zones` refused the tile layer's `COALESCE(observedAt, updatedAt)` fallback**,
  which would launder PlantGeo's polling clock into a fabricated observation time.
- **`watersheds` separated two dates that both look like "the release day"** —
  `geo.features.created_at` (one load day, 9,396 rows) vs WBD's per-basin `loaddate` (some 2013).
- **`burn-severity` found MTBS bypasses `agri.source_release`/`data_source` entirely**, so
  `allowed_client_exposure` cannot be joined and is a literal `FALSE` — which **contradicts
  `geo.layers.is_public = true` for that layer**. Pre-existing discrepancy, not fixed, flagged.
- **`fire-detections` chose a cell-day aggregate** (0.005°, the finest existing tile rollup)
  because per-hotspot lat/lon cannot satisfy the identical-grain rule. It also notes VIIRS and
  MODIS FRP differ by ~an order of magnitude for the same fire and are **not** split by
  instrument — an inherited limitation, documented rather than silently engineered around.

**A defect the fan-out caught in the just-committed template:** `signal.py` annotated
`cell_ids: Sequence[int]` while both `agri.spatial_cell.id` and `agri.signal_observation.cell_id`
are **`uuid`**. The exports were correct (asyncpg returns `UUID`, passed straight through) but the
annotation lied while nine lanes copied it. Fixed in `f2e668a`.

#### 0.26.7 SECOND ENFORCEMENT GAP, FOUND AND CLOSED

§0.25.2's rule — added earlier this session — covers **subpackages** of `ingest/` and `execution/`.
**Lanes are not subpackages.** They are flat modules in `pipeline/lanes/` and `warehouse/schemas/`,
so `layer-lanes.md` §1's "a lane never imports another lane" was **still unenforced** for the very
directories the ten lanes landed in. `test_lanes_do_not_import_each_other` now covers
`pipeline/lanes`, `warehouse/schemas`, `method/monte_carlo`, `pipeline/validation`.

**Closing it exposed a hole in the checker itself.** `from . import sibling` resolved to the
*package* and discarded the imported name, so that form passed silently — found **only** because
the synthetic fixture asserts the rule fires. The resolver now also yields `module.name` per
alias, and violations dedupe by `(file, line)`. **The lesson generalises: a rule with no failing
fixture is a rule you have not tested, and both isolation rules added this session needed one to
find a real bug.** The ten real lanes cross nothing today — verified.

#### 0.26.8 A documentation gap worth naming

`docs/lanes/weather-observations.md` describes the governed NASA POWER / ERA5-Land archive across
all seven of its sections — but that archive is the **`signal` stream**, already exported. The
producer the `weather-observations` *lane* needs (`ingest/open_meteo.py`'s `WEATHER_LAYER`
current-conditions poll into `geo.features`) has **no contract content at all**: no declared
cadence, horizon, historical depth, or known-gaps list. The lane was built from the code with the
gap stated rather than an invented contract. **Write that half of the contract before the lane is
scheduled**, or its history horizon and gap detection have nothing to check against.

### 0.38 HANDOFF — session 8 close (2026-08-24), d1 BUILT and DEPLOYED, the drain is what remains

§0.37 holds the detail. This is what a fresh session needs: what is true, what is running, and the
one decision that is genuinely open.

#### 0.38.1 State

| commit | what | verified |
|---|---|---|
| `2eabe6e` | `warehouse/parquet/tiers.py` — the pure z13 -> z9/z5/z0 derivation | smoke-tested by hand; 51 tests |
| `e2c099b` | the area floor scales with its tier | — |
| `8ce71fd` | 13 lane derivations + the 3-lane coordinate enrichment + the `gap_fill` fusion | queries verified against PRODUCTION |
| `3e5027f` | `pipeline/parquet/drain.py` + the `parquet-drain` verb | dry-run against the real bucket |
| `1dc2959` | six real defects a `/code-review high` found, plus the tests it said were missing | gate green |
| `ae63b02` | the cell join moves out of the hot path; the drain gets its own 600 s clock | A/B timed against production |
| `67b9958` | RUNBOOK 0.37 | docs |

**PUSHED AND DEPLOYED.** `plantgeo-ingest-cron` and `plantgeo-main` both redeployed SUCCESS. The
hourly cron now runs the enrichment, the fusion and the restructured SQL.

**Gate: 4,007 passed / 3 skipped**, ruff clean, mypy at its two pre-existing
`matview_refresh.py:657` errors. One real-DB test errors per full run and it is a DIFFERENT one each
time, passing in isolation — a teardown race, see §0.37.9. Do not chase it.

#### 0.38.2 What is PROVEN in production, not merely tested

- **The completion marker works.** 1,041 markers written by ordinary cron ticks (§0.37.11).
- **The four-rung ladder works.** One real `drought` day derived end to end against the live bucket:
  base 5 rows / 6 columns -> z9 410,068 B -> z5 158,514 B -> z0 137,951 B, all three rungs carrying
  their completion marker, verified by re-listing the bucket rather than by return value. The
  monotonic byte fall is the simplification doing its job; the row count holds at 5 because drought
  is simplify-only and all five USDM classes are meant to survive.
- **The enriched queries return real coordinates.** signal at (-116.0, 43.0), vegetation at
  (-119.875, 42.125), sensors 172 rows at (-117.498, 46.268), zero nulls, twelve columns each.

#### 0.38.3 THE OPEN DECISION: the drain is not a one-sitting job, and the estimate that said it was

The full drain was approved on the understanding it would take "likely hours". **The measurement
says otherwise and the owner should re-decide rather than have it started on the old estimate.**

Census as of session close — four lanes are 99.5% of the work:

| lane | missing days |
|---|---|
| `fire-detections` | 9,202 |
| `burn-severity` | 1,724 |
| `signal` | 1,337 |
| `vegetation` | 1,231 |
| everything else | 67 |
| **total** | **13,561** |

The cost driver is `signal`: one 250-cell batch of a cold day measured **135 s**, and `signal.py`
walks 1,965 cells in `CELL_BATCH_SIZE = 250` batches — roughly eight statements, so a cold signal day
is on the order of ten to twenty minutes. At that rate 1,337 days is not hours.

Three honest options, none of them yet taken:

1. **Drain the cheap lanes now and leave the four expensive ones running in the background.**
   `fire-perimeters` (67 days) finishes immediately; the rest streams progress and resumes freely,
   because THE BUCKET IS THE CHECKPOINT — re-running the verb skips every day that carries its
   completion marker.
2. **Fix `signal`'s query first.** It was ALREADY over the cron's 120 s ceiling before the
   enrichment (§0.37.12), which is very likely why it has 1,337 missing days at all: the cron has
   been cancelling them tick after tick. An `EXPLAIN ANALYZE` has NOT been run — that is the
   cheapest next diagnostic and nobody has spent it yet.
3. **Accept the runtime** and let it run for days, resuming as needed.

#### 0.38.4 THE PURGE STILL HAS NOT RUN, and the drain is partly blocked on it

`scripts/purge_parquet_layout.py --confirm` has still never been executed. Dry run at session close:
**2,795 objects selected**, plus 2,274 unparsable legacy objects that are equally condemned
(RUNBOOK 0.32.4) but need `--include-unparsable` to go.

It matters more than it looks. The ~1,043 days that already carry a base rung and a completion
marker read as COVERED, so **the drain skips them** — and every one was written at the old schema
with no coarse rungs and no coordinate columns. Without the purge those days stay permanently
un-tiered and permanently ten-column, because nothing will ever revisit a day that says it finished.

The cheaper alternative nobody has costed: retract just those completion markers, leaving the parts,
so the census calls the days `incomplete` and the drain re-exports them. `retract_partition_tier`
(added this session) already does exactly this for one rung.

#### 0.38.7 RUNNING THE DRAIN: scope it with `--layer`, or it looks dead for eight minutes

**The drain resolves EVERY static lane's source watermark before it walks a single day.**
`run_drain` calls `resolve_lane_watermarks` first, because the census cannot classify a reference
set without knowing what version its source is on. There are four static lanes, each watermark
query is bounded at the ordinary 120 s, and `soil-survey`'s is slow -- so an unscoped drain can
spend ~8 minutes in setup having written nothing at all.

That is not a hang, and it cost real time to work out twice. A sample run given a 420 s time budget
spent its ENTIRE budget in that phase and produced an empty log, which reads exactly like a dead
job.

**All four lanes with a backlog are non-static, so scope the run and the phase disappears:**

```
uv run agri-cli parquet-drain   --layer fire-detections --layer burn-severity --layer signal --layer vegetation   --days-per-lane-turn 50 --progress
```

Measured with that scoping: `fire-detections` drains at roughly **3-4 seconds per day** including
all three derived rungs -- 35 days in about two minutes -- which puts its 9,202 days in the range of
hours, not the days a cold `signal` batch had suggested. `burn-severity` is faster still because
most of its days are governed absences.

A `contended` line in the progress stream is HEALTHY: it is the hourly cron and the drain meeting on
one lane-day, the advisory lock refusing the second writer, and the drain putting the day back for a
later turn. RUNBOOK 0.33.3 B plans for exactly this overlap.

#### 0.38.5 Assumptions, highest reversal cost first

- **`min_area_tier_squares` unset on every geometry lane** · to reverse: setting it to 1.0 EMPTIES
  z0 for the whole PNW (§0.37.3). If a future lane needs it, set it per lane, never globally.
- **`wind_direction_deg` and the water-gauge hazard fields are nulled at coarse rungs** · to
  reverse: needs a vector mean and an ordered severity enum respectively, neither of which the
  closed aggregate vocabulary can express.
- **The base rung's NOT NULL guard is a DECLARATION now, not a schema constraint**
  (`TierDerivation.base_non_null_columns`) · to reverse: it is enforced only in `write_partition`
  and only at z13, so a producer bypassing that function bypasses the check.
- **The coarse-rung derivation reads the whole base day into memory** · to reverse: `soil-survey`'s
  1.5M-delineation universe would be gigabytes. It refuses loudly at `MAX_DERIVATION_ROWS` rather
  than swapping, and soil-survey writes nothing today anyway (key cap), so this has never been hit.

#### 0.38.6 Continuation plan, in order

1. **Take the §0.38.3 decision.** It gates everything below and nothing above.
2. **`EXPLAIN ANALYZE` the signal export** against production — the cheapest unspent diagnostic, and
   the one that decides whether option 2 is even available.
3. **Purge** (or retract markers — §0.38.4), then drain. Order matters; the drain skips covered days.
4. **Run the drain**, streaming progress. Resume by re-running it; there is no checkpoint file.
5. **Only then stop the cron.** Unchanged and still load-bearing: build -> run -> THEN stop.
   Stopping first freezes the warehouse with nothing replacing it.
6. **d3 serving** — `interface/http` is still an EMPTY STUB and the twelve planes still have ZERO
   callers, so none of this is visible on the map yet. That is expected, not a regression.


### 0.37 SESSION 8 (2026-08-24) — the zoom ladder has rungs, and what a review found on them

d1's build half. The map was empty above z13 BY DESIGN since the zoom axis shipped (§0.33.2 hazard
1); it is not any more, for every day written from here on. §0.36 is the state this started from.

#### 0.37.1 THE DEPLOY HAPPENED — `3ab85a6` is in production

§0.36.8 step 1 is closed. `main` was pushed and both `plantgeo-main` and `plantgeo-ingest-cron`
redeployed SUCCESS at 21:58 on 2026-08-23. **The completion marker now runs for real**: the claim
§0.36.2 flagged as "believed-correct but never verified by a cron tick" is being tested every hour
from that moment. It also unblocks the purge, which was pointless before it.

#### 0.37.2 FOUR OWNER DECISIONS, taken at the top of the session

| # | decision | why it was a genuine fork |
|---|---|---|
| 1 | **Push now** | the mechanism had never run in production, and pushing is the only thing that tests it |
| 2 | **ENRICH the three coordinate-less lanes** rather than passthrough or a lane-aware resolver | `signal`, `vegetation` and `sensors` carry no position at all, so they could not be re-floored. The alternatives were 4x storage on the biggest lanes, or changing `serving_zoom_tier`'s signature for all twelve planes |
| 3 | **Fuse tier derivation into `gap_fill` too**, not the drain alone | otherwise every day written AFTER the drain is invisible above z13 until d2 |
| 4 | **Hierarchical dissolve where a lane has a real hierarchy** | only `watersheds` does. `agri.spatial_cell.parent_cell_id` LOOKS like a second one and is populated on **0 of 1,965 rows** — measured 2026-08-23. Do not re-adopt it on the strength of the column existing |

#### 0.37.3 The resolution ladder, and the number that was wrong twice

`TIER_RESOLUTION_DEGREES = {9: 0.01, 5: 0.2, 0: 5.0}` — four web-map pixels at each tier's OWN zoom,
rounded to a clean decimal. A tier answers a SPAN (`zoom_tier_span`), so a request at the top of a
span is over-generalised; that is the stated price of one uniform ladder, not an oversight.

**`min_area_tier_squares` is UNSET on every geometry lane, and the first two attempts were both
wrong.** A fixed absolute area is wrong at three resolutions at once. Scaling it to the tier fixed
that and introduced something worse: at `1.0`, one z0 grid square is 5.0 x 5.0 = **25 square
degrees**, while the whole PNW universe is roughly 10 x 10 — so every feature in every geometry lane
dropped and z0 (which answers z0–z4, i.e. continent zoom) went blank. Simplification alone already
delivers the byte win. The knob stays for a future lane whose features are genuinely global.

#### 0.37.4 THE ENRICHMENT — three lanes gained a position, verified against production

`signal` and `vegetation` project `ST_X/ST_Y(agri.spatial_cell.centroid)` as `cell_longitude` /
`cell_latitude`, NOT NULL (centroid is populated on 1,965 of 1,965 rows). `sensors` projects
`ST_X/ST_Y(ST_Centroid(geo.features.geom))` as `station_longitude` / `station_latitude`, NULLABLE —
a row pushed through the older HTTP route may carry no geometry.

**Columns are APPENDED, never inserted**, so no existing reader's column order moves. This is safe
only because the purge and the drain rewrite every object anyway. On any other day it is a migration.

Run against PRODUCTION on 2026-08-24, real rows, zero null coordinates:
`signal` 11 rows / 12 columns at (-116.0, 43.0) · `vegetation` at (-119.875, 42.125) ·
`sensors` 172 rows at (-117.498, 46.268).

Two stale claims corrected in passing: the signal export's header said its `cell_ids` parameter was
`bigint[]` — **it is `uuid[]`**, and the foreign key proves it — and a comment claimed the new
coordinates joined the GROUP BY when they ride the lane's own newest-release `array_agg` instead
(deliberately: grouping on a PostGIS geometry compares it structurally).

#### 0.37.5 ORDERING: coarse rungs are written BEFORE the base marker, and it is load-bearing

Only the base tier is censused — `build_gap_census` walks `GAP_FILL_ZOOM_TIER` and nothing else — so
**the base marker is the only signal that can bring a day back for another attempt.** Mark it first
and then fail to derive, and the day is stranded base-complete: never revisited, permanently empty
above z13, **on a green tick**. Deriving first makes the identical failure self-healing, because an
unmarked day is simply re-exported.

`tests/parquet/test_derivation_and_drain.py::test_the_base_marker_is_written_after_every_coarse_rung`
pins it by intercepting every marker write and asserting the base one lands last. Do not delete it.

#### 0.37.6 `duckdb` spatial LOADS on this host — `planes/soil_survey.py:13` is WRONG

That header states the extension "is not installable offline in this environment (`INSTALL spatial`
requires network)", which is why that lane hand-rolls a WKB point-in-polygon reader in `struct`.
**Tested 2026-08-23: `LOAD spatial` succeeds and `ST_SimplifyPreserveTopology` works.** The
hand-rolled reader remains a defensible choice for a SERVING path that must not depend on an
extension — but it is not a reason for a batch path to avoid one, and the stated reason is false.

#### 0.37.7 THE REVIEW — ten findings, six of them real defects

`/code-review high` over `e2c099b..HEAD`. **Every review pass on this workstream has returned
changes-required; this is the fourth.** All fixed:

| finding | why it mattered |
|---|---|
| `precipitation_mm` took `sum` | depth is not additive across the stations reporting it, and these rows are per-station instantaneous polls — it double-counted along BOTH axes at once. Now `mean` |
| a `contended` day requeued forever | the drain fills oldest-first and the cron newest-first, so **every lane's endgame is precisely the day the cron holds**. With the default `time_budget_seconds=None` that is an infinite loop that reports progress. Capped at `MAX_CONTENDED_RETRIES_PER_DAY = 5` |
| an emptied rung skipped its prune | a rung deriving to zero rows `continue`d past the only code that prunes or re-marks it, so an earlier derivation's parts AND its completion marker survived — serving rows the base day no longer held, from a rung still claiming to be finished |
| `all`/`any` ignored nulls | Polars defaults to `ignore_nulls=True`, so an all-null `allowed_client_exposure` folded to **True — an exposure gate failing OPEN**. DuckDB's `bool_and` returns NULL. The same engine divergence as the `sum` bug, in the one place where the wrong answer publishes data rather than miscounting it |
| `water-gauges` keyed on `site_number` + `observed_at` | both unique per base row, so every group was a singleton and z9/z5/z0 were verbatim copies of z13 at four times the storage |
| eight fields relaxed to nullable | that silently removed the guard which made a NULL `sensor_id` fail the BASE export loudly. `TierDerivation.base_non_null_columns` names them back and `write_partition` enforces it at z13 only |

Plus two smaller ones: `ST_X` on the unconstrained `geometry(GEOMETRY,4326)` column would abort a
whole day's export on one non-point row (now `ST_Centroid` first), and a field comment claiming the
sensors coordinates were "the cell ORIGIN's centroid" said the exact opposite of the SQL producing
them.

A note on `water-gauges`: `condition` and `trend` are now NULLED at coarse rungs rather than taking
one arbitrary gauge's value. They are hazard fields, and `first` would report "normal" for a cell in
which another gauge sits at flood stage. There is no aggregate in the vocabulary meaning "the most
severe of these" — the values are free text, not an ordered enum — so the honest coarse answer is no
answer.

#### 0.37.8 What the MISSION got wrong, and the lesson that generalises

`.agentgraph/runs/d1-lane-tiers/` — 5 agents, 0 errors, ~$3.95, 16 files. All five reported clean.

One agent nonetheless imported `warehouse.parquet.tier_derivation` (**no such module**), passed
`method=` where the field is `strategy=`, and **never called `register_tier_derivation` at all** —
consistently, across all five of its files. It had the entire `tiers.py` as a reference. The brief
was not the problem; the absence of an interpreter was.

**An agent that cannot run code cannot check an API it was handed.** d0's lesson was "budget a
host-side FIXTURE pass". d1 adds: budget a host-side **API-conformance** pass as well. The thing
that found all of it was ~40 lines of throwaway Python that imported every lane, derived every rung
against a synthetic day, and cast the result back to the storage contract. It is now
`test_every_lane_derives_a_real_row_at_every_rung`. Write that script BEFORE reading the diff.

#### 0.37.9 Gate, and one flake worth not chasing

**4,007 passed / 3 skipped** with the real-DB env from §0.36.7 set (baseline was 3,948/3; 60 tests
added). `ruff` clean across `src/ tests/ scripts/`; `mypy` at its two pre-existing
`matview_refresh.py:657` errors.

**One real-DB test ERRORS per full run, and it is a DIFFERENT test each run** —
`test_covariate_wind_lane` and `test_vegetation_ndvi_release_materialisation` have each done it
once. Both PASS in isolation. It is a teardown race in the shared sweep database, unrelated to this
work; do not read it as a regression, and do not spend a session chasing it without first confirming
it reproduces on a clean checkout.

#### 0.37.10 Still open after this session

- **The drain has not been RUN.** Built, reviewed, and exercised against the real bucket in
  `--dry-run` only: **13,565 missing lane-days, `fire-detections` 9,203 of them (68%)** — closely
  matching the 13,037 / 69% §0.33.3 predicted.
- **The purge has not been run** (`--confirm` still never used). It must precede the drain, or the
  drain SKIPS days that read as covered because objects written at the OLD schema sit there with no
  coarse rungs.
- **`soil-survey` remains blocked by the 200,000-key cap** and drains nothing regardless.
- **The coarse-rung memory ceiling is untested at scale.** `derive_and_write_day_tiers` reads the
  whole base day back at once; `soil-survey`'s 1.5M-delineation universe would be gigabytes.
  `MAX_DERIVATION_ROWS` refuses loudly rather than swapping, but a lane that trips it needs a
  batched fold — correct only for associative aggregates, NEVER for `mean`.
- **`wind_direction_deg` is nulled at every coarse rung.** An honest coarse bearing needs a vector
  mean (atan2 of mean sine and cosine, speed-weighted) that the closed vocabulary cannot express per
  column. Reversible; nobody has asked for it.

#### 0.37.11 THE COMPLETION MARKER IS NOW VERIFIED IN PRODUCTION — §0.36.2's open claim is closed

§0.36.2 recorded the honest caveat that "nothing has ever written a `_complete.json` in production;
the whole mechanism is proven by tests and one dry-run listing, never by a real cron tick."

**It has now. Measured against the live bucket on 2026-08-24:**

```
objects in bucket:   5,069
completion markers:  1,041      all at zoom=13
oldest marker:       2026-08-24T05:29:48Z
newest marker:       2026-08-24T11:48:27Z
by layer:  drought 209 · fire-detections 222 · signal 222 · vegetation 205 · water-gauges 91 ·
           fire-perimeters 44 · sensors 25 · weather-observations 20 · calendar 1 ·
           evacuation-zones 1 · watersheds 1
```

Every one was written by an ordinary hourly tick of the deployed cron, across eleven of the twelve
layers, in the hours after the `3ab85a6` deploy. The mechanism works in production, not merely in
tests. `soil-survey` is the one layer with no marker, which is correct and expected: it is still
blocked by the 200,000-key cap and writes nothing at all (memory
`plantgeo-soil-survey-blocked-by-key-cap`).

All 1,041 sit at `zoom=13` because the coarse rungs did not exist yet when they were written. The
drain rewrites them.

#### 0.37.12 `signal` WAS ALREADY OVER THE CRON'S CEILING — measured, and it is not the enrichment

Suspecting the coordinate enrichment had made `signal` slower, the pre-enrichment query was fetched
out of git and timed against production beside the new one, same day, same 250-cell batch:

| query | time | rows | columns |
|---|---|---|---|
| `signal` BEFORE the enrichment (from `e2c099b`) | **160.0 s** | 2,750 | 10 |
| `signal` AFTER, with the join moved out of the hot path | **135.6 s** | 2,750 | 12 + coordinates |

**Both exceed the cron's 120 s statement timeout, and the older one is the slower.** The enrichment
did not cause this. State the caveat with the number: BEFORE ran first against a cold cache and
AFTER ran second against a warm one, so the 160 -> 136 improvement is confounded and must NOT be
quoted as a speed-up. What the measurement does establish is the thing that mattered: the new query
is not materially worse, and the ceiling was already being hit without it.

**This is very likely why `signal` has 1,338 missing days.** The cron has been cancelling them at
120 s, tick after tick, and reporting the lane as `raised` rather than as a lane that cannot fit.
The drain's own 600 s budget (`DRAIN_STATEMENT_TIMEOUT_SECONDS`) is what lets those days land at
all -- which is the concrete reason the two jobs needed two timeouts rather than one.

A caution for whoever sizes the drain: one 250-cell batch is ~135 s and `signal.py` walks 1,965
cells in `CELL_BATCH_SIZE = 250` batches, so a single cold `signal` day is roughly eight statements.
Measure the per-lane cost before assuming the whole 13,565-day backlog drains in one sitting.

### 0.36 HANDOFF — session 7 close (2026-08-23), d0 done, d1 is next

§0.35 holds the detail and the reasoning. This section is only what a fresh session needs to start
work: what is true, what is believed, and step 1.

#### 0.36.1 Goal

Cut PlantGeo's map layers from Postgres to a day-partitioned, zoom-partitioned Parquet warehouse read
by DuckDB+Polars. Postgres keeps only community features and becomes a one-time cut-off, not a
source. Map breakage during the transition is accepted. Track:
`conductor/tracks/parquet_duckdb_pivot_20260823/`.

**d0 was this session's slice and it is DONE.** d1 is next.

#### 0.36.2 State

| commit | what | verified |
|---|---|---|
| `3ab85a6` | the completion-marker contract end to end: third object kind, census rule, write protocol, lane-day advisory lock, reader sweep, tests | gate green; reviewed 3× |
| `a044ac1` | `scripts/purge_parquet_layout.py` | dry-run exercised against the real bucket; `--confirm` NEVER run |
| this commit | RUNBOOK §0.35/§0.36, track metadata | docs |

**Gate at handoff: 3,948 passed / 3 skipped** with the real-DB env set (baseline at `f811eb6` was
3,936/3), `ruff` clean across `src/ tests/ scripts/`, `mypy` at its two pre-existing
`matview_refresh.py:657` errors. **Pushed: NO.** Branch `main`, deliberately not pushed — Railway
push-deploys from `main`, and deploying is a separate decision (§0.36.8).

**Review ledger — this is the unusual part of this session, and it is why the work is trustworthy:**

| pass | scope | verdict | what it changed |
|---|---|---|---|
| adversarial #1 (contract/logic) | core mechanism, pre-mission | CHANGES-REQUIRED | found the failed-prune stable-lie, the zero-row lane wedge, the unconditional-clear cost |
| adversarial #2 (concurrency/idempotency) | core mechanism, pre-mission | CHANGES-REQUIRED | found the concurrent-writer truncation, the marker-agrees-with-corruption case, forced the lock |
| verifier (post-sweep) | the finished sweep | CHANGES-REQUIRED | found the `sensors.py` serving leak with an EXECUTED reproduction, and falsified a claim this runbook made |
| mutation tests (host) | the mechanism itself | PASS | deleting the completion rule fails 7 tests across 6 files; deleting the `soil_survey` gate fails its own test |

**Believed-correct but NOT verified end to end:** nothing has ever written a `_complete.json` in
production. The whole mechanism is proven by tests and one dry-run listing, never by a real cron
tick. The first deploy is where that claim gets tested.

#### 0.36.3 Key context a fresh read of the code will not give you

- **The map is empty above z13 and that is BY DESIGN** (§0.33.2 hazard 1). Nothing derives the coarse
  rungs yet — that is d1. It will read as a regression to anyone who does not know.
- **The planes have ZERO callers.** `interface/` is an empty stub. So the §0.35.9 serving gap is real
  but not yet live, and "no caller" is never evidence a plane is dead.
- **Three review passes, three CHANGES-REQUIRED.** The protocol that shipped is not the one first
  written; §0.35.1–0.35.3 record what changed and why. Do not "simplify" any of it back without
  reading those — each one closed a defect that looked fine.
- **The mission's agents cannot run tests** (`EDIT_TOOLS` has no Bash). Failures went 23 → 56 the
  moment it finished, every one a fixture gap. Budget a host-side fixture pass after any mission that
  touches tests. `.agentgraph/runs/INDEX.md` records this.
- **The prebaked partitions in `metadata.json` are now marked `stale`.** d0's real blast radius was
  33 files against 6 predicted, because a third object kind reaches every reader, not just the
  census. Re-grep before trusting any `owns` list.

#### 0.36.4 Decisions taken this session

- **Advisory lock now, in d0, and SESSION-scoped** — `execution/provenance.py::advisory_lock` is
  transaction-scoped and this driver rolls back before the prune that deletes, so that scope would
  have guarded the read and left the destructive half open (§0.35.4).
- **Purge the new-layout objects, do not backfill markers** — a backfill would stamp completion on
  days nothing verified (§0.35.5).
- **Retract at the first part write, not before the attempt** — clearing up front demoted intact
  releases on every unrelated failure (§0.35.1).
- **A failed prune withholds the mark** rather than failing the day (§0.35.2).
- **`missing_partition_days` split back apart** from the driver's union (§0.35.6).
- **The five `root: str` planes are d3's problem, not d0's** — fixing them is a signature change, and
  d3 is where those readers get their first callers anyway (§0.35.9).
- **Committed, not pushed** — owner call at handoff.

#### 0.36.5 Assumptions, highest reversal cost first

- **The `local_source_loader_engine` pool stays at `pool_size=1`** (`db/engine.py:121`) · default
  taken: relied upon, and now documented as a precondition in `postgres_lane_day_lock`'s docstring ·
  to reverse: raising it silently breaks the lock — the unlock lands on a different backend, the
  lane-day stays locked for that connection's life, and every later tick reports `contended` on a
  GREEN tick, because `contended` is deliberately not a failure. **This is the most expensive
  assumption in the session.**
- **The completion marker is trusted by EXISTENCE, never decoded** · default taken: key-match only,
  matching `GovernedAbsence`'s existing test-only decoder · to reverse: a GET per day at census time,
  or delete `from_json_bytes` and `COMPLETION_SCHEMA_VERSION` and say the key is the assertion. Until
  then a zero-byte `_complete.json` promotes a half-written day and a future v2 marker is silently
  accepted (§0.35.10).
- **The cron keeps running until the drain exists** · default taken: left armed · to reverse: trivial,
  but stopping it first freezes the warehouse with nothing replacing it.
- **886 zoom-layout objects will be purged, not migrated** · default taken: measured and left in place
  · to reverse: cheap now, impossible after `--confirm`.

#### 0.36.6 Relevant files

- `foundation/parquet/completion.py` — the payload, and the "why a third kind" argument.
- `foundation/parquet/paths.py` — `partition_day_statuses` is the census rule; the status vocabulary
  (`UNFILLED_`/`COVERED_PARTITION_STATUSES`) is above it, with the trap that `COVERED_` is wrong for a
  resolver.
- `pipeline/parquet/gap_fill.py` — the driver. `postgres_lane_day_lock` carries the pool precondition;
  `_finalize_written_day` is prune-then-mark; `_export_one_day` holds the `blocked` wedge fix.
- `pipeline/parquet/objectstore.py` — `write_partition` retracts at `part_index == 0`. That one line
  is the whole safety property.
- `planes/sensors.py` — the only plane whose serving path consults its own census. The other five are
  §0.35.9.
- `scripts/purge_parquet_layout.py` — run after the deploy, never before.
- `.agentgraph/runs/d0-completion-sweep/` — mission, log, story, transcripts. `resume.py` re-runs only
  the four agents that did not finish.

#### 0.36.7 Environment

- Branch `main`, HEAD `a044ac1` + this docs commit. **Not pushed.** No worktrees, no stashes.
- **Real-DB gate — set BOTH or ~110 tests silently skip and the sweep lies:**
  `AGRI_TEST_DATABASE_URL=postgresql://plantgeo_owner:sweeplocal@127.0.0.1:5442/agri_sweep` and
  `PGBIN="C:\Program Files\PostgreSQL\16\bin"`. Without them: 3,840/110. With: 3,948/3.
- Object store reachable from this machine — the purge dry run listed the real bucket. Credentials
  come from the service's own settings; never inline them.
- Prod DSN lives in the Railway variable `LOCAL_SOURCE_LOADER_DATABASE_URL` on `plantgeo-ingest-cron`.
  Prod times out on unbounded scans — use `EXISTS`/`LIMIT`.
- Cron armed `0 * * * *`, config-as-code in `infra/cron-ingest/railway.json`. Still on pre-marker code.
- **Never run PlantGeo locally** (§ memory `plantgeo-never-run-locally`).

#### 0.36.8 Continuation plan — d1, in order

1. **Decide the deploy question before anything else.** `3ab85a6` is committed and unpushed. The
   marker mechanism has never run in production. Pushing deploys it, after which §0.36.9 step 2
   becomes meaningful; not pushing leaves the cron writing marker-less objects for as long as it runs.
   This is a one-line decision that gates step 2 and nothing else — d1 can be built either way.
2. **Only after a deploy: run the purge.** `uv run python scripts/purge_parquet_layout.py` to see the
   count, then `--confirm`. Before the deploy it is pointless (§0.35.5).
3. **Build `warehouse/parquet/tiers.py`** — the pure derivation, z13 → z9/z5/z0, in Polars/DuckDB from
   the base Parquet, never from the Postgres matviews (§0.32.2 decision 2). Keep it a pure transform
   and test it independently; the fusion belongs in the driver, not the transform.
4. **Build `pipeline/parquet/drain.py`** — the bulk Postgres → Parquet pass, fused with step 3 so each
   drained day writes its base tier and immediately derives the coarse rungs from what is already in
   memory (§0.34.2). It writes the NEW layout directly. 13,037 lane-days remain, **69% of it
   `fire-detections` alone**, whose 2000-11-02 floor is real.
   **It must take the lane-day lock** (`postgres_lane_day_lock`) — §0.33.3 B has it running
   concurrently with the cron by design, which is exactly the interleaving §0.35.4 closes.
5. **Run the drain, then stop the cron.** Order matters and is unchanged: build → run → THEN stop.
   Stopping first freezes the warehouse with nothing replacing it.
6. **One sweep at the end**, with the real-DB env from §0.36.7 set.

#### 0.36.9 Open questions

- **Does the marker get decoded, or is the key the whole assertion?** Trigger: d3, when serving first
  reads a marker — or sooner if anyone wants `part_count` to skip orphans. §0.35.10.
- **Do `soil-field` and `climate-field` land as d5, or earlier?** They block 12 slider surfaces between
  them and are independent of d1/d3. Trigger: whenever map coverage outranks map resolution. §0.35.7.
- **Does `burn_severity` answering a conflict day as a governed absence stand?** Four readers disagree
  about conflict today. Trigger: d3, when one answer has to be picked. §0.35.10.

### 0.35 SESSION 7 (2026-08-23) — d0's mechanism survived review twice, and a second missing lane

The completion marker of §0.34.1 is built. It was reviewed adversarially by two independent passes
before anything was allowed to build on it, and BOTH returned changes-required. What shipped is not
what was first written, and the differences are the point of this section.

#### 0.35.1 THE PROTOCOL CHANGED: retract at the first part write, never before the attempt

The first draft cleared the completion marker in the driver, before the adapter ran. That is wrong
in a direction nobody had considered: **every failed attempt stripped the completion claim off a day
whose parts were an intact, previously-marked release.** A statement timeout, a transient database
error, a source that now returns zero rows — each one silently demoted a good day from `data` to
`incomplete`, while nothing on disk had got worse. Once serving consults completion (d3), that is a
healthy day vanishing from the API because an unrelated export attempt failed.

**`write_partition` now retracts the marker as it uploads `part-0`**, after the empty-row and
governed-absence refusals. A day nobody overwrote keeps its claim. The safety property is unchanged:
parts are still never uploaded under an earlier export's marker.

Order now: `part-0 retracts` → parts → prune → mark.

#### 0.35.2 A FAILED PRUNE WITHHOLDS THE MARK

§0.34 inherited "a failed prune must never fail the export" from `3b7ecfb`. Correct then, incomplete
now: marking a day whose surplus parts survived publishes a completion claim over a two-generation
mixture. Worse, it was a **regression from self-healing to stable** — before the marker, an unpruned
orphan dragged `oldest_export_instant` back and pinned the lane `stale`, forcing a re-export; after
it, a series day read `data` and was never re-censused.

The rows are still never lost and the day still counts as `written`. It simply is not marked, so it
stays `incomplete` and the next tick repairs it.

#### 0.35.3 TWO NEW DAY OUTCOMES, because "failed" was hiding two different operator actions

- **`blocked`** — the day needs an ADMIN and the lane KEEPS DRAINING. This is the zero-rows-over-
  existing-parts case: `write_absence` refuses a day that still holds data, so the day can be
  neither written nor governed. Reported as `raised` it stopped the lane on its NEWEST day and
  starved every older gap behind it, **every tick, forever** — a wedge that becomes reachable
  precisely because incomplete days are now re-exported, and one that §0.32.1's forward path
  (deprecating each lane's Postgres source) manufactures deliberately. `weather-observations` holds
  21 days in Postgres against a longer Parquet history and is the named example.
- **`contended`** — another run holds the lane-day's lock. NOT a failure, not in
  `FAILING_LANE_OUTCOMES`, counted in neither `written` nor `absent`.

#### 0.35.4 OWNER DECISION: take the lane-day lock now, in d0

Both reviews rated the concurrent-writer hazard critical. Two drivers on one lane-day can interleave
so the slower one's prune deletes parts the faster one just wrote, and then stamps a completion
marker whose `part_count` matches the truncated remainder **exactly** — the bucket and its receipt
agreeing on a population that lost rows, which no later census or audit can detect. `parquet-gap-fill`
took no lease. The prune moving from 3 static lanes to all 13 widened it.

Not hypothetical: §0.33.3 B has the bulk drain running CONCURRENTLY with this driver by design
("build drain → run drain → THEN stop the cron").

**It is a SESSION-scoped advisory lock, and that is load-bearing.** `execution/provenance.py::advisory_lock`
takes `pg_advisory_xact_lock`, which the very next `session.rollback()` releases — and this driver
rolls back immediately after every export, BEFORE the prune that deletes and the mark that publishes.
A transaction lock would have guarded the read and left the destructive half open. Do not "simplify"
it to the shared helper.

It is injected (`lane_day_lock=`), like `monotonic` and `now` already are, so testing the driver does
not oblige every fake session to answer `pg_try_advisory_lock`. `unlocked_lane_day` is the test seam.

#### 0.35.5 OWNER DECISION: purge the new-layout objects, do not backfill markers

Every day already in the zoom layout has no marker and classifies `incomplete` on deploy. A backfill
verb was rejected: it would stamp completion on days nothing verified, which is the one claim this
marker exists to make trustworthy. §0.32.4 already decided the existing objects are DISCARDED, not
migrated, so backfilling markers onto objects slated for deletion is wasted work.

**Purge everything written under the zoom layout, let the drain rewrite it.** The wedge fix (§0.35.3)
is mandatory regardless — it is not a deploy-time concern but a permanent property of any lane whose
Postgres window is shorter than its Parquet history.

**MEASURED 2026-08-23, and the order matters.** `scripts/purge_parquet_layout.py` (dry run is the
default; `--confirm` is the only way past it) reports the bucket holds:

| kind | count |
|---|---|
| part files (zoom layout) | 654 |
| governed-absence markers (zoom layout) | 232 |
| completion markers | **0** |
| pre-zoom legacy, unparsable | 2,274 |

886 objects would be purged. The 2,274 legacy objects need `--include-unparsable` and are equally
condemned by §0.32.4, but deleting a key the code cannot name is a blunter act so the script asks
separately. Zero completion markers confirms nothing has written one yet.

**PURGE AFTER THE DEPLOY, NEVER BEFORE.** The cron is still running the pre-marker code, so a purge
today is undone within the hour by the same lanes writing more marker-less objects. The sequence is:
deploy → purge → drain. Purging first buys nothing and destroys 886 objects that would have been
rewritten anyway.

#### 0.35.6 `missing_partition_days` was split back apart

It had been widened to mean missing ∪ incomplete. That turned two validators' operator-facing
findings into false statements — `vegetation.py` emitting "has no partition or absence marker" for a
day that demonstrably has partitions. `missing_partition_days` means strictly `missing` again; the
driver's union is `unfilled_partition_days`. Reports for humans keep the two apart.

Alongside it, the status vocabulary is now named rather than spelled as negations:
`PARTITION_DAY_STATUSES`, `COVERED_PARTITION_STATUSES` (`data`+`absent`, deliberately excluding
`conflict`), `UNFILLED_PARTITION_STATUSES`. **A reader written as `status != "missing"` silently
accepts whatever member is added next** — which is exactly what `incomplete` did to four readers the
day it landed.

#### 0.35.7 `climate-field` IS A SECOND MISSING LANE — §0.32.5 named only one

The §0.34.1 audit ("check if new ones need to be added") found `soil-field` is not alone.
**`climate-field` blocks 9 slider surfaces to `soil-field`'s 3.** Verified: `drizzle/0020_climate_field.sql`
defines `geo.climate_field_observation` over `agri.signal_observation`; `getPublishedClimateField`
is live in `environmental-read-model.ts`; neither slug appears in `lane_registry.py`.

It is **not** zoom-tiered — there is no `CLIMATE_FIELD_TIERS` to match `soil-field`'s
`SOIL_FIELD_TIERS`, so it needs geometry but not the full ladder, making it the SIMPLER of the two.
§0.32.5 missed it because that research was hunting zoom-tiered weather aggregation specifically.

Structural note for whoever builds them: the `signal` lane already exports the correct rows for both
— it just carries `cell_id` with no cell geometry. These are "the signal export needs a
geometry-carrying sibling", not "build from nothing", and the Postgres views at `drizzle/0016`,
`0019` and `0020` already encode exactly which governed rows qualify.

Everything else that looked incomplete is already decided: `interventions` stays in Postgres
(§0.26.1), GloFAS/CAMS/ensemble are persist-blocked (§0.32.7), fire-risk is chartered only.

#### 0.35.8 The reader sweep LANDED — what the gate says, and how it got there

**Gate with the real-DB env set: 3,947 passed / 3 skipped** (the documented `agri_db_cross_major`
skips), ruff clean across `src/ tests/ scripts/`, mypy at the two pre-existing `matview_refresh.py`
errors. Baseline at `f811eb6` was 3,936/3, so the sweep is net +11 tests.

Seven readers now apply the completion rule through `completed_partition_days`
(`planes/{burn_severity,drought,fire_perimeters,soil_survey,watersheds}.py`,
`pipeline/validation/{drought,soil_survey}.py`). **Zero modules hand-roll the marker parse and zero
spell the rule as a negation** — checked by grep, not asserted. Mutation-tested: deleting the
completion requirement from `partition_day_statuses` fails 7 tests across 6 files, and deleting the
per-plane gate in `soil_survey.py` fails its own dedicated test.

**A CLAIM THIS SECTION ORIGINALLY MADE WAS FALSE, and the correction is §0.35.9.** It said "the rest
route through `partition_day_statuses`, which applies it internally". Six planes do not.

It was run as an AgentGraph mission (`.agentgraph/runs/d0-completion-sweep/`, 10 agents, ~$5.80).
Three lessons worth keeping:

1. **Mission workers have no Bash and cannot run pytest.** They write correct-by-construction, and
   for fixtures that is not good enough: failures went 23 → 56 immediately after the run, every one
   a fixture writing parts with no completion marker so the reader correctly refused the day. The
   production code was right; the setup was not. Finishing by hand took it 56 → 0. Budget a
   host-side fixture pass after any mission that touches tests.
2. **Two agent-written "incomplete day" tests hand-built keys as `zoom=z13`** rather than the
   layout's `zoom=13`, so their marker deletions targeted nothing and the tests asserted the
   opposite of their names while passing. Never let a test build a layout key by f-string;
   `completion_marker_path` exists.
3. **`COVERED_PARTITION_STATUSES` is a trap for resolvers.** The brief told agents to use it as the
   reader allowlist, and in `planes/evacuation_zones.py` that made the explicit `conflicted` refusal
   at `:244` UNREACHABLE — a conflict day silently resolved to an older one instead. A resolver
   excludes `UNFILLED_PARTITION_STATUSES` and nothing else. The constant's own docstring now says so.

#### 0.35.9 THE SWEEP DID NOT REACH SERVING — six planes still read unfinished days

Found by an independent verifier AFTER the gate was green, with an executed reproduction. The census
half of the contract is done; the READ half is not, and a green suite hid that because no test
covered it.

**`planes/sensors.py` — FIXED, with a mutation-verified regression test.** It computed a full
coverage census, used it only as an existence gate (`if not coverage.data_days`), then globbed the
whole tier and filtered by the requested date RANGE. A window spanning a complete Aug 1 and a
killed-upload Aug 2 returned rows for BOTH while `coverage` simultaneously reported Aug 2 as
`incomplete` — a reader handed a prefix of a killed upload alongside a census saying that day is not
published. Now filtered by `coverage.data_days`.

**`planes/{signal,vegetation,water_gauges,weather_observations,fire_detections}.py` — NOT FIXED, and
not a one-liner.** They glob `**/*.parquet` for a caller-supplied day or window and consult no
listing at all. They cannot: **their signatures take `root: str`, not an `ObjectStore`**, so they
have nothing to list with. Fixing them means changing those signatures — which is d3's work, since
d3 is where these readers get their first callers and their shapes are finalised anyway. **Do not
wire any of these five into the serving API until this is closed**; until then each one will serve
the prefix of any killed upload inside the window it is asked for.

Not urgent today only because `interface/` is still an empty stub and the planes have zero callers
(§0.33.3 C). It becomes a live data-integrity bug the moment d3 lands.

#### 0.35.10 Smaller things the verifier found, not yet done

- **The marker is trusted by EXISTENCE and never decoded.** `completed_partition_days` regex-matches
  the key only; `PartitionCompletion.from_json_bytes` and `COMPLETION_SCHEMA_VERSION` have no
  callers, so a zero-byte `_complete.json` promotes a half-written day and a future v2 marker is
  silently accepted by every v1 reader. Decoding costs a GET per day, which is why it was not just
  done — decide deliberately: decode in the census, or delete the decoder and say the key IS the
  assertion. (`GovernedAbsence.from_json_bytes` has the same shape and is likewise test-only, so
  this is a convention question, not a one-off.)
- **`contended` and the advisory lock are never exercised.** No test injects a lock yielding
  `False`, so the outcome is unreached and unproven; `unlocked_lane_day` has zero callers. Since
  `contended` is deliberately NOT a failure, a lane-day stuck contended is a silent permanent gap on
  a green tick — the outcome that most needs a test.
- **`COVERED_PARTITION_STATUSES` and `PARTITION_DAY_STATUSES` have no consumers** and are not in
  `foundation/parquet/__init__.__all__`. The first documents a conflict policy that four readers
  violate: `burn_severity` answers a conflict day as a governed absence, while `drought`,
  `watersheds`, `soil_survey` and `fire_perimeters` serve it as data. Either give the constant
  callers or move the policy to `AGENTS.md` where an unenforced rule reads as guidance.
- **`pipeline/validation/signal.py::find_incomplete_export_partitions` is dead** — zero callers,
  zero tests. Delete it or test it.
- Two soil-survey tests guard their fixture with `if marker in objects: del` rather than `assert`,
  the same footgun as §0.35.8 lesson 2.

#### 0.35.11 Still open

- The purge of §0.35.5 has not been run, and must not be until AFTER the deploy.
- `soil-field` and `climate-field` lanes are not built (§0.35.7).
- d1 (the fused drain + tier derivation) is untouched; the map is still empty above z13.

### 0.34 OWNER DECISIONS 2026-08-23 (session 6 close) — completeness, and one pass not two

Two decisions taken at handoff. They change §0.33.3's ordering, so read this before acting on it.

#### 0.34.1 Fix the half-written-release hazard across ALL LANES, and audit for missing ones

The owner's instruction was **"fix and complete all lanes, check if new ones need to be added and
wired in"** — deliberately wider than the soil-survey patch that surfaced it. §0.33.2 hazard 2 is
pre-existing for **every multi-part lane** (watersheds, drought, evacuation-zones, burn-severity,
calendar, fire-perimeters, soil-survey); streaming only raised soil-survey's exposure from ~10 parts
to ~3,016. So the fix is per-mechanism, not per-lane.

**MECHANISM CHOSEN: the per-day completion marker, written LAST and required by the census.** The
cheaper `resolve_static_lane` rule was on the table and is REJECTED, on evidence gathered this same
session:

> The contract rule only fires when a failure was actually *recorded* as `raised`. **A container
> replaced mid-write records nothing.** That is not hypothetical here — a push at 19:30Z on
> 2026-08-23 deployed over the running 18:04Z tick and killed it roughly one minute before its
> parquet phase. Deploys replacing a mid-tick container is the NORMAL case in this environment, and
> a fix blind to it is not a fix.

**COST, stated plainly: this adds a THIRD object kind** to a layout `paths.py` has deliberately held
at exactly two, and every "exactly two kinds" assertion in the codebase becomes false. That is a real
regression in a constraint that has caught bugs. It is accepted because a day that cannot say whether
it finished is worse than a layout with three kinds. If a later reader wants to reverse it, the
alternative is the `resolve_static_lane` rule and its known blind spot — do not reverse it without
solving the kill-mid-write case some other way.

**Also in scope, per "check if new ones need to be added":** `soil-field` is the KNOWN missing lane
(§0.32.5 — zoom-tiered, `agri.spatial_cell`, no lane at all). Audit the rest rather than assuming it
is the only one: every plane, every producer, and every `geo.layers` row should map to a lane or have
a recorded reason it does not.

#### 0.34.2 FUSE the drain and the tier derivation — one pass, not two

§0.33.3 listed A (tier derivation) and B (bulk drain) as independent. **They are now ONE slice.**
Each drained day writes its base z13 tier and immediately derives z9/z5/z0 from what is already in
memory.

Why: the drain walks 13,037 lane-days once; a separate derivation walks all of them again, re-reading
every object it just wrote. Fusing also means the warehouse is **never in a state where the base tier
exists and the coarse tiers do not** — which is exactly the state §0.33.2 hazard 1 describes, where
the map looks empty above z13.

Cost accepted: a derivation bug now fails the drain too, and two pieces that would each be simpler
alone are coupled. Mitigate by keeping the derivation function pure and independently tested — the
fusion belongs in the driver, not in the transform.

**Ordering is unchanged and still matters: build → run → THEN stop the cron.** Stopping it first
freezes the warehouse with nothing replacing it.


### 0.33 STATE AT END OF SESSION 6 — the zoom axis shipped; what remains, in order

**HEAD `68da7af`, PUSHED, gate-green.** Four commits landed today. §0.32 holds the decisions, §0.31
the measurements. This section is only: what is done, what is left, and what will bite.

#### 0.33.1 Shipped today

| commit | what |
|---|---|
| `ceeb2c9` | sub-day version fix (owner committed mid-session) |
| `3b7ecfb` | the orphan-part blocker its review found, + the PUT-vs-SELECT race |
| `fe32aef` | conductor: zoom-axis decisions, Postgres demoted to a cut-off |
| `68da7af` | the zoom axis end to end, and `MAX_SOIL_SURVEY_POLYGON_KEYS` deleted |

**Gate at `68da7af`: 3,936 passed / 3 skipped** (from 3,747 — net +189 tests), `ruff` clean, 423
files formatted, `mypy` at the two pre-existing `matview_refresh.py` errors, `tsc` exit 0, 353 TS
tests across 26 files.

Client-side, additive and green, NOT yet wired to anything: `src/lib/map/zoom-tiers.ts` (the ladder,
mirrored from Python, `zoom=05` zero-padded), `src/lib/server/services/parquet-envelope.ts` (the
four-state union), `src/lib/server/services/parquet-plane-client.ts` (built on
`http/bounded-upstream.ts`; wire format isolated to `:197-320` so freezing it is ONE edit).

#### 0.33.2 TWO DEFERRED HAZARDS — read these before touching the warehouse

1. **Nothing derives the coarse rungs.** A serving read at z0/z5/z9 honestly returns empty. The map
   WILL look empty above z13 until the derivation step lands. Correct by design; it will read as a
   regression to anyone who does not know. See `planes/soil_survey.py:99-104` and
   `planes/evacuation_zones.py:127-131`, which say so in their own docstrings rather than borrowing
   the base tier.
2. **A half-written release can read as `current`.** `oldest_export_instant` catches a re-export
   MIXTURE, not a first export that never finished: every part of an incomplete first export is new,
   so the oldest instant still sits at or after the watermark. Pre-existing for every multi-part
   lane, but streaming moves soil-survey from a ~10-part upload to ~3,016, which changes the odds
   materially. **Two closed fixes, OWNER DECISION NEEDED:** a per-day completion marker written last
   and required by the census (stronger, but adds a third object kind to a layout deliberately held
   at two), or a rule in `resolve_static_lane` that a day whose last export `raised` must be
   re-exported regardless of instant (cheaper, stays inside the contract module).

#### 0.33.3 WHAT REMAINS, in dependency order

**A. Tier derivation (BLOCKS everything visual).** Derive z9/z5/z0 from the base z13 Parquet in
Polars/DuckDB — never from the Postgres matviews (§0.32.2 decision 2). Until this lands the ladder
exists but only its top rung has data. Home: `warehouse/parquet/tiers.py` (does not exist yet).

**B. The bulk Postgres drain.** One focused job, not the hourly trickle (§0.32.1 decision 2).
13,037 lane-days remain, **69% of it `fire-detections` alone** whose 2000-11-02 floor is REAL
(§0.32.6). It writes the NEW layout directly; the 2,274 old-layout objects are deleted, not
migrated. **Order matters: build drain → run drain → THEN stop the cron.** Stopping it first freezes
the warehouse with nothing replacing it.

**C. `interface/http` serving API.** `interface/` is still an EMPTY STUB and the twelve planes still
have ZERO callers. Its own docstring says `interface/http` is where they belong and that it is the
only package that may import `planes`. Endpoints: per-day, window, as-of, coverage. bbox pushdown
into the scan. Zoom routing via `serving_zoom_tier`. The four-state envelope.
**The coverage endpoint must REPRODUCE `getSliderCapabilities`' existing contract**, not invent one
— then `time-slider-store.ts`, `layer-coverage-track.ts` and `LayerTimeSlider.tsx` need ZERO changes.
It must stay whole-warehouse (no bbox, no zoom) or the 5–30 min memoization fragments per viewport.

**D. The Next.js repoint.** Six tRPC procedures move onto the client already built. Only these have
BOTH a tRPC path and Parquet data: drought, water-gauges, weather-observations (all three 100%
backfilled), plus signal, vegetation, fire-detections (thin but correct). `soil-survey` cannot go
until it has actually written; `burn-severity` until the walk reaches 2020-2024.
**fire-detections' live path is REST `/api/fires?date=` with NO bbox**, not tRPC — its tRPC
procedure has no caller.

**E. Forward path per lane.** Upstream API → Parquet directly, then that lane deprecates its Postgres
source (§0.32.1). `weather-observations` holds only 21 days in Postgres, so Open-Meteo historical is
the ONLY way to deepen it.

**F. New lanes.** `soil-field` (zoom-tiered, `agri.spatial_cell`, has NO lane at all — §0.32.5),
Open-Meteo ensemble/flood/CAMS (persist-blocked since 2026-08-06), the fire-risk feature plane
(no model — training blocked on Mojo).

**G. Static lookups leave the lane registry** (§0.31.5): `soil-survey` and `watersheds` to a
provisioning config area; `evacuation-zones` and `calendar` STAY. Note the new coverage check the
move needs — `build_gap_census` is also the safety net.

#### 0.33.4 Smaller things left behind, none hidden

- `tests/parquet/test_zoom_ladder.py:39,44` carry two unused `type: ignore[arg-type]` that mypy
  flags as `unused-ignore`. Harmless, not failing anything.
- `MAX_PART_INDEX = 9,999` is now a reachable ceiling: 9,999 × `ROWS_PER_PART` 500 = 4,999,500 rows.
  The PNW universe (~3,016 parts) fits with 3.3× headroom and raises loudly rather than truncating.
  Matters only if anyone LOWERS `ROWS_PER_PART`.
- The `calendar` lane has no geometry, so it writes `zoom=13` like everyone else. **A `zoom=13`
  prefix therefore does not imply geometry** — the derivation step must not assume it does.
- `LANE_BASE_ZOOM_TIER` lives in `pipeline/lanes/__init__.py:35` and `GAP_FILL_ZOOM_TIER` in
  `gap_fill.py:93`. Both are `ZOOM_TIERS[-1]` so they cannot drift, but `foundation/parquet/zoom.py`
  is where ONE definition belongs if a later pass wants to collapse them.
- Two env vars now point at one service: `AGRI_DATA_SERVICE_URL` (pre-existing, forecasts, degrades
  to unavailable) and `AGRI_PARQUET_SERVICE_URL` (new, must THROW rather than draw an empty map).
  Deliberate — different failure policies — but collapsible at `parquet-plane-client.ts:54`.
- Docs corrected by measurement this session: `wildfire.ts:72-74` and `src/lib/server/AGENTS.md:826-830`
  still claim `/api/fires` "takes no parameters". **False** — `useFireData.ts:111` sends `?date=`.

#### 0.33.5 A claim from this session that is WRONG — do not act on it

A subagent reported that ~102 of 107 frontend test files fail repo-wide under the default `jsdom`
environment and that only `// @vitest-environment node` files pass. **It does not reproduce.** The
same file passes under the default config and under `npm test`'s exact flags, and the full run is
353 passed across 26 files. Seven files carry the node pragma, not three. Likely contention from
three agents writing concurrently. **The frontend suite is fine.**


### 0.32 OWNER DECISIONS 2026-08-23 (sixth session, late) — the zoom axis, and Postgres becomes a cut-off

**HEAD `3b7ecfb`, PUSHED, gate-green.** This section is decisions; §0.31 is the measurements they
rest on. Read §0.31 first — several of these decisions exist because a measurement refuted a belief.

#### 0.32.1 THE FORWARD PATH — Postgres is a one-time cut-off, not a source

| # | decision | consequence |
|---|---|---|
| 1 | **Upstream API → Parquet directly** | Every lane's forward path writes Parquet straight from its upstream API. The Postgres exporters become BACKFILL-ONLY — they are the drain tool, not the pipeline |
| 2 | **Stop the cron; bulk-drain Postgres → Parquet; then the API takes over** | The 13,037 remaining lane-days are data ALREADY in Postgres. Draining them through a 600 s budget behind an 86-minute `ingest-all`, one day per lane per round, is absurd when it can be one focused job |
| 3 | **Each lane deprecates its Postgres source once its drain completes** | Per-lane, not big-bang. A lane is done when its history is in Parquet and its forward writer is API-direct |

**Do not stop the cron until the drain exists.** Stopping it now freezes the warehouse with nothing
replacing it. Order: build drain → run drain → then stop cron.

#### 0.32.2 THE ZOOM AXIS — four decisions taken together

| # | decision | consequence |
|---|---|---|
| 1 | **`zoom=` becomes a partition key, ABOVE `year=`** | `layer=X/kind=observed/zoom=Z/year=/month=/day=/part-N.parquet`. Polars/DuckDB prune a whole tier by directory before reading a byte; routing becomes a path template, not a scan |
| 2 | **Tiers derived in Polars/DuckDB FROM THE BASE PARQUET** | The only option consistent with 0.32.1. `geo.mv_soil_survey_grid`, `geo.watershed_rollup` and the soil-field lattice stay where they are and are NOT the source |
| 3 | **ONE uniform ladder for every layer** — z0 / z5 / z9 / z13 | One routing rule, one census shape, and a new layer inherits it free. Watersheds' HUC12/10/8/6/4 is mapped ONTO the ladder, not kept native |
| 4 | **Chunked streaming export; `MAX_SOIL_SURVEY_POLYGON_KEYS` DELETED** | Most granular data possible — all delineations — PLUS every zoom tier. Bounded batches to `part-0..part-N` keep memory flat, so the lane scales to the full 1,507,623-delineation PNW universe |

**Zoom is REQUIRED on every path call, never defaulted.** A default silently files everything under
one tier and the mistake stays invisible until serving.

#### 0.32.3 What these supersede — three things a later reader must not restore

1. **§0.30.3's "do not change the partition path layout" freeze is RETIRED for the zoom axis.** That
   rule correctly governed the sub-day fix, which had no business touching the layout. This does.
   **The DAY remains the version stamp**; zoom is ORTHOGONAL, not a second version.
2. **The soil-survey cap decision in §0.31.1 is void.** There is no cap, and "raise it to N" was
   also rejected. Granularity is the goal, not a tolerated cost.
3. **The zoom-bloat hypothesis is REFUTED — do not re-adopt it.** `geo.features` carries no
   zoom/LOD/generalization column anywhere; every existing tier lives in a SEPARATE matview. The
   SSURGO insert (`usda-soil.ts:916-928`) is a strict upsert on `properties->>'id' = polygonKey`, so
   the 238,986 rows are 238,986 real delineations — one per delineation, never one per zoom. The
   cap's own comment already said as much: *"a refusal to attempt something absurd, not a claim about
   how many delineations SSURGO holds"* (`lane_registry.py:92-96`).

#### 0.32.4 Migration — the existing 2,274 objects are DISCARDED, not migrated

Every object in the bucket sits at the pre-zoom layout. Because the bulk drain rewrites everything
from Postgres anyway, **the drain writes the new layout directly and the old objects are deleted.**
Do not write a path-rewriting migration, and do not add a backward-compatible parse — a path without
`zoom=` must fail to parse cleanly. A compatibility shim here would outlive its purpose and quietly
admit un-zoomed writes forever.

#### 0.32.5 `soil-field` HAS NO LANE — the gap the zoom research surfaced

The zoom-tiered WEATHER aggregation is not soil-survey; it is **`soil-field`** (soil moisture / VPD).
`SOIL_FIELD_TIERS` detail z9 / regional z7, Gaussian-kernel lattice, backed by `agri.spatial_cell`
via `geo.soil_field_observation` — it never touches `geo.features`. **None of the twelve lanes covers
it**, and `signal_plane_day_export.sql` exports `cell_id` with NO cell geometry. It needs a lane.

#### 0.32.6 Window parity — MEASURED, and essentially already correct

Measured against production 2026-08-23:

| layer | earliest published day | declared floor | verdict |
|---|---|---|---|
| fire-perimeters | 2025-07-28 | 2025-07-28 | exact |
| weather-observations | 2026-08-03 | 2026-08-01 | 2 phantom days, immaterial |

**`fire-detections`' 2000-11-02 floor is REAL** — it matches FIRMS' own `MODIS_SP` archive floor
(2000-11-01), corroborated across 3.0M+ rows. Its ~9,000-day share of the drain is genuine work, NOT
a phantom-floor artefact. An earlier guess in this session that window parity would cut the drain by
two-thirds was WRONG and is retracted.

**`weather-observations` holds only 21 days in Postgres (2026-08-03 → today, 34,819 rows).** There is
no deeper weather history to drain. Years of it must come from the Open-Meteo historical API writing
Parquet directly — decision 0.32.1 arriving on its own evidence.

#### 0.32.7 New lanes approved for this push

All three: **Open-Meteo ensemble + flood/GloFAS + CAMS** (built 2026-08-06, persist-blocked; closes
`upstream_dataset_expansion_20260806`), the **fire-risk cell-day feature plane** (feature plane only —
training stays blocked on Mojo), and **Open-Meteo forward+historical for the existing weather lanes**
(the only way to deepen weather-observations past 21 days, per §0.32.6).

#### 0.32.8 `3b7ecfb` — the orphan-part blocker, found by review and fixed

`ceeb2c9` shipped the sub-day fix and was live in production carrying a blocker that four adversarial
reviewers found, two of them converging on it independently.

A shrinking same-day re-export left orphans: day D exports as `part-0..part-3`; the source changes;
the re-export writes only `part-0` and `part-1`; the older two remain. **Two failures at once** — the
orphans are read by anything scanning the day prefix, so evacuation-zones would serve a RETRACTED
evacuation level beside the current one; and `oldest_export_instant` is a `min()` across the day, so
it reads the ORPHAN's timestamp, pins the lane below its watermark, and re-exports every tick forever.

`prune_surplus_parts` (`objectstore.py:360`) closes it: **write every new part first, then** remove
`part-<n>` for n >= the count just written, scoped to one layer/kind/day. Never the reverse — a
prune-then-write that fails midway leaves the day EMPTY, which reads as present-but-thin and is worse
than the orphan. A prune failure is REPORTED, never raised: the rows are written and correct.

Also closed: the PUT-vs-SELECT race (the export instant is upload-completion time, so a source change
inside the export window read as captured). The driver now re-reads the watermark after the export
and re-exports when it moved, bounded by `MAX_STATIC_EXPORT_ATTEMPTS`, and reports UNPROVEN when it
cannot show the window was clean.

**The blocker was caused by the orchestrator's brief**, which told the agent to use the OLDEST
LastModified as "conservative" AND separately to note-but-not-fix the surplus-part problem. Those two
instructions combine into the defect. Recorded because the lesson is about briefs, not agents.

Gate: **3,747 passed / 3 skipped**, ruff clean, 421 files formatted, mypy at the 2 pre-existing
`matview_refresh` errors. Parquet suite 474 → 491.


### 0.31 SESSION 6, 2026-08-23 — the sub-day fix landed, and soil-survey is not what §0.29.1 says

**HEAD `deba827` (UNPUSHED — `origin/main` is `ef789f7`).** s1's sub-day version fix is in the
working tree, gate-clean, under adversarial review, NOT yet committed. Read §0.30 for the decisions
this session executed against.

#### 0.31.1 CORRECTION — §0.29.1 is WRONG about soil-survey, on both the state and the reason

§0.29.1 records: *"`soil-survey` writing NOTHING is CORRECT: its Postgres source is filled only by
lazy viewport read-through nobody has triggered, so the watermark is `None` → `source_empty`."*

**Both halves are false.** Measured against production 2026-08-23 via
`LOCAL_SOURCE_LOADER_DATABASE_URL`:

| probe | result |
|---|---|
| watermark join returns rows? | `t` — the source is NOT empty |
| bounded count vs the cap | **200,001** against a cap of 200,000 → `export_would_refuse = t` |

The real blocker is `MAX_SOIL_SURVEY_POLYGON_KEYS: Final = 200_000` (`lane_registry.py:96`),
enforced at `lane_registry.py:288-293` and called from the **export** path (`lane_registry.py:615`),
not the watermark path. The lane resolves `stale`, attempts its export, and raises
`LaneRegistryError` every tick — *"exporting a truncated key list would write a partial release that
reads back as a complete one. Raise MAX_SOIL_SURVEY_POLYGON_KEYS deliberately, or shard the
release."*

**Consequence: soil-survey is not waiting on a backfill and will never drain on its own.** It is
waiting on a decision. Any plan that assumes it joins the warehouse by waiting is wrong.

#### 0.31.2 CORRECTION — the leading-edge lag is BY DESIGN, not backlog starvation

Every lane's newest partition day sits 1-9 days behind today. That is NOT the round-robin spending
its budget on history; it is `lane_window()`'s settling rule, `last_day = today -
publication_lag_days` (`gap_fill.py:239-259`). Measured 2026-08-23, five of six exact:

| lane | `publication_lag_days` | `today − lag` | measured newest |
|---|---:|---|---|
| signal | 9 | 2026-08-14 | 2026-08-14 |
| vegetation | 7 | 2026-08-16 | 2026-08-16 |
| burn-severity | 7 | 2026-08-16 | 2026-08-16 |
| fire-detections | 2 | 2026-08-21 | 2026-08-21 |
| fire-perimeters | 1 | 2026-08-22 | 2026-08-22 |
| drought | 4 (cadence 7) | 2026-08-19 | 2026-08-18 — weekly step |

The driver keeps the leading edge current AND drains backlog at the same time, exactly as designed.
**A serving cut-over must therefore expose a coverage endpoint**: a client asking for "today"
legitimately gets nothing, because today is not settled yet.

#### 0.31.3 `burn-severity`'s 383-markers-zero-parts IS correct — do not "fix" it

It looks identical to soil-survey's symptom and is not the same thing. Its source holds 541 real
rows at five MTBS release dates 2020-11-24..2024-08-22 (`lane_registry.py:650-669`, `cadence_days=1`
deliberate for an irregular release series). The newest-first walk is currently near 2025-07-30 and
has ~1,700 days to go before it reaches them. It will serve eventually.

#### 0.31.4 THE CRON IS ARMED AND DRAINING — measured, with backfill proof

`plantgeo-ingest-cron` (`3ae3cc37`), schedule `0 * * * *`, latest deployment SUCCESS 18:04:08Z,
instance RUNNING. Bucket `plantgeo-parquet-9ymvp7gv`: **2,274 objects / 677.1 MB**, up from
§0.29.1's 1,240 — **+1,034 in one day**.

Backfill depth, not just leading edge — the fifteen most recent writes at 18:02Z were all **2025**
partition-days, descending, five lanes interleaved at ~21 s per round:

    18:02:11Z fire-perimeters 2025-08-05    18:02:10Z fire-detections 2025-08-04
    18:02:08Z vegetation      2025-08-06    18:02:08Z burn-severity   2025-07-30
    18:02:05Z signal          2025-07-25

Per-lane depth: drought reaches back to 2022-08-09 (211 days), signal to 2025-07-25 (386 days).
Static lanes correctly idle: `watersheds` still 10 parts at one version day, `evacuation-zones` 4
parts at 2026-08-23, neither re-snapshotted.

The 86-minute trap held again: the 18:04Z tick was still in `ingest-all` at 19:01Z.

#### 0.31.5 OWNER DECISION — static lookups leave the lane registry

**`soil-survey` and `watersheds` drop out of `lane_registry.py`** into a separate config area for
provisioning static lookups, alongside things like sensor station locations. **`evacuation-zones`
and `calendar` STAY.**

The line: a lane belongs in the gap-fill registry when something must *react* to it on a cron
cadence. HUC12 boundaries, SSURGO delineations and sensor station locations are immutable reference
geometry wanting deliberate provisioning; OEM evacuation levels move within hours of a fire (the
entire reason for wave 4's sub-day fix), and the calendar dimension computes itself.

**This dissolves the soil-survey blocker rather than working around it.** An hourly driver can only
refuse a 200,001-key release; a provisioning step can shard it deliberately, which is exactly what
the cap's own error message asks for. The cap becomes a sharding parameter, not a refusal.

Three things the move must not lose:

1. **The layout does not change.** `watersheds`'s 10 parts stay where they are; `planes/watersheds.py`
   and `planes/soil_survey.py` keep reading by path. This changes who WRITES, not what or where.
2. **The gap census is also the safety net.** Once they leave the registry, `build_gap_census` no
   longer covers them and nothing notices if their objects go stale or vanish. The config area must
   carry its own coverage check or the move trades a noisy failure for a silent one.
3. `write_partition`'s zero-row refusal and governed-absence refusal still apply. Provisioning is
   not exempt.

#### 0.31.6 THE SERVING GAP, measured — the planes have ZERO callers

The pivot built a warehouse writer and twelve readers and never connected them to anything.

- All twelve `planes/*.py` read Parquet from S3 via Polars with predicate pushdown. They work.
- `grep -r "from agri_data_service.planes import"` across the whole service returns **ZERO matches**.
  Nothing in `routes/`, `app.py`, `cli.py` or `jobs/` imports any plane. They are exercised only by
  `tests/parquet/test_*_serving.py`.
- `interface/` is an EMPTY STUB — `__init__.py` and `AGENTS.md` only. Its own docstring declares it
  layer L4, says `interface/http` holds HTTP routes, and says it MAY import `planes`. **No other
  package may.** So the blueprint has a designated home that was never built.
- `src/lib/server` has zero parquet/duckdb/polars/S3 references. Every core layer the Next.js client
  reads comes from Postgres through tRPC (`environmental-read-model.ts`, 4,432 lines).

**The cut-over splits three ways and only one is a wiring job:**

| bucket | layers | cost |
|---|---|---|
| tRPC-served, Parquet has data | fire-detections, drought, vegetation, weather-observations, water-gauges, signal | build the HTTP surface, repoint the procedure |
| tRPC-served, Parquet has NO data | soil-survey (cap-blocked, §0.31.1), burn-severity (walk hasn't arrived, §0.31.3) | cannot cut over yet |
| **Martin/PostGIS tiles only, no tRPC path at all** | fire-perimeters, burn-severity, evacuation-zones, sensors | NOT wiring — needs PMTiles generation, a separate build |

#### 0.31.7 s1 LANDED — sub-day version collapse closed, gate-clean

`ObjectStoreBackend.list_keys` became `list_objects` carrying `LastModified`; `SourceWatermark`
gained `instant`; `resolve_static_lane` compares the EXPORT instant against the SOURCE instant when
both fall on the watermark day. **Zero extra API calls, zero layout change** — the instant comes from
the listing already made. 8 files, +510/-46.

The unknown-instant fallback (chosen, and it is a real decision): falls back to day resolution and
returns `current`, but stamps **"DAY-RESOLUTION"** into the verdict `detail` so a reader can see the
answer was not instant-resolved. Rationale: a lane that re-exports every tick forever is a worse bug
than the one being fixed — `watersheds` is 162 MB per export and the OLD code did exactly that.

**Gate: 3,730 passed / 3 skipped / exit 0** (baseline 3,719 — the +11 are s1's new tests; only 3
skips confirms the real-DB gate was live). `ruff check` passed, `ruff format --check` 421 files
already formatted, `mypy` still exactly the 2 pre-existing `matview_refresh.py` errors.

s1 deliberately left three defects, one of which reorders the queue: **a governed absence at the
watermark day makes the owed re-export raise forever.** That is s2's auto-retraction, and s1's fix
makes it MORE likely to bite because it creates more re-export attempts. s2 is now more urgent, not
less.

#### 0.31.8 Operational — things learned the expensive way

- **AgentGraph missions are for IMPLEMENTATION only** (owner, 2026-08-23). Mission workers lack the
  permission and skill access ordinary subagents have. Research, verification and review go to
  regular subagents; missions write code.
- **The claim ledger works.** s1 tried to write a scratch file `_s1_objectstore.py` into the service
  root and made one unclaimed edit; both were refused and the agent recovered. The write boundary is
  real — which is exactly why write scope must be approved up front.
- **The prebaked partition needed widening.** The `LastModified` change touches THREE backend fakes,
  not one: `RecordingBackend` (`test_objectstore_writer.py:47`), `RaisingBackend`
  (`test_gap_fill.py:107`) and `LocalFileBackend` (`test_sensors_serving.py:35`). A partition that
  lists only the first sends an agent into a wall.
- **Production is fragile under scans.** A six-way `count(*)` over `geo.features` timed out at 120 s.
  Use `EXISTS` and `LIMIT`-bounded counts (see §0.31.1's probes) — RAM is per-query here (§12.6).
- **The AgentGraph run log nests every event under an `event` key.** A parser reading `type` at the
  top level silently reports zero of everything and looks like a dead mission.


### 0.30 HANDOFF 2026-08-23 — wave 4 is decided, and the pivot finally has a track

**HEAD `ef789f7`, PUSHED, tree clean.** Read §0.29 next for what wave 3 actually did and what it
left broken. This section is the decisions on top of it.

#### 0.30.1 Four owner decisions, 2026-08-23 (end of fifth session)

| # | decision | consequence |
|---|---|---|
| 1 | **Wave 4 is all four scopes**, ordered: sub-day fix → pay-down → fire-risk feature plane → Open-Meteo products | The life-safety item leads. Everything else is feature work stacked on a serving path known to be wrong |
| 2 | **Sub-day fix = widen `ObjectStoreBackend` to surface `LastModified`.** The sidecar-object fork is REJECTED | S3 already returns it in `list_objects_v2` `Contents` and `_listed_keys` throws it away. Costs a Protocol signature and test fakes; **zero extra API calls, zero on-disk change, no third object kind** |
| 3 | **Auto-retract a governed absence when the retry returns rows** | See the flag below — this RELAXES an existing guarantee and was not the recommended option |
| 4 | **Charter the pivot as a real track** | `conductor/tracks/parquet_duckdb_pivot_20260823/` now exists with prebaked partitions at `ef789f7`, registered in `tracks.md`. Waves 1-3 ran with no track at all |

#### 0.30.2 FLAGGED — decision 3 relaxes a guarantee, deliberately

`GovernedAbsence` states that **"retracting it is a manual admin action"**. Auto-retraction removes
that. The owner chose it over shipping a retraction verb, with the trade understood: without it a
latched lane raises `GovernedAbsenceConflictError` **every tick forever** and nobody can clear it.

**Recorded as a relaxation, not an oversight**, so a later reader does not "fix" the auto-retraction
back into a manual gate without knowing it was chosen. The recommendation at the time was an
explicit verb; the owner overrode it. If auto-retraction ever erases an absence that was genuinely
correct, THIS is the decision to revisit.

#### 0.30.3 The one thing wave 4 must not do

**Do not change the partition path layout.** The day stays the version stamp. A static lane's export
is a full re-export of the whole population, so overwriting day D with the newer state is exactly
what "this version" means. An agent proposed a sidecar object and was correctly stopped; that fork
is closed by decision 2.

#### 0.30.4 Environment corrections

- **`origin/main` == `ef789f7`.** The three wave-3 commits are pushed.
- **`main` is no longer the only branch.** `codex/strategy-selection-benchmark` exists at `31ce91f`
  ("feat: add local warehouse dev launcher"), which contradicts the standing "everything lives on
  main, no branches" note. Unreviewed and unmerged; nobody has said what it is for.
- **Pin `ruff` exactly.** The pin is `ruff>=0.5`, installed is `0.15.22`, and the formatter drift
  reddened the gate's first stage across 36 files that nobody had touched. `ecb559a` swept it, but
  nothing stops it recurring.
- **AgentGraph `interrupt_after` is not a gate.** Every agent dispatches up front; it only stops the
  run afterwards. A write agent placed after six reviewers ran *alongside* them and never saw their
  findings. Sequence waves as two separate missions, not one list. The file-claim ledger works
  correctly and protects against collision, never against ordering.

### 0.29 REVIEW + FIXES 2026-08-23 (fifth session) — b794e98 got its adversarial pass

**Read this before §0.28: it CORRECTS two of §0.28's claims and records four defects §0.28 shipped
without knowing.** Commits: `269d299` (per-kind schema lookup), `ecb559a` (ruff format sweep), and
the defect-fix commit that follows this section.

#### 0.29.1 THE CRON IS CONFIRMED WORKING — §0.28.6's open question is CLOSED, positively

`parquet-gap-fill` **runs and writes**. Measured against the live bucket: **1,240 objects** (882 part
files + 358 governed-absence markers) across all twelve lanes, up from 15. The verb starts **~86
minutes** into a tick, third behind `ingest-all` (which has NO time budget) and `jobs-pulse`'s 600s.

**The trap that cost this session an hour, recorded so nobody repeats it:** a 58-minute watch of the
03:01Z tick saw ZERO writes and was read as "the verb never runs". The first write of that very tick
landed at **04:27:50Z** — 17 minutes after the watch stopped. *Do not conclude a tick is dead until
at least ~95 minutes after container start.*

Two independent proofs the NEW code is live: `layer=calendar/.../day=2026-08-23/part-0.parquet`
written 04:27:50Z (the calendar lane exists only in `b794e98`), and `watersheds` still holding ONLY
its 10 manual parts from 00:35Z — **not** re-snapshotted, which is the `static_lookup` watermark
model working. The old code rewrote 162 MB hourly.

Schedule is hourly, observed ticks are ~1-2h apart: a run exceeds its hour, so `restartPolicyType
NEVER` skips the overlapping tick. At ~408 objects/tick the 15,083-day backlog drains in ~37 ticks.
`soil-survey` writing NOTHING is CORRECT: its Postgres source is filled only by lazy viewport
read-through nobody has triggered, so the watermark is `None` → `source_empty`.

#### 0.29.2 FOUR CONFIRMED DEFECTS in `b794e98`, found by five adversarial agents

`b794e98` shipped unreviewed by its own admission. Five Opus reviewers found four real defects.

| # | defect | status |
|---|---|---|
| 1 | **Absence latch.** `newest_covered_day()` counted an absence marker as coverage, so a static lane whose export hit zero rows wrote a marker AT the watermark day and then read `current` **forever** | **FIXED** |
| 2 | **Sub-day version collapse.** The watermark truncates to a UTC date and compares `>=`, so after day D's first snapshot every later same-day change reads `current` until midnight — on **evacuation-zones**, where OEM levels change repeatedly during an active fire | **DEFERRED — needs an owner call** |
| 3 | **`--dry-run` opened a PRODUCTION session** by default | **FIXED** |
| 4 | **Ungated `updated_at`.** `usda-soil.ts` advanced it on every re-fetch of unchanged ground | **FIXED** |

**§0.28.8 IS WRONG IN KIND about #3.** It credits `--skip-watermarks` with closing that footgun.
It did not: the flag was opt-in, so the DEFAULT dry run still resolved `LOCAL_SOURCE_LOADER_DATABASE_URL`,
found it unset, and fell back to `DATABASE_URL` = **prod**. It is closed NOW, by flipping the default.

**§0.28.8's change-clock claim is TRUE PER FILE and FALSE GLOBALLY.** `refresh_features.sql` and
`link_feature_geometry.sql` are genuinely change-gated. There is a **third** writer of
`geo.features.updated_at` — `usda-soil.ts` step 4a — and it was ungated. Now fixed.

**§0.27.1's "six lanes can forecast" is FIVE**, already corrected in §0.28.8 and re-verified here
against the filesystem in both directions.

#### 0.29.3 THE ONE DECISION THIS SESSION REFUSED TO MAKE

Defect #2 cannot be fixed without somewhere to record *which instant* a version was exported at.
The partition day is the version stamp and there is no room in the layout for two versions of one
day. **The agent was forbidden from changing the partition layout unilaterally and correctly
stopped.** Two forks:

1. **Widen `ObjectStoreBackend`** to surface `LastModified`, which the S3 `Contents` entries already
   carry and `_listed_keys` currently discards. Costs a Protocol signature, a backend and test fakes.
   **Zero extra API calls, zero on-disk change. This is the recommended fork.**
2. **A sidecar object at the day prefix.** Costs a write per export, a new parser, and a THIRD object
   kind in a layout `paths.py` constrains to exactly two.

Until this lands, **evacuation-zones can serve a stale evacuation level for up to a day.** That is a
life-safety layer; treat it as the top of the queue.

#### 0.29.4 Three things the fixes CREATED or LEFT, none of them hidden

1. **No marker-retraction path.** The latch fix is only net-positive if a marker can be cleared. Once
   a lane is `stale` with a marker at W and the retry returns rows, `GovernedAbsenceConflictError`
   ("retracting it is a manual admin action") fires **every tick, forever**. Ship a retraction verb
   or accept a known-red lane.
2. **Re-publish is now invisible to the matview watermark.** The `usda-soil.ts` fix deliberately
   leaves `status='published'` unconditional while freezing `updated_at`, and
   `scripts/apply-pre-aggregation.mjs` declares `max(updated_at)` the watermark for every
   `geo.features`-backed matview. A re-publish of unchanged ground can now be missed.
3. **The absence latch is unit-covered but NOT driven through `_static_lane_census`** — which is the
   exact shape of the bug. Two agents wanted `test_gap_fill.py`; the claim ledger correctly refused
   the second, and the handover test arrived after the owner had finished.

#### 0.29.5 Gate

**3,719 passed · 3 skipped · exit 0** with `AGRI_TEST_DATABASE_URL` set (local `agri_sweep`, port
5442) and `PGBIN` set. `ruff check` clean. `ruff format --check` **now clean** — `ecb559a` swept 36
files that had drifted because the pin is `ruff>=0.5` and the installed ruff is 0.15.22. **Pin ruff
exactly** so the gate cannot rot again. `mypy` unchanged at the two pre-existing
`matview_refresh.py` errors. `npm run type-check` exit 0.

Doc surfaces still contradicting the code: `pipeline/parquet/AGENTS.md` and `README.md` both still
describe offline-mode as an opt-in flag.

### 0.28 HANDOFF 2026-08-23 — the forecast strategy changed. Read this before §0.27.

**HEAD `1c21a20`, pushed, tree clean.** §0.27 remains accurate about what was BUILT; this section
supersedes its forecast strategy and retracts one claim.

#### 0.28.0 CORRECTION -- `96617bc` is mislabelled, and this is the honest record

*Written 2026-08-23 by the session that landed the work `96617bc` half-swept in.*

`96617bc` reads `docs(conductor): handoff`. **It is not a docs commit.** A `git add -A` run while an
agent was mid-write swept in **7 code files**: `foundation/parquet/calendar.py`,
`foundation/parquet/lane_contract.py`, `pipeline/lanes/calendar.py`, `warehouse/schemas/calendar.py`
and the three `sql/pipeline/lane_watermark_*.sql` files. **No sweep ever ran against that
combination** -- it was a partial cut of an agent's in-flight tree, and its message describes none
of it.

History was NOT rewritten: `96617bc` is pushed and level with `origin/main`, so force-pushing shared
history to correct a message is the more expensive error. The framing is corrected **here** instead,
and the follow-up commit carries the rest of that agent's work plus the first sweep that ever
covered it.

**Do not cite `96617bc` as evidence that anything was verified.** The verified point is the commit
that follows it.

#### 0.28.1 Four owner decisions, 2026-08-23

| # | decision | consequence |
|---|---|---|
| 1 | **Per-kind schema lookup.** `get_stream_schema(layer, kind)` returns the observed schema, or observed + the six provenance columns for `kind=forecast` | Unblocks all five forecasters. Observed files stay byte-identical to what is already written. `layer-lanes.md` §2 relaxes from "identical column names" to **identical measurement columns** |
| 2 | **NWP first. Monte Carlo survives ONLY where it can serve to train ML** | The bootstrap forecasters are no longer the product. Real Open-Meteo forecast/ensemble output is the forecast; MC is retained only as a labelled climatology baseline feeding ML training, and is deleted anywhere it cannot earn that keep |
| 3 | **Fire-risk: chartered, feature plane BUILDABLE, model training blocked** on the Mojo runtime call. **Seasonality must influence the model output** — fires are heavily seasonal | Build the cell-day covariate plane now; write no Python model that will be thrown away. A comprehensive **ML→Mojo conversion lane** is chartered alongside it |
| 4 | **Ingest all Open-Meteo products, forward AND historical.** Quota is not a constraint at the $99 tier, "especially if we limit forecast out" | Both directions proceed. Bound the forecast horizon rather than the ingest breadth |

#### 0.28.2 RETRACTION — "a snapshot day the cron misses is lost, deliberately" (§0.27.2) is WRONG

Owner: *"all those 3 lanes are static one time reads with no historicals."* A HUC12 boundary is not a
measurement taken on a date; it is a reference fact with a **version**. The day in its partition path
is a version stamp, not an observation time, so **there was never a per-day obligation to miss.**

The fix, in flight at handoff: three declared lane natures — `daily_series` (forecastable),
`release_series` (discrete dated publications: USDM weekly, MTBS quarterly), `static_lookup` (never
forecastable) — plus **watermark-driven re-snapshot** for static lanes. Each declares a source
watermark (`max(updated_at)`); if a partition exists dated at or after it, there is nothing to do.
The partition day becomes the SOURCE's change date, not the cron's run date.

#### 0.28.3 The temporal model, as designed (not yet built)

- **`dim_date`** — one row per day; seasonality as meteorological/astronomical season **and cyclical
  sin/cos day-of-year**, because that is the form a model consumes and it has no Dec-31→Jan-1 discontinuity.
- **`dim_time_of_day`** — separate dimension, 96 rows at 15-min or 24 hourly, reused every day.
  **Crossing it into `dim_date` would multiply to millions of rows for nothing.** Needed because
  `sensors` is hourly and `water-gauges` keeps per-poll instants.
- **Daylight is NOT an attribute of either.** It depends on date AND latitude — 06:00 is dark in
  Missoula in January and bright in June. It belongs in a **solar fact per `(cell_id, date)`**:
  sunrise, sunset, solar noon, daylight seconds. Pure deterministic computation from lat/lon/date,
  no API, ~3M tiny rows. Gives photoperiod as a real covariate for vegetation and ET.

#### 0.28.4 Monte Carlo: what it actually is, and why it was demoted

The five landed forecasters are **seasonal anomaly bootstraps** — climatology plus resampled
residuals. They answer *"what does a typical mid-September look like here"*, never *"what will happen
this mid-September"*. They carry **no information about the future state**, so by construction they
cannot have skill beyond climatology. Publishing them as low/mid/high made them **look** like
forecasts while carrying no predictive content — the wrong-but-plausible output the engineering
principles exist to prevent.

**Any forecast, from any source, must be scored against what actually happened AND against the
climatology baseline. If it does not beat climatology it is not a forecast.** Evaluation machinery
already exists: `method/ml/seasonal_evaluation.py`, `conformal_calibration.py`.

The domain knowledge inside those bootstraps is worth keeping even as the framing changes: log-space
so discharge cannot go negative, a hurdle model for zero-inflated fire counts, an outright refusal
for circular wind direction. Preserve those as **draw strategies**, not as five separate forecasters.

#### 0.28.5 Geometry and the time-series contract — the settled answer

**Do not forecast shapes.** Forecasting polygon evolution is a different and much harder problem and
nothing here needs it. Geometry participates by being **reduced to a cell-indexed scalar first**.

`fire-detections` already proves the pattern: raw hotspots have no forecastable grain (an exact future
lat/lon is not predictable), so it aggregates to a 0.005° cell-day count. **The time-series contract
is keyed on cell × time × metric, never on geometry.** A geometry lane contributes a measurement
*over* space; the polygon itself stays observed-only. The three static lookups contribute nothing.

Contouring cell scores back into a displayable region is a **rendering** step, not a forecast step —
which is exactly how fire-risk should surface.

#### 0.28.6 Cron status at handoff — ticked, but the parquet verb is UNCONFIRMED

`plantgeo-ingest-cron` deployed SUCCESS on `abf777f`/`1c21a20` via `railway redeploy --from-source`
(plain `redeploy` reuses the prior snapshot; a Dockerfile change never takes effect through it). A
real tick fired **2026-08-23 02:11:17 UTC** and `ingest-all` was still running at 02:13.
**`parquet-gap-fill` runs THIRD, after `jobs-pulse`'s 600s budget, and was NOT observed before this
handoff was written.** Verifying it is continuation step 1 — do not assume it ran.

What IS proven: one manual `parquet-gap-fill` tick wrote a real drought release (5 rows, 3.8 MB,
13.2s) and the re-census showed `data_days: 1` with the day gone from the missing list.

#### 0.28.7 Continuation

1. **Confirm the cron's parquet verb actually ran.** `railway logs --service plantgeo-ingest-cron`,
   look for the `parquet-gap-fill` JSON. If the ENTRYPOINT is wrong the run is silently 2/3 useful.
2. **Land the per-kind schema lookup** (decision 1) — it unblocks five finished forecasters.
3. ~~**Integrate the lane-nature + calendar agent's work**, in flight at handoff (see §0.28.2).~~
   **DONE — see §0.28.8 for the as-built record**, including one deviation from §0.28.3 worth
   knowing (the sin/cos and meteorological-season columns landed in `dim_date`; time-of-day,
   astronomical season and daylight deliberately did not).
4. **Wire every Open-Meteo product**, forward and historical (decision 4). `ingest/open_meteo*.py`
   already has archive, air-quality, ensemble and flood modules; ensemble and flood are **built but
   persist-blocked** from the 2026-08-06 expansion wave. Surface as layers where possible.
   **Forecast layers appear in the timeslider ONLY where a genuine forecast source exists.**
5. **Build the fire-risk FEATURE plane** (decision 3) — cell-day covariates from fire detections,
   burn severity, NDVI, soil moisture and seasonality. No model until the Mojo call.
6. **Sub-daily dimension and the solar fact table** (§0.28.3).
7. **Refactor the five bootstraps into one harness** with pluggable draw strategies (§0.28.4).

#### 0.28.8 LANE NATURES, WATERMARKS AND THE CALENDAR — AS BUILT, 2026-08-23

**§0.28.2's retraction is now CODE, and §0.28.3's date dimension is BUILT.** This is the "as built"
half of that design — §0.28.7 item 3's "integrate the lane-nature + calendar agent's work" is this
section. The retracted sentence is §0.27.2's *"a snapshot day the cron misses is lost,
deliberately"*: what it framed as a deliberate trade was a defect — three reference lanes registered
as a daily series with a collapsed window, re-snapshotting the newest settled day every hourly tick
forever. **`window_kind` and `current_snapshot` no longer exist.** §0.27.2 now carries an inline
strike-through pointing here.

**Three natures, declared per lane** (`foundation/parquet/lane_contract.py`, binding via
`code_styleguides/layer-lanes.md` §1a):

| nature | `day=` means | lanes |
|---|---|---|
| `daily_series` | the observation day | `fire-detections`, `fire-perimeters`, `sensors`, `signal`, `vegetation`, `water-gauges`, `weather-observations` |
| `release_series` | the publication's own valid/issue date | `burn-severity`, `drought` |
| `static_lookup` | a **version stamp** | `evacuation-zones`, `soil-survey`, `watersheds`, **`calendar`** |

**Watermark model.** Each `static_lookup` lane declares a source watermark — the source's own "when
did this last change". A partition dated at or after it means **current**: not a gap, not an
absence. Otherwise ONE snapshot is owed, dated **at the watermark**, never at the run date. Static
lanes declare `publication_lag_days=0`; a version stamp is not settled by waiting.

- **Every watermark column is a CHANGE event.** `geo.features.updated_at` qualifies *only* because
  `sql/ingest/refresh_features.sql` moves it inside an UPDATE gated on
  `properties IS DISTINCT FROM next_properties` — verified by reading that file, and it is what
  makes the whole model possible. `created_at` rides alongside because an insert moves only that
  (`drizzle/0022:13`).
- **`geo.geometry.last_confirmed_at` is excluded everywhere, and it is the trap.**
  `src/lib/server/services/usda-soil.ts:769,833` advances it on every re-fetch of unchanged ground.
  It is a poll clock; putting it in a version stamp would restore the daily churn.
- **`soil-survey` = `GREATEST(saverest vintage, feature created_at)`** — the vintage alone never
  advances for a lazily-warmed survey area carrying an old `saverest`, so those delineations would
  never reach a release.
- **`current` is reported separately from `watermark_unread`.** Both show zero missing days; they
  are different claims. A `--dry-run` over series lanes alone still opens no database.
- **A watermark later than today is refused** as a clock disagreement.

**THREE watermark SQL files, not the four the brief estimated** —
`sql/pipeline/lane_watermark_{watersheds,evacuation_zones,soil_survey}.sql`. The fourth static lane
is `calendar`, which by the brief's own constraint has no Postgres source, so its watermark is a
Python resolver over the clock and its own object listing instead.

**`forecastable` is bounded by nature and PROVEN against the filesystem.** `static_lookup` can never
be forecastable (enforced at construction). For the rest, `forecast_module` names the real module
and a test asserts `method/monte_carlo/` holds **exactly** those five stems, in both directions.
**§0.27.1's "six that can honestly forecast" is off by one: there are FIVE** — `fire-detections`,
`sensors`, `signal`, `vegetation` (as `vegetation_ndvi_forecast.py`, the one module whose stem is
not its slug), `water-gauges`. `fire-perimeters` and `weather-observations` are daily series that
ship no forecaster, which is allowed: the nature is the ceiling, the module is the claim.

**`burn-severity` keeps `cadence_days=1`, and that answers §0.27.5 item 5.** Its five MTBS releases
sit on no fixed step from the floor, so any cadence above one would step straight past real
releases. The ~2,000 honest absence markers are the price of an *irregular* release series.

**The conformed calendar dimension — stream 13, and §0.28.3's `dim_date` half.**
`foundation/parquet/calendar.py` (stdlib only) generates it; `warehouse/schemas/calendar.py` holds
the schema; `pipeline/lanes/calendar.py` writes it and **takes no `AsyncSession`**, because it has no
source system. **Fourteen columns:** `calendar_day` (grain), `year`, `quarter`, `month`,
`day_of_month`, `day_of_year`, `iso_year`, `iso_week`, `iso_day_of_week`, `is_month_start`,
`is_month_end`, `meteorological_season`, `day_of_year_sin`, `day_of_year_cos`.

The last three are §0.28.3's, not the brief's: **cyclical sin/cos day-of-year** because that is the
form a model consumes and it has no Dec-31→Jan-1 discontinuity (the phase is taken over the day's
OWN year length, so a leap year does not drift the cycle), and the **meteorological** season because
it is a fixed three-month grouping. **Astronomical season, time-of-day and daylight are
deliberately NOT here** — §0.28.3 puts the first two in a separate dimension and daylight in a solar
fact per `(cell, date)`, since it depends on latitude as well as date.

Floor **derived** as `min(history_floor)` over the twelve database-backed lanes = **2000-11-02**
(`fire-detections`); each version covers 800 days forward and must reach `today + 400`, so a 30-day
horizon from any as-of date resolves and the lane regenerates roughly annually rather than daily.
**10,225 rows at today's floor, spilling to two parts, which `partition_day_statuses` reads as ONE
day.** No fiscal years, holidays or trading days — unsourced policy in a dimension every lane keys
to is worse than no dimension. **Lanes key to it BY VALUE; no lane schema gained a column**, and the
observed/valid/available/warehouse-recorded clocks stay separate per
`docs/holonic-kimball-modeling.md`.

**A FOOTGUN THIS CHANGE CREATED, FOUND BY TRIPPING IT.** `--dry-run` used to be a pure object
listing with no database at all. Reading a watermark needs a session, so a dry run with a static
lane in scope now opens the loader DSN — **and `LOCAL_SOURCE_LOADER_DATABASE_URL` is unset in this
repo's `.env`, so it falls back to `DATABASE_URL` = PRODUCTION.** Invoking the verb during
verification therefore issued **one read-only aggregate SELECT against prod** (rolled back, 120 s
statement timeout, zero objects written). Disclosed rather than quietly dropped, because the same
surprise is waiting for the next operator. **`--skip-watermarks` now keeps the audit offline**, and
the flag's help text and the `--dry-run` help text both name the connection explicitly.

The unintended read did corroborate the model against reality: watersheds' watermark came back
`2026-08-07T18:38:59.832394+00:00` over **9,396 published rows** — the load day and row count
§0.26.6 recorded, arrived at by a completely different query.

**Not done, deliberately:** no `docs/lanes/calendar.md` (there is no source contract to document —
the rationale is in the two `AGENTS.md` files), no `planes/calendar.py` or
`pipeline/validation/calendar.py` (nothing to serve yet, and validating pure computation against a
source system it does not have would be theatre), and §0.27.3's `kind=forecast` schema blocker is
untouched. **Nothing was written to the real bucket and no database was written to at all** —
every test runs on `RecordingBackend`/`RecordingSession`.

### 0.27 HANDOFF 2026-08-22 (third) — twelve streams, an armed cron, and one blocker

**HEAD `abf777f`, PUSHED to origin/main.** Tree clean. Sweep: **3,528 passed · 110 skipped ·
`ruff` clean · `mypy --strict` clean** except the two pre-existing `matview_refresh.py:667`
errors §0.26.4 records.

#### 0.27.1 What is code-complete

All **twelve** streams (the eleven `geo.layers` lanes plus `drought`) now carry the full
`layer-lanes.md` §1 set: `warehouse/schemas/<slug>.py`, `pipeline/lanes/<slug>.py`,
`pipeline/validation/<slug>.py`, `planes/<slug>.py`, and `method/monte_carlo/<slug>.py` for the six
that can honestly forecast. The six `horizon: none` lanes ship **no** forecaster — §2 says an empty
forecast module reads as unfinished work rather than a settled property.

`interventions` stays in Postgres (§0.26.1). `drought` was **stream S2 all along** and was missed by
the first fan-out because that was scoped to `geo.layers`; **`forecast-observation` (S4) and
`agri.artifact` are still uncovered** — see §0.27.5.

#### 0.27.2 THE CRON IS ARMED, AND ONE REAL TICK HAS RUN

`infra/cron-ingest/Dockerfile` runs `agri-cli parquet-gap-fill --time-budget-seconds 900` as a third
verb, hourly, all three verbs' exit codes AND-ed (never `&&`, so an unrelated FIRMS outage cannot
starve the backfill).

**Incremental and backfill are ONE mechanism.** Window = `[history_floor, today - publication_lag]`;
missing days returned **newest-first**, so a newly published day *is* the newest gap. Lanes walk
**round-robin, one day per lane per round**, because sequential order would let `fire-detections`'
~9,400-day window eat a whole tick before `signal` wrote anything.

**Proven end to end against production**, not merely unit-tested:
```
{"lane":"drought","outcome":"filled","written":1,"parts":1,"rows":5,"bytes":3827547,"seconds":13.2}
```
and the re-run census then reported `data_days: 1` with that day gone from the missing list — so a
second tick does not rewrite it. **Idempotency is measured, not assumed.**

**Gap census, measured 2026-08-22: 15,083 missing days across 11 lanes** (before `drought` joined).
Every floor carries a `floor_basis` **citation as a data field**, echoed by `--dry-run`, because a
wrong floor invents thousands of phantom gap-days. Three worth knowing: `signal` uses lag **9**
(ERA5-Land), not NASA's 5; `water-gauges` floors at **2026-05-24**, rejecting both the borrowed 2022
constant and the 1990 `min()` trap; **`weather-observations` is the ONE guessed floor**, labelled
`FALLBACK` and pinned by a test that asserts it says so.

**`cadence_days` was added** so a weekly source is not registered as a daily one: `drought` chases
**211** Tuesday candidates instead of 1,472 days. **`burn-severity` is still `cadence_days=1` and
quarterly** — it will write ~2,000 honest-but-pointless absence markers before reaching its five real
releases. Self-terminating, but give it a cadence.

**Three lanes refuse historical backfill by construction.** `evacuation-zones`, `watersheds` and
`soil-survey` broadcast the caller's day with no date predicate, because Postgres holds no record of
what those current-state feeds published on a past day. Backfilling them would stamp today's state
onto a past date — fabrication. They collapse to one newest-day snapshot; ~~**a snapshot day the
cron misses is lost, deliberately.**~~

> **RETRACTED — declared in §0.28.2, built in §0.28.8.** The first two sentences stand: those
> exports genuinely cannot reconstruct a past day. The conclusion does not. Their partition day is
> a **version stamp**, not an observation time, so no calendar day ever carried an obligation and
> nothing can be "missed". They are now `static_lookup` lanes driven by a **source watermark**.

#### 0.27.3 THE BLOCKER — forecasts cannot be written, and all five agents found it independently

`ObjectStore.write_partition` resolves a schema **by layer name alone, never by `kind`**, so
`kind=observed` and `kind=forecast` share one schema — and there is **nowhere to put §3's six
mandatory provenance columns** (`forecast_run_id`, `random_seed`, `ensemble_size`, `horizon_days`,
`issued_on`, `quantile`/`draw_index`). §2's "identical column names" and §3's six columns are in
direct tension.

**Five working Monte Carlo implementations exist and none can write a partition.** Each agent
reported it separately and declined to fix it unilaterally. **Owner decision needed:** a per-kind
schema lookup, or nullable provenance columns on the observed side. Nothing forecast-related ships
until this is settled.

#### 0.27.4 What is real in the bucket, and the size picture

`signal` 4 days · `watersheds` 1 ten-part release · `drought` 1 release. **That is all** — the other
nine lanes have code but have never written. `signal` alone is 1,564 days short.

**§0.22.6's ~35 MB projection does not describe the shipped layout.** It measured July as ONE monthly
file at 1.43 B/row; day partitions measure **5.29 B/row** (3.7× worse — zstd has less to work with per
file), so the signal plane is **~130 MB**. And geometry breaks it entirely: `watersheds` alone is
**162 MB for 9,396 rows**, `drought` is **~765 KB/row**. **Do not size the warehouse from the signal
figure.** Owner has said storage is not a constraint, but the runbook should stop asserting a number
for a layout that was not shipped.

#### 0.27.5 Continuation, in priority order

1. **Settle §0.27.3.** Everything forecast-shaped is blocked behind it.
2. **Watch a real hourly cron tick** and confirm it fills gaps in prod. The deploy of `abf777f` was
   in flight at handoff; `railway redeploy --from-source` is required (plain `redeploy` reuses the
   prior snapshot and a Dockerfile change never takes effect).
3. **Write the missing half of `docs/lanes/weather-observations.md`** — it documents the `signal`
   stream, not the lane bearing its name (§0.26.8) — then replace that lane's guessed floor.
4. **Cover the two remaining streams:** `agri.forecast_observation` (S4, 116 MB) and `agri.artifact`
   (173 MB, in no plan at all).
5. **Give `burn-severity` a cadence** (§0.27.2).
6. **`planes/drought.py` needs DuckDB's spatial extension**, a one-time network fetch per machine. It
   will fail closed in a container the first time something serves from it. Not in the cron path today.
7. **Converge the two NDVI forecasters.** The `execution/` copy is wired, the `method/` one was brought
   into contract conformance — repointing callers needs files outside any lane's ownership.

#### 0.26.9 Continuation

1. **Extract the shared Open-Meteo client primitives out of `ingest/open_meteo.py`**, then create
   `ingest/weather_observations/`. Same shape as §0.26.3; the default-deny rule already covers it.
2. **Then fan out the remaining ten lanes** (§0.24 wave 2), grepping each target's sibling exports first.
3. **Nothing has written a real Parquet partition yet.** Wave 2 → 3's gate is a file a reader opened, not
   code complete. The bucket is live and empty; the first lane to land a partition proves the whole chain.
4. `method/monte_carlo/` and `execution/` still hold two near-identical NDVI forecasters — unchanged, still
   converge when `vegetation` moves (§0.25.5 item 6).

---

#### 0.25.6 Open questions

- ~~**What actually stays in Postgres?**~~ **ANSWERED 2026-08-22 — see §0.26.1.** The `interventions` lane
  contract recommends that lane stays — it is community-submitted, 0–2 rows, and Postgres is being retained
  for exactly that. Nothing else has been classified.
- **PMTiles still have no producer.** Martin serves them; nothing here makes them from a Parquet geometry
  store. Gates whether the map returns (§0.23.9).
- **`sensors` captures 16 measurement fields hourly and serves none of them**, and its history is ~3 weeks with
  no deeper archive obtainable. Product decision, not implementation.
- **`allowed_client_exposure` is `False` on every governed signal-plane row** while the map paints that data.
  The `vegetation` lane contract established its own source is `true`, so this is scoped to the signal plane —
  still unresolved there.

---

### 0.39 HANDOFF — session 9 close (2026-08-24). THE BACKLOG IS DRAINED. The next lever is gated.

**Supersedes §0.38's "the drain is what remains".** It ran, on Railway, and finished.

#### Goal

Drain the historical Parquet backlog, then shrink Postgres. The drain is **done**; the shrink is
**blocked on work that does not exist yet** (§0.39.6).

#### State — verified unless marked

- **Backlog: 12,365 → 0 lane-days.** All four drained lanes (`fire-detections`, `burn-severity`,
  `signal`, `vegetation`) report `remaining: 0`. VERIFIED from the drain's own JSON census.
- **The drain moved to Railway** as `plantgeo-parquet-drain`
  (`9ec08964-754d-4408-aff1-073a2618d28f`), config-as-code `infra/parquet-drain/railway.json`,
  commit `fe9b241`. **It is now STOPPED** (`railway down`, no active deployment).
- **Bucket: 95,017 objects, 2.11 GiB (2.26 GB decimal), 42,916 completion markers, 0 non-current
  versions.** VERIFIED by full listing.
- **Postgres is unchanged at 38 GB.** Nothing was dropped — the drain projects, it never deletes.
- **Nothing is ingesting.** See §0.39.5. This is the most time-sensitive item in this section.

#### 0.39.1 The measurement that justified the move

`parquet-drain` was never data-bound; it was round-trip-bound. Measured against production:

| | local (laptop → Railway) | Railway (in-region) |
|---|---|---|
| s per lane-day | 3.48 / 3.73 / 3.73 / 3.58 → **3.63 avg** | **0.62–0.74** |
| s per written day | 6.08 | 1.20 |
| lane-days / min | 16.8 | 81.3 |

A written day costs **~2.85 s fixed + ~0.27 ms/row** — 489× the data buys only 4.25 s. That fixed
cost is ~16 serialized object-store calls per day: a HEAD (`absence_exists`), the part PUT and the
marker PUT per zoom rung, plus a marker DELETE on part 0, issued one at a time by
`BotoObjectStoreBackend.put`. Three quarters of the drain's life was the wire.

**The whole remaining backlog drained in 1 h 44 min** (17:58 → 19:42 UTC) against a local projection
of ~19 h wall clock — **~11×**. The like-for-like per-day figure is ~5×; the rest is duty cycle
(the local loop gave the drain only ~52% of the machine, the forward path taking the rest).

#### 0.39.2 Coverage — 7 of 12 lanes provably whole, 3 genuinely incomplete

Rebuilt each lane's expected window from its own registry declaration (`history_floor` →
`today − publication_lag`, stepped by `cadence_days`) and compared against z13 markers + absences.

**Complete, zero missing:** `drought` (211), `fire-detections` (9,425), `sensors` (26),
`signal` (1,569), `vegetation` (1,474), `water-gauges` (91), `weather-observations` (22).

| lane | missing | cause |
|---|---|---|
| `burn-severity` | **1** — `2024-08-22` | Base rung written, coarse rungs never derived ⇒ never markable ⇒ re-taken on every census. **This is what made the service spin for 6 hours.** A real derivation bug, not a data gap. |
| `fire-perimeters` | **60** — 2025-08-02 … 2025-09-30 | Contiguous two-month hole in a `daily_series` lane. |
| `soil-survey` | **364, zero written** | The known 200,001-vs-200,000 key cap. The export raises *after* writing parts, leaving 478 orphan parquet files and no marker. Never self-heals. |

**UNVERIFIED — do not treat as gaps:** `evacuation-zones` (497) and `watersheds` (17) are
`static_lookup` lanes keyed to a source watermark, not a daily calendar, so a day-by-day expectation
model overcounts them by construction. Each has exactly 1 marker, which is what a healthy static
lane looks like. Confirming needs the watermark resolver, which was not run.

#### 0.39.3 Where the 38 GB actually is

| relation | total | heap | index | rows |
|---|---|---|---|---|
| `agri.signal_observation` | **25.86 GB** | 10.71 | **15.14** | 46,068,872 |
| `geo.features` | 7.90 GB | 3.80 | 2.56 | 5,025,009 |
| `geo.geometry` | 2.93 GB | 1.16 | 1.34 | 3,277,801 |

Three relations are **96.6%** of the database. **No bloat** (nothing over 100k dead tuples) and
**WAL is only 512 MB** — the volume is honestly full, not garbage. The same table is **0.280 GiB in
Parquet: 92× smaller**, 38× against heap alone.

#### 0.39.4 CORRECTION — `idx_scan = 0` was used as evidence and it is not

This section's first draft recommended dropping
`uq_signal_observation_release_cell_signal_time` (10.78 GB) on the grounds that it had **0 scans,
ever**. That is wrong, and this runbook's own header (line 13) already gates it: `stats_reset` is
NULL, which does not mean "since forever".

Confirmed 2026-08-25: **`pg_postmaster_start_time()` = 2026-08-24 14:40:46 UTC** — the instance
restarted 10 h 40 m before the reading, and that is the same restart that crashed
`plantgeo-ingest-cron`. `signal_observation` also shows **`n_tup_ins = 0`** in that window, so a
*unique-constraint* index had no writes to enforce against and would not register regardless.

The index inventory, for whoever revisits it:

| index | size | scans (unbounded-below window) | note |
|---|---|---|---|
| `uq_..._release_cell_signal_time` | 10.78 G | 0 | UNIQUE **constraint** — needs `ALTER TABLE … DROP CONSTRAINT`, not `DROP INDEX` |
| `ix_..._cell_time_signal` | 2.85 G | 4,163,429 | **hot — serving production** |
| `pk_signal_observation` | 0.97 G | 0 | PRIMARY KEY |
| `ix_..._release_time` | 0.55 G | 606 | |

**Owner decision 2026-08-24: HOLD all index drops until the Parquet API cutover.** If they are
dropped later, the justification must be architectural ("this table is no longer read or written"),
never the counter.

#### 0.39.5 Nothing is ingesting, and this was a side effect

Commit `fe9b241` shipped the already-staged removal of `"cronSchedule"` from
`infra/cron-ingest/railway.json`. That did **not** pause the cron — it converted it from a cron
service into an ordinary one, so it started immediately on the push, ran one full forward path
(`ingest-all` → `jobs-pulse` → `parquet-gap-fill`), and then, with `restartPolicyType: NEVER`,
exited and stayed exited.

Combined with the local loop being stopped, **no forward path runs anywhere.** Last ingest completed
~2026-08-24 19:30 UTC.

**Owner directive 2026-08-24:** do *not* simply restore the schedule. Ingestion is to be **repointed
to write the Parquet lanes directly**; ingestion into Postgres stops and that code is removed.

> **THE TRAP IN THAT DIRECTIVE.** Every lane adapter in
> `pipeline/parquet/lane_registry.py` (`_fill_signal`, `_fill_burn_severity`, …) reads **from
> Postgres**. Parquet is currently a *derivative* of Postgres, not an independent store. Removing
> the Postgres ingest path severs the thing that produces Parquet. Repointing is a rewrite of the
> ingest layer, not a configuration change, and until it exists there is no ingestion at all.

#### 0.39.6 The shrink is gated, and the gate is unbuilt

Owner picked "finish the DB shrink" as the next milestone. **Recorded tension:** the shrink's two
real levers are the 15.14 GB of indexes and the 25.86 GB table, and both are gated on serving no
longer reading Postgres. What remains executable *today* is only the TimescaleDB drop, which is
small.

**The Parquet read API does not exist.**

- `src/lib/server/services/parquet-plane-client.ts` — 516 lines, well-built (wire format quarantined
  in one `WIRE` section, days passed through as opaque `YYYY-MM-DD` strings with a test that fails
  if a date conversion appears, faults thrown through the `bounded-upstream` taxonomy). **Nothing
  imports it** except its own test and `parquet-envelope.ts`.
- `services/agri-data-service/` has **no HTTP surface at all** — zero files referencing FastAPI,
  APIRouter or uvicorn. It is a CLI.
- No `duckdb`/parquet dependency in `package.json` (correct for this design — Python serves the
  reads — but confirms nothing reads Parquet app-side either).

#### 0.39.7 TimescaleDB — safe to drop, not dropped

`timescaledb` 2.29.0 and `timescaledb_toolkit` 1.24.0 are installed. **One hypertable,
`tracking.positions`, and it is EMPTY** — 0 rows, 0 chunks, 40 kB. `DROP EXTENSION … CASCADE` costs
no data. Its definition was captured for faithful recreation as a plain table:

```sql
CREATE TABLE tracking.positions (
    "time"   timestamptz NOT NULL,
    asset_id uuid        NOT NULL REFERENCES tracking.assets(id) ON DELETE CASCADE,
    heading double precision, speed double precision, altitude double precision,
    metadata jsonb DEFAULT '{}'::jsonb,
    geom     geometry
);
CREATE INDEX idx_positions_asset ON tracking.positions USING btree (asset_id, "time" DESC);
CREATE INDEX idx_positions_geom  ON tracking.positions USING gist (geom);
CREATE INDEX positions_time_idx  ON tracking.positions USING btree ("time" DESC);
CREATE UNIQUE INDEX positions_asset_time_unique ON tracking.positions USING btree (asset_id, "time");
```

**Caveat:** `alembic/versions/20260719_0001_agri_foundation.py` and
`20260816_0024_matview_refresh_state.py` both reference timescaledb, so a manual drop drifts from
migration state and a fresh `alembic upgrade head` would recreate it. Do it as a migration.

**Execution was blocked** by the harness permission classifier. The prepared script is at
`<scratchpad>/drop.py` and needs a Bash permission rule or a re-run with approval.

#### 0.39.8 Decisions (2026-08-24, owner unless noted)

1. **The drain runs on Railway, not locally.** Measured ~5× per lane-day, ~11× wall clock.
2. **The local loop is retired.** `scripts/warehouse_status.py` reporting
   `SUPERVISOR IS NOT RUNNING` is now **correct**. Do **not** restart the loop — it would put a
   second writer on the same database and re-create the contention the move removed.
3. **Index drops HELD** until the Parquet API cutover (§0.39.4).
4. **Ingestion repoints to Parquet lanes only**; Postgres ingestion stops and its code is dropped
   (§0.39.5).
5. **Next milestone: finish the DB shrink** — read alongside the tension in §0.39.6.
6. `plantgeo-parquet-drain` was created with `create_service` + `update_service`
   (`root_directory: /`, `railway_config_file: infra/parquet-drain/railway.json`). **The auto-deploy
   fires before `update_service` lands**, so the first build always builds the *root* Dockerfile and
   dies on `NEXT_PUBLIC_PMTILES_URL must be a reviewed production URL`. Fix is
   `railway redeploy --from-source`; a plain redeploy reuses the old snapshot and fails identically.

#### 0.39.9 Assumptions not tested

- `evacuation-zones` and `watersheds` are complete · default taken: treated as complete ·
  to reverse: one watermark-resolver run.
- The TimescaleDB drop is still wanted · default taken: left installed, prepared but unexecuted ·
  to reverse: run the script (needs permission).
- `continuous-warehouse-loop.sh` and `scripts/warehouse_status.py` stay **untracked** · default
  taken: not committed · to reverse: one `git add`. The status script is the only monitoring tool
  for the warehouse and exists on this machine only.

#### 0.39.10 Continuation plan

1. **Decide the ingest repoint shape** (§0.39.5) before writing code — whether lane adapters read
   Postgres for a transition period or ingestion writes Parquet directly from source. Everything
   else depends on this and it is not yet decided.
2. **Build the Parquet read API** in `services/agri-data-service/` against the four routes
   `parquet-plane-client.ts`'s `WIRE` section already specifies. This is the gate on the shrink.
3. **Drop TimescaleDB as an alembic migration** (§0.39.7) — the only shrink step executable now.
4. **Fix `burn-severity 2024-08-22`** — the coarse-rung derivation failure. Until fixed, any drain
   or gap-fill pass will spin on it and re-export it forever.
5. **Close `fire-perimeters` 2025-08-02 … 2025-09-30** and **unblock `soil-survey`'s key cap**.
6. **Then** revisit the index drops with an architectural justification, not a counter.

---

### 0.40 VERIFICATION — session 10 (2026-08-25). The pivot verifies; §0.39's runner claims did not survive an hour.

**Supersedes §0.39 "State" bullets 2 and 5 and §0.39.10 step 1.** Everything below was measured against
production, the bucket, and Railway this session; nothing is inferred from a counter.

#### 0.40.1 CORRECTION — the drain was NOT stopped and ingestion DID run

Commit `72845d3` (the readiness fix, 01:44 UTC) auto-deployed `plantgeo-parquet-drain`,
`plantgeo-ingest-cron` and `plantgeo-cron-soilgrids` at 01:45. `440d9b5` did it again at 02:04. Railway
deploys every repo-sourced service on every push; with no `cronSchedule` and `restartPolicyType: NEVER`,
**a push is one ingest pass**, and the drain restarts each time and spins on the §0.39.2 bug — its log is
the same line every 20 s: `burn-severity 2024-08-22 raised: the base rung is written but its coarse rungs
are not`. Those batch jobs (FIRMS archive walk; `jobs_pulse_tick_failed`, `matview-refresh` 106 standing
dead letters; quality gates) are what refilled the DB container from 0.11 GB to 7–11 GB within minutes of
the Timescale restart and pushed `pg_stat_database.temp_bytes` from 22 to 26 GB.

**Both were taken down with `railway down` at 02:05 UTC (owner: stop both).** They come back on the next
push until `cronSchedule` is restored deliberately (track P0).

#### 0.40.2 CORRECTION — "backlog 0" was z13 only, and the base signal rung is on an old schema

| lane | z13 parts | z9 / z5 / z0 |
|---|---|---|
| `signal` | 1,560 (2022-04-30 … 2026-08-06) | 0 / 0 / 0 |
| `fire-detections` | 8,357 | 0 |
| `vegetation` | 1,195 | 0 |
| `burn-severity` | 8 (4 complete) | 0 |

No coarse rung exists for any lane; `_complete.json` is per tier and the census read z13. The written
signal files carry 10 columns and **no `cell_longitude`/`cell_latitude`** — `warehouse/parquet/schema.py`
declares both non-nullable and `GridAggregation` keys on them — so the base must be re-exported (~1,560 ×
0.7 s on Railway) before a coarse rung can be derived or a viewport read can filter. `signal` 2026-08-08..16
are `absent.json` written because Postgres had no rows yet; `write_partition` refuses a day carrying an
absence marker, so they stay absent until retracted.

#### 0.40.3 The performance question, measured like-for-like

Postgres (prod, `EXPLAIN (ANALYZE, BUFFERS)`, PNW bbox, the map's own soil-field reads):

| query | cold | warm |
|---|---|---|
| `soilFieldNewestDay` (LATERAL over 1,105 cells) | **25.0 s** — 17,336 pages off the volume ≈ 5 MB/s | 110 ms |
| `soilFieldCells` | 49 ms | 41 ms |
| `geo.soil_field()` lattice | 1.24 s | 55 ms |
| watersheds watermark `max()` over `geo.features` | 12.7 s | 263 ms |

DuckDB over the signal lane (one z13 day = 26 KB; 37 days = 2.85 MB): compute-only 1 ms / 5 ms (30-day
newest-day scan) / 19 ms (535k rows); from the laptop at ~200 ms RTT 230–930 ms and 1.3–3.7 s; in-region
RTT is ~5× lower (§0.39.1) → ~50–100 ms per served day. **Warm is a wash; cold is two orders of
magnitude, and a 38 GB volume has no cold-proof path.** Postgres' own shared memory is 289 MB, `work_mem`
16 MB; the container's GBs are page cache + temp from batch jobs. The memory win comes from taking batch
off Postgres — which is the repoint — not from what serves reads. `pg_stat_statements` is available but
not preloaded, so there is no per-query ledger to read. DuckDB against this bucket needs `URL_STYLE
'vhost'` (path style 404s) and explicit keys (S3 globbing does not list here).

#### 0.40.4 TimescaleDB drift

Gone from `pg_extension` (manual drop, restart 01:37 UTC) and `shared_preload_libraries` still says
`timescaledb,pg_textsearch` (Railway-managed image — not changeable from here).

**Prod is one revision behind the tree.** `440d9b5` added
`alembic/versions/20260825_0026_drop_timescaledb_extensions.py` and moved both pins to it
(`tests/conftest.py:34`, `routes/health/contracts.py:17`), while production's `alembic_version` still
reads `20260817_0025` (measured 02:00 UTC). Nothing gates a deploy on the agri pin today, so this is
drift rather than an outage — but a `/api/ready`-style agri readiness check would now disagree with prod.

**A fresh build is deadlocked.** `20260719_0001_agri_foundation.py:34` requires `timescaledb` to be
*installed* before it will create the `agri` schema, while `tests/test_migration_runtime_contract.py:34`
asserts `infra/local-warehouse/enable-extensions.sql` no longer creates it — so `alembic upgrade head`
from zero needs an operator to hand-install timescaledb purely so `0026` can drop it again. That is the
strongest independent argument for the owner's call: **reset the alembic history to a greenfield
baseline** (agri schema only — drizzle owns `geo` and `tracking`), forward migration only as fallback.

#### 0.40.5 Decisions (2026-08-25, owner)

1. **Repoint shape: bridge, then cut per lane.** Restore the schedule as a transition writer; direct
   writers lane by lane, rolling-window lanes first (`sensors`: NWS keeps ~6 days, days after ~08-31
   without ingest are unrecoverable); retire each Postgres path only after parity.
2. **Both runners stopped** (§0.40.1).
3. **Zoom ladder: fix the derivation and materialise all four rungs** (pivot track slice d1 owns it).
4. **Alembic reset to current state** (§0.40.4).

Track: `conductor/tracks/postgres_shrink_ingest_repoint_20260825/`. Memories:
`plantgeo-repoint-decisions-2026-08-25`, `plantgeo-parquet-coarse-rungs-unbuilt`,
`plantgeo-postgres-memory-is-batch-not-serving`, `plantgeo-push-redeploys-drain-and-ingest`.

---

### 0.41 SESSION 11 (2026-08-24) — the warehouse answered a science question, and the answer is narrower than it looks

**This section adds no infrastructure claims and supersedes nothing.** It records the first
analytical use of the drained Parquet warehouse, four new tracks, and one new module. Read it
for the measured results and — more importantly — for **the three claims that were briefly
believed here and then refuted by their own evidence** (§0.41.4).

**Provenance for every number below:** DuckDB over the object store, read-only, 2026-08-24.
Nothing was written to Postgres. Feature window 2026-04-01 → 2026-06-30; outcome window opens
2026-07-01; USDM release 2026-08-18. Code is now `services/agri-data-service/analysis/`.

#### 0.41.1 The warehouse is analytically usable, and it validated itself

Starting question was a user asking for terrain similar to `43.643120, -118.062598`. That point
resolves to cell `sentinel2-ndvi-0p25deg:43.6250:-118.1250`.

**CONFIRMED — the pivot's output is queryable end to end with no Postgres in the path.** Signal,
vegetation, drought, fire-detections and fire-perimeters were all read directly from the bucket
and joined on `cell_id`. The one Postgres read in the whole session was the cell dimension
(`agri.spatial_cell`, 1,568 cells on the sentinel2 grid + 397 nasa-power).

**CONFIRMED — the ingest is accurate.** The `fire-perimeters` lane reports Coleman Creek at
**308,864 acres**; InciWeb reports **308,863**. One acre apart on an independently-sourced
figure. That is the strongest external validation of the pipeline recorded so far.

**A grain fact worth knowing:** era5-land signals are stored against **sentinel2-ndvi-0.25°
cells**, not an era5 grid. `support_key` names the upstream resolution; `cell_id` names the
analysis grid. Vegetation and soil moisture therefore co-register with no regridding — which is
what made this analysis cheap. 1,470 of 1,568 cells carry both; 98 lack soil and drop out of any
inner join.

**A trap in the object layout:** `layer=signal/` still holds **386 pre-zoom-ladder keys** at a
shallower depth (`kind=observed/year=…`, no `zoom=` segment). A `zoom=*` glob double-counts them.
Every reader must pin a tier explicitly; `analysis/warehouse_session.py` does.

#### 0.41.2 Fire occurrence is predictable; the skill is real and modest

Leakage-free: features close 30 Jun, Coleman Creek ignited 25 Jul. 1,470 cells, 492 burned
(33.5 %). Scored on the rangeland strata only (greenness quartiles 1–2; 736 cells, 268 burned).

| signal | AUC | direction |
|---|---|---|
| composite index | **0.725** | — |
| vapour pressure deficit | 0.697 | higher burns |
| soil temperature (0–7 cm) | 0.677 | higher burns |
| surface soil moisture | 0.605 | **lower** burns |
| spring NDVI | 0.588 | higher burns |

Decile lift: **14.9 % burned in the lowest decile → 67.1 % in the highest**, monotonic above
decile 3.

**The composite beats VPD alone by ~0.03.** Do not sell the composite as a material improvement
over a single-variable screen; VPD is doing the work.

**The index is stratified for a reason and does not transfer.** VPD/soil-temperature separate the
dry interior from the wet coast, so an unstratified AUC partly measures "the interior burns".
Within greenness quartiles the VPD AUC runs 0.693 / 0.746 / 0.667 / **0.586** — it decays to
near-nothing in closed forest. **Applying this index to forest is a misuse.**

Within the sparsest quartile only, NDVI flips to a strong positive predictor (**0.674**): more
spring fine fuel, more fire. Stratum-specific; the sign does not generalise.

#### 0.41.3 The shade hypothesis: mechanism real, net effect opposite

Tested because the owner proposed that canopy/shade should suppress fire in treeless grassland.

Raw correlations support it — cover vs soil temperature **−0.339**, vs VPD **−0.264**, vs surface
moisture **+0.534**. **Holding VPD quartile constant, most of it is climate confound:** soil
temperature moves only ~0.5 °C across cover terciles (Q3: 17.4 / 17.5 / 16.9). Surface moisture
genuinely does roughly double (Q3: 0.086 → 0.177).

**But burn rate rises with cover inside every dryness quartile** — Q3 goes 8.1 % → 63.9 % → 63.9 %
from sparsest to greenest. **Fuel load dominates microclimate, decisively.** The largest jump is
sparsest→middle, which is the fuel-**continuity** threshold: sparse cover cannot carry fire
between plants, moderate cover can.

**Scope limit, stated so it is not over-read:** NDVI at 28 km cannot separate tree canopy from
grass. This refutes "greener is safer" at landscape scale. It does **not** test tree shade, and
does not by itself refute silvopasture.

#### 0.41.4 THREE CLAIMS REFUTED BY THEIR OWN EVIDENCE

Recorded because each is plausible, each was briefly believed in this session, and each is wrong.

1. **"Fire intensity is predictable from the same signals."** REFUTED. Raw `frp_sum` suggested a
   ~47× inversion. `frp_sum` sums over detections and so confounds intensity with duration and
   extent; **per detection** the range is 12.8–29.8 MW and the correlation with the index is
   **+0.131**. No intensity signal exists here.
2. **"Large fires concentrate in low-risk cells."** REFUTED. Mean detections per burned cell by
   risk band were 1256 / 957 / 398, but medians are **16 / 84 / 13**. A handful of megafire cells,
   not a gradient.
3. **"The fire layer supports a multi-year trend."** REFUTED. `fire-detections` in the warehouse
   holds **2000 (35 d), 2001 (233 d), 2002 (270 d), 2003 (1 d), 2026 (224 d)** — a **23-year hole,
   2003–2025**. 2026 figures are sound; any cross-year trend is not. An empty year means *not yet
   backfilled*, never *no fire*.

**Consequence for the carbon question: no carbon-targeting signal was found.** The index predicts
where fire occurs, not how much carbon a fire releases. Those are separate problems and nothing
measured here says they share a map.

#### 0.41.5 The carbon lane is identified but unbuilt

`geo.published_raster` catalogues **ISRIC SoilGrids** COGs + PMTiles over the full extent
(−127.04→−110.17, 42.00→49.00), including the two that matter:

| product | unit | scale_divisor | range | object key |
|---|---|---|---|---|
| `soc_0-5cm_mean` | g/kg | 10 | 5.7 – 462.1 | `raster/soil/soilgrids-v2.0/soc_0-5cm_mean_4326.tif` |
| `ocd_0-5cm_mean` | kg/m³ | 10 | 10.3 – 111.2 | `raster/soil/soilgrids-v2.0/ocd_0-5cm_mean_4326.tif` |

**Blocked on a reader, now unblocked by decision.** No raster library was installed; owner
approved adding `rasterio`, and it is pinned in `pyproject.toml` (`rasterio>=1.3,<2`) so a
future `uv sync` cannot silently drop it — **installed with `uv pip install`, deliberately not
`uv sync`**, per the known pytest-removal behaviour.

`geo.soil_survey` carries **no** carbon field (SSURGO map units — drainage class, land capability,
soil series). SoilGrids is the only carbon source in the estate.

External grounding for scale (literature, not measured here): cheatgrass conversion costs
**6–9 Mg C/ha belowground**, roughly double the aboveground loss, with the loss appearing
**below 20 cm and more than five years after fire**. At 7.5 Mg C/ha over Coleman Creek's 124,990
ha that is ~**0.94 Mt C ≈ 3.4 Mt CO₂e** *if the ground converts* — which is decided in the
post-fire window, not at the fire.

#### 0.41.6 THE MEMORY INCIDENT — why `max_temp_directory_size='0GiB'` is not tuning

**A local DuckDB query consumed the host and disrupted unrelated processes.** Cause: a cross-join
of 1,568 cells against 1,045 CONUS-wide USDM multipolygons (up to ~140k vertices, ~2.2 MB WKB
each), which materialises a geometry reference per output row — 1.6 M rows carrying large
geometries. It spilled to local disk until the machine was unusable.

Owner directive: **do not use the local volume.** `analysis/warehouse_session.py` now sets
`memory_limit=1600MB`, `threads=3`, `max_temp_directory_size='0GiB'`, and opens `:memory:` with
no database file. The same query now raises `OutOfMemoryError` in ~1 s and the host is unaffected.

Two disciplines make the spatial work cheap enough never to approach the ceiling:

1. **Clip before probing.** `ST_Intersection` against the analysis envelope cuts the largest USDM
   polygon **140,352 → 6,300 vertices (22×)** with *no* precision loss inside the probed region.
2. **One polygon per query.** After clipping, a full 5-band release costs ~**0.1 s per band** —
   down from OOM.

`ST_Simplify` was evaluated and **rejected as the primary lever**: 0.002° tolerance gave only 4×,
and unlike clipping it can move a boundary and flip a cell.

**Also measured: USDM severity bands are EXCLUSIVE, not nested.** Zero cells matched more than one
category on the 2026-08-18 release. The nested reading is the common assumption and would assign
the wrong level everywhere.

#### 0.41.7 Ingestion constraint restated (owner, 2026-08-24)

**All new data ingestion targets day-partitioned Parquet/GeoParquet**, aimed at serverless query
on cold compute. The serving pattern is **one day per layer**; only the MCP asks for historical
day lists. New lanes must declare a nature per `foundation/parquet/lane_contract.py` — SOC is a
`static_lookup` keyed to a source watermark, **not** a daily series, because it does not vary by
day and must not be written 365 times a year.

#### 0.41.8 What was created

**Module** — `services/agri-data-service/analysis/` (sibling of `scripts/`, outside
`src/agri_data_service/`, so `tests/test_layer_import_contract.py` does not bind it):

| file | role |
|---|---|
| `AGENTS.md` | the memory incident, the leakage trap, results, the three refutations |
| `warehouse_session.py` | bounded read-only DuckDB session; tier pinning; spill disabled |
| `fire_risk_index.py` | leakage-free feature plane, stratified index, rank-based AUC |

**Tracks** — four chartered 2026-08-24:

| slug | status | note |
|---|---|---|
| `regional_fire_risk_surface_20260824` | chartered | the cross-state prioritisation surface as a day-partitioned lane |
| `rangeland_carbon_lane_20260824` | chartered | SoilGrids SOC/OCD as a `static_lookup` lane |
| `fire_feature_plane_validation_20260824` | blocked | needs the historical backfill; that is pivot item B |
| `rangeland_partnership_outreach_20260824` | chartered | non-software; programme and feedstock-policy lane |

`fire_risk_zone_forecast_20260823` was **updated, not duplicated** — this session is empirical
evidence for its §2 feature plane, which it asserted was "BUILDABLE NOW" and which is now built
and scored.

#### 0.41.9 The one blocker, and it is already chartered

**Validation needs a second fire season.** AUC 0.725 was fit and scored on 2026 alone; it is
in-sample and optimistic. The 23-year `fire-detections` hole is the blocker — and it is **not new
work**: it is `parquet_duckdb_pivot_20260823` **item B**, the bulk Postgres drain, where
`fire-detections` is **69 % of the 13,037 lane-days**. Finishing that drain is what converts these
associations into something a district could fund against.

Deliverable for the owner: <https://claude.ai/code/artifact/853658d5-8424-411a-9e81-1de7cd7758ea>
---

### 0.42 ORCHESTRATION PACKET — session 10 close (2026-08-25). Three lanes, three monitors, one cutover.

**Reverses §0.21.8's "stop running workflows" for this phase, by owner request 2026-08-25.** That
directive was written when workflows were producing unverified sprawl; the two tracks now carry
collision-proofed file partitions, which is the precondition it was missing. Small steps still apply
*inside* a slice.

#### Goal

Drive the planned work in `conductor/tracks/` to code completeness along three lanes running
concurrently — **data completeness & ingestion**, **the API layer**, **the UI display surface** — each
with a monitor agent orchestrating its own lane. Then one adversarial pass, then a hard cutover to
Parquet-first serving. Done looks like: every layer that can render real data does so at every zoom
rung, the map reads Parquet, and no layer keeps a Postgres read path it no longer needs.

#### 0.42.1 The lane map — monitors DRIVE existing slices, they do not re-partition

Owner decision: the monitors dispatch the slices the two tracks already prove disjoint, and append new
slices only for work no track covers. Re-cutting the boundaries would discard a proof that cost real
analysis. **Every `owns` list is `confidence: planned` and must be re-verified with a grep before
launch** — HEAD has moved since both were baked.

| lane | monitor owns | existing slices | new slices needed |
|---|---|---|---|
| **A · data & ingestion** | lanes produce complete, correct Parquet | shrink `s0` `s1` `s2` `s3` `s4` `s5`; pivot `d0` `d1` | — |
| **B · API layer** | the four routes serve what the client expects | pivot `d3` | `b1` coverage endpoint reproducing `getSliderCapabilities` |
| **C · UI display surface** | every layer renders real data at every zoom | pivot `d4` `d5` | `u1` capability rows · `u2` zoom resolution · `u3` per-layer cutover · `u4` the four non-Parquet surfaces |

**§0.41's four chartered tracks are downstream consumers of lane A, not extra lanes.**
`regional_fire_risk_surface_20260824` and `rangeland_carbon_lane_20260824` each need a lane that
lane A produces; `fire_feature_plane_validation_20260824` is addressed in §0.42.12;
`rangeland_partnership_outreach_20260824` is non-software. None of them are dispatched by a monitor
in this programme — they become runnable once lane A completes.

`s6` (the final shrink) sits **after** the adversarial pass and belongs to no lane — it is the last
step of the whole programme, not of a lane.

**Cross-lane write collisions to hold:** `pipeline/parquet/lane_registry.py` and `cli.py` are lane A's
(owner `s2`); `src/lib/server/services/environmental-read-model.ts` (4,432 lines) is lane C's and is
touched by `d4`, `u1` and `u3` — **serialise those three, never run them concurrently**;
`alembic/versions/` is `s1`'s and `s5`/`s6` append after it.

#### 0.42.2 Step zero is a contract freeze, and it is what makes concurrency safe

Lanes B and C would otherwise both be guessing. The wire contract already exists in one place —
`src/lib/server/services/parquet-plane-client.ts` (`WIRE`: `basePath /api/v1/parquet`, routes `day`
`window` `release` `coverage`, params `layer` `kind` `zoom` `bbox` `day` `first_day` `last_day`
`as_of`) plus the four envelope states in `parquet-envelope.ts`. Freezing it means: a golden fixture
per route, asserted by a Python contract test on the serving side and the existing TS test on the
client side, so lane B builds the routes and lane C builds the readers **against the same artefact**
without waiting for each other. Until that fixture exists, concurrency is a rework generator.

**Lane B inherits a hard constraint from §0.41.6.** An unbounded local DuckDB query consumed the host
on 2026-08-24 — a cross-join materialising ~140k-vertex USDM geometries per output row, spilling to
disk until the machine was unusable. The serving API runs DuckDB on request paths, so every session it
opens must carry the same bounds `analysis/warehouse_session.py` already proves:
`memory_limit`, a `threads` cap, `max_temp_directory_size='0GiB'` (spill DISABLED, so an over-budget
query raises in ~1 s instead of eating the host) and `:memory:` with no database file. **Clip before
probing** is the companion discipline — `ST_Intersection` against the request envelope cut the largest
USDM polygon 140,352 → 6,300 vertices with no precision loss inside the probed region, and
`ST_Simplify` was evaluated and rejected as the primary lever because it can move a boundary and flip
a cell. A serving route that spills is an outage, not a slow query.

#### 0.42.3 Layer census — what "all 19 render real data" actually costs

Measured against production 2026-08-25 (`geo.features` published rows per layer, plus the planes):

| layer toggle | render | backing | rows | standing |
|---|---|---|---|---|
| `fire` | component | fire-detections | 3,039,749 | Parquet + tRPC ready |
| `water` | component | water-gauges | 1,443,978 | Parquet + tRPC ready |
| `drought` | component | `geo.drought_areas` (→2026-08-18) | 1,045 | Parquet + tRPC ready |
| `weather` | component | weather-observations | 35,772 | Parquet + tRPC ready |
| `vegetation` | component | vegetation | 185,302 | Parquet + tRPC ready |
| `soil-moisture` / `soil-temperature` / `soil-vpd` | component | signal plane | 46 M | Parquet, needs re-export |
| `soil-survey` | component | soil-survey | **238,986** | source NOT empty; cap removed in `68da7af`; the lane's 0-written is an export failure, not a data gap |
| `sensors` | style | sensors | 206,947 | Martin tiles, Postgres |
| `watersheds` | style | watersheds | 9,396 | Martin tiles, Postgres |
| `evacuation-zones` | style | evacuation-zones | 679 | Martin tiles, Postgres |
| `burn-severity` | style | burn-severity | 541 | Martin tiles; walk has not reached 2020-2024 |
| `fire-perimeters` | style | fire-perimeters | 207 | Martin tiles; 60-day hole 2025-08-02…09-30 |
| `interventions` | style | 2 features, **0 published** | — | **not a Parquet lane** — community, stays in Postgres (§0.26.1); blocked on a publish step that is never invoked, not on data |
| `strategy-recommendations` | component | `geo.v_strategy_recommendation_cells` | — | **not a Parquet lane** — needs the ML label plane, which is label-blocked |
| `soil` | component | — | — | `permanentlyUnavailableReason` set deliberately (raster) |
| `demand-heatmap` | component | computed | — | derived surface, no warehouse row |

**The honest reading of "all 19 at every zoom":** twelve layers are reachable by code work in these
lanes. Four are not Parquet lanes at all and cannot be made so by this programme —
`interventions` (community/Postgres by design), `strategy-recommendations` (ML-label-blocked),
`soil` (raster, deliberately unavailable), `demand-heatmap` (derived). Lane C's `u4` gives those four
an honest published state rather than a silent blank; it does not invent producers for them.

#### 0.42.4 The cutover, and the tension the two answers create

Owner chose **all 19 layers** *and* **Parquet-only, hard cutover, no fallback**. Taken literally and
simultaneously those conflict: a big-bang hard cutover over lanes with known holes (no coarse rung
anywhere, `fire-perimeters` 60 days, `soil-survey` 0 written) is a visible outage on the day it runs.

**Resolution, and the assumption to challenge first:** the cutover is **per layer, hard, staged**. A
layer cuts the day its Parquet lane proves complete, and its Postgres read path is deleted in that
same change. No layer ever carries two read paths — which is what "no fallback" protects — and no
layer is cut before its data exists, which is what "all 19" needs. The programme's cutover is
complete when the last eligible layer has cut, not on a single date. To reverse: one dated big-bang
release, which re-introduces the outage this avoids.

#### 0.42.5 Decisions (2026-08-25, owner, from the gate)

1. **Monitors drive existing slices** (§0.42.1) — chosen over re-partitioning because the two tracks'
   boundaries are already proven disjoint and re-cutting discards that proof.
2. **Three lanes run in parallel behind a contract freeze** (§0.42.2) — chosen over sequencing for the
   ~3× wall-clock, with the freeze as the rework guard.
3. **Completeness bar is every layer that can render real data, at every zoom** — chosen over the
   narrower "six Parquet-backed layers"; the four non-lanes get honest state (§0.42.3).
4. **Cutover is Parquet-only with no fallback path** — staged per layer (§0.42.4).
5. **Workflows are re-authorised for this phase**, reversing §0.21.8, because the partitions now exist.

#### 0.42.6 Assumptions — highest reversal cost first

- **The cutover is per-layer rather than one release** · default taken: staged, each layer cutting when
  its lane completes · to reverse: a dated big-bang release and an accepted outage window.
- **`interventions`, `strategy-recommendations`, `soil`, `demand-heatmap` are out of the Parquet
  cutover** · default taken: honest published state, no producer invented · to reverse: three new
  producers and, for strategy, unblocking the ML label plane — a track of its own.
- **Monitors run as long-lived orchestrators, one per lane, dispatching slices** · default taken: three
  concurrent monitors · to reverse: collapse to serial dispatch, losing the parallelism but no work.
- **Lane A's `s1` (alembic baseline) belongs to the data lane** · default taken: lane A owns it, since
  it gates `s5`/`s6` migrations · to reverse: hand it to whoever runs the shrink; no code changes.
- **The adversarial pass runs once, after all three lanes report complete, before `s6`** · default
  taken: one pass at the join · to reverse: per-lane adversarial passes, which costs three reviews
  instead of one but catches lane-local defects earlier.
- **Token spend is unbounded across the three monitors** · default taken: no cap set · to reverse: a
  per-lane budget, which the monitors would need told up front.

#### 0.42.7 Relevant files

- `src/lib/server/services/parquet-plane-client.ts` — the `WIRE` block is the contract to freeze;
  516 lines, nothing imports it yet but `parquet-envelope.ts` and its own test.
- `src/lib/server/services/environmental-read-model.ts` — 4,432 lines, the contested file: `d4`, `u1`
  and `u3` all write it. Serialise. Carries `PUBLISHER_NAMED_DAY_RULE` and the soil/climate field reads.
- `src/lib/map/layer-registry.ts:177` — `LAYER_REGISTRY`, the census in §0.42.3 in code form;
  `warehouseLayerName` is what publishes a slider capability row.
- `services/agri-data-service/src/agri_data_service/pipeline/parquet/lane_registry.py` — all twelve
  adapters, all reading Postgres; lane A's shared registry, owner `s2`.
- `services/agri-data-service/src/agri_data_service/sql/pipeline/signal_plane_day_export.sql` — the
  governance contract, release dedup and cell join a direct writer must reproduce verbatim.
- `services/agri-data-service/src/agri_data_service/foundation/parquet/zoom.py` — `ZOOM_TIERS (0,5,9,13)`
  and `serving_zoom_tier`; lane C must resolve through this, never re-derive a rung.
- `conductor/tracks/parquet_duckdb_pivot_20260823/metadata.json` and
  `conductor/tracks/postgres_shrink_ingest_repoint_20260825/metadata.json` — the two partition sets the
  monitors dispatch.

#### 0.42.8 Environment

Branch `main`, level with origin at `91a5a1e`. Untracked and deliberately so:
`services/agri-data-service/continuous-warehouse-loop.sh`, `scripts/warehouse_status.py`.
**`plantgeo-parquet-drain` and `plantgeo-ingest-cron` are DOWN** (`railway down`, 2026-08-25 02:05 UTC)
— and they come back on the next push (§0.40.1), so a monitor that pushes restarts them. Prod DSN in
`services/agri-data-service/.env` (`DATABASE_URL_SYNC`); object store creds under `OBJECT_STORE_*` in
the same file. DuckDB against this bucket needs `URL_STYLE 'vhost'` and explicit keys, never a glob.
Never run PlantGeo locally; never restart the warehouse loop.

#### 0.42.9 Continuation plan

1. **Freeze the wire contract** (§0.42.2). Write golden fixtures for the four routes plus the four
   envelope states, a Python contract test asserting the serving side matches, and extend the existing
   TS test to read the same fixture. This is the only step that must finish before anything runs in
   parallel. Files: `services/agri-data-service/tests/contract/`,
   `src/__tests__/services/parquet-plane-client.test.ts`.
2. **Re-verify both partition sets against HEAD** — grep every `owns` list; `confidence: planned` is a
   hypothesis stamped at a commit that has moved. Upgrade to `verified` or fix the boundary.
3. **Launch the three monitors** (§0.42.1), lane A first by a few minutes so `d1`'s re-export starts
   early — it is the longest pole (1,560 signal days × ~0.7 s, plus every other lane's coarse rungs).
4. **Lane A's first slice is `s0`** — restore `cronSchedule`. Time-critical: the `sensors` lane's
   upstream keeps ~6 days and Postgres is its only archive, so days after **2026-08-31** are
   unrecoverable.
5. **Hold the drain down until `s0` is verified.** The cron and the drain collide on the same database
   (a signal lane-day: ~8 s alone, ~25 min beside a cron tick). One writer at a time.
6. **Per-layer cutover as each lane completes** (`u3`), deleting that layer's Postgres read path in the
   same change.
7. **One adversarial pass at the join** — separate context, prompted to refute rather than confirm,
   over the whole cutover surface. `/code-review high` on the serving API and the repointed readers.
   Record the verdict; a lane with no verdict is unreviewed, not done.
8. **Then `s6`** — the final shrink, justified architecturally (a grep proving no reader, plus a
   coverage proof), never by an `idx_scan` counter.

#### 0.42.10 Open questions

- **Does `burn-severity`'s MTBS walk need to reach 2020-2024 before that layer may cut over?** Live once
  lane A reports the coarse rungs done — until then the layer has 541 rows and 8 z13 parts, and it is
  not clear whether the gap is the walk or the source.
- **Does `soil-survey`'s export failure survive the cap removal?** Live at lane A's first `soil-survey`
  run: 238,986 rows exist and `68da7af` deleted `MAX_SOIL_SURVEY_POLYGON_KEYS`, so the 0-written state
  should now be reproducible-or-fixed rather than blocked.

#### 0.42.11 CORRECTION to §0.41.9 — that blocker cleared the same day it was written

§0.41.9 marks `fire_feature_plane_validation_20260824` **blocked** on the 23-year `fire-detections`
hole, calling it pivot item B and 69 % of 13,037 lane-days. **That drain finished at 19:42 UTC on
2026-08-24**, most likely after §0.41 was written: `fire-detections` reports zero missing across 9,425
expected days (§0.39.2) and the bucket holds **8,357 z13 parts** (§0.40.2), reaching the lane's
`history_floor` of 2000-11-02.

So the second fire season that validation needs **already exists in Parquet**, and AUC 0.725 can be
scored out-of-sample now. Re-status that track from `blocked` to runnable. The one caveat: no coarse
rung exists yet, so a validation run must read z13 explicitly and must not assume a `serving_zoom_tier`
resolution will find anything above it.

#### 0.42.12 What §0.41 corroborates

§0.41.7's restated owner constraint — *"all new data ingestion targets day-partitioned
Parquet/GeoParquet, aimed at serverless query on cold compute; the serving pattern is one day per
layer"* — is the same direction this packet executes, arrived at independently. It also settles a
question lane A would otherwise raise per lane: a fact that does not vary by day is a `static_lookup`
keyed to a source watermark and **must not be written 365 times a year**.

#### 0.42.13 Invocations

- `/slice` on lane C's uncovered work (`u1`–`u4`) — **trigger:** four new slices with no proven
  boundaries yet, and three of them contend for `environmental-read-model.ts`. Plan before launching.
- `/conductor-okf:implement postgres_shrink_ingest_repoint_20260825` — **trigger:** lane A's slices are
  already specced with tasks and acceptance criteria; step 4 above is its P0.
- `/code-review high` on the serving API and the repointed readers — **trigger:** the four routes are
  the contract the whole map reads through, and `parquet-plane-client.ts` already encodes the named-day
  trap they must honour.
- `oh-my-claudecode:critic` for the adversarial pass at step 7 — **trigger:** a hard cutover with no
  fallback is a one-way door; the pass must be prompted to refute, in a context that did not write it.


#### 0.42.14 GATE ANSWERS (2026-08-25, owner) — four decisions taken before launch

Asked at the start of the execution session, ranked by blast radius. These **supersede the matching
entries in 0.42.6's assumptions block**.

1. **The dirty tree is committed, both sessions together** — `876c011`. `RUNBOOK.md` and `tracks.md`
   each carried 0.41 *and* 0.42 in one file, so a partial commit was hunk surgery, not a file split.
   Provenance is in the commit message. Held back deliberately per 0.42.8:
   `continuous-warehouse-loop.sh` and `scripts/warehouse_status.py` are operator scratch.
   **NOT PUSHED** — a push redeploys the runners (0.40.1).

2. **Token spend across the three monitors stays UNBOUNDED** — 0.42.6's recorded default, confirmed
   rather than capped. Monitors are told there is no per-lane budget. To reverse mid-flight is to
   re-brief every monitor, which is why it was asked before launch.

3. **`plantgeo-parquet-drain` is DISABLED, not deleted, and not left to a rule.** The owner's
   instruction was to remove the code if unused and disable if that is the way; investigation settled
   which:
   - **The drain code is USED.** Pivot slice `d1` **owns** `pipeline/parquet/drain.py` and rewrites it
     ("FUSED drain + tier derivation ... Order: build, run, THEN stop the cron"). Removing it would
     delete the file the longest-pole slice is about to author.
   - **`infra/parquet-drain/AGENTS.md` pre-authorises deletion "when the backlog reaches zero" — and
     that condition is NOT met.** 0.40.2 measured backlog-zero at **z13 only**: no coarse rung exists
     for any lane, and the `signal` base lacks `cell_longitude`/`cell_latitude`, so ~1,560 days need
     re-export first. The service is what makes that tractable (a drained day is round-trip-bound, not
     data-bound — see that file's measurements), so deleting it destroys the in-region runner `d1` needs.
   - **Action taken:** `railway service source disconnect --service plantgeo-parquet-drain`. Verified —
     the service no longer carries a `repo:` line in `railway service list`. Pushes can no longer revive
     it, so 0.42.9 step 5's "hold the drain down" is now enforced by construction instead of by whoever
     remembers it. Variables and config survive; reconnect is one command, recorded in that AGENTS.md
     along with the warning that the `while true ... sleep 15` start command is wrong now (it spins on
     `burn-severity 2024-08-22` doing no work while competing with Postgres).
   - **`plantgeo-ingest-cron` was deliberately LEFT CONNECTED** — restoring its `cronSchedule` is lane
     A's `s0`, and `sensors`' upstream keeps only ~6 days.

4. **Adversarial review is per-lane (3) PLUS the join** — not the single join pass 0.42.6 assumed.
   The reason the assumption was wrong: decision 4 deletes a layer's Postgres read path **in the same
   change that cuts it**, with no fallback, and 0.42.4 stages those cuts across the whole programme. A
   single pass at the join therefore arrives *after* every irreversible cut has already shipped. Each
   lane now gets a refute-prompted review in a context that did not write it, at lane completion,
   before its cuts are considered done; the broad pass at the join stays. A lane with no recorded
   verdict is unreviewed, not done.

#### 0.42.15 PARTITION RE-VERIFICATION at `80ac72a` — one cross-lane collision, two drifts

0.42.9 step 2 run: every `owns` path in both sets grepped against HEAD, plus a **prefix** check for
directory-owner-versus-file-inside-it, which a string-equality check cannot see. Both sets are now
`confidence: verified`.

**One real cross-lane collision, and it would have put two lanes in one directory.** Pivot `d5`
creates `pipeline/lanes/soil_field.py` — **inside the directory `shrink:s5` owns and deletes
producers from**. 0.42.1 assigned `d5` to lane **C** (UI display surface) and `s5` to lane **A**, so
they would have run concurrently against the same directory. `d5` is also a Python data-lane file,
not a UI surface, so the lane assignment was wrong on its face. **`d5` moves to lane A and must land
before `s5`.** Lane C is now `d4` plus `u1`–`u4`; this costs lane C nothing, since `d5` never
belonged to it.

**Two drifts in `d1`.** `warehouse/parquet/tiers.py` and `pipeline/parquet/drain.py` were marked
`future` but both **exist** at HEAD (built in `3e5027f` / `ae63b02`). `d1` is therefore a **rewrite**,
not a create — a distinction that changes how it must be briefed: the cell join is already out of the
hot path and the drain already has its own 600 s clock, and an agent told "create these" would throw
that away.

**Twenty-two same-lane overlaps that are ordering, not contention.** `s2` owns `pipeline/direct/` and
`tests/direct/` as directories while `s3`/`s4` own individual files inside them — but `s3` and `s4`
both `depends_on: [s2]` and all three are lane A, so `s2` creates the directory and the others fill
it in sequence. Left as is; recorded so the next reader does not re-raise it as a defect.

**Nothing else overlaps across the two sets.**

#### 0.42.16 The contract freeze is DONE — `80ac72a`

0.42.9 step 1 is complete, so the gate on parallel work is lifted. Nine golden fixtures in
`services/agri-data-service/tests/contract/fixtures/`, a pydantic declaration with `extra="forbid"`,
16 Python contract tests and a new describe block in
`src/__tests__/services/parquet-plane-client.test.ts` reading **the same fixture files** through the
real zod schemas. The load-bearing test **parses the `WIRE` block out of the TypeScript source** and
compares it to the Python table, so renaming a route on either side fails the build — that is what
makes it a freeze rather than two hopeful copies.

One asymmetry recorded rather than silently accepted: **zod strips unknown keys by default, so the
Python side is the strict one.** A server that adds a field fails in Python and passes in TypeScript.

Sweep at the freeze: 16 passed (`pytest tests/contract`), 41 passed (vitest, client + envelope),
ruff clean.

#### 0.42.17 WAVE 1 LAUNCHED (2026-08-25) — five agents, and why it is five rather than three

Owner said launch all outstanding work. "All outstanding" resolved to **all currently UNBLOCKED**
work: `s2` needs `s0`, `s3`/`s4` need `s2`, `s5` needs `s1`–`s4`, `d5` needs `d1`, `d4` needs `d3`,
and `s6` is last by construction. Launching those now would only have agents waiting on files that do
not exist. Five slices are unblocked, and they were launched together:

| agent | lane | slice | model | owns |
|---|---|---|---|---|
| A/s0 | A | `s0` restore `cronSchedule` + fix the false Dockerfile header | sonnet | `infra/cron-ingest/*` |
| A/d1 | A | `d1` fused drain + tier derivation **rewrite** | opus | `tiers.py`, `drain.py`, their 2 tests |
| A/s1 | A | `s1` alembic greenfield baseline | opus | `alembic/*`, 12 contract tests, readiness pin |
| B | B | `d3` serving API + `b1` coverage endpoint | opus | `interface/http/`, `app.py`, `tests/interface/` |
| C | C | `u1`–`u4` boundary plan, then implement `u4` | opus | frontend tree only |

**Lanes B and C are NOT blocked on lane A's data, and that is what the freeze bought.** `d3` builds
its routes against the golden fixtures; `u4` needs no Parquet lane at all. Without 0.42.16 both would
be waiting on `d1`'s re-export.

**No agent runs a git write.** Five agents committing to one branch contend on `index.lock`, and
worktree isolation would have stranded the Python lanes without `.env` (the production DSN). They
edit; the orchestrator commits per lane at the join.

**Two couplings the briefs name explicitly**, because they look like defects from inside an agent:
- `s1` owns `tests/conftest.py`, which `d1` depends on while running `tests/parquet/`. A test failure
  that reads as an alembic head-pin mismatch is `s1` in flight — report, do not "fix".
- `s0`'s config change takes effect only on a DEPLOY, and no agent may push. The `sensors` deadline of
  **2026-08-31** therefore needs an operator push, which is now the programme's nearest hard date.

**Two things deliberately withheld from agents:** the full ~1,560-day signal re-export (`d1` builds and
sample-verifies, then reports the command) and the production alembic stamp (`s1` prepares and verifies
against a disposable database, then stops). Both are one-way operator decisions.

#### 0.42.18 WAVE 1 COMPLETE — and §0.40.2 IS REFUTED ON BOTH ITS HEADLINE CLAIMS

All five wave-1 slices landed. **Read this before planning any Parquet work: §0.40.2's two
load-bearing facts are dead**, measured against the production bucket (95,030 keys) by `d1` and
corroborated independently by lane B, which found the same rungs while smoke-testing its census.

| §0.40.2 said | measured 2026-08-25 |
|---|---|
| no coarse rung exists for ANY lane | **six lanes have them** — `fire-detections` ladder-complete on 8,135/8,357 days, `signal` on 1,338/1,560, z0/z5/z9 back to 2022-04-30 |
| the `signal` base carries 10 columns, no `cell_longitude`/`cell_latitude`, so ~1,560 days must be re-exported | **the base carries all twelve**, checked on the OLDEST part (2022-04-30, 14,948 rows). **No re-export is owed.** |

Why it went stale: `continuous-warehouse-loop.log` shows the fused path writing `derived z9 … z5 … z0`
per day until it stopped at 2026-08-24 11:59. The listing behind §0.40.2 was taken before that work
landed. **Any plan still budgeting "1,560 signal days × 0.7 s" as the longest pole is spending on work
already done** — §0.42.9 step 3 said exactly that and was wrong.

**The real gap is worse than the one it replaced.** `build_gap_census` walks `GAP_FILL_ZOOM_TIER` and
nothing else, so a day written BEFORE the fusion shipped reads as base-complete, is therefore invisible
to the census, and is therefore permanently empty at every zoom below 13 — **on a green tick, forever**.
Nothing in the codebase could see those days, let alone select them. **1,040 lane-days across eleven
lanes.** Built and dry-run verified in `239a079`; **the repair has NOT been run** (~1–2 hours, resumable).

**A defect nobody was looking for.** `tiers.py` opened DuckDB with NO guards at all — no memory limit,
no thread cap, and spilling at DuckDB's default of **90% of available disk**, verified by probe. That is
the same query class that consumed the host on 2026-08-24, sitting in the derivation path the whole
time. `soil-survey` is a `GeometrySimplification` lane at ~1.5M delineations whose `_dissolve_query`
builds `ST_Union_Agg` over a whole day. Now guarded, including on a caller-supplied connection.

#### 0.42.19 THREE MORE CORRECTIONS, and two decisions waiting on the owner

**Corrections to this runbook, each found by an agent that measured rather than trusted:**

1. **§0.40.3's "DuckDB against this bucket needs `URL_STYLE 'vhost'` (path style 404s)" is wrong.**
   Lane B measured both styles working against `t3.storageapi.dev` for explicit-key reads. `vhost` is
   still the default it ships; the 404 claim should stop being repeated as a constraint.
2. **`retract_partition_tier` does NOT remove `absent.json`.** It clears the completion marker and
   deletes only keys matching `try_parse_partition_path`; absence markers parse to `None` there. So
   **no public `ObjectStore` method can retract a governed absence today**, and `signal`'s absent base
   days cannot be re-exported by anything until one is added.
3. **The coverage golden had a hole, and the bucket found it.** `coverage.json` left 2026-08-07
   accounted for by neither the absence range nor the gap range. Lane B's census of the real bucket
   disagreed with the fixture and the bucket was right. Fixed in `369a810`.

**Also found and deliberately NOT fixed, because it is a governance decision:** governed absences are
written at the **base tier only** — 3,740 base-absent days. A reader at z9 on such a day cannot
distinguish "deliberately empty" from "never written", which is the exact confusion the marker exists
to end. Fixing it means minting ~11,000 absence objects from a repair driver.

**Two destructive operations are built, dry-run verified, and awaiting owner approval:**
- **The 1,040-day ladder repair** — writes ~3 objects per day, no source queries, ~1–2 hours, resumable.
- **The legacy-layout sweep** — 2,211 superseded objects, 645.7 MB. `d1` also **refused** to delete 63
  orphaned pre-zoom objects whose day is not published in the zoom layout at all, since they are the
  only copy in the bucket. `include_orphaned=True` exists and is off by default.

**Per-lane adversarial reviews are running** on `s1`, `d3`/`b1` and `d1` — gate answer 4 (§0.42.14),
which supersedes §0.42.6's single pass at the join. No lane counts as done without a recorded verdict.

#### 0.42.20 ALL THREE REVIEWS RETURNED CHANGES-REQUIRED — the per-lane gate paid for itself

Gate answer 4 (§0.42.14) put a refute-prompted review at each lane's completion rather than one pass
at the join. **Every one of the three came back CHANGES-REQUIRED**, and two of the findings would have
shipped silently into a cutover with no fallback. Recorded so the cadence is not quietly dropped.

**Lane B — a false content claim, live today.** `warehouse_reader.py:212` probes bbox applicability
with `read_parquet(…, union_by_name=true)`. `union_by_name` makes the column set the UNION across
objects, so the refusal fires only when NO object carries the position columns. In the real
mid-re-export state the probe passes, `WHERE cell_longitude BETWEEN ? AND ?` goes NULL for rows in
objects lacking the column, and those rows are DROPPED — the day answers
`state: "published", rows: [], truncated: false`. A positive claim that the warehouse holds nothing in
that viewport, for days that hold rows. `_unpositioned_rows` cannot catch it, because `signal` and
`vegetation` declare those columns `nullable=False`, so it returns 0.

**Lane A `d1` — the commit's entire capability was unreachable.** `cli.py` never passes `selection`,
declares no `--selection`, and its `--dry-run` calls the BASE census. Seven new public functions had
zero callers outside `drain.py`. An operator would have run `parquet-drain --dry-run`, seen no ladder
work, run the drain, and left ~1,037 days permanently empty below z13 — on a green tick and exit 0.
**The defect the commit existed to fix survived the commit.**

**Lane A `s1` — the baseline forbade its own successor, twice.**
`tests/test_alembic_baseline_contract.py:60-73` asserts `versions/` holds EXACTLY the baseline, so an
ordinary follow-on revision fails the suite — while `revision_graph` still reports one head, which is
what the stated "ambiguity" rationale was actually about and which `test_alembic_head_pin_contract`
already guards. Slices `s5`/`s6` schedule new revisions into that very directory. Separately, the
baseline REPLAYS `db/agri/**` at apply time while `regenerate.py` rebuilds that tree from
`alembic upgrade head`, so a future `op.create_table("foo")` → regenerate → fresh build creates it
twice (`ERROR: relation "species" already exists`, demonstrated). That takes the whole `agri_db` test
gate with it, silently, the day after migration 27.

**Two more worth carrying forward:**

- **`s1`'s privilege tightening is not free.** Two of the three functions losing `PUBLIC EXECUTE` are
  invoked from CHECK constraints (`forecast_derived_signal_value.sql:29`,
  `forecast_candidate_evaluation.sql:35`), and a CHECK is evaluated with the WRITER's privileges.
  Proved twice on a disposable PG18: a non-owner role with schema USAGE now gets
  `permission denied for function` on INSERT. Harmless while `plantgeo_owner` is the sole writer;
  a regression for any greenfield build that provisions one.
- **`d1`'s guard re-pins the DuckDB INSTANCE, not the connection.** `memory_limit`, `threads` and
  `max_temp_directory_size` are global options; handing in one connection re-pinned a sibling that was
  never handed in, permanently. Pointed at the serving instance it would cap the API at 1600 MB.

**The parity gate is now self-confirming.** `db/AGENTS.md:25-26` still claims parity "proves the tree
is exactly what the migrations produce" — false, since the migration head IS the rebuild.
`dump(replay(T)) == T` holds for any round-trippable T, so **a corrupted tree passes** as soon as
regeneration is re-run, which is exactly what the workflow instructs.

**Owner decision taken:** the contract gains a **fifth state, `day_unresolvable`**
(`reason: conflict | incomplete`), so a window answers 200 with its resolvable days instead of
refusing as a whole. Chosen because the honest reason never reached the user anyway —
`bounded-upstream.ts:157` throws on any non-2xx and DISCARDS the body, so `partition_day_incomplete`
was unreachable from the client; a 409 surfaced as a generic crash and a 503 as
"temporarily unavailable" plus a retry. One mid-export day at the live edge took a whole month of
window with it. This unfreezes §0.42.16 deliberately, for one coordinated change across the `WIRE`
block, `wire_contract.py`, the fixtures and the client.

#### 0.42.21 THE PRODUCTION STAMP — the procedure, written where it belongs

`s1`'s review found the stamp existed only in an agent's report: a repo-wide grep for `20260825_0000`
returned the revision, its pins, tests and docstrings, and **nothing operational**. The commit message
calling it "prepared and documented" was not true of the repository. It is now.

**Ordering is not optional.** `routes/health/contracts.py:22` moved the pin and
`sql/routes/health_migration.sql:53` demands EXACT equality, while `railway.json:9` makes `/ready` the
healthcheck. **Stamp BEFORE deploying this build**, or `/ready` reports `migration=false` and Railway
holds the old deployment. It fails closed, which is the good news.

1. **Confirm the target and its revision, read-only.** `uv run python scripts/readiness.py --json` —
   expect `head 20260817_0025`, `expected 20260825_0000`, `matches false`.
2. **Prove production's schema still matches what the baseline builds, applying nothing.** Production
   is PG18, so this is the cross-major path (`test_cross_major_tree_matches_migrations`). A runnable
   pre-stamp diff is being added under `scripts/` — the track's own risk register already required
   this gate (`spec.md:418`) and it did not exist.
3. **Check the extension by hand before trusting the skip.** `SELECT extname FROM pg_extension` must
   NOT list `timescaledb`. The procedure skips `20260825_0026`, whose only effect was dropping it, on
   the grounds that production already dropped it manually on 2026-08-25. **Nothing verifies that
   before stamping and nothing ever will afterwards** — a database where the hand-drop did not happen
   silently records "timescaledb is gone" forever.
4. **Stamp.** `alembic current` must print `20260817_0025` and the announced target host (`env.py`'s
   `announce_target` logs host/port/database, no credentials). Then `alembic stamp 20260825_0000`,
   then `alembic current` again. `DATABASE_URL_SYNC` is what alembic reads — **overriding
   `DATABASE_URL` does nothing and migrates production.**
5. **Then deploy**, then check `/ready`.

**Rollback:** `alembic stamp 20260817_0025` and redeploy the prior build. The stamp rewrites one row
in `public.alembic_version` and touches no schema object, so it is fully reversible.

**A consequence nobody had noticed:** the stamp breaks the governed promotion path.
`execution/promotion.py:750` and `routes/historical_promotion.py:379-382` compare revisions for
EQUALITY — the second against a field NAMED `minimum_target_revision`. A bundle exported before the
stamp records `20260817_0025`; afterwards every restore and chunk append is refused, on a
byte-identical schema. **Re-export any in-flight bundle, or fix the comparison to be the minimum its
name already promises.**

#### 0.42.22 THE AGENT SQL PLANE IS ALREADY BROKEN — 4 tools read a relation dropped a week ago

Raised by the owner 2026-08-25 looking at `sql/agent/nearest_signal_cells.sql`: *"on the sql side a
lot of that likely needs to be repointed to duckdb or polars helper functions."* Correct, and the
state is worse than "needs repointing".

Census of `src/agri_data_service/sql/agent/` — **9 of 12 statements read a matview or view over a
data plane that is moving to Parquet:**

| statement | reads |
|---|---|
| `nearest_signal_cells` · `signals_near_point` · `signal_value_on_day` · `signal_neighbors_in_time` | **`geo.mv_signal_cell_daily`** |
| `drought_history_at_point` | `geo.mv_drought_release_index` |
| `fire_history_near_point` | `geo.mv_feature_observation_day` |
| `forecast_summary_for_cell` | `agri.mv_forecast_ml_daily_serving` |
| `observation_coverage_on_day` · `observation_temporal_neighbors` | `geo.v_observation_day_census` |
| `signal_coverage_on_day` | `agri.signal_coverage_audit` |
| `feature_value_near_point` | `geo.features` / `geo.layers` (direct) |
| `materialized_plane_populated` | the probe itself |

**`geo.mv_signal_cell_daily` was DROPPED on 2026-08-18.** So those four tools have not been
answering for a week — `agent/tools.py:494` `_plane_refusal` catches it and returns a typed
`pre_aggregated_plane_unbuilt`. The refusal is honest about the OUTCOME and **wrong about the
CAUSE**: its note says the relation *"exists in the schema but has never been refreshed"*, which
was true when written and is now false. Fix the wording in the same pass, or the next reader
debugs a refresh that cannot happen.

`nearest_signal_cells.sql` is the clearest example of the cost. It carries ~60 lines of excellent
clause-by-clause rationale — the LEFT-JOIN-not-INNER argument, the `coalesce` to an explicit zero,
the governed-plane caveat — all of it explaining a query against a relation that no longer exists.
**That reasoning is the asset worth carrying to DuckDB; the SQL is not.**

Nothing in either partition set owns `sql/agent/` or `agent/tools.py`. This is uncovered work, which
is exactly what §0.42.1 reserves new slices for.

#### 0.42.23 ARCHITECTURE — one Parquet core, three thin surfaces (owner, 2026-08-25)

Owner: *"this is something the api and mcp will consume — let's keep the parquet ops business logic
separate and containerized for reuse across mcp and api surfaces separately alongside the cli."*

**The rule:** Parquet operations are ONE self-contained core with no surface dependencies. Three
adapters consume it and own nothing but their own protocol:

| surface | adapter | today |
|---|---|---|
| **API** | `interface/http/` | built at `273828b`, fixed at `4a53deb` — but the core is INSIDE it |
| **MCP / agent** | `agent/tools.py` + `sql/agent/` | still 12 raw Postgres statements, 4 of them dead |
| **CLI** | `interface/cli/` | does not exist; `s2a` creates it |

**This is a correction to lane B's just-landed work, not a future concern.** Of the ~1,991 lines in
`interface/http/`, roughly **1,164 are core, not HTTP**:

- core → `warehouse_reader.py` (407), `serving.py` (309), `coverage.py` (267), `duckdb_session.py` (181)
- surface → `parquet_routes.py` (261), `wire.py` (247), `request_params.py` (180), `faults.py` (139)

The split is already clean along file lines, which is the good news — nothing needs untangling
within a file. The core moves; the four surface files stay and import it.

**Proposed home: `warehouse/parquet/serving/`**, since `warehouse/parquet/` already owns the read
side (`schema.py`, `tiers.py`) and `interface/` already means "a protocol surface". A slice may
argue for a top-level `parquet_ops/` instead — record the choice, do not re-litigate it twice.
Reversal cost is one package rename plus imports.

**What each surface must NOT do**, because all three have already done it once:
- No surface opens its own DuckDB session. `duckdb_session.py`'s guard
  (`memory_limit`, `threads`, `max_temp_directory_size='0GiB'`, `:memory:`) belongs to the core, and
  `tiers.py` proved what an unguarded one costs.
- No surface re-derives a zoom rung. Resolve through `foundation/parquet/zoom.py`.
- No surface spells a wire name outside its own protocol module — that is what the freeze protects.

**Sequencing.** `s2a` (the CLI split) now carries the extraction, because it is the slice that has
to import the core anyway and doing it twice is how the two copies drift. Order within `s2a`:
lift the core out of `interface/http/` first, leaving the HTTP surface importing it and its 104
tests green; then build `interface/cli/` on the same core; then the agent/MCP repoint as its own
slice, since it also needs the four dead statements re-authored rather than translated.

#### 0.42.24 SANIC IS THE API, AND IT STAYS PRIVATE (owner, 2026-08-25)

Owner: *"for the api surface I would like to leverage sanic and have that serve to the client."*
Sanic was already the choice — lane B built `interface/http/parquet_routes.py` as a Sanic blueprint
mounted on `app.py`'s `combined_local` and `published_reader` profiles, and `sanic>=24.6` is a core
dependency. What needed deciding was **who calls it**.

**Decision: the Next server stays in front; Sanic is never exposed to the browser.** The chain
remains browser → tRPC → `parquet-plane-client.ts` → Sanic. Sanic owns all Parquet logic and is the
API in the architectural sense (§0.42.23); it is simply not a public origin.

Chosen over browser-direct for three reasons, in order of weight:

1. **`plantgeo-martin` is the precedent and its lockdown is still open** — a public data surface on
   this project measured a 27 MB / 40 s tile fetch, and retrofitting the limits has outlived several
   sessions. A second public read surface would inherit that whole problem before it had one user.
2. **The frozen wire client cannot run in a browser as written.** It resolves its base URL through
   `providerUrl(PARQUET_SERVICE_URL_ENV, …)` and reads env vars; browser-direct means authoring a
   second client, which is a second place the frozen contract can drift.
3. **Auth and org context already work where they are.** Moving them onto Sanic is real work that
   buys the user nothing they can see — the removed hop is server-to-server.

Cost accepted: one hop the user never perceives. **To reverse:** a browser-safe client module, CORS
pinned to the app origin, rate limits, and a read-only public profile designed in BEFORE any
cutover — not retrofitted. Do not reverse this casually; the split-by-sensitivity variant (public
planes direct, community via Next) was considered and rejected as two client paths to keep in sync.

#### 0.42.25 THE ORM MODELS — a subset dies with the planes, and the alembic coupling is a liability

Owner asked whether `models/` is still necessary after the pivot. Measured: **13 files, 66 tables,
but 18 modules import models against 48 using the raw-SQL loader.** The ORM is already the minority
path in this service.

**The heaviest consumers die by design.** Seven `execution/historical_writer/*` modules import
exactly `SignalObservation`, `SignalCoverageAudit` and `CellSourceCrosswalk` — which is what `s5`
retires ("delete each verified lane's ingest producer") and `s6` drops (`agri.signal_observation`).
So the largest block of model usage is already scheduled for deletion, not because the models are
wrong but because their tables go away.

**What survives has nothing to do with the pivot:** `jobs.py` (the `agri.job_*` ledger every load
runs on), `strategy.py` / `profiles.py` / `species.py` / `knowledge.py` / `location.py` (community
and reference data §0.23.4 deliberately keeps in Postgres), `provenance.py` (governance), and
`geospatial.py` (`spatial_cell`, still the grid the signal export and the agent tools key on).

**The real finding is three sources of truth for one schema:** `models/` (`Base.metadata`),
`db/agri/**` (the declarative tree), and the alembic baseline that replays the tree.
`alembic/env.py:21` sets `target_metadata = Base.metadata`, so `alembic revision --autogenerate`
would diff the MODELS against the database — but migrations here are hand-authored and
`test_declarative_schema_parity` compares **tree to migrations, never models to either**. Nothing
keeps the models honest. A model can drift from the real schema silently, and the only thing that
would notice is an autogenerate nobody runs.

**Verdict: do not delete `models/` wholesale.** Two actions instead:
- **Sever the alembic coupling.** With the tree as the source of truth, `--autogenerate` is a trap:
  it emits a migration diffing models against a schema they do not define. Either set
  `target_metadata = None` or add the parity gate that would make the coupling honest — but do not
  leave it as it is, silently authoritative-looking and ungated.
- **Let `s5`/`s6` delete the plane-bound models as their tables drop** — `historical.py`,
  `historical_promotion.py` and the plane-bound parts of `forecasting.py`. They are not a separate
  cleanup; they are part of the retirement that already has an owner.

#### 0.42.26 MONITOR-ARCHITECT VERDICT — every gate green, and HEAD did not build

First tree-wide sweep of the phase, run by a lane that wrote none of the code. **4,218 Python tests,
1,384 TypeScript tests, mypy (262 files), the deferred session-wide ruff, `tsc`, `eslint` and
`next build` — all green, zero failures, and every suite verified as actually RUN rather than
collected-and-skipped.** The three Python skips are each pinned by reason.

**And the tree was red on the one thing no gate can see.** Three modules — `db/extensions.py`,
`db/revisions.py`, `db/schema_diff.py` — were written by `s1`'s fix pass and **never staged**, while
eight committed files imported them, including the baseline migration, the `/ready` pin, and
`db/tools/verify_stamp_target.py`. So `alembic upgrade head` could not import its own extension
list, `/ready` could not import at all, and since `railway.json:9` makes `/ready` the healthcheck, a
deploy from a clean checkout would never have gone healthy. **0.42.21's stamp procedure could not
run, because its gate was one of the broken importers — the same "documented but not in the
repository" failure 0.42.21 exists to correct, reproduced one commit later.** Orchestrator staging
error, fixed. Invisible by construction: pytest, mypy and ruff all read the WORKING TREE, where the
files existed. A general check now confirms every `agri_data_service` import across all 500 tracked
`.py` files resolves to a module HEAD contains.

#### 0.42.27 THE 0.42.23 SPLIT IS MISCLASSIFIED — as written, the extraction inverts its own rule

**Do not execute 0.42.23's file lists verbatim.** The shape is right — only `parquet_routes.py`
imports a web framework, so seven of eight modules are already framework-free — but **each of the
four files called "core" imports a file called "surface", at runtime**: `warehouse_reader.py:18`,
`serving.py:20,22`, `coverage.py:19,21`, `duckdb_session.py:18`. Move the four and leave the four,
and the core has **six runtime imports into an HTTP package**. The CLI would import `coverage`,
which imports `interface.http.wire`, and the containerization is fictional.

**Corrected classification.** `wire.py` is core — it imports nothing from the project and is the
serialization contract all three adapters render. `request_params.py` is core — no `Request` object,
every function is `str | None` to a domain type, and a CLI parsing `--bbox` needs byte-identical
validation or it skips the antimeridian check at `:58`. `faults.py` is core in substance; its only
real HTTP leak is three constants at `:11-13` and `status:` on `ServingRefusalError`. **The true
split is ~1,703 core / 261 surface, not 1,164 / 827 — `parquet_routes.py` alone is the adapter.**
One genuine untangle, ~15 lines: `serving.py:191,199` construct refusals carrying
`HTTP_SERVICE_UNAVAILABLE`, so a warehouse rule states an HTTP status. Core raises
`(code, message)`; the adapter owns one code-to-status table.

**Home: `parquet_ops/` (top-level), NOT `warehouse/parquet/serving/`.** Decisive and measurable:
`warehouse/` imports `pipeline/` nowhere today, while `pipeline/` imports `warehouse/` densely. The
serving core needs `pipeline.parquet.lane_registry` at runtime (`coverage.py:22`), so putting it
under `warehouse/` mints **the first-ever warehouse-to-pipeline edge**, inverting the only clean
layering the Parquet tree has. `parquet_ops/` sits beside both and may import either. Recorded; do
not re-litigate.

#### 0.42.28 THE LARGEST RISK — the memory guard is split across the core/surface line

`duckdb_session.py:34-38` states it: a bare `duckdb.connect()` builds a NEW instance, so
`memory_limit` binds to one connection, not the process — "the per-request session is therefore only
half a guard: the other half is admission control." **The core owns the half that cannot bound the
process; the surface owns the half that can** (`parquet_routes.py:80` pool, `:85-95` semaphore).

So 0.42.23's rule "no surface opens its own DuckDB session" is necessary and **not sufficient**: a
CLI can obey it perfectly, call `open_serving_session` in a loop over twenty lanes, and consume
24 GB — every session guarded, the process ceiling never consulted, because the only thing that
consults it lives in a Sanic blueprint the CLI does not import. `s2a` builds exactly that CLI next.

**Cheapest retirement: make acquiring a session BE acquiring a slot.** Move the pool and semaphore
into the core beside `open_serving_session` and expose it only through a context manager that
cannot return a connection without taking a slot. `parquet_routes.py` loses ~15 lines and keeps its
`serving_at_capacity` refusal by catching a typed core exception; CLI and MCP inherit the ceiling
free. **Do it DURING the extraction, not after** — moving the core first and the guard second leaves
a window where two adapters exist and one ceiling does not.

#### 0.42.29 FOUR GUARD SPELLINGS, TWO UNGUARDED SESSIONS, AND ZOOM IS CLEAN

| # | site | memory | threads | `max_temp_directory_size` |
|---|---|---|---|---|
| 1 | `analysis/warehouse_session.py` | 1600MB | 3 | `0GiB` |
| 2 | `warehouse/parquet/tiers.py` | 1600MB | 3 | `0GiB` — **identical values, different constant names** |
| 3 | `interface/http/duckdb_session.py` | 1200MB | 2 | `0GiB` — a legitimate second profile, expressed as a copied block |
| 4 | `execution/historical_parquet.py:151` | 1GB | 1 | **ABSENT — spills at DuckDB's 90%-of-disk default** |

**Spelling 4 is a live defect of the 0.42.18 class**, on a `COPY (SELECT ... ORDER BY ...) TO ...
PARTITION_BY` — a full sort over every NASA cell-parameter-day, the highest-spill shape DuckDB has.
`tiers.py:107` calls that setting "the load-bearing one"; this call site omits it.

**`planes/drought.py:247` opens an in-memory DuckDB connection with NO guard at all** and then runs
`ST_Contains` over a day of USDM polygons — the ~140k-vertex geometries that consumed the host on
2026-08-24. Mitigating: `most_severe_class_at_point` has **no production caller** today. It is a
loaded landmine for whichever slice wires the drought lane up. The same file contradicts
`duckdb_session.py:130-140` by installing `spatial` on demand mid-call.

**Zoom is genuinely clean.** `foundation/parquet/zoom.py:27` is the single definition, every consumer
resolves through it, and no site re-derives a rung. That mistake is retired — say so rather than
re-checking it.

#### 0.42.30 THE 0.42.22 CENSUS OVERSTATED `s7` — five statements must be LEFT ALONE

"9 of 12 read a matview or view over a moving plane" flattens three different situations, and a
slice briefed from that sentence would rewrite five statements harmfully.

- **Group A — DEAD, re-author as core calls (4):** the four reading `geo.mv_signal_cell_daily`.
  Carry the LEFT-JOIN-not-INNER and explicit-zero reasoning into the tool docstring and refusal
  text; the core's five-state contract plus `GovernedAbsence` already says it better.
- **Group B — ALIVE and Postgres-by-design, LEAVE (5):** `drought_history_at_point`,
  `fire_history_near_point`, `observation_coverage_on_day`, `observation_temporal_neighbors`,
  `feature_value_near_point`. **`geo` does not exist in Alembic at all** — it is Drizzle-managed, and
  all four relations are read by the live app (`environmental-read-model.ts:1059,1515,3480,3558`).
  **Repointing them would fork the read model and make the agent answer from a different source than
  the map.**
- **Group C — decide per relation (2 + the probe):** `agri.mv_forecast_ml_daily_serving` and
  `agri.signal_coverage_audit` both EXIST. `signal_coverage_on_day` should CEASE TO EXIST — the
  core's census answers it from the authoritative source. `forecast_summary_for_cell` moves when the
  ML plane moves. `materialized_plane_populated` becomes a warehouse readiness probe.

**Shape: 4 re-authored, 1 deleted, 1 probe rewritten, 5 untouched, 1 deferred.**

**Still live at HEAD:** `agent/tools.py:502-503` states the false cause 0.42.22 asked to fix in the
same pass. One line, belongs to `s7`.

**Two green ticks rest on container privilege, not code:** `plantgeo_owner` on `:5442` is superuser
(masking the `CREATEROLE` the non-owner test needs) and that image ships `timescaledb 2.27.0`
(masking the archive-replay ERROR path). CI on a least-privilege server behaves differently.

#### 0.42.31 DATA COMPLETENESS IS AT A STANDSTILL, AND THE LADDER GAP IS VISIBLE IN THE MARKS

Owner observed no change in the bucket. Confirmed by `scripts/warehouse_status.py` (read-only,
2026-08-25):

```
supervisor      : NOT RUNNING
log last moved  : 1476.3 min ago          (24.6 hours)
bucket objects  : 95,048
completion marks: 42,929  by zoom {'00': 10473, '05': 10473, '09': 10473, '13': 11510}
governed absent : 4,527
days            : 1685 written, 1247 absent, 1 raised, 0 contended
```

**The ladder gap is arithmetic you can read off the marks: 11,510 − 10,473 = 1,037.** Every lane-day
that holds a base rung but no coarse rung. That is `d1`'s census (§0.42.18) reproduced by a tool
that knows nothing about `d1`, and it is the third independent derivation of the same number.

**Nothing is writing, and that is by construction rather than by fault:**

| writer | state | why |
|---|---|---|
| `plantgeo-parquet-drain` | Failed, **no `repo:`** | source-disconnected on purpose (§0.42.14 item 3) |
| `plantgeo-ingest-cron` | Failed | `s0` restored `cronSchedule` in config; **config only takes effect on a deploy** |
| `plantgeo-cron-mtbs` | Crashed | pre-existing |

**The 18 objects that DID appear are agent test-writes, not progress.** `d1`'s small-sample
end-to-end wrote `watersheds 2026-08-07` and `weather-observations 2026-08-03` through the real
advisory lock (95,030 → 95,048). Do not read that delta as a lane advancing.

**So every hour of engineering today moved zero bytes of warehouse data**, and that is the honest
summary: the wave built the machinery — the ladder census, the reachable CLI verb, the guards, the
serving routes, the freeze — and **not one of the three things that would actually close a gap has
been authorized to run.** Data completeness is blocked on operator decisions, not on code:

1. **The 1,040-day ladder repair** — built, dry-run verified, reachable from the CLI since `549346f`
   (`parquet-drain --selection ladder`). ~1–2 hours, resumable, no source queries. **Never run.**
2. **A deploy** to arm `s0`'s restored schedule, so a forward writer exists at all. `sensors`
   upstream keeps ~6 days; days after **2026-08-31** are unrecoverable.
3. **The legacy sweep** (2,211 superseded objects, 645.7 MB) — report-only unless `--delete`, and
   safer since `549346f` reclassified on *servable* rather than *mentioned*.

**`1 raised` and `1 non-zero verb exit` are also unexplained** and predate this session. Whoever
resumes should read them before assuming the backlog is only the 1,037.

---


## 1. Goal

Get the 3 GB-capped production database to a small, honest working set — the application runs **no analytical
queries**, every serving path reads a pre-aggregated relation, and base (non-reclaimable) memory is cut so the
Railway cap can come down. Alongside it, make every data layer self-healing per
[`docs/layer-lane-standard.md`](../docs/layer-lane-standard.md) so lanes close their own gaps without an agent
watching.

Done looks like: no analytical query on any request path, base memory measured and the cap right-sized, the
hourly pulse green and cheap, and no fabricated data on any serving path.

---

## 2. State

### Dynamic layers were CORS-blocked in prod — found and FIXED 2026-08-17

**Root cause of every dynamic map layer rendering blank in production: Martin's CORS allow-list named the wrong
origin.** This is the single most important state change of the session, and it dominates §9.

- Prod Martin carried `TILE_CORS_ORIGIN=https://plantgeo-main-production.up.railway.app`. The app's **canonical
  user-facing origin is `https://plantgeo.aevani.com`**. **BOTH domains are ACTIVE** on `plantgeo-main` — custom
  domain `plantgeo.aevani.com` (`03f92270-f8f6-414c-bac8-abeb2fa4c5dd`) and service domain
  `plantgeo-main-production.up.railway.app` (`de80644f-46a3-4530-8906-8faaa03d00f6`), both `target_port` 8080.
- `infra/martin/martin.yaml:7-10` interpolates that single var into `cors.origin` as a **one-element YAML list**:
  `- "${TILE_CORS_ORIGIN:http://localhost:3001}"`.
- Result: Martin answered `HTTP/1.1 200 OK` with
  `vary: accept-encoding, Origin, Access-Control-Request-Method, Access-Control-Request-Headers` and **NO
  `Access-Control-Allow-Origin` header at all** — the CORS layer was active, simply did not match, and emitted
  nothing. `OPTIONS` preflight returned **400 Bad Request**. The browser blocked the response.
- Live console error from prod:
  `MapLibre reported a load error. If the basemap is blank, the pinned Protomaps build may have expired -- set NEXT_PUBLIC_PMTILES_URL to a current archive. eZ: AJAXError: Failed to fetch (0): https://plantgeo-martin-production.up.railway.app/fire_risk_tiles,sensor_tiles,evacuation_zone_tiles,burn_severity_tiles,intervention_tiles,watershed_tiles`
- **That error message is actively misleading.** It blames an expired Protomaps/PMTiles pin; the failing URL is the
  **Martin composite**. **PMTiles was verified healthy:** `https://tiles.aevani.com/pnw-2026-08-02.pmtiles` returns
  `206 Partial Content`, `Access-Control-Allow-Origin: *`, `Cache-Control: public, max-age=31536000, immutable`,
  **1,411,574,646 bytes** total. AWS terrain tiles also return `Access-Control-Allow-Origin: *`.
  **Only Martin was broken.**
- Because `src/lib/map/sources.ts:28-32` composes all six function sources into **ONE** MapLibre source, this single
  CORS failure **failed the whole TileJSON and blanked every dynamic layer at once** — exactly the failure mode that
  file warns about.
- **`curl` did NOT reproduce it, because curl sends no `Origin` header. This is why the defect survived every prior
  server-side investigation** (§5 for the required command shape).

**FIX APPLIED TO PRODUCTION THIS SESSION:** `TILE_CORS_ORIGIN` set to `https://plantgeo.aevani.com` on the
`plantgeo-martin` service via Railway (project **Aevani** `6faaf3ea-ac46-4c8b-bbfe-1351dbb9d990`, environment
`b7cfa813-8a5c-4fcd-80f2-cab736d840a7`). **VERIFIED:** Martin now returns
`access-control-allow-origin: https://plantgeo.aevani.com`, the MapLibre load error is gone from the console, and
the map renders — terrain hillshade, coverage boundary and MapLibre attribution all draw. **Confirmed in a real
visible browser against prod.**

**CAVEAT, known consequence:** `cors.origin` receives exactly **one** interpolated entry, so
`https://plantgeo-main-production.up.railway.app` is now the origin that is **BLOCKED**. Anyone loading the app on
the raw Railway domain gets blank dynamic layers. The durable fix is to make `martin.yaml` list **both** origins —
a repo change plus a Martin deploy, **NOT done** (§7). Also stale: `docs/deployment.md:383` and
`infra/railway/README.md:98` both still document the old (now wrong) value, and `.env.example:61` /
`docker-compose.yml:56` default to `http://localhost:3001`.

**PROVENANCE — the wrong value was set deliberately, which is why it never looked suspicious.**
`.mpg/plantgeo-hardening.json:95` records `TILE_CORS_ORIGIN=https://plantgeo-main-production.up.railway.app`
as a *hardening* decision from an earlier session: at that time the Railway service domain was the only
origin, and locking CORS to one exact origin was correct. The custom domain `plantgeo.aevani.com` became
the user-facing origin afterwards and **nothing re-visited the allow-list**. So this was not a typo or a
default left unset — it was a correct decision that silently expired. **The lesson for §7's two-origin
fix: adding a domain to `plantgeo-main` is not complete until Martin's `cors.origin` lists it.** Treat the
allow-list as coupled to the domain set, the same way the migration contract is coupled to the journal.
A repo-wide search confirms `infra/martin/martin.yaml:10` is the **only** runtime consumer of the
variable; every other hit is docs, `docker-compose.yml`, or a stash.

**What this explains, now settled:** the owner's live observation that the canvas "eventually loads, it just takes a
long time" — the basemap/terrain path (PMTiles + AWS, both `ACAO: *`) always worked and was merely slow; the
Martin-backed dynamic layers were **not slow, they were blocked outright**.

### Live in production (verified 2026-08-16)

Prod is at **drizzle 0029**, alembic `20260814_0023`. Repo and prod agree. Deploy `890f430` succeeded across
services; `plantgeo-martin` was redeployed because 0028 replaced a tile function.

| | before | after |
|---|---|---|
| `geo.features` total | 12 GB | **7,219 MB** |
| — index footprint | 6,804 MB | **2,064 MB** |
| slider day axis | scan 4.97M + 46M rows | **31,321 rows** |
| `effective_cache_size` | 768 MB | 2 GB |
| `work_mem` | 4 MB | 16 MB |
| idle-window cache hit | — | **99.88%** |

- **5,392 MB of dead indexes dropped** `CONCURRENTLY`: `idx_features_properties` (4,372 MB / 11 lifetime scans),
  `idx_features_layer_external_id_lookup` (678 MB / 0), `ix_geometry_centroid` (159 MB / 0), `ix_geometry_geom`
  (183 MB / 1).
- **Two indexes built**, both `indisvalid`: `ix_features_layer_observation_day` (406.9s),
  `ix_features_updated_at` (59.3s). The planner **does** use the first — day bounds are now an `Index Cond`.
- **8 of 9 matviews populated.** `geo.mv_soil_survey_union` failed (see §7 step 3).
- **0028 verified honest:** the three strategy matviews hold 0 rows because their sources are empty,
  `causal_benefit_tau` is gone, `effect_utility_score/_lower/_upper` plus `label_release_key` / `source_count` /
  `label_review_tier` are present, and `geo.strategy_recommendations_tiles(z,x,y)` resolves at z4/z9/z13 via the
  empty-tile path. **The rewrite invented nothing.**

### The matview-refresh lane: root cause found and recovered (2026-08-16, later)

- **Root cause of ~24h of `matview-refresh` / `strategy-mv-refresh` dead-lettering: alembic
  `20260816_0024` had never been applied to prod.** It creates `agri.matview_refresh_state`, the ledger the
  whole watermark lane writes through. Applied. **CONFIRMED RECOVERED:** the ledger now holds **11 rows**,
  **6 views refreshed**, and `agri.mv_forecast_ml_daily_serving` came back as `self_healed_unpopulated`.
- **Four still fail, and now fail VISIBLY every pulse** (the intended improvement, not a regression):
  `mv_feature_observation_day` **302s** against its 300s cap · `mv_signal_observation_day` **301s**, same cap ·
  `mv_soil_survey_grid` **66s** · `mv_soil_survey_union` **133s** — the known GEOS `CollectionExtract` bug,
  §7 step 3.
- **`ANALYZE geo.features` + `geo.layers` run.** Stats were 2 days stale: the planner estimated **499,986** rows
  where the truth is **541**. **NECESSARY BUT NOT SUFFICIENT** — the plan is still >300s afterwards, so the
  missing index is genuine and not a statistics artefact.

### The map: not missing data — but the client IS defective (2026-08-16, corrected 2026-08-17)

**This subsection previously read "not a code bug and not missing data". The second half stands; the first half
is WRONG and is corrected here.** The data-extent evidence below is unchanged and still valid — nothing is cut.
But §9 documents **ten client-side code defects** (D1–D10) reached by read-only code trace, three of them
sufficient on their own to make a correctly-populated layer render stale, empty or mislabelled. "Diagnosed as
purely a database/serving problem" was a premature closure.

- Every `geo.*_tiles()` function collapses to a **BitmapAnd whose GiST leg scans ~1,318,892 FIRMS index
  entries to return 137 rows**: **45.6s per tile**. Eight such tiles saturate Martin's 8-connection pool —
  `intervention_tiles`, a **two-row** layer, was observed running **10 minutes**.
- **The data is NOT cut.** Verified extents: sensors **149,466** features spanning
  −124.56..−111.10 / 42.02..48.99 · watersheds **9,396** · burn-severity **541** · fire-detections
  **3,009,567** at exactly −125..−111 / 42..49. `INGEST_BBOX` ≈ (−125, 42, −111, 49).
- **The green diagonal is `ServiceAreaLayer`'s coverage boundary under camera pitch.** It dims outside and
  clips nothing. Do not chase it as a data defect.
- **Martin has NO statement timeout** — `statement_timeout = 0`, source `default`. `postgres` is the **only**
  login role, so a role- or database-level timeout would also hit the matview lane that legitimately needs
  1800s, and a dedicated role collides with the settled "no custom DB roles" decision. **Remedy:** append
  `options=-c statement_timeout=20000` to `DATABASE_URL` on the **`plantgeo-martin` service only**.
  **STILL NOT APPLIED** — but no longer blocked: the Railway MCP is authenticated again as of 2026-08-17 (§5).
  The remaining reason it is unapplied is that nobody has applied it.

### The map UI, measured live against prod (2026-08-17)

The tile path is no longer an EXPLAIN estimate — it was measured end to end against
`https://plantgeo-martin-production.up.railway.app` and `https://plantgeo.aevani.com`. **Full evidence in §9.**

- **`fire_risk_tiles` returns 26,765 bytes after 117.2 s** — the worst work-to-output ratio in the system, and it
  is the `fire-perimeters` layer, not fire-detections.
- **Cold composite tile: 84.4 s at z6.** Warm repeat of a neighbouring tile: 1.6 s — Martin's 128 MB /
  `tile_expiry: 5m` cache works, it just cannot hold a working set this size, so nearly every pan is cold again.
- **Payload is a second, independent problem: 10,751,237 bytes (10.3 MB) in a single vector tile at z5**, ~2–3 MB
  at z6 — and z5/z6 is where the map opens. `sensor_tiles` alone is **2,155,849 bytes**, 149,466 unclustered
  points serialized whole. Even at zero latency this is a client decode and RAM problem.
- **Tile responses carry no `Cache-Control`** — `etag` only, so a returning session re-pays the multi-MB cold cost.
- The 45.6 s BitmapAnd figure above is a plan cost; the 117 s / 84 s figures are cold wall times under real pool
  contention. They are the same defect measured at two points, not two different numbers.
- The app shell is **not** implicated: `/api/ready` 0.43 s, `/api/health` 0.36 s, TTFB 217 ms, load 1697 ms.

### Migration authored, NOT applied

`drizzle/0030_features_layer_geom_tile_index.sql`. The index must be built **out of band** —
`CREATE INDEX CONCURRENTLY` cannot run inside a transaction:

```
CREATE INDEX CONCURRENTLY ix_features_layer_geom ON geo.features USING gist (layer_id, geom)
  WHERE status = 'published' AND geom IS NOT NULL
```

Needs `btree_gist`. Estimated **25–60 min**, **400–600 MB**. Use `lock_timeout = '20min'`, **not** the `'5s'`
hardcoded in `scripts/apply-pre-aggregation.mjs`. Verify **both** `indisvalid` **and** `indisready` after.
**A failed build leaves an INVALID index occupying the name**, so `IF NOT EXISTS` on a retry silently
succeeds while the 45s plan persists — **DROP it first**. Martin restart **not** required (no function
changed). The journal entry and the migration-contract re-pin **must not land** until it is live in prod
(§5, "Migration ordering").

### Review ledger

| phase | checkpoint | findings | verdict |
|---|---|---|---|
| sharding wave (`4a685a1`) | adversarial verifier on per-shard truncation | 6 scenarios, high confidence | **refuted** — 2 died on contact (pre-S2 snapshot; inclusive bounds shipped), 3 stand (dead-code cap, no test coverage, `session_lock` serialization) |
| pre-aggregation build (`4d1345e`) | adversarial matview-equivalence verifier | 11 defects, high confidence | **refuted, then fixed** — day-rule fork, lane/unit gates, two watermark bugs, join-shape changes |
| prod apply (`890f430`) | measured against prod | index used by planner; census 68.7s single vs 95.3s sharded | **confirmed** |
| layer-lane conformance | §13 audit, 21 enumerated | **0 conformant** | **not uniform** — 12-item backlog stands |
| base-memory reduction | — | — | **unreviewed** — not started |
| pulse standing-failure signal (uncommitted) | adversarial review, 2026-08-16 | 2 HIGH + 6 MEDIUM + 1 LOW | **CHANGES-REQUIRED → 2 HIGH + 2 cheap fixed, 5 deferred** (§8) |
| service worker "probably dead" (§11.7) | live prod browser + build artifacts, 2026-08-17 | 1 claimed HIGH | **REFUTED** — SW is `activated` and CONTROLLING, manifest 200; premise was a `public/**` glob missing `src/app/manifest.ts`. Zero code changed. §11.7 rewritten |
| **all 4 client lanes — REMEDIATION ROUND** | re-verified per lane, 2026-08-17 | every finding addressed or defended | **RESOLVED.** Cache: historical revalidation restored + **four** immutability assertions corrected (reviewer found three), quota ceiling now *learned* from a refused write, 304 fiction deleted, payload walk replaced by a metadata store at DB version 2 so a large budget costs O(entries) not O(bytes). Fires: `60 → 4` cached responses (**~290 MB → ~19 MB**), `, f.id` tiebreakers added to both queries so the content ETag stops flapping, `today` threaded through the read model. Slider: focus moved onto an after-commit effect, **plus a second bug the prescribed fix would have missed** — a successful clear disables the trigger in the same commit and a natively `disabled` element cannot take `.focus()`, so it moved to `aria-disabled` (also keeps the control in tab order); real success signal replaces the dead `catch`; own dotted appearance instead of reusing `dense`. Map: `isShowingPreviousDay` split into **`hasLandedForRequestedDate`** + `isShowingPreviousDay` (they are not negations — a failed request is neither), store made **per-publisher** so climate can publish without two writers erasing each other, coverage now **18/27**. Tests: 28/28, 60/60, 166/166 |
| agri refresh lane (uncommitted) | adversarial review, 2026-08-17 | pending | **UNDER REVIEW** — per-spec parallelism, failure backoff, `drizzle/0031` re-grain, two further livelocks. Author's own gates: ruff clean, mypy 2 pre-existing, 153 pytest, declarative parity green, `0031` executed against prod inside a **rolled-back** transaction with the census view's 8-column contract byte-identical |
| map lane D1/D4/D5/D10 (uncommitted) | adversarial review vs installed `maplibre-gl@5.22.0` + `query-core@5.96.2`, 2026-08-17 | 3 HIGH + 4 MED + 5 LOW; **9 claims survived** | **CHANGES-REQUIRED** — **the lane broke the rule it wrote**: `keepPreviousData` was added to the 9 climate signals with **no** drawn-day report, so they retain the previous day's isobands while the caption states the new one — verbatim against its own *"retaining is permitted, misstating is not; do not add one without the other."* Also: **`isPlaceholderData` is false on ERROR**, so a failed fetch records as a landing and the surface names a day the canvas is not drawing; and `placeholderData` sets `status="success"`, so **`isLoading` is permanently false after first success**, killing three panel loading indicators and freezing panel counts to the previous viewport. Plus **"`styledata` fires once per tile" is FALSE** in 5.22.0 and asserted as load-bearing in four places. **Survived: the deepEqual/no-render-loop claim, the `getLayer()` safety proof, no rAF race, unthrottled-is-correct, no memory growth, and the D1 tests genuinely failing if the gate were restored.** Remediation dispatched |
| slider lane synced-days track (uncommitted) | adversarial review, 2026-08-17 | 3 HIGH + 3 MED + 3 LOW; 8 claims survived | **CHANGES-REQUIRED** — **the lane's own test suite is red**: focus is never returned to the trigger after Cancel, because `isConfirming` toggles the root element type so React nulls the ref before the inline `.focus()` runs (the Confirm path has the identical bug, untested). **A failed clear is indistinguishable from success** — `clearLayerSyncedDays` is documented *never throws*, so the `catch` that sets `clearFailed` is dead code. And `.is-pending`'s animation **outranks `:focus-visible` by cascade origin regardless of specificity**, so a pending thumb shows no focus ring. **Survived: the render-cost fear REFUTED** (`floorAxisRunsToBands` bounds output to ~111 bands however alternating the pattern), memoization holds, note-joining sound, three states render distinctly, gating correct, Enter-Enter on *opening* refuted. Remediation dispatched |
| cache lane C2 + sync index (uncommitted) | adversarial review, 2026-08-17 | 1 CRITICAL + 2 HIGH + 3 lower; 4 claims survived | **CHANGES-REQUIRED** — **the historical no-revalidate rule is a strict C1 REGRESSION**: every correction path for a past day is now closed (revalidation hard-returns, TTL 30 d, the `dataRevision` compare is unreachable, and the 512 MB raise removed LRU eviction, which was the accidental healer) — and the false immutability premise is **re-asserted in three places**, incl. a *new* AGENTS.md claim. Plus an **unrecoverable quota wedge** (a failed `setEntry` freezes the running total, so eviction never fires again — iOS Safari, which pre-16.4 has no `storage.estimate` backstop), and the **304 short-circuit is unreachable production code** whose test asserts a `dataRevision` payload **no allowlisted procedure emits** — the same fiction class as the `job_run.status` finding, and it kills the planned C1 `dataRevision` gate. Remediation dispatched |
| fires lane D3/D7/C5 (uncommitted) | adversarial review, 2026-08-17 | 3 HIGH + 1 MED-HIGH + 5 lower | **CHANGES-REQUIRED** — three findings reintroduce the defect they claim to close: `undefined` aliases two meanings so the staleness flag reads false on the live path; the content ETag hashes a **non-deterministic row order** (no tiebreaker, `LIMIT 2000` cuts through ties) trading false-304 for false-200; and `MAX_CACHED_RESPONSES = 60` retains **~240–290 MB** across two hook instances — **the worst RAM regression in the batch, against the owner's stated constraint.** Plus a TOCTOU on `today` straddling the await that ships a live payload under the 48 h historical contract. Remediation dispatched |

### Not started

Sharding revert · `mv_signal_cell_daily` re-grain · `mv_soil_survey_union` DDL fix · base-memory cuts ·
the 12-item conformance backlog · ML Phase B · USDM/ERA5 self-healing lanes · persistence audit · FIRMS cap.

---

## 3. Decisions

- **Revert the sharding.** Measured, not argued: 68.7s single vs 95.3s across 13 shards, with `shared_blks_read`
  a wash (331,152 vs 332,076). Sharding never reduced work; it added ~27s of overhead. The original 81–101s was
  the missing index all along.
- **`geo.mv_signal_cell_daily` gets re-grained coarser**, not kept and not dropped. At 6,349 MB / 24,968,939 rows
  it is only a ~1.8× reduction because its grain is nearly the source grain. Keep the release-winner dedup, lose
  the width.
- **`geo.mv_soil_survey_union` gets its DDL fixed**, not dropped — and `usda-soil.ts` should then actually be
  repointed at it, since today it has no reader.
- **DECISION CHANGE (2026-08-16, later): the approved `autovacuum_max_workers` 10 → 3 cut is ON HOLD.** Stale
  autoanalyze on `geo.features` was half the cause of the planner collapse (499,986 estimated vs 541 actual);
  cutting workers by 70% risks recurrence. **`max_connections` 100 → 25 is also held** pending a real
  concurrent-connection count, because it pulls against Martin's `pool_size`. The original approval
  (2026-08-16 morning: both cuts with a restart, no users; declining `shared_buffers`/`maintenance_work_mem`
  cuts and dropping TimescaleDB) stands as the *starting* position, not the current one.
- **Martin gets a per-service statement timeout, not a role- or database-level one.**
  `options=-c statement_timeout=20000` appended to `DATABASE_URL` on `plantgeo-martin` only. `postgres` is the
  only login role, so anything broader would also cap the matview lane that needs 1800s, and a dedicated role
  collides with the settled "no custom DB roles" decision.
  **The decision STANDS; its ordering relative to the tile-performance work is now an OPEN QUESTION (2026-08-17).**
  With `fire_risk_tiles` measured at **117 s**, a 20 s cap converts that layer from "slow but eventually renders"
  into "always fails" until the §10 relation work or the `0030` index lands. Sequencing is not decided here — see
  §7 step 2c.
- **PER-LAYER PARTIAL INDEXES ARE REJECTED — recorded so nobody re-proposes them.** The tile functions select
  the layer **by name through a join**, so no constant `f.layer_id` is ever derived, and a partial index
  predicated on `layer_id = '<uuid>'` can never be chosen by the planner. One composite
  `gist (layer_id, geom)` partial on `status = 'published'` is the shape that works.
- **The pulse's standing-failure signal is a dead-lettered WORK ITEM count, never `job_run.status`.** See §8
  (2026-08-16 review) for the two-directional proof. Corollary, binding: **an operator cancellation must never
  red the tick.**
- **The Railway cap is not lowered until measured.** Report actual non-reclaimable usage after the restart, then
  propose a number with burst headroom.
- **Additive DDL may be applied to prod directly; destructive DDL needs owner approval.** Established this
  session and honoured throughout.
- **Lanes self-heal via the ledger + three crons per lane family** (`docs/layer-lane-standard.md` §8). No agent
  babysits a backfill. Owner call 2026-08-15.
- **Hypertable conversion is its own planned migration**, never a step inside another workstream. See §5.
- **NEW 2026-08-17 — `drizzle/0030` is LANDED AND KEPT PERMANENTLY.** Owner overrode §10's own recommendation to
  skip it. The 400–600 MB carrying cost with one thin reader is accepted; disk is explicitly not a constraint.
  Settles §10 decision (a). See §12.5.
- **NEW 2026-08-17 — refresh incrementally, never recompute.** Owner directive. Continuous aggregates are the
  named mechanism but are **blocked** (no hypertable exists; `geo.features`' day is a jsonb function, not a
  column). The approved route is **delta refresh on the existing `agri.matview_refresh_state` watermark** —
  same O(changed) property, no conversion. **Full reasoning and the four structural blockers in §12.3–12.4.**
- **NEW 2026-08-17 — offline sync is a byproduct of scrubbing, NOT a bulk-sync button.** Approved shape in §11.
  Do not reintroduce a range picker or a prefetch-everything affordance.
- **NEW 2026-08-17 — RAM, not disk, is the constraint.** Owner stated it plainly. This retires size-based
  arguments against §10's relations and against 0030, and sharpens every remaining one onto working set.
- Settled earlier and still binding: maintenance is a third pass inside `jobs-pulse` (order is load-bearing —
  reconcile settles → plan-gaps measures → validate-streams last); **no dedicated ML service**; agri is a local
  CLI; we persist everything we serve.

---

## 4. Assumptions

Ordered by reversal cost, highest first.

- **The pulse will go RED every hour until prod's buried `matview-refresh` work items are cleared.** · default
  taken: ship the honest signal · to reverse: expensive — it is the whole point. The four still-failing
  matviews (§2) are real failures; the count clears when an operator requeues or cancels those items. Nothing
  in the codebase requeues a dead-lettered item today (`sql/ingest/reopen_gap_windows.sql` reopens only
  *succeeded* windows, and gap planning **reports** a dead-lettered day rather than converting it), so the
  clearing action is a hand-written `UPDATE` until a requeue verb exists.
- **No visual/interaction pass has ever been completed on the map, so §9's D1/D3/D4/D5/D9 are code-traced, not
  eyeball-confirmed.** · default taken: report them as CONFIRMED-from-code, because each is a read of the shipped
  control flow with a file:line, not an inference from symptoms · to reverse: cheap — one browser session in a
  **visible** window (see §5's `visibilityState` trap; the automation tab stayed `hidden`, rAF stayed suspended
  and MapLibre never painted, which is why the pass did not happen). These five are the ones a visual pass would
  confirm or refute fastest. The owner's "fire-detections come back eventually" observation is consistent with
  §9's tile latencies but was never timed end-to-end in the running app.
- **§10's polygon relation sizes and two of its row counts are estimates, not measurements.** · default taken:
  proceed with order-of-magnitude figures (150–400 MB, 30–120 MB, fire grid tiers ±5×) · to reverse: cheap
  read-only census. `fire-perimeters` and `evacuation-zones` have **never been censused**; the 740 B/row average
  from `geo.features` is dominated by 3M small fire points and understates polygons badly. **No `EXPLAIN` was run
  against prod in the UI-defect pass** — §10 stands on this runbook's existing measurements plus the `0030` header
  EXPLAIN.
- **`to_regclass` contract has no disposable-DB test (MEDIUM #6, deferred).** · default taken: recorded, not
  written · to reverse: cheap. It false-passes on indexes and sequences (any relkind resolves) and on a
  matview created `WITH NO DATA`. Preflight therefore proves *a relation of that name exists*, not *that
  relation is usable*.
- **The dead-letter census is unbounded in time.** · default taken: count every dead-lettered item of a
  definition, across every run it ever opened · to reverse: cheap, but adding a recency window would
  reintroduce a second notion of failure. A lane with an old, genuinely-abandoned burial stays red until
  someone settles it — which is the intended, operator-reachable behaviour.
- **`npm test` stays broken and unfixed.** · default taken: leave `package.json` alone, invoke `vitest` without
  `--configLoader runner` · to reverse: trivial edit — but first check whether the Docker build gate depends on
  `npm test`, because if it does it has been failing for reasons unrelated to any code.
- **The 17% lifetime transaction rollback rate (104,968 of 510,806) is not investigated.** · default taken:
  recorded, not chased · to reverse: cheap to look at, unknown what it reveals.
- **The 454 stuck `firms-archive` work items stay queued.** · default taken: untouched this session · to reverse:
  cheap to drain, but they drain *into* the throttled box — do it after the memory work, not before.
- **`ix_features_updated_at` (newly built) is kept.** · default taken: keep · to reverse: cheap drop, but
  `idx_features_layer_updated_at` has 9,595 real scans so the access pattern is genuine.
- **`geo.mv_soil_survey_grid` is kept despite having no reader.** · default taken: keep alongside the union fix ·
  to reverse: cheap drop; it costs 339s per refresh.
- **`services/agri-data-service/ml/` stays uncommitted.** · default taken: untracked · to reverse: cheap to
  commit, but a `git add -A` would sweep it into an unrelated commit — stage explicit paths only.

---

## 5. Environment & gotchas

### NEVER RUN PLANTGEO LOCALLY

**Standing owner rule, established 2026-08-17.** PlantGeo is not to be started on the workstation — **especially
the data-warehouse stack**, which destroys the machine. There is no "just this once" exemption for reproducing a
UI bug. Test against production:

- app · `https://plantgeo.aevani.com`
- tiles · `https://plantgeo-martin-production.up.railway.app`

Everything in §9 was gathered this way — `curl`, browser instrumentation and read-only code trace. No local
process, no local database, no write.

### A Martin tile problem MUST be reproduced with an `Origin` header

**`curl` sends no `Origin`, so a total CORS outage returns a clean `200 OK` and looks perfectly healthy.** That is
exactly how the 2026-08-17 outage (§2) survived every prior server-side investigation. Always send the browser's
real origin:

```
curl -sSI -H "Origin: https://plantgeo.aevani.com" \
  https://plantgeo-martin-production.up.railway.app/fire_risk_tiles,sensor_tiles,evacuation_zone_tiles,burn_severity_tiles,intervention_tiles,watershed_tiles/6/11/22
```

**Read the response for `access-control-allow-origin`.** A `200` with `vary: … Origin …` and **no**
`access-control-allow-origin` is the signature: the CORS layer is active and did not match. Confirm with an
`OPTIONS` preflight — a mismatched origin returns **400 Bad Request**.

**FIXED IN THE REPO 2026-08-17 (not yet deployed).** `cors.origin` now carries **two independent placeholders**,
each with a correct-by-construction default, so the origin *count* is structurally fixed by the file and cannot
silently collapse:

```yaml
origin:
  - "${TILE_CORS_ORIGIN:http://localhost:3001}"
  - "${TILE_CORS_ORIGIN_RAILWAY_DOMAIN:https://plantgeo-main-production.up.railway.app}"
```

`TILE_CORS_ORIGIN` is untouched, so the already-correct live value keeps working. The new var needs **no Railway
variable at all** — its YAML default already equals the real domain — so the fix is self-sufficient from code.

**MARTIN IS v1.10.1, NOT v1.4.** `Dockerfile.martin:1` and `docker-compose.yml:52` pin v1.10.1; the project
`CLAUDE.md` stack list says v1.4 and is **wrong**. Verified against Martin's source at tag `martin-v1.10.1`:
`config/file/cors.rs` types `origin` as `Vec<String>` matched by **exact string equality** per entry, and
`config/primitives/env.rs` expands env vars into the **raw YAML text before parsing** using the `subst` crate
(pinned v0.3.8), which substitutes **one placeholder → one scalar and never comma-splits.**

**THE NAIVE FIX IS WORSE THAN THE BUG — recorded so nobody tries it.** Making `TILE_CORS_ORIGIN` a
comma-separated list produces **one literal `"a,b"` origin string that matches no real browser `Origin` header**,
silently blocking **both** domains — behind a still-clean `curl` 200. That is a bigger outage than the current
one, with the same invisible signature.

**DEPLOY REQUIRES A REBUILD, NOT A RESTART.** `martin.yaml` is `COPY`'d into the image at build time
(`Dockerfile.martin:3`), not runtime-mounted. `railway service restart` reuses the already-built image with the
old config baked in and **will appear to do nothing.** Push to `main`, confirm Railway rebuilds
**`plantgeo-martin`** specifically, then re-run the two-origin curl check.

- `infra/martin/martin.yaml:7-10` accepts a **LIST** of origins but only **one** is interpolated from the env var:
  `- "${TILE_CORS_ORIGIN:http://localhost:3001}"`. Whatever single value `TILE_CORS_ORIGIN` holds is the only
  allowed origin; every other active domain on `plantgeo-main` is blocked (§2 caveat).
- **Three docs files still carry stale values** — `docs/deployment.md:383` and `infra/railway/README.md:98` document
  the old `https://plantgeo-main-production.up.railway.app`, and `.env.example:61` / `docker-compose.yml:56` default
  to `http://localhost:3001`. Do not treat any of them as the prod truth; read the Railway variable.
- One CORS failure blanks **everything**: `src/lib/map/sources.ts:28-32` puts all six function sources in one
  composite, so a single blocked response fails the whole TileJSON.

### Production access

- **Prod DSN:** `DATABASE_URL_SYNC` in `services/agri-data-service/.env` (gitignored). Values live there only —
  never copy them into docs, logs, shell lines or commits.
- Query with `services/agri-data-service/.venv/Scripts/python.exe` — it has **psycopg2, not psycopg v3**, so use
  `conn.cursor()`, never `conn.execute()`. System python has no driver.
- CLI verbs: `env -u DATABASE_URL LOCAL_SOURCE_LOADER_DATABASE_URL="$DSN" .venv/Scripts/agri-cli.exe <verb>`.
- Prod is **PostgreSQL 18.4, PostGIS 3.6.4, TimescaleDB 2.29.0**. Railway project **Aevani**
  `6faaf3ea-ac46-4c8b-bbfe-1351dbb9d990`, environment `b7cfa813-8a5c-4fcd-80f2-cab736d840a7`. **Railway MCP is
  authenticated as the owner again as of 2026-08-17** — the expiry that blocked the Martin `statement_timeout`
  change (§2) is over, Martin restarts are automatable again, and **the change is unblocked but still NOT
  applied** (§7 step 2c).
- **`SET statement_timeout` explicitly on every long statement.** Redirect long output to a file and use
  `python -u`; piping a background run through `tail` yields an empty file until the process exits.

### The memory picture — read before "fixing" the gauge

- **The 3 GB reading is page cache and cannot fall.** Six proofs: `shared_buffers` is only 256 MB (8.5% of cap);
  2 client backends at sampling; 99.98% of limit during a 150s window with 35 transactions and 1.2 MB read;
  post-restart refill 0.051 GB → 2.98 GB in ~50 min from a 42 GB data dir; no OOM signature; Railway counts page
  cache via cgroup. **It will read ~100% at any cap you set.**
- Verdict refined: **(b) ordinary cache fill for the steady gauge, (a) genuine pressure during bursts.** A pulse
  burst previously read **1,203 MB in a single 15-second interval**, then ~6 minutes of near silence. So a lower
  cap is riskier than a pure-cache reading suggests — bursts hit the ceiling, not the idle baseline.
- **Judge changes by idle/burst window sampling, never the lifetime figure.** Lifetime cache hit is 30.96% over
  37.5 billion reads with `stats_reset` NULL; one session cannot move it.
- **Base memory is autovacuum, not cache:** `autovacuum_max_workers = 10` × `maintenance_work_mem = 128 MB` is up
  to **1.28 GB** non-reclaimable with zero users. That is the cut approved in §3.
- **TimescaleDB is loaded and delivering nothing** — one hypertable, `tracking.positions`, **0 chunks / 40 kB**,
  no compression, retention or CAgg policies. It costs a launcher plus scheduler workers.

### THERE ARE ZERO USEFUL HYPERTABLES

`agri.signal_observation` is a **plain heap table**. Nothing ever called `create_hypertable` (repo-wide grep
returns nothing; `plans/postgresql-18-migration-rehearsal-2026-08-02.md:50-51` recorded 0 hypertables).
Therefore **continuous aggregates and columnar compression are both unavailable.** Conversion is not a quick win:
`create_hypertable(migrate_data => true)` over 46M rows / 26 GB on a 3 GB cap rewrites the whole table, and
**`pk_signal_observation PRIMARY KEY (id)` is illegal on a hypertable partitioned by `observed_at`** — a
hypertable's unique constraints must contain the partitioning column, and `id` carries FKs.

### Compression conflicts with backfill

If hypertable conversion ever happens: compressing old chunks blocks or badly slows inserts into them, and this
service's whole data-completion workstream is historical backfill. `compress_after` must sit beyond the backfill
horizon, which today is most of the table.

### Layer populations — "21 layers" matches nothing

`LAYER_REGISTRY` has **27** toggles (`layer-registry.ts:171-433`), `geo.layers` has **11** rows, the slider
capability catalogue publishes **24** entries (11 + 13 non-features streams). Scope uniformity work against
27/24. **Nothing on the layer side is deletable** — all 11 `geo.layers` rows have a real producer and real rows,
down to `interventions` at 2 features (kept: `conductor/tracks/community_engagement_completion_20260805` is
active). **Do not drop the 16 empty `agri.forecast_*` governance tables** — they are the upstream of
`mv_forecast_ml_daily_serving`.

### THE DISPOSABLE-DB TEST GATE WAS DARK FOR A WHOLE ALEMBIC REVISION

**Found 2026-08-17.** `tests/conftest.py:31`'s `EXPECTED_ALEMBIC_HEAD` **was never bumped when alembic
`20260816_0024` landed.** `_assert_head_and_safe` **hard-fails** (does not skip) when `AGRI_TEST_DATABASE_URL`
is set and the revision mismatches — so every `agri_db`-marked test **refused any database actually at head**
for the entire window 0024 and 0025 were live. Now bumped to `20260817_0025`.

**This is the mechanism behind §4's "2494 passed unset vs 2536 set" discrepancy.** The gate did not fail loudly;
it stayed quiet only because **nobody ran the DB-backed sweep against a genuinely current head during that
window**. A gate that is dark and silent is worse than one that is red.

**22 test files depend on `agri_db_dsn` / `agri_db_async_dsn` / `agri_db_connection` and are all candidates for
the same rot.** Repairing the pin immediately exposed one:
`test_jobs_pulse_agri_db.py::test_run_jobs_pulse_reaches_a_real_dispatchable_lane_and_a_real_durable_definition_in_one_call`
failed with 4 lanes instead of 2, because `run_jobs_pulse` was called without `include_maintenance=False`
(`include_maintenance` defaults **True**, and `_plan_maintenance_steps` adds a reconcile + plan-gaps step per
durable entry regardless of `lane_filter`). Fixed at the call site.

**And that test carried a latent WRONG-TARGET hazard, which is the real lesson.** `ingest/commands.py:21`
imports `ingest_session` into **its own namespace**, so the test's `_bind_real_ingest_session` helper — which
patches only `jobs_pulse_command.ingest_session` — never rebinds it. Had maintenance actually run,
`reconcile_archive_lane` / `plan_archive_lane_gaps` would have resolved their session through the **real**
`db.engine.ingest_session()`, reading `settings.require_local_source_loader_database_url()` — **a different,
unmocked, env-configured DSN, not the disposable engine.** Monkeypatching a name in one module does not rebind
a `from X import Y` copy taken in another.

**Highest-risk next checks** (both exercised directly by `jobs_pulse_command.py`):
`test_jobs_dispatch_agri_db.py` and `test_strategy_mv_refresh_postgresql.py`.

**Working recipe** — the disposable warehouse is podman container `agri-sweep-db` on host port **5442**, database
`agri_sweep`, and it is now migrated to head `20260817_0025`:

```
AGRI_TEST_DATABASE_URL="postgresql://plantgeo_owner:sweeplocal@127.0.0.1:5442/agri_sweep" \
  PGBIN="C:\Program Files\PostgreSQL\16\bin" uv run pytest tests/<file> -q
```

### Build, test and git traps

- **`npm test` is broken repo-wide.** The packaged script runs `vitest run --configLoader runner`, which fails
  **every** suite with "Vitest failed to find the runner" — verified on files nothing touched. Drop the flag and
  the identical suite passes (1,192 tests).
- **mypy is unconfigured and has never been green.** HEAD baseline is **679 errors across 70 files**, verified by
  running the same interpreter against a detached worktree. There is no `[tool.mypy]` section and no CI gate.
  Diff against the baseline; never treat red mypy as a regression. **Ruff is the real Python gate** and is clean.
- **Never `git add -A`.** Multiple sessions edit this tree. Three things must stay uncommitted:
  `services/agri-data-service/ml/`, the unstaged deletion of
  `services/agri-data-service/docs/research/label-harvest-strategy-2026-08-14.md`, and any runbook artifact.
- **Pre-commit runs lint-staged + `eslint --fix` and exceeds 2 minutes** — allow ≥10 min for `git commit`.
- `stash@{0}` is `codex-preserve-pre-main-integration-20260726`, pre-existing — **never drop it**.
- Repo convention: **batch all edits, then one sweep** —
  `npm run check:data-boundary && npm run type-check && npm run lint && npm test && npm run build` (~340s) plus
  `pytest -q`, ruff.
- A commit appeared on `origin` this session with content byte-identical to a local one (`4d1345e` vs `bb03b08`),
  producing an `ahead 1, behind 1` divergence. Resolved with `git reset --mixed origin/main`. **Something else
  commits and pushes to this repo** — check `git status -sb` before assuming your local branch is authoritative.

### Migration ordering — getting this wrong blocks every deploy

`readiness-migration-contract.test.ts` asserts `migration-contract.ts` matches the journal's **latest** entry, and
landing that pair **before** the migration is live in prod makes `/api/ready` return 503 and fails the Railway
healthcheck. Correct order: ship the `.sql` alone → apply to prod → commit journal entry **and** contract re-pin
in **one** commit → push → **restart Martin** if any tile function changed. The journal legitimately jumps 25 → 27
(0026 was a reverted agri-schema mistake) — do not "repair" that gap.

**Never write the literal drizzle statement-breakpoint marker inside a comment.** `drizzle-orm/migrator.js:16`
does a naive `query.split()` on that string, so even in a comment it cuts the file header in half and kills the
migration with a 42601 syntax error. This bit 0029 and would have broken every deploy.

**`scripts/apply-pre-aggregation.mjs` hardcodes `SET lock_timeout = '5s'`,** which cannot clear a running
`observed_days` census snapshot. The first `CREATE INDEX CONCURRENTLY` died on 55P03 and left an **INVALID**
index that had to be dropped and rebuilt at `lock_timeout = 20min`. Always check `pg_index.indisvalid` after a
CONCURRENTLY build.

### A blank map in a background Chrome tab is a harness artefact, not an outage

A tab that is not the **active** tab of its window reports `visibilityState: "hidden"` **even when
`document.hasFocus() === true`**. Chrome suspends rAF, MapLibre never loads its style, **zero** tile requests
fire, and the page renders an empty map frame — indistinguishable from a total layer outage. This reproduced
exactly in the UI-defect pass and cost the whole visual verification (§4).

**`document.hasFocus()` is NOT sufficient. Assert `document.visibilityState === "visible"` before believing any
blank-map observation.** That is the new detail on top of the existing `plantgeo-hidden-tab-blank-map` finding.

### A backtick inside `LayerTimeSlider`'s CSS breaks the whole build

`LayerTimeSlider.tsx` injects its slider CSS through a **JavaScript template literal**
(`useLayerTimeSliderStyles`). A backtick anywhere inside it — **including inside a CSS comment** —
terminates the string early and the rest of the file parses as broken JavaScript. This happened
2026-08-17: a comment written as `` `filter: drop-shadow(...)` `` produced
`Cannot find name 'drop'`, `Cannot find name 'shadow'` and six `TS1005` errors, and **the entire repo
stopped compiling**. Nothing about the error messages points at a comment. Write that comment block with
no backticks; the file now carries a note saying so.

Same family as the drizzle statement-breakpoint trap (§5, "Migration ordering"): **a marker that is inert
in prose but load-bearing to a naive parser, hidden inside a comment.** When a file's content is embedded in
another language's string literal, comments are not safe ground.

### Traps discovered — do not "fix" these

- **§9 carries an eight-item list of map behaviour that LOOKS like a bug and is correct** — snapshot layers
  drawing their whole record with no slider, vegetation's monthly GIBS composite not moving inside a month, the
  `setStyle` gotcha being absent from the date path, out-of-order clobbering being structurally impossible. Read
  it before opening a map defect.
- **The 1,069 firms "missing" days are governed absences.** Month histogram over 25 years: 68% Dec–Feb, 1.7% peak
  fire season. A fetch defect cannot preferentially fail in winter. `ingest/archive_walk.py:603` states the rule.
- **`surface_shortwave_radiation` is a governed dead layer, not a broken one** — `agri.signal_coverage_audit`
  holds 397 `no_data` windows over all 397 POWER cells, 2022-08-02 → 2026-08-02.
- **`geo.watershed_rollup` is populated (2,162 rows) and `geo.refresh_watershed_rollup()` is never called** by
  anything in the repo. It has been serving stale data since it was built.
- The `soil` toggle's `permanentlyUnavailableReason` (`layer-registry.ts:289-290`) is **stale and wrong** —
  `geo.raster_release` holds 12 rows published 2026-08-10.
- **Do not confuse `geo.features_layer_external_id_unique`** (811 MB, UNIQUE, 23 scans, **KEEP**) with the dropped
  `idx_features_layer_external_id_lookup`. Also keep `idx_features_layer_updated_at` (73 MB, 9,595 real scans) and
  `uq_geometry_version` (400 MB, backs a UNIQUE constraint).
- **The index DDL is deliberately not the obvious one:**
  `ON geo.features (layer_id, geo.feature_observation_day(properties)) INCLUDE (geometry_id) WHERE status = 'published'`
  — **not** `AND geometry_id IS NOT NULL`, because `getMetricAtDate`'s summary *counts* the `geometry_id IS NULL`
  rows and partialling them away makes that count unanswerable.
- ~~**`reconcile.py`'s `session_lock` serializes the shard fan-out**~~ — **removed** with the sharding revert
  (there is no fan-out left to serialize). Kept here only so the finding is not re-raised as still open.
- Two ML labels were silently lost at load because their `condition_envelope` was empty — by design
  (`expert_label_plane.py:128-131`), but nothing warns at authoring time. Harvest doc had 32; prod holds 30.

---

## 6. Key files

### Sharding revert — DONE (was §7 step 1)

`observed_day_shards`, `session_lock` (moot once unsharded) and `_probe_census_shards` are **removed**. Any
note below still naming them is describing the pre-revert tree.

- `services/agri-data-service/src/agri_data_service/ingest/reconcile.py` · `observed_layer_days`,
  `observed_layer_days_in_range`, `MAX_OBSERVED_DAY_ROWS = 50_000`.
- `services/agri-data-service/src/agri_data_service/sql/ingest/observed_days.sql` · the `{layer_scope}` and
  `{day_scope}` slots; the `-- observed_days` dispatch marker must stay line 1.
- `services/agri-data-service/src/agri_data_service/ingest/validation/queries.py` · `_ALL_DAYS_SCOPE`,
  `_DAY_RANGE_SCOPE` (inclusive at both ends), `OBSERVED_DAYS_FOR_LAYER`. **The only place `observed_days.sql` is
  `.format()`-ed.** `MAX_OBSERVED_DAY_ROWS` must stay a package-level global on `validation/__init__.py` — tests
  monkeypatch it there.
- `services/agri-data-service/src/agri_data_service/execution/jobs_pulse_command.py` · `_execute_maintenance_step`,
  `DEFAULT_PULSE_TIME_BUDGET_SECONDS = 600`, and the standing-failure signal: `_COUNT_DEAD_LETTERED_WORK_ITEMS`,
  `read_standing_dead_letters`, `_fold_in_standing_dead_letters`, `FAILING_PULSE_OUTCOMES`.
- `services/agri-data-service/src/agri_data_service/sql/jobs/count_dead_lettered_work_items.sql` · the census.
- `services/agri-data-service/src/agri_data_service/jobs/worker.py` · `slice_summary_is_failing` (one slice's
  landing only — it deliberately reads no run status).
- `services/agri-data-service/src/agri_data_service/ingest/commands.py` · unowned file that wraps reconcile and
  validation for the pulse — a signature change here breaks the pulse at runtime with no file conflict.

### Matviews and refresh

- `drizzle/0029_pre_aggregation_layer.sql` · the nine matviews; `:897-901` header prescribes the geometry repair
  chain, `:918` omits `ST_CollectionExtract(...,3)` — that is the bug in §7 step 3.
- `services/agri-data-service/src/agri_data_service/jobs/matview_refresh.py` · the watermark lane; `:294`
  registers `mv_soil_survey_union`.
- `services/agri-data-service/db/agri/tables/matview_refresh_state.sql` · the refresh-state ledger.
- `drizzle/0023_watershed_zoom_generalization.sql:21,110` · the rollup precedent (MV + refresh function).
- `src/lib/server/services/usda-soil.ts:1047,1148` · documents that `readAggregatedFeatures` and
  `readSummaryFeatures` were **not** repointed at the soil matviews.

### Serving and catalogue

- `src/lib/server/services/{environmental-read-model,regional-context,analytics,usda-soil,tracking}.ts`
- `src/lib/server/trpc/routers/{analytics,jobs,visualization,interventions}.ts`
- `src/lib/map/layer-registry.ts` · 27 toggles; `warehouseLayerName` drives the §9 `LEFT JOIN`.
- `src/__tests__/services/pre-aggregation-catalogue.test.ts` · hand-spelled catalogue assertions.
- `src/lib/cache/AGENTS.md` (read first), `src/lib/cache/query-persister.ts:68` allowlist, `:120`
  `resolveCacheTtlMs`.

### Governance and ML

- [`docs/layer-lane-standard.md`](../docs/layer-lane-standard.md) · **268 lines, the contract.** §5 completeness
  engine · §6 self-healing loop · §7 governed absences in `agri.signal_coverage_audit` · §8 three crons + Railway
  traps · §9 the catalogue `LEFT JOIN` · §13 definition of done · §14 verified environment facts.
- `services/agri-data-service/ml/research/label-plane-readiness-2026-08-14.md` · census, 32 settled decisions,
  plan. **Line-number citations to `recommendation_models.py` (§3.1 → L347-355, §3.3 → L414) are stale.**
- `services/agri-data-service/src/agri_data_service/method/ml/recommendation_models.py` · Phase A landed:
  subject vocabulary on for both kinds, species-trait term groups, drought block in `SITE_COVARIATE_FEATURES`,
  `confidence_weight / instance_count`.
- `src/lib/server/db/migration-contract.ts` + `drizzle/meta/_journal.json` + `docs/pending-migrations/README.md`.

---

## 7. Continuation plan

### HANDOFF STATE — 2026-08-17, session ended on context exhaustion. Start here.

**SHIPPED AND VERIFIED IN PROD: `de3139e`** (55 files, +7,569/−426, pushed to `main`). Contains the C2/D1/D3/D4/
D5/D7/D10/D12 client fixes, the offline sync-on-scrub feature (§11), the uniform request budget, the two-origin
`martin.yaml` fix, and **the satellite default that had never shipped**. Verified after deploy: `/api/ready` 200
in 0.41 s, and **Martin now returns `access-control-allow-origin` for BOTH origins** (the raw Railway domain was
hard-blocked before). Sweep before commit was fully green — 1,320 JS tests, 3,057 pytest, ruff, build, `tsc`.

**NOT VERIFIED, and this is the biggest gap:** every *visual* behaviour in `de3139e` is unconfirmed — the
satellite basemap, the synced-days track, the pending indicator, the D1 date filter. The automation tab reported
`visibilityState: "hidden"` on three attempts, which suspends rAF so MapLibre never paints and **zero tiles of
any kind fire** — do not read a blank map or a zero tile count as evidence of anything (§5). **This needs one
session in a foregrounded Chrome window with the tab active.** It is the highest-value cheap next action.

**AGRI BATCH: both NO-GO blockers are FIXED. Uncommitted, not applied, and it needs a SECOND REVIEW PASS** — the
author explicitly declined to self-approve after a NO-GO, which is the right call.

- **Blocker 1 fixed structurally, not by documentation.** The migration is **split so the dangerous ordering is
  impossible**: `drizzle/0031_observation_day_axis.sql` creates the axis `WITH NO DATA` and **reads nothing and is
  read by nothing** (zero blast radius); `drizzle/0032_observation_day_census_repoint.sql` does the
  `CREATE OR REPLACE VIEW` **behind a precondition on `pg_class.relispopulated`** — deliberately not
  `to_regclass`, which resolves for a `WITH NO DATA` matview and would pass while handing production a 500.
  Three rolled-back prod proofs: 0031 alone leaves the census readable at 11,231 rows; 0032 against an
  unpopulated axis **raises as designed**; after populate the column OIDs are identical
  (`25,1043,1082,20,20,20,1184,3802`) and the census reads 11,231 rows with **0 ghost days**. The wrong FULL-JOIN
  rationale is deleted; the FULL JOIN is kept and justified honestly (the two relations refresh on *different
  cadences*, so each can legitimately know a `(surface, day)` the other does not).
- **Blocker 2 fixed and the test is proven to have teeth.** `has_failures` now precedes `budget_exhausted`
  (`matview_refresh.py:1195-1198`); reverting the branch order makes the new test **fail**, so it is not
  decorative. Two tests, so the fix cannot over-reach: a budget-exhausted tick with a standing failure must be
  `failed`, and a *clean* budget-limited tick must still park.

**THE AUTHOR'S OWN HEADLINE CLAIM WAS WRONG BY 13×, self-found and retracted. Read this before budgeting the
apply window.** The axis populate measured **286.8 s on prod, not the extrapolated ~22 s** (11,231 rows, against
the combined statement's 283,049 ms). The 0.82 s/layer extrapolation assumed the *aggregate* dominates; at 5M rows
the **seq scan and the `properties` detoast dominate and both variants pay them**. Consequences:

1. **The re-grain is NOT a latency win.** It is a **spill-width** win (33 B/tuple vs 511 → ~165 MB vs ~1.4 GiB)
   and a **reliability** win (287 s completes under a 900 s cap; it never will under 300 s). **Total refresh
   seconds per day go slightly UP.** That is a deliberate trade of seconds for peak allocation and correctness —
   which is exactly the right trade given §12.6, but it must not be sold as "faster".
2. The author **had freshly reintroduced the very defect it was fixing** — the axis spec shipped with
   `statement_timeout_seconds = 300` against a 287 s statement, 96% of cap. Now 900 s.
3. **The next real lever, recorded rather than guessed:** `ix_features_layer_observation_day` covers every column
   the axis reads; the only thing forcing a heap scan is the fire-perimeters `properties` COALESCE guard. An
   index-only branch for the ten layers carrying no `fireDiscoveryDateTime` could remove the detoast outright.
   DDL redesign, needs its own EXPLAIN, **not attempted.**

**Other corrections now in-tree:** every figure re-read live (**791 s/tick doomed work, FOUR views**, soil at
86,320 / 104,269 ms); the `Parallel Hash` rationale replaces the false "parallel workers = the leak" story, with
the rule stated as **"does the plan contain `Parallel Hash`?"** not "is it parallel?"; `= 1` is documented as
*what shipped, not a mitigation*; wide census cadence 86,400 → **21,600** (six-hourly, keeping the
`newest_observed_at` **status driver** under a day); budget 2,400 → **2,100** and lease 3,000 → **2,700** (worst
tick ~32 min, ~25 min clear of the hourly cron); `mv_signal_cell_daily` 1,800 → **1,900**;
`SUCCESSFUL_REFRESH_OUTCOMES` exported so the reset rule is defined once; and **a new
`tests/test_alembic_head_pin_contract.py`** that makes the §5 dark-gate class impossible (note its regex needs
`(?:\s*:[^=]+)?` — this tree mixes `revision = "..."` and `revision: str = "..."`, and the bare-form-only version
silently skipped 0001 and derived the wrong head).

**Gates:** ruff clean · mypy 2 pre-existing · **160 passed / 1 skipped** · declarative parity green · 2 separators
each in 0031/0032 with **0 in prose** · **0** `CONCURRENTLY` outside comments.

**APPLY ORDER — do not improvise, and budget ~5 minutes for step 3, not seconds:**
1. `alembic upgrade head` → `20260817_0025`. **Before the Python deploy** — the shared upsert writes the two new
   columns unconditionally, so the ledger write fails until they exist. Additive, `IF NOT EXISTS`, re-runnable.
2. `drizzle/0031_observation_day_axis.sql`. Safe at any time; nothing reads the new relation.
3. **Same window:** `SET statement_timeout='900s'; REFRESH MATERIALIZED VIEW geo.mv_feature_observation_day_axis;`
   — non-concurrent (CONCURRENTLY is illegal on an empty matview). **~287 s.** Then assert `relispopulated = true`.
4. `drizzle/0032_observation_day_census_repoint.sql`. **Refuses if step 3 was skipped or failed.**
5. **One commit:** journal entries for **both** 0031 and 0032 + `migration-contract.ts` re-pinned to 0032. Not
   before 2–4 are live. `0030` stays authored-but-unapplied.
6. Deploy the Python. **No Martin restart** — no tile function changed.
7. Clear the 15 standing dead letters when green is wanted; the three still-broken views keep it red via
   `deferred_failing`, which is intended.

**Owner decisions still open:** whether to commit the pre-existing pulse batch (~456 lines in
`jobs_pulse_command.py`, deliberately not swept into `de3139e`); whether to clear the **15 standing dead letters**
that keep the hourly pulse red; §10 decision (c) on `tile_interventions` gaining a day column.

**Cheapest high-value next actions, in order:** (1) the browser validation pass above; (2) close the two agri
blockers and apply in the revised order; (3) `drizzle/0030` — approved to land and keep, 25–60 min out of band,
`btree_gist`, `lock_timeout = '20min'`, verify **both** `indisvalid` and `indisready`, DROP any INVALID leftover
first; (4) §10's tile relations, which are the real answer to §12.6; (5) the 5-line alembic-head parity test that
makes the §5 dark-gate class impossible.

**REPRIORITISED 2026-08-17 by owner directive. Work the A-block first; the numbered legacy plan below it is
unchanged and still correct, but is no longer the top of the queue.**

- **A1. The cheap client fixes, as one batch.** C2 (one `setQueryData` in `revalidateAgainstDW` — every
  allowlisted layer is permanently one fetch behind), D1 (`applyDateFilter` onto the `styledata` convergence
  path), D5 (`placeholderData: keepPreviousData` **plus the loading state §11.1 step 2 requires** — one change,
  not two), D4 (caption from `resolvedDate`, not the slider day), D3, D7, D10. Ordered cheapest-first in §9.
  **These are what make the map trustworthy; nothing downstream is judgeable until they land.**
- **A2. Commit and deploy the satellite default** (§11.6). It is in the working tree and has never shipped —
  `HEAD` still opens on `dark`. Decide separately whether `currentStyle` should persist across reloads.
- **A3. Verify the service worker actually installs** (§11.7). Code-traced dead, not observed. One browser
  session. **Do this before building anything that assumes offline works.**
- **A4. The sync index** (§11.2) — stamp `layerId` + `day` onto `StoredLayerQueryEntry`, bump
  `CACHE_SCHEMA_VERSION`, expose enumeration. **Everything else in §11 is blocked on this.**
- **A5. The synced-days track** (§11.3) and **per-timeline reset** (§11.4).
- **A6. The uniform rate limiter** (§11.5) — one client-side limiter over the ~16-key scrub fan-out, covering
  `/api/fires`' raw-fetch path too. Do not extend either server-side Redis limiter.
- **A7. The two census matviews via delta refresh** (§12.4). **Coupled to §11:** until a newly-ingested day is
  selectable, the sync track will faithfully cache a frozen axis. This is also the C3 causal spine.
- **A8. Then the DB block below** — 0030 (now approved to land and keep, §12.5), §10's relations, the Martin
  timeout **after** the latency work, and the two-origin `martin.yaml` fix.

1. ~~**Revert the sharding.**~~ **DONE.** `observed_layer_days` is a single unbounded statement again (68.7s vs
   95.3s, block reads a wash, 57% of the 120s timeout); `session_lock` and `_probe_census_shards` are gone.
   `{day_scope}` in `observed_days.sql` and `_DAY_RANGE_SCOPE` in `validation/queries.py` were kept on purpose —
   bounded day ranges are what the new expression index makes fast, so the *slider* still wants them, and
   `MAX_OBSERVED_DAY_ROWS` still lives on `validation/__init__.py` where the tests monkeypatch it.
2. **Land the tile index, then the Martin timeout — in that order.**
   a. Build `ix_features_layer_geom` out of band per §2 ("Migration authored, NOT applied"): `btree_gist`,
      `lock_timeout = '20min'`, verify `indisvalid` **and** `indisready`, DROP any INVALID leftover before a
      retry. Re-measure a `geo.*_tiles()` call; the target is the 45.6s BitmapAnd disappearing.
   b. Only once it is live in prod: commit `drizzle/0030_features_layer_geom_tile_index.sql`, the journal entry
      and the `migration-contract.ts` re-pin **in one commit**. Landing that pair early 503s `/api/ready`.
   c. Append `options=-c statement_timeout=20000` to `DATABASE_URL` on **`plantgeo-martin` only** (**unblocked** —
      the Railway MCP is authenticated again as of 2026-08-17; it is simply not done). A tile that would take 45s
      then fails fast instead of holding one of 8 pool connections. Note §9's measurement: cold composites run
      **84–117 s**, so a 20 s cap will shed real tiles, not just pathological ones — that is the intent, but the
      blank-layer consequence via `sources.ts:28-32` must be expected.
      **NEW RISK, ordering (2026-08-17): `fire_risk_tiles` is measured at 117 s.** A 20 s cap converts that layer
      from "slow but eventually renders" into **"always fails"** until step 2a's index or §10's relation work lands.
      Sequencing therefore matters — apply the cap **after** the latency work, not before, or accept a hard
      `fire-perimeters` outage in the interval. **Flagged, not decided** (§3).
   d. **Make `martin.yaml` list BOTH origins — the durable fix for §2's CORS outage.** Today `cors.origin` takes one
      interpolated entry, so setting `TILE_CORS_ORIGIN=https://plantgeo.aevani.com` (applied to prod this session)
      **blocks** `https://plantgeo-main-production.up.railway.app`. Change `infra/martin/martin.yaml:7-10` to a
      two-element list, then **deploy `plantgeo-martin`** — repo change + deploy, **NOT done**. In the same pass
      correct the stale documented value at `docs/deployment.md:383` and `infra/railway/README.md:98`.
3. **Base-memory cuts are ON HOLD, not pending application** — see §3. Do not `ALTER SYSTEM SET
   autovacuum_max_workers = 3` until autoanalyze on `geo.features` has been observed keeping up, and do not cut
   `max_connections` before a real concurrent-connection count exists (it pulls against Martin's `pool_size`).
   Both still require a full restart, not `pg_reload_conf()`, when they do happen.
3. **Fix `geo.mv_soil_survey_union`.** It fails to build with
   `GEOS TopologyException: unable to assign free hole to a shell at -121.5955,42.6522`, and
   `matview_refresh.py:294` retries and fails on **every pulse**. The file's own header (`0029:897-901`)
   prescribes snap → MakeValid → **CollectionExtract**, but the `delineation` CTE at `:918` applies only
   `ST_MakeValid(ST_SnapToGrid(f.geom, 0.000001))`. Add `ST_CollectionExtract(..., 3)` in a new migration, rebuild
   the matview, then **repoint `usda-soil.ts:1047` at it** — today it has no reader, which is why the failure has
   been costless.
4. **Measure base memory and propose a cap.** After the restart, report actual non-reclaimable usage:
   `shared_buffers` + peak backend `work_mem` + autovacuum workers + TimescaleDB workers. Sample an **idle window
   and a pulse-burst window separately** — the burst is what a lower cap would hit. Then recommend a specific cap
   with headroom. Do not lower it before this number exists.
5. **Re-grain `geo.mv_signal_cell_daily`.** 6,349 MB / 24,968,939 rows / 25-minute refresh for only a ~1.8×
   reduction, because its grain `(support_key, signal_name, normalized_unit, cell_id, observed_day)` is nearly the
   source grain. Keep the release-winner dedup (`DISTINCT ON ... release_retrieved_at DESC`) that makes reads
   cheap; cut the width. Re-measure its size and refresh time after.
6. **Resume the layer-lane conformance backlog** — the 12-item ranked list in §8's 2026-08-15 entry. Highest
   blast radius first: the **454 `firms-archive` work items queued since 2026-08-08** (drain *after* step 2, since
   they drain into the throttled box); the **signal plane being on no schedule at all**; `coverage_fill`'s
   **filesystem `Path.exists()` idempotence key**, which is not durable on an ephemeral cron container; and the
   **agent drought tool reading the 0-row `drought_polygon_snapshot`** while the map serves `geo.drought_areas`.
7. **Clear the deferred review findings** (full list in §8): a disposable-DB contract test for `to_regclass`
   semantics (MEDIUM #6), repointing `routes/ops.py` at `jobs/lease.py::failure_condition_name` (MEDIUM #7), an
   EXPLAIN that actually demonstrates the index-causation claim (MEDIUM #4), and the structurally-unreachable
   `MAX_OBSERVED_DAY_ROWS` cap on the one-layer path (MEDIUM #5, pre-existing).
8. **Then the deferred workstreams:** USDM and ERA5-Land as self-healing lanes, the persistence audit, the FIRMS
   `INGEST_MAX_SOURCE_RECORDS` decision, ML Phase B. Phase B is **hard-blocked** until drought covariates exist —
   drought features are now required inputs, so a missing covariate index 36–38 makes `build_design_row` return
   `None` for every row.

---

## 8. Session log

### 2026-08-21 — the map got fixed; three changes, and a great deal of scaffolding around them

- **Done:** composite split (`c1c0428`); service-worker cache-first for Martin tiles plus a per-layer Refresh (`2b38c66`); `drizzle/0038` `sensor_tiles` `DISTINCT ON` **applied to production** — 14,258,826 → 745,755 bytes (19.1×), read straight from Postgres so Martin's 5-minute cache cannot flatter it. Owner confirms almost all layers now render with caching working.
- **Reviewed:** the composite-split lane caught that `getSources()` has ZERO callers and `styles.ts` hardcodes the source map — repointing layers alone would have failed MapLibre style validation and blanked the WHOLE map, not five-of-six. The tile-function lane overturned the brief it was given: `EXPLAIN` prices all five functions identically because the layer is chosen by name through a join (§0.21.2). Both corrections were load-bearing; neither was in its brief.
- **In flight:** nothing. Working tree clean, level with origin at `2b38c66`. All workflows and monitors stopped at the owner's instruction.
- **Blocked:** nothing blocking. `drizzle/0030`–`0038` are applied-or-dormant and unregistered (§0.21.6) — recorded, deliberately not reconciled, because the owner prefers a greenfield reset over investing further in a lineage that is being migrated to Parquet.
- **Next:** start the Parquet path (§7 and §0.19.5 Tier 4, with gates 33 and 34 WAIVED) — the owner's chosen priority over further map polish.

### 2026-08-17 — owner directives folded in; client batch COMMITTED AND PUSHED as `de3139e`

**The client half is committed and pushed: `de3139e`, 55 files, +7,569 / −426, `890f430..de3139e`.** Pushing to
`main` is the deploy path, so this is live-or-deploying. **The agri half is deliberately NOT committed** — it
carries a drizzle migration and must follow the ship→apply→pin ordering (§5), and its adversarial review was
still open at commit time.

**Sweep, all green:** `check:data-boundary` · `type-check` · `lint` · **1,320 JS tests passed / 13 skipped** (up
from the 1,192 baseline, so ~128 new) · `npm run build` · `tsc --noEmit` exit 0 · **`ruff` All checks passed** ·
**`pytest` 3,057 passed / 105 skipped** (§4's old "2494 unset vs 2536 set" figures are superseded).

**Also in `de3139e`, beyond the defect fixes:** the satellite default (which had never shipped — `HEAD` opened on
`dark`), the two-origin `martin.yaml` fix, and the `CLAUDE.md` correction that Martin is **v1.10.1, not v1.4**.
**`martin.yaml` is `COPY`'d into the image at build time, so this needs a genuine REBUILD of `plantgeo-martin`;
a restart reuses the old baked-in config and appears to do nothing.**

**Deliberately left uncommitted:** `conductor/RUNBOOK.md`, `services/agri-data-service/ml/`, the
`label-harvest-strategy-2026-08-14.md` deletion, the whole agri tree, and `drizzle/0030`/`0031`. **The
pre-existing pulse batch (`jobs_pulse_command.py` and friends, ~456 changed lines) is also still uncommitted** —
it is a separate concern from an earlier session and was not swept in.

**Judgement call recorded:** `map-store.ts`, `ServiceAreaLayer.tsx` and `coverage-region.ts` carried an *earlier*
session's uncommitted viewport work. They went in because `map-store.ts` is where the satellite default lives, so
excluding it would have dropped the owner's request.

Seven parallel lanes on disjoint file boundaries; the partition held with zero collisions. **All four client
lanes were adversarially reviewed and all four returned CHANGES-REQUIRED** — see the §2 ledger.

**Owner decisions taken:** land AND keep `drizzle/0030` permanently · refresh incrementally rather than
recompute (continuous aggregates named; see §12.3 for why they are blocked and what replaces them) · offline
sync is a **byproduct of scrubbing**, not a bulk button (§11) · **RAM is the constraint, disk is not.**

**Landed in the working tree:**

- **C2 FIXED — the highest value-per-line change in the batch.** `revalidateAgainstDW` persisted to IndexedDB
  and dropped the result into a floating promise; nothing ever called `setQueryData`, so **every allowlisted
  layer rendered exactly one fetch behind, permanently.** Fixed with `createIndexedDbLayerQueryPersister(getQueryClient)`
  — a factory closing over the client, injected from `providers.tsx`. `query.setData()` was **rejected**: it is
  a `query-core` class method, not app-facing API, and skips the client's `structuralSharing`. A module-global
  client was rejected as hidden mutable state that binds to whichever client registered last.
- **The sync index (§11.2) is built.** `src/stores/sync-index-store.ts` ships the pinned contract verbatim:
  `useSyncedDays` · `useSyncIndexReady` · `useLayerSyncedBytes` · `clearLayerSyncedDays` · `useHydrateSyncIndex`.
  **`CACHE_SCHEMA_VERSION` was deliberately NOT bumped** — see the correction below.
- **`MAX_TOTAL_CACHE_BYTES` 50 MB → 512 MB, and the enabler matters more than the number.** Every write
  previously read the **entire** cache into RAM just to ask whether the incoming entry fitted. At 512 MB that
  pass is what would have killed the tab. Replaced with a **cursor walk** (`forEachEntry`) plus O(1) running
  totals. **This is the RAM-not-disk distinction applied correctly**: the budget rise is only safe because the
  working set no longer scales with the store.
- **Fires lane: D3, D7 and C5 fixed; D11 and D12 discovered** (§9). D12 in particular — a 304 could repaint the
  **wrong day's** content — would have made D3's new staleness label truthful-sounding but wrong.
- **§11.7's service-worker claim REFUTED against prod** and the section rewritten. Zero code changed.

**CORRECTION to §11.2, which was wrong in this runbook and in the brief derived from it: the `queryHash` IS
reversible.** It is plain `JSON.stringify(queryKey)`, so `JSON.parse` recovers the router path and the whole
input including `date` and the discriminators. A real key, measured from prod:
`[["environmental","getClimateField"],{"input":{"bbox":"-154.726352,29.145296,-81.273648,61.854704","renderForm":"field","signal":"air-temperature","variant":"mean"},"type":"query"}]`.
The shipped design **stamps forward and parses back as a fallback**, which is why no schema bump was needed and
why 504 live entries were not discarded. The `queryHash` format is nonetheless **react-query's internal
contract, not ours** — a minor bump could change it, which is exactly why the stamp exists.

**Measured live in production 2026-08-17 (real browser, `https://plantgeo.aevani.com`):**

| | |
|---|---|
| IndexedDB `plantgeo-query-cache` | **504 entries, 49.57 MB against the 50 MB cap — 99.1% full** |
| expired but still resident | **235 of 504 (47%)** — eviction ran only on write, only by LRU, **never by expiry** |
| service worker | `activated`, **CONTROLLING**; `caches.keys()` = `["plantgeo-v2"]` |
| `/manifest.webmanifest` | **200** (served by `src/app/manifest.ts`, a Next file-convention route) |
| cached paths | `getSoilField` 137 · `getStreamflow` 104 · `getGroundwater` 99 · `getWeatherForBbox` 80 · `getVegetationIndex` 51 · `getDroughtClassification` 30 · `getClimateField` 3 · `getSoilSurvey` **0** · `getWatersheds` **0** |

**Known limitations, accepted and stated rather than hidden:**

- **`useSyncedDays` keys on the day alone, not `(day, bbox)`.** The track means "an answer for this day is on
  disk", **not** "the next read will hit". Driven by D6 — the bbox is serialized at 6 decimal places, so a
  ~10 cm pan mints a new key. If D6 is fixed with a quantised bbox the over-claim narrows automatically.
- **`getStreamflow` and `getGroundwater` both attribute to the `water` toggle.** Two feeds, one toggle — so a
  day holding only one of them still reads as synced. Flagged for adversarial review as a possible user-visible
  over-claim.
- **Dateless entries never appear on any track.** `useClimateFieldQuery` passes `date: undefined` whenever a row
  sits at server-today, so those entries take the 5-minute live TTL and name no day. Stamping them "today" would
  become a lie at UTC midnight. Consequence: **a layer sitting exactly at server-today will not light up.** Per
  the staleness table this affects only the 1-day-lag group; the 11 layers that are 11–15 days stale open on a
  historical day and **do** attribute.
- **Revalidation policy changed to none-at-all on historical days, ≤1/min otherwise.** **This is under
  adversarial review as a possible C1 regression** — the agri pipeline *does* rewrite historical days, so a
  corrected day could stay stale for the full 30-day TTL with nothing to notice. Do not treat as settled.

**API break, repo-wide:** `indexedDbLayerQueryPersister` **no longer exists**. Use
`createIndexedDbLayerQueryPersister(getQueryClient)`.

### 2026-08-16 (late) — 0024 applied, map diagnosed, pulse review fixes (UNCOMMITTED)

**Prod changes applied this session:** alembic `20260816_0024` (creates `agri.matview_refresh_state`) and
`ANALYZE geo.features` + `geo.layers`. Numbers and the recovery evidence are in §2. Nothing else was applied;
`drizzle/0030` and the Martin `DATABASE_URL` change are both authored/decided but **not** applied.

**Adversarial review of the pulse batch: CHANGES-REQUIRED. Both HIGH findings shared one root cause**, and the
diagnosis was **independently re-derived and confirmed** before implementing:

- `_slice_outcome` derived `failed_run` from `JobSliceSummary.run_status`, which is wrong in both directions.
  *(a) It cannot fire when it should* — `select_open_job_run.sql` selects only `'queued'`/`'running'`, so the
  tick after a run rolls terminal selects no run, reports `run_status=None`, and reads healthy. Coverage was
  exactly ONE tick, and that tick was already loud via `dead_lettered > 0`. *(b) It fires when it must not* —
  `refresh_job_run_rollup.sql:98` counts a `'cancelled'` **work item** as `failed` and the run CASE has no
  `'cancelled'` branch, so an operator cancellation rolls the run to `'partial'`/`'failed'`, both of which were
  "terminally failed".
- **Confirmed by grep over every writer:** only two statements touch `agri.job_run.status` —
  `insert_job_run.sql` writes `'queued'`, the rollup CASE writes `'running'/'succeeded'/'failed'/'partial'`.
  **Nothing ever writes `'dead_letter'` or `'cancelled'` there**, so a third of the old
  `TERMINALLY_FAILED_RUN_STATUSES` set was dead code and the "cancelled is excluded" comment was vacuous.
- **Stronger than the review stated:** `jobs/matview_refresh.py` opens ONE never-rotating run by design, so it
  accumulates settled items forever. One historical burial or cancellation pins that run at `'partial'`
  **permanently** — the hourly cron would have exited 1 every hour forever, even with every current refresh
  succeeding, and the old detail line told the operator to "cancel it deliberately", which made it worse.

**Fix as shipped:** new `sql/jobs/count_dead_lettered_work_items.sql` counts `agri.job_work_item` rows in
`'dead_letter'`, joined up to `agri.job_definition.name`, across every run that definition ever opened —
issued **unconditionally per lane per tick** by `_fold_in_standing_dead_letters`, on the healthy path and the
raised path alike, and **failing closed** (an unreadable census leaves the lane `raised`, never `ran`). The
outcome literal `failed_run` became `standing_dead_letters`; `PulseLaneResult` gained a
`standing_dead_letters` count that triggers `failing_lanes` independently of the outcome label.
`TERMINALLY_FAILED_RUN_STATUSES` was deleted and `slice_summary_is_failing` now describes one slice's landing
only. `'cancelled'` is deliberately not counted — that is the safety property.

**Tests rewritten to exercise the runtime path, not the classifier.** `tests/test_jobs_pulse_command.py`'s
fake session now answers the census query for real, so a test states buried work **on the ledger** and lets
the pulse's own code find it. Three complicit tests were replaced: one that hand-built a
`no_claimable_work` + `run_status='failed'` pair the runtime cannot produce after burial; one that
parametrized `'dead_letter'`, never written to `job_run.status`; and one that parametrized `'cancelled'`,
likewise unwritable, while asserting the very safety property finding (b) proves false. New: buried items
stay RED on ticks 1, 2 **and** 3 · operator cancellation keeps the tick GREEN · every writable run status is
green on its own · the census is issued even when dispatch raised · an unreadable census fails closed · a
paused lane is never censused. Plus a real-SQL test in `tests/test_jobs_pulse_agri_db.py` proving the
three-table join counts a `'dead_letter'` item and **not** a `'cancelled'` one (DSN-gated; **not** run this
session — no database was touched).

**Also fixed:** MEDIUM #10 — `jobs/lease.py::failure_summary`'s SQLAlchemy branch now returns through
`clamp_summary()`, restoring the invariant that every return has passed redaction and clamping (not a live
leak; both halves are `__name__`s). LOW #9 — the stale `ingest.reconcile.observed_day_shards` pointer in
`ingest/validation/queries.py` is gone.

**Deferred, by explicit instruction — report only:**

| # | finding | why deferred |
|---|---|---|
| MEDIUM #4 | the index-causation claim is asserted, not demonstrated | needs a DB `EXPLAIN`; out of scope, no DB this session |
| MEDIUM #5 | `MAX_OBSERVED_DAY_ROWS` structurally unreachable on the one-layer path | pre-existing, not introduced by this batch |
| MEDIUM #6 | no disposable-DB contract test for `to_regclass` semantics | false-passes on indexes/sequences and on a matview created `WITH NO DATA` — recorded in §4 as a known gap |
| MEDIUM #7 | `failure_condition_name` duplicated in `jobs/lease.py` and `routes/ops.py` | `routes/` is not this batch's to change; follow-up in §7 step 7 |
| MEDIUM #8 | rationale duplicated across source and `AGENTS.md` | noted only |

**Frontend, same batch:** default basemap is now `satellite` (`map-store.ts`), and `DEFAULT_VIEWPORT` is
**computed** from the coverage bbox via a new optional `NEXT_PUBLIC_INGEST_BBOX` (falling back to the verified
prod values). **It needs a Dockerfile `ARG` to be settable in prod — NOT yet added.**

### 2026-08-16 06:10 — pre-aggregation live in prod; base-memory cuts approved; handoff

- **Applied to prod and registered as `890f430`:** drizzle 0028 + 0029, two `CONCURRENTLY` indexes, 8 of 9
  matviews, 5,392 MB of dead indexes dropped, `effective_cache_size` 768 MB → 2 GB, `work_mem` 4 MB → 16 MB,
  Martin redeployed. Numbers in §2.
- **Measured: sharding is worse.** 68.7s single vs 95.3s across 13 shards, `shared_blks_read` a wash. Revert
  decided.
- **Diagnosed base memory:** the 3 GB gauge is page cache and cannot fall; the real reservation is
  `autovacuum_max_workers = 10` × `maintenance_work_mem = 128 MB` ≈ 1.28 GB. Owner approved cutting workers to 3
  and `max_connections` to 25 with a restart; declined `shared_buffers`/`maintenance_work_mem` cuts and dropping
  TimescaleDB for now.
- **Caught a latent deploy-breaker:** 0029 contained the literal drizzle statement-breakpoint string inside a
  comment, which the migrator splits on — `npm run db:migrate` would have failed on every deploy.
- Resolved an `ahead 1 / behind 1` divergence against a byte-identical commit pushed by something else.
- **Grilling gate answers:** revert sharding first · re-grain `mv_signal_cell_daily` · fix (not drop)
  `mv_soil_survey_union` · measure before lowering the Railway cap.

### 2026-08-15 — pre-aggregation built (`wf_d69f07c0-dd9` → `4d1345e`)

- 11 new matviews + 2 orphans adopted (`watershed_rollup`, `mv_forecast_ml_daily_serving`), watermark-driven
  refresh via a new `agri.matview_refresh_state` ledger, read paths rewired, agent SQL tools repointed, catalogue
  registration extended.
- **Adversarial equivalence verifier refuted the build on 11 grounds, all fixed before commit** — the day-rule
  fork (four-key vs three-key COALESCE) that would have reported observations on days the map renders none; lane
  and unit gates missing from `mv_signal_cell_daily`; two watermarks omitting the clock component their own DDL
  requires; `getRecentActivity` over-counting by up to 59 minutes; an INNER→LEFT join changing the row set.
- Established: **zero useful hypertables**, so continuous aggregates and compression are unavailable; the "21
  layers" figure matches nothing (27/11/24); nothing on the layer side is deletable.

### 2026-08-15 — sharding wave (`wf_1920bfbd-7d9` → `4a685a1`)

- Sharded the observed-day census, surfaced `reopen_exhausted` gap windows in the admin console, rewrote the
  three `geo.mv_strategy_recommendations_*` matviews off fabricated `random()` coordinates onto real
  `agri.spatial_cell` geometry, and landed ML Phase A's four spec changes.
- Adversarial verifier refuted the per-shard truncation claim; two grounds died on contact, three stand
  (structurally unreachable cap, no test coverage, `session_lock` serialization).
- **Layer-lane conformance audit: 0 of 21 conformant**, producing the 12-item ranked backlog now in §7 step 6.
  Headline: 454 `firms-archive` work items queued since 2026-08-08 through ~168 hourly pulses; the signal plane
  on no cron at all; `coverage_fill.write_fill_plan()` using a filesystem path as its idempotence key; the agent
  drought tool answering from a 0-row plane; no pre-aggregation existing at all.

### 2026-08-15 — deploy pushed; self-healing correction

- The two held commits were pushed. Owner corrected the backfill approach: lanes must self-heal via the ledger
  and crons with no agent monitoring, and `docs/layer-lane-standard.md` is the governing contract (decision S5).
  Runbook steps 8 and 9 were rewritten from agent-run backfills into lane-conformance work.

### 2026-08-15 — ML label-plane review merged with the serving/maintenance handoff

- Prod census of the label plane found `expert_label_training_instance` at 0 rows, so the cited 0.025/0.541
  failure is **not reproducible from prod**; found `agri.species` and `companion_relationships` present with the
  needed columns and empty; found the strategy matviews fabricating coordinates with `random()`. 32 owner
  decisions settled across three rounds.

### 2026-08-15 — data-quality loop scheduled inside the pulse

- `3016dca` (maintenance as a third pulse pass) and `c14e36b` (gap reopening capped at 5 generations,
  `reopen_exhausted`), both verified against prod.

---

## 9. The map UI — measured defects (2026-08-16/17)

Evidence: live `curl` and browser measurement against production plus three read-only opus code traces. Nothing
was run locally, no database was written, no code was changed. **A production environment variable WAS changed —
see D0.**

**Evidence-gathering lesson, binding: `curl` sends no `Origin` header.** Every measurement below was taken without
one, which is why a total CORS outage sat underneath all of it returning clean `200`s. Any future tile diagnosis
must send `-H "Origin: https://plantgeo.aevani.com"` and read the response for `access-control-allow-origin` (§5
for the exact command shape). A green `curl` is not evidence that a browser can load the tile.

### D0 CONFIRMED — Martin CORS blocked every dynamic layer (ROOT CAUSE, FIXED 2026-08-17)

**This dominates everything else in this section and is listed first for that reason.** Full detail, IDs and
verification in §2. In short:

- Prod Martin allow-listed `https://plantgeo-main-production.up.railway.app`; the browser origin is
  `https://plantgeo.aevani.com`. `infra/martin/martin.yaml:7-10` interpolates one value into `cors.origin`.
- Martin returned `200 OK` with `vary: … Origin …` and **no `Access-Control-Allow-Origin` header**; `OPTIONS`
  preflight returned **400**. The browser blocked the response.
- `src/lib/map/sources.ts:28-32` composes all six sources into one request, so the single failure **failed the whole
  TileJSON and blanked every dynamic layer at once**.
- The shipped error message blames an expired Protomaps pin. **It is wrong.** PMTiles
  (`https://tiles.aevani.com/pnw-2026-08-02.pmtiles`, `206`, `ACAO: *`, 1,411,574,646 bytes) and AWS terrain
  (`ACAO: *`) were both healthy. **Only Martin was broken.**
- **FIXED:** `TILE_CORS_ORIGIN=https://plantgeo.aevani.com` on `plantgeo-martin`. Verified — header present, console
  error gone, map renders in a real visible browser. **Caveat:** the raw Railway domain is now the blocked one
  (§2, §7 step 2d).

**D0 → D1 linkage, and it reframes D1.** D1 is gated on `map.isStyleLoaded()`, which stays false while any source is
unhealthy. **This CORS outage WAS exactly that unhealthy source, and it was permanent.** So **D1 was firing 100% of
the time in production** — the date filter was **NEVER** applied to any tile-baked layer, not merely "when a source
is slow". **D1's code defect is still real and still needs fixing**: any future slow or failing source re-triggers
it. The CORS fix removed the trigger that was present; it did not remove the fragility.

### Live measurements

**Martin tile latency, per source, cold, PNW.** Composite is
`fire_risk_tiles,sensor_tiles,evacuation_zone_tiles,burn_severity_tiles,intervention_tiles,watershed_tiles`.

| source | HTTP | bytes | wall time |
|---|---|---|---|
| `fire_risk_tiles` | 200 | 26,765 | **117.2 s** |
| `sensor_tiles` | 200 | **2,155,849** | **23.8 s** |
| `burn_severity_tiles` | 200 | 87,115 | 6.8 s |
| `watershed_tiles` | 200 | 26,522 | 1.2 s |
| `evacuation_zone_tiles` | 200 | 22,070 | 1.0 s |
| `intervention_tiles` | 204 | 0 | 0.34 s |

`fire_risk_tiles` returns **26 KB after 117 seconds** — the worst work-to-output ratio in the system, and it is
the `fire-perimeters` layer, **not** fire-detections.

**Composite tile latency and payload by zoom** — this is the request the browser actually issues, one composite
URL per tile:

| z/x/y | bytes | wall time |
|---|---|---|
| 5/5/11 | **10,751,237** (10.3 MB) | 23.6 s |
| 6/11/22 | 3,353,118 | **84.4 s** |
| 6/10/23 | 2,893,572 | 19.9 s |
| 6/11/23 | 2,318,321 | 1.6 s (warm — Martin cache primed by the per-source run) |
| 7/22/46 | 1,173,172 | 3.3 s |
| 8/44/92 | 290,622 | 2.9 s |

**Two independent problems; do not conflate them.** *Latency* is 20–120 s per cold tile. *Payload* is a **10.3 MB
single vector tile at z5** and 2–3 MB at z6 — and z5/z6 is where the map opens. Even at zero latency the payload
is a client RAM and decode problem. Warm repeats of 6/11/23 ran 1.6 s then 2.1 s, so Martin's 128 MB /
`tile_expiry: 5m` cache does work — it simply cannot hold a working set of multi-megabyte tiles, so nearly every
pan is cold again.

**Tile responses carry NO `Cache-Control`.** Headers on the composite are `HTTP/1.1 200 OK` + `etag:` only — no
`Cache-Control`, no `Age`. Browser and CDN fall back to heuristic caching, so a returning session re-pays the
multi-MB cold cost. `infra/nginx/nginx.conf` would have set one, but that proxy is commented out of
`docker-compose.yml` and Railway serves Martin directly.

**Live per-layer staleness** — `environmental.getSliderCapabilities` against prod, fetched in 0.47 s (server memo
warm). `serverCurrentDate: 2026-08-17`, `streamsUnavailable: false`, 24 layer entries, `streams: []`.

| lag (days) | layer | temporalKind | earliest | latest |
|---|---|---|---|---|
| — | `soil-survey` | snapshot | null | **null** |
| — | `interventions` | snapshot | null | **null** |
| 2461 | `watersheds` | snapshot | 2019-11-21 | 2019-11-21 |
| 725 | `burn-severity` | event | 2024-08-22 | 2024-08-22 |
| **78** | `climate-field-shortwave-radiation` | daily_series | 2022-04-30 | 2026-05-31 |
| **15** | `soil-field-vpd` / `-temperature` / `-moisture` | daily_series | 2022-04-30 | 2026-08-02 |
| **11** | `climate-field-*` (wind-speed, soil-wetness ×3, relative-humidity, precipitation, dew-point, air-temperature) | daily_series | 2022-04-30 / 2022-08-05 | 2026-08-06 |
| 6 | `vegetation` | daily_series | 2022-08-05 | 2026-08-11 |
| 2 | `evacuation-zones` | snapshot | 2026-06-16 | 2026-08-15 |
| 1 | `weather-observations`, `water-gauges`, `sensors`, `fire-perimeters`, `fire-detections`, `drought-areas` | — | — | 2026-08-16 |

- The 1-day group is **healthy** — yesterday's data on a UTC server day.
- `watersheds` and `burn-severity` are `snapshot`/`event`; their old dates are **correct**, not stale.
- The **11–15 day** group (3 soil-field + 8 climate-field = **11 layers**) is the real staleness the owner sees:
  `daily_series` layers whose newest day is nearly two weeks old.
- `climate-field-shortwave-radiation` at **78 days** is the known governed dead layer (§5) — correct *emptiness*,
  but it still publishes a slider whose track ends 78 days ago with no indication why.
- `soil-survey` and `interventions` report `latestObservedDate: null`.

**App-shell latency, prod.** `/api/ready` 0.43 s · `/api/health` 0.36 s · navigation TTFB 217 ms, DCL 967 ms,
load 1697 ms · `/api/auth/session` **1518 ms** · first tRPC batch **1130 ms**. **The shell is fine; the map is
not.**

### Time-slider defects

Path: `LayerTimeSlider` → `useTimeSliderStore.setLayerDate` (clamped) → `layerDates[toggleId]` (sparse; absent =
follow that layer's `latestObservedDate`) → two consumers: (a) `useDebouncedLayerDay` (250 ms) → tRPC keys;
(b) `LayerManager.applyDateFilter` → `map.setFilter(id, ["<=", ["get","observed_day"], day])`.

**The date never reaches a Martin URL, by design.** `src/lib/map/sources.ts:75-86` builds
`${baseUrl}/${sourceIds.join(",")}` — no date, no query string. Tiles are identical for every date; the day is a
baked MVT attribute (`drizzle/0015_tile_observation_day.sql`) filtered client-side
(`src/lib/map/tile-layer-date-filter.ts:11-14`, "dragging across 1,400 days issues zero requests"). Correct
design — **conditional on the filter always being applied**, which is where D1 fails.

- **D1 CONFIRMED (highest value).** `src/components/map/LayerManager.tsx:583-586` gates `applyDateFilter` on
  `map.isStyleLoaded()`. The same file at `:540-549` documents why that gate is wrong — `isStyleLoaded()` requires
  *every* source's tiles to be in, so one unhealthy source holds it false indefinitely ("that is the outage of
  2026-08-15") — and states the visibility applier was moved onto `styledata` for exactly that reason, while
  `:548-549` explicitly excludes opacity and the date filter from that recovery path. Consequence: while any
  source is slow or 404ing, the only filter ever applied is the one written once in `onStyleLoad` (`:533`),
  typically `null`. **Symptom: dragging the slider changes nothing; the map draws the whole multi-year record
  under a row whose slider reads one day. Silent — no error, no caption.** Given the measurements above (a source
  routinely taking 84–117 s), `isStyleLoaded()` is false for minutes at a time on every session, so **D1 fires
  constantly.** **Stronger, per D0: until the 2026-08-17 CORS fix the Martin composite never loaded at all, so
  `isStyleLoaded()` was PERMANENTLY false and D1 fired 100% of the time in production — the date filter was never
  applied to any tile-baked layer.** The fix removes that standing trigger, not the defect. Fix: put
  `applyDateFilter` on the same `styledata` convergence path `applyVisibility` uses, throttled.
- **D2 CONFIRMED (fragility).** `tile-layer-date-filter.ts:44-53` passes any feature lacking `observed_day`.
  Correct per-row, but a whole layer whose tile function predates 0015 — or a Martin process not restarted after a
  tile migration (§5) — passes everything at every date, indistinguishable from D1 and from "no history".
  Nothing logs.
- **D3 CONFIRMED.** `src/hooks/useFireData.ts:71-92` — on 304 or on error, `data` is left untouched, and it is
  never reset when `date` changes (deps `[enabled, date, fetchFires]`, `:115`). Ordering *is* correctly guarded
  (monotonic `latestRequestRef` + `AbortController`, `:40-53`, `:87`), so this is unlabelled retention, **not** a
  race. Symptom: scrub to a past date; if that request fails, the previous day's detections stay painted while
  every caption says the new date.
- **D4 CONFIRMED.** `src/components/map/MapDateSummary.tsx:107,201` renders `layer.date` — the slider position,
  never the drawn collection's actual observed day. `src/stores/useMetricAtDate.ts:118-142` states the opposite
  rule ("consumers that assert a date to the user must read `resolvedDate`"); **no consumer does.** This is what
  makes staleness read as a data bug rather than a loading state.
- **D5 CONFIRMED.** The whole debounce / keep-previous / `resolvedDate` / prefetch layer is **dead code** — only
  `SCRUB_SETTLE_MS` is imported from `useMetricAtDate.ts`. Every live layer uses raw `trpc.*.useQuery` with **no
  `placeholderData: keepPreviousData`** (`LayerManager.tsx:230,239,243,256,334`;
  `useViewportProxiedLayers.ts:150,197,240`), each falling back to `EMPTY_FEATURE_COLLECTION` while pending.
  Symptom: **every date change and every pan drops the layer to zero features for a full round trip, then
  refills** — the blank-and-refill `useMetricAtDate.ts:196-199` was written to prevent. Reads to the user as both
  latency and staleness.
- **D6 CONFIRMED (latency).** `useViewportProxiedLayers.ts:77-95` + `viewport-bbox.ts:29` serialize bbox at **6
  decimal places** and pass raw float `zoom` into ~16 tRPC keys. A pan of ~10 cm mints a cold key for streamflow,
  groundwater, vegetation, weather, 3 soil fields, soil survey and 9 climate signals at once — each then blanks
  per D5. Mitigated only by `moveend` publishing (`MapView.tsx:147`), so it is per-gesture, not per-frame.
- **D7 CONFIRMED.** `layer-toggle-context.ts:320` sends `requestDate: undefined` at server-today, and
  `src/app/api/fires/route.ts:23,59` treats omitted as **the live FIRMS lookback window**, not today. Symptom:
  stepping the fire slider one day back collapses a multi-day window to one day — a large abrupt drop that looks
  like a data gap, at exactly the day users check first. Sound for warehouse readers where omitted == today;
  **unsound for `/api/fires`.**
- **D8 CONFIRMED.** Two 250 ms settle timers armed by different comparisons (`layer-toggle-context.ts:211-231`
  vs `:249-274`); the doc at `:203-210` admits one may lag a settle window. With D4, the caption visibly trails
  its layer during a drag.
- **D9 CONFIRMED (design cost).** `TimeSliderCapabilitiesLoader.tsx:8,71-76` polls every 5 min; `setCapabilities`
  re-clamps overrides (`time-slider-store.ts:472-479`) and re-resolves the sparse default to a possibly-new
  `latestObservedDate` (`:311-321`). Symptom: **the map appears to reload and jump a day on its own, mid-session,
  with no user action.**
- **D10 LOW.** `LayerManager.tsx:204-205` comment claims burn-severity gets no slider;
  `environmental-read-model.ts:2916` now sets `"burn-severity": "event"`, so it does. Misleads anyone debugging D1.
- **D11 CONFIRMED, NEW 2026-08-17, UNOWNED — the real cause of the fire layer's "abrupt drop".** Surfaced by the
  fires lane while fixing D7, and it **outlives that fix**. `/api/fires` resolves two genuinely different query
  *shapes*, not two date arguments: `{kind:"live"}` is a **rolling multi-day `createdAt` window** via
  `firmsDayRange()`, while `{kind:"historical"}` is a **strict single-day `observed_day` filter**
  (`resolveRequestedObservationDay` / `getPublishedFireDetections`, `environmental-read-model.ts:175-192`).
  Stepping the slider one day back therefore does not move a window — it **changes the kind of question being
  asked**, collapsing a multi-day window to one day. D7's fix canonicalised the *cache contract* around this and
  closed a latent CDN mismatch; it **cannot touch the shape asymmetry**, which lives in a file no lane owned.
  This is the defect actually responsible for the user-visible cliff at the day users check first.
- **D12 CONFIRMED and FIXED 2026-08-17 (found while fixing D3).** `useFireData`'s `etagCacheRef` cached only ETag
  *strings*. A 304 asserts the server's copy of **that date** is unchanged — it says nothing about what is
  currently painted in `data` if the slider visited another day and came back. So a 304 could repaint the
  **wrong day's** content. Left unfixed, D3's new staleness label would have been truthful-sounding on data that
  was still wrong — a worse failure than the silent retention it replaced. Fixed by caching the payload beside
  its ETag (`CachedFireResponse` / `responseCacheRef`, bounded at 60 with the `upsertBoundedFeature` LRU idiom).

### Cache and staleness defects

**Cache inventory, database → pixels:**

| # | layer | caches | TTL / invalidation | stale? | symptom |
|---|---|---|---|---|---|
| 1 | **IndexedDB persisted query cache** `plantgeo-query-cache` (`query-persister.ts:303`) | whole tRPC payloads for 9 allowlisted layer reads (`:68-81`), keyed by `queryHash` (path+bbox+date+depth+zoom) | **30 days** if `input.date < serverCurrentDate`, else 5 min (`:120-127`); LRU 50 MB (`:43`); or bump `CACHE_SCHEMA_VERSION` (`:34`) | **YES, 30 d, survives reload** | old value returns after a hard refresh |
| 2 | TanStack Query in-memory (`providers.tsx:12-22`) | everything | `staleTime` 60 s; `refetchOnWindowFocus:false`; per-query 24 h soil-survey, 1 h watersheds/soil-field/climate-field/vegetation/service-area, 15 min water/weather, 5 min metric | yes, min→24 h | toggling a panel off/on shows the old answer |
| 3 | server in-process capability memos (`environmental-read-model.ts:3701,3719,3751-3810`) | slider capabilities, `latestObservedDate` | 5 min feature / 30 min SWR signal; per-process, so N replicas disagree | yes, +30 min | a fresh day isn't offered for ~35 min |
| 4 | Redis GeoJSON (`redis.ts:110-133`) | `drought:current` 6 h, `drought:date:{date}` 24 h, `watersheds:{bbox}` 1 h, `mtbs:{bbox}:{y1}:{y2}` 24 h | `setex` only; no versioning, no purge verb | yes, 1–24 h | stale watershed/MTBS geometry |
| 5 | Next.js fetch cache (`bounded-upstream.ts:24-29`) | upstream providers | FIRMS 3600 s, hydrosheds 3600 s, MTBS 86400 s, LandFire 86400 s, GIBS 7 d/6 h | yes; **stacks under Redis** (watersheds 1 h + 1 h) | fires up to 1 h old before any other cache |
| 6 | HTTP/CDN (`api/fires/route.ts:16-18`, vegetation tile `:109`, mapillary `:89`) | public GETs | fires live `max-age=30,s-maxage=300,swr=600`; **fires historical `max-age=3600,s-maxage=86400,swr=86400`**; tRPC is POST → not CDN-cacheable | yes, **up to 48 h** on a past fire day | past fire day frozen at CDN |
| 7 | Martin tile cache (`martin.yaml:12-16`) | MVTs ≤ z14, 128 MB, `tile_expiry: 5m` | — | yes, 5 min + MapLibre session cache | the latency/payload tables above |
| 8 | **materialized views** (0029, 0023) | pre-aggregated census + rollups | `matview-refresh` pulse vs `agri.matview_refresh_state`; **4 fail every pulse**, 1 never called | **YES, unbounded** | see C3 |
| 9 | SoilGrids DB cell cache (`soilgrids.ts:62,252`) | per 0.001° cell | 90 d + deliberate serve-expired | by design (static ISRIC release) | negligible |
| 10 | nginx tile proxy (`nginx.conf:16-32`) | `/tiles/` | `proxy_cache_valid 200 1d` | **INACTIVE** (commented out of `docker-compose.yml:92-101`) | none today |

- **C1 CONFIRMED — every layer's default view gets the 30-day persisted TTL, not the 5-minute one.**
  `resolveCacheTtlMs` gives 30 days to any `date < serverCurrentDate` (`query-persister.ts:126`). But **no layer
  defaults to today** — each opens on its own `latestObservedDate` (`time-slider-store.ts:318-319`;
  `layer-toggle-context.ts:163-171`, comment `:295` "which for most layers is not today"). So the day the user
  sees on load is *always* past for vegetation, soil-field, climate-field, streamflow, groundwater and drought —
  written to IndexedDB with a **30-day** expiry and re-served for a month. **The staleness table above measures
  exactly this:** 11 layers open on a day 11–15 days old, so all 11 are pinned client-side for 30 days.
  The TTL rests on a premise that is **false for this warehouse**: `src/lib/cache/AGENTS.md:48-51` asserts "once a
  day is in the past, the warehouse's observations for it are immutable". The agri pipeline explicitly rewrites
  historical days — gap reopening (`sql/ingest/reopen_gap_windows.sql`), the 5-generation cap (`c14e36b`), and the
  open USDM/ERA5 self-healing lanes. **This is the #1 explanation for "comes back after a reload as an old
  value."**
- **C2 CONFIRMED — SWR revalidation writes IndexedDB but never the react-query cache.** `revalidateAgainstDW`
  (`query-persister.ts:251-293`) persists the fresh payload and returns it into a **floating promise**; the
  persister already returned `stored.value` at `:320` and nothing calls `queryClient.setQueryData`. Its own
  docstring at `:248-249` ("IndexedDB & react-query cache are updated") is **false**. Consequence: every
  allowlisted layer renders **exactly one fetch behind, permanently** — correct data appears only on the *next*
  mount of that exact key. Matches "doesn't update after changing controls; right on the second look." Secondary:
  revalidation fires on every fresh hit (`:319`), so the long TTL buys no network saving — only display latency.
  **Cheapest high-value fix in the whole pass: one `setQueryData` call.**
- **C3 CONFIRMED — failing / never-refreshed matviews, mapped to the surfaces they freeze.** The four failures
  are already recorded in §2; this is what each one costs the user:

  | matview | status | reader | frozen surface |
  |---|---|---|---|
  | `geo.mv_feature_observation_day` | **302 s vs 300 s cap — fails every pulse** | `environmental-read-model.ts:3386` (via `geo.v_observation_day_census`, `surface_kind='feature'`), `:1420`, `:4137` | **the time slider's available days + `latestObservedDate` for every `geo.features`-backed layer** (vegetation, fires, sensors, interventions, evacuation zones, burn severity) → the map opens on a frozen day and **cannot select newer ones**; also the vegetation "newest observation" caption and `getMetricAtDate`'s unlinked-count summary |
  | `geo.mv_signal_observation_day` | **301 s vs 300 s cap — fails every pulse** | `environmental-read-model.ts:3462-3466` (`surface_kind IN ('signal','polygon')`) | **slider axis for the 12 signal streams** — soil-moisture/soil-temperature field, climate field, streamflow, groundwater — plus `drought-areas` |
  | `geo.mv_soil_survey_grid` | fails, 66 s | **none** (`usda-soil.ts:1047,1148` not repointed) | no user-visible staleness; cost + red pulse only |
  | `geo.mv_soil_survey_union` | fails, 133 s — GEOS bug, `0029:918` omits `ST_CollectionExtract(...,3)` | **none** | no user-visible staleness |
  | `geo.watershed_rollup` | `geo.refresh_watershed_rollup()` called by **nothing** | `geo.watershed_tiles()` at `0023:201`, taken when `z<10` (`:149-155`) | **watersheds at z<10** draw an `observed_day` frozen at build time; **crossing z10 visibly changes vintage** |

  Net: the two census failures mean **no newly-ingested day is selectable in any slider**, which then feeds C1
  (frozen past `latestObservedDate` → 30-day client cache). **This is the causal spine linking the matview lane to
  the owner's staleness reports, and the live per-layer table above is its fingerprint.**
- **C4 CONFIRMED — Martin layers have no date dimension in the cache key because they have none in the URL.**
  Corroborates D1: nothing calls `setTiles` for them (only `VegetationLayer.tsx:364,375` and `SoilLayer.tsx:160`
  do, for rasters). Moving the slider **cannot** change a Martin tile; `tile_expiry` + MapLibre's session cache
  pin whatever drew first.
- **C5 CONFIRMED — `/api/fires` ETag is a feature *count*, not a content hash.** `api/fires/route.ts:60-61`:
  `W/"fire-${date ?? "live"}-${data.features.length}"`. Any corrective re-ingest preserving row count (geometry
  fix, confidence re-grade, replaced FIRMS batch) returns **304 forever**. With `HISTORICAL_DAY_CACHE_CONTROL`
  (`:18`) a past fire day is up to **48 h stale at the CDN**, with a 304 handshake that never breaks the tie.
- **C6 CONFIRMED — cache keys missing a user-changeable dimension.** `drought:current` (`services/drought.ts:4`)
  keys on **nothing** — no date, no bbox, 6 h; currently unreferenced (the router uses
  `getPublishedDroughtClassification`, `routers/environmental.ts:297-308`), so a **dormant landmine**.
  `watersheds:{bbox}` (`services/hydrosheds.ts:45`) has **no zoom**; safe only because `getWatersheds` is
  bbox-only today (`routers/environmental.ts:315`) — adding a zoom arg without touching the key immediately serves
  the wrong tier. **No Redis key carries a schema/version prefix**, so a response-shape change is served in the old
  shape for a full TTL after deploy.
- **C7 CONFIRMED — stream capabilities are SWR with no staleness ceiling.** `readStreamCapabilities`
  (`environmental-read-model.ts:3789-3810`) serves the cached value while a refresh runs and replaces it only on
  success. With `mv_signal_observation_day` failing every pulse, the 30-minute TTL (`:3719`) is irrelevant —
  refreshed on schedule with permanently frozen content. The comment at `:3717-3719` justifies 30 min by the
  "6 h min / 24 h max staleness in `agri.matview_refresh_state`" — **exactly the assumption the failing lane
  breaks.**
- **C8 PLAUSIBLE, dormant — nginx tile TTL overrides are dead regexes.** `nginx.conf:18` is
  `~^\tiles\(?:pmtiles|fonts|sprites)` — literal backslash, no leading `/`, can never match; `:19`'s
  `~^/tiles/geo\.` cannot match either (Martin source ids are bare, `martin.yaml:25-26`). Everything would fall to
  `default 86400` — 24 h browser cache on *dynamic* MVTs. Not active; a trap for whoever re-enables it.
- **C9 NOTE — `serverCurrentDate` is NOT compromised.** Derived from the server clock
  (`environmental-read-model.ts:3118`) and stamped fresh on every capabilities call outside the memo
  (`:3879-3883`). The frozen census does not corrupt "today"; C1 is reached through `latestObservedDate`, not
  through a bad today.

### Why the map feels stale, slow and wrong

Four independent causes, **each sufficient on its own** — which is why single fixes have not helped. **Above all
four sat D0: the Martin CORS block, which made every dynamic layer blank outright until it was fixed 2026-08-17.**

1. **The census matviews that gate the sliders fail every pulse** (C3) → no new day is selectable → every layer
   opens on a past day → **the 30-day IndexedDB TTL pins it for a month** (C1). Live fingerprint: 11 layers
   11–15 days stale.
2. **The persisted-cache revalidation never reaches react-query** (C2) → every allowlisted layer is permanently
   one fetch behind. One-line fix.
3. **The date filter is gated behind `isStyleLoaded()`** (D1) → and because a tile source routinely takes
   84–117 s, `isStyleLoaded()` is false for minutes on every session → **the slider silently does nothing** for
   the tile-baked layers. **Under D0 it was worse: the composite never loaded, so this fired continuously.**
4. **The tile path scans a 7.2 GB table through a shared 1.3M-entry GiST tree and serializes unsimplified
   geometry** → 117 s tiles and 10.3 MB payloads → pool saturation, blank map, and the RAM pressure the owner
   named. **§10 is the structural answer.**

D4 (captions assert the slider's day, not the drawn day) and D5 (no `keepPreviousData`, blank-and-refill) are what
turn all of the above from "slow" into "wrong and untrustworthy".

**Cheapest-first fix order:**

1. **C2** — one `setQueryData` in `revalidateAgainstDW`. Minutes.
2. **D1** — move `applyDateFilter` onto the `styledata` convergence path, throttled. Small.
3. **D5** — add `placeholderData: keepPreviousData` to the layer queries. Small, kills the flicker.
4. **D4** — caption from `resolvedDate`, not the slider day. Small, restores trust.
5. **C1** — TTL policy relative to `latestObservedDate`, or a `dataRevision` gate. Design needed.
6. **C3** — the two census matviews (index or re-grain so they finish under the 300 s cap).
7. **D6** — quantize bbox/zoom in the tRPC keys.
8. **C5** — content-hash the fires ETag.
9. **§10** — the join-free relation layer. Largest, and the only one that fixes the latency/payload/RAM axis.

### Correct behaviour that must NOT be "fixed"

1. `sensors`, `evacuation-zones`, `interventions`, `watersheds` draw their whole record at every date and have no
   slider — declared `snapshot` (`environmental-read-model.ts:2917-2922`); `LayerManager.tsx:423-428` passes
   `null`, which *clears* the filter. Reasoned at `:201-210`: filtering them on `latestObservedDate` was erasing
   sensors observed on a partially-ingested live-edge day.
2. `soil-survey` likewise takes `DEFAULT_TEMPORAL_KIND = "snapshot"` (`environmental-read-model.ts:2944`) —
   consistent, though by table omission rather than decision.
3. Vegetation's NDVI raster does not change when scrubbing inside a month — GIBS is a monthly composite
   (`layer-toggle-context.ts:530-531`, `VegetationLayer.tsx:348,362-375,407`). The vegetation GeoJSON overlay
   *does* move per day. **Half the layer changing is correct.**
4. `soil` never draws — permanently withheld with a stated reason. (The trace cites
   `layer-registry.ts:295-296`; §5's trap entry cites `:289-290` for the same block — line drift, same code, and
   §5's separate finding that the `permanentlyUnavailableReason` is **stale and wrong** still stands.)
5. `demand-heatmap` / `strategy-recommendations` have no time control by declaration
   (`layer-registry.ts:390,423`).
6. **The MapLibre v5 `setStyle` gotcha is NOT in this path.** `setStyle` is called only on basemap change
   (`MapView.tsx:217-241`) with `once("style.load", …)` registered *before* it (`:233-234`), exactly as the gotcha
   requires. No date change calls `setStyle`; no layer remounts on a date change.
7. **Out-of-order response clobbering is structurally absent.** React-query keys by date and cannot write a stale
   key into a fresh one; the one hand-rolled fetch carries a monotonic id and an `AbortController`. The staleness
   is D3/D4/D5, **not** a race.
8. **"Most layers empty before 2026-08-02" is NO LONGER a valid excuse for emptiness.** Since per-layer dates,
   each row opens on its own `latestObservedDate` (`time-slider-store.ts:311-321`) and `setLayerDate` clamps to
   that layer's own `firstDay` (`:434-448`) — a layer *cannot be scrubbed* before its own record starts. A layer
   rendering empty inside its own drawn track is a **real defect**. **This supersedes the
   `plantgeo-layer-history-depth` caveat as a triage shortcut.**

Also not a defect: a blank map in a non-active Chrome tab is the rAF-suspension harness artefact (§5), and
`document.hasFocus()` is **not** sufficient to rule it out.

---

## 10. Join-free tile serving relations (PROPOSAL, not approved)

**Nothing here is implemented and nothing here is approved.** This answers the owner's directive — *"there is a
fundamental issue with RAM — we need to be more able to easily and effectively query pre-aggregated views,
returning everything we need with a simple query, with no joins if possible"* — as a design for review.

**Blocked on three owner decisions** (see the ordered path below): **(a)** keep or skip `drizzle/0030`;
**(b)** the fire-detail time cap `N`, or "no cap"; **(c)** whether `tile_interventions` gains a day column — it
has none today, and adding one changes what the slider does to that layer.

### Current shape

Seven live `geo.*_tiles()` functions (latest `CREATE OR REPLACE` wins):

| function | authoritative file:line | source | join | layer resolved by | day emitted |
|---|---|---|---|---|---|
| `fire_risk_tiles` | `0015_tile_observation_day.sql:159` | `geo.features` | `JOIN geo.layers` | `l.name='fire-perimeters'` | yes |
| `sensor_tiles` | `0015:198` | `geo.features` | `JOIN geo.layers` | `l.name='sensors'` | yes |
| `evacuation_zone_tiles` | `0015:116` | `geo.features` | `JOIN geo.layers` | `l.name='evacuation-zones'` | yes |
| `burn_severity_tiles` | `0015:63` | `geo.features` | `JOIN geo.layers` | `l.name='burn-severity'` | yes |
| `intervention_tiles` | `0005_intervention_priority_tiles.sql:6` | `geo.features` | `JOIN geo.layers` | `l.name='interventions'` | **no** |
| `watershed_tiles` | `0023:131` | `geo.features` (z≥10) / `geo.watershed_rollup` (z<10) | join **only on z≥10** | `l.name='watersheds'` | yes |
| `strategy_recommendations_tiles` | `0028:360` | 3 matviews by zoom | **none** | n/a | no |
| `building_tiles` | `0001:485` | `geo.osm_buildings` | none | n/a | no |

The five joined functions are byte-identical in shape (canonical `0015:101-108`): `FROM geo.features f JOIN
geo.layers l ON f.layer_id = l.id WHERE l.name = '<layer>' AND l.is_public IS TRUE AND f.status = 'published' AND
f.geom IS NOT NULL AND f.geom && bounds_4326 AND ST_Intersects(f.geom, bounds_4326)`. Each then does **7–10
`f.properties ->> '<key>'` jsonb extractions per row** against a 1,467 MB TOAST relation (`0015:83-100`), plus a
per-row `ST_Transform(f.geom, 3857)` (`0015:82`) while bounds are transformed the other way (`0015:76`) — **two
reprojections per tile row, both avoidable.**

**Fire-detections is not a tile function at all** — `layer-registry.ts:178-187` marks it `renderKind:"component"`,
served as GeoJSON by `environmental-read-model.ts:317-339`. Its 3,009,567 rows harm the tile path *indirectly*, by
owning ~1.3M entries in the shared `idx_features_geom` tree every tile function's `&&` leg must walk.

### The join claim — proved, with one important correction

`geo.layers` (`0000_narrow_tony_stark.sql:133-147`) could supply `name`, `is_public`, `style`, `min_zoom`,
`max_zoom`, `sort_order`. Grep across all seven bodies: **zero references to `l.style`, `l.min_zoom`, `l.max_zoom`,
`l.type`, `l.sort_order`.** Zoom tiering is hardcoded in the bodies (`0023:149-155`, `0028:376,393`), not read from
`layers`. So the join carries exactly **name→id resolution plus a publication boolean** — two constants a per-layer
relation eliminates by construction. This does **not** revive per-layer partial indexes: §3's rejection stands, and
for the same reason (only `l.name` is constant; equivalence classes propagate `f.layer_id = l.id` but never the
restriction on `l.name`).

**Correction that matters: the join is NOT the reason the query is slow.** The layer leg is 0.8 ms for all 541 rows
(`0030` header EXPLAIN). The 45.6 s is the **global geometry leg**. Removing the join is necessary for the relation
design but is not itself the win — **the win is that a per-layer relation's GiST tree contains only that layer's
geometries**, shrinking the expensive leg by 3–4 orders of magnitude.

### Proposed relations

Grain rule: **one row per feature per observation day, geometry pre-projected to 3857, every wire attribute a
native typed column, zero jsonb, zero join.** Schema `geo`, prefix `tile_` to distinguish from 0029's analytics
`mv_`.

| relation | grain | rows | extra columns (all carry `geom_3857`, `observed_day`) | est. size | day? |
|---|---|---|---|---|---|
| `geo.tile_sensors` | 1/feature | 149,466 | `id`, `network`, `sensor_id`, `station_name`, `observed_at` | 25–45 MB | yes |
| `geo.tile_watersheds_detail` | 1/HUC12 | 9,396 | `id`, `huc12`, `huc_level`, `basin_count`, `name`, `areasqkm`, `tohuc`, `states`, `hutype` | 150–400 MB (**unmeasured**) | yes |
| `geo.watershed_rollup` | existing | 2,162 | unchanged | existing | yes |
| `geo.tile_burn_severity` | 1/perimeter | 541 | `id`, `fire_id`, `fire_name`, `fire_year`, `ignition_date`, `fire_type`, `assessment_type`, `acres`, `severity_class`, `observed_at` | 30–120 MB (**unmeasured**) | yes |
| `geo.tile_fire_perimeters` | 1/perimeter | **uncensused** | `id`, `risk_level`, `severity`, `name` | unknown | yes |
| `geo.tile_evacuation_zones` | 1/zone | **uncensused** | `id`, `evacuation_area_name`, `fire_name`, `county`, `severity`, `evacuation_level_label`, `structures_within`, `population_within` | small | yes |
| `geo.tile_interventions` | 1/feature | 2 | `id`, `intervention_type`, `priority`, `status`, `name`, `description` | 16 kB | **no** — emits none today; do not invent one |
| `geo.tile_fire_detections_detail` | 1/detection | 3,009,567 | `id`, `brightness`, `frp`, `confidence`, `acq_time`, `satellite` | ~300 MB + ~200 MB GiST | yes |
| `geo.tile_fire_detections_z9` | 0.005° cell × day | ~1.2–2.4M (**unmeasured**) | `cell_id`, `detection_count`, `max_frp`, `max_confidence` | ~150 MB | yes |
| `geo.tile_fire_detections_z6` | 0.02° cell × day | ~0.4–1.0M | same | ~60 MB | yes |
| `geo.tile_fire_detections_z0` | 0.1° cell × day | ~0.15–0.5M | same | ~30 MB | yes |

**Type: materialized view** — the only shape that plugs into the existing refresh machinery with no new writer
(0029's header argues the case at `:26-37`). Not partitioned: everything except the fire tiers is under 150 K rows,
and the fire tiers are already partitioned *by zoom*, which is the access pattern.

**Exactly two indexes each:** `CREATE UNIQUE INDEX uq_tile_<layer> ... (id)` (required by `REFRESH CONCURRENTLY`)
and `CREATE INDEX ix_tile_<layer>_geom ... USING gist (geom_3857)`. Fire tiers key on `(cell_id, observed_day)`.

**The day dimension stays in every relation that emits it today — non-negotiable.**
`tile-layer-date-filter.ts:17-23` names four toggles (`fire-perimeters`, `evacuation-zones`, `burn-severity`,
`sensors`) whose slider *is* a MapLibre expression over the `observed_day` MVT attribute. Drop it and the slider
silently shows everything at every date — the exact regression 0015 fixed. Materialize as
`geo.feature_observation_day(properties)::text`, unchanged, so `observation-day-contract.test.ts` keeps holding.

**Fire-detections has a different day rule that must NOT be unified.** `environmental-read-model.ts:309-315`
states `geo.feature_observation_day` does not know `acqDate` and returns NULL for most detections. Fire relations
carry `COALESCE(substring(properties->>'observedAt',1,10), properties->>'acqDate')::date` — a deliberate second
rule. Fire is also an **event** layer (`environmental-read-model.ts:2905`), so its client semantics are "on that
day", not the "at or before" of `tileLayerDateFilter`; a fire tile function needs its own filter expression.

**Zoom tiering follows 0023's precedent** (detail tier reads the base relation, coarser tiers read a pre-unioned
rollup keyed by a real published hierarchy, `CASE` on `z` at `0023:149-155`, dispatch `:157/:185`):

- *Polygons* — watersheds keep 0023's shape (only the z≥10 branch changes source). Burn-severity and evacuation
  zones get **simplification, not rollup**: a second `geom_3857_z6 = ST_SimplifyPreserveTopology(...)` column
  selected by zoom. There is **no published hierarchy** to roll up by, so unioning would fabricate groupings —
  which 0023 explicitly refused to do.
- *Points (fire-detections)* — rollup by **grid cell × day** (`ST_SnapToGrid` 0.1°/0.02°/0.005°, `count(*)`,
  `max(frp)`). A 0.1° cell at z4 is sub-pixel, so the aggregate is visually lossless. **Keep `observed_day` in
  every tier** — coarsening to month would make the slider a lie.
- The fire **detail** tier is the one relation to cap by time (`WHERE observed_day >= current_date - N`), matching
  the live path which already reads only `firmsDayRange()` (`environmental-read-model.ts:355`). This is a real
  semantic narrowing and **needs owner decision (b) on `N`.**

**This design targets §9's payload problem, not only latency:** `sensor_tiles` alone returns 2.1 MB at z6 today
because 149,466 unclustered points are serialized whole. Simplification and grid rollup are what bring a 10.3 MB z5
tile down to something a browser can decode without stalling.

### Refresh registration — no new mechanism

Register each as one more `MatviewRefreshSpec` in `jobs/matview_refresh.py:220-320`. The lane already opens one
never-rotating run, gates on a watermark, self-heals unpopulated views with a non-concurrent first `REFRESH`, and
writes `agri.matview_refresh_state`.

| relation | watermark | min_interval / max_staleness | priority | stmt timeout |
|---|---|---|---|---|
| `tile_interventions`, `tile_evacuation_zones`, `tile_burn_severity`, `tile_fire_perimeters` | reuse `_WATERMARK_FEATURES_UPDATED_AT` | 900 / 21,600 | 0 | 60 |
| `tile_sensors` | reuse `_WATERMARK_FEATURES_UPDATED_AT` | 900 / 21,600 | 1 | 120 |
| `tile_watersheds_detail` | reuse `_WATERMARK_WATERSHED_FEATURES` | 86,400 / 604,800 | 1 | 300 |
| `tile_fire_detections_*` (4) | **new** `matview_refresh_watermark_fire_detections.sql` | 3,600 / 21,600 | 2 | 600–1,800 |

**Caveats:** adding 11 specs to an 11-spec lane doubles per-tick watermark probes, and the lane is **already
blowing its 300 s cap on two views** (§2). Fire detail belongs at `priority=2` beside `mv_signal_cell_daily` so it
cannot starve the cheap ones. Every relation needs its unique index or `REFRESH CONCURRENTLY` refuses
(`0029:34-37`).

**Free bug fix:** watersheds' `geo.refresh_watershed_rollup()` is called by nothing today (§5, §9 C3); the rollup
is already adopted into this lane, and the new detail relation joins it under the same discipline.

### Expected win, and the honest unknowns

**Structural, knowable now:** every tile query stops touching `geo.features` (7,219 MB), `idx_features_geom`
(310 MB) and the 1,467 MB TOAST relation. The 1,318,892-entry GiST walk **cannot occur** — `tile_burn_severity`'s
tree holds 541 entries, `tile_interventions`' holds 2. The 7–10 jsonb detoasts per row per tile become native
column reads (**the single largest RAM working-set reduction, and a certainty**). Both per-tile reprojections
disappear. Tile-path resident working set drops from "whatever fraction of a 7.2 GB table the envelopes touch" to
**~250–600 MB** for the five style-baked layers, or ~1.0–1.5 GB with the full fire detail tier. On a 3 GB cap that
is the difference between a burst that hits the ceiling and one that does not.

**Latency: single-digit-to-low-hundreds of milliseconds per tile** expected (small GiST descent +
`ST_AsMVTGeom` on ≤10 K features, capped by `max_feature_count: 10000` in `martin.yaml:19`), against the
**117 s / 84 s measured in §9. No tighter number is claimed.**

**Cannot be known without prod EXPLAIN / census** (recorded as a standing assumption in §4): on-disk size of every
polygon relation (vertex counts unmeasured — 150–400 MB and 30–120 MB are order-of-magnitude only); row counts for
`fire-perimeters` and `evacuation-zones` (**never censused**); fire grid-tier row counts (could be 5× either way);
refresh duration per relation, hence whether the lane's budget absorbs 11 more specs; whether the planner actually
picks the new GiST index (it should — small relation, `&&` on the leading and only column — but that is exactly
deferred finding MEDIUM #4, "asserted, not demonstrated").

**Relationship to 0030, stated plainly:** this design makes `ix_features_layer_geom` unnecessary *for the tile
path*, the only consumer with a measured EXPLAIN. It would still help `readFireDetectionsOnDay`'s bbox branch.
Since 0030 is authored, needs no code change, no Martin restart, and 25–60 minutes, **the recommendation is to land
it first as the de-risking fix** (which is what §7 step 2 already says) and treat this design as the structural
replacement — but if both land, 0030's index becomes a 400–600 MB carrying cost with one thin reader. **That is
owner decision (a) and it should be made BEFORE the 25–60 minute build, not after.**

### Migration path and ordering traps

**Load-bearing property: every tile function keeps its exact name, argument list and return type.**
`CREATE OR REPLACE FUNCTION geo.<name>(integer,integer,integer) RETURNS bytea` with a new body is **no catalog
change and no Martin restart** — 0015's header states this at `:7-10` and 0015 shipped on that basis.

1. **Never rename a tile function, never add/remove one in the same change.** `sources.ts:33-40` composes six ids
   into ONE MapLibre source and `:28-32` states a 404 on any member fails the whole TileJSON and **blanks every
   dynamic layer**. A rename is a 404 for the window between SQL landing and Martin restarting.
2. **Never change an MVT tag or attribute name.** `ST_AsMVT(tile,'burn_severity',...)` and `'watersheds'` are
   declared verbatim in `src/lib/map/layers.ts`; `0023:121-123` records a mismatch "renders nothing while
   reporting no error". `observed_day` must keep its name and `::text` YYYY-MM-DD form or
   `tile-layer-date-filter.ts` silently stops filtering (§9 D2).
3. A *new* function is only ever safe in this order: create in SQL → add to `infra/martin/martin.yaml` →
   **redeploy `plantgeo-martin`** (`auto_publish:false`, catalogue read at startup) → confirm it answers → *then*
   add its id to `DYNAMIC_TILE_SOURCE_IDS`. **This design needs none of that.**

**Ordered steps:**

1. **Owner decisions first, before any DDL:** (a) keep or skip 0030; (b) the fire-detail cap `N`, or "no cap";
   (c) whether `tile_interventions` gains a day column.
2. **Census `fire-perimeters` and `evacuation-zones` and measure real polygon byte sizes**, so the size table
   stops being a guess. Read-only, cheap.
3. **`0031_tile_serving_relations.sql`** — create every matview **`WITH NO DATA`** plus unique + GiST indexes.
   `WITH NO DATA` is mandatory (0029 header `:26-32`): creating with data runs every defining query inside the
   migration's single transaction during `preDeployCommand`, on the box this whole workstream exists to protect.
   **No `CONCURRENTLY` anywhere in the file** (25001 inside the migrator's transaction; `IF NOT EXISTS` does not
   save you). **Never write the drizzle statement-breakpoint literal inside a comment** — see §5; this bit 0029.
4. **Populate out of band**, session-tuned, in the style of `scripts/apply-pre-aggregation.mjs` — but **do not copy
   its `lock_timeout = '5s'`** (§5: that hardcoded value left an INVALID index that had to be dropped and rebuilt
   at 20 min).
5. **Verify populated and non-empty per relation before any function is touched.** A tile function pointed at an
   unpopulated matview returns empty tiles — not an error, a silently blank layer. The known `to_regclass` gap
   applies (§4): existence is not usability, and it false-passes on a matview created `WITH NO DATA`. Check
   `pg_class.relispopulated` **and** a real `count(*)`.
6. **`0032_tile_functions_read_preaggregates.sql`** — `CREATE OR REPLACE` the six bodies, same signatures, same
   MVT tags, same attribute names and order. Apply to prod.
7. **Restart `plantgeo-martin` anyway.** Not strictly required (signatures unchanged), but bodies changed and
   Martin caches tiles for 5 min; a restart is the deterministic way to drop stale bytes, and the standing rule is
   "restart after a tile migration".
8. **Register the specs** in `jobs/matview_refresh.py` + the new fire watermark SQL. Code, not DDL; ships on the
   ordinary deploy.
9. **Only after 0031 and 0032 are both live in prod**, commit the two journal entries **and** the
   `migration-contract.ts` re-pin **in one commit** (§5, "Migration ordering"). Landing that pair early 503s
   `/api/ready` and fails the Railway healthcheck. Note `migration-contract.ts:2` is currently pinned to
   `0029_pre_aggregation_layer` and **0030 is not journalled** — if 0030 lands first that is its own separate
   one-commit pair, ahead of this one.
10. The journal legitimately skips 26 — do not "repair" it. Next free indices: 30 (taken by unapplied 0030), then
    31, 32.

**Rollback:** every step before 6 is purely additive and reversible by dropping the matviews. Step 6 is reversible
by re-applying the 0015/0005/0023 bodies via `CREATE OR REPLACE` — again no rename, again no composite 404.

---

## 11. Offline sync-on-scrub — owner spec, 2026-08-17 (APPROVED, not built)

Owner directive, verbatim: *"it should sync what has been selected previously by the user, by default that's the
most recent day, but when the slider slides there is a debounce and a fetch lag (loading state needed) and then
the data for that day is fetched where it should then be also persisted at which point on top of the time slider
there should be another line that fills in according to what days have been synced to local"* · plus *"It should
rely on what has been locally synced first"* and *"The sync by the user should be rate limited for all the time
streams using a uniform convention for all layers."*

**Read this before designing anything: there is NO bulk "Sync" button.** Sync is a **byproduct of ordinary
scrubbing**. The user browses; each day they land on is fetched, persisted, and then lights up on a second track
above the slider. This is materially simpler than a range-picker or a prefetch-everything button, and it is the
approved shape. Do not reintroduce a bulk-sync affordance.

### 11.1 The loop, end to end

1. Slider scrubs → **debounce** (`SCRUB_SETTLE_MS = 250`, `stores/useMetricAtDate.ts:34`, already shared by
   `layer-toggle-context.ts:223,266` so no two consumers double-fire).
2. Settled day issues the layer's fetch → **a real loading state must be visible for the fetch lag.**
   **This is defect D5 (§9) stated as a feature request.** Today every layer falls back to
   `EMPTY_FEATURE_COLLECTION` while pending, so the layer *blanks and refills* with no indication it is loading.
   `placeholderData: keepPreviousData` plus an explicit pending indicator is the fix; they are one change.
3. Result persists to IndexedDB — **already happens** for the 9 allowlisted tRPC paths via
   `lib/cache/query-persister.ts:303-363`. Nothing new is needed for the *write*; what is missing is the *index*
   (11.2).
4. The **synced-days track** above the slider fills in for that day.

Default day is already correct: `layerDates` is sparse and an absent key means "follow this layer's
`latestObservedDate`" (`time-slider-store.ts:311-321`, `resolveLayerDate`). No change.

### 11.2 The blocker nobody can design around: entries are not enumerable by layer

`StoredLayerQueryEntry` (`query-persister.ts:46-58`) carries
`{key, schemaVersion, createdAt, expiresAt, lastAccessedAt, approxByteSize, value, etag?, dataRevision?}` —
**no `layerId`, no `day`.** The IDB key is the opaque tRPC `queryHash`, which is **not reversible**. And
`indexeddb-store.ts:22-40` opens the store with **no `keyPath` and no `createIndex`** — it is a bare
out-of-line key→value store with no secondary index of any kind.

**Consequence: "which days does layer X have on disk" is unanswerable today.** Both the coloured track and the
per-timeline reset depend on it. This is the first thing to build.

**Shape:** stamp `layerId` (via `routerPathFromQueryKey` → `toggleIdForWarehouseLayerName`,
`layer-registry.ts:480-485`) and the `day` (already in `queryInputRecord(queryKey).date`) onto the entry at
**both** write sites (`:284` revalidation, `:356` cold write), then enumerate with the existing
`getAllEntries` (`indexeddb-store.ts:105-108`). Bump `CACHE_SCHEMA_VERSION` (`:34`) so pre-existing entries
without the stamp are treated as misses rather than as "not synced" lies.

**Trap:** `getAllEntries` is a full scan of a 50 MB store. Do not call it per render. Read it once into a
zustand store on mount and mutate that store on every persister write.

### 11.3 The synced-days track — where it goes

`LayerTimeSlider.tsx` already renders exactly this shape, so **do not invent a new rendering approach.** The
track is a stack of absolutely-positioned `<div>` bands under one transparent `<input type="range">`:

- dense base band `:471-479` · future hatch `:482-493` · **coverage bands `bands.map(...)` `:501-518`** ·
  latest tick `:522-531` · today tick `:533-538` · the range input `:550-565`.
- Bands come from `drawCoverageBands(domain, capability)` (`:253-256`) in
  `layer-panel/layer-coverage-track.ts`; kinds are the `CoverageKind` union (`:39`) styled by
  `TRACK_REGION_APPEARANCE` (`:356-381`).

The owner asked for a **second line on top of** the existing track — so this is a **sibling row**, not a new
`CoverageKind` blended into the existing band run. Render it as its own thin absolutely-positioned strip above
the range input, reusing `percentOfDayOffset` for geometry so the two lines register pixel-exactly.

### 11.4 Per-timeline reset

New control. Home is the controls stack in `LayerRow.tsx:187-202` (which already hosts `LayerOpacitySlider` and
the `LayerTimeSlider` slot), or the slider's own top row `LayerTimeSlider.tsx:404-456` beside the existing
"Latest" button. Both are legitimate; the top row is the tighter fit because that row already carries the date
input, the "NOT LATEST" badge and an action button.

**There is no precedent to copy.** The only existing "clear" affordance is `OfflinePanel.tsx`'s "Clear Cache" →
`clearTileCache()` (`lib/offline/tile-cache.ts:156-164`), which deletes **only** CacheStorage keys prefixed
`plantgeo-` and touches none of the query cache.

### 11.5 The uniform rate limit — a real consolidation, not a wrapper

Owner: *"uniform convention for all layers."* Today the client has **six independent, differently-shaped
request-shaping mechanisms and no shared limiter**:

| mechanism | file:line | governs |
|---|---|---|
| `MAX_CONCURRENT_REVALIDATIONS = 2` hand-rolled semaphore | `query-persister.ts:213`, `:225-244` | SWR revalidation after an IDB hit, allowlisted layers only |
| `SCRUB_SETTLE_MS = 250` | `useMetricAtDate.ts:34` | slider scrub → request |
| `useDebounce(value, 300)` | `hooks/useDebounce.ts:3` | generic, used beyond the slider |
| `PREFETCH_RADIUS_DAYS = 1` | `useMetricAtDate.ts:54` | neighbour-day prefetch volume |
| reconnect backoff 1 s→30 s | `useSSE.ts:16-77`, `useWebSocket.ts:17-86` | reconnect only, not requests |
| MapLibre paint throttle | `ui/layer-opacity-slider.tsx:21` | **render, not network — do not conflate** |

**And two confirmed-unthrottled user-triggered paths:** `useOfflineSync.runSync()`'s plain sequential
`for (const op of ops)` replay (`hooks/useOfflineSync.ts:80`) with zero cap, zero delay, zero backoff; and
`prefetchTiles`' no-service-worker fallback (`lib/offline/tile-cache.ts:80-104`) which fires `fetch()` for every
tile URL in a batch with no concurrency cap.

**Server side there is nothing to lean on.** `trpc/init.ts:18` — `publicProcedure = t.procedure`, **zero
middleware**; the four procedure builders add only auth/role checks. The `environmental`, `wildfire` and
`layers` routers that back every map layer have **no rate limiting at all.** The two Redis limiters that do
exist (`security/public-provider-rate-limit.ts:36-64`, `middleware/api-auth.ts:262-289`) cover only the
anonymous provider proxies and `/api/v1/*` API-key routes, and they have **different failure semantics**
(one fails closed only in prod, the other always). Do not extend either; write one client-side limiter.

**The limiter must cover the fan-out, which is the actual problem.** One settled scrub with the usual layers on
mints ~16 tRPC keys at once — 9 climate signals each with their own `useDebouncedLayerDay` + query
(`ClimateFieldLayers.tsx:40-59`, `:82`, `:86-92`), 3 soil fields, vegetation, weather, streamflow, groundwater,
drought. Combined with D6 (6-decimal bbox in the key, `viewport-bbox.ts:29`) a 10 cm pan mints all of them cold.

**`/api/fires` is structurally outside all of it.** `useFireData.ts:66-69` is a raw `fetch`, not react-query, so
the persister can never see it and it is not covered by the allowlist as designed. It needs explicit handling in
both the sync index and the limiter, or fire silently behaves differently from every other layer.

### 11.6 Satellite first — status corrected

Owner: *"ensure the satellite views are shown first for the visualization."*

- **`HEAD` still opens on `dark`.** `git show HEAD:src/stores/map-store.ts` → `currentStyle: "dark"` at `:81`.
  The working tree changes it to `"satellite"` (`map-store.ts:98`) but **that change has never been committed or
  deployed**, so production has never once opened on satellite. §8's "default basemap is now satellite" describes
  the working tree, not prod.
- The style itself is complete and correct: `satelliteStyle` (`lib/map/styles.ts:271-320`) uses Esri World
  Imagery, `layers[]` ordered `satellite-base` → hillshade → 3D buildings → dynamic layers → labels. Basemap is
  correctly at the bottom; nothing to reorder.
- **The choice does not survive a reload.** `map-store.ts` has **no `persist` middleware** (only `layer-store.ts`
  and `search-store.ts` persist, both to localStorage). Every reload resets to the default, so "shown first"
  requires the committed default *and* — if the user's own pick should stick — persisting `currentStyle`.
- **Standing risk, unowned:** satellite is `server.arcgisonline.com`, per `app/about/page.tsx:270` *"the one tile
  service in the stack we do not host"*. Making it the default makes an unkeyed third-party endpoint load-bearing
  for first paint. Flagged, not blocking.

### 11.7 The service worker is HEALTHY — a claimed defect, REFUTED 2026-08-17

**This section previously claimed the service worker was probably dead. That was WRONG and is corrected here.
Recorded in full because it is a reusable lesson about how the claim was manufactured.**

**The claim:** `public/sw.js:7-10` precaches `APP_SHELL` including `/manifest.webmanifest`; no such file exists
under `public/`; `cache.addAll` (`:39-45`) rejects wholesale on any 404; therefore `install` rejects and the
browser discards the worker.

**Why it was wrong: `src/app/manifest.ts` exists.** It is a Next.js App Router `MetadataRoute.Manifest`
**file-convention route**, which Next serves at exactly `/manifest.webmanifest` and for which it **auto-injects
`<link rel="manifest">`**. A glob of `public/**` was never going to find it. The premise came from enumerating
the filesystem and inferring the served reality — those are not the same thing.

**Measured against production 2026-08-17, in a real browser at `https://plantgeo.aevani.com`:**

- `fetch('/manifest.webmanifest')` → **HTTP 200**.
- `document.querySelector('link[rel="manifest"]').href` → `https://plantgeo.aevani.com/manifest.webmanifest`.
- `navigator.serviceWorker.getRegistration()` → scope `/`, **`active: "activated"`**, installing `null`,
  waiting `null`.
- `navigator.serviceWorker.controller` → **CONTROLLING**.
- `caches.keys()` → `["plantgeo-v2"]` — **the cache exists, so `install` and `cache.addAll` both succeeded.**

Corroborated independently from build artifacts: `.next/server/app/manifest.webmanifest.{body,meta}` exists with
`{"status":200,"content-type":"application/manifest+json"}`, and the same pair exists under
`.next/standalone/...`, which `output: "standalone"` (`next.config.ts:18`) copies into the Railway runtime image.
`APP_SHELL`'s literal `/manifest.webmanifest` matches the served path exactly — no basePath, no trailing-slash
drift, and `src/middleware.ts` matches only `/dashboard` and `/onboarding`.

**Two further claims in the original finding are also false:** the two icons are **not** "referenced by nothing"
(`src/app/manifest.ts:13,15` reference both), and there is **no missing `<link rel="manifest">`** to add to
`layout.tsx` — Next injects it.

**Do NOT create `public/manifest.webmanifest`.** A static file at that path would be served in preference to the
generated route, **silently shadowing** `src/app/manifest.ts` and turning a non-bug into a real one.

**One genuine latent hazard survives, unfixed on purpose.** `cache.addAll(APP_SHELL)` is still all-or-nothing, so
a *future* 404 in `APP_SHELL` would still kill the install. It is **not firing today**. The minimal fix is
per-entry `cache.add(url).catch(...)` or `Promise.allSettled`, which needs **no `CACHE_NAME` bump** (cached
content shape is unchanged) and has no happy-path behaviour change. Deliberately deferred: `public/sw.js` has
**zero test coverage**, and it is live-controlling real production clients. It deserves its own pass with a
harness, not a same-session unverified edit to service-worker lifecycle code.

**The lesson, which is the reusable part:** a filesystem glob is not evidence about what a URL serves, and this
is the second time in two days that a confident code-trace finding died on contact with a real browser (the
first being D0's misleading "expired Protomaps pin" error message). **Framework file-convention routes —
`app/manifest.ts`, `app/robots.ts`, `app/sitemap.ts`, `app/opengraph-image.tsx` — produce URLs that exist in no
directory listing.** Check the served response before claiming an asset is missing.

### 11.8 Five disk stores, no shared registry — scope note for "reset local storage"

1. IndexedDB `plantgeo-query-cache` / `layer-query-entries` — allowlisted layer reads (`lib/cache/*`). **The one
   this feature is about.**
2. IndexedDB `plantgeo-offline` v2 / `sync-queue` + `sync-conflicts` — outbound mutation replay, a **second
   independent wrapper** (`lib/offline/indexed-db.ts`, `keyPath:"id"`, no TTL/LRU/schema-version).
3. CacheStorage `plantgeo-v2` — app shell + static tile hosts, 500 MB cap (`public/sw.js`).
4. localStorage `plantgeo-layer-opacity` (`layer-store.ts:57`).
5. localStorage `plantgeo-search` (`search-store.ts:31`).

**None share a registry, naming convention, version scheme or reset path.** Per-timeline reset targets **#1
only**. Say so in the UI; do not let it read as "clear everything".

**Dead scaffolding, do not mistake for a working path:** `time-slider-store.ts:421`
`hydratePersistedLayerDates` exists and its own doc at `:359-374` states plainly that **nothing persists this
store today**, verified 2026-08-09.

---

## 12. Memory: the verdict, and what "continuous aggregate" can actually mean here

Owner, 2026-08-17: *"still seeing maxed out memory usage it does not matter what I throttle it to it always
expands to the max amount"* · *"the new logic should make these views update with newly fetched data without
recomputing materialized views using the continuous aggregate feature — disk volume size is not an issue, RAM
memory usage is the issue."* Supplied source:
`https://oneuptime.com/blog/post/2026-02-02-timescaledb-high-ingestion/view`, captured in full at
[`.omc/research/timescaledb-high-ingestion-2026-08-17.md`](../.omc/research/timescaledb-high-ingestion-2026-08-17.md).

### 12.1 Why throttling never helps — the gauge is not measuring what it looks like

**The steady 3 GB reading is page cache, counted through the cgroup. It rises to fill whatever limit is set, at
any limit.** That is the complete explanation for "it always expands to the max no matter what I throttle it
to", and it is not a defect. §5 carries six independent proofs. **Lowering the cap lowers the number the gauge
saturates at; it does not reduce pressure.** Stop reading the steady figure as a problem.

Three different things get conflated under "memory is maxed" and only one is worth engineering:

- **(a) steady gauge — page cache.** Cannot fall, is not pressure. Judge changes by idle/burst window sampling,
  never the lifetime figure.
- **(b) base reservation — real but capped and small.** `autovacuum_max_workers = 10` ×
  `maintenance_work_mem = 128 MB` ≈ 1.28 GB, plus TimescaleDB launcher/scheduler workers. **The worker cut is ON
  HOLD** (§3) because stale autoanalyze on `geo.features` was half the planner collapse.
- **(c) burst pressure — the real axis.** A pulse burst read **1,203 MB in a single 15 s interval**. Tile queries
  scan a 7,219 MB table through a **1,318,892-entry** shared GiST tree and detoast 7–10 jsonb keys per row from a
  1,467 MB TOAST relation. **§10 is the structural answer and it is unchanged by anything here.**

### 12.2 The supplied article does not apply, and its numbers are dangerous here

Every figure in it is scaled to a **64 GB dedicated server** — `shared_buffers 16GB`, `effective_cache_size
48GB`, `maintenance_work_mem 2GB`. Prod is a **3 GB-capped** Railway Postgres currently at `shared_buffers
256 MB` / `effective_cache_size 2 GB` / `work_mem 16 MB`. **Do not import these values.** The article also never
diagnoses unbounded memory growth — it has no such section; its memory content is prescriptive sizing, not
pathology. Its one directly applicable knob, `timescaledb.max_background_workers`, points the *opposite* way
here: it says raise it to 8, whereas TimescaleDB on this box is delivering nothing and should be reduced or
dropped.

### 12.2b MEASURED 2026-08-17 — the RAM fault nobody had found, and a livelock

**Everything below is measured against prod (PG 18.4 / PostGIS 3.6.4 / TimescaleDB 2.29.0), read-only.
It supersedes the parts of §12.3–12.4 it contradicts, and those corrections are marked inline.**

**THE FINDING: the refresh lane is failing on `/dev/shm`, which is RAM.** Reproducing
`mv_soil_survey_grid`'s defining query read-only:

```
ERROR after 55.05s: DiskFull: could not resize shared memory segment
"/PostgreSQL.667954444" to 16777216 bytes: No space left on device
```

**Control, same query, `max_parallel_workers_per_gather = 0`: no failure**, ran past 280 s.
`dynamic_shared_memory_type = posix`, so parallel-query DSM segments are files in **`/dev/shm` — tmpfs, i.e.
RAM, counted inside the 3 GB cgroup cap.** This is parallel-query shared-memory exhaustion. It is **not** a
statement timeout (the cap is 300 s; it died at 55 s) and **not** the GEOS bug. **Both census plans use
`Gather Merge`, so both are one allocation away from the same fault.** This is a genuine, previously
undiagnosed mechanism by which this workload consumes RAM — and it is directly actionable.

**THE SECOND FINDING: a permanent retry livelock.** `upsert_matview_refresh_state.sql:51` does
`refreshed_at = COALESCE(EXCLUDED.refreshed_at, existing)` and `matview_refresh.py:645` passes NULL on
failure; `matview_refresh.py:681-682` then returns `True, "never successfully refreshed"`. **A view that can
never succeed is therefore eligible on every tick forever, with no backoff.** Cost: **~731 s (12.2 min) of
guaranteed-doomed work per hour** against an 1,800 s budget. `agri.job_attempt` over 48 h: **46 failed, 7
deferred, 0 succeeded.** Meanwhile `agri.source_release`'s newest `retrieved_at` is **2026-08-09** — **0 new
releases in 7 days** — so `mv_signal_observation_day` burns 301 s hourly recomputing a view whose source has
not moved in 8 days. **Nothing in this runbook previously recorded this.**

**WHY THE FEATURE CENSUS COSTS 302 s — decomposed by measurement** (`vegetation`, 184,943 rows,
`EXPLAIN (ANALYZE, BUFFERS)`):

| variant | plan | spill | exec |
|---|---|---|---|
| `count(*)` only | **HashAggregate**, Memory 8,281 kB | **0** | **0.82 s** |
| + `count(DISTINCT geometry_id)` | GroupAggregate + Sort, quicksort 14,814 kB | 0 | 2.17 s |
| + `MAX(…properties…)` | GroupAggregate + Sort | **external merge Disk 118,504 kB** | 7.74 s |
| **both (the live DDL)** | GroupAggregate + Sort | **external merge Disk 118,504 kB** | **27.0 s** |

**`count(DISTINCT geometry_id)` forces sort-instead-of-hash; `MAX(properties->>'observedAt' …)` makes each
sorted tuple 511 bytes wide** by dragging the whole jsonb through the sort — ~1.4 GiB of sort input at full
scale against 32 MB `work_mem`. **The day expression is NOT the cost**; the pure day axis is 0.82 s with zero
spill. `mv_signal_observation_day` is a different shape entirely — **not a sort problem**, but ~22 GB of
sequential heap reads (two scans of an 11 GB heap) per refresh, with no usable index.

**Delta refresh measured: 14.0 s vs 302 s, spill eliminated** (36 changed groups over 24 h, LATERAL /
nested-loop shape, `Sort Method: quicksort Memory: 165 kB`). **But the naive join shape measured 98 s with a
full seq scan of 5,010,553 rows and ~52 GB of buffer traffic** — worse than useless. Shape is load-bearing.

**Change rate:** 1 h → 411 rows / **2 distinct (layer, day) groups**; 24 h → 25,049 rows / **36 groups**.
The matview holds 11,225 rows.

**RANKED FIX ORDER (measured, supersedes §12.4's ordering):**

1. **Pin `max_parallel_workers_per_gather = 0`** in `matview_refresh.py:510-516`. Stops the `/dev/shm` fault —
   **the single change that most directly reduces the RAM being measured.** Must land WITH #3.
2. **Break the livelock** — consecutive-failure backoff in `_eligibility`, needing a `consecutive_failures`
   column on `agri.matview_refresh_state`. Pure waste removal, zero correctness risk.
3. **Re-grain the feature census** — split `newest_observed_at` and the distinct/metric counts out, so the day
   axis is the 0.82 s HashAggregate path. ~22 s vs 302 s *(extrapolated)*.
4. **Delta-upsert the feature census**, LATERAL shape, **plus an explicit emptied-group DELETE**.
5. **Signal census: delta on `source_release_id`, NOT `updated_at`** — re-measure after #2 first.
6. **Weekly full reconcile** off the hourly lane, diff recorded in the ledger — the only honest equivalence proof.

**#1 + #2 + #3 are cheap and low-risk and may bring the lane inside its cap with no delta machinery at all.
§12.4 is worth building but it is FOURTH, not the whole answer.**

**PREREQUISITE BUG for any delta work:** `sql/ingest/link_feature_geometry.sql:97-100` updates
`geo.features.geometry_id` but **does not set `updated_at`**. `refresh_features.sql:131` and
`src/lib/server/services/ingest.ts:111` both do. So the watermark is maintained **by convention in 2 of 3
writers**, and `observation_count` / `unlinked_count` / `distinct_key_count` are driven entirely by
`geometry_id` — a delta keyed on `updated_at` would silently miss every linking pass.

**WHERE THIS RUNBOOK WAS WRONG — corrected by measurement:**

1. **"`id` carries FKs" (§5, §12.3) is FALSE.** `pg_constraint` returns **zero** FKs referencing
   `agri.signal_observation.id`, and **zero** referencing `geo.features.id`. It was cited as a structural
   blocker and is not one. Worse, `uq_signal_observation_release_cell_signal_time` **already contains
   `observed_at`**, so a hypertable-legal key already exists.
2. **§12.4's `WHERE updated_at > <watermark>` is inapplicable to half the problem** — **`agri.signal_observation`
   has no `updated_at` column at all.** Its available key is `source_release_id`.
3. **`mv_soil_survey_grid`'s cause was never recorded** — it is the `/dev/shm` fault above, not GEOS and not
   time. Live ledger durations are 74 s / 54 s, not the 66 s / 133 s recorded in §2.
4. **`create_hypertable(migrate_data => true)` as "a RAM event" is backwards** — per-index build memory is
   bounded by `maintenance_work_mem` (128 MB); the real costs are ~26 GB of WAL, a disk doubling and a long
   exclusive lock *(reasoning, not measured)*. Since disk is not a constraint, conversion is **more** feasible
   than §12.3 recorded — it simply buys nothing (see below).
5. **`mv_feature_observation_day` does NOT "fail every pulse"** (§9 C3) — it is **marginal**: last success
   2026-08-16 19:59:54, 301.5 s against a 300 s cap. `mv_signal_observation_day` has genuinely **never**
   succeeded (`refreshed_at` NULL).
6. **`drizzle/0029_pre_aggregation_layer.sql:72-77` raises an exception to guarantee
   `ix_features_layer_observation_day` exists**, on the grounds that without it the refresh seq-scans a 3,677 MB
   heap. **The live plan seq-scans anyway, twice, with the index present.** 284 MB serving nothing here.

**AND THE CAgg VERDICT IS NOW PROVEN, not argued:** with parallel costs forced to zero,
`count(*)` yields `Finalize GroupAggregate`/`Partial GroupAggregate` and `max(timestamptz)` carries
`aggcombinefn` — both partializable. **`count(DISTINCT geometry_id)` yields a plain `GroupAggregate` with no
Partial/Finalize at any cost setting.** Continuous aggregates store partials and finalize at read time, so
**`count(DISTINCT …)` is structurally incompatible with a CAgg** — demonstrated on this box.
`mv_signal_observation_day` additionally reads two views, uses `UNION ALL`, and uses `count(DISTINCT cell_id)`.
**Do not convert `agri.signal_observation`**: not because it is impossible (the FK blocker is fictional and disk
is free) but because **the CAgg it would enable cannot be built anyway.** If a hypertable is ever justified,
justify it with **`geo.mv_signal_cell_daily`** — 24,958,092 rows, 6,349 MB, **1,729 s measured refresh** — the
one relation where chunk-wise incremental refresh would genuinely pay.

**§10 impact:** the eight per-feature `tile_*` relations are **projections, not aggregates** — wrong shape for a
CAgg, but *safer* to delta than the census (keyed on feature id, so a vanished row deletes by id and there is no
emptied-group problem). **`tile_fire_detections_detail` (3,009,567 rows) must never be a full
`REFRESH CONCURRENTLY`** — that rebuilds 3M rows plus a GiST tree every pass. The fire grid rollups
(`cell × day`, `count(*)`, `max(frp)`) are **the only genuinely CAgg-shaped thing in the codebase** — and they
fail on *source* shape, not aggregate shape. **§10's refresh-budget caveat is understated: the lane has 0
successful attempts in 48 hours. Adding 11 specs before #1 and #2 land makes it strictly worse.**

### 12.3 The owner's principle is RIGHT; the named mechanism is BLOCKED

**The principle — refresh incrementally, touch only what changed, never recompute the whole relation — is
correct and is exactly the fix for the failing lane.** `mv_feature_observation_day` (302 s) and
`mv_signal_observation_day` (301 s) blow a 300 s cap **because `REFRESH MATERIALIZED VIEW` recomputes
everything**, every pulse, forever. That is the RAM and time cost the owner is pointing at, and C3 proves it is
also the causal spine of every staleness symptom in the UI.

**But a continuous aggregate requires a hypertable, and this database has no useful one.** §5,
"THERE ARE ZERO USEFUL HYPERTABLES": `agri.signal_observation` is a **plain heap table**; nothing ever called
`create_hypertable` (repo-wide grep returns nothing). The only hypertable, `tracking.positions`, holds **0 chunks
/ 40 kB**. No hypertable → no chunks → **no continuous aggregates and no columnar compression.**

Conversion is not a small step, and two of its blockers are structural rather than budgetary — so *"disk is not
an issue"*, while true and helpful, does not by itself unblock it:

- **`pk_signal_observation PRIMARY KEY (id)` is illegal** on a hypertable partitioned by `observed_at` — a
  hypertable's unique constraints must contain the partitioning column, and `id` carries FKs.
- **`geo.features` is worse:** its observation day is `geo.feature_observation_day(properties)`, a **function
  over jsonb**, not a column. A hypertable needs a real time column, so `geo.features` cannot be converted at all
  without first materialising `observed_day` as a stored column.
- `create_hypertable(migrate_data => true)` over 46M rows / 26 GB rewrites the whole table **on the 3 GB box this
  workstream exists to protect** — a RAM event, not a disk one.
- `compress_after` collides head-on with the data-completion workstream, which is **historical backfill**;
  compressed chunks block or badly slow inserts into them.

### 12.4 What to build instead — same property, no conversion

**Incremental delta refresh on the watermark ledger that already exists.** Replace
`REFRESH MATERIALIZED VIEW` on the two census relations with a plain table maintained by
`INSERT … SELECT … WHERE updated_at > <last watermark> … ON CONFLICT DO UPDATE`, driven by
`agri.matview_refresh_state` — the ledger the lane already writes through
(`jobs/matview_refresh.py`, `db/agri/tables/matview_refresh_state.sql`).

This is **O(rows changed), not O(table)** — the exact property a continuous aggregate provides — using machinery
that is already built, already tested and already on a schedule. No hypertable, no PK surgery, no 26 GB rewrite,
no compression/backfill conflict. It also removes the 300 s cap failure by construction rather than by raising
the cap.

**Ordering, and it matters:** the two census matviews gate every slider (C3), so fixing them is what makes a
newly-ingested day *selectable* — which is the precondition for §11's sync track ever showing a fresh day. **§11
and §12.4 are coupled: build the census fix, or the offline sync feature will faithfully cache a frozen axis.**

**Still worth doing, separately and with real evidence:** a proper feasibility pass on hypertable conversion for
`agri.signal_observation` specifically — measured, against prod, including whether TimescaleDB 2.29 FK support
covers the `id` references and what a `(id, observed_at)` composite key would break. **Do not treat 12.4 as a
refusal of the owner's instruction — it is the same property delivered by the route that is not blocked, with the
blocked route investigated rather than assumed.**

### 12.6 OWNER OBSERVATION 2026-08-17 (late) — idle is CLEAN, one query maxes it. This supersedes 12.1.

**Owner, with a Railway memory graph:** *"idle state is clean the moment i try to query anything it maxes out —
for 1 user mbs of memory should be fine."* Graph shape: flat at **~50–100 MB** while idle, then a **vertical jump
to ~1.00 GB the instant a query runs**, then flat at 1 GB.

**This is a materially different signal from §12.1 and it narrows the problem usefully.** §12.1's "the 3 GB gauge
is page cache and cannot fall" was measured on a long-lived instance with a warm 42 GB data dir, and it remains
true *for that steady reading*. But **an idle baseline of ~50 MB rising to 1 GB on a single query is not cache
fill — it is one query's working set**, and it is the clean confirmation of §12.1(c), the axis that was already
identified as the only one worth engineering.

**One user, one query, ~1 GB is exactly what the measurements predict**, so this is consistent, not mysterious:

- the wide feature census sorts **~1.4 GiB** (5M rows × 511-byte tuples, `external merge Disk 118,504 kB`
  measured on a *single* 184,943-row layer);
- the axis query plans `HashAggregate … **Planned Partitions: 32**` at 5,001,027 rows;
- `mv_soil_survey_grid` carries a **`Parallel Hash Left Join`** — a *shared, resizable* hash table in `/dev/shm`,
  i.e. RAM, which is what `could not resize shared memory segment` reports;
- a tile query walks a **1,318,892-entry** shared GiST tree and detoasts 7–10 jsonb keys per row from a
  1,467 MB TOAST relation, returning a **10.3 MB** vector tile at z5.

`work_mem = 16 MB` is not a ceiling on any of this: it is **per sort/hash node, per worker**, and
`max_parallel_workers_per_gather = 2` multiplies it, while a partitioned hash aggregate and a `Parallel Hash`
allocate beyond it in spill files and DSM respectively.

**So the fix list is unchanged and now has direct owner-visible evidence behind it:** §12.2b's ranked order
(parallel-worker pinning by `Parallel Hash` presence, the livelock backoff, the census re-grain) and **§10's
per-layer tile relations, which cut the tile-path resident working set from "whatever fraction of a 7,219 MB
table the envelopes touch" to ~250–600 MB.** §10 is the answer to this observation.

### 12.7 DROPPING TimescaleDB — AUTHORIZED, but it is NOT the cause. Do not expect it to fix this.

**Owner, 2026-08-17:** *"If needed drop timescale db entirely if its the main reason we are having these ram
memory issues."* **Authorization granted and recorded. But the premise is measured false, and whoever picks this
up must not spend the RAM budget expecting relief from it.**

**Measured against prod:** `timescaledb_information.hypertables` returns **one row** — `tracking.positions`,
1 dimension, **0 chunks**, compression off — and `timescaledb_information.continuous_aggregates` returns
**zero**. `agri.signal_observation` is `relkind = 'r'`, a plain heap, 26 GB / 46,068,872 rows. **TimescaleDB is
loaded and delivering nothing.**

Its actual cost is a **launcher plus background workers** — tens of MB, and it does not participate in a query's
working set at all. **It cannot account for a 50 MB → 1 GB jump on one query**, because none of the four
mechanisms in §12.6 involve it. Dropping it is worthwhile **hygiene** — it removes real base reservation, one
extension's worth of shared-memory allocation, and a `shared_preload_libraries` entry — and it is now
**authorized**. It is **not** the fix for §12.6.

**Ordering, and it matters:** drop it **after** the §12.2b fixes and §10, not before, so the relief attributable
to each is measurable rather than confounded. The owner previously *declined* dropping it (2026-08-16 morning);
that decision is now **reversed and superseded** by the authorization above.

**Before dropping, verify:** `tracking.positions` is the only hypertable and holds **0 chunks / 40 kB** (so
nothing is lost), and `tracking.positions` must be converted back to a plain table or dropped first — a
hypertable cannot survive `DROP EXTENSION timescaledb`. Check `shared_preload_libraries` and whether removing it
requires a **restart** (it does). **This is destructive DDL and needs its own window**, not a step inside
another workstream — same rule §3 applies to hypertable conversion.

### 12.5 Owner decisions taken 2026-08-17

- **`drizzle/0030`: LAND IT AND KEEP IT PERMANENTLY.** Owner chose "both" over §10's own recommendation to skip
  it. §10's note that it becomes a 400–600 MB carrying cost with one thin reader
  (`readFireDetectionsOnDay`'s bbox branch) **stands as recorded, and is accepted.** Disk is explicitly not a
  constraint. This settles §10 decision (a) and unblocks §7 step 2a.
- **Fire-detail time cap `N`: NO CAP — assume all 3,009,567 rows**, on the stated grounds that disk volume is not
  an issue. This is an **assumption drawn from the disk statement, not an explicit answer** to §10 decision (b);
  reversal is cheap (add a `WHERE observed_day >= current_date - N` and re-refresh). Flagged in §4.
- **§10 decision (c)** — whether `tile_interventions` gains a day column — **remains unanswered.** Default taken:
  **no day column**, matching today's behaviour exactly (`intervention_tiles` emits none); adding one changes what
  the slider does to that layer.

**Planner + lock settings measured 2026-08-20 — two of these change the plan.**

| setting | value | consequence |
|---|---|---|
| `enable_partitionwise_aggregate` | **off** (default) | **This is the lever that makes partitioning pay off for memory, and it is currently disabled.** The all-layer census matviews (§0.6) are exactly the workload blowing the cap. With it off they aggregate over one flat Append and peak allocation is unchanged by partitioning. Turn it **on** and re-measure — otherwise the restructure buys pruning for the layer-scoped reads and nothing at all for the censuses. |
| `enable_partitionwise_join` | **off** (default) | Same story for any join across partitioned relations. Enable with the above and measure both. |
| `max_locks_per_transaction` | **128** | 12 partitions x 11 indexes + parents is ~145 relations. **Re-creating every index inside one transaction exceeds the lock budget.** The swap must chunk index creation across transactions, and `LOCK TABLE geo.features` in the two ops scripts (§0.5) now takes 13 locks, not 1. |
| `maintenance_work_mem` | 128 MB | Per-index-build working memory during the copy. |
| `work_mem` | 16 MB | Per-sort/hash node, and the censuses use many. |
| `shared_buffers` | 256 MB | |
| `effective_cache_size` | 2 GB | 100% of the container, as §0 records. Still deliberately left alone. |
| `autovacuum_max_workers` | **3, `source=configuration file`** | The 2026-08-18 `ALTER SYSTEM` **persisted** — this partly answers §0.2's open question. Still unverified across a Railway-initiated restart. |
| `lock_timeout` | 0 (no timeout) | Must be set explicitly on the rename per §0.8; the default will let it queue indefinitely. |

Database is **37 GB** on PostgreSQL **18.4** — the `mv_signal_cell_daily` drop held.


# Backfill passoff — 2026-08-07

Every backfill and data-load stream, measured against production, with what is missing and what
to run next. Machine-readable companion: `docs/reports/data-stream-validation-2026-08-07.md`
(regenerate with `agri-cli validate-streams`). Operator guide:
`docs/runbooks/durable-backfill-lanes.md`.

**Headline: nothing is dead-lettered, and nothing is silently lost any more — but 8,932 days of
fire history and 38 of 48 streamflow windows are still un-walked, and the two workers doing the
walking still live on a laptop.**

---

## 1. Where every stream stands

Measured 2026-08-08T03:46Z against server day 2026-08-08, bbox `-125,42,-111,49`.

| Stream | Verdict | Rows | Days | Span | Worst gap | What it means |
| --- | --- | ---: | ---: | --- | ---: | --- |
| `fire-detections` | INCOMPLETE | 476,016 | 480 | 2022-08-05 → 2026-08-07 | 7,947d | 8,932 missing days. See §2. |
| `water-gauges` | **INVALID** | 393,177 | 496 | 1990-09-30 → 2026-08-07 | 223d | 689 sentinel rows. See §3. |
| `vegetation` | INCOMPLETE | 184,554 | 1,197 | 2022-08-05 → 2026-08-06 | 7d | Marginal — see §5. |
| `evacuation-zones` | INCOMPLETE | 457 | 40 | 2025-04-14 → 2026-08-08 | 101d | Upstream publishes on incident. §5 |
| `drought_areas` | **INVALID** | 1,035 | 207 | 2022-08-09 → 2026-08-04 | 20d | 154 rows outside bbox. §4 |
| `fire-perimeters` | **INVALID** | 119 | 27 | 2025-07-28 → 2026-08-06 | 323d | 13 undated. Fix written, §6. |
| `soil-survey` | **INVALID** | 218,653 | 0 | (undated by design) | — | 3,031 rows outside bbox. §4 |
| `weather-observations` | complete | 6,953 | 6 | 2026-08-03 → 2026-08-08 | 0 | Rolling window, healthy. |
| `sensors` | complete | 38,309 | 10 | 2026-07-30 → 2026-08-08 | 0 | Rolling window, healthy. |
| `watersheds` | complete | 9,396 | 40 | 2013-01-18 → 2019-11-21 | — | Reference snapshot. Done. |
| `burn-severity` | complete | 478 | 4 | 2020-11-24 → 2024-08-22 | — | Reference, MTBS cadence. |
| `interventions` | INCOMPLETE | 0 | — | — | — | **No producer exists.** §7 |
| `historical_vegetation` | INCOMPLETE | 0 | — | — | — | Never populated. §7 |
| `historical_fire_data` | INCOMPLETE | 0 | — | — | — | Never populated. §7 |
| `historical_water_drought` | INCOMPLETE | 0 | — | — | — | Never populated. §7 |

`INCOMPLETE` is not a failure state. An in-flight backfill is incomplete by definition, which is
why `validate-streams` exits 0 on it and 1 only on `INVALID`.

---

## 2. fire-detections — the largest outstanding debt

**8,932 missing days, in two distinct holes.**

| Hole | Days | Cause |
| --- | ---: | --- |
| 2000-11-01 → 2022-08-04 | 7,947 | Never attempted. The full-archive floor was only adopted 2026-08-07. |
| 2023-11-16 → 2026-07-22 | 980 | **Lost work.** Windows that failed and were skipped. |
| 4 isolated days in 2022–23 | 4 | Same cause as the 980. |
| 2026-08-08 | 1 | Today, not yet ingested. |

The 980-day hole is the one that matters, because it was not a gap in the upstream — it was
work that ran and was thrown away. 169 of 298 windows failed with `ConnectError`, the bash
driver advanced its cursor past every one, wrote a completion sentinel, and reported success.
Boundaries are exact: last day before is `2023-11-15`, first day after is `2026-07-23`. Both
ends are wider than the "2023-12 to 2026-06" estimate quoted earlier today.

**Lane state:** `agri.ingest.archive_walk.firms-archive`, 1,882 windows — **93 succeeded, 1,789
queued, 0 dead-lettered**. The 93 were settled by measuring what the layer already serves, not
by trusting the bash cursor. Oldest outstanding is `2000-11-01..2000-11-06`.

**To finish it:** the lane is planned and reconciled; it needs a worker. See §8.

---

## 3. water-gauges — INVALID, and it was getting worse hourly

**689 rows serve `flowCfs = -999999` as a real measurement.** That is USGS's "no reading"
marker stored as a JSON number, so it flattens every colour scale and percentile computed from
streamflow.

This was not historical. **669 of them were written in the six days to 2026-08-07** by the live
30-minute cron — 27 distinct sites, each qualified `Ssn` (seasonally monitored, i.e. legitimately
out of service). The archive path had guarded against this since it was built; the forward path
never did.

- **Fixed in code** — `ingest/usgs_nwis.py` now filters the sentinel through a single shared
  `is_missing_value_sentinel` predicate, matched by *value* never by sign (genuine reverse flow
  reaches −172,000 cfs). A gauge whose only reading is the sentinel is simply not reported this
  tick, and dropped sites are now counted in `details.sentinel_gauges` — nothing counted them
  before, which is why six days went unnoticed. **Not yet deployed.**
- **Cleanup written, NOT run** — `docs/runbooks/usgs-sentinel-cleanup.md`. It proposes `DELETE`
  rather than nulling the field, because a null flow is still a row asserting an observation of
  an absence. **Deploy the parser fix first**; the cron will otherwise rewrite the rows within
  30 minutes. Four days hold only a sentinel row and will leave the time axis; that is correct.

**Also on this stream:** 46 rows from decommissioned gauges dated 1990-09-30 → 2020-12-03. These
are real USGS readings — a current-conditions pull returns each discontinued site's last-ever
reading with that reading's own timestamp. They are legitimate data, not a defect, and the
slider's 21-day continuity rule already drops them so no user ever sees 1990. They are now
reported separately as `days_below_expected_floor` instead of inflating the gap count 12.4×.

**Lane state:** `streamflow-archive`, 48 windows — **10 succeeded, 38 queued, 0 dead-lettered.**
Only 10 of 48 fully landed despite the bash walk reporting progress.

---

## 4. Rows outside the bounded-ingestion contract — newly visible

`soil-survey` **3,031 rows** and `drought_areas` **154 rows** fall outside `INGEST_BBOX`. These
were written past the bounded-ingestion contract, so no cron tick will ever refresh or retire
them — they are permanent orphans of some earlier, wider-bounded load.

This check had **zero coverage** in the first validation run because `INGEST_BBOX` is absent from
`services/agri-data-service/.env`; it only evaluated once the bbox was passed explicitly. Worth
setting the variable in `.env` so a local run matches what the cron sees.

Decide whether to retire these rows or widen the declared bbox. Not actioned — it is a
data-retention call, not a bug fix.

---

## 5. Gaps that are the upstream's own cadence, not missing work

Do not re-investigate these:

- **`vegetation`** — 268 missing days across 165 gaps, worst 7 days, against a ~5-day Sentinel-2
  revisit. The record is dense (1,197 of 1,465 days). Marginal at worst. 102 thin days sit inside
  the slider window under a density floor of 8.
- **`evacuation-zones`** — 442 missing days, worst 101. Oregon OEM publishes on incident, not on
  a schedule. A quiet winter is not a gap.
- **`drought_areas`** — 207 observed days over four years is exactly the weekly USDM rhythm. The
  one genuine finding is the **20-day gap 2026-02-11 → 2026-03-02**, roughly three skipped
  releases. Worth a `ingest-drought-history` pass over that window.
- **`watersheds`, `burn-severity`** — reference layers with no declared cadence.

---

## 6. fire-perimeters — 13 undated rows, fix written and unapplied

All 13 carry a valid `fireDiscoveryDateTime` and a JSON-null `polygonDateTime`.
`geo.feature_observation_day` coalesced only `observedAt → updatedAt → polygonDateTime`, so the
function's key list was short by one. The data was fine.

`drizzle/0018_fire_discovery_observation_day.sql` adds the key **last** in the chain — discovery
dates the *incident*, `polygonDateTime` dates the *geometry on this row*, and all 119 rows carry
both, so ranking discovery higher would re-date 106 rows onto their incident's discovery day and
collapse a perimeter's revision history. Migration contract updated
(`d13c81ec9b3d1032ce314dad4cc6a2846a11a244bca00c7daee386ffd6d1acce`).

**Not applied to production.** It applies automatically on the next Railway push-deploy
(`preDeployCommand`). No Martin restart needed — identical signature, `CREATE OR REPLACE`, and
`tile_expiry: 5m` ages the cached tiles out.

⚠️ **`drizzle/0017_watershed_persistence.sql` is still untracked in git.** It must be committed
alongside 0018 or the journal and contract will reference a file the deploy image does not carry.

---

## 7. Streams with no producer at all

`interventions`, `historical_vegetation`, `historical_fire_data`, `historical_water_drought` —
all zero rows. `interventions` is a known standing gap. The three `historical_*` tables have
never been populated. None of these is a backfill that stalled; they are features that were never
built. Flagging so they stop reading as regressions.

---

## 8. The one thing still blocking — the workers live on a laptop

Everything above is planned, reconciled and queryable. **Nothing is running it in a durable
place yet.**

Two Windows scheduled tasks are still walking these archives from this machine:
`PlantGeo-FIRMS-archive-backfill` and `PlantGeoStreamflowArchiveBackfill` (plus two completed
Open-Meteo tasks that are now no-ops). They stall silently whenever the laptop sleeps, reboots or
loses network — which is how 57% of FIRMS windows were lost in the first place.

They were **deliberately left running**. Stopping them before the Railway services exist would
leave nothing walking the archive at all. They are safe to leave: the walk is idempotent and the
ledger now measures truth from the data rather than from their cursors, so whatever they land
before cutover is simply reconciled in.

**The cutover needs a Railway dashboard change that cannot be scripted:**

1. Create three services from `infra/cron-archive-firms/`, `infra/cron-archive-streamflow/` and
   `infra/cron-validate/`. Each needs **Root Directory `/`** *and* the `dockerfilePath` from its
   `railway.json` — both settings must change together or the build silently uses the wrong
   context.
2. Set on each: `INGEST_BBOX`, `INGEST_MAX_SOURCE_RECORDS=50000`, the
   `LOCAL_SOURCE_LOADER_DATABASE_URL` / `DATABASE_URL` pair, and `NASA_FIRMS_KEY` on the FIRMS
   service.
3. Then, on this machine:
   ```powershell
   Stop-ScheduledTask   -TaskName PlantGeo-FIRMS-archive-backfill, PlantGeoStreamflowArchiveBackfill
   Unregister-ScheduledTask -TaskName PlantGeo-FIRMS-archive-backfill, PlantGeoStreamflowArchiveBackfill, PlantGeo-OpenMeteo-SoilTemp-backfill, PlantGeo-OpenMeteo-VPD-backfill -Confirm:$false
   ```
4. `services/agri-data-service/durable-archive-backfill.sh` and `firms-archive-full.sh` are then
   superseded and can be deleted. They were **not** deleted here — they are still running, and
   editing a live bash script can corrupt the running process. Their cursor and failure files in
   `.agri-local-runs/locks/` need no migration: `jobs-reconcile-lane` derives coverage from the
   data, which is the whole point.
5. Verify: `agri-cli jobs-status` should show `succeeded` climbing and `queued` falling.

---

## 9. Follow-ups, ranked

1. **Deploy the streamflow sentinel fix, then run the cleanup** (§3). Live corruption, ~110
   rows/day.
2. **Stand up the three Railway cron services** (§8). Until then the archive walk is laptop-bound.
3. **Commit `drizzle/0017`** before or with `0018` (§6).
4. **`ingest-drought-history` over 2026-02-11 → 2026-03-02** (§5), ~3 USDM releases.
5. **Decide on the out-of-bbox rows** (§4) — retire or widen. Set `INGEST_BBOX` in `.env` so
   local runs evaluate the check.
6. **Real-database coverage for `reconcile.py`.** Its first `--apply` against production failed
   with `IndeterminateDatatypeError` — `jsonb_build_object` is `variadic "any"` and an untyped
   bind in the key position cannot be resolved. The unit tests cannot catch this class of bug
   (they answer `AsyncSession.execute` from a recording stub) and neither can psycopg2 (it
   substitutes client-side). `tests/test_jobs_protocol_agri_db.py` covers the lease protocol this
   way; `reconcile.py` and `validation.py` have no equivalent.
7. **`durable-backfill.sh` is still a separate mechanism.** The plan-based ERA5/Open-Meteo lanes
   run through their own checkpoint tables and were deliberately out of scope. Both of its lanes
   are complete, so this is not urgent — but it is the remaining non-uniform loader.
8. **`fireDiscoveryDateTime` is not in `PUBLISHER_NAMED_DAY_RULE.observationTimeKeys`**
   (`environmental-read-model.ts`), so the slider axis buckets from three keys while the tile
   reads four. Harmless today and now pinned by a contract test rather than silent.
9. **Watershed render path** still proxies. Adding `watershed_tiles` to `DYNAMIC_TILE_SOURCE_IDS`
   before Martin redeploys would 404 the whole composite and blank every dynamic layer; steps are
   in `src/lib/map/sources.ts`.
10. **Admin boundaries and the wildfire risk-zone layer** were scoped earlier today and are still
    not started.

---

## 10. Why the ledger, and why not DBOS

`agri.job_*` was already migrated into production, fully modelled, with **zero rows and zero
executing code**. It carries exactly what this problem needs: `shard_key` work items, fenced
leases, append-only checkpoints, `time_budget_seconds`, retry policy, incidents. The runtime was
built onto it rather than beside it.

DBOS Transact was evaluated against its own source, not its marketing, and declined:

- Recovery filters on `get_pending_workflows(executor_id, app_version)` where `app_version` is a
  **hash of registered function source** — any deploy touching backfill code permanently strands
  in-flight work.
- Its system database engine is `sa.create_engine(url.set(drivername="postgresql+psycopg"))` —
  **synchronous psycopg3**, wrapped in `asyncio.to_thread` at every call site, inside a service
  that hard-validates `postgresql+asyncpg` on every DSN.
- `DBOS.launch()` runs its own migrations unconditionally, including `CREATE DATABASE`, and
  proceeds **without the advisory lock** on timeout — against a database Alembic owns.
- Its checkpoints are opaque step blobs. They cannot answer "which archive windows have landed",
  which is the question this ledger exists to answer.

Its async workflow support is genuine and its `executor_id` default would have worked for cron
containers. It is a good library that does not fit one-shot cron containers. **Settled — do not
re-open.**

---

## 11. Verification behind this document

- Python: **1,545 passed, 38 skipped**; ruff, ruff format, mypy (94 files) clean.
- TypeScript: **738 passed / 77 files**; `tsc --noEmit` clean; lint 0 errors; `next build` clean.
- **Real-database protocol suite: 11 passed** against a live PostGIS/TimescaleDB container at
  head `20260803_0018` — fenced-lease claim, expired-lease reclaim, `FOR UPDATE SKIP LOCKED`
  interleaving across two connections, `max_attempts` → `dead_letter`, and the parameter bindings
  that unit tests structurally cannot exercise.
- The fencing guarantee was proved by negative control: removing the fence predicate at runtime
  failed exactly one test, the one asserting a fenced-out worker cannot complete a shard.

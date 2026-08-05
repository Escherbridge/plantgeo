---
type: lane-brief
track: ingestion_warehouse_consolidation_20260803
lane: D
status: partial
depends_on: A
started_at: 2026-08-03
blocked_on: owner - Railway cron service Root Directory must move to / before the image can build
---

# Lane D — six Python ingest modules + cron container swap

This is the main body of **Phase 1**. Read
[`lanes/README.md`](README.md) first (wave plan, file boundaries, inherited rules), then
[`plans/ingestion-warehouse-consolidation-2026-08-03.md`](../../../../plans/ingestion-warehouse-consolidation-2026-08-03.md)
§3 (job-by-job port table, the 12 behaviours, the 6-step verification method) and §7 Phase 1.
Do not re-derive anything already settled in [`spec.md`](../spec.md).

---

## 1. Goal

The six ingestion jobs run as Python modules under
`services/agri-data-service/src/agri_data_service/ingest/`, invoked by a Railway cron container
that **runs to completion and exits non-zero when any job fails**. Today
`src/app/api/cron/ingest/route.ts:30-46` sets an in-memory flag, fires
`void runAllIngestionJobs()` **detached**, and returns `202 {status:"started"}` before any job has
done anything; `infra/cron-ingest/Dockerfile:6` treats that `202` as success. A job that throws
after the response lands in `console.error` (`route.ts:39-41`) and **nothing anywhere goes red** —
that is the entire reliability motivation for this lane. When you are done, a failed FIRMS fetch is
a failed Railway cron run. **Zero schema changes**: `geo.features`, `geo.layers` and
`geo.drought_areas` already exist and are the same targets the TS writes to.

---

## 2. Prerequisites

**Lane A must have landed `ingest/identity.py`.** Verify — do not assume:

```powershell
Test-Path services\agri-data-service\src\agri_data_service\ingest\identity.py
```
Expected: `True`. (At the time this brief was written the `ingest/` directory did **not** exist —
`services/agri-data-service/src/agri_data_service/` holds only `app.py`, `cli.py`, `config.py`,
`db/`, `execution/`, `models/`, `routes/`, `schemas/`, `seed/`.)

```powershell
cd <repo-root>/services/agri-data-service
uv run pytest tests/test_ingest_identity.py -q
```
Expected: all pass, `0 failed`. Do **not** set `AGRI_TEST_DATABASE_URL` for this — the identity
tests are pure, and `pytest_sessionfinish` fails the session if any `agri_db` test *skips* while
that variable is set (track plan, "Other verification facts").

Read `identity.py` before writing a single job module. Its `build_*_identity` functions are the
**only** place an identity string may be built — lane A ships one per producer plus
`format_coordinate` and `format_javascript_timestamp`. If it does not expose a builder for one of
your keyed producers, stop and report to the orchestrator rather than writing your own.

Lane A also created `ingest/__init__.py` (deliberately **empty** — leave it that way unless you
have a reason, and say so) and `ingest/AGENTS.md` (with the `identity.py` paragraph). **Append** to
that AGENTS.md; never rewrite it. Lane E hands you its MTBS paragraph through the orchestrator
rather than editing the file, so you are its only wave-1 writer.

**Local services** (all podman containers are stopped; start only these):

```powershell
podman compose up -d postgis redis     # postgis 127.0.0.1:5434, redis 127.0.0.1:6379
```
`docker-compose.yml:5,26` — ports come from `POSTGRES_HOST_PORT` / `REDIS_HOST_PORT` in `.env`.
Run `SHOW server_version` before trusting any local port (track plan, environment section).

---

## 3. Files you own

Exactly this list, from [`lanes/README.md`](README.md) §"File boundaries", lane D row:

| Action | Path |
|---|---|
| create | `services/agri-data-service/src/agri_data_service/ingest/*.py` — **except `identity.py`** (lane A) and **except `mtbs.py`** (lane E) |
| create | `services/agri-data-service/tests/test_ingest_*.py` for the modules you write — **except `test_ingest_identity.py`** (lane A) and **except `test_ingest_mtbs.py`** (lane E) |
| append | `services/agri-data-service/src/agri_data_service/ingest/AGENTS.md` (lane A created it) |
| rewrite | `infra/cron-ingest/Dockerfile`, `infra/cron-ingest/railway.json` |
| delete (after verification) | `src/app/api/cron/ingest/route.ts`, `src/lib/server/services/ingestion-jobs.ts`, `src/__tests__/api/cron-ingest.test.ts`, `src/__tests__/services/ingestion-jobs.test.ts` |

**Must not touch:** `src/lib/server/db/**`, `services/agri-data-service/db/agri/**`, `drizzle/**`,
any Alembic revision, `ingest/identity.py`, `ingest/mtbs.py`, `tests/test_ingest_identity.py`,
`tests/test_ingest_mtbs.py`, anything under `scripts/`, and `infra/tiles/**` (lane F). Your `infra/`
claim is `infra/cron-ingest/**` and nothing wider.

**Lanes B, E, F, G and H are running concurrently in the same wave.** Anything outside the table
above belongs to someone else or to a later wave. If you need it, stop and report — do not reach
across. The four files you will want that are **not** yours are handled in §7 (open questions 1-3);
`README.md` §"Shared files that no single lane owns" already grants you `cli.py`, `config.py`,
`pyproject.toml` and `uv.lock` on an announce-first basis.

The two `src/__tests__/` files above are yours **to delete only**. Do not create anything under
`src/__tests__/` — lane B owns `src/__tests__/lib/geometry-migration.test.ts` and lane G owns
`src/__tests__/stores/**` and `src/__tests__/components/**`.

**Do not delete `src/lib/server/services/ingest.ts`.** It looks like part of the job, but five other
API routes still import it: `src/app/api/ingest/{fires,firms,interventions,sensors,weather}/route.ts`.
Only `ingestion-jobs.ts` and the cron route go.

---

## 4. The work

### Step 1 — Package skeleton

`ingest/__init__.py` already exists and is empty (lane A) — **do not recreate or overwrite it**.
Add a shared `ingest/writer.py` that owns the port of
`src/lib/server/services/ingest.ts` (layer resolve, advisory lock, insert, refresh-in-place diff,
Redis publish); a shared `ingest/http.py` mirroring the bounds in
`src/lib/server/http/bounded-upstream.ts` (max bytes, timeout, typed `UpstreamHttpError` /
`UpstreamPayloadError` — USDM's 404 handling depends on that distinction).

Match `cli.py`'s idiom (`cli.py` is 1814 lines; the shape is stable throughout):
`@click.group()` / `@cli.command("verb-name")` (`cli.py:140-148, 267-292`), sync Click callback
wrapping `asyncio.run(_impl(...))`, failures raised as `click.ClickException` (`cli.py:169-175`),
success emitted as one `json.dumps(..., sort_keys=True)` line to stdout (`cli.py:292`),
`structlog.get_logger()` for everything else (`cli.py:129`). SQLAlchemy async sessions come from
`agri_data_service.db.engine`; the existing per-purpose session helpers are at
`db/engine.py:115-160`.

House style: full-word names (`build_firms_feature_identity`, not `mk_id`), one-line doc-comments,
rationale in a directory-level `AGENTS.md` next to the modules.

### Step 2 — Port the six jobs

One module per job. Target names and CLI verbs are fixed by the plan (§3 port table):

| Module | CLI verb | Ports from | Layer / table |
|---|---|---|---|
| `ingest/firms.py` | `ingest-firms` | `ingestion-jobs.ts:118-168` `runFireIngestionJob` | `geo.features` / `fire-detections` |
| `ingest/usgs_nwis.py` | `ingest-streamflow` | `ingestion-jobs.ts:171-208` `runWaterDroughtIngestionJob` | `geo.features` / `water-gauges` |
| `ingest/open_meteo.py` | `ingest-weather` | `ingestion-jobs.ts:264-310` `runWeatherIngestionJob` | `geo.features` / `weather-observations` |
| `ingest/wfigs.py` | `ingest-fire-perimeters` | `ingestion-jobs.ts:313-360` `runFirePerimetersIngestionJob` | `geo.features` / `fire-perimeters` |
| `ingest/usdm.py` | `ingest-drought` | `ingestion-jobs.ts:368-377` → `drought-ingestion.ts:47-130` | `geo.drought_areas` |
| `ingest/ndvi.py` | `ingest-ndvi` | `ingestion-jobs.ts:380-388` — a stub that unconditionally returns `skipped` | nothing yet |

Upstream adapters to port alongside them (URL + parsing + validation live here, not in the job):

| Adapter | Source | Note |
|---|---|---|
| FIRMS | `nasa-firms.ts:81-124`, URL at `:93` | VIIRS_SNPP_NRT area CSV, needs `FIRMS_API_KEY` |
| USGS NWIS | `usgs-water.ts:163-209`, URL at `:165` | see trap T5 — `updatedAt` fallback |
| Open-Meteo | `weather.ts:40-87`, base URL `:17` | `observedAt` built at `:74` — see trap T4 |
| WFIGS | `wfigs-fire-perimeters.ts:188-207`, query URL `:28-29` | |
| USDM | `usdm-drought.ts:106-181`, base URL `:28` | date-walk `usdmValidDateCandidates` `:71-88` |
| FIRMS time rules | `environmental-time.ts:15-84` | `parseFirmsObservationTime`, `firmsDayRange`, `isFreshObservation` |

**Keep `ingest-ndvi` a hard stub that exits non-zero-free (`skipped`, exit 0) with the same reason
string.** Filling it is Phase 5 / lane F. Porting the stub keeps `ingest-all`'s summary shape
complete and stops anyone "fixing" the empty layer — empty layers here are deliberate governance
stubs, not bugs.

### Step 3 — `ingest-all`

Ports `runAllIngestionJobs` (`ingestion-jobs.ts:391-423`). Same six jobs, same per-job isolation
(`Promise.all` + per-job try/except at `:407-421` — one failure must not erase the other five's
progress), prints the JSON summary, and then **exits non-zero if any job's status is `failed`**.
That last clause is the whole point of the lane; it has no equivalent in the TS.

### Step 4 — Swap the cron container

1. Rewrite `infra/cron-ingest/Dockerfile` as a Python image that installs the `agri-data-service`
   package and runs `agri-cli ingest-all`. The existing curl entrypoint
   (`infra/cron-ingest/Dockerfile:3,6`) goes entirely, along with the `202|409 → exit 0` mapping.
2. `infra/cron-ingest/railway.json` keeps `cronSchedule: 0 * * * *` and
   `restartPolicyType: NEVER` (`railway.json:4-5`) — that pair, not the deleted in-memory
   `ingestionInFlight` boolean (`route.ts:12`), is now the concurrency guard.
3. **Build context** — see open question 2. The service's Railway root directory is
   `/infra/cron-ingest` (`docs/deployment.md:389-392`), which cannot see
   `services/agri-data-service/`. This must change before the image can build.
4. **Database URL** — see open question 1. `CRON_SECRET` is no longer needed by this service; the
   container talks to Postgres on the private network.

### Step 5 — Verify, then delete

Run the six-step verification in plan §3 ("Verification method"), in order, per job. Step 1 (the
golden-file ID test) **gates DB access** — no job connects to a database until its identity strings
are byte-identical to the TS output for a recorded payload.

Only after all six pass, delete `src/app/api/cron/ingest/route.ts`,
`src/lib/server/services/ingestion-jobs.ts` and their two test files, then run the full sweep (§6).

---

## 5. Traps

Generic rules are in [`lanes/README.md`](README.md) §"Rules every lane inherits" — not repeated.
These are lane D's own. **T1-T3 are the ones that will actually bite.**

### T1 — The refresh diff ignores geometry **on purpose**. Preserve it exactly.

`src/lib/server/services/ingest.ts:107-122`, comment at `:95-106`. The `drizzle/0004` trigger
(`drizzle/0004_repair_ingested_geometries.sql:45-49`) rewrites `properties.geometry` through
`ST_AsGeoJSON` and may add `properties.geometry_repaired` on **every** write, so the stored copy can
never equal the raw upstream text. A whole-payload compare therefore rewrites every row on every
run — endless churn plus a realtime storm. The diff instead uses the producer's own **scalar**
revision fields (WFIGS advances `polygonDateTime` / `percentContained`). This is load-bearing, not
an oversight, and §2.0 of the plan reuses the same finding as the Type-2 change-detection rule.

The predicate is also deliberately **asymmetric** (`ingest.ts:116-117`):

```sql
(properties - 'geometry' - 'geometry_repaired')
  IS DISTINCT FROM ($candidate::jsonb - 'geometry')
```

Stored side strips both keys; candidate side strips only `geometry`, because a freshly built
candidate never carries `geometry_repaired`. Reproduce it literally. Do not "tidy" it into a
symmetric strip — that changes nothing today and silently breaks if a producer ever emits that key.

### T2 — Feature-ID drift duplicates ~15 000 rows and climbs hourly

Dedupe is entirely `properties->>'id'` (`ingest.ts:56-65`); the only DB-side guard is
`features_layer_external_id_unique` on `(layer_id, properties->>'id')`
(`src/lib/server/db/schema.ts:180-183` — **the plan cites `:181-184`; it is off by one**), which
stops a *duplicate* key and does nothing about a *changed* one. Risk 2 in the plan's register. Every identity string comes from `identity.py` — no
exceptions, no local f-strings.

Formats being reproduced (verified below against the TypeScript). **If the shipped
`identity.py` disagrees with this table, `identity.py` wins** — lane A resolved the plan's
producer-vs-layer-name ambiguity and owns the final format. Read the table as the TS source of
truth you are porting *from*, not as the key format you are porting *to*.

| Producer | Format | Source |
|---|---|---|
| FIRMS | `satellite:acqDate:acqTime:lat.toFixed(4):lon.toFixed(4)` | `ingestion-jobs.ts:100-115` |
| USGS gauges | `${siteNo}:${updatedAt}` | `ingestion-jobs.ts:189` |
| Open-Meteo | `${lat.toFixed(4)}:${lon.toFixed(4)}:${weather.observedAt}` | `ingestion-jobs.ts:294` |
| WFIGS | `perimeter.uniqueFireIdentifier` (bare) | `ingestion-jobs.ts:334` — **the plan cites `:335`; the correct line is 334.** `polygonDateTime` is at `:340`. |

### T3 — `toISOString()` emits milliseconds, and they are **inside** a key

`weather.ts:74` builds `observedAt` as `new Date(c.time * 1000).toISOString()` →
`2026-08-03T14:05:00.000Z`. `ingestion-jobs.ts:294` embeds that string in the weather feature id.
Python's `datetime.isoformat()` yields `2026-08-03T14:05:00+00:00` — a different key for the same
observation, i.e. T2 firing across the whole `weather-observations` layer. The same `.000Z` form
comes out of `parseFirmsObservationTime` (`environmental-time.ts:49`) into
`properties.observedAt`, which the T1 diff compares. Emit the JS form explicitly; assert it in the
golden-file test.

### T4 — `toFixed(4)` is not `f"{x:.4f}"`

JS `Number.prototype.toFixed` and Python's format spec disagree on ties (`(0.12345).toFixed(4)`
= `"0.1235"`; `f"{0.12345:.4f}"` = `"0.1234"`). Lane A owns the fix inside `identity.py`; your job
is to never bypass it and to include tie-value coordinates in your recorded fixtures.

### T5 — USGS `updatedAt` has a `now()` fallback that mints a fresh key every run

`usgs-water.ts:183` (and `:233`): `const updatedAt = latest?.dateTime ?? new Date().toISOString()`.
A gauge with no current timeseries value therefore gets a wall-clock `updatedAt`, which goes
straight into its feature id (`ingestion-jobs.ts:189`) — a **new row every hour, forever**. This is
a plausible cause of the +2 244 water-gauges growth the plan measured during drafting (§2.0). The
port must not launder it: either skip a gauge with no reading, or keep the behaviour deliberately
and say so in `ingest/AGENTS.md`. Do not silently port it. Flag whichever you choose.

### T6 — The USDM job is not bbox-scoped and has three rules that look like bugs

`drought-ingestion.ts` + `usdm-drought.ts`. All four are deliberate:

1. The date is the **request parameter**, never parsed from the payload; the GeoJSON carries no
   date field, so `usdm_current.json` is deliberately never used, and a `404` means "not published
   yet", not a failure (`usdm-drought.ts:95-132`).
2. A repeated DM class **rejects the whole release** rather than picking one (`:141-154`).
3. Geometry is repaired **in the database** —
   `ST_Multi(ST_CollectionExtract(ST_MakeValid(ST_SetSRID(ST_GeomFromGeoJSON(...),4326)),3))` with
   `ON CONFLICT (valid_date, dm_category) DO UPDATE ... WHERE <replace>`
   (`drought-ingestion.ts:55-74`). Keep it in SQL; do not move it to Shapely.
4. The prune keeps the newest N releases, default 8, ~19 MB each
   (`drought-ingestion.ts:82-97`, constants `:10-12`).

### T7 — Behaviours that are quiet correctness, not defaults

- `perimeterSeverity` returns **`null`** when WFIGS reports no containment
  (`ingestion-jobs.ts:215-226`) — never the lowest severity. `src/lib/map/layers.ts:71` renders
  against that contract.
- The weather grid is **densified, never sliced**: spacing scales up until `columns * rows ≤ 150`
  (`ingestion-jobs.ts:233-261`, cap at `:27`). Slicing the list instead would silently blank half
  the bbox.
- `INGEST_BBOX` policy: `west,south,east,north`, `east-west ≤ 30°`, `north-south ≤ 20°`, throws
  otherwise (`ingestion-jobs.ts:70-94`). An unset bbox is `skipped`, not `failed`.
- FIRMS rejects observations older than `min(10, max(1, dayRange))` days
  (`ingestion-jobs.ts:136-141`) and future-skewed ones beyond 5 minutes
  (`environmental-time.ts:3,73-84`).
- `FIRMS_DAY_RANGE` must match `/^\d+$/` exactly — `"5abc"` falls back to the default rather than
  being partially accepted (`environmental-time.ts:52,66-70`).
- Env vars are read **at call time**, not at import, so cron env changes take effect without a
  restart (`ingestion-jobs.ts:29-39, 41-51`).

### T8 — Redis publish is part of the contract, and the Python service has no Redis client

One publish per **written** row to `layer:<name>` (`ingest.ts:163-172`, transport at
`realtime.ts:55-63`); the channel is validated against `/^layer:[a-z0-9-]{1,100}$/`
(`ingest.ts:145`). Dropping it costs the map live invalidation. **`redis` is not in
`services/agri-data-service/pyproject.toml`'s dependency list** (verified — the only `redis`
matches in that tree are the word "redistribution"). Adding it means editing `pyproject.toml` and
regenerating `uv.lock` — see open question 3.

### T9 — `docs/reviews/data-readiness-2026-08-02.md` has wrong line numbers

It cites `ingestion-jobs.ts:139-176`, `:197-239`, `:263`, `:291-299` — none of which match the
current file. That review is known-unreliable; verify any citation you take from it. Use the plan's
§3 table and this brief instead.

### T10 — Do not amputate the shared helpers

- `authorizeCronRequest` (`src/lib/server/security/ingress.ts:71-77`) loses its only caller when the
  route goes. Deleting it is legitimate "clean up as you touch it", but check
  `src/__tests__` first and report the removal.
- `src/__tests__/services/ingestion-jobs.test.ts:3` holds `PNW_BBOX = "-125,42,-111,49"`, which
  plan §4 cites as the canonical coverage box for lanes E and F. Port that constant into the Python
  package before deleting the file, and note the new home in your report.

---

## 6. Definition of done

Run the sweep **once, at the end** — not test→fix→test.

**Python** (from `services/agri-data-service`, matching the gates in `Dockerfile:33-36`):

```powershell
uv run ruff format --check src tests
uv run ruff check .
uv run mypy src
uv run pytest tests/test_ingest_identity.py <your new test files, named explicitly> -q
```
Proof: ruff/mypy exit 0 with no output; pytest reports `0 failed` and **`0 skipped`** across your
new files.

**Name your test files explicitly — do not glob `tests/test_ingest_*.py`.** That glob picks up
`tests/test_ingest_mtbs.py`, which lane E is writing concurrently; a half-written file there would
fail your sweep for a reason that is not yours. Same for the whole-tree `uv run ruff check .` and
`uv run mypy src`: if either reports an error inside `ingest/mtbs.py`, that is lane E's, so report
it rather than fixing it.

**Golden-file gate** — the recorded-payload → identity-string test must be byte-exact for all four
keyed producers before any DB-touching test runs. This is plan §3 verification step 1 and it is
non-negotiable.

**Idempotence** (plan §3 step 2) — against the local postgis at `127.0.0.1:5434`:

```powershell
$env:DATABASE_URL="<local dsn>"; uv run agri-cli ingest-all
```
For each layer, capture `SELECT count(*) FROM geo.features f JOIN geo.layers l ON l.id=f.layer_id
WHERE l.name=$1` **inside the same transaction** immediately before the replayed-payload run and
again after. **Delta exactly 0.** Never compare against a literal — the numbers in the plan
(6297 / 7596 / 1013 / 110) are labelled orientation-only and were already stale when written.

**Exit-code proof — the reason this lane exists:**

```powershell
uv run agri-cli ingest-all   # with one upstream forced to fail
echo $LASTEXITCODE
```
Expected: **non-zero**. A run in which one job fails and the command exits `0` is a failed lane,
regardless of what the tests say.

**Realtime** (plan §3 step 5): subscribe to `layer:fire-detections` on `127.0.0.1:6379` and assert
one message per written row.

**Next.js**, after the deletions:

```powershell
npm run type-check
npm run lint
npm test
npm run check:data-boundary
```
Proof: `tsc` clean, eslint **0 errors**, vitest all-pass with no remaining reference to
`ingestion-jobs` or `/api/cron/ingest`, data-boundary check clean. Confirm with
`grep -rn "runAllIngestionJobs\|api/cron/ingest" src infra` returning only `docs/` hits.

**Cron container** builds and runs locally to completion:

```powershell
podman build -f infra/cron-ingest/Dockerfile -t plantgeo-ingest-cron .
podman run --rm --env-file <env> plantgeo-ingest-cron; echo $LASTEXITCODE
```

---

## 7. Open questions

### 1. Where does the cron container get its DB URL? *(plan open question 10 — and it is worse than "mechanical")*

`Settings.require_local_source_loader_database_url` (`config.py:136-170`) **hard-rejects** anything
that is not `postgresql+asyncpg://plantgeo_loader@127.0.0.1:5442/plantgeo*` — host, port, role,
scheme and empty query string are all validated (constants at `config.py:13-16`). The Railway cron
container is none of those. Separately, `config.py:378` forbids production service profiles from
receiving `DATABASE_URL` at all.

**Recommendation:** add one new setting, `INGEST_DATABASE_URL`, with its own validator that requires
`postgresql+asyncpg`, a non-loopback host, and explicitly refuses to equal `DATABASE_URL` (the same
shape as the existing `require_*` accessors), plus a matching `ingest_session()` helper alongside
`db/engine.py:115-160`. Wire it on Railway as a **reference variable** to `plantgeo`'s private-network
Postgres URL. Do **not** widen the loader validator — it is deliberately narrow and other lanes
depend on it. `config.py` is owned by no wave-1 lane; confirm with the orchestrator before editing,
and expect `tests/test_config.py` to need a case.

### 2. Railway build context for the cron service

`docs/deployment.md:389-392` records the service's repository root as `/infra/cron-ingest`. A Python
image needs `services/agri-data-service/{pyproject.toml,uv.lock,src/}` in its build context, which
that root cannot see.

**Recommendation:** set the service's Root Directory to `/`, its Dockerfile path to
`infra/cron-ingest/Dockerfile`, and its config-as-code path to `infra/cron-ingest/railway.json`.
That keeps every file inside lane D's boundary and leaves the repo-root `railway.json`
(plantgeo-main) untouched. **The dashboard change is the owner's to make** — flag it explicitly at
handoff; the image cannot build until it lands. Reuse the multi-stage pattern from
`services/agri-data-service/Dockerfile:1-62` (uv, locked sync, `--no-dev` runtime) rather than
inventing a second Python base.

### 3. Files you need that the boundary table does not grant you

| File | Why you need it | Recommendation |
|---|---|---|
| `services/agri-data-service/src/agri_data_service/cli.py` | register the seven `ingest-*` verbs | Lane K owns it, but K is **wave 3** — no concurrent writer. Keep your edit to `from agri_data_service.ingest.commands import register_ingest_commands` plus one call, with all logic in `ingest/`. Announce the edit. |
| `services/agri-data-service/config.py` | question 1 | unowned; announce |
| `services/agri-data-service/pyproject.toml` + `uv.lock` | the Redis client (T8) | unowned; `uv lock` regenerates deterministically. Announce. |
| `docs/deployment.md:385-400`, `docs/env-vars.md:60` | both describe the curl trigger and `CRON_SECRET` | doc-only; safe, but report the edit |

### 4. USGS `updatedAt` wall-clock fallback (T5)

**Recommendation:** skip gauges with no current reading rather than minting a clock-derived id, and
record the change in `ingest/AGENTS.md`. This is a behaviour change, not a pure port — get it
acknowledged rather than deciding it silently.

### 5. Does `ingest-all` fan out concurrently?

The TS uses `Promise.all` (`ingestion-jobs.ts:407`). Six concurrent upstream fetches from one
container is fine, but the weather job already fans out up to 150 Open-Meteo requests internally
(`ingestion-jobs.ts:283-285`, cap at `:27`).

**Recommendation:** run the six jobs sequentially in the container (simpler failure attribution, and
the hourly budget is ample), keep the weather job's internal fan-out bounded as today, and preserve
per-job isolation so one failure never erases another's progress. Note the deviation from
`Promise.all` in your report — it is a deliberate difference, not a missed behaviour.

---

## 8. Execution log

### Launched 2026-08-03 — workflow `wf_41869a8a-31f`

**Lane A was not actually landed.** `lane-A-identity-contract.md` carried `status: in-progress`
since an earlier session, but nothing had been written: the directory
`services/agri-data-service/src/agri_data_service/ingest/` did not exist, so neither did
`identity.py` nor `tests/test_ingest_identity.py`. §2's prerequisite check returns `False`, not
`True`. Lane A was therefore folded into this run as a blocking wave 0 — lane D's phases do not
start until the identity contract and its golden-file test exist.

**Verification target is production, by owner instruction.** `switchback.proxy.rlwy.net:37967/plantgeo`
(the DSN in `services/agri-data-service/.env`), with `INGEST_BBOX=-125,42,-111,49`. This is DML
only into `geo.features` / `geo.drought_areas` — the same writes the hourly cron already performs.
The inherited "never run a migration against production" rule still holds absolutely: this lane
makes zero schema changes.

Two gaps the prod run will hit: `NASA_FIRMS_KEY` is empty in every local env file, and
`INGEST_BBOX` is unset — hence supplying the PNW box explicitly.

### Owner decisions on the open questions

| # | Question | Decision |
|---|---|---|
| 1 | Cron container DB URL | **Widen the existing loader validator** rather than add `INGEST_DATABASE_URL`. Only the host/port/role allowlist widens; scheme, empty-query and database-name guards stay. Residual risk accepted and flagged: §7 warns the validator is deliberately narrow and other lanes depend on that. |
| 2 | Railway build context | Unchanged from the brief's recommendation — Dockerfile written for repo-root context; **Root Directory → `/` is the owner's dashboard change** and the image cannot build on Railway until it lands. |
| 4 | T5 USGS wall-clock fallback | **Port as-is.** Gauges with no current reading keep the clock-derived id. Recorded in `ingest/AGENTS.md` as design-of-record, with the consequence stated plainly (the `water-gauges` layer grows every hour) and a per-run count of fallback gauges as the metric to watch. |
| 5 | `ingest-all` concurrency | **Sequential**, per the brief's recommendation. Weather keeps its internal bounded fan-out. Per-job isolation preserved. |

### Deviation from §6's method: one sweep, at the end

Per owner instruction, no agent runs tests, linters, type-checks or builds during implementation.
Every gate in §6 — golden-file, ruff/mypy, pytest, the prod idempotence and exit-code proofs,
realtime, the Next.js sweep after the deletions, and the container build — runs once in a single
dedicated verification phase after all code is written and the TypeScript path is deleted.

### Second, concurrent execution 2026-08-03 — workflow `wf_4f98c325-5fa`

A separate session ran lane D again, in parallel with `wf_41869a8a-31f` above, against the same
working tree. **Both runs' output is now merged on disk and the full sweep is green**, but the
duplication is real and the merge was never independently reviewed. The two runs converged on
open question 1 without coordinating: this run detected that the loader validator had already been
widened in `config.py` / `db/engine.py` (outside its boundary) and therefore did **not** add a
competing `INGEST_DATABASE_URL` setting.

Sweep after both runs: ruff format 149 files clean, `ruff check` all passed, mypy clean over 67
files, pytest **645 passed / 26 skipped / 0 failed**, `npm run build` + `lint` + `type-check`
clean, `/api/cron/ingest` absent from the generated route table.

One real defect this run fixed: `ingest-all` emitted 3 stdout lines instead of 2 because
structlog's default sink is `sys.stdout`, so `_run_all`'s `realtime_publish_totals` INFO line
landed in the same stream as the per-job JSON summaries. `commands.py`'s logger is now bound to
stderr.

**Status is `partial`, not `complete`.** Outstanding:

| # | Item | Owner |
|---|---|---|
| 1 | **Railway cron service Root Directory must move from `/infra/cron-ingest` to `/`** (Dockerfile path `infra/cron-ingest/Dockerfile`, config path `infra/cron-ingest/railway.json`). The Dockerfile needs repo-root context and **cannot build on Railway until this lands.** | owner dashboard |
| 2 | Container image never built (`podman build -f infra/cron-ingest/Dockerfile .`) | verification |
| 3 | Production idempotence proof (delta exactly 0 per layer, captured in the same transaction) and the forced-failure exit-code proof — both need a live write run, which was withheld under the read-only production rule | owner approval |
| 4 | End-to-end realtime proof (subscribe `layer:fire-detections`, one message per written row). `test_ingest_realtime.py` only proves correct RESP against a loopback stand-in | verification |
| 5 | `CRON_SECRET` no longer needed by the cron service; it now needs `LOCAL_SOURCE_LOADER_DATABASE_URL` and optionally `REDIS_URL`, `INGEST_BBOX`, `NASA_FIRMS_KEY`. **`INGEST_BBOX` is unset in every env file — without it every bbox job reports `skipped` (exit 0, not a failure).** | owner |
| 6 | `redis>=5.0,<6` was added to `pyproject.toml` and locked, but `realtime.py` speaks RESP directly over `asyncio.open_connection` and imports nothing from it. `ingest/AGENTS.md` asserts both and contradicts itself. Drop the dependency or reimplement on `redis.asyncio` — do not leave both | follow-up |
| 7 | Dead/stale after the TypeScript deletion, all outside this lane's boundary: `src/lib/server/security/ingress.ts:72` `authorizeCronRequest` lost its only caller; `docs/deployment.md:385-400` and `docs/env-vars.md:60` still document the curl trigger and `CRON_SECRET`; prose references in `src/lib/server/AGENTS.md:108`, `src/lib/map/layers.ts:71`, `src/lib/server/services/environmental-read-model.ts:215` | follow-up |
| 8 | `src/lib/server/services/nasa-firms.ts` carries an **uncommitted** VIIRS constellation upgrade from another agent (SNPP/NOAA20/NOAA21, `bright_ti4`, `Promise.allSettled`). The behaviour is carried forward in `ingest/firms.py`, but that diff is now orphaned — decide keep or revert | owner |

---

### Production outage 2026-08-04 20:01Z–23:37Z — and its true root cause

**What happened.** The TypeScript cron route was deleted and pushed before the Python cron
container could build, so scheduled ingestion simply stopped. Last insert `20:01:45Z`; the
21:01, 22:01 and 23:01 ticks all did nothing. This is exactly the handoff risk recorded above
("the image cannot build until the Root Directory change lands"), realised.

**The diagnosis in the earlier handoff was wrong, and the wrong fix was retried four times.**
It read as "`RAILWAY_DOCKERFILE_PATH` didn't take because redeploy reuses build config." It was
never going to take, on any build, fresh or not: the **repo-root `railway.json` explicitly declares
`"build": {"dockerfilePath": "Dockerfile"}`** (the Next.js web app), and Railway's config-as-code
**overrides** the environment variable. Every build — including a genuinely fresh one triggered at
`23:38Z` (`d05ba2af`) — built the web app and died on its tile-URL gate
(`NEXT_PUBLIC_PMTILES_URL must be a reviewed production URL`).

`infra/cron-ingest/railway.json` had carried the correct `build.dockerfilePath` all along. The
service just was not reading that file.

**What does not work, verified rather than assumed:**

| Attempt | Result |
|---|---|
| `RAILWAY_DOCKERFILE_PATH` variable | Ignored — root `railway.json` wins |
| `RAILWAY_CONFIG_PATH` variable | Not honoured; fresh build still used the root Dockerfile |
| `railway service` CLI | No `update`/`settings` subcommand — only list/delete/link/source/status/logs/redeploy/restart/scale/files |
| `railway up` | Blocked: `services/agri-data-service/.pytest_cache` is ACL-poisoned. `takeown` and `icacls` both fail with Access Denied **for the owner**; `.railwayignore` does not help because the indexer stats before filtering |

**The fix is the dashboard Config-as-code path** → `infra/cron-ingest/railway.json`. Once the owner
set it, the build immediately switched to the correct Dockerfile.

**Second failure, once the right Dockerfile was building:**
`"/services/agri-data-service/alembic.ini": not found`. The file exists on disk but **was never
`git add`ed**, so it is absent from the GitHub build context while every other COPY resolved.

Fixed by **deleting** the `alembic/`, `db/` and `alembic.ini` COPY lines rather than committing the
file — which also closes the quality reviewer's hardening finding. The cron container must never run
a migration; omitting the machinery makes that true *by construction* instead of by trusting the
`ENTRYPOINT` string. Safe because `cli.py:19` imports the alembic *package* (a locked dependency),
while `alembic.ini` is read only inside `_alembic_config()` (`cli.py:266-267`), which only the
migration verbs call. `ingest-all` never touches it.

**Interim mitigation.** A manual `agri-cli ingest-all` against production at `23:36Z` closed the gap
and did considerably more than that — it ran the three producers this track had built but never
executed against production, and self-healed every orphan:

| Source | Result |
|---|---|
| `sentinel2-ndvi` | 1,549 written |
| `nws-sensors` | 590 written |
| `evacuation-zones` | 381 written |
| `open-meteo` | 94 written |
| `wfigs-fire-perimeters` | 14 written |
| `usdm-drought` | 0 written (already current) |
| `geometry-repair` | **793 of 793 confirmed — orphan backlog cleared** |
| `nasa-firms` | failed — `NASA_FIRMS_KEY` not set |
| `usgs-streamflow` | failed — upstream timeout (transient) |

**Standing lesson for this track.** Do not delete a producer's only live path in the same push that
introduces its replacement, unless the replacement has been observed running in its target
environment. The lane's own §6 required a container build proof and got one *locally*; a local
`podman build` cannot prove a Railway build, because Railway's build config lives outside the
repository.

**Third failure — the settings come in a PAIR, and only one was changed.** After the
config-as-code path landed, the build used the right Dockerfile but failed on
`"/services/agri-data-service/src": not found` (the `pyproject.toml`/`uv.lock` COPY errored too).
The build context contained no `services/` directory, i.e. **Root Directory was still
`/infra/cron-ingest`**. Both settings are required and neither is reachable from the CLI:

| Setting | Required value |
|---|---|
| Root Directory | `/` |
| Config-as-code path | `infra/cron-ingest/railway.json` |
| (Dockerfile path — redundant once config-as-code is set) | `infra/cron-ingest/Dockerfile` |

The Dockerfile deliberately builds from the repository root, because the image needs
`services/agri-data-service/{pyproject.toml,uv.lock,src/}` — a Root Directory of
`/infra/cron-ingest` cannot see them. That is the constraint recorded in §7 open question 2 from
the day the brief was written; it has now cost three separate failed builds because the two
settings were changed one at a time.

### USDM retention — a live landmine at the seam between two changes

The two-year (in practice four-year) history backfill and the weekly job's prune are on a
collision course, and neither change on its own is wrong:

- `ingest/usdm.py` prunes to `DROUGHT_RETAINED_RELEASES` after **every** run, default
  `DEFAULT_RETAINED_RELEASES = 8`.
- `MAX_RETAINED_RELEASES` was raised `52 -> 120` so a backfill *can* be protected. Raising the
  ceiling does not arm it — the default is still 8.
- Nothing set the variable. `docs/env-vars.md:113` documents the hazard; no service configured it.

Set `DROUGHT_RETAINED_RELEASES=110` on `plantgeo-ingest-cron` (2026-08-05, `--skip-deploys`) so the
first successful cron tick does not silently prune the backfill back to 8 releases.

**Unresolved ceiling.** The backfill reached 95 releases spanning `2022-08-09`..`2026-07-28` and was
still climbing. If the final count exceeds **110**, the next `ingest-drought` run trims to 110; if it
exceeds **120**, `MAX_RETAINED_RELEASES` clamps it and the variable cannot protect the history at all.
Check the final release count, then either raise the variable (<=120) or raise the constant. A
four-year weekly series is ~208 releases, which the current cap cannot hold.

This also applies to the new per-layer `infra/cron-drought/` service (`0 14 * * 4`) — it runs the
same prune and needs the same variable set when it is created.

**Ceiling resolved 2026-08-05.** The backfill did not stop near 104. It walked to
`2022-08-09`..`2026-07-28` — 172 releases and still advancing, heading for roughly 208 (four years
of weekly releases). Both earlier caps were therefore below what the backfill actually produces,
which is the dangerous shape: `MAX_RETAINED_RELEASES` **clamps the tunable**, so a cap set too low
means *no* value of `DROUGHT_RETAINED_RELEASES` can protect the history.

Resolution: `MAX_RETAINED_RELEASES` `120 -> 280` (five years with headroom) and
`DROUGHT_RETAINED_RELEASES=260` on `plantgeo-ingest-cron`. `test_ingest_usdm.py` now asserts
`MAX_RETAINED_RELEASES >= 208` against a literal as well as the symbol, so a future edit that drops
the cap below a full backfill fails the suite instead of silently deleting history — this constant
has regressed twice.

Measured cost: **421 MB for 172 releases, ~2.5 MB per release** of stored geometry. The lane brief's
"~19 MB each" is the *upstream payload*, not what lands in `geo.drought_areas` — a ~7x difference
that makes full retention affordable. 280 releases is roughly 700 MB, the deliberate price of
"we persist and serve everything we present" and a slider whose depth is whatever was ingested.

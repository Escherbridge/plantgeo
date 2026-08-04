---
type: lane-index
track: ingestion_warehouse_consolidation_20260803
status: active
---

# Parallel lane plan

Created 2026-08-03 so phases 1 and 3–7 can run in several sessions at once. Each lane
file in this directory is a self-contained brief for a zero-context session: open a new
session, point it at the lane file, and it has everything it needs.

**Read this index before starting any lane.** The file boundaries below are what keep
concurrent sessions from colliding, and lane A is a hard prerequisite for three others
(B, D and E).

## The one real hazard

`ingest/identity.py` (lane A) defines the namespaced `natural_key`. `geo.geometry`
(lane B) stores it. If those are written independently they *will* diverge — a
`toFixed(4)` versus `f"{x:.4f}"` difference is enough — and under a Type-2 dimension a
mismatched key does not merge two rows, it **interleaves two producers into one version
chain**, producing a plausible-looking history that is fiction and far harder to detect
than a duplicate. That is the single failure this whole dimension exists to prevent.

So: **lane A ships first, alone.** It is small (~0.5 session). Everything else waits on
its key format, not on its code.

## Wave plan

```
WAVE 0  (blocking, ~0.5 session)
  A  identity contract ......... ingest/identity.py + golden-file test

WAVE 1  (all parallel, no shared files)
  B  geo.geometry DDL + backfill ..... Drizzle / geo
  D  six Python ingest modules ....... agri CLI
  E  MTBS ingest ..................... easiest of phase 5
  F  raster sources to R2 ............ soil/terrain/NLCD/LANDFIRE/NDVI
  G  time-slider front end ........... against a typed contract, no schema
  H  browser QA of the shipped UI ..... no schema, tiny diffs
  S  soil, the serving path ........... carved 2026-08-03 from "no soil data";
                                        serving half only, lane F owns acquisition

WAVE 2  (each waits on one wave-1 lane)
  C  Alembic repoint of agri FKs ...... after B (needs geo.geometry to exist)
  I  operational provenance ........... after D (phase 4)

WAVE 3
  J  geo.metric_daily + covariates v2 + Monte Carlo ... after B, C, G
  K  ML variant + CLI publisher ....................... after J
```

## File boundaries — pre-declared and disjoint

No two lanes in the same wave touch the same file. Stay inside your lane's list; if you
need something outside it, stop and report rather than reaching across. **Cite this table
by lane letter, not by line number** — it gets edited.

Paths are repo-relative. `<py>` abbreviates
`services/agri-data-service/src/agri_data_service`.

| Lane | Owns | Must not touch |
|---|---|---|
| **A** | `<py>/ingest/identity.py`, `<py>/ingest/__init__.py` (empty), `<py>/ingest/AGENTS.md` (creates), `services/agri-data-service/tests/test_ingest_identity.py` | everything else |
| **B** | `src/lib/server/db/schema.ts`, `src/lib/server/db/migration-contract.ts`, `src/lib/server/db/AGENTS.md`, `drizzle/**`, `scripts/backfill-geometry.*`, `src/__tests__/lib/geometry-migration.test.ts` | `services/agri-data-service/**`, `src/lib/server/services/ingestion-jobs.ts`, `scripts/raster/**` |
| **C** | `services/agri-data-service/alembic/versions/*_0019_*.py`, `services/agri-data-service/db/agri/**` (regenerated), `<py>/models/**` | `src/**` at repo root, `drizzle/**` |
| **D** | `<py>/ingest/*.py` **except `identity.py` (A) and `mtbs.py` (E)**, `<py>/ingest/AGENTS.md` (appends; A created it), `services/agri-data-service/tests/test_ingest_*.py` **except `test_ingest_identity.py` (A) and `test_ingest_mtbs.py` (E)**, `infra/cron-ingest/**`; deletes `src/app/api/cron/ingest/route.ts`, `src/lib/server/services/ingestion-jobs.ts`, `src/__tests__/api/cron-ingest.test.ts`, `src/__tests__/services/ingestion-jobs.test.ts` | `src/lib/server/db/**`, `drizzle/**`, `services/agri-data-service/db/agri/**`, `scripts/**`, `infra/tiles/**` |
| **E** | `<py>/ingest/mtbs.py`, `services/agri-data-service/tests/test_ingest_mtbs.py`, `services/agri-data-service/tests/fixtures/mtbs/**` | every other `ingest/*.py`, `<py>/ingest/AGENTS.md` (hand your paragraph to lane D) |
| **F** | `scripts/raster/**`, `scripts/deploy-pmtiles.sh`, `infra/tiles/**`, `data/**` | `src/**`, `services/agri-data-service/**`, `infra/cron-ingest/**`, `scripts/backfill-geometry.*`, everything else under `scripts/` |
| **G** | `src/types/time-slider.ts`, `src/stores/**`, `src/components/map/TimeSlider.tsx`, `src/components/map/LayerManager.tsx`, `src/components/panels/**`, `src/__tests__/stores/**`, `src/__tests__/components/**`; **deletes** `src/components/ui/time-slider.tsx` | `src/lib/server/db/**`, `drizzle/**`, `src/lib/server/services/environmental-read-model.ts`, `src/components/map/MapView.tsx`, `src/components/map/Legend.tsx` (both are hand-offs) |
| **H** | small fixes only, reported before applying, under `src/app/**`, `src/components/**` (excluding lane G's paths), `src/styles/globals.css` and sibling `AGENTS.md` files | any schema file, `src/__tests__/**`, `src/types/**`, `src/stores/**`, `src/components/panels/**`, `src/components/map/TimeSlider.tsx`, `src/components/map/LayerManager.tsx`, `src/components/ui/time-slider.tsx`, `infra/**`, `docker-compose.yml` |
| **S** | `src/components/map/layers/SoilLayer.tsx`, `src/lib/server/services/{soilgrids,usda-soil,carbon-potential,usle}.ts`, `src/app/api/ingest/soil/route.ts`, `src/lib/server/trpc/routers/environmental.ts` (**soil procedures only**) | `src/components/panels/**` and `src/stores/**` (G), `src/components/map/LayerManager.tsx` (G), `scripts/**` + `data/**` + `infra/**` (F), `src/lib/server/db/schema.ts` + `drizzle/**` (B) |
| **I** | `<py>/ingest/provenance.py` + its test, `<py>/ingest/AGENTS.md` (appends) | `services/agri-data-service/db/agri/**` |
| **J** | `drizzle/**`, `src/lib/server/db/migration-contract.ts`, `services/agri-data-service/alembic/versions/*_0020_*.py`, `src/lib/server/services/environmental-read-model.ts`, `src/lib/server/trpc/routers/environmental.ts` | lane G's components and stores |
| **K** | `<py>/cli.py`, model training modules | `drizzle/**` |

### Shared files that no single lane owns

Three paths are wanted by more than one lane. The arbitration is fixed here so nobody has
to negotiate mid-flight:

| Path | Rule |
|---|---|
| `<py>/ingest/AGENTS.md` | Lane **A** creates it with the `identity.py` paragraph. Lane **D** is the only wave-1 lane that may append. Lane **E** hands its paragraph to the orchestrator instead of writing. |
| `<py>/cli.py` | Owned by lane **K** (wave 3), so there is no concurrent writer in wave 1. Lane D may add a one-line `register_ingest_commands` import + call, and must announce it. Lane E must not touch it. |
| `services/agri-data-service/config.py`, `pyproject.toml`, `uv.lock` | Unowned. Lane **D** only, and only after announcing the edit to the orchestrator. |
| `src/lib/server/trpc/routers/environmental.ts` | Lane **J** owns it, but J is wave 3 — no concurrent writer. Lane **S** may edit the **soil procedures only** and must announce it, so lane J rebases rather than reverts. Lane G must not touch it at all. |
| `src/components/map/LayerManager.tsx` | Lane **G** owns it outright as of the 2026-08-03 ruling. Lanes **H** and **S** read it and report; neither edits. |

## Rules every lane inherits

These are the traps that already cost time on this track. They are not optional.

- **Never run a migration against production.** `services/agri-data-service/.env` points at
  production, so every alembic/pytest command needs an explicit `$env:DATABASE_URL_SYNC`
  or `$env:AGRI_TEST_DATABASE_URL` in the *same* PowerShell statement. `VAR=x cmd` does
  not set a variable in PowerShell.
- **A Drizzle migration must update `src/lib/server/db/migration-contract.ts` in the same
  commit**, or `/api/ready` fails its hash check and Railway kills the release.
- **`.env.local:11` `DATABASE_URL` points at PRODUCTION** (the Railway proxy), and
  `drizzle.config.ts:8` reads `process.env.DATABASE_URL`. So `drizzle-kit` and
  `scripts/migrate.mjs` run from the repo root target **production by default** — the same
  trap as `services/agri-data-service/.env`, one directory up and previously undocumented.
  Set `$env:DATABASE_URL` explicitly, in the same PowerShell statement, every time.
- **Never run `npm run db:generate`.** `drizzle/meta/` holds snapshots for `0000`–`0004` and
  `0006` only — `0005` and `0007` were hand-authored and never snapshotted. `drizzle-kit`
  would diff against a stale snapshot and emit **destructive DDL** for objects it thinks are
  missing. Hand-author the migration `.sql`, matching the style of `0005`/`0007`. This applies
  to lanes B and J, which both add a Drizzle migration.
- **Never hardcode a row count.** Ingestion runs on a cron, so any number written into a
  test or a document is stale within hours. Capture the baseline inside the same transaction.
- **`geo.features.created_at` is "last touched", not "first seen"** — the refresh path
  rewrites it. Never derive a first-observation time, `data_available_at`, or slider depth
  from it.
- **A revision that drops an object must first inline its DDL into every earlier revision
  that loads it**, and teardown drops of triggers/constraints must be `IF EXISTS`. See the
  track plan for why — this broke the chain twice on 2026-08-03.
- **Assume a governed constraint does not work until you have watched it reject the bad
  case.** Two in this schema silently covered nothing while passing every format check.
- **Run the test sweep once, at the end** — not test→fix→test.
- **Empty map layers: check the AGENTS.md before "fixing", but do not treat a stub as sacred.**
  The standing rule was "empty layers are deliberate governance stubs, not bugs" — several are
  (`demand-heatmap` is documented as parked in `src/components/map/AGENTS.md`; the `sensors`
  style layer was removed on purpose). The owner **narrowed that on 2026-08-03**: role gating is
  no longer a blocker, aggregate output is fine, and a stub that exists only for governance
  red tape should be opened rather than preserved. So: identify *why* the layer is empty and
  say which it is — **DELIBERATE** (documented, keep and surface the reason), **BROKEN** (fix),
  or **NO_DATA** (nothing ingested; degrade honestly). What has not changed is the invariant
  underneath: an empty feed must never be silently indistinguishable from a toggle being off,
  and unavailable data must stay visibly unavailable rather than getting a substitute value.

## Environment

- Local Postgres containers are all **stopped** (nothing removed, volumes intact). Restart
  only what your lane needs — see the track plan's environment section for which is which
  and for the pg16 verification-container recipe.
- `psql` is at `C:\Program Files\PostgreSQL\16\bin\psql.exe`, options **before** the
  connection string. Host port mappings are crossed: run `SHOW server_version` before
  trusting any local port.

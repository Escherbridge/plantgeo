---
type: lane-brief
track: ingestion_warehouse_consolidation_20260803
lane: E
status: in-progress
depends_on: A
started_at: 2026-08-03
---

# Lane E — MTBS burn severity ingest

Read [`lanes/README.md`](README.md) first (wave plan, file boundaries, inherited rules).
Phase 5 context: [`plans/ingestion-warehouse-consolidation-2026-08-03.md`](../../../../plans/ingestion-warehouse-consolidation-2026-08-03.md) §4 "MTBS burn severity (D3)".
Settled decision D3: [`spec.md`](../spec.md):29.

**Two `src/` trees exist in this repo.** `<repo>/src` is the Next.js app; `<repo>/services/agri-data-service/src` is the Python package. Every path below is repo-relative and disambiguated.

## 1. Goal

A single Python module, `services/agri-data-service/src/agri_data_service/ingest/mtbs.py`, fetches the MTBS burn-severity perimeter service **completely** — paged, never truncated — over the PNW bbox, partitioned one capture per annual release, and normalises each fire into a record carrying a namespaced `natural_key` (`mtbs:<Fire_ID>`), a polygon geometry, and a `data_available_at` derived from the **annual release publication date**. The module is pure: it fetches, validates, normalises and emits; it writes no database rows. It also declares the governed `agri.data_source` identity for MTBS, including the Esri AGOL hosting terms, as a module constant that a later lane hands to the warehouse. When this lane is done the live truncation bug at `src/lib/server/services/mtbs.ts:51` has a proven-correct replacement, and no downstream model can be poisoned by an ignition-date-shaped `data_available_at`.

## 2. Prerequisites

| # | What | Verify | Expected |
|---|---|---|---|
| 1 | Lane A has landed `ingest/identity.py` | `Test-Path C:\Users\atooz\Programming\plantgeo\services\agri-data-service\src\agri_data_service\ingest\identity.py` | `True` |
| 2 | You know lane A's public key-builder surface | from `services/agri-data-service`: `uv run python -c "from agri_data_service.ingest import identity; print([n for n in dir(identity) if not n.startswith('_')])"` | a list containing `build_burn_severity_identity`, `FeatureIdentity` and `MissingNativeKeyError`. **`uv run`, not bare `python`** — the package lives in a uv-managed venv (`services/agri-data-service/Dockerfile:33-36` runs every gate through `uv run`) |
| 3 | Lane D has **not** already created `ingest/mtbs.py` | `Test-Path ...\ingest\mtbs.py` | `False` — if `True`, stop and report (see trap 7) |

At the time this brief was written, `services/agri-data-service/src/agri_data_service/ingest/` **does not exist** — lane A creates it. If prerequisite 1 fails, set this brief's `status: blocked` and stop; do not write your own identity builder.

**No database, no container, no migration is needed for this lane.** Do not start Postgres. Do not run Alembic. Do not set `AGRI_TEST_DATABASE_URL`.

## 3. Files you own

From [`lanes/README.md`](README.md) §"File boundaries", lane E row.

| Path | State |
|---|---|
| `services/agri-data-service/src/agri_data_service/ingest/mtbs.py` | new |
| `services/agri-data-service/tests/test_ingest_mtbs.py` | new (repo convention is `tests/test_*.py`, flat — there is no fixtures directory today) |
| `services/agri-data-service/tests/fixtures/mtbs/` | new, optional — only if inline JSON literals in the test become unreadable |

**Other sessions are running concurrently against this repo.** Touch nothing else. In particular do not edit `cli.py`, `execution/contracts.py`, `execution/source_ingestion.py`, any other `ingest/*.py`, `<repo>/src/**`, `drizzle/**`, or `services/agri-data-service/alembic/**`. Importing from another module is fine; editing it is not. If you need a change outside your list, stop and report it.

**`ingest/AGENTS.md` is explicitly not yours.** Lane A created it and lane D is its only wave-1
writer (`README.md` §"Shared files that no single lane owns"). Write the MTBS paragraph you would
have added — the `data_available_at` rule, the paging/truncation contract, and why an unknown
severity code raises — into your handoff report, and let lane D land it. Editing that file
concurrently with lane D is the one way this lane can corrupt someone else's work.

## 4. The work

1. **Create `ingest/mtbs.py`.** Pure module: no `AsyncSession`, no ORM import, no writes. Precedent for the shape — bounded async HTTP capture with a `main()` entry point — is `services/agri-data-service/src/agri_data_service/execution/geospatial_capture.py` (client construction at `:338`, `main()` at `:728`).

2. **Module constants.**
   - `MTBS_FEATURE_SERVICE_QUERY_URL` — the same endpoint the TS uses, `src/lib/server/services/mtbs.ts:21-22`.
   - `PACIFIC_NORTHWEST_BBOX = (-125.0, 42.0, -111.0, 49.0)` — matches `src/__tests__/services/ingestion-jobs.test.ts:3`.
   - `SEVERITY_CLASS_BY_CODE` — copy the codes verbatim from `src/lib/server/services/mtbs.ts:13-19` (1 unburned, 2 low, 3 moderate, 4 high, 5 increased_greenness). Do not renumber.
   - `MTBS_ANNUAL_RELEASE_DATES: Mapping[int, date]` — ignition year → that release's publication date. Hand-maintained, each entry commented with where the date came from. See open question 1.

3. **Declare the governed source identity** as a module constant `MTBS_SOURCE_DEFINITION`, shaped as `execution.source_ingestion.SourceDefinition` (`services/agri-data-service/src/agri_data_service/execution/source_ingestion.py:42-57`). Import the class; do not edit that file.
   - `key="mtbs-burn-severity"` (must match `^[a-z0-9][a-z0-9-]{1,98}$`, `:47`).
   - `license_name` — **this is where the AGOL terms get recorded.** `publish_source_release` copies it verbatim into `source_release.license_snapshot` (`source_ingestion.py:297`), and the column is `varchar(255)` (`services/agri-data-service/db/agri/tables/data_source.sql:14`). Name both instruments inside 255 chars: the underlying USGS/USFS MTBS product (normally US federal public domain) **and** the fact that we obtain it through an Esri-hosted ArcGIS Online feature service whose hosting terms are separate. Longer wording goes in `purpose`.
   - `base_url` = the query URL. `retention_days=None` — every annual release is kept forever (plan §4: *"keep every annual release"*).
   - `license_url` — verify the MTBS program URL live before writing it; do not guess a URL into a governed column.
   - `reviewed_at` / `reviewed_by` are required by the model (`:56-57`) and are operator-supplied, not hardcoded.

4. **Page the fetch.** `resultRecordCount` alone is the bug: `src/lib/server/services/mtbs.ts:51` sends `resultRecordCount: "500"` with no `resultOffset`, so any bbox with more than 500 fires is silently truncated. Replace with a real pager.
   - Mirror the existing query shape (`mtbs.ts:42-52`: `geometry`, `geometryType=esriGeometryEnvelope`, `inSR=4326`, `spatialRel=esriSpatialRelIntersects`, `outSR=4326`, `f=geojson`), then add `resultOffset` and **`orderByFields=Fire_ID`**.
   - `orderByFields` is mandatory. ArcGIS paging without a deterministic sort can repeat or skip rows between pages, and neither `mtbs.ts` nor `src/lib/server/services/wfigs-fire-perimeters.ts:29` (same host family) pages today, so there is no in-repo precedent to copy.
   - Consider `outFields=*` rather than the five fields at `mtbs.ts:48` — this lane persists the raw payload, and dropping columns at fetch time is unrecoverable.

5. **Detect truncation with three independent signals. All three, because any one alone is unreliable.**

   | Signal | How | Rule |
   |---|---|---|
   | **a. Authoritative count (primary gate)** | issue the same `where` + `geometry` query with `returnCountOnly=true` **before** paging; record `count` | after paging, `len(features) == count` or raise and exit non-zero |
   | **b. `exceededTransferLimit`** | the service sets it on a truncated response | truthy ⇒ keep paging. **Absent ⇒ inconclusive, not "complete"** — which is why (a) is the gate |
   | **c. Full-page heuristic** | a page returning exactly `resultRecordCount` features | keep paging; stop only on a short page *and* falsy `exceededTransferLimit` |

   Also dedupe by `Fire_ID` across pages and assert zero duplicates — a duplicate proves the ordering was unstable.

6. **Partition by annual release, not one all-time pull.** `fetch_release(ignition_year)` sets `where = Ig_Year = <year>`. Three reasons, all verified:
   - The plan keeps every annual release as a distinct versioned product (§4, "Retention").
   - Each release has its own `data_available_at`; a mixed payload cannot carry one honest value.
   - The repo's only existing publication path caps a payload at 5 000 000 bytes (`execution/contracts.py:53`, enforced at `:708-709`) and 5 000 features (`:54`, `:718`). The plan's estimate for the PNW all-time share is ~3-5 k polygons at ~5-20 MB — i.e. a single pull sits on top of both caps. Per-release captures stay comfortably under.

7. **`data_available_at` — the reason this lane exists.**
   ```python
   def resolve_data_available_at(ignition_year: int) -> datetime:
       """Annual release publication date; never Ig_Date, which leaks ~18 months."""
   ```
   - Look up `MTBS_ANNUAL_RELEASE_DATES`. A year not in the table **raises**. There is no fallback: not `Ig_Date`, not `now()`, not `Ig_Year + 18 months`.
   - Assert the resolved value is strictly later than the cohort's `max(Ig_Date)`, with a floor of ≥180 days' separation as a tripwire.
   - Apply the plan's batch-runner rule (risk 4): reject a release whose `data_available_at` is within 60 s of `now()`.
   - `observed_from` / `observed_to` = the cohort's min/max `Ig_Date`. Those are *when it happened*; `data_available_at` is *when we could have known*. Both are distinct columns on `agri.source_release` (`services/agri-data-service/db/agri/tables/source_release.sql:12-14`).

8. **Normalise each feature** into a typed record (pydantic or dataclass — the package already depends on pydantic v2):
   - `natural_key` built through **lane A's builder** with producer `mtbs` and local id `Fire_ID`. Never `f"mtbs:{fire_id}"` inline — one module owns identity strings (plan §3).
   - A feature with a missing or blank `Fire_ID` **raises**. Never synthesise a key from coordinates or a payload hash (plan §2.0 backfill, "Forward-looking requirement").
   - `geom_kind='polygon'`, `producer='mtbs'`.
   - `release_identifier` — the Type-2 change-detection signal. MTBS revises perimeters between releases under a stable `Fire_ID`, so version on the release identifier and **never compare geometry floats** (plan §2.0 rule 1; risk 1b).
   - Severity: an unknown or absent code **raises**. Do not copy `mtbs.ts:78`'s `?? "unburned"` — see trap 3.

9. **Emit, do not persist.** Write per release: the raw payload GeoJSON and a `SourceIngestionPlan`-shaped sidecar (`source_ingestion.py:110-119`) carrying `MTBS_SOURCE_DEFINITION` and the resolved `SourceReleasePlan`. Default output root is `settings.local_execution_root` (`services/agri-data-service/src/agri_data_service/config.py:134`, `.agri-local-runs`). See open question 3 for why persistence is out of scope here.

10. **Write `tests/test_ingest_mtbs.py`, DB-free.** Cases:
    - three recorded pages reassemble into one complete set, correct offsets, zero duplicate `Fire_ID`;
    - `returnCountOnly` says N but paging yields N−1 ⇒ raises;
    - final page carries `exceededTransferLimit: true` ⇒ raises;
    - `resolve_data_available_at` returns the release date and is **not** equal to any `Ig_Date` in the cohort; an unlisted year raises;
    - an unknown severity code raises rather than becoming `unburned`;
    - a feature with no `Fire_ID` raises;
    - `natural_key` is exactly `mtbs:<Fire_ID>` and is produced by lane A's builder.

## 5. Traps

Lane-specific only; the generic rules are in [`README.md`](README.md) §"Rules every lane inherits".

| # | Trap | Evidence |
|---|---|---|
| 1 | **Path corrections to the brief that spawned this lane.** `Fire_ID` really is at `src/lib/server/services/mtbs.ts:48` (the `outFields` line) — cited correctly. But the plan cites `mtbs.ts:50` for the truncation; the actual `resultRecordCount: "500"` is at **`mtbs.ts:51`** (line 50 is `f: "geojson"`). Use 51. |
| 2 | **`Ig_Date` as `data_available_at` leaks ~18 months** into every model that consumes MTBS, and it is invisible — the forecast just looks good. Risk 4, `plans/ingestion-warehouse-consolidation-2026-08-03.md`:792; D3, `spec.md`:29. |
| 3 | **`SEVERITY_MAP[severityCode] ?? "unburned"` at `src/lib/server/services/mtbs.ts:78` fabricates data.** `unburned` is a real class (code 1), so an absent or unrecognised code silently becomes a legitimate-looking observation. Raise instead. |
| 4 | **The existing `source-ingest` CLI cannot ingest MTBS.** `validate_phase_one_geojson_payload` rejects any geometry that is not a `Point` — `execution/contracts.py:725-726`, *"only Point observations are accepted in this phase-one slice"*. MTBS is polygons. Do not try to wire this lane into `cli.py:955`; widening that contract touches files you do not own. |
| 5 | **`publish_source_release` inlines the payload into the database.** `storage_class="database_inline"`, `content_bytes=payload` at `execution/source_ingestion.py:336-338` — directly against the plan's ">1 MB goes to R2" rule for a 5-20 MB MTBS release. Second reason not to call it from this lane. |
| 6 | **The loader database is unreachable from this machine.** `config.py:141-169` hard-pins `LOCAL_SOURCE_LOADER_DATABASE_URL` to `postgresql+asyncpg://plantgeo_loader@127.0.0.1:5442/plantgeo`, and the track plan records that the pg16 warehouse on `:5442` answers TCP but drops every host connection (`podman exec` only) — [`plan.md`](../plan.md):130-136, :163-165. A DB-writing design here would be untestable. |
| 7 | **Lane boundary with lane D — resolved, but verify anyway.** An earlier revision of [`README.md`](README.md) granted lane D `ingest/*.py (except identity.py)`, a glob that nominally swallowed `mtbs.py`. The boundary table now excludes `mtbs.py` and `test_ingest_mtbs.py` from lane D explicitly. Lane D's brief may still be running from an older read, so if `ingest/mtbs.py` already exists when you start, **stop and report — do not merge.** |
| 8 | **Payload caps sit right on the expected volume.** `MAX_SOURCE_GEOJSON_BYTES = 5_000_000` and `MAX_SOURCE_GEOJSON_FEATURES = 5_000` (`execution/contracts.py:53-54`) vs. a ~3-5 k-polygon, 5-20 MB all-time PNW pull. Per-release partitioning (step 6) is not an optimisation, it is what keeps captures legal. |
| 9 | **Do not let this test become a skipping db test.** `pytest_sessionfinish` sets `exitstatus = 1` if *any* `agri_db` test skips while `AGRI_TEST_DATABASE_URL` is set ([`plan.md`](../plan.md):184-186). Keep `tests/test_ingest_mtbs.py` free of the `agri_db` fixture entirely. |
| 10 | **AGOL paging is unordered by default.** No in-repo precedent pages this host family — `mtbs.ts` does not, and `src/lib/server/services/wfigs-fire-perimeters.ts:29` does not either. `orderByFields=Fire_ID` plus the cross-page duplicate assertion is your only protection. |
| 11 | **`license_name` is the license snapshot, and it is 255 chars.** `source_ingestion.py:297` copies it verbatim to `source_release.license_snapshot`; the column is `varchar(255)` (`db/agri/tables/data_source.sql:14`). Write the AGOL statement to fit; overflow goes to `purpose`. |
| 12 | **A governed source is immutable once written.** `publish_source_release` raises `"source key is already governed by different source metadata"` if any of ten fields later differs (`source_ingestion.py:258-273`). Get `license_name` and `citation` right the first time — a later reword is a migration, not an edit. |

## 6. Definition of done

Run from `C:\Users\atooz\Programming\plantgeo\services\agri-data-service`. One sweep, at the end.

Every command goes through `uv run` — the package lives in a uv-managed venv and the Docker
`checks` stage invokes it that way (`Dockerfile:33-36`). Bare `python -m ruff` will not resolve.

```powershell
uv run ruff format --check src/agri_data_service/ingest/mtbs.py tests/test_ingest_mtbs.py
uv run ruff check         src/agri_data_service/ingest/mtbs.py tests/test_ingest_mtbs.py
uv run mypy               src/agri_data_service/ingest/mtbs.py
uv run pytest tests/test_ingest_mtbs.py -q
```

Proof of success:

| Command | Output that proves it |
|---|---|
| `ruff format --check` | `1 file already formatted` / no reformat listed |
| `ruff check` | `All checks passed!` (config: `ruff.toml`, line-length 120, `ANN` and `DTZ` enabled — annotate everything, and every datetime must be timezone-aware) |
| `mypy` | `Success: no issues found` |
| `pytest tests/test_ingest_mtbs.py -q` | all tests passed, **0 skipped** |

Then the sweep the Docker `checks` stage runs, to prove you broke nothing
(`Dockerfile:33-36`):

```powershell
uv run ruff format --check src tests
uv run ruff check .
uv run mypy src
uv run pytest -q             # AGRI_TEST_DATABASE_URL must NOT be set
```

Expected: ruff/mypy clean; pytest green with db-backed tests skipping (normal when the env var is unset).

**These three commands sweep the whole tree, and lanes A and D are writing in it.** A failure
inside `ingest/identity.py`, `ingest/writer.py`, any other `ingest/*.py`, or a `tests/test_ingest_*.py`
that is not yours is **not yours to fix** — report it and re-run scoped to your two files. Only a
failure in `ingest/mtbs.py` or `tests/test_ingest_mtbs.py` blocks this lane.

Finally, a one-off live smoke that proves the truncation is actually gone. It hits the network; run it once and paste the numbers into your report:

```powershell
uv run python -m agri_data_service.ingest.mtbs --bbox -125,42,-111,49 --release-year <year>
```

Proof: the module prints the `returnCountOnly` total and the paged feature count, and they are equal. Then run an all-time pull across the release years in your table and confirm the total is **> 500** — that number is the bug at `mtbs.ts:51` being fixed.

## 7. Open questions

| # | Question | Recommendation |
|---|---|---|
| 1 | **What are the annual release publication dates?** `MTBS_ANNUAL_RELEASE_DATES` is the single most load-bearing constant in this lane and it cannot be derived from the feature service. | Hand-maintain the mapping, comment each entry with its source, and `raise` on an unlisted year. **Escalate to the owner rather than approximating.** A guessed `Ig_Year + 18 months` written into a persisted `data_available_at` is exactly risk 4 with extra steps. |
| 2 | **Does the feature service expose a per-fire release/version field?** It would be the ideal Type-2 change-detection signal. `mtbs.ts:48` requests only five fields, so the repo does not know. | Query the layer metadata (`<layer-root>?f=json`) once, record the field list as a test fixture, and use a real release field if one exists; otherwise fall back to the `Ig_Year` cohort. Report which you found. |
| 3 | **Does this lane persist to Postgres, or stop at the capture?** | **Stop at the capture.** Polygons are rejected by the only existing publication path (trap 4), that path would inline a 5-20 MB blob (trap 5), the loader DB is unreachable here (trap 6), and `geo.geometry` (lane B) plus the `agri` FK repoint (lane C) have not landed — `cell_source_crosswalk.cell_id` (`db/agri/tables/cell_source_crosswalk.sql:10`) is itself being repointed to `geometry_id`. Emit payload + sidecar; hand persistence to lane I (phase 4). |
| 4 | **Where does the `ingest-mtbs` CLI verb get registered?** `cli.py` belongs to lane K ([`README.md`](README.md) §"File boundaries", lane K row), not to you. | Expose `async def ingest_mtbs(...)` plus a `main()` in `mtbs.py` (precedent: `execution/geospatial_capture.py:728`) and let the `cli.py` owner add the `@cli.command("ingest-mtbs")` wrapper. Report the intended signature in your handoff. |
| 5 | **MTBS licensing under Esri AGOL hosting terms** — deferred by the owner under D3; plan open question 5 (`plans/…`:966), risk 13 (`:802`). | Record both instruments in `license_name`, leave `allowed_client_exposure=False` (already the default at `source_ingestion.py:248`), and **publish nothing MTBS-derived to the public CDN in this lane.** Persisting rows privately is low exposure; a public tile publish is a different and larger one — escalate before that step. |
| 6 | **Should the first pull carry an `Ig_Year` floor?** The plan offers one as optional (§4, "Bounding"). | **No floor.** The all-time PNW share is ~3-5 k polygons (small), and "years since burn" is a proposed covariate (plan §5a) that wants full history. Per-release partitioning already bounds each individual capture. |

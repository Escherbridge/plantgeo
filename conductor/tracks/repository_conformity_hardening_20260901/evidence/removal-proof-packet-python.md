---
type: track-evidence
track: repository_conformity_hardening_20260901
slice: c3
status: one_removal_four_retained_with_named_blockers
observed_at: 2026-09-02
base_commit: ad4e015
---

# Removal proof packet — Python side (`services/agri-data-service`)

Scope: the Python candidates only. The TypeScript candidates are in
`removal-proof-packet.md`. Every verdict below is a static scan across imports, `importlib`,
`pyproject.toml` entry points, Dockerfiles, `railway*.json`, `infra/**`, `scripts/**` and the docs
tree, followed by a named replacement or a named blocker. A search result creates a candidate; it
never creates deletion authority.

## Verdict summary

| candidate | verdict | one-line reason |
|---|---|---|
| `whichnull.py` | **REMOVED** | zero consumers of any kind, never copied into any image |
| `src/agri_data_service/planes/` | **RETAINED — blocker named** | six point/containment behaviours have no `parquet_ops` counterpart |
| `execution/hot_projection.py` | **RETAINED — blocker named** | two docs still mandate the Railway hot projection it types |
| `execution/public_evaluation_lineage.py` | **RETAINED — owner named** | the executed loader behind a recorded production lineage write |
| `services/agri-data-service/docker-compose.yml` | **RECONCILED, not deleted** | README names it a supported HTTP-only dev stack; only its Martin pin was stale |
| `s3fs`, `redis` dependencies | **REMOVAL-READY, not removed** | lock regeneration and image smoke cannot run in this environment |

---

## 1. REMOVED — `services/agri-data-service/whichnull.py`

An unreferenced hard-coded debug print script.

| consumer class | result |
|---|---|
| Python imports (`import whichnull`, `from whichnull`) | zero, repo-wide |
| `pyproject.toml` `[project.scripts]` | only `agri-service = "agri_data_service.interface.cli:cli"` |
| Dockerfiles | `services/agri-data-service/Dockerfile` copies `src/ alembic/ db/ alembic.ini` (and, new in `c1`, `tests/ scripts/` into the quality-receipt stage only). `whichnull.py` sat at the service root and matched none of them. `infra/job-executor/Dockerfile` likewise. |
| `railway*.json`, `infra/**`, `scripts/**`, `*.sh` | zero |
| tests / fixtures | zero |
| documentation | mentioned only as a removal candidate: `conductor/RUNBOOK.md:401` and this track's `metadata.json:114` |

The only other hits are inside `.mpg/*.json`, which is the `scrt` mind-palace search cache — an
index of files, not a consumer.

**Canonical replacement:** none is owed. Its behaviour was `print`ing which columns a tier
derivation nulls, which is one interactive call to
`agri_data_service.warehouse.parquet.tiers.tier_derivation`.

---

## 2. RETAINED — `src/agri_data_service/planes/` (12 modules, 2,754 lines)

**The audit's premise is correct and its conclusion does not follow.** `planes/` really is imported
only by tests (`conductor/RUNBOOK.md:5443` records the zero-caller finding, and it still holds: the
only importers are the twelve `tests/parquet/test_*_serving.py` files plus the layer-lattice test's
own rule table). But the deletion condition set for this candidate was "if ALL public behaviours are
covered by `parquet_ops`" — and they are not.

### What `parquet_ops` does cover

`parquet_ops.serving.resolve_day / resolve_window / resolve_release / day_status_sets /
read_absence_evidence` plus `parquet_ops.warehouse_reader.DuckDbRowReader` give a layer-agnostic
day, window and release row read for every registered lane, and `interface/http/parquet_routes.py`
mounts exactly those four routes (`read_day`, `read_window`, `read_release`, `read_coverage`). That
supersedes the *row-read* half of `planes/`: `read_vegetation_window`, `read_fire_detections_window`,
`read_water_gauges_observed/forecast`, `read_weather_observations_window/day`,
`read_fire_perimeters_day`, `read_sensors_readings`, `read_signal_time_window` and friends.

### What it does not cover — the exact uncovered behaviours

`parquet_ops`'s entire spatial vocabulary is a **bounding box**.
`warehouse_reader.spatial_support` returns one of three shapes —
`PointSupport` (a `BETWEEN` range predicate on a longitude/latitude column pair),
`GeometrySupport` (WKB clipped to the envelope) or `NoSpatialSupport` — and `_predicate` /
`_clipped_scan` implement only those. There is no point-in-polygon containment, no natural-key
geometry lookup and no severity ranking anywhere in the package:

```
grep -rniE "huc12|contains_point|point_in|most_severe|mupolygonkey" src/agri_data_service/parquet_ops/
→ only comments about WKB columns; zero implementations
```

Six behaviours therefore have **no counterpart**:

| uncovered behaviour | file |
|---|---|
| `lookup_watershed_by_huc12`, `find_containing_watersheds`, `point_is_within_watershed_geometry`, `decode_polygon_rings` | `planes/watersheds.py:213,243,350,292` |
| `soil_survey_at_point`, `find_soil_survey_at_point`, `read_soil_survey_by_mupolygonkeys`, `wkb_polygon_contains_point` | `planes/soil_survey.py:219,190,172,339` |
| `most_severe_class_at_point` (severity ranking over covering polygons) | `planes/drought.py:205` |
| `resolve_burn_severity_as_of` (as-of resolution across release days) | `planes/burn_severity.py:130` |
| `resolve_evacuation_zones_as_of`, `classify_evacuation_zones_coverage` | `planes/evacuation_zones.py:188,100` |
| `sensors_plane_coverage` (month-partition coverage, distinct from `parquet_ops.coverage`'s day census) | `planes/sensors.py:128` |

The nearest thing in the tree to a point lookup is `agent/tools.py:268`'s
`drought_history_at_point.sql`, which is a **PostgreSQL** query, not a Parquet read — so it is not a
Parquet-side replacement either.

**Named blocker:** delete `planes/` only after `parquet_ops` (or a successor) owns point-in-polygon
containment, HUC12/mukey natural-key lookup and as-of release resolution, and the six behaviours
above have a proven contract-parity counterpart. Deleting the package now would delete the only
implementation of behaviours the TypeScript side is concurrently wiring — the working tree at the
time of this scan has `src/__tests__/services/watershed-soil-survey.test.ts` open under edit.

**Consequently retained too:** the twelve plane-only tests
(`tests/parquet/test_{burn_severity,drought,evacuation_zones,fire_detections,fire_perimeters,sensors,signal,soil_survey,vegetation,water_gauges,watersheds,weather_observations}_serving.py`).
They are the only executable proof those six behaviours still work.

---

## 3. RETAINED — `execution/hot_projection.py` + `tests/test_hot_projection_contract.py`

Pure Pydantic contract: `HotProjectionWindow`, `HotProjectionManifest`, `HotProjectionPointer`,
`build_hot_projection_manifest`, `rolling_hot_projection_window`,
`prepare/apply_hot_projection_pointer_advance`. 299 lines, no writer, no reader.

| consumer class | result |
|---|---|
| imports | `tests/test_hot_projection_contract.py` only |
| CLI verbs / `[project.scripts]` | zero |
| routes, `jobs/`, `app.py` | zero |
| Dockerfiles, `railway*.json`, `infra/**` | zero |
| docs referencing its **output** | **two, and both are prescriptive** |

`docs/historical-backfill-runbook.md:183-186` — "Railway receives a separate hot projection
containing only the most recent 365 days of source observations plus forecast outputs; its
**selection boundary, release identity, source receipt manifest, and row counts** must be verified
before it advances a Railway pointer." Those four nouns are exactly this module's four types.
`docs/sql-forecasting-framework.md:236` calls it "the existing hot-projection manifest … a selection
contract only".

There is also a live guard that names it: `tests/test_forecasting_migration_contract.py:22` asserts
`"hot_projection" not in migration`, i.e. that the forecasting revision creates no hot-projection
state. That test would still pass after a deletion, but its subject would become unnameable.

**Named blocker:** the Railway promotion-gate policy in `docs/historical-backfill-runbook.md` still
mandates a hot projection and this is its only typed expression. Retire the policy first, in the
same change, or retain the module. The track spec's own out-of-scope line applies: an artefact is not
dead because it has no import edge.

---

## 4. RETAINED — `execution/public_evaluation_lineage.py` + `tests/test_public_evaluation_lineage.py`

| consumer class | result |
|---|---|
| imports of it | `tests/test_public_evaluation_lineage.py` |
| imports **by** it | `execution/public_evaluation_rehash.py` (separately owned, separately tested by `tests/test_public_evaluation_rehash.py`), `execution/provenance.py`, `db/engine.py`, `models/provenance.py` |
| external operator consumer | **yes** |

`conductor/retros/model_delivery_public_evaluation_20260726/lineage-receipt-2026-08-14.md:14`
records the exact invocation `uv run python -m agri_data_service.execution.public_evaluation_lineage`,
and `evidence-matrix-2026-08-14.md:32` records what that run wrote to production: 1 `data_source`,
1 `source_release`, 1 validated `release_set`, 1 `release_set_item`, 3 artifacts.

**Named owner:** the `model_delivery_public_evaluation_20260726` retro. This module is the only
executable reproduction of a production lineage write whose SQL sibling
(`plantgeo-retain-ghisaconus-vetted-2026-08-14.sql`) is recorded as vetted-but-blocked because
`pg_read_binary_file` cannot reach a remote server. Deleting it discards the reproduction path for
rows that exist in production today.

---

## 5. RECONCILED — `services/agri-data-service/docker-compose.yml`

The audit row read "stale Martin pin and obsolete Redis wiring". One half is right.

**Stale Martin pin — fixed.** The file pinned `ghcr.io/maplibre/martin:v0.14` while
`Dockerfile.martin:1` and the root `docker-compose.yml:54` both deploy `1.10.1`. That is a different
config-file generation: a tile bug reproduced locally would not have been the deployed one. Now
`ghcr.io/maplibre/martin:1.10.1`.

**"Obsolete Redis wiring" — the audit conflated two different things, and this packet corrects it.**
The Redis *container* is not obsolete: `src/agri_data_service/ingest/realtime.py:48-50` reads
`REDIS_URL` **at call time** and `:154-156` opens a raw RESP connection with
`asyncio.open_connection`, so a local Redis is a real backing service for the realtime ingest path.
What is unused is the **`redis` PyPI package** (§6). The compose `redis` service stays.

**Not deleted, and the root compose is not a substitute.** `README.md:94-115` documents this file as
the deliberately-warned-about *HTTP-service-only* dev stack — "it does still work for `make dev`
against a toy database" — and names `infra/local-warehouse` (Postgres 16 + TimescaleDB + PostGIS +
pgvector on `127.0.0.1:5442`) as the supported ingestion target, while the root `docker-compose.yml`
stands up the whole PlantGeo application stack. Three compose files, three distinct jobs; none is
redundant. Both `infra/martin/config.yaml` and `infra/db/init-extensions.sql`, which this file
mounts, exist.

---

## 6. REMOVAL-READY, NOT REMOVED — `s3fs` and `redis`

Both are direct `pyproject.toml` dependencies with zero direct imports. Neither is removed here:
regenerating `uv.lock` and running the image smoke are outside this environment's authority
(no `uv sync`, no network, no image build).

`s3fs>=2024.6` (`pyproject.toml:32`) — zero `import s3fs` / `from s3fs` anywhere in `src/`, `tests/`
or `scripts/`. Object-store access goes through `boto3` (`pipeline/parquet/objectstore.py`) and
DuckDB `httpfs` (`parquet_ops/duckdb_session.py`). Caveat to check during removal: `s3fs` is a
common *transitive* enabler for `polars`/`pyarrow` `s3://` URI handling; the smoke below must open a
real `s3://` scan, not merely import the packages.

`redis>=5.0,<6` (`pyproject.toml:22`) — zero `import redis` / `from redis`. `ingest/realtime.py`
speaks RESP directly over `asyncio.open_connection` by design.

Exact commands for whoever holds lock and image authority:

```bash
cd services/agri-data-service
uv remove s3fs                      # or: uv remove redis  -- one class at a time
uv lock                             # regenerate; commit uv.lock in the same change
uv sync --locked --all-extras       # the ONLY sanctioned sync
uv run --no-sync python scripts/check.py           # four gates
uv run --no-sync python -c "import polars as pl; pl.scan_parquet('s3://<bucket>/<a real key>').head(1).collect()"
docker build -f services/agri-data-service/Dockerfile services/agri-data-service
docker build -f infra/job-executor/Dockerfile .
uv run --no-sync agri-service ops jobs-pulse --help   # operator smoke on the executor image path
```

Note the ordering trap: `scripts/check.py --write-receipt` must run **after** `uv.lock` changes,
because `uv.lock` is a digest input. A dependency removal that does not refresh
`QUALITY_RECEIPT.json` will fail both image builds at the `quality-receipt` stage.

**Removed 2026-09-03.** Re-confirmed zero `import s3fs`/`from s3fs`/`import redis`/`from redis`
in `src/`, `tests/`, `scripts/`, and zero direct `fsspec` usage. Checked the s3fs caveat above by
reading `polars_storage_options()` (`pipeline/parquet/objectstore.py:1048-1055`): it returns
`aws_endpoint_url`/`aws_region`/`aws_access_key_id`/`aws_secret_access_key`, the key set Polars'
native Rust `object_store` cloud reader consumes directly — the function's own docstring says
"Polars/object_store connection options", not fsspec. Every `pl.scan_parquet(uris,
storage_options=...)` call site (`planes/burn_severity.py:126`, `planes/water_gauges.py:90,119,127`,
`planes/fire_perimeters.py:83`, `planes/drought.py:163`) goes through that same native path; reads
also go through DuckDB's own `httpfs` extension via `SET s3_*` pragmas
(`parquet_ops/duckdb_session.py:239-243`), and writes go through `boto3` directly
(`pipeline/parquet/objectstore.py:421-426`). The caveat does not block removal — `s3fs` is
genuinely dead weight, confirmed by what came out with it: `uv remove --no-sync s3fs redis` (from
`services/agri-data-service/`, resolved in 5.25s) dropped `s3fs` and `redis` themselves plus
`s3fs`'s exclusive transitive closure — `aiobotocore`, `aiohappyeyeballs`, `aiohttp`,
`aioitertools`, `aiosignal`, `frozenlist`, `fsspec`, `propcache`, `pyjwt`, `wrapt`, `yarl` — 526
lines removed from `uv.lock` and zero lines added, so no other pinned version moved. `pyproject.toml`
lost only the two `redis>=5.0,<6` and `s3fs>=2024.6` dependency lines. Per the environment's
mandatory contract, `uv sync`/bare `uv run` were **not** run (would have stripped `pytest`), and
`scripts/check.py --write-receipt`, `mypy`, `ruff`, `pytest`, and both `docker build`s were
deliberately **not** run here — those belong to the separate sweep that must run after `uv.lock`
changes and will refresh `QUALITY_RECEIPT.json`.

---

## 7. Defect surfaced by the c1 gate extension (not a removal)

Extending `mypy` to `scripts/` found a script that **could not import at all**:

```
scripts/build_soil_moisture_from_canonical_snapshot.py:35: error: Cannot find implementation or
library stub for module named "agri_data_service.warehouse.schemas.soil_field_moisture"
```

`warehouse/schemas/soil_field_moisture.py` was split into three per-depth modules
(`soil_field_moisture_{0_7cm,7_28cm,28_100cm}.py`) and its `SOIL_FIELD_MOISTURE_STREAMS` mapping was
not re-homed, leaving a dangling import in an operator script that three
`tests/test_snapshot_builder_contracts.py` cases assert against by AST but never execute. Repaired by
sourcing each stream name from the schema object that owns it
(`SOIL_FIELD_MOISTURE_0_7CM_SCHEMA.name`), which removes the duplicated literal rather than restoring
it. Verified by import, producing the identical three stream names:

```
['soil-field-moisture-0-7cm', 'soil-field-moisture-7-28cm', 'soil-field-moisture-28-100cm']
```

---

## 8. Structural debt handed to `c2` — the CLI thin-adapter violation list

`tests/test_layer_import_contract.py` now carries a bounded AST rule for `interface/cli/**`
(`test_cli_is_a_thin_click_adapter`, `xfail(strict=True)`) plus a pin
(`test_cli_adapter_violations_stay_pinned`, `CLI_ADAPTER_VIOLATION_COUNT = 26`). Deliberately
reported, not fixed — the extraction is `c2`'s. All 26 are in one file:

- `interface/cli/commands.py:610` owns a transaction boundary `session.begin()`
- `interface/cli/commands.py:724` owns a transaction boundary `session.begin()`
- `interface/cli/commands.py:795` owns a transaction boundary `session.begin()`
- `interface/cli/commands.py:970` owns a transaction boundary `session.begin()`
- `interface/cli/commands.py:1053` owns a transaction boundary `session.begin()`
- `interface/cli/commands.py:1164` owns a transaction boundary `session.begin()`
- `interface/cli/commands.py:1570` owns a transaction boundary `combined_local_engine().begin()`
- `interface/cli/commands.py:1846` owns a transaction boundary `session.begin()`
- `interface/cli/commands.py:1940` owns a transaction boundary `session.begin()`
- `interface/cli/commands.py:1949` owns a transaction boundary `session.begin()`
- `interface/cli/commands.py:2037` owns a transaction boundary `session.begin()`
- `interface/cli/commands.py:2155` owns a transaction boundary `session.begin()`
- `interface/cli/commands.py:2159` owns a transaction boundary `session.begin()`
- `interface/cli/commands.py:2218` defines execution machinery `class LaneChunkRunner`
- `interface/cli/commands.py:2231` defines execution machinery `class ChunkedLane`
- `interface/cli/commands.py:2490` owns a transaction boundary `session.begin()`
- `interface/cli/commands.py:2530` owns a transaction boundary `session.begin()`
- `interface/cli/commands.py:2616` owns a transaction boundary `session.begin()`
- `interface/cli/commands.py:2628` owns a transaction boundary `session.begin()`
- `interface/cli/commands.py:2730` owns a transaction boundary `session.begin()`
- `interface/cli/commands.py:2743` owns a transaction boundary `session.begin()`
- `interface/cli/commands.py:2927` owns a transaction boundary `session.begin()`
- `interface/cli/commands.py:2929` owns a transaction boundary `session.begin()`
- `interface/cli/commands.py:2984` owns a transaction boundary `session.begin()`
- `interface/cli/commands.py:2991` owns a transaction boundary `session.begin()`
- `interface/cli/commands.py:3035` owns a transaction boundary `session.begin()`

Four `Protocol`s that belong to the same lane framework are **not** on this list, because the rule
matches class-name suffixes and these do not end in one: `LaneChunk` (2179), `LaneReceipt` (2186),
`LanePlan` (2193), `LaneCheckpoint` (2203). `c2` should move all six classes together; moving only
the two the rule names would leave the framework split across two packages.

When `c2` lands, lower `CLI_ADAPTER_VIOLATION_COUNT` in the same change. At zero the `xfail(strict)`
turns into an XPASS failure, which is the intended signal to delete the marker and let the rule
enforce.

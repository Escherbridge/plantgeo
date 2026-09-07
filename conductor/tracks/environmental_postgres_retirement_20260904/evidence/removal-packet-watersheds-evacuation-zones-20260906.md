---
type: evidence
track: environmental_postgres_retirement_20260904
criterion: 4
slice: watersheds + evacuation-zones Postgres producer removal
status: watersheds_removed_whole; evacuation_zones_partial_with_named_blocker
observed_at: 2026-09-06
base_commit: f115537
---

# Removal proof packet — watersheds and evacuation-zones Postgres path

Acceptance criterion 4: *"The legacy code is deleted, not merely unused. Every Postgres fill command,
its SQL, its lane spec and its tests are removed with a c2-style removal packet proving zero imports.
An orphaned module that nothing calls is not done; it is the next reader's trap."*

Form follows `repository_conformity_hardening_20260901/evidence/removal-proof-packet-python.md`.
**A search result creates a candidate; it never creates deletion authority.** Every verdict below is
a static scan followed by a named replacement or a named blocker.

## The one command, defined once

Every "zero readers" claim in this packet is the same command, run from the repository root
(`c:/Users/atooz/Programming/plantgeo`), over the six trees the brief named — the Python source, its
tests, `scripts/`, `alembic/`, `db/`, and the Next.js app's TypeScript:

```bash
grep -rn "<SYMBOL>" \
  services/agri-data-service/src services/agri-data-service/tests \
  services/agri-data-service/scripts services/agri-data-service/alembic \
  services/agri-data-service/db src \
  --include=*.py --include=*.ts --include=*.tsx --include=*.sql --include=*.md
```

`--include` is what keeps `__pycache__/*.pyc` and the `.mpg/` scrt cache out of the counts; an
un-filtered run of the same pattern reports compiled bytecode as a "reader", which it is not.

**A grep is not an import proof.** Text hits cannot tell a live call from a docstring. The binding
proof is an AST walk, run once over the whole tree after every deletion below:

```bash
UV_NO_SYNC=1 uv run --no-sync python -c "<ast walk of every ImportFrom/Import under src/, tests/, scripts/>"
#  -> BROKEN IMPORTS: none
```

and an actual import of the two modules whose module-level invariants could have been broken:

```bash
UV_NO_SYNC=1 uv run --no-sync python -c "from agri_data_service.execution.job_executor_service import LANE_SPECS; ..."
#  LANE_SPECS count: 61
#  postgres-watersheds present: False
#  parquet-watersheds present: True
#  postgres lanes: ['postgres-evacuation-zones', 'postgres-fire-perimeters', 'postgres-geometry-repair', 'postgres-sensors']
#  verbs: ('ingest-fire-perimeters', 'ingest-sensors', 'ingest-evacuation-zones', 'ingest-mtbs',
#          'ingest-backfill', 'ingest-geometry-repair', 'ingest-all', 'jobs-plan-lane', ...)
```

That import is the strongest available proof for item 8: `job_executor_service.py` carries two
module-level `assert`s that fire at import time, and a successful import IS the statement that both
still hold after the spec deletion.

## Verdict summary

| # | candidate | verdict | one-line reason |
|---|---|---|---|
| 1 | `pipeline/lanes/watersheds.py` | **REMOVED** | zero code readers; only test file + prose |
| 2 | `pipeline/lanes/evacuation_zones.py` | **REMOVED** | same; the one surviving reader is an `importorskip` designed for this |
| 3 | `sql/pipeline/watersheds_day_export.sql` | **REMOVED** | its single `load_query_sql` site was item 1 |
| 4 | `sql/pipeline/evacuation_zones_day_export.sql` | **REMOVED** | its single `load_query_sql` site was item 2 |
| 5 | `ingest/watersheds.py`: `run_watersheds_ingestion_job`, `build_watershed_write`, `WATERSHEDS_SOURCE` | **REMOVED** (module survives) | only readers were item 7's verb and their own tests |
| 6 | `ingest/evacuation_zones.py`: `run_evacuation_zones_ingestion_job`, `build_evacuation_zone_write` | **RETAINED — blocker named** | **`ingest/runner.py:48` is a live non-test caller.** The brief's premise ("same shape" as watersheds) is wrong |
| 7a | `ingest/commands.py::ingest_watersheds` + registration | **REMOVED** | only readers were the deleted lane spec and its verb-list tests |
| 7b | `ingest/commands.py::ingest_evacuation_zones` + registration | **RETAINED — blocker named** | its job is still live via item 6; cutting the targeted verb while `ingest-all` still runs the job is an operational regression on a life-safety layer |
| 8a | lane spec `postgres-watersheds` | **REMOVED** | invariants verified by import; nothing points at it |
| 8b | lane spec `postgres-evacuation-zones` | **RETAINED — blocker named** | it schedules item 7b, which is retained with item 6 |
| 8c | lane spec `parquet-watersheds` | **STOP — code invariant refuses** | generated per `LaneRegistration`; two module-level asserts and a pinned test forbid it |
| 8d | lane spec `parquet-evacuation-zones` | **STOP — code invariant refuses** | identical |
| 9 | `tests/parquet/test_watersheds_lane.py`, `tests/parquet/test_evacuation_zones_lane.py`, five tests in `tests/test_ingest_watersheds.py` | **REMOVED** | tests that existed only to exercise deleted functions |

**Lines removed: 1,015. Lines added: 258. Net −757**, across 35 files (6 deleted outright).
`git diff --cached --numstat | awk '{a+=$1; d+=$2} END {print a, d}'` → `258 1015`.

---

## 1. REMOVED — `src/agri_data_service/pipeline/lanes/watersheds.py` (80 lines)

The Postgres-reading Parquet exporter for the watersheds lane: `read_watersheds_release` ran
`watersheds_day_export.sql` against `geo.features`, and `export_watersheds_release` sliced the result
into `ROWS_PER_PART = 1_000` part files. Its only caller was the `_fill_watersheds` registry adapter,
which `f115537` had already replaced with a source-direct refusal.

| symbol | pre-deletion hits | non-test code readers |
|---|---|---|
| `export_watersheds_release` | 8 | **0** — 4 prose (`pipeline/direct/AGENTS.md:1270`, `direct/watersheds/forward.py:3`, `direct/watersheds/rows.py:80`, `lanes/soil_survey.py:143`), 1 definition, 3 in `tests/parquet/test_watersheds_lane.py` |
| `read_watersheds_release` | 5 | **0** — 2 definition/self-call, 3 in the same test file |
| `agri_data_service.pipeline.lanes.watersheds` (import path) | 3 | **0** — 2 in that test file, 1 the module's own header |

Post-deletion: `export_watersheds_release` 3 hits, `read_watersheds_release` 0. All three survivors
are prose that now says *deleted*.

**What remains.** `pipeline/direct/watersheds/rows.py::watersheds_snapshot_table` writes the same
stream from a direct NHDPlus_HR fetch, and it does NOT copy `ROWS_PER_PART`: `rows.py:25-31` records
that 1,000 rows × ~21,572 B WKB is ~21.5 MB per part, nearly 3× the 8 MiB budget, and uses
`chunk_rows_by_geometry_bytes` instead. The zero-row / `EmptyPartitionError` contract this module's
docstring stated is quoted verbatim into `rows.py:80-85`, because the docstring that held it is gone.

## 2. REMOVED — `src/agri_data_service/pipeline/lanes/evacuation_zones.py` (89 lines)

Same shape: `read_evacuation_zones_snapshot` + `split_into_parts` + `export_evacuation_zones_day`,
loading `evacuation_zones_day_export.sql`, replaced by `pipeline/direct/evacuation_zones/`.

| symbol | pre-deletion hits | non-test code readers |
|---|---|---|
| `export_evacuation_zones_day` | 7 | **0** — 4 prose, 1 definition, 2 in `tests/parquet/test_evacuation_zones_lane.py` |
| `read_evacuation_zones_snapshot` | 6 | **0** — 1 prose (`direct/evacuation_zones/rows.py:186`), 2 definition/self-call, 3 in that test file |
| `agri_data_service.pipeline.lanes.evacuation_zones` | 3 | **0** — see below |

The third row needs naming because it looks like a live reader and is not.
`tests/direct/test_evacuation_zones_direct_products.py:101` holds the module path as a **string**
inside `pytest.importorskip(...)`, and its own docstring says why:

> `SKIPS ONCE THE POSTGRES LANE IS DELETED, deliberately: the restatement exists precisely so this
> package survives that deletion, and a test that hard-imported the removed module would turn the
> successful removal into a red sweep.`

That test now skips instead of failing, which is the behaviour it was written for. It was **not**
deleted, per the brief.

`MAX_ROWS_PER_PART = 200` was restated (not imported) at
`pipeline/direct/evacuation_zones/products.py:31-37` for exactly this reason; that restatement is
what made the deletion a no-op for the direct package.

## 3. REMOVED — `src/agri_data_service/sql/pipeline/watersheds_day_export.sql` (98 lines)

**This deletion was mandatory, not optional.** `tests/test_sql_tree_conventions.py`'s rule (d),
LOADED ONCE, requires *"every file is referenced by exactly one `load_query_sql("...")` call
somewhere under src/"*, and its failure message is literally *"Wire it up at its call site, or delete
the file if it is unused."* Item 1 held the only call site, so leaving the file would have turned a
clean removal into a red sweep.

Pre-deletion `watersheds_day_export` hits: 11 — 1 `load_query_sql` call (item 1), 5 in the file's own
header, 5 cross-references in prose. Post-deletion: 10, all prose, all rewritten to say *deleted*.

Re-verified after the deletion by re-implementing the convention outside pytest:

```
sql files: 195
files with NO load site: []
calls with NO file:      []
files loaded more than once: []
```

**What remains.** The header's clause-by-clause walkthrough was the last written explanation of the
tile-function transcription and the 21,572 B WKB measurement. Both facts were relocated rather than
lost: the predicate set to `pipeline/direct/watersheds/parity.py:42-47` (now explicitly labelled the
last copy), and the byte measurement to `pipeline/direct/watersheds/support.py:46-48`, repointed at
`conductor/RUNBOOK.md:1783-1785` as its only other copy.

## 4. REMOVED — `src/agri_data_service/sql/pipeline/evacuation_zones_day_export.sql` (83 lines)

Same LOADED-ONCE argument; item 2 held its only call site.

Pre-deletion `evacuation_zones_day_export` hits: 9 — 1 `load_query_sql` call, 2 in its own header, 6
cross-references. Post-deletion: 8, all prose.

**The comment the brief singled out.** `pipeline/direct/evacuation_zones/parity.py:72` *transcribes*
this file's four WHERE predicates into `_POSTGRES_PUBLISHED_ZONES_SQL` — it never loaded them — and
already recorded that `lane_watermark_evacuation_zones.sql` had been deleted in `f115537`. It now
records that **both** sources of the transcription are gone, in the same form
`sql/pipeline/lane_watermark_fire_perimeters.sql:24-29` was updated in `f115537`:

> `#: THE PREDICATES WERE TRANSCRIBED from sql/pipeline/evacuation_zones_day_export.sql:79-82. BOTH`
> `#: SOURCES OF THAT TRANSCRIPTION ARE NOW GONE [...] SO THIS IS THE LAST COPY of the four`
> `#: predicates. There is no second definition left to stay identical to: an edit here silently`
> `#: redefines what "the Postgres side of evacuation-zones" means`

`sql/pipeline/fire_perimeters_day_export.sql:32` and `:45` cited this file twice as the live model
for the current-state export shape; both now say it is deleted and that the fire-perimeters file is
the last copy — the identical treatment its watermark sibling got in `f115537`.

## 5. REMOVED — three symbols from `src/agri_data_service/ingest/watersheds.py`; the module survives

Removed: `run_watersheds_ingestion_job` (39 lines), `build_watershed_write` (37 lines),
`WATERSHEDS_SOURCE`, and the four imports that became unused with them (`upstream_client`,
`format_javascript_timestamp`, `UNCONFIGURED_BBOX_REASON`/`resolve_bounded_bbox`,
`IngestionJobResult`/`skipped_result`, `FeatureWrite`, and the `FeatureWriter` type-checking import).
Module: 304 → 223 lines.

| symbol | pre-deletion hits | classification |
|---|---|---|
| `run_watersheds_ingestion_job` | 10 | 1 definition; **1 code reader — `ingest/commands.py:64,180`, deleted as item 7a**; 4 prose; 3 in `tests/test_ingest_watersheds.py` |
| `build_watershed_write` | 11 | 1 definition; 1 self-call inside the deleted job; 5 prose; 4 in `tests/test_ingest_watersheds.py` |
| `WATERSHEDS_SOURCE` | 7 | 1 definition; 2 self-uses in the deleted job; **1 code reader — `commands.py:64,179`, deleted as item 7a**; 2 in tests |

Post-deletion: 8 / 7 / 2 hits respectively, **all prose**, all past-tense.

**`ingest/runner.py` was checked explicitly and does not import any of the three** — it never
ingested watersheds. This is the single fact that separates item 5 from item 6.

**What remains, and why the module must not be deleted.** `pipeline/direct/watersheds/source.py`
imports `fetch_watersheds`, `build_watershed_identity`, `WBDHU12_BOUNDS` and
`WATERSHEDS_PROPERTY_SOURCE` from here; `pipeline/validation/watersheds.py:41-42` imports
`fetch_watershed_object_ids` and `parse_load_date`. Deleting the file would break the lane this
packet is cleaning up after. The module docstring now opens with that fact so the next reader cannot
mistake the survivors for leftovers.

## 6. RETAINED — `run_evacuation_zones_ingestion_job` / `build_evacuation_zone_write`. **The blocker is `ingest/runner.py:48`.**

**The brief's premise is factually wrong and this is the finding.** It says this item has "the same
shape" as item 5 — one caller in `ingest/commands.py`. It has two:

```
services/agri-data-service/src/agri_data_service/ingest/commands.py:46   from ... import EVACUATION_ZONES_SOURCE, run_evacuation_zones_ingestion_job
services/agri-data-service/src/agri_data_service/ingest/commands.py:159      lambda write_features: run_evacuation_zones_ingestion_job(write_features, bbox=bbox),
services/agri-data-service/src/agri_data_service/ingest/runner.py:8      from ... import EVACUATION_ZONES_SOURCE, run_evacuation_zones_ingestion_job
services/agri-data-service/src/agri_data_service/ingest/runner.py:48         (EVACUATION_ZONES_SOURCE, lambda: run_evacuation_zones_ingestion_job(write_features, bbox=bbox)),
```

`ingest/runner.py::run_all_ingestion_jobs` is the `ingest-all` orchestrator, reachable today as
`agri-service data ingest-all` (`ingest/commands.py:357-368`; it is excluded from the *top-level*
command families, which is what `tests/test_cli_contract.py:49` pins, not from the CLI). It is a live,
non-test reader in `src/`. Per the brief's own rule — *a live reader → DO NOT DELETE, report it* —
the whole evacuation-zones producer chain stays.

`build_evacuation_zone_write` is called only from the retained job
(`ingest/evacuation_zones.py:454`), so it is retained with it.

**Why this is a decision and not a deletion.** Cutting it means editing the job list in
`run_all_ingestion_jobs`, which changes what `ingest-all` does. That is a behaviour change to a
production macro, and `runner.py`'s own docstring states the rule it would now be violating:

> `What is left is the three layers that have NO Parquet writer yet -- their generic parquet-*`
> `exporters still read geo.features, so removing their producers would stop the layer rather than`
> `finish its cutover`

Evacuation-zones no longer satisfies that rule — it *does* have a Parquet writer, and
`evacuation-zones-direct-forward` is active. But `geo.evacuation_zone_tiles` still renders this layer
on the map as a style-backed source (`src/components/map/AGENTS.md:287`, *"They stay style-backed
after the Parquet cutover, deliberately"*), so stopping the Postgres producer freezes what the map
draws. That trade belongs to an owner, not to a removal pass.

**Recorded in code, not only here:** `ingest/AGENTS.md` (the "Kept, and why" section) and
`pipeline/direct/AGENTS.md:1316-1322` both now name `ingest/runner.py:48` as the one blocker, so the
next reader finds it before re-attempting the deletion.

## 7. Split verdict on the two CLI verbs

### 7a. REMOVED — `ingest/commands.py::ingest_watersheds` (21 lines) and its registration

Pre-deletion `ingest_watersheds` hits: 4 — the `@click.command` decorator, the function, the
`INGEST_COMMANDS` tuple entry (`commands.py:1173`), and one import. Post-deletion: **0**.

Pre-deletion `ingest-watersheds` (the verb string) hits: 5 — the decorator, the deleted
`postgres-watersheds` spec's `command=` tuple (`job_executor_service.py:517`), `ingest/AGENTS.md:20`,
`tests/test_ingest_runner.py:35`, and a comment in `src/lib/map/layer-registry.ts:253`.
Post-deletion: 8, all prose, all rewritten to say *deleted*. It is not in `_BBOX_SCOPED_VERBS`
(`tests/test_ingest_commands.py:78-82`), so that table needed no edit.

### 7b. RETAINED — `ingest/commands.py::ingest_evacuation_zones`

Its job (item 6) is live. Deleting the targeted verb while `ingest-all` still runs the same job would
leave an operator with only the macro — which also re-runs fire-perimeters, sensors and the geometry
repair — as the way to refresh **a life-safety evacuation layer**. That is a capability regression,
not a cleanup. The verb goes in the same push that removes item 6, and `tests/test_ingest_commands.py:81`
and `tests/test_ingest_runner.py:38` go with it then.

## 8. Lane specs — one removed, one retained, two refused by the code's own invariants

### 8a. REMOVED — `postgres-watersheds` (`job_executor_service.py`, 13-line `_spec(...)` block)

The brief's gate was: *verify the code's own invariants (an activation allow-list entry naming an
unknown lane, a `conflicts_with` pointing at a deleted id, handoff-acknowledgement parsing) before
you cut.* Checked, all four:

| invariant | site | result |
|---|---|---|
| `conflicts_with` must name a live lane | module-level `assert` after `LANE_SPECS` | nothing declares `conflicts_with=("postgres-watersheds",)`; **the assert passes at import** |
| `_FORWARD_SIBLING_LANE_BY_SLUG` slugs must exist | module-level `assert` before `_parquet_spec` | untouched; it maps registration *slugs* to *direct* lane ids, not to `postgres-*` |
| unknown active lane → `ExecutorConfigurationError` | `parse_activation`, `LANE_SPECS.keys()` | production `PLANTGEO_JOB_EXECUTOR_ACTIVE_LANES` does not name it (owner-supplied; see Assumption A1) |
| handoff-acknowledgement parsing | `parse_activation`, `acknowledgements.keys() - LANE_SPECS.keys()` | the spec carried `legacy_owners=()` and `required_handoffs=()`, so it is in no owner's `_lanes_owned_by` tuple and no acknowledgement could name it |

A ledger row for a deleted lane fails **cleanly**, not catastrophically:
`run_scheduled_command` returns `JobHandlerOutcome.failed("unknown_executor_lane", ...)` for a work
item naming an unregistered lane, and `_plan_active_lanes` simply stops planning it.

Import proof: `LANE_SPECS` is 61 (was 62), `postgres-watersheds` absent, the four survivors are
`postgres-evacuation-zones`, `postgres-fire-perimeters`, `postgres-geometry-repair`,
`postgres-sensors`.

### 8b. RETAINED — `postgres-evacuation-zones`

It schedules item 7b, which is retained with item 6. Removing the schedule while keeping the verb and
the job splits one producer across three artifacts with three different reasons — exactly the
half-state criterion 4 warns about. It goes in one push with items 6 and 7b.

### 8c/8d. STOP — `parquet-watersheds` and `parquet-evacuation-zones` cannot be removed

**These are not hand-written specs.** `job_executor_service.py:621-623`:

```python
_PARQUET_SPECS: Final[tuple[LaneExecutionSpec, ...]] = tuple(
    _parquet_spec(registration.slug) for registration in LANE_REGISTRATIONS
)
```

One generic spec exists per `LaneRegistration`. There is no way to remove either lane id without
removing the `LaneRegistration` for `watersheds` / `evacuation-zones` from
`pipeline/parquet/lane_registry.py` — and that registration is the **serving** contract (stream
schema, grain, nature, coverage, watermark), read by `planes/watersheds.py`,
`planes/evacuation_zones.py`, the coverage census and the availability index. Removing it would break
serving, which is the opposite of this track's criterion 2.

Three independent guards would fire first:

1. `job_executor_service.py:986` — `assert not {target for spec in _LANE_SPECS for target in spec.conflicts_with} - LANE_SPECS.keys()`. `watersheds-direct-forward` declares `conflicts_with=("parquet-watersheds",)` and `evacuation-zones-direct-forward` declares `conflicts_with=("parquet-evacuation-zones",)`, so deleting either target **raises at import** — the executor would not start.
2. `job_executor_service.py:429` — `assert set(_FORWARD_SIBLING_LANE_BY_SLUG) <= {registration.slug for registration in LANE_REGISTRATIONS}`, which fires the moment the registration goes.
3. `tests/test_job_executor_service.py:711-716` — `test_every_registered_lane_has_exactly_one_generic_parquet_spec` asserts `parquet_specs == {f"parquet-{r.slug}" for r in LANE_REGISTRATIONS}`.

Both lane ids survive with their adapters already refusing (`f115537`) and `conflicts_with` still
doing the mutual-exclusion work. Verified after item 8a: `parquet-watersheds present: True`.

## 9. REMOVED — the tests that existed only to exercise deleted code

| file / test | lines | why it goes |
|---|---|---|
| `tests/parquet/test_watersheds_lane.py` | 145 | imports `export_watersheds_release` / `read_watersheds_release` at module scope; subject deleted |
| `tests/parquet/test_evacuation_zones_lane.py` | 158 | imports `export_evacuation_zones_day` / `read_evacuation_zones_snapshot` / `split_into_parts` from the deleted module |
| `tests/test_ingest_watersheds.py::test_a_write_carries_observed_at_so_the_read_model_can_date_it` | | `build_watershed_write` |
| `…::test_an_undated_basin_is_written_without_acquiring_a_clock_reading` | | `build_watershed_write` |
| `…::test_a_feature_with_no_huc12_is_rejected_rather_than_keyed_on_nothing` | | `build_watershed_write` |
| `…::test_the_job_skips_rather_than_querying_the_world_without_a_bbox` | | `run_watersheds_ingestion_job` |
| `…::test_the_job_applies_no_record_cap_because_boundaries_are_a_closed_set` | | `run_watersheds_ingestion_job` |
| `…::RecordingWriter` + the `FeatureWrite` type-checking import | | shared only by the five above |

`tests/test_ingest_watersheds.py` went 184 → 113 lines and keeps five tests, all covering the surface
`pipeline/direct/watersheds/` fetches through: the two `parse_load_date` tests, the
`build_watershed_identity` snapshot-key test, and the two `fetch_watersheds` tests (explicit
`objectIds` paging, and the ArcGIS-fault-behind-HTTP-200 refusal). Its module docstring names the
five deleted tests so the loss is visible rather than silent.

**NOT deleted, per the brief:** `tests/direct/test_evacuation_zones_direct_products.py` — see item 2.

Tests repointed rather than deleted (their subject is the executor, not the deleted lane):

- `tests/test_job_executor_service.py:65` — `_EXPECTED_SPEC_COUNT` 62 → **61**, with the reason and the explicit note that the parquet count stays 32.
- `:144-155` — the `postgres-watersheds` legacy-owner/executable assertions become `assert "postgres-watersheds" not in LANE_SPECS` plus a positive assertion that `watersheds-direct-forward` runs the module and `parquet-watersheds` survives.
- `:322-326` and `:1514-1518` — used `postgres-watersheds` as a *representative* lane (no required acknowledgements; daily cadence). Repointed to `watersheds-direct-forward`, which carries the same `legacy_owners=()` and the same 86400 s cadence, so the tests still test something.
- `:688-698` — the phase-mirror assertion read the offset off the Postgres spec. Now literal (`7200 + 3600`), with the deleted lane's own numbers recorded in the comment.
- `:760-766` — `assert "postgres-watersheds" not in activation.active_lanes` would have become vacuously true; repointed to `watersheds-direct-forward` so it still proves an owner-scoped activation drags in no legacy-owner-free lane.
- `tests/test_ingest_runner.py:34-41` — `ingest-watersheds` dropped from `EXPECTED_VERBS`, with the second-wave reason recorded beside the first wave's.

---

## Dangling-pointer repairs (comment-only, no behaviour)

A comment naming a file that no longer exists is the trap criterion 4 describes. Every prose
reference to a deleted artifact was rewritten to past tense and, where the deleted text was the last
written copy of a fact, the fact was relocated and the new site labelled as the last copy:

`pipeline/direct/AGENTS.md:1270,1301,1316` · `direct/watersheds/{__init__,forward,parity,rows,source,support}.py` ·
`direct/evacuation_zones/{__init__,forward,parity,products,rows,source}.py` ·
`pipeline/lanes/soil_survey.py:143` · `pipeline/validation/watersheds.py:64-66` ·
`planes/watersheds.py:47-50` · `warehouse/schemas/watersheds.py:38,74` ·
`sql/pipeline/{drought_release_export,soil_survey_day_export,fire_perimeters_day_export}.sql` ·
`ingest/AGENTS.md` · `ingest/watersheds.py` module docstring ·
`src/lib/server/services/parquet-trpc-readers.ts:1917,2050` · `src/lib/map/layer-registry.ts:253`.

---

## What was NOT removed, and why — the input to the next wave

Ordered by how close each is to being removable.

### R1 — The evacuation-zones Postgres producer (items 6, 7b, 8b). **One owner decision away.**
`run_evacuation_zones_ingestion_job`, `build_evacuation_zone_write`, the `ingest-evacuation-zones`
verb and the `postgres-evacuation-zones` lane form one unit. **Blocker: `ingest/runner.py:48`.** The
decision needed is not about dead code — it is *"may `ingest-all` stop refreshing `geo.features` for
evacuation-zones, freezing `geo.evacuation_zone_tiles` while the direct writer serves the layer from
Parquet?"* Once answered yes, all four artifacts and the tests at `tests/test_ingest_commands.py:81`
and `tests/test_ingest_runner.py:38` delete together in one push.

### R2 — The watersheds `LayerBinding` block. Test-only readers, **kept on precedent.**
`WATERSHEDS_LAYER`, `WATERSHEDS_CHANNEL`, `WATERSHEDS_LAYER_VARIABLE`,
`DEFAULT_WATERSHEDS_LAYER_NAME` and `resolve_watersheds_layer_name` lost their last production reader
when `build_watershed_write` and the job went. Their only remaining readers are
`tests/test_ingest_layer_binding.py:57-62,104-109`.

Kept because that file is **not** a test of the deleted functions — it is the cross-cutting
`LayerBinding` convention suite over all nine producers — and because the previous wave set the
precedent: `ingest/firms.py`, `ingest/open_meteo.py` and `ingest/vegetation.py` all kept their
binding blocks (`firms.py:53-59`, `vegetation.py:301`) after their jobs were deleted on the same day.
Removing watersheds' alone would make this module the odd one out. Revisit when the whole
`LayerBinding` convention is retired, not lane by lane.

### R3 — `parquet-watersheds` / `parquet-evacuation-zones` (items 8c, 8d). **Blocked by design.**
Not removable while the layers are served: the spec is generated from the `LaneRegistration`, and the
registration is the serving contract. The correct end state is what exists now — the spec survives,
its adapter refuses, `conflicts_with` keeps the two writers apart. A future removal is a
`lane_registry.py` change under criterion 2, not an executor change.

### R4 — `pipeline/validation/watersheds.py` (313 lines). **Deliberately caller-less; do not confuse with an orphan.**
Its own docstring: *"Nothing invokes this automatically today (docs/lanes/watersheds.md section 6);
it is a callable…"*. It reads `fetch_watershed_object_ids` and `parse_load_date` from
`ingest/watersheds.py`, which is one of the reasons that module had to survive item 5. Out of scope
here; it is a reconciliation tool, not a Postgres fill path.

### R5 — `geo.features` rows for `watersheds` and `evacuation-zones`. **Not touched, by rule.**
No table was dropped, altered or read. Those need the three-part drop packet from D1 (parity receipt,
zero readers, archived `pg_dump`) and are owner-confirmed. `pipeline/direct/watersheds/parity.py` is
the counted receipt the watersheds drop packet reads first, and its comment now records that zero
Postgres rows is the **expected** end state rather than a wrong-database mistake.

### R6 — `ingest/validation/models.py:156`, `StreamDefinition(stream="watersheds", ...)`.
Describes what `geo.features` still holds for watersheds, for `validate-streams`. Correct while the
rows exist; it carries no `cadence_basis` naming the deleted lane, so nothing went stale. It goes
with R5.

---

## Assumptions

**A1 — production `PLANTGEO_JOB_EXECUTOR_ACTIVE_LANES` does not name `postgres-watersheds`.**
Supplied by the brief and consistent with `pipeline/direct/evacuation_zones/parity.py:29` (*"`postgres-evacuation-zones`
is STOPPED (owner decision 2026-09-04)"*) and the 2026-09-04 owner decision to stop all ten
`postgres-*` lanes. **Not independently verified** — this pass was forbidden Railway, database and
object-store access. If it IS still named, `parse_activation` raises
`ExecutorConfigurationError: unknown active lane(s): postgres-watersheds` and **every** lane fails,
not just that one. Reversal is one line: restore the `_spec("postgres-watersheds", ...)` block.

**A2 — `watersheds-direct-forward` is active in production.** Supplied by the brief (9,396 basins
from NHDPlus_HR, correct no-op). If it is not, the watersheds stream now has no writer at all, since
`parquet-watersheds`'s adapter has refused since `f115537`.

## What this pass did not run

No pytest, no mypy, no `ruff check` — one sweep runs at the end and the owner runs it.
`ruff format` reported *31 files left unchanged*. No Railway, no object store, no database, no
Alembic, no local app run. `QUALITY_RECEIPT.json` untouched. Nothing committed or pushed.

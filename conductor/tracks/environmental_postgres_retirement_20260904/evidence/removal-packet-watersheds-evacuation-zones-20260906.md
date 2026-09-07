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

> **This verdict was discharged on 2026-09-07 — see "Wave 3" appended at the end of this file.** The
> blocker below was correctly named; the owner then made the decision it was waiting for, and the
> `geo.evacuation_zone_tiles` sub-argument was refuted separately
> (`evidence/reader-not-parquet-scope-20260907.md`, section "REFUTED"). Both symbols are now deleted.
> The text below is left exactly as written, because it is the record of what was true on 2026-09-06.

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

> **Discharged 2026-09-07 — see "Wave 3" below.** The verb and its registration are deleted.

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

> **Discharged 2026-09-07 — see "Wave 3" below.** The lane spec is deleted, together with
> `postgres-sensors`, which this 2026-09-06 pass did not examine at all.

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
> **CLOSED 2026-09-07 — see "Wave 3" below.** The owner made the decision; all four artifacts and
> their tests were deleted in one push, and `postgres-sensors` went with them.
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

---

# Wave 3 — appended 2026-09-07: sensors and evacuation-zones Postgres producers REMOVED

`observed_at: 2026-09-07` · `base_commit: ec92226` · `status: sensors_removed; evacuation_zones_removed; fire_perimeters_and_geometry_repair_retained_with_reasons`

This section is an APPEND. Nothing above it was rewritten; items 6, 7b, 8b and R1 each carry a
one-line pointer here, and their 2026-09-06 text stands as the record of what was true that day.

## What changed in the world between the two waves

The 2026-09-06 pass retained evacuation-zones for one named blocker and never examined sensors. Two
production facts moved since, and they are the whole authority for this pass:

1. `evacuation-zones-direct-forward` is ACTIVE and `parquet-evacuation-zones` is retired from the
   active set. Its direct writer was proven against production — 116 zones, verified against the live
   Oregon OEM feed.
2. `sensors-direct-forward` is ACTIVE and `parquet-sensors` is retired. Proving run: **2,361 rows
   written in one part, `outcome: complete`, `availability_extended: 1`** — it extended the published
   availability index directly.

## The rule, restated so it produces the right answer

`ingest/runner.py`'s docstring stated the old rule as *"the layers that have NO Parquet writer yet"*.
That phrasing is about WRITERS and it no longer discriminates. The rule that actually governs, and
that all three waves obeyed, is about **READERS**:

> A PostgreSQL producer may be deleted once its layer's direct writer is ACTIVE **and** the generic
> `parquet-*` exporter that read `geo.features` for that stream is retired from the active set —
> because at that moment the producer is feeding nobody.

`runner.py`'s docstring has been rewritten to say exactly that, in the same voice, and to name WFIGS
as the one source it still keeps and why.

**The 2026-09-06 `geo.evacuation_zone_tiles` sub-argument is REFUTED**, and this pass did not re-adopt
it. "Style-backed" in `src/components/map/AGENTS.md:287` means the style layer is declared in
`styles.ts` rather than component-added; the tile function is unpublished in Martin and named by no
style. See `evidence/reader-not-parquet-scope-20260907.md`, section "REFUTED".

## Method — same command, same standard as the 2026-09-06 pass

Zero-reader claims use the packet's one command (repository root
`c:/Users/atooz/Programming/plantgeo`, six trees, `--include` filtered). Binding proof is the same
AST walk, re-run after every deletion:

```bash
UV_NO_SYNC=1 uv run --no-sync python -c "<ast walk of every Import/ImportFrom under src/, tests/, scripts/>"
#  files walked: 752
#  BROKEN IMPORTS: none
```

and a real import of the module whose module-level `assert`s could have been broken by the spec
deletions:

```bash
UV_NO_SYNC=1 uv run --no-sync python -c "from agri_data_service.execution.job_executor_service import LANE_SPECS; ..."
#  LANE_SPECS 59                         (was 61)
#  postgres lanes: ['postgres-fire-perimeters', 'postgres-geometry-repair']
#  parquet count: 32                     (unchanged — one generic spec per LaneRegistration)
#  verbs: ['ingest-fire-perimeters', 'ingest-mtbs', 'ingest-backfill', 'ingest-geometry-repair',
#          'ingest-all', 'jobs-plan-lane', 'jobs-plan-gaps', 'jobs-run', 'jobs-status',
#          'jobs-reconcile-lane', 'validate-streams']
#  runner has sensors? False False
#  sensors leftovers: []   evac leftovers: []
```

A successful import IS the statement that both module-level asserts still hold — including
`job_executor_service.py:991`, *"a lane declares conflicts_with against a lane id that is not in
LANE_SPECS"*.

## Verdict summary

| # | Item | Verdict | Decisive evidence |
|---|---|---|---|
| W3-1 | `ingest/runner.py` — the two `jobs` entries, their imports, the docstring | **REMOVED + REWRITTEN** | both jobs deleted below; docstring restated on the reader rule and reduced from "three surviving sources" to one |
| W3-2 | `ingest/evacuation_zones.py::run_evacuation_zones_ingestion_job` | **REMOVED** (module survives) | non-test readers were `runner.py:48` and `commands.py:158`, both deleted in this push; **0 remaining** |
| W3-3 | `ingest/evacuation_zones.py::build_evacuation_zone_write` | **REMOVED** (module survives) | sole call site was W3-2, `evacuation_zones.py:454`; **0 remaining** |
| W3-4 | `ingest/sensors.py::run_sensor_ingestion_job` + `_run_sensor_job` | **REMOVED** (module survives) | non-test readers were `runner.py:47` and `commands.py:142`; `_run_sensor_job` had exactly one caller, the job itself; **0 remaining** |
| W3-5 | `ingest/sensors.py::NO_STATIONS_REASON` | **REMOVED** | the skip reason of W3-4 and nothing else; the direct lane has its own bbox gate |
| W3-6 | `ingest/commands.py::ingest_sensors`, `::ingest_evacuation_zones` + registrations | **REMOVED** | verb names verified, not assumed: `ingest-sensors` / `ingest-evacuation-zones` |
| W3-7 | lane specs `postgres-sensors`, `postgres-evacuation-zones` | **REMOVED** | hand-written `_postgres_spec` calls; nothing names either in `conflicts_with`; import passes |
| W3-8 | tests of the deleted functions | **REMOVED**, replaced by executable "cannot come back" proofs | see W3-8 |
| — | `ingest/sensors.py::build_sensor_reading_write` | **KEPT — live reader** | it is `nws_sensor_source().build_feature_write`, which `pipeline/direct/sensors/source.py` runs through `select_writes` on every poll |
| — | `postgres-fire-perimeters`, `postgres-geometry-repair` | **KEPT — live readers** | `parquet-fire-perimeters` still reads `geo.features` because its direct sibling is SHADOW on an open owner decision; the repair maintains the `geo.geometry` that exporter joins |

## W3-1 — `src/agri_data_service/ingest/runner.py`. Two entries gone, docstring rewritten.

Removed: the `NWS_SENSOR_SOURCE` and `EVACUATION_ZONES_SOURCE` entries from the `jobs` list and their
two `from ... import` lines. The list is now WFIGS then the geometry repair.

The module docstring said *"three surviving sources"* and stated a rule that no longer produced that
answer — the exact drift the brief called worse than no docstring. It now says seven sources have
left this list across three dated waves, states the reader rule verbatim, and gives WFIGS its own
paragraph: `fire-perimeters-direct-forward` EXISTS but is SHADOW because it refuses to publish while
any upstream WFIGS perimeter carries invalid geometry, so `parquet-fire-perimeters` is still the
ACTIVE writer and this producer is still its only filler. The geometry-repair paragraph is unchanged
except for its last clause, which now names WFIGS as the source still writing rows that need linking.

## W3-2 / W3-3 — `run_evacuation_zones_ingestion_job` and `build_evacuation_zone_write`

**90 lines removed** from the tail of `src/agri_data_service/ingest/evacuation_zones.py` (466 → 376),
plus the imports only they used: `upstream_client`, `UNCONFIGURED_BBOX_REASON`,
`resolve_bounded_bbox`, `IngestionJobResult`, `skipped_result`, `FeatureWrite`, and the
`TYPE_CHECKING` import of `FeatureWriter`.

Readers before this push
(`grep -rn "\brun_evacuation_zones_ingestion_job\b" --include=*.py src/ tests/ scripts/ alembic/ db/`):

```
src/agri_data_service/ingest/commands.py:46    (import)      -> DELETED in W3-6
src/agri_data_service/ingest/commands.py:158   (call)        -> DELETED in W3-6
src/agri_data_service/ingest/runner.py:8       (import)      -> DELETED in W3-1
src/agri_data_service/ingest/runner.py:48      (call)        -> DELETED in W3-1
tests/test_ingest_commands.py:81                             -> DELETED in W3-8
tests/test_ingest_runner.py:230                              -> DELETED in W3-8
tests/test_ingest_evacuation_zones.py:32,401,413,434         -> DELETED in W3-8
```

`build_evacuation_zone_write` (`grep -rn "\bbuild_evacuation_zone_write\b" ...`): one non-test call
site, `evacuation_zones.py:454`, inside W3-2 itself; tests at
`test_ingest_evacuation_zones.py:24,199`. **Zero non-test readers → delete; readers only in tests of
the deleted thing → both deleted.**

After: **0 executable readers of either.** The 7 and 11 remaining text hits are all prose — the
rewritten module docstring, this packet's own names, historical citations in `pipeline/direct/`, and
the two removal-proof test tuples.

**THE MODULE SURVIVES and its docstring now opens by saying so.**
`pipeline/direct/evacuation_zones/source.py:28` imports `EVACUATION_ZONES_BOUNDS` and
`fetch_evacuation_zones`; `pipeline/direct/evacuation_zones/products.py:18-22` imports
`EVACUATION_ZONES_PRODUCER`, `EVACUATION_ZONES_PROPERTY_SOURCE`, `EVACUATION_ZONES_QUERY_URL`;
`pipeline/validation/evacuation_zones.py:36` imports `EVACUATION_ZONES_BOUNDS` and
`fetch_evacuation_zones` again. Deleting the file would have broken the lane this packet is cleaning
up after.

## W3-4 / W3-5 — `run_sensor_ingestion_job`, `_run_sensor_job`, `NO_STATIONS_REASON`

**58 lines removed** from the tail of `src/agri_data_service/ingest/sensors.py` (673 → 615), plus the
`NO_STATIONS_REASON` constant and the imports only they used: `UNCONFIGURED_BBOX_REASON`,
`resolve_bounded_bbox`, `IngestionJobResult`, `skipped_result`, `select_writes`, and the
`TYPE_CHECKING` import of `FeatureWriter`. `FetchRequest` MOVED from a runtime import into the
`TYPE_CHECKING` block, because after the deletion its only remaining use is the annotation on
`build_sensor_reading_write` — a runtime import used solely in an annotation is what ruff's `TCH`
rules exist to catch, and this repo selects them (`ruff.toml`).

Readers before this push:

```
src/agri_data_service/ingest/commands.py:55    (import)      -> narrowed in W3-6 to `nws_sensor_source` only
src/agri_data_service/ingest/commands.py:142   (call)        -> DELETED in W3-6
src/agri_data_service/ingest/runner.py:10      (import)      -> DELETED in W3-1
src/agri_data_service/ingest/runner.py:47      (call)        -> DELETED in W3-1
tests/test_ingest_commands.py:80, test_ingest_runner.py:229,311, test_ingest_sensors.py:33,264,277
```

`_run_sensor_job`: one caller, `run_sensor_ingestion_job`, twice (`sensors.py:669,671`).
`NO_STATIONS_REASON`: one use, `_run_sensor_job`'s skip at `sensors.py:625`, plus the test that
asserted it. All three: **zero readers after → deleted.**

### What was KEPT in `sensors.py`, and exactly why — the trap the brief warned about

`pipeline/direct/sensors/source.py:56-62` imports **`NWS_OBSERVATION_RETENTION`,
`NWS_SENSOR_SOURCE`, `collect_sensor_records`, `fetch_station_roster`, `nws_sensor_source`**;
`pipeline/direct/sensors/forward.py:57` imports **`NWS_OBSERVATION_RETENTION`, `OBSERVATION_BOUNDS`**.
So the roster walk, the batched poll, the retention constant and the composed source all had live
non-test readers and could not go.

The non-obvious one: **`build_sensor_reading_write` LOOKS like a Postgres-write helper and is not.**
`nws_sensor_source()` passes it as `build_feature_write` (`sensors.py:609` before this edit), and the
direct lane calls `select_writes(nws_sensor_source(now), records, request)`, so the direct writer
executes this function on every poll. Deleting it as "the thing that built FeatureWrites for
Postgres" would have broken the ACTIVE sensors lane. Everything it transitively needs is kept with
it: `build_sensor_reading_identity`, `_required_text`, `_parse_upstream_timestamp`,
`resolve_sensors_layer_name`, `SENSORS_CHANNEL`, `SENSORS_PROPERTY_SOURCE`.

`nws_sensor_source` has a second live reader that is easy to miss: `ingest/commands.py`'s
`_build_backfillable_sources`, which is what makes `agri-service data ingest-backfill --source
nws-sensors` resolve. **The forward verb is gone; the backfill token is not.** That asymmetry is now
stated in that function's docstring.

Runtime smoke of the kept path (not a test run — one call, no database, no network):

```
build_sensor_reading_write(...) -> natural_key 'nws-api:KBOI:2026-08-04T13:00:00+00:00', channel 'layer:sensors'
select_writes(nws_sensor_source(...), [record], request) -> 0 writes, 1 rejected (the record is older
    than the 6-day freshness rule measured against the real clock — correct behaviour, not a regression)
```

## W3-6 — the CLI verbs. Names verified, not assumed.

`grep -n '@click.command' src/agri_data_service/ingest/commands.py` confirmed the two verbs are
literally `ingest-sensors` and `ingest-evacuation-zones`. Removed: both `@click.command` functions
(32 lines), the `evacuation_zones` import line, the two entries in `INGEST_COMMANDS`, and
`NWS_SENSOR_SOURCE` / `run_sensor_ingestion_job` from the sensors import — which narrows to
`from agri_data_service.ingest.sensors import nws_sensor_source`.

`register_ingest_commands` needed no edit: it iterates `INGEST_COMMANDS`. Verified by import — the
group now registers 11 verbs, and `tests/test_ingest_runner.py::EXPECTED_VERBS` is asserted equal to
it, so a verb that came back fails there.

## W3-7 — the two lane specs. The file's own invariants were read FIRST.

The brief's warning was specific: `parquet-*` specs are GENERATED per `LaneRegistration` and
`job_executor_service.py:991` asserts every `conflicts_with` target resolves, so deleting a spec that
something still names raises at import and the executor does not start. Checked before cutting:

| invariant | site | result for `postgres-sensors` / `postgres-evacuation-zones` |
|---|---|---|
| `conflicts_with` must name a live lane | module-level `assert` at `:991` | **nothing declares `conflicts_with` against either.** The direct lanes conflict with `parquet-sensors` / `parquet-evacuation-zones`, which are untouched. Import passes |
| generated vs hand-written | `_POSTGRES_SPECS` | both were hand-written `_postgres_spec(...)` one-liners, not generated — safe to delete, unlike the `parquet-*` pair |
| `_FORWARD_SIBLING_LANE_BY_SLUG` slugs must exist | module-level `assert` before `_parquet_spec` | untouched: it maps registration *slugs* to *direct* lane ids, never to `postgres-*` |
| unknown active lane → `ExecutorConfigurationError` | `parse_activation` | production `PLANTGEO_JOB_EXECUTOR_ACTIVE_LANES` no longer names either (owner-supplied; Assumption W3-A1) |
| legacy-owner completeness | `_lanes_owned_by(INGEST_CRON_OWNER)` | computed dynamically from `_LANE_SPECS`, so `LEGACY_RAILWAY_RESPONSIBILITIES` merely shrinks; `test_every_observed_legacy_railway_writer_has_a_complete_terminal_mapping` still holds |

**Nothing had to be stopped and reported.** No invariant would have broken.

The `#:` comment above `_POSTGRES_SPECS` said *"The FOUR surviving PostgreSQL forward-ingestion lanes,
and why exactly four"* and gave four reasons that no longer produce four. Rewritten to TWO, with the
reader rule and with fire-perimeters' shadow-writer blocker named.

## W3-8 — tests

**Deleted, because they existed only to exercise deleted functions:**

- `tests/test_ingest_sensors.py` — `test_an_unset_bbox_is_skipped_and_never_failed`,
  `test_an_empty_roster_is_an_honest_skip_with_no_rows_written`, and the `RecordingWriter` helper
  (whose only users they were), plus the then-unused `TYPE_CHECKING` block.
- `tests/test_ingest_evacuation_zones.py` — `test_an_unset_bbox_is_skipped_and_never_failed`,
  `test_the_job_writes_the_zones_it_fetched_and_reports_nothing_rejected`,
  `test_the_job_respects_the_record_ceiling_and_reports_truncation`, `RecordingWriter`, and the
  `build_evacuation_zone_write` half of
  `test_a_recorded_production_zone_parses_and_keys_to_the_bare_global_id` (its parse half stays, now
  asserting the identity instead, so the created-date rule is still pinned end to end).
- `tests/test_ingest_commands.py` — the two `_BBOX_SCOPED_VERBS` rows. The table is now one row and
  stays table-driven.
- `tests/test_ingest_runner.py` — the two `EXPECTED_VERBS` entries, the two `EXPECTED_SOURCE_ORDER` /
  `EXPECTED_JOB_ORDER` entries, the two fixture fakes and their `monkeypatch.setattr` lines.

**Rewritten rather than deleted, because they test the runner/executor and not the deleted jobs:**

- `test_runner_a_single_job_failure_does_not_erase_the_other_results` exploded the sensors job to
  prove isolation "in the middle of the sequence". The sequence is now two jobs long, so there is no
  middle. It became `test_runner_a_geometry_repair_failure_does_not_erase_the_source_result` — it
  fails the LAST job, while the surviving
  `test_runner_a_failure_in_the_first_job_still_lets_the_remaining_jobs_run` fails the FIRST. Two
  jobs, two directions, one test each; isolation stays fully pinned.
- `tests/test_job_executor_service.py` used `"postgres-sensors"` in **10 places** as a generic
  hourly / `coalesce_latest` / `incremental` Postgres-lane fixture — scheduling, fair ordering,
  missed-tick policy, connection invalidation, writer non-overlap. None of them is a test of the
  sensors lane. All 10 repointed to `"postgres-fire-perimeters"`, which `_postgres_spec` gives the
  identical cadence (3600s), schedule (`0 * * * *`), phase offset (0), work class and catch-up
  policy. `fair_due_order` breaks timestamp ties **alphabetically by `lane_id`**
  (`job_executor_service.py:1209-1210`), so the two order assertions were re-derived by hand rather
  than assumed: `fire-detections-direct-forward` still sorts before `postgres-fire-perimeters`, and
  the oldest-first entry is still first.

**Counts updated, with their reasoning:** `_EXPECTED_SPEC_COUNT` 61 → **59**, and the comment above
it gained the third dated paragraph explaining why the `parquet-*` count stays 32. The companion
`#:` comment's "30 non-parquet duties" became 27.

**Added — executable "cannot come back" proofs**, because both modules still legitimately export
write-shaped functions, so "no Postgres writer here" is not a claim a reader can check by eye:

- `tests/test_ingest_sensors.py::test_the_postgres_forward_job_is_gone_and_cannot_come_back_unnoticed`
  asserts `not hasattr(sensors_module, ...)` for `run_sensor_ingestion_job`, `_run_sensor_job` and
  `NO_STATIONS_REASON`.
- `tests/test_ingest_evacuation_zones.py::test_the_postgres_forward_job_and_its_write_builder_are_gone`
  for `run_evacuation_zones_ingestion_job` and `build_evacuation_zone_write`.
- `tests/test_ingest_runner.py::test_runner_never_runs_a_source_whose_layer_has_a_parquet_writer`
  extended with the two job names **and the two `*_SOURCE` constants**, which were imported into
  `runner.py` only to label the deleted entries.

## Dangling-pointer repairs (comment/doc only, no behaviour)

Every one of these was a statement this deletion made **false**, not merely a drifted line number:

| file | was | now |
|---|---|---|
| `ingest/runner.py:1,27` | "three surviving sources"; the writer-shaped rule | one source; the reader rule; WFIGS' shadow-writer blocker named |
| `ingest/evacuation_zones.py:1` | "…and its retrying, bounded, paged **job**" | "…paged **walk**", plus a header stating what was deleted and why the module survived |
| `ingest/sensors.py:1` | "NOAA NWS ground-station **ingestion**" | "…**upstream**", plus the same survivor note including the `build_sensor_reading_write` trap |
| `ingest/commands.py` `_build_backfillable_sources` | silent on the asymmetry | states that `nws-sensors` is the one token whose forward verb is gone |
| `execution/job_executor_service.py:478-512` | "The FOUR surviving…" + four reasons | "The TWO surviving…" + the reader rule |
| `execution/job_executor_service.py:926` | "the cadence of the postgres-evacuation-zones poller it **replaces**" | "…it **replaced** — that lane… deleted 2026-09-07, so this is now the ONLY writer" |
| `ingest/validation/models.py:152` | `cadence_basis="job-executor lane postgres-evacuation-zones runs hourly"` | names `evacuation-zones-direct-forward` at `:35` and records the deletion — the same pattern the vegetation entry already used |
| `pipeline/parquet/lane_registry.py:992` | "…beside it in SHADOW" | records the activation, and that this Postgres-READING gap-fill adapter never needed the producer to keep running |
| `pipeline/direct/evacuation_zones/forward.py:6` | "**that producer is still live** inside `ingest-all` (`ingest/runner.py:48`)" | records the deletion |
| `pipeline/direct/evacuation_zones/parity.py:30` | "`postgres-evacuation-zones` **is STOPPED**… frozen" | "is GONE… permanently frozen", and states that this receipt deliberately compares against a dead snapshot |
| `pipeline/direct/evacuation_zones/parity.py:156` | present-tense "`build_evacuation_zone_write` **refuses**" | past tense + "(deleted 2026-09-07)" |
| `pipeline/direct/evacuation_zones/products.py:70` | cited `ingest/evacuation_zones.py:430-432` and a test line range, both now dead | names the deletion and says this function is now the only place the unset-bbox gate lives |
| `pipeline/direct/evacuation_zones/source.py:44` | cited `ingest/evacuation_zones.py:457-463` | "(DELETED 2026-09-07)" |
| `ingest/AGENTS.md:20-32` | listed both verbs under "Kept, and why", and named `runner.py:48` as the single blocker | lists only the survivors, states the reader rule, and records that the blocker was discharged |
| `pipeline/direct/AGENTS.md:1319,1374` | "**THAT PRODUCER IS STILL LIVE**"; "the poller it replaces" | records the deletion and what survives in `ingest/evacuation_zones.py` |

## What was NOT removed, and why — the input to the next wave

### W3-R1 — `postgres-fire-perimeters`, `ingest-fire-perimeters`, `run_fire_perimeters_ingestion_job`. Blocked on an open owner decision, not on dead code.
`fire-perimeters-direct-forward` EXISTS but is SHADOW: it refuses to publish while any upstream WFIGS
perimeter carries invalid geometry. So `parquet-fire-perimeters` is still the ACTIVE writer of that
object stream, it still reads `geo.features`, and deleting this producer would **stop the layer**.
This is the same shape evacuation-zones had on 2026-09-06: one decision away, and the decision is not
this pass's to make.

### W3-R2 — `postgres-geometry-repair` and `run_geometry_repair`. Structurally last, deliberately.
Kept for W3-R1's exporter (`geo.geometry` is what it joins) plus a reason of its own: orphans regrow
continuously because the `/api/ingest/*` push routes set no `geometry_id` at all, and WFIGS is still
writing rows that need linking. It stays the last job of every tick.

### W3-R3 — `ingest/evacuation_zones.py::build_evacuation_zone_identity`. Orphaned by this pass; kept, and flagged.
Its only caller was `build_evacuation_zone_write`, so after W3-3 its only readers are tests
(`test_ingest_evacuation_zones.py`, 8 assertions). Kept for three reasons: the brief named exactly two
functions to remove and this is not one of them; its docstring is the CITED contract for the direct
writer's `created_date` column (`pipeline/direct/evacuation_zones/rows.py:17`,
`warehouse/schemas/evacuation_zones.py:51`); and the 2026-09-06 wave set the precedent for exactly
this case with the watersheds `LayerBinding` block (item R2 above). **This is the honest weak point of
this packet** — a reviewer may reasonably call it dead code. It should go with the `geo.features`
evacuation-zones row drop, not lane by lane.

### W3-R4 — the evacuation-zones `LayerBinding` block and `EVACUATION_ZONES_SOURCE`.
`EVACUATION_ZONES_LAYER`, `EVACUATION_ZONES_CHANNEL`, `EVACUATION_ZONES_LAYER_VARIABLE`,
`DEFAULT_EVACUATION_ZONES_LAYER_NAME` and `resolve_evacuation_zones_layer_name` lost their last
production reader with W3-3; their remaining reader is
`tests/test_ingest_layer_binding.py:7-12,97-101`, which is the cross-cutting `LayerBinding`
convention suite over all producers, **not** a test of the deleted function. `EVACUATION_ZONES_SOURCE`
likewise survives — it is still used at `test_ingest_evacuation_zones.py:238` for the
history-capability refusal, and it is the layer's source token. Kept on the identical precedent as
item R2 above. The sensors equivalents were never orphaned: `resolve_sensors_layer_name` and
`SENSORS_CHANNEL` are reached through `build_sensor_reading_write`.

### W3-R5 — `LANE_REGISTRY['sensors'].adapter` is STILL `_fill_sensors`, which reads `geo.features`.
Reported rather than treated as a contradiction. That adapter is the **gap-fill** path for days older
than NWS's rolling ~6-day window, not the forward exporter, and it reads rows `geo.features` already
holds. Removing the producer stops that table GROWING for sensors; it makes nothing already reachable
unreachable, because every newer day now comes from the direct writer and every day older than the
rolling window was never reachable from the source anyway. The registration comment now says this in
place of its stale "beside it in SHADOW".
`tests/direct/test_direct_package_registration.py::PENDING_REGISTRATION` still exempts `sensors` for
its own separate reason (no cited ownership-boundary day), untouched here.

### W3-R6 — `README.md`. Refused, deliberately, and it is a real defect.
`README.md:238,433,445,456,595,596,599` still document `agri-service data ingest-sensors` and
`data ingest-evacuation-zones` as runnable, and the "fastest surviving real write" walkthrough makes
`ingest-sensors` its example command. Not fixed here for two reasons: the README was ALREADY stale in
the same way (`data ingest-watersheds` is still in its table after the 2026-09-06 deletion), so this
is a standing gap rather than one this pass opened; and choosing the replacement walkthrough command
— `ingest-fire-perimeters` is the only remaining feature-writing verb, and it is slower and
bbox-sensitive — is an editorial call an owner should make. **Recommended:** repoint the walkthrough
to `ingest-fire-perimeters` and delete three table rows in one small doc push.

### W3-R7 — `src/components/map/AGENTS.md:287` (the Next.js tree).
The one hit outside the service tree. It cites `evacuation_zones.py build_evacuation_zone_write` as
the origin of the `severity` property. It is a documentation pointer, not a code reader, and this
pass was scoped out of the repo-root `src/` tree. The property itself is unaffected: the direct writer
populates `severity` from the same parse layer. Worth a one-line fix whenever that file is next
touched.

### W3-R8 — this file's YAML frontmatter.
`status: watersheds_removed_whole; evacuation_zones_partial_with_named_blocker` is now stale, and the
`observed_at` / `base_commit` fields describe the 2026-09-06 pass. Left untouched on the brief's
append-only rule; this section's own header carries the 2026-09-07 values. An owner may want to bump
the frontmatter, or split this section into its own file.

### W3-R9 — line-number citations in prose that were already drifting.
`warehouse/schemas/evacuation_zones.py:51,59` cites `ingest/evacuation_zones.py:283-305` and
`:308-351`; those ranges were **already wrong before this pass** (the functions had moved to `:352`
and `:377`). Only the citations this deletion made point at *nothing that exists* were repaired (table
above). A repo-wide line-citation audit is a separate job and is not smuggled in here.

## Assumptions

**W3-A1 — production `PLANTGEO_JOB_EXECUTOR_ACTIVE_LANES` names neither `postgres-sensors` nor
`postgres-evacuation-zones`.** Supplied by the brief ("`parquet-sensors` is retired from the active
set"; the 2026-09-04 owner decision to stop all ten `postgres-*` lanes). **Not independently
verified — this pass was forbidden Railway, database and object-store access.** If either IS still
named, `parse_activation` raises `ExecutorConfigurationError: unknown active lane(s): …` and **every**
lane fails, not just that one. Reversal is two lines: restore the two `_postgres_spec(...)` calls.

**W3-A2 — `sensors-direct-forward` and `evacuation-zones-direct-forward` are ACTIVE and their generic
siblings are retired.** Supplied by the brief with proving-run figures (2,361 rows /
`availability_extended: 1`; 116 zones). If either is not, that layer now has no forward writer at all.

**W3-A3 — the `geo.evacuation_zone_tiles` blocker stays refuted.** Taken from
`evidence/reader-not-parquet-scope-20260907.md`, section "REFUTED", per the brief's explicit
instruction not to re-adopt it.

## What this pass did not run

No pytest, no mypy, no `ruff check` — one sweep runs at the end and the owner runs it. `ruff format`
reported *16 files left unchanged*. `python -m py_compile` passed on all 16 touched Python files, and
the import / AST checks above ran. No Railway, no object store, no database, no Alembic, no local app
run; the long-running fire-detections `availability-bootstrap --apply` was not disturbed.
`QUALITY_RECEIPT.json` untouched. Nothing committed or pushed.

**Net change across the service tree: +256 / -447 lines (net −191) over 18 files.**

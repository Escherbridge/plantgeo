# `warehouse/parquet` — the Parquet schema registry

## Responsibility
One canonical storage contract per object stream: its Arrow schema, the grain it is sorted to
before writing, and its compression codec. Stream **S0** owns `schema.py`; per-lane schemas live
in `warehouse/schemas/<slug>.py` and are owned by their lane.

## The layer slug IS the stream name
`ObjectStore.write_partition(table, layer="sensors", ...)` looks up `get_stream_schema("sensors")`
and writes under `layer=sensors/`. One identifier names the schema, the module, and the object
prefix, so the three cannot drift. Slug `fire-detections` maps to module
`warehouse/schemas/fire_detections.py` (hyphens to underscores) — `stream_schema_module` is the
only place that mapping is spelled.

## Registration is autoloading, deliberately

The three land-context products share `schemas/land_context.py`: boundaries, office geography,
and documented routes belong to one source capture and schema owner. `stream_schema_module`
maps those exact three slugs to the shared module; other slugs retain automatic naming.
`get_stream_schema` imports the lane's module on a miss and expects it to have called
`register_stream_schema` at import time. The alternative — a central dict every lane edits — would
serialise the sixteen-stream wave-2 fan-out onto one file. A lane that registers nothing fails
loudly, naming the module it expected to find.

Re-registering an identical contract is a no-op (module re-import is safe); re-registering the
same name with a *different* contract raises `StreamSchemaConflictError` rather than letting the last
importer win.

## The signal plane: ten columns, and the three that are absent
`SIGNAL_PLANE_SCHEMA` is defined here rather than in `warehouse/schemas/signal.py` because it is
frozen owner-decided truth (RUNBOOK §0.22.4, §0.23.4 decision 8), not a lane's private choice.
**S3 must re-export it, never redefine it.**

`min_value`, `max_value` and `avg_value` are deliberately absent. §0.22 measured them identical to
`normalized_value` on **0 of 701,257** rows differing, and the 13-column variant of one real month
cost **2,647,775 B against 695,338 B — 3.81x**. Do not re-add them; the four `sql/agent/*.sql`
statements re-aggregate the spread from `normalized_value` instead.

Types are confirmed against production: `observed_day` is `date32`, `newest_observed_at` is
`timestamp[us, tz=UTC]`, `cell_id` is a uuid rendered as `string`, `observation_count` is `int64`
(`COUNT(*)::bigint` in `drizzle/0029`), and **`allowed_client_exposure` is `boolean`** — reading
it as a string cost a failed run.

**Nullability follows the source, not uniformity.** The eight columns `drizzle/0029` guarantees
present are non-nullable, so a null fails the write loudly. `coverage_fraction` and
`allowed_client_exposure` are nullable because the matview derives them with `array_agg` over base
columns that admit NULL — even though §0.22.3 measured them constant at `1.0` and `False`.

**`allowed_client_exposure` is unresolved**, not merely constant: every governed row says exposure
is *not* permitted while the map paints the data (§0.22.7). Do not build an exposure gate on this
column until that is settled.

### ADDENDUM 2026-09-04 — historical base rungs predate the position columns, and a re-export is owed

A production probe of `layer=signal/kind=observed/zoom=13/year=2026/month=08/day=06/part-0.parquet`
(`environmental_postgres_retirement_20260904/evidence/rung-coverage-census.md:154-174`) found only
ten columns — no `cell_longitude`, no `cell_latitude` — although both are declared `nullable=False`
above. **This refuted a recorded project note that the signal base already carries positions and
that no re-export is owed.**

**The mechanism is schema evolution, not a live bug.** Commit `8ce71fd` (2026-08-24 06:18:47 -0600)
is the FIRST commit where `cell_longitude`/`cell_latitude` exist anywhere in the signal lane: it
added them to `SIGNAL_PLANE_SCHEMA` here AND to `sql/pipeline/signal_plane_day_export.sql`'s SELECT
list in the same change (`git show 8ce71fd -- .../signal_plane_day_export.sql`). Before that commit
the query never selected them and `pipeline/lanes/signal.py::read_signal_day` never built an Arrow
table containing them — there was no non-nullable declaration to violate yet. Every base-rung object
written by an earlier deploy of the exporter structurally lacks both columns, and Parquet objects are
immutable: nothing in the forward pipeline retroactively adds a column to an already-written file.
`day=2026-08-06` is one such object.

**The forward path needs no code change.** As of `8ce71fd` (perf-optimized in `ae63b02`, which moved
the join out of the per-observation hot path without changing what it selects), the schema, the SQL,
and `read_signal_day`'s column-name-driven table build all agree on the same twelve columns, and
`conform_to_stream_schema` (`pipeline/parquet/objectstore.py:1039-1045`) does `table.select(...)`,
which raises `ParquetSchemaMismatchError` rather than silently dropping a column the current query
failed to produce — so a live regression of this kind fails loudly at write time, not silently. The
existing mocked-session test `tests/parquet/test_signal_lane_export.py::test_the_read_conforms_to_the_registered_schema`
already pins this via `table.schema.equals(SIGNAL_PLANE_SCHEMA.arrow_schema)`.

**Where the positions come from, and a dependency this adds.** `signal_plane_day_export.sql` resolves
them with `INNER JOIN agri.spatial_cell AS cell ON cell.id = aggregated.cell_id` then
`ST_X/ST_Y(cell.centroid)` — the identical pattern `pipeline/direct/soil/support.py` uses for the soil
lattice. `agri.spatial_cell.centroid` is declared `NOT NULL` (`db/agri/tables/spatial_cell.sql:13`)
and the join is INNER because `cell_id` is a foreign key, so a resolvable cell always yields a
position — the `nullable=False` declaration above is honest for any row the query can produce at all.
This makes **signal-plane a code-level dependent of `agri.spatial_cell`**, the same table
`retirement-inventory.md:37` lists "drop now" and already records as a live dependency for vegetation
and soil. `retirement-inventory.md:37,94` separately flags that the table's absence from production is
*asserted, not verified* — if that assertion is correct, every signal export since `8ce71fd` (not only
the pre-fix historical objects) has been failing outright at the database level with
`relation "agri.spatial_cell" does not exist`, which would itself explain why so much of the
signal-plane history remains on the pre-fix schema: nothing has been able to re-export it since.

**A re-export is owed, not fired.** No lane-scoped rewrite tool exists for `signal` yet — only
vegetation has one (`parquet-rewrite-vegetation`, `pipeline/parquet/vegetation_rewrite.py`), which
retracts *only* z13 partitions matching "the exact legacy shape missing both cell coordinate fields"
so `parquet-drain --selection missing` can rewrite them. Neither `--selection missing` nor
`--selection ladder` alone can touch a day that already carries a base completion marker — both are
additive, never destructive — so building a `parquet-rewrite-signal` (generalizing the vegetation
tool) is itself part of the owed work. Once that exists, the sequence is: retract the affected
`signal/observed` days (`--apply`) → `parquet-drain --layer signal --selection missing` (re-exports
the base rung with positions, via the already-correct SQL) → `parquet-drain --layer signal --selection
ladder` (derives z9/z5/z0, which could never complete against a base rung missing the coordinate
columns `GridAggregation` reads). The 222-of-1,560 "ladder INCOMPLETE" population measured for
`signal-plane` (`rung-coverage-census.md:61`) is the best current estimate of the affected day count —
stated as inference, since no schema-fingerprint census across all 1,560 published z13 objects has
been run — putting the owed rewrite at roughly 222 lane-days × 4 objects (one retracted+rewritten z13,
three new coarse rungs) ≈ **~888 objects**, a lower bound if any of the other 1,338 "complete" days
also predate `8ce71fd`.

## Sorting and codec
`sort_columns` is the grain from §0.22.1, matching `uq_mv_signal_cell_daily`:
`(support_key, signal_name, normalized_unit, cell_id, observed_day)`. Inside a day partition
`observed_day` is constant, so this is exactly `drizzle/0029`'s physical clustering order — the
ordering that makes `normalized_value` locally homogeneous per signal, which matters because that
one column is **93.9%** of the compressed file.

`zstd`, measured at 695,338 B against snappy's 874,945 B on the same month. Float32 for
`normalized_value` would roughly halve the file and remains an open **data-fidelity** decision
(§0.22.6) — it is not taken here.

## Snapshot lineage digest aggregation

`snapshot_signal_product.py` holds the shared registration factories for immutable snapshot-derived
signal products. Keeping those factories below `warehouse/schemas` preserves one lane module per
slug and the no-sibling-import boundary while retaining one exact schema definition per product family.

`sha256-lines` is a closed aggregation used only by the completed soil-temperature snapshot
contract. Both engines sort the contributing string values, append one newline to each, and hash
those exact bytes with SHA-256. This mirrors the immutable builder's coarse-tier lineage and must
not be replaced with `first`, which would silently discard all but one child digest.

## Per-rung key columns on `GridAggregation` (`key_columns_by_tier`, 2026-09-18)

`key_columns` was one tuple for every rung, so a lane whose *vocabulary* coarsens with zoom — the
vegetation-type ladder EVT code (1,069) → group (193) → physiognomy (20) → lifeform (10) across
z13/z9/z5/z0; the three coarse counts include the `-9999 Fill-NoData` sentinel, so the real
vocabularies are 192/19/9 and the test fixture pins the sentinel-inclusive numbers on purpose — was
not expressible: keying on the code kept 1,069 classes per cell at z0.
`key_columns_by_tier: Mapping[ZoomTier, tuple[str, ...]] | None = None` replaces the grain rung by
rung. **`None` is today's behaviour for every existing lane, byte for byte**, pinned by
`tests/parquet/test_grid_per_tier_keys.py` with content digests computed at HEAD `ec172e88` before
the field existed, over a synthetic lane that exercises every member of the `Aggregation`
vocabulary and over the real `fire-detections` lane. Resolution is `grid_key_columns(strategy, tier)`;
`_derive_grid_tier` groups on the tier's tuple and skips the declared aggregate of any column that
is a key at that rung.

**The dropped-key rule.** A column that is a key at *any* rung (`key_columns` or any tier tuple) but
not at this one is a *dropped* key and may only aggregate `first` or `null` — refused otherwise at
declaration (`GridAggregation.__post_init__`), naming the column and the rung. A label is carried or
withheld, never summed. `__post_init__` also requires every derived rung to be named (no silent
fallback — `HierarchicalDissolve.code_length_by_tier` has the same rule, enforced later, at
derivation) and requires a `13:` entry, if present, to equal `key_columns`, so the base grain is
stated once. With no ladder at all, a column in both `key_columns` and `aggregations` is refused:
such a key is never dropped, so its aggregate would silently never apply (before this field Polars
raised a duplicate-column error for the same declaration; the failure stays loud). The one thing
declaration cannot catch is a column misspelled identically at every rung; `_require_columns`
refuses that at derivation and `validate_derivation_against_schema` — which now checks per-tier keys
AND `key_columns` against the arrow schema — reports it at registration, where the sweep runs it
over every lane.

**Chain safety.** A coarser rung may be derived from the finer *derived* rung above it, not only
from the base — the banded fold derives z0 from the written z5. So for each consecutive pair of
derived rungs (finer F, coarser C) every key of C must *survive* F: be a key of F, or be carried
through F by `first`. A key nulled at F would make C group on an all-null column — silently, no
exception, every class collapsed into one row per cell. `__post_init__` refuses this naming both
rungs. The joint ladder below satisfies it trivially (each coarser tuple ⊆ the finer one); the
single-column ladder satisfies it only under `first`.
The observable symptom of a non-nested `first` is not an exception: a rung derived from the rung
above it silently differs from the same rung derived from the base, and counts migrate between
labels. A lane that relies on `first` must therefore prove chained == base on its real legend in its
own tests; the platform can only refuse what it can see at declaration.

**The ladder is copied and the strategy stays hashable.** `__post_init__` replaces the caller's
mapping with a read-only `MappingProxyType` copy (a later `d[5] = ...` on the caller's dict cannot
bypass the refusals), and `GridAggregation.__hash__` is explicit because a proxy is unhashable while
a frozen dataclass advertises hashability; `TierDerivation.__eq__`, which `register_tier_derivation`
uses to detect conflicting re-registration, compares the proxies by value.

**`first` is lawful only where the finer vocabulary nests functionally inside the coarser** (each
child value has exactly one parent), because `first` over a group picks an arbitrary member. The
lane, not the platform, owns proving that. Measured on the real LF2025 legend (fixture
`tests/parquet/fixtures/lf2025-evt-hierarchy.csv`, source sha256 `5ccc130b…`): `VALUE → EVT_GP`
nests; **`EVT_GP → EVT_PHYS` does not** (47 of 193 groups span several physiognomies — including
PNW groups 645 Western Red-cedar–Western Hemlock, 632 Red Alder, 629 Western Oak Woodland, 609
Pacific Coastal Scrub, 617/618 Grassland and Steppe); **`EVT_PHYS → EVT_LF` does not** (Riparian,
Agricultural, Developed, Exotic Tree-Shrub each span Tree/Shrub/Herb). So the plan's single-column
ladder with `first` on `evt_phys` at z9 and `evt_lifeform` at z5 would fabricate labels. The honest
ladder widens the tuple where nesting fails — `{9: ("evt_group_code","evt_phys","evt_lifeform"),
5: ("evt_phys","evt_lifeform"), 0: ("evt_lifeform",)}` with the finer codes `null` — and is
expressible with this field as it stands (the last test in that file constructs it). One `how` per
column serves every rung; a "first here, null there" declaration is not expressible and should not
be added for this.

**`MAX_DERIVATION_ROWS` is a per-CALL bound.** Its sole consumer is the height check at the top of
`derive_tier`; it bounds the table handed to one call, not a lane-day. A day larger than the cap is
derived in latitude bands whose edges are multiples of every rung pitch (the fold belongs to
`pipeline/parquet/derivation.py`, track `vegetation_type_landfire_evt_20260918` Phase 1B). Do not
raise it: it is the only guard a grid lane has, and a value admitting the 23 M-row vegetation-type
base would bound nothing the host can hold (`ROW-CAP-ANALYSIS.md` §4.1).

**Deriving a rung from the rung above it is lawful only for associative aggregates.** z0-from-z5
(and any banded fold) equals z0-from-base only for `sum`, `min`, `max`, `all`, `any`, `null` and
`first` under functional nesting; `mean` of means is not the mean, and `sha256-lines` of digests is
not the digest of the lines. The associativity test in `test_grid_per_tier_keys.py` uses only those.
Refusing `mean`/`sha256-lines` in a chained or banded derivation is 1B's job, where the fold is
introduced; the constraint is recorded here because this is where the ladder is declared.

## The DuckDB guards in `tiers.py`, and the one thing they are NOT
Only the geometry lanes open DuckDB at all — a `GridAggregation` lane coarsens in Polars and a
`TierPassthrough` lane does nothing. Every session this module opens carries `DERIVATION_MEMORY_LIMIT`
(1600MB), `DERIVATION_THREAD_COUNT` (3), `max_temp_directory_size = '0GiB'` and `:memory:`.

**Disabling the spill is the load-bearing one.** DuckDB's default is *90% of available disk space*:
with spilling enabled an over-budget `ST_Union_Agg` quietly writes tens of gigabytes to local disk
and takes the host down slowly, and an unguarded query of exactly that shape **consumed the host on
2026-08-24**. With it disabled the same query raises in about a second and the drain records a
failed day an operator can see. A tier that was never written is recoverable; a host is not.

**A caller-supplied `connection` is re-pinned AND restored.** The override is deliberate — the caller
who hands in an unguarded connection is precisely the caller who would eat the host — but all three
settings are **instance-wide in DuckDB, not connection-local**. Measured 2026-08-25: pinning them
through one cursor re-pins every SIBLING cursor of the same instance, including ones this module was
never handed. Left in place, one derivation would cap a co-resident serving session
(`parquet_ops/duckdb_session.py`) at the batch budget for the process lifetime, and that session's
owner has no return point at which to notice. So `_geometry_session` snapshots the three via
`current_setting`, pins, and restores in a `finally` — on the caller-supplied branch only; a session
this module opened is closed instead. The restore is of the RENDERED value ('900MB' reads back as
'858.3 MiB'), so it can move a ceiling by a fraction of a MiB — never by the ~16x it exists to undo.

The guards hold for exactly the derivation. That window is the whole of the interval in which a spill
could happen, so nothing is weakened; what changes is that the mutation ends where the derivation does.

**`base_tier` is unregistered on the way out.** A registration outlives the statement that used it, so
on a reused connection the base day — and its arrow buffers — stayed reachable after the return. For a
`soil-survey` day that is gigabytes pinned past use, on the one connection a driver was told to reuse
across a thousand days.

**The reuse is wired, not merely advertised.** `derivation_session()` → `derive_and_write_day_tiers(
connection=...)` → `derive_tier(connection=...)`; `pipeline/parquet/drain.py` opens exactly one for a
whole `--selection ladder` walk. Before that chain existed, the docstring offered a capability whose
only driver had no parameter for it, and every geometry rung of every day re-ran `LOAD spatial`.

**The derivation session and the extension directory (2026-09-03).** Every geometry lane's z9 rung
failed in production on 2026-09-02 (`parquet-drought`, `parquet-evacuation-zones`,
`parquet-fire-perimeters`) with `TierWriteError ... IOException: Can't find the home directory at
'/nonexistent'`. Both runtime images create the `plantgeo` user with `--home-dir /nonexistent`, so
DuckDB's default extension directory (`$HOME/.duckdb`) can never exist; the serving session
(`parquet_ops/duckdb_session.py`) always pointed at the image's `/opt/duckdb-extensions` before its first
`LOAD`, but `_load_spatial` here did not, so on the executor both `LOAD spatial` and the `INSTALL`
fallback died before touching the network. `_load_spatial` now runs
`foundation/parquet/duckdb_extensions.extension_directory_setting()` first -- the one definition of the
directory both sessions share -- and only when that directory exists, so a developer machine still falls
back to `$HOME/.duckdb`. Point lanes never saw this: their coarse rungs are Polars aggregations and never
open DuckDB.

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

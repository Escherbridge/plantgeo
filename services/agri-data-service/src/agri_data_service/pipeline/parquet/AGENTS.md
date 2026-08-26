# `pipeline/parquet` — the object store and the partition writer

## Responsibility
The single seam through which every lane puts a Parquet partition into Railway object storage.
Stream **S0** owns `objectstore.py`; no lane reimplements uploading, and no lane composes an
object key by hand.

## Two objects, on purpose
- **`ObjectStoreBackend`** (Protocol) — `put`, `list_keys`, `size_of`. Everything the warehouse
  needs of a bucket and nothing else. `BotoObjectStoreBackend` is the boto3 implementation;
  a test substitutes a dictionary-backed fake and the whole writer runs with no network, no
  credentials and no `moto`.
- **`ObjectStore`** — layout awareness: prefix handling, `foundation/parquet/paths.py` for keys,
  schema conformance, and the receipt. It knows nothing about S3.

That split is why "constructible from settings and unit-testable without network access" is one
design rather than two code paths.

## Credential wiring
`OBJECT_STORE_ENDPOINT_URL`, `OBJECT_STORE_BUCKET`, `OBJECT_STORE_ACCESS_KEY_ID`,
`OBJECT_STORE_SECRET_ACCESS_KEY`, plus `OBJECT_STORE_REGION` (default `auto` — **it must match the
bucket's signing region**) and the optional `OBJECT_STORE_PREFIX`. All live on `Settings` in
`config.py`; values come from `.env` locally and from **Railway reference variables** pointing at
the bucket service in production (RUNBOOK §0.23.8 step 2). The names above are ours precisely
because reference variables let the operator choose them — nothing here guesses at what Railway
injects.

Missing configuration is not an error until a write is attempted: `require_object_store()` raises
naming **every** variable still unset, so wiring is one round trip rather than four.
`OBJECT_STORE_PREFIX` sits *outside* the frozen `layer=.../kind=.../zoom=...` layout and exists so
one bucket can hold an isolated sandbox beside the real warehouse.

## Every operation names ONE zoom tier, and none may span the ladder
`zoom=NN` sits between `kind=` and `year=` (owner, 2026-08-23), two-digit zero-padded so a
lexicographic listing walks the ladder in numeric order. `foundation/parquet/zoom.py` owns the four
published rungs; `zoom_prefix(layer, kind, zoom)` is the prefix that covers exactly one of them.

**`zoom` is a required argument of every write, listing, existence check and prune here. There is
deliberately no "all tiers" mode and no default.** One convenient tier-less listing is all it takes
to hand a reader four resolutions of the same day as though they were one population: nothing
raises, the row counts merely quadruple and the geometry silently disagrees with itself. A caller
that genuinely wants the whole ladder asks four times and knows it asked.

The prune is scoped the same way and for the same reason — removing a day's surplus parts at z13
must not reach the z09 parts of that same day, which are a **different resolution** of it rather
than an older export of it. `oldest_export_instant` ignores other tiers for the mirror-image
reason: a coarse rung derived hours after the base one would otherwise drag the base day's
freshness back to the derivation's clock.

**Cross-tier agreement of one day is NOT this module's invariant.** `write_absence` still refuses to
mark a day that already holds data, but only at the tier being marked: the four tiers of one day
live under four disjoint prefixes, so policing them together would cost four listings per marker and
still race. "Every tier of a published day is present" is the **derivation step's** obligation,
because derivation is the only thing that knows a coarse tier was computed from a base one.

Read-side consequences of the same rule live one directory over, and they differ on purpose:
`planes/AGENTS.md` (every public function takes a `requested_zoom` and resolves it once through
`serving_zoom_tier`, which walks **down** — z11 reads the z9 rung) and
`pipeline/validation/__init__.py` (every module pins `WRITTEN_ZOOM_TIER = ZOOM_TIERS[-1]` and takes
no `zoom` argument at all, because a validator has no viewport and the writer writes one rung).

## Rules the writer enforces, and why each is fail-closed
- **The layer slug selects the schema.** `write_partition(table, layer="sensors", ...)` looks up
  `get_stream_schema("sensors")`. A lane cannot write a shape it has not registered.
- **Conform, then sort, then write.** Columns are selected in schema order and cast, so a Polars
  `large_string` or a column ordering difference is absorbed rather than corrupting the file.
  PyArrow refuses a cast that would put a null in a non-nullable field, which is what makes the
  schema the null gate. Sorting to the grain is what produces the clustering the compression needs
  (RUNBOOK §0.22.5).
- **A zero-row write is refused.** An empty Parquet file reads to gap detection as a *present*
  day, silently converting a real hole into apparent coverage. The absence mechanism is
  `write_absence` (settled 2026-08-22, RUNBOOK §0.25.3): an `absent.json` marker at the day's
  partition path carrying `GovernedAbsence` evidence, never an empty data file.
- **Data and absence refuse to coexist, in both directions — at one tier.** `write_partition`
  refuses a day carrying an absence marker; `write_absence` refuses a day already holding a part
  file (including an incomplete day). Both checks are scoped to the tier being written, per the zoom
  section above. Retracting either side is a manual admin action (§0.21.5) — there is deliberately
  no API here that does it. Reading a marker's evidence back is S17's concern; the backend seam has
  no `get` yet on purpose.
- **Completion markers (`_complete.json`) are the third object kind (§0.34.1).** A day holding parts
  WITHOUT this marker is a release whose upload was killed part-way through; its parts are real but
  they are a prefix of the day, not the complete day. The marker is written LAST, after the prune,
  and is what lets gap detection distinguish `data` (complete) from `incomplete` (partial).
- **A receipt carries the sha256 of the uploaded bytes.** That is an upload-integrity digest, not
  a cross-version reproducibility claim: `pq.write_table` stamps the writing pyarrow version into
  the file, so the same rows written by a different pyarrow need not be byte-identical.

## The write protocol — as it shipped after two adversarial reviews (§0.35.1)

**Order: retract at first part → parts → prune → mark.** `write_partition` retracts any existing
completion marker as it uploads `part-0`, AFTER the empty-row and governed-absence refusals but
BEFORE writing the first new part. A failed attempt that writes nothing — a statement timeout, a
transient database error, a source that now returns zero rows — leaves a previously-complete day
exactly as it found it. Then parts upload, surplus parts are pruned, and the completion marker is
written last.

**A failed prune withholds the mark rather than failing the day.** Marking a day whose surplus
parts survived would publish a completion claim over a two-generation mixture. The rows are never
lost and the day counts as `written` in the driver's census, but it is not marked. It stays
`incomplete` and the next tick repairs it.

**A failed mark is `raised`.** The parts are on disk but the completion claim is missing, so the
day resolves as `incomplete` and is re-exported on the next tick (an unfinished SERIES day is now
re-exported, which is why the prune moved into the shared per-day path — it was static-lanes-only
before §0.35).

**A governed absence retracts any completion marker.** `write_absence` removes `_complete.json` if
it exists, because an absence claim and a data claim cannot coexist.

**The whole lane-day runs under a session-scoped Postgres advisory lock.** Two drivers on one
lane-day can interleave so the slower one's prune deletes parts the faster one just wrote, then
stamps a completion marker whose `part_count` matches the truncated remainder exactly — the bucket
and its receipt agreeing on a population that lost rows, which no later census or audit can detect.
The lock is taken before any object is touched and held until the session closes (§0.35.4).

## This path is synchronous, deliberately
`python.md` calls for async I/O, and this module is sync. It runs from the ingestion CLI and the
Railway cron, never on the Sanic event loop; boto3 has no async client, and wrapping it would add
a thread pool for no caller that exists. **If a route ever needs it, run it in an executor rather
than making this async.**

## `lane_registry.py` — why it is here and not in `pipeline/lanes/`
It imports all thirteen lane modules. A module *inside* `pipeline/lanes/` that did that would
(correctly) fail `test_layer_import_contract.py::test_lanes_do_not_import_each_other`, because
`layer-lanes.md` §1's "a lane never imports another lane" is enforced there by directory. **The
registry is not a lane** — it is the one module allowed to know all of them, and it lives beside the
writer they all publish through.

### Four return shapes, one result
The eleven exporters landed concurrently with divergent signatures. `normalise_export_outcome` folds
them into `LaneRunResult(part_count, row_count, byte_count, absence_recorded)`:

| exporter shape | lanes | folded by |
|---|---|---|
| `ParquetWriteReceipt` | signal, vegetation, weather-observations, water-gauges, sensors | `_from_parts((receipt,))` |
| `tuple[ParquetWriteReceipt, ...]` | watersheds, evacuation-zones, soil-survey | `_from_parts(receipts)` |
| `ParquetWriteReceipt \| AbsenceWriteReceipt` | fire-detections, burn-severity | `isinstance` dispatch |
| `FirePerimetersExportOutcome` | fire-perimeters | its own `parts`/`absence` fields |

An **empty** tuple is refused rather than folded: a day that produced no object is a gap, and
reporting it as a completed export would hide one.

Each lane's adapter resolves its own arguments (`agri.spatial_cell` ids, a `geo.layers` id, the day's
sensor stations, the published SSURGO keys) from `sql/pipeline/lane_registry_*.sql`, so the driver
never has to know that five lanes take five different second arguments.

### Floors, lags, and which are guesses
Every `history_floor`/`publication_lag_days` pair carries a `floor_basis` citation on the
registration itself, echoed by `--dry-run`, because **a wrong floor invents thousands of phantom
gap-days**. Three are worth knowing without reading the table:

- **`signal` uses lag 9, not 5.** NASA POWER publishes at 5 days and ERA5-Land at 9; the larger is
  the safe one, since at 5 the four newest days are declared missing while ERA5-Land genuinely has
  not published them.
- **`water-gauges` floors at 2026-05-24, NOT 2022-08-05.** That code constant is explicitly
  *borrowed* from the vegetation layer and nothing confirms the archive walk reached it; the dense
  record starts 2026-05-24, and the bare `min(observed_day)` of 1990-10-01 is a documented trap.
- **`weather-observations` is the one FALLBACK.** RUNBOOK §0.26.8: the lane doc describes the
  `signal` stream, and the producer this lane actually exports has no contract at all. 2026-08-01 /
  lag 2 is a deliberately shallow guess so being wrong costs dozens of phantom days, not thousands.
  Write that half of the contract and measure the real floor before trusting it.

### Every lane declares a NATURE, and it says what the partition day means
The vocabulary lives in `foundation/parquet/lane_contract.py`; the registrations pick one each.

| nature | the partition day is | gap-fill | forecastable |
|---|---|---|---|
| `daily_series` | the observation day | every day in `[floor, today − lag]` | may be |
| `release_series` | the publication's own valid/issue date | every cadence step in that window | may be |
| `static_lookup` | a **version stamp** | one snapshot at the source watermark, or nothing | **never** |

`LaneWindowKind` and `current_snapshot` are **gone**. That model registered three reference lanes
as a daily series with a collapsed window, which meant re-snapshotting the newest settled day on
every tick and calling any tick they missed a permanent loss. Both halves were wrong: a HUC12
boundary is not a measurement taken on a date, so no calendar day ever carried an obligation.

`lane_window` now **refuses** a static lane rather than answering with a plausible-looking range —
handing one back is precisely how the old model came to churn.

### Static lanes are watermark-driven, not schedule-driven
Each `static_lookup` lane declares a `watermark` resolver. `resolve_lane_watermarks` runs them
first, before any listing, and `resolve_static_lane` applies one rule: **a partition dated at or
after the watermark means there is nothing to do — not a gap, not an absence, just current.**
Otherwise exactly one snapshot is owed, dated at the **watermark day**, never at the run date.

- **Every watermark column is a CHANGE event, never a poll clock.** `geo.features.updated_at`
  qualifies only because `sql/ingest/refresh_features.sql` moves it inside an UPDATE gated on
  `properties IS DISTINCT FROM next_properties` — an unchanged re-fetch moves nothing.
  `geo.geometry.last_confirmed_at` is deliberately **excluded everywhere**: `usda-soil.ts:769,833`
  advances it on every re-fetch of unchanged ground, and a poll clock inside a version stamp would
  reinstate the daily churn.
- **`created_at` rides alongside `updated_at`** because an insert moves only the former
  (`drizzle/0022:13`), so neither column alone sees every change.
- **`soil-survey` takes `GREATEST(saverest vintage, feature created_at)`.** The vintage alone would
  never advance for a lazily-warmed survey area carrying an old `saverest`, and those delineations
  would then never reach a release.
- **The census reports `current` distinctly from `watermark_unread`.** Both show zero missing days
  and they are different claims; a listing-only `--dry-run` over a lane whose DSN it could not
  resolve says so rather than implying the lane is fine.
- **A watermark later than today is refused** as a clock disagreement — an observed partition dated
  in the future is never right.
- **`--dry-run` now opens the loader DSN when a static lane is in scope**, because coverage cannot
  be told from the listing alone. That DSN falls back to `DATABASE_URL`, which in this repo's `.env`
  is **production** — one read-only aggregate, rolled back, but a surprise nonetheless.
  **`--skip-watermarks` keeps the audit entirely offline** and reports `watermark_unread`.

### `calendar` is the thirteenth stream and has no source system
The conformed date dimension. `static_lookup`, `horizon: none`, floor **derived** as
`min(history_floor)` over the twelve database-backed lanes. Its generator is
`foundation/parquet/calendar.py` (stdlib only) and its lane module takes **no `AsyncSession`** —
the registry's `_fill_calendar` absorbs the uniform adapter shape so the lie stays in one annotated
place. Its watermark comes from the clock and its own listing: a version covers 800 days forward
and must reach `today + 400`, so it regenerates roughly annually instead of daily.

## `gap_fill.py` — the driver a Railway cron runs
- **Newest-first is the design, not a preference.** A newly published day *is* the newest missing day,
  so one ordering serves the incremental tick and the backfill with no second job to keep in sync.
- **Round-robin across lanes, one day per lane per round.** Sequential order would let
  `fire-detections`' ~9,400-day window eat a whole tick before `signal` wrote anything.
- **A governed-absence marker counts as covered.** `unfilled_partition_days` excludes it; that is
  what stops a genuinely empty day being re-attempted every hour forever. **An incomplete day
  (parts but no completion marker) counts as unfilled** and is re-exported — which is how a killed
  upload repairs itself on the next tick.
- **The absence payload claims only what the run observed.** It states that the day-scoped export
  query over *this warehouse's own tables* returned zero rows, and explicitly says the upstream was
  not contacted. Reconciling against the live source is `pipeline/validation/<slug>.py`'s job.
- **The session is rolled back after every day, success included.** These reads are read-only, so
  holding one snapshot across a 600-second tick would pin a production xmin horizon for nothing — and
  after a failed statement, that rollback is what makes per-lane isolation real rather than asserted.
  `SET LOCAL statement_timeout` dies with it, so the 120 s pin is re-applied per day.

## Five lane-day outcomes, and what each one means to an operator

**`LaneDayOutcome`** classifies what happened to one lane-day attempt. The driver's census groups
by outcome; an operator reading a failed tick needs to know what each one means and what to DO.

- **`written`** — parts uploaded, pruned, and marked. The day is complete. Nothing to do.
- **`absent`** — a governed-absence marker was written because the export query returned zero rows.
  The day is covered. Nothing to do unless the absence is unexpected (then reconcile against the
  upstream source via `pipeline/validation/<slug>.py`).
- **`raised`** — the export, prune, or mark raised an exception. The day failed and needs
  investigation. Check the logs for the exception; common causes: statement timeout, transient
  database error, object store unavailable, schema mismatch. The lane STOPPED on this day (newer
  days behind it were not attempted this tick).
- **`blocked`** — the export returned zero rows over a day that ALREADY holds parts (an incomplete
  day whose parts are real but the source now says there is nothing). `write_absence` refuses to
  mark it because data and absence cannot coexist. The day needs an ADMIN decision: either manually
  retract the parts (make it `missing` so the next tick can govern it absent), or manually mark it
  complete if the parts are the correct answer. **The lane KEEPS DRAINING** — this outcome does not
  stop the tick, because newer days may still be fillable. The blocked day stays blocked until an
  admin resolves it.
- **`contended`** — another run holds this lane-day's advisory lock. NOT a failure; the other run is
  working on it. Nothing to do. Not counted in `written`, `absent`, or any failure tally.

**A remaining backlog after a tick is not a failure.** Only outcomes in `FAILING_LANE_OUTCOMES`
(`raised`, `blocked`) set a non-zero exit code — matching `jobs-pulse`'s exit philosophy, and safe
under `restartPolicyType: NEVER`. A tick that wrote 50 days and has 500 still unfilled exits 0.

## Reading it back
`polars_storage_options(credentials)` returns the Polars/`object_store` connection dict for
`pl.scan_parquet`/DuckDB `httpfs`. It is credentials in a dictionary — never log the result.
Reading is otherwise S18/S20's concern; this module writes and lists, and deliberately offers no
`get_bytes`.

## The bulk drain has TWO selections, and choosing the wrong one reports a green lie
`drain.py` walks a backlog; `--selection` says WHAT a lane-day owes.

- **`missing`** (default) — days with no base rung at all, exported from Postgres. Its census is
  `build_gap_census`, which walks `GAP_FILL_ZOOM_TIER` **and nothing else** and says so in its own
  docstring.
- **`ladder`** — days whose base rung is already published and correct but which are missing one or
  more coarse rungs. Derived FROM THE BUCKET; it opens no source query at all, which is what makes a
  thousand-day repair affordable (`signal` measured 151 s for ONE cold Postgres day).

**The two censuses answer different questions and neither may borrow the other's answer.** A day
written before the zoom fusion shipped is base-complete, therefore invisible to the missing census,
therefore empty at every zoom under 13 **forever, on a green tick**. Measured 2026-08-25: ~1,040
lane-days across eleven lanes. So `parquet-drain --dry-run` reports the census OF THE SELECTION
ASKED FOR — a ladder repair audited through the base census reads as "nothing to do".

**Every entry point is reachable from a verb, and that is a correctness property.** The ladder walk
is `parquet-drain --selection ladder`; the pre-zoom sweep is `parquet-retire-legacy-layout`. A
repair only a REPL can start is one an operator reads about in a commit message, does not run, and
believes has happened.

**One DuckDB session serves a whole ladder walk.** `run_drain` opens a single `derivation_session()`
and threads it through `derive_and_write_day_tiers(connection=...)`; otherwise a geometry lane opens
a session and pays `LOAD spatial` per rung — three per day, ~3,000 across the measured repair. See
`warehouse/parquet/AGENTS.md` for what that session does to a connection it is handed.

**A ladder day is re-selected forever if ANY rung emptied.** An emptied rung is retracted and carries
no completion marker, and the census intersects markers across every rung — so a day whose z9 wrote
and whose z0 emptied looks `written` by part count and is selected again by every future census.
`DerivationResult.emptied` names the rungs, and the summary's `emptied_ladders` reports the days.
This is the one place the bucket-as-checkpoint rule does not self-terminate; only lanes with
nullable coordinates (`water-gauges`, `sensors`) can reach it.

**Nothing raises out of one lane-day.** `_drain_one_day` guards `_run_one_day`, and `_derive_one_day`
guards the advisory lock itself — `pg_try_advisory_lock` is a real statement, and a session left in
a failed transaction raises THERE, before any derivation. Unguarded, one such day took the whole walk
down and lost every lane's tally. Each lane-day also rolls back: SQLAlchemy 2.0 autobegins on the
lock statement, so a walk that never rolled back would hold ONE transaction open for hours, which
`idle_in_transaction_session_timeout` eventually terminates mid-run. The advisory lock is
session-scoped, so the rollback does not release it.

## Retiring the pre-zoom layout: superseded means SERVABLE, not mentioned
The keys written before the zoom axis existed sit one path segment shallower, so all three live
parsers reject them: `list_partition_objects` filters them out, `prune_surplus_parts` skips them, and
no census counts them. Nothing reads them and nothing would ever collect them (2,274 keys, 645.7 MB,
2026-08-25). `retire_legacy_layout_objects` is the only code that can see them, and it takes the
`ObjectStoreBackend` as a SEPARATE argument because the store itself cannot list them.

A legacy object is **superseded** only when the zoom layout holds its day in a state a reader may
answer from — `COVERED_PARTITION_STATUSES`, i.e. `data` or `absent`. "Some key mentions that day" is
a strictly weaker claim: a completion marker whose parts were deleted underneath it is `missing`, and
parts with no marker are `incomplete`, and **no reader serves either**. Classifying those as
superseded deletes the only readable copy of the day while reporting the deletion as safe.

This stops being theoretical the moment the hourly cron runs beside the sweep: `write_partition`
clears the completion marker as it uploads `part-0`, so any day mid-re-export reads `incomplete`
inside the sweep's window — and the sweep lists once, at the start of the layer. Everything else is
`orphaned`, reported, and kept unless asked for by name.

## `vegetation_rewrite.py` — the current-layout exception is pinned and schema-specific

The 2026-08-25 vegetation repair is not the pre-zoom retirement above. Its stale objects already
live in the current `zoom=NN` layout, but their z13 Parquet schema predates the required
`cell_longitude`/`cell_latitude` fields. Deriving z9/z5/z0 from those bases fails; the governed
PostgreSQL exporter can now add the coordinates, so those exact days must become `missing` and pass
through the ordinary export drain again.

`parquet-rewrite-vegetation` is deliberately narrower than a general admin delete:

- the manifest is strict JSON for `vegetation` / `observed`, sorted unique days only, and its raw
  bytes must match both an independently supplied count and SHA-256;
- no flag can select another layer, kind, date range, or tier subset;
- dry-run is the default, and even dry-run acquires `_lane_day_lock_key` before listing or reading;
- a completed z13 day is eligible only when its Arrow schema is exactly the previous coordinate-less
  schema, including the old non-null `cell_id` and `observation_checksum` fields. Current-schema
  data, an absence, a conflict, an incomplete upload, and marker-only residue are refusals rather
  than broadening rules;
- an exactly empty z13 prefix is the resumable checkpoint. It is accepted because an interrupted
  earlier run may already have removed the base while z9/z5/z0 remain; all-empty is idempotent
  success;
- apply order is z13, z9, z5, z0. Each rung goes through `ObjectStore.retract_partition_tier`, so
  the completion marker is cleared before parts are deleted. Each object operation has a bounded
  exponential retry budget, every day emits structured progress, and the session transaction
  opened by the advisory lock is rolled back at the day boundary.

An incomplete z13 prefix is never treated as a resume checkpoint. It could be a crashed exporter,
an externally interrupted delete, or an unknown mixture; only exact missing is unambiguous enough
for this destructive command. Such a day requires inspection rather than a wider retry predicate.

## Vegetation completion: absence ladders and exact reconciliation

Vegetation data days and source-empty days both occupy the zoom ladder. Data is derived from z13,
while an absence has no rows to derive; `vegetation_absence.py` therefore copies the exact governed
evidence object from z13 to z9/z5/z0. It first proves the governed source has no row on the day,
takes the same whole-ladder advisory lock as the writer, then repeats both the source-day query and
the four-rung status census while holding that lock. Every already-present coarse marker on an
actionable, partially complete ladder is decoded so it cannot preserve different evidence. The
exact reconciliation below audits fully complete ladders without making them recurring propagation
work. A closing source census catches late promotions that raced the locked object work. The default
is a locked dry-run, retries are bounded, and written markers are the per-rung resume checkpoint.

`pipeline/validation/vegetation_exact.py` is the final source-of-truth gate. Day-marker census is
necessary but insufficient: release duplication can change `release_count`, and an old file can be
schema-valid while carrying stale values. The exact gate reads the governed export projection in
the established 200-cell batches, compares all 12 z13 fields in canonical grain order, re-derives
z9/z5/z0 with the production transform, and checks each marker's part and row counts against the
physical objects. It also checks settled source-empty days across all tiers. The source's promoted
last day and the lane's settled coverage last day are separate inputs so publication lag never
hides promoted leading-edge rows or asserts premature absences. Source-empty days after that settled
boundary must remain missing at every tier: data, absence, conflicts, or incomplete objects there are
all extra state. Settled absence is proven by decoding all four markers and comparing their complete
payloads, not merely by seeing marker keys. A second pass over every governed source day fingerprints
the full 12-column projection again, catching mutable licensing, coordinate, or release-selection
inputs that a cell/day/count census alone cannot see.

`parquet-retract-vegetation-absences` is the narrow inverse for premature leading-edge absence
claims. The operator names each day explicitly and must supply the current settled cutoff; every day
must be after that cutoff. The command proves the governed source remains empty, accepts only
`missing`/`absent` rung states, requires identical evidence across every present marker, takes the
whole-ladder lock, and removes z13/z9/z5/z0 with bounded retries and per-rung verification. Dry-run
is the default, and a partially completed apply resumes from the surviving markers.

## `vegetation_forward.py` — persisted NDVI to the full governed ladder

The NDVI ingest callback promotes only the exact deduplicated cell-day pairs accepted by that run.
Registration commits before object publication, then the affected-day query reads those pairs back
from the new source release; a caller-provided day is never enough evidence to author Parquet.
Each day goes through `fill_one_lane_day`, so z13 and z9/z5/z0 share the ordinary whole-ladder
session advisory lock, pruning order and completion-marker rules.

Forward work is bounded twice: registration batches both cells and days, while publication caps
non-checkpointed days and checks its 600-second budget before starting each additional day. All four tier markers must carry at least the monotonic
governed observation-count revision before a day is skipped. That revision is read after the
registration commit, so concurrent releases cannot give a later day rewrite an older checkpoint
than a release already visible to its full-day export. Contention and failures retry with a bounded
exponential delay; subsequent callbacks resume from the markers. The affected-day query
can only return a governed data day, so the forward path never invents calendar absences beyond the
governed source.

A marker payload alone is not a forward checkpoint. While holding the lane-day lock, every rung
must classify as physical `data`, its part indexes must be exactly `0..part_count-1`, and its marker
revision must be current. Marker-only, truncated, surplus and absence-conflict rungs are rewritten
or fail loudly; none can strand a touched day behind a false resume signal.

Two kinds of preparation read remain release-wide rather than pretending to be batch-bounded: the
corpus digest defines source-release identity and is repeated after materialisation, while the
governed observation count orders concurrent completion-marker revisions. All run under the
120-second PostgreSQL statement timeout and the whole preparation has three bounded attempts. The
600-second budget applies to object publication after those reads; summaries and runbooks must not
describe it as an end-to-end wall-clock cap.
The corpus is digested again after materialisation and the transaction refuses if it moved between
the two reads, so a READ COMMITTED registration cannot label observations from one raw revision
with another revision's source-release checksum.

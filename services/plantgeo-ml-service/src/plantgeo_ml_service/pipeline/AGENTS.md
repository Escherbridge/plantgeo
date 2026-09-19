# pipeline (L3)

Reads observed partitions, builds leakage-gated features, runs the estimators and writes
`kind=forecast` partitions with their receipts. `strategy_selection.py` and
`strategy_label_mapping.py` live here because they read and write local artifact bundles.

Import rules: may import `foundation`, `method` and `warehouse`; may NOT import `planes` or
`interface`.

Every read is bounded at the boundary: date range, zoom rung, row limit, horizon count. Every
feature respects its producer's publication lag, because leakage is an algorithmic bug
(`engineering-principles.md` section 3).

## The bucket I/O layer (phase 2A)

`object_store.py`, `duckdb_session.py`, `observed_reader.py`, `availability_publisher.py` and
`expert_labels.py` are everything this service knows about the warehouse bucket. None of them
imports `planes` or `interface`, and the lattice test enforces that.

### `object_store.py` -- every write produces a receipt

A receipt names the key, the relative path, the tier, the day, the row count, the byte count and the
SHA-256 of the exact bytes uploaded. It exists so a later reader can bind a day's parts by key and
digest without downloading one, and so a run that half-finished is legible after the fact.

**Three refusals worth naming, because each has a silently plausible wrong answer.**

- An EMPTY table is refused. Emptiness already has two names in this layout -- a governed absence at
  the base rung, a derived-empty completion marker above it -- and an empty part file is a silent
  third claim that no reader classifies.
- A base-rung table that NULLS a column only the coarse rungs may null is refused. `cell_id` and
  `observation_checksum` are nullable in the Arrow schema solely so coarsening can null them; a NULL
  at the base is a defect that used to fail loudly before the zoom axis existed.
- A relative path that would escape the configured prefix is refused. The prefix is what keeps a dry
  run inside `ml/scratch/<date>/` instead of inside the published warehouse.

Nothing here falls back to another store on a refusal: a refusal is the answer.

**The schema is the registry's, never the caller's.** `write_partition` takes `(layer, kind)` and
looks the contract up through `warehouse/streams.stream_schema`, exactly as the sibling's
`get_stream_schema(layer, kind)` does. The optional `stream=` argument exists only so a test can
state which contract it believes is in force; a value that is not the registered one is refused
rather than honoured, because a partition written under a caller's own schema is unreadable by every
reader that trusts the registry.

**The sort is part of the write.** `conform_to_stream_schema` selects, casts AND sorts to the
stream's grain. Without the sort a partition's bytes depend on the order the caller happened to build
its rows in, and NFR 1 (same artifact, same inputs, same seed -> byte-identical partition) is not
provable. `tests/fixtures/parity/streams.json` pins the sha256 of the bytes BOTH services write for
one fixed out-of-order table, so a sort dropped on either side fails the sweep.

**The two retraction guards, and why the second one has a name.** A write and a governed absence may
not make opposite claims about one day: `write_partition` refuses over an existing absence marker and
`write_absence_marker` refuses over existing parts, both with `GovernedAbsenceConflictError`, because
retracting either claim is a manual admin action. At `part_index == 0` the write clears the day's
completion marker first -- THE RETRACTION POINT. Every export writes its parts contiguously from 0,
so `part-0` is the first byte of a new export and the moment the previous one stops describing this
day. Clearing before the upload means a failure there leaves the old export and old claim both
intact, which is the safe direction; clearing after would leave a completed claim over half a new
export. A later part index clears nothing: a continuing export must not retract its own day.

`InMemoryObjectStoreBackend` is not a convenience. No test in this service touches a network, so the
in-memory backend is how the writer, the reader and the publisher are all proven end to end.

### `duckdb_session.py` -- bounded, spill-free, and never a glob

`max_temp_directory_size='0GiB'` is NOT a tuning knob. With spilling enabled an over-budget query
eats the disk and takes unrelated processes down with it; with it disabled the same query raises in
about a second. The memory ceiling comes from config (2 GB, the spec's whole daily-run budget) and
is validated at config time, because a `SET memory_limit` DuckDB cannot parse leaves the session
unbounded rather than failing.

Both extensions are LOADED and never INSTALLED, from a directory the image pre-installs. `INSTALL`
would be a network fetch and a filesystem write on a run path, and in the image `$HOME` is
`/nonexistent`. `extension_directory` is set as a SETTING rather than a connect-time config, matching
the sibling, so one pre-installed directory serves both services.

`read_parquet_keys` takes an EXPLICIT key list, never a glob. A glob would answer from whatever the
prefix happened to hold, including a half-written day that the governance check above just refused.

`apply_object_store` is the only place a credential is rendered into SQL, and it drops the DuckDB
cause rather than chaining it: the chained message quotes the rendered statement, secret included.

### `observed_reader.py` -- a refusal, not a narrower window

`read_lane_window` refuses five ways by name: `window_inverted`, `window_too_wide`,
`below_history_floor`, `beyond_publication_lag` and `day_not_governed`. The caller writes the reason
into whatever it was building; a bare `False` is not actionable.

**Why an unresolved day refuses instead of shrinking the window.** Silently dropping a day the
bucket does not govern turns a leakage guard into a data-quality surprise the caller never sees, and
a feature built over a quietly shorter window scores better than the live lane ever can. A day is
answerable only when it is `data` or `absent` -- `incomplete` and `conflict` are both refusals, and
the rule is spelled as membership in `COVERED_PARTITION_STATUSES` rather than as `!= "missing"`,
because a negation silently accepts whatever status is added next.

The listing is bounded by the window: only the month prefixes the window touches are listed, never
the whole tier.

### `availability_publisher.py` -- writing a partition does not publish it (FR-4a)

The generation object goes up FIRST and is content-addressed, so a pointer that never advances
leaves a complete, verifiable, unreferenced object rather than a dangling reference. The pointer is
then advanced by compare-and-set on its ETag: `IfNoneMatch: *` to create, `IfMatch: <etag>` to
advance.

**One retry, then a receipt.** A publisher that looped would livelock against a peer publishing the
same lane. A lost race is recoverable without rebuilding a byte: the generation is immutable and
content-addressed, so the next turn re-reads the pointer and re-points at the same object. The
refusal returns a `PublicationReceipt` with `outcome="lost_race"` -- never `None`, never an
exception a caller can mistake for a transport failure.

**The pointer is written through the ObjectStore's own key resolution.** `publish_generation` passes
`store.absolute_key(availability_pointer_path(...))` to the pointer store, and `BotoPointerStore`
holds no prefix of its own. A pointer store with an independent prefix is how a scratch dry run
advances the PRODUCTION `_LATEST.json` while every other object it wrote stayed in the scratch root.

**A lost race rebinds; it never re-puts.** On a 412/409 the head is re-read and the pointer payload
is REBUILT with the newly read generation as `prior_generation_key`. Re-putting the payload that was
built against the old head is the lost update the compare-and-set exists to prevent. Four heads are
refused outright rather than succeeded: a body that is not a pointer, a pointer for another lane,
one whose digest is already this generation (re-pointing would name it its own predecessor), and one
past `POINTER_MAX_BYTES` -- a larger body at the pointer key is not a pointer and is not parsed to
find out. A refused or exhausted race leaves an `availability/pending/day=<iso>.json` claim, which is
what the sibling's retry sweep looks for.

**`compare_and_set` returning True proves nothing on its own.** A store that ignores `IfMatch`
answers 200 to a request it never gated, which silently degrades compare-and-set to
last-write-wins. Every advance is therefore read back and compared to the bytes written and to the
etag that was expected; a mismatch returns `outcome="conditional_put_unsupported"` and latches the
process, so the next lane cannot overwrite a peer either. The latch is process-wide on purpose: a
per-call refusal would let the very next publish do the damage.

**The generation object is put with adopt-exact-replay.** Its key names its own content digest, so a
key already holding identical bytes IS this object and the retry is a success; a key holding
different bytes under a content address means the address is a lie, and
`ImmutableObjectConflictError` refuses rather than overwriting.

**What p2a does NOT write, and who owes it.** A `kind=forecast` lane root's BOOTSTRAP marker and
receipt (`<lane_root>/availability/bootstrap/_BOOTSTRAPPED.json`, sibling
`availability_index.bootstrap_availability`) are not written by this slice. `AvailabilityConfig`
requires the receipt and the publisher carries it into every pointer, but nothing here creates the
lane's generation-zero history. The lanes slice owes that bootstrap before acceptance 5.2 can be
met: until it exists, `parquet_ops/availability_coverage.py` has no bootstrapped lane to read a
`selectable_days` answer through, however many generations this publisher writes.

`availability_lane_root(layer, "forecast")` and `availability_lane_root(layer, "observed")` are
different prefixes, so the other session's `vegetation_partition_promotion.py` (observed side) and
this publisher never contend for one pointer. A test asserts that directly.

### `expert_labels.py` -- read only; the export is an agri-side verb

The 28-row plane is exported once from Postgres by `agri-service ops export-expert-labels`, which is
a production mutation and waits for an owner go. This service only reads it, and refuses by naming
that verb when the object is absent, rather than returning zero labels -- an empty label set and an
unexported release are different facts and a trainer must not confuse them.

`condition_envelope` is carried as canonical JSON TEXT rather than an Arrow struct: the envelope's
key set is an open vocabulary (`ENVELOPE_TERM_SUPPORT`), and a struct would freeze today's seven
terms into the file's own schema.


## The weather-forecast cell inventory is warehouse data, not configuration

`PLANTGEO_ML_FORECAST_CELLS_KEY` names a bucket object holding the cells the lane is fetched for;
`read_forecast_cells` reads it under `MAX_FORECAST_CELL_INVENTORY_BYTES`. A deployment variable
holding hundreds of coordinates is a configuration nobody reviews and a lane nobody can correct
without a redeploy. A turn with no key configured reports the lane `refused` with
`forecast_cells_unconfigured` and NEVER `skipped`: a lane that will never run must not arrive under
a word that reads like somebody decided it. A key naming a broken object refuses under
`ForecastCellInventoryError`, which is a different operator action and so a different type.

## One lane's fault stays in one lane

`_fire_risk_outcome` catches `ObjectStoreError` as this lane's refusal rather than raising the
turn's `PredictDailyInfrastructureError`. Every lane after it reaches the same bucket through its
own calls and is perfectly able to say so itself; raising made one lane's bad read end the turn.
Receipt details carry the stable code `error_code_for` derives from the exception TYPE, never the
message: a receipt is compared against other receipts, and a detail carrying a key or a wrapped
provider string is a field no two turns agree on even when they failed the same way.

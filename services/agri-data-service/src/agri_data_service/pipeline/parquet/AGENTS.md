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
`OBJECT_STORE_PREFIX` sits *outside* the frozen `layer=.../kind=...` layout and exists so one
bucket can hold an isolated sandbox beside the real warehouse.

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
- **Data and absence refuse to coexist, in both directions.** `write_partition` refuses a day
  carrying an absence marker; `write_absence` refuses a day already holding a part file.
  Retracting either side is a manual admin action (§0.21.5) — there is deliberately no API here
  that does it. Reading a marker's evidence back is S17's concern; the backend seam has no `get`
  yet on purpose.
- **A receipt carries the sha256 of the uploaded bytes.** That is an upload-integrity digest, not
  a cross-version reproducibility claim: `pq.write_table` stamps the writing pyarrow version into
  the file, so the same rows written by a different pyarrow need not be byte-identical.

## This path is synchronous, deliberately
`python.md` calls for async I/O, and this module is sync. It runs from the ingestion CLI and the
Railway cron, never on the Sanic event loop; boto3 has no async client, and wrapping it would add
a thread pool for no caller that exists. **If a route ever needs it, run it in an executor rather
than making this async.**

## `lane_registry.py` — why it is here and not in `pipeline/lanes/`
It imports all eleven lane modules. A module *inside* `pipeline/lanes/` that did that would
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

### Three lanes refuse historical backfill by construction
`evacuation_zones_day_export.sql`, `watersheds_day_export.sql` and `soil_survey_day_export.sql` all
**broadcast** the caller's day onto every row and apply no date predicate — Postgres holds no record
of what those current-state feeds published on any day but today. Filling one of their historical
gaps would stamp today's state onto a past date, which is fabrication, not backfill. They carry
`window_kind="current_snapshot"` and `lane_window` collapses them to the newest settled day. **A
snapshot day the cron missed is lost, deliberately.**

## `gap_fill.py` — the driver a Railway cron runs
- **Newest-first is the design, not a preference.** A newly published day *is* the newest missing day,
  so one ordering serves the incremental tick and the backfill with no second job to keep in sync.
- **Round-robin across lanes, one day per lane per round.** Sequential order would let
  `fire-detections`' ~9,400-day window eat a whole tick before `signal` wrote anything.
- **A governed-absence marker counts as covered.** `missing_partition_days` already treats it that
  way; that is what stops a genuinely empty day being re-attempted every hour forever.
- **The absence payload claims only what the run observed.** It states that the day-scoped export
  query over *this warehouse's own tables* returned zero rows, and explicitly says the upstream was
  not contacted. Reconciling against the live source is `pipeline/validation/<slug>.py`'s job.
- **A remaining backlog is not a failure.** Only a lane that raised, whose listing failed, or whose
  absence marker was refused sets a non-zero exit — matching `jobs-pulse`'s exit philosophy, and safe
  under `restartPolicyType: NEVER`.
- **The session is rolled back after every day, success included.** These reads are read-only, so
  holding one snapshot across a 600-second tick would pin a production xmin horizon for nothing — and
  after a failed statement, that rollback is what makes per-lane isolation real rather than asserted.
  `SET LOCAL statement_timeout` dies with it, so the 120 s pin is re-applied per day.

## Reading it back
`polars_storage_options(credentials)` returns the Polars/`object_store` connection dict for
`pl.scan_parquet`/DuckDB `httpfs`. It is credentials in a dictionary — never log the result.
Reading is otherwise S18/S20's concern; this module writes and lists, and deliberately offers no
`get_bytes`.

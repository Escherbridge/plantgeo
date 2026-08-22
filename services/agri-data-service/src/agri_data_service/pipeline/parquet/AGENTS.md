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

## Reading it back
`polars_storage_options(credentials)` returns the Polars/`object_store` connection dict for
`pl.scan_parquet`/DuckDB `httpfs`. It is credentials in a dictionary — never log the result.
Reading is otherwise S18/S20's concern; this module writes and lists, and deliberately offers no
`get_bytes`.

# Local forecast artifact boundary

`artifacts.py` accepts an already admitted deterministic sampled forecast and its
exact captured source payload. It does not acquire sources, admit licences/models,
operate production storage, schedule work, or register a live lane. Run timestamps
for source fetch/admission remain caller-supplied assertions; the source hash binds
bytes, not semantic admission. The prepared run's publication field is provisional
and never makes a prepared run visible to the serving plane.

Preparation requires complete sample × variable × hourly instant inventory for a
half-open UTC window of at most ten days. Every absent value must have an explicit
status. Limits are 256 samples, 50,000 rows, 8 MiB captured source, 16 MiB compressed
and declared uncompressed Parquet, and 256 KiB manifest. Each run references exactly
two immutable blobs (source and Parquet); there is no unbounded partition scan.
No claim of native cell area or interpolation follows from sample coordinates.
The registered `warehouse/schemas/weather_forecast.py` Arrow schema stores UTC
timestamp columns and numeric nullable values even when every value is missing.
Reads require exact schema/metadata equality. This is a separate forecast product;
the local artifact path does not call the generic observed-to-Monte-Carlo schema
extension or publish weather observations under a forecast label.

Content hashes name blobs. The product/run manifest is immutable and binds hashes,
sizes, inventory and missingness counts. Files are flushed/fsynced before atomic
replacement under a bounded file lock. A crash before manifest completion leaves
only unreachable blobs; retry verifies rather than overwrites existing immutable
bytes. No automatic garbage collection deletes such blobs. This protects process
interruption; whole-filesystem power-loss durability depends on filesystem semantics.

Activation verifies the run and conditionally replaces a product active pointer
under the same file lock. Repeating the already active target is idempotent; another
writer's pointer conflicts. Explicitly activating a previous known run with the
current pointer hash is rollback. Pinned readers never consult this pointer. Local
root is trusted configuration, but descendant symlinks/path traversal are refused;
hostile concurrent filesystem mutation is outside this local development boundary.

Activation creates an immutable publication receipt bound to the prepared manifest
hash before replacing the active pointer. Its commit UTC time (system clock by
default, explicit clock for local fixtures) becomes the returned run's publication
time and must follow admission. `read_run` is local preparation/replay inspection;
the serving plane calls `read_published_run`, which requires the receipt and verifies
the bound manifest. A crash between receipt and pointer can leave a committed run
that is not active; its explicit run ID remains readable and retry completes the
pointer transition. Replaying/rolling back never changes the original publication
instant. A missing publication receipt is not_yet_generated, while missing/corrupt
files behind an existing receipt are upstream_unavailable.

`planes/weather_forecast.py` resolves required requested zoom once and returns the
resolved tier as request context. All tiers use the same bounded point inventory;
this does not invent spatial aggregation, footprints or additional resolution.
Selected-location results preserve requested coordinates and timezone, return actual
sample distance, and choose no sample beyond the explicit distance cap. Outside
distance support means outside_domain, not proof that the provider has no forecast.
Missing runs return not_yet_generated; integrity failures return upstream_unavailable.
Staleness is relative to initialization and an explicit reader age policy, retaining
the pinned run values. Windows outside its published inventory refuse exactly.

The capability read is a local-serving discovery seam for `combined_local`. It reads
the product-owned `active.json` pointer, verifies the pointer's manifest digest and
the immutable publication receipt, and only then returns run identity, valid-time
coverage, variables, lifecycle, sampled support, and bounded inventory counts. A
missing pointer is `not_yet_generated`; malformed or incomplete state is
`upstream_unavailable`. Neither state exposes run metadata that was not verified.
Remote catalogue discovery remains a separate serving integration because this HTTP
blueprint does not yet own production object-store credentials or a remote reader.

## Exact provider source adapter

`source.py` normalizes the Open-Meteo Single Runs endpoint for the fixed ECMWF IFS
model and the fixture-probed UTC 00 cycle. The exact request pins initialization;
provider issue time remains unknown and is never inferred. Its single sample uses
nearest-cell selection with elevation adjustment disabled (`elevation=nan`), and
the returned provider coordinate is distinct from the originally requested place.
The bounded fetch is at most 1 MiB and fifteen seconds. Normalization requires all
240 hourly instants and adds mathematically derived u/v components with explicit
missingness. Lead-zero precipitation/gust intervals preceding initialization carry
no forecast value. The retained real fixture and receipt live under
`tests/fixtures/weather_forecast/`.

The shared bounded transport preserves `Retry-After`. `source.py` remains a narrow
payload adapter, while `duties.py` consumes the preserved value and stores the
resulting cooldown in durable work state. This adapter installs no scheduler or
production writer.

## Remote publication and retained-run catalogue

`remote_artifacts.py` copies only a locally verified prepared run. Source bytes,
Parquet bytes, manifests, and publication receipts use SHA-256 object names and
immutable creates. A product-scoped set-once commit maps the exact run ID to its
manifest and first publication receipt. That commit lands before the bounded
product catalogue advances by ETag compare-and-swap, so interruption after commit
and before catalogue publication is recoverable without changing publication time.
Publication verifies product, provider, model/version, initialization, optional provider
issue, valid-time window, row counts, byte counts, schema, missingness, and every
digest across manifest, receipt, catalogue, source, and Parquet. Older runs cannot
supersede a newer active run through ordinary publication. Rollback names a retained
target and the expected active run, then conditionally changes the whole-run head;
pinned readers never mix runs.

Each artifact retains at most 50,000 rows, 8 MiB of source, 16 MiB of Parquet, and
256 KiB of manifest. The visible catalogue retains at most 16 runs and the sum of
their declared bytes under the reviewed ceiling; a stricter deployment policy may
lower either bound. Retired entries author delayed prune work. Cleanup removes only
the product-scoped run commit after re-reading the current catalogue. Global
content-addressed source, Parquet, manifest, and receipt objects are deliberately
not deleted by this product-local code because another product or in-flight commit
may share them. Physical blob collection requires a separately proven global
reference census.

## Durable duties, currently unregistered

`duties.py` exposes three independent APIs and UTC cron descriptors without editing
the shared executor or lane registry:

- forward (`17 * * * *`) discovers at most four exact run identities, durably
  records them before materialization, and publishes at most one admitted run;
- repair (`47 */6 * * *`) verifies at most four retained runs, authors exact
  recoverable work rather than only reporting damage, and can drain at most two
  delayed product-commit prunes; and
- status (`7 */3 * * *`) publishes one content-addressed coverage/status document
  with active and retained runs, half-open valid-time availability, pending work,
  and provider cooldown, then conditionally advances its status pointer.

The work queue holds at most 48 deterministic items. Provider `Retry-After` is
bounded to 24 hours and stored with its reason and expiry, so a restart cannot turn
backoff into a tight retry loop. Ordinary failures receive bounded exponential
retry times. Forward materialization receives a `ForecastCandidate` carrying the
exact product, provider, model, run, initialization, and issue time; the prepared
artifact must match all of them before remote publication.

No production endpoint, credentials, provider entitlement, model admission,
Railway service, executor registration, or active cron is supplied here. Integration
must inject the existing conditional S3 storage ports and an admitted discovery and
materialization implementation. PostgreSQL is not part of this plane.

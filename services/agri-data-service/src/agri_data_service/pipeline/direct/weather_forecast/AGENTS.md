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

The shared bounded transport does not expose Retry-After; persistent cooldown is
therefore still unresolved. This adapter installs no scheduler or production writer.

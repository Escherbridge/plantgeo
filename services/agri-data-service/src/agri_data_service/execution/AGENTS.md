# Execution modules

`source_ingestion.py` is the phase-one operational vertical slice for a governed, locally captured current-observation release: a bounded GeoJSON payload is structurally validated, checkpointed locally, then persisted idempotently as a source release, content-addressed artifact, and validated release set. It is intentionally not an upstream fetcher, generic data loader, forecast, trainer, or public prediction publisher. See `docs/data-ingestion-and-serving-contract.md` for the server/local ownership boundary.

Source-ingestion checkpoint v2 binds both the complete reviewed plan and the release-set content checksum. New release sets must be populated while `draft` and transition to `validated` only after their membership is flushed, because the warehouse trigger freezes membership after validation.

`source-ingest` requires `LOCAL_SOURCE_LOADER_DATABASE_URL` to name `plantgeo_loader` on the dedicated local Compose target at `127.0.0.1:5442/plantgeo`; it rejects the `plantgeo_owner` bootstrap role and never falls back to the service `DATABASE_URL`. Before any checksum, checkpoint, or artifact write, it performs a bounded whole-document GeoJSON custody scan (50,000 JSON nodes, depth 32) and rejects canonicalized credential field names/suffixes plus Bearer/Basic authorization strings. It does not silently redact an immutable source artifact.

`promotion.py` is an offline semantic lineage bundle contract for already validated phase-one release sets. It re-applies the same bounded GeoJSON custody validation to embedded source artifacts, verifies hashes and supersession closure, and creates only a trigger-safe draft → membership → validated restore plan. It is not a general `pg_restore` wrapper, database exporter, restore CLI, or Railway job; those remain a separately reviewed private-control-plane integration.

`historical_backfill.py` owns deterministic, bounded NASA POWER daily request and response contracts for the initial four-year meteorology baseline. It validates the exact four-calendar-year window, canonical sampling-point plan, per-source query, response payload size, UTC observation timestamps, missing values, coverage accounting, a checksum-bound complete local receipt checkpoint, and raw response cache. The cache is written only after complete validation and before a warehouse transaction, so retried writes never re-request a successful source response. A later NASA finalization can only rebind a complete source replay to an advanced release-set as-of time; it never refetches or rewrites source receipts. It never carries credentials, opens a database connection, selects an ingestion geography, or publishes to Railway.

`historical_parquet.py` converts only a complete local NASA raw-receipt set into an immutable, compressed daily Hive-partitioned Parquet dataset. It stages one bounded source-cell file at a time, caps DuckDB to one thread and 1 GB with a build-local spill directory, and atomically publishes a manifest-bound dataset. An interrupted conversion reuses its single target-bound build directory only after each staged cell's row count, key, and payload checksum are revalidated against the raw receipt; ambiguous or mismatched staging fails closed. Successful publication removes staging. It is intentionally a local cold-history store; it never requests an upstream API, writes PostgreSQL, or promotes a full history to Railway.

`historical_era5.py` owns cache-first CDS capture for the governed ERA5-Land plan. It treats each calendar month as one immutable ZIP artifact, validates every planned point/variable/day before advancing the durable checkpoint, and requires local CDS credentials only for a missing cache entry. Its requested one-degree points remain point samples; they never claim the product's native 0.1-degree grid or acre-scale precision.

`historical_era5_parquet.py` turns only a complete ERA5 receipt set into an atomic Zstandard-compressed daily Hive lake. It re-parses the locally cached monthly ZIPs without a provider or database call, emits a bounded daily row set, and ties the manifest to both the exact plan and receipt manifest. It is the compact cold-history representation and does not promote history to Railway.

`historical_usdm.py` owns bounded U.S. Drought Monitor medium-resolution ZIP capture. It accepts only reviewed Tuesday releases in the four-year plan, verifies the exact WGS84 shapefile package/schema, preserves only native D0–D4 polygons without inferring absent classes or normal conditions, and writes checksum-bound weekly checkpoints. It is not an analysis-grid interpolation or local-condition source.

`historical_writer.py` persists only complete, checkpointed NASA POWER source cells, ERA5-Land monthly point samples, and USDM weekly vectors through the dedicated local loader session. It owns lineage, raw receipts, crosswalks, normalized observations, complete coverage audits, and release-set finalization, but commits nothing itself. ERA5 artifacts retain a checksum-bound local-cache pointer rather than inlining large ZIPs; its 9-km source resolution is context metadata and its response remains a requested point sample. USDM keeps the raw canonical geometry checksum while its reviewed transform may use PostGIS `MakeValid` to store a valid serving multipolygon; that behavior must be reflected in the immutable transform version. The caller owns transaction boundaries and advances a checkpoint only after commit. It is not a Railway receiver or a scheduler.

`historical_promotion.py` carries only typed, content-addressed historical lineage across the local-to-Railway boundary. Its 8 MB chunk and raw-artifact limits are deliberately aligned with the reviewed USDM acquisition ceiling; a 50-million-record, 20,000-chunk root remains bounded but must be streamed/spooled rather than materialized in memory. Grid crosswalks declare immutable `spatial_support_kind`; a caller must preserve that support and native resolution so regional cells cannot be represented as acre-scale observations.

`geospatial_capture.py` is a local, database-free custody boundary for reviewed
public geospatial requests. Its frozen plan allowlists exact HTTPS hosts,
expected feature identities, byte checksums, source support, licence status,
and inference ceilings; it publishes a cache only after the complete receipt
set validates. A checksum change is a new provider release requiring review,
not an automatic refresh. The active pilot plan contains only explicitly open
sources; no blocked source may enter a plan. Consumers use the exact byte
buffers returned by cache revalidation rather than reopening raw files. The
pilot's WUI request is a pinned property-bounding-box AOI query, not a
hand-selected object list.

`geospatial_pilot.py` consumes only a fully revalidated all-open capture and
writes one immutable local release set. The Hillside to Hollow subject is a
named OSM property with neighborhood support, never a cadastral parcel. Outputs
are facts, PostGIS-derived context, and known evidence gaps; the module has no
publication, forecast, strategy, selection, or recommendation path. Its
analysis receipt hashes the exact executed SQL and bind parameters, input
feature checksums, PostGIS version, output schema, disclosed rounding rules,
and the conservative year-end convention used for the WUI vintage minimum-age
lower bound.

`strategy_selection.py` is a database-free, evaluation-only causal benchmark
for a strict external intervention-label bundle. It compares matched
difference-in-differences, cross-fitted AIPW, a doubly robust ridge learner,
and an arm-specific ridge sensitivity model on expanding-time,
held-out-spatial-block folds. The canonical JSON artifact contains
coefficients, diagnostics, and both the finalized label-release checksum and
exact trimmed UTF-8 bundle-text checksum rather than executable pickle bytes.
PostgreSQL recomputes the digest from the authoritative JSONB export before
training validation. Hard support, availability, overlap, balance, agreement,
and conservative-effect
gates produce an explicit abstention; this module never publishes a forecast
or recommendation.

`strategy_label_mapping.py` is the database-free custody preflight before any
external intervention rows may be normalized. Its versioned manifest permits
only direct source-field references and requires the named source release,
outcome definition, treatment/control risk set, subject and assignment
windows, spatial block, raw evidence lineage, and time-honest covariates. An
incomplete manifest reports every missing path and has no checksum; only a
complete canonical mapping receives a SHA-256 digest. The module deliberately
has no row transform or database path, and rejects Boise forecast actuals
because forecast-error labels cannot establish intervention effects.

`covariate_wind_model.py` is a database-free-at-the-core, evaluation-only direct
multi-horizon ridge forecaster over the `0016` covariate layer. It reads the
pinned covariate vector and the WS2M target through their own availability-gated
SQL functions, fits one standardized closed-form ridge per horizon step, and
calibrates a p10-p90 band from residuals on a held-out calibration window that
ends strictly before the forecast origin. The split is temporal only -- fit
window, then calibration window, then a single held-out origin -- so no target
day ever appears in the window that produced the model scoring it. It never
writes to the warehouse, never joins a serving or publication surface, and never
produces a receipt: its output is a JSON report labelled `evaluation_only`.

Its scores prove the framework runs end to end; they are not an operational or
life-safety forecast. Interval coverage is an empirical residual band, not a
calibrated confidence bound. The comparison baseline is the existing
`daily_increment_bootstrap_v1` iteration read through
`agri.forecast_iteration_evaluation`, at exactly the same origin and horizon
steps.

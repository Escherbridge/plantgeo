# Source-direct Parquet producers

Modules here fetch upstream products and publish registered Parquet schemas without staging
ingested rows in PostgreSQL. PostgreSQL may still supply the shared session-scoped lane-day
advisory lock during the transition; it is coordination, not a data sink.

## Water gauges

water_gauges.py partitions NWIS instantaneous values by the publisher-named day: the first ten
characters of updatedAt, before converting the timestamp to UTC. Records whose parser had to
substitute the wall clock are not source observations and must be dropped before this transformer.

The nominal base grain is (site_number, observed_at), but four reconciled historical days contain
duplicate physical rows at that grain in both PostgreSQL and Parquet. A completed partition keeps
every such row and its provenance. A repeated source grain refreshes source fields only when it maps
to exactly one existing row; a match to multiple historical rows is ambiguous and fails without
changing or dropping either. Unmatched published rows are retained byte-for-byte and unseen grains
are appended. New direct rows truthfully use geometry_linked=false, a null availability time, and
the direct fetch instant as ingested_at.

Publication goes through gap_fill.fill_one_lane_day: z13 is written and pruned, z9/z5/z0 are
derived and marked, and the z13 completion marker lands last. An object-store failure may therefore
leave z13 incomplete. The same process replays its pre-mutation intended table; a later process
reads every physically present row, adds the current fetch only when every matching grain is
unambiguous, rewrites one complete z13 part, and lets the shared finalizer prune the residue. It
never grain-deduplicates an incomplete partition: that would make an interrupted generation
indistinguishable from the legitimate duplicate source rows already proven by reconciliation.

Every successful tick re-reads z13, proves the complete duplicate-preserving table, and checks
every incoming source field at every incoming grain. Completion-marker status alone is not forward
writer evidence.

## Fire detections

`fire_detections.py` refreshes settled NASA FIRMS days in the dedicated
`layer=fire-detections/kind=observed/zoom=...` namespace. It intentionally leaves
`ingest/firms.py`, `ingest/commands.py`, and `ingest/runner.py` unchanged, so the existing
PostgreSQL FIRMS ingestion path remains available while the direct writer is proven.

The writer is bounded in four independent dimensions:

- one exact UTC day per FIRMS request, over a maximum five-day lookback;
- a fail-closed 50,000-record ceiling per day;
- a maximum number of days per process that cannot be smaller than the lookback window; and
- finite exponential retries for source and object-store failures.

Every lane-day uses the same session-scoped advisory-lock identity and the same base-write,
coarse-tier derivation, prune, and completion-marker finalizer as the Parquet gap-fill/drain.
The lock is acquired before HTTP and held across every bounded publish retry; contention performs
no fetch, while each actual retry refetches once so stale source cannot overwrite a newer release.
The adapter rolls back the statement-timeout transaction before HTTP because the session lock
survives rollback. Retry waits are finite and capped (60 s base, 300 s max, 3,600 s contention).

Normal hourly runs consult FIRMS' live availability table and revisit the bounded settled window
from every applicable product even when completion markers already exist, but never before the
shared `FIRE_DETECTIONS_DIRECT_WRITER_START_DAY` ownership boundary (2026-08-25).
`FIRE_FORWARD_START_DAY` and `--forward-start-day` may repeat that value but cannot move it.
SP records supersede NRT
records with the same native identity, matching the historical ingest contract.
That is the forward-refresh contract: a late NRT revision inside the window must not be hidden by an
earlier successful write. A complete zero-row constellation response records a governed z13 absence;
it never writes an empty Parquet file.

FIRMS may later revise an initially empty direct-owned day to contain detections. The adapter is
already running inside that day’s shared advisory lock, so a complete non-empty response explicitly
retracts the z13 absence immediately before the first base write. The inverse transition remains
fail-closed: a later empty response never removes published data or governs it absent automatically.

The Railway entry point is `python -m agri_data_service.pipeline.direct.fire_detections`, configured
by `services/agri-data-service/railway.fire-detections-forward.json`. Required runtime variables are
the ordinary object-store settings, `LOCAL_SOURCE_LOADER_DATABASE_URL` (or its existing fallback),
`INGEST_BBOX`, and `NASA_FIRMS_KEY`. Optional `FIRE_FORWARD_*` variables tune the bounded lookback,
day count, record cap, retry series, and contention timeout. `--force-day YYYY-MM-DD` intentionally
re-publishes one already-settled day inside that same bounded NRT window for a one-day operational proof.

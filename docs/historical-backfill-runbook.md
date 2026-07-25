# Historical ingestion runbook

## Status and definition of done

The local Podman warehouse and local Parquet lake are the acquisition,
validation, and full-history plane. Railway is a rolling serving plane. A
four-year historical release is complete
only when every approved source has a validated release set whose UTC coverage
starts exactly four calendar years before its successful end date, contains no
unexplained coverage gaps, retains immutable source receipts, and can be read
through `agri.v_signal_timeseries_contract(as_of_time, release_set_id)`.

The initial required source set is:

| Source | Scope | Cadence | Required retention |
| --- | --- | --- | --- |
| NASA POWER daily | Explicitly approved sampling points; do not describe a partial region as global coverage | Daily UTC | Raw bounded response, normalized daily signals, and point/signal coverage audit |
| U.S. Drought Monitor | United States national vector release | Weekly | Native polygons, geometry checksums, and source-level feature coverage audit |
| ERA5-Land | Approved retrospective cells/variables | Daily | A separately accepted CDS acquisition plan and product-latency evidence |

NASA POWER data is requested only with `time-standard=UTC`; repeated sampling
point requests are avoided. Its API response geometry is a point, while the
plan's surrounding cell is only a coverage/sampling area and never a native
source pixel. U.S. Drought Monitor releases retain the official vector
geometry rather than a display grid. See the [NASA POWER daily API](https://power.larc.nasa.gov/docs/services/api/temporal/daily/)
and [U.S. Drought Monitor GIS data](https://droughtmonitor.unl.edu/DmData/GISData.aspx).

## Spatial fidelity and local acre-scale work

The continental historical release supplies regional environmental context, not
acre-accurate weather or soil measurements: NASA POWER meteorology is supplied
at roughly 0.5 by 0.625 degrees and ERA5-Land is supplied at 0.1 degrees.
The service must return each normalized observation with the source grid,
analysis and native resolutions, support kind, mapping method, and coverage
fraction. `is_acre_scale_compatible` is true only for a fully covered native
grid cell at 64 metres or finer; it is false for the NASA POWER and ERA5-Land
baseline. A client must never relabel a false value as field or acre data.

Small-area analysis is therefore an on-demand tier rather than a continent-wide
preload: users submit an explicit field/AOI and the local operator captures
reviewed, source-native high-resolution imagery and field measurements for that
AOI. Sentinel-2 10-m surface reflectance is the initial open North America
imagery target (roughly forty pixels per acre); source artifacts, cloud/quality
flags, AOI coverage, and native pixel support must be retained before any
acre-scale product is shown. It complements, and never downscales, the coarse
climate baseline.

## Governed North America baseline

`infra/local-warehouse/plans/nasa-power-na-sampling-20220430-20260430.json`
materializes the initial regional NASA POWER baseline: 2,980 one-degree sampling
points whose requested coordinates fall inside the reviewed Natural Earth 1:50m
US, Canada, and Mexico boundary. The plan embeds the exact boundary SHA-256 and
is a sampling lattice, not a claim of continuous native NASA grid coverage.
It intentionally records an acquisition as-of time; after the long-running
fetch succeeds, create a later finalization sidecar and run the NASA finalizer.
The superseded July-cutoff plan remains as failure evidence: NASA POWER's
`ALLSKY_SFC_SW_DWN` field was incomplete after 2026-04-30, so it is never used
to produce a partial release.

`infra/local-warehouse/plans/era5-land-na-sampling-20220430-20260430.json`
pins the matching ERA5-Land scope: the same 2,980 reviewed North American
locations, six daily-mean land-state variables, and 49 retryable calendar-month
source artifacts covering the exact four-year window. The CDS product is native
0.1-degree/roughly 9-km, but this acquisition explicitly requests a one-degree
output grid. It is recorded as a requested point sample and is never represented
as native 9-km or acre-scale data. The operator must accept the CDS terms in the
web form and supply `CDSAPI_URL` and `CDSAPI_KEY` before the ERA5 command is
allowed to contact the provider.

## One-time local warehouse bootstrap

The database owner must perform these gates manually; neither the application
nor the scheduler may enable extensions or create privileged roles.

1. Run `infra/local-warehouse/enable-extensions.sql` against the healthy
   Podman database as `plantgeo_owner`.
2. Apply Alembic through revision `20260720_0004` with the owner/migration DSN.
3. Run `infra/local-warehouse/create-loader-role.sql` as the owner and store
   the resulting `plantgeo_loader` DSN only in the local operator environment
   as `LOCAL_SOURCE_LOADER_DATABASE_URL`.
4. Verify that `postgis`, `timescaledb`, `vector`, and `pgcrypto` are installed,
   that the `agri` tables exist, and that the loader role has no database/schema
   create privilege.

The loader role receives only the source-lineage and append-only historical
tables. It has no schema DDL, ownership, or application-database access.

## Backfill operating sequence

1. Approve a versioned scope sidecar: exact UTC end date, four-year start date,
   provider availability cutoff, geographic boundary, materialized source-grid
   cells, required parameters, source review metadata, and transform version.
2. Fetch bounded NASA POWER source-grid responses locally. A complete response
   is first written to the checksum-bound local raw cache, then persisted to
   PostgreSQL; a failed or retried database transaction reuses that cache and
   does not re-request the upstream API. Validate JSON content
   type, byte cap, response parameter set, UTC timestamps, source missingness,
   payload checksum, and coverage count before persistence.
3. Capture one immutable U.S. Drought Monitor medium-resolution shapefile ZIP
   per Tuesday issue date with `historical-usdm-backfill`. Validate the exact
   reviewed WGS84 package/schema, native D0–D4 multipolygons, geometry
   checksums, and expected versus received source feature counts. Missing
   severity classes are not fabricated; the product does not represent normal
   conditions or unreviewed territories.
   If a complete replay has receipts later than its reviewed `release_set_as_of`,
   retain that original plan and checkpoint. Create a separate checksum-bound
   finalization record with a later as-of time, then run
   `agri-cli historical-usdm-finalize --source-plan <plan> --finalization <record>`.
   That command may only rebind a complete checkpoint with the identical source
   scope, weekly dates, and transform; it never re-fetches or silently rewrites
   the original replay.
4. A long-running NASA replay may complete after its original release-set
   as-of time. In that case, retain the complete source checkpoint and create a
   separate checksum-bound finalization record, then run
   `agri-cli historical-nasa-finalize --source-plan <plan> --finalization <record> --output-plan <final-plan>`.
   This rebinds only the release-set identity and as-of time; it does not fetch
   data or rewrite the immutable source receipts. The emitted immutable
   `<final-plan>` is required for a matching `historical-nasa-materialize-parquet`
   invocation.
5. Create all source releases and artifacts first. Insert observations, native
   feature-to-cell crosswalks, and coverage audits in the same local warehouse
   transaction. Create the release set in `draft`, attach every source release,
   then transition it to `validated` only after the complete membership and
   audits are verified.
6. Run a representative pinned read at the end-date `as_of_time`. A partial,
   no-data, failed, or unexplained missing window fails the backfill.
7. After a complete NASA checkpoint, create the local Parquet lake with
   `agri-cli historical-nasa-materialize-parquet --plan <release-plan>`. Use the
   original source plan only when no finalization was required; otherwise use the
   finalizer's emitted release plan. It
   writes compressed Hive-style daily folders at
   `.agri-local-runs/warehouse/nasa-power-daily/<plan-sha>/source=nasa-power-daily/year=YYYY/month=MM/day=DD/`.
   The manifest binds the materialized rows to the source plan and complete raw
   receipt manifest. It never contacts NASA or the database.
8. After accepting the CDS terms and configuring `CDSAPI_URL` and `CDSAPI_KEY`,
   run `agri-cli historical-era5-backfill --plan <plan>`. The command advances a
   durable monthly checkpoint only after each raw ZIP has been fully validated
   and saved under `.agri-local-runs`; a retry reuses that cache before making a
   provider request. Then run
   `agri-cli historical-era5-materialize-parquet --plan <plan>` to create the
   Zstandard-compressed daily Hive lake at
   `.agri-local-runs/warehouse/era5-land-daily/<plan-sha>/source=era5-land-daily/year=YYYY/month=MM/day=DD/`.
   Run `agri-cli historical-era5-persist --plan <plan>` to load the same
   cache-backed monthly releases into the local warehouse; it requires the
   pre-existing NASA sampling lattice and never calls CDS. If the completed
   receipts are later than the original as-of time, create a governed
   finalization sidecar and run
   `agri-cli historical-era5-finalize --source-plan <plan> --finalization <record> --output-plan <final-plan>`.
   The finalizer reuses the original raw-cache checksum rather than duplicating
   ZIPs, persists any missing local source releases, and validates the release
   set. Use `<final-plan>` for the matching Parquet materialization. None of
   these commands transfers historical data to Railway.
9. Take an external custom-format PostgreSQL backup after each completed source
   and after Parquet materialization. The Podman named volume survives ordinary
   Compose teardown, but the independent archive is the recovery boundary; see
   [the local warehouse README](../infra/local-warehouse/README.md).
10. Archive the immutable source receipts, Parquet manifest, PostgreSQL backup
   manifest, and release manifest before any promotion attempt.

## Railway promotion gate

The existing phase-one `source-ingest` and `local_publication` receiver publish
only one small GeoJSON source and an `artifact_only` pointer. They are not a
historical row transfer path. Historical promotion therefore requires a separate
versioned, chunked receiver or reviewed semantic-restore adapter that verifies
checksums, natural-key idempotency, source/cell coverage, the target migration
revision, extensions, least-privilege role, and a pinned-contract read before
advancing the Railway publication pointer.

Do not transfer the local owner connection string or raw provider credentials to
Railway. The full four-year history remains local. Railway receives a separate
hot projection containing only the most recent 365 days of source observations
plus forecast outputs; its selection boundary, release identity, source receipt
manifest, and row counts must be verified before it advances a Railway pointer.
The existing full-root historical spool is therefore not the Railway hot-data
path and must not be used for this policy. Deploy only after the local release
passes the preceding checks and the Railway target has completed the same
migration/extension verification.

## Recurring ingestion

Use a local operator-controlled schedule, not Railway Cron, Celery, or a web
replica. The recommended cadence is weekly: run after the weekly U.S. Drought
Monitor release and replay the NASA POWER late-arrival window in the same
checkpointed job. The schedule must use a whole-run lease/fencing token,
redacted logs, a bounded retry policy, a last-success freshness alert, and a
post-run coverage/pinned-read check. Install it only after the first successful
four-year load and after the operator confirms weekly versus biweekly cadence.

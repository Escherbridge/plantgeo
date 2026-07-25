---
type: retrospective
---

# Historical warehouse handoff retrospective — 2026-07-21

## Outcome at the handoff boundary

The local Podman warehouse is durable and contains the completed four-year USDM
history plus a finalized cache-first NASA POWER release. NASA has persisted all
2,980 reviewed North America sampling-point source cells in validated release
set `10f6933b-c048-4dbc-9c33-68e00d2e6d87`; every successful payload remains
in the plan-bound local raw cache before its PostgreSQL transaction, so a
restart never re-requests completed provider data.

The four-year USDM release is likewise validated: 209 weekly source members
cover 2022-07-19 through 2026-07-14, with 209 `complete` audits and 1,045 of
1,045 expected native polygons received. No coverage audit is partial.

The local cold-history design is now daily Hive-partitioned Parquet, with
checksum-bound manifests, alongside PostgreSQL serving facts and independent
custom-format restore points. Railway remains deliberately empty of historical
facts: the target policy is a rolling 365-day operational projection plus
forecasts, not the full local four-year source archive.

## What worked

- Treating raw provider responses as immutable local artifacts before database
  writes made the long NASA run recoverable and rate-limit friendly.
- A checksum-bound checkpoint separates provider acquisition from database
  persistence and makes failed transactions safe to retry locally.
- Separating coarse regional sources from acre-scale claims preserved spatial
  honesty. NASA and ERA5 are point/context sources; field-scale data remains an
  explicit, source-native AOI workflow.
- The existing source-release and coverage-audit model captures the lineage
  needed for a correct hot-data projection later.

## Recovery and compaction evidence

- The local Podman container was intentionally stopped, not deleted. Its named
  `plantgeo_warehouse_pgdata` volume remains present, so normal restart
  recovers the validated NASA facts and raw cache.
- `C:\Users\atooz\PlantGeoWarehouseBackups\plantgeo-20260721T070210Z.dump`
  has a matching SHA-256 manifest and `pg_restore --list` succeeded with 315
  table-of-contents lines. It is structurally recoverable, but it predates the
  completed NASA replay and is therefore not its restore point.
- NASA now has a receipt-bound Zstandard Hive lake at
  `services/agri-data-service/.agri-local-runs/warehouse/nasa-power-daily/fa4b726f6bb728906580f429d82be3480dd5c1b1633172f0a133c17f4868352b`:
  34,854,080 rows, 1,462 UTC partitions, and 1,462 Parquet files. The manifest
  checksum is `7586ea8eae24f9e1e6adfc26f42bb89e2abbe3066be119e63536dbfa1fb8e6fc`.
- `C:\Users\atooz\PlantGeoWarehouseBackups\plantgeo-20260721T201702Z.dump`
  is the current NASA restore point: 1,578,003,562 bytes, SHA-256
  `7cc183b0ba7391b2bd49404c400bc4d494885a8c3951ae2e3b35620ca0222969`, and
  `pg_restore --list` returned 315 catalog entries.
- The contract view is bitemporal: a pinned read must use an as-of time at or
  later than the actual `validated_at` timestamp, not merely the source receipt
  cutoff. A current pinned read returned the 2026-04-30 NASA observations with
  `point_sample`, 55,660-m native resolution, and `is_acre_scale_compatible = false`.
- Do not run `VACUUM FULL`, remove the named Podman volume, use `down -v`, or
  delete the raw receipt cache as a space-saving step. Cold-history compaction
  is the immutable Parquet representation; PostgreSQL remains the local
  serving/lineage store.

## Gaps and decisions

- Do not push or deploy a full historical promotion. Railway still needs a
  separate validated rolling-year projection contract, migrations/extension
  verification, a receiver, and a pinned-contract read.
- The rolling-year manifest deliberately requires actual forecast receipts, but
  the service currently has no forecast producer or validated forecast artifact.
  Do not weaken that contract or create an empty/fake forecast receipt merely
  to publish a projection; select and validate a forecast source first.
- ERA5-Land is prepared for cache-first cold-history acquisition: dependencies
  are locked, synthetic NetCDF ZIP parser/cache and daily Zstandard-Parquet
  tests pass, operator CLI commands write resumable local checkpoints, and the
  bounded local writer/release-set path is implemented. It still needs a
  controlled local-warehouse integration run plus CDS terms and local
  credentials before any provider run. The source remains a requested
  point sample, never a native-grid or acre-scale claim.
- A weekly automation must be gated until NASA and ERA5 have validated release
  sets and the hot projection exists. It must never turn an incomplete initial
  history into recurring provider traffic.
- The working tree contains a large set of unrelated in-progress changes.
  Stage and commit only an independently verified path set; do not use a broad
  commit or force a cleanup as part of warehouse deployment.

## Next session order

1. Validate ERA5 warehouse writer/release-set persistence against the existing
   cache/parser/Parquet contract. Accept the CDS terms and configure credentials
   only in the local operator environment, then execute the 49 monthly
   resume-safe requests.
2. Implement and validate the Railway 365-day projection receiver separately
   from the full-root spool. Deploy only after the Railway target gates pass.
3. Create the weekly local automation after the initial release and projection
   gates pass; include a whole-run lease, a bounded late-arrival replay window,
   coverage verification, backup, and failure-only notifications.

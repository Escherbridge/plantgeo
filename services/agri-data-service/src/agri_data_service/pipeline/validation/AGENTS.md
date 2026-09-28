# Pipeline validation

Validation reads the governed source and the published object plane independently. A clean report
must not be inferred from the writer's own intermediate counts.

## Exact vegetation parity

`vegetation.py` retains the inexpensive day/status and duplicate-release audit.
`vegetation_exact.py` is the production completion gate: it compares all 12 z13 fields in canonical
`(cell_id, observed_day)` order, validates completion receipts against physical parts and rows, then
re-derives and exactly compares z9/z5/z0. Canonical Arrow row digests are evidence summaries; Parquet
file bytes are deliberately not compared because writer metadata can differ without changing rows.

The promoted source boundary and settled absence boundary are distinct. Source-backed days through
the promoted cutoff must exist even when they are newer than publication lag; source-empty days are
required to carry absence evidence only through the settled boundary.

The exact gate treats every object after the settled boundary as an assertion too: a source-empty
day must remain missing there, while a source-backed day must hold data. For settled empty days it
downloads every absence marker so malformed or divergent evidence cannot pass on key presence.
The gate takes opening and closing per-day SHA-256 snapshots of the exact 12-column source projection;
this detects changes to mutable dimensions and release ordering as well as appended observation keys
without a second per-day database walk after the object-plane audit.
The public exact entry point holds the vegetation-wide session advisory barrier across the opening
census, all per-day reads and object comparisons, and the closing snapshot. Governed promotion uses the
same key transactionally, so continuous raw ingestion may proceed while governed registration waits;
the audit has a fixed source rather than an endless moving target.

## Exact current-weather parity

`weather_observations_exact.py` compares only the registry-governed current-conditions window. It
reports any earlier objects as an excluded Historical Forecast prefix so those rows cannot silently
expand the lane contract. For each governed day it hashes canonical Arrow rows from PostgreSQL and
z13, derives z9/z5/z0 from the PostgreSQL base with the pure tier function, and validates each
completion or absence marker. The opening source snapshot supplies exact z13 and derived-tier
expectations; after the object walk a new repeatable-read transaction reprojects every governed day.
Aggregate row and Arrow digests, plus per-day change findings, prevent a moving PostgreSQL source from
producing a clean gate. A source-empty day requires governed-absence evidence at z13 and intentionally
empty coarse rungs: the existing ladder driver explicitly does not mint three new governed statements
from one base marker. Physically stricter nullability remains read-compatible with the registered
schema, but relaxed required fields, type drift, or field-order drift fail. The audit re-lists every
zoom prefix and re-reads canonical rows and marker bodies, so both same-key overwrites and key-set
changes fail. The JSON contains relative scope/count/hash evidence and redacted exception classes only,
never a database URL, bucket name, endpoint, access key, secret, or provider error text.

## Soil-survey candidate validation (SSURGO port, slice S2)

`soil_survey.py::validate_soil_survey_candidate` reconciles one prepared shard candidate
(`foundation/soil_survey/release.py::Candidate`) against a **current** USDA SDA census, the same
per-area checks `validate_soil_survey_release` runs on a day partition: delineation count on
`mupolygonkey`, and vintage staleness. It reads the rung-13 parts through any
`AvailabilityStorage`, so it runs **before** staging against the local capture root
(`pipeline/direct/soil_survey/local_storage.py::LocalCandidateStorage`, plan finding F8) and can
re-run against the bucket after staging. Either way every part is checksum-verified.

- **Bounded reads.** One call checks 1..`MAX_VALIDATION_AREAS` (50) areas and reads at most
  `MAX_VALIDATION_PART_BYTES` (128 MiB) of native parts. `candidate_validation_groups` /
  `group_areas_for_validation` split a shard's areas greedily, in sorted order, under both caps; an
  area whose own parts exceed 128 MiB is refused rather than read partially.
- **Q4 labels are checked, not trusted.** Owner Q4 serves every row, labelling unrepaired invalid
  geometry `invalid_unrepaired` and repaired geometry `repaired`. Each part's `geometry_quality`
  column must match the `repaired_rows` and `labelled_rows` its manifest `Part` records, and a null
  label is refused, so a manifest can never under-report what the bytes serve.
- **Network step.** Each area costs one SDA call (`HttpxSoilSurveySdaClient`) under a 120 s
  budget for the whole group, checked between areas rather than wrapped once around the whole
  loop: cancelling a whole-group `asyncio.timeout` mid-flight would abort the area in progress
  *and* discard every finding already collected. Instead each call gets its own remaining share
  via `asyncio.wait_for`, and once the deadline is passed every area not yet reached becomes its
  own `source_query_failed` finding instead of being silently dropped. One area's transport failure
  (or its own timeout) becomes a `source_query_failed` finding for that area only, never the end of
  the run.

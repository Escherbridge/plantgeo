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
After the Parquet walk it re-runs the exact 12-column source projection for every governed day; this
detects changes to mutable dimensions and release ordering as well as appended observation keys.

## Exact current-weather parity

`weather_observations_exact.py` compares only the registry-governed current-conditions window. It
reports any earlier objects as an excluded Historical Forecast prefix so those rows cannot silently
expand the lane contract. For each governed day it hashes canonical Arrow rows from PostgreSQL and
z13, derives z9/z5/z0 from the PostgreSQL base with the pure tier function, and validates each
completion or absence marker. The JSON contains relative scope/count/hash evidence only, never a
database URL, bucket name, endpoint, access key, or secret.

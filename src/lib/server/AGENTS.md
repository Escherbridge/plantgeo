# Server contracts

PostgreSQL is limited to application, community, tracking, operational-control, intervention, and
bounded lookup data. Environmental observations and derived layer products are read from governed
Parquet through the data service. Do not add an environmental PostgreSQL query, writer, cache, or
fallback.

`geo.features` is available only for user-authored interventions and community contributions.
Every generic feature query must use an explicit application-layer allowlist.

Parquet responses follow the selected-day contract: report the requested day, resolved publication
day, coverage, provenance, governed absences, and temporal/spatial neighbours. Missing data stays
unavailable until a publication or validation job repairs it.

Keep cross-cutting rationale in this file and code comments short. Run all edits before the final
type, lint, boundary, and test sweep.

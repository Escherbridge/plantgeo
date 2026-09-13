---
type: evidence
track: botanical_species_profile_lookup_20260911
status: production-census-pending
---

# Production authoring census gate

No completed PlantGeo production census is available. Current deployed columns,
row counts, review states, field coverage and values remain unknown. Local
PostgreSQL and `pgt` belong to another project and provide no PlantGeo evidence.

Before host shutdown, a separately authorized attempt through the saved service
connection returned a sanitized `network_connection_unavailable` result. It did
not establish table contents or produce an accepted authoring snapshot. The JSON
receipt preserves that limited outcome without credentials or exception text.

The continuation after shutdown is local only: no private environment files,
remote services, database access or source ingestion. This gate remains pending.
A future authorized census must use read-only controls, record exact deployed
schemas/constraints/counts and evidence coverage, and preserve all original UUIDs
and reviewed evidence. Missing canonical crosswalks, source versions, licences,
measurement context or per-value provenance prevent automatic admission. Default
false values and name matches are not reviewed trait evidence.

The current WCVP artifacts are an unaccepted local fixture/candidate. They neither
replace existing relational records nor claim to exhaust them. The lookup reader
uses pinned Parquet only, with no live relational fallback.

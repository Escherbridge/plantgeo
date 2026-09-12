---
type: evidence
track: botanical_species_profile_lookup_20260911
status: production-census-pending
---

# Species authoring and serving inventory

The initial implementation base was `b9b7bf4fcc0c58556d10fc58522edced8962b869`.
Before integration, the isolated branch was fast-forwarded to committed main
`bc7b5e1ff5dbb6eac929d5db927acd6b7ff2ea4a`, preserving the PNW admission packet.
Production species and companion populations, counts, review states and values
are **unknown**. The local PostgreSQL/`pgt` investigation was superseded because
that database belongs to another project. A subsequently authorized Railway
attempt did not produce a completed census; its sanitized pending-gate record is
`railway-census.json`. The continuation after host shutdown is local only and
performs no private environment access, remote calls, database access, source
ingestion, publication or deployment. Checked-in schemas below are repository
evidence, not a claim about the exact deployed schema or current row contents.

## Checked-in relational surfaces

`db/agri/tables/species.sql` and `models/species.py` agree on a UUID primary key
and unique scientific-name text. They do not carry a taxonomic authority, authority
version, taxon concept ID, synonym/cultivar identity, assertion source/version,
licence, review state, method, measurement context, corrections or withdrawals.

| Existing columns | Compatibility with a reviewed profile |
| --- | --- |
| scientific_name, common_name, usda_symbol, family | Census/descriptive values only until an explicit authority/version/concept crosswalk and source evidence are attached. Name normalization cannot prove a join. |
| growth_habit, native_status | Categorical assertions need vocabulary, geographic scope, source and review evidence. |
| usda_zones | An integer range is insufficient without hardiness scale/version and interval semantics. |
| min_precip_mm, max_precip_mm | Millimetres are named, but period, requirement versus observed association, geography and method are absent. |
| min_ph, max_ph | Bounds need substrate, method and applicability context. |
| light_requirement, drought_tolerance, salt_tolerance | Source vocabulary and evidence kind must be retained; tolerance does not prove mitigation. |
| nitrogen_fixer, edible, timber_value | Non-null Boolean model defaults are false; false is not evidence of a negative assertion. Never import these defaults as reviewed facts. |
| pollinator_value, guild_roles | Agricultural role assertions need source, context and review; they are not an outcome ranking. |
| created_at, updated_at | Authoring timestamps are not a source release or measurement date. |

`companion_relationships` preserves two species UUIDs, relationship type, guild
function, notes, citation, URL, evidence grade, applicability context, jurisdiction,
review state, reviewed_at and reviewed_by. Its approval check requires a timestamp
and citation, but the table has no source release/licence or canonical concept
crosswalk. A later importer must verify all of these before exporting an approved
companion assertion; an existing approved row is preserved even if it cannot yet
pass publication admission. The pair is ordered and unique; a species cannot pair
with itself. No relationship is synthesized from general plant traits.

## Existing consumers and scientific limits

`method/ml/recommendation_models.py` accepts pre-fetched species mappings, with
precipitation, pH, hardiness, Boolean, categorical and guild-role feature terms.
Its historical comment that production contained zero species rows is not a live
local census. Its record/field-present features distinguish missing records from
available records but do not add missing provenance to a row.

The historical recommendation decision record reports a small literature-label
experiment, including only eight fitted species labels and a confounded held-out
source/species design. Existing `/api/v1/recommendations/species` serves that
separate model surface. This task neither refits it nor makes it consume new
profiles. The active recommendation-validation track owns the replacement
occurrence/environment/effect composition and validation gates.

## Implementation placement and database handoff

The pure botanical domain contract lives under
`warehouse/botanical_species_profiles/`, with storage schemas in
`warehouse/schemas/botanical_species_profile.py`. This is a documented adjustment
to p1's proposed `foundation/` path: Foundation's admission rule prohibits domain
nouns and new helpers committed with their first callers. No Foundation exception
or shared model migration is needed to represent a bounded immutable release.

The existing relational tables remain the census/authoring surfaces. A later
credentialed census must capture complete original rows and counts, preserve their
UUIDs and review evidence, and resolve them with an explicit canonical crosswalk.
Assertions from reviewed rows enter a new candidate alongside admitted source
assertions; conflicts retain both alternatives. No import may overwrite existing
rows or promote a name-only match or default Boolean. This release's source scope
is not a declaration that it exhausts existing reviewed authoring records.

Serving uses the immutable Parquet release only. An unconfigured store, absent
release, absent taxon, invalid release or absent trait never causes a database read.
The HTTP, model tool registry and MCP dispatcher must demonstrate that boundary
through their registered production surfaces.

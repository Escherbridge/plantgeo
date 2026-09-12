# Botanical species profile evidence

This nonspatial reference product belongs to Warehouse because its identity and
evidence vocabulary is domain data. It imports no Pipeline or serving code.
Its canonical identity is the entire authority/version/taxon-ID triple. Names,
synonyms and cultivars preserve explicit source identifiers and accepted-ID
links; no string-name matching creates taxon identity.

The vocabulary separates growth requirements, fuel measurements, fire response,
agricultural roles, companion evidence and objective effects. Taxonomy-only
releases publish every trait as unknown. Unknown is never false, compatible,
unsuitable or evidence of absence. Objective-effect inference belongs to the
separate recommendation-validation track and is refused by this lookup.

Every assertion retains the admitted source version/licence, raw and normalized
values, units, evidence kind, locator, method, contextual fields, validity,
reviewer, relational authoring provenance and optional authoring-row ID. The
only normalization implemented is identity plus explicit temperature conversion;
unsupported unit conversion cannot be silently introduced by an adapter.

Fuel claims require attributable measured or individually reviewed literature
evidence and explicit component, live/dead state, moisture basis, method,
season, life stage, geography and conditions. A scientifically justified
`not_applicable` context is an explicit source/reviewer assertion; null or
unknown context refuses publication of the fuel value. Fire response never
substitutes for fuel composition or fire-mitigation effect.

Reconciliation is conservative. Approved assertions may agree only when their
normalized value, context, evidence class, qualifier and companion identity
agree. Other approved alternatives produce conflict; no averaging is implied.
Reviewed corrections and withdrawals name retained original assertions and
cannot cross taxon/trait identity or form cycles. The local relational census
may be unavailable; that condition is recorded in the release and never guessed
from missing credentials or a source-only publication.

An explicit approved correction supersedes its original even when the corrected
evidence is unknown or restricted. A corrected missing value cannot leave the old
value serving as known. Unreviewed and rejected corrections have no superseding
effect, and both records remain linked in every case.

Canonical taxon IDs equal the admitted authority source-record IDs. Assertions
from that same source and version must identify the canonical taxon's own source
record. Admission of another record in the same release does not authorize
copying its traits across taxa. Separately attributed source/version enrichment
retains its explicit canonical target and source record; no name crosswalk is
inferred by this publisher.

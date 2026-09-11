# Soil-survey preserved-Parquet restoration

`prepare.py` consumes a SHA256-pinned `soil-survey-preservation/v1` manifest, its separately pinned
`preservation-receipt.json`, and every named native Parquet part from an existing local directory.
The external receipt is retained evidence for a reviewer; its opaque bytes do not independently
certify the operator's complete-preserved-population assertion. A missing receipt, a missing part,
hash/length/row-count disagreement, changed schema, null required field, wrong release day, duplicate
`mupolygonkey`, altered natural identity, invalid/nonpolygon/empty geometry or rung row loss refuses.
`mukey` is not a deduplication key. The original hydric tri-state, vintage, confirmation time and
geometry identities survive unchanged. This tool opens no PostgreSQL connection or source endpoint.

The input describes exactly one preserved snapshot and an explicit population scope; it always
retains `upstream_population_complete=false`. There is no defensible claim of complete regional
SSURGO coverage from a historical lazy read-through population. Release day comes from preservation
evidence, never from preparation time. Bounds are 256 input parts, 32 MiB compressed and decoded
per part, 512 MiB compressed input total and 100,000 native rows per candidate. Decoding occurs in
500-row batches. A larger inventory requires a reviewed partitioned restoration driver, not silently
truncating this candidate or presenting one bounded candidate as a whole-lane replacement.

All z0/z5/z9 rows derive independently from the same native batch through the registered
`SOIL_SURVEY_DERIVATION`, whose existing policy is topology-preserving simplification without an
area floor. z13 retains native detail. The three derived outputs must conserve every native
delineation and every nongeometry attribute. The caller must provide a local DuckDB session with
`spatial` already loaded; the CLI disables automatic installation and never installs an extension.

Conform each derived result to the registered Arrow schema before comparing all nongeometry
attributes with native rows. DuckDB returns timestamp-with-timezone columns using its session zone:
for example, midnight UTC can return as 18:00 on the previous Denver calendar day with the same
instant. The storage schema requires microsecond UTC timestamps. Schema conformance preserves
those instants and native sort order; the strict full-attribute comparison then detects any actual
value, null or row change. Conforming only while writing is too late for this comparison. The
regression exercises UTC and Denver sessions and compares every persisted nongeometry field and
the exact registered schema at every rung, while checking native and generalized geometry separately.

Native validation runs before output creation. A failed later rung leaves only an incomplete local
directory, with no candidate manifest. The script has no `--apply`, terminal marker, active pointer,
availability generation or scheduler registration.

Generic-owner integration requires a fresh `parquet-soil-survey` owner/lease fence, an exact complete
source-inventory proof and rollback reference. Replace the PostgreSQL keyset/watermark/export
adapter with a preserved-Parquet/native source adapter, use the existing lane-day lock and normal
all-rung finalization/barrier, then admit availability. Retain `static_lookup` vintage semantics.
Never reactivate the old environmental PostgreSQL ingestion/export path. Local candidate generation
does not grant any of those actions. The native reader and coarse rendering reader must name the
same release and one resolved rung; selected-day, spatial/temporal-neighbour and browser evidence
remain separately owed after a reviewed deployment/publication.

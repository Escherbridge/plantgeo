# Community data interventions

Use the **Data** intervention category to organize collection work or submit community data
against an environmental lane. Both actions use the existing location, authorship, consent,
review, publication, and social workflows.

| Type | Purpose | Required data details |
| --- | --- | --- |
| Data collection | Plan a collection activity at a site | Environmental lane and collection method |
| Data submission | Submit an existing observation or dataset for review | Environmental lane, collection method, observation date, and dataset or evidence URL |

The map workspace and the community recommendation form offer the same fields. A submission
references a dataset or evidence at an HTTP or HTTPS URL. PlantGeo stores the reference; it does
not download the URL or ingest its contents. The observation date must be a real calendar day
and cannot be in the future. Collection plans can be submitted before evidence is available.

Select the lane that the work informs. Soil and climate sublanes identify the particular
measurement or depth rather than bundling distinct measurements under one label. Botanical,
land-context, and SoilGrids reference targets are also available, including snapshot sources that
do not have a time slider. A lane selection does not assert provider coverage, a validated
publication, or compatibility between community measurements and the provider's measurement
protocol. Forecast targets identify the product the evidence informs; they do not turn submitted
evidence into a provider forecast.

The intervention still needs a point or drawn area, a name, and publication consent. The existing
interactive vertex limit applies, and data polygons retain the 500-acre site limit. Use the site
geometry to identify where the work takes place; the linked dataset can describe its own coverage.

## Provenance and review

The server records these interventions with `dataOrigin = community`. Contributors cannot select
verified-source status. The lane and details are stored under `properties.dataDetails` on the
community intervention, alongside the submitter identity and consent.

Expert publication changes the intervention's review status and public visibility. It does not
change where its data originated. Collection plans and submitted evidence remain visibly
identified as community work in lists, review, and map details after publication. Published data
interventions have their own map category colour; pending interventions retain the common orange
review colour.

Verified-source environmental data continues through the existing admitted provider and governed
Parquet paths. A reviewed community submission does not replace provider observations, fill
provider coverage gaps, or become a verified-source publication. Legacy records without a supported
community origin are displayed as unknown. A `verified_source` string in an old community record
is not provider verification and cannot earn a verified-source label.

## Storage and API boundary

`interventions.submitIntervention` is the interactive entry point for both data types. It validates
the type/category pairing, lane, method, date, and evidence URL before inserting a pending-review
community feature. Public strategy requests remain their existing land-only social workflow.
Alternative generic and legacy intervention writers cannot bypass the new data validation or
write community observations into environmental layer partitions.

No database migration is required: the existing intervention JSON properties carry the additional
fields, and vector tiles already project the category. This feature adds a community review
workflow, not an environmental ingestion adapter or a file-upload service.

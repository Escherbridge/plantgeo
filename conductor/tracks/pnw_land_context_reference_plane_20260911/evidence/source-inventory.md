---
type: evidence
track: pnw_land_context_reference_plane_20260911
status: research-baseline
accessed_on_utc: 2026-09-12
---

# PNW source inventory and contact-routing evidence

## Evidence boundary

This is a concise preservation of the completed exploration, shared by the
[reference-plane specification](../spec.md) and
[experience specification](../../pnw_land_contact_experience_20260911/spec.md).
The original research accessed the sources below on **2026-09-12 UTC
(September 11 local)**. That date applies to every URL in this memo; it is an
access/discovery date, not a dataset publication or contact-tenure date.
The preservation pass did not re-fetch sources, acquire GIS records, validate
live regional completeness, contact offices or admit releases.

“Inspected” means published page/schema/metadata was examined. “Unresolved”
means a current machine endpoint, terms, identity or live coverage could not be
established. Search-discovered or failed-fetch candidates are qualified below.
No inaccessible page or current source field is silently promoted to verified.

Input provenance was the completed local exploration and parcel, utility,
land-management and inquiry memos under `.omc/research/`. The substantive
findings and direct source links are retained here so future planning does not
depend on those local notes. Raw external pages are not reproduced.

The user accepts and prefers omitted private parcel-owner names. Exclude private
names and personal owner contact fields by default; omission is not a source
coverage defect. Public office/SME contacts and a documented route for asking
about the contact process remain in scope.

## Parcels and nonpersonal use

| Source | Retained finding | Open gate / use |
| --- | --- | --- |
| [WA public county parcels](https://wisaard.dahp.wa.gov/server/rest/services/County_Parcels/MapServer/0) | Inspected schema exposes county/FIPS, parcel IDs, land-use code, DATA_LINK and FILE_DATE; owner-name/mailing fields absent despite broader description prose. | Owner omission fits scope. County completeness, stable keys, current vintage, nonpersonal field quality and reuse terms remain unverified. |
| [Oregon Metro taxlot metadata](https://www.arcgis.com/sharing/rest/content/items/b3cabe5845ec47eab61c54e0c631313c/info/metadata/metadata.xml?format=default&output=html) | Portland-region source, not statewide Oregon; public distribution excludes ownership. Metadata documents quarterly updates, TLID and limits on parcel-specific interpretation of generalized use codes. | [RLIS license](https://rlisdiscovery.oregonmetro.gov/pages/open-database-license) content did not load; exact reuse obligations remain open. Verify distribution/vintage and current field population. |
| [Public Idaho Parcels metadata](https://www.arcgis.com/sharing/rest/content/items/65a3f7c6d4ca404ba6ab677913953b35/info/metadata/metadata.xml?format=default&output=html), [Idaho GIO entry](https://gis.idaho.gov/parcel-technical-working-group-standard) | County-contributed data with PARCEL_ID, COUNTY/FIPS and documented owner fields. Metadata describes 44 counties as a goal while credits list 13; updates follow submissions. Terms restrict county persons-list mailing/telephone uses and apply to derived products. | Live [feature service](https://services1.arcgis.com/CNPdEkvnGl65jCX8/arcgis/rest/services/Public_Idaho_Parcels_/FeatureServer) could not be opened. Actual county coverage/values and permitted nonpersonal projection require verification; do not infer statewide completeness. |
| [IDWR parcel service](https://gis.idwr.idaho.gov/hosting/rest/services/Reference/Parcels/FeatureServer) | Metadata restricts sharing outside IDWR. | Exclude this distribution as an ingestion candidate. It is distinct from Idaho GIO public parcels. |
| [USDA Cropland Data Layer FAQ](https://www.nass.usda.gov/Research_and_Science/Cropland/sarsfaqs2.php), [releases](https://www.nass.usda.gov/Research_and_Science/Cropland/Release/) | Annual crop raster; recent vintages changed resolution/method. Derived parcel/year crop fractions are plausible nonpersonal enrichment. | Pin year, resolution and method; preserve raw raster versus derived summary identity. Crop cover does not identify owner/operator, zoning or legal farm status. Do not presume underlying FSA farmer/CLU records public. |
| [Census urban definition](https://www.census.gov/programs-surveys/geography/guidance/geo-areas/urban-rural.html), [Urban Areas service](https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/Urban/MapServer/0) | Dated settlement classification with geographic identifiers; can intersect a farm parcel. | Urban membership is not city jurisdiction, local zoning or permission to develop. Admit the exact census vintage separately. |

## Electric territories

| Source | Retained finding | Open gate / use |
| --- | --- | --- |
| [WA Ecology service areas](https://gis.ecology.wa.gov/serverext/rest/services/CPR/CPR/FeatureServer/0) | Inspected polygon service supports queries/exports; utility Name and OBJECTID, no EIA ID/contact/history in inspected schema. Publisher qualifies boundaries as approximate. | Verify stable organization crosswalk, coverage, vintage and reuse terms. Direct actual service/capacity questions to utility. |
| [ODOE Find Your Utility](https://www.oregon.gov/energy/energy-oregon/pages/find-your-utility.aspx) | Official electric/gas finder; linked data item 2102826e218c4d36a24583613df76f0c and metadata item 8bd794c3c26f46a19e086d3e2e2af049. | Current item payload unresolved. Exact current electric machine feed must be resolved; gas is discovery context outside the initial electric family. |
| [Older ODOE electric service](https://services.arcgis.com/uUvqNMGPm7axC2dD/arcgis/rest/services/OregonElectric_Utilities_WGS_1984_6_26_2023/FeatureServer) | Inspected ODOE service; metadata describes 2–3 updates/year and overlapping territories, while layer title is June 2024. | Do not assume it is the current finder feed or use a title as a proven current watermark. Reconcile exact source/vintage and rights. |
| [Idaho PUC maps](https://puc.idaho.gov/Page/Standard/20), [electric PDF](https://puc.idaho.gov/Fileroom/PublicFiles/maps/elec.pdf) | Official maps discovered/inspected; PDF availability established. | Current, complete machine-readable polygons and refresh cadence remain unresolved. No fabricated polygons from PDF availability. |
| [EIA-861](https://www.eia.gov/electricity/data/eia861/) | Utility identity/attributes and county service-territory table can aid reviewed crosswalks. | County membership is not precise service geometry or historical polygon validity. |
| [HIFLD catalog](https://catalog.data.gov/dataset/electric-retail-service-territories) | Catalog advertised public/government-work terms and an October 2022 dataset update, distinct from an August 2026 catalog check. | Current upstream download, provenance, rights and refresh unresolved. No arbitrary mirror as an admitted fallback. |

Gas, drinking water, sewer and irrigation should not be inferred from electric
coverage. The original exploration noted [EPA water service areas](https://www.epa.gov/ground-water-and-drinking-water/public-water-system-service-areas)
as a later candidate with sourced/modelled distinctions; it is outside these
four initial families and grants no expanded scope.

## BLM and state-managed interests

| Source | Retained finding | Open gate / use |
| --- | --- | --- |
| [BLM OR/WA surface jurisdiction](https://gis.blm.gov/orarcgis/rest/services/Land_Status/BLM_OR_Ownership/MapServer) | Regional surface-jurisdiction source based on cadastral/title-map inputs, no scale dependencies; nonfederal ownership explicitly incomplete. | Validate exact schema, version, detailed suitability, cadence and distribution terms. Do not label it a full private-ownership inventory. |
| [National BLM SMA](https://gis.blm.gov/arcgis/rest/services/lands/BLM_Natl_SMA_LimitedScale/MapServer) | Broader federal surface-manager context includes Idaho; does not provide ownership-pattern attributes. | Scale-limited detailed suitability remains open. BLM toggle selects only verified BLM-manager records; admin polygons cannot substitute. |
| [BLM Field Boundary layer 3](https://gis.blm.gov/arcgis/rest/services/admin_boundaries/BLM_Natl_AdminUnit/MapServer/3), [schema](https://gis.blm.gov/arcgis/rest/services/admin_boundaries/BLM_Natl_AdminUnit/MapServer/layers) | ADM_UNIT_CD, parent hierarchy, name/state and effective/approval fields; office points share organization code. | Validate actual source keys and temporal meaning. Jurisdiction can cover interests beyond BLM-managed surface. |
| [BLM administrative metadata](https://www.arcgis.com/sharing/rest/content/items/4ec898f8fb104ce4910932d02791563a/info/metadata/metadata.xml?format=default&output=html) | Monthly maintenance and public-domain access documented with attribution/modification and accuracy qualifications. | Applies to this administrative dataset only, not every BLM dataset. Exact-release admission remains required. |
| [WA DNR managed parcels](https://gis.dnr.wa.gov/site3/rest/services/Public_Boundaries/WADNR_PUBLIC_Cadastre_OpenData/MapServer/6) | Parcel/region identifiers, separate owner/steward/manager IDs and surface/mineral/timber flags. Description calls ownership parcels complete within this upland DNR interest; other interest types incomplete. | Not all state agencies or aquatic interests. Verify crosswalks, current population, cadence and redistribution terms. |
| [Oregon SLIS](https://maps.dsl.state.or.us/arcgis/rest/services/SlisPublic/FeatureServer/0), [metadata](https://maps.dsl.state.or.us/arcgis/rest/services/SlisPublic/FeatureServer/0/metadata?f=html&format=default) | Multiagency SURF_OWNER/SUB_OWNER and county/taxlot fields. Continual maintenance documented with agency-reporting gaps and omitted public rights-of-way/untaxlotted waterbodies. | Durable feature ID, actual manager relationship and full reuse obligations remain open. State ownership does not imply DSL manages each tract. |
| [IDL GIS program](https://www.idl.idaho.gov/idl-gis-program-idaho-maps-and-land-records/), [supervisory areas service](https://gis1.idl.idaho.gov/arcgis/rest/services/Portal/IDL_Supervisory_Area_Boundary/MapServer) | Official program and jurisdiction service discovered; parent service describes supervisory areas/offices. | Exact layer schema/office keys not verified because layer fetch failed. |
| [Idaho State Ownership candidate](https://services2.arcgis.com/1cvrwLhZRFh3okEF/arcgis/rest/services/State_Ownership/FeatureServer) | Search-discovered surface/subsurface state ownership candidate. | Production authority, stable keys, current release/coverage and rights unresolved. An indexed gis1-t test-host candidate is not a production source. |

These sources do not provide a complete public-land/authority inventory.
USFS, NPS, tribal, county and other authorities remain distinct; absence cannot
be interpreted as private or unregulated.

## Public office and SME routes

All entries below were accessed in the original exploration on the date above.
Public directory publication is evidence of a route, not a contacted office's
response or verified current staff tenure.

| Route | Documented use / relationship gate |
| --- | --- |
| [WSU county/tribal Extension](https://extension.wsu.edu/in-your-county-or-tribe/), [OSU Extension](https://extension.oregonstate.edu/find-us), [University of Idaho Extension](https://www.uidaho.edu/extension/county) | Regional adviser discovery; associate topic and published service geography explicitly. A county office or nearby person is not automatically a specialist for every topic. |
| [USDA Service Centers](https://www.farmers.gov/working-with-us/service-center-locator), [WA conservation districts](https://www.conserve.wa.gov/map) | Public agriculture/conservation assistance routes. District boundaries can differ from counties; do not imply access to private farmer/operator contacts. |
| [ODOE utility directory](https://www.oregon.gov/energy/energy-oregon/pages/oregon-utilities.aspx) | Public organization/contact seed across provider types, including cooperatives, municipalities and PUDs; regulator-only lists are incomplete. |
| [PSE construction services](https://www.pse.com/en/construction-services/cs-project-steps), [PGE interconnection](https://portlandgeneral.com/renewable-installers/interconnection-qualifications), [Idaho Power economic development](https://www.idahopower.com/about-us/economic-development/) | Task-specific service/development routes; Idaho Power publishes regional staff/team information. Match the utility and project type, then verify the appropriate channel. No service/capacity promise. |
| [BLM OR/WA Public Room](https://www.blm.gov/media/public-room/oregon-washington), [ROW contacts](https://www.blm.gov/programs/lands-and-realty/Rights-of-way/contact-list) | Field-jurisdiction-first routing and documented ROW specialists. Public Room instruction was captured in search; later page open timed out. Do not generalize ROW expertise to all programs. |
| [WA DNR contacts](https://www.dnr.wa.gov/about/contact-dnr) | Regional/program directory; parcel-region crosswalk still requires review. |
| [Oregon DSL land staff](https://www.oregon.gov/dsl/lands/Pages/land-staff.aspx), [assigned-regions map](https://geo.maps.arcgis.com/home/item.html?id=878e9d5f51ff4eedb68484fa6f7cfefd) | Explicit spatial staff-assignment candidate; layer schema not extracted. Non-DSL tracts require their own agency. |
| [Idaho supervisory offices](https://www.idl.idaho.gov/about-us/supervisory-areas/), [IDL contacts](https://www.idl.idaho.gov/contact-us/) | Regional office and program routing; area-by-topic assignments still need evidence. |

## Who to ask about a parcel contact process

| Pilot example | Public office evidence | Permitted product claim |
| --- | --- | --- |
| [King County Assessor](https://kingcounty.gov/en/dept/assessor/about-king-county/about-king-county-assessor/contact), [property lookup instructions](https://kingcounty.gov/en/dept/assessor/buildings-and-property/property-value-and-information/look-up-property-information) | General assessor/real-property help; parcel/account lookup and public records. Published office phone 206-296-7300, Assessor.Info@KingCounty.gov. | Property-record assistance and an inquiry about the correct contact process; no owner-introduction service verified. |
| [Multnomah Assessment, Recording & Taxation](https://multco.us/info/contact-division-assessment-recording-taxation), [lookup instructions](https://multco.us/info/property-search-tools-and-maps) | Explicit Property Tax and Ownership route: 503-988-2225, propertytax@multco.us. Recorded documents: 503-988-2273, clerk@multco.us. Email preferred. | Ownership-record assistance, separate from records research and any direct owner/representative contact. |
| [Kootenai Land Records](https://www.kcgov.us/182/Land-Records-Division), [Recorder](https://www.kcgov.us/343/Recorder) | Public mapping/ownership/property-information help, 208-446-1500; recorder supports document research. Land Records email display/href differed, so retain official Email Us link pending verification. | Records/inquiry route. No verified forwarding, introduction or private phone/email disclosure. |

Ada assessor retrieval failed; Kootenai is the verified Idaho example.
None of these county pages establishes an introduction, forwarding service or
identity of an authorized representative. A user-reviewed draft may ask which
process exists. Store “unknown” for unsupported capabilities, not “yes.”

## Required next gates

1. Verify Idaho utility polygons and the production Idaho state-land distribution.
2. Resolve Oregon's current utility machine feed rather than assume the older
   service is current.
3. Audit county parcel geometry/nonpersonal-field and rights coverage; owner
   names deliberately excluded are not an admission requirement.
4. Complete exact-distribution rights, source-watermark, native-ID and coverage
   admission for every family and public directory, including retained,
   derived, display/API/agent and any export use.
5. Review place-office-topic assignments and documented help; distinguish
   office referral from a matched SME and authority from advice.

These findings support planned investigation only. They authorize no download,
ingestion, contact, publication or deployment.

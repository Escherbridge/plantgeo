# PNW land-context source-rights outreach

Tracking document for the human-gated rights questions behind the [PNW land context and public contact experience](../conductor/tracks/pnw_land_contact_experience_20260911/spec.md) feature. See [`rights-gate-verdicts-20260912.md`](../conductor/tracks/pnw_land_context_reference_plane_20260911/evidence/rights-gate-verdicts-20260912.md) for the full research trail this summarizes.

**Status key:** 🔴 not sent · 🟡 sent, awaiting reply · 🟢 reply received · ✅ resolved (admitted or rejected)

No email in this document has been sent. Sending is a human action — update the status column here once one goes out, and record the reply (verbatim terms, or a link/paste of the actual response) rather than summarizing it favorably.

## Outreach tracker

| # | State | Family | Contact | Status | Notes |
|---|---|---|---|---|---|
| 1 | ID | Electric utility | secretary@puc.idaho.gov | 🔴 | IPUC only publishes a static PDF map |
| 2 | ID | Electric utility + state trust land | ITS-GIS@its.idaho.gov | 🔴 | Idaho Geospatial Office — statewide coordinating body, best shot at a real answer for both gates at once |
| 3 | OR | Electric utility | gis.odoe@energy.oregon.gov | 🔴 | Strongest lead of the six — dataset is already public/as-is per its own metadata; this email just confirms scope + gets the raw endpoint |
| 4 | WA | Electric utility | brian.goldgeier@ecy.wa.gov | 🔴 | Dept. of Ecology; metadata already says "Category 1 — Public Information... can be shared publicly" |
| 5 | WA | State-managed land | mld@dnr.wa.gov | 🔴 | DNR Product Sales & Leasing Division (manages the actual trust lands) |
| 6 | WA | Parcels | giscenter@kingcounty.gov | 🔴 | King County GIS Center; terms already public, only "selling the data" is barred |

## BLM lands — no outreach needed

BLM Surface Management Agency and field-office-jurisdiction data resolved to **likely admittable now** without contacting anyone. Federal-agency works are public domain under 17 U.S.C. § 105, and BLM's own [IM 2014-029 "Disclaimer Statement Policy for Datasets Held by BLM"](https://www.blm.gov/policy/im-2014-029) confirms this explicitly: accuracy/fitness-for-use caveats only, no reuse or redistribution restriction.

| Layer | Endpoint | Confirmed field mapping (2026-09-13) |
|---|---|---|
| Surface management | `https://gis.blm.gov/arcgis/rest/services/lands/BLM_Natl_SMA_LimitedScale/MapServer` (sublayer 1); ID-specific high-res variant `https://gis.blm.gov/idarcgis/rest/services/realty/BLM_ID_Surface_Management_Agency/FeatureServer` | Filter: `ADMIN_AGENCY_CODE = 'BLM'`. Also carries `ADMIN_DEPT_CODE = 'DOI'`, `ADMIN_UNIT_NAME = 'Bureau of Land Management'`. |
| Field-office jurisdiction | `https://gis.blm.gov/arcgis/rest/services/admin_boundaries/BLM_Natl_AdminUnit/MapServer` (sublayer 3 = Field Boundary) | `ADMU_NAME` (office name), `ADM_UNIT_CD` (office code), `BLM_ORG_TYPE` (State/District/Field/Other), `PARENT_CD`/`PARENT_NAME` (hierarchy), `EFF_DT`/`APPRV_DT`. |

Both mechanical verification steps are closed. What remains is a real ingestion decision (wiring these into a Parquet lane per `docs/layer-lane-standard.md`), not more research — no live fetch was wired directly into the reader-contract stub, since that would bypass this repo's ingest-then-serve pattern.

Sources: [BLM National SMA Limited Scale Service](https://gis.blm.gov/arcgis/rest/services/lands/BLM_Natl_SMA_LimitedScale/MapServer) · [Layer 1 schema](https://gis.blm.gov/arcgis/rest/services/lands/BLM_Natl_SMA_LimitedScale/MapServer/1?f=json) · [BLM National Administrative Unit boundaries](https://gis.blm.gov/arcgis/rest/services/admin_boundaries/BLM_Natl_AdminUnit/MapServer) · [ScienceBase: BLM National SMA Polygons (NGDA)](https://www.sciencebase.gov/catalog/item/59b83c14e4b08b1644df5f6e) · [IM 2014-029](https://www.blm.gov/policy/im-2014-029)

## County parcels — no outreach drafted yet

Washington reads as the strongest state (King, Spokane, Snohomish, Whatcom — 4/4 admittable-leaning, Whatcom carries an explicit redistribution grant). Oregon reads as consistently restrictive (Washington County + Lane County both carry an indemnify-on-redistribution clause, likely a statewide template). Idaho is mixed (Canyon County blocked, Ada County unresolved — its terms PDF has a "RESTRICTIONS ON USE" section whose body text couldn't be extracted automatically, Bonneville County has no discoverable terms). See the full county table in the evidence file.

No draft is written yet for any county — King County's terms are already public enough that a confirmation email (draft #6 above) covers the most promising one. If Spokane, Snohomish, or Whatcom become the actual acquisition target, they'll each need their own confirmation email before ingestion.

---

## Email drafts

### 1 — Idaho PUC (electric utility territories)

**To:** secretary@puc.idaho.gov
**Subject:** Machine-readable electric utility service-territory data — does one exist?

> Hello,
>
> I'm building PlantGeo, an open-source 3D geospatial mapping platform, and I'm trying to show electric utility service territories for Idaho alongside similar layers already sourced for Washington and Oregon. The only IPUC resource I could find is the static PDF map on the Utility-Maps page.
>
> I wanted to ask directly:
>
> 1. Does IPUC (or a utility filing with IPUC) maintain a machine-readable version of service-territory boundaries — a shapefile, GeoJSON, or feature-service endpoint — rather than the PDF?
> 2. If not at IPUC, is there another state office you'd point me to?
> 3. If such data exists, what are the terms for a non-commercial, open-source project to ingest and re-serve it through a public map, API, and AI assistant tool?
>
> Thank you for your time.

### 2 — Idaho Geospatial Office (utility + state trust land, combined)

**To:** ITS-GIS@its.idaho.gov
**Subject:** Authoritative source + reuse terms for Idaho utility territories and state trust land

> Hello,
>
> I'm building PlantGeo, an open-source 3D geospatial mapping platform, and I'm trying to source two Idaho datasets to show alongside parcel and public-land context on the map:
>
> 1. **Electric utility service territories** — IPUC's own site only publishes a static PDF; I came across an "ISTC Utilities Web Map" on ArcGIS that may be a live feature service, but I couldn't confirm its publisher or terms.
> 2. **State trust land boundaries/ownership** (Idaho Department of Lands) — IDL's GIS program page links to a geoportal but doesn't state a license or terms of use for the data.
>
> Since your office coordinates GIS activity statewide, I wanted to ask:
>
> - For each dataset, is there a current, authoritative feature-service endpoint you'd point us to?
> - What are the reuse/redistribution terms — specifically, can this data be ingested, stored, and re-served through a public web map, a public API, and an AI assistant tool (all read-only, non-commercial, open-source)?
> - Is there a better office to redirect either question to?
>
> Thanks for your help — happy to share more about the project if useful.

### 3 — Oregon ODOE (electric utility territories)

**To:** gis.odoe@energy.oregon.gov
**Subject:** Reuse confirmation — Oregon Natural Gas and Electric Utility Service Areas layer

> Hello,
>
> I'm building PlantGeo, an open-source 3D geospatial mapping platform, and came across your "Oregon Natural Gas and Electric Utility Service Areas" feature service (the one behind the "Find Your Utility" tool). The item's license info reads as an as-is/no-warranty disclaimer with public access, which looks promising, but I wanted to confirm directly before we ingest it:
>
> 1. Can this dataset be stored and re-served through a public web map, a public API, and an AI assistant tool (read-only, non-commercial, open-source)?
> 2. Could you share the current FeatureServer/REST endpoint for the electric layer specifically? We found the item listing but not the raw service URL.
> 3. Is there any attribution or "confirm with your utility directly" disclaimer language you'd like carried through to end users?
>
> Thanks for maintaining such a clean, current dataset — this is the best-documented source we've found across WA/OR/ID.

### 4 — WA Dept. of Ecology (electric utility territories)

**To:** brian.goldgeier@ecy.wa.gov
**Subject:** Reuse confirmation — Electric Utility Service Areas layer for an open-source mapping platform

> Hi Brian,
>
> I'm reaching out about the "Electric Utility Service Areas" layer published on Ecology's GIS portal (metadata lists it as Category 1 – Public Information, shareable publicly). I'm building PlantGeo, an open-source 3D geospatial mapping platform, and want to ingest this layer to show utility service territories alongside parcel and public-land context for Washington.
>
> Before we do, I wanted to confirm directly rather than assume from the metadata alone:
>
> 1. Can this dataset be stored, redistributed, and re-served through a public web map, a public API, and an AI assistant tool (all read-only, non-commercial, open-source)?
> 2. Is there a specific feature-service/REST endpoint you'd recommend we pull from for the most current version, and roughly how often it's refreshed?
> 3. Is there any attribution language you'd like us to display alongside the data?
>
> Happy to share more about the project if useful. Thanks for your time.

### 5 — WA DNR (state-managed/trust land)

**To:** mld@dnr.wa.gov
**Subject:** Licensing/reuse question — WA DNR Managed Land Parcels dataset

> Hello,
>
> I'm reaching out about the "WA DNR Managed Land Parcels" dataset on DNR's GIS Open Data portal (ownership parcels held for Common School, Indemnity, and other trusts). I'm building PlantGeo, an open-source 3D geospatial mapping platform, and would like to display DNR-managed land boundaries alongside parcel and utility-territory context for the public.
>
> I couldn't find an explicit license or terms-of-use statement for this specific dataset on the open-data portal, so I wanted to ask directly:
>
> 1. Can this dataset be ingested, stored, and re-served through a public web map, a public API, and an AI assistant tool (read-only, non-commercial, open-source)?
> 2. Are there any attribution or disclaimer requirements we should carry through to end users?
> 3. Is there a specific feature-service endpoint and refresh cadence you'd point us to for the authoritative current version?
>
> If this isn't the right team for a data-licensing question, I'd appreciate a pointer to whoever handles it. Thank you.

### 6 — King County GIS Center (parcels)

**To:** giscenter@kingcounty.gov
**Subject:** Confirming reuse scope — public parcel data for a non-commercial open-source map

> Hello,
>
> I'm building PlantGeo, an open-source 3D geospatial mapping platform, and would like to use King County's published parcel data (boundaries, county-namespaced parcel IDs, published nonpersonal ownership category — no private owner names or personal contact fields) to show parcel and land-use context on the map.
>
> Your terms page states copying/distributing/using the data is permitted, with the sole restriction being "selling the data" without a written agreement. I wanted to confirm directly that a free, open-source, non-commercial public map/API/AI-assistant feature isn't considered "selling" under that clause before we proceed, and ask whether there's any attribution language you'd like displayed.
>
> Thanks for your time.

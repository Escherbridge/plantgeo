---
type: track-spec
track: pnw_land_sources_deferred_20260920
status: planned
---

# PNW remaining land sources

Track missing authoritative sources without exposing empty switches or inventing administrative facts.

Owner authorization: 2026-09-20 request to implement in an isolated worktree, maximize sourced data and reasonable estimates, remove unavailable frontend switches, create a PR and ingest production data as needed. This supersedes the earlier implementation-only limitation for BLM and crop sources. Existing unresolved parcel/utility source restrictions remain explicit follow-up work.

Data contracts: governed Parquet publication; observed source classifications distinguished from forecasts; source year, release day and capture time remain separate; no fabricated parcels, service territories, ownership or responsible contacts. Outside-source geography remains unavailable. UI switches depend on actual readable publication, not registration alone.

Verification: source evidence, meaningful domain tests, full type/lint/boundary checks, affected Python tests and an independent review after the complete implementation batch. Production writes stay within the new product prefixes and use the ordinary publication locks and completion protocol. PR creation does not authorize merging main.

## Next source admissions

These are separate products to admit, not evidence that BLM surface-management polygons are missing.
Official public services were identified on 2026-09-21; each still needs a complete PNW capture,
native-key validation, an explicit interest type, publication/recovery contract and reader tests.

| Priority | Product | Official candidate | Admission distinction |
| --- | --- | --- | --- |
| 1 | Grazing allotments and pastures | [OR/WA](https://gis.blm.gov/orarcgis/rest/services/Administrative/BLM_OR_Grazing_Allotments_and_Pastures/MapServer/0), [Idaho](https://gis.blm.gov/idarcgis/rest/services/range/BLM_ID_Grazing_Allotments/MapServer), [national](https://gis.blm.gov/arcgis/rest/services/range/BLM_Natl_Grazing_Allotment/MapServer) | Allotments can include private, state and other federal land; they must not imply BLM surface ownership. |
| 2 | Wilderness and wilderness study areas | [National WLD/WSA](https://gis.blm.gov/arcgis/rest/services/lands/BLM_Natl_NLCS_WLD_WSA/MapServer) | Designation and management are different facts; preserve designation type and source-effective dates. |
| 3 | Recreation sites and facilities | [National recreation](https://gis.blm.gov/arcgis/rest/services/recreation/BLM_Natl_Recreation/MapServer), [sites/facilities](https://gis.blm.gov/arcgis/rest/services/recreation/BLM_Natl_Recreation_Sites_Facilities/MapServer) | Point amenities and recreation areas need their own renderer and availability, not synthetic land boundaries. |
| 4 | Broader land cover | Annual NLCD admission | Forest, developed land and change history need a separate classification product; crop estimates remain explicitly USDA CDL. |
| 5 | County parcels, electric territories, state-managed lands | Prior source-rights investigations plus PAD-US/state candidates | Continue the existing source and redistribution checks; keep these switches unavailable until an authoritative population is published. |

Also deferred: mineral/lease interests, a replacement high-resolution Idaho SMA endpoint, native
10 m regional crop processing, pre-2022 crop history, new annual editions and additional verified
office contact routes. No responsible individual, phone, email or forwarding relationship is inferred.

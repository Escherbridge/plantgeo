---
type: evidence
track: pnw_land_context_reference_plane_20260911
review_scope: source-rights-gates
observed_at: 2026-09-12
verdict: deferred — external rights unresolved
---

# Source rights gate verdicts

This receipt documents the status of four externally-dependent rights gates that remain unresolved as of 2026-09-12. These gates block data acquisition and ingestion from their respective sources, but do **not** block schema design, reader contracts, UI implementation, or agent integration — those proceeded in parallel under owner authorization.

## Gate verdicts

### Idaho utility polygon source
**Verdict:** Deferred — genuinely unclear, needs a human contact. Still no acquisition authorized.

No confirmed current machine-readable Idaho electric-territory feed with stated terms exists in the public record. Idaho PUC publishes only a static PDF (`elec.pdf`). A candidate lead — the "ISTC Utilities Web Map" (ArcGIS item `e680875e27a041d19c5edbff8ad8a4ce`) — surfaced in a 2026-09-12 research pass but could not be verified (publisher identity, license, cadence, or production-vs-demo status all unconfirmed; the item page is JS-rendered and did not yield metadata via automated fetch). **Next step:** a human sends two emails — one to IPUC, one to the ISTC Utilities Web Map's listed owner — asking for a feature-service endpoint and explicit reuse/redistribution terms. Not resolvable by further automated research alone.

### Idaho production state-land source
**Verdict:** Deferred — genuinely unclear on terms, likely admittable on access. Still no acquisition authorized.

IDL's own GIS program page links to an "IDL Geoportal" but states no explicit license or terms of use for the data — confirmed absent, not merely unfetched; silence is not permission. INSIDE Idaho (University of Idaho's statewide GIS clearinghouse) is a stronger candidate for a documented open-data policy but its terms language for state-land layers specifically was not confirmed in the 2026-09-12 pass. **Next step:** either a direct IDL contact, or confirming INSIDE Idaho's clearinghouse-wide terms actually cover this layer before treating it as admitted.

### Oregon current utility machine feed
**Verdict:** Likely admittable — new finding, resolves the prior "stale service" concern. Acquisition still not authorized pending endpoint confirmation.

A 2026-09-12 research pass identified **"Oregon Natural Gas and Electric Utility Service Areas"** (ArcGIS item owned by `jim.gores_odoe`, Oregon Dept. of Energy), created Dec 13 2024 and modified Aug 19 2025 — materially newer than the previously-flagged `OregonElectric_Utilities_WGS_1984_6_26_2023` service, and the layer actually wired into ODOE's live "Find Your Utility" public experience. Its `licenseInfo` is a standard as-is/no-warranty disclaimer with **no anti-redistribution or non-commercial clause** — a materially more permissive posture than a restrictive license. The item metadata was confirmed via direct ArcGIS item JSON (not JS-rendered), so this is a higher-confidence finding than the other three gates. **Next step:** resolve the raw FeatureServer REST endpoint (not yet obtained — an implementation task, not a rights question) and carry the as-is/informational-only disclaimer through to end users (direct them to confirm with the utility directly). This is the one gate closest to a real admission verdict.

### County parcel field and rights coverage
**Verdict:** Deferred — no state-level aggregator in WA, OR, or ID currently grants clean statewide reuse rights (confirmed, not just inherited from prior inventory). A partial-coverage first slice is possible only county-by-county.

State-level aggregators checked 2026-09-12: Washington's UW-run statewide parcel project explicitly states it is years from a public product and requires signed license agreements for some counties (San Juan) — not usable as a shortcut. Oregon's ORMAP is a coordination/index layer, not a rights grant, and its own pages redirect to "contact the county assessor." Idaho's "Public Idaho Parcels" statewide layer's redistribution restriction is specifically about owner-name/mailing-list data (already excluded by this feature's default private-field policy), which may make its geometry-only portion more tractable — but the feature service itself would not open during research, so this remains unconfirmed.

County-level spot-checks (largest/most relevant counties, for a defensible partial-coverage slice):

| County | Terms | Verdict |
|---|---|---|
| King County, WA | Explicit terms page: copy/distribute/use permitted; sole restriction is "selling the data" without written agreement. | Likely admittable — confirm the product isn't construed as "selling" the data itself. |
| Ada County, ID | GIS data free to download; owner-name/mailing data already excluded (matches this feature's policy); full terms live in an unparsed PDF (`GISUserGuide_PUBLIC.pdf`). | Likely admittable, unverified — read the full PDF before acting. |
| Washington County, OR | Terms require anyone who disseminates the data to a third party to indemnify and hold harmless the county from any claims. | Likely blocked / high-friction — an indemnification obligation on redistribution is real legal exposure for a public-facing product; flag for legal review. Oregon county GIS disclaimers are frequently templated, so other OR counties likely carry the same clause. |

County-level spot-checks, round 2 (2026-09-13):

| County | Terms | Verdict |
|---|---|---|
| Spokane County, WA | Standard disclaimer + RCW 42.56.070(8) mailing-list carve-out only; no anti-redistribution clause found. | Likely admittable. |
| Snohomish County, WA | Hold-harmless is for the user's own use, not a redistribution-indemnification requirement (distinct from the Oregon pattern below). | Likely admittable. |
| Whatcom County, WA | Explicit grant: "a limited, revocable license to use, reproduce, and **redistribute** the Data." | Likely admittable — the cleanest explicit redistribution grant found in any county so far. |
| Marion County, OR | Terms page 503'd during research; no license/indemnification text retrieved either way. | Not found — do not assume favorable. |
| Lane County, OR | Requires a **signed** GIS data order/license agreement, not an open-data click-through. Oregon's own state-level sample agreement template carries the same indemnify-on-redistribution clause as Washington County OR. | Likely blocked / high-friction — reinforces a real Oregon-wide pattern, not a one-county fluke. |
| Canyon County, ID | Explicit: "For-profit use is prohibited without the consent of Canyon County." Free redistribution limited to government agencies/contractors. | Likely blocked. |
| Bonneville County, ID | No terms discoverable. | Not found. |

**Ada County correction:** the 2026-09-12 "likely admittable, unverified" placeholder should be walked back, not upgraded. The PDF (`GISUserGuide_PUBLIC.pdf`, mirrored at adacountyassessor.org and adacounty.id.gov) contains a section literally headed "ADA COUNTY ASSESSOR'S OFFICE RESTRICTIONS ON USE:" whose body text could not be extracted by automated fetch — the header alone signals a real restriction exists, which is a more cautious posture than the placeholder assumed. A human needs to open the PDF directly before this county is treated as admittable either way.

County-level spot-checks, round 3 (2026-09-13, Pierce/Clark/Thurston/Yakima/Benton/Skagit/Grant/Chelan):

| County | Terms | Verdict |
|---|---|---|
| Pierce County, WA | Only restriction found (search snippets, not full PDF): must pass along the same disclaimer if the source is revealed. No anti-redistribution/commercial-use ban surfaced. | Likely admittable, lower confidence — terms PDF did not machine-read; open it directly before treating as settled. |
| Clark County, WA | No redistribution clause found; only the statutory RCW 42.56.070(8) owner-list carve-out. | Likely admittable. |
| Thurston County, WA | **Internally contradictory — the most actionable finding of this round.** One clause grants "a limited, revocable license to use, reproduce and redistribute the Data" (word-for-word Whatcom's grant). A separate clause in the same terms bars users from "transfer[ring], sell[ing] or otherwise mak[ing] available any portion of these Data... to a third party in any format revealing Thurston County as the source" — which on its face prohibits the exact redistribution the first clause grants. | **Conflicting — not admittable without a human reading the full terms and reconciling the two clauses.** This directly undercuts the "no anti-redistribution clause found in any WA county" claim below; do not treat Thurston as consistent with Whatcom. |
| Yakima County, WA | Only disclaimer/fitness-for-purpose language found; no redistribution clause surfaced, but the full terms page wasn't directly fetched. | Not confirmed — absence-in-search is weaker evidence than a direct read. |
| Benton County, WA | Search collided with Benton County, **Arkansas**; the actual WA county's terms were not retrieved this pass. | Not found — do not infer anything. |
| Skagit County, WA | Standard as-is disclaimer. One caveat: Skagit won't redistribute data it received from other agencies under those agencies' own license terms — this restricts what Skagit can hand out (third-party-sourced layers), not what a recipient can do with Skagit's own data. | Likely admittable, moderate confidence — verify no state/federal-sourced sublayers are riding along before ingesting. |
| Grant County, WA | Standard as-is disclaimer + the same statutory owner-list carve-out. No redistribution grant, but no prohibition either — the terms are simply silent. | Not found / ambiguous — silence is not permission, same standard already applied to Idaho. |
| Chelan County, WA | Unconditional indemnification clause: "indemnify, defend, and hold harmless Chelan County for any and all liability... arising from... the use or misuse of the data" — not scoped to redistribution the way Oregon's clause is; broader exposure. | Likely blocked / high-friction. |

**State-level pattern — corrected and weaker than the round-2 read.** Across all 12 WA counties now checked: a near-universal substrate (as-is disclaimer + the RCW 42.56.070(8)/(9) owner-list carve-out) appears in King, Spokane, Clark, Thurston, Grant, and Chelan — likely a shared statutory floor every county must comply with, not a county choice, and safe to treat as state-uniform for that one field-level restriction (already consistent with this feature's private-field-exclusion policy). **Redistribution terms do NOT follow a clean statewide pattern**: King/Spokane/Snohomish/Clark read cleanly admittable, Whatcom has the cleanest explicit grant, but Thurston uses Whatcom's identical grant language while pairing it with a contradicting prohibition, Grant and Yakima are silent (undetermined, not permissive), and Chelan's unconditional indemnification clause reads closer to the Oregon pattern than to Whatcom's. 4 of 12 WA counties read cleanly admittable; 1 (Thurston) is a genuine legal contradiction requiring human reconciliation; the rest are unverified or unfavorable. **"Washington as one policy question" is not defensible — continue county-by-county**, same standard already applied to Idaho and Oregon.

Oregon: Washington County and Lane County both carry the same indemnify-on-redistribution shape, and Lane additionally requires a signed contract rather than open download — likely a templated statewide pattern, not coincidence. Idaho remains mixed (Canyon blocked, Ada unresolved, Bonneville unknown).

**Next step:** King, Spokane, Snohomish, and Clark counties (WA) are the actual clean four for a first parcel slice — not "Washington" as a whole. Franklin and Walla Walla counties (WA) were not yet reached and remain open for a future round. Do not generalize any single county's terms to others, including within Washington, without checking each one individually — Thurston is the concrete proof of why.

## BLM lands — resolved without an email (2026-09-13)

Unlike the four gates above, BLM Surface Management Agency and field-office-jurisdiction data did not need a rights-holder contact. Federal-agency works are public domain under 17 U.S.C. § 105, and BLM's own written policy, [IM 2014-029 "Disclaimer Statement Policy for Datasets Held by BLM"](https://www.blm.gov/policy/im-2014-029), confirms this explicitly for its GIS datasets: accuracy/fitness-for-use caveats only, no reuse or redistribution restriction.

**Verdict: likely admittable now**, both layers:

| Layer | Endpoint | Notes |
|---|---|---|
| Surface management | `https://gis.blm.gov/arcgis/rest/services/lands/BLM_Natl_SMA_LimitedScale/MapServer` (sublayer 1); state-level higher-res variant `https://gis.blm.gov/idarcgis/rest/services/realty/BLM_ID_Surface_Management_Agency/FeatureServer` | Confirmed via BLM's own `gis.blm.gov` ArcGIS Server, not a third-party mirror. Multi-agency layer (also carries NPS/USFS/DOD/BIA/etc.) — filter field is `ADMIN_AGENCY_CODE`. |
| Field-office jurisdiction | `https://gis.blm.gov/arcgis/rest/services/admin_boundaries/BLM_Natl_AdminUnit/MapServer` (sublayer 3 = Field Boundary — the parcel-to-office join layer) | Same publisher/policy umbrella. |

**2026-09-13 live verification (both mechanical steps closed):**

- Surface-management filter, confirmed by a live query against sublayer 1 for `ADMIN_ST='ID' AND ADMIN_AGENCY_CODE='BLM'` (one feature returned, no error): `ADMIN_AGENCY_CODE = "BLM"`, `ADMIN_DEPT_CODE = "DOI"`, `ADMIN_UNIT_NAME = "Bureau of Land Management"`. The literal filter is `ADMIN_AGENCY_CODE = 'BLM'`.
- Field-office jurisdiction schema, confirmed via `MapServer/3?f=json`: `ADM_UNIT_CD` (office code), `ADMU_NAME` (office name, e.g. "Bishop Field Office"), `BLM_ORG_TYPE` (State/District/Field/Other), `PARENT_CD`/`PARENT_NAME` (hierarchy), `ADMIN_ST`, `EFF_DT`/`APPRV_DT`.

**No human contact needed for this lane, and no further research step remains open.** What's left is a real ingestion decision, not verification: this repo's data layers are ingested into day-partitioned Parquet lanes read by DuckDB (see `docs/layer-lane-standard.md`), not fetched live from an external API per-request. Wiring `BLM_Natl_SMA_LimitedScale`/`BLM_Natl_AdminUnit` into `src/lib/server/services/land-context/parquet-reader.ts` as a direct live fetch would bypass that pattern and constitutes real acquisition — that's an implementation decision for whoever owns the ingestion pipeline, not something to build as a shortcut inside the reader-contract stub.

## Implementation note

Schema design, reader contracts, UI components, and agent tool parity for these surfaces proceeded in parallel to this gate review under 2026-09-12 owner authorization. Implementation of these contracts is authorized and proceeding independently because it does not require acquiring, importing, or publishing data from any of these four gated sources.

Real ingestion against any of these sources remains blocked until the corresponding rights gate receives explicit clearance via a future gate review. This deferral is deliberate and documented; silence on a source should never be mistaken for approval.


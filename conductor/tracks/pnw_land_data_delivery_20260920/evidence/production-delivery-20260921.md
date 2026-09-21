---
type: evidence
track: pnw_land_data_delivery_20260920
recorded_at: 2026-09-21
status: verified_initial_publication
---

# Initial PNW land-context production publication

The isolated `feat/pnw-land-context-data` worktree, delivered in
[PR #10](https://github.com/Escherbridge/plantgeo/pull/10), implements real BLM reference products,
USDA-derived crop-cover estimates, publication-dependent controls, and explicit unavailable
states for parcels/recorded land use, electric territories and state-managed lands. The user
authorized this implementation, production ingestion and a pull request on September 20.

Production writes completed on September 21 UTC in the existing governed Parquet warehouse.
The initial preflight found no base objects for any of these four products, no BLM state
pointer and no crop availability index. PostgreSQL provided publication locks only. The
new product prefixes contain immutable source evidence and complete serving ladders; no
observation fallback or synthetic ownership/contact record was introduced.

## Published products

| Product / crop year | Partition release day | z13 rows | z9 rows | z5 rows | z0 rows |
| --- | --- | ---: | ---: | ---: | ---: |
| BLM surface management | 2026-09-21 | 39,630 | 3 | 3 | 3 |
| BLM office jurisdiction | 2026-09-21 | 34 | 34 | 34 | 34 |
| BLM office inquiry records | 2026-09-21 | 34 | 34 | 34 | 34 |
| Crop cover 2022 | 2023-01-30 | 107,767 | 6,834 | 447 | 123 |
| Crop cover 2023 | 2024-01-31 | 107,767 | 6,834 | 447 | 123 |
| Crop cover 2024 | 2025-02-27 | 107,759 | 6,833 | 447 | 123 |
| Crop cover 2025 | 2026-02-27 | 109,009 | 6,896 | 450 | 125 |

These are stored records, not unique parcels or independent field observations. Crop base
cells are 3-km equal-area squares; coarse cells are 12, 48 and 96 km. Their 432,302 detailed
records span four editions of the declared PNW envelope, including adjacent US fringes.
BLM coarse boundaries are state aggregates for display. Office jurisdictions and surface
management are distinct interests. Inquiry records carry official names and public state
office websites; route status remains unverified and does not establish responsibility,
an individual contact, or a permit/lease relationship.

## Original source evidence and estimation limits

The BLM capture at `2026-09-21T02:53:48.368027+00:00` binds 41,190 OR/WA source features,
six national SMA Idaho features, 35 office source pieces and three Census state masks.
Clipping to actual WA/OR/ID geography and dissolving duplicate office pieces produces the
served counts above. Original features and identifiers remain in the source archive.

- [BLM OR/WA ownership](https://gis.blm.gov/orarcgis/rest/services/Land_Status/BLM_OR_Ownership/MapServer/0)
- [National BLM surface management used for Idaho](https://gis.blm.gov/arcgis/rest/services/lands/BLM_Natl_SMA_LimitedScale/MapServer/1)
- [BLM administrative units](https://gis.blm.gov/arcgis/rest/services/admin_boundaries/BLM_Natl_AdminUnit/MapServer/3)
- [Census state masks](https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/State_County/MapServer/0)

BLM manifest SHA-256:
`d3ff5ede79ed9be2245748013a4564b3fb6e9bcc4dba5ad35883aea8ccd33af9`.
Content SHA-256:
`f418156340be17c67a08d13481f7a530bb6ed6d0928a047d6640e8ab7a5d4939`.
All 227 original capture objects are archived under
`layer=land-context-boundaries/kind=observed/availability/blm-source`.
Idaho uses the published limited-scale product with explicit display generalization.

Crop data comes from the [official USDA CDL ImageServer](https://pdi.scinet.usda.gov/image/rest/services/CDL_WM/ImageServer).
Each edition binds its exact annual raster item, official publication metadata, legend and
99 raw-class TIFF requests. All pixels were analyzed at 30 m. The 2022/2023 source is native
30 m; 2024/2025 is native 10 m, resampled by nearest neighbour. This estimates classified
crop area at the analysis resolution. It cannot establish planting declarations, parcel
boundaries, ownership, or exact year-to-year land-use change. NoData remains in the whole
cell denominator; classified fraction describes coverage, not model confidence.

| Crop year | Immutable capture manifest SHA-256 |
| --- | --- |
| 2022 | `964563f4259f6ac0fd83312d3b441a9d93383dbc50e172fd1ca9ed81dd45d773` |
| 2023 | `9fb53fa9296f0a36ee2a5dee2d70186f9c2135c917e22ec6db44b5cd5f5c0a4a` |
| 2024 | `69aee4813c1d080daa2505c13f5d984cf8347330de9d9398c0ba1d7da0bf1782` |
| 2025 | `e9b1e08059dc360663a8943a4de468a96f8155c6445840aa352f0714339763fa` |

Each exact manifest is stored at
`layer=crop-cover/kind=observed/availability/source-captures/manifests/<sha256>.json`;
its original raster and metadata bytes are content-addressed alongside it. Source year,
official release day and acquisition interval remain separate. Current captures preserve
the source's present historical products, including any upstream revisions.

## Publication and serving verification

BLM publication reported `completed`, verified every product at all four rungs, compared
each base readback with its reconstructed source table, and advanced the package state from
pending to published. These are versioned static references and have no daily availability
index.

All crop editions first completed physical publication. Their initial command failures
explicitly reported that availability was still incomplete. The subsequent governed
bootstrap digested all 16 rung/edition combinations, excluded zero days, and published four
selectable releases. Availability generation SHA-256:
`2036876282ff95203d123236a45fbe2d54efb262c33fa11a431156b87f2de2fe`.
Bootstrap receipt SHA-256:
`5dc4fdd7cd12fd72a052b7bb42b64541a2c8e26fa3ccb7867285b7d8ec98d53a`.
The final check bound each physical completion receipt to this index and read every table.

The worktree's serving implementation was run against production object storage. All seven
selected-release reads returned `published`, nonempty rows and `truncated=false`. At z5 in
the requested `[-125,42,-111,49]` viewport, crop editions returned 391, 391, 391 and 393 rows;
the BLM products returned 3, 34 and 34. Crop request and served release days matched exactly.
The scoped coverage route returned all 16 product/rung entries with no withheld reason,
all four crop publication points, and no gap ranges. This verifies the publication evidence
consumed by the frontend switches.

These checks used the new code against production data. They do not claim that the current
deployed API or browser already contains the unmerged branch. Machine-readable evidence is
in [production-receipt.json](production-receipt.json). Full raw captures, upload logs and
local validation outputs remain outside Git in `local-artifacts/pnw-land-context` and
`.omc/research`; durable source copies are in the product prefixes above.

## Integrated validation and review

- Frontend data-boundary, TypeScript and ESLint checks passed.
- Full frontend suite: 229 test files, 3,072 tests passed.
- Full Python release sweep: Ruff formatting, Ruff lint, mypy and pytest passed in the
  isolated locked environment. The Docker quality-receipt verifier also passed.
- Python receipt digest:
  `sha256:2dec127f6fc65218a9ad503fa541754830049b87aac12c94d0844525a687cac6`
  over 928 inputs, generated `2026-09-21T03:24:11.125207Z`.
- Source identity, spatial matching, selected-edition agent evidence, explicit annual
  release dates, stale capture protection, archive replay and conserved crop aggregation
  received domain regression coverage. Crop agricultural area was conserved across all
  four rungs for each actual captured edition.
- Separate BLM, crop and frontend reviewers evaluated the completed implementation and
  final fixes; no blocking findings remain.

## Deployment and follow-up

After the PR is merged and Railway deploys its code, verify the live BLM and crop switches,
each crop edition and unavailable notices. Then add the following IDs to the production
active-lane allowlist, preserving every existing active ID:

| Lane ID | Schedule UTC | Duty |
| --- | --- | --- |
| `land-context-blm-forward` | Daily 09:00 | Capture/check current official source |
| `land-context-blm-reconcile` | Daily 11:00 | Detect and repair incomplete published products |
| `land-context-blm-backfill` | Daily 13:00 | Replay admitted source evidence |
| `crop-cover-usda-maintain` | Daily 10:00 | Repair oldest incomplete admitted edition; monthly latest refresh |

Definitions are present as shadow registrations. They were not activated against the old
executor code. Collect the first successful scheduled turns before closing the delivery,
BLM and crop tracks. See the execution directory's schedule contract and each producer's
directory documentation for bounds and replay commands.

[The deferred source track](../../pnw_land_sources_deferred_20260920/spec.md) prioritizes
BLM grazing allotments/pastures, wilderness and study areas, recreation, and separately
typed mineral/lease interests; Annual NLCD; broader crop history and native 10-m processing;
and source admission for parcels, utility territories, state inventories and stronger
documented contact routes. Unavailable families have explanatory notices and no map switch.

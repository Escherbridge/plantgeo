---
type: verification-evidence
---

# PUDL bounded actual-row pilot

Performed November 2 2026 America/Denver (November 3 UTC), read-only against public S3. This supersedes the earlier report's statement that actual rows had not been validated, but is not full dataset certification.

## Release and bounded transport

- Pinned path `v2026.10.0`, rather than mutable stable/nightly aliases. Objects last modified October 14 2026.
- Generator object: `out_eia__yearly_generators.parquet`, 10,643,130 bytes, 666,788 rows, one row group. ETag `a0154f6aa2669f9bc0e9ad9a46dc1ee8`.
- Monthly object: `out_eia923__monthly_generation_fuel_combined.parquet`, 90,286,407 bytes, 3,535,321 rows, four row groups. ETag `dab3ba0967dd157751ef037061b78f34`. Latest group has 389,593 rows covering May 2024â€“June 2026.
- Downloaded footers and eight selected-column byte ranges, not either full data object; used `If-Match` to bind ranges to inspected ETags. SHA-256 captured for each fetched range. These are range hashes, not whole-file hashes; ETags are not described as cryptographic content proofs.
- Also fetched the release-matched 12,326-byte energy-source code table to verify classification and units.
- Total successful HTTP requests including preliminary HEAD: **16**. Total response-body bytes: **5,550,660** (5.29 MiB), under the 20-request/20-MiB cap. One initial sandbox-blocked network probe transferred no data.
- Native venv launcher was broken; system Python 3.12 successfully used installed PyArrow 21 from the project's venv site-packages. No packages installed. Network requests used approved read-only escalation.

## Selection and findings

Projected generator columns were decoded for its sole row group, then restricted to report year 2025 and states WA/OR/ID. 1,419 generator records met the regional/year condition. Selected primary energy codes were WAT/WDS/WDL/BLQ/LFG/OBG/OBS/AB; this is a deliberate hydro/selected-biomass cohort, not an exhaustive biomass classifier or secondary-fuel inventory.

| Check | Annual generator cohort | Monthly generation/fuel cohort |
|---|---:|---:|
| Selected rows | 833 | 3,060 |
| Distinct plants | 275 | 249 |
| Duplicate declared primary keys | 0 | 0 |
| Null key fields | 0 | 0 |
| Maturity | All final | All final |
| Missing latitude/longitude | 1 pair | Not present in this table |
| Out-of-range non-null coordinates | 0 | Not tested |
| Negative capacity | 0 | Not applicable |
| Negative net generation | Not projected | 108 rows |

Generator key `(report_date, plant_id_eia, generator_id)`; monthly key `(plant_id_eia, report_date, prime_mover_code, energy_source_code)`. Monthly rows were restricted to 2025 and plant IDs from the whole 1,419-record regional generator cohort, then the selected fuel codes. All twelve 2025 months occur, but that does not prove each possible plant-month is complete.

Annual fuel counts: WAT 700, WDS 29, LFG 63, WDL 2, BLQ 23, OBG 15, OBS 1. Status: 757 existing, 5 proposed, 71 retired. Therefore 833 cannot be called operating generators. The missing coordinate pair belongs to proposed WA plant 56281 / generator GEN1 / WDS / 28 MW; retain it as an unlocated asset, not a point at an invented centroid. Prime movers include 694 HY and six PS, so the WAT category must be split by prime mover to distinguish conventional hydro from pumped storage.

Monthly fuel counts: WAT 2,508, WDS 204, LFG 156, BLQ 84, OBG 96, OBS 12. All projected monthly values were non-null. The 108 negative-generation rows must be investigated with source semantics, not clipped/rejected wholesale; examples include conventional-hydro rows, so do not explain all negatives as pumped storage without evidence.

## Actual query examples

- Plant 622, generator 1, WA, year 2025: WAT/HY, existing, 16.8 MW; plant-site coordinates 47.699167, -121.783333. January plant/HY/WAT net generation is 2,856 MWh. This supports historical asset/generation context, not an estimate of currently available output.
- Plant 550, generator 1, WA, year 2025: WDS/ST, existing, 50.7 MW. January plant/ST/WDS net generation is 28,249.259 MWh, fuel quantity 47,598 physical units and heat content 422,194 MMBtu. Release-matched WDS code identifies physical units as short tons. Plant-grain generation must not be assigned wholly to a single generator if the plant has others.
- Code table distinguishes WDS/BLQ/OBS/AB in short tons, LFG/OBG in mcf, WDL in barrels; WAT has no physical fuel units. Never sum these physical quantities across codes, or infer water volume from hydro MMBtu. The sampled hydro January record has nonzero MMBtu despite zero physical fuel quantity; understanding the upstream accounting treatment is needed before using it in efficiency/physical water calculations.

## What this validates and does not

It establishes that the pinned public Parquet can be accessed economically; the expected keys/selected fields exist; meaningful US/PNW hydro and biomass records and monthly histories are present; tested cohort keys are unique; geometry and maturity caveats are observable; code-table units support typed agent responses.

It does not certify all PUDL data, source accuracy, plant-to-watershed connectivity, exact asset footprints, current operational capacity, complete per-plant history, secondary-fuel biomass coverage, field-level allocation provenance, reliability/cooling datasets, causal prevention effects, or profitability. No source totals were independently reconciled against EIA forms. Annual/ monthly report dates remain periods, not publication dates or daily readings.

## Artifacts

The artifacts below are local working evidence under `.omc/research/`, not shipped runtime datasets. The public pinned object links and selection details make the pilot reproducible from a fresh checkout.

- `pudl-actual-row-pilot-2026-11-02.json`: object identity, selected columns/ranges, hashes, checks, illustrative records.
- `pudl-sample-metadata-2026-11-02.json`: real footer schemas, row-group inventory and dates.
- `pudl-fuel-code-pilot-2026-11-02.json`: actual classification/units and full small-object SHA-256.
- `out_eia__yearly_generators.pnw-2025-pilot.parquet`: selected 833 records and projected columns.
- `out_eia923__monthly_generation_fuel_combined.pnw-2025-pilot.parquet`: selected 3,060 records and projected columns.
- `pudl_sample_probe.py`, `pudl_actual_sample.py`, `pudl_code_probe.py`: reproducible scratch validation scripts. Sparse transport files were removed by exact known path after selected cohorts were written; original source objects were untouched.

Public objects: [annual generators](https://s3.us-west-2.amazonaws.com/pudl.catalyst.coop/v2026.10.0/out_eia__yearly_generators.parquet), [monthly generation/fuel](https://s3.us-west-2.amazonaws.com/pudl.catalyst.coop/v2026.10.0/out_eia923__monthly_generation_fuel_combined.parquet), [fuel code table](https://s3.us-west-2.amazonaws.com/pudl.catalyst.coop/v2026.10.0/core_eia__codes_energy_sources.parquet).

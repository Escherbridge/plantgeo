---
type: track-evidence
---

# Saved SoilGrids assets: verified evidence and proposed admission

Status: preparation only. No new ingestion, database access, bucket mutation, network
request or test execution occurred in this independent review. The active retirement
track remains open. This concerns static SoilGrids topsoil, not the daily ERA5 soil lane.

## What was independently verified

The saved main-repository `data/raster/soil/manifest.json` has SHA256
`a70359386bea7468b10bda0665f3b1728dd9c0c5118c8d94300e4b7933e92c5f`.
All twelve local files were rehashed: six COGs match the manifest's full hashes and sizes;
six PMTiles match the inspection receipt's independently recorded full local hashes.
They total 237,891,309 bytes, including 63,503,828 COG bytes. Exact per-file hashes are
retained in the [independent local receipt](../../../../.omc/research/soil-independent-local-hashes-20260910.json).
The manifest itself does not bind the PMTiles hashes.

Re-reading local raster and PMTiles metadata reproduced the prior metadata JSON exactly:
[original](../../../../.omc/research/soil-offline-metadata-20260910.json),
[independent inspection](../../../../.omc/research/soil-independent-metadata-20260910.json).
Every COG is one int16 EPSG:4326 grid, 5,353 × 2,222, nodata -32768,
with average overviews 2/4/8/16 and COG layout tags. Pixel steps are
0.003150828497520356 degrees. Actual bounds are
[-127.03887772562518, 42.000619809699636, -110.17249277839872, 49.00176073118987],
not the requested [-125,42,-111,49]. In particular, the southern edge stops just
north of 42 degrees; requested extent alone cannot establish coverage there.
The directory's older 4,943 × 3,027 build note describes a different stage/grid and
must not override these inspected saved-file dimensions. Its measured-population
percentage and historic REST comparison were not recomputed in this review.

| Property | Stored integer divisor | Physical unit |
| --- | ---: | --- |
| phh2o | 10 | pH |
| soc | 10 | g/kg |
| nitrogen | 100 | g/kg |
| bdod | 100 | kg/dm³ |
| cec | 10 | cmol(c)/kg |
| ocd | 10 | kg/m³ |

All six are 0–5 cm mean predictions, tagged SoilGrids v2.0 and CC-BY 4.0.
Their saved PMTiles declare PNG imagery, zooms 0–10 and 1,696 addressed tiles each;
metadata retains physical-unit seven-stop ramps. The publisher derives ramp stops
from valid scaled COG values at quantiles .02/.15/.35/.55/.75/.90/.98.
The tile builder maps those values to a finite palette and uses averaged numeric
overviews before coloring. A colored pixel is therefore not an exact point reading.
Metadata inspection did not decode every PNG or independently prove every ramp color.

The [prior remote receipt](../../../../.omc/research/soil-saved-asset-verification-20260910.json)
records 24 HTTP206 first/last 64-KiB probes, 1,572,864 bytes and 11.812 seconds.
Every sampled range matched its local bytes and advertised total length. It explicitly
records `remote_full_hash_verified=false`: the untouched middle bytes are not verified,
ETags are not SHA256, and matching endpoint samples are not a full remote identity proof.
The script's 24-request/body caps and per-request timeout are explicit; its 180-second
check occurs between requests and is not a hard process deadline. No probe was repeated.

## Meaning and proposed bounded next step

[ISRIC's product documentation](https://docs.isric.org/globaldata/soilgrids/index.html)
describes predicted soil-property maps and depth intervals;
[its layer guidance](https://docs.isric.org/globaldata/soilgrids/SoilGrids_faqs_01.html)
distinguishes properties and statistics. These are model estimates, not individual
field measurements or daily observations. The retained `latest` source URL and v2.0
label do not invent an immutable upstream acquisition timestamp. The saved COGs were
bilinearly reprojected by `scripts/raster/build-soil-cogs.py`; their EPSG:4326 pixels
must not be described as the original native 250-m source grid.

1. Prepare a strict, content-addressed bucket manifest binding all twelve object keys,
   full SHA256/length, release label, license, property/depth/statistic, COG transform,
   CRS, nodata, scale, units, PMTiles hash and exact ramp. Record actual admission time
   separately; preserve unknown upstream retrieval/version evidence explicitly.
2. Before admitting an existing object, perform one bounded full-object verification
   against the saved digest with stable conditional identity, or publish the already
   verified local bytes under immutable versioned keys and verify ordinary write receipts.
   Proposed ceilings: twelve objects, 64 MiB each, 256 MiB total, two concurrent streams,
   ten minutes total. Fail without changing the active manifest if any object changes,
   exceeds a cap or disagrees. Remote range sampling alone cannot authorize admission.
3. Publish the immutable manifest and replace its active pointer through the existing
   lock/compare-and-swap ownership pattern, retaining the prior pointer for rollback.
   This requires a separately reviewed command; the old raster publisher's PostgreSQL
   registration must not be called as an implicit bucket-only migration.
4. A point reader should select the admitted COG's full-resolution pixel using its
   exact affine transform, return that pixel's row/column and center alongside requested
   coordinates, mask nodata before scaling exactly once, and retain physical units.
   Do not sample palette colors or overviews. Bound to six properties, one point and
   a small declared block-read budget; out-of-extent/nodata/timeout remain distinct.
   This is a point lookup on the saved reprojected grid. A truly native-source-grid
   lookup needs the original projected assets and an additional explicit admission.
5. Validate the proposed reader with known raw-pixel fixtures, cell-edge and nodata
   cases, scale/unit checks and source/hash provenance. Register static availability
   and replacement-refresh semantics without manufacturing daily gaps or absence days.
   Existing Parquet point populations, cached REST samples and these raster pixels
   are different supports; no equivalence or full population completeness is claimed.

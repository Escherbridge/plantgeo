# USDA Cropland Data Layer area estimates

## Source, rights and scope

The source is USDA NASS's public annual Cropland Data Layer (CDL), a satellite-derived
classification with agricultural ground reference inputs. The
[USDA FAQ](https://www.nass.usda.gov/Research_and_Science/Cropland/sarsfaqs2.php) permits
redistribution and describes its limitations. Classified pixels do not establish ownership,
legal parcels, planting declarations, utility service territories or zoning. No fabricated
features fill those gaps.

`source.py` queries the official `CDL_WM/ImageServer` catalogue, binds the exact annual
`OBJECTID` and native resolution, and requests single-band unsigned-byte GeoTIFFs with
`rasterFunction=None`, nearest-neighbour interpolation, and an explicit raster lock.
The [ArcGIS exportImage contract](https://developers.arcgis.com/rest/services-reference/enterprise/export-image/)
defines these arguments. RGB renderings, shifted grids, unknown class codes, unexpected
projection, source errors and incomplete tile inventories refuse publication.

The admitted geographic envelope is `[-125,42,-111,49]`, projected into EPSG:5070 and
rounded outward to 3-km grid boundaries. It contains Washington, Oregon and Idaho plus
adjacent US fringes. It is a regional envelope, not a state-border mask; no Canada or ocean
coverage is inferred from absent pixels. Subregional capture/prepare is useful for offline
inspection; publication refuses a bbox smaller than the complete declared PNW envelope.

## Time means annual source publication

`observed_year` identifies the crop year. `release_day` identifies the verified official
publication and is the physical partition day. The admitted releases are:

| Crop year | Release day | Official metadata |
| --- | --- | --- |
| 2022 | 2023-01-30 | `metadata_wa22.htm` |
| 2023 | 2024-01-31 | `metadata_wa23.htm` |
| 2024 | 2025-02-27 | `metadata_CDL24_FGDC-STD-001-1998.htm` |
| 2025 | 2026-02-27 | `metadata_CDL25_FGDC-STD-001-1998.htm` |

Metadata lives under `https://www.nass.usda.gov/Research_and_Science/Cropland/metadata/`.
Each capture rechecks its `dc.date` against this table. January 1 is never invented as a
publication date. Readers select the latest actual release no later than the selected day,
then display its crop year. Captures preserve the annual product as obtained now, including
any upstream revisions; they cannot reconstruct an unpublished original historical edition.
Future crop years require an explicit admission with verified metadata and a history update.

There are no daily observations and no forecast. Nonrelease dates are governed by this annual
calendar, not proof of missing raster data. Generic daily ingestion is refused by the central
lane registration. The annual forward/repair operators select these four dates explicitly.

## What the estimates mean

Every base cell is a 3,000-m EPSG:5070 square. Default exports analyze 30-m pixels;
2022/2023 are native 30-m CDL, while 2024/2025 are native 10-m classifications resampled
with nearest-neighbour interpolation. Native 10-m analysis is an explicit option for those
newer years, with a much larger capture cost. `source_resolution_m` and
`analysis_resolution_m` keep these facts separate. Resampled results are estimates at the
analysis resolution and cannot claim the area accuracy of a full native-resolution count.

All analyzed pixels contribute deterministically. Pixel area is resolution squared, in an
equal-area CRS. The agricultural code set includes classes 1-61 (including hay and
fallow/idle cropland), 66-77 (including orchards and Christmas trees), and 204-254 where the
official legend supplies a class name. Grassland/pasture 176 is excluded. Background 0,
cloud/no-data 81, and 255 are excluded from classified counts. Unknown unnamed codes refuse.

- `class_areas_json`: all classified class codes, including nonagricultural classes, to hectares.
- `class_names_json`: the official legend names for those same class codes.
- `crop_area_ha`: summed agricultural hectares including fallow/idle cropland.
- `crop_fraction`: agricultural hectares divided by the **whole grid square's** hectares.
- `classified_fraction`: all classified hectares divided by that same whole-square area.
- `dominant_crop_code/name`: the largest agricultural class, with ties resolved by lower code;
  null where the cell has no agricultural pixels.

NoData remains in the denominator. A low `classified_fraction` identifies coverage gaps at
coastlines, envelope edges and outside US coverage; neither fraction is classification
confidence. Empty cells are omitted, but valid noncrop cells remain so that the map can
distinguish forest/developed/water coverage from missing data. This is not a parcel product.

Map rungs are 3 km (z13), 12 km (z9), 48 km (z5), and 96 km (z0). Coarse cells sum integer
class counts before recomputing fractions and the dominant class. They never average dominant
codes or fractions. `rows.derive_table` performs the same aggregation from persisted class
areas for repair; it refuses mixed source generations. Grid identity includes year, cell size
and projected grid coordinates. Polygon vertices are transformed to EPSG:4326 for WKB serving.

## Capture and publication

`capture` writes at most 1,600 bounded source tiles, each at most 64 MiB, with a 4-GiB aggregate
ceiling, 1-8 workers and a finite wall-clock budget. Default 30-m requests are at most
4,000 by 4,000 pixels. HTTP errors fail the capture. Each finished tile has a SHA-256 receipt;
interrupted work resumes verified tiles without treating an incomplete inventory as complete.
The closing manifest binds every tile, request scope, catalogue, source metadata and legend.
Replays hash-check all named bytes and validate the complete expected tile inventory.

`source_sha256` is the manifest digest, which in turn binds every exact TIFF digest. Keep
the capture directory and its raw files as one immutable operational artifact. Publication
also archives the complete graph under
`layer=crop-cover/kind=observed/availability/source-captures/`: all TIFFs, catalogue, legend
and official metadata are immutable `blobs/<sha256>` objects; the closing
`manifests/<sha256>.json` object is written last. Four bounded upload workers handle the
regional graph. Direct-source availability evidence names that exact durable manifest key.
Loss of a Railway working directory therefore does not destroy the original annual pixels.

`--operation replay --year <year> --source-manifest-sha256 <digest> --capture-dir <directory>`
restores the original bounded graph from object storage, verifies every digest and source
claim, then closes its local manifest. Run `prepare` or `publish` against that directory to
reconstruct the original rungs without contacting the mutable USDA service. A missing or
corrupt archive member refuses replay. The manifest SHA is present on every served row and
in the availability source receipt, so it remains discoverable after a local cache loss.

A source
refresh uses a **new capture directory**; reusing an old complete directory deliberately
replays that immutable capture. The original mutable USDA service cannot guarantee that no
source changes occurred during a capture; the start and end instants are recorded honestly.
Partial captures persist their original start instant across resumes and each reused tile
must match the original request and carry a capture timestamp.

The package shell is `source -> rows -> adapter -> forward`. `prepare` performs no database
or object-store writes. `publish` uses the shared session-scoped lane-day advisory lock,
base-part writer, count-preserving derived rungs, pruning, completion markers and digested
availability extension. The base completion marker lands last. Data never stages through
PostgreSQL; the database provides only coordination. Production publication requires all
four completion markers. A partial ladder is selected by `repair` and rebuilt as a whole.

Examples, from the service directory with ordinary environment configuration:

```powershell
python -m agri_data_service.pipeline.direct.crop_cover --operation capture --year 2025 --capture-dir data/cdl-2025-a
python -m agri_data_service.pipeline.direct.crop_cover --operation prepare --year 2025 --capture-dir data/cdl-2025-a
python -m agri_data_service.pipeline.direct.crop_cover --operation publish --year 2025 --capture-dir data/cdl-2025-a
python -m agri_data_service.pipeline.direct.crop_cover --operation repair --year 2024 --capture-dir data/cdl-2024-a
```

The scheduled entry point is `--operation maintain --capture-root <durable-working-directory>`.
Each turn selects the oldest admitted release whose four physical completion receipts do
not match its authoritative availability index, then captures and publishes that whole
annual release. Once the history is complete, it refreshes the newest admitted crop year
once per calendar month, using a new `<year>/<YYYY-MM>` capture directory. Each invocation
does at most one annual release. A local file lock serializes maintenance in the working
directory; the shared lane-day database lock serializes actual publication across machines.
Publication refuses an older capture if another writer already published newer source bytes.

Schedule maintenance daily for forward refresh and gap repair, and additional bounded
history turns until all admitted years are published. It refuses to report success while
the availability index is missing or references different completion bytes, even when all
physical data has landed. Initial index bootstrap is a distinct governed operation. A cron
must not mark work complete from a source capture alone. Source fetch failures are retryable
operational failures, never governed absences. NLCD, native-10-m regional publication,
pre-2022 history and future-year admissions are separate follow-up work.

On machines with PostgreSQL's unrelated PROJ installation in environment variables, set
`PROJ_DATA` and `PROJ_LIB` to Rasterio's bundled `proj_data` directory **before importing
Rasterio**. A misconfigured CRS database must fail; the writer does not silently use a
different projection or ignore a CRS mismatch.

---
type: source-register
---

# Restoration Agriculture source register

## Scope and intake rule

This is the authoritative intake queue for the evaluation-only restoration
agriculture evidence plane. It is not a list of model claims and it is not an
authorization to infer intervention effects. Every downloaded object must have
its producer URL, release/version, retrieval time, terms snapshot, checksum,
native support, availability time, and transformation recorded before it can
become a frozen feature or label release.

Kaggle is a discovery and distribution channel, never sufficient provenance.
Its contents are eligible only when the original producer, exact source version,
license/terms, transformation, and payload checksum can be independently
verified. Synthetic or untraceable datasets remain rejected.

## Curated acquisition queue

| ID | Producer and source | Native support and release | Intended bounded use | Status and gates |
| --- | --- | --- | --- | --- |
| `usgs_landsat_c2_l2` | [USGS Landsat Collection 2 Level-2](https://www.usgs.gov/landsat-missions/landsat-collection-2-level-2-science-products) | Global scene products; 30 m surface-reflectance grid and product-specific thermal support/effective resolution; scene acquisition, processing, reprocessing, and QA are required | Predictive vegetation, moisture, and temperature covariates; never property-scale yield, water-use, or treatment-effect outcomes | Admissible public covariate. Save scene ID, collection/processing version, processing date, QA mask, scaling, cloud fraction, retrieval time, AOI geometry version, and any reprocessing event. Do not represent thermal pixels as independent 30 m thermal observations. |
| `ornl_daymet_v4r1` | [Daymet V4.1](https://daymet.ornl.gov/) and its [web services](https://daymet.ornl.gov/web_services.html) | Daily 1 km gridded weather estimates; continental North America/Hawaiʻi from 1980 and Puerto Rico from 1950, through the most recently completed calendar year | Weather/seasonality covariates and horizon baselines | Candidate public covariate. Pin the exact source-year revision and tile/query; preserve Earthdata/access terms, query URL, grid cell(s), variable list, event date, retrieval date, and no-imputation gaps. Do not represent 1 km estimates as field measurements. |
| `usda_nass_cdl_2025` | [USDA NASS 2025 CDL metadata](https://www.nass.usda.gov/Research_and_Science/Cropland/metadata/metadata_CDL25_FGDC-STD-001-1998.htm) | CONUS crop-specific 2025 growing-season land cover; 10 m; published 2026-02-27 | Crop/land-cover context and spatial stratification | Candidate public covariate. Freeze this annual release, its class legend, release-specific accuracy/classification profile, raster checksum, and resampling method. The metadata states that farmer-reported data cannot be derived; it is not a management, outcome, or intervention label. |
| `usda_nass_cdl_2020_wcs_smoke` | [USDA CropScape WCS developer guide](https://nassgeodata.gmu.edu/CropScape/devhelp/cdlwms.html) | Bounded 2020 CDL WCS extraction; EPSG:5070 request, 30 m requested resolution, 642 × 642 requested cells | Local curation and receipt-validation smoke artifact only | Quarantined local sample. Its receipt records payload and guide checksums, but it is not accepted as a model release until a release-specific metadata/terms snapshot and intrinsic GeoTIFF profile are captured. It is a crop/land-cover label artifact, never a restoration or intervention outcome. |
| `usda_nrcs_sda_ssurgo` | [USDA NRCS Soil Data Access](https://sdmdataaccess.sc.egov.usda.gov/webservicehelp.aspx) | Survey polygons and tabular soil properties; native survey support varies materially by area | Static soil context and stratified quality slices | Admissible only after bounded-query profiling. Persist exact SQL/request, survey version, map-unit/component aggregation, support mismatch flag, and retrieval time. Never impute a sensor reading from SSURGO. |
| `usda_nass_quickstats` | [USDA NASS developer catalog](https://www.nass.usda.gov/developer/) | Official agricultural statistics with query-dependent geography, periodicity, disclosure, and revision support; API key required | County-scale yield/production benchmark targets and seasonal evaluation context | Metadata-ready, acquisition awaits a registered API key and a preflight query proving geography, period, units, publication/revision time, and suppression status. It is not property-scale ground truth. |
| `usgs_ghisaconus_v001` | [USGS GHISA](https://www.usgs.gov/centers/western-geographic-science-center/science/global-hyperspectral-imaging-spectral-library) / [NASA data catalog user guide](https://lpdaac.usgs.gov/documents/609/GHISACONUS_User_Guide_V1.pdf) | CONUS crop spectral library; EO-1 Hyperion observations from 2008–2015; five crop classes and growth stages | Offline crop-spectral benchmark/pretraining only | Candidate pending direct original-product access, release-object identity, applicable data-license/terms, and checksum verification. Acquire from NASA/USGS only; the Kaggle mirror below stays quarantined even if its compilation claims U.S.-government ownership. No yield, water, efficacy, or recommendation label. |
| `kaggle_ghisaconus_mirror` | [Kaggle GHISACONUS mirror](https://www.kaggle.com/datasets/billbasener/hyperspectral-library-of-agricultural-crops-usgs) | Kaggle advertises one 11.54 MB CSV, Version 1, US Government Works | Convenience mirror for the GHISACONUS benchmark | Quarantined until checksum, columns, license, and original NASA/USGS release identity agree. Download to local quarantine only; do not make it a system of record. |
| `kaggle_faostat_mirror` | [Kaggle FAOSTAT crop/livestock mirror](https://www.kaggle.com/datasets/vijayveersingh/faostat-crops-and-livestock-data) / [FAOSTAT](https://www.fao.org/Faostat/en/) | Country-year annual statistics, not local operational support | Global macro benchmark and schema cross-check | Quarantined. Re-acquire directly from FAOSTAT bulk download for any accepted use; reconcile all values and flags before retaining a Kaggle copy. No strategy label. |
| `openet` | [OpenET API](https://openet.gitbook.io/docs/reference/api-reference) / [terms](https://etdata.org/terms-of-service) | Model-derived ET outputs; temporal aggregation, spatial support, model, and availability are request/product-specific | Possible future ET context | Blocked. Current terms restrict commercial use, data mining/scraping, and applications interacting with the service without prior written consent; do not retrieve from the API, ingest, derive, or publish OpenET data without written authorization that covers this exact use. |

## Explicit rejections

- Kaggle listings that describe generated/simulated values, lack a source-of-
  source chain, omit a usable license, or claim unsupported field-scale sensor
  provenance are rejected as training or validation evidence.
- Forecast residuals, remote-sensing indices, public crop statistics, utility
  aggregates, and ET values are **not** intervention-effect labels.
- Restoration Agriculture Development/Mark Shepard public material may inform
  vocabulary and strategy taxonomy, but it is not a public experimental dataset
  and supplies no intervention-control outcome record.

## First reproducible public benchmark bundle

The first executable bundle is deliberately predictive-only and public:

1. If its direct original release passes the acquisition gates, use
   `usgs_ghisaconus_v001` as the small supervised crop-spectrum benchmark;
   reserve agroecological zones, growth stages, and source scenes coherently so
   the holdout is not a random row split. Until then, it is not an executable
   bundle and no Kaggle mirror may substitute for it.
2. Add Landsat, Daymet, CDL, and SSURGO only as separately versioned covariate
   releases after a public, non-sensitive AOI and availability calendar are
   chosen.
3. Add NASS Quick Stats only where the frozen preflight query establishes
   published county/annual support; otherwise abstain. Evaluate
   persistence/seasonal-naive and regularized time-honest baselines before
   complexity.
4. Show evidence coverage, predictive uncertainty, and abstention in the demo.
   Do not call this a causal strategy ranking.

## Required release receipt fields

| Field | Requirement |
| --- | --- |
| Identity | Stable source ID, producer URL, distributor URL if distinct, release/version, terms URL and retrieved terms checksum |
| Object | Retrieval timestamp, payload byte size, SHA-256, format, schema fingerprint, row/tile/scene count |
| Time | Observed/event time, producer publication time, local availability time, timezone, revision/correction policy |
| Space | Native coordinate system, spatial support/resolution, AOI/boundary version, aggregation/resampling method, permitted inference scale |
| Quality | Null/duplicate profile, units/ranges, QA flags, missingness periods, join-cardinality proof, rejected-record count |
| Role | Predictive target/covariate/context/benchmark only; treatment assignment and outcome definition are mandatory before any causal role |
| Release | Train/validation/final-holdout assignment, feature availability cutoff, artifact checksum, reviewer decision, approved demo wording |

## Next acquisition sequence

1. Obtain direct access to the original GHISACONUS release and record its
   terms, exact object identity, and checksum. Only then may the Kaggle mirror
   be downloaded into a separate local quarantine receipt for
   checksum/schema/row-count comparison; reject it on any ambiguous mismatch.
2. Register a public non-sensitive AOI and bounded Daymet/CDL/SSURGO requests;
   profile every object using the required receipt fields.
3. Request NASS Quick Stats access and freeze one county/year query manifest.
4. Request written OpenET clarification before making any OpenET retrieval.
5. Only after all accepted public predictive releases pass quality and
   time-availability checks, build the public benchmark bundle. Utility and
   intervention/control data follow a separate consent and governed-label path.

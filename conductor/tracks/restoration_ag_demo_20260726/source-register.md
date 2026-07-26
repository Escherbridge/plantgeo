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

Kaggle is a discovery and distribution channel. A publicly accessible,
non-synthetic dataset may be used for this evaluation-only demo; record the
listed source, version, terms link, transformation, and payload checksum.
Unconfirmed upstream provenance is a visible caveat, not an acquisition
blocker. Explicitly synthetic datasets remain rejected.

## Curated acquisition queue

| ID | Producer and source | Native support and release | Intended bounded use | Status and gates |
| --- | --- | --- | --- | --- |
| `usgs_landsat_c2_l2` | [USGS Landsat Collection 2 Level-2](https://www.usgs.gov/landsat-missions/landsat-collection-2-level-2-science-products) | Global scene products; 30 m surface-reflectance grid and product-specific thermal support/effective resolution; scene acquisition, processing, reprocessing, and QA are required | Predictive vegetation, moisture, and temperature covariates; never property-scale yield, water-use, or treatment-effect outcomes | Admissible public covariate. Save scene ID, collection/processing version, processing date, QA mask, scaling, cloud fraction, retrieval time, AOI geometry version, and any reprocessing event. Do not represent thermal pixels as independent 30 m thermal observations. |
| `ornl_daymet_v4r1` | [Daymet V4.1](https://daymet.ornl.gov/) and its [web services](https://daymet.ornl.gov/web_services.html) | Daily 1 km gridded weather estimates; continental North America/Hawaiʻi from 1980 and Puerto Rico from 1950, through the most recently completed calendar year | Weather/seasonality covariates and horizon baselines | Candidate public covariate. Pin the exact source-year revision and tile/query; preserve Earthdata/access terms, query URL, grid cell(s), variable list, event date, retrieval date, and no-imputation gaps. Do not represent 1 km estimates as field measurements. |
| `usda_nass_cdl_2025` | [USDA NASS 2025 CDL metadata](https://www.nass.usda.gov/Research_and_Science/Cropland/metadata/metadata_CDL25_FGDC-STD-001-1998.htm) | CONUS crop-specific 2025 growing-season land cover; 10 m; published 2026-02-27 | Crop/land-cover context and spatial stratification | Candidate public covariate. Freeze this annual release, its class legend, release-specific accuracy/classification profile, raster checksum, and resampling method. The metadata states that farmer-reported data cannot be derived; it is not a management, outcome, or intervention label. |
| `usda_nass_cdl_2020_wcs_smoke` | [USDA CropScape WCS developer guide](https://nassgeodata.gmu.edu/CropScape/devhelp/cdlwms.html) | Bounded 2020 CDL WCS extraction; EPSG:5070 request, 30 m requested resolution, 642 × 642 requested cells | Local curation and receipt-validation smoke artifact only | Public, non-synthetic smoke artifact. Its receipt records payload and guide checksums; profile the GeoTIFF before modeling. It is a crop/land-cover label artifact, never a restoration or intervention outcome. |
| `usda_nrcs_sda_ssurgo` | [USDA NRCS Soil Data Access](https://sdmdataaccess.sc.egov.usda.gov/webservicehelp.aspx) | Survey polygons and tabular soil properties; native survey support varies materially by area | Static soil context and stratified quality slices | Admissible only after bounded-query profiling. Persist exact SQL/request, survey version, map-unit/component aggregation, support mismatch flag, and retrieval time. Never impute a sensor reading from SSURGO. |
| `usda_nass_quickstats` | [USDA NASS developer catalog](https://www.nass.usda.gov/developer/) | Official agricultural statistics with query-dependent geography, periodicity, disclosure, and revision support; API key required | County-scale yield/production benchmark targets and seasonal evaluation context | Metadata-ready, acquisition awaits a registered API key and a preflight query proving geography, period, units, publication/revision time, and suppression status. It is not property-scale ground truth. |
| `usgs_ghisaconus_v001` | [USGS GHISA](https://www.usgs.gov/centers/western-geographic-science-center/science/global-hyperspectral-imaging-spectral-library) / [NASA data catalog user guide](https://lpdaac.usgs.gov/documents/609/GHISACONUS_User_Guide_V1.pdf) | CONUS crop spectral library; EO-1 Hyperion observations from 2008–2015; five crop classes and growth stages | Offline crop-spectral benchmark/pretraining only | Public benchmark candidate. Prefer a direct NASA/USGS object when available; otherwise use the non-synthetic Kaggle mirror below with its URL/version/checksum and visible provenance caveat. No yield, water, efficacy, or recommendation label. |
| `kaggle_ghisaconus_mirror` | [Kaggle GHISACONUS mirror](https://www.kaggle.com/datasets/billbasener/hyperspectral-library-of-agricultural-crops-usgs) | 6,988 rows, 131 spectral bands, five crop labels, six growth stages, 99 source images; 2008–2015 | First executable public crop-spectrum benchmark | Accepted evaluation-only source; see its [local receipt](./receipts/kaggle-ghisaconus-v1.json). The public Version 1 CSV was non-synthetic on schema/contents inspection. Its Kaggle-listed USGS/NASA lineage remains a visible provenance caveat. |
| `kaggle_faostat_mirror` | [Kaggle FAOSTAT crop/livestock mirror](https://www.kaggle.com/datasets/vijayveersingh/faostat-crops-and-livestock-data) / [FAOSTAT](https://www.fao.org/Faostat/en/) | Country-year annual statistics, not local operational support | Global macro benchmark and schema cross-check | Admissible public aggregate data if non-synthetic. Record the Kaggle and listed FAOSTAT versions, flags, units, row count, and checksum. No strategy label. |
| `openet` | [OpenET API](https://openet.gitbook.io/docs/reference/api-reference) / [terms](https://etdata.org/terms-of-service) | Model-derived ET outputs; temporal aggregation, spatial support, model, and availability are request/product-specific | Possible future ET context | Blocked. Current terms restrict commercial use, data mining/scraping, and applications interacting with the service without prior written consent; do not retrieve from the API, ingest, derive, or publish OpenET data without written authorization that covers this exact use. |

## Explicit rejections

- Kaggle listings that describe generated/simulated values are rejected.
  Missing upstream detail or a licence note becomes a visible dataset caveat;
  it does not block this evaluation-only public-data demo.
- Forecast residuals, remote-sensing indices, public crop statistics, utility
  aggregates, and ET values are **not** intervention-effect labels.
- Restoration Agriculture Development/Mark Shepard public material may inform
  vocabulary and strategy taxonomy, but it is not a public experimental dataset
  and supplies no intervention-control outcome record.

## First reproducible public benchmark bundle

The first executable bundle is deliberately predictive-only and public:

1. Use `kaggle_ghisaconus_mirror` as the small supervised crop-spectrum
   benchmark once downloaded and profiled as non-synthetic; retain the listed
   USGS/NASA lineage as a dataset caveat. Reserve agroecological zones, growth
   stages, and source scenes coherently so the holdout is not a random row
   split.
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
| Identity | Stable source ID, producer URL, distributor URL if distinct, release/version, listed license/terms link or an explicit no-link note |
| Object | Retrieval timestamp, payload byte size, SHA-256, format, schema fingerprint, row/tile/scene count |
| Time | Observed/event time, producer publication time, local availability time, timezone, revision/correction policy |
| Space | Native coordinate system, spatial support/resolution, AOI/boundary version, aggregation/resampling method, permitted inference scale |
| Quality | Null/duplicate profile, units/ranges, QA flags, missingness periods, join-cardinality proof, rejected-record count |
| Role | Predictive target/covariate/context/benchmark only; treatment assignment and outcome definition are mandatory before any causal role |
| Release | Train/validation/final-holdout assignment, feature availability cutoff, artifact checksum, reviewer decision, approved demo wording |

## Next acquisition sequence

1. Download the Kaggle GHISACONUS CSV into the local evidence plane, record its
   URL/version/listed lineage/row count/schema/checksum, and reject it only if
   inspection finds synthetic or malformed contents. Compare with a direct
   original release later if access becomes convenient.
2. Register a public non-sensitive AOI and bounded Daymet/CDL/SSURGO requests;
   profile every object using the required receipt fields.
3. Request NASS Quick Stats access and freeze one county/year query manifest.
4. Request written OpenET clarification before making any OpenET retrieval.
5. Only after all accepted public predictive releases pass quality and
   time-availability checks, build the public benchmark bundle. Utility and
   intervention/control data follow a separate consent and governed-label path.

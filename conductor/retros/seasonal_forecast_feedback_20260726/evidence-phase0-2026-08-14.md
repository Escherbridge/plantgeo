---
type: evidence-report
---

# Phase 0 evidence: forecast iterations and Boise-area data quality

Collected 2026-08-14T20:05:22.979358+00:00 from the retained warehouse over a READ ONLY transaction (`SET TRANSACTION READ ONLY`, 120 s statement timeout, UTC pinned). No row was written.

Cells: na-sample:1deg:p044.00:m116.00, na-sample:1deg:p043.00:m116.00, na-sample:1deg:p044.00:m117.00, na-sample:1deg:p043.00:m117.00.
Observation window: 2022-04-30 to 2026-08-07 (half-open).


## 1. Forecast iteration inventory

| method | status | purpose | availability mode | iterations | series | origins from | origins to | last recorded | max horizon days |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ndvi_seasonal_anomaly_bootstrap_v1 | finalized | forward_simulation | as_of_pinned_release | 1558 | 1558 | 2026-08-05 | 2026-08-05 | 2026-08-05T20:16:04+00:00 | 30 |
| ndvi_seasonal_anomaly_bootstrap_v1 | finalized | holdout_evaluation | retrospective_pinned_release | 118 | 24 | 2025-05-01 | 2026-07-01 | 2026-08-05T19:54:04+00:00 | 30 |

Staleness at collection time: `ndvi_seasonal_anomaly_bootstrap_v1` last recorded 8 day(s) ago.


## 2. Iterations and scored actuals per simulated origin

| method | origin date | iterations | series | scored actuals | latest actual available at |
| --- | --- | --- | --- | --- | --- |
| ndvi_seasonal_anomaly_bootstrap_v1 | 2025-05-01 | 17 | 17 | 53 | 2026-08-05T19:46:54+00:00 |
| ndvi_seasonal_anomaly_bootstrap_v1 | 2025-07-01 | 24 | 24 | 112 | 2026-08-05T19:46:54+00:00 |
| ndvi_seasonal_anomaly_bootstrap_v1 | 2025-09-01 | 24 | 24 | 97 | 2026-08-05T19:46:54+00:00 |
| ndvi_seasonal_anomaly_bootstrap_v1 | 2026-03-01 | 5 | 5 | 14 | 2026-08-05T19:46:54+00:00 |
| ndvi_seasonal_anomaly_bootstrap_v1 | 2026-05-01 | 24 | 24 | 83 | 2026-08-05T19:46:54+00:00 |
| ndvi_seasonal_anomaly_bootstrap_v1 | 2026-07-01 | 24 | 24 | 114 | 2026-08-05T19:46:54+00:00 |
| ndvi_seasonal_anomaly_bootstrap_v1 | 2026-08-05 | 1558 | 1558 | 0 | - |


## 2b. Registered forecast series, by input adapter

| input adapter | metric | signal | series | cells |
| --- | --- | --- | --- | --- |
| forecast_observation | ndvi | (none) | 1568 | 1568 |


## 3. The three observation planes, counted separately

| plane | rows |
| --- | --- |
| agri.forecast_observation | 184,409 |
| agri.normalized_source_feature | 0 |
| agri.signal_coverage_audit | 33,529 |
| agri.signal_observation | 46,146,568 |

| source | releases | landed in signal_observation | landed in forecast_observation | landed in normalized_source_feature | landed nowhere |
| --- | --- | --- | --- | --- | --- |
| kaggle-ghisaconus-mirror | 1 | 0 | 0 | 0 | 1 |
| nasa-power-daily | 1600 | 1600 | 0 | 0 | 0 |
| open-meteo-era5-archive | 8 | 8 | 0 | 0 | 0 |
| open-meteo-era5-land-archive | 202 | 194 | 0 | 0 | 8 |
| sentinel2-ndvi-l2a | 1 | 0 | 1 | 0 | 0 |


## 4. Boise-area governed series: cadence, duplication, missingness, availability

| cell | source | signal | support | unit | rows | observed days | span days | missing days | rows/day | releases | first | last | availability from | availability to |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| na-sample:1deg:p043.00:m116.00 | nasa-power-daily | air_temperature_max | surface | C | 4386 | 1560 | 1560 | 0 | 2.81 | 3 | 2022-04-30 | 2026-08-06 | 2026-08-05T03:09:36+00:00 | 2026-08-09T00:03:22+00:00 |
| na-sample:1deg:p043.00:m116.00 | nasa-power-daily | air_temperature_mean | surface | C | 4386 | 1560 | 1560 | 0 | 2.81 | 3 | 2022-04-30 | 2026-08-06 | 2026-08-05T03:09:36+00:00 | 2026-08-09T00:03:22+00:00 |
| na-sample:1deg:p043.00:m116.00 | nasa-power-daily | air_temperature_min | surface | C | 4386 | 1560 | 1560 | 0 | 2.81 | 3 | 2022-04-30 | 2026-08-06 | 2026-08-05T03:09:36+00:00 | 2026-08-09T00:03:22+00:00 |
| na-sample:1deg:p043.00:m116.00 | nasa-power-daily | dew_point_temperature | surface | C | 4386 | 1560 | 1560 | 0 | 2.81 | 3 | 2022-04-30 | 2026-08-06 | 2026-08-05T03:09:36+00:00 | 2026-08-09T00:03:22+00:00 |
| na-sample:1deg:p043.00:m116.00 | nasa-power-daily | precipitation | surface | mm/day | 4386 | 1560 | 1560 | 0 | 2.81 | 3 | 2022-04-30 | 2026-08-06 | 2026-08-05T03:09:36+00:00 | 2026-08-09T00:03:22+00:00 |
| na-sample:1deg:p043.00:m116.00 | nasa-power-daily | relative_humidity | surface | % | 4386 | 1560 | 1560 | 0 | 2.81 | 3 | 2022-04-30 | 2026-08-06 | 2026-08-05T03:09:36+00:00 | 2026-08-09T00:03:22+00:00 |
| na-sample:1deg:p043.00:m116.00 | nasa-power-daily | soil_wetness_profile | surface | fraction_of_saturation | 4386 | 1560 | 1560 | 0 | 2.81 | 3 | 2022-04-30 | 2026-08-06 | 2026-08-05T19:15:56+00:00 | 2026-08-09T05:18:03+00:00 |
| na-sample:1deg:p043.00:m116.00 | nasa-power-daily | soil_wetness_root_zone | surface | fraction_of_saturation | 4386 | 1560 | 1560 | 0 | 2.81 | 3 | 2022-04-30 | 2026-08-06 | 2026-08-05T19:15:56+00:00 | 2026-08-09T05:18:03+00:00 |
| na-sample:1deg:p043.00:m116.00 | nasa-power-daily | soil_wetness_surface | surface | fraction_of_saturation | 4386 | 1560 | 1560 | 0 | 2.81 | 3 | 2022-04-30 | 2026-08-06 | 2026-08-05T19:15:56+00:00 | 2026-08-09T05:18:03+00:00 |
| na-sample:1deg:p043.00:m116.00 | nasa-power-daily | surface_shortwave_radiation | surface | MJ/m^2/day | 4386 | 1493 | 1493 | 0 | 2.94 | 3 | 2022-04-30 | 2026-05-31 | 2026-08-05T03:09:36+00:00 | 2026-08-08T23:32:36+00:00 |
| na-sample:1deg:p043.00:m116.00 | open-meteo-era5-archive | surface_shortwave_radiation | era5-0.25deg | MJ/m^2/day | 1462 | 1462 | 1462 | 0 | 1.00 | 1 | 2022-08-02 | 2026-08-02 | 2026-08-09T05:35:51+00:00 | 2026-08-09T05:35:51+00:00 |
| na-sample:1deg:p043.00:m116.00 | nasa-power-daily | wind_speed | surface | m/s | 4386 | 1560 | 1560 | 0 | 2.81 | 3 | 2022-04-30 | 2026-08-06 | 2026-08-05T03:09:36+00:00 | 2026-08-09T00:03:22+00:00 |
| na-sample:1deg:p043.00:m117.00 | nasa-power-daily | air_temperature_max | surface | C | 4386 | 1560 | 1560 | 0 | 2.81 | 3 | 2022-04-30 | 2026-08-06 | 2026-08-05T03:10:17+00:00 | 2026-08-09T00:03:40+00:00 |
| na-sample:1deg:p043.00:m117.00 | nasa-power-daily | air_temperature_mean | surface | C | 4386 | 1560 | 1560 | 0 | 2.81 | 3 | 2022-04-30 | 2026-08-06 | 2026-08-05T03:10:17+00:00 | 2026-08-09T00:03:40+00:00 |
| na-sample:1deg:p043.00:m117.00 | nasa-power-daily | air_temperature_min | surface | C | 4386 | 1560 | 1560 | 0 | 2.81 | 3 | 2022-04-30 | 2026-08-06 | 2026-08-05T03:10:17+00:00 | 2026-08-09T00:03:40+00:00 |
| na-sample:1deg:p043.00:m117.00 | nasa-power-daily | dew_point_temperature | surface | C | 4386 | 1560 | 1560 | 0 | 2.81 | 3 | 2022-04-30 | 2026-08-06 | 2026-08-05T03:10:17+00:00 | 2026-08-09T00:03:40+00:00 |
| na-sample:1deg:p043.00:m117.00 | nasa-power-daily | precipitation | surface | mm/day | 4386 | 1560 | 1560 | 0 | 2.81 | 3 | 2022-04-30 | 2026-08-06 | 2026-08-05T03:10:17+00:00 | 2026-08-09T00:03:40+00:00 |
| na-sample:1deg:p043.00:m117.00 | nasa-power-daily | relative_humidity | surface | % | 4386 | 1560 | 1560 | 0 | 2.81 | 3 | 2022-04-30 | 2026-08-06 | 2026-08-05T03:10:17+00:00 | 2026-08-09T00:03:40+00:00 |
| na-sample:1deg:p043.00:m117.00 | nasa-power-daily | soil_wetness_profile | surface | fraction_of_saturation | 4386 | 1560 | 1560 | 0 | 2.81 | 3 | 2022-04-30 | 2026-08-06 | 2026-08-05T19:18:40+00:00 | 2026-08-09T05:18:25+00:00 |
| na-sample:1deg:p043.00:m117.00 | nasa-power-daily | soil_wetness_root_zone | surface | fraction_of_saturation | 4386 | 1560 | 1560 | 0 | 2.81 | 3 | 2022-04-30 | 2026-08-06 | 2026-08-05T19:18:40+00:00 | 2026-08-09T05:18:25+00:00 |
| na-sample:1deg:p043.00:m117.00 | nasa-power-daily | soil_wetness_surface | surface | fraction_of_saturation | 4386 | 1560 | 1560 | 0 | 2.81 | 3 | 2022-04-30 | 2026-08-06 | 2026-08-05T19:18:40+00:00 | 2026-08-09T05:18:25+00:00 |
| na-sample:1deg:p043.00:m117.00 | nasa-power-daily | surface_shortwave_radiation | surface | MJ/m^2/day | 4386 | 1493 | 1493 | 0 | 2.94 | 3 | 2022-04-30 | 2026-05-31 | 2026-08-05T03:10:17+00:00 | 2026-08-08T23:32:41+00:00 |
| na-sample:1deg:p043.00:m117.00 | open-meteo-era5-archive | surface_shortwave_radiation | era5-0.25deg | MJ/m^2/day | 1462 | 1462 | 1462 | 0 | 1.00 | 1 | 2022-08-02 | 2026-08-02 | 2026-08-09T05:35:51+00:00 | 2026-08-09T05:35:51+00:00 |
| na-sample:1deg:p043.00:m117.00 | nasa-power-daily | wind_speed | surface | m/s | 4386 | 1560 | 1560 | 0 | 2.81 | 3 | 2022-04-30 | 2026-08-06 | 2026-08-05T03:10:17+00:00 | 2026-08-09T00:03:40+00:00 |
| na-sample:1deg:p044.00:m116.00 | nasa-power-daily | air_temperature_max | surface | C | 4386 | 1560 | 1560 | 0 | 2.81 | 3 | 2022-04-30 | 2026-08-06 | 2026-08-05T03:10:41+00:00 | 2026-08-09T00:10:13+00:00 |
| na-sample:1deg:p044.00:m116.00 | nasa-power-daily | air_temperature_mean | surface | C | 4386 | 1560 | 1560 | 0 | 2.81 | 3 | 2022-04-30 | 2026-08-06 | 2026-08-05T03:10:41+00:00 | 2026-08-09T00:10:13+00:00 |
| na-sample:1deg:p044.00:m116.00 | nasa-power-daily | air_temperature_min | surface | C | 4386 | 1560 | 1560 | 0 | 2.81 | 3 | 2022-04-30 | 2026-08-06 | 2026-08-05T03:10:41+00:00 | 2026-08-09T00:10:13+00:00 |
| na-sample:1deg:p044.00:m116.00 | nasa-power-daily | dew_point_temperature | surface | C | 4386 | 1560 | 1560 | 0 | 2.81 | 3 | 2022-04-30 | 2026-08-06 | 2026-08-05T03:10:41+00:00 | 2026-08-09T00:10:13+00:00 |
| na-sample:1deg:p044.00:m116.00 | nasa-power-daily | precipitation | surface | mm/day | 4386 | 1560 | 1560 | 0 | 2.81 | 3 | 2022-04-30 | 2026-08-06 | 2026-08-05T03:10:41+00:00 | 2026-08-09T00:10:13+00:00 |
| na-sample:1deg:p044.00:m116.00 | nasa-power-daily | relative_humidity | surface | % | 4386 | 1560 | 1560 | 0 | 2.81 | 3 | 2022-04-30 | 2026-08-06 | 2026-08-05T03:10:41+00:00 | 2026-08-09T00:10:13+00:00 |
| na-sample:1deg:p044.00:m116.00 | nasa-power-daily | soil_wetness_profile | surface | fraction_of_saturation | 4386 | 1560 | 1560 | 0 | 2.81 | 3 | 2022-04-30 | 2026-08-06 | 2026-08-05T19:18:52+00:00 | 2026-08-09T05:21:55+00:00 |
| na-sample:1deg:p044.00:m116.00 | nasa-power-daily | soil_wetness_root_zone | surface | fraction_of_saturation | 4386 | 1560 | 1560 | 0 | 2.81 | 3 | 2022-04-30 | 2026-08-06 | 2026-08-05T19:18:52+00:00 | 2026-08-09T05:21:55+00:00 |
| na-sample:1deg:p044.00:m116.00 | nasa-power-daily | soil_wetness_surface | surface | fraction_of_saturation | 4386 | 1560 | 1560 | 0 | 2.81 | 3 | 2022-04-30 | 2026-08-06 | 2026-08-05T19:18:52+00:00 | 2026-08-09T05:21:55+00:00 |
| na-sample:1deg:p044.00:m116.00 | nasa-power-daily | surface_shortwave_radiation | surface | MJ/m^2/day | 4386 | 1493 | 1493 | 0 | 2.94 | 3 | 2022-04-30 | 2026-05-31 | 2026-08-05T03:10:41+00:00 | 2026-08-08T23:34:41+00:00 |
| na-sample:1deg:p044.00:m116.00 | open-meteo-era5-archive | surface_shortwave_radiation | era5-0.25deg | MJ/m^2/day | 1462 | 1462 | 1462 | 0 | 1.00 | 1 | 2022-08-02 | 2026-08-02 | 2026-08-09T05:35:51+00:00 | 2026-08-09T05:35:51+00:00 |
| na-sample:1deg:p044.00:m116.00 | nasa-power-daily | wind_speed | surface | m/s | 4386 | 1560 | 1560 | 0 | 2.81 | 3 | 2022-04-30 | 2026-08-06 | 2026-08-05T03:10:41+00:00 | 2026-08-09T00:10:13+00:00 |
| na-sample:1deg:p044.00:m117.00 | nasa-power-daily | air_temperature_max | surface | C | 4386 | 1560 | 1560 | 0 | 2.81 | 3 | 2022-04-30 | 2026-08-06 | 2026-08-05T03:11:07+00:00 | 2026-08-09T00:10:31+00:00 |
| na-sample:1deg:p044.00:m117.00 | nasa-power-daily | air_temperature_mean | surface | C | 4386 | 1560 | 1560 | 0 | 2.81 | 3 | 2022-04-30 | 2026-08-06 | 2026-08-05T03:11:07+00:00 | 2026-08-09T00:10:31+00:00 |
| na-sample:1deg:p044.00:m117.00 | nasa-power-daily | air_temperature_min | surface | C | 4386 | 1560 | 1560 | 0 | 2.81 | 3 | 2022-04-30 | 2026-08-06 | 2026-08-05T03:11:07+00:00 | 2026-08-09T00:10:31+00:00 |
| na-sample:1deg:p044.00:m117.00 | nasa-power-daily | dew_point_temperature | surface | C | 4386 | 1560 | 1560 | 0 | 2.81 | 3 | 2022-04-30 | 2026-08-06 | 2026-08-05T03:11:07+00:00 | 2026-08-09T00:10:31+00:00 |
| na-sample:1deg:p044.00:m117.00 | nasa-power-daily | precipitation | surface | mm/day | 4386 | 1560 | 1560 | 0 | 2.81 | 3 | 2022-04-30 | 2026-08-06 | 2026-08-05T03:11:07+00:00 | 2026-08-09T00:10:31+00:00 |
| na-sample:1deg:p044.00:m117.00 | nasa-power-daily | relative_humidity | surface | % | 4386 | 1560 | 1560 | 0 | 2.81 | 3 | 2022-04-30 | 2026-08-06 | 2026-08-05T03:11:07+00:00 | 2026-08-09T00:10:31+00:00 |
| na-sample:1deg:p044.00:m117.00 | nasa-power-daily | soil_wetness_profile | surface | fraction_of_saturation | 4386 | 1560 | 1560 | 0 | 2.81 | 3 | 2022-04-30 | 2026-08-06 | 2026-08-05T19:19:04+00:00 | 2026-08-09T05:22:05+00:00 |
| na-sample:1deg:p044.00:m117.00 | nasa-power-daily | soil_wetness_root_zone | surface | fraction_of_saturation | 4386 | 1560 | 1560 | 0 | 2.81 | 3 | 2022-04-30 | 2026-08-06 | 2026-08-05T19:19:04+00:00 | 2026-08-09T05:22:05+00:00 |
| na-sample:1deg:p044.00:m117.00 | nasa-power-daily | soil_wetness_surface | surface | fraction_of_saturation | 4386 | 1560 | 1560 | 0 | 2.81 | 3 | 2022-04-30 | 2026-08-06 | 2026-08-05T19:19:04+00:00 | 2026-08-09T05:22:05+00:00 |
| na-sample:1deg:p044.00:m117.00 | nasa-power-daily | surface_shortwave_radiation | surface | MJ/m^2/day | 4386 | 1493 | 1493 | 0 | 2.94 | 3 | 2022-04-30 | 2026-05-31 | 2026-08-05T03:11:07+00:00 | 2026-08-08T23:34:46+00:00 |
| na-sample:1deg:p044.00:m117.00 | open-meteo-era5-archive | surface_shortwave_radiation | era5-0.25deg | MJ/m^2/day | 1462 | 1462 | 1462 | 0 | 1.00 | 1 | 2022-08-02 | 2026-08-02 | 2026-08-09T05:35:51+00:00 | 2026-08-09T05:35:51+00:00 |
| na-sample:1deg:p044.00:m117.00 | nasa-power-daily | wind_speed | surface | m/s | 4386 | 1560 | 1560 | 0 | 2.81 | 3 | 2022-04-30 | 2026-08-06 | 2026-08-05T03:11:07+00:00 | 2026-08-09T00:10:31+00:00 |

Null values: 0; unobserved rows: 0; rows flagged other than `accepted`: 0.


## 5. Frozen source-release lineage

| source | source version | transform version | payload checksum | validation | rows | first | last |
| --- | --- | --- | --- | --- | --- | --- | --- |
| nasa-power-daily | nasa-power-daily-v1:20220430-20260430:na-sample:1deg:p043.00:m116.00 | nasa-power-point-sample-normalization-v2 | aa1ed9981dbdd3a3... | valid | 11696 | 2022-04-30 | 2026-04-30 |
| nasa-power-daily | nasa-power-daily-v1:20220430-20260430:na-sample:1deg:p043.00:m116.00 | nasa-power-point-sample-normalization-v2 | ed79e3ed0a45c379... | valid | 11696 | 2022-04-30 | 2026-04-30 |
| nasa-power-daily | nasa-power-daily-v1:20220430-20260430:na-sample:1deg:p043.00:m116.00 | nasa-power-point-sample-normalization-v2 | ab6a198ca57955d2... | valid | 4386 | 2022-04-30 | 2026-04-30 |
| nasa-power-daily | nasa-power-daily-v1:20220430-20260430:na-sample:1deg:p043.00:m117.00 | nasa-power-point-sample-normalization-v2 | 6aded3734f1ac6fc... | valid | 11696 | 2022-04-30 | 2026-04-30 |
| nasa-power-daily | nasa-power-daily-v1:20220430-20260430:na-sample:1deg:p043.00:m117.00 | nasa-power-point-sample-normalization-v2 | c4de756e9511a14e... | valid | 4386 | 2022-04-30 | 2026-04-30 |
| nasa-power-daily | nasa-power-daily-v1:20220430-20260430:na-sample:1deg:p043.00:m117.00 | nasa-power-point-sample-normalization-v2 | e1d4b554d529e96e... | valid | 11696 | 2022-04-30 | 2026-04-30 |
| nasa-power-daily | nasa-power-daily-v1:20220430-20260430:na-sample:1deg:p044.00:m116.00 | nasa-power-point-sample-normalization-v2 | 3c7a8a40e983b9a1... | valid | 4386 | 2022-04-30 | 2026-04-30 |
| nasa-power-daily | nasa-power-daily-v1:20220430-20260430:na-sample:1deg:p044.00:m116.00 | nasa-power-point-sample-normalization-v2 | a9818192a68bc839... | valid | 11696 | 2022-04-30 | 2026-04-30 |
| nasa-power-daily | nasa-power-daily-v1:20220430-20260430:na-sample:1deg:p044.00:m116.00 | nasa-power-point-sample-normalization-v2 | 639eae0f3e416679... | valid | 11696 | 2022-04-30 | 2026-04-30 |
| nasa-power-daily | nasa-power-daily-v1:20220430-20260430:na-sample:1deg:p044.00:m117.00 | nasa-power-point-sample-normalization-v2 | b07881ee8f819402... | valid | 4386 | 2022-04-30 | 2026-04-30 |
| nasa-power-daily | nasa-power-daily-v1:20220430-20260430:na-sample:1deg:p044.00:m117.00 | nasa-power-point-sample-normalization-v2 | 0b6fd1fb7e4b015a... | valid | 11696 | 2022-04-30 | 2026-04-30 |
| nasa-power-daily | nasa-power-daily-v1:20220430-20260430:na-sample:1deg:p044.00:m117.00 | nasa-power-point-sample-normalization-v2 | 37f2349b642caf4d... | valid | 11696 | 2022-04-30 | 2026-04-30 |
| nasa-power-daily | nasa-power-daily-v1:20220531-20260531:na-sample:1deg:p043.00:m116.00 | nasa-power-point-sample-normalization-v2 | 7d1dbb9e3e5db950... | valid | 1462 | 2022-05-31 | 2026-05-31 |
| nasa-power-daily | nasa-power-daily-v1:20220531-20260531:na-sample:1deg:p043.00:m117.00 | nasa-power-point-sample-normalization-v2 | 19b1d07f4a92417a... | valid | 1462 | 2022-05-31 | 2026-05-31 |
| nasa-power-daily | nasa-power-daily-v1:20220531-20260531:na-sample:1deg:p044.00:m116.00 | nasa-power-point-sample-normalization-v2 | 9a8d1939a83b94e0... | valid | 1462 | 2022-05-31 | 2026-05-31 |
| nasa-power-daily | nasa-power-daily-v1:20220531-20260531:na-sample:1deg:p044.00:m117.00 | nasa-power-point-sample-normalization-v2 | f064fd6ff412783e... | valid | 1462 | 2022-05-31 | 2026-05-31 |
| nasa-power-daily | nasa-power-daily-v1:20220805-20260805:na-sample:1deg:p043.00:m116.00 | nasa-power-point-sample-normalization-v2 | 21fdfbe4ed54d1f8... | valid | 4386 | 2022-08-05 | 2026-08-05 |
| nasa-power-daily | nasa-power-daily-v1:20220805-20260805:na-sample:1deg:p043.00:m117.00 | nasa-power-point-sample-normalization-v2 | f1e855727bd04e65... | valid | 4386 | 2022-08-05 | 2026-08-05 |
| nasa-power-daily | nasa-power-daily-v1:20220805-20260805:na-sample:1deg:p044.00:m116.00 | nasa-power-point-sample-normalization-v2 | 46d8f6b1113bf590... | valid | 4386 | 2022-08-05 | 2026-08-05 |
| nasa-power-daily | nasa-power-daily-v1:20220805-20260805:na-sample:1deg:p044.00:m117.00 | nasa-power-point-sample-normalization-v2 | 571c7447eaa98520... | valid | 4386 | 2022-08-05 | 2026-08-05 |
| nasa-power-daily | nasa-power-daily-v1:20220806-20260806:na-sample:1deg:p043.00:m116.00 | nasa-power-point-sample-normalization-v2 | 59cf206ce10a6f39... | valid | 10234 | 2022-08-06 | 2026-08-06 |
| nasa-power-daily | nasa-power-daily-v1:20220806-20260806:na-sample:1deg:p043.00:m116.00 | nasa-power-point-sample-normalization-v2 | 5f4bc2f122334433... | valid | 4386 | 2022-08-06 | 2026-08-06 |
| nasa-power-daily | nasa-power-daily-v1:20220806-20260806:na-sample:1deg:p043.00:m117.00 | nasa-power-point-sample-normalization-v2 | 919b63b6d14b0c62... | valid | 4386 | 2022-08-06 | 2026-08-06 |
| nasa-power-daily | nasa-power-daily-v1:20220806-20260806:na-sample:1deg:p043.00:m117.00 | nasa-power-point-sample-normalization-v2 | d9b687e6ae1ec143... | valid | 10234 | 2022-08-06 | 2026-08-06 |
| nasa-power-daily | nasa-power-daily-v1:20220806-20260806:na-sample:1deg:p044.00:m116.00 | nasa-power-point-sample-normalization-v2 | f4df411ba0bce7b6... | valid | 4386 | 2022-08-06 | 2026-08-06 |
| nasa-power-daily | nasa-power-daily-v1:20220806-20260806:na-sample:1deg:p044.00:m116.00 | nasa-power-point-sample-normalization-v2 | 558477194108671c... | valid | 10234 | 2022-08-06 | 2026-08-06 |
| nasa-power-daily | nasa-power-daily-v1:20220806-20260806:na-sample:1deg:p044.00:m117.00 | nasa-power-point-sample-normalization-v2 | 334fde62ac31de74... | valid | 10234 | 2022-08-06 | 2026-08-06 |
| nasa-power-daily | nasa-power-daily-v1:20220806-20260806:na-sample:1deg:p044.00:m117.00 | nasa-power-point-sample-normalization-v2 | 4a92b43bb49d4ebc... | valid | 4386 | 2022-08-06 | 2026-08-06 |
| open-meteo-era5-archive | open-meteo-era5-land-archive-daily-v1:20220802-20260802:nasa-power-0.5-degree:cells-0004 | open-meteo-era5-land-archive-daily-mean-normalization-v1 | 9a343b78be4e37aa... | valid | 5848 | 2022-08-02 | 2026-08-02 |

29 releases contributed to this window.


## 6. Findings

1. **The spec's Boise WS2M forecast series is not registered here.** `agri.forecast_series` holds 0 series on a non-`forecast_observation` input adapter, so the SQL-linear and daily-increment-bootstrap baselines the spec names as comparators have no registered metric series in this warehouse. Every registered series and every finalized iteration belongs to the Sentinel-2 NDVI lane. The candidate ladder therefore reads the governed observation plane directly, and its comparison to those baselines is a reimplementation of their published method, not a read of their stored output.

2. **Every value in the 2022-04-30-2026-08-06 history became warehouse-visible no earlier than 2026-08-05.** `data_available_at` is the real server-recorded arrival time, and its minimum across all profiled series sits within days of collection. A simulated origin in 2023 therefore reads the 2026 revision of a 2023 observation. This evaluation is **observation-time honest** (no value dated on or after an origin enters that origin's fit) and is **not** revision/point-in-time honest. That is the same `as_of_mode = global` limitation the covariate wind lane records, and it is why no result here may be read as an operational skill estimate.

3. **Cadence and duplication.** 48 of 48 profiled series have no calendar gap inside their observed span. 44 carry more than one admissible row per cell-day, because `uq_signal_observation_release_cell_signal_time` includes `source_release_id` and re-ingests are legitimate. The export deduplicates with `DISTINCT ON (... UTC day) ORDER BY data_available_at DESC, id DESC`, which is the same precedence the shipped covariate reader uses.

4. **Independent scored origins in the existing iteration plane: 6.** They belong to one method on one input adapter, so the existing plane cannot by itself support model selection for a metric series; the frozen export is what supplies the origins.


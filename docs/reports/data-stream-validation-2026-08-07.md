# PlantGeo warehouse completeness and validity

Generated 2026-08-08T03:46:34.576824+00:00 against server day 2026-08-08.
Bbox: `-125,42,-111,49`.
Verdicts: 4 complete, 7 incomplete, 4 invalid.

Axis rules mirrored from `src/lib/server/services/environmental-read-model.ts`: continuity gap 21 days, density floor 1% of the busiest day in the newest cluster.
Scan bounds: 120s statement timeout, read-only snapshot, at most 200,000 observed-day rows, 10 gaps and 10 thin days listed per stream.

## Summary

| Stream | Verdict | Rows | Days | First | Last | Worst gap | Slider window |
| --- | --- | ---: | ---: | --- | --- | ---: | --- |
| `fire-detections` | INCOMPLETE | 476,016 | 480 | 2022-08-05 | 2026-08-07 | 7,947 | 2026-07-23 to 2026-08-07 (16d) |
| `water-gauges` | INVALID | 393,177 | 496 | 1990-09-30 | 2026-08-07 | 223 | 2026-06-08 to 2026-08-07 (61d) |
| `weather-observations` | complete | 6,953 | 6 | 2026-08-03 | 2026-08-08 | 0 | 2026-08-03 to 2026-08-08 (6d) |
| `vegetation` | INCOMPLETE | 184,554 | 1,197 | 2022-08-05 | 2026-08-06 | 7 | 2022-08-05 to 2026-08-06 (1463d) |
| `fire-perimeters` | INVALID | 119 | 27 | 2025-07-28 | 2026-08-06 | 323 | 2026-06-17 to 2026-08-06 (51d) |
| `evacuation-zones` | INCOMPLETE | 457 | 40 | 2025-04-14 | 2026-08-08 | 101 | 2026-06-16 to 2026-08-08 (54d) |
| `sensors` | complete | 38,309 | 10 | 2026-07-30 | 2026-08-08 | 0 | 2026-08-04 to 2026-08-08 (5d) |
| `soil-survey` | INVALID | 218,653 | 0 | - | - | 0 | none |
| `watersheds` | complete | 9,396 | 40 | 2013-01-18 | 2019-11-21 | 755 | 2019-11-21 to 2019-11-21 (1d) |
| `burn-severity` | complete | 478 | 4 | 2020-11-24 | 2024-08-22 | 519 | 2024-08-22 to 2024-08-22 (1d) |
| `interventions` | INCOMPLETE | 0 | 0 | - | - | 0 | none |
| `drought_areas` | INVALID | 1,035 | 207 | 2022-08-09 | 2026-08-04 | 20 | 2022-08-09 to 2026-08-04 (1457d) |
| `historical_vegetation` | INCOMPLETE | 0 | 0 | - | - | 0 | none |
| `historical_fire_data` | INCOMPLETE | 0 | 0 | - | - | 0 | none |
| `historical_water_drought` | INCOMPLETE | 0 | 0 | - | - | 0 | none |

## `fire-detections` -- INCOMPLETE

time_series stream in `features`; publication cadence 1 day(s).

- largest gap is 7,947 day(s) against a 1-day publication cadence

### Completeness

- 476,016 rows over 480 observed days, 2022-08-05 to 2026-08-07.
- Expected from 2000-11-01 (lane_floor) through 2026-08-08: 9412 day(s).
- 8,932 missing days across 7 gap(s), measured inside the expected window only.
- Worst gaps:
  - 2000-11-01 to 2022-08-04 (7,947 days)
  - 2023-11-16 to 2026-07-22 (980 days)
  - 2022-12-23 to 2022-12-23 (1 days)
  - 2022-12-25 to 2022-12-25 (1 days)
  - 2023-03-13 to 2023-03-13 (1 days)
  - 2023-04-21 to 2023-04-21 (1 days)
  - 2026-08-08 to 2026-08-08 (1 days)
- Slider window: 2026-07-23 to 2026-08-07 (16 days, rule `gap_clustered`); clustering dropped 464 day(s) and the density floor dropped 0 more.

### Validity

- Every evaluated check returned zero.
- _missing_value_sentinel: not evaluated -- this producer emits no numeric missing-value marker, so there is no sentinel to find_

### Lanes

- `agri.ingest.archive_walk.firms-archive` run `archive-walk:firms-archive:2000-11-01`: 1,882 window(s), 93 succeeded, 0 retry_wait, 0 dead_letter, 1,789 queued; outstanding firms-archive:2000-11-01..2000-11-06 to firms-archive:2026-07-18..2026-07-23

## `water-gauges` -- INVALID

time_series stream in `features`; publication cadence 1 day(s).

- missing_value_sentinel: 689 row(s) -- -999999 is USGS's 'no reading' marker stored as a JSON number, so it is served as a real measurement and flattens every colour scale it lands in
- largest gap is 223 day(s) against a 1-day publication cadence

### Completeness

- 393,177 rows over 496 observed days, 1990-09-30 to 2026-08-07.
- Expected from 2022-08-05 (lane_floor) through 2026-08-08: 1465 day(s).
- 1,007 missing days across 18 gap(s), measured inside the expected window only.
- 38 observed day(s) carrying 46 row(s) fall BELOW the expected first day, 1990-09-30 to 2020-12-03. These are real observations outside the declared window, so they are neither a gap nor coverage: they open no missing day, and only 458 of 496 observed day(s) count towards the span above.
- Worst gaps:
  - 2024-10-02 to 2025-05-12 (223 days)
  - 2023-10-06 to 2024-04-04 (182 days)
  - 2023-05-15 to 2023-10-04 (143 days)
  - 2025-08-26 to 2026-01-08 (136 days)
  - 2024-06-11 to 2024-09-30 (112 days)
  - 2024-04-06 to 2024-06-09 (65 days)
  - 2025-05-16 to 2025-06-12 (28 days)
  - 2026-04-09 to 2026-04-30 (22 days)
  - 2026-05-02 to 2026-05-23 (22 days)
  - 2026-01-19 to 2026-02-02 (15 days)
  - ...and 8 further gap(s) not listed.
- Slider window: 2026-06-08 to 2026-08-07 (45 days, rule `density_floored`); clustering dropped 450 day(s) and the density floor dropped 1 more.
- 9 day(s) inside that window carry fewer rows than the density floor of 126, so they draw as near-empty days:
  - 2026-07-02: 1 row(s)
  - 2026-07-07: 1 row(s)
  - 2026-07-29: 1 row(s)
  - 2026-06-30: 2 row(s)
  - 2026-07-28: 2 row(s)
  - 2026-07-30: 2 row(s)
  - 2026-08-01: 2 row(s)
  - 2026-07-31: 3 row(s)
  - 2026-07-01: 8 row(s)

### Validity

- **missing_value_sentinel: 689** -- -999999 is USGS's 'no reading' marker stored as a JSON number, so it is served as a real measurement and flattens every colour scale it lands in

### Lanes

- `agri.ingest.archive_walk.streamflow-archive` run `archive-walk:streamflow-archive:2022-08-05`: 48 window(s), 10 succeeded, 0 retry_wait, 0 dead_letter, 38 queued; outstanding streamflow-archive:2023-05-02..2023-06-01 to streamflow-archive:2026-06-15..2026-07-15

## `weather-observations` -- complete

time_series stream in `features`; publication cadence 1 day(s).

- no dead-lettered window, no gap beyond the cadence, and every validity check at zero

### Completeness

- 6,953 rows over 6 observed days, 2026-08-03 to 2026-08-08.
- Expected from 2026-08-03 (first_observed) through 2026-08-08: 6 day(s).
- 0 missing days across 0 gap(s), measured inside the expected window only.
- Slider window: 2026-08-03 to 2026-08-08 (6 days, rule `full_history`); clustering dropped 0 day(s) and the density floor dropped 0 more.

### Validity

- Every evaluated check returned zero.
- _missing_value_sentinel: not evaluated -- this producer emits no numeric missing-value marker, so there is no sentinel to find_

### Lanes

- No lane in the job ledger claims this stream, so nothing records what filled it.

## `vegetation` -- INCOMPLETE

time_series stream in `features`; publication cadence 5 day(s).

- largest gap is 7 day(s) against a 5-day publication cadence

### Completeness

- 184,554 rows over 1,197 observed days, 2022-08-05 to 2026-08-06.
- Expected from 2022-08-05 (first_observed) through 2026-08-08: 1465 day(s).
- 268 missing days across 165 gap(s), measured inside the expected window only.
- Worst gaps:
  - 2025-02-13 to 2025-02-19 (7 days)
  - 2023-11-30 to 2023-12-05 (6 days)
  - 2024-01-19 to 2024-01-24 (6 days)
  - 2023-03-02 to 2023-03-06 (5 days)
  - 2023-04-18 to 2023-04-22 (5 days)
  - 2024-01-08 to 2024-01-12 (5 days)
  - 2025-01-01 to 2025-01-05 (5 days)
  - 2022-12-17 to 2022-12-20 (4 days)
  - 2023-01-12 to 2023-01-15 (4 days)
  - 2023-02-20 to 2023-02-23 (4 days)
  - ...and 155 further gap(s) not listed.
- Slider window: 2022-08-05 to 2026-08-06 (1,197 days, rule `full_history`); clustering dropped 0 day(s) and the density floor dropped 0 more.
- 102 day(s) inside that window carry fewer rows than the density floor of 8, so they draw as near-empty days:
  - 2023-01-08: 1 row(s)
  - 2023-01-20: 1 row(s)
  - 2023-02-16: 1 row(s)
  - 2023-06-19: 1 row(s)
  - 2023-07-24: 1 row(s)
  - 2023-11-19: 1 row(s)
  - 2023-12-28: 1 row(s)
  - 2024-02-27: 1 row(s)
  - 2024-03-11: 1 row(s)
  - 2024-05-20: 1 row(s)
  - ...and 92 further thin day(s) not listed.

### Validity

- Every evaluated check returned zero.
- _malformed_identity: not evaluated -- identity.PRODUCER_BY_LAYER_NAME names no producer for this stream, so no key ceiling applies_
- _missing_value_sentinel: not evaluated -- this producer emits no numeric missing-value marker, so there is no sentinel to find_

### Lanes

- No lane in the job ledger claims this stream, so nothing records what filled it.

## `fire-perimeters` -- INVALID

time_series stream in `features`; publication cadence 1 day(s).

- undated_day: 13 row(s) -- geo.feature_observation_day returns NULL, so the client filter treats the row as undated and shows it at EVERY date on the slider instead of on its own day
- largest gap is 323 day(s) against a 1-day publication cadence

### Completeness

- 119 rows over 27 observed days, 2025-07-28 to 2026-08-06.
- Expected from 2025-07-28 (first_observed) through 2026-08-08: 377 day(s).
- 350 missing days across 9 gap(s), measured inside the expected window only.
- Worst gaps:
  - 2025-07-29 to 2026-06-16 (323 days)
  - 2026-06-27 to 2026-07-07 (11 days)
  - 2026-06-18 to 2026-06-23 (6 days)
  - 2026-07-12 to 2026-07-14 (3 days)
  - 2026-07-09 to 2026-07-10 (2 days)
  - 2026-08-07 to 2026-08-08 (2 days)
  - 2026-06-25 to 2026-06-25 (1 days)
  - 2026-07-20 to 2026-07-20 (1 days)
  - 2026-07-27 to 2026-07-27 (1 days)
- Slider window: 2026-06-17 to 2026-08-06 (26 days, rule `gap_clustered`); clustering dropped 1 day(s) and the density floor dropped 0 more.

### Validity

- **undated_day: 13** -- geo.feature_observation_day returns NULL, so the client filter treats the row as undated and shows it at EVERY date on the slider instead of on its own day
- _missing_value_sentinel: not evaluated -- this producer emits no numeric missing-value marker, so there is no sentinel to find_

### Lanes

- No lane in the job ledger claims this stream, so nothing records what filled it.

## `evacuation-zones` -- INCOMPLETE

time_series stream in `features`; publication cadence 1 day(s).

- largest gap is 101 day(s) against a 1-day publication cadence

### Completeness

- 457 rows over 40 observed days, 2025-04-14 to 2026-08-08.
- Expected from 2025-04-14 (first_observed) through 2026-08-08: 482 day(s).
- 442 missing days across 15 gap(s), measured inside the expected window only.
- Worst gaps:
  - 2025-12-15 to 2026-03-25 (101 days)
  - 2025-09-07 to 2025-12-13 (98 days)
  - 2026-03-27 to 2026-06-15 (81 days)
  - 2025-04-18 to 2025-06-17 (61 days)
  - 2025-07-09 to 2025-08-12 (35 days)
  - 2025-08-14 to 2025-09-02 (20 days)
  - 2026-06-17 to 2026-07-04 (18 days)
  - 2025-06-19 to 2025-06-27 (9 days)
  - 2025-07-03 to 2025-07-07 (5 days)
  - 2025-06-29 to 2025-07-01 (3 days)
  - ...and 5 further gap(s) not listed.
- Slider window: 2026-06-16 to 2026-08-08 (29 days, rule `gap_clustered`); clustering dropped 11 day(s) and the density floor dropped 0 more.

### Validity

- Every evaluated check returned zero.
- _malformed_identity: not evaluated -- identity.PRODUCER_BY_LAYER_NAME names no producer for this stream, so no key ceiling applies_
- _missing_value_sentinel: not evaluated -- this producer emits no numeric missing-value marker, so there is no sentinel to find_

### Lanes

- No lane in the job ledger claims this stream, so nothing records what filled it.

## `sensors` -- complete

snapshot stream in `features`; publication cadence none declared.

- no dead-lettered window, no gap beyond the cadence, and every validity check at zero

### Completeness

- 38,309 rows over 10 observed days, 2026-07-30 to 2026-08-08.
- Expected from 2026-07-30 (first_observed) through 2026-08-08: 10 day(s).
- 0 missing days across 0 gap(s), measured inside the expected window only.
- Slider window: 2026-08-04 to 2026-08-08 (5 days, rule `density_floored`); clustering dropped 0 day(s) and the density floor dropped 5 more.

### Validity

- Every evaluated check returned zero.
- _malformed_identity: not evaluated -- identity.PRODUCER_BY_LAYER_NAME names no producer for this stream, so no key ceiling applies_
- _missing_value_sentinel: not evaluated -- this producer emits no numeric missing-value marker, so there is no sentinel to find_

### Lanes

- No lane in the job ledger claims this stream, so nothing records what filled it.

## `soil-survey` -- INVALID

reference stream in `features`; publication cadence none declared.

- outside_bbox: 3,031 row(s) -- the row falls outside INGEST_BBOX, so it was written past the bounded-ingestion contract and no cron tick will ever refresh or retire it

### Completeness

- 218,653 rows over 0 observed days, never to never.
- Expected from unknown (none) through unknown: unknown day(s).
- 0 missing days across 0 gap(s), measured inside the expected window only.
- Slider window: empty to empty (0 days, rule `no_observations`); clustering dropped 0 day(s) and the density floor dropped 0 more.

### Validity

- **outside_bbox: 3,031** -- the row falls outside INGEST_BBOX, so it was written past the bounded-ingestion contract and no cron tick will ever refresh or retire it
- _malformed_identity: not evaluated -- identity.PRODUCER_BY_LAYER_NAME names no producer for this stream, so no key ceiling applies_
- _undated_day: not evaluated -- a reference layer describes places rather than moments, so an undated row is how it is modelled and not a defect: 218,653 of 218,653 published row(s) carry no observation date, and every one of them shows at EVERY date on the slider rather than on its own day_
- _missing_value_sentinel: not evaluated -- this producer emits no numeric missing-value marker, so there is no sentinel to find_

### Lanes

- No lane in the job ledger claims this stream, so nothing records what filled it.

## `watersheds` -- complete

reference stream in `features`; publication cadence none declared.

- no dead-lettered window, no gap beyond the cadence, and every validity check at zero

### Completeness

- 9,396 rows over 40 observed days, 2013-01-18 to 2019-11-21.
- Expected from 2013-01-18 (first_observed) through 2019-11-21: 2499 day(s).
- 2,459 missing days across 34 gap(s), measured inside the expected window only.
- Worst gaps:
  - 2013-03-01 to 2015-03-25 (755 days)
  - 2018-04-21 to 2019-11-20 (579 days)
  - 2015-10-03 to 2016-03-21 (171 days)
  - 2017-04-27 to 2017-08-23 (119 days)
  - 2015-05-15 to 2015-08-19 (97 days)
  - 2016-07-26 to 2016-10-17 (84 days)
  - 2016-04-08 to 2016-06-01 (55 days)
  - 2016-06-03 to 2016-07-24 (52 days)
  - 2017-11-23 to 2018-01-11 (50 days)
  - 2018-03-02 to 2018-04-19 (49 days)
  - ...and 24 further gap(s) not listed.
- Slider window: 2019-11-21 to 2019-11-21 (1 days, rule `gap_clustered`); clustering dropped 39 day(s) and the density floor dropped 0 more.

### Validity

- Every evaluated check returned zero.
- _malformed_identity: not evaluated -- identity.PRODUCER_BY_LAYER_NAME names no producer for this stream, so no key ceiling applies_
- _undated_day: not evaluated -- a reference layer describes places rather than moments, so an undated row is how it is modelled and not a defect: 0 of 9,396 published row(s) carry no observation date, and every one of them shows at EVERY date on the slider rather than on its own day_
- _missing_value_sentinel: not evaluated -- this producer emits no numeric missing-value marker, so there is no sentinel to find_

### Lanes

- No lane in the job ledger claims this stream, so nothing records what filled it.

## `burn-severity` -- complete

reference stream in `features`; publication cadence none declared.

- no dead-lettered window, no gap beyond the cadence, and every validity check at zero

### Completeness

- 478 rows over 4 observed days, 2020-11-24 to 2024-08-22.
- Expected from 2020-11-24 (first_observed) through 2024-08-22: 1368 day(s).
- 1,364 missing days across 3 gap(s), measured inside the expected window only.
- Worst gaps:
  - 2020-11-25 to 2022-04-27 (519 days)
  - 2022-04-29 to 2023-08-08 (467 days)
  - 2023-08-10 to 2024-08-21 (378 days)
- Slider window: 2024-08-22 to 2024-08-22 (1 days, rule `gap_clustered`); clustering dropped 3 day(s) and the density floor dropped 0 more.

### Validity

- Every evaluated check returned zero.
- _malformed_identity: not evaluated -- identity.PRODUCER_BY_LAYER_NAME names no producer for this stream, so no key ceiling applies_
- _undated_day: not evaluated -- a reference layer describes places rather than moments, so an undated row is how it is modelled and not a defect: 0 of 478 published row(s) carry no observation date, and every one of them shows at EVERY date on the slider rather than on its own day_
- _missing_value_sentinel: not evaluated -- this producer emits no numeric missing-value marker, so there is no sentinel to find_

### Lanes

- No lane in the job ledger claims this stream, so nothing records what filled it.

## `interventions` -- INCOMPLETE

reference stream in `features`; publication cadence none declared.

- the stream holds no published rows at all, so nothing it claims to serve is served

### Completeness

- 0 rows over 0 observed days, never to never.
- Expected from unknown (none) through unknown: unknown day(s).
- 0 missing days across 0 gap(s), measured inside the expected window only.
- Slider window: empty to empty (0 days, rule `no_observations`); clustering dropped 0 day(s) and the density floor dropped 0 more.

### Validity

- Every evaluated check returned zero.
- _malformed_identity: not evaluated -- identity.PRODUCER_BY_LAYER_NAME names no producer for this stream, so no key ceiling applies_
- _undated_day: not evaluated -- a reference layer describes places rather than moments, so an undated row is how it is modelled and not a defect: 0 of 0 published row(s) carry no observation date, and every one of them shows at EVERY date on the slider rather than on its own day_
- _missing_value_sentinel: not evaluated -- this producer emits no numeric missing-value marker, so there is no sentinel to find_

### Lanes

- No lane in the job ledger claims this stream, so nothing records what filled it.

## `drought_areas` -- INVALID

time_series stream in `drought_areas`; publication cadence 7 day(s).

- outside_bbox: 154 row(s) -- the row falls outside INGEST_BBOX, so it was written past the bounded-ingestion contract and no cron tick will ever refresh or retire it
- largest gap is 20 day(s) against a 7-day publication cadence

### Completeness

- 1,035 rows over 207 observed days, 2022-08-09 to 2026-08-04.
- Expected from 2022-08-09 (first_observed) through 2026-08-08: 1461 day(s).
- 1,254 missing days across 207 gap(s), measured inside the expected window only.
- Worst gaps:
  - 2026-02-11 to 2026-03-02 (20 days)
  - 2022-08-10 to 2022-08-15 (6 days)
  - 2022-08-17 to 2022-08-22 (6 days)
  - 2022-08-24 to 2022-08-29 (6 days)
  - 2022-08-31 to 2022-09-05 (6 days)
  - 2022-09-07 to 2022-09-12 (6 days)
  - 2022-09-14 to 2022-09-19 (6 days)
  - 2022-09-21 to 2022-09-26 (6 days)
  - 2022-09-28 to 2022-10-03 (6 days)
  - 2022-10-05 to 2022-10-10 (6 days)
  - ...and 197 further gap(s) not listed.
- Slider window: 2022-08-09 to 2026-08-04 (207 days, rule `full_history`); clustering dropped 0 day(s) and the density floor dropped 0 more.

### Validity

- **outside_bbox: 154** -- the row falls outside INGEST_BBOX, so it was written past the bounded-ingestion contract and no cron tick will ever refresh or retire it
- _unlinked_geometry: not evaluated -- geo.drought_areas holds no properties, no external id and no geometry link_
- _missing_external_id: not evaluated -- geo.drought_areas holds no properties, no external id and no geometry link_
- _malformed_identity: not evaluated -- geo.drought_areas holds no properties, no external id and no geometry link_
- _duplicate_identity: not evaluated -- geo.drought_areas holds no properties, no external id and no geometry link_
- _missing_value_sentinel: not evaluated -- geo.drought_areas holds no properties, no external id and no geometry link_

### Lanes

- No lane in the job ledger claims this stream, so nothing records what filled it.

## `historical_vegetation` -- INCOMPLETE

reference stream in `historical_table`; publication cadence none declared.

- the stream holds no published rows at all, so nothing it claims to serve is served

### Completeness

- 0 rows over 0 observed days, never to never.
- Expected from unknown (none) through unknown: unknown day(s).
- 0 missing days across 0 gap(s), measured inside the expected window only.
- Slider window: empty to empty (0 days, rule `no_observations`); clustering dropped 0 day(s) and the density floor dropped 0 more.

### Validity

- Every evaluated check returned zero.
- _unlinked_geometry: not evaluated -- the geo.historical_* tables hold no properties, no external id and no geometry link_
- _missing_external_id: not evaluated -- the geo.historical_* tables hold no properties, no external id and no geometry link_
- _malformed_identity: not evaluated -- the geo.historical_* tables hold no properties, no external id and no geometry link_
- _duplicate_identity: not evaluated -- the geo.historical_* tables hold no properties, no external id and no geometry link_
- _undated_day: not evaluated -- a reference layer describes places rather than moments, so an undated row is how it is modelled and not a defect: 0 of 0 published row(s) carry no observation date, and every one of them shows at EVERY date on the slider rather than on its own day_
- _missing_value_sentinel: not evaluated -- the geo.historical_* tables hold no properties, no external id and no geometry link_

### Lanes

- No lane in the job ledger claims this stream, so nothing records what filled it.

## `historical_fire_data` -- INCOMPLETE

reference stream in `historical_table`; publication cadence none declared.

- the stream holds no published rows at all, so nothing it claims to serve is served

### Completeness

- 0 rows over 0 observed days, never to never.
- Expected from unknown (none) through unknown: unknown day(s).
- 0 missing days across 0 gap(s), measured inside the expected window only.
- Slider window: empty to empty (0 days, rule `no_observations`); clustering dropped 0 day(s) and the density floor dropped 0 more.

### Validity

- Every evaluated check returned zero.
- _unlinked_geometry: not evaluated -- the geo.historical_* tables hold no properties, no external id and no geometry link_
- _missing_external_id: not evaluated -- the geo.historical_* tables hold no properties, no external id and no geometry link_
- _malformed_identity: not evaluated -- the geo.historical_* tables hold no properties, no external id and no geometry link_
- _duplicate_identity: not evaluated -- the geo.historical_* tables hold no properties, no external id and no geometry link_
- _undated_day: not evaluated -- a reference layer describes places rather than moments, so an undated row is how it is modelled and not a defect: 0 of 0 published row(s) carry no observation date, and every one of them shows at EVERY date on the slider rather than on its own day_
- _missing_value_sentinel: not evaluated -- the geo.historical_* tables hold no properties, no external id and no geometry link_

### Lanes

- No lane in the job ledger claims this stream, so nothing records what filled it.

## `historical_water_drought` -- INCOMPLETE

reference stream in `historical_table`; publication cadence none declared.

- the stream holds no published rows at all, so nothing it claims to serve is served

### Completeness

- 0 rows over 0 observed days, never to never.
- Expected from unknown (none) through unknown: unknown day(s).
- 0 missing days across 0 gap(s), measured inside the expected window only.
- Slider window: empty to empty (0 days, rule `no_observations`); clustering dropped 0 day(s) and the density floor dropped 0 more.

### Validity

- Every evaluated check returned zero.
- _unlinked_geometry: not evaluated -- the geo.historical_* tables hold no properties, no external id and no geometry link_
- _missing_external_id: not evaluated -- the geo.historical_* tables hold no properties, no external id and no geometry link_
- _malformed_identity: not evaluated -- the geo.historical_* tables hold no properties, no external id and no geometry link_
- _duplicate_identity: not evaluated -- the geo.historical_* tables hold no properties, no external id and no geometry link_
- _undated_day: not evaluated -- a reference layer describes places rather than moments, so an undated row is how it is modelled and not a defect: 0 of 0 published row(s) carry no observation date, and every one of them shows at EVERY date on the slider rather than on its own day_
- _missing_value_sentinel: not evaluated -- the geo.historical_* tables hold no properties, no external id and no geometry link_

### Lanes

- No lane in the job ledger claims this stream, so nothing records what filled it.

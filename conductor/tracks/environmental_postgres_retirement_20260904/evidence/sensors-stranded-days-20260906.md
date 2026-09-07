---
type: evidence
---

# The sensors coarse-rung deficit is 25 historical days with a hard date boundary

Measured 2026-09-06 by reading every `layer=sensors/kind=observed/` object in the bucket and then
reading the Parquet footer schema of the stranded and the healthy days.

## The symptom

The live executor's `parquet-sensors` gap-fill tick reports success while carrying a permanently stuck
day:

```
lane="sensors" outcome="complete" remaining=0 ladder_remaining=0 lanes_with_ladder_backlog=[]
detail="2026-08-23: the coarse rungs could not be derived from the published base rung ...
  TierDerivationError: sensors: the tier derivation names coordinate column(s)
  ['station_longitude','station_latitude'] that the base table does not carry; it has
  ['data_available_at','feature_id','measurement_name','network','observed_at','observed_day',
   'quality_control','sensor_id','station_name','unit_code','value']"
```

The production coverage answer prices it: `sensors` publishes **37 days at z13 but only 12 at
z0/z5/z9** — the entire 25-day coarse-rung shortfall across all 28 layers.

## The measurement, and the boundary it found

Bucket listing plus footer reads:

| population | days | span | columns | coords | rungs |
|---|---|---|---|---|---|
| stranded | **25** | 2026-07-30 .. **2026-08-23** | 11 | **no** | z13 only |
| healthy | 14 | **2026-08-24** .. 2026-09-06 | 13 | yes | z0/z5/z9/z13 |

**The cut is exact and falls between 2026-08-23 and 2026-08-24.** Every day written from 2026-08-24
onward carries the coordinate columns and ladders to all four rungs. Every day before it does not and
cannot.

## This is historical, not ongoing — every current writer emits the columns

Verified in all three places that could produce a sensors base rung:

- **The active gap-fill export** — `sql/pipeline/sensors_day_export.sql:119-120`:
  ```sql
  ST_X(ST_Centroid(winning_observation.geom)) AS station_longitude,
  ST_Y(ST_Centroid(winning_observation.geom)) AS station_latitude
  ```
- **The new direct writer** — `pipeline/direct/sensors/rows.py:165-166`:
  ```python
  "station_longitude": longitude,
  "station_latitude": latitude,
  ```
- **The declared schema** — `warehouse/schemas/sensors.py:97-98`, both `pa.float64()`, plus
  `longitude_column`/`latitude_column` bound at `:109-110`.

So no new sensors day will strand. This corrects any reading of the executor log that treats the
`sensors` `TierDerivationError` as a live, recurring fault: it is 25 fixed days of pre-2026-08-24
history, and the lane's `history_floor` is 2026-07-29, so it is essentially that whole early window.

## The remedy, and why it is not just "re-tick"

Re-deriving cannot work — the base rung genuinely lacks the columns the derivation needs, which is
what the error says. The days must be **retracted and re-exported**. The re-export will succeed,
because the gap-fill SQL above emits the coordinates and the source rows still live in Postgres.

Two things stand in the way and both are small:

1. **There is no generic retract verb.** `agri-service data --help` offers
   `parquet-retract-vegetation-absences`, `parquet-rewrite-vegetation` and `parquet-rewrite-signal` —
   all lane-specific. `agri-service ops --help` offers none. So this needs either a bounded new verb
   or a deliberate object deletion.
2. **Gap-fill will not re-export a day that already carries a completion marker**, which is why the
   retract must come first rather than being skipped.

Bounded scope: 25 days, `2026-07-30 .. 2026-08-23`, one lane, one rung to retract (z13) and one
gap-fill pass to rebuild all four.

## Relationship to the signal lane

`signal` shows the same error class at 2026-08-06 for `['cell_longitude','cell_latitude']`, and the
same root cause is already documented at `agent/warehouse.py:362-370` — a day "exported before the
positions were added and has not been re-exported since". Unlike `sensors`, `signal` already has its
retract tooling: `parquet-rewrite-signal-census` (read-only, classifies every day as `legacy`,
`current` or `refused`) feeding `parquet-rewrite-signal`. A full-history census over
2022-04-30..2026-09-06 is running as this is written.

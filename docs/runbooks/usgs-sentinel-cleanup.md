# Runbook — remove the USGS `-999999` sentinel rows from `water-gauges`

> **NOTHING IN THIS FILE HAS BEEN RUN.** Only the read-only `SELECT`s in step 1 were executed, against
> production, inside a `READ ONLY` transaction that was rolled back. Every `DELETE` below is a proposal
> and must be reviewed and run deliberately by someone who has read step 0.

## What happened

USGS NWIS writes `-999999` in place of a discharge it does not have. It arrives as an ordinary numeric
string, so an unguarded parse stores it as a real measurement. The archive parser
(`parse_daily_value_series`) has always refused it; the forward parser (`parse_gauge`, the instantaneous
path the 30-minute cron runs) did not, and wrote it straight into `geo.features.properties->>'flowCfs'`.

Stored as a reading it flattens every percentile, colour ramp and forecast feature computed from
streamflow — a single `-999999` next to values in the hundreds destroys the scale for the whole layer.

The parser is fixed in `services/agri-data-service/src/agri_data_service/ingest/usgs_nwis.py`
(`is_missing_value_sentinel`; see `ingest/AGENTS.md` §`usgs_nwis.py`). This runbook covers the rows
already in the warehouse.

## Step 0 — order of operations, and why it matters

**Deploy the parser fix first, and confirm one clean cron tick, before deleting anything.** The forward
job runs every 30 minutes. Cleaning up against an unfixed producer wins back an average of 15 minutes
before the next tick rewrites the same rows for whichever gauges are still sentinel — measured
2026-08-07, 11 of 194 series in a single PNW tile were sentinel at that moment.

Confirm the fix is live by checking one cron summary line for the new `details.sentinel_gauges` field:
a non-zero count there with no new `flowCfs = -999999` rows appearing is the proof the guard is running.

## Step 1 — read-only: count what is affected (RUN THIS FIRST)

Safe to run at any time. This is the exact query the validation report's `missing_value_sentinel` check
uses, narrowed to `water-gauges`.

```sql
-- READ ONLY. Confirms the blast radius before anything is deleted.
SELECT count(*)                                                       AS sentinel_rows,
       count(DISTINCT features.properties ->> 'siteNo')               AS distinct_sites,
       count(*) FILTER (WHERE features.status = 'published')          AS published_rows,
       count(*) FILTER (WHERE features.geometry_id IS NOT NULL)       AS geometry_linked_rows,
       min(geo.feature_observation_day(features.properties))          AS earliest_day,
       max(geo.feature_observation_day(features.properties))          AS latest_day
  FROM geo.features AS features
  JOIN geo.layers   AS layers ON layers.id = features.layer_id
 WHERE layers.name = 'water-gauges'
   -- Matched by VALUE, never by sign: genuine reverse flow reaches -172,000 cfs at these gauges.
   AND jsonb_typeof(features.properties -> 'flowCfs') = 'number'
   AND (features.properties ->> 'flowCfs')::double precision = -999999;
```

**Measured 2026-08-07 against production, read-only:**

| sentinel_rows | distinct_sites | published_rows | geometry_linked_rows | earliest_day | latest_day |
| ------------- | -------------- | -------------- | -------------------- | ------------ | ---------- |
| **680**       | 27             | 680            | 680                  | 2024-04-05   | 2026-08-07 |

Every affected row is `published` and geometry-linked, so every one of them is both drawn on the map and
counted by the time axis. The whole `water-gauges` layer held 390,082 rows at that moment, so this is
0.17% of the layer by row count and 100% of the damage to its numeric scale.

Per-day breakdown, which is what identifies this as a live forward-cron defect rather than a historical
artifact — **669 of the 680 were written in the six days to 2026-08-07**:

```sql
-- READ ONLY.
SELECT geo.feature_observation_day(features.properties) AS observed_day,
       count(*)                                         AS sentinel_rows
  FROM geo.features AS features
  JOIN geo.layers   AS layers ON layers.id = features.layer_id
 WHERE layers.name = 'water-gauges'
   AND jsonb_typeof(features.properties -> 'flowCfs') = 'number'
   AND (features.properties ->> 'flowCfs')::double precision = -999999
 GROUP BY 1
 ORDER BY 1 DESC;
```

| observed_day | rows |
| ------------ | ---- |
| 2026-08-07   | 108  |
| 2026-08-06   | 141  |
| 2026-08-05   | 136  |
| 2026-08-04   | 82   |
| 2026-08-03   | 167  |
| 2026-08-02   | 35   |
| earlier      | 11 rows total, roughly one per quarter back to 2024-04-05 |

The pre-2026-08-02 trickle is not evidence the bug is recent — the bug is as old as the parser. The rate
tracks how many gauges are seasonally out of service (`Ssn` qualifier), not how long the defect existed.

## Step 2 — read-only: the two side effects to accept before deleting

### 2a. Four days leave the time axis

```sql
-- READ ONLY. Days whose ONLY water-gauges rows are sentinel rows.
WITH gauge_rows AS (
    SELECT geo.feature_observation_day(features.properties) AS observed_day,
           jsonb_typeof(features.properties -> 'flowCfs') = 'number'
             AND (features.properties ->> 'flowCfs')::double precision = -999999 AS is_sentinel
      FROM geo.features AS features
      JOIN geo.layers   AS layers ON layers.id = features.layer_id
     WHERE layers.name = 'water-gauges'
       AND features.status = 'published'
       AND features.geometry_id IS NOT NULL
)
SELECT observed_day, count(*) AS rows_on_day
  FROM gauge_rows
 WHERE observed_day IS NOT NULL
 GROUP BY observed_day
HAVING count(*) = count(*) FILTER (WHERE is_sentinel)
 ORDER BY observed_day;
```

**Measured 2026-08-07: four days — 2024-04-05, 2025-05-15, 2025-08-25, 2026-05-24 — hold exactly one
water-gauges row each, and it is a sentinel.** Deleting removes those four days from the layer's date
slider. That is the correct outcome, not a regression: the layer never held a real streamflow reading on
those days, and the day was only ever advertised on the strength of a fabricated measurement.

### 2b. One geometry-dimension row is left unreferenced

```sql
-- READ ONLY. How many geo.geometry rows the sentinel features point at, and how many of those
-- would have no remaining feature referencing them after the delete.
SELECT count(DISTINCT features.geometry_id) AS geometry_rows_touched,
       count(DISTINCT features.geometry_id) FILTER (
           WHERE NOT EXISTS (
               SELECT 1
                 FROM geo.features AS other
                WHERE other.geometry_id = features.geometry_id
                  AND NOT (jsonb_typeof(other.properties -> 'flowCfs') = 'number'
                           AND (other.properties ->> 'flowCfs')::double precision = -999999)
           )
       ) AS geometry_rows_left_unreferenced
  FROM geo.features AS features
  JOIN geo.layers   AS layers ON layers.id = features.layer_id
 WHERE layers.name = 'water-gauges'
   AND jsonb_typeof(features.properties -> 'flowCfs') = 'number'
   AND (features.properties ->> 'flowCfs')::double precision = -999999;
```

**Measured 2026-08-07: 27 geometry rows touched, 1 left unreferenced.** Do **not** delete geometry rows
as part of this cleanup. The dimension is keyed by the *place*, not by the observation (see
`ingest/AGENTS.md` §"geometry.py: the dimension is keyed by the place, not by the observation"), so 26
of those 27 rows still back that gauge's good readings and deleting them would break real rows. The one
left unreferenced is a gauge whose only stored rows were sentinels; leaving it is harmless — it is a
dimension entry with no current fact, not a dangling reference.

## Step 3 — proposed correction: DELETE, not UPDATE (NOT RUN)

**`DELETE`, not `UPDATE ... SET flowCfs = null`.** Nulling the value keeps a row asserting that the gauge
made an observation at a specific instant and that the observation was "no value" — a fabricated
observation of an absence, which is precisely what the fixed parser now refuses to write. Correcting the
stored rows to a shape the producer would never emit would leave the warehouse permanently
self-inconsistent. There is also no value to recover: `-999999` carries no information beyond "this gauge
did not measure", and the gauge's last real reading is already stored under its own real timestamp.

Run inside an explicit transaction and inspect the count before committing.

```sql
-- ⚠️  NOT RUN. Review, then run deliberately, and only AFTER the parser fix is deployed (step 0).
BEGIN;

SET LOCAL statement_timeout = '120s';

DELETE FROM geo.features AS features
 USING geo.layers AS layers
 WHERE layers.id = features.layer_id
   AND layers.name = 'water-gauges'
   AND jsonb_typeof(features.properties -> 'flowCfs') = 'number'
   AND (features.properties ->> 'flowCfs')::double precision = -999999;

-- Expect 680 as of 2026-08-07. A LARGER number means the cron has written more since this runbook
-- was measured (re-check step 0 — the fix may not be live). A SMALLER number means someone else has
-- already cleaned up. Either way, read before you commit.

-- ROLLBACK;   -- default: leave the transaction rolled back until the count is understood
-- COMMIT;     -- only after the count above has been read and accepted
```

## Step 4 — verify

Re-run the step 1 `SELECT`. It must return `sentinel_rows = 0`.

Then re-run the data-validation report (`missing_value_sentinel` check on the `water-gauges` stream) and
confirm the finding has cleared. Watch `details.sentinel_gauges` on the next few `ingest-streamflow` cron
summaries: a non-zero count there with `sentinel_rows` still at 0 is the steady state — it means gauges
are reporting the sentinel and the parser is correctly declining to store it.

## What this runbook does not do

- It does not backfill the dropped ticks. There is nothing to backfill: those gauges did not measure.
- It does not touch `geo.geometry` (step 2b).
- It does not touch any layer other than `water-gauges`. Per `validation.py`'s
  `MISSING_VALUE_SENTINEL_PROPERTY_BY_STREAM`, `water-gauges` is the only stream whose producer emits a
  numeric missing-value marker at all; every other layer signals absence with an absent key.

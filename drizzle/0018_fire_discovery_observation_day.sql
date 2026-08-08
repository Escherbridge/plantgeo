-- Date the fire perimeters WFIGS publishes with no polygon timestamp.
--
-- geo.feature_observation_day (drizzle/0015_tile_observation_day.sql) coalesced
-- observedAt -> updatedAt -> polygonDateTime. Measured on production 2026-08-07
-- (PostgreSQL 18.4), every one of the 119 published `fire-perimeters` rows carries BOTH
-- `polygonDateTime` and `fireDiscoveryDateTime`, and on 13 of them `polygonDateTime` is
-- JSON null. `'{"a": null}'::jsonb ->> 'a'` is SQL NULL, so COALESCE stepped straight past
-- it, ran off the end of the chain and returned NULL.
--
-- Those 13 rows are not bad data. WFIGS legitimately publishes a perimeter whose polygon
-- carries no timestamp, and every one of them names a valid discovery instant
-- ("2026-07-16T01:07:00.000Z" and eight other days). The function's key list was short by
-- one, so the warehouse threw away a date it had been given.
--
-- The cost of that is not a blank row, it is a wrong one: an undated feature is KEPT at
-- every slider date by src/lib/map/tile-layer-date-filter.ts's ["!", ["has",
-- "observed_day"]] clause. Keeping a row the warehouse genuinely cannot date is correct --
-- deleting data from the map on the strength of a parse failure would be worse. Keeping
-- these 13 is not, because they have a date, so they were drawn as if they had existed on
-- every day of a four-year axis, including a year before their fire started.
--
-- Everything else about the function is unchanged and deliberately so. This is CREATE OR
-- REPLACE over an identical signature: no catalog change, no Martin restart, and the five
-- tile functions that emit the attribute (geo.burn_severity_tiles,
-- geo.evacuation_zone_tiles, geo.fire_risk_tiles and geo.sensor_tiles from 0015, plus
-- geo.watershed_tiles from 0017) keep working untouched. Verified on production that no
-- index expression and no column default or generated column references this function, so
-- redefining it leaves nothing stale to reindex -- but it stays IMMUTABLE regardless,
-- because that declaration is what makes the future functional index legal at all
-- (src/lib/server/db/AGENTS.md §tile-observation-day proves the promotion is safe).
--
-- Blast radius, measured by evaluating this exact chain against production before writing
-- it: of the 119 published fire-perimeters rows, 106 are unchanged and 13 become dated.
-- No other layer in the warehouse carries a `fireDiscoveryDateTime` key at all.

CREATE OR REPLACE FUNCTION geo.feature_observation_day(feature_properties jsonb)
RETURNS date
LANGUAGE sql
-- Declared IMMUTABLE over two callees PostgreSQL catalogues STABLE. That is a knowing,
-- safe promotion, not an oversight -- AGENTS.md §tile-observation-day proves both halves.
IMMUTABLE
PARALLEL SAFE
SET search_path = public, pg_catalog
AS $$
  -- The ten characters the publisher named, read once so the COALESCE order cannot fork.
  -- NEVER `(...)::timestamptz::date` and never `(... AT TIME ZONE 'UTC')::date`: an
  -- instant-based conversion moves 6,279 of the 16,743 production water-gauge rows onto
  -- the day AFTER the one they name, which is the disagreement this function prevents.
  --
  -- BOTH guards must hold before to_date runs, and neither is decoration. The regex proves
  -- the shape; pg_input_is_valid proves the day EXISTS, because `to_date('2026-02-31',
  -- 'YYYY-MM-DD')` raises "date/time field value out of range" and one raise inside
  -- ST_AsMVT blanks the entire tile -- every feature in it, not just the bad row. A row
  -- that cannot be dated returns NULL and is treated as undated by the client filter,
  -- which shows it at every date rather than hiding it.
  SELECT CASE
           WHEN named.day ~ '^\d{4}-\d{2}-\d{2}$'
                AND pg_input_is_valid(named.day, 'date')
             THEN to_date(named.day, 'YYYY-MM-DD')
         END
    FROM (
      SELECT substring(
        COALESCE(
          feature_properties ->> 'observedAt',
          feature_properties ->> 'updatedAt',
          feature_properties ->> 'polygonDateTime',
          -- LAST, and the position is the whole point. `fireDiscoveryDateTime` is when the
          -- INCIDENT was found; `polygonDateTime` is when the GEOMETRY on this row was
          -- mapped. They are different events and discovery is routinely the earlier one:
          -- a fire is reported, and the perimeter is flown, digitized and republished for
          -- days or weeks afterwards, each release a new snapshot of the same incident.
          -- Ranked ABOVE polygonDateTime it would re-date all 106 rows that do carry a
          -- polygon timestamp back onto their incident's discovery day, collapsing a
          -- perimeter's revision history onto one day and making the slider show a
          -- late-August footprint as though it had been known in mid-July. Ranked last it
          -- fires only where every timestamp that describes the geometry itself is absent,
          -- which is exactly the 13 rows and nothing else.
          --
          -- It is a fallback, not a co-equal key, so it is also NOT in
          -- PUBLISHER_NAMED_DAY_RULE.observationTimeKeys: the slider axis
          -- (environmental-read-model.ts) still buckets days from the three shared keys.
          -- That divergence advertises no day the axis lacks -- measured, all nine days
          -- these 13 rows land on already appear on the fire-perimeters axis from rows
          -- with a real polygonDateTime -- and the client filter is `<=`, not `=`, so a
          -- dated row draws from its own day forward regardless. See
          -- src/__tests__/lib/observation-day-contract.test.ts, which pins the chain as
          -- the rule's keys in order followed by exactly this one tile-only fallback.
          feature_properties ->> 'fireDiscoveryDateTime'
        ),
        1,
        10
      )
    ) AS named(day);
$$;

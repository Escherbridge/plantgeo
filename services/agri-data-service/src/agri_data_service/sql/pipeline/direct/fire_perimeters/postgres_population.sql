-- Purpose: D2's parity ground list for the fire-perimeters lane -- one row per published perimeter
--          PostgreSQL's `geo.features` currently serves, reduced to the three facts the receipt
--          compares: the incident identifier, the observation day, and a digest of the geometry.
-- Loaded by: agri_data_service.pipeline.direct.fire_perimeters.parity
-- Params: none. Unlike the sensors and weather-observations twins this file binds nothing -- the
--         layer is selected by name rather than by a caller-resolved id (see below), and this lane
--         compares a whole population rather than one day at a time.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md -- SQLAlchemy's text() scans comments too, and a colon-prefixed word here would
-- mint a phantom bind parameter no caller supplies.
--
-- THIS IS A POPULATION COMPARISON, NOT A DAY-BY-DAY ONE, and the lane's nature is why. A
-- `release_series` lane like drought owes one partition per release, so its parity query groups by
-- `valid_date`. Fire perimeters is a `static_lookup`: `geo.features` holds ONE ROW PER WFIGS
-- INCIDENT, refreshed in place, and keeps no record of what it said yesterday. The only honest
-- question is therefore whether the newest published VERSION reproduces the population PostgreSQL
-- is serving right now, which is the whole of what this query returns.
--
-- THE PREDICATES ARE A TRANSCRIPTION of `sql/pipeline/fire_perimeters_day_export.sql`'s own WHERE
-- clause, which is itself a transcription of `geo.fire_risk_tiles`. Matching them exactly is what
-- makes the count meaningful: a parity receipt built on a looser filter would compare Parquet
-- against rows the existing Postgres-reading lane adapter would never have exported.
--
-- WHY THERE IS NO `features.geometry_id IS NOT NULL` GATE, deliberately: the tile function has no
-- such predicate, an unlinked feature is still drawn, and orphans regrow because the forward path
-- does not maintain the geometry dimension. Adding that gate could silently drop a served
-- perimeter from this side of the comparison and report parity the map would contradict.
--
-- How this query works, clause by clause:
--
--   features.properties ->> 'uniqueFireIdentifier' AS unique_fire_identifier
--     The WFIGS incident key both sides are matched on. `->>` (not `->`) so it arrives as text
--     rather than as a quoted JSON scalar, which is the shape the Parquet column holds.
--
--   geo.feature_observation_day(features.properties)::text AS observed_day
--     The same database function the census, the export and the map's date slider all call
--     (drizzle/0018_fire_discovery_observation_day.sql). It returns NULL for a row it cannot date,
--     and such a row must show at EVERY slider date -- the client keeps it via
--     `src/lib/map/tile-layer-date-filter.ts`'s `["!", ["has", "observed_day"]]`. That NULL is
--     carried through rather than filtered out, because the retired day export DELETED those rows
--     (its `= observed_day` equality can never match NULL) and the receipt counts the NULL bucket
--     on both sides explicitly. `::text` so the caller compares ISO strings on both sides rather
--     than a date object against a string.
--
--   md5(ST_AsBinary(features.geom)) AS geometry_md5
--     The geometry compared BY DIGEST rather than by bytes on the wire. Pulling roughly 23 MB of
--     WKB across the connection to compare it would make this receipt cost more than a
--     publication; `md5` over a scan costs almost nothing. `ST_AsBinary` emits standard WKB with
--     no SRID header, matching what DuckDB's `ST_AsWKB` writes into the Parquet column, so equal
--     digests are the expected result -- though neither library PROMISES byte-identical
--     serialisation, which is why `parity.py` reports a digest mismatch rather than failing on it.
--
--   FROM geo.layers AS layers JOIN geo.features AS features ON features.layer_id = layers.id
--     The join is what lets the layer be selected by NAME here. The sibling parity queries bind a
--     caller-resolved `layer_id` instead; this one does not, because the caller must also be able
--     to ask the separate question "is the layer withdrawn" (`is_public IS FALSE` makes this query
--     return nothing at all, which reads as "the layer is empty" rather than "withdrawn"), and
--     keeping both questions on the layer's name keeps them obviously about the same layer.
--
--   AND layers.is_public IS TRUE
--     The layer gate the export and the tile function both apply. This writer has no `geo.layers`
--     to consult and so cannot see a layer withdrawn from publication; this receipt is the one
--     place that can still observe it, which is why `parity.py` reports the flag separately.
--
--   AND features.status = 'published'
--     Only rows the map itself can show. A draft or superseded row is real but invisible, so
--     counting it would compare Parquet against a population nothing would ever have exported.
--
--   AND features.geom IS NOT NULL
--     A perimeter with no shape has nothing for the digest to compare and nothing a serving reader
--     could draw; the export query rejects it for the same reason.
--
--   ORDER BY unique_fire_identifier
--     A stable, deterministic walk. The caller reduces these rows into a dict, so ordering is not
--     required for correctness -- it keeps a printed receipt or a debugging session reading the
--     perimeters in a reproducible order.
SELECT
    features.properties ->> 'uniqueFireIdentifier'          AS unique_fire_identifier,
    geo.feature_observation_day(features.properties)::text  AS observed_day,
    md5(ST_AsBinary(features.geom))                         AS geometry_md5
FROM geo.layers AS layers
JOIN geo.features AS features
    ON features.layer_id = layers.id
WHERE layers.name = 'fire-perimeters'
  AND layers.is_public IS TRUE
  AND features.status = 'published'
  AND features.geom IS NOT NULL
ORDER BY unique_fire_identifier

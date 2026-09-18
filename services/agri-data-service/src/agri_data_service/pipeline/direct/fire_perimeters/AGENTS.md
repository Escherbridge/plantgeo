# Fire perimeters -- lane notes

The turn's shape, the no-backfill argument and the activation swap are in `pipeline/direct/AGENTS.md`,
"Fire perimeters". This file holds what outgrew a docstring inside the package itself.

## Geometry repair

**The chain in `support.py` is the baseline trigger's, and the earlier refuse-everything reading was
built on a citation that does not exist.** Until 2026-09-15 `support.py` refused the whole snapshot on
the first `ST_IsValid = false` polygon, arguing that `geo_features_sync_geom` "does exactly three
things -- parse, stamp SRID 4326, and REFUSE an invalid shape. There is NO `ST_MakeValid` in that
chain" and citing `drizzle/0001_handy_riptide.sql:151-186`. That file is gone, and the trigger that
actually produced every byte of `geo.features.geom` this layer ever served --
`geo.sync_feature_geom_from_properties`, `drizzle/0000_baseline.sql:106-159` -- does the opposite:

    IF NOT ST_IsValid(parsed) THEN
      repaired := ST_MakeValid(parsed);
      IF GeometryType(repaired) = 'GEOMETRYCOLLECTION' THEN
        repaired := ST_CollectionExtract(repaired, ST_Dimension(parsed) + 1);
      END IF;
      IF repaired IS NULL OR ST_IsEmpty(repaired) OR NOT ST_IsValid(repaired) THEN
        RAISE EXCEPTION ... USING ERRCODE = '22023';
      END IF;
      NEW.properties := jsonb_set(jsonb_set(..., '{geometry}', ST_AsGeoJSON(parsed)::jsonb),
                                  '{geometry_repaired}', 'true'::jsonb);
    END IF;

So PostgreSQL DID hold repaired perimeters, flagged `geometry_repaired: true` -- which is exactly why
`sql/ingest/refresh_features.sql`'s change gate strips that key (`watermark.py` quotes it). Refusing
here published strictly FEWER incidents than the trigger accepted: the inverse of the "silently ADDING
perimeters" overcount the old docstring feared.

**The live feed makes refuse-all a total outage, not a filter.** Probed 2026-09-15 against WFIGS
`_Current` clipped to `-125,42,-111,49`, with DuckDB 1.5.4 spatial: 99 perimeters, **41 invalid**
(2 POLYGON, 39 MULTIPOLYGON); `ST_MakeValid` returned POLYGON 2 / MULTIPOLYGON 39, no
GEOMETRYCOLLECTION, all valid, none empty; planar area change on repair between **-22.6%** and 0.0%.
At a 41% invalid rate the old chain refused 100% of ticks, and the executor latches a lane after three
-- the state `sensors` was found in -- while the `_Current` feed, which "does not retain what it
reported yesterday", kept moving. A refused tick on a mutable current-state feed is a version lost
permanently; there is no archive to re-fetch it from (`__init__.py`, "THERE IS NO BACKFILL MODULE").

**Repair-and-flag is therefore the honest answer for THIS feed, and refuse stays for the unrepairable.**
`support.py::_REPAIR_SQL` is `evacuation_zones/support.py`'s chain keyed by position: a valid shape
passes UNTOUCHED (belt and braces: on DuckDB 1.5.4 `ST_MakeValid` was byte-identical on the two valid
fixture shapes, but a re-noded byte from a future GEOS would read as a content change to
`watermark.py`'s digest, and the branch costs nothing); an invalid one goes through
`ST_CollectionExtract(ST_MakeValid(parsed), 3)`; what is STILL invalid or empty refuses the whole
snapshot by name, exactly as the trigger's final `RAISE` aborted the whole INSERT.
`WRITER_CONTRACT.geometry_defect` stays `refuse_whole_release`, the word every repairing sibling
(`evacuation_zones`, `burn_severity`, `drought`) declares: the policy names the fate of a shape with
no honest repair, not whether a repair is attempted. The CHAIN is `evacuation_zones`'s exactly; it is
strictly stricter than the other two, which check only `is_empty` after repair
(`burn_severity/support.py:147`, `drought/support.py:128`) where this lane and the trigger also check
`is_valid`. More than a fifth of a perimeter's planar area can move in that repair, so the trade is
recorded rather than hidden --
next section.

`ST_CollectionExtract(x, 3)` on a GEOMETRYCOLLECTION yields a MULTIPOLYGON in both engines, one
polygonal part or many, and is a no-op on a bare POLYGON / MULTIPOLYGON; so a repaired POLYGON stays
POLYGON unless the repair genuinely shed non-polygonal debris. There is deliberately no `ST_Multi`:
that is `drought/support.py`'s chain, for `store_drought_area.sql`'s reasons, not this lane's.

## Where the repair flag lives

Every row carries `geometry_repaired: bool` and `geometry_repaired_area_change: float | None`
(`rows.py`, `GEOMETRY_REPAIRED_KEY` / `GEOMETRY_REPAIRED_AREA_CHANGE_KEY`) -- the trigger's
`properties.geometry_repaired` stamp, per row. They are NOT Parquet columns, and
`rows.py::fire_perimeters_table` projects them off onto the registered column names. Three contracts
outside this package would have to move together to make them columns, and none of them is this
package's to edit:

- `warehouse/schemas/fire_perimeters.py` (L1) is the frozen stream schema; `objectstore.write_partition`
  conforms every table to it by `select(column_names)`.
- `src/lib/server/services/parquet-trpc-readers.ts::firePerimeterRowSchema` is `.strict()` over exactly
  those columns, by design ("a column added upstream therefore fails these readers loudly").
- `watermark.py::DIGESTED_COLUMNS` is derived from the schema, so a new column would make
  `table_content_digest` refuse every version already published under sixteen columns as "written under
  a different schema", and `_encode` refuses booleans outright -- the first turn after such a schema
  change would fail the way the invalid-geometry turns did.

Until an owner lands that three-way change, the audit trail is the turn itself: the
`fire_perimeters_forward_fetched` event carries `geometry_repaired` (a count), the
`fire_perimeters_forward_geometry_repaired` stderr event names every repaired incident with its area
ratio, and the terminal report carries `perimeters_repaired` and `geometry_repairs`. The Parquet a
reader sees is identical in shape to what the trigger-fed exporter wrote.

## The area-change number

`(ST_Area(repaired) - ST_Area(parsed)) / ST_Area(parsed)`, planar, in the feed's own EPSG:4326
degrees. It is a ratio of two areas in the same units, so the unit cancels and no geodesic function is
needed -- and none may be called here (`ST_Distance_Sphere`'s argument-order trap is documented in
`support.py`). It is NULL for an untouched shape and for a repaired one whose original's SIGNED planar
area is not positive: zero for a self-intersecting bowtie, NEGATIVE when DuckDB's shell-minus-holes
sum goes below zero (a hole drawn larger than its shell probed at -99.0). Negative USUALLY means
overlapping or self-intersecting parts were unioned, not that ground was lost: two overlapping 2x2
squares probe at -25% with nothing dropped, because the invalid input double-counted the overlap, and
Skull (25 -> 22 parts) and Wolf Creek (5 -> 4) are consistent with parts being merged. The near-zero
values on some repaired POLYGONs are floating-point noise from re-noding one vertex, not growth. It is
an audit number, not a measurement of the fire.

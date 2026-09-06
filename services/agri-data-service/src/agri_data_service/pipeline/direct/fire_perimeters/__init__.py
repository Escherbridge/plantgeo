"""Direct-to-Parquet WFIGS fire-perimeter writer: source, geometry, watermark, forward publication.

This package is what makes `ingest-fire-perimeters`, `ingest/wfigs.py`'s Postgres write path and the
`postgres-fire-perimeters` executor lane DELETABLE. Until it exists, deleting them stops the layer
rather than finishing its cutover, which is why the prior lane deleted five siblings' ingestion and
left this one standing.

THIS LANE IS A `static_lookup`, NOT A DAY SERIES, and every module here is shaped by that. Its
partition day is a VERSION STAMP driven by a source watermark
(`sql/pipeline/lane_watermark_fire_perimeters.sql`, reproduced source-side in `watermark.py`), so
there is no day loop: one turn publishes at most ONE version, and publishes none at all when the
source has not changed. See `warehouse/schemas/fire_perimeters.py` and
`pipeline/parquet/lane_registry.py`'s `FIRE_PERIMETERS_STREAM` registration for the 2026-09-04
re-registration this follows.

THERE IS NO BACKFILL MODULE, AND THERE CANNOT BE ONE. The sibling packages ship `backfill.py`
because their upstreams keep a dated archive: USDM publishes one map per Tuesday forever, ERA5-Land
answers for any past day. WFIGS publishes `_Current` -- "a live mutable snapshot, not a versioned
release", which "does not retain what it reported yesterday" (`docs/lanes/fire-perimeters.md`
section 6, quoted in this lane's own `floor_basis`). There is no past version to re-fetch, so a
backfill here could only re-stamp TODAY's population under a past day, manufacturing a version that
never existed -- the exact fabrication `_static_lane_census` refuses when it declines to re-export a
stranded old version. `geo.features` cannot supply one either: it refreshes one row per incident in
place and keeps no past state, and `geo.geometry`'s Type-2 chain is not a substitute (only 6 of
thousands of dimension entries across every producer ever reached a second WFIGS version, and its
forward path has a known silent-freeze failure mode). The history this lane has is the 45 partition
days the retired `daily_series` shape already wrote; nothing here adds to it and nothing can.

Modules, in dependency order:

* `products.py`  -- the lane's identity, kind and tier ladder, read from the registry.
* `source.py`    -- one bounded WFIGS `_Current` walk, reusing `ingest/wfigs.py`'s fetch and parse.
* `support.py`   -- the DuckDB spatial session and the GeoJSON-to-WKB conversion that reproduces
                    `geo.sync_feature_geom_from_properties`.
* `rows.py`      -- conform one fetched population to `FIRE_PERIMETERS_SCHEMA`.
* `watermark.py` -- reproduce the Postgres watermark's semantics against the object store.
* `adapter.py`   -- write one version's base rung under the caller's lane-day lock.
* `forward.py`   -- one bounded turn: fetch, resolve, publish at most one version, report.
* `parity.py`    -- the read-only counted receipt against what PostgreSQL still holds.
"""

from __future__ import annotations

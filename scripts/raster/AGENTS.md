# `scripts/raster/` — first-party raster acquisition and publication

Turns upstream raster products into archives PlantGeo serves itself. Code here carries one-line
doc-comments; the reasoning, the measured costs and the traps live in this file.

Today's live catalog carries one product: **ISRIC SoilGrids v2.0 topsoil (0–5 cm), six
properties, clipped to the PNW.** The scripts themselves are not limited to that: `--depths`
(default all three: 0-5, 5-15, 15-30 cm, or `all` to spell that out) and `--properties` (default
all ten: phh2o, soc, nitrogen, bdod, cec, ocd, clay, sand, silt, cfvo) build any subset of the 30
property-depths, so a release expansion is a rerun, not a rewrite. See
`.omc/soil-data-plane-20260927/DESIGN.md` §3.

`build-soil-cogs.py --list` prints the planned property-depths and exits -- no network, no files
written -- so a release-expansion run can be sanity-checked before it downloads anything.
`--only-new` skips the six property-depths already live at 0-5cm (phh2o, soc, nitrogen, bdod, cec,
ocd), so `--depths all --only-new` builds exactly the 24 new WS-B products and never re-touches
the six that are already published. `--capture-dir <dir>` reads a WS-A `soil_properties capture`
capture manifest (CONTRACT C8) instead of downloading from ISRIC: the manifest's own pinned
Last-Modified/ETag per file is cross-checked against its `.receipt.json` sidecar, and the file's
sha256 is re-verified, before anything is read, so a drifted or corrupt capture refuses rather
than silently building from the wrong bytes. Omitting `--capture-dir` keeps the original
`/vsicurl` download path exactly as it was.

## Why bulk COGs and not the REST API

The app already reads SoilGrids per point through `src/lib/server/services/soilgrids.ts`, and
`scripts/warm-soilgrids.mjs` has filled `public.soil_grid_cache` with the whole
`sentinel2-ndvi-0p25deg` lattice — 1,573 cells, 1,442 of them with a SoilGrids value.

That cache cannot become a raster. Its lattice step is 0.25° (~28 km) against SoilGrids' native
250 m, and ISRIC's REST endpoint refuses sustained traffic faster than roughly one request per
20 s (measured 2026-08-07; see the pacing constants in `warm-soilgrids.mjs`). Densifying the
cache to native resolution over the PNW is ~15 M points, which at that pacing is not a schedule.

So the granular data comes from the published VRTs and the point cache is repurposed as the
**validation set** — 1,437 readings that came down a different pipe, used to prove the clip
describes the same ground the point query does (verification now lives in the
`soil_properties verify` verb, `.omc/soil-data-plane-20260927/DESIGN.md` §2.8, not a script here;
`verify-soil-cogs.py` was deleted in `fd602901`).

## The two scripts

| script | does |
| --- | --- |
| `build-soil-cogs.py` | windowed read of the ISRIC VRTs over `/vsicurl`, reproject to EPSG:4326, write COGs + `manifest.json` |
| `publish-soil-rasters.py` | derive the colour ramp, upload to R2, register a row in `geo.raster_release`; `build-soil-tiles.py` then cuts PMTiles from the published COGs |

Run order is build → publish → tiles. Publish derives the ramp, uploads, then registers, in
that order, so a failed upload leaves no catalog row and a row always describes bytes that exist.
`--dry-run --manifest-out <path>` on publish derives ramps and writes them locally without
touching R2 or Postgres; `build-soil-tiles.py --releases-file <path>` reads that file back, so
tiles can be cut for a release that has not been registered in the catalog yet.

Dependencies are not vendored; every script is run through `uv` with the packages named inline,
e.g. `uv run --with rasterio --with boto3 --with psycopg2-binary --no-project python …`. There is
no GDAL, `pmtiles`, `tippecanoe` or `aws` binary on the build machine and none is required.

## Measured, 2026-08-10

- Whole build: **4 m 53 s** for six properties, 37–58 s each, over a residential connection.
- Clip is 4,943 × 3,027 px per property in the **source CRS** — the pre-warp window
  `read_bbox_window` returns before `warp_to_wgs84` reprojects it, not the final COG's own
  width/height (that varies per depth/property with the reprojected pixel grid). Each pre-warp
  window is **78.5 %** valid pixels (the remainder is Pacific). The build log says "valid", never
  "measured": a SoilGrids pixel is a model estimate.
- Archive sizes: 4.3 MB (phh2o) to 16.0 MB (soc); **~64 MB** for the set.
- Verification: agreement 97.6–99.9 %, r = 0.994–0.997, permutation null |r| ≤ 0.054 (historical,
  from the deleted `verify-soil-cogs.py`; the live gates are DESIGN.md §2.8 G-V1..G-V5).

## §proj-collision — a global `PROJ_LIB` breaks every CRS lookup

This machine sets `PROJ_LIB=C:\Program Files\PostgreSQL\17\share\contrib\postgis-3.5\proj`
globally. That `proj.db` predates the layout GDAL 3.12 requires, so *every* CRS operation fails
with `DATABASE.LAYOUT.VERSION.MINOR = 2 whereas a number >= 6 is expected` — including
`CRS.from_epsg(4326)`. It is not a rasterio bug and reinstalling does not help.

Every script here repoints `PROJ_LIB`/`PROJ_DATA` at rasterio's bundled copy before rasterio is
imported, locating it with `importlib.util.find_spec` precisely because `find_spec` does **not**
execute the module — PROJ initialises once, on first use, so a fix applied after `import
rasterio` is already too late.

## §homolosine — SoilGrids is not in a lon/lat CRS

The published VRTs are Interrupted Goode Homolosine (`+proj=igh`), 159,246 × 58,034 at 250 m.
A lon/lat window against them does not fail — it reads a different part of the world and returns
entirely plausible soil values. `read_bbox_window` reprojects the bbox into the source CRS first
(`densify_pts=64`, because the projection curves the box edges).

Two consequences worth knowing:

- The 4326 COG's bounds **overhang the requested bbox** — asking for `-125,42,-111,49` yields a
  west edge of −127.04, because the reprojected Homolosine window is not a lon/lat rectangle.
  Extra coverage, never missing coverage.
- This is exactly the failure a tolerance-based check cannot catch, which is why
  `verify-soil-cogs.py` carries a permutation null instead of only a tolerance.

## §verification — moved to the `soil_properties` pipeline

Verification against the REST point cache (agreement, a permutation null, a locality check) used
to live in `verify-soil-cogs.py`; that script is deleted (`fd602901`). The same three-statistic
reasoning now runs as the `verify` verb's G-V1/G-V3 gates in
`pipeline/direct/soil_properties/` — see `.omc/soil-data-plane-20260927/DESIGN.md` §2.8 for the
gate table (G-V1..G-V5) and why a tolerance alone cannot distinguish "correct" from "smooth". That
verb also checks the COGs built here directly (`--cogs <dir>`: value at pixel centre vs the
capture, tolerance for bilinear smoothing).

## §watermark — a per-file date split is not a refusal

`build-soil-cogs.py::probe_source_metadata` HEADs every VRT it touches (built or skipped) and
records `sourceLastModified` / `sourceEtag` in `manifest.json`, per property-depth. Three `ocd`
VRTs are dated 2020-05-26 against 2020-06-02 for the other 27; `release_watermark` takes the MAX
Last-Modified across all of them rather than refusing on the split — the release's watermark is
one instant, not a promise that every file shares it. This mirrors the `soil-properties` Parquet
lane's own drift rule (DESIGN.md §2.1): a file's date/ETag drifting from what was captured is what
matters, not files disagreeing with each other on day one.

## §catalog — what a release row means

`geo.raster_release` (migration `0024`) is append-only. A row asserts that a specific set of
bytes, with a known checksum and licence, is being served. Re-publishing stamps `superseded_at`
and inserts; nothing is updated in place. Read through `geo.published_raster`, never the table,
so no call site can forget the `superseded_at IS NULL` predicate.

Live-uniqueness is keyed on `(collection, property, depth, statistic, archive_format)` —
**format included** — because one property is published twice over: the COG is the archival
artifact anyone can download and compute against, the tile archive is what the map draws. They
supersede independently.

`color_ramp` lives on the row rather than in TypeScript. The archive is immutable, so the ramp it
was painted with is a property of the release; a legend restated in the client can drift from the
pixels, and a legend that lies is worse than none. Ramps are fitted to **quantiles** of the actual
PNW distribution, not to the min–max range — `soc` spans 5.7–462 g/kg but sits under 60 for most
of the region, so a linear stretch renders the entire map one flat colour.

## §env — R2 credentials are in `.env`, not `.env.local`

`.env.local` declares `R2_ENDPOINT`, `R2_ACCESS_KEY_ID` and `R2_SECRET_ACCESS_KEY` **with empty
values**; the real ones are in `.env`. An env reader that returns on first *match* rather than
first non-empty *value* silently loses them and fails at the upload. `read_environment` in
`publish-soil-rasters.py` treats an empty assignment as absent and keeps looking.

## §tiles — the pyramid

`build-soil-tiles.py` cuts z0–10 and packs each property as a PMTiles archive. 1,696 tiles per
property (350 more are wholly nodata and are skipped, not written empty), ~23 s each.

Two things in there are load-bearing and non-obvious:

- The `WarpedVRT` is defined over the **whole mercator square on the XYZ pyramid grid**
  (`TILE_SIZE * 2**MAX_ZOOM` = 262,144 px square, virtual). Every tile at every zoom is then an
  exact integer window fully inside the dataset. This exists because `WarpedVRT` **refuses
  boundless reads** — a VRT sized to the data would need every edge tile hand-pasted into a
  nodata canvas.
- Tiles must be written in **ascending tile id**, and the render loop walks zoom-major, which is
  not that order. The `sorted()` before `write_tile` is correctness, not tidiness.

Tiles are **paletted**, not truecolour: the ramp is baked into a 256-entry PNG palette (index 0
transparent for nodata, 1–255 the ramp sampled across its span). This cut the six archives from
285 MB to 174 MB and build time from 379 s to 137 s. The piecewise quantile spacing survives
because palette entry *i* is the ramp evaluated at the value it stands for. A truecolour cut is
not 4–6× larger as one might guess — PNG's filters already exploit a smooth gradient — so the
win is ~39%, worth taking but not dramatic.

## Application serving path

Both formats are published and catalogued (6 COG + 6 PMTiles live in `geo.published_raster`).
`environmental.getPublishedSoilRasters` reads the live PMTiles rows through
`src/lib/server/services/raster-catalog.ts`; that service validates each object key and joins it
to `RASTER_TILES_BASE_URL`. `LayerManager` threads those releases into one
`pmtiles://` raster source per property. The six property rows are independent switches with
independent opacity, and their legends are built from each release's own `color_ramp`.

SSURGO remains a separate drainage/map-unit survey. It must not be presented as the source of
the six continuous SoilGrids property rasters or folded into their toggles.

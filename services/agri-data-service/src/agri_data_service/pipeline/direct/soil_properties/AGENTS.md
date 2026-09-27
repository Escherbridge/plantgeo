# ISRIC SoilGrids v2.0 soil properties

Design of record: `.omc/soil-data-plane-20260927/DESIGN.md` (rev 2) and `CONTRACT.md` (C1, C8,
C10, C11).

## Status

**Registration shell (WS-A A0).** The schema, derivation, release identity, pins, watermark,
parser and writer contract are final. Every operator verb raises
`SoilPropertiesOperationNotBuiltError` until A1 fills `forward.py::OPERATION_HANDLERS`. The lane
is registered but never written, the same state `soil-survey` was in before its capture.

## Source, rights and what the numbers are

- **Source**: ISRIC SoilGrids v2.0 mean predictions, one VRT per property and depth under
  `https://files.isric.org/soilgrids/latest/data/<p>/<p>_<depth>_mean.vrt`.
- **Rights**: CC-BY 4.0. Cite `products.py::SOURCE_CITATION`.
- **What a value is**: SoilGrids is a 250 m machine-learning **model estimate**. A value is never
  a measurement and never a soil sample at a point. Every label, prompt, test and doc says so.
  The label is "SoilGrids v2.0 250 m model estimate, <depth>".
- **Scope**: ten properties (`phh2o soc nitrogen bdod cec ocd clay sand silt cfvo`) at three
  depths (`0-5cm`, `5-15cm`, `15-30cm`), so thirty files.
- **Deferred**: SSURGO/gNATSGO survey values. The `soil-survey` lane is SSURGO polygons and
  `soil-field-*` is ERA5-Land moisture (`pipeline/direct/soil/`). Neither is this lane.

## Watermark and drift

- **Nature**: `static_lookup`, lag 0, no forecaster and no writer ceiling. The partition day is a
  VERSION STAMP, not an observation day.
- **Watermark**: `watermark.py::read_soil_properties_source_watermark` is pure. It returns the MAX
  Last-Modified instant over the thirty pins in `products.py::SOURCE_FILE_PINS`, which is
  `sand_15-30cm` at 2020-06-02T16:14:20Z, so the day is **2020-06-02**. The three `ocd_*` files
  are dated 2020-05-26, which is why this is a max over files and not one shared day.
- **Why pure**: `pipeline/parquet/lane_registry.py` imports `watermark.py`. So `watermark.py` and
  `products.py` import only `foundation`, `warehouse` and this package's leaves. Never import
  `forward.py`, `gap_fill` or anything that reads `LANE_REGISTRY` from them: that closes the
  cycle the registry's module docstring describes. `tests/direct/soil_properties/
  test_registration_shell.py` pins this rule with an AST scan.
- **Drift**: a file drifts when its Last-Modified or ETag differs from its pin, or when a file
  appears or disappears. `capture` refuses on drift. `maintain` re-probes the thirty HEADs and
  reports drift as "new ISRIC release suspected; republish". Drift is never an owed day, and the
  gap-repair command excludes this lane (`execution/gap_repair_contract.py::REPAIR_EXCLUSIONS`).
- **Pins**: taken from a live HEAD on 2026-09-27 (P0.1). `content_length` is recorded as
  evidence; the drift rule reads only Last-Modified and ETag.
- **Release id**: `soilgrids-v2.0/2020-06-02`. The partition is
  `layer=soil-properties/kind=observed/zoom=NN/year=2020/month=06/day=02/`.
- **Readers**: they ask `as_of = server today`, never a UI day. Soil is a static reference and must
  not vanish when the map's slider says 2018.

## Lattice, keys and sampling

- **Lattice**: 0.005 deg ORIGINS aligned to (-125, 42). That is 2,800 columns by 1,400 rows,
  3,920,000 cells, over (-125, 42) to (-111, 49).
  - It nests exactly into z9 (x2), z5 (x40) and z0 (x1,000).
  - It is the same lattice as `fire-detections`, so the two join 1:1 on the key.
- **Pinned, not read from `foundation/region`**: the lattice is the lane's SUPPORT. Binding it to
  a mutable region manifest would silently change which cells a release covers. For the same
  reason the writer takes no `--bbox` (`WRITER_CONTRACT.unconfigured_bbox = not_bbox_bounded`).
- **Keys**: `cell_longitude` and `cell_latitude` hold the south-west origin. The centre is
  origin + (0.0025, 0.0025). Distance math uses the centre and keys use the origin (CONTRACT C2,
  C11). There is no `geometry_wkb`.
- **Sampling**: nearest native Homolosine pixel containing the cell centre, never an average. So
  every stored number is one ISRIC published, and verification is an exact-match test.

## Columns and the row rule

- **Values**: thirty `<p>_<t>_<b>cm` columns (for example `clay_15_30cm`), float64, non-null,
  holding ISRIC's MAPPED integer (`products.py::PROPERTY_UNITS` gives the divisor to physical
  units).
- **Why float64 and not int16**:
  - every rung shares one arrow schema;
  - a coarse `mean` returns a double, and `conform_to_stream_schema`'s safe cast refuses a
    fraction into an int;
  - the recon measured float64 at the same compressed bytes as int16 on this data.
- **Integer contract at z13**: readers assert each value is integral (|v - round(v)| < 1e-9)
  before converting to int. A fractional base value means a corrupt lane (`read_failed`) and is
  never rounded silently.
- **Constants**: `source_release`, `source_manifest_sha256` and `release_day`.
- **Row rule**: a row exists only where all thirty values are present. Every coarse mean is then
  over one well-defined cell set.
- **Sort**: `cell_latitude`, then `cell_longitude`. Latitude-major lets row-group statistics
  prune a point read.

## Rungs

- `warehouse/schemas/soil_properties.py::SOIL_PROPERTIES_DERIVATION` is a `GridAggregation`
  with `key_columns=()`: thirty `mean`s plus three `first`s.
- Every rung derives from the base, so a coarse value is the exact mean of its valid base cells
  and never a mean of means.
- **Unbanded**: at most 3.92M base rows is below `MAX_DERIVATION_ROWS` (5,000,000) by
  construction.
- **Overflow**: no column is summed, so nothing can overflow.
- **Coarse label**: "mean of SoilGrids v2.0 estimates over a <pitch> deg cell". No consumer in
  this build reads the coarse rungs; they exist because publication requires all four.

## Verbs (built by A1)

- `capture`: HEAD the thirty VRTs and refuse on drift. Then read each Homolosine window (region
  plus a 2 km margin) and write one deflate GeoTIFF per file plus `capture-manifest.json`
  (CONTRACT C8). It is resumable per file.
- `prepare`: nearest warp onto the centre grid, apply the row rule, add origins and constants, and
  sort.
- `publish`: write base parts at `--rows-per-part`, derive the rungs, and write completion markers
  last. It runs under the lane-day advisory lock, then archives the capture immutably.
- `verify`: gates G-V1 to G-V5 (DESIGN section 2.8).
- `maintain`: re-probe the pins and report `StaticLaneState` plus any drift.
- `retract`: the rollback. It is a dry run by default; `--confirm soilgrids-v2.0/2020-06-02` acts.
  It clears the markers first, then z0, z5, z9 and z13.

## Writer contract (CONTRACT C10)

- **Defects**: `identity_defect` and `geometry_defect` are both `no_such_defect`, because a
  raster cell has no record identity and no geometry.
- **Exposed flags**:
  - `--max-days` (must be 1, since one release is indivisible) and `--run-id`;
  - `--time-budget-seconds` and the three `--retry-*` flags, which the network-bound capture uses.
- **Excused with a reason**: `--bbox`, `--product`, `--contention-timeout-seconds`,
  `--max-records` and `--max-records-per-day`.
- **Outcomes**: the five lane-day words plus `idempotent_noop`.

## Run environment

- Run local Python with `PROJ_LIB`, `PROJ_DATA` and `GDAL_DATA` unset (the `scripts/raster/AGENTS.md`
  PROJ trap).
- `publish` reads `OBJECT_STORE_*`, and it reads the production DSN only for the advisory lock.
  Never print either.
- No cron, no lane spec and no TOML: this is a one-off operator publication.

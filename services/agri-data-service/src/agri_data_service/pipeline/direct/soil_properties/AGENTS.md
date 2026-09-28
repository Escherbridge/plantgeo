# ISRIC SoilGrids v2.0 soil properties

Design of record: `.omc/soil-data-plane-20260927/DESIGN.md` (rev 2) and `CONTRACT.md` (C1, C8,
C10, C11).

## Status

**Built (WS-A A1), not yet run.** All six verbs are wired in `forward.py::OPERATION_HANDLERS`. The
lane is registered and still never written until the operator's P1 (capture, prepare), P3
(publish) and P4 (verify). Reads stay behind `SOIL_PROPERTIES_READS_ENABLED` in both services.

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

## Verbs

`python -m agri_data_service.pipeline.direct.soil_properties <verb> [flags]`; each prints one JSON
report. Only `publish` reports an `outcome` word (the writer-contract vocabulary); the other verbs
report `status`, so no undeclared outcome word is ever emitted.

### Capture (`capture.py`)
- HEAD all thirty VRTs first (`source.py::probe_pins`, retried with the `--retry-*` backoff) and
  refuse on ANY drift (`SoilPropertiesDriftError`, code `source_drift`). Then read each native
  Homolosine window over `/vsicurl` and write `<p>_<depth>_mean_homolosine.tif` (deflate,
  predictor 2, 512 tiles) plus a per-file `.receipt.json`.
- **Window**: the pinned envelope plus a 2 km margin, sized at the northern edge (the widest degree
  margin), transformed with 64 densify points, rounded OUTWARD to whole native pixels and clamped.
  All thirty VRTs share one 250 m lattice, so the thirty CAPTURED GeoTIFFs must have an identical
  CRS, six-term transform and shape (`capture.py::shared_grid`) or capture refuses. The comparison is
  never on `col_off`/`row_off`: those index into each VRT, and ISRIC's bdod and soc VRTs start 750 m
  (three pixels) west of the other 24, so one geographic window has two pixel offsets (found in P1,
  2026-09-28). The manifest `window` records that shared grid; each receipt keeps its own offsets.
- **Resumable per file**: a GeoTIFF is reused only when its receipt's ETag and Last-Modified equal
  the pin AND its bytes still hash to the receipt. `--time-budget-seconds` stops before starting a
  new file and reports `status: incomplete`; rerun the same command to resume.
- **Manifest** (`capture-manifest.json`, C8): closed only when all thirty files exist;
  `manifest_sha256` is sha256 over its own canonical JSON (C5.5 rules) without that field.
  `read_capture_manifest` re-proves the digest, the pins and every file's bytes before any later
  verb trusts the capture.

### Prepare (`prepare.py`)
- **The documented source is the capture, not the local 4326 COGs.** `data/raster/soil/*_4326.tif`
  were warped with `Resampling.bilinear` (`scripts/raster/build-soil-cogs.py::warp_to_wgs84`), so
  their values are interpolations ISRIC never published; sampling them would break the "each stored
  number is an ISRIC value" rule, the exact-match gates G-V1/G-V2 and the integer contract. They
  are used only by `verify --cogs`.
- Each captured window is reprojected with `Resampling.nearest` onto the 2,800 x 1,400 lattice,
  whose pixel (column, row) IS the cell keyed by its SW origin; nearest samples each destination
  pixel at its centre, so a cell takes the native pixel containing its centre. `tolerance=0`
  forces GDAL's exact transformer: the default approximate one (0.125 px) can pick the neighbouring
  native pixel near an edge.
- **Row rule**: a row exists only where all thirty samples are valid (a nodata or off-window cell
  holds `MISSING_VALUE`, outside int16). Origins are exact thousandths:
  `(-125000 + 5c) / 1000`, `(49000 - 5(r + 1)) / 1000`. Sort: latitude, then longitude.
- Writes `prepared/soil-properties-z13.parquet` and `prepared/prepare-report.json` (rows, valid
  fraction, per-column mapped min/max, wall time, and the Boise/Pullman/Corvallis 1,000 m probe
  that DESIGN 2.6 asks P1 to re-check). No database or object-store access. Peak RSS is not
  measured in-process; the operator records it (DESIGN P1).

### Publish (`publish.py`)
- Requires `--capture-dir` and `--rows-per-part` (no default: P1b's measurement picks it; bounds
  10,000..4,000,000). Re-proves the manifest, then refuses a prepared table whose columns, manifest
  sha, release day or row count do not match, or whose values are not integral.
- **Idempotent**: with all four markers present, the base marker counting exactly these rows and
  parts, AND the published base built from this capture manifest, it reports `idempotent_noop` unless
  `--force`. The manifest check (review m2) reads only the first base part's `source_manifest_sha256`
  (`published_manifest_sha256`), and only once the counts already match: a different capture with
  equal counts is a republish, never a no-op. The markers carry no manifest hash, so the rows are
  the only place to read it.
- **Archive first**: every captured window, then `capture-manifest.json` LAST, to
  `layer=soil-properties/kind=observed/availability/source-captures/<manifest-sha>/` via
  `put_immutable` (exact replays are accepted, different bytes refuse).
- **Write**: `fill_one_lane_day` with the registry lane's adapter replaced by
  `SoilPropertiesAdapter` (contiguous `--rows-per-part` slices of the sorted table, so each part's
  latitude statistics prune a point read) and `derive_tiers=derive_and_write_day_tiers(base_table=
  <the same table>)`, so every coarse rung is the exact mean of its base cells without a read-back.
  The lane-day lock is `postgres_lane_day_lock` (Postgres is control plane only); the static-lane
  path brackets the export with two reads of the pure watermark. Completion markers land last.
- **No availability-index extension** (`extend_availability=False`), as `land_context` publishes:
  a `static_lookup` lane is served from its physical listing and stays on the census, so there is
  no index to extend and no publication barrier to take. `publish` then proves all four markers and
  the base row count, and writes `publish-report.json`.

### Verify (`verify.py`, P4)
`verify --capture-dir <dir> --reference-archive <local path | object key>` reads the PUBLISHED z13
and the capture, writes `verification-soilgrids-v2.0.json` beside the manifest and archives a
timestamped copy under the capture's archive prefix. Gates (DESIGN 2.8):

| gate | check | pass |
|---|---|---|
| G-V1 (primary) | `soil_grid_cache` 0-5 cm REST readings vs the captured native pixel containing each point | >= 99% exact per property; every mismatch in the 3 x 3 native neighbourhood |
| G-V2 | lane vs captured pixel at 10,000 sampled centres (seed 20200602), all thirty columns | 100% |
| G-V3 | Pearson r, lane cell containing each cached point vs the cached value | >= 0.95 per property |
| G-V4 | ISRIC REST `properties/query` at 30 latitude-stratified centres, one call per point, 12 s apart | >= 98% exact per column, mismatches in the 3 x 3; an outage, an unexpected answer shape or all-null means is `deferred`, never `fail`; a column REST never answered defers the gate unless another column failed; `--rest-points 0` is `not_run` |
| G-V5 | sand + silt + clay in 950-1050 g/kg for >= 99% of rows per depth; physical plausibility | as stated |

- `status: pass` needs G-V1, G-V2, G-V3, G-V5 pass and G-V4 pass-or-deferred; a deferred G-V4 is
  re-run within 7 days. A G-V4 that never ran (`not_run`, `--rest-points 0`) makes the status
  `incomplete`, never `pass` (review m1): P5 may not proceed on it.
- **Reference archive**: a tar(.gz) preserve set or one extracted file. The member is found by the
  name `soil_grid_cache` (or by that name inside a plain-SQL dump's `COPY` block); CSV, TSV, JSON,
  JSONL and plain-SQL COPY parse. A pg_dump CUSTOM archive refuses with a request to extract CSV
  (P0.4). `--reference-units physical` (default; docs/schema.dbml: pH, g/kg, kg/dm3, cmol(c)/kg)
  converts by decimal round-half-up of `physical x divisor`; `mapped` compares as stored.
- `verify --cogs <dir>` (P2) checks the WS-B 4326 COGs only: a bilinear pixel must lie within the
  min..max of the captured 3 x 3 neighbourhood at its centre (>= 99% of 2,000 samples per COG). It
  needs no lane and no reference, and writes a local `verification-cogs-<stamp>.json`.
- Captured windows are opened lazily, two at a time, so thirty never sit in memory at once.

### Maintain (`maintain.py`) and Retract (`retract.py`)
- `maintain` HEADs the thirty pins, reads the four markers, and reports `resolve_static_lane`'s
  verdict (`watermark.py::pinned_source_watermark`) plus `status: pinned | drift_suspected`. It
  writes nothing.
- `retract` is a dry run (the per-rung plan) unless `--confirm soilgrids-v2.0/2020-06-02`. Confirmed,
  under the lane-day lock, it clears ALL FOUR markers first, then empties z0, z5, z9 and z13 in that
  order (`ObjectStore.retract_partition_tier`); any delete failure is fatal and named. The capture
  archive is kept. Readers return `lane_never_written`.

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

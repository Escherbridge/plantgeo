# `pipeline/lanes/_shared/` — the lane library

Shared, lane-agnostic code extracted from the legacy lane implementations so every config-lane
strategy reuses one copy instead of restating it. Sanctioned by `pipeline/lanes/AGENTS.md`.

## The rule

- **Any lane may import `_shared`; `_shared` imports no lane** (only `foundation`, the standard
  library and third-party packages). `_shared` never imports `pipeline/direct/**` or any runner
  module.
- **No lane-specific logic inside.** A helper here returns a neutral answer; the lane decides what
  it means. Concretely, these stay at the call site and are passed in or applied afterwards:
  resource caps (memory, threads, temp-dir size), refusal policy (empty / invalid / key-set checks),
  exception types and messages, field exclusions from a digest (`updated_at`, `ingested_at`,
  `snapshot_day`, ...), and provider-specific error mapping.
- **A change here changes every lane at once.** Every byte a helper emits is published by some lane
  and compared at G6; a "harmless" normalisation here is a published-data change everywhere.

## Geometry (`geometry.py`)

`spatial_session(memory_limit=, threads=, max_temp_directory_size=)` opens the `:memory:`,
single-use, spill-guarded DuckDB session; `load_spatial` points DuckDB at the image's extension
directory first (the runtime user's home is `/nonexistent`). `convert_geojson_to_wkb(session,
geometries, mode=)` converts a whole population in ONE round trip, ordered on a carried `position`
column (never registration order) and positionally aligned with its input.

### Why three named chain modes, not one

The legacy lanes run three genuinely different chains, and each one's bytes are what that lane has
already published. The extraction moved code, not decisions -- each chain is a mode, byte-for-byte:

| mode | SQL geometry expression | lanes | origin |
| --- | --- | --- | --- |
| `bare` | `ST_GeomFromGeoJSON(geojson)` | watersheds | legacy `ST_SetSRID(ST_GeomFromGeoJSON(...))`, no repair |
| `always_multi` | `ST_Multi(ST_CollectionExtract(ST_MakeValid(...), 3))` | drought, burn_severity | `store_drought_area.sql` / MTBS PostGIS chain |
| `pass_through_flagged` | `CASE WHEN ST_IsValid(parsed) THEN parsed ELSE ST_CollectionExtract(ST_MakeValid(parsed), 3) END` | evacuation_zones, fire_perimeters | `geo.sync_feature_geom_from_properties` trigger |

Only `pass_through_flagged` computes `is_valid`, `was_repaired` and `area_change`; the other modes
answer NULL for them (the legacy SQL never computed them, and computing `ST_IsValid` over 9,396
basins is cost nobody asked for). evacuation_zones reads `is_valid`/`is_empty` and ignores the flags;
fire_perimeters surfaces the flags per row. The repaired geometry is the same expression either way,
so the two lanes' WKB stays identical to their legacy writers'.

**Open decision, NOT made here:** whether burn-severity's missing pass-through/flagging is a bug
(`fire_perimeters/support.py` docstring). It belongs to 7S with the legacy packages' full history in
view. Never "fix" it by switching a lane's mode: that rewrites published geometry bytes.

Behaviour notes vs. the five restatements this replaced (none changes a produced WKB byte):

- Refusal now names the FIRST offending input in input order, not in key order.
- A duplicate key that a lane collapses into a dict (drought's `dm_category`) now resolves to the
  LAST input position deterministically, rather than to whichever tie `ORDER BY key` returned.
- A NULL conversion result raises `SpatialConversionError`; the old code reached `bytes(None)`.
- Count/order misalignment raises `SpatialConversionError`; fire_perimeters maps it (and
  `duckdb.Error`) to `FirePerimeterGeometryError`, the others let it propagate.

## Digests (`digest.py`)

`canonical_json_text` / `canonical_json_bytes` are `json.dumps` with the digest-relevant kwargs
(`sort_keys`, `separators`, `allow_nan`) **required**, so no call site inherits a default by
accident; `default=` passes through. `sha256_hex` hashes exact bytes; `sha256_hex_of_lines` is the
per-row variant (`"\n".join(lines)`, UTF-8, no trailing newline). `ensure_ascii` is never exposed:
every call site used the default (`True`), which also makes UTF-8 vs `.encode()` identical.

**Never normalise kwargs across call sites.** The lanes pass different combinations on purpose (or by
history), and each combination is frozen into published digests:

| call site | sort_keys | separators | allow_nan | default |
| --- | --- | --- | --- | --- |
| `land_context/source.py::canonical_bytes` | True | compact | False | - |
| `crop_cover/source.py::canonical_bytes` | True | compact | True | - |
| `watersheds/nhdplus.py` geometry fingerprint | True | compact | True | - |
| `watersheds/nhdplus.py` outer fact list | False | compact | True | - |
| `drought/usdm_releases.py::_source_digest` | True | compact | True | - |
| `burn_severity/mtbs_releases.py` fetch body | True | compact | False | - |
| `evacuation_zones/oregon_oem.py::_descriptor_bytes` | True | DEFAULT | True | - |
| `fire_perimeters/wfigs.py::content_digest` (per row, joined) | True | DEFAULT | True | `str` |
| `fire_perimeters/wfigs.py` fetch body | True | DEFAULT | True | - |
| `water_gauges/usgs_water_data.py::day_source_digest` (per row, joined) | False | compact | True | - |

`DEFAULT_SEPARATORS` is `json.dumps`'s own `(", ", ": ")`, named so the sites that relied on it still
say so. Not migrated, deliberately: `evacuation_zones/rows.py`'s `content_digest`/`row_content_digests`
(a streaming type-length-value encoding over Arrow cells, not JSON) and the non-digest `json.dumps`
used for conflict detection in `water_gauges`.

## ArcGIS envelope (`arcgis.py`)

`arcgis_envelope_fault(parsed)` returns `NOT_AN_OBJECT`, `SERVICE_ERROR` (`"error"` key PRESENT,
not truthy) or `TRUNCATED` (`exceededTransferLimit` truthy), or None. JSON decoding stays at the
call site because the callers differ (`land_context/source.py` lets `JSONDecodeError` escape raw;
`blm_arcgis.py` maps it to `ValueError`). Callers map the fault to their own exception and message.

Not migrated, deliberately: `burn_severity/mtbs_releases.py::_query` is NOT behaviour-compatible --
it treats `"error"` by truthiness, splits 5xx `code`s into `OversizedPageError` (the page-halving
signal), and reads `exceededTransferLimit` as a paging CONTINUATION signal, not a refusal.
`crop_cover`'s `_json_get` is an image service; `watersheds` and `fire_perimeters` page through
`ingest/` already. Follow-up outside this package: `ingest/mtbs.py`'s private paging walk vs
`ingest/arcgis.py`.

## The `direct:` feature-id prefix

Not extracted: each lane must equal its OWN legacy twin under `pipeline/direct/<lane>/rows.py`, which
no lane may import. `tests/lanes/test_direct_feature_id_parity.py` imports both sides and asserts
byte equality for all four pairs (watersheds, drought, burn_severity, fire_perimeters), so the
"keep this synced by hand" comments are now executable.

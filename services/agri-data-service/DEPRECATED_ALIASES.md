# Deprecated aliases and shims

Every name kept importable only so an existing importer does not break, with the condition under
which it is deleted. `engineering-principles.md` §2 forbids a disabled-but-kept name with no owner
and no tracking; this file is that tracking. A shim not listed here is a defect.

Twenty-three re-export shims are live, all created by `p4-extract` (track
`config_driven_ingestion_20260926`, Phase 4) and listed in the next section. Every earlier shim is
deleted; those are recorded further down so the removals are not re-litigated.

## Live: the `p4-extract` shims (created 2026-10-24, CA14)

`p4-extract` moved what non-lane modules and the shadow strategies import out of `pipeline/direct/**`
-- the field-product catalogues to `warehouse/field_products/`, and each layer's source protocol,
source and products leaves to `pipeline/lanes/<layer>/` -- because Phase 7 deletes `pipeline/direct/**`
and a strategy may not import it (`pipeline/lanes/AGENTS.md`). Each legacy file is a plain re-export
module in the 2026-09-18 style: it enumerates its names in `__all__` and binds the SAME objects, so
`isinstance`, `except` and identity hold across both spellings. A monkeypatch must target the new
module: patching a shim rebinds only the shim's name, never the global the moved code reads.

**Removal condition, every row: deleted at 7D-n of the supersession cleanup lane.**
`tests/test_direct_extraction_shims.py` holds each row to its new home (identity) and to this table.

| Shim (legacy module) | Moved to |
|---|---|
| `agri_data_service.pipeline.direct.climate.products` | `warehouse/field_products/climate.py` |
| `agri_data_service.pipeline.direct.soil.products` | `warehouse/field_products/soil.py` |
| `agri_data_service.pipeline.direct.vegetation.products` | `warehouse/field_products/vegetation.py` |
| `agri_data_service.pipeline.direct.burn_severity.source_protocol` | `pipeline/lanes/burn_severity/source_protocol.py` |
| `agri_data_service.pipeline.direct.burn_severity.mtbs` | `pipeline/lanes/burn_severity/mtbs.py` |
| `agri_data_service.pipeline.direct.burn_severity.products` | `pipeline/lanes/burn_severity/products.py` |
| `agri_data_service.pipeline.direct.drought.source_protocol` | `pipeline/lanes/drought/source_protocol.py` |
| `agri_data_service.pipeline.direct.drought.usdm` | `pipeline/lanes/drought/usdm.py` |
| `agri_data_service.pipeline.direct.drought.products` | `pipeline/lanes/drought/products.py` |
| `agri_data_service.pipeline.direct.evacuation_zones.watermark` | `pipeline/lanes/evacuation_zones/watermark.py` |
| `agri_data_service.pipeline.direct.evacuation_zones.products` | `pipeline/lanes/evacuation_zones/products.py` |
| `agri_data_service.pipeline.direct.evacuation_zones.rows` | `pipeline/lanes/evacuation_zones/rows.py` |
| `agri_data_service.pipeline.direct.evacuation_zones.source` | `pipeline/lanes/evacuation_zones/source.py` |
| `agri_data_service.pipeline.direct.evacuation_zones.support` | `pipeline/lanes/evacuation_zones/support.py` |
| `agri_data_service.pipeline.direct.watersheds.watermark` | `pipeline/lanes/watersheds/watermark.py` |
| `agri_data_service.pipeline.direct.watersheds.source` | `pipeline/lanes/watersheds/source.py` |
| `agri_data_service.pipeline.direct.crop_cover.source_protocol` | `pipeline/lanes/crop_cover/source_protocol.py` |
| `agri_data_service.pipeline.direct.crop_cover.source` | `pipeline/lanes/crop_cover/source.py` |
| `agri_data_service.pipeline.direct.crop_cover.products` | `pipeline/lanes/crop_cover/products.py` |
| `agri_data_service.pipeline.direct.land_context.source_protocol` | `pipeline/lanes/land_context/source_protocol.py` |
| `agri_data_service.pipeline.direct.land_context.source` | `pipeline/lanes/land_context/source.py` |
| `agri_data_service.pipeline.direct.land_context.products` | `pipeline/lanes/land_context/products.py` |
| `agri_data_service.pipeline.direct.sensors.adapter` (partial: `merge_sensors_day`, `DirectSensorsMerge`, `DirectSensorsError` only) | `pipeline/lanes/sensors/merge.py` |

The sensors row is the one partial shim: `adapter.py` keeps the lock-holding
`DirectSensorsForwardAdapter` (it prints to stderr, which a lane module may not) and re-exports only
the merge unit. The adapter itself goes with `pipeline/direct/sensors/` in Phase 7.

## Deleted 2026-09-18 (N8 — wave-4 deletion list closed)

Both were plain re-export modules, not `__getattr__` shims: they enumerated their names in
`__all__`, so mypy checked every attribute and nothing resolved to `object`. Their removal
condition — every importer, CLI verb, lane spec and test path reads the real module — was met once
the last remaining importer, the adapter test file in each directory, was repointed.

| Shim | Moved to |
|---|---|
| `agri_data_service.pipeline.direct.burn_severity.source` | `burn_severity/mtbs.py` |
| `agri_data_service.pipeline.direct.drought.source` | `drought/usdm.py` |

## Deleted 2026-09-18 (wave-5 fix-up of STYLE-REVIEW-W4 S3)

The three module-level `__getattr__` shims and the five names they resolved, deleted rather than
disabled. Two facts decided it, and both were checked rather than assumed:

1. **Nothing reads them.** A column-0 grep of `src/` finds no reader of any of the five names on the
   modules that carried them; only the deprecation tests did, and those were deleted with the shims.
   This service is not a published package, so there is no importer outside the repo either -- which
   also retires the unfalsifiable "once no importer outside this repo is known to read it" condition
   the old table carried.
2. **The shim cost real type safety.** A module-level `def __getattr__(name: str) -> object` tells a
   type checker the module has attributes it cannot enumerate, so under `mypy.ini`'s `strict = true`
   *any* attribute of these three widely imported modules -- including a typo -- resolved to `object`
   instead of erroring. And the `DeprecationWarning` was decoration, not evidence: Python's default
   filter ignores `DeprecationWarning` outside `__main__`, and this service configures no
   `filterwarnings` and no `logging.captureWarnings(True)`, so a deployed lane reading the alias got
   the value in silence.

| Deleted alias | Module it was on | Read instead |
|---|---|---|
| `PACIFIC_NORTHWEST_BBOX` | `agri_data_service.ingest.mtbs` | `agri_data_service.ingest.mtbs.burn_severity_bounding_box()` |
| `inline_bbox_value` | `agri_data_service.ingest.mtbs` | `agri_data_service.foundation.geography.bounding_box.format_bounding_box_inline()` |
| `parse_bounding_box` | `agri_data_service.ingest.mtbs` | `agri_data_service.foundation.geography.bounding_box.parse_bounding_box()` |
| `SEED_ENVELOPE` | `agri_data_service.foundation.botanical_occurrences.coordinates` | nothing: the module and its `botanical_seed` envelope were retired with the herbaria lanes on 2026-10-03 (`agent/AGENTS.md`, "Herbaria surfaces are retired") |
| `BBOX` | `agri_data_service.pipeline.direct.burn_severity.current_snapshot` | `agri_data_service.ingest.mtbs.burn_severity_bounding_box()` |

`foundation/geography/bounding_box.py`'s `inline_bbox_value` is NOT in that list and is not
deprecated: it is a plain name for `format_bounding_box_inline` on the module that owns the
mechanism, with no `__getattr__` behind it.

## Deleted in the 2026-09-18 wave-4 style pass

- `pipeline/direct/burn_severity/capture.py`'s `BBOX as BBOX` re-export (`noqa: PLC0414`). `daily.py`
  now imports `burn_severity_bounding_box` from `ingest/mtbs.py`, which owns the definition, so the
  indirection had nothing left to carry (STYLE-REVIEW-W2 S8).

## Why the replacements are functions, not constants

`federation.md` §1: a lane, plane, agent tool or reader takes the region as a value. A module-level
constant hides that dependency and, worse, freezes `load_region()` at import time — so a
`PLANTGEO_REGION` set after the first import of the module silently serves the pilot footprint under
another region's name. `coordinates.py` is additionally an L0 `foundation` module, where
`foundation/AGENTS.md` forbids I/O; a module-level read put a filesystem read in its import.

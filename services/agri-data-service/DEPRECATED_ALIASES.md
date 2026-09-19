# Deprecated aliases and shims

Every name kept importable only so an existing importer does not break, with the condition under
which it is deleted. `engineering-principles.md` §2 forbids a disabled-but-kept name with no owner
and no tracking; this file is that tracking. A shim not listed here is a defect.

No shim is currently live. The two `source.py` re-export modules (below) were the last ones and are
now deleted; the three module-level `__getattr__` shims were deleted on 2026-09-18 and are recorded
further down so the removal is not re-litigated.

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
| `SEED_ENVELOPE` | `agri_data_service.foundation.botanical_occurrences.coordinates` | `botanical_seed_envelope()` |
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

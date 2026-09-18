# Deprecated aliases and shims

Every name kept importable only so an existing importer does not break, with the condition under
which it is deleted. `engineering-principles.md` §2 forbids a disabled-but-kept name with no owner
and no tracking; this file is that tracking. A shim not listed here is a defect.

Each Python alias below is resolved by a module-level `__getattr__`, so the value is read **at
attribute access**, never at import, and every access emits a `DeprecationWarning` naming the
replacement. Nothing in `src/` reads these names; only the deprecation tests do.

| Alias | Module | Replacement | Removal condition |
|---|---|---|---|
| `PACIFIC_NORTHWEST_BBOX` | `agri_data_service.ingest.mtbs` | `burn_severity_bounding_box()` | Delete after one release (earliest: the release following 2026-09-18), once no importer outside this repo is known to read it. |
| `SEED_ENVELOPE` | `agri_data_service.foundation.botanical_occurrences.coordinates` | `botanical_seed_envelope()` | Same as above. |
| `BBOX` | `agri_data_service.pipeline.direct.burn_severity.current_snapshot` | `agri_data_service.ingest.mtbs.burn_severity_bounding_box()` | Same as above. |

Deleted in the 2026-09-18 wave-4 style pass, recorded so the removal is not re-litigated:

- `pipeline/direct/burn_severity/capture.py`'s `BBOX as BBOX` re-export (`noqa: PLC0414`). `daily.py`
  now imports `burn_severity_bounding_box` from `ingest/mtbs.py`, which owns the definition, so the
  indirection had nothing left to carry (STYLE-REVIEW-W2 S8).

## Why these three are functions, not constants

`federation.md` §1: a lane, plane, agent tool or reader takes the region as a value. A module-level
constant hides that dependency and, worse, freezes `load_region()` at import time — so a
`PLANTGEO_REGION` set after the first import of the module silently serves the pilot footprint under
another region's name. `coordinates.py` is additionally an L0 `foundation` module, where
`foundation/AGENTS.md` forbids I/O; a module-level read put a filesystem read in its import.

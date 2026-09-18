# `foundation/geography/`

Generic spatial-envelope mechanisms with zero domain meaning: `west,south,east,north` bounding-box
parsing and CLI-argument formatting. Extracted 2026-09-18 from `ingest/mtbs.py`, which minted
`inline_bbox_value` for MTBS's own `--bbox` flag; six `pipeline/direct/<domain>` lanes
(`evacuation_zones/forward.py`, `sensors/forward.py`, `watersheds/forward.py`,
`weather_observations/forward.py`, `fire_perimeters/forward.py`, `fire_detections.py`) came to
import it from one source's ingest module -- a cross-domain leak `layer-lanes.md` §5a names
directly: "the shared half moves down; the dependents never move sideways." (The `W3-B` survey that
first flagged this recorded five lanes; `fire_perimeters/forward.py` had gained the same import by
the time this extraction ran, making it six.)

## Admission

Passes `foundation/AGENTS.md`'s four-criterion test with no ruled exception, unlike
`foundation/region/`:
1. No first-party import (stdlib `argparse` only).
2. No forbidden third-party import.
3. Used by six pipeline lanes plus `ingest/mtbs.py` itself -- well past "two or more".
4. `bounding_box` names a mechanism (an envelope and its CLI encoding), not a domain noun.

## What moved, what stayed

Moved: `BoundingBox`, `parse_bounding_box`, `inline_bbox_value` (renamed
`format_bounding_box_inline`; the old name is kept as a plain alias here, not deprecated -- only its
old home deprecates it) and the two constants they need (`BBOX_OPTION`, `BBOX_ORDINATE_COUNT`).

Stayed in `ingest/mtbs.py`, deliberately: `bounding_box_token` (a release-identity fingerprint tied
to MTBS's release-set keying; never imported by another lane), `_bbox_parameters` (renders the
envelope the way the retired TypeScript's ArcGIS query did -- one source's wire format, not a shared
mechanism) and `burn_severity_bounding_box()` (reads the region manifest's `burn_severity`
sub-envelope; a manifest read is I/O and lane-specific, not generic bbox math).

## Deprecated aliases left behind

`ingest.mtbs.parse_bounding_box` and `ingest.mtbs.inline_bbox_value` resolve lazily via that
module's `__getattr__` and warn on access; see `DEPRECATED_ALIASES.md`. `ingest.mtbs.BoundingBox`
was **not** put behind the same warning -- it is a bare type alias with no runtime behaviour,
re-exported plainly (`from agri_data_service.foundation.geography.bounding_box import BoundingBox as
BoundingBox`) so `ingest/mtbs.py`'s own function signatures, which use it throughout, keep resolving
without adding deprecation noise to a name nothing calls at runtime (`from __future__ import
annotations` means annotation references to it are never evaluated at runtime in the first place).

## Known near-duplicate, not touched here

`parquet_ops/request_params.py` also defines a `BoundingBox` (a `NamedTuple`/dataclass with
`west`/`south`/`east`/`north` fields, not this module's plain 4-tuple) and
`pipeline/direct/soil_survey/source_protocol.py` re-declares the same tuple alias this module now
owns. `W3-B`'s judgement call #5 already flagged the `soil_survey` duplication as wave-4 debt to
collapse once this extraction landed; it is now unblocked but out of scope for this push
(behaviour-neutral extraction only).

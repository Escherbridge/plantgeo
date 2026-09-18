"""The typed `Region` manifest: one declared footprint, read from `pnw.json`, never re-literalled.

`federation.md` section 1 names this the single declaration of a deployment's footprint and lists
the migration list of literals a later push moves in or points at: `PACIFIC_NORTHWEST_BBOX`
(`ingest/mtbs.py`), `SEED_ENVELOPE` (`foundation/botanical_occurrences/coordinates.py`),
`PNW_STATE_CODES`/`PnwStateCode` (`src/lib/server/db/schema/land-context/shared.ts`) and
`PNW_COARSE_NODES`. This module and `pnw.json` are the destination those literals move into or
behind; see `AGENTS.md` in this directory for why the values differ from each other today.
"""

from __future__ import annotations

import json
import os
from importlib import resources
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, model_validator

#: `PLANTGEO_REGION` selects the manifest `load_region` returns; unset defaults to the pilot.
REGION_ENV_VAR: Final = "PLANTGEO_REGION"

LatticeOriginRule = Literal["floor_to_cell_origin"]
SourceCoverage = Literal["global", "regional"]


class RegionEnvelope(BaseModel):
    """A WGS84 west/south/east/north bounding box; the manifest's own footprint claim."""

    model_config = ConfigDict(frozen=True)

    west: float
    south: float
    east: float
    north: float

    @model_validator(mode="after")
    def _bounds_are_ordered(self) -> RegionEnvelope:
        if self.west >= self.east:
            raise ValueError(f"envelope west ({self.west}) must be < east ({self.east})")
        if self.south >= self.north:
            raise ValueError(f"envelope south ({self.south}) must be < north ({self.north})")
        return self


class LayerBinding(BaseModel):
    """One `geo.layers` slug bound to the source that fills it in this region, per `federation.md` §2."""

    model_config = ConfigDict(frozen=True)

    layer_slug: str
    source_slug: str
    coverage: SourceCoverage


class Region(BaseModel):
    """A deployment's one typed footprint: envelope, lattice, timezone, admin scope and layer bindings.

    Fields match `federation.md` §1's minimum list. `sub_envelopes` is not in that list; it is a
    transitional field (see `AGENTS.md`) holding the PNW pilot's two envelopes narrower than
    `envelope` itself, keyed by the purpose that still owns a private literal today
    (`ingest/mtbs.py`'s burn envelope, `foundation/botanical_occurrences/coordinates.py`'s
    `SEED_ENVELOPE`), so the migration in `federation.md` §5 step 2 has somewhere to point instead
    of restating the numbers.
    """

    model_config = ConfigDict(frozen=True)

    slug: str
    display_name: str
    envelope: RegionEnvelope
    #: The narrower opening-camera/burn-envelope box (`FALLBACK_COVERAGE_BBOX` in
    #: `coverage-region.ts`); kept distinct from `envelope` so migrating the manifest in never
    #: widened the client's default camera. See `AGENTS.md` §default_camera_envelope.
    default_camera_envelope: RegionEnvelope
    sub_envelopes: dict[str, RegionEnvelope] = {}
    crs: int | None
    lattice_pitch_degrees: float
    lattice_origin_rule: LatticeOriginRule
    timezone: str
    iso_country_codes: tuple[str, ...]
    admin_codes: tuple[str, ...]
    enabled_layers: tuple[LayerBinding, ...]

    @model_validator(mode="after")
    def _lattice_pitch_is_positive(self) -> Region:
        if self.lattice_pitch_degrees <= 0:
            raise ValueError(f"lattice_pitch_degrees ({self.lattice_pitch_degrees}) must be > 0")
        return self


def _load_manifest_json(slug: str) -> Region:
    """Parse `<slug>.json` from this package into a validated `Region`; raises on a missing file."""
    package_files = resources.files(__package__)
    manifest_path = package_files / f"{slug}.json"
    if not manifest_path.is_file():
        raise ValueError(f"no region manifest named {slug!r} in {__package__}")
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    return Region.model_validate(raw)


#: The pilot region, loaded once at import time; see the module docstring for why PNW is the base.
PNW: Final[Region] = _load_manifest_json("pnw")

_REGISTRY: Final[dict[str, Region]] = {"pnw": PNW}


def load_region(slug: str | None = None) -> Region:
    """Return the named region manifest, defaulting to `PLANTGEO_REGION` and then the PNW pilot.

    Raises `ValueError` for a slug this deployment has no manifest for, rather than falling back
    silently -- an unrecognised region is a configuration error, not a reason to serve the pilot's
    footprint under someone else's name.
    """
    resolved_slug = slug or os.environ.get(REGION_ENV_VAR) or "pnw"
    try:
        return _REGISTRY[resolved_slug]
    except KeyError as error:
        raise ValueError(f"unknown region {resolved_slug!r}; registered manifests are {sorted(_REGISTRY)}") from error

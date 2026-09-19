"""The typed `Region` manifest: one declared footprint, read from `pnw.json`, never re-literalled.

`federation.md` section 1 names this the single declaration of a deployment's footprint and lists
the migration list of literals a later push moves in or points at: `burn_severity_bounding_box()`
(`ingest/mtbs.py`, formerly `PACIFIC_NORTHWEST_BBOX`), `botanical_seed_envelope()`
(`foundation/botanical_occurrences/coordinates.py`, formerly `SEED_ENVELOPE`),
`PNW_STATE_CODES`/`PnwStateCode` (`src/lib/server/db/schema/land-context/shared.ts`) and
`PNW_COARSE_NODES`. This module and `pnw.json` are the destination those literals move into or
behind; see `AGENTS.md` in this directory for why the values differ from each other today.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping  # noqa: TC003 - pydantic resolves this at runtime
from importlib import resources
from types import MappingProxyType
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

#: `PLANTGEO_REGION` selects the manifest `load_region` returns; unset defaults to the pilot.
REGION_ENV_VAR: Final = "PLANTGEO_REGION"

LatticeOriginRule = Literal["floor_to_cell_origin"]
SourceCoverage = Literal["global", "regional"]


class RegionEnvelope(BaseModel):
    """A WGS84 west/south/east/north bounding box; the manifest's own footprint claim."""

    model_config = ConfigDict(frozen=True, extra="forbid")

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

    model_config = ConfigDict(frozen=True, extra="forbid")

    layer_slug: str
    source_slug: str
    coverage: SourceCoverage


class Region(BaseModel):
    """A deployment's one typed footprint: envelope, lattice, timezone, admin scope and layer bindings.

    Fields match `federation.md` §1's minimum list. `sub_envelopes` is not in that list; it is a
    transitional field (see `AGENTS.md`) holding the PNW pilot's two envelopes narrower than
    `envelope` itself, keyed by the purpose that still owns a private literal today
    (`ingest/mtbs.py`'s `burn_severity_bounding_box()`, `foundation/botanical_occurrences/
    coordinates.py`'s `botanical_seed_envelope()`), so the migration in `federation.md` §5 step 2 has
    somewhere to point instead of restating the numbers.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    slug: str
    display_name: str
    envelope: RegionEnvelope
    #: The narrower opening-camera/burn-envelope box (`FALLBACK_COVERAGE_BBOX` in
    #: `coverage-region.ts`); kept distinct from `envelope` so migrating the manifest in never
    #: widened the client's default camera. See `AGENTS.md` §default_camera_envelope.
    default_camera_envelope: RegionEnvelope
    #: A `Mapping`, not a `dict`: `_sub_envelopes_are_immutable` below wraps every value in
    #: `MappingProxyType` so `region.sub_envelopes["x"] = ...` fails even though `frozen=True` only
    #: blocks reassigning the `sub_envelopes` attribute itself, not mutating what it points at.
    sub_envelopes: Mapping[str, RegionEnvelope] = Field(default_factory=lambda: MappingProxyType({}))
    crs: int | None
    lattice_pitch_degrees: float
    lattice_origin_rule: LatticeOriginRule
    timezone: str
    iso_country_codes: tuple[str, ...]
    admin_codes: tuple[str, ...]
    #: The platform's whole layer VOCABULARY, restated here so the web tree compiles in the same
    #: enumeration the service walks (`layer_availability.PLATFORM_LAYER_SLUGS`, which
    #: `tests/foundation/test_region_layer_availability.py` pins this field to). It is not the
    #: region's bindings: a slug here and absent from `enabled_layers` is a GOVERNED ABSENCE, and a
    #: slug absent from here is not a federated layer at all. Without it, a client could not tell
    #: those two apart from the manifest alone (STYLE-REVIEW-W5 B1).
    platform_layers: tuple[str, ...]
    enabled_layers: tuple[LayerBinding, ...]

    @model_validator(mode="after")
    def _every_binding_names_a_platform_layer(self) -> Region:
        outside = sorted({b.layer_slug for b in self.enabled_layers} - set(self.platform_layers))
        if outside:
            raise ValueError(
                f"enabled_layers binds {outside}, which are absent from this manifest's platform_layers; "
                f"a bound layer that is not in the vocabulary cannot be reported as available or unbound"
            )
        return self

    @field_validator("sub_envelopes", mode="after")
    @classmethod
    def _sub_envelopes_are_immutable(cls, value: Mapping[str, RegionEnvelope]) -> Mapping[str, RegionEnvelope]:
        return MappingProxyType(dict(value))

    @model_validator(mode="after")
    def _lattice_pitch_is_positive(self) -> Region:
        if self.lattice_pitch_degrees <= 0:
            raise ValueError(f"lattice_pitch_degrees ({self.lattice_pitch_degrees}) must be > 0")
        return self


def _load_manifest_json(slug: str) -> Region:
    """Parse the registered JSON data file for `slug` into a validated `Region`.

    Raises when the slug is registered but its file is missing from the installed package, which is
    a packaging fault rather than a configuration one -- `load_region()` has already refused an
    unregistered slug before this is reached.
    """
    package_files = resources.files(__package__)
    manifest_path = package_files / _MANIFEST_FILE_BY_SLUG[slug]
    if not manifest_path.is_file():
        raise ValueError(f"no region manifest named {slug!r} in {__package__}")
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    return Region.model_validate(raw)


#: The pilot slug; the manifest `load_region()` falls back to when nothing selects another.
_PILOT_REGION_SLUG: Final = "pnw"

#: The second deployment's slug: a real manifest this tree ships, not a test fixture. See
#: `AGENTS.md` in this directory, section "Why a second manifest is data rather than a fixture".
_SECOND_REGION_SLUG: Final = "kenya-highlands"

#: Every manifest this deployment ships, slug to its JSON data file beside this module. Not the
#: manifests themselves -- nothing reads a JSON file until `load_region()` is actually called, per
#: `python.md` "the region is a value, not a constant": a module-level `Region` constant is the
#: exact hidden dependency that rule forbids, and it would do filesystem I/O at import time whether
#: or not `PLANTGEO_REGION` names something else.
#:
#: The file name is the slug with its hyphen written as an underscore, spelled here rather than
#: computed, so a registered slug always names a file that exists in the package data and a reader
#: can see which file backs which slug without running the transformation in their head.
_MANIFEST_FILE_BY_SLUG: Final[Mapping[str, str]] = MappingProxyType(
    {
        _PILOT_REGION_SLUG: "pnw.json",
        _SECOND_REGION_SLUG: "kenya_highlands.json",
    }
)

#: The slugs `load_region()` will resolve; derived from the file registry so the two cannot drift.
_KNOWN_REGION_SLUGS: Final[tuple[str, ...]] = tuple(_MANIFEST_FILE_BY_SLUG)

#: Populated lazily, one entry per slug `load_region()` has actually resolved; never read directly.
_REGION_CACHE: Final[dict[str, Region]] = {}


def load_region(slug: str | None = None) -> Region:
    """Return the named region manifest, defaulting to `PLANTGEO_REGION` and then the PNW pilot.

    The only sanctioned door into this package's data: there is no module-level `Region` constant to
    import instead, so every caller's dependency on a region is visible in its own call site. Reads
    `PLANTGEO_REGION` fresh on every call rather than once at import, and caches by resolved slug so
    repeat calls for the same slug do not re-parse `<slug>.json`.

    Raises `ValueError` for a slug this deployment has no manifest for, rather than falling back
    silently -- an unrecognised region is a configuration error, not a reason to serve the pilot's
    footprint under someone else's name.
    """
    resolved_slug = slug or os.environ.get(REGION_ENV_VAR) or _PILOT_REGION_SLUG
    if resolved_slug not in _KNOWN_REGION_SLUGS:
        raise ValueError(f"unknown region {resolved_slug!r}; registered manifests are {sorted(_KNOWN_REGION_SLUGS)}")
    if resolved_slug not in _REGION_CACHE:
        _REGION_CACHE[resolved_slug] = _load_manifest_json(resolved_slug)
    return _REGION_CACHE[resolved_slug]

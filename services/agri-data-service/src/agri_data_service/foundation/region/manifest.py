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
import re
from collections.abc import Mapping  # noqa: TC003 - pydantic resolves this at runtime
from dataclasses import dataclass
from decimal import Decimal
from importlib import resources
from types import MappingProxyType
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

#: `PLANTGEO_REGION` selects the manifest `load_region` returns; unset defaults to the pilot.
REGION_ENV_VAR: Final = "PLANTGEO_REGION"

LatticeOriginRule = Literal["floor_to_cell_origin"]
#: `half_step`: centroids sit half a pitch inside the lattice envelope's west/south edge.
AnalysisLatticeOriginRule = Literal["half_step"]
SourceCoverage = Literal["global", "regional"]

#: A named analysis lattice's key: lowercase alphanumerics joined by single hyphens.
_ANALYSIS_LATTICE_KEY_PATTERN: Final = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
#: Decimal places a cell key carries per ordinate; soil's keys read `<prefix><lat>:<lon>` at this.
_CELL_KEY_ORDINATE_PLACES: Final = 4


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


@dataclass(frozen=True, slots=True)
class AnalysisLatticeCell:
    """One analysis-lattice cell: its region-free key and its centroid."""

    cell_key: str
    latitude: float
    longitude: float


def _decimal(value: float) -> Decimal:
    """The shortest decimal that round-trips `value`, so 0.25 is 0.25 and never 0.2500000000000001."""
    return Decimal(repr(value))


class AnalysisLattice(BaseModel):
    """A named, complete analysis lattice (spec §4.1 C2): a pitch, an origin rule, an envelope, a key prefix.

    Lanes name one by key (`grid = "analysis-0p25"`) instead of restating its numbers. See
    `AGENTS.md` in this directory, "Named analysis lattices".
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    pitch_degrees: float
    origin_rule: AnalysisLatticeOriginRule
    envelope: RegionEnvelope
    cell_key_prefix: str

    @model_validator(mode="after")
    def _the_envelope_holds_whole_cells(self) -> AnalysisLattice:
        if self.pitch_degrees <= 0:
            raise ValueError(f"pitch_degrees ({self.pitch_degrees}) must be > 0")
        if not self.cell_key_prefix.endswith(":"):
            raise ValueError(f"cell_key_prefix {self.cell_key_prefix!r} must end with ':'")
        pitch = _decimal(self.pitch_degrees)
        for axis, low, high in (
            ("west-east", self.envelope.west, self.envelope.east),
            ("south-north", self.envelope.south, self.envelope.north),
        ):
            span = _decimal(high) - _decimal(low)
            if span % pitch:
                raise ValueError(
                    f"the {axis} span {span} is not a whole number of {pitch}-degree cells; a partial "
                    "edge column would silently drop cells from the lattice"
                )
        return self

    def cells(self) -> tuple[AnalysisLatticeCell, ...]:
        """Every cell, south to north then west to east, keyed `<prefix><lat>:<lon>` at 4 places."""
        pitch = _decimal(self.pitch_degrees)
        half_pitch = pitch / 2
        west = _decimal(self.envelope.west)
        south = _decimal(self.envelope.south)
        column_count = int((_decimal(self.envelope.east) - west) / pitch)
        row_count = int((_decimal(self.envelope.north) - south) / pitch)
        places = _CELL_KEY_ORDINATE_PLACES
        cells: list[AnalysisLatticeCell] = []
        for row in range(row_count):
            latitude = south + half_pitch + row * pitch
            for column in range(column_count):
                longitude = west + half_pitch + column * pitch
                cell_key = f"{self.cell_key_prefix}{latitude:.{places}f}:{longitude:.{places}f}"
                cell = AnalysisLatticeCell(cell_key=cell_key, latitude=float(latitude), longitude=float(longitude))
                cells.append(cell)
        return tuple(cells)

    def cell_keys(self) -> frozenset[str]:
        """The set of every cell key this lattice defines."""
        return frozenset(cell.cell_key for cell in self.cells())


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
    #: Named analysis lattices lanes grid onto (spec §4.1 C2). Optional and empty by default, so a
    #: manifest that declares none (`kenya_highlands.json`) stays valid unedited; a lane naming a
    #: lattice its region lacks is quarantined by `foundation/lane_config/loader.py`. Immutable for
    #: the same reason `sub_envelopes` is.
    analysis_lattices: Mapping[str, AnalysisLattice] = Field(default_factory=lambda: MappingProxyType({}))

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

    @field_validator("analysis_lattices", mode="after")
    @classmethod
    def _analysis_lattices_are_named_and_immutable(
        cls, value: Mapping[str, AnalysisLattice]
    ) -> Mapping[str, AnalysisLattice]:
        unnamed = sorted(key for key in value if not _ANALYSIS_LATTICE_KEY_PATTERN.match(key))
        if unnamed:
            raise ValueError(f"analysis lattice keys {unnamed} must be lowercase alphanumerics joined by hyphens")
        return MappingProxyType(dict(value))

    @model_validator(mode="after")
    def _every_analysis_lattice_sits_inside_the_envelope(self) -> Region:
        for key, lattice in self.analysis_lattices.items():
            inner, outer = lattice.envelope, self.envelope
            inside_west_east = outer.west <= inner.west and inner.east <= outer.east
            inside_south_north = outer.south <= inner.south and inner.north <= outer.north
            if not (inside_west_east and inside_south_north):
                raise ValueError(f"analysis lattice {key!r} reaches outside this manifest's envelope")
        return self

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

"""The typed `Region` manifest: one declared footprint, read from `pnw.json`, never re-literalled.

`federation.md` section 1 names this the single declaration of a deployment's footprint and lists
the migration list of literals a later push moves in or points at: `burn_severity_bounding_box()`
(`ingest/mtbs.py`, formerly `PACIFIC_NORTHWEST_BBOX`),
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
from decimal import Decimal, localcontext
from functools import lru_cache
from importlib import resources
from types import MappingProxyType
from typing import Final, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from agri_data_service.foundation.region.layer_availability import PLATFORM_LAYER_SLUGS

#: `PLANTGEO_REGION` selects the manifest `load_region` returns; unset defaults to the pilot.
REGION_ENV_VAR: Final = "PLANTGEO_REGION"
REGION_MANIFEST_ENV_VAR: Final = "PLANTGEO_REGION_MANIFEST_JSON"
MAX_REGION_MANIFEST_BYTES: Final = 64 * 1024
CUSTOM_REGION_CACHE_SIZE: Final = 8
MAX_ANALYSIS_LATTICE_CELLS: Final = 100_000

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

    west: float = Field(ge=-180, le=180, allow_inf_nan=False, strict=True)
    south: float = Field(ge=-90, le=90, allow_inf_nan=False, strict=True)
    east: float = Field(ge=-180, le=180, allow_inf_nan=False, strict=True)
    north: float = Field(ge=-90, le=90, allow_inf_nan=False, strict=True)

    @model_validator(mode="after")
    def _bounds_are_ordered(self) -> RegionEnvelope:
        if self.west >= self.east:
            raise ValueError(f"envelope west ({self.west}) must be < east ({self.east})")
        if self.south >= self.north:
            raise ValueError(f"envelope south ({self.south}) must be < north ({self.north})")
        return self

    def contains(self, other: RegionEnvelope) -> bool:
        """Whether this rectangle fully contains another rectangle, including its boundary."""
        return (
            self.west <= other.west
            and other.east <= self.east
            and self.south <= other.south
            and other.north <= self.north
        )


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

    pitch_degrees: float = Field(gt=0, allow_inf_nan=False, strict=True)
    origin_rule: AnalysisLatticeOriginRule
    envelope: RegionEnvelope
    cell_key_prefix: str

    @model_validator(mode="after")
    def _the_envelope_holds_whole_cells(self) -> AnalysisLattice:
        if self.pitch_degrees <= 0:
            raise ValueError(f"pitch_degrees ({self.pitch_degrees}) must be > 0")
        if not self.cell_key_prefix.endswith(":"):
            raise ValueError(f"cell_key_prefix {self.cell_key_prefix!r} must end with ':'")
        with localcontext() as context:
            context.prec = 400
            pitch = _decimal(self.pitch_degrees)
            precision = Decimal("0.0001")
            if pitch > Decimal(360):
                raise ValueError("analysis lattice pitch exceeds the WGS84 envelope")
            if pitch % precision:
                raise ValueError("analysis lattice pitch must be exactly representable at four decimal places")
            cell_count = 1
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
                if (_decimal(low) + pitch / 2) % precision:
                    raise ValueError("analysis lattice centroids must be exactly representable at four decimal places")
                cell_count *= int(span / pitch)
            if cell_count > MAX_ANALYSIS_LATTICE_CELLS:
                raise ValueError(f"analysis lattice exceeds {MAX_ANALYSIS_LATTICE_CELLS} cells")
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

    layer_slug: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=128)
    source_slug: str = Field(pattern=r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$", max_length=128)
    coverage: SourceCoverage


class Region(BaseModel):
    """A deployment's one typed footprint: envelope, lattice, timezone, admin scope and layer bindings.

    Fields match `federation.md` §1's minimum list. `sub_envelopes` is not in that list; it is a
    transitional field (see `AGENTS.md`) holding the PNW pilot's two envelopes narrower than
    `envelope` itself, keyed by the purpose that still owns a private literal today
    (`ingest/mtbs.py`'s `burn_severity_bounding_box()`, `pipeline/direct/crop_cover/products.py`),
    so the migration in `federation.md` §5 step 2 has somewhere to point instead of restating the
    numbers.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    slug: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=64)
    display_name: str = Field(min_length=1, max_length=256)
    envelope: RegionEnvelope
    #: The narrower opening-camera/burn-envelope box (`FALLBACK_COVERAGE_BBOX` in
    #: `coverage-region.ts`); kept distinct from `envelope` so migrating the manifest in never
    #: widened the client's default camera. See `AGENTS.md` §default_camera_envelope.
    default_camera_envelope: RegionEnvelope
    #: A `Mapping`, not a `dict`: `_sub_envelopes_are_immutable` below wraps every value in
    #: `MappingProxyType` so `region.sub_envelopes["x"] = ...` fails even though `frozen=True` only
    #: blocks reassigning the `sub_envelopes` attribute itself, not mutating what it points at.
    sub_envelopes: Mapping[str, RegionEnvelope] = Field(default_factory=lambda: MappingProxyType({}))
    crs: int | None = Field(strict=True)
    lattice_pitch_degrees: float = Field(gt=0, allow_inf_nan=False, strict=True)
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

    @field_validator("timezone")
    @classmethod
    def _timezone_is_known(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ValueError("timezone must name an installed IANA time zone") from error
        return value

    @model_validator(mode="after")
    def _scope_codes_and_bindings_are_unambiguous(self) -> Region:
        groups = {
            "iso_country_codes": self.iso_country_codes,
            "admin_codes": self.admin_codes,
            "platform_layers": self.platform_layers,
            "enabled_layers": tuple(binding.layer_slug for binding in self.enabled_layers),
        }
        for name, values in groups.items():
            if len(values) != len(set(values)):
                raise ValueError(f"{name} must not contain duplicates")
        if not self.iso_country_codes or any(
            re.fullmatch(r"[A-Z]{2}", code) is None for code in self.iso_country_codes
        ):
            raise ValueError("iso_country_codes must contain uppercase two-letter country codes")
        for code in self.admin_codes:
            if re.fullmatch(r"[A-Z]{2}-[A-Z0-9]{1,3}", code) is None or code[:2] not in self.iso_country_codes:
                raise ValueError("admin_codes must name subdivisions of the declared countries")
        if any(_ANALYSIS_LATTICE_KEY_PATTERN.fullmatch(layer) is None for layer in self.platform_layers):
            raise ValueError("platform_layers must contain safe layer slugs")
        return self

    @model_validator(mode="after")
    def _camera_and_sub_envelopes_are_contained(self) -> Region:
        if not self.envelope.contains(self.default_camera_envelope):
            raise ValueError("default_camera_envelope reaches outside this manifest's envelope")
        for key, envelope in self.sub_envelopes.items():
            if not self.envelope.contains(envelope):
                raise ValueError(f"sub_envelopes {key!r} reaches outside this manifest's envelope")
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
            if not self.envelope.contains(lattice.envelope):
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

#: Packaged reference slugs, derived from their file registry.
_KNOWN_REGION_SLUGS: Final[tuple[str, ...]] = tuple(_MANIFEST_FILE_BY_SLUG)

#: Populated lazily, one entry per slug `load_region()` has actually resolved; never read directly.
_REGION_CACHE: Final[dict[str, Region]] = {}


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Refuse ambiguous JSON objects rather than letting later fields replace earlier ones."""
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("region manifest JSON contains a duplicate object key")
        result[key] = value
    return result


@lru_cache(maxsize=CUSTOM_REGION_CACHE_SIZE)
def _parse_custom_manifest(payload: str) -> Region:
    """Parse one bounded canonical-shape manifest; cache identity includes its complete content."""
    try:
        raw = json.loads(payload, object_pairs_hook=_unique_json_object)
    except (json.JSONDecodeError, RecursionError):
        raise ValueError("region manifest must be valid JSON") from None
    try:
        region = Region.model_validate(raw)
    except ValidationError:
        raise ValueError("invalid custom region manifest") from None
    if region.slug in _KNOWN_REGION_SLUGS:
        raise ValueError("custom region manifests must not shadow packaged regions")
    if set(region.platform_layers) != set(PLATFORM_LAYER_SLUGS):
        raise ValueError("custom region manifest must declare the complete platform layer vocabulary")
    return region


def _custom_region(payload: str, explicit_slug: str | None) -> Region:
    """Require an explicit deployment selection and bounded custom manifest before loading it."""
    selected = os.environ.get(REGION_ENV_VAR)
    if not selected:
        raise ValueError(f"{REGION_MANIFEST_ENV_VAR} requires {REGION_ENV_VAR}")
    try:
        size = len(payload.encode("utf-8"))
    except UnicodeError:
        raise ValueError("region manifest must contain valid UTF-8 text") from None
    if size > MAX_REGION_MANIFEST_BYTES:
        raise ValueError(f"region manifest exceeds {MAX_REGION_MANIFEST_BYTES} UTF-8 bytes")
    region = _parse_custom_manifest(payload)
    if selected != region.slug or (explicit_slug is not None and explicit_slug != region.slug):
        raise ValueError("custom region manifest slug must match the selected region")
    return region


def load_region(slug: str | None = None) -> Region:
    """Resolve a packaged or selected custom region; see AGENTS.md, Custom deployment manifests."""
    if slug not in _KNOWN_REGION_SLUGS:
        custom = os.environ.get(REGION_MANIFEST_ENV_VAR)
        if custom:
            return _custom_region(custom, slug)
    resolved_slug = slug or os.environ.get(REGION_ENV_VAR) or _PILOT_REGION_SLUG
    if resolved_slug not in _KNOWN_REGION_SLUGS:
        raise ValueError(f"unknown region {resolved_slug!r}; registered manifests are {sorted(_KNOWN_REGION_SLUGS)}")
    if resolved_slug not in _REGION_CACHE:
        _REGION_CACHE[resolved_slug] = _load_manifest_json(resolved_slug)
    return _REGION_CACHE[resolved_slug]

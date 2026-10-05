"""Curation-region data the rules read (in-region readings, habitat MLRAs), loaded per call; see AGENTS.md §Regions."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

# The pilot release beside this module; the wiring passes the published curation release to prepare() instead.
PILOT_REGION_DATA_PATH = Path(__file__).with_name("curation_regions.json")
# Habitat qualifiers read through an MLRA list: the release must give each one (applicability.habitat_status).
MLRA_HABITAT_QUALIFIERS = frozenset({"forest_woodland"})


@dataclass(frozen=True)
class InRegionReading:
    """One owner reading that puts named guides in-region for a curation region, with the decision it records."""

    region: str
    source_ids: tuple[str, ...]
    decision: str

    def __post_init__(self) -> None:
        """Freeze the source ids; refuse an empty region, source list or source id."""
        object.__setattr__(self, "source_ids", tuple(self.source_ids))
        if not self.region or not self.source_ids or not all(self.source_ids):
            message = f"an in-region reading needs a region and source ids, got {self.region!r} {self.source_ids}"
            raise ValueError(message)


@dataclass(frozen=True)
class CurationRegionData:
    """One release of curation-region facts: named in-region readings and the MLRAs each habitat qualifier reads."""

    release: str
    in_region_readings: Mapping[str, InRegionReading]
    habitat_mlras: Mapping[str, tuple[str, ...]]

    def __post_init__(self) -> None:
        """Freeze read-only copies; refuse a release without an id, or an MLRA qualifier missing or listing none."""
        object.__setattr__(self, "in_region_readings", MappingProxyType(dict(self.in_region_readings)))
        mlras = {qualifier: tuple(values) for qualifier, values in self.habitat_mlras.items()}
        object.__setattr__(self, "habitat_mlras", MappingProxyType(mlras))
        malformed = sorted(
            qualifier
            for qualifier in MLRA_HABITAT_QUALIFIERS | set(mlras)
            if qualifier not in MLRA_HABITAT_QUALIFIERS or not mlras.get(qualifier) or not all(mlras[qualifier])
        )
        if not self.release or malformed:
            message = (
                f"curation region data {self.release!r} must name a release and list MLRAs for exactly "
                f"{sorted(MLRA_HABITAT_QUALIFIERS)}; malformed or unknown: {malformed}"
            )
            raise ValueError(message)

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> CurationRegionData:
        """The release from its JSON form (`curation_regions.json`)."""
        readings = {
            name: InRegionReading(reading["region"], tuple(reading["source_ids"]), reading["decision"])
            for name, reading in values["in_region_readings"].items()
        }
        return cls(values["release"], readings, values["habitat_mlras"])

    def in_region_overrides(self, readings: Iterable[str]) -> frozenset[tuple[str, str]]:
        """Every (source_id, region) the named readings put in-region; a reading this release lacks raises."""
        names = sorted(set(readings))
        unknown = [name for name in names if name not in self.in_region_readings]
        if unknown:
            message = f"curation region data {self.release!r} has no in-region readings {unknown}"
            raise ValueError(message)
        return frozenset(
            (source_id, self.in_region_readings[name].region)
            for name in names
            for source_id in self.in_region_readings[name].source_ids
        )

    def forest_woodland_mlras(self) -> tuple[str, ...]:
        """The MLRAs the `forest_woodland` habitat qualifier applies in."""
        return self.habitat_mlras["forest_woodland"]

    def digest_value(self) -> dict[str, Any]:
        """The whole release as plain values, for the inputs digest."""
        return {
            "release": self.release,
            "in_region_readings": {
                name: {"region": reading.region, "source_ids": sorted(reading.source_ids), "decision": reading.decision}
                for name, reading in self.in_region_readings.items()
            },
            "habitat_mlras": {qualifier: sorted(values) for qualifier, values in self.habitat_mlras.items()},
        }


def pilot_region_data() -> CurationRegionData:
    """The pilot release, read from `curation_regions.json` on each call (never at import)."""
    return CurationRegionData.from_mapping(json.loads(PILOT_REGION_DATA_PATH.read_text(encoding="utf-8")))

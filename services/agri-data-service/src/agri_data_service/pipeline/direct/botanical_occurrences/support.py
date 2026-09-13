"""Deterministic index cells, confirmed/possible membership, and sparse exact-set aggregates."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from agri_data_service.foundation.botanical_occurrences.coordinates import (
    DECLARED_ENVELOPE,
    polygon_wkb,
    within_declared_envelope,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from agri_data_service.pipeline.direct.botanical_occurrences.normalize import NormalizedOccurrence

#: Metres per degree of latitude, near enough everywhere for an uncertainty footprint. Longitude is
#: scaled by cos(latitude) at the record's own latitude, which is what keeps a 5 km circle from
#: spanning half a continent at 49 degrees north.
_METERS_PER_DEGREE_LATITUDE: Final = 111_320.0

#: The support family. `grid-{degrees}` cells are aligned to the whole-degree origin, so a cell id is
#: reproducible from a coordinate alone and two generations index the same point identically.
SUPPORT_DEGREES: Final[dict[str, float]] = {"grid-0.25": 0.25, "grid-0.05": 0.05}

#: How many cells one record's uncertainty footprint may be spread across before the fan-out is
#: refused. Nine is the 3x3 neighbourhood: past that the record is not "somewhere in these cells", it
#: is a claim too weak to attach to a map at this rung.
MAX_FANOUT_CELLS: Final = 9

#: What a distance to an association means. Never mixed: a distance to a confirmed point and a
#: distance to a cell centroid are different measurements and a reader must be able to tell them apart.
POINT_DISTANCE: Final = "to_record_point"
CENTROID_DISTANCE: Final = "to_cell_centroid"


class SupportError(ValueError):
    """Raised when a support id is not one this lane publishes."""


@dataclass(frozen=True, slots=True)
class GridSupport:
    """One rung of the support family: a fixed-degree lattice with a reproducible cell id."""

    support_id: str
    degrees: float

    def cell_indices(self, longitude: float, latitude: float) -> tuple[int, int]:
        """Return the (column, row) of the cell containing a point."""
        return math.floor(longitude / self.degrees), math.floor(latitude / self.degrees)

    def cell_id(self, column: int, row: int) -> str:
        """Return the cell identifier, which carries its own support so two rungs never collide."""
        return f"{self.support_id}:{column}:{row}"

    def bounds(self, column: int, row: int) -> tuple[float, float, float, float]:
        """Return the cell's (min_longitude, min_latitude, max_longitude, max_latitude)."""
        return (
            column * self.degrees,
            row * self.degrees,
            (column + 1) * self.degrees,
            (row + 1) * self.degrees,
        )

    def centroid(self, column: int, row: int) -> tuple[float, float]:
        """Return the cell centre. A CELL CENTRE IS NOT A COLLECTION LOCATION; it is where the cell is."""
        min_longitude, min_latitude, max_longitude, max_latitude = self.bounds(column, row)
        return ((min_longitude + max_longitude) / 2, (min_latitude + max_latitude) / 2)

    def polygon(self, column: int, row: int) -> bytes:
        """Return the cell's WKB polygon in WGS 84."""
        min_longitude, min_latitude, max_longitude, max_latitude = self.bounds(column, row)
        return polygon_wkb(
            (
                (min_longitude, min_latitude),
                (max_longitude, min_latitude),
                (max_longitude, max_latitude),
                (min_longitude, max_latitude),
            )
        )


def support_for(support_id: str) -> GridSupport:
    """Return the named support rung, or refuse a name this lane does not publish."""
    if support_id not in SUPPORT_DEGREES:
        raise SupportError(f"unknown support {support_id!r}; this lane publishes {sorted(SUPPORT_DEGREES)}")
    return GridSupport(support_id=support_id, degrees=SUPPORT_DEGREES[support_id])


@dataclass(frozen=True, slots=True)
class SpatialAssociation:
    """One occurrence's admitted relationship to one cell of one support."""

    occurrence_id: str
    support_id: str
    cell_id: str
    membership: str
    distance_semantics: str


def _uncertainty_degrees(uncertainty_meters: float, latitude: float) -> tuple[float, float]:
    latitude_degrees = uncertainty_meters / _METERS_PER_DEGREE_LATITUDE
    scale = max(math.cos(math.radians(latitude)), 1e-6)
    return uncertainty_meters / (_METERS_PER_DEGREE_LATITUDE * scale), latitude_degrees


def associate_record(
    record: NormalizedOccurrence,
    support: GridSupport,
    *,
    max_fanout: int = MAX_FANOUT_CELLS,
) -> tuple[SpatialAssociation, ...]:
    """Place one record on one support: confirmed inside a cell, possible across the ones it touches.

    A record with no usable coordinates produces NO association at all -- it is counted as excluded
    by QC, never placed at a cell centroid, because placing it would manufacture a location the
    publisher declined to give. A generalized record is never `confirmed` even when its footprint
    lies inside one cell: the publisher already told us the point is not where it says it is.

    Past `max_fanout` the footprint stops being informative at this rung. The record keeps ONE
    `possible` association to the cell holding its nominal point, which makes that cell read
    `withheld_or_generalized_only` unless a real record confirms it -- evidence preserved, claim not.
    """
    if record.longitude is None or record.latitude is None or record.spatial_class in {"withheld", "nonspatial"}:
        return ()
    column, row = support.cell_indices(record.longitude, record.latitude)
    uncertainty = record.coordinate_uncertainty_m or 0.0
    longitude_span, latitude_span = _uncertainty_degrees(uncertainty, record.latitude)
    min_column, min_row = support.cell_indices(record.longitude - longitude_span, record.latitude - latitude_span)
    max_column, max_row = support.cell_indices(record.longitude + longitude_span, record.latitude + latitude_span)
    touched = [
        (touched_column, touched_row)
        for touched_column in range(min_column, max_column + 1)
        for touched_row in range(min_row, max_row + 1)
    ]
    if len(touched) > max_fanout:
        return (
            SpatialAssociation(
                occurrence_id=record.occurrence_id,
                support_id=support.support_id,
                cell_id=support.cell_id(column, row),
                membership="possible",
                distance_semantics=CENTROID_DISTANCE,
            ),
        )
    if len(touched) == 1 and record.spatial_class == "exact":
        return (
            SpatialAssociation(
                occurrence_id=record.occurrence_id,
                support_id=support.support_id,
                cell_id=support.cell_id(column, row),
                membership="confirmed",
                distance_semantics=POINT_DISTANCE,
            ),
        )
    return tuple(
        SpatialAssociation(
            occurrence_id=record.occurrence_id,
            support_id=support.support_id,
            cell_id=support.cell_id(touched_column, touched_row),
            membership="possible",
            distance_semantics=CENTROID_DISTANCE,
        )
        for touched_column, touched_row in touched
    )


@dataclass(frozen=True, slots=True)
class SupportEvaluation:
    """One cell's evidence state under one release set. Never an absence claim about the ground."""

    release_set_id: str
    support_id: str
    cell_id: str
    evaluation: str
    record_count: int
    documented_taxa: int
    event_estimate: int
    collection_count: int
    excluded_by_qc: int
    possible_only_records: int
    geom: bytes


@dataclass(frozen=True, slots=True)
class CellTaxonSummary:
    """One (cell, concept) pair's documented evidence. Sparse: no row means no documented record."""

    release_set_id: str
    support_id: str
    cell_id: str
    taxon_concept_id: str
    record_count: int
    event_estimate: int
    collection_count: int
    earliest_event: object | None
    latest_event: object | None


def _cell_indices_from_id(cell_id: str) -> tuple[int, int]:
    _, column, row = cell_id.rsplit(":", 2)
    return int(column), int(row)


def _event_key(record: NormalizedOccurrence) -> tuple[str, object, object]:
    """A collecting event, as far as the record can distinguish one: who, and over what interval."""
    return ((record.recorded_by or "").casefold(), record.event.start, record.event.end)


def evaluate_support(  # noqa: PLR0913 - release set, support, records, associations and envelope are five independent inputs
    records: Sequence[NormalizedOccurrence],
    associations: Sequence[SpatialAssociation],
    support: GridSupport,
    *,
    release_set_id: str,
    envelope: tuple[float, float, float, float] = DECLARED_ENVELOPE,
    include_evaluated_zero: bool = True,
) -> tuple[SupportEvaluation, ...]:
    """Evaluate every cell this generation can honestly speak about, and no others.

    `evaluated_zero` is materialised ONLY inside the declared envelope, because that is the only area
    where "we looked and found nothing admitted" is a statement this lane is entitled to make. Cells
    outside it are not written at all: the reader answers `outside_coverage` for them, which is the
    honest reading of a cell nobody evaluated. `not_evaluated` is therefore never written by this
    function -- a cell it computed was, by definition, evaluated -- and the word exists in the schema
    for a future partial-coverage generation to use.
    """
    by_occurrence = {record.occurrence_id: record for record in records}
    cells: dict[str, list[SpatialAssociation]] = {}
    for association in associations:
        cells.setdefault(association.cell_id, []).append(association)

    excluded_total = sum(1 for record in records if record.excluded_by_qc)
    evaluations: list[SupportEvaluation] = []
    for cell_id, cell_associations in sorted(cells.items()):
        column, row = _cell_indices_from_id(cell_id)
        confirmed = [
            by_occurrence[association.occurrence_id]
            for association in cell_associations
            if association.membership == "confirmed" and association.occurrence_id in by_occurrence
        ]
        possible_only = len(cell_associations) - len(confirmed)
        evaluations.append(
            SupportEvaluation(
                release_set_id=release_set_id,
                support_id=support.support_id,
                cell_id=cell_id,
                evaluation="documented" if confirmed else "withheld_or_generalized_only",
                record_count=len(confirmed),
                # Distinct concepts among CONFIRMED records only, recomputed from the exact set. A
                # richness summed from a finer rung would double-count a concept present in two cells.
                documented_taxa=len({record.taxon_concept_id for record in confirmed}),
                event_estimate=len({_event_key(record) for record in confirmed}),
                collection_count=len({record.collection_key for record in confirmed}),
                excluded_by_qc=excluded_total,
                possible_only_records=possible_only,
                geom=support.polygon(column, row),
            )
        )

    if include_evaluated_zero:
        evaluations.extend(_evaluated_zero_cells(support, envelope, set(cells), release_set_id, excluded_total))
    return tuple(evaluations)


def _evaluated_zero_cells(
    support: GridSupport,
    envelope: tuple[float, float, float, float],
    occupied: set[str],
    release_set_id: str,
    excluded_total: int,
) -> list[SupportEvaluation]:
    """Every envelope cell no admitted record reached: looked at, and empty of admitted evidence."""
    min_longitude, min_latitude, max_longitude, max_latitude = envelope
    first_column, first_row = support.cell_indices(min_longitude, min_latitude)
    last_column, last_row = support.cell_indices(max_longitude, max_latitude)
    zero_cells: list[SupportEvaluation] = []
    for column in range(first_column, last_column + 1):
        for row in range(first_row, last_row + 1):
            cell_id = support.cell_id(column, row)
            if cell_id in occupied:
                continue
            zero_cells.append(
                SupportEvaluation(
                    release_set_id=release_set_id,
                    support_id=support.support_id,
                    cell_id=cell_id,
                    evaluation="evaluated_zero",
                    record_count=0,
                    documented_taxa=0,
                    event_estimate=0,
                    collection_count=0,
                    excluded_by_qc=excluded_total,
                    possible_only_records=0,
                    geom=support.polygon(column, row),
                )
            )
    return zero_cells


def summarise_cell_taxa(
    records: Sequence[NormalizedOccurrence],
    associations: Sequence[SpatialAssociation],
    support: GridSupport,
    *,
    release_set_id: str,
) -> tuple[CellTaxonSummary, ...]:
    """Build the sparse (cell, concept) summary from CONFIRMED records only.

    Sparse by construction: a pair with no confirmed record produces no row. Nothing here zero-fills
    cells x taxa x days, and an absent row means "no documented record", never "the taxon is absent".
    """
    by_occurrence = {record.occurrence_id: record for record in records}
    grouped: dict[tuple[str, str], list[NormalizedOccurrence]] = {}
    for association in associations:
        if association.membership != "confirmed":
            continue
        record = by_occurrence.get(association.occurrence_id)
        if record is None:
            continue
        grouped.setdefault((association.cell_id, record.taxon_concept_id), []).append(record)
    summaries: list[CellTaxonSummary] = []
    for (cell_id, taxon_concept_id), group in sorted(grouped.items()):
        starts = [record.event.start for record in group if record.event.start is not None]
        ends = [record.event.end for record in group if record.event.end is not None]
        summaries.append(
            CellTaxonSummary(
                release_set_id=release_set_id,
                support_id=support.support_id,
                cell_id=cell_id,
                taxon_concept_id=taxon_concept_id,
                record_count=len(group),
                event_estimate=len({_event_key(record) for record in group}),
                collection_count=len({record.collection_key for record in group}),
                earliest_event=min(starts) if starts else None,
                latest_event=max(ends) if ends else None,
            )
        )
    return tuple(summaries)


def cell_within_envelope(
    support: GridSupport,
    cell_id: str,
    envelope: tuple[float, float, float, float] = DECLARED_ENVELOPE,
) -> bool:
    """Report whether a cell's centre lies inside the declared admitted envelope."""
    column, row = _cell_indices_from_id(cell_id)
    longitude, latitude = support.centroid(column, row)
    return within_declared_envelope(longitude, latitude, envelope)


__all__ = [
    "CENTROID_DISTANCE",
    "MAX_FANOUT_CELLS",
    "POINT_DISTANCE",
    "SUPPORT_DEGREES",
    "CellTaxonSummary",
    "GridSupport",
    "SpatialAssociation",
    "SupportError",
    "SupportEvaluation",
    "associate_record",
    "cell_within_envelope",
    "evaluate_support",
    "summarise_cell_taxa",
    "support_for",
]

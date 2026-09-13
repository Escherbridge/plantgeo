"""Raw verbatim rows to normalized occurrences under one pinned taxonomy/QC recipe."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from agri_data_service.foundation.botanical_occurrences.coordinates import (
    DECLARED_ENVELOPE,
    classify_coordinate,
    point_wkb,
)
from agri_data_service.foundation.botanical_occurrences.event_interval import resolve_event_interval
from agri_data_service.foundation.botanical_occurrences.limits import (
    QC_POLICY_VERSION,
    TAXONOMY_RECIPE_VERSION,
)
from agri_data_service.foundation.botanical_occurrences.release_identity import occurrence_id

if TYPE_CHECKING:
    from agri_data_service.foundation.botanical_occurrences.event_interval import EventInterval
    from agri_data_service.pipeline.direct.botanical_occurrences.rows import SourceRow

#: A name-keyed authority table supplied in memory by the caller. `Sequence[str]` rather than `str`
#: because a name matching more than one concept is AMBIGUOUS and must stay ambiguous -- a homonym
#: collapsed by a name-only join is the exact failure the track spec forbids.
TaxonAuthority = Mapping[str, Sequence[str]]


@dataclass(frozen=True, slots=True)
class NormalizedOccurrence:
    """One raw revision under one recipe: what is claimable about it, and why."""

    occurrence_id: str
    collection_key: str
    release_key: str
    source_record_key: str
    taxon_concept_id: str
    taxonomy_recipe_version: str
    resolution_state: str
    scientific_name: str | None
    family: str | None
    event: EventInterval
    longitude: float | None
    latitude: float | None
    coordinate_uncertainty_m: float | None
    spatial_class: str
    qc_policy_version: str
    qc_reasons: tuple[str, ...]
    within_envelope: bool
    geom: bytes | None
    catalog_number: str | None
    recorded_by: str | None
    basis_of_record: str | None
    rights_uri: str | None
    attribution_text: str | None

    @property
    def excluded_by_qc(self) -> bool:
        """True when this record cannot contribute a point to any support cell.

        Withheld and nonspatial records are counted, never discarded: a specimen whose location the
        publisher suppressed is still evidence that the collection event happened, and dropping it
        would understate the collection.
        """
        return self.spatial_class in {"withheld", "nonspatial"}


def _blank_to_none(value: str | None) -> str | None:
    return value.strip() if value and value.strip() else None


def source_record_key(row: SourceRow, collection_key: str) -> str:
    """The publisher's native key: `occurrenceID`, else `catalogNumber`, else the source locator.

    The locator fallback is NOT an identifier the publisher would recognise, and a record that needs
    it is flagged `no_native_record_key` so nothing downstream reads it as one. It exists because a
    keyless row is still a documented specimen, and the alternative -- dropping it -- would silently
    shrink the population a reconciliation is supposed to match.
    """
    for candidate in (row.values.get("occurrenceID"), row.values.get("catalogNumber"), row.record_id):
        cleaned = _blank_to_none(candidate)
        if cleaned:
            return cleaned
    return f"{collection_key}#{row.member_name}:{row.row_number}"


def _resolve_taxon(
    scientific_name: str | None,
    *,
    collection_key: str,
    authority: TaxonAuthority | None,
) -> tuple[str, str]:
    """Bind a name to a concept, or keep it honestly unbound. Never a fuzzy match, never a guess."""
    name = _blank_to_none(scientific_name)
    if name is None:
        digest = hashlib.sha256(b"").hexdigest()
        return f"source:{collection_key}:{digest}", "unmatched"
    concepts = tuple(authority.get(name, ())) if authority else ()
    if len(concepts) == 1:
        return concepts[0], "resolved"
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()
    source_concept = f"source:{collection_key}:{digest}"
    # More than one concept for one string is a homonym or a disputed name: it stays ambiguous under
    # a source-local key rather than being assigned to whichever authority row sorted first.
    return source_concept, "ambiguous" if len(concepts) > 1 else "unmatched"


def normalize_row(  # noqa: PLR0913 - one release-level binding per argument; none may be defaulted away
    row: SourceRow,
    *,
    collection_key: str,
    release_key: str,
    rights_uri: str | None = None,
    attribution_text: str | None = None,
    authority: TaxonAuthority | None = None,
    envelope: tuple[float, float, float, float] = DECLARED_ENVELOPE,
) -> NormalizedOccurrence:
    """Apply the pinned recipe to one raw row, recording every reason the outcome is what it is."""
    native_key = source_record_key(row, collection_key)
    coordinate = classify_coordinate(
        decimal_longitude=row.values.get("decimalLongitude"),
        decimal_latitude=row.values.get("decimalLatitude"),
        geodetic_datum=row.values.get("geodeticDatum"),
        coordinate_uncertainty=row.values.get("coordinateUncertaintyInMeters"),
        information_withheld=row.values.get("informationWithheld"),
        data_generalizations=row.values.get("dataGeneralizations"),
        envelope=envelope,
    )
    event = resolve_event_interval(
        row.values.get("eventDate"),
        year=row.values.get("year"),
        month=row.values.get("month"),
        day=row.values.get("day"),
    )
    scientific_name = _blank_to_none(row.values.get("scientificName"))
    taxon_concept_id, resolution_state = _resolve_taxon(
        scientific_name, collection_key=collection_key, authority=authority
    )
    reasons = list(coordinate.reasons)
    if event.precision == "unknown":
        reasons.append("event_date_unknown")
    elif event.precision != "day":
        reasons.append(f"event_date_{event.precision}_precision")
    if resolution_state != "resolved":
        reasons.append(f"taxon_{resolution_state}")
    if not _blank_to_none(row.values.get("occurrenceID")) and not _blank_to_none(row.values.get("catalogNumber")):
        reasons.append("no_native_record_key")
    geom = (
        point_wkb(coordinate.longitude, coordinate.latitude)
        if coordinate.longitude is not None and coordinate.latitude is not None
        else None
    )
    return NormalizedOccurrence(
        occurrence_id=occurrence_id(collection_key, native_key, row.row_sha256),
        collection_key=collection_key,
        release_key=release_key,
        source_record_key=native_key,
        taxon_concept_id=taxon_concept_id,
        taxonomy_recipe_version=TAXONOMY_RECIPE_VERSION,
        resolution_state=resolution_state,
        scientific_name=scientific_name,
        family=_blank_to_none(row.values.get("family")),
        event=event,
        longitude=coordinate.longitude,
        latitude=coordinate.latitude,
        coordinate_uncertainty_m=coordinate.uncertainty_meters,
        spatial_class=coordinate.spatial_class,
        qc_policy_version=QC_POLICY_VERSION,
        qc_reasons=tuple(reasons),
        within_envelope=coordinate.within_envelope,
        geom=geom,
        catalog_number=_blank_to_none(row.values.get("catalogNumber")),
        recorded_by=_blank_to_none(row.values.get("recordedBy")),
        basis_of_record=_blank_to_none(row.values.get("basisOfRecord")),
        rights_uri=rights_uri,
        attribution_text=attribution_text,
    )


def mark_native_duplicates(records: Sequence[NormalizedOccurrence]) -> tuple[NormalizedOccurrence, ...]:
    """Record native-key collisions as reasons on EVERY colliding row; delete nothing.

    Native identity removes duplicate DOWNLOADS of one record, not duplicate SPECIMENS. Two sheets
    from one collecting event legitimately share a collector, a date and a locality, and the spec is
    explicit that name/location/date coincidence is not a deletion rule. So a repeated native key
    within one release is annotated -- `duplicate_native_key`, plus whether the CONTENT also matched
    -- and both rows survive for a reviewer to adjudicate.
    """
    groups: dict[tuple[str, str], list[NormalizedOccurrence]] = {}
    for record in records:
        groups.setdefault((record.collection_key, record.source_record_key), []).append(record)
    annotated: list[NormalizedOccurrence] = []
    for record in records:
        group = groups[(record.collection_key, record.source_record_key)]
        if len(group) == 1:
            annotated.append(record)
            continue
        identical_content = len({member.occurrence_id for member in group}) == 1
        annotated.append(
            replace(
                record,
                qc_reasons=(
                    *record.qc_reasons,
                    f"duplicate_native_key:{record.source_record_key}",
                    "duplicate_content_identical" if identical_content else "duplicate_content_differs",
                ),
            )
        )
    return tuple(annotated)


def normalize_rows(  # noqa: PLR0913 - mirrors `normalize_row`; the release bindings travel together
    rows: Sequence[SourceRow],
    *,
    collection_key: str,
    release_key: str,
    rights_uri: str | None = None,
    attribution_text: str | None = None,
    authority: TaxonAuthority | None = None,
    envelope: tuple[float, float, float, float] = DECLARED_ENVELOPE,
) -> tuple[NormalizedOccurrence, ...]:
    """Normalize a whole member, then annotate native-key collisions across it."""
    normalized = [
        normalize_row(
            row,
            collection_key=collection_key,
            release_key=release_key,
            rights_uri=rights_uri,
            attribution_text=attribution_text,
            authority=authority,
            envelope=envelope,
        )
        for row in rows
    ]
    return mark_native_duplicates(normalized)


__all__ = [
    "NormalizedOccurrence",
    "TaxonAuthority",
    "mark_native_duplicates",
    "normalize_row",
    "normalize_rows",
    "source_record_key",
]

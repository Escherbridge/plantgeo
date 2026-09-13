"""Event intervals, coordinate classification and duplicate annotation, read as three tables."""

# ruff: noqa: PLR2004 - the small literal counts ARE the assertion; naming each one hides it.

from __future__ import annotations

from datetime import date

import pytest

from agri_data_service.foundation.botanical_occurrences.coordinates import classify_coordinate
from agri_data_service.foundation.botanical_occurrences.event_interval import (
    parse_event_parts,
    resolve_event_interval,
)
from agri_data_service.pipeline.direct.botanical_occurrences.normalize import normalize_rows
from agri_data_service.pipeline.direct.botanical_occurrences.rows import SourceRow


@pytest.mark.parametrize(
    ("raw", "expected_start", "expected_end", "precision"),
    [
        ("1987-06-15", date(1987, 6, 15), date(1987, 6, 15), "day"),
        ("1987-06", date(1987, 6, 1), date(1987, 6, 30), "month"),
        ("1987", date(1987, 1, 1), date(1987, 12, 31), "year"),
        ("1987-02", date(1987, 2, 1), date(1987, 2, 28), "month"),
        ("1988-02", date(1988, 2, 1), date(1988, 2, 29), "month"),
        ("1987-06-01/1987-06-15", date(1987, 6, 1), date(1987, 6, 15), "interval"),
        ("1987-06-01/1987-06-01", date(1987, 6, 1), date(1987, 6, 1), "day"),
        ("", None, None, "unknown"),
        ("not a date", None, None, "unknown"),
        ("1987-06-31", None, None, "unknown"),
    ],
)
def test_event_date_parsing_never_invents_a_day(
    raw: str, expected_start: date | None, expected_end: date | None, precision: str
) -> None:
    parsed = resolve_event_interval(raw)
    assert (parsed.start, parsed.end, parsed.precision) == (expected_start, expected_end, precision)


def test_a_year_spans_its_whole_year_rather_than_its_first_day() -> None:
    parsed = resolve_event_interval("1913")
    assert parsed.start == date(1913, 1, 1)
    assert parsed.end == date(1913, 12, 31)
    assert parsed.precision == "year"


def test_the_separate_parts_are_only_a_fallback_and_widen_on_every_gap() -> None:
    assert parse_event_parts("1987", "6", None).precision == "month"
    assert parse_event_parts("1987", None, None).precision == "year"
    assert parse_event_parts(None, "6", "15").precision == "unknown", "a month with no year is not a date"
    assert resolve_event_interval("1987-06-15", year="1990").start == date(1987, 6, 15)


def test_an_unknown_interval_overlaps_nothing() -> None:
    unknown = resolve_event_interval(None)
    assert unknown.overlaps(date(1900, 1, 1), date(2100, 1, 1)) is False


def test_interval_overlap_is_reported_as_overlap() -> None:
    parsed = resolve_event_interval("1987-06-01/1987-06-15")
    assert parsed.overlaps(date(1987, 6, 10), date(1987, 7, 1)) is True
    assert parsed.overlaps(date(1987, 7, 1), date(1987, 8, 1)) is False


@pytest.mark.parametrize(
    ("longitude", "latitude", "uncertainty", "withheld", "generalized", "datum", "expected"),
    [
        ("-122.3", "47.6", "30", "", "", "WGS84", "exact"),
        ("-122.3", "47.6", "25000", "", "", "WGS84", "generalized"),
        ("-122.3", "47.6", "", "", "Coordinates generalized to 10km", "WGS84", "generalized"),
        ("-122.3", "47.6", "30", "", "", "NAD27", "generalized"),
        ("-122.3", "47.6", "30", "Coordinates withheld", "", "WGS84", "withheld"),
        ("", "", "", "", "", "", "nonspatial"),
        ("0", "0", "", "", "", "WGS84", "nonspatial"),
        ("-400", "47.6", "", "", "", "WGS84", "nonspatial"),
    ],
)
def test_coordinate_classification_table(  # noqa: PLR0913 - one DwC term per parametrised column
    longitude: str, latitude: str, uncertainty: str, withheld: str, generalized: str, datum: str, expected: str
) -> None:
    classified = classify_coordinate(
        decimal_longitude=longitude,
        decimal_latitude=latitude,
        coordinate_uncertainty=uncertainty,
        information_withheld=withheld,
        data_generalizations=generalized,
        geodetic_datum=datum,
    )
    assert classified.spatial_class == expected


def test_a_withheld_record_keeps_no_coordinates_at_all() -> None:
    """A suppressed location must not survive as a county centroid a reader could plot."""
    classified = classify_coordinate(
        decimal_longitude="-122.9",
        decimal_latitude="47.1",
        information_withheld="Coordinates withheld for a sensitive taxon",
    )
    assert classified.spatial_class == "withheld"
    assert classified.longitude is None
    assert classified.latitude is None


def _row(number: int, occurrence_id: str, name: str = "Lupinus argenteus", *, content: str = "A") -> SourceRow:
    values = {"occurrenceID": occurrence_id, "scientificName": name, "recordedBy": content}
    return SourceRow(
        member_name="occurrence.txt",
        row_number=number,
        row_sha256=f"hash-{content}-{number}",
        record_id=occurrence_id,
        values=values,
        verbatim=dict(values),
    )


def test_duplicate_native_keys_keep_both_rows_and_say_why() -> None:
    records = normalize_rows(
        [_row(1, "urn:occ:1", content="A"), _row(2, "urn:occ:1", content="B"), _row(3, "urn:occ:2")],
        collection_key="test:COLL:vascular",
        release_key="release-1",
    )
    assert len(records) == 3, "a duplicate native key annotates; it never deletes a specimen"
    duplicates = [record for record in records if any("duplicate_native_key" in reason for reason in record.qc_reasons)]
    assert len(duplicates) == 2
    assert all("duplicate_content_differs" in record.qc_reasons for record in duplicates)


def test_an_unresolved_name_keeps_a_source_local_concept_and_says_it_is_unmatched() -> None:
    (record,) = normalize_rows([_row(1, "urn:occ:9")], collection_key="test:COLL:vascular", release_key="release-1")
    assert record.resolution_state == "unmatched"
    assert record.taxon_concept_id.startswith("source:test:COLL:vascular:")
    assert "taxon_unmatched" in record.qc_reasons


def test_a_name_matching_two_authority_concepts_stays_ambiguous() -> None:
    (record,) = normalize_rows(
        [_row(1, "urn:occ:9")],
        collection_key="test:COLL:vascular",
        release_key="release-1",
        authority={"Lupinus argenteus": ("concept:a", "concept:b")},
    )
    assert record.resolution_state == "ambiguous"
    assert record.taxon_concept_id.startswith("source:"), "a homonym is not assigned to whichever sorted first"


def test_an_exact_authority_match_resolves() -> None:
    (record,) = normalize_rows(
        [_row(1, "urn:occ:9")],
        collection_key="test:COLL:vascular",
        release_key="release-1",
        authority={"Lupinus argenteus": ("concept:a",)},
    )
    assert record.resolution_state == "resolved"
    assert record.taxon_concept_id == "concept:a"


def _atomized_row(number: int, occurrence_id: str, **name_parts: str) -> SourceRow:
    values = {"occurrenceID": occurrence_id, **name_parts}
    return SourceRow(
        member_name="occurrence.txt",
        row_number=number,
        row_sha256=f"hash-atomized-{number}",
        record_id=occurrence_id,
        values=values,
        verbatim=dict(values),
    )


def test_atomized_genus_and_species_join_when_no_combined_field_is_exported() -> None:
    """UBC's occurrence.txt exports genus/specificEpithet, never a combined scientificName column."""
    row = _atomized_row(1, "urn:occ:atomized", genus="Lupinus", specificEpithet="argenteus")
    (record,) = normalize_rows([row], collection_key="test:COLL:vascular", release_key="release-1")
    assert record.scientific_name == "Lupinus argenteus"


def test_a_combined_field_wins_over_atomized_parts_when_both_are_present() -> None:
    row = _atomized_row(
        1, "urn:occ:combined", scientificName="Lupinus argenteus Pursh", genus="Lupinus", specificEpithet="argenteus"
    )
    (record,) = normalize_rows([row], collection_key="test:COLL:vascular", release_key="release-1")
    assert record.scientific_name == "Lupinus argenteus Pursh"


def test_a_genus_only_determination_is_not_padded_with_an_invented_species() -> None:
    row = _atomized_row(1, "urn:occ:genus-only", genus="Lupinus")
    (record,) = normalize_rows([row], collection_key="test:COLL:vascular", release_key="release-1")
    assert record.scientific_name == "Lupinus"


def test_an_infraspecific_epithet_joins_with_its_rank() -> None:
    row = _atomized_row(
        1,
        "urn:occ:infraspecific",
        genus="Lupinus",
        specificEpithet="argenteus",
        infraspecificEpithet="argophyllus",
        taxonRank="variety",
    )
    (record,) = normalize_rows([row], collection_key="test:COLL:vascular", release_key="release-1")
    assert record.scientific_name == "Lupinus argenteus variety argophyllus"


def test_no_genus_and_no_combined_field_stays_unmatched_with_no_name() -> None:
    row = _atomized_row(1, "urn:occ:blank", recordedBy="A. Botanist")
    (record,) = normalize_rows([row], collection_key="test:COLL:vascular", release_key="release-1")
    assert record.scientific_name is None
    assert record.resolution_state == "unmatched"


def test_two_unnamed_records_in_one_collection_get_distinct_taxon_concept_ids() -> None:
    """Regression: `_resolve_taxon` once hashed a constant for a missing name, so every
    unidentified specimen in a collection collapsed onto the same `taxon_concept_id` and
    silently undercounted `documented_taxa` for any cell holding more than one of them."""
    first = _atomized_row(1, "urn:occ:blank-1", recordedBy="A. Botanist")
    second = _atomized_row(2, "urn:occ:blank-2", recordedBy="A. Botanist")
    first_record, second_record = normalize_rows(
        [first, second], collection_key="test:COLL:vascular", release_key="release-1"
    )
    assert first_record.resolution_state == "unmatched"
    assert second_record.resolution_state == "unmatched"
    assert first_record.taxon_concept_id != second_record.taxon_concept_id


def test_a_keyless_row_is_kept_under_a_locator_key_and_flagged() -> None:
    keyless = SourceRow("occurrence.txt", 7, "hash", "", {"scientificName": "Carex sp."}, {})
    (record,) = normalize_rows([keyless], collection_key="test:COLL:vascular", release_key="release-1")
    assert "no_native_record_key" in record.qc_reasons
    assert record.source_record_key.endswith("occurrence.txt:7")

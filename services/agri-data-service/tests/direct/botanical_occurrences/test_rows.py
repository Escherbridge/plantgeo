"""Streaming row reads: verbatim values, source locators, content hashes and the partial cap."""

# ruff: noqa: PLR2004 - the small literal counts ARE the assertion; naming each one hides it.

from __future__ import annotations

from typing import TYPE_CHECKING

from agri_data_service.pipeline.direct.botanical_occurrences.archive_descriptor import parse_meta_descriptor
from agri_data_service.pipeline.direct.botanical_occurrences.quarantine import read_member_bytes
from agri_data_service.pipeline.direct.botanical_occurrences.rows import read_member
from tests.direct.botanical_occurrences.conftest import CORE_COLUMNS, DEFAULT_RECORDS, delimited

if TYPE_CHECKING:
    from pathlib import Path


def _core_descriptor(archive: Path):  # noqa: ANN202 - a test helper, typed at the call site
    return parse_meta_descriptor(read_member_bytes(archive, "meta.xml")).core


def test_every_data_row_is_read_with_its_locator(valid_archive: Path) -> None:
    rows, result = read_member(valid_archive, _core_descriptor(valid_archive), max_rows=1000)
    assert result.rows_read == 4
    assert result.outcome == "complete"
    assert [row.row_number for row in rows] == [1, 2, 3, 4]
    assert {row.member_name for row in rows} == {"occurrence.txt"}


def test_the_header_line_is_skipped_rather_than_read_as_a_record(valid_archive: Path) -> None:
    rows, _ = read_member(valid_archive, _core_descriptor(valid_archive), max_rows=1000)
    assert rows[0].values["occurrenceID"] == "urn:occ:1"
    assert rows[0].row_number == 1, "row numbers count DATA rows, so they do not shift with the header"


def test_verbatim_keeps_every_column_under_its_term_uri(valid_archive: Path) -> None:
    rows, _ = read_member(valid_archive, _core_descriptor(valid_archive), max_rows=1000)
    verbatim = rows[0].verbatim
    assert "http://rs.tdwg.org/dwc/terms/scientificName" in verbatim
    assert verbatim["http://rs.tdwg.org/dwc/terms/scientificName"] == "Lupinus argenteus"


def test_the_row_hash_changes_with_content_and_only_with_content(valid_archive: Path, archive_factory) -> None:  # noqa: ANN001
    rows, _ = read_member(valid_archive, _core_descriptor(valid_archive), max_rows=1000)
    edited = list(DEFAULT_RECORDS)
    edited[0] = (*edited[0][:3], "Renamed Collector", *edited[0][4:])
    changed_archive = archive_factory("changed.zip", {"occurrence.txt": delimited(CORE_COLUMNS, edited)})
    changed_rows, _ = read_member(changed_archive, _core_descriptor(changed_archive), max_rows=1000)
    assert changed_rows[0].row_sha256 != rows[0].row_sha256
    assert changed_rows[1].row_sha256 == rows[1].row_sha256


def test_the_row_cap_truncates_and_says_so(valid_archive: Path) -> None:
    """A cap produces `partial`, which is a reportable outcome, not a silently shortened population."""
    rows, result = read_member(valid_archive, _core_descriptor(valid_archive), max_rows=2)
    assert len(rows) == 2
    assert result.truncated is True
    assert result.outcome == "partial"


def test_a_cap_equal_to_the_row_count_is_not_truncation(valid_archive: Path) -> None:
    _, result = read_member(valid_archive, _core_descriptor(valid_archive), max_rows=4)
    assert result.truncated is False
    assert result.outcome == "complete"


def test_the_extension_rows_are_read_with_their_core_id(valid_archive: Path) -> None:
    descriptor = parse_meta_descriptor(read_member_bytes(valid_archive, "meta.xml"))
    rows, result = read_member(valid_archive, descriptor.extensions[0], max_rows=1000)
    assert result.rows_read == 2
    assert {row.record_id for row in rows} == {"urn:occ:1"}
    assert [row.values["identifiedBy"] for row in rows] == ["E. Determiner", "F. Determiner"]

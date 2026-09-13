"""Every archive-safety control, exercised against an archive built to trip exactly one of them."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

from agri_data_service.foundation.botanical_occurrences.limits import ADMITTED_LIMITS
from agri_data_service.pipeline.direct.botanical_occurrences.archive_descriptor import (
    ArchiveDescriptorError,
    parse_eml_facts,
    parse_meta_descriptor,
)
from agri_data_service.pipeline.direct.botanical_occurrences.quarantine import (
    inspect_archive,
    read_member_bytes,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_a_valid_archive_is_accepted_with_measured_hashes(valid_archive: Path) -> None:
    receipt = inspect_archive(valid_archive)
    assert receipt.accepted, receipt.reasons
    assert receipt.reasons == ()
    assert len(receipt.archive_sha256) == 64
    assert receipt.meta_sha256 and receipt.eml_sha256
    assert {member.member_name for member in receipt.members} == {
        "meta.xml",
        "eml.xml",
        "occurrence.txt",
        "identification.txt",
    }


def test_every_member_crc_is_recomputed_not_trusted(valid_archive: Path) -> None:
    """The CRC in the receipt is measured while streaming, so a lying directory entry is visible."""
    receipt = inspect_archive(valid_archive)
    for member in receipt.members:
        assert member.measured_crc32 == member.declared_crc32
        assert member.uncompressed_bytes > 0


@pytest.mark.parametrize(
    ("fixture_name", "reason"),
    [
        ("traversal_archive", "member_path_traversal"),
        ("absolute_path_archive", "member_absolute_path"),
        ("casefold_duplicate_archive", "member_name_collision_after_normalisation"),
        ("nested_archive", "member_nested_archive"),
        ("encrypted_member_archive", "member_encrypted"),
        ("missing_meta_archive", "missing_required_member"),
        ("over_member_count_archive", "member_count_over_cap"),
        ("ratio_bomb_archive", "member_compression_ratio_over_cap"),
    ],
)
def test_each_hostile_archive_is_rejected_by_its_own_control(
    request: pytest.FixtureRequest, fixture_name: str, reason: str
) -> None:
    receipt = inspect_archive(request.getfixturevalue(fixture_name))
    assert not receipt.accepted
    assert reason in receipt.reasons, receipt.reasons


def test_a_file_that_is_not_a_zip_is_refused_without_being_opened(tmp_path: Path) -> None:
    plain = tmp_path / "not-an-archive.zip"
    plain.write_bytes(b"this is not a zip file")
    receipt = inspect_archive(plain)
    assert receipt.reasons == ("not_a_zip_archive",)
    assert receipt.members == ()


def test_an_archive_over_the_per_archive_byte_cap_is_refused(valid_archive: Path) -> None:
    tiny = replace(ADMITTED_LIMITS, compressed_bytes_per_archive=16)
    receipt = inspect_archive(valid_archive, tiny)
    assert "archive_over_compressed_byte_cap" in receipt.reasons


def test_a_doctype_in_meta_xml_is_refused_before_parsing(doctype_meta_archive: Path) -> None:
    """The refusal comes from the byte scan, so it does not depend on `defusedxml` being installed."""
    payload = read_member_bytes(doctype_meta_archive, "meta.xml")
    with pytest.raises(ArchiveDescriptorError, match="doctype_declaration"):
        parse_meta_descriptor(payload)


def test_the_descriptor_reads_the_core_and_its_one_supported_extension(valid_archive: Path) -> None:
    descriptor = parse_meta_descriptor(read_member_bytes(valid_archive, "meta.xml"))
    assert descriptor.core.member_name == "occurrence.txt"
    assert descriptor.core.ignore_header_lines == 1
    assert descriptor.core.fields_terminated_by == "\t"
    assert descriptor.core.fields[0] == "occurrenceID"
    assert [extension.member_name for extension in descriptor.extensions] == ["identification.txt"]
    assert descriptor.unread_row_types == ()


def test_a_non_occurrence_core_is_refused_rather_than_read_as_specimens() -> None:
    checklist = (
        b'<?xml version="1.0"?><archive xmlns="http://rs.tdwg.org/dwc/text/">'
        b'<core rowType="http://rs.tdwg.org/dwc/terms/Taxon"><files><location>taxon.txt</location></files>'
        b'<id index="0"/></core></archive>'
    )
    with pytest.raises(ArchiveDescriptorError, match="not an occurrence core"):
        parse_meta_descriptor(checklist)


def test_eml_facts_carry_the_package_id_and_licence(valid_archive: Path) -> None:
    facts = parse_eml_facts(read_member_bytes(valid_archive, "eml.xml"))
    assert facts.package_id == "test-collection/v1.0"
    assert facts.pub_date == "2026-09-01"
    assert facts.rights_uri and "creativecommons.org" in facts.rights_uri
    assert facts.publisher == "Test Herbarium"

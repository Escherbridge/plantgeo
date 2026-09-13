"""Synthetic Darwin Core archives, valid and hostile, built in `tmp_path`.

No fixture here downloads anything. Every archive is assembled byte by byte so a test can name the
exact control it exercises, and so the hostile variants exist in this repository rather than in a
quarantine directory nobody may read.
"""

from __future__ import annotations

import zipfile
from typing import TYPE_CHECKING, Final

import pytest

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

DWC: Final = "http://rs.tdwg.org/dwc/terms/"

#: The core columns every synthetic occurrence file carries, in index order.
CORE_COLUMNS: Final[tuple[str, ...]] = (
    "occurrenceID",
    "catalogNumber",
    "basisOfRecord",
    "recordedBy",
    "eventDate",
    "year",
    "month",
    "day",
    "scientificName",
    "family",
    "decimalLatitude",
    "decimalLongitude",
    "geodeticDatum",
    "coordinateUncertaintyInMeters",
    "informationWithheld",
    "dataGeneralizations",
)

IDENTIFICATION_COLUMNS: Final[tuple[str, ...]] = ("occurrenceID", "identifiedBy", "dateIdentified", "scientificName")


def _field_lines(columns: Sequence[str]) -> str:
    """Render one `<field index=... term=.../>` line per column, joined for embedding in `META_XML`."""
    return "".join(f'    <field index="{index}" term="{DWC}{column}"/>\n' for index, column in enumerate(columns))


_CORE_FIELD_LINES: Final = _field_lines(CORE_COLUMNS)
_IDENTIFICATION_FIELD_LINES: Final = _field_lines(IDENTIFICATION_COLUMNS)
_ROW_TYPE_ATTRIBUTES: Final = 'fieldsTerminatedBy="\\t" linesTerminatedBy="\\n" encoding="UTF-8" ignoreHeaderLines="1"'

META_XML: Final = f"""<?xml version="1.0" encoding="UTF-8"?>
<archive xmlns="http://rs.tdwg.org/dwc/text/">
  <core rowType="{DWC}Occurrence" {_ROW_TYPE_ATTRIBUTES}>
    <files><location>occurrence.txt</location></files>
    <id index="0"/>
{_CORE_FIELD_LINES}  </core>
  <extension rowType="{DWC}Identification" {_ROW_TYPE_ATTRIBUTES}>
    <files><location>identification.txt</location></files>
    <coreid index="0"/>
{_IDENTIFICATION_FIELD_LINES}  </extension>
</archive>
"""

EML_XML: Final = """<?xml version="1.0" encoding="UTF-8"?>
<eml:eml xmlns:eml="eml://ecoinformatics.org/eml-2.1.1" packageId="test-collection/v1.0">
  <dataset>
    <title>Synthetic Vascular Specimens</title>
    <pubDate>2026-09-01</pubDate>
    <creator><organizationName>Test Herbarium</organizationName></creator>
    <abstract><para>Coordinates are published at full precision except where withheld.</para></abstract>
    <intellectualRights><para>Released under
      <ulink url="https://creativecommons.org/publicdomain/zero/1.0/legalcode"><citetitle>CC0 1.0</citetitle></ulink>
    </para></intellectualRights>
  </dataset>
</eml:eml>
"""

#: Four records exercising the four spatial classes and three event precisions.
DEFAULT_RECORDS: Final[tuple[tuple[str, ...], ...]] = (
    (
        "urn:occ:1",
        "WTU-1",
        "PreservedSpecimen",
        "A. Collector",
        "1987-06-15",
        "1987",
        "6",
        "15",
        "Lupinus argenteus",
        "Fabaceae",
        "47.6",
        "-122.3",
        "WGS84",
        "30",
        "",
        "",
    ),
    (
        "urn:occ:2",
        "WTU-2",
        "PreservedSpecimen",
        "B. Collector",
        "1987-06",
        "1987",
        "6",
        "",
        "Lupinus argenteus",
        "Fabaceae",
        "47.65",
        "-122.35",
        "WGS84",
        "25000",
        "",
        "Coordinates generalized",
    ),
    (
        "urn:occ:3",
        "WTU-3",
        "PreservedSpecimen",
        "C. Collector",
        "1913",
        "1913",
        "",
        "",
        "Camassia quamash",
        "Asparagaceae",
        "47.1",
        "-122.9",
        "WGS84",
        "",
        "Coordinates withheld",
        "",
    ),
    (
        "urn:occ:4",
        "WTU-4",
        "PreservedSpecimen",
        "D. Collector",
        "",
        "",
        "",
        "",
        "Camassia quamash",
        "Asparagaceae",
        "",
        "",
        "",
        "",
        "",
        "",
    ),
)

DEFAULT_IDENTIFICATIONS: Final[tuple[tuple[str, ...], ...]] = (
    ("urn:occ:1", "E. Determiner", "1990-02-01", "Lupinus argenteus"),
    ("urn:occ:1", "F. Determiner", "2019-05-04", "Lupinus argenteus var. argenteus"),
)


def delimited(header: Sequence[str], rows: Sequence[Sequence[str]]) -> bytes:
    """Render a tab-delimited member with one header line, the way a real DwC-A ships it."""
    lines = ["\t".join(header), *["\t".join(row) for row in rows]]
    return ("\n".join(lines) + "\n").encode("utf-8")


def write_archive(path: Path, members: Mapping[str, bytes]) -> Path:
    """Write a plain archive from an exact member map."""
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)
    return path


def default_members(
    records: Sequence[Sequence[str]] = DEFAULT_RECORDS,
    identifications: Sequence[Sequence[str]] = DEFAULT_IDENTIFICATIONS,
) -> dict[str, bytes]:
    """The four members a valid archive holds."""
    return {
        "meta.xml": META_XML.encode("utf-8"),
        "eml.xml": EML_XML.encode("utf-8"),
        "occurrence.txt": delimited(CORE_COLUMNS, records),
        "identification.txt": delimited(IDENTIFICATION_COLUMNS, identifications),
    }


@pytest.fixture
def valid_archive(tmp_path: Path) -> Path:
    """One archive that passes every control, with four records and two determinations."""
    return write_archive(tmp_path / "valid.zip", default_members())


@pytest.fixture
def archive_factory(tmp_path: Path):  # noqa: ANN201 - a pytest factory fixture, typed at the call site
    """Build an archive with member overrides, for the hostile variants."""

    def build(name: str, overrides: Mapping[str, bytes] | None = None, drop: Sequence[str] = ()) -> Path:
        members = default_members()
        for member in drop:
            members.pop(member, None)
        members.update(overrides or {})
        return write_archive(tmp_path / name, members)

    return build


@pytest.fixture
def traversal_archive(archive_factory) -> Path:  # noqa: ANN001 - the factory fixture above
    """A member whose name climbs out of the directory it would be read into."""
    return archive_factory("traversal.zip", {"../escaped.txt": b"payload\n"})


@pytest.fixture
def absolute_path_archive(archive_factory) -> Path:  # noqa: ANN001
    return archive_factory("absolute.zip", {"/etc/passwd.txt": b"payload\n"})


@pytest.fixture
def casefold_duplicate_archive(archive_factory) -> Path:  # noqa: ANN001
    """Two members that are distinct in the zip and identical on a case-insensitive filesystem."""
    return archive_factory("casefold.zip", {"Occurrence.TXT": b"payload\n"})


@pytest.fixture
def nested_archive(archive_factory, tmp_path: Path) -> Path:  # noqa: ANN001
    inner = write_archive(tmp_path / "inner.zip", {"payload.txt": b"payload\n"})
    return archive_factory("nested.zip", {"inner.zip": inner.read_bytes()})


@pytest.fixture
def encrypted_member_archive(tmp_path: Path) -> Path:
    """An archive whose directory marks one member encrypted, which this lane will not open.

    `zipfile.ZipFile._open_to_write` unconditionally resets `zinfo.flag_bits = 0x00` before
    writing (CPython `zipfile.py`), so setting the bit on a `ZipInfo` passed to `writestr` is
    silently discarded -- the only way to produce a fixture with the encryption bit actually set
    is to flip it in the written bytes afterward, in both the local file header and the central
    directory record for that member (general-purpose bit flag, bit 0, at byte offset 6 of the
    local header and offset 8 of the central directory header, per the ZIP spec).
    """
    path = tmp_path / "encrypted.zip"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in default_members().items():
            archive.writestr(name, payload)
    with zipfile.ZipFile(path) as archive:
        target_info = archive.getinfo("occurrence.txt")
        local_header_offset = target_info.header_offset
        central_directory_offset = archive.start_dir

    raw = bytearray(path.read_bytes())
    local_flag_offset = local_header_offset + 6
    raw[local_flag_offset] |= 0x1

    cursor = central_directory_offset
    while raw[cursor : cursor + 4] == b"PK\x01\x02":
        filename_length = int.from_bytes(raw[cursor + 28 : cursor + 30], "little")
        extra_length = int.from_bytes(raw[cursor + 30 : cursor + 32], "little")
        comment_length = int.from_bytes(raw[cursor + 32 : cursor + 34], "little")
        name = bytes(raw[cursor + 46 : cursor + 46 + filename_length]).decode("utf-8")
        if name == "occurrence.txt":
            raw[cursor + 8] |= 0x1
            break
        cursor += 46 + filename_length + extra_length + comment_length
    else:
        raise AssertionError("central directory record for occurrence.txt was not found")

    path.write_bytes(bytes(raw))
    return path


@pytest.fixture
def doctype_meta_archive(archive_factory) -> Path:  # noqa: ANN001
    """A `meta.xml` carrying a DOCTYPE, which the byte scan refuses before any parser sees it."""
    hostile = b'<?xml version="1.0"?>\n<!DOCTYPE archive [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>\n<archive/>\n'
    return archive_factory("doctype.zip", {"meta.xml": hostile})


@pytest.fixture
def missing_meta_archive(archive_factory) -> Path:  # noqa: ANN001
    return archive_factory("no-meta.zip", drop=("meta.xml",))


@pytest.fixture
def over_member_count_archive(archive_factory) -> Path:  # noqa: ANN001
    """More members than the admitted ceiling of thirty-two."""
    padding = {f"pad-{index:03d}.txt": b"padding\n" for index in range(40)}
    return archive_factory("many-members.zip", padding)


@pytest.fixture
def ratio_bomb_archive(archive_factory) -> Path:  # noqa: ANN001
    """One highly compressible member whose expansion ratio exceeds the guard."""
    return archive_factory("ratio-bomb.zip", {"bomb.txt": b"\0" * (8 * 1024 * 1024)})

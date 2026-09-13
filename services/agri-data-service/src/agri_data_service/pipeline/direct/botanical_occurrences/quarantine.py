"""Archive safety: stream one DwC-A inside the admitted envelope and refuse anything unsafe.

NOTHING IS EXTRACTED TO DISK. Every member is read through `ZipFile.open` in chunks, so a member
that turns out to be a bomb is refused at the chunk that proves it rather than after it has landed.
"""

from __future__ import annotations

import hashlib
import posixpath
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final

from agri_data_service.foundation.botanical_occurrences.limits import ADMITTED_LIMITS

if TYPE_CHECKING:
    from agri_data_service.foundation.botanical_occurrences.limits import AcquisitionLimits

#: Read granularity. Small enough that a decompression bomb trips a cap inside the first megabyte,
#: large enough that a 60 MiB archive is not read in a million calls.
_CHUNK_BYTES: Final = 262_144

#: Members a DwC-A is allowed to contain. Two descriptors and delimited text; nothing executable,
#: nothing binary, nothing whose handling this lane would have to guess at.
_REQUIRED_MEMBERS: Final[tuple[str, ...]] = ("meta.xml", "eml.xml")
_ALLOWED_SUFFIXES: Final[frozenset[str]] = frozenset({".xml", ".txt", ".csv", ".tsv"})

#: Suffixes and magic bytes that mean a member is itself an archive. Refused: a nested archive is an
#: unbounded budget, because nothing inside it was counted against the caps this one was checked with.
_NESTED_ARCHIVE_SUFFIXES: Final[frozenset[str]] = frozenset({".zip", ".gz", ".tgz", ".tar", ".bz2", ".xz", ".7z", ".rar"})
_NESTED_ARCHIVE_MAGIC: Final[tuple[bytes, ...]] = (b"PK\x03\x04", b"\x1f\x8b", b"BZh", b"\xfd7zXZ", b"7z\xbc\xaf")

#: `flag_bits & 0x1` is ZIP's encryption bit. An encrypted member is refused rather than prompted
#: for: this lane has no credential to supply and must never acquire one for a public export.
_ENCRYPTED_FLAG: Final = 0x1

#: The ZIP external-attribute bits that mark a POSIX symlink.
_UNIX_MODE_SHIFT: Final = 16
_FILE_TYPE_MASK: Final = 0o170000
_SYMLINK_TYPE: Final = 0o120000

REJECTION_REASONS: Final[tuple[str, ...]] = (
    "not_a_zip_archive",
    "archive_over_compressed_byte_cap",
    "member_count_over_cap",
    "member_path_traversal",
    "member_absolute_path",
    "member_drive_letter",
    "member_unc_path",
    "member_symlink",
    "member_encrypted",
    "member_nested_archive",
    "member_unsupported_suffix",
    "member_name_collision_after_normalisation",
    "member_crc_mismatch",
    "member_compression_ratio_over_cap",
    "archive_over_decompressed_byte_cap",
    "missing_required_member",
)


@dataclass(frozen=True, slots=True)
class MemberReceipt:
    """What one archive member is, measured rather than declared."""

    member_name: str
    compressed_bytes: int
    uncompressed_bytes: int
    declared_crc32: int
    #: Recomputed while streaming. A member whose bytes do not match its own directory entry is not
    #: the member the archive says it is.
    measured_crc32: int
    sha256: str


@dataclass(frozen=True, slots=True)
class ArchiveReceipt:
    """The immutable safety record of one archive: what it holds, and whether it may be read."""

    archive_path: str
    archive_sha256: str
    archive_bytes: int
    members: tuple[MemberReceipt, ...]
    total_compressed_bytes: int
    total_decompressed_bytes: int
    meta_sha256: str | None
    eml_sha256: str | None
    outcome: str
    reasons: tuple[str, ...]

    @property
    def accepted(self) -> bool:
        """True only when every control passed. There is no partially-safe archive."""
        return self.outcome == "accepted"

    def member(self, member_name: str) -> MemberReceipt | None:
        """Return one member's receipt by name, or None when the archive does not hold it."""
        return next((entry for entry in self.members if entry.member_name == member_name), None)


def _file_sha256(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK_BYTES):
            digest.update(chunk)
            total += len(chunk)
    return digest.hexdigest(), total


def _normalised_name(member_name: str) -> str:
    """Collapse Unicode form and case, which is how two members collide on a real filesystem."""
    return unicodedata.normalize("NFC", member_name).casefold()


def _path_reasons(member_name: str) -> list[str]:
    """Every way one member name could write outside the directory it is read into."""
    reasons: list[str] = []
    normalised = member_name.replace("\\", "/")
    if normalised.startswith("//"):
        reasons.append("member_unc_path")
    if normalised.startswith("/"):
        reasons.append("member_absolute_path")
    if len(normalised) > 1 and normalised[1] == ":":
        reasons.append("member_drive_letter")
    if ".." in posixpath.normpath(normalised).split("/"):
        reasons.append("member_path_traversal")
    return reasons


def _is_symlink(entry: zipfile.ZipInfo) -> bool:
    return (entry.external_attr >> _UNIX_MODE_SHIFT) & _FILE_TYPE_MASK == _SYMLINK_TYPE


def _looks_nested(member_name: str, head: bytes) -> bool:
    suffix = posixpath.splitext(member_name)[1].casefold()
    if suffix in _NESTED_ARCHIVE_SUFFIXES:
        return True
    return any(head.startswith(magic) for magic in _NESTED_ARCHIVE_MAGIC)


def _stream_member(
    archive: zipfile.ZipFile,
    entry: zipfile.ZipInfo,
    *,
    limits: AcquisitionLimits,
    already_decompressed: int,
) -> tuple[MemberReceipt, list[str]]:
    """Read one member through, measuring its hash, CRC and true size against the caps as it goes."""
    reasons: list[str] = []
    digest = hashlib.sha256()
    crc = 0
    measured = 0
    head = b""
    with archive.open(entry, "r") as handle:
        while chunk := handle.read(_CHUNK_BYTES):
            if not head:
                head = chunk[:8]
            digest.update(chunk)
            crc = zipfile.crc32(chunk, crc)
            measured += len(chunk)
            if already_decompressed + measured > limits.decompressed_bytes_total:
                reasons.append("archive_over_decompressed_byte_cap")
                break
    if crc != entry.CRC and "archive_over_decompressed_byte_cap" not in reasons:
        reasons.append("member_crc_mismatch")
    if entry.compress_size > 0 and measured / entry.compress_size > limits.max_compression_ratio:
        reasons.append("member_compression_ratio_over_cap")
    if _looks_nested(entry.filename, head):
        reasons.append("member_nested_archive")
    return (
        MemberReceipt(
            member_name=entry.filename,
            compressed_bytes=entry.compress_size,
            uncompressed_bytes=measured,
            declared_crc32=entry.CRC,
            measured_crc32=crc,
            sha256=digest.hexdigest(),
        ),
        reasons,
    )


def inspect_archive(path: Path, limits: AcquisitionLimits = ADMITTED_LIMITS) -> ArchiveReceipt:
    """Measure one archive against every admitted control and return its immutable receipt.

    FAIL-CLOSED AND EXHAUSTIVE: the walk collects every reason it can rather than stopping at the
    first, because an operator who fixes one refusal and refetches should not discover the second on
    the next attempt. The two exceptions are the size caps, which abort the read at the byte that
    breaks them -- continuing past a cap is exactly the resource exhaustion the cap exists to stop.
    """
    archive_sha256, archive_bytes = _file_sha256(path)
    reasons: list[str] = []
    if archive_bytes > limits.max_archive_bytes:
        reasons.append("archive_over_compressed_byte_cap")
    if not zipfile.is_zipfile(path):
        return ArchiveReceipt(
            archive_path=str(path),
            archive_sha256=archive_sha256,
            archive_bytes=archive_bytes,
            members=(),
            total_compressed_bytes=0,
            total_decompressed_bytes=0,
            meta_sha256=None,
            eml_sha256=None,
            outcome="rejected",
            reasons=("not_a_zip_archive", *reasons),
        )

    members: list[MemberReceipt] = []
    seen_names: dict[str, str] = {}
    total_compressed = 0
    total_decompressed = 0
    with zipfile.ZipFile(path) as archive:
        entries = [entry for entry in archive.infolist() if not entry.is_dir()]
        if len(entries) > limits.members:
            reasons.append("member_count_over_cap")
            entries = entries[: limits.members]
        for entry in entries:
            # Per-member, not accumulated: one bad name must not make every later member look bad.
            member_path_reasons = _path_reasons(entry.filename)
            reasons.extend(member_path_reasons)
            if _is_symlink(entry):
                reasons.append("member_symlink")
                continue
            if entry.flag_bits & _ENCRYPTED_FLAG:
                reasons.append("member_encrypted")
                continue
            suffix = posixpath.splitext(entry.filename)[1].casefold()
            if suffix not in _ALLOWED_SUFFIXES:
                reasons.append("member_unsupported_suffix")
            collision_key = _normalised_name(posixpath.basename(entry.filename))
            if collision_key in seen_names:
                reasons.append("member_name_collision_after_normalisation")
            seen_names[collision_key] = entry.filename
            if member_path_reasons:
                # A member that names a path outside the archive is never opened, safe suffix or not.
                continue
            receipt, member_reasons = _stream_member(
                archive, entry, limits=limits, already_decompressed=total_decompressed
            )
            members.append(receipt)
            reasons.extend(member_reasons)
            total_compressed += receipt.compressed_bytes
            total_decompressed += receipt.uncompressed_bytes
            if "archive_over_decompressed_byte_cap" in member_reasons:
                break

    by_basename = {posixpath.basename(entry.member_name).casefold(): entry for entry in members}
    for required in _REQUIRED_MEMBERS:
        if required not in by_basename:
            reasons.append("missing_required_member")
    if total_compressed > limits.compressed_bytes_total:
        reasons.append("archive_over_compressed_byte_cap")

    ordered_reasons = tuple(dict.fromkeys(reasons))
    return ArchiveReceipt(
        archive_path=str(path),
        archive_sha256=archive_sha256,
        archive_bytes=archive_bytes,
        members=tuple(members),
        total_compressed_bytes=total_compressed,
        total_decompressed_bytes=total_decompressed,
        meta_sha256=by_basename["meta.xml"].sha256 if "meta.xml" in by_basename else None,
        eml_sha256=by_basename["eml.xml"].sha256 if "eml.xml" in by_basename else None,
        outcome="rejected" if ordered_reasons else "accepted",
        reasons=ordered_reasons,
    )


def read_member_bytes(path: Path, member_name: str, limits: AcquisitionLimits = ADMITTED_LIMITS) -> bytes:
    """Read ONE member fully into memory under the decompressed cap; for the two small descriptors.

    Used for `meta.xml` and `eml.xml` only. The data members are streamed row by row in `rows.py`,
    because a 600,000-row occurrence file is not something to hold twice.
    """
    with zipfile.ZipFile(path) as archive, archive.open(member_name, "r") as handle:
        payload = handle.read(limits.decompressed_bytes_total + 1)
    if len(payload) > limits.decompressed_bytes_total:
        raise ValueError(f"member {member_name!r} exceeds the decompressed byte cap on its own")
    return payload


def resolve_member_name(receipt: ArchiveReceipt, basename: str) -> str | None:
    """Return the receipt's exact member name for a basename, honouring an archive that nests them."""
    target = basename.casefold()
    return next(
        (entry.member_name for entry in receipt.members if posixpath.basename(entry.member_name).casefold() == target),
        None,
    )


__all__ = [
    "REJECTION_REASONS",
    "ArchiveReceipt",
    "MemberReceipt",
    "inspect_archive",
    "read_member_bytes",
    "resolve_member_name",
]

"""Streaming reader over the archive's delimited members: verbatim values, locators, row hashes."""

from __future__ import annotations

import csv
import io
import json
import sys
import zipfile
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from agri_data_service.foundation.botanical_occurrences.limits import ADMITTED_LIMITS
from agri_data_service.foundation.botanical_occurrences.release_identity import row_sha256

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from agri_data_service.foundation.botanical_occurrences.limits import AcquisitionLimits
    from agri_data_service.pipeline.direct.botanical_occurrences.archive_descriptor import MemberDescriptor

#: A DwC occurrence file routinely carries a locality paragraph; the stdlib default field size is
#: smaller than several real herbarium remarks fields and raises on them.
_FIELD_SIZE_LIMIT: Final = 4 * 1024 * 1024

#: Rows accumulated before a batch is handed to Arrow. Chosen so one batch of a 600,000-row core is
#: a few tens of megabytes rather than the whole file.
DEFAULT_BATCH_ROWS: Final = 25_000


@dataclass(frozen=True, slots=True)
class SourceRow:
    """One source row: where it came from, what it says, and the hash of what it said."""

    member_name: str
    #: 1-based among DATA rows, so it does not shift when a header line is or is not skipped.
    row_number: int
    row_sha256: str
    #: The record's own id column (`<id>`/`<coreid>`), empty when the member declares none.
    record_id: str
    #: Short column name -> verbatim value, for terms this lane knows.
    values: dict[str, str]
    #: Term URI -> verbatim value, for EVERY column, including terms this lane has not learned.
    verbatim: dict[str, str]

    def verbatim_json(self) -> str:
        """Render the full verbatim row as the JSON the raw stream stores."""
        return json.dumps(self.verbatim, sort_keys=True, ensure_ascii=False)


@dataclass(frozen=True, slots=True)
class MemberReadResult:
    """What one member read produced, and whether the row cap cut it short."""

    member_name: str
    rows_read: int
    truncated: bool

    @property
    def outcome(self) -> str:
        """`partial` when a cap stopped the read; a truncated member is never `complete`."""
        return "partial" if self.truncated else "complete"


def _ensure_field_size_limit() -> None:
    if csv.field_size_limit() < _FIELD_SIZE_LIMIT:
        csv.field_size_limit(min(_FIELD_SIZE_LIMIT, sys.maxsize))


def iter_member_rows(
    archive_path: Path,
    descriptor: MemberDescriptor,
    *,
    max_rows: int,
) -> Iterator[SourceRow]:
    """Stream one member's data rows, honouring its declared encoding, delimiters and header lines.

    Stops AT the cap rather than raising, because a row ceiling is an admitted budget and a release
    that hits it is `partial` -- a real, reportable outcome -- not a failure. The caller learns which
    happened from `read_member`, and a `partial` release is never compared for identity stability.

    Decoding errors are replaced rather than fatal: a single mis-encoded character in one locality
    field should not discard 200,000 specimen records, and the substitution is visible in the
    verbatim value and in the row hash.
    """
    _ensure_field_size_limit()
    with zipfile.ZipFile(archive_path) as archive, archive.open(descriptor.member_name, "r") as raw:
        stream = io.TextIOWrapper(raw, encoding=descriptor.encoding, errors="replace", newline="")
        reader = csv.reader(
            stream,
            delimiter=descriptor.fields_terminated_by or "\t",
            quotechar=descriptor.fields_enclosed_by or '"',
            quoting=csv.QUOTE_NONE if not descriptor.fields_enclosed_by else csv.QUOTE_MINIMAL,
        )
        for _ in range(descriptor.ignore_header_lines):
            next(reader, None)
        row_number = 0
        for raw_values in reader:
            if not raw_values:
                continue
            row_number += 1
            if row_number > max_rows:
                return
            values = {
                column: raw_values[index] for index, column in descriptor.fields.items() if index < len(raw_values)
            }
            verbatim = {term: raw_values[index] for index, term in descriptor.terms.items() if index < len(raw_values)}
            record_id = (
                raw_values[descriptor.id_index]
                if descriptor.id_index is not None and descriptor.id_index < len(raw_values)
                else ""
            )
            yield SourceRow(
                member_name=descriptor.member_name,
                row_number=row_number,
                # Over the row as the file gave it, in column order: reordering would make two
                # different files hash alike, and the hash is how a changed record is detected.
                row_sha256=row_sha256(raw_values),
                record_id=record_id,
                values=values,
                verbatim=verbatim,
            )


def read_member(
    archive_path: Path,
    descriptor: MemberDescriptor,
    *,
    max_rows: int,
    limits: AcquisitionLimits = ADMITTED_LIMITS,
) -> tuple[tuple[SourceRow, ...], MemberReadResult]:
    """Read one member into memory under its cap, reporting whether the cap truncated it.

    Reads one row past the cap on purpose: that is the only way to tell "the file ended exactly at
    the cap" from "the file was cut off there", and reporting `complete` for a truncated member would
    make a partial population look like a reconciled one.
    """
    # Never above the admitted extension ceiling, whatever a caller asks for: the caps narrow, and a
    # caller-supplied number is a request for LESS, never a licence for more.
    ceiling = min(max_rows, limits.extension_rows)
    collected = tuple(iter_member_rows(archive_path, descriptor, max_rows=ceiling + 1))
    truncated = len(collected) > ceiling
    kept = collected[:ceiling]
    return kept, MemberReadResult(member_name=descriptor.member_name, rows_read=len(kept), truncated=truncated)


def batched(rows: tuple[SourceRow, ...], batch_rows: int = DEFAULT_BATCH_ROWS) -> Iterator[tuple[SourceRow, ...]]:
    """Split rows into Arrow-sized batches, preserving order."""
    for start in range(0, len(rows), batch_rows):
        yield rows[start : start + batch_rows]


__all__ = [
    "DEFAULT_BATCH_ROWS",
    "MemberReadResult",
    "SourceRow",
    "batched",
    "iter_member_rows",
    "read_member",
]

"""The acquisition ceiling, copied from the admission decision that owns it."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

#: The one file these numbers may be changed in. Restated here so a code reader can find it.
ADMISSION_DECISION_PATH: Final = (
    "conductor/tracks/pnw_herbaria_source_admission_20260911/evidence/admission-decisions.json"
)

#: The parser recipe that read an archive. Any change to member handling, delimiters or promoted
#: terms mints a new value, because a re-read under different rules is a different release row.
PARSER_VERSION: Final = "dwca-parser-v1"

#: The normalization/QC recipe bound onto every normalized row.
QC_POLICY_VERSION: Final = "botanical-qc-v1"

#: The taxonomy recipe. `source-names-v0` binds NO external authority: it keeps the publisher's own
#: names and resolves nothing it was not handed an exact match for. A pinned external authority is a
#: later recipe with a later version, never a silent upgrade of this one.
TAXONOMY_RECIPE_VERSION: Final = "source-names-v0"

#: The support family version, bound into the release-set identity alongside the two recipes above.
SUPPORT_VERSION: Final = "grid-support-v1"


class AcquisitionLimitError(RuntimeError):
    """Raised when an archive, member, row count or clock exceeds the admitted ceiling."""


@dataclass(frozen=True, slots=True)
class AcquisitionLimits:
    """Exactly the envelope `admission-decisions.json` -> `limits` admits, plus two derived guards.

    Overruns defer or fail; they never widen. An advertised megabyte count is not a measured one, so
    every field below is enforced against bytes this process actually counted.
    """

    archives: int = 2
    concurrent_transfers: int = 1
    compressed_bytes_per_archive: int = 67_108_864
    compressed_bytes_total: int = 134_217_728
    decompressed_bytes_total: int = 2_147_483_648
    members: int = 32
    core_rows: int = 600_000
    extension_rows: int = 2_000_000
    http_attempts: int = 8
    request_seconds: int = 30
    wall_seconds: int = 600
    #: NOT from the decisions file: a compression-ratio guard, because the decompressed-total cap
    #: alone lets one 1 KiB member expand to the whole 2 GiB budget before anything notices.
    max_compression_ratio: int = 200

    @property
    def max_archive_bytes(self) -> int:
        """Alias for `compressed_bytes_per_archive`, named the way the fetch path reads."""
        return self.compressed_bytes_per_archive


#: The admitted envelope. A caller wanting a smaller one constructs its own; nothing constructs a
#: larger one, and a third archive slot is not implied by anything here.
ADMITTED_LIMITS: Final = AcquisitionLimits()

__all__ = [
    "ADMISSION_DECISION_PATH",
    "ADMITTED_LIMITS",
    "PARSER_VERSION",
    "QC_POLICY_VERSION",
    "SUPPORT_VERSION",
    "TAXONOMY_RECIPE_VERSION",
    "AcquisitionLimitError",
    "AcquisitionLimits",
]

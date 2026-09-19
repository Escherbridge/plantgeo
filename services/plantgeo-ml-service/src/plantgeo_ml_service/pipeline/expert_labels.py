"""Read the exported expert label plane out of the bucket; the one-time export is an agri-side verb.

Layer L3, spec FR-7. Why the 28-row plane is a Parquet object rather than a table, and why an absent
release refuses instead of returning zero labels, live in `AGENTS.md` in this directory.
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

import pyarrow as pa  # type: ignore[import-untyped]  # pyarrow ships no stubs
import pyarrow.parquet as pq  # type: ignore[import-untyped]  # pyarrow ships no stubs

from plantgeo_ml_service.method.ml.expert_label_plane import (
    CONFIDENCE_WEIGHTS,
    OUTCOMES_BY_KIND,
    ExpertLabelPlaneError,
)
from plantgeo_ml_service.pipeline.object_store import sha256_of

if TYPE_CHECKING:
    from datetime import datetime

    from plantgeo_ml_service.pipeline.object_store import ObjectStore

#: Where an exported release lives, relative to the service's `ml_prefix`. One part file per
#: release: the plane is 28 rows, and a multi-part layout would need a completion marker to say so.
EXPERT_LABEL_PREFIX: Final = "labels/expert"
EXPERT_LABEL_PART_NAME: Final = "part-0000.parquet"

#: A release is a literature harvest, not a data lane. Anything past this is a different document.
MAX_EXPERT_LABELS: Final = 5_000

#: A release identifier is an object-key segment, so it may not traverse or contain a separator.
_FORBIDDEN_RELEASE_CHARACTERS: Final = frozenset({"/", "\\"})

# Columns mirror `agri.expert_label` one for one, plus the release and source keys that resolve its
# two foreign keys without the other tables. `condition_envelope` is carried as canonical JSON TEXT
# rather than as an Arrow struct: the envelope's key set is an open vocabulary
# (`ENVELOPE_TERM_SUPPORT`), and a struct would freeze today's seven terms into the file's schema.
EXPERT_LABEL_SCHEMA: Final = pa.schema(
    [
        pa.field("label_key", pa.string(), nullable=False),
        pa.field("release_key", pa.string(), nullable=False),
        pa.field("source_key", pa.string(), nullable=False),
        pa.field("label_kind", pa.string(), nullable=False),
        pa.field("subject", pa.string(), nullable=False),
        pa.field("subject_normalized", pa.string(), nullable=False),
        pa.field("outcome", pa.string(), nullable=False),
        pa.field("condition_envelope", pa.string(), nullable=False),
        pa.field("envelope_checksum", pa.string(), nullable=False),
        pa.field("rationale", pa.string(), nullable=False),
        pa.field("supporting_quote", pa.string(), nullable=True),
        pa.field("confidence", pa.string(), nullable=False),
        pa.field("confidence_weight", pa.float64(), nullable=False),
        pa.field("harvest_slice", pa.string(), nullable=False),
        pa.field("citation_check_refuted", pa.bool_(), nullable=False),
        pa.field("citation_check_doi_resolves", pa.bool_(), nullable=False),
        pa.field("citation_check_reason", pa.string(), nullable=False),
        pa.field("review_state", pa.string(), nullable=False),
        pa.field("review_note", pa.string(), nullable=True),
        pa.field("reviewed_by", pa.string(), nullable=True),
        pa.field("reviewed_at", pa.timestamp("us", tz="UTC"), nullable=True),
        pa.field("owner_signature_reference", pa.string(), nullable=True),
        pa.field("label_checksum", pa.string(), nullable=False),
        pa.field("created_at", pa.timestamp("us", tz="UTC"), nullable=False),
    ]
)


class ExpertLabelReadError(ExpertLabelPlaneError):
    """Raised when a release cannot be read; never a signal to answer from somewhere else."""


@dataclass(frozen=True, slots=True)
class ExpertLabel:
    """One exported expert label, with its envelope already decoded from canonical JSON."""

    label_key: str
    release_key: str
    source_key: str
    label_kind: str
    subject: str
    subject_normalized: str
    outcome: str
    condition_envelope: dict[str, object]
    envelope_checksum: str
    rationale: str
    supporting_quote: str | None
    confidence: str
    confidence_weight: float
    harvest_slice: str
    citation_check_refuted: bool
    citation_check_doi_resolves: bool
    citation_check_reason: str
    review_state: str
    review_note: str | None
    reviewed_by: str | None
    reviewed_at: datetime | None
    owner_signature_reference: str | None
    label_checksum: str
    created_at: datetime

    def __post_init__(self) -> None:
        allowed = OUTCOMES_BY_KIND.get(self.label_kind)
        if allowed is None:
            raise ExpertLabelReadError(f"label {self.label_key!r} carries unknown kind {self.label_kind!r}")
        if self.outcome not in allowed:
            raise ExpertLabelReadError(
                f"label {self.label_key!r} is a {self.label_kind} carrying outcome {self.outcome!r}; "
                f"allowed: {', '.join(allowed)}"
            )
        if self.confidence not in CONFIDENCE_WEIGHTS:
            raise ExpertLabelReadError(f"label {self.label_key!r} carries unknown confidence {self.confidence!r}")


@dataclass(frozen=True, slots=True)
class ExpertLabelRelease:
    """One read release: its labels and the digest of the exact bytes they were decoded from."""

    release: str
    relative_path: str
    labels: tuple[ExpertLabel, ...]
    sha256: str


def expert_label_part_path(release: str, *, ml_prefix: str = "ml/") -> str:
    """Return the relative object key of one exported release's single part file."""
    validated = validate_release_identifier(release)
    return f"{ml_prefix}{EXPERT_LABEL_PREFIX}/{validated}/{EXPERT_LABEL_PART_NAME}"


def validate_release_identifier(release: str) -> str:
    """Return `release` if it is a safe single object-key segment, else raise."""
    if not release or release != release.strip() or release in {".", ".."}:
        raise ExpertLabelReadError(f"release identifier {release!r} is not a usable object-key segment")
    if _FORBIDDEN_RELEASE_CHARACTERS & set(release):
        raise ExpertLabelReadError(f"release identifier {release!r} may not contain a path separator")
    return release


def read_expert_labels(store: ObjectStore, release: str, *, ml_prefix: str = "ml/") -> ExpertLabelRelease:
    """Read one exported release, refusing with a named reason when the object is absent or off-schema."""
    relative_path = expert_label_part_path(release, ml_prefix=ml_prefix)
    payload = store.read_object(relative_path)
    if payload is None:
        raise ExpertLabelReadError(
            f"expert label release {release!r} has not been exported to {relative_path!r}; the one-time "
            "export is the agri-side verb `agri-service ops export-expert-labels` and needs an owner go"
        )
    table = pq.read_table(io.BytesIO(payload))
    if not table.schema.remove_metadata().equals(EXPERT_LABEL_SCHEMA):
        raise ExpertLabelReadError(
            f"expert label release {release!r} does not carry the pinned schema; it was written by a "
            "different exporter, and reading it as this shape would silently mis-type a column"
        )
    if table.num_rows > MAX_EXPERT_LABELS:
        raise ExpertLabelReadError(f"expert label release {release!r} holds more than {MAX_EXPERT_LABELS} labels")
    labels = tuple(_label_from_row(row, release=release) for row in table.to_pylist())
    return ExpertLabelRelease(
        release=release,
        relative_path=relative_path,
        labels=labels,
        sha256=sha256_of(payload),
    )


def _label_from_row(row: dict[str, object], *, release: str) -> ExpertLabel:
    """Decode one exported row, narrowing the envelope from canonical JSON into a mapping."""
    envelope = row.get("condition_envelope")
    if not isinstance(envelope, str):
        raise ExpertLabelReadError(f"release {release!r} carries a non-text condition_envelope")
    try:
        decoded = json.loads(envelope)
    except json.JSONDecodeError as error:
        raise ExpertLabelReadError(f"release {release!r} carries an undecodable condition_envelope") from error
    if not isinstance(decoded, dict):
        raise ExpertLabelReadError(f"release {release!r} carries a condition_envelope that is not an object")
    return ExpertLabel(**{**row, "condition_envelope": decoded})  # type: ignore[arg-type]  # schema-pinned row

"""The Arrow shape of one exported expert label release, pinned so both services can parity-test it.

Layer L1: may import `foundation`; may NOT import method, pipeline, planes, or interface.

NOT A LANE, AND DELIBERATELY NOT REGISTERED. `expert-labels` is no `geo.layers` slug, has no
partition day, no zoom ladder and no coverage census; it is a one-time export of the 28-row reviewed
label plane to a single object at `ml/labels/expert/<release>/part-0000.parquet` (spec FR-7, track
`plantgeo_ml_service_20260918`). It lives in this package because this is where the service keeps
its pinned Arrow schemas, the same way `availability_index.py` does -- see this directory's
`AGENTS.md`.

THE SHAPE IS THE ML SERVICE'S `EXPERT_LABEL_SCHEMA`
(`plantgeo_ml_service/pipeline/expert_labels.py`), field for field and in order. That reader
compares the file's schema against its own copy and REFUSES a mismatch outright rather than reading
it loosely, so a divergence here is not a soft warning: it makes every exported release unreadable.

COLUMNS MIRROR `agri.expert_label` ONE FOR ONE, plus the two natural keys that resolve its foreign
keys without the other tables: `release_key` from `agri.expert_label_release` and `source_key` from
`agri.expert_label_source`. The surrogate UUIDs are deliberately NOT exported -- they identify rows
in a database the consuming service has no access to and refuses to have (spec D5, zero Postgres).

`condition_envelope` IS CANONICAL JSON TEXT, NOT AN ARROW STRUCT. The envelope's key set is an open
vocabulary validated by `agri.expert_label_envelope_valid`, and a struct would freeze today's terms
into every file's schema, so adding a term would change the shape of a plane that is supposed to be
append-only.
"""

from __future__ import annotations

from typing import Final

import pyarrow as pa  # type: ignore[import-untyped]

#: Where one exported release lives, relative to the bucket root. One part file per release: the
#: plane is 28 rows, and a multi-part layout would need a completion marker to say it was finished.
EXPERT_LABEL_PREFIX: Final = "ml/labels/expert"
EXPERT_LABEL_PART_NAME: Final = "part-0000.parquet"

#: A release is a literature harvest, not a data lane. Anything past this is a different document.
#: The same ceiling the ML reader applies, so neither side can write what the other refuses.
MAX_EXPERT_LABELS: Final = 5_000

EXPERT_LABEL_EXPORT_SCHEMA: Final = pa.schema(
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

#: The export's row order: `label_key` is unique across the plane, so one sort key is total and the
#: same rows always produce the same bytes.
EXPERT_LABEL_SORT_COLUMNS: Final[tuple[str, ...]] = ("label_key",)

EXPERT_LABEL_COLUMNS: Final[tuple[str, ...]] = tuple(EXPERT_LABEL_EXPORT_SCHEMA.names)

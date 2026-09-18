"""The geography sub-package: generic spatial-envelope mechanisms with zero domain meaning.

Layer L0 (`foundation`). Imports stdlib only, satisfying `foundation/AGENTS.md`'s Admission Test in
full -- no ruled exception needed, unlike `foundation/region/`. `bounding_box.py` names a mechanism
(an envelope and its CLI encoding), not a domain noun, and is used by six `pipeline/direct/<domain>`
lanes plus `ingest/mtbs.py` itself. See `AGENTS.md` in this directory for the extraction record.
"""

from agri_data_service.foundation.geography.bounding_box import (
    BBOX_OPTION,
    BBOX_ORDINATE_COUNT,
    BoundingBox,
    format_bounding_box_inline,
    inline_bbox_value,
    parse_bounding_box,
)

__all__ = [
    "BBOX_OPTION",
    "BBOX_ORDINATE_COUNT",
    "BoundingBox",
    "format_bounding_box_inline",
    "inline_bbox_value",
    "parse_bounding_box",
]

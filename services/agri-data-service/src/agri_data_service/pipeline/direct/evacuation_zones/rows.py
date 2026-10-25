"""Shim: moved to `pipeline/lanes/evacuation_zones/rows.py` by p4-extract; see `DEPRECATED_ALIASES.md`."""

from agri_data_service.pipeline.lanes.evacuation_zones.rows import (
    CONTENT_DIGEST_COLUMNS,
    DIRECT_GEOMETRY_VERSION_PREFIX,
    EvacuationZonesRowError,
    apply_carried_forward_updated_at,
    content_digest,
    direct_geometry_version_id,
    evacuation_zones_table,
    natural_key_for,
    row_content_digests,
    split_into_parts,
    updated_at_by_natural_key,
)

__all__ = [
    "CONTENT_DIGEST_COLUMNS",
    "DIRECT_GEOMETRY_VERSION_PREFIX",
    "EvacuationZonesRowError",
    "apply_carried_forward_updated_at",
    "content_digest",
    "direct_geometry_version_id",
    "evacuation_zones_table",
    "natural_key_for",
    "row_content_digests",
    "split_into_parts",
    "updated_at_by_natural_key",
]

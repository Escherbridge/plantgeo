"""Conform one fetched USDM release to `DROUGHT_SCHEMA`, WKB-repaired through DuckDB spatial."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Final

import pyarrow as pa  # type: ignore[import-untyped]

from agri_data_service.pipeline.direct.drought.support import drought_geometry_session, repair_drought_areas_to_wkb
from agri_data_service.warehouse.schemas.drought import DROUGHT_SCHEMA

if TYPE_CHECKING:
    from datetime import datetime

    from agri_data_service.ingest.usdm import DroughtRelease

#: Namespaces a direct row's `area_id` as never a genuine `geo.drought_areas.id`. There is no
#: Postgres row behind a direct fetch, so a reader that joined this column back to that table on the
#: strength of its shape alone would be reading a namespace that never existed there. See
#: `pipeline/direct/AGENTS.md`, "Drought" -- "The `direct:` area_id, and why it is not a lineage column".
DIRECT_AREA_ID_PREFIX: Final = "direct"


def direct_area_id(valid_date: str, drought_monitor_category: int) -> str:
    """Build the deterministic, `direct:`-namespaced id a direct row carries in place of a real one."""
    return f"{DIRECT_AREA_ID_PREFIX}:{valid_date}:{drought_monitor_category}"


def drought_release_table(release: DroughtRelease, *, ingested_at: datetime) -> pa.Table:
    """Build the base-rung Arrow table for one release, repairing every class through DuckDB spatial.

    `ingested_at` is the caller's fetch instant, matching `water_gauges.py`'s convention (see
    `pipeline/direct/AGENTS.md`, "Water gauges"): a direct row truthfully records when THIS repo
    fetched it, never a Postgres write time that never happened.
    """
    with drought_geometry_session() as session:
        repaired = repair_drought_areas_to_wkb(session, release.areas)
    valid_date = date.fromisoformat(release.valid_date)
    rows = [
        {
            "area_id": direct_area_id(release.valid_date, area.drought_monitor_category),
            "valid_date": valid_date,
            "dm_category": area.drought_monitor_category,
            "source_url": release.source_url,
            "ingested_at": ingested_at,
            "geom": repaired[area.drought_monitor_category],
        }
        for area in release.areas
    ]
    return pa.Table.from_pylist(rows, schema=DROUGHT_SCHEMA.arrow_schema)


__all__ = ["DIRECT_AREA_ID_PREFIX", "direct_area_id", "drought_release_table"]

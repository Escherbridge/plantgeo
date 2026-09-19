"""Conform one fetched USDM release to `DROUGHT_SCHEMA`, WKB-repaired through DuckDB spatial."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

import pyarrow as pa  # type: ignore[import-untyped]

from agri_data_service.pipeline.direct.drought.support import drought_geometry_session, repair_drought_areas_to_wkb
from agri_data_service.warehouse.schemas.drought import DROUGHT_SCHEMA

if TYPE_CHECKING:
    from datetime import datetime

    from agri_data_service.pipeline.direct.drought.source_protocol import DroughtReleasePayload

#: Namespaces a direct row's `area_id` as never a genuine `geo.drought_areas.id`. There is no
#: Postgres row behind a direct fetch, so a reader that joined this column back to that table on the
#: strength of its shape alone would be reading a namespace that never existed there. See
#: `pipeline/direct/AGENTS.md`, "Drought" -- "The `direct:` area_id, and why it is not a lineage column".
DIRECT_AREA_ID_PREFIX: Final = "direct"


def direct_area_id(valid_date: str, drought_intensity_class: int) -> str:
    """Build the deterministic, `direct:`-namespaced id a direct row carries in place of a real one."""
    return f"{DIRECT_AREA_ID_PREFIX}:{valid_date}:{drought_intensity_class}"


def drought_release_table(release: DroughtReleasePayload, *, ingested_at: datetime) -> pa.Table:
    """Build the base-rung Arrow table for one release, repairing every class through DuckDB spatial.

    `ingested_at` is the caller's fetch instant, matching `water_gauges.py`'s convention (see
    `pipeline/direct/AGENTS.md`, "Water gauges"): a direct row truthfully records when THIS repo
    fetched it, never a Postgres write time that never happened.

    Takes the LAYER's payload protocol, never `ingest.usdm.DroughtRelease`: this module is lane
    logic, and lane logic that names one source's type is the source-name branch `federation.md` §2
    calls the bug. `release.release_day` is already a `date` because the source normalised USDM's
    ISO string at its own boundary (`ingest/usdm.py::DroughtRelease.release_day`).
    """
    with drought_geometry_session() as session:
        repaired = repair_drought_areas_to_wkb(session, release.areas)
    valid_date = release.release_day
    rows = [
        {
            "area_id": direct_area_id(valid_date.isoformat(), area.drought_intensity_class),
            "valid_date": valid_date,
            # The one line where the layer's name meets the source's. `dm_category` is the PERSISTED
            # spelling -- the US Drought Monitor's, abbreviated -- and it is pinned by the Parquet
            # schema and by `DROUGHT_GRAIN`, so renaming it is a schema migration over every written
            # partition, unlike the in-memory member which was free to rename. The layer contract is
            # `drought_intensity_class` and it is authoritative; see this lane's `AGENTS.md`
            # ("Still not normalized") for the deletion condition (STYLE-REVIEW-W6 S4).
            "dm_category": area.drought_intensity_class,
            "source_url": release.source_url,
            "ingested_at": ingested_at,
            "geom": repaired[area.drought_intensity_class],
        }
        for area in release.areas
    ]
    return pa.Table.from_pylist(rows, schema=DROUGHT_SCHEMA.arrow_schema)


__all__ = ["DIRECT_AREA_ID_PREFIX", "direct_area_id", "drought_release_table"]

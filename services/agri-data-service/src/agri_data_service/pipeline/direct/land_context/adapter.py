"""Write a validated captured table through the shared all-rung publication lifecycle."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from agri_data_service.pipeline.direct.land_context.rows import split_parts
from agri_data_service.pipeline.parquet.lane_registry import normalise_export_outcome

if TYPE_CHECKING:
    from datetime import date

    import pyarrow as pa  # type: ignore[import-untyped]
    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.pipeline.parquet.lane_registry import LaneRunResult
    from agri_data_service.pipeline.parquet.objectstore import ObjectStore


@dataclass(frozen=True, slots=True)
class LandContextAdapter:
    layer: str
    table: pa.Table

    async def __call__(self, session: AsyncSession, store: ObjectStore, *, day: date, run_id: str) -> LaneRunResult:
        await session.rollback()
        if not run_id or set(self.table.column("release_day").to_pylist()) != {day}:
            raise ValueError("BLM adapter day or run identity does not match the captured table")
        return normalise_export_outcome(
            tuple(
                store.write_partition(part, layer=self.layer, kind="observed", zoom=13, day=day, part_index=index)
                for index, part in enumerate(split_parts(self.table))
            )
        )

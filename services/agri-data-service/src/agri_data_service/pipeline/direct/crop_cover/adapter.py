"""Bind prepared annual crop-cover tables to the shared publication finalizer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import polars as pl

from agri_data_service.foundation.parquet.paths import try_parse_partition_path
from agri_data_service.foundation.parquet.zoom import validate_zoom_tier
from agri_data_service.pipeline.direct.crop_cover.source import fail
from agri_data_service.pipeline.parquet.derivation import DerivationResult, _land_tier
from agri_data_service.pipeline.parquet.lane_registry import normalise_export_outcome
from agri_data_service.warehouse.schemas.crop_cover import CROP_COVER_STREAM

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import date, datetime

    import pyarrow as pa  # type: ignore[import-untyped]
    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.pipeline.parquet.lane_registry import LaneRunResult
    from agri_data_service.pipeline.parquet.objectstore import ObjectStore


@dataclass
class CropCoverAdapter:
    """Write a complete annual base while the shared lane-day lock is held."""

    tables: dict[int, pa.Table]

    async def __call__(
        self,
        session: AsyncSession,
        store: ObjectStore,
        *,
        day: date,
        run_id: str,
    ) -> LaneRunResult:
        del run_id
        await session.rollback()
        base = self.tables[13]
        if set(base.column("release_day").to_pylist()) != {day}:
            raise fail("Prepared annual data cannot be published under a different release day")
        existing_parts = store.list_partition_keys(CROP_COVER_STREAM, "observed", 13, year=day.year, month=day.month)
        if any((parsed := try_parse_partition_path(key)) is not None and parsed.day == day for key in existing_parts):
            previous = store.read_partition(CROP_COVER_STREAM, "observed", 13, day)
            if max(previous.column("ingested_at").to_pylist()) > min(base.column("ingested_at").to_pylist()):
                raise fail("An older capture cannot overwrite a newer published annual classification")
        receipts = tuple(
            store.write_partition(
                base.slice(offset, 10000),
                layer=CROP_COVER_STREAM,
                kind="observed",
                zoom=13,
                day=day,
                part_index=index,
            )
            for index, offset in enumerate(range(0, base.num_rows, 10000))
        )
        return normalise_export_outcome(receipts)

    def derive(  # noqa: PLR0913 - shared derivation callback coordinates
        self,
        store: ObjectStore,
        *,
        layer: str,
        kind: str,
        day: date,
        run_id: str,
        now: Callable[[], datetime],
        **_kwargs: object,
    ) -> DerivationResult:
        """Land count-preserving coarse rungs through ordinary prune/receipt/marker machinery."""
        if layer != CROP_COVER_STREAM or kind != "observed":
            raise fail("Crop derivation cannot write a different stream or forecast")
        reports = []
        for tier in (9, 5, 0):
            zoom = validate_zoom_tier(tier)
            frame = pl.from_arrow(self.tables[tier])
            if not isinstance(frame, pl.DataFrame):
                raise fail("Crop derivation requires a complete tabular rung")
            report = _land_tier(
                store,
                frame,
                layer=layer,
                kind="observed",
                tier=zoom,
                day=day,
                run_id=run_id,
                now=now,
            )
            if report is None:
                raise fail("A classified annual crop release unexpectedly produced an empty coarse rung")
            reports.append(report)
        return DerivationResult(tuple(reports), ())

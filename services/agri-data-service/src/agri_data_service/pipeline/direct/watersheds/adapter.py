"""The lane adapter: write one already-fetched NHDPlus_HR snapshot as watersheds' base rung.

NEVER CONSTRUCTS `TerminalEvidence` AND NEVER PASSES `provenance=`. Hands `store.write_partition` a
validated, geometry-byte-chunked sequence of tables; the shared finalizer
(`gap_fill.fill_one_lane_day` -> `_bind_rung`/`_rung_objects_from_ledger`) builds every
`TerminalEvidence` from the real written-object ledger, so provenance defaults to `digested` by
construction -- the identical discipline `pipeline/direct/drought/adapter.py`,
`pipeline/direct/soil/adapter.py`, `pipeline/direct/climate/adapter.py` and
`pipeline/direct/vegetation/adapter.py` all document. `scripts/compile_availability_bootstrap.py` is
the ONLY caller that legitimately passes `provenance=manifest_trusted`, and it is the bootstrap
path, not this one.

UNLIKE EVERY OTHER `pipeline/direct/*` ADAPTER, `fetch_source` HERE NEVER MAKES A NETWORK CALL. It
closes over `forward.py`'s already-completed `WatershedsSnapshotSource` fetch (one whole-lane read,
shared with the watermark resolver -- see `forward.py`'s module docstring, "One fetch, not three"),
so the entire cost of `__call__` is DuckDB conversion and object-store PUTs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

from agri_data_service.pipeline.constants import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.direct.watersheds.rows import chunk_rows_by_geometry_bytes, watersheds_snapshot_table
from agri_data_service.pipeline.parquet.lane_registry import normalise_export_outcome
from agri_data_service.warehouse.schemas.watersheds import WATERSHEDS_STREAM

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from datetime import date

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.pipeline.direct.watersheds.source import WatershedsSnapshotSource
    from agri_data_service.pipeline.parquet.lane_registry import LaneRunResult
    from agri_data_service.pipeline.parquet.objectstore import ObjectStore

#: `Final` so this narrows to the `PartitionKind` literal rather than bare `str`. No `kind=forecast`
#: sibling: watersheds is `horizon: none` (`warehouse/schemas/watersheds.py` module docstring), so
#: only `kind=observed` is ever written.
WATERSHEDS_DIRECT_KIND: Final = "observed"


class DirectWatershedsError(RuntimeError):
    """Raised when a version day cannot support a complete direct Parquet publication."""


@dataclass(slots=True)
class DirectWatershedsAdapter:
    """Write one already-fetched snapshot while the caller holds the shared lane-day advisory lock."""

    fetch_source: Callable[[], Awaitable[WatershedsSnapshotSource]]
    source: WatershedsSnapshotSource | None = field(default=None, init=False)

    async def __call__(
        self,
        session: AsyncSession,
        store: ObjectStore,
        *,
        day: date,
        run_id: str,  # noqa: ARG002 - uniform adapter shape; a full-population snapshot carries no per-run marker
    ) -> LaneRunResult:
        """Roll back the timeout transaction, then write every part of one whole-lane snapshot.

        No fetch happens under THIS lock beyond `self.fetch_source()` returning the memoised
        snapshot `forward.py` already read before it ever asked for the advisory lock -- see that
        module's docstring. The entire cost paid while holding the lock is DuckDB conversion plus
        the object-store PUTs, not another NHDPlus_HR walk.
        """
        await session.rollback()
        source = await self.fetch_source()
        self.source = source
        table = watersheds_snapshot_table(source, release_day=day)
        chunks = chunk_rows_by_geometry_bytes(table)
        receipts = tuple(
            store.write_partition(
                chunk,
                layer=WATERSHEDS_STREAM,
                kind=WATERSHEDS_DIRECT_KIND,
                zoom=LANE_BASE_ZOOM_TIER,
                day=day,
                part_index=part_index,
            )
            for part_index, chunk in enumerate(chunks)
        )
        return normalise_export_outcome(receipts)


__all__ = ["WATERSHEDS_DIRECT_KIND", "DirectWatershedsAdapter", "DirectWatershedsError"]

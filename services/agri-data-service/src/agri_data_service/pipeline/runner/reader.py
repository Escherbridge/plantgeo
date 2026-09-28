"""The read side of a turn: census, turn receipts, published partitions and the served snapshot day.

`ObjectStoreLaneReader` is the production binding over `pipeline/parquet/objectstore.py::ObjectStore`;
a `--compare` turn gets exactly this and no writer. See `pipeline/runner/AGENTS.md` "Reader".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from agri_data_service.foundation.parquet.lane_contract import newest_data_day
from agri_data_service.pipeline.constants import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.parquet.objectstore import PartitionNotWrittenError, conform_to_stream_schema
from agri_data_service.pipeline.runner.census import CONFIG_LANE_KIND, read_census
from agri_data_service.pipeline.runner.digests import table_digest
from agri_data_service.warehouse.parquet.schema import get_stream_schema

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date

    import pyarrow as pa  # type: ignore[import-untyped]

    from agri_data_service.pipeline.parquet.objectstore import ObjectStore
    from agri_data_service.pipeline.runner.census import LaneCensus
    from agri_data_service.pipeline.runner.receipts import DayReceipt, TurnReceipts


class LaneReader(Protocol):
    """The turn's read port."""

    def census(self, streams: Sequence[str], first: date, last: date) -> LaneCensus: ...

    def receipt(self, stream: str, day: date) -> DayReceipt | None: ...

    def read_published(self, stream: str, day: date) -> pa.Table | None: ...

    def newest_data_day(self, stream: str) -> date | None: ...

    def canonical_digest(self, stream: str, table: pa.Table) -> str: ...


@dataclass(frozen=True, slots=True)
class ObjectStoreLaneReader:
    """Reads a lane's streams from the bucket; never writes."""

    store: ObjectStore
    receipts: TurnReceipts

    def census(self, streams: Sequence[str], first: date, last: date) -> LaneCensus:
        """The full-ladder census over `[first, last]`."""
        return read_census(self.store, streams, first, last)

    def receipt(self, stream: str, day: date) -> DayReceipt | None:
        """The stream-day's turn receipt, or `None`."""
        return self.receipts.read(stream, day)

    def read_published(self, stream: str, day: date) -> pa.Table | None:
        """The stream-day's base-rung rows, or `None` when nothing is published."""
        try:
            return self.store.read_partition(stream, CONFIG_LANE_KIND, LANE_BASE_ZOOM_TIER, day)
        except PartitionNotWrittenError:
            return None

    def newest_data_day(self, stream: str) -> date | None:
        """The newest base-rung day carrying values: a static lookup's served snapshot."""
        keys = self.store.list_partition_keys(stream, CONFIG_LANE_KIND, LANE_BASE_ZOOM_TIER)
        return newest_data_day(layer=stream, kind=CONFIG_LANE_KIND, zoom=LANE_BASE_ZOOM_TIER, keys=keys)

    def canonical_digest(self, stream: str, table: pa.Table) -> str:
        """The digest of `table` as the store would hold it: conformed to the stream schema and sorted to its grain."""
        return table_digest(conform_to_stream_schema(table, get_stream_schema(stream, CONFIG_LANE_KIND)))


__all__ = ["LaneReader", "ObjectStoreLaneReader"]

"""An in-memory warehouse: object keys and rows, with no bucket and no DuckDB behind them.

Both ports of `interface/http/warehouse_reader.py` implemented over dictionaries, so the four-state
resolver can be exercised exactly -- including the states real data cannot produce on demand.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

from agri_data_service.foundation.parquet.absence import GovernedAbsence
from agri_data_service.foundation.parquet.paths import (
    absence_marker_path,
    completion_marker_path,
    month_prefix,
    partition_path,
    year_prefix,
    zoom_prefix,
)
from agri_data_service.interface.http.warehouse_reader import RowReadResult

if TYPE_CHECKING:
    from datetime import date

    from agri_data_service.foundation.parquet.paths import PartitionKind
    from agri_data_service.foundation.parquet.zoom import ZoomTier
    from agri_data_service.interface.http.warehouse_reader import RowRead
    from agri_data_service.interface.http.wire import ServedRow


@dataclass
class FakeListing:
    """A `WarehouseListing` over a set of relative keys and the marker bytes behind them."""

    keys: set[str] = field(default_factory=set)
    objects: dict[str, bytes] = field(default_factory=dict)

    def list_keys(
        self,
        layer: str,
        kind: PartitionKind,
        tier: ZoomTier,
        *,
        year: int | None = None,
        month: int | None = None,
    ) -> tuple[str, ...]:
        """Return every held key under the requested tier/year/month prefix, sorted."""
        if year is None:
            prefix = zoom_prefix(layer, kind, tier)
        elif month is None:
            prefix = year_prefix(layer, kind, tier, year)
        else:
            prefix = month_prefix(layer, kind, tier, year, month)
        return tuple(sorted(key for key in self.keys if key.startswith(prefix)))

    def read_object(self, relative_key: str) -> bytes | None:
        """Return one held object's bytes, or `None`."""
        return self.objects.get(relative_key)

    def write_day(self, layer: str, kind: PartitionKind, tier: ZoomTier, day: date, *, complete: bool = True) -> str:
        """Add one day's part file, plus the completion marker that makes it servable."""
        part = partition_path(layer, kind, tier, day)
        self.keys.add(part)
        if complete:
            self.keys.add(completion_marker_path(layer, kind, tier, day))
        return part

    def write_absence(  # noqa: PLR0913 - one argument per partition coordinate, plus the evidence
        self,
        layer: str,
        kind: PartitionKind,
        tier: ZoomTier,
        day: date,
        *,
        reason: str,
        upstream_response: str,
        recorded_at: datetime,
        run_id: str,
    ) -> str:
        """Add one governed-absence marker and the evidence object behind it."""
        key = absence_marker_path(layer, kind, tier, day)
        self.keys.add(key)
        self.objects[key] = GovernedAbsence(
            reason=reason,
            upstream_response=upstream_response,
            recorded_at=recorded_at,
            run_id=run_id,
        ).to_json_bytes()
        return key


@dataclass
class FakeRowReader:
    """A `PartitionRowReader` answering from a dictionary, honouring the read's own row budget."""

    rows_by_key: dict[str, tuple[ServedRow, ...]] = field(default_factory=dict)
    unpositioned_rows: int = 0
    reads: list[RowRead] = field(default_factory=list)

    def read_rows(self, read: RowRead) -> RowReadResult:
        """Return the held rows for the requested keys, in key order, truncated to the budget."""
        self.reads.append(read)
        rows = tuple((key, row) for key in read.keys for row in self.rows_by_key.get(key, ()))
        return RowReadResult(
            rows=rows[: read.row_budget],
            budget_exhausted=len(rows) > read.row_budget,
            unpositioned_rows=self.unpositioned_rows,
        )


def instant(text: str) -> datetime:
    """Parse a fixture's ISO-8601 instant, accepting the `Z` designator the wire uses."""
    return datetime.fromisoformat(text.replace("Z", "+00:00"))

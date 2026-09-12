"""The fixture's typed Arrow port; see direct/weather_forecast/AGENTS.md."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Protocol, cast

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence


class ArrowTable(Protocol):
    """Only the materialized-table operations used by this bounded fixture."""

    def sort_by(self, sorting: list[tuple[str, str]]) -> ArrowTable: ...

    def to_pylist(self) -> list[object]: ...


class ArrowBuffer(Protocol):
    """Serialized bytes returned by Arrow's memory buffer."""

    def to_pybytes(self) -> bytes: ...


class ArrowBufferOutput(Protocol):
    """The writer's bounded in-memory serialization destination."""

    def getvalue(self) -> ArrowBuffer: ...


class ArrowTableFactory(Protocol):
    """Construct the table from typed source rows and its exact schema."""

    def from_pylist(self, rows: Sequence[Mapping[str, object]], *, schema: object) -> ArrowTable: ...


class ArrowModule(Protocol):
    """The installed untyped library's narrowly declared fixture surface."""

    Table: ArrowTableFactory
    BufferOutputStream: Callable[[], ArrowBufferOutput]
    BufferReader: Callable[[bytes], object]
    ArrowException: type[Exception]

    def schema(self, fields: list[object], *, metadata: dict[bytes, bytes]) -> object: ...

    def field(self, name: str, field_type: object, *, nullable: bool = True) -> object: ...

    def string(self) -> object: ...

    def float64(self) -> object: ...

    def int16(self) -> object: ...

    def timestamp(self, unit: str, *, tz: str) -> object: ...


class ParquetRowGroup(Protocol):
    """Declared decoded byte count checked before materialization."""

    total_byte_size: int


class ParquetMetadata(Protocol):
    """The physical row and decoded-size inventory."""

    num_rows: int

    def row_group(self, index: int) -> ParquetRowGroup: ...


class ArrowParquetFile(Protocol):
    """Bounded metadata-first Parquet input."""

    metadata: ParquetMetadata
    schema_arrow: object
    num_row_groups: int

    def read(self) -> ArrowTable: ...


class ParquetModule(Protocol):
    """Only the reader and writer used by the fixture."""

    ParquetFile: Callable[[object], ArrowParquetFile]

    def write_table(
        self, table: ArrowTable, destination: ArrowBufferOutput, *, compression: str, row_group_size: int
    ) -> None: ...


arrow = cast("ArrowModule", import_module("pyarrow"))
parquet = cast("ParquetModule", import_module("pyarrow.parquet"))

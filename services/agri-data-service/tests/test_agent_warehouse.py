"""Concurrency contracts for the agent's shared Parquet warehouse seam."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Any

import pytest

from agri_data_service.agent import warehouse
from tests.parquet_ops.fakes import FakeListing

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from agri_data_service.parquet_ops.duckdb_session import ServingSession
    from agri_data_service.parquet_ops.request_params import ReadScope


@dataclass
class _ThreadRecordingSource:
    authorized_threads: list[int] = field(default_factory=list)
    held: FakeListing = field(default_factory=FakeListing)

    def listing(self) -> FakeListing:
        return self.held

    async def run(self, work: Callable[[ServingSession], Any], *, operation: str) -> Any:
        del work, operation
        raise AssertionError("metadata-only concurrency tests must not open DuckDB")

    def availability_evidence(self, layers: Sequence[str], now: Any) -> tuple[Any, ...]:
        del layers, now
        return ()

    def authorized_listing(self, scope: ReadScope) -> FakeListing:
        del scope
        self.authorized_threads.append(threading.get_ident())
        return self.held


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["window", "years", "release"])
async def test_authority_loading_runs_off_the_event_loop(operation: str) -> None:
    source = _ThreadRecordingSource()
    token = warehouse.set_source(source)
    event_loop_thread = threading.get_ident()
    try:
        if operation == "window":
            await warehouse.lane_window(
                layer="signal",
                first_day=date(2026, 8, 1),
                last_day=date(2026, 8, 2),
            )
        elif operation == "years":
            await warehouse.lane_years(layer="drought", years=(2025, 2026))
        else:
            async def no_rows(_keys: tuple[str, ...], _listing: Any) -> tuple[Any, ...]:
                return ()

            await warehouse.release_rows(
                layer="drought",
                as_of=date(2026, 8, 2),
                row_limit=1,
                read=no_rows,
            )
    finally:
        warehouse.reset_source(token)

    assert source.authorized_threads
    assert all(thread_id != event_loop_thread for thread_id in source.authorized_threads)

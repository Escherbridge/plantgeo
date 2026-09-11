"""Contract tests for the interface-agnostic chunked-lane driver."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Self

import pytest

from agri_data_service.execution.chunked_lane import ChunkedLane, ChunkedLaneBackfillError

if TYPE_CHECKING:
    from datetime import datetime
    from pathlib import Path

_CONCURRENCY = 2


@dataclass(frozen=True)
class _Chunk:
    key: str


@dataclass(frozen=True)
class _Receipt:
    chunk_key: str


@dataclass(frozen=True)
class _Plan:
    cells: tuple[object, ...]
    chunks: tuple[_Chunk, ...]


@dataclass(frozen=True)
class _Checkpoint:
    state: str
    reason: str | None
    receipts: tuple[_Receipt, ...]
    updated_at: datetime | None = None

    def model_copy(self, *, update: dict[str, Any]) -> Self:
        return replace(self, **update)


async def _successful_runner(
    _plan: _Plan,
    _chunks: tuple[_Chunk, ...],
    *,
    concurrency: int,
) -> tuple[object | BaseException, ...]:
    del concurrency
    return ()


def _lane(
    plan: _Plan,
    checkpoint: _Checkpoint,
    *,
    runner: Any = _successful_runner,
    writes: list[_Checkpoint] | None = None,
) -> ChunkedLane[_Chunk, _Plan, _Checkpoint, object]:
    written = writes if writes is not None else []
    return ChunkedLane(
        error_token="fixture_chunks_failed",
        parse_plan=lambda _payload: plan,
        plan_checksum=lambda _plan: "a" * 64,
        checkpoint_path=lambda root, _plan: root / "checkpoint.json",
        initialize_checkpoint=lambda _plan: checkpoint,
        read_checkpoint=lambda _path: checkpoint,
        rederive_checkpoint=lambda _plan, value: value,
        write_checkpoint=lambda _path, value: written.append(value),
        load_cached_result=lambda _root, _plan, _chunk: None,
        cache_result=lambda _root, _plan, _result: None,
        record_result=lambda _plan, value, _result: value,
        run_chunks=runner,
        release_manifest=lambda _plan, _checkpoint: "b" * 64,
        failure_reason=str,
        status_identity=lambda _plan: {"domain": "fixture"},
        status_totals=lambda _plan, _checkpoint: {"observation_count": 0},
        backfill_extras=lambda _plan, _checkpoint: {"next_steps": ["persist"]},
    )


def test_status_payload_preserves_the_command_wire_shape(tmp_path: Path) -> None:
    plan_path = tmp_path / "plan.json"
    plan_path.write_text("{}", encoding="utf-8")
    plan = _Plan(cells=(object(), object()), chunks=(_Chunk("done"), _Chunk("pending")))
    checkpoint = _Checkpoint(state="running", reason=None, receipts=(_Receipt("done"),))

    payload = _lane(plan, checkpoint).status_payload(tmp_path, plan_path)

    assert payload == {
        "plan_checksum": "a" * 64,
        "checkpoint": str(tmp_path / "checkpoint.json"),
        "state": "running",
        "reason": None,
        "domain": "fixture",
        "cell_count": 2,
        "chunk_count": 2,
        "completed_chunk_count": 1,
        "pending_chunk_count": 1,
        "pending_chunks": ["pending"],
        "observation_count": 0,
    }


@pytest.mark.asyncio
async def test_failed_wave_returns_native_error_payload_and_records_blocked_checkpoint(tmp_path: Path) -> None:
    async def fail_runner(
        _plan: _Plan,
        chunks: tuple[_Chunk, ...],
        *,
        concurrency: int,
    ) -> tuple[object | BaseException, ...]:
        assert concurrency == _CONCURRENCY
        return tuple(ValueError("provider unavailable") for _chunk in chunks)

    plan_path = tmp_path / "plan.json"
    plan_path.write_text("{}", encoding="utf-8")
    plan = _Plan(cells=(object(),), chunks=(_Chunk("chunk-1"),))
    checkpoint = _Checkpoint(state="running", reason=None, receipts=())
    writes: list[_Checkpoint] = []

    with pytest.raises(ChunkedLaneBackfillError) as raised:
        await _lane(plan, checkpoint, runner=fail_runner, writes=writes).backfill_payload(
            tmp_path,
            plan_path,
            None,
            _CONCURRENCY,
        )

    assert raised.value.payload == {
        "checkpoint": str(tmp_path / "checkpoint.json"),
        "state": "running",
        "completed_chunk_count": 0,
        "chunk_count": 1,
        "pending_chunk_count": 1,
        "failed_chunks": [{"chunk_key": "chunk-1", "reason": "provider unavailable"}],
        "release_receipt_manifest_checksum": None,
        "next_steps": ["persist"],
        "error": "fixture_chunks_failed",
    }
    assert writes[-1].state == "blocked"
    assert writes[-1].reason == "1 chunk(s) failed; first: provider unavailable"

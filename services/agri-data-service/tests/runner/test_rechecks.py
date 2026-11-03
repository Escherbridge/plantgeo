"""Durable retry fairness and bounded conditional scheduling evidence."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from agri_data_service.pipeline.parquet.availability_index import BotoAvailabilityStorage
from agri_data_service.pipeline.runner.rechecks import SourceRechecks, due_days, recheck_key, recheck_scope
from tests.parquet.availability_documents import MemoryAvailabilityStorage
from tests.parquet.test_availability_index import ScriptedS3Client
from tests.runner.fakes import NOW, TODAY, grid_lane, spec_for

if TYPE_CHECKING:
    import pytest

SCOPE = "a" * 64
STREAM = "fixture-grid-value"


def test_year_shards_merge_monotonically_and_require_every_stream() -> None:
    storage = MemoryAvailabilityStorage()
    index = SourceRechecks(storage)
    other = TODAY - timedelta(days=400)
    index.confirm_many(STREAM, SCOPE, {TODAY: NOW, other: NOW}, now=NOW)
    assert len(storage.objects) == len({TODAY.year, other.year})
    index.confirm(STREAM, SCOPE, TODAY, NOW - timedelta(days=1), now=NOW)
    assert index.checked((STREAM,), SCOPE, other, TODAY) == {other: NOW, TODAY: NOW}
    assert index.checked((STREAM, "other"), SCOPE, other, TODAY) == {}
    assert index.checked((STREAM,), "b" * 64, other, TODAY) == {}


def test_cas_loser_preserves_newer_same_day_and_other_day(monkeypatch: pytest.MonkeyPatch) -> None:
    storage = MemoryAvailabilityStorage()
    index = SourceRechecks(storage)
    swap = storage.compare_and_swap
    injected = False
    other = TODAY - timedelta(days=1)
    current = NOW
    newer = NOW + timedelta(seconds=1)

    def clock() -> datetime:
        return current

    def interleave(key: str, payload: bytes, *, expected_etag: str | None, content_type: str) -> bool:
        nonlocal injected, current
        if not injected:
            injected = True
            current = newer
            index.confirm_many(STREAM, SCOPE, {TODAY: newer, other: newer}, now=clock)
        return swap(key, payload, expected_etag=expected_etag, content_type=content_type)

    monkeypatch.setattr(storage, "compare_and_swap", interleave)
    index.confirm(STREAM, SCOPE, TODAY, NOW - timedelta(days=1), now=clock)
    assert index.checked((STREAM,), SCOPE, other, TODAY) == {TODAY: newer, other: newer}


def test_corrupt_index_is_due_and_replaced_only_by_conditional_success() -> None:
    storage = MemoryAvailabilityStorage()
    key = recheck_key(STREAM, SCOPE, TODAY.year)
    storage.seed(key, b"invalid json")
    index = SourceRechecks(storage)
    assert index.checked((STREAM,), SCOPE, TODAY, TODAY) == {}
    index.confirm(STREAM, SCOPE, TODAY, NOW, now=NOW)
    assert index.checked((STREAM,), SCOPE, TODAY, TODAY) == {TODAY: NOW}


def test_missing_future_and_oldest_success_order_without_calendar_aliasing() -> None:
    days = tuple(TODAY - timedelta(days=offset) for offset in range(4))
    checks = {days[0]: NOW, days[1]: NOW - timedelta(days=40), days[2]: NOW + timedelta(days=1)}
    due, waiting = due_days(days, checks, now=NOW, interval_days=30)
    assert due == (days[3], days[2], days[1])
    assert waiting == {days[0]: NOW + timedelta(days=30)}


def test_future_timestamp_becomes_waiting_after_successful_evaluation() -> None:
    storage = MemoryAvailabilityStorage()
    index = SourceRechecks(storage)
    future = NOW + timedelta(days=100)
    index.confirm(STREAM, SCOPE, TODAY, future, now=future)
    checked = index.checked((STREAM,), SCOPE, TODAY, TODAY)
    assert due_days((TODAY,), checked, now=NOW, interval_days=7)[0] == (TODAY,)
    index.confirm(STREAM, SCOPE, TODAY, NOW, now=NOW)
    checked = index.checked((STREAM,), SCOPE, TODAY, TODAY)
    assert checked == {TODAY: NOW}
    assert due_days((TODAY,), checked, now=NOW, interval_days=7)[0] == ()


def test_support_and_contract_change_reopens_scheduling_but_history_does_not() -> None:
    lane = grid_lane(recurring_cost={"source_contract": "fixture-v1", "unresolved_recheck_days": 7})
    region = spec_for(lane).region
    units = frozenset({"west"})
    scope = recheck_scope(lane, region, units)
    assert scope != recheck_scope(lane, region, units | {"east"})
    assert lane.recurring_cost is not None
    changed = lane.model_copy(
        update={"recurring_cost": lane.recurring_cost.model_copy(update={"source_contract": "fixture-v2"})}
    )
    assert scope != recheck_scope(changed, region, units)
    assert lane.days is not None
    history = lane.model_copy(update={"days": lane.days.model_copy(update={"floor": TODAY})})
    assert scope == recheck_scope(history, region, units)


def test_real_boto_adapter_creates_and_updates_with_conditional_headers() -> None:
    client = ScriptedS3Client(put_results=[None, None], visible=[None])
    index = SourceRechecks(BotoAvailabilityStorage(bucket="bucket", client=client, prefix="sandbox/"))
    index.confirm(STREAM, SCOPE, TODAY, NOW, now=NOW)
    assert client.puts[0]["IfNoneMatch"] == "*"
    assert client.puts[0]["Key"] == "sandbox/" + recheck_key(STREAM, SCOPE, TODAY.year)
    payload = client.puts[0]["Body"]
    assert isinstance(payload, bytes)
    client.visible.append(payload)
    index.confirm(STREAM, SCOPE, TODAY, NOW + timedelta(days=1), now=NOW + timedelta(days=1))
    assert client.puts[1]["IfMatch"] == '"visible-etag"'

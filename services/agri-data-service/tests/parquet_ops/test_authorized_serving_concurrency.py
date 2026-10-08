"""Receipt verification fans out on one bounded pool yet admits, refuses and stages in input order."""

from __future__ import annotations

import tempfile
import threading
import time
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from agri_data_service.agent import warehouse as agent_warehouse
from agri_data_service.config import ObjectStoreCredentials
from agri_data_service.foundation.canonical import sha256_digest
from agri_data_service.foundation.parquet.paths import completion_marker_path, partition_path
from agri_data_service.interface.http import parquet_routes
from agri_data_service.parquet_ops import authorized_serving as serving
from agri_data_service.parquet_ops import faults
from agri_data_service.parquet_ops.request_params import ReadScope
from agri_data_service.pipeline.parquet.availability_index import EvidenceReceipt
from tests.parquet_ops.fakes import FakeListing, FakeRowReader

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from typing import Any

FIRST_DAY = date(2026, 8, 1)
SCOPE = ReadScope(layer="vegetation", kind="observed", tier=13, bbox=None)
TERMINAL = EvidenceReceipt(
    key="layer=vegetation/kind=observed/availability/evidence/terminal=" + "1" * 64 + ".json",
    sha256="1" * 64,
)


# A stalled GET is released by its test; this is only the backstop if a test fails before releasing it.
STALL_BACKSTOP_SECONDS = 10.0


@pytest.fixture(autouse=True)
def _no_straggler_crosses_tests() -> Iterator[None]:
    """Every test starts and ends with no abandoned fetch, since the count is process-wide."""
    _wait_for_stragglers()
    assert serving._ABANDONED_FETCHES.count == 0
    yield
    _wait_for_stragglers()
    assert serving._ABANDONED_FETCHES.count == 0


def _wait_for_stragglers() -> None:
    give_up = time.monotonic() + STALL_BACKSTOP_SECONDS * 2
    while serving._ABANDONED_FETCHES.count and time.monotonic() < give_up:
        time.sleep(0.01)


class _LatencyStore:
    """An R2 stand-in that sleeps per key and records every fetch's start/end and peak concurrency."""

    def __init__(
        self,
        objects: dict[str, bytes],
        *,
        latency: Callable[[str], float],
        hold: Callable[[str], object] = lambda _key: None,
    ) -> None:
        self.objects = objects
        self.latency = latency
        self.hold = hold
        self.events: list[tuple[str, str]] = []
        self.in_flight = 0
        self.max_in_flight = 0
        self._lock = threading.Lock()

    def read(self, key: str, *, max_bytes: int) -> Any:
        with self._lock:
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
            self.events.append(("start", key))
        try:
            self.hold(key)
            time.sleep(self.latency(key))
            payload = self.objects.get(key)
        finally:
            with self._lock:
                self.in_flight -= 1
                self.events.append(("end", key))
        if payload is None:
            return None
        assert len(payload) <= max_bytes
        return SimpleNamespace(payload=payload, etag="test", version_id=None)


class _Lane:
    """A published window: one part and one completion marker per day, receipts bound to true bytes."""

    def __init__(self, days: int) -> None:
        self.days = tuple(FIRST_DAY + timedelta(days=offset) for offset in range(days))
        self.parts = tuple(partition_path("vegetation", "observed", 13, day) for day in self.days)
        self.markers = tuple(completion_marker_path("vegetation", "observed", 13, day) for day in self.days)
        self.objects = {key: f"bytes of {key}".encode() for key in self.parts + self.markers}

    def index(self) -> Any:
        rows = tuple(
            SimpleNamespace(
                day=day,
                rung=13,
                terminal_state="published",
                provenance="digested",
                terminal_receipt=TERMINAL,
                data_receipts=(EvidenceReceipt(key=part, sha256=sha256_digest(self.objects[part])),),
                completion_receipt=EvidenceReceipt(key=marker, sha256=sha256_digest(self.objects[marker])),
            )
            for day, part, marker in zip(self.days, self.parts, self.markers, strict=True)
        )
        return SimpleNamespace(
            rows=rows,
            pointer=SimpleNamespace(identity=SimpleNamespace(), generation_bytes=len(rows), rows=len(rows)),
        )


class _StagedBytesReader(FakeRowReader):
    """Records the bytes behind each staged URI at the moment DuckDB would open them."""

    staged: tuple[tuple[str, bytes], ...] = ()

    def read_rows(self, read: Any) -> Any:
        assert read.object_uris is not None
        self.staged = tuple((key, Path(uri).read_bytes()) for key, uri in zip(read.keys, read.object_uris, strict=True))
        return super().read_rows(read)


def _stub_index(monkeypatch: pytest.MonkeyPatch, index: Any) -> None:
    monkeypatch.setattr(serving, "read_availability_pointer", lambda *_args, **_kwargs: index.pointer)
    monkeypatch.setattr(serving, "read_latest_availability", lambda *_args, **_kwargs: index)


def _read_window(store: _LatencyStore, lane: _Lane, reader: FakeRowReader) -> Any:
    return serving.resolve_authorized_window(
        serving.AuthorizedServingReader(store),
        FakeListing(),
        reader,
        scope=SCOPE,
        first_day=lane.days[0],
        last_day=lane.days[-1],
    )


def test_the_per_request_window_is_at_most_half_the_shared_pool() -> None:
    """The abandoned-fetch cap only leaves a live request a full window while window <= pool / 2."""
    assert 2 * serving._RECEIPT_FETCH_WINDOW <= serving._RECEIPT_FETCH_WORKERS


def test_window_fetches_concurrently_within_the_bound_and_stages_each_key_its_own_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lane = _Lane(days=20)
    _stub_index(monkeypatch, lane.index())
    position = {part: index for index, part in enumerate(lane.parts)}
    # Later parts finish first, so staging in completion order would hand a key another day's bytes.
    store = _LatencyStore(lane.objects, latency=lambda key: 0.003 * (len(lane.parts) - position.get(key, 0)))
    reader = _StagedBytesReader(rows_by_key={part: ({"cell_id": part},) for part in lane.parts})

    envelopes = _read_window(store, lane, reader)

    assert [envelope.to_wire()["state"] for envelope in envelopes] == ["published"] * len(lane.days)
    assert reader.staged == tuple((part, lane.objects[part]) for part in lane.parts)
    assert 1 < store.max_in_flight <= serving._RECEIPT_FETCH_WINDOW < serving._RECEIPT_FETCH_WORKERS


def test_every_completion_marker_is_verified_before_any_part_fetch_starts(monkeypatch: pytest.MonkeyPatch) -> None:
    lane = _Lane(days=12)
    _stub_index(monkeypatch, lane.index())
    store = _LatencyStore(lane.objects, latency=lambda key: 0.02 if key == lane.markers[-1] else 0.002)

    _read_window(store, lane, FakeRowReader())

    last_marker_end = max(
        position for position, (kind, key) in enumerate(store.events) if kind == "end" and key in lane.markers
    )
    first_part_start = min(
        position for position, (kind, key) in enumerate(store.events) if kind == "start" and key in lane.parts
    )
    assert last_marker_end < first_part_start


def test_corrupt_marker_refuses_before_any_part_is_fetched(monkeypatch: pytest.MonkeyPatch) -> None:
    lane = _Lane(days=12)
    _stub_index(monkeypatch, lane.index())
    lane.objects[lane.markers[5]] = b"rewritten after the generation was cut"
    store = _LatencyStore(lane.objects, latency=lambda _key: 0.002)
    reader = FakeRowReader()

    with pytest.raises(faults.ServingRefusalError) as caught:
        _read_window(store, lane, reader)

    assert caught.value.code == "availability_checksum_invalid"
    assert lane.markers[5] in caught.value.message
    assert not {key for _kind, key in store.events} & set(lane.parts)
    assert reader.reads == []


def test_first_failure_in_input_order_wins_even_when_a_later_one_lands_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lane = _Lane(days=12)
    _stub_index(monkeypatch, lane.index())
    write_times = _record_write_times(monkeypatch)
    slow_tampered, fast_missing = lane.parts[3], lane.parts[5]
    lane.objects[slow_tampered] = b"replacement bytes"
    del lane.objects[fast_missing]
    # Parts 4 and 6 share part 3's window and are still running when it fails; part 5 fails first.
    still_running = {lane.parts[4], lane.parts[6]}
    assert len({slow_tampered, fast_missing, *still_running}) <= serving._RECEIPT_FETCH_WINDOW
    store = _LatencyStore(
        lane.objects,
        latency=lambda key: 0.05 if key == slow_tampered else 0.2 if key in still_running else 0.001,
    )
    reader = FakeRowReader()

    with pytest.raises(faults.ServingRefusalError) as caught:
        _read_window(store, lane, reader)
    refused_at = time.perf_counter()  # high-resolution: monotonic ticks ~15.6 ms on Windows

    assert caught.value.code == "availability_checksum_invalid"
    assert slow_tampered in caught.value.message
    assert reader.reads == []
    _wait_for_stragglers()
    assert store.in_flight == 0
    assert max(write_times, default=0.0) < refused_at  # the siblings still running at the refusal wrote nothing


def test_overlapping_requests_share_one_process_wide_fetch_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    lane = _Lane(days=12)
    _stub_index(monkeypatch, lane.index())
    store = _LatencyStore(lane.objects, latency=lambda _key: 0.03)
    start = threading.Barrier(3)
    failures: list[BaseException] = []

    def request() -> None:
        start.wait()
        try:
            _read_window(store, lane, FakeRowReader())
        except BaseException as exc:  # surfaced through the assertion below
            failures.append(exc)

    requests = [threading.Thread(target=request) for _ in range(3)]
    for thread in requests:
        thread.start()
    for thread in requests:
        thread.join(timeout=30)

    assert failures == []
    assert store.max_in_flight <= serving._RECEIPT_FETCH_WORKERS
    pool_threads = [thread for thread in threading.enumerate() if thread.name.startswith("plantgeo-receipt-fetch")]
    assert len(pool_threads) <= serving._RECEIPT_FETCH_WORKERS


def test_a_request_stalled_on_r2_leaves_workers_for_an_overlapping_request(monkeypatch: pytest.MonkeyPatch) -> None:
    # More days than one window, so only the window (never the day count) can stop the stalled request at it.
    lane = _Lane(days=2 * serving._RECEIPT_FETCH_WINDOW)
    _stub_index(monkeypatch, lane.index())
    stalled = _LatencyStore(lane.objects, latency=lambda _key: 0.5)
    healthy = _LatencyStore(lane.objects, latency=lambda _key: 0.001)
    stalled_request = threading.Thread(target=_read_window, args=(stalled, lane, FakeRowReader()))
    stalled_request.start()
    deadline = time.monotonic() + 5
    while stalled.in_flight < serving._RECEIPT_FETCH_WINDOW and time.monotonic() < deadline:
        time.sleep(0.005)

    envelopes = _read_window(healthy, lane, FakeRowReader())

    # The healthy request finished while every stalled fetch was still hanging, so none queued behind them.
    assert ("end", lane.markers[0]) not in stalled.events
    assert [envelope.to_wire()["state"] for envelope in envelopes] == ["published"] * len(lane.days)
    stalled_request.join(timeout=30)
    assert stalled.max_in_flight == serving._RECEIPT_FETCH_WINDOW


def test_staging_past_its_deadline_refuses_as_a_timeout_and_leaves_no_staged_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    lane = _Lane(days=4)
    _stub_index(monkeypatch, lane.index())
    monkeypatch.setattr(serving, "_RECEIPT_STAGING_DEADLINE_SECONDS", 0.2)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    hung, hung_seconds = lane.parts[1], 1.0
    store = _LatencyStore(lane.objects, latency=lambda key: hung_seconds if key == hung else 0.001)
    reader = FakeRowReader()

    started = time.monotonic()
    with pytest.raises(faults.ServingRefusalError) as caught:
        _read_window(store, lane, reader)
    elapsed = time.monotonic() - started

    assert caught.value.code == "read_timed_out"
    assert elapsed < hung_seconds * 0.9  # refused at the deadline, not after the hung GET returned
    assert reader.reads == []
    give_up = time.monotonic() + 10
    while store.in_flight and time.monotonic() < give_up:
        time.sleep(0.01)
    assert list(tmp_path.iterdir()) == []  # the request removed its tree; the late fetch left nothing behind


def _record_write_times(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record when every file write is attempted, including one into an already-removed tree."""
    attempts: list[float] = []
    write_bytes = Path.write_bytes

    def recording_write_bytes(self: Path, data: Any) -> int:
        attempts.append(time.perf_counter())
        return write_bytes(self, data)

    monkeypatch.setattr(Path, "write_bytes", recording_write_bytes)
    return attempts


def test_a_definite_refusal_is_not_held_behind_a_stalled_sibling(monkeypatch: pytest.MonkeyPatch) -> None:
    lane = _Lane(days=4)
    _stub_index(monkeypatch, lane.index())
    corrupt, stalled = lane.markers[0], lane.markers[1]
    lane.objects[corrupt] = b"rewritten after the generation was cut"
    stalled_started, release = threading.Event(), threading.Event()

    def hold(key: str) -> None:
        if key == stalled:
            stalled_started.set()
            release.wait(STALL_BACKSTOP_SECONDS)
        elif key == corrupt:
            stalled_started.wait(STALL_BACKSTOP_SECONDS)  # fail only once its sibling is really running

    store = _LatencyStore(lane.objects, latency=lambda _key: 0.0, hold=hold)
    started = time.monotonic()
    try:
        with pytest.raises(faults.ServingRefusalError) as caught:
            _read_window(store, lane, FakeRowReader())
        elapsed = time.monotonic() - started
    finally:
        release.set()

    assert caught.value.code == "availability_checksum_invalid"
    assert corrupt in caught.value.message
    assert elapsed < STALL_BACKSTOP_SECONDS / 4  # answered at once, not when the stalled marker returned


def test_abandoned_stragglers_at_the_window_size_refuse_new_staging_until_they_drain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lane = _Lane(days=2 * serving._RECEIPT_FETCH_WINDOW)
    _stub_index(monkeypatch, lane.index())
    monkeypatch.setattr(serving, "_RECEIPT_STAGING_DEADLINE_SECONDS", 0.3)
    release = threading.Event()
    stalled = _LatencyStore(
        lane.objects,
        latency=lambda _key: 0.0,
        hold=lambda _key: release.wait(STALL_BACKSTOP_SECONDS),
    )
    healthy = _LatencyStore(lane.objects, latency=lambda _key: 0.001)
    try:
        with pytest.raises(faults.ServingRefusalError) as timed_out:
            _read_window(stalled, lane, FakeRowReader())
        assert timed_out.value.code == "read_timed_out"
        assert serving._ABANDONED_FETCHES.count == serving._RECEIPT_FETCH_WINDOW

        started = time.monotonic()
        with pytest.raises(faults.ServingRefusalError) as at_capacity:
            _read_window(healthy, lane, FakeRowReader())
        elapsed = time.monotonic() - started
    finally:
        release.set()

    assert at_capacity.value.code == "serving_at_capacity"
    assert elapsed < 1.0  # refused up front rather than queued behind the stragglers
    assert healthy.events == []
    _wait_for_stragglers()
    assert serving._ABANDONED_FETCHES.count == 0
    envelopes = _read_window(healthy, lane, FakeRowReader())
    assert [envelope.to_wire()["state"] for envelope in envelopes] == ["published"] * len(lane.days)


def test_a_straggler_released_while_its_tree_is_removed_writes_nothing_into_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lane = _Lane(days=2)
    _stub_index(monkeypatch, lane.index())
    corrupt, hung = lane.parts
    lane.objects[corrupt] = b"replacement bytes"
    hung_started, release, hung_staged = threading.Event(), threading.Event(), threading.Event()

    def hold(key: str) -> None:
        if key == hung:
            hung_started.set()
            release.wait(STALL_BACKSTOP_SECONDS)
        elif key == corrupt:
            hung_started.wait(STALL_BACKSTOP_SECONDS)  # refuse only once the hung part is really running

    stage = serving.AvailabilityAuthorizedListing._stage

    def stage_then_signal(listing: Any, receipt: EvidenceReceipt, *args: Any) -> int:
        try:
            return stage(listing, receipt, *args)
        finally:
            if receipt.key == hung:
                hung_staged.set()

    cleanup = tempfile.TemporaryDirectory.cleanup
    left_in_tree: list[str] = []

    def cleanup_after_the_straggler(directory: tempfile.TemporaryDirectory[str]) -> None:
        # The worst interleaving: the hung read returns and its worker finishes before the tree is removed.
        release.set()
        assert hung_staged.wait(STALL_BACKSTOP_SECONDS)
        left_in_tree.extend(sorted(entry.name for entry in Path(directory.name).iterdir()))
        cleanup(directory)

    monkeypatch.setattr(serving.AvailabilityAuthorizedListing, "_stage", stage_then_signal)
    monkeypatch.setattr(tempfile.TemporaryDirectory, "cleanup", cleanup_after_the_straggler)
    store = _LatencyStore(lane.objects, latency=lambda _key: 0.0, hold=hold)
    try:
        with pytest.raises(faults.ServingRefusalError) as caught:
            _read_window(store, lane, FakeRowReader())
    finally:
        release.set()

    assert caught.value.code == "availability_checksum_invalid"
    assert hung_staged.is_set()
    assert left_in_tree == []  # the hung part was verified after abandonment, and refused its write


def _settings_without_network() -> Any:
    credentials = ObjectStoreCredentials(
        endpoint_url="https://storage.example.com",
        region="auto",
        bucket="plantgeo-warehouse",
        access_key_id="test-access-key",
        secret_access_key="test-secret-key",
    )
    return SimpleNamespace(require_object_store=lambda: credentials, object_store_prefix="")


def test_the_serving_client_is_built_with_bounds_inside_the_staging_deadline() -> None:
    source = _settings_without_network()

    reader = serving.AuthorizedServingReaderHolder().get(source)

    config = reader._store.inner.client.meta.config
    attempts = config.retries["total_max_attempts"]
    assert attempts * (config.connect_timeout + config.read_timeout) < serving._RECEIPT_STAGING_DEADLINE_SECONDS
    # One connection per fetch worker: boto's default 10 would queue GETs inside botocore, past every bound.
    assert config.max_pool_connections >= serving._RECEIPT_FETCH_WORKERS
    # Ingestion builds the same storage without the serving bounds; long uploads keep botocore defaults.
    ingestion = serving.BotoAvailabilityStorage.from_settings(source).client.meta.config
    assert ingestion.read_timeout > config.read_timeout


def _route_listing(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setattr(parquet_routes, "settings", _settings_without_network())
    return parquet_routes._ListingHolder().get()


def _agent_listing(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setattr(agent_warehouse, "settings", _settings_without_network())
    return agent_warehouse._ObjectStoreSource().listing()


@pytest.mark.parametrize("build", [_route_listing, _agent_listing], ids=["parquet-routes", "agent-warehouse"])
def test_both_physical_listing_holders_use_the_serving_client_bounds(
    monkeypatch: pytest.MonkeyPatch, build: Callable[[pytest.MonkeyPatch], Any]
) -> None:
    # A manifest-trusted day's LIST runs before the staging deadline starts, so only these bounds end a stalled one.
    config = build(monkeypatch).backend.client.meta.config

    assert (config.connect_timeout, config.read_timeout, config.retries["total_max_attempts"]) == (
        serving.SERVING_CLIENT_CONFIG.connect_timeout,
        serving.SERVING_CLIENT_CONFIG.read_timeout,
        serving.SERVING_CLIENT_CONFIG.retries["total_max_attempts"],
    )
    assert config.max_pool_connections >= serving._RECEIPT_FETCH_WORKERS

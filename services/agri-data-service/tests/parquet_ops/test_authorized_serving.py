"""Time-bearing row reads are authorized by receipts, never by physical discovery."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from agri_data_service.agent.surfaces import SURFACE_PARQUET_LANES
from agri_data_service.foundation.canonical import sha256_digest
from agri_data_service.foundation.parquet.absence import GovernedAbsence
from agri_data_service.foundation.parquet.paths import absence_marker_path, completion_marker_path, partition_path
from agri_data_service.parquet_ops import authorized_serving as serving
from agri_data_service.parquet_ops import faults
from agri_data_service.parquet_ops.request_params import ReadScope
from agri_data_service.pipeline.parquet.availability_index import AvailabilityUnavailableError, EvidenceReceipt
from tests.parquet_ops.fakes import FakeListing, FakeRowReader

if TYPE_CHECKING:
    from typing import Any


class _ReadOnlyStore:
    """The narrow storage fake: any attempted mutation makes the GET-path test fail."""

    def __init__(self, objects: dict[str, bytes] | None = None) -> None:
        self.objects = objects or {}
        self.reads: list[str] = []

    def read(self, key: str, *, max_bytes: int) -> Any:
        self.reads.append(key)
        payload = self.objects.get(key)
        if payload is None:
            return None
        assert len(payload) <= max_bytes
        return SimpleNamespace(payload=payload, etag="test", version_id=None)

    def put_immutable(self, *_args: object, **_kwargs: object) -> None:
        raise AssertionError("a GET path attempted to publish")

    def compare_and_swap(self, *_args: object, **_kwargs: object) -> bool:
        raise AssertionError("a GET path attempted to advance a pointer")


def _index(*rows: object) -> Any:
    return SimpleNamespace(
        rows=rows,
        pointer=SimpleNamespace(identity=SimpleNamespace(), generation_bytes=max(1, len(rows)), rows=max(1, len(rows))),
    )


def _stub_index(monkeypatch: pytest.MonkeyPatch, index: Any) -> None:
    monkeypatch.setattr(serving, "read_availability_pointer", lambda *_args, **_kwargs: index.pointer)
    monkeypatch.setattr(serving, "read_latest_availability", lambda *_args, **_kwargs: index)


def _row(day: date, *, part: str, completion: str, part_payload: bytes, completion_payload: bytes) -> Any:
    return SimpleNamespace(
        day=day,
        rung=13,
        terminal_state="published",
        terminal_receipt=EvidenceReceipt(
            key="layer=vegetation/kind=observed/availability/evidence/terminal=" + "1" * 64 + ".json",
            sha256="1" * 64,
        ),
        data_receipts=(EvidenceReceipt(key=part, sha256=sha256_digest(part_payload)),),
        completion_receipt=EvidenceReceipt(key=completion, sha256=sha256_digest(completion_payload)),
    )


def test_day_reads_only_generation_receipts_and_ignores_physical_extra_parts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    day = date(2026, 8, 6)
    authorized_part = partition_path("vegetation", "observed", 13, day)
    completion = completion_marker_path("vegetation", "observed", 13, day)
    part_payload = b"receipt-bound parquet bytes"
    completion_payload = b"receipt-bound completion"
    extra_part = authorized_part.replace("part-0.parquet", "part-1.parquet")
    index = _index(
        _row(
            day,
            part=authorized_part,
            completion=completion,
            part_payload=part_payload,
            completion_payload=completion_payload,
        )
    )
    _stub_index(monkeypatch, index)
    physical = FakeListing(keys={authorized_part, extra_part, completion})
    rows = FakeRowReader(
        rows_by_key={authorized_part: ({"cell_id": "authorized"},), extra_part: ({"cell_id": "extra"},)}
    )
    scope = ReadScope(layer="vegetation", kind="observed", tier=13, bbox=None)

    answered = serving.resolve_authorized_day(
        serving.AuthorizedServingReader(
            _ReadOnlyStore({authorized_part: part_payload, completion: completion_payload})
        ),
        physical,
        rows,
        scope=scope,
        day=day,
    )

    assert answered.to_wire()["rows"] == [{"cell_id": "authorized"}]
    assert rows.reads[0].keys == (authorized_part,)
    assert rows.reads[0].object_uris is not None


def test_mutated_part_is_refused_before_the_row_reader_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    day = date(2026, 8, 6)
    part = partition_path("vegetation", "observed", 13, day)
    completion = completion_marker_path("vegetation", "observed", 13, day)
    original = b"original parquet"
    completion_payload = b"completion"
    index = _index(
        _row(
            day,
            part=part,
            completion=completion,
            part_payload=original,
            completion_payload=completion_payload,
        )
    )
    _stub_index(monkeypatch, index)
    rows = FakeRowReader(rows_by_key={part: ({"cell_id": "must-not-serve"},)})

    with pytest.raises(faults.ServingRefusalError) as caught:
        serving.resolve_authorized_day(
            serving.AuthorizedServingReader(_ReadOnlyStore({part: b"replacement", completion: completion_payload})),
            FakeListing(),
            rows,
            scope=ReadScope(layer="vegetation", kind="observed", tier=13, bbox=None),
            day=day,
        )

    assert caught.value.code == "availability_checksum_invalid"
    assert rows.reads == []


def test_verified_part_bytes_are_the_sources_given_to_the_row_reader(monkeypatch: pytest.MonkeyPatch) -> None:
    day = date(2026, 8, 6)
    part = partition_path("vegetation", "observed", 13, day)
    completion = completion_marker_path("vegetation", "observed", 13, day)
    payload = b"the exact authorized bytes"
    completion_payload = b"completion"
    index = _index(
        _row(
            day,
            part=part,
            completion=completion,
            part_payload=payload,
            completion_payload=completion_payload,
        )
    )
    _stub_index(monkeypatch, index)

    class InspectingReader(FakeRowReader):
        observed_payload: bytes | None = None

        def read_rows(self, read: Any) -> Any:
            assert read.object_uris is not None
            self.observed_payload = Path(read.object_uris[0]).read_bytes()
            return super().read_rows(read)

    rows = InspectingReader(rows_by_key={part: ({"cell_id": "verified"},)})
    serving.resolve_authorized_day(
        serving.AuthorizedServingReader(_ReadOnlyStore({part: payload, completion: completion_payload})),
        FakeListing(),
        rows,
        scope=ReadScope(layer="vegetation", kind="observed", tier=13, bbox=None),
        day=day,
    )

    assert rows.observed_payload == payload


def test_aggregate_staging_budget_stops_before_fetching_remaining_parts(monkeypatch: pytest.MonkeyPatch) -> None:
    day = date(2026, 8, 6)
    parts = tuple(partition_path("vegetation", "observed", 13, day, index) for index in range(3))
    payloads = (b"aaaaaa", b"bbbbbb", b"cccccc")
    completion = completion_marker_path("vegetation", "observed", 13, day)
    completion_payload = b"completion"
    row = SimpleNamespace(
        day=day,
        rung=13,
        terminal_state="published",
        terminal_receipt=EvidenceReceipt(
            key="layer=vegetation/kind=observed/availability/evidence/terminal=" + "1" * 64 + ".json",
            sha256="1" * 64,
        ),
        data_receipts=tuple(
            EvidenceReceipt(key=key, sha256=sha256_digest(payload))
            for key, payload in zip(parts, payloads, strict=True)
        ),
        completion_receipt=EvidenceReceipt(key=completion, sha256=sha256_digest(completion_payload)),
    )
    _stub_index(monkeypatch, _index(row))
    monkeypatch.setattr(serving, "MAX_VERIFIED_READ_BYTES", 10)
    store = _ReadOnlyStore({completion: completion_payload, **dict(zip(parts, payloads, strict=True))})

    with pytest.raises(faults.ServingRefusalError) as caught:
        serving.resolve_authorized_day(
            serving.AuthorizedServingReader(store),
            FakeListing(),
            FakeRowReader(),
            scope=ReadScope(layer="vegetation", kind="observed", tier=13, bbox=None),
            day=day,
        )

    assert caught.value.code == "read_over_budget"
    assert store.reads == [completion, parts[0], parts[1]]


def test_listing_absence_history_opens_no_terminal_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    days = (date(2026, 8, 5), date(2026, 8, 6))
    rows = tuple(
        SimpleNamespace(
            day=day,
            rung=13,
            terminal_state="governed_absence",
            terminal_receipt=EvidenceReceipt(
                key=f"layer=vegetation/kind=observed/availability/evidence/terminal={'1' * 62}{offset:02d}.json",
                sha256=f"{'1' * 62}{offset:02d}",
            ),
            data_receipts=(),
            completion_receipt=None,
        )
        for offset, day in enumerate(days)
    )
    _stub_index(monkeypatch, _index(*rows))
    monkeypatch.setattr(
        serving,
        "read_terminal_evidence",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unselected absence evidence was read")),
    )
    scope = ReadScope(layer="vegetation", kind="observed", tier=13, bbox=None)
    listing = serving.AuthorizedServingReader(_ReadOnlyStore()).listing(FakeListing(), scope=scope)

    keys = listing.list_keys("vegetation", "observed", 13, year=2026)
    first = next(listing.iter_tier_keys("vegetation", "observed", 13))

    assert len(keys) == len(days)
    assert first == absence_marker_path("vegetation", "observed", 13, days[0])


def test_missing_pointer_refuses_even_when_physical_day_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    day = date(2026, 8, 6)
    physical = FakeListing()
    physical.write_day("vegetation", "observed", 13, day)

    def missing(*_args: object, **_kwargs: object) -> Any:
        raise AvailabilityUnavailableError("availability_missing", "pointer missing")

    monkeypatch.setattr(serving, "read_availability_pointer", missing)
    monkeypatch.setattr(serving, "read_latest_availability", missing)

    with pytest.raises(faults.ServingRefusalError, match="physical objects alone") as caught:
        serving.resolve_authorized_day(
            serving.AuthorizedServingReader(_ReadOnlyStore()),
            physical,
            FakeRowReader(),
            scope=ReadScope(layer="vegetation", kind="observed", tier=13, bbox=None),
            day=day,
        )

    assert caught.value.code == "availability_unpublished"


def test_governed_absence_marker_is_read_by_terminal_receipt_and_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    day = date(2026, 8, 6)
    marker = absence_marker_path("vegetation", "observed", 13, day)
    payload = GovernedAbsence(
        reason="upstream_published_nothing",
        upstream_response="empty feed",
        recorded_at=datetime(2026, 8, 7, tzinfo=UTC),
        run_id="run-1",
    ).to_json_bytes()
    absence_receipt = EvidenceReceipt(key=marker, sha256=sha256_digest(payload))
    row = SimpleNamespace(
        day=day,
        rung=13,
        terminal_state="governed_absence",
        terminal_receipt=EvidenceReceipt(
            key="layer=vegetation/kind=observed/availability/evidence/terminal=" + "1" * 64 + ".json",
            sha256="1" * 64,
        ),
        data_receipts=(),
        completion_receipt=None,
    )
    _stub_index(monkeypatch, _index(row))
    monkeypatch.setattr(
        serving,
        "read_terminal_evidence",
        lambda *_args, **_kwargs: SimpleNamespace(absence_receipt=absence_receipt),
    )

    answered = serving.resolve_authorized_day(
        serving.AuthorizedServingReader(_ReadOnlyStore({marker: payload})),
        FakeListing(),
        FakeRowReader(),
        scope=ReadScope(layer="vegetation", kind="observed", tier=13, bbox=None),
        day=day,
    )

    assert answered.to_wire()["state"] == "governed_absence"


def test_static_lookup_preserves_the_physical_listing() -> None:
    physical = FakeListing()
    authority = serving.AuthorizedServingReader(_ReadOnlyStore())

    resolved = authority.listing(
        physical,
        scope=ReadScope(layer="watersheds", kind="observed", tier=13, bbox=None),
    )

    assert resolved is physical


def test_every_agent_served_lane_has_authorization_metadata() -> None:
    served = {lane for lanes in SURFACE_PARQUET_LANES.values() for lane in lanes}

    assert served <= set(serving._LANES)
    assert {lane for lane in served if serving._LANES[lane].nature == "static_lookup"} == {
        "evacuation-zones",
        "fire-perimeters",
        "soil-survey",
        "watersheds",
    }


def test_repeated_reads_refresh_the_pointer_but_parse_one_unchanged_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index = _index()
    pointer_reads = 0
    generation_reads = 0

    def pointer(*_args: object, **_kwargs: object) -> Any:
        nonlocal pointer_reads
        pointer_reads += 1
        return index.pointer

    def generation(*_args: object, **_kwargs: object) -> Any:
        nonlocal generation_reads
        generation_reads += 1
        return index

    monkeypatch.setattr(serving, "read_availability_pointer", pointer)
    monkeypatch.setattr(serving, "read_latest_availability", generation)
    authority = serving.AuthorizedServingReader(_ReadOnlyStore())
    scope = ReadScope(layer="vegetation", kind="observed", tier=13, bbox=None)
    instant = datetime(2026, 8, 7, tzinfo=UTC)

    authority.listing(FakeListing(), scope=scope, now=instant)
    authority.listing(FakeListing(), scope=scope, now=instant)

    expected_pointer_reads = 2
    assert pointer_reads == expected_pointer_reads
    assert generation_reads == 1


def test_parsed_generation_cache_evicts_by_cumulative_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(serving, "MAX_CACHED_INDEX_BYTES", 10)
    monkeypatch.setattr(serving, "MAX_CACHED_INDEX_ROWS", 10)
    authority = serving.AuthorizedServingReader(_ReadOnlyStore())
    first = SimpleNamespace(pointer=SimpleNamespace(generation_bytes=6, rows=4))
    second = SimpleNamespace(pointer=SimpleNamespace(generation_bytes=6, rows=4))

    authority._remember_index("first", first)
    authority._remember_index("second", second)

    assert authority._indexes == {"second": second}
    expected_bytes = 6
    expected_rows = 4
    assert authority._index_cache_bytes == expected_bytes
    assert authority._index_cache_rows == expected_rows

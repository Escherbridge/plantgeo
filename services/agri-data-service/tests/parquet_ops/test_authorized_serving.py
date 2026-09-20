"""Time-bearing row reads are authorized by receipts, never by physical discovery."""

from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from agri_data_service.foundation.canonical import sha256_digest
from agri_data_service.foundation.parquet.absence import GovernedAbsence
from agri_data_service.foundation.parquet.paths import absence_marker_path, completion_marker_path, partition_path
from agri_data_service.parquet_ops import faults
from agri_data_service.parquet_ops import authorized_serving as serving
from agri_data_service.parquet_ops.request_params import ReadScope
from agri_data_service.pipeline.parquet.availability_index import AvailabilityUnavailableError, EvidenceReceipt
from tests.parquet_ops.fakes import FakeListing, FakeRowReader

if TYPE_CHECKING:
    from typing import Any


class _ReadOnlyStore:
    """The narrow storage fake: any attempted mutation makes the GET-path test fail."""

    def __init__(self, objects: dict[str, bytes] | None = None) -> None:
        self.objects = objects or {}

    def read(self, key: str, *, max_bytes: int) -> Any:
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
    return SimpleNamespace(rows=rows, pointer=SimpleNamespace(identity=SimpleNamespace()))


def _row(day: date, *, part: str, completion: str) -> Any:
    return SimpleNamespace(
        day=day,
        rung=13,
        terminal_state="published",
        terminal_receipt=EvidenceReceipt(
            key="layer=signal/kind=observed/availability/evidence/terminal=" + "1" * 64 + ".json",
            sha256="1" * 64,
        ),
        data_receipts=(EvidenceReceipt(key=part, sha256="2" * 64),),
        completion_receipt=EvidenceReceipt(key=completion, sha256="3" * 64),
    )


def test_day_reads_only_generation_receipts_and_ignores_physical_extra_parts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    day = date(2026, 8, 6)
    authorized_part = partition_path("signal", "observed", 13, day)
    completion = completion_marker_path("signal", "observed", 13, day)
    extra_part = authorized_part.replace("part-0.parquet", "part-1.parquet")
    index = _index(_row(day, part=authorized_part, completion=completion))
    monkeypatch.setattr(serving, "read_latest_availability", lambda *_args, **_kwargs: index)
    physical = FakeListing(keys={authorized_part, extra_part, completion})
    rows = FakeRowReader(
        rows_by_key={authorized_part: ({"cell_id": "authorized"},), extra_part: ({"cell_id": "extra"},)}
    )
    scope = ReadScope(layer="signal", kind="observed", tier=13, bbox=None)

    answered = serving.resolve_authorized_day(
        serving.AuthorizedServingReader(_ReadOnlyStore()), physical, rows, scope=scope, day=day
    )

    assert answered.to_wire()["rows"] == [{"cell_id": "authorized"}]
    assert rows.reads[0].keys == (authorized_part,)


def test_missing_pointer_refuses_even_when_physical_day_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    day = date(2026, 8, 6)
    physical = FakeListing()
    physical.write_day("signal", "observed", 13, day)
    monkeypatch.setattr(
        serving,
        "read_latest_availability",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AvailabilityUnavailableError("availability_missing", "pointer missing")
        ),
    )

    with pytest.raises(faults.ServingRefusalError, match="physical objects alone") as caught:
        serving.resolve_authorized_day(
            serving.AuthorizedServingReader(_ReadOnlyStore()),
            physical,
            FakeRowReader(),
            scope=ReadScope(layer="signal", kind="observed", tier=13, bbox=None),
            day=day,
        )

    assert caught.value.code == "availability_unpublished"


def test_governed_absence_marker_is_read_by_terminal_receipt_and_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    day = date(2026, 8, 6)
    marker = absence_marker_path("signal", "observed", 13, day)
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
            key="layer=signal/kind=observed/availability/evidence/terminal=" + "1" * 64 + ".json",
            sha256="1" * 64,
        ),
        data_receipts=(),
        completion_receipt=None,
    )
    monkeypatch.setattr(serving, "read_latest_availability", lambda *_args, **_kwargs: _index(row))
    monkeypatch.setattr(
        serving,
        "read_terminal_evidence",
        lambda *_args, **_kwargs: SimpleNamespace(absence_receipt=absence_receipt),
    )

    answered = serving.resolve_authorized_day(
        serving.AuthorizedServingReader(_ReadOnlyStore({marker: payload})),
        FakeListing(),
        FakeRowReader(),
        scope=ReadScope(layer="signal", kind="observed", tier=13, bbox=None),
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

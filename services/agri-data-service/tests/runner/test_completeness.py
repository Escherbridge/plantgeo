"""Durable source debt across restarts, support changes, contention and malformed proofs."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, timedelta

import pytest

from agri_data_service.pipeline.runner.completeness import SourceCompleteness, completeness_key
from agri_data_service.pipeline.runner.receipts import DayReceipt, TurnReceiptError
from tests.parquet.availability_documents import MemoryAvailabilityStorage

STREAM = "water-gauges-daily"
DAY = date(1990, 9, 30)
UNITS = frozenset({"west", "east"})
FULL = DayReceipt(
    stream=STREAM,
    day=DAY,
    lane=STREAM,
    outcome="written",
    expected_units=len(UNITS),
    present_units=len(UNITS),
    expected_unit_ids=UNITS,
    present_unit_ids=UNITS,
)


def test_a_restarted_reader_requires_a_proof_for_the_current_support() -> None:
    storage = MemoryAvailabilityStorage()
    proofs = SourceCompleteness(storage)
    assert proofs.complete_days(STREAM, DAY, DAY, UNITS) == frozenset()
    proofs.confirm(FULL)

    restarted = SourceCompleteness(storage)
    assert restarted.complete_days(STREAM, DAY, DAY, UNITS) == frozenset({DAY})
    assert restarted.complete_days(STREAM, DAY, DAY, UNITS | {"north"}) == frozenset()
    restarted.invalidate(STREAM, DAY)
    restarted.confirm(replace(FULL, present_units=1, present_unit_ids=frozenset({"west"})))
    assert SourceCompleteness(storage).complete_days(STREAM, DAY, DAY, UNITS) == frozenset()


def test_a_count_only_legacy_receipt_is_readable_but_cannot_claim_proven_support() -> None:
    payload = json.loads(FULL.to_payload())
    del payload["expected_unit_ids"]
    del payload["present_unit_ids"]
    del payload["publication_state"]
    old = DayReceipt.from_payload(json.dumps(payload).encode())
    assert old.expected_units == len(UNITS)
    assert old.expected_unit_ids is None
    assert old.publication_state == "complete"
    storage = MemoryAvailabilityStorage()
    SourceCompleteness(storage).confirm(old)
    assert SourceCompleteness(storage).complete_days(STREAM, DAY, DAY, UNITS) == frozenset()


def test_a_pending_receipt_is_refused_by_every_v1_reader_while_v1_receipts_still_decode() -> None:
    """Review L6: every reader before 696f1ae5 accepted only `lane-turn-receipt-v1` and ignored
    `publication_state`, so it would read a pending receipt as complete. A receipt written now carries
    another version, which those readers refuse; this reader still decodes both shapes of v1."""
    pending = replace(FULL, publication_state="pending")
    written = json.loads(pending.to_payload())

    assert written["schema_version"] != "lane-turn-receipt-v1"
    assert DayReceipt.from_payload(pending.to_payload()).publication_state == "pending"
    v1_pending = {**written, "schema_version": "lane-turn-receipt-v1"}
    del v1_pending["source_resolved"]
    assert DayReceipt.from_payload(json.dumps(v1_pending).encode()).publication_state == "pending"
    v1_legacy = {key: value for key, value in v1_pending.items() if key != "publication_state"}
    assert DayReceipt.from_payload(json.dumps(v1_legacy).encode()).publication_state == "complete"
    with pytest.raises(TurnReceiptError, match="schema version"):
        DayReceipt.from_payload(json.dumps({**written, "schema_version": "lane-turn-receipt-v3"}).encode())


def test_a_pending_receipt_cannot_confirm_full_source_support() -> None:
    storage = MemoryAvailabilityStorage()
    SourceCompleteness(storage).confirm(replace(FULL, publication_state="pending"))

    assert SourceCompleteness(storage).complete_days(STREAM, DAY, DAY, UNITS) == frozenset()


@pytest.mark.parametrize("fault", ["scope", "year", "version", "date", "digest", "shape"])
def test_an_invalid_completeness_document_never_hides_source_debt(fault: str) -> None:
    storage = MemoryAvailabilityStorage()
    SourceCompleteness(storage).confirm(FULL)
    key = completeness_key(STREAM, DAY.year)
    document = json.loads(storage.objects[key].payload)
    if fault == "scope":
        document["stream"] = "another-stream"
    elif fault == "year":
        document["year"] = DAY.year + 1
    elif fault == "version":
        document["schema_version"] = "future"
    elif fault == "date":
        document["complete_days"] = {"1991-01-01": "a" * 64}
    elif fault == "digest":
        document["complete_days"][DAY.isoformat()] = "unproven"
    else:
        document["surprise"] = True
    storage.seed(key, json.dumps(document).encode())

    with pytest.raises(TurnReceiptError):
        SourceCompleteness(storage).complete_days(STREAM, DAY, DAY, UNITS)


def test_a_cas_loser_reloads_and_keeps_another_days_proof(monkeypatch: pytest.MonkeyPatch) -> None:
    storage = MemoryAvailabilityStorage()
    swap = storage.compare_and_swap
    competitor = replace(FULL, day=DAY + timedelta(days=1))
    injected = False

    def interleave(key: str, payload: bytes, *, expected_etag: str | None, content_type: str) -> bool:
        nonlocal injected
        if not injected:
            injected = True
            SourceCompleteness(storage).confirm(competitor)
        return swap(key, payload, expected_etag=expected_etag, content_type=content_type)

    monkeypatch.setattr(storage, "compare_and_swap", interleave)
    SourceCompleteness(storage).confirm(FULL)

    assert SourceCompleteness(storage).complete_days(STREAM, DAY, competitor.day, UNITS) == frozenset(
        {DAY, competitor.day}
    )

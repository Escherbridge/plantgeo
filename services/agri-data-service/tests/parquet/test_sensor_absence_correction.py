"""Correction candidates preserve exact physical evidence and refuse unknown recovery states."""

from __future__ import annotations

import io
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest

from agri_data_service.pipeline.parquet.availability_extension import (
    _claim_from_finalized,
    _DayClaim,
    _retry_marker_payload,
)
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from agri_data_service.pipeline.parquet.sensor_absence_correction import (
    DAYS,
    LANE_ROOT,
    RUNGS,
    build_ladder,
    check_known_objects,
    finalized,
    ledger_wire,
    verify_physical,
    verify_quiescence,
    verify_retry,
)
from agri_data_service.warehouse.schemas.sensors import SENSORS_SCHEMA

PUBLISHED = datetime(2026, 9, 10, 20, tzinfo=UTC)
REQUEST_SHA = "a" * 64
STATION_LONGITUDE = -116.2228


def _candidate(day: date = DAYS[0]) -> bytes:
    observed = datetime(day.year, day.month, day.day, 23, tzinfo=UTC)
    table = pa.Table.from_pylist(
        [
            {
                "sensor_id": "KBOI",
                "station_name": "Boise",
                "network": "ASOS",
                "observed_day": day,
                "observed_at": observed,
                "measurement_name": "temperature",
                "value": 20.0,
                "unit_code": "wmoUnit:degC",
                "quality_control": "S",
                "feature_id": f"direct:KBOI:{observed.isoformat()}",
                "data_available_at": None,
                "station_longitude": -116.2228,
                "station_latitude": 43.5644,
            }
        ],
        schema=SENSORS_SCHEMA.arrow_schema,
    )
    output = io.BytesIO()
    pq.write_table(table, output)
    return output.getvalue()


def _proof() -> dict[str, Any]:
    return {
        "schema_version": "sensors-correction-quiescence/v1",
        "request_sha256": REQUEST_SHA,
        "lane_root": LANE_ROOT,
        "no_inflight_retry_workers": True,
        "writers_stopped": True,
        "operator": "reviewed operator",
        "evidence": ["process-census-sha256"],
        "observed_at": PUBLISHED.isoformat(),
        "valid_until": (PUBLISHED + timedelta(minutes=30)).isoformat(),
    }


def test_real_writer_candidates_are_deterministic_and_read_back_at_every_rung() -> None:
    first, ledger = build_ladder(_candidate(), day=DAYS[0], run_id="repair", published_at=PUBLISHED)
    second, repeated = build_ladder(_candidate(), day=DAYS[0], run_id="repair", published_at=PUBLISHED)
    assert first.objects == second.objects
    assert ledger_wire(ledger) == ledger_wire(repeated)
    assert {receipt.zoom for receipt in ledger.completions.values()} == set(RUNGS)
    verify_physical(ObjectStore(first), second.objects, first.objects, DAYS[0])
    row = ObjectStore(first).read_partition("sensors", "observed", 13, DAYS[0]).to_pylist()[0]
    assert row["quality_control"] == "S"
    assert row["data_available_at"] is None
    assert row["station_longitude"] == STATION_LONGITUDE


@pytest.mark.parametrize("current", [{}, {"old": b"changed"}, {"old": b"absence", "new": b"candidate"}])
def test_missing_or_changed_originals_require_a_durable_mutation_phase(current: dict[str, bytes]) -> None:
    with pytest.raises(ValueError, match="original physical inventory changed"):
        check_known_objects(current, {"old": b"absence"}, {"new": b"candidate"}, mutation_started=False)


def test_resume_accepts_only_prepared_transition_bytes() -> None:
    original, candidate = {"old": b"absence"}, {"new": b"candidate"}
    for current in ({}, original, candidate, original | candidate):
        check_known_objects(current, original, candidate, mutation_started=True)
    with pytest.raises(ValueError, match="unknown transition object"):
        check_known_objects({"new": b"foreign"}, original, candidate, mutation_started=True)
    with pytest.raises(ValueError, match="unknown transition object"):
        check_known_objects({"unrelated": b"candidate"}, original, candidate, mutation_started=True)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("writers_stopped", False),
        ("no_inflight_retry_workers", False),
        ("request_sha256", "b" * 64),
        ("evidence", []),
        ("operator", ""),
    ],
)
def test_quiescence_requires_explicit_matching_evidence(field: str, value: object) -> None:
    proof = _proof()
    proof[field] = value
    with pytest.raises(ValueError, match=r"quiescence|proof scope mismatch"):
        verify_quiescence(proof, request_sha=REQUEST_SHA, now=PUBLISHED)


def test_quiescence_cannot_be_inferred_from_an_expired_receipt() -> None:
    verify_quiescence(_proof(), request_sha=REQUEST_SHA, now=PUBLISHED)
    with pytest.raises(ValueError, match="expired or future"):
        verify_quiescence(_proof(), request_sha=REQUEST_SHA, now=PUBLISHED + timedelta(hours=1))


def test_durable_finalized_replay_preserves_timestamp_and_refuses_foreign_retry() -> None:
    _, ledger = build_ladder(_candidate(), day=DAYS[0], run_id="repair", published_at=PUBLISHED)
    plan = {
        "days": {str(DAYS[0]): {"ledger": ledger_wire(ledger)}},
        "published_at": PUBLISHED.isoformat(),
        "run_id": "repair",
        "source_ceiling": str(DAYS[1]),
    }
    outcome = finalized(plan, DAYS[0], REQUEST_SHA)
    assert outcome.published_at == PUBLISHED
    assert outcome.written == ledger
    claim = _claim_from_finalized(outcome, lane="sensors", kind="observed", lane_root=LANE_ROOT, day=DAYS[0])
    assert isinstance(claim, _DayClaim)
    payload = _retry_marker_payload(claim, error_kind="publication_pending", recorded_at=PUBLISHED)
    verify_retry(payload, outcome)
    with pytest.raises(ValueError, match="differs from durable"):
        verify_retry(payload.replace(b'"run_id":"repair"', b'"run_id":"foreign"'), outcome)


def test_physical_readback_refuses_even_one_changed_byte() -> None:
    memory, _ = build_ladder(_candidate(), day=DAYS[0], run_id="repair", published_at=PUBLISHED)
    wrong = dict(memory.objects)
    key = next(iter(wrong))
    wrong[key] += b"changed"
    with pytest.raises(ValueError, match="differs from prepared"):
        verify_physical(ObjectStore(memory), memory.objects, wrong, DAYS[0])

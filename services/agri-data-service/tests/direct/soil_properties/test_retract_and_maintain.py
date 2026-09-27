"""The rollback verb's order and confirmation, and maintain's read-only drift and state report.

Rationale: the lane's AGENTS.md, "Maintain and Retract".
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any, Final

import pytest

from agri_data_service.pipeline.direct.soil_properties import maintain, retract
from agri_data_service.pipeline.direct.soil_properties.products import RELEASE_DAY, RELEASE_ID, SOURCE_FILE_PINS
from agri_data_service.pipeline.direct.soil_properties.source import ProbedFile, SoilPropertiesPipelineError

TODAY: Final = date(2026, 9, 27)
OK: Final = 200


class _RecordingStore:
    """Records every marker clear and rung retraction in call order."""

    def __init__(self, *, failing_tier: int | None = None, markers: dict[int, Any] | None = None) -> None:
        self.calls: list[tuple[str, int]] = []
        self.failing_tier = failing_tier
        self.markers = markers or {}

    def list_day_parts(self, layer: str, kind: str, zoom: int, day: date) -> tuple[str, ...]:
        assert (layer, kind, day) == ("soil-properties", "observed", RELEASE_DAY)
        return (f"z{zoom}/part-0",)

    def read_completion_marker(self, layer: str, kind: str, zoom: int, day: date) -> Any:
        assert (layer, kind, day) == ("soil-properties", "observed", RELEASE_DAY)
        return self.markers.get(zoom)

    def clear_completion_marker(self, layer: str, kind: str, zoom: int, day: date) -> None:
        del layer, kind, day
        self.calls.append(("clear_marker", zoom))

    def retract_partition_tier(self, layer: str, kind: str, zoom: int, day: date) -> Any:
        del layer, kind, day
        self.calls.append(("retract", zoom))
        failures = (f"z{zoom}/part-0: denied",) if zoom == self.failing_tier else ()
        return SimpleNamespace(removed=(f"z{zoom}/part-0",), failures=failures)


def test_retraction_clears_every_marker_before_emptying_coarse_then_base() -> None:
    store = _RecordingStore()
    removed = retract.retract_rungs(store)
    assert store.calls == [
        ("clear_marker", 0),
        ("clear_marker", 5),
        ("clear_marker", 9),
        ("clear_marker", 13),
        ("retract", 0),
        ("retract", 5),
        ("retract", 9),
        ("retract", 13),
    ]
    assert removed == {"0": 1, "5": 1, "9": 1, "13": 1}


def test_a_failed_delete_is_fatal_and_named() -> None:
    with pytest.raises(SoilPropertiesPipelineError, match="z9/part-0: denied"):
        retract.retract_rungs(_RecordingStore(failing_tier=9))


def test_the_plan_reads_every_rung_without_changing_anything() -> None:
    store = _RecordingStore(markers={13: object()})
    plan = retract.retraction_plan(store)
    assert plan["13"] == {"parts": 1, "marked": True}
    assert plan["0"]["marked"] is False
    assert store.calls == []


async def test_retract_is_a_dry_run_unless_confirmed_by_the_exact_release(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _RecordingStore()
    monkeypatch.setattr(retract.ObjectStore, "from_settings", classmethod(lambda _cls: store))
    dry = await retract.retract_release(SimpleNamespace(confirm=None))  # type: ignore[arg-type]
    assert dry["status"] == "dry_run"
    assert RELEASE_ID in dry["detail"]
    with pytest.raises(SoilPropertiesPipelineError, match="--confirm must be exactly"):
        await retract.retract_release(SimpleNamespace(confirm="soilgrids-v2.0"))  # type: ignore[arg-type]
    assert store.calls == []


def _probe(*, etag: str | None = None) -> list[ProbedFile]:
    return [
        ProbedFile(
            pin=pin,
            status=OK,
            last_modified=pin.last_modified,
            etag=etag if etag is not None and index == 0 else pin.etag,
            content_length=pin.content_length,
        )
        for index, pin in enumerate(SOURCE_FILE_PINS)
    ]


def test_maintain_reports_pinned_or_a_suspected_new_release() -> None:
    pinned = maintain.maintenance_report(_probe(), {"static_lane_state": "owed"})
    assert pinned["status"] == "pinned"
    assert pinned["action"] == "none"
    drifted = maintain.maintenance_report(_probe(etag='"new"'), {"static_lane_state": "current"})
    assert drifted["status"] == "drift_suspected"
    assert "republish" in drifted["action"]
    assert "owed" not in drifted["action"], "drift is a republish, never an owed day"


def test_an_unwritten_lane_owes_its_one_version_and_a_published_one_is_current() -> None:
    unwritten = maintain.lane_state(_RecordingStore(), today=TODAY)  # type: ignore[arg-type]
    assert unwritten["version_day"] == RELEASE_DAY.isoformat()
    assert all(rung is None for rung in unwritten["rungs"].values())
    base = SimpleNamespace(row_count=10, part_count=1, completed_at=datetime(2026, 10, 1, tzinfo=UTC))
    markers = dict.fromkeys((0, 5, 9, 13), base)
    published = maintain.lane_state(_RecordingStore(markers=markers), today=TODAY)  # type: ignore[arg-type]
    assert published["static_lane_state"] == "current"

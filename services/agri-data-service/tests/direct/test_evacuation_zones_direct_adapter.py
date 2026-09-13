"""The lane adapter: what it writes, what it retracts, and the sentence it refuses to publish.

Every store here is an in-memory `RecordingBackend` -- these tests open no socket, no bucket and no
database, matching the adapter itself, which touches Postgres only to roll back the timeout
transaction it is handed.
"""

# ruff: noqa: PLR2004 - the small literal counts ARE the assertion; naming each one hides it.

from __future__ import annotations

import ast
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

import agri_data_service.pipeline.direct.evacuation_zones as package
from agri_data_service.foundation.parquet.absence import GovernedAbsence
from agri_data_service.foundation.parquet.zoom import ZOOM_TIERS
from agri_data_service.pipeline.constants import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.direct.evacuation_zones.adapter import (
    _ABSENCE_LADDER_TIERS,
    DirectEvacuationZonesAdapter,
    DirectEvacuationZonesError,
)
from agri_data_service.pipeline.direct.evacuation_zones.products import (
    DIRECT_QUERY_URL,
    EVACUATION_ZONES_DIRECT_KIND,
    EVACUATION_ZONES_STREAM,
)
from agri_data_service.pipeline.direct.evacuation_zones.rows import evacuation_zones_table
from agri_data_service.pipeline.direct.evacuation_zones.source import EvacuationZonesSource
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from tests.parquet.test_objectstore_writer import RecordingBackend

RUN_ID = "evacuation-zones-adapter-test"
FETCHED_AT = datetime(2026, 9, 3, 17, 30, tzinfo=UTC)
DAY = date(2026, 9, 3)

SQUARE: dict[str, Any] = {
    "type": "Polygon",
    "coordinates": [[[-123.0, 44.0], [-122.9, 44.0], [-122.9, 44.1], [-123.0, 44.1], [-123.0, 44.0]]],
}


class RollbackOnlySession:
    """The whole Postgres surface this adapter uses: one rollback of the statement-timeout transaction."""

    def __init__(self) -> None:
        self.rollbacks = 0

    async def rollback(self) -> None:
        self.rollbacks += 1


def zone(global_id: str) -> dict[str, Any]:
    return {
        "globalId": global_id,
        "evacuationAreaName": "Milepost 97",
        "fireName": "Milepost 97 Fire",
        "county": "Douglas",
        "hazardType": "Wildfire",
        "editorName": "OEM Sync",
        "evacuationLevel": 3,
        "evacuationLevelLabel": "Go Now",
        "severity": "critical",
        "structuresWithin": 12.0,
        "addressesWithin": 30.0,
        "populationWithin": 74.0,
        "createdAt": datetime(2025, 4, 14, tzinfo=UTC),
        "createdDate": "2025-04-14T00:00:00Z",
        "lastEditedDate": "2026-09-03T17:29:00Z",
        "geometry": SQUARE,
    }


def source(*global_ids: str) -> EvacuationZonesSource:
    return EvacuationZonesSource(
        bbox="-125,42,-111,49", fetched_at=FETCHED_AT, zones=tuple(zone(global_id) for global_id in global_ids)
    )


def adapter_for(*global_ids: str) -> DirectEvacuationZonesAdapter:
    captured = source(*global_ids)
    return DirectEvacuationZonesAdapter(table=evacuation_zones_table(captured, snapshot_day=DAY), source=captured)


@pytest.mark.asyncio
async def test_a_captured_snapshot_writes_its_base_rung_and_nothing_else() -> None:
    store = ObjectStore(RecordingBackend())

    result = await adapter_for("{A}", "{B}")(RollbackOnlySession(), store, day=DAY, run_id=RUN_ID)

    assert result.part_count == 1
    assert result.row_count == 2
    assert result.absence_recorded is False
    assert store.partition_exists(
        EVACUATION_ZONES_STREAM, EVACUATION_ZONES_DIRECT_KIND, LANE_BASE_ZOOM_TIER, DAY, part_index=0
    )
    assert not store.absence_exists(EVACUATION_ZONES_STREAM, EVACUATION_ZONES_DIRECT_KIND, LANE_BASE_ZOOM_TIER, DAY)


@pytest.mark.asyncio
async def test_a_statewide_quiet_day_is_a_governed_absence_that_names_the_upstream_it_asked() -> None:
    """DO NOT DELETE. `gap_fill._govern_absent_day` would have published a factual lie here.

    That helper's `upstream_response` states "THIS RUN DID NOT CONTACT THE UPSTREAM SOURCE SYSTEM --
    this records what Postgres held at export time". For a source-direct writer both halves are
    false, and a governed absence is exactly the object a later reader trusts to say what was asked
    and what answered. So the adapter writes its own marker rather than letting `EmptyPartitionError`
    bubble into that path.
    """
    store = ObjectStore(RecordingBackend())

    result = await adapter_for()(RollbackOnlySession(), store, day=DAY, run_id=RUN_ID)

    assert result.absence_recorded is True
    absence = store.read_absence(EVACUATION_ZONES_STREAM, EVACUATION_ZONES_DIRECT_KIND, LANE_BASE_ZOOM_TIER, DAY)
    assert absence is not None
    payload = json.loads(absence.upstream_response)
    assert payload["requested_url"] == DIRECT_QUERY_URL
    assert payload["requested_bbox"] == "-125,42,-111,49"
    assert payload["zones_returned"] == 0
    assert payload["fetched_at"] == FETCHED_AT.isoformat()
    assert "DID NOT CONTACT" not in absence.upstream_response


@pytest.mark.asyncio
async def test_a_quiet_day_governs_the_whole_ladder_not_just_the_base_rung() -> None:
    """DO NOT DELETE. A base-only marker is stale evacuation levels at coarse zoom.

    `planes/evacuation_zones.py` resolves "as of" ONE TIER at a time, so a z9 rung left `missing`
    through a quiet spell answers a coarse-zoom viewport with the last snapshot that HAD zones --
    drawing evacuation areas over a state that has stood down. `gap_fill._govern_absent_day` writes
    all four rungs for this reason and this adapter cannot reach it, so it writes them itself.
    """
    store = ObjectStore(RecordingBackend())

    await adapter_for()(RollbackOnlySession(), store, day=DAY, run_id=RUN_ID)

    for tier in ZOOM_TIERS:
        assert store.absence_exists(EVACUATION_ZONES_STREAM, EVACUATION_ZONES_DIRECT_KIND, tier, DAY), tier


@pytest.mark.asyncio
async def test_a_same_day_stand_down_is_refused_rather_than_deleting_a_published_snapshot() -> None:
    """DO NOT DELETE. An empty answer from a glitching feed looks exactly like a real stand-down.

    `planes/evacuation_zones.py` renders a governed absence as an affirmative "zero active zones, a
    normal quiet state", so publishing one wrongly tells someone inside a Level 3 area there is no
    evacuation. Governing a day that already holds zones would first require DELETING them, so the
    adapter refuses and names the admin action instead. Only reachable when zones were published
    earlier the SAME UTC day; any other day the ladder writes cleanly.
    """
    store = ObjectStore(RecordingBackend())
    await adapter_for("{A}")(RollbackOnlySession(), store, day=DAY, run_id=RUN_ID)

    with pytest.raises(DirectEvacuationZonesError, match="already holds published zones"):
        await adapter_for()(RollbackOnlySession(), store, day=DAY, run_id=RUN_ID)

    assert not store.absence_exists(EVACUATION_ZONES_STREAM, EVACUATION_ZONES_DIRECT_KIND, LANE_BASE_ZOOM_TIER, DAY)
    assert store.partition_exists(
        EVACUATION_ZONES_STREAM, EVACUATION_ZONES_DIRECT_KIND, LANE_BASE_ZOOM_TIER, DAY, part_index=0
    )


def test_the_absence_ladder_writes_coarse_rungs_first_and_the_base_rung_last() -> None:
    """The ORDER is the safety property: an interrupted run must leave the day `missing`, never
    covered-but-empty above the base rung. Same reason `gap_fill._ABSENCE_LADDER_TIERS` is ordered."""
    assert _ABSENCE_LADDER_TIERS[-1] == LANE_BASE_ZOOM_TIER
    assert set(_ABSENCE_LADDER_TIERS) == set(ZOOM_TIERS)


@pytest.mark.asyncio
async def test_an_absence_a_new_capture_disproves_is_retracted_at_every_rung_before_the_write() -> None:
    """A base-only retraction leaves three coarse rungs claiming a quiet spell that has just ended --
    which on this layer means three of four resolutions showing no evacuation where there is one."""
    store = ObjectStore(RecordingBackend())
    for tier in ZOOM_TIERS:
        store.write_absence(
            GovernedAbsence(
                reason="quiet", upstream_response="{}", recorded_at=datetime(2026, 9, 2, tzinfo=UTC), run_id=RUN_ID
            ),
            layer=EVACUATION_ZONES_STREAM,
            kind=EVACUATION_ZONES_DIRECT_KIND,
            zoom=tier,
            day=DAY,
        )

    await adapter_for("{A}")(RollbackOnlySession(), store, day=DAY, run_id=RUN_ID)

    for tier in ZOOM_TIERS:
        assert not store.absence_exists(EVACUATION_ZONES_STREAM, EVACUATION_ZONES_DIRECT_KIND, tier, DAY)


@pytest.mark.asyncio
async def test_a_snapshot_over_the_part_bound_spills_into_contiguous_parts_from_zero() -> None:
    store = ObjectStore(RecordingBackend())
    captured = source(*[f"{{Z{index:03d}}}" for index in range(250)])
    adapter = DirectEvacuationZonesAdapter(table=evacuation_zones_table(captured, snapshot_day=DAY), source=captured)

    result = await adapter(RollbackOnlySession(), store, day=DAY, run_id=RUN_ID)

    assert result.part_count == 2
    assert result.row_count == 250


@pytest.mark.asyncio
async def test_the_adapter_rolls_the_statement_timeout_transaction_back_before_touching_the_store() -> None:
    session = RollbackOnlySession()

    await adapter_for("{A}")(session, ObjectStore(RecordingBackend()), day=DAY, run_id=RUN_ID)

    assert session.rollbacks == 1


def test_no_module_in_this_package_ever_constructs_terminal_evidence() -> None:
    """DO NOT DELETE. A forward writer must let `TerminalEvidence.provenance` default to digested.

    The bootstrap compiler passes `provenance=` because it IS the bootstrap path; a chokepoint guard
    now refuses a trusted row from anywhere else, so a slip fails loudly rather than silently -- but
    the cheapest place to catch it is before it is ever written. Receipts and digests here come
    entirely from `gap_fill.py`'s own written-object ledger, so this package has no reason to name
    the type at all: never constructing it is a stronger guarantee than never passing the keyword.
    """
    banned = {"TerminalEvidence", "provenance"}
    for module in Path(package.__file__).parent.glob("*.py"):
        # Parsed rather than grepped: this package's own docstrings DESCRIBE the trap at length, and
        # a substring search would fail on the prose that exists to prevent it.
        tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        keywords = {node.arg for node in ast.walk(tree) if isinstance(node, ast.keyword)}
        assert not (banned & (names | attributes | keywords)), module.name

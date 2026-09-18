"""The direct water-gauges forward adapter: the status branches and governed-absence reconciliation.

`merge_water_gauges_day`'s own merge semantics (append, refresh, ambiguous-refresh refusal) are
pinned where they were; this file pins the ADAPTER's branch over a `status=absent` day, which until
2026-09-15 raised outright -- the construct that held `sensors-direct-forward`'s breaker for a week.
Mirrors `test_sensors_direct_adapter.py::TestGovernedAbsenceReconciliation`: rows disprove a marker at
every tier with provenance kept; a fetch with NO rows never touches one; `conflict` and an undecodable
marker stay an admin's call. Nothing here touches the publisher-named-day arithmetic
(`publisher_named_day`), which is a known client-contract trap and is deliberately left alone.
"""

# ruff: noqa: PLR2004 - the small literal counts ARE the assertion; naming each one hides it.

from __future__ import annotations

import asyncio
import json
from datetime import UTC, date, datetime, timedelta

import pyarrow as pa  # type: ignore[import-untyped]
import pytest

from agri_data_service.foundation.parquet.absence import GovernedAbsence
from agri_data_service.foundation.parquet.paths import absence_marker_path
from agri_data_service.foundation.parquet.zoom import ZOOM_TIERS
from agri_data_service.ingest.usgs_nwis import USGS_PROPERTY_SOURCE
from agri_data_service.pipeline.constants import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.direct.water_gauges import (
    DirectWaterGaugesError,
    DirectWaterGaugesForwardAdapter,
)
from agri_data_service.pipeline.parquet.derivation import govern_day_absent
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from agri_data_service.warehouse.schemas.water_gauges import WATER_GAUGES_SCHEMA, WATER_GAUGES_STREAM
from tests.parquet.test_objectstore_writer import RecordingBackend

KIND = "observed"
DAY = date(2026, 9, 3)
FIRST_INSTANT = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)
SECOND_INSTANT = FIRST_INSTANT + timedelta(minutes=15)
#: The retired Postgres-reading gap-fill adapter, the only producer that ever governed this stream absent.
RETIRED_PRODUCER_RUN_ID = "pipeline/parquet/gap_fill.py:_fill_water_gauges:20260905T020000Z"


class _SessionDouble:
    async def rollback(self) -> None:
        return None


class _RefusesOneDelete(RecordingBackend):
    """A bucket that refuses exactly one named delete, then behaves -- the transient the retry exists for."""

    def __init__(self, refused_key: str) -> None:
        super().__init__()
        self.refuse_once: str | None = refused_key

    def delete(self, key: str) -> None:
        if key == self.refuse_once:
            self.refuse_once = None
            raise OSError(f"bucket refused to delete {key}")
        super().delete(key)


def _retired_producer_absence() -> GovernedAbsence:
    return GovernedAbsence(
        reason="the water-gauges export for this day produced zero rows from geo.features",
        upstream_response='{"producer": "gap_fill._fill_water_gauges", "rows": 0}',
        recorded_at=datetime(2026, 9, 5, 2, 0, tzinfo=UTC),
        run_id=RETIRED_PRODUCER_RUN_ID,
    )


def _govern_absent_at_every_tier(store: ObjectStore, absence: GovernedAbsence) -> None:
    """Seed the whole absence ladder the way the retired producer's finalizer did, coarse rungs first."""
    govern_day_absent(store, absence, layer=WATER_GAUGES_STREAM, kind=KIND, day=DAY)


def _row(
    *, site_number: str = "13206000", observed_at: datetime = FIRST_INSTANT, flow_cfs: float = 483.0
) -> dict[str, object]:
    return {
        "site_number": site_number,
        "observed_at": observed_at,
        "observed_day": DAY,
        "site_name": "BOISE RIVER AT GLENWOOD BRIDGE",
        "latitude": 43.66,
        "longitude": -116.28,
        "flow_cfs": flow_cfs,
        "percentile": 42.0,
        "condition": "normal",
        "trend": "steady",
        "source": USGS_PROPERTY_SOURCE,
        "geometry_linked": False,
        "data_available_at": None,
        "ingested_at": observed_at,
    }


def _table(rows: list[dict[str, object]]) -> pa.Table:
    return pa.Table.from_pylist(rows, schema=WATER_GAUGES_SCHEMA.arrow_schema)


class TestDirectWaterGaugesForwardAdapter:
    def test_a_missing_day_is_written_whole(self) -> None:
        store = ObjectStore(RecordingBackend())
        adapter = DirectWaterGaugesForwardAdapter(_table([_row()]))

        result = asyncio.run(adapter(_SessionDouble(), store, day=DAY, run_id="test"))

        assert result.row_count == 1
        assert store.partition_exists(WATER_GAUGES_STREAM, KIND, LANE_BASE_ZOOM_TIER, DAY) is True
        assert adapter.absence_overturned is None

    def test_a_second_fetch_the_same_day_merges_into_the_first(self) -> None:
        store = ObjectStore(RecordingBackend())
        asyncio.run(DirectWaterGaugesForwardAdapter(_table([_row()]))(_SessionDouble(), store, day=DAY, run_id="a"))
        second = DirectWaterGaugesForwardAdapter(_table([_row(observed_at=SECOND_INSTANT, flow_cfs=490.0)]))

        result = asyncio.run(second(_SessionDouble(), store, day=DAY, run_id="b"))

        assert result.row_count == 2
        assert store.read_partition(WATER_GAUGES_STREAM, KIND, LANE_BASE_ZOOM_TIER, DAY).num_rows == 2

    def test_a_replayed_incomplete_attempt_rewrites_the_checkpointed_merge_verbatim(self) -> None:
        """Crash recovery: the second attempt must republish what the first INTENDED, not re-merge the fetch."""
        store = ObjectStore(RecordingBackend())
        adapter = DirectWaterGaugesForwardAdapter(_table([_row(flow_cfs=400.0)]))
        asyncio.run(adapter(_SessionDouble(), store, day=DAY, run_id="a"))  # a part with no completion marker
        checkpointed = adapter.merge
        assert checkpointed is not None
        store.write_partition(
            _table([_row(site_number="13213000", flow_cfs=900.0)]),
            layer=WATER_GAUGES_STREAM,
            kind=KIND,
            zoom=LANE_BASE_ZOOM_TIER,
            day=DAY,
        )

        asyncio.run(adapter(_SessionDouble(), store, day=DAY, run_id="a"))

        published = store.read_partition(WATER_GAUGES_STREAM, KIND, LANE_BASE_ZOOM_TIER, DAY)
        assert published.to_pylist() == checkpointed.table.to_pylist()
        assert adapter.merge is checkpointed
        assert adapter.absence_overturned is None


class TestGovernedAbsenceReconciliation:
    """Rows disprove a marker; no rows leave it alone; a conflict and a corrupt marker stay an admin's call."""

    def test_an_absence_disproven_by_fetched_rows_is_retracted_at_every_tier_and_the_rows_written(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """INVERTED 2026-09-15: this setup used to raise `status=absent`; observed IV readings now win."""
        store = ObjectStore(RecordingBackend())
        original = _retired_producer_absence()
        _govern_absent_at_every_tier(store, original)
        adapter = DirectWaterGaugesForwardAdapter(_table([_row(), _row(site_number="13213000")]))

        result = asyncio.run(adapter(_SessionDouble(), store, day=DAY, run_id="water-gauges-nwis-forward-test"))

        assert result.row_count == 2
        assert result.absence_recorded is False
        assert store.partition_exists(WATER_GAUGES_STREAM, KIND, LANE_BASE_ZOOM_TIER, DAY) is True
        for tier in ZOOM_TIERS:
            assert store.absence_exists(WATER_GAUGES_STREAM, KIND, tier, DAY) is False, tier
        overturned = adapter.absence_overturned
        assert overturned is not None, "the forward reads this to put the retraction on the day's checkpoint"
        assert overturned.day == DAY
        assert overturned.tiers == ZOOM_TIERS
        assert overturned.incoming_rows == 2
        assert overturned.overturned_by_run_id == "water-gauges-nwis-forward-test"
        for marker in overturned.markers:
            assert marker.absence == original, f"z{marker.tier} provenance must survive the retraction verbatim"
        event = json.loads(capsys.readouterr().err.strip())
        assert event["event"] == "water_gauges_forward_absence_retracted"
        assert event["layer"] == WATER_GAUGES_STREAM
        assert event["run_id"] == "water-gauges-nwis-forward-test"
        assert event["overturned_by"] == "observed_rows"
        assert event["tiers"] == list(ZOOM_TIERS)
        assert {marker["run_id"] for marker in event["markers"]} == {RETIRED_PRODUCER_RUN_ID}
        assert event["markers"][-1] == {
            "tier": LANE_BASE_ZOOM_TIER,
            "reason": original.reason,
            "upstream_response": original.upstream_response,
            "recorded_at": "2026-09-05T02:00:00+00:00",
            "run_id": RETIRED_PRODUCER_RUN_ID,
        }

    def test_an_absence_with_no_fetched_rows_is_respected_and_no_marker_is_touched(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """DO NOT WEAKEN. A fetch that says nothing about a day may never manufacture data over its absence."""
        store = ObjectStore(RecordingBackend())
        _govern_absent_at_every_tier(store, _retired_producer_absence())
        adapter = DirectWaterGaugesForwardAdapter(_table([]))

        with pytest.raises(DirectWaterGaugesError, match="empty source day"):
            asyncio.run(adapter(_SessionDouble(), store, day=DAY, run_id="test"))

        for tier in ZOOM_TIERS:
            assert store.absence_exists(WATER_GAUGES_STREAM, KIND, tier, DAY) is True, tier
        assert store.partition_exists(WATER_GAUGES_STREAM, KIND, LANE_BASE_ZOOM_TIER, DAY) is False
        assert adapter.absence_overturned is None
        assert capsys.readouterr().err == ""

    def test_a_malformed_fetch_never_reaches_the_marker(self) -> None:
        """The merge's own validation runs BEFORE any retraction, so a bad fetch cannot cost a marker."""
        store = ObjectStore(RecordingBackend())
        _govern_absent_at_every_tier(store, _retired_producer_absence())
        wrong_day_row = _row()
        wrong_day_row["observed_day"] = DAY + timedelta(days=1)

        with pytest.raises(DirectWaterGaugesError, match="observed_day"):
            asyncio.run(
                DirectWaterGaugesForwardAdapter(_table([wrong_day_row]))(_SessionDouble(), store, day=DAY, run_id="t")
            )

        for tier in ZOOM_TIERS:
            assert store.absence_exists(WATER_GAUGES_STREAM, KIND, tier, DAY) is True, tier

    def test_a_day_holding_both_parts_and_a_marker_is_still_refused_for_an_admin(self) -> None:
        """`conflict` is two contradictory claims already published; a fetch may not pick between them."""
        backend = RecordingBackend()
        store = ObjectStore(backend)
        asyncio.run(DirectWaterGaugesForwardAdapter(_table([_row()]))(_SessionDouble(), store, day=DAY, run_id="a"))
        backend.put(
            store.key_for(absence_marker_path(WATER_GAUGES_STREAM, KIND, LANE_BASE_ZOOM_TIER, DAY)),
            _retired_producer_absence().to_json_bytes(),
            content_type="application/json",
        )
        adapter = DirectWaterGaugesForwardAdapter(_table([_row(observed_at=SECOND_INSTANT, flow_cfs=500.0)]))

        with pytest.raises(DirectWaterGaugesError, match="status=conflict"):
            asyncio.run(adapter(_SessionDouble(), store, day=DAY, run_id="b"))

        assert store.absence_exists(WATER_GAUGES_STREAM, KIND, LANE_BASE_ZOOM_TIER, DAY) is True
        published = store.read_partition(WATER_GAUGES_STREAM, KIND, LANE_BASE_ZOOM_TIER, DAY)
        assert published.to_pylist()[0]["flow_cfs"] == pytest.approx(483.0)
        assert adapter.absence_overturned is None

    def test_a_clear_refused_part_way_is_finished_by_the_retry_and_the_union_of_tiers_is_reported(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The bucket refuses ONE delete mid-ladder; the bounded retry re-reads the survivors and clears them."""
        original = _retired_producer_absence()
        store = ObjectStore(RecordingBackend())
        backend = _RefusesOneDelete(store.key_for(absence_marker_path(WATER_GAUGES_STREAM, KIND, 9, DAY)))
        store = ObjectStore(backend)
        _govern_absent_at_every_tier(store, original)
        adapter = DirectWaterGaugesForwardAdapter(_table([_row()]))

        with pytest.raises(OSError, match="refused to delete"):
            asyncio.run(adapter(_SessionDouble(), store, day=DAY, run_id="t"))

        assert [store.absence_exists(WATER_GAUGES_STREAM, KIND, tier, DAY) for tier in ZOOM_TIERS] == [
            False,
            False,
            True,
            True,
        ]
        assert store.partition_exists(WATER_GAUGES_STREAM, KIND, LANE_BASE_ZOOM_TIER, DAY) is False
        first = adapter.absence_overturned
        assert first is not None, "the removals before the refusal must be on record"
        assert first.tiers == (0, 5), "the removals before the refusal must be on record"

        result = asyncio.run(adapter(_SessionDouble(), store, day=DAY, run_id="t"))

        assert result.row_count == 1
        for tier in ZOOM_TIERS:
            assert store.absence_exists(WATER_GAUGES_STREAM, KIND, tier, DAY) is False, tier
        union = adapter.absence_overturned
        assert union is not None
        assert union.tiers == ZOOM_TIERS
        assert all(marker.absence == original for marker in union.markers)
        events = [json.loads(line) for line in capsys.readouterr().err.strip().splitlines()]
        assert [event["tiers"] for event in events] == [[0, 5], [0, 5, 9, 13]], (
            "each attempt announces the union so far; the last event per (run_id, day) is the truth"
        )

    def test_an_undecodable_marker_is_refused_rather_than_retracted_without_its_provenance(self) -> None:
        """A marker whose reasoning cannot be read cannot be carried forward, so it is not deleted blind."""
        backend = RecordingBackend()
        store = ObjectStore(backend)
        backend.put(
            store.key_for(absence_marker_path(WATER_GAUGES_STREAM, KIND, LANE_BASE_ZOOM_TIER, DAY)),
            b"not a governed absence",
            content_type="application/json",
        )
        adapter = DirectWaterGaugesForwardAdapter(_table([_row()]))

        with pytest.raises(DirectWaterGaugesError, match="cannot decode"):
            asyncio.run(adapter(_SessionDouble(), store, day=DAY, run_id="t"))

        assert store.absence_exists(WATER_GAUGES_STREAM, KIND, LANE_BASE_ZOOM_TIER, DAY) is True
        assert store.partition_exists(WATER_GAUGES_STREAM, KIND, LANE_BASE_ZOOM_TIER, DAY) is False
        assert adapter.absence_overturned is None

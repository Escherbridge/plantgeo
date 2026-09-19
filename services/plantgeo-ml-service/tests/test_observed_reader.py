"""The leakage guard: which windows the observed reader answers, and which it refuses by name."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime

import duckdb
import pyarrow as pa
import pytest

from plantgeo_ml_service.foundation.parquet_markers import GovernedAbsence, PartitionCompletion
from plantgeo_ml_service.pipeline.duckdb_session import DuckDbSession
from plantgeo_ml_service.pipeline.object_store import InMemoryObjectStoreBackend, ObjectStore
from plantgeo_ml_service.pipeline.observed_reader import MAX_WINDOW_DAYS, ObservedReader, ObservedReadRefusalError
from plantgeo_ml_service.warehouse.lanes import lane_contract
from plantgeo_ml_service.warehouse.streams import FIRE_DETECTIONS_SCHEMA

MOMENT = datetime(2026, 9, 10, 12, 0, 0, tzinfo=UTC)
AS_OF = date(2026, 9, 19)

# `fire-detections` publishes with a two-day lag, so a feature issued on the 19th reads through the
# 17th. Every window below is expressed against that one fact.
CEILING = lane_contract("fire-detections").settled_through(AS_OF)


def _reader() -> tuple[ObservedReader, ObjectStore]:
    store = ObjectStore(backend=InMemoryObjectStoreBackend())
    # A real connection that is never executed against: every case below refuses or answers empty
    # before a read is attempted, which is itself the thing being asserted.
    session = DuckDbSession(connection=duckdb.connect(), bucket_uri="s3://bucket")
    return ObservedReader(store=store, session=session), store


def _write_absence(store: ObjectStore, day: date) -> None:
    for zoom in (0, 5, 9, 13):
        store.write_absence_marker(
            GovernedAbsence(reason="source_empty", upstream_response="HTTP 200", recorded_at=MOMENT, run_id="test"),
            layer="fire-detections",
            kind="observed",
            zoom=zoom,
            day=day,
        )


def _rows(day: date) -> pa.Table:
    return pa.table(
        {
            "cell_longitude": [-120.0],
            "cell_latitude": [46.0],
            "observed_day": [day],
            "detection_count": [3],
            "frp_sum": [12.5],
            "frp_observation_count": [3],
            "high_confidence_detection_count": [1],
            "newest_observed_at": [MOMENT],
        },
        schema=FIRE_DETECTIONS_SCHEMA.arrow_schema,
    )


def test_a_day_the_producer_could_not_have_published_is_refused_not_skipped() -> None:
    """Silently narrowing the window turns a leakage guard into a surprise the caller never sees."""
    reader, _store = _reader()

    with pytest.raises(ObservedReadRefusalError) as refusal:
        reader.read_lane_window("fire-detections", 13, CEILING, AS_OF, as_of=AS_OF)

    assert refusal.value.reason == "beyond_publication_lag"
    assert refusal.value.days == (AS_OF,)


def test_the_last_settled_day_is_readable() -> None:
    reader, store = _reader()
    _write_absence(store, CEILING)

    frame = reader.read_lane_window("fire-detections", 13, CEILING, CEILING, as_of=AS_OF)

    assert frame.height == 0
    assert frame.columns == list(FIRE_DETECTIONS_SCHEMA.column_names)


def test_a_window_below_the_lanes_history_floor_is_refused() -> None:
    """The window must stay inside the day budget too, or `window_too_wide` fires first."""
    reader, _store = _reader()
    floor = lane_contract("fire-detections").history_floor

    with pytest.raises(ObservedReadRefusalError) as refusal:
        reader.read_lane_window(
            "fire-detections", 13, date(floor.year - 1, 1, 1), date(floor.year - 1, 1, 2), as_of=AS_OF
        )

    assert refusal.value.reason == "below_history_floor"


def test_a_backwards_window_is_refused() -> None:
    reader, _store = _reader()

    with pytest.raises(ObservedReadRefusalError) as refusal:
        reader.read_lane_window("fire-detections", 13, CEILING, CEILING.replace(day=CEILING.day - 1), as_of=AS_OF)

    assert refusal.value.reason == "window_inverted"


def test_a_window_past_the_read_budget_is_refused() -> None:
    reader, _store = _reader()
    first = date(2001, 1, 1)
    last = date(first.year + (MAX_WINDOW_DAYS // 365) + 2, 1, 1)

    with pytest.raises(ObservedReadRefusalError) as refusal:
        reader.read_lane_window("fire-detections", 13, first, last, as_of=AS_OF)

    assert refusal.value.reason == "window_too_wide"


def test_a_day_that_is_neither_a_partition_nor_a_governed_absence_is_refused() -> None:
    """A missing day is not an empty day: answering the window without it would understate coverage."""
    reader, store = _reader()
    _write_absence(store, CEILING)

    with pytest.raises(ObservedReadRefusalError) as refusal:
        reader.read_lane_window("fire-detections", 13, CEILING.replace(day=CEILING.day - 1), CEILING, as_of=AS_OF)

    assert refusal.value.reason == "day_not_governed"
    assert refusal.value.days == (CEILING.replace(day=CEILING.day - 1),)


def test_a_day_with_parts_but_no_completion_marker_is_refused_as_ungoverned() -> None:
    """A run killed part way leaves a prefix of the parts; half a release is not a day."""
    reader, store = _reader()
    store.write_partition(_rows(CEILING), layer="fire-detections", kind="observed", zoom=13, day=CEILING)

    with pytest.raises(ObservedReadRefusalError) as refusal:
        reader.read_lane_window("fire-detections", 13, CEILING, CEILING, as_of=AS_OF)

    assert refusal.value.reason == "day_not_governed"


def test_a_lane_this_service_has_no_contract_for_is_refused_before_any_listing() -> None:
    reader, _store = _reader()

    with pytest.raises(Exception, match="has no contract in this service"):
        reader.read_lane_window("soil-survey", 13, CEILING, CEILING, as_of=AS_OF)


def test_a_completed_day_hands_exactly_its_own_part_keys_to_the_read() -> None:
    """The bucket read is stubbed; what is asserted is WHICH keys a governed window resolves to."""
    store = ObjectStore(backend=InMemoryObjectStoreBackend())
    recording = _RecordingConnection(statements=[], result=_rows(CEILING))
    reader = ObservedReader(
        store=store,
        session=DuckDbSession(connection=recording, bucket_uri="s3://bucket"),  # type: ignore[arg-type]
    )
    part = store.write_partition(_rows(CEILING), layer="fire-detections", kind="observed", zoom=13, day=CEILING)
    store.write_completion_marker(
        PartitionCompletion(part_count=1, row_count=1, completed_at=MOMENT, run_id="test"),
        layer="fire-detections",
        kind="observed",
        zoom=13,
        day=CEILING,
    )
    # A coarse rung's object of the same day must not join the base rung's read.
    store.write_partition(_rows(CEILING), layer="fire-detections", kind="observed", zoom=9, day=CEILING)

    frame = reader.read_lane_window("fire-detections", 13, CEILING, CEILING, as_of=AS_OF)

    _statement, parameters = recording.statements[0]
    assert parameters == [[f"s3://bucket/{part.relative_path}"]]
    assert frame.height == 1


@dataclass(slots=True)
class _RecordingConnection:
    """A connection that records what it was asked to read, standing in for the bucket."""

    statements: list[tuple[str, object]]
    result: pa.Table

    def execute(self, statement: str, parameters: object = None) -> _RecordingConnection:
        """Record one execution and return self, the shape DuckDB's own API returns."""
        self.statements.append((statement, parameters))
        return self

    def arrow(self) -> pa.Table:
        """Return the primed table, standing in for the rows the bucket would have answered."""
        return self.result

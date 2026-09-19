"""The forecast lane's publication plumbing: the ladder, the bootstrap documents, the ceiling.

Also holds the harness the other three forecast-lane test modules import: an object store that
mirrors every write to a temporary directory, so a real `ObservedReader` can read partitions back
through DuckDB without a network.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Final

import duckdb
import polars as pl
import pyarrow as pa
import pytest

from plantgeo_ml_service.foundation.canonical import canonical_json, sha256_digest
from plantgeo_ml_service.foundation.lattice import TIER_RESOLUTION_DEGREES
from plantgeo_ml_service.foundation.parquet_paths import (
    ZOOM_TIERS,
    availability_bootstrap_marker_key,
    availability_lane_root,
    completion_marker_path,
    partition_path,
)
from plantgeo_ml_service.pipeline.availability_publisher import InMemoryPointerStore, reset_conditional_put_support
from plantgeo_ml_service.pipeline.duckdb_session import DuckDbSession
from plantgeo_ml_service.pipeline.forecast_lane_bootstrap import (
    BOOTSTRAP_MARKER_SCHEMA_VERSION,
    FORECAST_TIER_DERIVATIONS,
    UNFORECAST_DAY_REASON,
    ColumnAggregation,
    ForecastLaneError,
    GridAggregation,
    RowSelect,
    bootstrap_forecast_lane,
    bootstrap_marker_payload,
    derive_coarse_rung,
    expected_forecast_days,
    floor_to_tier,
    forecast_issue_frontier,
    forecast_source_ceiling,
    read_bootstrap_receipt,
    terminal_rows_for_day,
    write_forecast_day,
    write_run_receipt,
)
from plantgeo_ml_service.pipeline.object_store import (
    InMemoryObjectStoreBackend,
    ListedObject,
    ObjectStore,
)
from plantgeo_ml_service.pipeline.observed_reader import ObservedReader
from plantgeo_ml_service.warehouse.availability import AvailabilityRow, EvidenceReceipt
from plantgeo_ml_service.warehouse.lanes import settled_through
from plantgeo_ml_service.warehouse.streams import FIRE_RISK_STREAM, SIGNAL_STREAM, stream_schema

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

MOMENT: Final = datetime(2026, 9, 19, 12, 0, 0, tzinfo=UTC)
ISSUED_ON: Final = date(2026, 9, 19)
SIGNAL_CEILING: Final = settled_through(SIGNAL_STREAM, ISSUED_ON)


@dataclass(slots=True)
class MirroredObjectStoreBackend:
    """An in-memory bucket that also lands every object on disk, so DuckDB can read it back."""

    root: Path
    inner: InMemoryObjectStoreBackend = field(default_factory=InMemoryObjectStoreBackend)

    def put(self, key: str, payload: bytes, *, content_type: str) -> None:
        """Store the bytes and mirror them to the temporary directory."""
        self.inner.put(key, payload, content_type=content_type)
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)

    def get(self, key: str) -> bytes | None:
        """Return one object's bytes, or `None` when the key is absent."""
        return self.inner.get(key)

    def head(self, key: str) -> int | None:
        """Return one object's byte count, or `None` when the key is absent."""
        return self.inner.head(key)

    def delete(self, key: str) -> None:
        """Remove one object from both the dict and the mirror."""
        self.inner.delete(key)
        path = self.root / key
        if path.exists():
            path.unlink()

    def list_objects(self, prefix: str, *, max_keys: int = 500_000) -> Iterator[ListedObject]:
        """Walk one prefix in key order."""
        return self.inner.list_objects(prefix, max_keys=max_keys)


@dataclass(frozen=True, slots=True)
class ForecastHarness:
    """A store, a pointer store and a reader wired to one temporary bucket root."""

    store: ObjectStore
    pointers: InMemoryPointerStore
    reader: ObservedReader

    @property
    def objects(self) -> dict[str, bytes]:
        """Return every object the bucket currently holds, by key."""
        backend = self.store.backend
        assert isinstance(backend, MirroredObjectStoreBackend)
        return backend.inner.objects


def build_harness(root: Path) -> ForecastHarness:
    """Build a bucket rooted at `root`, with a DuckDB session pointed at the same directory."""
    reset_conditional_put_support()
    store = ObjectStore(backend=MirroredObjectStoreBackend(root=root))
    session = DuckDbSession(connection=duckdb.connect(), bucket_uri=root.as_posix())
    return ForecastHarness(
        store=store,
        pointers=InMemoryPointerStore(),
        reader=ObservedReader(store=store, session=session),
    )


def signal_forecast_frame(*, day: date, cells: tuple[tuple[str, float, float], ...]) -> pl.DataFrame:
    """Build one day of signal forecast rows in the pinned contract, for the ladder tests."""
    schema = stream_schema(SIGNAL_STREAM, "forecast")
    rows = [
        {
            "support_key": "surface",
            "signal_name": "air_temperature_mean",
            "normalized_unit": "C",
            "cell_id": cell_id,
            "observed_day": day,
            "normalized_value": 12.5 + index,
            "observation_count": 0,
            "newest_observed_at": MOMENT,
            "coverage_fraction": None,
            "allowed_client_exposure": True,
            "cell_longitude": longitude,
            "cell_latitude": latitude,
            "forecast_run_id": "a" * 64,
            "random_seed": 7,
            "ensemble_size": 20,
            "horizon_days": 1,
            "issued_on": SIGNAL_CEILING,
            "quantile": 0.5,
        }
        for index, (cell_id, longitude, latitude) in enumerate(cells)
    ]
    return pl.from_arrow(pa.Table.from_pylist(rows, schema=schema.arrow_schema))


def test_the_source_ceiling_comes_from_the_provider_frontier_not_today() -> None:
    """A ceiling read off the wall clock moves on its own and makes a lane look fresh for free."""
    ceiling = forecast_source_ceiling(SIGNAL_STREAM, issued_on=ISSUED_ON, horizon_days=30)

    assert ceiling == SIGNAL_CEILING + timedelta(days=30)
    assert SIGNAL_CEILING < ISSUED_ON
    with pytest.raises(ForecastLaneError, match="at least one day"):
        forecast_source_ceiling(SIGNAL_STREAM, issued_on=ISSUED_ON, horizon_days=0)


def test_a_lattice_edge_bins_by_rounding_never_by_raw_division() -> None:
    """Polars division is frame-length dependent, so the binning is integer micro-degrees."""
    edge = pl.DataFrame({"latitude": [46.0]})
    bulk = pl.DataFrame({"latitude": [46.0, 46.1, 46.2, 46.3, 46.4, 46.5]})

    one_row = edge.select(floor_to_tier(pl.col("latitude"), zoom=5, axis="latitude")).item()
    many_rows = bulk.select(floor_to_tier(pl.col("latitude"), zoom=5, axis="latitude")).to_series().to_list()[0]

    assert one_row == pytest.approx(46.0)
    assert many_rows == pytest.approx(46.0)
    assert TIER_RESOLUTION_DEGREES[5] == pytest.approx(0.2)


def test_every_rung_of_the_ladder_is_written_with_its_own_completion_marker(tmp_path: Path) -> None:
    harness = build_harness(tmp_path)
    day = SIGNAL_CEILING
    frame = signal_forecast_frame(day=day, cells=(("cell-a", -120.01, 46.01), ("cell-b", -120.02, 46.02)))

    written = write_forecast_day(harness.store, frame, layer=SIGNAL_STREAM, day=day, run_id="run", completed_at=MOMENT)

    for zoom in ZOOM_TIERS:
        assert partition_path(SIGNAL_STREAM, "forecast", zoom, day) in harness.objects
        assert completion_marker_path(SIGNAL_STREAM, "forecast", zoom, day) in harness.objects
        assert written.row_counts_by_rung[zoom] > 0
    # Two base cells 0.01 apart merge into one z5 cell, and the coarse rungs null the cell id.
    merged_base_cell_count = 2
    assert written.row_counts_by_rung[ZOOM_TIERS[-1]] == merged_base_cell_count
    assert written.row_counts_by_rung[5] == 1


def test_a_coarse_rung_nulls_the_identifier_no_merged_row_can_honestly_name(tmp_path: Path) -> None:
    build_harness(tmp_path)
    frame = signal_forecast_frame(day=SIGNAL_CEILING, cells=(("cell-a", -120.01, 46.01), ("cell-b", -120.02, 46.02)))

    coarse = derive_coarse_rung(frame, layer=SIGNAL_STREAM, zoom=5)

    assert coarse.height == 1
    assert coarse.get_column("cell_id").to_list() == [None]
    assert coarse.get_column("normalized_value").item() == pytest.approx(13.0)
    assert coarse.columns == list(stream_schema(SIGNAL_STREAM, "forecast").column_names)


def test_a_quantile_coarse_row_is_the_mean_of_its_cells_never_the_worst_of_them() -> None:
    """The merge rule is a property of the FIELD: a p50 of two cells is their mean, not their max.

    Fire-risk's coarse rung is a `RowSelect` because a probability surface has no honest mean; the
    signal lane's quantiles are a `ColumnAggregation` mean, per (quantile, horizon, run). Both
    strategies live in one vocabulary so a lane declares which one it means.
    """
    derivation = FORECAST_TIER_DERIVATIONS[SIGNAL_STREAM]
    values = (10.0, 16.0)
    frame = signal_forecast_frame(
        day=SIGNAL_CEILING,
        cells=(("cell-a", -120.01, 46.01), ("cell-b", -120.02, 46.02)),
    ).with_columns(pl.Series("normalized_value", list(values), dtype=pl.Float64))

    coarse = derive_coarse_rung(frame, layer=SIGNAL_STREAM, zoom=5)

    assert derivation.row_select is None
    assert ColumnAggregation("normalized_value", "mean") in derivation.aggregations
    assert coarse.height == 1
    assert coarse.get_column("normalized_value").item() == pytest.approx(sum(values) / len(values))
    assert coarse.get_column("normalized_value").item() != pytest.approx(max(values))


def test_the_fire_risk_lane_declares_a_row_select_over_the_shared_vocabulary() -> None:
    """M7 + the coordinator decision: worst-cell select is DECLARED, not a per-lane special case."""
    derivation = FORECAST_TIER_DERIVATIONS[FIRE_RISK_STREAM]

    assert derivation.aggregations == ()
    assert derivation.row_select == RowSelect(
        order_by=("risk_score", "probability", "refused_reason"),
        descending=(True, True, False),
    )
    with pytest.raises(ForecastLaneError, match="EITHER per-column merges or one row select"):
        GridAggregation(
            longitude_column="cell_longitude",
            latitude_column="cell_latitude",
            key_columns=("observed_day",),
        )


def test_an_empty_day_returns_a_governed_absence_rather_than_an_empty_partition(tmp_path: Path) -> None:
    """M2: a hole in a horizon is indexed with a reason; raising would have failed the whole run."""
    harness = build_harness(tmp_path)
    schema = stream_schema(SIGNAL_STREAM, "forecast")
    empty = pl.from_arrow(schema.arrow_schema.empty_table())

    written = write_forecast_day(harness.store, empty, layer=SIGNAL_STREAM, day=SIGNAL_CEILING, run_id="run")

    assert written.is_absent
    assert written.absence_reason == UNFORECAST_DAY_REASON
    assert written.parts_by_rung == {}
    assert partition_path(SIGNAL_STREAM, "forecast", ZOOM_TIERS[-1], SIGNAL_CEILING) not in harness.objects

    rows = terminal_rows_for_day(
        written,
        layer=SIGNAL_STREAM,
        source_receipt=_source_receipt(harness),
        source_ceiling=forecast_source_ceiling(SIGNAL_STREAM, issued_on=ISSUED_ON, horizon_days=30),
        published_at=MOMENT,
    )

    assert tuple(row.rung for row in rows) == ZOOM_TIERS
    assert {row.terminal_state for row in rows} == {"governed_absence"}
    assert {row.absence_reason for row in rows} == {UNFORECAST_DAY_REASON}
    assert all(row.row_count == 0 and not row.data_receipts for row in rows)


def test_the_day_ladder_runs_from_the_issue_frontier_to_the_ceiling() -> None:
    """Every day of the horizon owes a terminal row, not only the days the run happened to fill."""
    frontier = forecast_issue_frontier(SIGNAL_STREAM, issued_on=ISSUED_ON)
    ceiling = forecast_source_ceiling(SIGNAL_STREAM, issued_on=ISSUED_ON, horizon_days=30)

    days = expected_forecast_days(issued_on=frontier, source_ceiling=ceiling)

    horizon_day_count = 30
    assert frontier == SIGNAL_CEILING
    assert len(days) == horizon_day_count
    assert days[0] == frontier + timedelta(days=1)
    assert days[-1] == ceiling
    with pytest.raises(ForecastLaneError, match="precedes the issue frontier"):
        expected_forecast_days(issued_on=frontier, source_ceiling=frontier - timedelta(days=1))


def test_the_bootstrap_marker_is_byte_identical_to_the_siblings_document(tmp_path: Path) -> None:
    """`parquet_ops/availability_coverage.py` re-serializes this marker and refuses any difference."""
    build_harness(tmp_path)
    lane_root = availability_lane_root(SIGNAL_STREAM, "forecast")
    receipt = EvidenceReceipt(key=f"{lane_root}/availability/bootstrap/receipt={'b' * 64}.json", sha256="b" * 64)

    payload = bootstrap_marker_payload(lane_root, receipt)

    assert (
        payload
        == (
            '{"bootstrap_receipt_key":"layer=signal/kind=forecast/availability/bootstrap/receipt='
            f"{'b' * 64}"
            '.json","bootstrap_receipt_sha256":"'
            f"{'b' * 64}"
            '","lane_root":"layer=signal/kind=forecast","schema_version":"availability-bootstrap-marker-v1"}'
        ).encode()
    )
    assert json.loads(payload)["schema_version"] == BOOTSTRAP_MARKER_SCHEMA_VERSION


def test_the_bootstrap_receipt_and_marker_replay_to_the_same_bytes(tmp_path: Path) -> None:
    harness = build_harness(tmp_path)
    rows = _terminal_rows(harness)

    first = bootstrap_forecast_lane(
        harness.store,
        rows,
        layer=SIGNAL_STREAM,
        source_receipt=_source_receipt(harness),
        source_ceiling=forecast_source_ceiling(SIGNAL_STREAM, issued_on=ISSUED_ON, horizon_days=30),
        created_at=MOMENT,
    )
    second = bootstrap_forecast_lane(
        harness.store,
        rows,
        layer=SIGNAL_STREAM,
        source_receipt=_source_receipt(harness),
        source_ceiling=forecast_source_ceiling(SIGNAL_STREAM, issued_on=ISSUED_ON, horizon_days=30),
        created_at=MOMENT,
    )

    assert first == second
    assert read_bootstrap_receipt(harness.store, layer=SIGNAL_STREAM) == first
    marker_key = availability_bootstrap_marker_key(availability_lane_root(SIGNAL_STREAM, "forecast"))
    assert harness.objects[marker_key] == bootstrap_marker_payload(
        availability_lane_root(SIGNAL_STREAM, "forecast"), first
    )


def test_a_lane_that_was_never_bootstrapped_answers_none_without_a_listing(tmp_path: Path) -> None:
    harness = build_harness(tmp_path)

    assert read_bootstrap_receipt(harness.store, layer=SIGNAL_STREAM) is None


def test_the_run_receipt_is_content_addressed_and_adopts_its_own_replay(tmp_path: Path) -> None:
    harness = build_harness(tmp_path)
    payload = {"forecast_run_id": "c" * 64, "lane": SIGNAL_STREAM}

    first = write_run_receipt(harness.store, payload, layer=SIGNAL_STREAM, forecast_run_id="c" * 64)
    second = write_run_receipt(harness.store, payload, layer=SIGNAL_STREAM, forecast_run_id="c" * 64)

    assert first == second
    assert first.sha256 == sha256_digest(canonical_json(payload))
    assert harness.objects[first.key] == canonical_json(payload).encode("utf-8")


def _source_receipt(harness: ForecastHarness) -> EvidenceReceipt:
    """Write and return the run receipt the bootstrap binds its verified inventory root to."""
    return write_run_receipt(
        harness.store,
        {"forecast_run_id": "d" * 64, "lane": SIGNAL_STREAM},
        layer=SIGNAL_STREAM,
        forecast_run_id="d" * 64,
    )


def _terminal_rows(harness: ForecastHarness) -> tuple[AvailabilityRow, ...]:
    """Write one day at every rung and project it into the terminal rows a generation is built from."""
    frame = signal_forecast_frame(day=SIGNAL_CEILING, cells=(("cell-a", -120.01, 46.01),))
    written = write_forecast_day(
        harness.store,
        frame,
        layer=SIGNAL_STREAM,
        day=SIGNAL_CEILING,
        run_id="run",
        completed_at=MOMENT,
    )
    return terminal_rows_for_day(
        written,
        layer=SIGNAL_STREAM,
        source_receipt=_source_receipt(harness),
        source_ceiling=forecast_source_ceiling(SIGNAL_STREAM, issued_on=ISSUED_ON, horizon_days=30),
        published_at=MOMENT,
    )

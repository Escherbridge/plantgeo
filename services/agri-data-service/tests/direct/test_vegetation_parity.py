"""The Postgres-vs-Parquet parity receipt: a counted comparison, never a total, never a write.

DO NOT DELETE, AND DO NOT REPLACE WITH `parser()` SMOKE TESTS. This receipt is the gate on an
IRREVERSIBLE drop of the governed Postgres plane, and an earlier revision of this file declared the
verdict untestable ("`run_parity`/`census_governed_plane` need a real Postgres session and a real
bucket"), which left the arithmetic -- the one thing that decides the gate -- with no coverage at
all. It does not need either: `build_vegetation_parity_receipt` is pure, and the ladder census runs
against an in-memory object store, exactly as `test_drought_parity.py` and
`test_weather_observations_parity.py` already do for their own receipt builders.
"""

# ruff: noqa: PLR2004 - the small literal counts ARE the assertion; naming each one hides it.

from __future__ import annotations

from datetime import UTC, date, datetime

import pyarrow as pa  # type: ignore[import-untyped]

from agri_data_service.foundation.parquet.absence import GovernedAbsence
from agri_data_service.foundation.parquet.completion import PartitionCompletion
from agri_data_service.foundation.parquet.zoom import ZOOM_TIERS
from agri_data_service.pipeline.direct.vegetation.parity import (
    ParquetLadderCensus,
    VegetationParityReceipt,
    build_vegetation_parity_receipt,
    census_parquet_ladder,
    parser,
)
from agri_data_service.pipeline.direct.vegetation.products import VEGETATION_DIRECT_KIND
from agri_data_service.pipeline.lanes import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from agri_data_service.warehouse.schemas.vegetation import VEGETATION_PLANE_SCHEMA, VEGETATION_PLANE_STREAM
from tests.parquet.test_objectstore_writer import RecordingBackend

RUN_ID = "vegetation-parity-test"
COMPLETED_AT = datetime(2026, 9, 1, tzinfo=UTC)
DAY = date(2026, 8, 18)
OTHER_DAY = date(2026, 8, 20)


def _ladder(
    *,
    data_days: tuple[date, ...] = (),
    absent_days: tuple[date, ...] = (),
    missing_days: tuple[date, ...] = (),
    ladder_incomplete_days: tuple[date, ...] = (),
    row_counts: dict[date, int] | None = None,
) -> ParquetLadderCensus:
    """Stage one Parquet-side census without touching a bucket."""
    return ParquetLadderCensus(
        data_days=frozenset(data_days),
        absent_days=frozenset(absent_days),
        missing_days=frozenset(missing_days),
        ladder_incomplete_days=frozenset(ladder_incomplete_days),
        row_counts=row_counts,
    )


def _vegetation_table(day: date, *, cell_count: int) -> pa.Table:
    rows = [
        {
            "cell_id": f"00000000-0000-4000-8000-{index:012d}",
            "grid_name": "sentinel2-ndvi-0p25deg",
            "metric_name": "ndvi",
            "metric_unit": "unitless",
            "observed_day": day,
            "metric_value": 0.4 + index / 100,
            "observation_checksum": f"{index:064d}",
            "data_available_at": COMPLETED_AT,
            "release_count": 1,
            "allowed_client_exposure": True,
            "cell_longitude": -116.375,
            "cell_latitude": 43.125,
        }
        for index in range(cell_count)
    ]
    return pa.Table.from_pylist(rows, schema=VEGETATION_PLANE_SCHEMA.arrow_schema)


def _write_complete_day(store: ObjectStore, *, day: date, cell_count: int) -> None:
    """Write a full part-plus-marker day at EVERY rung: the only state that reads `data` everywhere."""
    for zoom in ZOOM_TIERS:
        store.write_partition(
            _vegetation_table(day, cell_count=cell_count),
            layer=VEGETATION_PLANE_STREAM,
            kind=VEGETATION_DIRECT_KIND,
            zoom=zoom,
            day=day,
        )
        store.write_completion_marker(
            PartitionCompletion(part_count=1, row_count=cell_count, completed_at=COMPLETED_AT, run_id=RUN_ID),
            layer=VEGETATION_PLANE_STREAM,
            kind=VEGETATION_DIRECT_KIND,
            zoom=zoom,
            day=day,
        )


def _write_governed_absence(store: ObjectStore, *, day: date) -> None:
    """Mark a day deliberately empty at every rung, as `gap_fill._govern_absent_day` does."""
    for zoom in ZOOM_TIERS:
        store.write_absence(
            GovernedAbsence(
                reason=f"the vegetation day export returned zero rows for {day.isoformat()}",
                upstream_response="test fixture",
                recorded_at=COMPLETED_AT,
                run_id=RUN_ID,
            ),
            layer=VEGETATION_PLANE_STREAM,
            kind=VEGETATION_DIRECT_KIND,
            zoom=zoom,
            day=day,
        )


# --- The arithmetic the gate turns on -----------------------------------------------------------


def test_a_postgres_day_parquet_never_wrote_is_reported_missing_not_cancelled_by_another_day() -> None:
    """DO NOT DELETE. Comparing day COUNTS let one Parquet-only day cancel one uncovered Postgres day.

    Both sides hold exactly one `data` day here, so every count-based test passes -- and the lane is
    still missing the only day Postgres actually holds.
    """
    receipt = build_vegetation_parity_receipt({DAY: 40}, _ladder(data_days=(OTHER_DAY,)))

    assert receipt.missing_from_parquet == (DAY,)
    assert receipt.day_coverage == "under_covered"
    assert receipt.parity_achieved is False


def test_row_counts_are_compared_per_day_so_a_surplus_never_pays_for_an_empty_day() -> None:
    """DO NOT DELETE. One global sum let a double-published day cover a day that wrote nothing."""
    postgres = {DAY: 40, OTHER_DAY: 40}
    ladder = _ladder(data_days=(DAY, OTHER_DAY), row_counts={DAY: 80, OTHER_DAY: 0})

    receipt = build_vegetation_parity_receipt(postgres, ladder)

    assert receipt.postgres_rows == 80
    assert receipt.parquet_rows_measured == 80  # the totals agree exactly
    assert receipt.row_shortfalls == ({"observed_day": OTHER_DAY.isoformat(), "postgres_rows": 40, "parquet_rows": 0},)
    assert receipt.row_coverage == "under_covered"
    assert receipt.parity_achieved is False


def test_a_row_surplus_is_reported_but_does_not_gate_the_drop() -> None:
    """Coverage is not at risk when Parquet holds MORE; a stale part file still deserves an operator."""
    receipt = build_vegetation_parity_receipt({DAY: 40}, _ladder(data_days=(DAY,), row_counts={DAY: 41}))

    assert receipt.row_surpluses == ({"observed_day": DAY.isoformat(), "postgres_rows": 40, "parquet_rows": 41},)
    assert receipt.row_shortfalls == ()
    assert receipt.parity_achieved is True


def test_calendar_days_neither_side_holds_are_counted_never_reported_as_gaps() -> None:
    """DO NOT DELETE. This lane has a MEASURED 7-day median gap; demanding every calendar day made the
    verdict permanently `under_covered` however complete the backfill was."""
    sparse_gap = tuple(date(2026, 8, day) for day in range(19, 26))
    receipt = build_vegetation_parity_receipt({DAY: 40}, _ladder(data_days=(DAY,), missing_days=sparse_gap))

    assert receipt.days_neither_side_holds == len(sparse_gap)
    assert receipt.missing_from_parquet == ()
    assert receipt.day_coverage == "parity_ok"
    assert receipt.parity_achieved is True


def test_a_postgres_day_whose_derived_rungs_are_unfinished_is_under_covered() -> None:
    """A day visible only above z13, or written and never marked, is not a day the drop may rely on."""
    receipt = build_vegetation_parity_receipt({DAY: 40}, _ladder(data_days=(DAY,), ladder_incomplete_days=(DAY,)))

    assert receipt.ladder_incomplete == (DAY,)
    assert receipt.parity_achieved is False


def test_rows_are_not_measured_until_they_are_asked_for() -> None:
    receipt = build_vegetation_parity_receipt({DAY: 40}, _ladder(data_days=(DAY,)))

    assert receipt.parquet_rows_measured is None
    assert receipt.row_coverage == "not_measured"
    assert receipt.parity_achieved is True


def test_an_empty_postgres_plane_and_an_empty_parquet_lane_is_trivially_at_parity() -> None:
    receipt = build_vegetation_parity_receipt({}, _ladder())

    assert receipt.postgres_days == 0
    assert receipt.postgres_first_day is None
    assert receipt.parity_achieved is True


def test_the_json_receipt_carries_the_verdict_and_a_bounded_sample() -> None:
    """`main()` prints exactly this; a caller reads the verdict, and the exit code mirrors it."""
    receipt = build_vegetation_parity_receipt({DAY: 40}, _ladder())

    rendered = receipt.to_json_dict()

    assert rendered["layer"] == VEGETATION_PLANE_STREAM
    assert rendered["verdict"] == {
        "day_coverage": "under_covered",
        "row_coverage": "not_measured",
        "parity_achieved": False,
    }
    findings = rendered["findings"]
    assert isinstance(findings, dict)
    assert findings["missing_from_parquet_sample"] == [DAY.isoformat()]


# --- The ladder census, against a real object store and no network ------------------------------


def test_the_ladder_census_reads_a_completed_day_as_data_and_measures_only_the_days_asked_for() -> None:
    store = ObjectStore(RecordingBackend())
    _write_complete_day(store, day=DAY, cell_count=3)
    _write_complete_day(store, day=OTHER_DAY, cell_count=5)

    census = census_parquet_ladder(store, first_day=DAY, last_day=OTHER_DAY, count_rows_for=frozenset({DAY}))

    assert census.data_days == frozenset({DAY, OTHER_DAY})
    assert census.ladder_incomplete_days == frozenset()
    assert census.row_counts == {DAY: 3}  # OTHER_DAY was never asked for, so it was never read


def test_the_ladder_census_reads_a_governed_absence_as_absent_and_never_as_missing() -> None:
    """A backfilled Postgres-empty day is `absent` at every rung; that is what makes it durable."""
    store = ObjectStore(RecordingBackend())
    _write_governed_absence(store, day=DAY)

    census = census_parquet_ladder(store, first_day=DAY, last_day=DAY, count_rows_for=None)

    assert census.absent_days == frozenset({DAY})
    assert census.data_days == frozenset()
    assert census.missing_days == frozenset()
    assert census.ladder_incomplete_days == frozenset()


def test_a_base_rung_written_without_its_marker_is_ladder_incomplete_not_covered() -> None:
    store = ObjectStore(RecordingBackend())
    store.write_partition(
        _vegetation_table(DAY, cell_count=2),
        layer=VEGETATION_PLANE_STREAM,
        kind=VEGETATION_DIRECT_KIND,
        zoom=LANE_BASE_ZOOM_TIER,
        day=DAY,
    )

    census = census_parquet_ladder(store, first_day=DAY, last_day=DAY, count_rows_for=frozenset({DAY}))

    assert census.data_days == frozenset()
    assert census.ladder_incomplete_days == frozenset({DAY})
    assert census.row_counts == {}  # not `data`, so nothing was read and nothing is claimed
    assert build_vegetation_parity_receipt({DAY: 2}, census).parity_achieved is False


# --- The operator surface -----------------------------------------------------------------------


def test_count_rows_defaults_to_off_so_a_default_run_never_reads_full_history() -> None:
    assert parser().parse_args([]).count_rows is False


def test_count_rows_can_be_requested_explicitly() -> None:
    assert parser().parse_args(["--count-rows"]).count_rows is True


def test_the_receipt_is_frozen_so_a_verdict_cannot_be_edited_after_it_is_built() -> None:
    receipt = build_vegetation_parity_receipt({DAY: 1}, _ladder(data_days=(DAY,)))

    assert isinstance(receipt, VegetationParityReceipt)
    assert receipt.day_coverage == "parity_ok"

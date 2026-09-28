"""The full-ladder census: one status per stream-day across all four rungs, listed by month or by year."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import TYPE_CHECKING

import pytest

from agri_data_service.foundation.parquet.paths import (
    absence_marker_path,
    completion_marker_path,
    partition_path,
)
from agri_data_service.pipeline.runner.census import (
    FULL_LADDER_TIERS,
    CensusConflictError,
    fold_ladder,
    read_census,
)

if TYPE_CHECKING:
    from agri_data_service.foundation.parquet.paths import PartitionDayStatus, PartitionKind
    from agri_data_service.foundation.parquet.zoom import ZoomTier

STREAM = "fixture-grid-value"
DAY = date(2026, 3, 10)
BASE, *COARSE = FULL_LADDER_TIERS


@pytest.mark.parametrize(
    ("base", "coarse", "folded"),
    [
        ("data", "data", "data"),
        ("data", "missing", "incomplete"),
        ("incomplete", "data", "incomplete"),
        ("missing", "missing", "missing"),
        ("absent", "absent", "absent"),
        ("absent", "missing", "absent"),
    ],
)
def test_a_day_is_data_only_when_every_rung_is(
    base: PartitionDayStatus, coarse: PartitionDayStatus, folded: str
) -> None:
    """A published base under an unfinished ladder is owed work, never a finished day."""
    rungs = {BASE: base, **dict.fromkeys(COARSE, coarse)}

    assert fold_ladder(DAY, rungs, stream=STREAM) == folded


@pytest.mark.parametrize(
    "rungs",
    [
        {BASE: "conflict", **dict.fromkeys(COARSE, "data")},
        {BASE: "absent", **dict.fromkeys(COARSE, "data")},
    ],
    ids=["data-and-absence-on-one-rung", "coarse-rows-over-an-absent-base"],
)
def test_the_two_shapes_no_writer_may_leave_stop_the_turn(rungs: dict[ZoomTier, PartitionDayStatus]) -> None:
    with pytest.raises(CensusConflictError):
        fold_ladder(DAY, rungs, stream=STREAM)


@dataclass
class _ListingBucket:
    """The bucket's listing edge: real key shapes, and every (tier, year, month) scope it was asked for."""

    keys: list[str] = field(default_factory=list)
    scopes: list[tuple[int, int, int | None]] = field(default_factory=list)

    def publish(self, day: date, tiers: tuple[ZoomTier, ...] = FULL_LADDER_TIERS) -> None:
        for tier in tiers:
            self.keys += [
                partition_path(STREAM, "observed", tier, day),
                completion_marker_path(STREAM, "observed", tier, day),
            ]

    def govern_absent(self, day: date) -> None:
        self.keys += [absence_marker_path(STREAM, "observed", tier, day) for tier in FULL_LADDER_TIERS]

    def list_partition_keys(
        self, layer: str, kind: PartitionKind, zoom: ZoomTier, *, year: int | None = None, month: int | None = None
    ) -> tuple[str, ...]:
        del layer, kind
        self.scopes.append((zoom, year or 0, month))
        return tuple(self.keys)  # the census itself ignores another tier's keys


def test_a_forward_window_is_listed_by_month_and_folds_every_rung() -> None:
    bucket = _ListingBucket()
    bucket.publish(DAY)
    bucket.publish(DAY + timedelta(days=1), tiers=(BASE,))
    bucket.govern_absent(DAY + timedelta(days=2))

    census = read_census(bucket, [STREAM], DAY, DAY + timedelta(days=3))

    assert [census.status(STREAM, DAY + timedelta(days=offset)) for offset in range(4)] == [
        "data",
        "incomplete",
        "absent",
        "missing",
    ]
    assert census.base_data_days() == frozenset({DAY, DAY + timedelta(days=1)})
    assert {month for _, _, month in bucket.scopes} == {DAY.month}


def test_a_gap_fill_window_is_listed_by_year_rather_than_month_by_month() -> None:
    """Forty years of history are forty listings per rung, not four hundred and eighty."""
    bucket = _ListingBucket()
    bucket.publish(DAY)
    first = date(2020, 1, 1)

    census = read_census(bucket, [STREAM], first, DAY)

    assert census.status(STREAM, DAY) == "data"
    assert census.status(STREAM, first) == "missing"
    assert {(year, month) for _, year, month in bucket.scopes} == {(year, None) for year in range(2020, 2027)}

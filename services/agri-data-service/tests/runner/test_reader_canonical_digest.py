"""The PRODUCTION digest seam G6 compare rides: `ObjectStoreLaneReader.canonical_digest`.

`tests/runner/fakes.py::MemoryLaneStore.canonical_digest` drops columns without conforming, so the
compare flow tests alone prove the fake. This file pins the real reader (review 2026-10-25 finding 2):
conform (select + cast + grain sort) happens BEFORE the drop, over a real registered stream (`drought`).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

import pyarrow as pa
import pytest

from agri_data_service.pipeline.runner.exits import TurnConfigurationError
from agri_data_service.pipeline.runner.reader import ObjectStoreLaneReader

if TYPE_CHECKING:
    from collections.abc import Sequence

STREAM = "drought"
#: canonical_digest never touches the store or receipts, so the reader can hold inert stand-ins.
READER = ObjectStoreLaneReader(store=cast("Any", object()), receipts=cast("Any", object()))


def _table(*, order: Sequence[int], ingested_minute: int, first_category: int = 2) -> pa.Table:
    """Two drought rows, emitted in `order`, stamped at `ingested_minute`; grain sort must normalize order."""
    rows = [
        ("direct:area-a", first_category, b"\x01"),
        ("direct:area-b", 3, b"\x02"),
    ]
    picked = [rows[i] for i in order]
    return pa.table(
        {
            "area_id": pa.array([r[0] for r in picked], pa.string()),
            "valid_date": pa.array([datetime(2026, 10, 20, tzinfo=UTC).date()] * 2, pa.date32()),
            "dm_category": pa.array([r[1] for r in picked], pa.int32()),
            "source_url": pa.array(["https://example.invalid/usdm"] * 2, pa.string()),
            "ingested_at": pa.array([datetime(2026, 10, 21, 4, ingested_minute, tzinfo=UTC)] * 2),
            "geom": pa.array([r[2] for r in picked], pa.binary()),
        }
    )


def test_excluding_hides_only_the_capture_instant_and_survives_row_order() -> None:
    """Same content, different ingested_at AND different row order -> equal once excluded, differ otherwise."""
    built = _table(order=(1, 0), ingested_minute=30)
    published = _table(order=(0, 1), ingested_minute=5)

    with_exclusion = (
        READER.canonical_digest(STREAM, built, excluding=("ingested_at",)),
        READER.canonical_digest(STREAM, published, excluding=("ingested_at",)),
    )
    without_exclusion = (
        READER.canonical_digest(STREAM, built),
        READER.canonical_digest(STREAM, published),
    )

    assert with_exclusion[0] == with_exclusion[1]
    assert without_exclusion[0] != without_exclusion[1]


def test_a_content_change_still_differs_under_the_exclusion() -> None:
    built = _table(order=(0, 1), ingested_minute=5, first_category=4)
    published = _table(order=(0, 1), ingested_minute=5)

    assert READER.canonical_digest(STREAM, built, excluding=("ingested_at",)) != READER.canonical_digest(
        STREAM, published, excluding=("ingested_at",)
    )


def test_an_excluded_column_absent_from_the_schema_is_a_named_refusal() -> None:
    """A loader that skipped `stream_contract` must still fail loudly here, naming stream and column."""
    table = _table(order=(0, 1), ingested_minute=5)

    with pytest.raises(TurnConfigurationError, match=r"'drought'.*not_a_column"):
        READER.canonical_digest(STREAM, table, excluding=("not_a_column",))

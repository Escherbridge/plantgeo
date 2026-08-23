"""The soil-survey release exporter: streaming, grain conformance, part-spilling, refusal paths.

The governed SQL itself is exercised against a real database elsewhere; these tests pin the
behaviour that is pure Python -- that mupolygonkeys are read in bounded batches (this lane's
rows each carry a polygon, so an unbounded key list is the same size risk signal.py's cell
batching exists to bound, even though the lookup itself is indexed), that the release day is
bound into every row rather than derived from source data, that a release larger than one
part's row budget spills across `part-N` files, and that an empty input or a batch matching
nothing currently published is refused rather than silently reporting success with nothing
written.

THE STREAMING TESTS ARE THE POINT OF THIS FILE NOW. The export used to read a whole release into
one table before writing any of it, behind a 200,000-key ceiling that production passed -- so the
lane raised every tick and never wrote an object. What replaced it can only be trusted if the
parts genuinely land AS the key walk advances, so that is asserted directly (against the backend's
own object count at the moment each batch is pulled) rather than inferred from the result.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

import pytest

from agri_data_service.foundation.parquet.paths import partition_path
from agri_data_service.pipeline.lanes import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.lanes import soil_survey as soil_survey_lane
from agri_data_service.pipeline.lanes.soil_survey import (
    POLYGON_KEY_BATCH_SIZE,
    SoilSurveyExportError,
    export_soil_survey_release,
    read_soil_survey_release,
)
from agri_data_service.pipeline.parquet.objectstore import EmptyPartitionError, ObjectStore
from agri_data_service.warehouse.schemas.soil_survey import SOIL_SURVEY_SCHEMA, SOIL_SURVEY_STREAM
from tests.parquet.test_objectstore_writer import RecordingBackend

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Sequence

AUGUST_EIGHTH = date(2026, 8, 8)


def soil_survey_row(mupolygonkey: str, release_day: date) -> dict[str, object]:
    """One exported grain row, shaped exactly as the SQL's column list returns it."""
    return {
        "natural_key": f"usda-sda:{mupolygonkey}",
        "mupolygonkey": mupolygonkey,
        "mukey": "123456",
        "map_unit_name": "Test loam",
        "soil_series": "Test series",
        "drainage_class": "well_drained",
        "hydric_rating": False,
        "land_capability_class": "3e",
        "survey_area_symbol": "ID001",
        "survey_area_vintage": datetime(2025, 8, 26, tzinfo=UTC),
        "geometry_id": "11111111-1111-1111-1111-111111111111",
        "last_confirmed_at": datetime(2026, 8, 8, tzinfo=UTC),
        "release_day": release_day,
        "geometry_wkb": b"\x01\x03\x00\x00\x00",
        "producer": "usda-sda",
    }


class _Result:
    def __init__(self, rows: Sequence[dict[str, object]]) -> None:
        self._rows = rows

    def mappings(self) -> Sequence[dict[str, object]]:
        return self._rows


class RecordingSession:
    """Captures each statement's bound natural-key batch and answers with rows for known keys.

    `known_mupolygonkeys=None` means "everything asked for is currently published" -- one row
    per key in the batch. A concrete tuple restricts answers to that set, so a batch that asks
    for delineations the source cannot currently serve can be exercised too.
    """

    def __init__(self, *, known_mupolygonkeys: tuple[str, ...] | None = None) -> None:
        self.batches: list[list[str]] = []
        self._known = known_mupolygonkeys

    async def execute(self, _statement: Any, params: dict[str, Any]) -> _Result:
        batch = list(params["natural_keys"])
        self.batches.append(batch)
        release_day = params["release_day"]
        requested = [key.removeprefix("usda-sda:") for key in batch]
        served = requested if self._known is None else [key for key in requested if key in self._known]
        return _Result([soil_survey_row(key, release_day) for key in served])


async def key_batches(
    mupolygonkeys: Sequence[str],
    *,
    size: int = POLYGON_KEY_BATCH_SIZE,
    on_pull: Callable[[], None] | None = None,
) -> AsyncIterator[tuple[str, ...]]:
    """Stand in for the registry's keyset walk: hand the export one bounded key page at a time.

    `on_pull` fires immediately BEFORE each page is yielded, which is what lets a test observe the
    store as the walk advances rather than only after it has finished.
    """
    for start in range(0, len(mupolygonkeys), size):
        if on_pull is not None:
            on_pull()
        yield tuple(mupolygonkeys[start : start + size])


@pytest.mark.asyncio
async def test_mupolygonkeys_are_read_in_bounded_batches_not_one_array() -> None:
    """This lane's rows each carry a polygon; an unbounded key list is the size risk
    signal.py's cell batching exists to bound, even though this lookup is indexed."""
    session = RecordingSession()
    remainder = 7
    mupolygonkeys = [f"poly-{i}" for i in range(POLYGON_KEY_BATCH_SIZE * 2 + remainder)]

    await read_soil_survey_release(
        session,  # type: ignore[arg-type]
        mupolygonkeys=mupolygonkeys,
        release_day=AUGUST_EIGHTH,
    )

    assert [len(batch) for batch in session.batches] == [
        POLYGON_KEY_BATCH_SIZE,
        POLYGON_KEY_BATCH_SIZE,
        remainder,
    ]
    served_keys = [key.removeprefix("usda-sda:") for batch in session.batches for key in batch]
    assert served_keys == mupolygonkeys


@pytest.mark.asyncio
async def test_release_day_is_bound_into_every_row_not_derived_from_source_data() -> None:
    """A delineation carries no per-row export day of its own (docs/lanes/soil-survey.md
    sections 2, 7): SSURGO issues no periodic re-observation to slice a day out of."""
    session = RecordingSession()
    mupolygonkeys = ["poly-1", "poly-2"]

    table = await read_soil_survey_release(
        session,  # type: ignore[arg-type]
        mupolygonkeys=mupolygonkeys,
        release_day=AUGUST_EIGHTH,
    )

    assert table.column("release_day").to_pylist() == [AUGUST_EIGHTH, AUGUST_EIGHTH]


@pytest.mark.asyncio
async def test_the_read_conforms_to_the_registered_schema() -> None:
    session = RecordingSession()
    mupolygonkeys = ["poly-1", "poly-2", "poly-3"]

    table = await read_soil_survey_release(
        session,  # type: ignore[arg-type]
        mupolygonkeys=mupolygonkeys,
        release_day=AUGUST_EIGHTH,
    )

    expected_rows = 3
    assert table.schema.equals(SOIL_SURVEY_SCHEMA.arrow_schema)
    assert table.num_rows == expected_rows


@pytest.mark.asyncio
async def test_the_export_lands_at_the_observed_partition_sorted_to_the_grain() -> None:
    session = RecordingSession()
    backend = RecordingBackend()
    store = ObjectStore(backend)
    mupolygonkeys = ["poly-3", "poly-1", "poly-2"]

    receipts = await export_soil_survey_release(
        session,  # type: ignore[arg-type]
        store,
        day=AUGUST_EIGHTH,
        mupolygonkey_batches=key_batches(mupolygonkeys),
    )

    expected_rows = 3
    assert len(receipts) == 1
    receipt = receipts[0]
    assert receipt.key == partition_path(SOIL_SURVEY_STREAM, "observed", LANE_BASE_ZOOM_TIER, AUGUST_EIGHTH, 0)
    assert receipt.kind == "observed"
    assert receipt.zoom == LANE_BASE_ZOOM_TIER
    assert receipt.row_count == expected_rows


@pytest.mark.asyncio
async def test_a_release_larger_than_one_part_spills_across_part_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SSURGO map-unit polygons are heavy; a release must be able to spread across bounded
    part files rather than trust row count alone (geo.drought_areas is 995 rows and 500 MB)."""
    monkeypatch.setattr(soil_survey_lane, "ROWS_PER_PART", 2)
    mupolygonkeys = [f"poly-{i}" for i in range(5)]
    session = RecordingSession()
    backend = RecordingBackend()
    store = ObjectStore(backend)

    receipts = await export_soil_survey_release(
        session,  # type: ignore[arg-type]
        store,
        day=AUGUST_EIGHTH,
        mupolygonkey_batches=key_batches(mupolygonkeys),
    )

    assert [receipt.row_count for receipt in receipts] == [2, 2, 1]
    assert [receipt.key for receipt in receipts] == [
        partition_path(SOIL_SURVEY_STREAM, "observed", LANE_BASE_ZOOM_TIER, AUGUST_EIGHTH, part_index)
        for part_index in range(3)
    ]
    # Every part still reads as one present day; gap detection lists the directory, never a file.
    assert store.list_partition_keys(SOIL_SURVEY_STREAM, "observed", LANE_BASE_ZOOM_TIER) == tuple(
        receipt.relative_path for receipt in receipts
    )


@pytest.mark.asyncio
async def test_parts_land_as_the_key_walk_advances_rather_than_after_it_finishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The release is never held whole: this is the defect that kept the lane from ever writing.

    Production passed the old 200,000-key ceiling, so a read-everything export raised on every
    tick. Asserting the RESULT would pass just as well for an export that buffered all 1,507,623
    delineations and wrote at the end, so what is pinned here is the store's own object count at
    the moment each key page is pulled.
    """
    monkeypatch.setattr(soil_survey_lane, "ROWS_PER_PART", 2)
    session = RecordingSession()
    backend = RecordingBackend()
    store = ObjectStore(backend)
    objects_when_pulled: list[int] = []

    receipts = await export_soil_survey_release(
        session,  # type: ignore[arg-type]
        store,
        day=AUGUST_EIGHTH,
        mupolygonkey_batches=key_batches(
            [f"poly-{index}" for index in range(6)],
            size=2,
            on_pull=lambda: objects_when_pulled.append(len(backend.objects)),
        ),
    )

    # One page of two keys fills exactly one part, so page N is pulled with N-1 parts already up.
    assert objects_when_pulled == [0, 1, 2]
    assert [receipt.row_count for receipt in receipts] == [2, 2, 2]


@pytest.mark.asyncio
async def test_the_receipts_count_every_part_uploaded_so_the_prune_cannot_delete_a_live_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`gap_fill._prune_surplus` deletes `part-<n>` for n at or above the reported count.

    A streamed export that under-reported would hand the store a licence to delete objects it had
    just written, which is why the receipt tuple has to be the whole upload rather than a sample.
    """
    monkeypatch.setattr(soil_survey_lane, "ROWS_PER_PART", 2)
    session = RecordingSession()
    backend = RecordingBackend()
    store = ObjectStore(backend)

    receipts = await export_soil_survey_release(
        session,  # type: ignore[arg-type]
        store,
        day=AUGUST_EIGHTH,
        mupolygonkey_batches=key_batches([f"poly-{index}" for index in range(7)], size=3),
    )

    assert len(receipts) == len(backend.objects)
    assert [receipt.relative_path for receipt in receipts] == sorted(backend.objects)
    surplus = store.prune_surplus_parts(
        SOIL_SURVEY_STREAM, "observed", LANE_BASE_ZOOM_TIER, AUGUST_EIGHTH, written_part_count=len(receipts)
    )
    assert surplus.removed == ()
    assert len(backend.objects) == len(receipts)


@pytest.mark.asyncio
async def test_streamed_parts_carry_the_key_order_across_files_not_only_inside_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Key pages arrive in `mupolygonkey` order, which IS the grain, so the parts tile that order.

    The read-everything shape could only sort within an arbitrary slice of an unsorted table; the
    streamed one makes part N's keys all precede part N+1's, which is what a range-pruning reader
    needs from the min/max statistics Parquet writes per part.
    """
    monkeypatch.setattr(soil_survey_lane, "ROWS_PER_PART", 2)
    session = RecordingSession()
    store = ObjectStore(RecordingBackend())
    ordered = [f"poly-{index}" for index in range(6)]

    receipts = await export_soil_survey_release(
        session,  # type: ignore[arg-type]
        store,
        day=AUGUST_EIGHTH,
        mupolygonkey_batches=key_batches(ordered, size=2),
    )

    served = [key.removeprefix("usda-sda:") for batch in session.batches for key in batch]
    assert served == ordered, "the export must consume the key walk in the order it is handed"
    assert [receipt.relative_path for receipt in receipts] == [
        partition_path(SOIL_SURVEY_STREAM, "observed", LANE_BASE_ZOOM_TIER, AUGUST_EIGHTH, part_index)
        for part_index in range(3)
    ], "part indices are assigned in walk order, so the files tile the key range in sequence"


@pytest.mark.asyncio
async def test_an_empty_mupolygonkey_list_is_refused_rather_than_querying_nothing() -> None:
    session = RecordingSession()

    with pytest.raises(SoilSurveyExportError, match="at least one mupolygonkey"):
        await read_soil_survey_release(
            session,  # type: ignore[arg-type]
            mupolygonkeys=[],
            release_day=AUGUST_EIGHTH,
        )

    assert session.batches == []


@pytest.mark.asyncio
async def test_a_batch_matching_nothing_published_is_refused_rather_than_silently_writing_nothing() -> None:
    """A source with nothing currently published for the requested keys must fail loudly, not
    report success with zero receipts -- the same contract watersheds.py's exporter uses."""
    session = RecordingSession(known_mupolygonkeys=())
    backend = RecordingBackend()
    store = ObjectStore(backend)

    with pytest.raises(EmptyPartitionError):
        await export_soil_survey_release(
            session,  # type: ignore[arg-type]
            store,
            day=AUGUST_EIGHTH,
            mupolygonkey_batches=key_batches(["poly-1"]),
        )

    assert backend.objects == {}

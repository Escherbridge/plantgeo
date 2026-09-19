"""Per-day-partition content-SHA scoped promotion: idempotent, resumable, and evaluation-safe.

Exercises `execution/vegetation_partition_promotion.py` against an in-memory `ObjectStore` backend
(the same idiom `tests/parquet/test_objectstore_writer.py::RecordingBackend` uses) and a stub
register call, so no real Postgres session or Parquet partition is required to prove the checksum
scoping decision (owner 2026-09-18) is correct.
"""

# ruff: noqa: PLR2004 - the literals here are fixture call counts, and naming each one hides the assertion.

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, cast

import pytest

from agri_data_service.execution.vegetation_ndvi_plane import (
    GovernedPlane,
    RegistrationSummary,
    ReleaseMaterialisation,
    SelectionMaterialisation,
)
from agri_data_service.execution.vegetation_partition_promotion import (
    AvailabilityIndexDays,
    AvailabilityPartitionConflictError,
    EmptyDayPartitionError,
    EvaluationArtifactNotPromotableError,
    IndexedDay,
    VegetationDayPartitionKey,
    VegetationPromotionReceipt,
    day_partition_content_sha256,
    default_promotion_days,
    exit_code_for,
    load_promotion_receipt,
    promote_vegetation_day_partition,
    run_vegetation_promotion,
    save_promotion_receipt,
)
from agri_data_service.pipeline.constants import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.parquet.objectstore import (
    ConcurrentPrunePartitionError,
    ListedObject,
    ObjectStore,
    PartitionNotWrittenError,
)
from agri_data_service.warehouse.schemas.vegetation import VEGETATION_PLANE_STREAM

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.ext.asyncio import AsyncSession

DAY = date(2026, 9, 10)


class InMemoryBackend:
    """The `ObjectStoreBackend` shape, kept in memory so no network or credentials are required."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put(self, key: str, payload: bytes, *, content_type: str) -> None:
        del content_type
        self.objects[key] = payload

    def get(self, key: str) -> bytes | None:
        return self.objects.get(key)

    def delete(self, key: str) -> None:
        self.objects.pop(key, None)

    def list_objects(self, prefix: str) -> Iterator[ListedObject]:
        for key in sorted(self.objects):
            if key.startswith(prefix):
                yield ListedObject(key=key, last_modified=None)

    def size_of(self, key: str) -> int | None:
        payload = self.objects.get(key)
        return None if payload is None else len(payload)


class RefusingBackend:
    """An `ObjectStoreBackend` that fails any access, so a test can prove no object was touched."""

    def _refuse(self, key: str) -> None:
        raise AssertionError(f"the object store was read for {key!r}, which this turn must never do")

    def put(self, key: str, payload: bytes, *, content_type: str) -> None:
        del payload, content_type
        self._refuse(key)

    def get(self, key: str) -> bytes | None:
        self._refuse(key)
        return None

    def delete(self, key: str) -> None:
        self._refuse(key)

    def list_objects(self, prefix: str) -> Iterator[ListedObject]:
        self._refuse(prefix)
        return iter(())

    def size_of(self, key: str) -> int | None:
        self._refuse(key)
        return None


@pytest.fixture
def store() -> ObjectStore:
    return ObjectStore(backend=InMemoryBackend())


def _registration_summary() -> RegistrationSummary:
    plane = GovernedPlane(
        data_source_id=uuid.uuid4(),
        source_release_id=uuid.uuid4(),
        release_set_id=uuid.uuid4(),
        release_manifest_checksum="a" * 64,
        payload_checksum="b" * 64,
        corpus_cell_count=1,
        corpus_cell_day_count=1,
        corpus_row_count=1,
        first_observed_day=DAY,
        last_observed_day=DAY,
    )
    materialisation = ReleaseMaterialisation(
        observation_count=1, series_count=1, first_observed_day=DAY, last_observed_day=DAY
    )
    selection = SelectionMaterialisation(observation_count=1, series_count=1)
    return RegistrationSummary(
        plane=plane,
        requested_cell_count=1,
        spatial_cell_count=1,
        series_count=1,
        observation_count=1,
        materialisation=materialisation,
        selection=selection,
    )


class RecordingRegister:
    """A stub `register_governed_forward_plane` that counts calls without touching Postgres."""

    def __init__(self) -> None:
        self.calls: list[tuple[date, tuple[tuple[str, date], ...]]] = []

    async def __call__(
        self, session: object, *, cutoff_day: date, cell_days: tuple[tuple[str, date], ...]
    ) -> RegistrationSummary:
        del session
        self.calls.append((cutoff_day, cell_days))
        return _registration_summary()


def test_day_partition_content_sha256_is_order_independent() -> None:
    forward = day_partition_content_sha256([("cell-b", 0.4), ("cell-a", 0.2)])
    reversed_order = day_partition_content_sha256([("cell-a", 0.2), ("cell-b", 0.4)])
    assert forward == reversed_order


def test_day_partition_content_sha256_changes_with_a_value() -> None:
    original = day_partition_content_sha256([("cell-a", 0.2)])
    changed = day_partition_content_sha256([("cell-a", 0.3)])
    assert original != changed


def test_day_partition_content_sha256_rejects_duplicate_cell_keys() -> None:
    with pytest.raises(ValueError, match="duplicated"):
        day_partition_content_sha256([("cell-a", 0.2), ("cell-a", 0.3)])


async def test_evaluation_only_partition_is_never_promoted() -> None:
    register = RecordingRegister()
    with pytest.raises(EvaluationArtifactNotPromotableError):
        await promote_vegetation_day_partition(
            None,
            day=DAY,
            kind="evaluation",
            cell_values=[("cell-a", 0.2)],
            previous_receipt=None,
            register=register,
        )
    assert register.calls == []


async def test_first_promotion_calls_register_and_records_a_receipt() -> None:
    register = RecordingRegister()
    outcome = await promote_vegetation_day_partition(
        None,
        day=DAY,
        kind="observed",
        cell_values=[("cell-a", 0.2), ("cell-b", 0.4)],
        previous_receipt=None,
        now=datetime(2026, 9, 18, tzinfo=UTC),
        register=register,
    )
    assert outcome.status == "promoted"
    assert len(register.calls) == 1
    promoted_day, cell_days = register.calls[0]
    assert promoted_day == DAY
    assert set(cell_days) == {("cell-a", DAY), ("cell-b", DAY)}
    assert outcome.receipt.content_sha256 == outcome.content_sha256


async def test_unchanged_partition_re_run_is_a_no_op() -> None:
    register = RecordingRegister()
    cell_values = [("cell-a", 0.2), ("cell-b", 0.4)]
    first = await promote_vegetation_day_partition(
        None, day=DAY, kind="observed", cell_values=cell_values, previous_receipt=None, register=register
    )
    assert first.status == "promoted"
    assert len(register.calls) == 1

    second = await promote_vegetation_day_partition(
        None,
        day=DAY,
        kind="observed",
        cell_values=list(cell_values),  # same content, re-read in a different order downstream
        previous_receipt=first.receipt,
        register=register,
    )
    assert second.status == "unchanged"
    assert len(register.calls) == 1, "an unchanged partition must never call the register verb again"
    assert second.receipt is first.receipt


async def test_changed_partition_re_promotes_only_itself() -> None:
    register = RecordingRegister()
    first = await promote_vegetation_day_partition(
        None, day=DAY, kind="observed", cell_values=[("cell-a", 0.2)], previous_receipt=None, register=register
    )
    assert first.status == "promoted"

    second = await promote_vegetation_day_partition(
        None,
        day=DAY,
        kind="observed",
        cell_values=[("cell-a", 0.35)],  # the partition's content actually changed
        previous_receipt=first.receipt,
        register=register,
    )
    assert second.status == "promoted"
    assert len(register.calls) == 2
    assert second.content_sha256 != first.content_sha256
    # Re-promotion is still scoped to this one day's cells, never widened to a corpus-wide call.
    assert register.calls[1] == (DAY, (("cell-a", DAY),))


def test_promotion_receipt_round_trips_through_the_object_store(store: ObjectStore) -> None:
    partition = VegetationDayPartitionKey(day=DAY, kind="observed")
    receipt = VegetationPromotionReceipt(
        partition=partition,
        content_sha256="c" * 64,
        promoted_at=datetime(2026, 9, 18, 12, 0, tzinfo=UTC),
        release_set_id=str(uuid.uuid4()),
        source_release_id=str(uuid.uuid4()),
    )
    assert load_promotion_receipt(store, day=DAY) is None

    written_path = save_promotion_receipt(store, receipt)
    assert "year=2026" in written_path
    assert "month=09" in written_path
    assert "day=10" in written_path

    loaded = load_promotion_receipt(store, day=DAY)
    assert loaded is not None
    assert loaded.content_sha256 == receipt.content_sha256
    assert loaded.release_set_id == receipt.release_set_id
    assert loaded.source_release_id == receipt.source_release_id


def test_evaluation_kind_receipt_cannot_be_constructed() -> None:
    with pytest.raises(EvaluationArtifactNotPromotableError):
        VegetationDayPartitionKey(day=DAY, kind="evaluation")


#: An index that has published nothing yet: every day it is asked about is `not_yet_indexed`.
EMPTY_AVAILABILITY_INDEX = AvailabilityIndexDays(verdicts={})


async def test_an_indexed_governed_absence_is_reported_with_the_index_reason() -> None:
    """The index, not a failed object read, produces the absence -- and carries its OWN reason (B2)."""
    session = cast("AsyncSession", object())  # never touched: the absent path reaches no register call
    availability = AvailabilityIndexDays(
        verdicts={DAY: IndexedDay(state="governed_absence", absence_reason="upstream_scene_not_published")}
    )
    refusing_store = ObjectStore(backend=RefusingBackend())

    report = await run_vegetation_promotion(session, refusing_store, days=[DAY], availability=availability)

    assert report["absent_days"] == [DAY.isoformat()]
    (entry,) = cast("list[dict[str, object]]", report["days"])
    assert entry["status"] == "absent"
    assert entry["reason"] == "upstream_scene_not_published"
    assert entry["layer"] == "vegetation"
    # `RefusingBackend` proves the claim: an indexed absence is answered from the index alone.


async def test_a_day_the_index_still_calls_published_after_a_re_read_is_a_conflict(store: ObjectStore) -> None:
    """Index says published, no part file, and the re-read agrees: corruption, raised (B2, W5 S4)."""
    session = cast("AsyncSession", object())
    availability = AvailabilityIndexDays(verdicts={DAY: IndexedDay(state="published")})
    re_reads = 0

    def winning_generation() -> AvailabilityIndexDays:
        nonlocal re_reads
        re_reads += 1
        return availability

    with pytest.raises(AvailabilityPartitionConflictError) as raised:
        await run_vegetation_promotion(
            session, store, days=[DAY], availability=availability, refresh_availability=winning_generation
        )

    assert DAY.isoformat() in str(raised.value)
    assert raised.value.layer == VEGETATION_PLANE_STREAM
    assert re_reads == 1, "the pointer is re-read exactly once, and only on the conflict path"


async def test_a_prune_inside_the_turn_window_is_reclassified_not_paged(store: ObjectStore) -> None:
    """The TOCTOU window, closed: the day the winning generation states is the day that is reported.

    A retention pass removing a day BETWEEN the turn's availability snapshot and the object open
    used to raise `AvailabilityPartitionConflictError` -- an operator page, for a benign race the
    mid-read path already names `ConcurrentPrunePartitionError` (STYLE-REVIEW-W5 S4).
    """
    session = cast("AsyncSession", object())
    snapshot = AvailabilityIndexDays(verdicts={DAY: IndexedDay(state="published")})
    after_prune = AvailabilityIndexDays(
        verdicts={DAY: IndexedDay(state="governed_absence", absence_reason="pruned_by_retention")}
    )

    report = await run_vegetation_promotion(
        session, store, days=[DAY], availability=snapshot, refresh_availability=lambda: after_prune
    )

    assert report["absent_days"] == [DAY.isoformat()]
    (entry,) = cast("list[dict[str, object]]", report["days"])
    assert entry["status"] == "absent"
    assert entry["reason"] == "pruned_by_retention"
    assert entry["reclassified"] == "availability_index_advanced_during_turn"


#: Two fabricated generations, so a conflict message can be checked for naming BOTH of them.
SNAPSHOT_GENERATION = "a" * 64
WINNING_GENERATION = "b" * 64
AVAILABILITY_POINTER_KEY = "parquet/layer=vegetation/kind=observed/availability/_LATEST.json"


async def test_a_winning_generation_that_lost_the_row_is_a_conflict_not_a_benign_reclassification(
    store: ObjectStore,
) -> None:
    """STYLE-REVIEW-W6 S2: only a governed ABSENCE reclassifies; a vanished row is index corruption.

    The snapshot stated `published`, the store holds no part file, and the winning generation has no
    row for the day AT ALL. That is not a retention pass recording a reason -- it is the index
    losing a row it had, one half of exactly what this error exists for. Reclassifying it made the
    day `not_yet_indexed`, which `_promotion_report` turns into `waiting_for_writer` and
    `exit_code_for` returns 0 for: an availability regression exiting silently green.
    """
    session = cast("AsyncSession", object())
    snapshot = AvailabilityIndexDays(
        verdicts={DAY: IndexedDay(state="published")},
        generation_sha256=SNAPSHOT_GENERATION,
        pointer_key=AVAILABILITY_POINTER_KEY,
    )
    after_regression = AvailabilityIndexDays(
        verdicts={},
        generation_sha256=WINNING_GENERATION,
        pointer_key=AVAILABILITY_POINTER_KEY,
    )

    with pytest.raises(AvailabilityPartitionConflictError) as raised:
        await run_vegetation_promotion(
            session, store, days=[DAY], availability=snapshot, refresh_availability=lambda: after_regression
        )

    assert raised.value.fresh_state == "not_yet_indexed"
    assert raised.value.snapshot_generation == SNAPSHOT_GENERATION
    assert raised.value.winning_generation == WINNING_GENERATION
    assert raised.value.pointer_key == AVAILABILITY_POINTER_KEY


async def test_the_conflict_message_names_both_generations_and_the_pointer_key(store: ObjectStore) -> None:
    """STYLE-REVIEW-W6 S3 / W5 S4's second half: two generations are in play, so the page names both.

    `layer-lanes.md` §4 -- "Failures name the day, the lane, and the source response". After the
    re-read the operator is reconciling between two generations and cannot tell a stale snapshot
    from a real divergence unless the message says which two.
    """
    session = cast("AsyncSession", object())
    snapshot = AvailabilityIndexDays(
        verdicts={DAY: IndexedDay(state="published")},
        generation_sha256=SNAPSHOT_GENERATION,
        pointer_key=AVAILABILITY_POINTER_KEY,
    )
    still_published = AvailabilityIndexDays(
        verdicts={DAY: IndexedDay(state="published")},
        generation_sha256=WINNING_GENERATION,
        pointer_key=AVAILABILITY_POINTER_KEY,
    )

    with pytest.raises(AvailabilityPartitionConflictError) as raised:
        await run_vegetation_promotion(
            session, store, days=[DAY], availability=snapshot, refresh_availability=lambda: still_published
        )

    message = str(raised.value)
    assert SNAPSHOT_GENERATION in message
    assert WINNING_GENERATION in message
    assert AVAILABILITY_POINTER_KEY in message
    assert "published" in message


async def test_a_day_the_index_has_no_row_for_is_skipped_as_not_yet_indexed(store: ObjectStore) -> None:
    """Nobody has looked yet: not an absence, not a failure, and no object read (B2)."""
    session = cast("AsyncSession", object())

    report = await run_vegetation_promotion(session, store, days=[DAY], availability=EMPTY_AVAILABILITY_INDEX)

    assert report["absent_days"] == []
    assert report["not_yet_indexed_days"] == [DAY.isoformat()]
    (entry,) = cast("list[dict[str, object]]", report["days"])
    assert entry["status"] == "not_yet_indexed"


async def test_a_turn_whose_every_day_is_absent_does_not_complete(store: ObjectStore) -> None:
    """The vacuous-success guard: no promotion, no completion, a named reason and a non-zero exit (B1)."""
    session = cast("AsyncSession", object())
    other_day = date(2026, 9, 11)
    availability = AvailabilityIndexDays(
        verdicts={
            DAY: IndexedDay(state="governed_absence", absence_reason="upstream_scene_not_published"),
            other_day: IndexedDay(state="governed_absence", absence_reason="upstream_scene_not_published"),
        }
    )

    report = await run_vegetation_promotion(session, store, days=[DAY, other_day], availability=availability)

    assert report["status"] == "no_days_promoted"
    assert report["reason"] == "all_days_absent"
    assert exit_code_for(report) == 1


async def test_a_turn_whose_every_day_is_unindexed_waits_for_the_writer(store: ObjectStore) -> None:
    """The steady state of the intended configuration is not a failure (W5 S3).

    The lane is disabled by default, its forward writer has not started, and `--max-days` defaults
    to 1 -- so every scheduled turn evaluates exactly one `not_yet_indexed` day. Exiting non-zero
    for that would page, every turn, indefinitely. It still may not read as `completed`: nothing was
    promoted, and the status says which of the three outcomes this is.
    """
    session = cast("AsyncSession", object())

    report = await run_vegetation_promotion(session, store, days=[DAY], availability=EMPTY_AVAILABILITY_INDEX)

    assert report["status"] == "waiting_for_writer"
    assert report["reason"] == "forward_writer_has_indexed_none_of_these_days"
    assert report["not_yet_indexed_days"] == [DAY.isoformat()]
    assert exit_code_for(report) == 0


async def test_a_mixed_turn_that_promoted_nothing_still_fails(store: ObjectStore) -> None:
    """`waiting_for_writer` is the ALL-unindexed case only; a mixed no-progress turn stays non-zero."""
    session = cast("AsyncSession", object())
    other_day = date(2026, 9, 11)
    availability = AvailabilityIndexDays(
        verdicts={DAY: IndexedDay(state="governed_absence", absence_reason="upstream_scene_not_published")}
    )

    report = await run_vegetation_promotion(session, store, days=[DAY, other_day], availability=availability)

    assert report["status"] == "no_days_promoted"
    assert report["reason"] == "no_indexed_day_promoted"
    assert exit_code_for(report) == 1


def test_only_a_promoting_or_waiting_turn_exits_zero() -> None:
    """Three terminal statuses, two exit codes, and `waiting_for_writer` is not `completed` (W5 S3)."""
    assert exit_code_for({"status": "completed"}) == 0
    assert exit_code_for({"status": "waiting_for_writer"}) == 0
    assert exit_code_for({"status": "no_days_promoted", "reason": "all_days_absent"}) == 1
    assert exit_code_for({"status": "no_days_promoted", "reason": "no_indexed_day_promoted"}) == 1


def test_an_absent_day_and_a_race_are_different_exception_types(store: ObjectStore) -> None:
    """`PartitionNotWrittenError` is the ONLY absence-shaped read refusal; the prune race is not (B1)."""
    with pytest.raises(PartitionNotWrittenError):
        store.read_partition(VEGETATION_PLANE_STREAM, "observed", LANE_BASE_ZOOM_TIER, DAY)

    assert not issubclass(ConcurrentPrunePartitionError, PartitionNotWrittenError)
    assert not issubclass(PartitionNotWrittenError, ConcurrentPrunePartitionError)


def test_a_written_but_empty_partition_still_fails_naming_the_lane_and_the_day() -> None:
    """The anomaly keeps its raise, and the message says which lane and which day (S5)."""
    error = EmptyDayPartitionError(layer="vegetation", day=DAY)

    assert "vegetation" in str(error)
    assert DAY.isoformat() in str(error)


def test_default_promotion_days_uses_the_newest_published_day_as_the_ceiling() -> None:
    """The ceiling is the AVAILABILITY INDEX's newest `published` day, not `settled_through`."""
    newest_published = date(2026, 9, 15)
    availability = AvailabilityIndexDays(
        verdicts={
            date(2026, 9, 12): IndexedDay(state="published"),
            newest_published: IndexedDay(state="published"),
            date(2026, 9, 18): IndexedDay(state="governed_absence", absence_reason="upstream_scene_not_published"),
        }
    )

    days = default_promotion_days(availability=availability, today=date(2026, 9, 18), max_days=3)

    assert days == (
        newest_published - timedelta(days=2),
        newest_published - timedelta(days=1),
        newest_published,
    )


def test_default_promotion_days_ignores_a_published_day_after_today() -> None:
    """A `published` day beyond `today` is never the ceiling, even if it is the newest in the index."""
    availability = AvailabilityIndexDays(
        verdicts={
            date(2026, 9, 10): IndexedDay(state="published"),
            date(2026, 9, 30): IndexedDay(state="published"),
        }
    )

    days = default_promotion_days(availability=availability, today=date(2026, 9, 18), max_days=1)

    assert days == (date(2026, 9, 10),)


def test_default_promotion_days_returns_empty_when_the_index_has_no_published_day() -> None:
    """An index with nothing `published` yields no days, never a `settled_through`-style raise.

    Feeding the empty tuple through the real `run_vegetation_promotion` turn (rather than asserting
    on `default_promotion_days` alone) proves the caller's existing `no_days_promoted` handling
    absorbs it end to end -- the exact turn shape that raised in production before this fix.
    """
    availability = EMPTY_AVAILABILITY_INDEX

    days = default_promotion_days(availability=availability, today=date(2026, 9, 18), max_days=1)
    assert days == ()


async def test_an_empty_default_promotion_days_result_reports_no_days_promoted(store: ObjectStore) -> None:
    """Zero days evaluated ends the turn as `no_days_promoted`, never a raised `ValueError`."""
    session = cast("AsyncSession", object())  # never touched: an empty `days` sequence opens no object
    days = default_promotion_days(availability=EMPTY_AVAILABILITY_INDEX, today=date(2026, 9, 18), max_days=1)

    report = await run_vegetation_promotion(session, store, days=days, availability=EMPTY_AVAILABILITY_INDEX)

    assert report["status"] == "no_days_promoted"
    assert report["days"] == []
    assert exit_code_for(report) == 1


def test_default_promotion_days_rejects_a_non_positive_max_days() -> None:
    with pytest.raises(ValueError, match="--max-days"):
        default_promotion_days(availability=EMPTY_AVAILABILITY_INDEX, today=date(2026, 9, 18), max_days=0)


def test_vegetation_partition_promotion_never_imports_the_frozen_postgres_forward_module() -> None:
    """Execution must not reach into `pipeline.direct.vegetation.forward` (Postgres `agri.vegetation`,
    retired 2026-09-04). The source is walked directly, rather than asserting on `sys.modules`,
    because an already-imported sibling module would make a `sys.modules` check pass even if this
    module re-added the import (`tests/test_layer_import_contract.py` uses the same AST-walk idiom).
    """
    import ast
    from pathlib import Path

    module_path = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "agri_data_service"
        / "execution"
        / "vegetation_partition_promotion.py"
    )
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    imported_modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.append(node.module)
        elif isinstance(node, ast.Import):
            imported_modules.extend(alias.name for alias in node.names)

    forbidden = "agri_data_service.pipeline.direct.vegetation.forward"
    assert not any(module == forbidden or module.startswith(forbidden + ".") for module in imported_modules)

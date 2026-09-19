"""Per-day-partition content-SHA scoped promotion: idempotent, resumable, and evaluation-safe.

Exercises `execution/vegetation_partition_promotion.py` against an in-memory `ObjectStore` backend
(the same idiom `tests/parquet/test_objectstore_writer.py::RecordingBackend` uses) and a stub
register call, so no real Postgres session or Parquet partition is required to prove the checksum
scoping decision (owner 2026-09-18) is correct.
"""

# ruff: noqa: PLR2004 - the literals here are fixture call counts, and naming each one hides the assertion.

from __future__ import annotations

import ast
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

from agri_data_service.execution.lane_specs import (
    VEGETATION_PROMOTION_STALE_CEILING_LAG_ALLOWANCES,
    vegetation_promotion_declared_lag_days,
)
from agri_data_service.execution.vegetation_ndvi_plane import (
    GovernedPlane,
    PartitionRegistrationError,
    RegistrationSummary,
    ReleaseMaterialisation,
    SelectionMaterialisation,
    UnregisteredPartitionCellsError,
)
from agri_data_service.execution.vegetation_partition_promotion import (
    COMPLETED_STATUS,
    FAILED_STATUS,
    FAILING_TURN_STATUSES,
    NO_DAYS_PROMOTED_STATUS,
    NOT_SERVABLE_DAY_STATUS,
    REGISTRATION_REFUSED_STATUS,
    STALE_CEILING_STATUS,
    SUCCESSFUL_TURN_STATUSES,
    TERMINAL_REPORT_CORE_KEYS,
    TERMINAL_STATUSES,
    WAITING_FOR_WRITER_STATUS,
    AvailabilityIndexDays,
    AvailabilityPartitionConflictError,
    EmptyDayPartitionError,
    EvaluationArtifactNotPromotableError,
    IndexedDay,
    PromotionCeiling,
    VegetationDayPartitionKey,
    VegetationPromotionReceipt,
    availability_days_at_base_rung,
    ceiling_fields,
    day_partition_content_sha256,
    default_promotion_days,
    exit_code_for,
    failed_report,
    load_promotion_receipt,
    newest_servable_day,
    promote_vegetation_day_partition,
    promotion_ceiling,
    run_vegetation_promotion,
    save_promotion_receipt,
    stale_ceiling_report,
)
from agri_data_service.execution.vegetation_partition_promotion import (
    _promotion_report as promotion_report_of,
)
from agri_data_service.foundation.parquet.zoom import ZOOM_TIERS
from agri_data_service.parquet_ops.coverage import CensusLane
from agri_data_service.pipeline.constants import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.parquet.lane_registry import LANE_REGISTRY
from agri_data_service.pipeline.parquet.objectstore import (
    ConcurrentPrunePartitionError,
    ListedObject,
    ObjectStore,
    PartitionNotWrittenError,
)
from agri_data_service.warehouse.schemas.vegetation import VEGETATION_PLANE_STREAM

# `index_of`/`terminal_row` are the only builders here that assemble a VALID `AvailabilityIndex` --
# pointer digest, receipt shapes and rung ladder all conforming. Imported rather than copied: a
# second set of index builders drifts from the contract the first encodes (engineering-principles §1).
from tests.parquet_ops.test_availability_coverage import index_of, terminal_row

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
    """A stub `register_governed_partition_plane` that counts calls without touching Postgres.

    Its signature is the real verb's, keyword for keyword: a stub that accepted `**kwargs` would
    have kept passing through the 2026-09-19 rename that broke the two halves apart.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[date, tuple[tuple[str, float], ...]]] = []

    async def __call__(
        self, session: object, *, observed_day: date, cell_values: tuple[tuple[str, float], ...]
    ) -> RegistrationSummary:
        del session
        self.calls.append((observed_day, cell_values))
        return _registration_summary()


class RefusingRegister:
    """A stub register verb that refuses by name, the way every real refusal does."""

    def __init__(self, refusal: PartitionRegistrationError) -> None:
        self.refusal = refusal
        self.calls = 0

    async def __call__(self, session: object, *, observed_day: date, cell_values: object) -> RegistrationSummary:
        del session, observed_day, cell_values
        self.calls += 1
        raise self.refusal


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
    promoted_day, cell_values = register.calls[0]
    assert promoted_day == DAY
    # The VALUES, not just the keys: they are the register verb's only source now that the frozen
    # `agri.vegetation` corpus it used to re-read them from is gone.
    assert set(cell_values) == {("cell-a", 0.2), ("cell-b", 0.4)}
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
    # Re-promotion is still scoped to this one day's cells, never widened to a corpus-wide call,
    # and carries the CHANGED value rather than the one the first promotion registered.
    assert register.calls[1] == (DAY, (("cell-a", 0.35),))


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
EMPTY_AVAILABILITY_INDEX = AvailabilityIndexDays(verdicts={}, servable_days=frozenset())


async def test_an_indexed_governed_absence_is_reported_with_the_index_reason() -> None:
    """The index, not a failed object read, produces the absence -- and carries its OWN reason (B2)."""
    session = cast("AsyncSession", object())  # never touched: the absent path reaches no register call
    availability = AvailabilityIndexDays(
        verdicts={DAY: IndexedDay(state="governed_absence", absence_reason="upstream_scene_not_published")},
        servable_days=frozenset(),
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
    availability = AvailabilityIndexDays(verdicts={DAY: IndexedDay(state="published")}, servable_days=frozenset({DAY}))
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
    snapshot = AvailabilityIndexDays(verdicts={DAY: IndexedDay(state="published")}, servable_days=frozenset({DAY}))
    after_prune = AvailabilityIndexDays(
        verdicts={DAY: IndexedDay(state="governed_absence", absence_reason="pruned_by_retention")},
        servable_days=frozenset(),
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
        servable_days=frozenset({DAY}),
        generation_sha256=SNAPSHOT_GENERATION,
        pointer_key=AVAILABILITY_POINTER_KEY,
    )
    after_regression = AvailabilityIndexDays(
        verdicts={},
        servable_days=frozenset(),
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
        servable_days=frozenset({DAY}),
        generation_sha256=SNAPSHOT_GENERATION,
        pointer_key=AVAILABILITY_POINTER_KEY,
    )
    still_published = AvailabilityIndexDays(
        verdicts={DAY: IndexedDay(state="published")},
        servable_days=frozenset({DAY}),
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
        },
        servable_days=frozenset(),
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
        verdicts={DAY: IndexedDay(state="governed_absence", absence_reason="upstream_scene_not_published")},
        servable_days=frozenset(),
    )

    report = await run_vegetation_promotion(session, store, days=[DAY, other_day], availability=availability)

    assert report["status"] == "no_days_promoted"
    assert report["reason"] == "no_indexed_day_promoted"
    assert exit_code_for(report) == 1


def test_only_a_promoting_or_waiting_turn_exits_zero() -> None:
    """Six terminal statuses, two exit codes, and `waiting_for_writer` is not `completed` (W5 S3)."""
    assert exit_code_for({"status": "completed"}) == 0
    assert exit_code_for({"status": "waiting_for_writer"}) == 0
    assert exit_code_for({"status": "no_days_promoted", "reason": "all_days_absent"}) == 1
    assert exit_code_for({"status": "no_days_promoted", "reason": "no_indexed_day_promoted"}) == 1
    assert exit_code_for({"status": STALE_CEILING_STATUS}) == 1
    assert exit_code_for({"status": REGISTRATION_REFUSED_STATUS}) == 1
    assert exit_code_for({"status": FAILED_STATUS}) == 1


def test_every_terminal_status_has_exactly_one_exit_code() -> None:
    """The vocabularies W9-C, W9-F and `main()`'s own handler grew must PARTITION: one outcome, one status.

    `exit_code_for` fails closed on anything else, so this is the proof that "anything else" is
    empty rather than a silent third category. `failed` is in the set because `main()` REALLY prints
    it; while it sat outside, this test proved a property of a vocabulary that excluded a reachable
    report status (STYLE-REVIEW-W9 S3).
    """
    assert SUCCESSFUL_TURN_STATUSES.isdisjoint(FAILING_TURN_STATUSES)
    assert TERMINAL_STATUSES == SUCCESSFUL_TURN_STATUSES | FAILING_TURN_STATUSES
    assert {COMPLETED_STATUS, WAITING_FOR_WRITER_STATUS} == SUCCESSFUL_TURN_STATUSES
    assert {
        NO_DAYS_PROMOTED_STATUS,
        REGISTRATION_REFUSED_STATUS,
        STALE_CEILING_STATUS,
        FAILED_STATUS,
    } == FAILING_TURN_STATUSES
    for status in TERMINAL_STATUSES:
        assert exit_code_for({"status": status}) == (0 if status in SUCCESSFUL_TURN_STATUSES else 1)


def test_an_unknown_status_still_fails_closed() -> None:
    """Outside the vocabulary is a defect in the caller, and the lane may not exit 0 on one."""
    assert exit_code_for({"status": "green"}) == 1
    assert exit_code_for({}) == 1
    assert exit_code_for({"status": None}) == 1


def test_an_escaped_exception_is_reported_in_the_turns_own_vocabulary() -> None:
    """`main()`'s handler goes THROUGH `exit_code_for`, rather than writing its exit code by hand."""
    report = failed_report(RuntimeError("the object store refused"))

    assert report["status"] == FAILED_STATUS
    assert report["status"] in TERMINAL_STATUSES
    assert report["error"] == "RuntimeError: the object store refused"
    assert report["days"] == []
    assert exit_code_for(report) == 1


def test_a_day_refused_for_non_servability_is_distinguishable_from_a_registration_refusal() -> None:
    """Two different refusals, two different day statuses and two different top-level lists.

    Nothing about a `not_servable` day is defective -- the register verb was never offered it -- so
    folding it into `registration_refused_days` would send an operator looking for a lattice cell or
    a non-finite value that does not exist.
    """
    report = promotion_report_of([_not_servable_day_entry(DAY), _refused_entry(date(2026, 9, 11))])

    assert report["not_servable_days"] == [DAY.isoformat()]
    assert report["registration_refused_days"] == [date(2026, 9, 11).isoformat()]
    assert report["status"] == REGISTRATION_REFUSED_STATUS, "a real defect still dominates"


def _refused_entry(day: date) -> dict[str, object]:
    """One day entry shaped exactly as `run_vegetation_promotion` renders a refusal."""
    return {
        "day": day.isoformat(),
        "layer": VEGETATION_PLANE_STREAM,
        "status": REGISTRATION_REFUSED_STATUS,
        "error_class": "UnregisteredPartitionCellsError",
        "reason": "agri.spatial_cell does not hold these cells",
    }


def _not_servable_day_entry(day: date) -> dict[str, object]:
    """One day entry shaped exactly as `run_vegetation_promotion` renders a non-servable day."""
    return {
        "day": day.isoformat(),
        "layer": VEGETATION_PLANE_STREAM,
        "status": NOT_SERVABLE_DAY_STATUS,
        "reason": "availability_index_does_not_publish_this_day_at_every_required_rung",
    }


def _governed_absence_day_entry(day: date) -> dict[str, object]:
    """One day entry shaped exactly as `run_vegetation_promotion` renders an indexed absence."""
    return {
        "day": day.isoformat(),
        "layer": VEGETATION_PLANE_STREAM,
        "status": "absent",
        "reason": "upstream_scene_not_published",
    }


def _promoted_entry(day: date) -> dict[str, object]:
    """One day entry shaped exactly as `run_vegetation_promotion` renders a promotion."""
    return {
        "day": day.isoformat(),
        "layer": VEGETATION_PLANE_STREAM,
        "status": "promoted",
        "content_sha256": "0" * 64,
        "cell_count": 2,
    }


def test_a_refused_registration_is_a_named_terminal_status_not_a_bare_exception() -> None:
    """The refusal reaches the report, which is what the 2026-09-19 rollbacks did not get."""
    report = promotion_report_of([_refused_entry(DAY)])

    assert report["status"] == REGISTRATION_REFUSED_STATUS
    assert report["registration_refused_days"] == [DAY.isoformat()]
    assert report["reason"] == "the_registration_verb_refused_at_least_one_day_partition"
    assert exit_code_for(report) == 1


def test_a_refusal_dominates_a_turn_that_also_promoted() -> None:
    """A refusal is a defect in the day it names, so a mixed turn may not report `completed`.

    It is equally not `no_days_promoted`: one day DID promote, and that status's name would be
    false. The two lanes' vocabularies are resolved by precedence, not by sharing a name.
    """
    report = promotion_report_of([_promoted_entry(date(2026, 9, 11)), _refused_entry(DAY)])

    assert report["status"] == REGISTRATION_REFUSED_STATUS
    assert report["registration_refused_days"] == [DAY.isoformat()]
    assert exit_code_for(report) == 1


async def test_the_day_verb_lets_a_registration_refusal_through_for_the_turn_to_name() -> None:
    """`promote_vegetation_day_partition` never swallows a refusal; the turn loop renders it."""
    refusal = UnregisteredPartitionCellsError(observed_day=DAY, cell_keys=("43.1250:-116.1250",))
    register = RefusingRegister(refusal)

    with pytest.raises(PartitionRegistrationError) as caught:
        await promote_vegetation_day_partition(
            None,
            day=DAY,
            kind="observed",
            cell_values=[("43.1250:-116.1250", 0.4)],
            previous_receipt=None,
            register=register,
        )

    assert caught.value is refusal
    assert register.calls == 1


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
        },
        servable_days=frozenset({date(2026, 9, 12), newest_published}),
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
        },
        servable_days=frozenset({date(2026, 9, 10), date(2026, 9, 30)}),
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


# The coverage helpers' own pointer ceiling is 2026-08-07, so these days sit under it.
FULL_LADDER_DAY = date(2026, 8, 5)
BASE_ONLY_DAY = date(2026, 8, 6)
LADDER_TODAY = date(2026, 8, 7)
VEGETATION_CENSUS_LANE = CensusLane(layer=VEGETATION_PLANE_STREAM, nature="daily_series", kind="observed")


def mixed_ladder_availability() -> AvailabilityIndexDays:
    """One real index: an older day published at every rung, a newer one published at the base only."""
    rows = [terminal_row(VEGETATION_CENSUS_LANE, day=FULL_LADDER_DAY, rung=rung) for rung in ZOOM_TIERS]
    rows += [
        terminal_row(
            VEGETATION_CENSUS_LANE,
            day=BASE_ONLY_DAY,
            rung=rung,
            terminal_state="published" if rung == LANE_BASE_ZOOM_TIER else "governed_absence",
        )
        for rung in ZOOM_TIERS
    ]
    return availability_days_at_base_rung(index_of(VEGETATION_CENSUS_LANE, rows))


def test_a_day_published_only_at_the_base_rung_is_not_servable() -> None:
    """§4a: a selectable day is the rung ladder's INTERSECTION, not the base rung's own verdict."""
    availability = mixed_ladder_availability()

    assert availability.indexed_day(BASE_ONLY_DAY).state == "published", "the base rung does state it"
    assert not availability.is_servable(BASE_ONLY_DAY), "but the ladder above it does not"
    assert availability.is_servable(FULL_LADDER_DAY)


def test_the_ceiling_skips_a_day_the_rung_ladder_does_not_agree_on() -> None:
    """The promoted day registers a WHOLE zoom-independent governed day, so it must be servable."""
    ceiling = promotion_ceiling(
        availability=mixed_ladder_availability(),
        today=LADDER_TODAY,
        declared_lag_days=vegetation_promotion_declared_lag_days(),
    )

    assert ceiling.day == FULL_LADDER_DAY
    assert ceiling.age_days == (LADDER_TODAY - FULL_LADDER_DAY).days
    assert newest_servable_day(availability=mixed_ladder_availability(), today=LADDER_TODAY) == ceiling.day, (
        "one predicate decides the ceiling for the window and for the measurement"
    )


# The catch-up shape is the MIRROR of `mixed_ladder_availability`: here the ladder agrees on the
# NEWER day, so the base-rung-only day sits below the ceiling and inside a `--max-days > 1` window.
CATCH_UP_CEILING_DAY = date(2026, 8, 6)
CATCH_UP_BASE_ONLY_DAY = date(2026, 8, 5)


def catch_up_ladder_availability() -> AvailabilityIndexDays:
    """The `--max-days` catch-up shape: a servable ceiling with a base-rung-only day BELOW it."""
    rows = [terminal_row(VEGETATION_CENSUS_LANE, day=CATCH_UP_CEILING_DAY, rung=rung) for rung in ZOOM_TIERS]
    rows += [
        terminal_row(
            VEGETATION_CENSUS_LANE,
            day=CATCH_UP_BASE_ONLY_DAY,
            rung=rung,
            terminal_state="published" if rung == LANE_BASE_ZOOM_TIER else "governed_absence",
        )
        for rung in ZOOM_TIERS
    ]
    return availability_days_at_base_rung(index_of(VEGETATION_CENSUS_LANE, rows))


async def test_a_wider_window_never_promotes_a_day_only_the_base_rung_publishes() -> None:
    """STYLE-REVIEW-W9 B2: §4a's intersection binds EVERY day in the window, not only the ceiling.

    The ceiling gate alone left the defect one operator flag away: a `--max-days 7` catch-up after
    an outage registered every sub-ceiling day off `indexed_day`, the BASE-rung verdict, so a day
    the finer rungs do not publish became a whole zoom-independent governed day serving cannot
    answer above the base rung (STYLE-REVIEW-W8 S3).

    `RefusingBackend` carries the other half of the claim: such a day is settled from the index
    alone, and no object is opened for it.
    """
    availability = catch_up_ladder_availability()
    session = cast("AsyncSession", object())
    refusing_store = ObjectStore(backend=RefusingBackend())

    window = default_promotion_days(availability=availability, today=LADDER_TODAY, max_days=2)
    report = await run_vegetation_promotion(
        session, refusing_store, days=[CATCH_UP_BASE_ONLY_DAY], availability=availability
    )

    assert window == (CATCH_UP_BASE_ONLY_DAY, CATCH_UP_CEILING_DAY), "the window still NAMES the day"
    assert availability.indexed_day(CATCH_UP_BASE_ONLY_DAY).state == "published", "the base rung does state it"
    assert report["not_servable_days"] == [CATCH_UP_BASE_ONLY_DAY.isoformat()]
    assert report["registration_refused_days"] == [], "nothing was offered to the register verb"
    (entry,) = cast("list[dict[str, object]]", report["days"])
    assert entry["status"] == NOT_SERVABLE_DAY_STATUS
    assert entry["reason"] == "availability_index_does_not_publish_this_day_at_every_required_rung"
    assert report["status"] == WAITING_FOR_WRITER_STATUS
    assert report["reason"] == "forward_writer_has_published_none_of_these_days_at_every_required_rung"
    assert exit_code_for(report) == 0


def test_an_availability_must_state_its_own_servable_days() -> None:
    """STYLE-REVIEW-W10 S7: there is ONE servability predicate, and a test double runs the same one.

    While `servable_days` defaulted to `None`, `is_servable` fell back to the base-rung verdict --
    an arm `availability_days_at_base_rung`, the only production constructor, can never reach,
    because it always supplies a frozenset. Every governed-absence test above ran against that
    unreachable arm. The field now has no default, so a double states the intersection it wants and
    the turn evaluates the shipped predicate either way.
    """
    with pytest.raises(TypeError):
        AvailabilityIndexDays(verdicts={DAY: IndexedDay(state="published")})  # type: ignore[call-arg]

    stated = AvailabilityIndexDays(verdicts={DAY: IndexedDay(state="published")}, servable_days=frozenset({DAY}))
    assert stated.is_servable(DAY)
    assert not stated.is_servable(DAY + timedelta(days=1))

    #: The case the fallback used to hide: the base rung publishes the day and the ladder does not.
    base_rung_only = AvailabilityIndexDays(verdicts={DAY: IndexedDay(state="published")}, servable_days=frozenset())
    assert base_rung_only.indexed_day(DAY).state == "published"
    assert not base_rung_only.is_servable(DAY)


async def test_a_day_that_is_both_an_indexed_absence_and_unservable_keeps_the_index_reason() -> None:
    """The `_non_promotable_entry` ordering, proved rather than read (STYLE-REVIEW-W10 S7).

    An indexed absence is ALSO unservable -- an absent day is in no rung's published set -- so the
    two predicates overlap on every governed absence the index records. Servability is checked LAST
    for exactly that reason: reporting this day as `not_servable` would replace the index's own
    `absence_reason` with a rung-ladder verdict, and send an operator looking at the writer's rung
    publication for a day the source never had. `RefusingBackend` carries the other half: neither
    predicate opens an object.
    """
    session = cast("AsyncSession", object())
    availability = AvailabilityIndexDays(
        verdicts={DAY: IndexedDay(state="governed_absence", absence_reason="upstream_scene_not_published")},
        servable_days=frozenset(),
    )
    refusing_store = ObjectStore(backend=RefusingBackend())

    report = await run_vegetation_promotion(session, refusing_store, days=[DAY], availability=availability)

    assert report["absent_days"] == [DAY.isoformat()]
    assert report["not_servable_days"] == [], "the absence wins, and it is not double-counted"
    (entry,) = cast("list[dict[str, object]]", report["days"])
    assert entry["status"] == "absent"
    assert entry["reason"] == "upstream_scene_not_published", "the INDEX's reason, not the ladder's"


#: The bound this lane declares, aliased only so the assertions below fit one line.
STALE_AFTER_ALLOWANCES = VEGETATION_PROMOTION_STALE_CEILING_LAG_ALLOWANCES
#: The turn measures against the lane's REGISTERED lag, so every case below reads it too.
DECLARED_LAG_DAYS = vegetation_promotion_declared_lag_days()
STALE_TODAY = date(2026, 11, 18)


def ceiling_behind_today(*, days_behind: int, today: date = STALE_TODAY) -> PromotionCeiling:
    """Measure a lane whose newest servable day sits `days_behind` days behind `today`."""
    ceiling_day = today - timedelta(days=days_behind)
    availability = AvailabilityIndexDays(
        verdicts={ceiling_day: IndexedDay(state="published")}, servable_days=frozenset({ceiling_day})
    )
    return promotion_ceiling(availability=availability, today=today, declared_lag_days=DECLARED_LAG_DAYS)


def test_a_ceiling_at_the_declared_lag_day_has_used_no_allowance() -> None:
    """A healthy lane already sits a whole declared lag behind today; that is not lateness."""
    ceiling = ceiling_behind_today(days_behind=DECLARED_LAG_DAYS)

    assert ceiling.declared_lag_day == STALE_TODAY - timedelta(days=DECLARED_LAG_DAYS)
    assert ceiling.age_days == DECLARED_LAG_DAYS, "behind TODAY by the whole registered lag"
    assert ceiling.age_beyond_declared_lag_days == 0, "and behind the DECLARED-LAG DAY by nothing"
    assert ceiling.elapsed_lag_allowances == 0


def test_a_ceiling_ahead_of_the_declared_lag_day_is_floored_at_zero_rather_than_running_negative() -> None:
    """A lane that beat its own declared lag must not bank credit against a future outage."""
    ceiling = ceiling_behind_today(days_behind=1)

    assert ceiling.age_days == 1
    assert ceiling.age_beyond_declared_lag_days == 0
    assert ceiling.elapsed_lag_allowances == 0


def test_a_cloudy_fortnight_inside_the_declared_allowance_is_not_called_stale() -> None:
    """STYLE-REVIEW-W9 B1: the bound may not refuse a healthy lane in a routine PNW overcast stretch.

    Sixteen days between usable Sentinel-2 scenes is an ordinary Oct-Mar gap on a lane whose
    registered lag is a MEASURED MEDIAN of 7 with a heavy tail. Counted from `today` it exceeded two
    lags and the turn refused -- and because `--max-days` is 1, the day it skipped was never
    revisited, so the gate manufactured the hole it exists to detect. Counted past the DECLARED-LAG
    DAY it is one elapsed allowance, which the declared lag already anticipates.
    """
    cloudy_edge = ceiling_behind_today(days_behind=16)

    assert cloudy_edge.age_days > DECLARED_LAG_DAYS * STALE_AFTER_ALLOWANCES, (
        "the OLD bound, counted from today, called this lane dead"
    )
    assert cloudy_edge.age_beyond_declared_lag_days == 16 - DECLARED_LAG_DAYS
    assert cloudy_edge.elapsed_lag_allowances == 1
    assert not cloudy_edge.is_stale(stale_after_elapsed_lag_allowances=STALE_AFTER_ALLOWANCES)


def test_a_writer_that_stopped_for_two_whole_allowances_is_still_called_stale() -> None:
    """The other half of the bound: it must still catch the lane the freshness yardstick cannot."""
    last_healthy = ceiling_behind_today(days_behind=DECLARED_LAG_DAYS * STALE_AFTER_ALLOWANCES + 6)
    stopped = ceiling_behind_today(days_behind=DECLARED_LAG_DAYS * (STALE_AFTER_ALLOWANCES + 1))

    assert last_healthy.elapsed_lag_allowances == STALE_AFTER_ALLOWANCES - 1
    assert not last_healthy.is_stale(stale_after_elapsed_lag_allowances=STALE_AFTER_ALLOWANCES), (
        "one day short of the second elapsed allowance is still inside the declared slack"
    )
    assert stopped.elapsed_lag_allowances == STALE_AFTER_ALLOWANCES
    assert stopped.is_stale(stale_after_elapsed_lag_allowances=STALE_AFTER_ALLOWANCES)


def test_a_stale_turn_reports_the_day_it_promoted_rather_than_consuming_it() -> None:
    """STYLE-REVIEW-W9 B1, the third requirement: a refusal may not eat the day it refuses for.

    `DEFAULT_MAX_DAYS` is 1 and nothing revisits a day below a later ceiling, so a refusal taken
    BEFORE the window ran left its ceiling day unpromoted forever. The verdict is now applied to a
    turn that already ran, and the turn's own outcome survives as `promotion_status`.
    """
    promoted = promotion_report_of([_promoted_entry(DAY)])
    assert promoted["status"] == COMPLETED_STATUS

    report = stale_ceiling_report(promoted)

    assert report["status"] == STALE_CEILING_STATUS
    assert report["status"] not in SUCCESSFUL_TURN_STATUSES
    assert report["promotion_status"] == COMPLETED_STATUS
    assert report["days"] == [_promoted_entry(DAY)], "the day was promoted, and the report says so"
    assert exit_code_for(report) == 1


def test_a_stale_turn_keeps_the_reason_the_turn_itself_failed_for() -> None:
    """STYLE-REVIEW-W10 S3: the staleness verdict may not overwrite the turn's own reason.

    A stale turn whose ceiling day was pruned mid-turn ends `no_days_promoted`/`all_days_absent`.
    Preserving only the STATUS left the operator with `stale_ceiling` + `no_days_promoted` and no
    way to tell `all_days_absent` (the source had nothing) from `no_indexed_day_promoted` (a mixed
    turn that promoted nothing) -- two different next actions.
    """
    expected_reason = "more_declared_lag_allowances_have_elapsed_past_the_newest_servable_day_than_this_lane_permits"
    absent = promotion_report_of([_governed_absence_day_entry(DAY)])
    assert absent["reason"] == "all_days_absent"

    report = stale_ceiling_report(absent)

    assert report["promotion_status"] == NO_DAYS_PROMOTED_STATUS
    assert report["promotion_reason"] == "all_days_absent"
    assert report["reason"] == expected_reason


def test_the_ceiling_fields_name_the_declared_lag_the_bound_is_actually_applied_to() -> None:
    """A reader must re-derive the verdict from the report, and the names must not claim a probe.

    `ceiling_declared_lag_day` is `today - publication_lag_days`, a LANE_REGISTRY constant. Nothing
    in this turn queries the provider, so nothing here may be called a frontier: this directory
    already spends that word on `plan_continuation.probe_provider_frontier`, which really does probe
    (STYLE-REVIEW-W10 S1).
    """
    ceiling = ceiling_behind_today(days_behind=16)

    assert ceiling_fields(ceiling, stale_after_elapsed_lag_allowances=STALE_AFTER_ALLOWANCES) == {
        "ceiling_day": (STALE_TODAY - timedelta(days=16)).isoformat(),
        "ceiling_age_days": 16,
        "ceiling_declared_lag_day": (STALE_TODAY - timedelta(days=DECLARED_LAG_DAYS)).isoformat(),
        "ceiling_age_beyond_declared_lag_days": 16 - DECLARED_LAG_DAYS,
        "ceiling_declared_lag_days": DECLARED_LAG_DAYS,
        "ceiling_elapsed_lag_allowances": 1,
        "ceiling_stale_after_elapsed_lag_allowances": STALE_AFTER_ALLOWANCES,
        "ceiling_is_stale": False,
    }


def test_the_ceiling_fields_are_total_on_a_turn_that_never_read_an_index() -> None:
    """STYLE-REVIEW-W10 S2: the report's SHAPE may not depend on how far the turn got.

    `main()` merges these fields onto the `failed` report too, and an exception before
    `read_lane_availability` leaves no ceiling to describe. Every key is still stated, `None` where
    unknown -- except the declared bound, which is a constant and is known regardless.
    """
    rendered = ceiling_fields(None, stale_after_elapsed_lag_allowances=STALE_AFTER_ALLOWANCES)

    assert set(rendered) == set(
        ceiling_fields(ceiling_behind_today(days_behind=1), stale_after_elapsed_lag_allowances=STALE_AFTER_ALLOWANCES)
    )
    assert rendered["ceiling_stale_after_elapsed_lag_allowances"] == STALE_AFTER_ALLOWANCES
    assert all(value is None for key, value in rendered.items() if key != "ceiling_stale_after_elapsed_lag_allowances")


def test_an_index_with_no_servable_day_is_never_called_stale() -> None:
    """`no_indexed_day_promoted` is the honest answer there, and it already exits 1 on its own."""
    ceiling = promotion_ceiling(
        availability=EMPTY_AVAILABILITY_INDEX, today=STALE_TODAY, declared_lag_days=DECLARED_LAG_DAYS
    )

    assert ceiling.day is None
    assert ceiling.age_days is None
    assert ceiling.age_beyond_declared_lag_days is None
    assert ceiling.elapsed_lag_allowances is None
    assert not ceiling.is_stale(stale_after_elapsed_lag_allowances=STALE_AFTER_ALLOWANCES)


def test_the_declared_lag_is_the_lanes_registered_lag_and_never_a_literal() -> None:
    """The helper reads `publication_lag_days` at call time, and is named for that field only.

    It must NOT read `cadence_days`: vegetation is `daily_series`, whose cadence `layer-lanes.md`
    (96831d8b) §1a pins at 1, so a helper named for a "publication window" asserted a 7-day cadence
    the registry does not hold (STYLE-REVIEW-W10 S1).
    """
    registration = LANE_REGISTRY[VEGETATION_PLANE_STREAM]

    assert vegetation_promotion_declared_lag_days() == registration.publication_lag_days
    assert registration.nature == "daily_series"
    assert registration.cadence_days == 1, "the field the old name claimed, and the value it holds"
    assert vegetation_promotion_declared_lag_days() != registration.cadence_days, (
        "so the lag and the cadence are not interchangeable, and the name must say which is read"
    )
    assert STALE_AFTER_ALLOWANCES >= 2, (
        "one elapsed allowance is a single provider edge the 7-day MEDIAN gap already straddles"
    )


def test_a_non_positive_declared_lag_is_refused_rather_than_divided_by() -> None:
    """`elapsed_lag_allowances` is undefined for a zero lag, so the ceiling refuses to exist."""
    with pytest.raises(ValueError, match="declared publication lag"):
        promotion_ceiling(availability=EMPTY_AVAILABILITY_INDEX, today=STALE_TODAY, declared_lag_days=0)


def test_every_terminal_status_reports_the_same_core_keys() -> None:
    """STYLE-REVIEW-W10 S2/N3: one shape for a log consumer, across all six statuses.

    `failed` was the only terminal report with no `reason`, and `completed` the only one among the
    in-turn statuses; both are stated now. Additions are allowed and named -- `error` on `failed`,
    `promotion_status`/`promotion_reason` on `stale_ceiling` -- because a consumer keying on the
    core set never sees a key DISAPPEAR.
    """
    completed = promotion_report_of([_promoted_entry(DAY)])
    refused = promotion_report_of([_refused_entry(DAY)])
    waiting = promotion_report_of([_not_servable_day_entry(DAY)])
    absent = promotion_report_of([_governed_absence_day_entry(DAY)])
    failed = failed_report(RuntimeError("the object store refused"))
    stale = stale_ceiling_report(completed)
    by_status = {
        cast("str", report["status"]): report for report in (completed, refused, waiting, absent, failed, stale)
    }

    assert set(by_status) == TERMINAL_STATUSES, "one example report per terminal status, and no status missed"
    for status, report in by_status.items():
        assert set(report) >= TERMINAL_REPORT_CORE_KEYS, f"{status} drops a core key"
        assert isinstance(report["reason"], str), f"{status} states no reason"
    assert set(failed) - TERMINAL_REPORT_CORE_KEYS == {"error"}
    assert set(stale) - TERMINAL_REPORT_CORE_KEYS == {"promotion_status", "promotion_reason"}


def test_a_completed_turn_states_its_reason_like_every_other_status() -> None:
    """The green report is not a different shape from the red ones (STYLE-REVIEW-W10 S2/N3)."""
    report = promotion_report_of([_promoted_entry(DAY)])

    assert report["status"] == COMPLETED_STATUS
    assert report["reason"] == "at_least_one_day_was_promoted_or_confirmed_unchanged"


def test_a_failed_report_states_a_reason_naming_the_gap_it_leaves() -> None:
    """`failed` carries a `reason` like every failing status, and it does not overclaim.

    STYLE-REVIEW-W9 S2 is still open -- days committed before the exception are not rendered -- so
    the reason says the per-day outcomes were not rendered rather than that there were none.
    """
    report = failed_report(RuntimeError("the object store refused"))

    assert report["reason"] == "an_exception_escaped_the_turn_and_its_per_day_outcomes_were_not_rendered"
    assert report["error"] == "RuntimeError: the object store refused"

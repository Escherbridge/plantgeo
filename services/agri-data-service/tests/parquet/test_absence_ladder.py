"""The governed-absence LADDER: one day, one piece of evidence, four rungs, or nothing at all.

Until 2026-09-06 every lane writer called `ObjectStore.write_absence` with
`zoom=LANE_BASE_ZOOM_TIER` and stopped, so a governed absence landed at z13 alone. That day is not a
weaker availability entry, it is not an entry: `availability_index._validate_generation_day` demands
the exact four-rung ladder and `_verify_absence_object` demands each rung's row cite a marker at ITS
OWN key. Measured in production, 3,205 days across five lanes hold that shape.

WHAT THESE TESTS PIN:

  * Four rungs, ONE `GovernedAbsence`, identical bytes. `_validate_generation_day` refuses a day
    whose rungs disagree about why it is empty, so shared evidence is the contract, not a convenience.
  * COARSE RUNGS FIRST, THE BASE RUNG LAST. Only the base rung is censused, so a run that dies
    mid-ladder must leave the day re-selectable rather than covered-but-empty above a silent base.
  * The whole ladder is checked for part files BEFORE the first marker lands, and a ladder that
    fails part way is ROLLED BACK -- but never past a marker it did not create.
  * Every rung's receipt reaches the run's written-object ledger, because
    `availability_extension._rung_objects` binds an absent day from that ledger and answers
    "z0 carries no governed-absence marker from this run" when a rung is missing from it.
  * NO LANE WRITER REACHES `store.write_absence` DIRECTLY ANY MORE. That is an AST assertion over
    the real tree, not a promise: the fix is only worth as much as its least-updated call site.
  * A published day is untouched by any of it.
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Final

import pytest

from agri_data_service.foundation.parquet.absence import GovernedAbsence
from agri_data_service.foundation.parquet.paths import absence_marker_path, partition_path
from agri_data_service.pipeline.parquet.derivation import (
    ABSENCE_LADDER_TIERS,
    AbsenceLadderError,
    govern_day_absent,
    write_absence_ladder,
)
from agri_data_service.pipeline.parquet.objectstore import (
    GovernedAbsenceConflictError,
    ObjectStore,
)
from agri_data_service.warehouse.parquet.schema import SIGNAL_PLANE_STREAM
from agri_data_service.warehouse.parquet.tiers import BASE_ZOOM_TIER
from agri_data_service.warehouse.schemas.availability_index import AVAILABILITY_REQUIRED_RUNGS
from tests.parquet.test_objectstore_writer import JULY_FOURTH, RecordingBackend, signal_rows

if TYPE_CHECKING:
    from agri_data_service.foundation.parquet.paths import PartitionKind
    from agri_data_service.foundation.parquet.zoom import ZoomTier

#: A REGISTERED stream, because `write_partition` resolves a real schema and the conflict tests below
#: need genuine part files rather than a fixture's idea of one.
LAYER: Final = SIGNAL_PLANE_STREAM
KIND: Final[PartitionKind] = "observed"
#: A COARSE rung, so a ladder that refused only at the rung it reached would already have marked one.
MIDDLE_TIER: Final[ZoomTier] = 5
#: The source roots every lane writer lives under. Both are walked by the AST assertion below.
LANE_WRITER_ROOTS: Final = ("src/agri_data_service/pipeline/direct", "src/agri_data_service/pipeline/lanes")


class RefusingBackend(RecordingBackend):
    """A `RecordingBackend` that refuses a named PUT, so a ladder failing MID-WAY can be exercised.

    Subclassed rather than added to the shared fake: exactly one behaviour in this file needs it, and
    a refusal switch on a backend a hundred tests share is a switch a hundred tests can leave on.
    """

    def __init__(self) -> None:
        super().__init__()
        self.refuses_put_of: set[str] = set()

    def put(self, key: str, payload: bytes, *, content_type: str) -> None:
        if key in self.refuses_put_of:
            raise OSError(f"bucket refused to put {key}")
        super().put(key, payload, content_type=content_type)


def sample_absence() -> GovernedAbsence:
    return GovernedAbsence(
        reason="upstream served no data for the day",
        upstream_response="HTTP 200 from api.example.gov with an empty feature set",
        recorded_at=datetime(2026, 7, 5, 8, 30, tzinfo=UTC),
        run_id="run-0042",
    )


def _marker_keys_in_write_order(backend: RecordingBackend) -> list[str]:
    """Every absence marker the backend holds, IN THE ORDER IT WAS PUT (dict insertion order)."""
    return [key for key in backend.objects if key.endswith("absent.json")]


# --- the ladder ------------------------------------------------------------------------------


def test_a_new_absence_lands_at_all_four_rungs_with_one_shared_reason() -> None:
    """The whole fix in one assertion: four markers, one reason, byte-identical evidence."""
    backend = RecordingBackend()
    store = ObjectStore(backend)

    receipt = govern_day_absent(store, sample_absence(), layer=LAYER, kind=KIND, day=JULY_FOURTH)

    assert sorted(_marker_keys_in_write_order(backend)) == sorted(
        absence_marker_path(LAYER, KIND, rung, JULY_FOURTH) for rung in AVAILABILITY_REQUIRED_RUNGS
    )
    payloads = {backend.objects[absence_marker_path(LAYER, KIND, rung, JULY_FOURTH)] for rung in ABSENCE_LADDER_TIERS}
    assert len(payloads) == 1, "every rung must carry the SAME bytes, not merely the same sentence"
    reasons = {
        GovernedAbsence.from_json_bytes(backend.objects[absence_marker_path(LAYER, KIND, rung, JULY_FOURTH)]).reason
        for rung in ABSENCE_LADDER_TIERS
    }
    assert reasons == {sample_absence().reason}
    # The rung `normalise_export_outcome` and every downstream census key on.
    assert receipt.zoom == BASE_ZOOM_TIER


def test_the_ladder_writes_the_coarse_rungs_first_and_the_base_rung_last() -> None:
    """Only the base rung is censused, so an interrupted run must leave the day re-selectable."""
    backend = RecordingBackend()
    store = ObjectStore(backend)

    govern_day_absent(store, sample_absence(), layer=LAYER, kind=KIND, day=JULY_FOURTH)

    assert _marker_keys_in_write_order(backend)[-1] == absence_marker_path(LAYER, KIND, BASE_ZOOM_TIER, JULY_FOURTH)
    assert ABSENCE_LADDER_TIERS[-1] == BASE_ZOOM_TIER
    assert set(ABSENCE_LADDER_TIERS) == set(AVAILABILITY_REQUIRED_RUNGS)


def test_every_rung_reaches_the_written_object_ledger() -> None:
    """`availability_extension._rung_objects` binds an absent day from THIS RUN's ledger, per rung.

    A rung missing from the ledger is answered with "z<n> carries no governed-absence marker from
    this run, so the day cannot be indexed as absent at every required rung" -- precisely the ladder
    gap this fix exists to close, arrived at from the index's side rather than the bucket's.
    """
    backend = RecordingBackend()
    store = ObjectStore(backend)

    with store.recording_written_objects() as ledger:
        govern_day_absent(store, sample_absence(), layer=LAYER, kind=KIND, day=JULY_FOURTH)

    for rung in ABSENCE_LADDER_TIERS:
        assert ledger.absence_for(kind=KIND, zoom=rung, day=JULY_FOURTH) is not None, f"z{rung} missing from the ledger"


def test_a_second_ladder_write_is_a_clean_no_op_over_the_same_four_objects() -> None:
    """Re-marking a fully marked day changes nothing observable and raises nothing.

    The re-put of identical bytes to the same key IS the object store's no-op -- one key, one object,
    the same digest before and after -- and it is deliberately not skipped, because a skipped rung is
    a rung absent from this run's ledger and therefore a ladder gap to the availability step.
    `scripts/backfill_absence_ladder.py`, which owes no ledger, is the caller that pays literally
    nothing for an already-complete day.
    """
    backend = RecordingBackend()
    store = ObjectStore(backend)
    absence = sample_absence()
    govern_day_absent(store, absence, layer=LAYER, kind=KIND, day=JULY_FOURTH)
    first = dict(backend.objects)

    with store.recording_written_objects() as ledger:
        receipt = govern_day_absent(store, absence, layer=LAYER, kind=KIND, day=JULY_FOURTH)

    assert backend.objects == first
    assert receipt.zoom == BASE_ZOOM_TIER
    assert len(ledger.absences) == len(ABSENCE_LADDER_TIERS)


# --- the refusals ----------------------------------------------------------------------------


def test_a_part_file_at_any_rung_refuses_the_whole_ladder_before_a_marker_lands() -> None:
    """The pre-flight covers every rung, so a conflict at a COARSE rung stops the base rung too."""
    backend = RecordingBackend()
    store = ObjectStore(backend)
    store.write_partition(signal_rows(), layer=LAYER, kind=KIND, zoom=MIDDLE_TIER, day=JULY_FOURTH)

    with pytest.raises(GovernedAbsenceConflictError, match="still holds part files"):
        govern_day_absent(store, sample_absence(), layer=LAYER, kind=KIND, day=JULY_FOURTH)

    assert _marker_keys_in_write_order(backend) == [], "a refused ladder must leave the day as it found it"


def test_a_failed_rung_rolls_the_markers_this_call_created_back() -> None:
    """A ladder that stops half way would leave coarse rungs governing a day the base rung does not."""
    backend = RefusingBackend()
    store = ObjectStore(backend)
    backend.refuses_put_of.add(absence_marker_path(LAYER, KIND, BASE_ZOOM_TIER, JULY_FOURTH))

    with pytest.raises(AbsenceLadderError, match="complete ladder"):
        govern_day_absent(store, sample_absence(), layer=LAYER, kind=KIND, day=JULY_FOURTH)

    assert _marker_keys_in_write_order(backend) == []


def test_the_rollback_never_removes_a_marker_it_did_not_create() -> None:
    """A failed re-run must leave an already-governed day exactly as governed as it found it."""
    backend = RefusingBackend()
    store = ObjectStore(backend)
    absence = sample_absence()
    survivor_rung = ABSENCE_LADDER_TIERS[0]
    store.write_absence(absence, layer=LAYER, kind=KIND, zoom=survivor_rung, day=JULY_FOURTH)
    backend.refuses_put_of.add(absence_marker_path(LAYER, KIND, BASE_ZOOM_TIER, JULY_FOURTH))

    with pytest.raises(AbsenceLadderError):
        govern_day_absent(store, absence, layer=LAYER, kind=KIND, day=JULY_FOURTH)

    assert _marker_keys_in_write_order(backend) == [absence_marker_path(LAYER, KIND, survivor_rung, JULY_FOURTH)]


def test_a_ladder_asked_for_no_rungs_is_refused_rather_than_reported_as_marked() -> None:
    """An empty tier list would report a governed day over one nothing was written for."""
    store = ObjectStore(RecordingBackend())

    with pytest.raises(AbsenceLadderError, match="NO rungs"):
        write_absence_ladder(store, sample_absence(), layer=LAYER, kind=KIND, day=JULY_FOURTH, tiers=())


def test_a_ladder_naming_one_rung_twice_is_refused() -> None:
    """The receipt count would overstate what the day holds, and the second write would be invisible."""
    store = ObjectStore(RecordingBackend())

    with pytest.raises(AbsenceLadderError, match="names a rung twice"):
        write_absence_ladder(
            store,
            sample_absence(),
            layer=LAYER,
            kind=KIND,
            day=JULY_FOURTH,
            tiers=(MIDDLE_TIER, MIDDLE_TIER),
        )


def test_a_published_four_rung_day_is_untouched_by_the_ladder_writer() -> None:
    """The negative control: nothing about a healthy published day may change."""
    backend = RecordingBackend()
    store = ObjectStore(backend)
    for rung in ABSENCE_LADDER_TIERS:
        store.write_partition(signal_rows(), layer=LAYER, kind=KIND, zoom=rung, day=JULY_FOURTH)
    published = dict(backend.objects)

    with pytest.raises(GovernedAbsenceConflictError):
        govern_day_absent(store, sample_absence(), layer=LAYER, kind=KIND, day=JULY_FOURTH)

    assert backend.objects == published
    assert all(partition_path(LAYER, KIND, rung, JULY_FOURTH, 0) in backend.objects for rung in ABSENCE_LADDER_TIERS)


# --- the seam ---------------------------------------------------------------------------------


def _direct_write_absence_call_sites(root: Path) -> list[str]:
    """Return every `<something>.write_absence(...)` call under `root`, as `path:line`.

    AN AST WALK, NOT A GREP, so a docstring naming the method -- several lane modules still do, and
    correctly -- can never be mistaken for a call.
    """
    found: list[str] = []
    for module in sorted(root.rglob("*.py")):
        tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
        found.extend(
            f"{module.as_posix()}:{node.lineno}"
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "write_absence"
        )
    return found


def test_no_lane_writer_marks_one_rung_directly_any_more() -> None:
    """The fix is worth exactly as much as its least-updated call site, so the real tree is asserted.

    `ObjectStore.write_absence` stays a correct per-tier primitive and keeps its contract; what may
    not happen again is a LANE WRITER calling it, because a lane writer marks a DAY. The two callers
    that legitimately span the ladder -- `derivation.write_absence_ladder` and
    `gap_fill._govern_absent_day` -- live under `pipeline/parquet/` and are outside these roots.
    """
    service_root = Path(__file__).resolve().parents[2]
    offenders = [
        site for directory in LANE_WRITER_ROOTS for site in _direct_write_absence_call_sites(service_root / directory)
    ]

    assert offenders == [], (
        f"{offenders} call ObjectStore.write_absence directly, so those lane-days will be marked at one rung and "
        f"be unindexable; call derivation.govern_day_absent instead"
    )


def test_the_walk_that_proves_the_seam_can_actually_find_a_violation(tmp_path: Path) -> None:
    """Guard the guard: an assertion that cannot fail proves nothing about the tree it walks."""
    offending = tmp_path / "adapter.py"
    offending.write_text(
        '"""A docstring naming store.write_absence must NOT count."""\n'
        "def write(store):\n"
        "    return store.write_absence(1, layer='x', kind='observed', zoom=13, day=None)\n",
        encoding="utf-8",
    )

    assert _direct_write_absence_call_sites(tmp_path) == [f"{offending.as_posix()}:3"]

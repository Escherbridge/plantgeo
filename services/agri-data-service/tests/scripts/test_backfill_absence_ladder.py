"""The absence-ladder backfill: what it repairs, what it refuses BY NAME, and what it never touches.

The forward defect is fixed at the writer (`pipeline/parquet/derivation.py::write_absence_ladder`).
This script is the repair for the 3,205 days already written with a marker at z13 alone, measured
against production on 2026-09-06 across `fire-detections` (1,069), `burn-severity` (2,102),
`vegetation` (4), `weather-observations` (2) and one `sensors` day.

WHAT THESE TESTS PIN, in the shapes the bucket really holds:

  * A day whose only object is `absent.json` at z13 gains exactly three coarse markers, carrying the
    BASE MARKER'S OWN BYTES. No reason is minted, because `_validate_generation_day` refuses a day
    whose rungs disagree about why it is empty.
  * The `sensors` shape -- a real `part-0.parquet` stranded at one rung by a different defect -- is
    REFUSED under its own name. The part object's existence is asserted BEFORE the call, so the test
    cannot pass by the fixture having failed to write it.
  * A day whose base marker cannot be parsed is refused rather than repaired: inventing the sentence
    a later reader trusts is the one thing this script may never do.
  * A second pass over a fully marked day writes literally nothing -- zero PUTs, not "the same bytes
    again". The backfill owes no written-object ledger, so it can and must be free.
  * A normal four-rung published day is untouched, silently, and does not become a refusal.
  * `--dry-run` is the default and writes nothing; `--apply` is the only path that writes; passing
    both is refused rather than resolved by precedence.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any, Final

import pytest

from agri_data_service.foundation.parquet.absence import GovernedAbsence
from agri_data_service.foundation.parquet.completion import PartitionCompletion
from agri_data_service.foundation.parquet.paths import (
    absence_marker_path,
    completion_marker_path,
    partition_path,
)
from agri_data_service.pipeline.parquet.objectstore import ListedObject, ObjectStore
from agri_data_service.warehouse.schemas.availability_index import AVAILABILITY_REQUIRED_RUNGS
from tests.scripts import load_scripts_module

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from agri_data_service.foundation.parquet.paths import PartitionKind
    from agri_data_service.foundation.parquet.zoom import ZoomTier

BACKFILL: Any = load_scripts_module("backfill_absence_ladder.py", "backfill_absence_ladder")

LAYER: Final = "test-lane"
KIND: Final[PartitionKind] = "observed"
PREFIX: Final = "warehouse"
RECORDED_AT: Final = datetime(2026, 9, 1, tzinfo=UTC)
ABSENCE_REASON: Final = "upstream published no records for this day"
BASE_RUNG: ZoomTier = AVAILABILITY_REQUIRED_RUNGS[-1]
COARSE_RUNGS: Final = tuple(rung for rung in AVAILABILITY_REQUIRED_RUNGS if rung != BASE_RUNG)

#: The measured production shapes, one day each.
ABSENCE_ONLY_DAY: Final = date(2026, 8, 1)
STRANDED_PART_DAY: Final = ABSENCE_ONLY_DAY + timedelta(days=1)
PUBLISHED_DAY: Final = ABSENCE_ONLY_DAY + timedelta(days=2)
FULL_ABSENCE_DAY: Final = ABSENCE_ONLY_DAY + timedelta(days=3)


class InMemoryBackend:
    """An `ObjectStoreBackend` with no network and no credentials, plus the log of every PUT."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.put_keys: list[str] = []

    def put(self, key: str, payload: bytes, *, content_type: str) -> None:
        del content_type
        self.objects[key] = payload
        self.put_keys.append(key)

    def delete(self, key: str) -> None:
        self.objects.pop(key, None)

    def list_objects(self, prefix: str) -> Iterator[ListedObject]:
        for key in sorted(self.objects):
            if key.startswith(prefix):
                yield ListedObject(key=key, last_modified=RECORDED_AT)

    def size_of(self, key: str) -> int | None:
        payload = self.objects.get(key)
        return None if payload is None else len(payload)

    def get(self, key: str) -> bytes | None:
        return self.objects.get(key)


def _store(backend: InMemoryBackend) -> ObjectStore:
    return ObjectStore(backend, prefix=PREFIX)


def _key(relative_path: str) -> str:
    return f"{PREFIX}/{relative_path}"


def _absence(*, reason: str = ABSENCE_REASON, run_id: str = "absence-run") -> GovernedAbsence:
    return GovernedAbsence(
        reason=reason,
        upstream_response="200 OK, 0 features",
        recorded_at=RECORDED_AT,
        run_id=run_id,
    )


def _seed_absent_day(
    backend: InMemoryBackend,
    day: date,
    *,
    rungs: tuple[ZoomTier, ...] = (BASE_RUNG,),
    absence: GovernedAbsence | None = None,
) -> None:
    """Write one governed-absence marker per named rung and NOTHING else on the day.

    THE DEFAULT IS THE MEASURED PRODUCTION SHAPE: `absent.json` at the base rung alone, no part file
    and no completion marker anywhere on the day. `ObjectStore.write_absence` marks one tier per call,
    so this is exactly what every lane writer left behind before the ladder fix.
    """
    payload = (absence or _absence()).to_json_bytes()
    for rung in rungs:
        backend.put(_key(absence_marker_path(LAYER, KIND, rung, day)), payload, content_type="application/json")


def _seed_published_day(backend: InMemoryBackend, day: date, *, rungs: tuple[ZoomTier, ...]) -> None:
    """Write one part per named rung, closed by the completion marker that finishes it."""
    for rung in rungs:
        backend.put(_key(partition_path(LAYER, KIND, rung, day, 0)), b"PAR1-not-really-parquet", content_type="x")
        backend.put(
            _key(completion_marker_path(LAYER, KIND, rung, day)),
            PartitionCompletion(part_count=1, row_count=3, completed_at=RECORDED_AT, run_id="export").to_json_bytes(),
            content_type="application/json",
        )


def _seed_stranded_part_day(backend: InMemoryBackend, day: date) -> None:
    """THE `sensors` DEFECT: a real part file at the base rung and nothing else on the day.

    Twenty-five days hold this, stranded by a tier derivation naming `station_longitude` /
    `station_latitude` columns its base table does not carry. It holds exactly ONE rung, like an
    absence-only day, which is why the backfill's decision may never key on rung count.
    """
    backend.put(_key(partition_path(LAYER, KIND, BASE_RUNG, day, 0)), b"PAR1-not-really-parquet", content_type="x")


# --- the repair ------------------------------------------------------------------------------


def test_the_three_missing_coarse_markers_are_written_with_the_base_marker_s_own_reason() -> None:
    backend = InMemoryBackend()
    _seed_absent_day(backend, ABSENCE_ONLY_DAY)

    result = BACKFILL.backfill_lane(_store(backend), layer=LAYER, kind=KIND, apply_changes=True)

    assert result.days_needing_markers == 1
    assert result.markers_written == len(COARSE_RUNGS)
    written = {
        rung: GovernedAbsence.from_json_bytes(
            backend.objects[_key(absence_marker_path(LAYER, KIND, rung, ABSENCE_ONLY_DAY))]
        )
        for rung in AVAILABILITY_REQUIRED_RUNGS
    }
    assert {absence.reason for absence in written.values()} == {ABSENCE_REASON}
    # THE BASE MARKER'S OWN BYTES, not a re-serialisation of a freshly minted claim: run_id and
    # recorded_at travel with the reason, so nothing about the day says it was governed twice.
    assert {absence.run_id for absence in written.values()} == {"absence-run"}
    assert {absence.recorded_at for absence in written.values()} == {RECORDED_AT}


def test_a_dry_run_is_the_default_and_writes_nothing() -> None:
    backend = InMemoryBackend()
    _seed_absent_day(backend, ABSENCE_ONLY_DAY)
    before = dict(backend.objects)

    result = BACKFILL.backfill_lane(_store(backend), layer=LAYER, kind=KIND)

    assert result.days_needing_markers == 1
    assert result.markers_owed == len(COARSE_RUNGS)
    assert result.markers_written == 0
    assert backend.objects == before


def test_a_partly_marked_day_is_completed_without_rewriting_the_rungs_it_already_holds() -> None:
    """A run that died mid-ladder leaves two rungs; only the missing ones may cost a PUT."""
    backend = InMemoryBackend()
    _seed_absent_day(backend, ABSENCE_ONLY_DAY, rungs=(COARSE_RUNGS[0], BASE_RUNG))
    backend.put_keys.clear()

    result = BACKFILL.backfill_lane(_store(backend), layer=LAYER, kind=KIND, apply_changes=True)

    assert result.markers_written == len(COARSE_RUNGS) - 1
    assert backend.put_keys == [
        _key(absence_marker_path(LAYER, KIND, rung, ABSENCE_ONLY_DAY))
        for rung in BACKFILL.LADDER_RUNGS
        if rung not in (COARSE_RUNGS[0], BASE_RUNG)
    ]


def test_a_second_pass_over_a_fully_marked_day_writes_nothing_at_all() -> None:
    """Idempotence measured in PUTs, not in bytes: an already-complete day must be free."""
    backend = InMemoryBackend()
    seeded = (FULL_ABSENCE_DAY, ABSENCE_ONLY_DAY)
    _seed_absent_day(backend, FULL_ABSENCE_DAY, rungs=AVAILABILITY_REQUIRED_RUNGS)
    _seed_absent_day(backend, ABSENCE_ONLY_DAY)
    BACKFILL.backfill_lane(_store(backend), layer=LAYER, kind=KIND, apply_changes=True)
    backend.put_keys.clear()

    result = BACKFILL.backfill_lane(_store(backend), layer=LAYER, kind=KIND, apply_changes=True)

    assert backend.put_keys == []
    assert result.markers_written == 0
    assert result.days_needing_markers == 0
    # Counted from what this test seeded, so adding a day to the fixture cannot leave the
    # assertion behind still asserting the old number.
    assert result.days_already_complete == len(seeded)
    assert result.refused == []


# --- the refusals ----------------------------------------------------------------------------


def test_a_day_holding_stranded_parts_is_refused_under_its_own_name_and_left_alone() -> None:
    """THE `sensors` CASE. One rung, like an absence-only day, and a governed absence over it lies."""
    backend = InMemoryBackend()
    _seed_stranded_part_day(backend, STRANDED_PART_DAY)
    part_key = _key(partition_path(LAYER, KIND, BASE_RUNG, STRANDED_PART_DAY, 0))
    # ASSERTED BEFORE THE CALL, so a fixture that silently failed to write the part cannot make this
    # test pass by leaving the script nothing to refuse.
    assert part_key in backend.objects
    before = dict(backend.objects)

    result = BACKFILL.backfill_lane(_store(backend), layer=LAYER, kind=KIND, apply_changes=True)

    assert result.markers_written == 0
    assert backend.objects == before
    assert part_key in backend.objects
    refusals = {verdict.day: verdict.refusal for verdict in result.refused}
    assert refusals == {STRANDED_PART_DAY: BACKFILL.REFUSAL_DAY_HOLDS_DATA}
    detail = result.refused[0].detail
    assert detail is not None
    assert partition_path(LAYER, KIND, BASE_RUNG, STRANDED_PART_DAY, 0) in detail


def test_an_absence_marker_sitting_beside_real_parts_is_refused_not_propagated() -> None:
    """A `conflict` day carries both claims; copying the absence upward would publish the wrong one."""
    backend = InMemoryBackend()
    _seed_absent_day(backend, STRANDED_PART_DAY)
    _seed_stranded_part_day(backend, STRANDED_PART_DAY)
    before = dict(backend.objects)

    result = BACKFILL.backfill_lane(_store(backend), layer=LAYER, kind=KIND, apply_changes=True)

    assert backend.objects == before
    assert [verdict.refusal for verdict in result.refused] == [BACKFILL.REFUSAL_DAY_HOLDS_DATA]


def test_a_base_marker_that_cannot_be_parsed_is_refused_rather_than_replaced_with_an_invention() -> None:
    backend = InMemoryBackend()
    backend.put(
        _key(absence_marker_path(LAYER, KIND, BASE_RUNG, ABSENCE_ONLY_DAY)),
        b'{"schema_version": 1, "reason": "",',
        content_type="application/json",
    )
    before = dict(backend.objects)

    result = BACKFILL.backfill_lane(_store(backend), layer=LAYER, kind=KIND, apply_changes=True)

    assert backend.objects == before
    assert result.markers_written == 0
    assert [verdict.refusal for verdict in result.refused] == [BACKFILL.REFUSAL_BASE_MARKER_UNREADABLE]


def test_an_absence_above_the_base_rung_only_is_refused_because_there_is_nothing_to_copy() -> None:
    """A coarse rung asserting an absence its base rung does not is an admin's problem, not a repair."""
    backend = InMemoryBackend()
    _seed_absent_day(backend, ABSENCE_ONLY_DAY, rungs=(COARSE_RUNGS[0],))
    before = dict(backend.objects)

    result = BACKFILL.backfill_lane(_store(backend), layer=LAYER, kind=KIND, apply_changes=True)

    assert backend.objects == before
    assert [verdict.refusal for verdict in result.refused] == [BACKFILL.REFUSAL_NO_BASE_MARKER]


def test_rungs_that_disagree_about_why_the_day_is_empty_are_refused() -> None:
    """`_validate_generation_day` refuses such a day, so completing its ladder would repair nothing."""
    backend = InMemoryBackend()
    _seed_absent_day(backend, ABSENCE_ONLY_DAY)
    _seed_absent_day(
        backend,
        ABSENCE_ONLY_DAY,
        rungs=(COARSE_RUNGS[0],),
        absence=_absence(reason="a different upstream said a different thing"),
    )
    before = dict(backend.objects)

    result = BACKFILL.backfill_lane(_store(backend), layer=LAYER, kind=KIND, apply_changes=True)

    assert backend.objects == before
    assert [verdict.refusal for verdict in result.refused] == [BACKFILL.REFUSAL_REASONS_DISAGREE]


def test_a_published_four_rung_day_is_untouched_and_is_not_reported_as_a_refusal() -> None:
    """The negative control. A healthy lane must produce zero refusals, or the real ones are buried."""
    backend = InMemoryBackend()
    _seed_published_day(backend, PUBLISHED_DAY, rungs=AVAILABILITY_REQUIRED_RUNGS)
    before = dict(backend.objects)

    result = BACKFILL.backfill_lane(_store(backend), layer=LAYER, kind=KIND, apply_changes=True)

    assert backend.objects == before
    assert result.refused == []
    assert result.days_published == 1
    assert result.days_needing_markers == 0
    assert result.markers_written == 0


# --- the receipt and the flags ------------------------------------------------------------------


def test_the_receipt_accounts_for_every_day_it_examined() -> None:
    """Every examined day lands in exactly one bucket, so an operator can prove nothing was dropped."""
    backend = InMemoryBackend()
    seeded = (ABSENCE_ONLY_DAY, STRANDED_PART_DAY, PUBLISHED_DAY, FULL_ABSENCE_DAY)
    _seed_absent_day(backend, ABSENCE_ONLY_DAY)
    _seed_stranded_part_day(backend, STRANDED_PART_DAY)
    _seed_published_day(backend, PUBLISHED_DAY, rungs=AVAILABILITY_REQUIRED_RUNGS)
    _seed_absent_day(backend, FULL_ABSENCE_DAY, rungs=AVAILABILITY_REQUIRED_RUNGS)

    receipt = BACKFILL.backfill_lane(_store(backend), layer=LAYER, kind=KIND).to_receipt()

    # One bucket per seeded day, counted from the fixture: a day added above must show up here or
    # the accounting assertion below is proving a smaller claim than it appears to.
    assert receipt["days_examined"] == len(seeded)
    accounted = (
        int(receipt["days_needing_markers"])
        + int(receipt["days_already_marked_at_every_rung"])
        + int(receipt["published_days_left_untouched"])
        + int(receipt["refused_day_count"])
    )
    assert accounted == receipt["days_examined"]
    assert receipt["refused_days_by_reason"] == {BACKFILL.REFUSAL_DAY_HOLDS_DATA: 1}
    assert receipt["markers_owed"] == len(COARSE_RUNGS)
    assert receipt["markers_written"] == 0
    assert receipt["projected_apply_requests"] == {
        "get": 1,
        "head": len(COARSE_RUNGS),
        "list": len(COARSE_RUNGS),
        "put": len(COARSE_RUNGS),
    }
    # The receipt is this script's product, so it must survive the trip through JSON intact.
    assert json.loads(json.dumps(receipt, sort_keys=True))["lane"] == LAYER


def test_dry_run_is_the_parsed_default_and_apply_is_the_only_writing_path() -> None:
    assert BACKFILL._parse_arguments(["--lane", LAYER]).apply_changes is False
    assert BACKFILL._parse_arguments(["--lane", LAYER, "--dry-run"]).apply_changes is False
    assert BACKFILL._parse_arguments(["--lane", LAYER, "--apply"]).apply_changes is True


def test_dry_run_and_apply_together_are_refused_rather_than_resolved_by_precedence() -> None:
    """Whichever way it resolved, half the operators who typed both would be surprised by a write."""
    with pytest.raises(SystemExit):
        BACKFILL._parse_arguments(["--lane", LAYER, "--dry-run", "--apply"])


def test_a_selection_naming_no_lane_is_refused() -> None:
    with pytest.raises(SystemExit):
        BACKFILL._parse_arguments([])


def test_the_ladder_it_fills_is_the_ladder_the_availability_contract_requires() -> None:
    """Filling a different set of rungs would leave every repaired day exactly as unindexable."""
    assert set(BACKFILL.LADDER_RUNGS) == set(AVAILABILITY_REQUIRED_RUNGS)
    assert BACKFILL.BASE_RUNG == BASE_RUNG


def test_the_output_directory_is_a_path_and_defaults_to_not_persisting(tmp_path: Path) -> None:
    """`--out` is what an operator attaches to a RUNBOOK entry; without it the receipt is stdout only."""
    assert BACKFILL._parse_arguments(["--lane", LAYER, "--out", str(tmp_path)]).out == tmp_path
    assert BACKFILL._parse_arguments(["--lane", LAYER]).out is None

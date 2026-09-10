"""The availability-bootstrap compiler: what it binds by digest, what it trusts, and what it refuses.

The compiler is what ends the time slider's startup cost. With
`PARQUET_COVERAGE_AUTHORITY=census_until_bootstrap` the first coverage request after a deploy runs a
whole-stream listing census for every un-bootstrapped lane -- ~28 s against an 8 s timeout -- and no
lane could be bootstrapped because nothing compiled the input the contract demands.

WHAT THESE TESTS PIN, all of it behaviour an operator's receipt depends on:

  * The digest window is a REAL boundary. A day inside it is downloaded and hashed; a day outside it
    is bound as a manifest-trusted row and NOT ONE OF ITS PARTS IS FETCHED. The read log proves it.
  * A completion marker that recorded its own part digests defeats the window in the honest
    direction: the row is bound fully, by digests the export computed, with no download at all.
  * The emitted document passes the contract's own offline validation, because that is the only
    thing that makes `--apply` safe to hand to an operator.
  * A day that cannot be indexed is EXCLUDED WITH A NAMED REASON rather than silently dropped: a
    lane that excludes most of its history has a ladder problem, and the receipt is where it shows.
  * The two kinds of incomplete ladder are named APART. A day carrying a governed-absence marker at
    one rung and nothing else is a writer gap a marker backfill closes; a day carrying real parts at
    one rung is a broken export. Both are refused, and telling them apart is decided from the
    LISTING, before a single object is fetched.
  * Neither of them becomes admissible by widening this compiler's gate, and the last test in this
    file is the executable proof: the one-rung document such an admission would emit is refused by
    `load_bootstrap_request` -- the contract's own loader, which `_compile_and_report` already calls.
"""

from __future__ import annotations

import io
import json
from datetime import UTC, date, datetime, timedelta
from threading import Barrier, Lock
from typing import TYPE_CHECKING, Any

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest

from agri_data_service.foundation.canonical import canonical_json, sha256_digest
from agri_data_service.foundation.parquet.absence import GovernedAbsence
from agri_data_service.foundation.parquet.completion import CompletedPart, PartitionCompletion
from agri_data_service.foundation.parquet.paths import (
    absence_marker_path,
    completion_marker_path,
    partition_path,
)
from agri_data_service.parquet_ops.coverage import CensusLane
from agri_data_service.pipeline.parquet.availability_index import (
    DIGESTED_PROVENANCE,
    MANIFEST_TRUSTED_PROVENANCE,
    PROVENANCE_FIELD,
    load_bootstrap_request,
)
from agri_data_service.pipeline.parquet.lane_registry import LANE_REGISTRY
from agri_data_service.pipeline.parquet.objectstore import ListedObject, ObjectStore
from agri_data_service.warehouse.schemas.availability_index import AVAILABILITY_REQUIRED_RUNGS
from tests.scripts import load_scripts_module

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from agri_data_service.foundation.parquet.paths import PartitionKind
    from agri_data_service.foundation.parquet.zoom import ZoomTier

COMPILER: Any = load_scripts_module("compile_availability_bootstrap.py", "compile_availability_bootstrap")


def test_graduated_temperature_lanes_compile_with_their_registered_source_clock() -> None:
    """The compiler accepts live temperature ladders without a staged or snapshot-specific bypass."""
    layers = [f"climate-field-air-temperature-{statistic}" for statistic in ("mean", "max", "min")]
    arguments = COMPILER._parse_arguments([argument for layer in layers for argument in ("--lane", layer)])
    resolved = COMPILER._resolve_lanes(arguments)
    assert [lane.layer for lane in resolved] == layers
    for lane in resolved:
        registration = LANE_REGISTRY[lane.layer]
        assert lane.nature == registration.nature == "daily_series"
        assert lane.publication_lag_days == registration.publication_lag_days


LAYER = "test-lane"
KIND: PartitionKind = "observed"
PREFIX = "warehouse"
TODAY = date(2026, 9, 3)
#: Midnight of the fixed compile day: every marker must predate the instant the compile runs, and
#: `_compile_lane` stamps that instant from the real clock rather than from `--today`.
COMPLETED_AT = datetime(2026, 9, 3, tzinfo=UTC)
RECENT_DAY = TODAY - timedelta(days=2)
OLD_DAY = TODAY - timedelta(days=400)
RECORDED_DAY = TODAY - timedelta(days=401)
PARTIAL_DAY = TODAY - timedelta(days=402)
#: The measured production shapes, one day each. `absent.json` at the base rung ALONE is what all
#: 1,069 refused `fire-detections` days and all 2,102 refused `burn-severity` days actually hold; a
#: bare part file at the base rung alone is the `sensors` defect (25 days), whose tier derivation
#: names `station_longitude`/`station_latitude` columns its base table does not carry.
ABSENCE_ONLY_DAY = TODAY - timedelta(days=403)
STRANDED_PART_DAY = TODAY - timedelta(days=404)
SUBSET_COMPLETION_DAY = TODAY - timedelta(days=405)
ABSENCE_BESIDE_PARTS_DAY = TODAY - timedelta(days=406)
MIXED_LADDER_DAY = TODAY - timedelta(days=407)
FULL_ABSENCE_DAY = TODAY - timedelta(days=408)
ROW_COUNT = 3
#: One reason across a day's whole ladder: `_one_absence_reason` refuses a day whose rungs disagree
#: about WHY it is empty, and `_validate_generation_day` refuses the generation that carried it.
ABSENCE_REASON = "upstream published no records for this day"
#: The rung nothing generalises, spelled off the availability ladder rather than imported as
#: `foundation.parquet.paths.BASE_PARTITION_ZOOM` -- the same number, declared `Final[int]` there,
#: which a `tuple[ZoomTier, ...]` default below cannot take.
BASE_RUNG: ZoomTier = AVAILABILITY_REQUIRED_RUNGS[-1]


@pytest.fixture(autouse=True)
def _frozen_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Freeze `_compile_lane`'s `created_at` strictly after COMPLETED_AT.

    `_require_not_future` compares every marker's timestamp against this instant, and every marker
    below is stamped COMPLETED_AT. Without this fixture the comparison is proven only by whatever real
    day the suite happens to run on -- true today, silently false on a machine whose clock reads
    before COMPLETED_AT. See "Test hygiene" in scripts/AGENTS.md.
    """
    monkeypatch.setattr(COMPILER, "_now", lambda: COMPLETED_AT + timedelta(hours=1))


class InMemoryBackend:
    """An `ObjectStoreBackend` with no network and no credentials, plus the log of every read."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.read_keys: list[str] = []

    def put(self, key: str, payload: bytes, *, content_type: str) -> None:
        del content_type
        self.objects[key] = payload

    def delete(self, key: str) -> None:
        self.objects.pop(key, None)

    def list_objects(self, prefix: str) -> Iterator[ListedObject]:
        for key in sorted(self.objects):
            if key.startswith(prefix):
                yield ListedObject(key=key, last_modified=COMPLETED_AT)

    def size_of(self, key: str) -> int | None:
        payload = self.objects.get(key)
        return None if payload is None else len(payload)

    def get(self, key: str) -> bytes | None:
        self.read_keys.append(key)
        return self.objects.get(key)


def test_the_digest_window_decides_which_days_are_downloaded(tmp_path: Path) -> None:
    """One day inside the window, one outside, one whose marker recorded its own digests."""
    backend = InMemoryBackend()
    reader = _reader(backend)
    _seed_published_day(backend, RECENT_DAY)
    _seed_published_day(backend, OLD_DAY)
    _seed_published_day(backend, RECORDED_DAY, record_parts=True)

    compilation = COMPILER._compile_lane(reader, _lane(), arguments=_arguments(tmp_path), today=TODAY)

    provenance = {(row.day, row.rung): row.provenance for row in compilation.rows}
    assert {day for day, _rung in provenance} == {RECENT_DAY, OLD_DAY, RECORDED_DAY}
    assert provenance[(RECENT_DAY, 13)] == DIGESTED_PROVENANCE
    assert provenance[(OLD_DAY, 13)] == MANIFEST_TRUSTED_PROVENANCE
    # THE MARKER DEFEATS THE WINDOW: recorded digests bind the day fully with no download at all.
    assert provenance[(RECORDED_DAY, 13)] == DIGESTED_PROVENANCE
    assert compilation.hashed_part_count == len(AVAILABILITY_REQUIRED_RUNGS)
    downloaded = {key for key in backend.read_keys if "part-" in key}
    assert downloaded == {
        _key(partition_path(LAYER, KIND, rung, RECENT_DAY, 0)) for rung in AVAILABILITY_REQUIRED_RUNGS
    }


def test_the_default_digest_window_matches_the_design_doc() -> None:
    """D3 and scripts/AGENTS.md both describe a 90-day window; the flag's own default must agree.

    This is the exact class of bug that shipped: a default that silently contradicts a recorded
    decision is invisible to every other check, so the default itself is the thing pinned here.
    """
    arguments = COMPILER._parse_arguments(["--lane", LAYER])

    assert arguments.digest_window_days == COMPILER.DEFAULT_DIGEST_WINDOW_DAYS == 90  # noqa: PLR2004


def test_dry_run_never_corrupts_the_requested_digest_window(tmp_path: Path) -> None:
    """`--dry-run` must not silently reset `--digest-window-days` to 0, omitted or explicit.

    A prior version of `_parse_arguments` reassigned `arguments.digest_window_days = 0` whenever
    `--dry-run` was set, so a preview run (the safe way to look before `--apply`) reported and
    exercised a weaker policy than the flag's own 90-day default, without saying so.
    """
    omitted_default = _arguments(tmp_path, dry_run=True, digest_window_days=None)
    explicit_value = _arguments(tmp_path, dry_run=True, digest_window_days=45)

    assert omitted_default.digest_window_days == 90  # noqa: PLR2004
    assert explicit_value.digest_window_days == 45  # noqa: PLR2004


def test_dry_run_applies_a_zero_day_window_internally_but_reports_the_real_request(tmp_path: Path) -> None:
    """`--dry-run` must still download nothing, but the requested window must reach the receipt intact.

    `RECENT_DAY` has real, unrecorded parts and sits inside a 90-day window, so
    `test_the_digest_window_decides_which_days_are_downloaded` proves a REAL compile downloads and
    digests it. Under `--dry-run` it must instead land `manifest_trusted` with zero downloads, while
    `digest_window_days_requested` still reads 90 -- the two fields this fix introduced to make the
    gap between "requested" and "applied" a diffable fact instead of something inferred from
    `provenance` alone.
    """
    backend = InMemoryBackend()
    reader = _reader(backend)
    _seed_published_day(backend, RECENT_DAY)

    compilation = COMPILER._compile_lane(
        reader, _lane(), arguments=_arguments(tmp_path, dry_run=True, digest_window_days=90), today=TODAY
    )

    assert compilation.digest_window_days_requested == 90  # noqa: PLR2004
    assert compilation.digest_window_days_applied == 0
    provenance = {(row.day, row.rung): row.provenance for row in compilation.rows}
    assert provenance[(RECENT_DAY, 13)] == MANIFEST_TRUSTED_PROVENANCE
    assert compilation.hashed_part_count == 0
    assert not any("part-" in key for key in backend.read_keys)


def test_receipt_flags_an_entirely_manifest_trusted_compile_before_apply(tmp_path: Path) -> None:
    """The weak case must be visible beside `apply_command`, not only inside `provenance`."""
    backend = InMemoryBackend()
    _seed_published_day(backend, RECENT_DAY)

    receipt = COMPILER._compile_and_report(
        _reader(backend),
        _lane(),
        arguments=_arguments(tmp_path, dry_run=True, digest_window_days=90),
        today=TODAY,
    )

    assert receipt["digest_window_days_requested"] == 90  # noqa: PLR2004
    assert receipt["digest_window_days_applied"] == 0
    note = str(receipt["apply_readiness_note"])
    assert "ALL" in note
    assert "review before --apply" in note
    assert "applied a 0-day digest window instead of the requested 90" in note


def test_receipt_names_no_gap_when_a_real_compile_used_the_requested_window(tmp_path: Path) -> None:
    """A non-dry-run compile must not carry the dry-run gap warning: applied equals requested."""
    backend = InMemoryBackend()
    _seed_published_day(backend, RECENT_DAY)
    _seed_published_day(backend, OLD_DAY)

    receipt = COMPILER._compile_and_report(_reader(backend), _lane(), arguments=_arguments(tmp_path), today=TODAY)

    assert receipt["digest_window_days_requested"] == receipt["digest_window_days_applied"] == 90  # noqa: PLR2004
    assert "instead of the requested" not in str(receipt["apply_readiness_note"])


def test_the_emitted_document_passes_the_contracts_own_validation(tmp_path: Path) -> None:
    """A document that does not load here is one an operator would discover at `--apply` time."""
    backend = InMemoryBackend()
    _seed_published_day(backend, RECENT_DAY)
    _seed_published_day(backend, OLD_DAY)

    receipt = COMPILER._compile_and_report(
        _reader(backend),
        _lane(),
        arguments=_arguments(tmp_path),
        today=TODAY,
    )

    document = json.loads((tmp_path / LAYER / "bootstrap-input.json").read_bytes())
    assert receipt["input_sha256"] == sha256_digest((tmp_path / LAYER / "bootstrap-input.json").read_bytes())
    assert {row[PROVENANCE_FIELD] for row in document["rows"]} == {DIGESTED_PROVENANCE, MANIFEST_TRUSTED_PROVENANCE}
    assert receipt["provenance"][MANIFEST_TRUSTED_PROVENANCE]["row_count"] == len(AVAILABILITY_REQUIRED_RUNGS)
    assert receipt["evidence_objects_owed"] == len(list((tmp_path / LAYER / "evidence").rglob("*.json")))
    request = load_bootstrap_request(
        tmp_path / LAYER / "bootstrap-input.json",
        expected_sha256=str(receipt["input_sha256"]),
        expected_row_count=int(str(receipt["row_count"])),
    )
    assert request.identity.lane == LAYER
    assert request.provenance_summary == receipt["provenance"]


def test_a_day_missing_a_rung_is_excluded_with_its_reason(tmp_path: Path) -> None:
    """A generation refuses a partial ladder, so the compiler names the day instead of emitting it.

    One refused day out of two considered is half -- above `REFUSED_DAY_FRACTION_CEILING` -- so this
    fixture also exercises `--accept-exclusions`, the override an operator who has reviewed the
    receipt passes to compile anyway. See MEDIUM 5 in scripts/AGENTS.md.
    """
    backend = InMemoryBackend()
    _seed_published_day(backend, RECENT_DAY)
    _seed_published_day(backend, PARTIAL_DAY, rungs=AVAILABILITY_REQUIRED_RUNGS[:2])

    compilation = COMPILER._compile_lane(
        _reader(backend), _lane(), arguments=_arguments(tmp_path, accept_exclusions=1), today=TODAY
    )

    assert (PARTIAL_DAY, COMPILER.EXCLUSION_PARTIAL_LADDER) in compilation.excluded_days
    assert {row.day for row in compilation.rows} == {RECENT_DAY}


def test_a_ladder_problem_is_refused_without_an_explicit_accept(tmp_path: Path) -> None:
    """The same fixture with no `--accept-exclusions` refuses the whole lane instead of shipping it."""
    backend = InMemoryBackend()
    _seed_published_day(backend, RECENT_DAY)
    _seed_published_day(backend, PARTIAL_DAY, rungs=AVAILABILITY_REQUIRED_RUNGS[:2])

    with pytest.raises(COMPILER.CompilationError, match="refused day"):
        COMPILER._compile_lane(_reader(backend), _lane(), arguments=_arguments(tmp_path), today=TODAY)


def test_an_absence_only_day_at_the_base_rung_is_refused_under_its_own_name(tmp_path: Path) -> None:
    """The measured production shape: `absent.json` at z13 and no other object on the day.

    STILL REFUSED. `availability_index._validate_generation_day` demands the exact required-rungs
    ladder from every day of a generation, and `_verify_absence_object` binds each rung's absence
    receipt to a marker AT THAT RUNG'S ZOOM -- so a day whose only marker sits at z13 cannot be
    carried by the index at one rung or at four. The last test in this file proves that end to end.

    What DOES change is the word: this is a writer that never derived its coarse markers, not an
    export whose parts are stranded, and an operator holding `--accept-exclusions 2102` has to be able
    to tell which one they are accepting.
    """
    backend = InMemoryBackend()
    _seed_published_day(backend, RECENT_DAY)
    _seed_absent_day(backend, ABSENCE_ONLY_DAY)
    marker_key = _key(absence_marker_path(LAYER, KIND, BASE_RUNG, ABSENCE_ONLY_DAY))
    assert marker_key in backend.objects

    compilation = COMPILER._compile_lane(
        _reader(backend), _lane(), arguments=_arguments(tmp_path, accept_exclusions=1), today=TODAY
    )

    assert (ABSENCE_ONLY_DAY, COMPILER.EXCLUSION_ABSENCE_ONLY_SUBLADDER) in compilation.excluded_days
    assert (ABSENCE_ONLY_DAY, COMPILER.EXCLUSION_PARTIAL_LADDER) not in compilation.excluded_days
    # The negative control travels with it: the healthy four-rung published day is untouched.
    assert {row.day for row in compilation.rows} == {RECENT_DAY}
    assert len(compilation.rows) == len(AVAILABILITY_REQUIRED_RUNGS)
    # DECIDED FROM PATHS ALONE. The rule runs inside `_candidate_days`, before `_read_markers`, so the
    # refusal costs no GET -- adding a download to that gate is what this assertion exists to catch.
    assert marker_key not in backend.read_keys


def test_parts_stranded_at_one_rung_are_still_a_partial_ladder(tmp_path: Path) -> None:
    """The `sensors` defect: a REAL part file at z13 alone, left by a tier derivation that raised.

    THE TEST THIS CHANGE MUST NOT BE ALLOWED TO PASS VACUOUSLY. Counting rungs cannot separate this
    day from the absence-only one above -- both hold exactly one rung -- so the rule keys on marker
    KIND instead. A day holding real rows at one resolution and nothing at the other three is a broken
    export, and bootstrapping it would publish a lane whose ladder really is shattered.
    """
    backend = InMemoryBackend()
    _seed_published_day(backend, RECENT_DAY)
    stranded_key = _key(partition_path(LAYER, KIND, BASE_RUNG, STRANDED_PART_DAY, 0))
    backend.put(stranded_key, _parquet_payload(ROW_COUNT), content_type="application/vnd.apache.parquet")
    assert stranded_key in backend.objects

    compilation = COMPILER._compile_lane(
        _reader(backend), _lane(), arguments=_arguments(tmp_path, accept_exclusions=1), today=TODAY
    )

    assert (STRANDED_PART_DAY, COMPILER.EXCLUSION_PARTIAL_LADDER) in compilation.excluded_days
    assert (STRANDED_PART_DAY, COMPILER.EXCLUSION_ABSENCE_ONLY_SUBLADDER) not in compilation.excluded_days
    assert {row.day for row in compilation.rows} == {RECENT_DAY}


def test_a_completion_marker_at_a_subset_of_rungs_is_still_a_partial_ladder(tmp_path: Path) -> None:
    """A finished export at two rungs of four is a partial ladder, never an absence-only sub-ladder."""
    backend = InMemoryBackend()
    _seed_published_day(backend, RECENT_DAY)
    _seed_published_day(backend, SUBSET_COMPLETION_DAY, rungs=AVAILABILITY_REQUIRED_RUNGS[:2])
    marked = completion_marker_path(LAYER, KIND, AVAILABILITY_REQUIRED_RUNGS[0], SUBSET_COMPLETION_DAY)
    assert _key(marked) in backend.objects

    compilation = COMPILER._compile_lane(
        _reader(backend), _lane(), arguments=_arguments(tmp_path, accept_exclusions=1), today=TODAY
    )

    assert (SUBSET_COMPLETION_DAY, COMPILER.EXCLUSION_PARTIAL_LADDER) in compilation.excluded_days
    assert (SUBSET_COMPLETION_DAY, COMPILER.EXCLUSION_ABSENCE_ONLY_SUBLADDER) not in compilation.excluded_days
    assert {row.day for row in compilation.rows} == {RECENT_DAY}


def test_an_absence_marker_beside_stranded_parts_is_never_read_as_absence_only(tmp_path: Path) -> None:
    """One absent rung does not make a day absent: the rule is EVERY rung present, or the ordinary word.

    The failure this forbids is an `any()` where the code says `all()`. Such a day would be filed as a
    writer gap a marker backfill closes, and a backfill would then write markers over a rung that
    holds real rows -- `ObjectStore.write_absence` refuses exactly that, one tier too late to help.
    """
    backend = InMemoryBackend()
    _seed_published_day(backend, RECENT_DAY)
    _seed_absent_day(backend, ABSENCE_BESIDE_PARTS_DAY)
    _seed_published_day(backend, ABSENCE_BESIDE_PARTS_DAY, rungs=AVAILABILITY_REQUIRED_RUNGS[:1])

    compilation = COMPILER._compile_lane(
        _reader(backend), _lane(), arguments=_arguments(tmp_path, accept_exclusions=1), today=TODAY
    )

    assert (ABSENCE_BESIDE_PARTS_DAY, COMPILER.EXCLUSION_PARTIAL_LADDER) in compilation.excluded_days
    assert (ABSENCE_BESIDE_PARTS_DAY, COMPILER.EXCLUSION_ABSENCE_ONLY_SUBLADDER) not in compilation.excluded_days
    assert {row.day for row in compilation.rows} == {RECENT_DAY}


def test_a_full_ladder_mixing_an_absence_and_published_rungs_is_still_refused_as_mixed(tmp_path: Path) -> None:
    """The terminal-state logic downstream of the gate is untouched: four rungs, two claims, refused."""
    backend = InMemoryBackend()
    _seed_published_day(backend, RECENT_DAY)
    _seed_published_day(backend, MIXED_LADDER_DAY, rungs=AVAILABILITY_REQUIRED_RUNGS[:3])
    _seed_absent_day(backend, MIXED_LADDER_DAY)

    compilation = COMPILER._compile_lane(
        _reader(backend), _lane(), arguments=_arguments(tmp_path, accept_exclusions=1), today=TODAY
    )

    assert (MIXED_LADDER_DAY, COMPILER.EXCLUSION_MIXED_TERMINAL_STATES) in compilation.excluded_days
    assert {row.day for row in compilation.rows} == {RECENT_DAY}


def test_the_refusal_budget_names_the_two_populations_where_the_number_is_typed(tmp_path: Path) -> None:
    """`--accept-exclusions N` means two different things, and this is where the operator meets N.

    `excluded_days_by_reason` already carries the split, but the refusal message is what an operator
    reads before deciding to accept 2,102 days. One share is a marker backfill; the other is a broken
    export. A number that hides which is which is a number nobody can consent to.
    """
    backend = InMemoryBackend()
    _seed_published_day(backend, RECENT_DAY)
    _seed_absent_day(backend, ABSENCE_ONLY_DAY)
    _seed_published_day(backend, SUBSET_COMPLETION_DAY, rungs=AVAILABILITY_REQUIRED_RUNGS[:2])

    with pytest.raises(COMPILER.CompilationError) as refusal:
        COMPILER._compile_lane(_reader(backend), _lane(), arguments=_arguments(tmp_path), today=TODAY)

    message = str(refusal.value)
    assert "2 refused day(s) out of 3 considered" in message
    assert "1 refused day(s) hold nothing but a governed-absence marker at a SUBSET of the rungs" in message
    assert "the other 1 strand parts or completion markers and need the export repaired" in message


def test_a_complete_four_rung_absence_day_compiles_to_governed_absence_rows(tmp_path: Path) -> None:
    """What the WRITER fix unlocks, and the reason it is the writer's fix and not this gate's.

    The moment `absent.json` exists at all four rungs the day needs nothing from this compiler that it
    does not already have: it compiles to four `governed_absence` rows, each citing the marker at its
    own zoom, and the document passes the contract's own loader untouched.
    """
    backend = InMemoryBackend()
    _seed_absent_day(backend, FULL_ABSENCE_DAY, rungs=AVAILABILITY_REQUIRED_RUNGS)

    receipt = COMPILER._compile_and_report(_reader(backend), _lane(), arguments=_arguments(tmp_path), today=TODAY)

    assert receipt["excluded_day_count"] == 0
    document = json.loads((tmp_path / LAYER / "bootstrap-input.json").read_bytes())
    assert {row["rung"] for row in document["rows"]} == set(AVAILABILITY_REQUIRED_RUNGS)
    assert {row["terminal_state"] for row in document["rows"]} == {"governed_absence"}
    assert {row["absence_reason"] for row in document["rows"]} == {ABSENCE_REASON}
    request = load_bootstrap_request(
        tmp_path / LAYER / "bootstrap-input.json",
        expected_sha256=str(receipt["input_sha256"]),
        expected_row_count=len(AVAILABILITY_REQUIRED_RUNGS),
    )
    assert {row.rung for row in request.rows} == set(AVAILABILITY_REQUIRED_RUNGS)


def test_the_contract_refuses_the_one_rung_document_an_absence_only_admission_would_emit(tmp_path: Path) -> None:
    """THE CITATION THAT SETTLES IT, executed rather than quoted.

    Admitting an absence-only day would make this compiler emit one row for it, because `_bind_day`
    binds the rungs the LISTING holds. Take the document the test above proves valid, drop the three
    coarse rows, and hand the remainder back to the same loader `_compile_and_report` runs on the
    bytes it just wrote: `availability_index._validate_generation_day` refuses it outright.

    So the widened gate would not have shipped a weaker claim -- it would have made every lane holding
    one such day fail to compile at all, since `main()` catches that `ValueError` into `failed_lanes`.
    `burn-severity` would go from five compiled days to none.
    """
    backend = InMemoryBackend()
    _seed_absent_day(backend, FULL_ABSENCE_DAY, rungs=AVAILABILITY_REQUIRED_RUNGS)
    COMPILER._compile_and_report(_reader(backend), _lane(), arguments=_arguments(tmp_path), today=TODAY)
    input_path = tmp_path / LAYER / "bootstrap-input.json"
    document = json.loads(input_path.read_bytes())

    document["rows"] = [row for row in document["rows"] if row["rung"] == BASE_RUNG]
    payload = canonical_json(document).encode("utf-8")
    input_path.write_bytes(payload)

    assert len(document["rows"]) == 1
    with pytest.raises(ValueError, match="does not contain the exact required_rungs ladder"):
        load_bootstrap_request(input_path, expected_sha256=sha256_digest(payload), expected_row_count=1)


def test_a_marker_disagreeing_with_the_listing_takes_its_whole_day(tmp_path: Path) -> None:
    """All four rungs or none: three bound rows over a refused fourth would fail validation later."""
    backend = InMemoryBackend()
    _seed_published_day(backend, RECENT_DAY)
    _seed_published_day(backend, OLD_DAY)
    backend.put(
        _key(completion_marker_path(LAYER, KIND, 13, OLD_DAY)),
        PartitionCompletion(part_count=2, row_count=ROW_COUNT, completed_at=COMPLETED_AT, run_id="run").to_json_bytes(),
        content_type="application/json",
    )

    compilation = COMPILER._compile_lane(_reader(backend), _lane(), arguments=_arguments(tmp_path), today=TODAY)

    assert (OLD_DAY, COMPILER.EXCLUSION_PART_COUNT_DISAGREES) in compilation.excluded_days
    assert {row.day for row in compilation.rows} == {RECENT_DAY}


def test_parallel_part_binding_is_bounded_and_emits_identical_evidence(tmp_path: Path) -> None:
    """Real part reads overlap while the document, artifacts, and accounting stay identical."""
    worker_count = 2
    barrier = Barrier(worker_count, timeout=5)
    lock = Lock()

    class ConcurrentBackend(InMemoryBackend):
        active = 0
        peak = 0

        def get(self, key: str) -> bytes | None:
            if not key.endswith(".parquet"):
                return super().get(key)
            with lock:
                self.active += 1
                self.peak = max(self.peak, self.active)
            try:
                barrier.wait()
                return super().get(key)
            finally:
                with lock:
                    self.active -= 1

    serial_backend = InMemoryBackend()
    for offset in range(4):
        _seed_published_day(serial_backend, RECENT_DAY - timedelta(days=offset))
    concurrent_backend = ConcurrentBackend()
    concurrent_backend.objects.update(serial_backend.objects)
    arguments = _arguments(tmp_path)
    arguments.workers = 1
    serial = COMPILER._compile_lane(_reader(serial_backend), _lane(), arguments=arguments, today=TODAY)
    arguments.workers = worker_count
    parallel = COMPILER._compile_lane(_reader(concurrent_backend), _lane(), arguments=arguments, today=TODAY)

    assert concurrent_backend.peak == worker_count
    assert concurrent_backend.active == 0
    assert parallel == serial
    assert COMPILER._json_bytes(COMPILER._bootstrap_document(parallel)) == COMPILER._json_bytes(
        COMPILER._bootstrap_document(serial)
    )


def test_parallel_failed_last_rungs_discard_day_costs_and_keep_refusals_ordered(tmp_path: Path) -> None:
    """Rejected days cannot donate downloaded bytes or source markers to surviving days."""
    backend = InMemoryBackend()
    rejected_days = (RECENT_DAY - timedelta(days=2), RECENT_DAY - timedelta(days=1))
    for day in (*rejected_days, RECENT_DAY):
        _seed_published_day(backend, day)
    for day in rejected_days:
        backend.put(
            _key(completion_marker_path(LAYER, KIND, max(AVAILABILITY_REQUIRED_RUNGS), day)),
            PartitionCompletion(
                part_count=2, row_count=ROW_COUNT, completed_at=COMPLETED_AT, run_id="bad"
            ).to_json_bytes(),
            content_type="application/json",
        )
    arguments = _arguments(tmp_path)
    arguments.workers = 1
    serial = COMPILER._compile_lane(_reader(backend), _lane(), arguments=arguments, today=TODAY)
    arguments.workers = 3
    parallel = COMPILER._compile_lane(_reader(backend), _lane(), arguments=arguments, today=TODAY)

    assert parallel == serial
    assert parallel.excluded_days == [(day, COMPILER.EXCLUSION_PART_COUNT_DISAGREES) for day in rejected_days]
    assert {row.day for row in parallel.rows} == {RECENT_DAY}
    assert parallel.hashed_part_count == parallel.digested_part_count == len(AVAILABILITY_REQUIRED_RUNGS)
    expected_bytes = sum(
        len(backend.objects[_key(partition_path(LAYER, KIND, rung, RECENT_DAY, 0))])
        for rung in AVAILABILITY_REQUIRED_RUNGS
    )
    assert parallel.hashed_part_bytes == parallel.digested_part_bytes == expected_bytes
    assert parallel.marker_recorded_rung_days == 0
    assert all(day.isoformat() not in receipt.key for day in rejected_days for receipt in parallel.input_receipts)


def test_only_time_bearing_lanes_may_be_compiled() -> None:
    """A static lookup's partition day is a version stamp, so there is no availability index to own."""
    lanes = COMPILER._resolve_lanes(COMPILER._parse_arguments(["--all-time-bearing"]))

    assert lanes
    assert {lane.nature for lane in lanes} <= {"daily_series", "release_series"}


def _reader(backend: InMemoryBackend) -> Any:
    return COMPILER.BucketReader(store=ObjectStore(backend, prefix=PREFIX), backend=backend)


def _lane() -> CensusLane:
    return CensusLane(layer=LAYER, nature="daily_series", kind=KIND)


def _arguments(
    tmp_path: Path,
    *,
    accept_exclusions: int | None = None,
    dry_run: bool = False,
    digest_window_days: int | None = 90,
) -> Any:
    argv = ["--lane", LAYER, "--out", str(tmp_path)]
    if digest_window_days is not None:
        argv.extend(["--digest-window-days", str(digest_window_days)])
    if accept_exclusions is not None:
        argv.extend(["--accept-exclusions", str(accept_exclusions)])
    if dry_run:
        argv.append("--dry-run")
    return COMPILER._parse_arguments(argv)


def _key(relative_path: str) -> str:
    return f"{PREFIX}/{relative_path}"


def _seed_published_day(
    backend: InMemoryBackend,
    day: date,
    *,
    rungs: tuple[ZoomTier, ...] = AVAILABILITY_REQUIRED_RUNGS,
    record_parts: bool = False,
) -> None:
    """Write one published day: one part per rung, then the completion marker that closes it."""
    for rung in rungs:
        part_path = partition_path(LAYER, KIND, rung, day, 0)
        payload = _parquet_payload(ROW_COUNT)
        backend.put(_key(part_path), payload, content_type="application/vnd.apache.parquet")
        recorded = (
            (
                CompletedPart(
                    relative_path=part_path,
                    row_count=ROW_COUNT,
                    byte_count=len(payload),
                    sha256=sha256_digest(payload),
                ),
            )
            if record_parts
            else ()
        )
        marker = PartitionCompletion(
            part_count=1,
            row_count=ROW_COUNT,
            completed_at=COMPLETED_AT,
            run_id=f"run-{day}-z{rung}",
            parts=recorded,
        )
        backend.put(
            _key(completion_marker_path(LAYER, KIND, rung, day)),
            marker.to_json_bytes(),
            content_type="application/json",
        )


def _seed_absent_day(
    backend: InMemoryBackend,
    day: date,
    *,
    rungs: tuple[ZoomTier, ...] = (BASE_RUNG,),
) -> None:
    """Write one governed-absence marker per named rung, and NOTHING else on the day.

    THE DEFAULT IS THE MEASURED PRODUCTION SHAPE: `absent.json` at the base rung alone, no part file
    and no completion marker anywhere on the day. `ObjectStore.write_absence` marks one tier per call,
    so this is exactly what a lane whose absence path never derived its coarse rungs leaves behind.

    One payload for the whole day, because a real backfill writes four markers in one run and because
    `_one_absence_reason` refuses a day whose rungs disagree about why it is empty.
    """
    payload = GovernedAbsence(
        reason=ABSENCE_REASON,
        upstream_response="200 OK, 0 features",
        recorded_at=COMPLETED_AT,
        run_id=f"absence-run-{day.isoformat()}",
    ).to_json_bytes()
    for rung in rungs:
        backend.put(_key(absence_marker_path(LAYER, KIND, rung, day)), payload, content_type="application/json")


def _parquet_payload(row_count: int) -> bytes:
    sink = io.BytesIO()
    pq.write_table(pa.table({"value": pa.nulls(row_count, type=pa.int8())}), sink)
    return sink.getvalue()

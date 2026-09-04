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
"""

from __future__ import annotations

import io
import json
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest

from agri_data_service.foundation.canonical import sha256_digest
from agri_data_service.foundation.parquet.completion import CompletedPart, PartitionCompletion
from agri_data_service.foundation.parquet.paths import completion_marker_path, partition_path
from agri_data_service.parquet_ops.coverage import CensusLane
from agri_data_service.pipeline.parquet.availability_index import (
    DIGESTED_PROVENANCE,
    MANIFEST_TRUSTED_PROVENANCE,
    PROVENANCE_FIELD,
    load_bootstrap_request,
)
from agri_data_service.pipeline.parquet.objectstore import ListedObject, ObjectStore
from agri_data_service.warehouse.schemas.availability_index import AVAILABILITY_REQUIRED_RUNGS
from tests.scripts import load_scripts_module

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from agri_data_service.foundation.parquet.paths import PartitionKind
    from agri_data_service.foundation.parquet.zoom import ZoomTier

COMPILER: Any = load_scripts_module("compile_availability_bootstrap.py", "compile_availability_bootstrap")

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
ROW_COUNT = 3


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


def test_only_time_bearing_lanes_may_be_compiled() -> None:
    """A static lookup's partition day is a version stamp, so there is no availability index to own."""
    lanes = COMPILER._resolve_lanes(COMPILER._parse_arguments(["--all-time-bearing"]))

    assert lanes
    assert {lane.nature for lane in lanes} <= {"daily_series", "release_series"}


def _reader(backend: InMemoryBackend) -> Any:
    return COMPILER.BucketReader(store=ObjectStore(backend, prefix=PREFIX), backend=backend)


def _lane() -> CensusLane:
    return CensusLane(layer=LAYER, nature="daily_series", kind=KIND)


def _arguments(tmp_path: Path, *, accept_exclusions: int | None = None) -> Any:
    argv = ["--lane", LAYER, "--digest-window-days", "90", "--out", str(tmp_path)]
    if accept_exclusions is not None:
        argv.extend(["--accept-exclusions", str(accept_exclusions)])
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


def _parquet_payload(row_count: int) -> bytes:
    sink = io.BytesIO()
    pq.write_table(pa.table({"value": pa.nulls(row_count, type=pa.int8())}), sink)
    return sink.getvalue()

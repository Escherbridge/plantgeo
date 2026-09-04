"""Compile one lane's `availability-bootstrap-input-v1` document from what the bucket already holds.

Run from ``services/agri-data-service``. READ-ONLY: it lists, downloads and hashes, and writes only
to ``--out`` on the local disk. It never uploads, never publishes, and never fires ``--apply``.

WHY THIS EXISTS. With ``PARQUET_COVERAGE_AUTHORITY=census_until_bootstrap`` the first coverage
request after an API deploy runs a whole-stream listing census for every un-bootstrapped lane --
measured at ~28 s against the app's 8 s coverage timeout, so every layer is withheld until the memo
warms. A lane that owns an availability index answers from one small GET instead. No lane has a
receipt today because nothing compiled the input the contract demands; this is that compiler.

THE DIGEST WINDOW, AND WHY HISTORY IS TRUSTED (owner decision D3,
``environmental_postgres_retirement_20260904``). The contract binds a published row to every part it
publishes by SHA-256. Computing those digests means downloading every part of every lane-day -- for
``fire-detections`` every day since 2000-11-01 at four rungs -- which would put the startup fix
behind the whole cutover. So parts are hashed inside ``--digest-window-days`` of today; older days
are bound as MANIFEST-TRUSTED rows, which name no part and rest on the completion marker, which is
itself fetched and digested. THIS IS TRUE OF DERIVED RUNGS ONLY. A derived rung's marker is written
by ``pipeline/parquet/derivation.py::_write_tier``, which DOES record per-part digests, so a day it
wrote needs neither a download nor the trust: the marker already carries a digest computed from each
object. The BASE rung's marker is still written v1, with no ``parts`` at all
(``pipeline/parquet/gap_fill.py::_finalize_written_day``, "stays v1 (D3)"), so every base-rung day
still falls through to a digest-window download or a manifest-trusted row exactly as before this
paragraph existed. Wiring per-part digests into that writer is chartered separately (lane A1c) and is
NOT attempted here -- see ``pipeline/parquet/AGENTS.md``, "Per-part digests", before touching it.

WHAT COMES OUT, per lane, under ``--out/<lane>/``:

* ``bootstrap-input.json`` and its SHA-256 -- the exact document ``agri data availability-bootstrap``
  consumes, already run through the offline validation path (``load_bootstrap_request``).
* ``evidence/`` -- the content-addressed source, terminal and inventory documents the input REFERS
  to. ``--apply`` reads them out of the bucket, so they must be uploaded before it is run. The
  receipt prints the exact ``aws s3 cp --recursive`` invocation; running it is an operator action.
* ``receipt.json`` -- what was compiled, the provenance split, and every excluded day with its
  reason. This is what ``evidence/availability-bootstrap-receipts.md`` records.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Final

import pyarrow.parquet as pq  # type: ignore[import-untyped]

SERVICE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVICE_ROOT / "src"))

from agri_data_service.config import settings  # noqa: E402
from agri_data_service.foundation.canonical import canonical_json, sha256_digest  # noqa: E402
from agri_data_service.foundation.parquet.absence import GovernedAbsence  # noqa: E402
from agri_data_service.foundation.parquet.completion import PartitionCompletion  # noqa: E402
from agri_data_service.foundation.parquet.lane_contract import nature_has_time_axis  # noqa: E402
from agri_data_service.foundation.parquet.paths import (  # noqa: E402
    BASE_PARTITION_ZOOM,
    try_parse_absence_marker_path,
    try_parse_completion_marker_path,
    try_parse_partition_path,
)
from agri_data_service.parquet_ops.coverage import registered_census_lanes  # noqa: E402
from agri_data_service.pipeline.parquet.availability_index import (  # noqa: E402
    BOOTSTRAP_INPUT_SCHEMA_VERSION,
    DIGESTED_PROVENANCE,
    MANIFEST_TRUSTED_PROVENANCE,
    MAX_AVAILABILITY_ROWS,
    MAX_PUBLICATION_ATTEMPTS,
    PROVENANCE_FIELD,
    AvailabilityIdentity,
    BootstrapInventoryEvidence,
    EvidenceReceipt,
    SourceEvidence,
    TerminalEvidence,
    availability_provenance_summary,
    availability_row_from_terminal_evidence,
    build_bootstrap_inventory_evidence,
    build_source_evidence,
    build_terminal_evidence,
    compute_verified_source_inventory_root,
    load_bootstrap_request,
)
from agri_data_service.pipeline.parquet.lane_ceiling import allowed_source_ceiling  # noqa: E402
from agri_data_service.pipeline.parquet.objectstore import (  # noqa: E402
    BotoObjectStoreBackend,
    ObjectStore,
    availability_lane_root,
)
from agri_data_service.warehouse.schemas.availability_index import (  # noqa: E402
    AVAILABILITY_REQUIRED_RUNGS,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from agri_data_service.foundation.parquet.paths import PartitionKind
    from agri_data_service.foundation.parquet.zoom import ZoomTier
    from agri_data_service.parquet_ops.coverage import CensusLane
    from agri_data_service.pipeline.parquet.availability_index import (
        AvailabilityProvenance,
        AvailabilityRow,
        TerminalState,
    )
    from agri_data_service.pipeline.parquet.objectstore import ObjectStoreBackend

DEFAULT_DIGEST_WINDOW_DAYS: Final = 90
DEFAULT_WORKERS: Final = 8
DEFAULT_OUT: Final = SERVICE_ROOT / ".agri-local-runs" / "availability-bootstrap"
BOOTSTRAP_INPUT_FILE_NAME: Final = "bootstrap-input.json"
RECEIPT_FILE_NAME: Final = "receipt.json"
EVIDENCE_DIRECTORY_NAME: Final = "evidence"

#: Why one day was left out of the document. Every one of these is a day the availability index will
#: NOT make selectable, so the count belongs in the receipt rather than in a log line: a lane that
#: excludes most of its history has a ladder problem, and the bootstrap is where it becomes visible.
EXCLUSION_PARTIAL_LADDER: Final = "partial_ladder"
EXCLUSION_MIXED_TERMINAL_STATES: Final = "mixed_terminal_states"
EXCLUSION_MIXED_ABSENCE_REASONS: Final = "mixed_absence_reasons"
EXCLUSION_UNMARKED_PARTS: Final = "parts_without_completion_marker"
EXCLUSION_MARKER_UNREADABLE: Final = "marker_missing_or_unreadable"
EXCLUSION_PART_COUNT_DISAGREES: Final = "part_count_disagrees_with_marker"
EXCLUSION_RECORDED_PARTS_DISAGREE: Final = "recorded_parts_disagree_with_listing"
EXCLUSION_PART_ROWS_DISAGREE: Final = "part_rows_disagree_with_marker"
EXCLUSION_PART_MISSING: Final = "part_object_missing"
EXCLUSION_EMPTY_MARKER_HOLDS_PARTS: Final = "derived_empty_marker_over_parts"
EXCLUSION_EMPTY_MARKER_AT_BASE_RUNG: Final = "derived_empty_marker_at_base_rung"
EXCLUSION_MARKER_NAME_DISAGREES: Final = "completion_marker_name_and_body_disagree"
EXCLUSION_BEYOND_SOURCE_CEILING: Final = "beyond_source_ceiling"
EXCLUSION_BEFORE_SINCE: Final = "before_requested_floor"
EXCLUSION_MARKER_IN_FUTURE: Final = "marker_postdates_this_compile"

#: These two are expected BY CONSTRUCTION -- the operator asked for them via `--since`, or the lane's
#: own source ceiling puts a day out of scope -- so they never count toward the refused-day budget
#: `_require_refusal_budget` enforces below. Every other reason is a REFUSED day: the ladder itself
#: could not be indexed, which is the shape of problem D2 warns a looser bar would hide.
FILTERED_EXCLUSION_REASONS: Final = frozenset({EXCLUSION_BEFORE_SINCE, EXCLUSION_BEYOND_SOURCE_CEILING})

#: Above this share of REFUSED (non-filtered) days out of the days considered, a lane looks like it
#: has a ladder problem rather than ordinary sparse history. `--accept-exclusions` names the exact
#: count an operator has reviewed in a `--dry-run` receipt and accepts anyway.
REFUSED_DAY_FRACTION_CEILING: Final = 0.10


class CompilationError(RuntimeError):
    """One lane or one day cannot be compiled, and the reason is the operator's to act on."""


def _now() -> datetime:
    """The wall clock `_compile_lane` stamps `created_at` from. A thin seam so tests can freeze it."""
    return datetime.now(tz=UTC)


@dataclass(frozen=True, slots=True)
class BucketReader:
    """Read-only seam over the frozen layout: one listing per rung, and byte reads by relative path."""

    store: ObjectStore
    #: The Protocol, not the boto implementation, so a test drives the whole compile with no network
    #: and no credentials -- the same seam `ObjectStore` itself is built on.
    backend: ObjectStoreBackend

    @classmethod
    def from_settings(cls) -> BucketReader:
        """Build the reader from the executor's own object-store credentials, performing no network call."""
        credentials = settings.require_object_store()
        backend = BotoObjectStoreBackend.from_credentials(credentials)
        return cls(store=ObjectStore(backend, prefix=settings.object_store_prefix), backend=backend)

    @property
    def prefix(self) -> str:
        """The bucket-root prefix every key of this warehouse sits beneath."""
        return self.store.prefix

    def list_rung(self, layer: str, kind: PartitionKind, rung: ZoomTier) -> tuple[str, ...]:
        """Return every part, completion marker and absence marker of ONE rung, as relative paths."""
        return self.store.list_partition_keys(layer, kind, rung)

    def read(self, relative_path: str) -> bytes | None:
        """Return one object's bytes, or `None` when it is not there."""
        return self.backend.get(self.store.key_for(relative_path))


@dataclass(frozen=True, slots=True)
class RungObjects:
    """Every object one rung of one lane-day holds, straight from the listing."""

    rung: int
    part_paths: tuple[str, ...]
    completion_path: str | None
    completion_is_derived_empty: bool
    absence_path: str | None

    @property
    def marker_path(self) -> str | None:
        """The one marker this rung is terminal by, if it has one at all."""
        return self.completion_path or self.absence_path


@dataclass(frozen=True, slots=True)
class MarkerRead:
    """One marker object as it was read back: its key, the digest of its bytes, and what it says."""

    relative_path: str
    sha256: str
    completion: PartitionCompletion | None
    absence: GovernedAbsence | None

    @property
    def receipt(self) -> EvidenceReceipt:
        """The receipt a row must cite this marker by."""
        return EvidenceReceipt(key=self.relative_path, sha256=self.sha256)


@dataclass(frozen=True, slots=True)
class EvidenceArtifactFile:
    """One evidence document owed to the bucket before `--apply` may verify anything."""

    key: str
    payload: bytes


@dataclass(slots=True)
class LaneCompilation:
    """Everything one lane's compile produced, including what it deliberately left out."""

    lane: CensusLane
    lane_root: str
    source_ceiling: date
    created_at: datetime
    identity: AvailabilityIdentity | None = None
    rows: tuple[AvailabilityRow, ...] = ()
    input_receipts: tuple[EvidenceReceipt, ...] = ()
    artifacts: tuple[EvidenceArtifactFile, ...] = ()
    excluded_days: list[tuple[date, str]] = field(default_factory=list)
    hashed_part_count: int = 0
    hashed_part_bytes: int = 0
    marker_read_count: int = 0
    marker_recorded_rung_days: int = 0
    #: Every DIGESTED part's count/bytes, whether hashed by THIS compile (inside the digest window) or
    #: read from a marker that already recorded them -- i.e. what `--apply` will GET at least twice.
    #: See `_apply_projected_cost` and MAJOR 3 in `scripts/AGENTS.md`.
    digested_part_count: int = 0
    digested_part_bytes: int = 0


@dataclass(slots=True)
class _DayCost:
    """Work priced while binding one day, folded into the lane's totals only once the day survives.

    A day whose LATER rung fails (`_bind_day` returns `None`) must not leave its earlier rungs' GETs
    counted in the lane total: the receipt would then price work for days that produced no row. See
    MINOR 10 in `scripts/AGENTS.md`.
    """

    hashed_part_count: int = 0
    hashed_part_bytes: int = 0
    marker_recorded_rung_days: int = 0
    digested_part_count: int = 0
    digested_part_bytes: int = 0


@dataclass(frozen=True, slots=True)
class _BoundRung:
    """One rung's binding facts, collected before the lane's identity is known.

    IDENTITY-FREE ON PURPOSE. Nothing here decides whether the rung binds -- that already happened in
    `_bind_rung` -- so this record can be turned into `TerminalEvidence` after `_bind_day` has run for
    every candidate day and the lane's `verified_source_inventory_root` is computed from the days that
    actually survived, not the days that merely looked terminal. See MINOR 8 in `scripts/AGENTS.md`.
    """

    rung: int
    terminal_state: TerminalState
    row_count: int
    published_at: datetime
    data_receipts: tuple[EvidenceReceipt, ...]
    completion_receipt: EvidenceReceipt | None
    absence_receipt: EvidenceReceipt | None
    absence_reason: str | None
    provenance: AvailabilityProvenance


@dataclass(frozen=True, slots=True)
class _BoundDay:
    """One day's whole ladder, bound and ready to render as evidence once identity is known."""

    day: date
    source_receipts: tuple[EvidenceReceipt, ...]
    rungs: tuple[_BoundRung, ...]


def main(argv: Sequence[str] | None = None) -> int:
    """Compile every requested lane and print one receipt per lane; a failed lane never stops the others."""
    arguments = _parse_arguments(argv)
    lanes = _resolve_lanes(arguments)
    reader = BucketReader.from_settings()
    today = _now().date()
    receipts: list[dict[str, object]] = []
    failures: list[dict[str, object]] = []
    for lane in lanes:
        try:
            receipts.append(_compile_and_report(reader, lane, arguments=arguments, today=today))
        except (OSError, ValueError, RuntimeError) as error:
            failures.append({"error": str(error), "lane": lane.layer, "type": type(error).__name__})
    # The receipt IS this script's product, printed for the operator to read or pipe onward.
    print(
        json.dumps(
            {
                "compiled_lanes": receipts,
                "digest_window_days": arguments.digest_window_days,
                "dry_run": arguments.dry_run,
                "failed_lanes": failures,
                "out": None if arguments.dry_run else str(arguments.out),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 1 if failures else 0


def _compile_and_report(
    reader: BucketReader,
    lane: CensusLane,
    *,
    arguments: argparse.Namespace,
    today: date,
) -> dict[str, object]:
    compilation = _compile_lane(reader, lane, arguments=arguments, today=today)
    if arguments.dry_run:
        return _receipt(compilation, input_sha256=None, input_path=None, prefix=reader.prefix)
    lane_directory = arguments.out / lane.layer
    input_path, input_sha256 = _write_lane_output(compilation, lane_directory=lane_directory)
    # THE OFFLINE VALIDATION PATH, run against the bytes just written rather than against the objects
    # still in memory: a document that parses here is one `agri data availability-bootstrap` accepts,
    # and this digest is what the operator passes as `--input-sha256`.
    request = load_bootstrap_request(
        input_path,
        expected_sha256=input_sha256,
        expected_row_count=len(compilation.rows),
    )
    receipt = _receipt(compilation, input_sha256=input_sha256, input_path=input_path, prefix=reader.prefix)
    receipt["validated_provenance"] = request.provenance_summary
    (lane_directory / RECEIPT_FILE_NAME).write_bytes(_json_bytes(receipt))
    return receipt


def _compile_lane(
    reader: BucketReader,
    lane: CensusLane,
    *,
    arguments: argparse.Namespace,
    today: date,
) -> LaneCompilation:
    """Walk one lane's ladder, read its markers, hash what the window covers, and build its rows."""
    kind: PartitionKind = arguments.kind
    compilation = LaneCompilation(
        lane=lane,
        lane_root=availability_lane_root(lane.layer, kind),
        source_ceiling=arguments.source_ceiling or allowed_source_ceiling(lane, today=today),
        created_at=_now(),
    )
    ladder = _walk_ladder(reader, layer=lane.layer, kind=kind)
    candidates = _candidate_days(ladder, compilation=compilation, since=arguments.since)
    markers = _read_markers(reader, ladder, days=candidates, workers=arguments.workers)
    compilation.marker_read_count = len(markers)
    days = _terminal_days(ladder, markers, compilation=compilation, days=candidates)
    if not days:
        raise CompilationError(
            f"{lane.layer}: no day holds the exact required-rungs ladder {AVAILABILITY_REQUIRED_RUNGS} in one "
            f"terminal state, so there is nothing to bootstrap"
        )
    if not arguments.dry_run:
        # `--dry-run` must always finish and report, even over a lane with a ladder problem: it is the
        # operator's only way to SEE `excluded_days`/`refused_day_count` before choosing a real
        # `--accept-exclusions` value. Gating it here as well would make the diagnostic un-runnable
        # for exactly the lanes that most need diagnosing.
        _require_refusal_budget(
            lane.layer,
            ladder_day_count=len(ladder),
            excluded_days=compilation.excluded_days,
            accept_exclusions=arguments.accept_exclusions,
        )
    _require_row_budget(lane.layer, day_count=len(days))
    digest_floor = today - timedelta(days=arguments.digest_window_days)
    bound_days: list[_BoundDay] = []
    for day in days:
        bound_day = _bind_day(reader, ladder, markers, day=day, digest_floor=digest_floor, compilation=compilation)
        if bound_day is not None:
            bound_days.append(bound_day)
    if not bound_days:
        raise CompilationError(f"{lane.layer}: every candidate day was excluded; see the receipt's excluded_days")
    surviving_days = tuple(bound_day.day for bound_day in bound_days)
    # IDENTITY IS COMPUTED HERE, AFTER BINDING -- not from the terminal-day set above, which can still
    # shrink inside `_bind_day` (a marker/part integrity failure on one rung takes its whole day). Its
    # `verified_source_inventory_root` and the inventory evidence below must describe exactly the days
    # that produced a row, or `--apply` pays a GET for a marker backing nothing. See MINOR 8.
    identity = _identity(compilation, markers=markers, days=surviving_days)
    compilation.identity = identity
    inventory = build_bootstrap_inventory_evidence(
        BootstrapInventoryEvidence(
            identity=identity,
            source_ceiling=compilation.source_ceiling,
            object_receipts=_inventory_receipts(markers, days=surviving_days),
        )
    )
    artifacts = [EvidenceArtifactFile(key=inventory.receipt.key, payload=inventory.payload)]
    rows: list[AvailabilityRow] = []
    for bound_day in bound_days:
        source_artifact = build_source_evidence(
            SourceEvidence(
                identity=identity,
                day=bound_day.day,
                source_ceiling=compilation.source_ceiling,
                object_receipts=bound_day.source_receipts,
            )
        )
        artifacts.append(EvidenceArtifactFile(key=source_artifact.receipt.key, payload=source_artifact.payload))
        for bound_rung in bound_day.rungs:
            terminal_evidence = TerminalEvidence(
                identity=identity,
                day=bound_day.day,
                rung=bound_rung.rung,
                terminal_state=bound_rung.terminal_state,
                row_count=bound_rung.row_count,
                source_ceiling=compilation.source_ceiling,
                published_at=bound_rung.published_at,
                source_receipt=source_artifact.receipt,
                data_receipts=bound_rung.data_receipts,
                completion_receipt=bound_rung.completion_receipt,
                absence_receipt=bound_rung.absence_receipt,
                absence_reason=bound_rung.absence_reason,
                provenance=bound_rung.provenance,
            )
            terminal_artifact = build_terminal_evidence(terminal_evidence)
            artifacts.append(EvidenceArtifactFile(key=terminal_artifact.receipt.key, payload=terminal_artifact.payload))
            rows.append(
                availability_row_from_terminal_evidence(terminal_evidence, terminal_receipt=terminal_artifact.receipt)
            )
    compilation.rows = tuple(sorted(rows, key=lambda row: (row.day, row.rung)))
    compilation.input_receipts = (inventory.receipt,)
    compilation.artifacts = tuple(artifacts)
    return compilation


def _identity(
    compilation: LaneCompilation,
    *,
    markers: dict[tuple[date, int], MarkerRead],
    days: Sequence[date],
) -> AvailabilityIdentity:
    """Name the lane and digest the marker inventory the whole compile rests on."""
    return AvailabilityIdentity(
        lane_root=compilation.lane_root,
        lane=compilation.lane.layer,
        # The lane slug is also the product: nothing reads `product` back -- `read_lane_root` passes
        # `expected_product=None` -- and a second invented name would be a second thing to keep true.
        product=compilation.lane.layer,
        nature="daily_series" if compilation.lane.nature == "daily_series" else "release_series",
        required_rungs=AVAILABILITY_REQUIRED_RUNGS,
        verified_source_inventory_root=compute_verified_source_inventory_root(_inventory_receipts(markers, days=days)),
    )


def _walk_ladder(reader: BucketReader, *, layer: str, kind: PartitionKind) -> dict[date, dict[int, RungObjects]]:
    """List all four rungs once each and fold them into one object inventory per lane-day.

    Four listings and no per-day requests: the point of a bootstrap is to pay this walk ONCE so that
    serving never lists again.
    """
    ladder: dict[date, dict[int, RungObjects]] = {}
    for rung in AVAILABILITY_REQUIRED_RUNGS:
        parts: dict[date, dict[int, str]] = {}
        completions: dict[date, tuple[str, bool]] = {}
        absences: dict[date, str] = {}
        for relative_path in reader.list_rung(layer, kind, rung):
            partition = try_parse_partition_path(relative_path)
            if partition is not None:
                parts.setdefault(partition.day, {})[partition.part_index] = relative_path
                continue
            completion = try_parse_completion_marker_path(relative_path)
            if completion is not None:
                completions[completion.day] = (relative_path, completion.derived_empty)
                continue
            absence = try_parse_absence_marker_path(relative_path)
            if absence is not None:
                absences[absence.day] = relative_path
        for day in set(parts) | set(completions) | set(absences):
            indexed = parts.get(day, {})
            completion_entry = completions.get(day)
            ladder.setdefault(day, {})[rung] = RungObjects(
                rung=rung,
                part_paths=tuple(indexed[index] for index in sorted(indexed)),
                completion_path=None if completion_entry is None else completion_entry[0],
                completion_is_derived_empty=completion_entry is not None and completion_entry[1],
                absence_path=absences.get(day),
            )
    return ladder


def _candidate_days(
    ladder: dict[date, dict[int, RungObjects]],
    *,
    compilation: LaneCompilation,
    since: date | None,
) -> tuple[date, ...]:
    """Drop the days no row could legally describe, before a single object is downloaded."""
    candidates: list[date] = []
    for day in sorted(ladder):
        if day > compilation.source_ceiling:
            compilation.excluded_days.append((day, EXCLUSION_BEYOND_SOURCE_CEILING))
        elif since is not None and day < since:
            compilation.excluded_days.append((day, EXCLUSION_BEFORE_SINCE))
        elif set(ladder[day]) != set(AVAILABILITY_REQUIRED_RUNGS):
            compilation.excluded_days.append((day, EXCLUSION_PARTIAL_LADDER))
        else:
            candidates.append(day)
    return tuple(candidates)


def _read_markers(
    reader: BucketReader,
    ladder: dict[date, dict[int, RungObjects]],
    *,
    days: Sequence[date],
    workers: int,
) -> dict[tuple[date, int], MarkerRead]:
    """Fetch every candidate rung-day's marker in parallel, hashing each as it lands.

    THE ONE UNAVOIDABLE PER-RUNG-DAY GET. The marker carries the row count and the digest a row must
    cite it by, and neither can be read out of a listing. It is a ~200-byte object; the parts it
    speaks for are megabytes, which is the whole reason the manifest-trusted class is worth having.
    """
    targets: list[tuple[date, int, str]] = []
    for day in days:
        for rung, objects in sorted(ladder[day].items()):
            marker_path = objects.marker_path
            if marker_path is not None:
                targets.append((day, rung, marker_path))
    reads: dict[tuple[date, int], MarkerRead] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        payloads = pool.map(lambda target: reader.read(target[2]), targets)
        for (day, rung, marker_path), payload in zip(targets, payloads, strict=True):
            if payload is None:
                continue
            marker = _decode_marker(marker_path, payload)
            if marker is not None:
                reads[(day, rung)] = marker
    return reads


def _decode_marker(relative_path: str, payload: bytes) -> MarkerRead | None:
    """Decode one marker, or answer `None` so its day is excluded instead of ending the lane."""
    digest = sha256_digest(payload)
    try:
        if try_parse_completion_marker_path(relative_path) is not None:
            return MarkerRead(
                relative_path=relative_path,
                sha256=digest,
                completion=PartitionCompletion.from_json_bytes(payload),
                absence=None,
            )
        return MarkerRead(
            relative_path=relative_path,
            sha256=digest,
            completion=None,
            absence=GovernedAbsence.from_json_bytes(payload),
        )
    except ValueError:
        # `PartitionCompletionError` and `GovernedAbsenceError` are both `ValueError`s. One broken
        # object costs its day, and the receipt names the day so it can be repaired.
        return None


def _terminal_days(
    ladder: dict[date, dict[int, RungObjects]],
    markers: dict[tuple[date, int], MarkerRead],
    *,
    compilation: LaneCompilation,
    days: Sequence[date],
) -> tuple[date, ...]:
    """Keep the days whose whole ladder makes ONE terminal statement, which is all a generation admits."""
    kept: list[date] = []
    for day in days:
        refusal = _day_refusal(ladder, markers, day=day)
        if refusal is not None:
            compilation.excluded_days.append((day, refusal))
            continue
        kept.append(day)
    return tuple(kept)


def _day_refusal(
    ladder: dict[date, dict[int, RungObjects]],
    markers: dict[tuple[date, int], MarkerRead],
    *,
    day: date,
) -> str | None:
    """Name why this day's ladder cannot be indexed, or `None` when it can."""
    states: set[str] = set()
    for rung, objects in sorted(ladder[day].items()):
        marker = markers.get((day, rung))
        if marker is None:
            return EXCLUSION_MARKER_UNREADABLE if objects.marker_path is not None else EXCLUSION_UNMARKED_PARTS
        if marker.completion is None:
            refusal = _absence_rung_refusal(objects)
            state = "governed_absence"
        else:
            refusal = _published_rung_refusal(objects, marker.completion)
            state = "published"
        if refusal is not None:
            return refusal
        states.add(state)
    if len(states) != 1:
        return EXCLUSION_MIXED_TERMINAL_STATES
    if states == {"governed_absence"} and not _one_absence_reason(ladder, markers, day=day):
        return EXCLUSION_MIXED_ABSENCE_REASONS
    return None


def _absence_rung_refusal(objects: RungObjects) -> str | None:
    """Refuse one rung's governed-absence claim, or admit it into the day's terminal-state tally."""
    if objects.part_paths:
        return EXCLUSION_MIXED_TERMINAL_STATES
    return None


def _published_rung_refusal(objects: RungObjects, completion: PartitionCompletion) -> str | None:
    """Refuse one rung's published claim, or admit it into the day's terminal-state tally."""
    if objects.completion_is_derived_empty != completion.derived_empty:
        # The key NAME is a claim and so is the body; `_verify_completion_object` refuses a row whose
        # marker makes them disagree, so the day is dropped here rather than at `--apply`.
        return EXCLUSION_MARKER_NAME_DISAGREES
    if completion.derived_empty:
        if objects.part_paths:
            return EXCLUSION_EMPTY_MARKER_HOLDS_PARTS
        if objects.rung == BASE_PARTITION_ZOOM:
            return EXCLUSION_EMPTY_MARKER_AT_BASE_RUNG
        return None
    if not objects.part_paths:
        return EXCLUSION_UNMARKED_PARTS
    return None


def _one_absence_reason(
    ladder: dict[date, dict[int, RungObjects]],
    markers: dict[tuple[date, int], MarkerRead],
    *,
    day: date,
) -> bool:
    """A generation refuses a day whose rungs disagree about WHY it is absent; find that out here."""
    reasons = {
        marker.absence.reason
        for rung in ladder[day]
        if (marker := markers.get((day, rung))) is not None and marker.absence is not None
    }
    return len(reasons) == 1


def _inventory_receipts(
    markers: dict[tuple[date, int], MarkerRead],
    *,
    days: Sequence[date],
) -> tuple[EvidenceReceipt, ...]:
    """Name every marker the compile trusted, which is exactly the inventory the identity digests.

    THIS IS THE MANIFEST THE TRUSTED ROWS REST ON. `verified_source_inventory_root` is computed over
    it, so a lane cannot be bootstrapped from one set of markers and audited against another. Callers
    pass the SURVIVING day set, never the merely-terminal one -- see MINOR 8 in `scripts/AGENTS.md`.
    """
    kept = set(days)
    receipts = {marker.relative_path: marker.receipt for (day, _rung), marker in markers.items() if day in kept}
    return tuple(receipts[key] for key in sorted(receipts))


def _bind_day(  # noqa: PLR0913 - one coordinate of the day being bound per argument
    reader: BucketReader,
    ladder: dict[date, dict[int, RungObjects]],
    markers: dict[tuple[date, int], MarkerRead],
    *,
    day: date,
    digest_floor: date,
    compilation: LaneCompilation,
) -> _BoundDay | None:
    """Collect one day's whole ladder as binding facts, or exclude the day and collect nothing.

    ALL FOUR RUNGS OR NONE. A generation refuses a day that does not carry the exact required-rungs
    ladder, so a rung that cannot be bound takes its day with it rather than leaving three rows that
    would fail validation later, further from the reason.

    NO IDENTITY HERE, on purpose: see `_BoundDay`. The cost counters on `compilation` are updated only
    once this whole day is kept, from a per-day `_DayCost` accumulator -- see MINOR 10.
    """
    day_markers = [markers[(day, rung)] for rung in sorted(ladder[day]) if (day, rung) in markers]
    source_receipts_by_key = {marker.relative_path: marker.receipt for marker in day_markers}
    source_receipts = tuple(source_receipts_by_key[key] for key in sorted(source_receipts_by_key))
    day_cost = _DayCost()
    bound_rungs: list[_BoundRung] = []
    for rung in sorted(ladder[day]):
        try:
            bound_rungs.append(
                _bind_rung(
                    reader,
                    objects=ladder[day][rung],
                    marker=markers[(day, rung)],
                    day=day,
                    digest_floor=digest_floor,
                    compilation=compilation,
                    day_cost=day_cost,
                )
            )
        except CompilationError as error:
            # The message IS the exclusion reason: every refusal below raises one of the named
            # constants, so the receipt reports the day and the reason without a second vocabulary.
            compilation.excluded_days.append((day, str(error)))
            return None
    compilation.hashed_part_count += day_cost.hashed_part_count
    compilation.hashed_part_bytes += day_cost.hashed_part_bytes
    compilation.marker_recorded_rung_days += day_cost.marker_recorded_rung_days
    compilation.digested_part_count += day_cost.digested_part_count
    compilation.digested_part_bytes += day_cost.digested_part_bytes
    return _BoundDay(day=day, source_receipts=source_receipts, rungs=tuple(bound_rungs))


def _bind_rung(  # noqa: PLR0913 - one coordinate of the rung being bound per argument
    reader: BucketReader,
    *,
    objects: RungObjects,
    marker: MarkerRead,
    day: date,
    digest_floor: date,
    compilation: LaneCompilation,
    day_cost: _DayCost,
) -> _BoundRung:
    """Bind ONE rung of one day, as a digested or a manifest-trusted claim."""
    if marker.absence is not None:
        published_at = marker.absence.recorded_at.astimezone(UTC)
        _require_not_future(published_at, compilation)
        return _BoundRung(
            rung=objects.rung,
            terminal_state="governed_absence",
            row_count=0,
            published_at=published_at,
            data_receipts=(),
            completion_receipt=None,
            absence_receipt=marker.receipt,
            absence_reason=marker.absence.reason,
            provenance=DIGESTED_PROVENANCE,
        )
    completion = marker.completion
    if completion is None:  # pragma: no cover - `_day_refusal` already removed this shape
        raise CompilationError(EXCLUSION_MARKER_UNREADABLE)
    published_at = completion.completed_at.astimezone(UTC)
    _require_not_future(published_at, compilation)
    if len(objects.part_paths) != completion.part_count:
        raise CompilationError(EXCLUSION_PART_COUNT_DISAGREES)
    if completion.parts and {part.relative_path for part in completion.parts} != set(objects.part_paths):
        raise CompilationError(EXCLUSION_RECORDED_PARTS_DISAGREE)
    data_receipts, provenance = _part_receipts(
        reader,
        objects=objects,
        completion=completion,
        day=day,
        digest_floor=digest_floor,
        day_cost=day_cost,
    )
    return _BoundRung(
        rung=objects.rung,
        terminal_state="published",
        row_count=completion.row_count,
        published_at=published_at,
        data_receipts=data_receipts,
        completion_receipt=marker.receipt,
        absence_receipt=None,
        absence_reason=None,
        provenance=provenance,
    )


def _part_receipts(  # noqa: PLR0913 - one coordinate of the rung being bound per argument
    reader: BucketReader,
    *,
    objects: RungObjects,
    completion: PartitionCompletion,
    day: date,
    digest_floor: date,
    day_cost: _DayCost,
) -> tuple[tuple[EvidenceReceipt, ...], AvailabilityProvenance]:
    """Return this rung's part receipts and the class they establish. THE DIGEST-WINDOW BOUNDARY.

    Three answers, ordered so the strongest claim available is also the cheapest one taken:

    1. No parts at all -- an emptied derived rung. It asserts nothing about parts, so it is
       `digested` and its marker proves the emptiness outright.
    2. A marker that RECORDED its parts, or a day inside the window -- real digests, so `digested`. A
       recorded digest was computed by the export from the bytes it uploaded, which is the same
       object the receipt describes: trusted-from-record, never fabricated. Its `byte_count` is
       already on the marker, so `day_cost.digested_part_bytes` counts it without a download -- this
       is what lets the receipt price `--apply`'s GETs for a day this compile never opened.
    3. Otherwise -- `manifest_trusted`: no part receipt at all, and the completion marker carries the
       whole of the claim. This is the branch owner decision D3 exists to permit.
    """
    if not objects.part_paths:
        return (), DIGESTED_PROVENANCE
    if completion.parts:
        day_cost.marker_recorded_rung_days += 1
        day_cost.digested_part_count += len(completion.parts)
        day_cost.digested_part_bytes += sum(part.byte_count for part in completion.parts)
        recorded = {part.relative_path: part.sha256 for part in completion.parts}
        return tuple(EvidenceReceipt(key=key, sha256=recorded[key]) for key in sorted(recorded)), DIGESTED_PROVENANCE
    if day < digest_floor:
        return (), MANIFEST_TRUSTED_PROVENANCE
    digests: dict[str, str] = {}
    observed_rows = 0
    for relative_path in objects.part_paths:
        payload = reader.read(relative_path)
        if payload is None:
            raise CompilationError(EXCLUSION_PART_MISSING)
        digests[relative_path] = sha256_digest(payload)
        observed_rows += int(pq.ParquetFile(io.BytesIO(payload)).metadata.num_rows)
        day_cost.hashed_part_count += 1
        day_cost.hashed_part_bytes += len(payload)
        day_cost.digested_part_count += 1
        day_cost.digested_part_bytes += len(payload)
    if observed_rows != completion.row_count:
        # `--apply` performs exactly this comparison for a digested row, so a day that would fail it
        # is excluded HERE, where the bytes are in hand and the reason can be named.
        raise CompilationError(EXCLUSION_PART_ROWS_DISAGREE)
    return tuple(EvidenceReceipt(key=key, sha256=digests[key]) for key in sorted(digests)), DIGESTED_PROVENANCE


def _require_not_future(moment: datetime, compilation: LaneCompilation) -> None:
    """A row may not be published after the generation that carries it was created."""
    if moment > compilation.created_at:
        raise CompilationError(EXCLUSION_MARKER_IN_FUTURE)


def _require_row_budget(layer: str, *, day_count: int) -> None:
    rows = day_count * len(AVAILABILITY_REQUIRED_RUNGS)
    if rows > MAX_AVAILABILITY_ROWS:
        raise CompilationError(
            f"{layer}: {day_count} terminal days at {len(AVAILABILITY_REQUIRED_RUNGS)} rungs is {rows} rows, above "
            f"the {MAX_AVAILABILITY_ROWS}-row generation cap; compile a bounded window with --since"
        )


def _require_refusal_budget(
    layer: str,
    *,
    ladder_day_count: int,
    excluded_days: list[tuple[date, str]],
    accept_exclusions: int | None,
) -> None:
    """Refuse the lane once REFUSED days -- everything but `--since`/source-ceiling filtering -- are a
    large enough share of what was considered that they look like a ladder problem, not sparse
    history (D2's warning that a looser bar silently shortens the time slider, arriving here through
    the bootstrap). `--accept-exclusions` names the exact count an operator has reviewed and accepts.

    Checked right after `_terminal_days`, before the expensive per-day binding walk: every reason this
    function sees is already known from listings and markers alone, so a lane with a real ladder
    problem is refused before it pays for a single part download.
    """
    filtered = sum(1 for _day, reason in excluded_days if reason in FILTERED_EXCLUSION_REASONS)
    refused = len(excluded_days) - filtered
    considered = ladder_day_count - filtered
    if considered <= 0 or refused == 0:
        return
    fraction = refused / considered
    if fraction <= REFUSED_DAY_FRACTION_CEILING:
        return
    if accept_exclusions is not None and refused <= accept_exclusions:
        return
    raise CompilationError(
        f"{layer}: {refused} refused day(s) out of {considered} considered ({fraction:.0%}) exceeds the "
        f"{REFUSED_DAY_FRACTION_CEILING:.0%} ladder-problem threshold; review excluded_days in a --dry-run "
        f"receipt, then pass --accept-exclusions {refused} to compile anyway"
    )


def _bootstrap_document(compilation: LaneCompilation) -> dict[str, object]:
    """Render the exact `availability-bootstrap-input-v1` document, rows in their canonical spelling."""
    identity = compilation.identity
    if identity is None:  # pragma: no cover - a compilation without an identity never reaches here
        raise CompilationError("cannot render a document for a lane that produced no identity")
    return {
        "created_at": _format_instant(compilation.created_at),
        "input_receipts": [receipt.to_wire() for receipt in compilation.input_receipts],
        "lane": identity.lane,
        "lane_root": identity.lane_root,
        "nature": identity.nature,
        "product": identity.product,
        "required_rungs": list(identity.required_rungs),
        # EVERY ROW DECLARES ITS CLASS IN WORDS, including the ordinary one. This document is
        # compiled once and then read by people; `_row_from_mapping` checks the declaration against
        # the row's shape and refuses a mismatch, so spelling it out hides nothing and costs nothing.
        "rows": [{**row.to_wire(), PROVENANCE_FIELD: row.provenance} for row in compilation.rows],
        "schema_version": BOOTSTRAP_INPUT_SCHEMA_VERSION,
        "source_ceiling": compilation.source_ceiling.isoformat(),
        "verified_source_inventory_root": identity.verified_source_inventory_root,
    }


def _format_instant(moment: datetime) -> str:
    """Spell one instant the way the availability contract's own parser demands."""
    rendered = moment.astimezone(UTC).isoformat(timespec="microseconds")
    return f"{rendered[:-6]}Z"


def _write_lane_output(compilation: LaneCompilation, *, lane_directory: Path) -> tuple[Path, str]:
    """Write the document and every evidence object it refers to; return the path and the digest."""
    lane_directory.mkdir(parents=True, exist_ok=True)
    payload = _json_bytes(_bootstrap_document(compilation))
    input_path = lane_directory / BOOTSTRAP_INPUT_FILE_NAME
    # BYTES, NEVER TEXT: a text write on Windows rewrites every newline and the digest stops matching
    # the file the operator passes to `--input-sha256`.
    input_path.write_bytes(payload)
    evidence_root = lane_directory / EVIDENCE_DIRECTORY_NAME
    for artifact in compilation.artifacts:
        destination = evidence_root / artifact.key
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(artifact.payload)
    return input_path, sha256_digest(payload)


def _json_bytes(value: object) -> bytes:
    return canonical_json(value).encode("utf-8")


def _receipt(
    compilation: LaneCompilation,
    *,
    input_sha256: str | None,
    input_path: Path | None,
    prefix: str,
) -> dict[str, object]:
    """Render what this lane's compile proved, what it cost, and what it left out."""
    days = sorted({row.day for row in compilation.rows})
    exclusions = Counter(reason for _day, reason in compilation.excluded_days)
    filtered_day_count = sum(count for reason, count in exclusions.items() if reason in FILTERED_EXCLUSION_REASONS)
    return {
        "apply_command": _apply_command(compilation, input_path=input_path, input_sha256=input_sha256),
        "apply_projected_cost": _apply_projected_cost(compilation),
        "created_at": _format_instant(compilation.created_at),
        "earliest_day": days[0].isoformat() if days else None,
        "evidence_objects_owed": len(compilation.artifacts),
        "evidence_upload_command": _upload_command(input_path=input_path, prefix=prefix),
        "excluded_day_count": len(compilation.excluded_days),
        "excluded_days": [
            {"day": day.isoformat(), "reason": reason} for day, reason in sorted(compilation.excluded_days)
        ],
        "excluded_days_by_reason": dict(sorted(exclusions.items())),
        "filtered_day_count": filtered_day_count,
        "refused_day_count": len(compilation.excluded_days) - filtered_day_count,
        "hashed_part_bytes": compilation.hashed_part_bytes,
        "hashed_part_count": compilation.hashed_part_count,
        "input_sha256": input_sha256,
        "lane": compilation.lane.layer,
        "lane_root": compilation.lane_root,
        "latest_day": days[-1].isoformat() if days else None,
        "marker_objects_read": compilation.marker_read_count,
        "marker_recorded_rung_days": compilation.marker_recorded_rung_days,
        "provenance": availability_provenance_summary(compilation.rows),
        "row_count": len(compilation.rows),
        "selectable_day_count": len(days),
        "source_ceiling": compilation.source_ceiling.isoformat(),
    }


def _apply_projected_cost(compilation: LaneCompilation) -> dict[str, object]:
    """Price `--apply`'s own GETs, which this receipt otherwise leaves unpriced (MAJOR 3).

    Every DIGESTED part -- whether THIS compile downloaded it inside the digest window, or read its
    size and digest straight off a marker that already recorded them -- is downloaded once by
    `availability_index.py::_verify_terminal_physical_objects` to verify it and once more by
    `_revalidate_snapshots` before the pointer swap. Both figures below are a MINIMUM for one
    successful attempt: `_revalidate_snapshots` sits inside the publication retry loop, so a contended
    pointer replays it again on each of up to `MAX_PUBLICATION_ATTEMPTS` attempts.
    """
    return {
        "get_bytes_minimum": compilation.digested_part_bytes * 2,
        "get_count_minimum": compilation.digested_part_count * 2,
        "note": (
            "one GET per digested part to verify plus one more to revalidate before the pointer swap; "
            f"a contended pointer replays the revalidation GET again on each retry, up to "
            f"{MAX_PUBLICATION_ATTEMPTS} attempts total"
        ),
    }


def _apply_command(
    compilation: LaneCompilation,
    *,
    input_path: Path | None,
    input_sha256: str | None,
) -> str | None:
    """Render the operator's next verb. PRINTED, NEVER RUN: `--apply` is an owner-confirmed action."""
    if input_path is None or input_sha256 is None:
        return None
    return (
        f"agri data availability-bootstrap --input {input_path} --input-sha256 {input_sha256} "
        f"--expected-row-count {len(compilation.rows)} --apply"
    )


def _upload_command(*, input_path: Path | None, prefix: str) -> str | None:
    """Render the evidence upload `--apply` needs, since it verifies these objects out of the bucket."""
    if input_path is None:
        return None
    evidence_root = input_path.parent / EVIDENCE_DIRECTORY_NAME
    return f"aws s3 cp --recursive {evidence_root} s3://$OBJECT_STORE_BUCKET/{prefix}"


def _resolve_lanes(arguments: argparse.Namespace) -> tuple[CensusLane, ...]:
    """Return the lanes to compile, refusing a lane with no time axis to own an index."""
    time_bearing = tuple(lane for lane in registered_census_lanes() if nature_has_time_axis(lane.nature))
    if arguments.all_time_bearing:
        return time_bearing
    by_layer = {lane.layer: lane for lane in time_bearing}
    unknown = [slug for slug in arguments.lane if slug not in by_layer]
    if unknown:
        known = ", ".join(sorted(by_layer))
        raise SystemExit(f"unknown or non-time-bearing lane(s): {', '.join(unknown)}; time-bearing lanes are: {known}")
    return tuple(by_layer[slug] for slug in arguments.lane)


def _parse_arguments(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--lane", action="append", default=[], help="Lane slug to compile; repeatable.")
    parser.add_argument(
        "--all-time-bearing",
        action="store_true",
        help="Compile every registered lane whose partition day is a time the source itself stamped.",
    )
    parser.add_argument(
        "--digest-window-days",
        type=int,
        default=DEFAULT_DIGEST_WINDOW_DAYS,
        help=(
            "Days back from today whose parts are downloaded and hashed. Older days are bound as "
            "manifest-trusted rows unless their completion marker recorded its own part digests "
            f"(default {DEFAULT_DIGEST_WINDOW_DAYS})."
        ),
    )
    parser.add_argument("--since", type=date.fromisoformat, default=None, help="Ignore days before this date.")
    parser.add_argument(
        "--source-ceiling",
        type=date.fromisoformat,
        default=None,
        help="Override the lane's computed source ceiling. Days after it are excluded.",
    )
    parser.add_argument("--kind", default="observed", choices=("observed", "forecast"), help="Stream kind.")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help=f"Output directory (default {DEFAULT_OUT}).")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS, help="Parallel marker reads.")
    parser.add_argument(
        "--accept-exclusions",
        type=int,
        default=None,
        help=(
            "The number of REFUSED days (a ladder problem, not --since/source-ceiling filtering) this "
            f"operator has reviewed and accepts. Required once refused days exceed "
            f"{REFUSED_DAY_FRACTION_CEILING:.0%} of the days considered."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Walk, read markers, report the plan. Hashes no part and writes no file.",
    )
    arguments = parser.parse_args(argv)
    if not arguments.lane and not arguments.all_time_bearing:
        parser.error("pass --lane at least once, or --all-time-bearing")
    if arguments.digest_window_days < 0:
        parser.error("--digest-window-days cannot be negative")
    if arguments.workers < 1:
        parser.error("--workers must be at least 1")
    if arguments.accept_exclusions is not None and arguments.accept_exclusions < 0:
        parser.error("--accept-exclusions cannot be negative")
    if arguments.dry_run:
        # A dry run must not download parts: its purpose is to price the compile before paying for
        # it. A zero-day window puts every day on the trusted side, which hashes nothing.
        arguments.digest_window_days = 0
    return arguments


if __name__ == "__main__":
    raise SystemExit(main())

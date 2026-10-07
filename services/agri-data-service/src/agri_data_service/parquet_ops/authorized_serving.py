"""Availability-authorized row serving for every time-bearing lane.

See `AGENTS.md`, "Availability-authorized serving".
"""

from __future__ import annotations

import threading
import time
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor, wait
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Final, Literal, cast

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import structlog
from botocore.config import Config  # type: ignore[import-untyped]

from agri_data_service.foundation.canonical import sha256_digest
from agri_data_service.foundation.parquet.completion import PartitionCompletion, PartitionCompletionError
from agri_data_service.foundation.parquet.paths import (
    absence_marker_path,
    completion_marker_path,
    try_parse_absence_marker_path,
    try_parse_completion_marker_path,
    try_parse_partition_path,
)
from agri_data_service.parquet_ops import faults
from agri_data_service.parquet_ops.availability_coverage import (
    GenerationCachingStorage,
    required_source_ceiling,
)
from agri_data_service.parquet_ops.coverage import (
    DEDICATED_SLIDER_PRODUCT_LAYERS,
    census_lane_from_registration,
)
from agri_data_service.parquet_ops.serving import resolve_day, resolve_release, resolve_window
from agri_data_service.pipeline.parquet.availability_index import (
    EVIDENCE_OBJECT_MAX_BYTES,
    MANIFEST_TRUSTED_PROVENANCE,
    AvailabilityChecksumError,
    AvailabilityError,
    AvailabilityMalformedError,
    AvailabilityUnavailableError,
    BotoAvailabilityStorage,
    read_availability_pointer,
    read_latest_availability,
    read_terminal_evidence,
)
from agri_data_service.pipeline.parquet.lane_registry import LANE_REGISTRY
from agri_data_service.pipeline.parquet.objectstore import availability_lane_root
from agri_data_service.warehouse.schemas.availability_index import AVAILABILITY_REQUIRED_RUNGS

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence

    from agri_data_service.config import Settings
    from agri_data_service.foundation.parquet.paths import PartitionKind
    from agri_data_service.foundation.parquet.zoom import ZoomTier
    from agri_data_service.parquet_ops.duckdb_session import ServingSession
    from agri_data_service.parquet_ops.mtbs_snapshot_catalog import VerifiedMtbsSnapshot
    from agri_data_service.parquet_ops.request_params import ReadScope
    from agri_data_service.parquet_ops.warehouse_reader import (
        PartitionRowReader,
        RowRead,
        RowReadResult,
        WarehouseListing,
    )
    from agri_data_service.parquet_ops.wire import DayEnvelope
    from agri_data_service.pipeline.parquet.availability_index import (
        AvailabilityIndex,
        AvailabilityNature,
        AvailabilityRow,
        AvailabilityStorage,
        EvidenceReceipt,
    )

logger = structlog.get_logger()

#: How strongly one published day's part bytes are proven; see AGENTS.md, "Trust levels".
type TrustLevel = Literal["hash_verified", "manifest_trusted"]

_LANES: Final = {slug: census_lane_from_registration(registration) for slug, registration in LANE_REGISTRY.items()}
_UNREGISTERED_PRODUCTS: Final = set(DEDICATED_SLIDER_PRODUCT_LAYERS) - _LANES.keys()
if _UNREGISTERED_PRODUCTS:
    raise ValueError("serving products lack a registered history floor: " + ", ".join(sorted(_UNREGISTERED_PRODUCTS)))

# One part is bounded independently, then the request is bounded across every part it selected.
# These are serving-resource guards, not publication limits: an oversized authorized object is
# refused rather than downloaded without a ceiling merely because its receipt is otherwise valid.
MAX_VERIFIED_PART_BYTES: Final = 64 * 1024 * 1024
MAX_VERIFIED_READ_BYTES: Final = 512 * 1024 * 1024
MAX_VERIFIED_MARKER_BYTES: Final = 1024 * 1024
MAX_VERIFIED_MARKERS_BYTES: Final = 8 * 1024 * 1024
MAX_VERIFIED_AVAILABILITY_INDEXES: Final = 16
MAX_CACHED_INDEX_GENERATION_BYTES: Final = 8 * 1024 * 1024
MAX_CACHED_INDEX_BYTES: Final = 16 * 1024 * 1024
MAX_CACHED_INDEX_ROWS: Final = 100_000

# One process-wide pool bounds concurrent receipt GETs across every overlapping request (boto's
# default connection pool is 10). Each request keeps at most a window of fetches outstanding, kept
# below the pool size so one request stalled on R2 never holds every worker; see AGENTS.md.
_RECEIPT_FETCH_WORKERS: Final = 8
_RECEIPT_FETCH_WINDOW: Final = 4
# Above every caller's own deadline (12 s agent tool, 14 s row route): it only frees a serving slot.
_RECEIPT_STAGING_DEADLINE_SECONDS: Final = 20.0
_RECEIPT_FETCH_POOL: Final = ThreadPoolExecutor(
    max_workers=_RECEIPT_FETCH_WORKERS,
    thread_name_prefix="plantgeo-receipt-fetch",
)
# The serving client's own bounds, so a fully stalled GET frees its worker inside the staging deadline:
# 2 attempts x (3 s connect + 6 s to first byte) = 18 s. botocore counts `max_attempts` as RETRIES, so the
# total is spelled `total_max_attempts`. Ingestion keeps botocore defaults; see AGENTS.md.
SERVING_CLIENT_CONFIG: Final = Config(
    connect_timeout=3,
    read_timeout=6,
    retries={"mode": "standard", "total_max_attempts": 2},
)


class _AbandonedFetches:
    """Process-wide count of fetches a request gave up on that still hold a pool worker."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._count = 0

    @property
    def count(self) -> int:
        with self._lock:
            return self._count

    def adopt(self, stragglers: Iterable[Future[int]]) -> None:
        """Count each straggler until it finishes; one already done is released at once."""
        for straggler in stragglers:
            with self._lock:
                self._count += 1
            straggler.add_done_callback(self._release)

    def _release(self, _straggler: Future[int]) -> None:
        with self._lock:
            self._count -= 1


_ABANDONED_FETCHES: Final = _AbandonedFetches()


class _StagedWrites:
    """One request's staged-file writes and kept control payloads, refused once the request abandons them."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._abandoned = False
        self.retained: dict[str, bytes] = {}

    def write(self, target: Path, payload: bytes) -> None:
        with self._lock:
            if not self._abandoned:
                target.write_bytes(payload)

    def retain(self, key: str, payload: bytes) -> None:
        with self._lock:
            if not self._abandoned:
                self.retained[key] = payload

    def abandon(self) -> None:
        with self._lock:
            self._abandoned = True


@dataclass(frozen=True, slots=True)
class _ManifestTrustedPart:
    """One part of a manifest-trusted day: named by the physical listing, bound by its marker's counts."""

    key: str
    day: date


#: What one staging fan-out fetches: a digest-bound receipt, or a trusted part with no digest to match.
type _StagedObject = EvidenceReceipt | _ManifestTrustedPart


@dataclass(frozen=True, slots=True)
class _TrustedDay:
    """A published row whose only proof is its completion marker (owner decision D3)."""

    row: AvailabilityRow
    marker: EvidenceReceipt


@dataclass(frozen=True, slots=True)
class _SelectedRead:
    """One row read's objects, resolved against this generation in input order."""

    parts: tuple[_StagedObject, ...]
    completions: tuple[EvidenceReceipt, ...]
    trusted_parts: dict[date, tuple[str, ...]]
    hash_verified_days: frozenset[date]


@dataclass(slots=True)
class _TrustedRowTally:
    """Sums each manifest-trusted day's staged Parquet rows; refuses a day whose total differs from its marker."""

    layer: str
    expected_rows: dict[date, int]
    unread_parts: dict[date, int]
    counted_rows: dict[date, int] = field(default_factory=dict)

    def admit(self, part: _ManifestTrustedPart, staged: Path) -> None:
        """Count one admitted part; on a day's last part, its rows must equal what its marker claims."""
        self.counted_rows[part.day] = self.counted_rows.get(part.day, 0) + _parquet_row_count(
            staged, layer=self.layer, key=part.key
        )
        self.unread_parts[part.day] -= 1
        if self.unread_parts[part.day] == 0 and self.counted_rows[part.day] != self.expected_rows[part.day]:
            raise _trusted_day_refusal(
                self.layer,
                part.day,
                f"its parts hold {self.counted_rows[part.day]} row(s) while its completion marker counts "
                f"{self.expected_rows[part.day]}",
            )


@dataclass(slots=True)
class AvailabilityAuthorizedListing:
    """A listing-shaped view containing only objects named by one verified generation."""

    index: AvailabilityIndex
    scope: ReadScope
    store: AvailabilityStorage
    physical: WarehouseListing
    _receipts: dict[str, EvidenceReceipt] = field(init=False, repr=False)
    _keys_by_day: dict[date, tuple[str, ...]] = field(init=False, repr=False)
    _rows_by_day: dict[date, AvailabilityRow] = field(init=False, repr=False)
    _absence_receipts: dict[date, EvidenceReceipt] = field(init=False, repr=False)
    _trusted: dict[date, _TrustedDay] = field(init=False, repr=False)
    _trusted_keys_by_day: dict[date, tuple[str, ...]] = field(init=False, repr=False)
    _trusted_part_days: dict[str, date] = field(init=False, repr=False)
    _physical_years: set[int] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._receipts = {}
        self._keys_by_day = {}
        self._rows_by_day = {}
        self._absence_receipts = {}
        self._trusted = {}
        self._trusted_keys_by_day = {}
        self._trusted_part_days = {}
        self._physical_years = set()
        for row in self.index.rows:
            if row.rung != self.scope.tier:
                continue
            self._rows_by_day[row.day] = row
            receipts = list(row.data_receipts)
            if row.completion_receipt is not None:
                receipts.append(row.completion_receipt)
            for receipt in receipts:
                self._receipts[receipt.key] = receipt
            keys = tuple(receipt.key for receipt in receipts)
            if row.terminal_state == "governed_absence" and not keys:
                keys = (absence_marker_path(self.scope.layer, self.scope.kind, self.scope.tier, row.day),)
            self._keys_by_day[row.day] = keys
            trusted_marker = self._manifest_trusted_marker(row)
            if trusted_marker is not None:
                self._trusted[row.day] = _TrustedDay(row=row, marker=trusted_marker)

    def _manifest_trusted_marker(self, row: AvailabilityRow) -> EvidenceReceipt | None:
        """Return the ordinary completion receipt of a manifest-trusted row, else `None`; see AGENTS.md."""
        if row.terminal_state != "published" or row.provenance != MANIFEST_TRUSTED_PROVENANCE:
            return None
        marker = row.completion_receipt
        expected = completion_marker_path(self.scope.layer, self.scope.kind, self.scope.tier, row.day)
        return marker if marker is not None and marker.key == expected else None

    def trust_level(self, day: date) -> TrustLevel | None:
        """How strongly a published day's part bytes are proven at this rung; `None` when it is not published."""
        row = self._rows_by_day.get(day)
        if row is None or row.terminal_state != "published":
            return None
        return "manifest_trusted" if day in self._trusted else "hash_verified"

    def list_keys(
        self,
        layer: str,
        kind: PartitionKind,
        tier: ZoomTier,
        *,
        year: int | None = None,
        month: int | None = None,
    ) -> tuple[str, ...]:
        """Return only generation-bound objects matching the requested physical coordinates."""
        if (layer, kind, tier) != (self.scope.layer, self.scope.kind, self.scope.tier):
            return ()
        self._load_trusted_parts(year=year, month=month)
        return tuple(
            key
            for day in sorted(self._rows_by_day)
            if (year is None or day.year == year) and (month is None or day.month == month)
            for key in self._day_keys(day)
        )

    def _day_keys(self, day: date) -> tuple[str, ...]:
        """Return generation-derived layout names; a trusted day's parts come from its year's one physical LIST."""
        if day not in self._trusted:
            return self._keys_by_day[day]
        self._load_trusted_parts(year=day.year, month=day.month)
        return self._trusted_keys_by_day[day]

    def _load_trusted_parts(self, *, year: int | None, month: int | None) -> None:
        """Bind the whole year of every trusted day in scope: ONE physical LIST per year prefix, never the tier.

        A scope holding no trusted day never LISTs, and a bound year is never listed again, so a day's
        part names cannot change between two resolves on this listing; see AGENTS.md, "Trust levels".
        """
        wanted = {
            day.year
            for day in self._trusted
            if (year is None or day.year == year) and (month is None or day.month == month)
        } - self._physical_years
        for wanted_year in sorted(wanted):
            keys = self.physical.list_keys(self.scope.layer, self.scope.kind, self.scope.tier, year=wanted_year)
            self._bind_trusted_parts(keys, year=wanted_year)

    def _bind_trusted_parts(self, keys: Iterable[str], *, year: int) -> None:
        """Give each trusted day of `year` its physical part names; an anomalous day gets only its marker."""
        coordinates = (self.scope.layer, self.scope.kind, self.scope.tier)
        parts: dict[date, list[str]] = {}
        anomalous: set[date] = set()
        for key in keys:
            part = try_parse_partition_path(key)
            if part is not None and (part.layer, part.kind, part.zoom) == coordinates:
                parts.setdefault(part.day, []).append(key)
                continue
            absence = try_parse_absence_marker_path(key)
            if absence is not None and (absence.layer, absence.kind, absence.zoom) == coordinates:
                anomalous.add(absence.day)
                continue
            finished = try_parse_completion_marker_path(key)
            if (
                finished is not None
                and finished.derived_empty
                and (finished.layer, finished.kind, finished.zoom) == coordinates
            ):
                anomalous.add(finished.day)
        for day, trusted in self._trusted.items():
            if day.year != year:
                continue
            day_parts = tuple(sorted(parts.get(day, ())))
            if day in anomalous or not day_parts:
                # The marker alone classifies `incomplete`, so the resolver refuses the day out loud.
                self._trusted_keys_by_day[day] = (trusted.marker.key,)
                continue
            self._trusted_keys_by_day[day] = (*day_parts, trusted.marker.key)
            for key in day_parts:
                self._trusted_part_days[key] = day
        self._physical_years.add(year)

    def _absence_receipt(self, day: date) -> EvidenceReceipt:
        """Verify and cache the selected absence receipt, never an unrelated day's evidence."""
        cached = self._absence_receipts.get(day)
        if cached is not None:
            return cached
        row = self._rows_by_day[day]
        try:
            terminal = read_terminal_evidence(self.store, row.terminal_receipt, identity=self.index.pointer.identity)
        except AvailabilityError as exc:
            raise _availability_refusal(self.scope.layer, exc) from exc
        if terminal.absence_receipt is None:
            raise faults.availability_malformed(
                layer=self.scope.layer,
                detail=f"{row.day.isoformat()} z{row.rung} has no absence receipt",
            )
        expected_key = absence_marker_path(self.scope.layer, self.scope.kind, self.scope.tier, day)
        if terminal.absence_receipt.key != expected_key:
            raise faults.availability_malformed(
                layer=self.scope.layer,
                detail=f"{day.isoformat()} z{row.rung} absence receipt names an unexpected object",
            )
        self._absence_receipts[day] = terminal.absence_receipt
        self._receipts[expected_key] = terminal.absence_receipt
        return terminal.absence_receipt

    def iter_tier_keys(self, layer: str, kind: PartitionKind, tier: ZoomTier) -> Iterator[str]:
        """Yield each day's keys lazily; the first key is always index-named, so an existence probe never LISTs."""
        if (layer, kind, tier) != (self.scope.layer, self.scope.kind, self.scope.tier):
            return
        for day in sorted(self._keys_by_day):
            trusted = self._trusted.get(day)
            if trusted is None:
                yield from self._keys_by_day[day]
                continue
            # The marker is known from the index; only a caller that resumes past it pays the year's LIST.
            yield trusted.marker.key
            yield from (key for key in self._day_keys(day) if key != trusted.marker.key)

    def iter_stream_keys(self, layer: str, kind: PartitionKind) -> Iterator[str]:
        if (layer, kind) == (self.scope.layer, self.scope.kind):
            yield from self.list_keys(layer, kind, self.scope.tier)

    def read_object(self, relative_key: str) -> bytes | None:
        """Read and re-check an authorized non-Parquet object by its recorded digest."""
        receipt = self._receipts.get(relative_key)
        if receipt is None:
            parsed = try_parse_absence_marker_path(relative_key)
            if (
                parsed is not None
                and (parsed.layer, parsed.kind, parsed.zoom) == (self.scope.layer, self.scope.kind, self.scope.tier)
                and parsed.day in self._rows_by_day
                and self._rows_by_day[parsed.day].terminal_state == "governed_absence"
            ):
                receipt = self._absence_receipt(parsed.day)
        if receipt is None:
            return None
        try:
            stored = self.store.read(relative_key, max_bytes=EVIDENCE_OBJECT_MAX_BYTES)
        except AvailabilityError as exc:
            raise _availability_refusal(self.scope.layer, exc) from exc
        if stored is None:
            raise faults.availability_stale(
                layer=self.scope.layer,
                detail=f"authorized object {relative_key!r} is missing",
            )
        if sha256_digest(stored.payload) != receipt.sha256:
            raise faults.availability_checksum_invalid(
                layer=self.scope.layer,
                detail=f"authorized object {relative_key!r} differs from its receipt",
            )
        return stored.payload

    @contextmanager
    def verified_object_uris(
        self,
        keys: Sequence[str],
    ) -> Iterator[dict[str, str]]:
        """Point DuckDB at the exact receipt-bound bytes selected by this generation."""
        selected = self._selected_read_receipts(keys)
        parts = selected.parts
        deadline = time.monotonic() + _RECEIPT_STAGING_DEADLINE_SECONDS
        # Completion is the publication commit point. Verify it before admitting any part bytes;
        # a concurrent rewrite clears it before replacing part-0 and therefore fails closed here.
        markers = self._verify_receipts(
            selected.completions,
            max_bytes=MAX_VERIFIED_MARKER_BYTES,
            aggregate_bytes=MAX_VERIFIED_MARKERS_BYTES,
            deadline=deadline,
        )
        tally = _TrustedRowTally(
            layer=self.scope.layer,
            expected_rows=self._trusted_day_claims(selected.trusted_parts, markers),
            unread_parts={day: len(day_keys) for day, day_keys in selected.trusted_parts.items()},
        )
        with TemporaryDirectory(prefix="plantgeo-serving-") as directory:
            root = Path(directory)
            targets = tuple(root / f"part-{index:05d}.parquet" for index in range(len(parts)))
            exact_sources: dict[str, str] = {}
            total_bytes = 0

            def admit(index: int, staged_bytes: int) -> None:
                nonlocal total_bytes
                total_bytes += staged_bytes
                if total_bytes > MAX_VERIFIED_READ_BYTES:
                    raise faults.ServingRefusalError(
                        "read_over_budget",
                        f"Receipt-bound read exceeded its {MAX_VERIFIED_READ_BYTES}-byte ceiling",
                    )
                part = parts[index]
                if isinstance(part, _ManifestTrustedPart):
                    tally.admit(part, targets[index])
                exact_sources[part.key] = targets[index].as_posix()

            self._stage_in_order(
                parts,
                targets,
                max_bytes=MAX_VERIFIED_PART_BYTES,
                deadline=deadline,
                admit=admit,
            )
            self._log_trust(selected)
            yield exact_sources

    @contextmanager
    def verified_session(
        self,
        session: ServingSession,
        keys: Sequence[str],
    ) -> Iterator[ServingSession]:
        """Install receipt-bound local sources on an agent query's serving session."""
        with self.verified_object_uris(keys) as exact_sources:
            yield replace(session, object_uris=exact_sources)

    def _selected_read_receipts(self, keys: Sequence[str]) -> _SelectedRead:
        """Resolve selected parts and their day commit markers from this exact generation."""
        parts: list[_StagedObject] = []
        completions: dict[str, EvidenceReceipt] = {}
        trusted_parts: dict[date, list[str]] = {}
        hash_verified_days: set[date] = set()
        for key in keys:
            trusted_day = self._trusted_part_days.get(key)
            if trusted_day is not None:
                marker = self._trusted[trusted_day].marker
                parts.append(_ManifestTrustedPart(key=key, day=trusted_day))
                trusted_parts.setdefault(trusted_day, []).append(key)
                completions[marker.key] = marker
                continue
            receipt = self._receipts.get(key)
            parsed = try_parse_partition_path(key)
            if receipt is None or parsed is None:
                raise faults.availability_unpublished(
                    layer=self.scope.layer,
                    detail=f"row read selected object {key!r} outside the current generation",
                )
            row = self._rows_by_day.get(parsed.day)
            if row is None or row.terminal_state != "published" or receipt not in row.data_receipts:
                raise faults.availability_malformed(
                    layer=self.scope.layer,
                    detail=f"row read selected object {key!r} without a published-day receipt",
                )
            completion = row.completion_receipt
            if completion is None:
                raise faults.availability_malformed(
                    layer=self.scope.layer,
                    detail=f"{parsed.day.isoformat()} z{row.rung} has no completion receipt",
                )
            parts.append(receipt)
            completions[completion.key] = completion
            hash_verified_days.add(parsed.day)
        return _SelectedRead(
            parts=tuple(parts),
            completions=tuple(completions[key] for key in sorted(completions)),
            trusted_parts={day: tuple(day_keys) for day, day_keys in trusted_parts.items()},
            hash_verified_days=frozenset(hash_verified_days),
        )

    def _trusted_day_claims(
        self,
        trusted_parts: Mapping[date, tuple[str, ...]],
        markers: Mapping[str, bytes],
    ) -> dict[date, int]:
        """Bind each trusted day's selected parts to its digest-verified marker; return the rows each must hold."""
        expected_rows: dict[date, int] = {}
        for day in sorted(trusted_parts):
            trusted = self._trusted[day]
            try:
                completion = PartitionCompletion.from_json_bytes(markers[trusted.marker.key])
            except PartitionCompletionError as exc:
                raise faults.availability_malformed(
                    layer=self.scope.layer,
                    detail=f"{day.isoformat()} z{self.scope.tier} completion marker is undecodable: {exc}",
                ) from exc
            if completion.derived_empty:
                raise _trusted_day_refusal(self.scope.layer, day, "its completion marker claims a derived-empty rung")
            if completion.row_count != trusted.row.row_count:
                raise _trusted_day_refusal(
                    self.scope.layer,
                    day,
                    f"its completion marker counts {completion.row_count} row(s) while the availability index "
                    f"counts {trusted.row.row_count}",
                )
            # Exactly part-0 .. part-(N-1): a missing, surplus or repeated part index all refuse here.
            indexes = sorted(_part_index(key) for key in trusted_parts[day])
            if indexes != list(range(completion.part_count)):
                raise _trusted_day_refusal(
                    self.scope.layer,
                    day,
                    f"{len(indexes)} part(s) are listed while its completion marker counts {completion.part_count}",
                )
            expected_rows[day] = completion.row_count
        return expected_rows

    def _log_trust(self, selected: _SelectedRead) -> None:
        """Record that a read admitted manifest-trusted days; silence means every day was hash-verified."""
        if not selected.trusted_parts:
            return
        trusted_days = sorted(selected.trusted_parts)
        logger.info(
            "authorized_read_trust",
            layer=self.scope.layer,
            tier=self.scope.tier,
            trust_level="manifest_trusted",
            manifest_trusted_days=len(trusted_days),
            hash_verified_days=len(selected.hash_verified_days),
            first_manifest_trusted_day=trusted_days[0].isoformat(),
            last_manifest_trusted_day=trusted_days[-1].isoformat(),
        )

    def _verify_receipts(
        self,
        receipts: Sequence[EvidenceReceipt],
        *,
        max_bytes: int,
        aggregate_bytes: int,
        deadline: float,
    ) -> dict[str, bytes]:
        """Verify small control objects in input order against one aggregate ceiling; return them by key."""
        consumed = 0

        def charge(_index: int, staged_bytes: int) -> None:
            nonlocal consumed
            consumed += staged_bytes
            if consumed > aggregate_bytes:
                raise faults.ServingRefusalError(
                    "read_over_budget",
                    f"Receipt-bound control objects exceeded their {aggregate_bytes}-byte ceiling",
                )

        return self._stage_in_order(
            receipts, (None,) * len(receipts), max_bytes=max_bytes, deadline=deadline, admit=charge
        )

    def _stage_in_order(
        self,
        receipts: Sequence[_StagedObject],
        targets: Sequence[Path | None],
        *,
        max_bytes: int,
        deadline: float,
        admit: Callable[[int, int], None],
    ) -> dict[str, bytes]:
        """Sliding-window fan-out on the shared pool, admitted strictly in input order; see AGENTS.md.

        Returns the verified payload of every object staged without a target file (control objects).
        """
        if receipts and _ABANDONED_FETCHES.count >= _RECEIPT_FETCH_WINDOW:
            # Abandoned stragglers already hold a window's worth of workers: refuse now, not queue behind them.
            raise faults.serving_at_capacity(operation="receipt verification", concurrent_reads=_RECEIPT_FETCH_WORKERS)
        window: deque[tuple[int, Future[int]]] = deque()
        upcoming = iter(range(len(receipts)))
        writes = _StagedWrites()

        def submit_next() -> None:
            index = next(upcoming, None)
            if index is not None:
                future = _RECEIPT_FETCH_POOL.submit(self._stage, receipts[index], targets[index], max_bytes, writes)
                window.append((index, future))

        try:
            for _ in range(_RECEIPT_FETCH_WINDOW):
                submit_next()
            while window:
                index, future = window[0]
                if not wait([future], timeout=max(0.0, deadline - time.monotonic())).done:
                    raise faults.read_timed_out(
                        operation="receipt verification",
                        timeout_seconds=_RECEIPT_STAGING_DEADLINE_SECONDS,
                    )
                window.popleft()
                # `result()` re-raises this receipt's own refusal, in input order. Admit before refilling
                # the window, so a budget breach queues no further GET.
                admit(index, future.result())
                submit_next()
        finally:
            # Refuse late writes first, then cancel queued fetches and count running ones as abandoned,
            # never waiting on them: the gate alone keeps a straggler out of the removed tree.
            writes.abandon()
            _ABANDONED_FETCHES.adopt(pending for _index, pending in window if not pending.cancel())
        # Only reached once every fetch was admitted, so no worker can still be writing into it.
        return writes.retained

    def _stage(
        self,
        receipt: _StagedObject,
        target: Path | None,
        max_bytes: int,
        writes: _StagedWrites,
    ) -> int:
        """Fetch and verify one object on a pool thread, write or retain it, and return its size."""
        payload = self._verified_payload(receipt, max_bytes=max_bytes)
        if target is None:
            writes.retain(receipt.key, payload)
        else:
            writes.write(target, payload)
        return len(payload)

    def _verified_payload(self, receipt: _StagedObject, *, max_bytes: int) -> bytes:
        """Read one bounded object; return it when its digest matches, or when it is a trusted part with none."""
        try:
            stored = self.store.read(receipt.key, max_bytes=max_bytes)
        except AvailabilityError as exc:
            raise _availability_refusal(self.scope.layer, exc) from exc
        if stored is None:
            raise faults.availability_stale(
                layer=self.scope.layer,
                detail=f"authorized object {receipt.key!r} is missing",
            )
        # A trusted part has no digest; `_TrustedRowTally` binds it to its day's verified marker instead.
        if not isinstance(receipt, _ManifestTrustedPart) and sha256_digest(stored.payload) != receipt.sha256:
            raise faults.availability_checksum_invalid(
                layer=self.scope.layer,
                detail=f"authorized object {receipt.key!r} differs from its receipt",
            )
        return stored.payload

    def mtbs_snapshot_loader(self, day: date) -> VerifiedMtbsSnapshot | None:
        """Delegate MTBS proof, but admit only a day present in this exact generation and rung."""
        loader = getattr(self.physical, "mtbs_snapshot_loader", None)
        loader = cast("Callable[[date], VerifiedMtbsSnapshot | None] | None", loader)
        snapshot = None if loader is None else loader(day)
        if snapshot is None:
            return None
        available_day = snapshot.descriptor.available_day
        if available_day not in self._keys_by_day:
            raise faults.availability_unpublished(
                layer=self.scope.layer,
                detail=f"MTBS snapshot {available_day.isoformat()} is absent from the current generation",
            )
        return snapshot


class AuthorizedServingReader:
    """Fresh-pointer reader whose immutable generations alone authorize time-bearing rows."""

    def __init__(self, store: AvailabilityStorage) -> None:
        self._store = GenerationCachingStorage(inner=store)
        self._indexes: dict[str, AvailabilityIndex] = {}
        self._index_cache_bytes = 0
        self._index_cache_rows = 0
        self._index_locks: dict[str, threading.Lock] = {}
        self._cache_lock = threading.Lock()

    def listing(
        self,
        physical: WarehouseListing,
        *,
        scope: ReadScope,
        now: datetime | None = None,
    ) -> WarehouseListing:
        """Return static physical behavior or an availability-bound listing for a time-bearing lane."""
        lane = _LANES.get(scope.layer)
        if lane is None:
            raise faults.availability_unpublished(layer=scope.layer, detail="the lane is not registered")
        if lane.nature == "static_lookup":
            return physical
        nature = lane.nature
        instant = datetime.now(UTC) if now is None else now
        try:
            index = self._read_index(
                nature=nature,
                scope=scope,
                required_ceiling=required_source_ceiling(lane, now=instant),
            )
        except AvailabilityError as exc:
            raise _availability_refusal(scope.layer, exc) from exc
        return AvailabilityAuthorizedListing(index=index, scope=scope, store=self._store, physical=physical)

    def _read_index(
        self,
        *,
        nature: AvailabilityNature,
        scope: ReadScope,
        required_ceiling: date,
    ) -> AvailabilityIndex:
        """Read a fresh pointer while parsing each immutable generation at most once per process."""
        lane_root = availability_lane_root(scope.layer, scope.kind)
        pointer = read_availability_pointer(
            self._store,
            lane_root=lane_root,
            expected_lane=scope.layer,
            expected_nature=nature,
            expected_required_rungs=AVAILABILITY_REQUIRED_RUNGS,
            required_source_ceiling=required_ceiling,
        )
        with self._cache_lock:
            lock = self._index_locks.setdefault(lane_root, threading.Lock())
        with lock:
            with self._cache_lock:
                cached = self._indexes.get(lane_root)
            if cached is not None and cached.pointer == pointer:
                return cached
            index = read_latest_availability(
                self._store,
                lane_root=lane_root,
                expected_lane=scope.layer,
                expected_nature=nature,
                expected_required_rungs=AVAILABILITY_REQUIRED_RUNGS,
                required_source_ceiling=required_ceiling,
            )
            with self._cache_lock:
                self._remember_index(lane_root, index)
            return index

    def _remember_index(self, lane_root: str, index: AvailabilityIndex) -> None:
        """Retain only small parsed generations under aggregate byte and row budgets."""
        generation_bytes = index.pointer.generation_bytes
        generation_rows = index.pointer.rows
        prior = self._indexes.pop(lane_root, None)
        if prior is not None:
            self._index_cache_bytes -= prior.pointer.generation_bytes
            self._index_cache_rows -= prior.pointer.rows
        if generation_bytes > MAX_CACHED_INDEX_GENERATION_BYTES or generation_rows > MAX_CACHED_INDEX_ROWS:
            return
        while self._indexes and (
            len(self._indexes) >= MAX_VERIFIED_AVAILABILITY_INDEXES
            or self._index_cache_bytes + generation_bytes > MAX_CACHED_INDEX_BYTES
            or self._index_cache_rows + generation_rows > MAX_CACHED_INDEX_ROWS
        ):
            oldest = next(iter(self._indexes))
            evicted = self._indexes.pop(oldest)
            self._index_cache_bytes -= evicted.pointer.generation_bytes
            self._index_cache_rows -= evicted.pointer.rows
        if (
            self._index_cache_bytes + generation_bytes <= MAX_CACHED_INDEX_BYTES
            and self._index_cache_rows + generation_rows <= MAX_CACHED_INDEX_ROWS
        ):
            self._indexes[lane_root] = index
            self._index_cache_bytes += generation_bytes
            self._index_cache_rows += generation_rows


class AuthorizedServingReaderHolder:
    """Build one read-only availability adapter per process."""

    def __init__(self) -> None:
        self._held: AuthorizedServingReader | None = None

    def get(self, source: Settings) -> AuthorizedServingReader:
        if self._held is None:
            storage = BotoAvailabilityStorage.from_settings(source, client_config=SERVING_CLIENT_CONFIG)
            self._held = AuthorizedServingReader(storage)
        return self._held


def _availability_refusal(layer: str, exc: AvailabilityError) -> faults.ServingRefusalError:
    """Translate evidence vocabulary once, without leaking it into HTTP or agent adapters."""
    if isinstance(exc, AvailabilityChecksumError):
        return faults.availability_checksum_invalid(layer=layer, detail=str(exc))
    if isinstance(exc, AvailabilityMalformedError):
        return faults.availability_malformed(layer=layer, detail=str(exc))
    if isinstance(exc, AvailabilityUnavailableError):
        refusal = faults.availability_unpublished if exc.code == "availability_missing" else faults.availability_stale
        return refusal(layer=layer, detail=str(exc))
    return faults.availability_malformed(layer=layer, detail=str(exc))


def _trusted_day_refusal(layer: str, day: date, detail: str) -> faults.ServingRefusalError:
    """A manifest-trusted day whose physical parts disagree with its marker: refused as incomplete, never served."""
    return faults.ServingRefusalError(
        "partition_day_incomplete",
        f"{layer} {day.isoformat()} is manifest-trusted and {detail}, so its parts are not the export its "
        "completion marker describes and the day is not served",
    )


def _part_index(key: str) -> int:
    """The part index of a key the physical binding already parsed as one of this scope's parts."""
    parsed = try_parse_partition_path(key)
    return -1 if parsed is None else parsed.part_index


def _parquet_row_count(staged: Path, *, layer: str, key: str) -> int:
    """Rows in one staged part, from its Parquet footer; bytes that are not Parquet are refused."""
    try:
        return int(pq.read_metadata(staged).num_rows)
    except (pa.ArrowException, OSError) as exc:
        raise faults.availability_malformed(
            layer=layer, detail=f"manifest-trusted part {key!r} is not readable Parquet"
        ) from exc


def resolve_authorized_day(
    authority: AuthorizedServingReader,
    physical: WarehouseListing,
    reader: PartitionRowReader,
    *,
    scope: ReadScope,
    day: date,
) -> DayEnvelope:
    """Resolve one day from a fresh availability head, preserving static lookup behavior."""
    listing = authority.listing(physical, scope=scope)
    return resolve_day(listing, _receipt_bound_reader(listing, reader), scope=scope, day=day)


def resolve_authorized_window(  # noqa: PLR0913 - authority, physical plane, reader, and closed scope/window
    authority: AuthorizedServingReader,
    physical: WarehouseListing,
    reader: PartitionRowReader,
    *,
    scope: ReadScope,
    first_day: date,
    last_day: date,
) -> tuple[DayEnvelope, ...]:
    """Resolve a bounded window from one availability generation and one shared row budget."""
    listing = authority.listing(physical, scope=scope)
    return resolve_window(
        listing,
        _receipt_bound_reader(listing, reader),
        scope=scope,
        first_day=first_day,
        last_day=last_day,
    )


def resolve_authorized_release(
    authority: AuthorizedServingReader,
    physical: WarehouseListing,
    reader: PartitionRowReader,
    *,
    scope: ReadScope,
    as_of: date,
) -> DayEnvelope:
    """Resolve the latest indexed release while retaining the caller's requested day."""
    listing = authority.listing(physical, scope=scope)
    envelope = resolve_release(listing, _receipt_bound_reader(listing, reader), scope=scope, as_of=as_of)
    return replace(envelope, requested_day=as_of)


@dataclass(frozen=True, slots=True)
class _ReceiptBoundRowReader:
    listing: AvailabilityAuthorizedListing
    inner: PartitionRowReader

    def read_rows(self, read: RowRead) -> RowReadResult:
        with self.listing.verified_object_uris(read.keys) as exact_sources:
            uris = tuple(exact_sources[key] for key in read.keys)
            return self.inner.read_rows(replace(read, object_uris=uris))


def _receipt_bound_reader(
    listing: WarehouseListing,
    reader: PartitionRowReader,
) -> PartitionRowReader:
    """Bind time-bearing row scans to verified bytes; static lookups retain physical serving."""
    if not isinstance(listing, AvailabilityAuthorizedListing):
        return reader
    return _ReceiptBoundRowReader(listing=listing, inner=reader)


@contextmanager
def verified_serving_session(
    listing: WarehouseListing,
    session: ServingSession,
    keys: Sequence[str],
) -> Iterator[ServingSession]:
    """Share exact-byte admission with agent queries that execute their own DuckDB statements."""
    if isinstance(listing, AvailabilityAuthorizedListing):
        with listing.verified_session(session, keys) as verified:
            yield verified
        return
    yield session

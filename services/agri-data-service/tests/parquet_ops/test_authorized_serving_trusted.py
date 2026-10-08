"""Manifest-trusted days serve at D3's trust level: the marker is digest-bound, its parts are bound by its counts.

Owner decision 2026-10-05, "serve now, hash later". See `parquet_ops/AGENTS.md`, "Trust levels".
Real Parquet bytes, the real availability row type, and real DuckDB behind both seams: the map's
authorized day/window resolvers and the agent's `distribution_at_point`.
"""

# ruff: noqa: PLR2004 - fixture days, part counts and measured values are the assertions.

from __future__ import annotations

import io
import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest
import structlog

from agri_data_service.agent import tools, window_distribution
from agri_data_service.foundation.canonical import sha256_digest
from agri_data_service.foundation.parquet.completion import PartitionCompletion
from agri_data_service.foundation.parquet.paths import absence_marker_path, completion_marker_path, partition_path
from agri_data_service.parquet_ops import authorized_serving as serving
from agri_data_service.parquet_ops import faults
from agri_data_service.parquet_ops.duckdb_session import ServingSession, open_guarded_connection
from agri_data_service.parquet_ops.request_params import ReadScope
from agri_data_service.parquet_ops.warehouse_reader import DuckDbRowReader
from agri_data_service.parquet_ops.wire import DayNotWritten, PublishedDay
from agri_data_service.pipeline.parquet.availability_index import AvailabilityRow, EvidenceReceipt
from agri_data_service.warehouse.parquet.schema import get_stream_schema
from tests.agent_fakes import FakeAgentWarehouse
from tests.parquet_ops.fakes import FakeListing

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence

    from agri_data_service.foundation.parquet.paths import PartitionKind
    from agri_data_service.foundation.parquet.zoom import ZoomTier

LANE = "climate-field-dew-point"
SCOPE = ReadScope(layer=LANE, kind="observed", tier=13, bbox=None)
#: Covers Boise at the agent's probe below; the second cell of every part is never at the point.
BOISE = {"longitude": -116.2, "latitude": 43.6}
#: An unreachable bucket: a key that was not staged would make DuckDB fail rather than read a real object.
UNSTAGED_BUCKET = "s3://unstaged-objects-are-never-read"


def _part(day: date, values: Sequence[float]) -> bytes:
    """One real dew-point part: the first value covers Boise, any further value lies one cell away."""
    rows = [
        {
            "support_key": "surface",
            "signal_name": "dew_point",
            "normalized_unit": "degC",
            "cell_id": f"cell-{index}",
            "observed_day": day,
            "normalized_value": value,
            "observation_count": 1,
            "newest_observed_at": datetime.combine(day, datetime.min.time(), UTC),
            "coverage_fraction": 1.0,
            "allowed_client_exposure": True,
            "cell_longitude": -116.0 + index,
            "cell_latitude": 43.0 + index,
        }
        for index, value in enumerate(values)
    ]
    sink = io.BytesIO()
    pq.write_table(pa.Table.from_pylist(rows, schema=get_stream_schema(LANE, "observed").arrow_schema), sink)
    return sink.getvalue()


def _rows_in(payload: bytes) -> int:
    return int(pq.ParquetFile(io.BytesIO(payload)).metadata.num_rows)


def _evidence(kind: str, day: date) -> EvidenceReceipt:
    digest = sha256_digest(f"{kind} {day.isoformat()}".encode())
    return EvidenceReceipt(key=f"layer={LANE}/kind=observed/availability/evidence/{kind}={digest}.json", sha256=digest)


class _Bucket:
    """One R2 stand-in: availability GETs read it and the physical listing LISTs it. Read-only by construction."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.reads: list[str] = []
        self.physical_lists: list[tuple[int | None, int | None]] = []

    def read(self, key: str, *, max_bytes: int) -> Any:
        self.reads.append(key)
        payload = self.objects.get(key)
        if payload is None:
            return None
        assert len(payload) <= max_bytes
        return SimpleNamespace(payload=payload, etag="test", version_id=None)

    def put_immutable(self, *_args: object, **_kwargs: object) -> None:
        raise AssertionError("a GET path attempted to publish")

    def compare_and_swap(self, *_args: object, **_kwargs: object) -> bool:
        raise AssertionError("a GET path attempted to advance a pointer")

    def physical(self) -> _CountingListing:
        return _CountingListing(keys=set(self.objects), bucket=self)

    def publish(  # noqa: PLR0913 - one override per way a trusted day can disagree with its marker
        self,
        day: date,
        parts: Sequence[bytes],
        *,
        trusted: bool,
        marker_part_count: int | None = None,
        marker_row_count: int | None = None,
        index_row_count: int | None = None,
    ) -> AvailabilityRow:
        """Write a day's parts and a count-only (v1) marker, and return the availability row that binds them."""
        keys = tuple(partition_path(LANE, "observed", 13, day, index) for index in range(len(parts)))
        self.objects.update(zip(keys, parts, strict=True))
        rows = sum(_rows_in(payload) for payload in parts)
        marker = PartitionCompletion(
            part_count=len(parts) if marker_part_count is None else marker_part_count,
            row_count=rows if marker_row_count is None else marker_row_count,
            completed_at=datetime(2026, 8, 24, 13, 18, tzinfo=UTC),
            run_id="parquet-drain:fixture",
        ).to_json_bytes()
        marker_key = completion_marker_path(LANE, "observed", 13, day)
        self.objects[marker_key] = marker
        data_receipts = (
            ()
            if trusted
            else tuple(
                sorted(
                    (
                        EvidenceReceipt(key=key, sha256=sha256_digest(payload))
                        for key, payload in zip(keys, parts, strict=True)
                    ),
                    key=lambda receipt: receipt.key,
                )
            )
        )
        return AvailabilityRow(
            lane=LANE,
            product=LANE,
            nature="daily_series",
            day=day,
            rung=13,
            terminal_state="published",
            row_count=(rows if marker_row_count is None else marker_row_count)
            if index_row_count is None
            else index_row_count,
            source_receipt=_evidence("source", day),
            terminal_receipt=_evidence("terminal", day),
            data_receipts=data_receipts,
            completion_receipt=EvidenceReceipt(key=marker_key, sha256=sha256_digest(marker)),
            absence_reason=None,
            source_ceiling=date(2026, 10, 1),
            published_at=datetime(2026, 9, 7, tzinfo=UTC),
        )


@dataclass
class _CountingListing(FakeListing):
    """The physical plane, recording each LIST the authorized listing spends on it."""

    bucket: _Bucket = field(default_factory=_Bucket)

    def list_keys(
        self,
        layer: str,
        kind: PartitionKind,
        tier: ZoomTier,
        *,
        year: int | None = None,
        month: int | None = None,
    ) -> tuple[str, ...]:
        self.bucket.physical_lists.append((year, month))
        return super().list_keys(layer, kind, tier, year=year, month=month)


def _stub_index(monkeypatch: pytest.MonkeyPatch, rows: Sequence[AvailabilityRow]) -> None:
    index = SimpleNamespace(
        rows=tuple(rows),
        pointer=SimpleNamespace(identity=SimpleNamespace(), generation_bytes=len(rows), rows=len(rows)),
    )
    monkeypatch.setattr(serving, "read_availability_pointer", lambda *_args, **_kwargs: index.pointer)
    monkeypatch.setattr(serving, "read_latest_availability", lambda *_args, **_kwargs: index)


@pytest.fixture
def duckdb_reader() -> Iterator[DuckDbRowReader]:
    """The production row reader over a real guarded connection; only staged local files are reachable."""
    connection = open_guarded_connection()
    try:
        yield DuckDbRowReader(ServingSession(connection=connection, bucket_uri=UNSTAGED_BUCKET))
    finally:
        connection.close()


def _values(day: PublishedDay) -> list[float]:
    return sorted(float(row["normalized_value"]) for row in day.rows)


# --- The map's day route: a trusted day serves, a disagreeing one refuses --------------------------


def test_a_manifest_trusted_day_serves_every_part_after_its_marker_and_reports_its_trust(
    monkeypatch: pytest.MonkeyPatch, duckdb_reader: DuckDbRowReader
) -> None:
    day = date(2024, 8, 22)
    bucket = _Bucket()
    # Two parts, like burn-severity's legacy markers (part_count=2 in the 2026-10-05 probe).
    row = bucket.publish(day, (_part(day, (4.5, 7.0)), _part(day, (1.25,))), trusted=True)
    _stub_index(monkeypatch, (row,))
    authority = serving.AuthorizedServingReader(bucket)

    with structlog.testing.capture_logs() as logs:
        answered = serving.resolve_authorized_day(authority, bucket.physical(), duckdb_reader, scope=SCOPE, day=day)

    assert isinstance(answered, PublishedDay)
    assert _values(answered) == [1.25, 4.5, 7.0]
    marker = completion_marker_path(LANE, "observed", 13, day)
    assert bucket.reads[0] == marker, "the commit point is verified before any part is fetched"
    assert sorted(bucket.reads[1:]) == [partition_path(LANE, "observed", 13, day, index) for index in (0, 1)]
    assert bucket.physical_lists == [(2024, None)], "one year LIST names the trusted day's parts"
    [trust] = [entry for entry in logs if entry["event"] == "authorized_read_trust"]
    assert (trust["trust_level"], trust["manifest_trusted_days"], trust["hash_verified_days"]) == (
        "manifest_trusted",
        1,
        0,
    )
    listing = authority.listing(bucket.physical(), scope=SCOPE)
    assert isinstance(listing, serving.AvailabilityAuthorizedListing)
    assert listing.trust_level(day) == "manifest_trusted"


def _surplus_part(bucket: _Bucket, day: date) -> None:
    bucket.objects[partition_path(LANE, "observed", 13, day, 2)] = _part(day, (9.0,))


def _missing_part(bucket: _Bucket, day: date) -> None:
    del bucket.objects[partition_path(LANE, "observed", 13, day, 1)]


def _swapped_marker(bucket: _Bucket, day: date) -> None:
    key = completion_marker_path(LANE, "observed", 13, day)
    bucket.objects[key] = bucket.objects[key].replace(b"fixture", b"rewrite")


def _governed_absence_beside_parts(bucket: _Bucket, day: date) -> None:
    bucket.objects[absence_marker_path(LANE, "observed", 13, day)] = b"{}"


def _part_is_not_parquet(bucket: _Bucket, day: date) -> None:
    bucket.objects[partition_path(LANE, "observed", 13, day, 0)] = b"not parquet bytes"


def _unchanged(_bucket: _Bucket, _day: date) -> None:
    return None


@pytest.mark.parametrize(
    ("publish_overrides", "damage", "code"),
    [
        # Part count: the physical LIST must name exactly part-0..part-(N-1) for the marker's N.
        ({}, _surplus_part, "partition_day_incomplete"),
        ({}, _missing_part, "partition_day_incomplete"),
        # ...even when the listed parts' rows happen to add up: the part count is its own check.
        ({"marker_part_count": 3}, _unchanged, "partition_day_incomplete"),
        # Row count: the parts' Parquet footers must add up to the marker's (and the index's) count.
        ({"marker_row_count": 4, "index_row_count": 4}, _unchanged, "partition_day_incomplete"),
        # The marker and the index disagree before a part is fetched.
        ({"index_row_count": 4}, _unchanged, "partition_day_incomplete"),
        # The marker's digest is the trust anchor, checked exactly as for a hashed day.
        ({}, _swapped_marker, "availability_checksum_invalid"),
        # A governed absence beside the parts is a conflict the trusted path never serves.
        ({}, _governed_absence_beside_parts, "partition_day_incomplete"),
        ({}, _part_is_not_parquet, "availability_malformed"),
    ],
    ids=[
        "surplus-part",
        "missing-part",
        "marker-counts-more-parts",
        "rows-short",
        "marker-vs-index",
        "marker-sha",
        "absent-json",
        "not-parquet",
    ],
)
def test_a_trusted_day_that_disagrees_with_its_marker_refuses_before_any_row_is_read(
    monkeypatch: pytest.MonkeyPatch,
    duckdb_reader: DuckDbRowReader,
    publish_overrides: dict[str, int],
    damage: Callable[[_Bucket, date], None],
    code: str,
) -> None:
    day = date(2023, 3, 9)
    bucket = _Bucket()
    row = bucket.publish(day, (_part(day, (2.0,)), _part(day, (3.0, 5.0))), trusted=True, **publish_overrides)
    damage(bucket, day)
    _stub_index(monkeypatch, (row,))
    reader = _RecordingReader(duckdb_reader)

    with pytest.raises(faults.ServingRefusalError) as caught:
        serving.resolve_authorized_day(
            serving.AuthorizedServingReader(bucket), bucket.physical(), reader, scope=SCOPE, day=day
        )

    assert caught.value.code == code
    assert reader.scans == 0, "a refused trusted day never reaches DuckDB"


@dataclass
class _RecordingReader:
    """The production reader, counting the scans that actually ran."""

    inner: DuckDbRowReader
    scans: int = 0

    def read_rows(self, read: Any) -> Any:
        result = self.inner.read_rows(read)
        self.scans += 1
        return result


# --- A mixed window: hashed and trusted days in one read ----------------------------------------


def _mixed_year(bucket: _Bucket, last_day: date) -> tuple[list[AvailabilityRow], dict[date, float]]:
    """365 days ending at `last_day`; the older half is trusted, like the bootstrap's 90-day digest window."""
    rows: list[AvailabilityRow] = []
    values: dict[date, float] = {}
    for offset in range(365):
        day = last_day - timedelta(days=364 - offset)
        value = float(offset % 37) - 10.0
        values[day] = value
        rows.append(bucket.publish(day, (_part(day, (value, 99.0)),), trusted=offset < 200))
    return rows, values


def test_a_mixed_365_day_window_serves_every_hashed_and_trusted_day(
    monkeypatch: pytest.MonkeyPatch, duckdb_reader: DuckDbRowReader
) -> None:
    last_day = date(2026, 6, 15)
    bucket = _Bucket()
    rows, values = _mixed_year(bucket, last_day)
    _stub_index(monkeypatch, rows)

    with structlog.testing.capture_logs() as logs:
        days = serving.resolve_authorized_window(
            serving.AuthorizedServingReader(bucket),
            bucket.physical(),
            duckdb_reader,
            scope=SCOPE,
            first_day=min(values),
            last_day=last_day,
        )

    assert len(days) == 365
    assert all(isinstance(day, PublishedDay) and not day.truncated for day in days)
    assert {day.served_day: _values(day) for day in days if isinstance(day, PublishedDay)} == {
        day: sorted((value, 99.0)) for day, value in values.items()
    }
    [trust] = [entry for entry in logs if entry["event"] == "authorized_read_trust"]
    assert (trust["manifest_trusted_days"], trust["hash_verified_days"]) == (200, 165)
    # One LIST per calendar YEAR holding a trusted day (2025 and 2026 here), never one per month or day.
    trusted_years = {day.year for day in sorted(values)[:200]}
    assert sorted(bucket.physical_lists) == [(year, None) for year in sorted(trusted_years)]


# --- The existence probe and the year binding ---------------------------------------------------


def _two_trusted_years(bucket: _Bucket) -> tuple[AvailabilityRow, ...]:
    """Trusted days in two calendar years: the shape that once turned the probe into a whole-tier LIST."""
    return tuple(
        bucket.publish(day, (_part(day, (1.0,)),), trusted=True) for day in (date(2023, 4, 2), date(2024, 7, 9))
    )


def test_a_day_in_a_month_with_no_index_rows_answers_not_written_without_any_physical_list(
    monkeypatch: pytest.MonkeyPatch, duckdb_reader: DuckDbRowReader
) -> None:
    bucket = _Bucket()
    _stub_index(monkeypatch, _two_trusted_years(bucket))
    authority = serving.AuthorizedServingReader(bucket)

    day = serving.resolve_authorized_day(authority, bucket.physical(), duckdb_reader, scope=SCOPE, day=date(2026, 1, 5))
    window = serving.resolve_authorized_window(
        authority,
        bucket.physical(),
        duckdb_reader,
        scope=SCOPE,
        first_day=date(2026, 1, 1),
        last_day=date(2026, 1, 5),
    )

    assert isinstance(day, DayNotWritten)
    assert all(isinstance(envelope, DayNotWritten) for envelope in window)
    assert bucket.physical_lists == [], "the lane-written probe is answered from the index alone"


def test_a_trusted_year_is_listed_once_and_its_day_keeps_its_parts_for_the_listing_life(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bucket = _Bucket()
    first, second = date(2024, 2, 3), date(2024, 11, 20)
    rows = tuple(bucket.publish(day, (_part(day, (1.0,)),), trusted=True) for day in (first, second))
    _stub_index(monkeypatch, rows)
    physical = bucket.physical()
    listing = serving.AuthorizedServingReader(bucket).listing(physical, scope=SCOPE)

    february = listing.list_keys(LANE, "observed", 13, year=2024, month=2)
    # A part written after the year was bound is not seen: the day answers the same parts again.
    physical.keys.add(partition_path(LANE, "observed", 13, first, 1))
    november = listing.list_keys(LANE, "observed", 13, year=2024, month=11)

    assert listing.list_keys(LANE, "observed", 13, year=2024, month=2) == february
    assert partition_path(LANE, "observed", 13, second) in november
    assert bucket.physical_lists == [(2024, None)], "one LIST binds every trusted month of the year"


# --- A hashed day is unchanged ------------------------------------------------------------------


def test_a_hashed_day_still_needs_its_digest_and_never_lists_the_physical_plane(
    monkeypatch: pytest.MonkeyPatch, duckdb_reader: DuckDbRowReader
) -> None:
    hashed_day, trusted_day = date(2026, 6, 10), date(2026, 5, 20)
    bucket = _Bucket()
    rows = (
        bucket.publish(hashed_day, (_part(hashed_day, (3.0,)),), trusted=False),
        bucket.publish(trusted_day, (_part(trusted_day, (8.0,)),), trusted=True),
    )
    _stub_index(monkeypatch, rows)
    authority = serving.AuthorizedServingReader(bucket)

    with structlog.testing.capture_logs() as logs:
        answered = serving.resolve_authorized_day(
            authority, bucket.physical(), duckdb_reader, scope=SCOPE, day=hashed_day
        )

    assert isinstance(answered, PublishedDay)
    assert _values(answered) == [3.0]
    assert bucket.physical_lists == [], "a hashed month is named by its receipts alone"
    assert not [entry for entry in logs if entry["event"] == "authorized_read_trust"]
    listing = authority.listing(bucket.physical(), scope=SCOPE)
    assert isinstance(listing, serving.AvailabilityAuthorizedListing)
    assert listing.trust_level(hashed_day) == "hash_verified"

    # The same bytes count as the same rows, but a hashed day is bound by digest, not by counts.
    bucket.objects[partition_path(LANE, "observed", 13, hashed_day)] = _part(hashed_day, (4.0,))
    with pytest.raises(faults.ServingRefusalError) as caught:
        serving.resolve_authorized_day(authority, bucket.physical(), duckdb_reader, scope=SCOPE, day=hashed_day)
    assert caught.value.code == "availability_checksum_invalid"


# --- The agent: distribution_at_point over a mixed 365-day window --------------------------------


@dataclass
class _AuthorizedLocalWarehouse(FakeAgentWarehouse):
    """The agent's source over the REAL availability-authorized listing and a real DuckDB connection."""

    bucket: _Bucket = field(default_factory=_Bucket)

    async def run(self, work: Callable[[Any], Any], *, operation: str) -> Any:
        self.operations.append(operation)
        connection = open_guarded_connection()
        try:
            return work(ServingSession(connection=connection, bucket_uri=UNSTAGED_BUCKET))
        finally:
            connection.close()

    def authorized_listing(self, scope: object) -> Any:
        assert isinstance(scope, ReadScope)
        return serving.AuthorizedServingReader(self.bucket).listing(self.bucket.physical(), scope=scope)


def _quantile(ordered: list[float], fraction: float) -> float:
    position = (len(ordered) - 1) * fraction
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (position - low) * (ordered[high] - ordered[low])


@pytest.mark.parametrize(
    "cap",
    [
        window_distribution.MAX_WINDOW_DAYS,  # no cut: every trusted and hashed day of the year
        window_distribution.MAX_DISTRIBUTION_DAYS_READ,  # the shipped cap: the latest days, still mixed trust
    ],
)
async def test_distribution_at_point_answers_a_mixed_365_day_window(monkeypatch: pytest.MonkeyPatch, cap: int) -> None:
    last_day = date(2026, 6, 15)
    monkeypatch.setattr(window_distribution, "utc_today", lambda: date(2026, 9, 1))
    monkeypatch.setattr(window_distribution, "MAX_DISTRIBUTION_DAYS_READ", cap)
    source = _AuthorizedLocalWarehouse()
    rows, values = _mixed_year(source.bucket, last_day)
    _stub_index(monkeypatch, rows)

    async with tools.run_context(warehouse_source=source):
        answer = json.loads(
            await tools.query_distribution_at_point(
                surface_name=LANE,
                **BOISE,
                range_start=min(values).isoformat(),
                range_end=last_day.isoformat(),
                zoom=13,
            )
        )

    [lane] = answer["lanes"]
    assert lane["state"] == "published", lane
    read = min(cap, 365)
    assert lane["day_states"] == {"published": read}
    assert (lane["days_with_data"], lane["days_read"], lane["truncated"]) == (read, read, read < 365)
    assert lane["read_range_start"] == (last_day - timedelta(days=read - 1)).isoformat()
    ordered = sorted(value for day, value in values.items() if day > last_day - timedelta(days=read))
    assert lane["stats"] == pytest.approx(
        {
            "min": ordered[0],
            "p10": _quantile(ordered, 0.1),
            "median": _quantile(ordered, 0.5),
            "p90": _quantile(ordered, 0.9),
            "max": ordered[-1],
            "mean": sum(ordered) / len(ordered),
        }
    )

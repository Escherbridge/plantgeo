"""Reconcile the burn-severity lane's `geo.features` rows against MTBS's own live completeness signals.

Layer: `pipeline` (needs the network; `layer-lanes.md` #1 forbids that in `method`). Per
docs/lanes/burn-severity.md #6, MTBS's own `returnCountOnly` completeness gate and cross-page
`fire_id` uniqueness assertion are reused via `ingest.mtbs.fetch_release_features` rather than
re-derived. An ignition year outside `MTBS_ANNUAL_RELEASE_DATES` is a governed gap and is reported,
never raised: `ingest-mtbs` itself refuses that outright on its own weekly cron
(docs/lanes/burn-severity.md #2), but a validator meant to run every tick must keep going rather
than replicate that refusal as a crash.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

import httpx

from agri_data_service.ingest.mtbs import (
    MTBS_ANNUAL_RELEASE_DATES,
    MtbsIngestError,
    build_mtbs_record,
    build_release_identifier,
    fetch_release_features,
    resolve_data_available_at,
    validate_release_window,
)
from agri_data_service.pipeline.lanes.burn_severity import read_burn_severity_release_day
from agri_data_service.warehouse.schemas.burn_severity import BURN_SEVERITY_STREAM

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from datetime import date

    import pyarrow as pa  # type: ignore[import-untyped]
    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.ingest.mtbs import BoundingBox, MtbsBurnSeverityRecord

_UNGOVERNED_YEAR_DETAIL: Final = (
    "has no established release publication date in MTBS_ANNUAL_RELEASE_DATES; this is a governance "
    "gap awaiting a dated release announcement, never a day for this validator to retry"
)


def governed_ignition_years() -> tuple[int, ...]:
    """Every fire year this validator can reconcile today, ascending -- the same set `ingest-mtbs` covers."""
    return tuple(sorted(MTBS_ANNUAL_RELEASE_DATES))


@dataclass(frozen=True, slots=True)
class BurnSeverityReleaseReconciliation:
    """One ignition year's reconciliation verdict, or the governed gap that makes it not yet possible.

    `release_day` and every MTBS-derived field are `None`/empty when `governed_gap_reason` is set.
    `severity_class` is never compared: it is null on every published row by design
    (docs/lanes/burn-severity.md #5), and MTBS publishes no polygon-level classification to check it
    against.
    """

    ignition_year: int
    checked_at: datetime
    bounding_box: BoundingBox
    governed_gap_reason: str | None
    release_day: date | None
    written_fire_identifiers: frozenset[str]
    mtbs_fire_identifiers: frozenset[str]
    mtbs_authoritative_count: int | None
    mismatches: tuple[str, ...]

    @property
    def missing_from_source(self) -> frozenset[str]:
        """Fires this lane wrote that MTBS's live feature service no longer reports."""
        return self.written_fire_identifiers - self.mtbs_fire_identifiers

    @property
    def missing_from_written(self) -> frozenset[str]:
        """Fires MTBS's live feature service reports that this lane never wrote."""
        return self.mtbs_fire_identifiers - self.written_fire_identifiers

    @property
    def is_complete(self) -> bool:
        """A governed gap always reconciles; otherwise every fire and every count must agree."""
        if self.governed_gap_reason is not None:
            return True
        return (
            not self.missing_from_source
            and not self.missing_from_written
            and not self.mismatches
            and len(self.written_fire_identifiers) == self.mtbs_authoritative_count
        )

    def failure_message(self) -> str | None:
        """Name the release, the lane, and the source response; `None` when it reconciles."""
        if self.is_complete:
            return None
        if self.governed_gap_reason is not None:  # pragma: no cover - governed gaps are always complete.
            return None
        release_label = self.release_day.isoformat() if self.release_day is not None else "unresolved"
        return (
            f"{BURN_SEVERITY_STREAM} reconciliation for fire year {self.ignition_year} "
            f"(release {release_label}, checked {self.checked_at.isoformat()} against MTBS's live "
            f"feature service over bbox {self.bounding_box}): wrote {len(self.written_fire_identifiers)} "
            f"fires, MTBS reports {self.mtbs_authoritative_count} authoritative / "
            f"{len(self.mtbs_fire_identifiers)} paged; missing from source: "
            f"{sorted(self.missing_from_source)}; missing from written: {sorted(self.missing_from_written)}; "
            f"mismatches: {list(self.mismatches)}"
        )


def _utc_now() -> datetime:
    return datetime.now(UTC)


async def reconcile_burn_severity_release(
    session: AsyncSession,
    *,
    ignition_year: int,
    bounding_box: BoundingBox,
    client: httpx.AsyncClient,
    now: Callable[[], datetime] = _utc_now,
) -> BurnSeverityReleaseReconciliation:
    """Compare one fire year's rows in `geo.features` against a fresh MTBS live feature-service fetch.

    The written side reuses `pipeline.lanes.burn_severity.read_burn_severity_release_day` -- the
    exact release-day-scoped query the exporter itself runs. `geo.features` holds one CURRENT row per
    Fire_ID, refreshed in place rather than versioned per release (docs/lanes/burn-severity.md #4), so
    reading current state IS reading the release history here, and a second, independently-scoped
    query would only prove this function agrees with itself. The source side reuses
    `ingest.mtbs.fetch_release_features`, which already enforces the authoritative `returnCountOnly`
    completeness gate and cross-page `fire_id` uniqueness this reconciliation would otherwise have to
    re-derive (docs/lanes/burn-severity.md #6).
    """
    release_date = MTBS_ANNUAL_RELEASE_DATES.get(ignition_year)
    if release_date is None:
        return BurnSeverityReleaseReconciliation(
            ignition_year=ignition_year,
            checked_at=now(),
            bounding_box=bounding_box,
            governed_gap_reason=f"MTBS fire year {ignition_year} {_UNGOVERNED_YEAR_DETAIL}",
            release_day=None,
            written_fire_identifiers=frozenset(),
            mtbs_fire_identifiers=frozenset(),
            mtbs_authoritative_count=None,
            mismatches=(),
        )

    written = await read_burn_severity_release_day(session, release_day=release_date)
    written_ids: frozenset[str] = frozenset(written.column("fire_id").to_pylist())

    try:
        features, authoritative_count = await fetch_release_features(ignition_year, bounding_box, client=client)
    except (MtbsIngestError, httpx.HTTPError) as exc:
        return BurnSeverityReleaseReconciliation(
            ignition_year=ignition_year,
            checked_at=now(),
            bounding_box=bounding_box,
            governed_gap_reason=None,
            release_day=release_date,
            written_fire_identifiers=written_ids,
            mtbs_fire_identifiers=frozenset(),
            mtbs_authoritative_count=None,
            mismatches=(f"MTBS feature service query failed: {type(exc).__name__}: {exc}",),
        )

    live_records, malformed = _normalise_live_features(features, ignition_year)
    window_failure = _reassert_release_window(live_records, ignition_year)
    row_mismatches = _compare_written_rows(written, live_records, build_release_identifier(ignition_year))
    mismatches = (*malformed, *((window_failure,) if window_failure else ()), *row_mismatches)

    return BurnSeverityReleaseReconciliation(
        ignition_year=ignition_year,
        checked_at=now(),
        bounding_box=bounding_box,
        governed_gap_reason=None,
        release_day=release_date,
        written_fire_identifiers=written_ids,
        mtbs_fire_identifiers=frozenset(live_records),
        mtbs_authoritative_count=authoritative_count,
        mismatches=mismatches,
    )


def _normalise_live_features(
    features: Sequence[Mapping[str, object]],
    ignition_year: int,
) -> tuple[dict[str, MtbsBurnSeverityRecord], tuple[str, ...]]:
    """Normalise MTBS's live features through the same builder ingest trusts; report what would not parse."""
    records: dict[str, MtbsBurnSeverityRecord] = {}
    malformed: list[str] = []
    for feature in features:
        try:
            record = build_mtbs_record(feature, ignition_year)
        except MtbsIngestError as exc:
            malformed.append(f"a live MTBS feature could not be normalised: {type(exc).__name__}: {exc}")
            continue
        records[record.producer_local_id] = record
    return records, tuple(malformed)


def _reassert_release_window(records: Mapping[str, MtbsBurnSeverityRecord], ignition_year: int) -> str | None:
    """Re-run the ignition-lead and now() tripwires MTBS's own capture already enforced once at ingest."""
    if not records:
        return None
    observed_to = _midnight_utc(max(record.ignition_date for record in records.values()))
    try:
        validate_release_window(resolve_data_available_at(ignition_year), observed_to)
    except MtbsIngestError as exc:
        return f"release-window tripwire failed on re-assertion: {type(exc).__name__}: {exc}"
    return None


def _midnight_utc(day: date) -> datetime:
    """Anchor a calendar day at midnight UTC, mirroring `ingest.mtbs`'s private helper of the same name."""
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


def _compare_written_rows(
    written: pa.Table,
    live_records: Mapping[str, MtbsBurnSeverityRecord],
    expected_release_identifier: str,
) -> tuple[str, ...]:
    """Check release identity and the acres/geometry relationship; `severity_class` is never compared.

    `geom` is `NOT NULL` on `BURN_SEVERITY_SCHEMA`, so "acres implies geometry" is vacuous by
    construction -- the real, checkable invariants are that the WKB bytes are actually non-empty and
    that a published acreage is never negative.
    """
    mismatches: list[str] = []
    for row in written.to_pylist():
        fire_id = row["fire_id"]
        if row["release_identifier"] != expected_release_identifier:
            mismatches.append(
                f"{fire_id}: written release_identifier {row['release_identifier']!r} != "
                f"{expected_release_identifier!r}"
            )
        if not row["geom"]:
            mismatches.append(f"{fire_id}: geom is empty bytes; BURN_SEVERITY_SCHEMA requires non-null WKB")
        acres_value = row["acres"]
        if acres_value is not None and acres_value < 0:
            mismatches.append(f"{fire_id}: acres {acres_value!r} is negative, which no burned area can be")
        live_record = live_records.get(fire_id)
        if live_record is None:
            continue
        if acres_value != live_record.acres:
            mismatches.append(f"{fire_id}: written acres {acres_value!r} != MTBS's live acres {live_record.acres!r}")
        if row["mapping_revision"] != live_record.mapping_revision:
            mismatches.append(
                f"{fire_id}: written mapping_revision {row['mapping_revision']!r} != MTBS's current "
                f"{live_record.mapping_revision!r} -- MTBS may have reissued this fire's mapping"
            )
    return tuple(mismatches)

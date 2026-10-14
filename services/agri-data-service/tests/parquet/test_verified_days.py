"""Verified days are manual-correction only: the seam, the gap-fill planner, ladder repair and the correction trace.

Driven through the public seams every writer uses -- `run_gap_fill`, `fill_one_lane_day` and
`repair_one_lane_day` -- over ONE in-memory bucket shared by the lane's object store and the
availability storage, as production shares R2. Only the database session is a fake.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest

from agri_data_service.foundation.canonical import sha256_digest
from agri_data_service.pipeline.parquet.gap_fill import (
    GAP_FILL_PARTITION_KIND,
    GAP_FILL_ZOOM_TIER,
    fill_one_lane_day,
    no_derived_tiers,
    repair_one_lane_day,
    unlocked_lane_day,
)
from agri_data_service.pipeline.parquet.lane_registry import LaneRegistration, LaneRunResult
from agri_data_service.pipeline.parquet.objectstore import ObjectStore, verified_days_register_path
from agri_data_service.pipeline.parquet.verified_days import (
    AUTOMATED_REWRITE,
    MANUAL_CORRECTION,
    VERIFIED_REFUSAL,
    VerifiedDay,
    VerifiedDays,
    forget_verified_days,
    read_verified_days,
    record_verified_days,
    render_verified_days,
)
from tests.parquet.test_availability_extension import LANE, new_lane, write_published_day
from tests.parquet.test_gap_fill import (
    FROZEN_NOW,
    TODAY,
    WINDOW_DAYS,
    LaneCall,
    RecordingSession,
    days_newest_first,
    drive,
    stub_lane,
)
from tests.parquet.test_objectstore_writer import RecordingBackend, signal_rows

if TYPE_CHECKING:
    from collections.abc import Iterable

    from agri_data_service.pipeline.parquet.verified_days import RewritePolicy
    from tests.parquet.test_availability_extension import LaneAvailabilityStorage, LoggingBackend

VERIFIED_DAY = date(2026, 8, 20)
MISSING_DAY = VERIFIED_DAY + timedelta(days=2)
CORRECTED_AT = datetime(2026, 10, 13, 9, tzinfo=UTC)


def verified_entry(day: date, *, at: datetime = FROZEN_NOW) -> VerifiedDay:
    """One register entry as a manual verb writes it; the ladder digest is a stand-in the seam never reads."""
    return VerifiedDay(
        day=day,
        ladder_sha256=sha256_digest(f"ladder {day.isoformat()}"),
        base_completion_sha256=None,
        generation_key="layer=signal/kind=observed/availability/generation=fixture",
        verified_at=at,
        verified_by="availability-digest-trusted",
    )


def seed_verified_register(backend: RecordingBackend, store: ObjectStore, lane: str, days: Iterable[date]) -> None:
    """Write a lane's register straight into the bucket, then start a new turn's reads of it."""
    register = VerifiedDays(lane=lane, kind="observed", days={day: verified_entry(day) for day in days})
    backend.put(
        verified_days_register_path(lane, "observed"), render_verified_days(register), content_type="application/json"
    )
    forget_verified_days(store)


@dataclass
class RevisedSource:
    """A lane adapter whose source now answers different rows than the published day holds."""

    calls: list[date] = field(default_factory=list)

    async def __call__(self, session: Any, store: ObjectStore, *, day: date, run_id: str) -> LaneRunResult:  # noqa: ARG002 - the LaneAdapter shape
        self.calls.append(day)
        written = store.write_partition(
            signal_rows(cell_ids=("revised-a", "revised-b")),
            layer=LANE,
            kind=GAP_FILL_PARTITION_KIND,
            zoom=GAP_FILL_ZOOM_TIER,
            day=day,
        )
        return LaneRunResult(
            part_count=1, row_count=written.row_count, byte_count=written.byte_count, absence_recorded=False
        )


def signal_lane(adapter: RevisedSource) -> LaneRegistration:
    return LaneRegistration(
        slug=LANE,
        adapter=adapter,
        history_floor=date(2026, 1, 1),
        publication_lag_days=0,
        nature="daily_series",
        floor_basis="verified-days fixture",
    )


async def fill(
    store: ObjectStore,
    storage: LaneAvailabilityStorage,
    adapter: RevisedSource,
    *,
    day: date,
    policy: RewritePolicy = AUTOMATED_REWRITE,
) -> tuple[str, int, int, int, str | None]:
    """One lane-day through the seam every writer shares, availability storage wired as production wires it."""
    return await fill_one_lane_day(
        RecordingSession(),  # type: ignore[arg-type]
        store,
        signal_lane(adapter),
        day=day,
        run_id="manual-correction:test",
        now=lambda: CORRECTED_AT,
        today=CORRECTED_AT.date(),
        lane_day_lock=unlocked_lane_day,
        derive_tiers=no_derived_tiers,
        extend_availability=False,
        availability_storage=storage,
        rewrite_policy=policy,
    )


def verified_published_day() -> tuple[LoggingBackend, ObjectStore, LaneAvailabilityStorage]:
    """A published ladder the owner has verified, registered through the manual verbs' own write path."""
    backend, store, storage, _log = new_lane()
    write_published_day(store, day=VERIFIED_DAY)
    record_verified_days(storage, lane=LANE, kind="observed", entries=[verified_entry(VERIFIED_DAY)])
    return backend, store, storage


# --- the seam ----------------------------------------------------------------------------------------


async def test_an_automated_fill_of_a_verified_day_whose_source_changed_leaves_it_untouched() -> None:
    """Gap fill meets a verified day: nothing is fetched or written, and the refusal is the day's recorded outcome."""
    backend, store, storage = verified_published_day()
    bucket = dict(backend.objects)
    source = RevisedSource()

    outcome, parts, rows, _bytes, detail = await fill(store, storage, source, day=VERIFIED_DAY)

    assert (outcome, parts, rows) == (VERIFIED_REFUSAL, 0, 0)
    assert (detail or "").startswith(f"{VERIFIED_REFUSAL}: {LANE} {VERIFIED_DAY.isoformat()} is verified")
    assert source.calls == []
    assert backend.objects == bucket


async def test_a_manual_correction_rewrites_a_verified_day_releases_it_and_leaves_a_trace() -> None:
    """`--correct-verified`: the day is rewritten, no longer verified, and the register names both marker digests."""
    _backend, store, storage = verified_published_day()
    old_marker = store.read_completion_receipt(LANE, GAP_FILL_PARTITION_KIND, GAP_FILL_ZOOM_TIER, VERIFIED_DAY)
    source = RevisedSource()

    outcome, _parts, rows, _bytes, detail = await fill(
        store, storage, source, day=VERIFIED_DAY, policy=MANUAL_CORRECTION
    )

    assert (outcome, rows, source.calls) == ("written", 2, [VERIFIED_DAY])
    assert "needs re-verifying" in (detail or "")
    register = read_verified_days(store, lane=LANE, kind="observed")
    assert not register.is_verified(VERIFIED_DAY)
    (correction,) = register.corrections
    new_marker = store.read_completion_receipt(LANE, GAP_FILL_PARTITION_KIND, GAP_FILL_ZOOM_TIER, VERIFIED_DAY)
    assert (correction.day, correction.run_id) == (VERIFIED_DAY, "manual-correction:test")
    assert correction.old_base_completion_sha256 == getattr(old_marker, "sha256", "no marker before")
    assert correction.new_base_completion_sha256 == getattr(new_marker, "sha256", "no marker after")
    assert correction.new_base_completion_sha256 != correction.old_base_completion_sha256
    assert correction.verified_ladder_sha256 == verified_entry(VERIFIED_DAY).ladder_sha256
    # Released, so it is ordinary automated work again until the owner re-verifies it.
    again = RevisedSource()
    assert (await fill(store, storage, again, day=VERIFIED_DAY))[0] == "written"
    assert again.calls == [VERIFIED_DAY]


async def test_an_unreadable_register_refuses_an_existing_day_and_still_writes_a_missing_one() -> None:
    """Fail-closed for overwrites only: a day that cannot be proven unverified is left alone; a new day is written."""
    backend, store, storage, _log = new_lane()
    write_published_day(store, day=VERIFIED_DAY)
    backend.objects[verified_days_register_path(LANE, "observed")] = b"not a register"
    published, missing = RevisedSource(), RevisedSource()

    refused = await fill(store, storage, published, day=VERIFIED_DAY)
    written = await fill(store, storage, missing, day=MISSING_DAY)

    assert refused[0] == VERIFIED_REFUSAL
    assert published.calls == []
    assert written[0] == "written"
    assert missing.calls == [MISSING_DAY]


async def test_automated_ladder_repair_of_a_verified_day_is_refused_because_the_digest_covers_every_rung() -> None:
    backend, store, storage = verified_published_day()
    bucket = dict(backend.objects)

    def refuse_to_derive(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("a verified day's coarse rungs must not be re-derived")

    repair = await repair_one_lane_day(
        RecordingSession(),  # type: ignore[arg-type]
        store,
        signal_lane(RevisedSource()),
        day=VERIFIED_DAY,
        run_id="ladder-repair:test",
        now=lambda: CORRECTED_AT,
        lane_day_lock=unlocked_lane_day,
        today=CORRECTED_AT.date(),
        derive_tiers=refuse_to_derive,  # type: ignore[arg-type]
        availability_storage=storage,
    )

    assert repair.outcome == VERIFIED_REFUSAL
    assert backend.objects == bucket


# --- the gap-fill planner ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("verified_count", "expected_attempts"),
    [
        # The whole owed window is verified: the lane authors nothing and says how many it skipped.
        (WINDOW_DAYS, []),
        # Only the newest day is unverified: it is still filled, exactly as before.
        (WINDOW_DAYS - 1, [TODAY]),
    ],
)
async def test_gap_fill_never_selects_a_verified_day_and_still_fills_the_rest(
    verified_count: int, expected_attempts: list[date]
) -> None:
    calls: list[LaneCall] = []
    backend = RecordingBackend()
    store = ObjectStore(backend)
    verified = days_newest_first(WINDOW_DAYS)[WINDOW_DAYS - verified_count :]
    seed_verified_register(backend, store, "signal", verified)
    session = RecordingSession()

    summary = await drive([stub_lane("signal", calls)], store, session=session)

    assert [call.day for call in calls] == expected_attempts
    (lane,) = summary.lanes
    assert (lane.skipped_verified, lane.written, lane.remaining) == (verified_count, len(expected_attempts), 0)
    assert summary.to_summary()["skipped_verified"] == verified_count
    assert not summary.failed
    # Dropped by the PLANNER, not refused at the seam: no verified day ever took a lane-day lock.
    day_locks = [statement for statement in session.statements if "pg_try_advisory_lock(" in statement]
    assert len(day_locks) == len(expected_attempts)

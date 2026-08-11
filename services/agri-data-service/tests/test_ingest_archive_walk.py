"""The archive walk as a durable handler: one window per work item, one chunk per call, and no silent completion."""

# ruff: noqa: PLR2004

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from agri_data_service.ingest import archive_walk as archive_walk_module
from agri_data_service.ingest.archive_walk import (
    ARCHIVE_WALK_CHUNK_ESTIMATE_MARGIN,
    ARCHIVE_WALK_HANDLER_TOKEN,
    ARCHIVE_WALK_LEASE_SECONDS,
    ARCHIVE_WALK_MAX_ATTEMPTS,
    ARCHIVE_WALK_TIME_BUDGET_SECONDS,
    ARCHIVE_WALK_WORK_ITEM_KIND,
    ARCHIVE_WALK_WORST_CHUNK_SECONDS,
    BUDGET_STOP_REASON,
    CURSOR_NEXT_CHUNK_START,
    CURSOR_RECORDS_REJECTED,
    CURSOR_RECORDS_SEEN,
    CURSOR_RECORDS_WRITTEN,
    CURSOR_SLOWEST_CHUNK_SECONDS,
    CURSOR_WALK_GENERATION,
    MISSING_CREDENTIAL_FAILURE_CLASS,
    PAYLOAD_WALK_GENERATION,
    SKIPPED_FAILURE_CLASS,
    TOTAL_REJECTION_FAILURE_CLASS,
    TRUNCATED_FAILURE_CLASS,
    UPSTREAM_FAILURE_CLASS,
    ArchiveWalkContext,
    ArchiveWalkContextError,
    ArchiveWalkCursorError,
    ArchiveWalkPayloadError,
    ArchiveWindowRequest,
    archive_lane_definition_name,
    archive_lane_definition_spec,
    archive_lane_run_key,
    archive_lane_work_items,
    archive_source,
    archive_sources,
    archive_walk_context,
    archive_walk_handler,
    archive_window_payload,
    backfill_outcome,
    chunk_budget_seconds,
    chunk_position,
    effective_cursor,
    pinned_source_record_cap,
    plan_archive_lane,
    walk_archive_chunk,
)
from agri_data_service.ingest.backfill import history_chunks
from agri_data_service.ingest.commands import _build_backfillable_sources
from agri_data_service.ingest.firms import FIRMS_API_KEY_VARIABLE
from agri_data_service.ingest.lanes import BACKFILL_LANES, FIRMS_ARCHIVE_LANE, BackfillLane, lane_windows
from agri_data_service.ingest.policy import MAX_SOURCE_RECORDS_VARIABLE
from agri_data_service.ingest.results import IngestionJobResult, skipped_result
from agri_data_service.ingest.source import HistoryWindow
from agri_data_service.jobs import JOB_HANDLERS, JobInvocation, JobWorkItemSpec

if TYPE_CHECKING:
    from collections.abc import Sequence

    from agri_data_service.ingest.writer import FeatureWrite

# A shallow-floored lane, so one whole plan fits in an assertion. Everything else about it is the FIRMS
# lane's measured shape: one day per fetched chunk, five days per work item.
TEST_LANE = BackfillLane(
    name="test-archive",
    source_name=FIRMS_ARCHIVE_LANE.source_name,
    floor=datetime(2026, 1, 1, tzinfo=UTC),
    chunk_days=1,
    window_days=5,
)

KEYED_LANE = BackfillLane(
    name="keyed-archive",
    source_name=FIRMS_ARCHIVE_LANE.source_name,
    floor=datetime(2026, 1, 1, tzinfo=UTC),
    chunk_days=1,
    window_days=5,
    credential_variable=FIRMS_API_KEY_VARIABLE,
)

WALK_END = datetime(2026, 1, 11, tzinfo=UTC)
NEWEST_WINDOW = HistoryWindow(start=datetime(2026, 1, 6, tzinfo=UTC), end=datetime(2026, 1, 11, tzinfo=UTC))


class RecordingWriter:
    """A feature writer that records what a walk handed it, so a handler test needs no database."""

    def __init__(self) -> None:
        self.writes: list[FeatureWrite] = []

    async def __call__(self, writes: Sequence[FeatureWrite]) -> int:
        self.writes.extend(writes)
        return len(writes)


@pytest.fixture(autouse=True)
def _clear_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for variable in (FIRMS_API_KEY_VARIABLE, "INGEST_BBOX", MAX_SOURCE_RECORDS_VARIABLE):
        monkeypatch.delenv(variable, raising=False)


def _use_lane(monkeypatch: pytest.MonkeyPatch, lane: BackfillLane) -> None:
    """Point the handler's lane lookup at one lane; `BACKFILL_LANES` is a frozen mapping by design."""
    monkeypatch.setattr(archive_walk_module, "resolve_lane", lambda _name: lane)


def _ingested(records_seen: int = 3, records_written: int = 3, rejected: int = 0) -> IngestionJobResult:
    return IngestionJobResult(
        source=TEST_LANE.source_name,
        status="ingested",
        records_seen=records_seen,
        records_written=records_written,
        details={"rejected": rejected, "dropped": 0},
    )


def _invocation(
    *,
    cursor: dict[str, object] | None = None,
    lane_name: str = TEST_LANE.name,
    fence_held: bool = True,
    seconds_remaining: float = 700.0,
    payload_generation: int | None = None,
) -> JobInvocation:
    async def heartbeat() -> bool:
        return fence_held

    payload: dict[str, object] = {
        "lane": lane_name,
        "window_start": NEWEST_WINDOW.start.isoformat(),
        "window_end": NEWEST_WINDOW.end.isoformat(),
    }
    if payload_generation is not None:
        payload[PAYLOAD_WALK_GENERATION] = payload_generation

    return JobInvocation(
        shard_key=f"{lane_name}:2026-01-06..2026-01-11",
        kind=ARCHIVE_WALK_WORK_ITEM_KIND,
        payload=payload,
        cursor=cursor,
        parameters={},
        attempt_number=1,
        max_attempts=ARCHIVE_WALK_MAX_ATTEMPTS,
        progress_fraction=0.0,
        seconds_remaining=seconds_remaining,
        heartbeat=heartbeat,
    )


# --------------------------------------------------------------------------- payload and cursor


def test_a_stored_payload_reads_back_as_the_window_it_names() -> None:
    newest = lane_windows(TEST_LANE, WALK_END)[0]
    payload = archive_window_payload(TEST_LANE, newest, bbox="-125,42,-111,49")

    request = ArchiveWindowRequest.from_payload(payload)

    assert request.lane_name == TEST_LANE.name
    assert request.window == NEWEST_WINDOW
    assert request.bbox == "-125,42,-111,49"


def test_a_payload_whose_window_bound_names_no_timezone_is_refused_rather_than_guessed_local() -> None:
    payload = {"lane": TEST_LANE.name, "window_start": "2026-01-06", "window_end": "2026-01-11T00:00:00+00:00"}

    with pytest.raises(ArchiveWalkPayloadError, match="must name its timezone"):
        ArchiveWindowRequest.from_payload(payload)


def test_an_inverted_payload_window_is_refused_as_naming_no_walkable_window() -> None:
    payload = {
        "lane": TEST_LANE.name,
        "window_start": "2026-01-11T00:00:00+00:00",
        "window_end": "2026-01-06T00:00:00+00:00",
    }

    with pytest.raises(ArchiveWalkPayloadError, match="no walkable window"):
        ArchiveWindowRequest.from_payload(payload)


def test_a_payload_whose_window_starts_where_it_ends_is_refused_before_it_can_index_an_empty_chunk_list() -> None:
    # `history_chunks` returns [] for start >= end and the handler indexes `chunks[position]`, so an
    # unguarded zero-width window is an IndexError -- a failure class naming nothing an operator can act
    # on, retried through all eight attempts of 30s-doubling-to-an-hour backoff before it dead-letters.
    # `HistoryWindow.__post_init__` refuses the shape first and `from_payload` re-raises it as this
    # module's own typed refusal, and the handler carries a second guard for the same span.
    payload = {
        "lane": TEST_LANE.name,
        "window_start": "2026-01-06T00:00:00+00:00",
        "window_end": "2026-01-06T00:00:00+00:00",
    }

    with pytest.raises(ArchiveWalkPayloadError, match="no walkable window"):
        ArchiveWindowRequest.from_payload(payload)


def test_an_absent_cursor_starts_the_window_at_its_first_chunk() -> None:
    assert chunk_position(None, history_chunks(NEWEST_WINDOW, TEST_LANE.chunk)) == 0


def test_a_cursor_resumes_the_window_at_the_chunk_it_names() -> None:
    chunks = history_chunks(NEWEST_WINDOW, TEST_LANE.chunk)

    assert chunk_position({CURSOR_NEXT_CHUNK_START: chunks[3].start.isoformat()}, chunks) == 3


def test_a_cursor_naming_no_chunk_of_this_window_refuses_rather_than_silently_restarting_the_walk() -> None:
    # A cursor the code cannot place means the lane's chunk_days changed under a run already in flight.
    # Restarting from the top would be safe but would hide it, and a walk that keeps going once its own
    # durable record has stopped making sense is the whole failure class this port exists to remove.
    chunks = history_chunks(NEWEST_WINDOW, TEST_LANE.chunk)

    with pytest.raises(ArchiveWalkCursorError, match="names no chunk"):
        chunk_position({CURSOR_NEXT_CHUNK_START: "2019-05-05T00:00:00+00:00"}, chunks)


# --------------------------------------------------------------------------- the walk generation


def test_a_window_that_was_never_reopened_keeps_its_cursor_exactly_as_it_found_it() -> None:
    cursor = {CURSOR_NEXT_CHUNK_START: "2026-01-09T00:00:00+00:00"}

    assert effective_cursor({"lane": TEST_LANE.name}, cursor) is cursor
    assert effective_cursor({"lane": TEST_LANE.name}, None) is None


def test_a_cursor_written_before_a_reopen_is_discarded_whole_rather_than_resumed_from() -> None:
    # Resume position comes from the newest job_checkpoint row, and a window that COMPLETED left its last
    # checkpoint pointing at its FINAL chunk. Reopened without this, a five-day window walks day five, finds
    # no further chunk, and succeeds again over the four days that were missing.
    stale = {CURSOR_NEXT_CHUNK_START: "2026-01-10T00:00:00+00:00", CURSOR_RECORDS_SEEN: 900}

    assert effective_cursor({PAYLOAD_WALK_GENERATION: 1}, stale) is None


def test_a_cursor_written_after_the_reopen_survives_so_the_new_pass_still_resumes() -> None:
    fresh = {CURSOR_NEXT_CHUNK_START: "2026-01-08T00:00:00+00:00", CURSOR_WALK_GENERATION: 2}

    assert effective_cursor({PAYLOAD_WALK_GENERATION: 2}, fresh) is fresh


# --------------------------------------------------------------------------- result to outcome


def test_an_ingested_chunk_that_is_not_the_last_progresses_and_records_where_the_next_one_starts() -> None:
    next_cursor = {CURSOR_NEXT_CHUNK_START: "2026-01-07T00:00:00+00:00"}

    outcome = backfill_outcome(_ingested(), next_cursor=next_cursor, progress_fraction=0.2, metrics={})

    assert outcome.kind == "progressed"
    assert outcome.cursor == next_cursor
    assert outcome.progress_fraction == 0.2


def test_the_last_ingested_chunk_completes_the_window() -> None:
    outcome = backfill_outcome(_ingested(), next_cursor=None, progress_fraction=1.0, metrics={})

    assert outcome.kind == "completed"
    assert outcome.progress_fraction == 1.0


def test_a_truncated_chunk_fails_carrying_the_narrower_chunk_days_verbatim_rather_than_re_deriving_it() -> None:
    # `backfill._truncated_chunk_reason` already names the retry width. Re-deriving it here would let the
    # text in `last_error_summary` drift from the text the CLI prints for the same chunk.
    reason = (
        "2026-01-06..2026-01-11 produced 23651 records against a 50000-record cap, so 3651 were dropped. "
        "Re-walk it with --chunk-days 4, or raise INGEST_MAX_SOURCE_RECORDS (ceiling 50000)."
    )
    truncated = IngestionJobResult(
        source=TEST_LANE.source_name,
        status="failed",
        records_seen=23_651,
        records_written=0,
        truncated=True,
        reason=reason,
    )

    outcome = backfill_outcome(truncated, next_cursor=None, progress_fraction=1.0, metrics={})

    assert outcome.kind == "failed"
    assert outcome.failure_class == TRUNCATED_FAILURE_CLASS
    assert outcome.reason == reason


def test_a_skipped_chunk_fails_rather_than_completing_because_a_skip_wrote_nothing() -> None:
    # An unconfigured INGEST_BBOX and a source's typed history refusal are both skips to
    # `run_source_backfill`, and both mean this window wrote nothing. Completing it is the silent hole.
    skipped = skipped_result(TEST_LANE.source_name, "INGEST_BBOX is not configured")

    outcome = backfill_outcome(skipped, next_cursor=None, progress_fraction=1.0, metrics={})

    assert outcome.kind == "failed"
    assert outcome.failure_class == SKIPPED_FAILURE_CLASS
    assert outcome.reason == "INGEST_BBOX is not configured"


def test_an_upstream_failure_never_advances_the_cursor_even_when_more_chunks_remain() -> None:
    failed = IngestionJobResult(
        source=TEST_LANE.source_name,
        status="failed",
        records_seen=0,
        records_written=0,
        reason="ConnectError",
    )

    outcome = backfill_outcome(
        failed,
        next_cursor={CURSOR_NEXT_CHUNK_START: "2026-01-07T00:00:00+00:00"},
        progress_fraction=0.2,
        metrics={},
    )

    assert outcome.kind == "failed"
    assert outcome.failure_class == UPSTREAM_FAILURE_CLASS
    assert outcome.cursor is None


@pytest.mark.parametrize("status", ["failed", "skipped"])
def test_no_status_other_than_ingested_can_ever_produce_a_completed_outcome(status: str) -> None:
    result = IngestionJobResult(
        source=TEST_LANE.source_name,
        status=status,  # type: ignore[arg-type]
        records_seen=0,
        records_written=0,
        reason="whatever went wrong",
    )

    assert backfill_outcome(result, next_cursor=None, progress_fraction=1.0, metrics={}).kind == "failed"


# ------------------------------------------------------- ingested is not on its own enough to advance


def test_a_chunk_that_fetched_records_and_rejected_every_one_fails_rather_than_reporting_it_ingested() -> None:
    # THE BASH BUG REINCARNATED ONE LAYER UP, and the case 142 green tests missed. FIRMS answers with its
    # plain-text `Invalid MAP_KEY` body under HTTP 200 and a renamed CSV column parses to nothing, so
    # `fetch_history` yields 21,000 rows, `select_writes` rejects all 21,000, writes nothing, and
    # `_run_backfill_chunk` still returns status="ingested" because only a bitten record cap fails it.
    # Left unguarded, all 298 FIRMS windows march to SUCCEEDED with progress_fraction=1 having written
    # zero rows, the run reports succeeded, and the completeness report sees no lane problem at all.
    rejected_everything = IngestionJobResult(
        source=TEST_LANE.source_name,
        status="ingested",
        records_seen=21_000,
        records_written=0,
        truncated=False,
        details={"rejected": 21_000, "dropped": 0},
    )

    outcome = backfill_outcome(rejected_everything, next_cursor=None, progress_fraction=1.0, metrics={})

    assert outcome.kind == "failed"
    assert outcome.failure_class == TOTAL_REJECTION_FAILURE_CLASS
    assert "21000" in (outcome.reason or "")


def test_a_total_rejection_fails_the_chunk_even_when_more_chunks_of_the_window_remain() -> None:
    rejected_everything = _ingested(records_seen=8_400, records_written=0, rejected=8_400)

    outcome = backfill_outcome(
        rejected_everything,
        next_cursor={CURSOR_NEXT_CHUNK_START: "2026-01-07T00:00:00+00:00"},
        progress_fraction=0.2,
        metrics={},
    )

    assert outcome.kind == "failed"
    assert outcome.failure_class == TOTAL_REJECTION_FAILURE_CLASS
    assert outcome.cursor is None


def test_a_window_whose_upstream_published_nothing_still_completes_because_zero_seen_is_not_zero_written() -> None:
    # FIRMS publishes nothing for a day no product covers and a winter day genuinely holds no detections.
    # Failing those would turn most of the archive red and teach an operator to ignore the colour.
    empty = _ingested(records_seen=0, records_written=0)

    assert backfill_outcome(empty, next_cursor=None, progress_fraction=1.0, metrics={}).kind == "completed"


def test_a_re_walk_that_writes_nothing_because_every_row_already_landed_still_completes() -> None:
    # `FeatureWriter` returns rows inserted plus genuinely-CHANGED refreshes, so a window walked twice
    # writes zero rows the second time on purpose -- which is precisely what makes the floor-anchored
    # grid free to replan. Keying the guard on records_written rather than on rejected would turn every
    # idempotent replan permanently red.
    replayed = _ingested(records_seen=8_400, records_written=0, rejected=0)

    assert backfill_outcome(replayed, next_cursor=None, progress_fraction=1.0, metrics={}).kind == "completed"


def test_a_chunk_that_rejected_most_but_not_all_of_its_records_still_advances() -> None:
    # Partial rejection is ordinary: FIRMS ships detections outside the bbox and rows with no acquisition
    # instant. Some write was produced, so the writer was handed something and the chunk did walk.
    partial = _ingested(records_seen=1_000, records_written=1, rejected=999)

    assert backfill_outcome(partial, next_cursor=None, progress_fraction=1.0, metrics={}).kind == "completed"


# --------------------------------------------------------------------------- the per-chunk time budget


def test_a_window_that_has_measured_nothing_yet_is_held_to_the_worst_chunk_ever_observed() -> None:
    assert chunk_budget_seconds(None) == ARCHIVE_WALK_WORST_CHUNK_SECONDS
    assert chunk_budget_seconds({CURSOR_RECORDS_SEEN: 5}) == ARCHIVE_WALK_WORST_CHUNK_SECONDS


def test_a_window_estimates_its_next_chunk_from_the_slowest_chunk_it_has_already_walked() -> None:
    # The chunks of one window are consecutive days of one season, so the window's own measurement is the
    # honest predictor. Holding every chunk to the archive-wide 690s worst case instead would fit exactly
    # one chunk in a 780s budget, and FIRMS' 298 windows are 1,490 chunks.
    assert chunk_budget_seconds({CURSOR_SLOWEST_CHUNK_SECONDS: 40}) == 40 * ARCHIVE_WALK_CHUNK_ESTIMATE_MARGIN


def test_a_chunk_estimate_is_never_slower_than_the_worst_chunk_ever_measured() -> None:
    assert chunk_budget_seconds({CURSOR_SLOWEST_CHUNK_SECONDS: 600}) == ARCHIVE_WALK_WORST_CHUNK_SECONDS


def test_a_windows_first_chunk_only_fits_a_tick_that_has_barely_started() -> None:
    # 780 - 690 = 90: a window with nothing measured is only ever started inside the first 90 seconds of
    # a tick, so a 690s chunk there lands ON the budget rather than 690s past it.
    assert ARCHIVE_WALK_TIME_BUDGET_SECONDS - ARCHIVE_WALK_WORST_CHUNK_SECONDS == 90


# --------------------------------------------------------------------------- planning


def test_a_lane_fans_out_one_work_item_per_window_newest_first() -> None:
    items = archive_lane_work_items(TEST_LANE, end=WALK_END)

    assert [item.shard_key for item in items] == [
        "test-archive:2026-01-06..2026-01-11",
        "test-archive:2026-01-01..2026-01-06",
    ]
    assert all(item.kind == ARCHIVE_WALK_WORK_ITEM_KIND for item in items)


def test_the_newest_window_carries_the_highest_priority_so_the_claims_priority_desc_walks_backward() -> None:
    assert [item.priority for item in archive_lane_work_items(TEST_LANE, end=WALK_END)] == [1, 0]


def test_replanning_the_same_lane_on_the_same_day_produces_an_identical_set_of_shards() -> None:
    first = archive_lane_work_items(TEST_LANE, end=WALK_END)
    again = archive_lane_work_items(TEST_LANE, end=WALK_END)

    assert [item.shard_key for item in first] == [item.shard_key for item in again]
    assert [item.payload for item in first] == [item.payload for item in again]


def test_replanning_within_the_same_utc_day_is_a_no_op_even_at_a_different_hour() -> None:
    morning = archive_lane_work_items(TEST_LANE, end=datetime(2026, 1, 11, 3, 14, tzinfo=UTC))
    evening = archive_lane_work_items(TEST_LANE, end=datetime(2026, 1, 11, 22, 59, tzinfo=UTC))

    assert [item.shard_key for item in morning] == [item.shard_key for item in evening]


def test_no_two_windows_of_one_plan_share_a_shard_key_so_none_vanishes_into_do_nothing() -> None:
    shard_keys = [item.shard_key for item in archive_lane_work_items(FIRMS_ARCHIVE_LANE, end=WALK_END)]

    assert len(shard_keys) == len(set(shard_keys))


def test_replanning_after_the_walk_end_advances_leaves_every_already_planned_shard_untouched() -> None:
    first = {item.shard_key for item in archive_lane_work_items(TEST_LANE, end=WALK_END)}
    later = {item.shard_key for item in archive_lane_work_items(TEST_LANE, end=datetime(2026, 1, 21, tzinfo=UTC))}

    assert first <= later


def test_the_run_key_carries_the_floor_so_a_deeper_walk_cannot_land_in_a_finished_run() -> None:
    # The bash `.done` sentinel recorded that a walk had completed but not the floor it completed at,
    # which is the sole reason firms-archive-full.sh exists: to delete that file before going deeper.
    assert archive_lane_run_key(FIRMS_ARCHIVE_LANE) == "archive-walk:firms-archive:2000-11-01"

    shallower = BackfillLane(
        name=FIRMS_ARCHIVE_LANE.name,
        source_name=FIRMS_ARCHIVE_LANE.source_name,
        floor=datetime(2022, 8, 5, tzinfo=UTC),
        chunk_days=FIRMS_ARCHIVE_LANE.chunk_days,
        window_days=FIRMS_ARCHIVE_LANE.window_days,
    )
    assert archive_lane_run_key(shallower) != archive_lane_run_key(FIRMS_ARCHIVE_LANE)


async def test_planning_a_lane_declares_it_and_opens_exactly_one_logically_keyed_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel_definition = object()
    declared: list[object] = []
    opened: dict[str, object] = {}
    fanned_out: list[JobWorkItemSpec] = []

    async def fake_ensure(_session: object, spec: object) -> object:
        declared.append(spec)
        return sentinel_definition

    async def fake_open(
        _session: object,
        definition: object,
        *,
        work_items: Sequence[JobWorkItemSpec],
        **rest: object,
    ) -> str:
        opened["definition"] = definition
        opened.update(rest)
        fanned_out.extend(work_items)
        return "opened-run"

    monkeypatch.setattr(archive_walk_module, "ensure_job_definition", fake_ensure)
    monkeypatch.setattr(archive_walk_module, "open_job_run", fake_open)

    result = await plan_archive_lane(None, TEST_LANE, end=WALK_END, bbox="-125,42,-111,49")  # type: ignore[arg-type]

    assert result == "opened-run"
    assert declared == [archive_lane_definition_spec(TEST_LANE)]
    assert opened["definition"] is sentinel_definition
    assert opened["logical_run_key"] == "archive-walk:test-archive:2026-01-01"
    assert opened["target_partitions"] == {"lane": "test-archive", "floor": "2026-01-01", "bbox": "-125,42,-111,49"}
    assert [item.shard_key for item in fanned_out] == [
        "test-archive:2026-01-06..2026-01-11",
        "test-archive:2026-01-01..2026-01-06",
    ]


# --------------------------------------------------------------------------- definition shape


def test_the_definition_leases_longer_than_one_slice_budget_and_longer_than_the_worst_measured_chunk() -> None:
    spec = archive_lane_definition_spec(FIRMS_ARCHIVE_LANE)

    # 690s is the measured 2026-07-23 peak-season FIRMS chunk. A lease shorter than one chunk fences the
    # live worker out of its own shard, because the lease cannot be extended inside `fetch_history`.
    assert spec.lease_seconds == ARCHIVE_WALK_LEASE_SECONDS
    assert spec.lease_seconds > 690
    assert spec.time_budget_seconds == ARCHIVE_WALK_TIME_BUDGET_SECONDS
    assert spec.lease_seconds > spec.time_budget_seconds
    assert spec.max_attempts == ARCHIVE_WALK_MAX_ATTEMPTS
    assert spec.handler == ARCHIVE_WALK_HANDLER_TOKEN
    assert spec.name == "agri.ingest.archive_walk.firms-archive"
    assert spec.parameters == FIRMS_ARCHIVE_LANE.to_parameters()


def test_the_lane_definition_name_is_the_single_spelling_of_the_token_a_run_is_written_under() -> None:
    # `archive_lane_definition_name` is the PRODUCER. Anything that reads the ledger back -- a
    # completeness report, an operator query, a `--definition` argument -- must call it rather than spell
    # the token, because a second hard-coded spelling joins to nothing, and a completeness report that
    # joins to nothing reports no missing shards, which is the same silence as a walk that never ran.
    assert archive_lane_definition_name(FIRMS_ARCHIVE_LANE) == "agri.ingest.archive_walk.firms-archive"

    for lane in BACKFILL_LANES.values():
        assert archive_lane_definition_spec(lane).name == archive_lane_definition_name(lane)
        assert archive_lane_definition_name(lane).endswith(f".{lane.name}")


def test_each_lane_gets_its_own_definition_and_its_own_concurrency_key() -> None:
    specs = [archive_lane_definition_spec(lane) for lane in BACKFILL_LANES.values()]

    assert len({spec.name for spec in specs}) == len(specs)
    assert len({spec.concurrency_key for spec in specs}) == len(specs)


def test_the_handler_is_bound_to_its_token_in_the_process_registry() -> None:
    assert ARCHIVE_WALK_HANDLER_TOKEN in JOB_HANDLERS
    assert JOB_HANDLERS.handler_for(ARCHIVE_WALK_HANDLER_TOKEN) is archive_walk_handler


def test_every_lane_walks_a_source_this_module_builds_and_the_backfill_cli_also_resolves() -> None:
    # Two registries that must not drift: this module builds its own so the CLI can import it without a
    # cycle, and a lane pointing at a source only one of them knows plans windows that can never run.
    assert set(archive_sources()) <= set(_build_backfillable_sources())
    for lane in BACKFILL_LANES.values():
        assert archive_source(lane).source_name == lane.source_name


# --------------------------------------------------------------------------- the record cap


def test_the_record_cap_is_pinned_to_the_lanes_declared_ceiling_for_the_walk_and_restored_after() -> None:
    # The environment IS the seam: `resolve_max_source_records` reads it at call time and
    # `run_source_backfill` builds its own FetchRequest from it, so a walk under the 10,000 default would
    # report `ingested` for a chunk whose earliest days the cap had deleted whole.
    with pinned_source_record_cap(FIRMS_ARCHIVE_LANE) as ceiling:
        assert ceiling == 50_000
        assert os.environ[MAX_SOURCE_RECORDS_VARIABLE] == "50000"

    assert MAX_SOURCE_RECORDS_VARIABLE not in os.environ


def test_the_record_cap_pin_restores_an_operators_own_value_rather_than_deleting_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(MAX_SOURCE_RECORDS_VARIABLE, "12000")

    with pinned_source_record_cap(FIRMS_ARCHIVE_LANE):
        assert os.environ[MAX_SOURCE_RECORDS_VARIABLE] == "50000"

    assert os.environ[MAX_SOURCE_RECORDS_VARIABLE] == "12000"


# --------------------------------------------------------------------------- the handler


async def test_a_walk_with_no_bound_write_path_refuses_rather_than_writing_nothing_quietly() -> None:
    with pytest.raises(ArchiveWalkContextError, match="archive walk context"):
        await walk_archive_chunk(TEST_LANE, NEWEST_WINDOW)


async def test_the_handler_walks_one_chunk_and_reports_where_the_next_one_starts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    walked: list[HistoryWindow] = []

    async def fake_backfill(_source: object, _write: object, plan: object) -> list[IngestionJobResult]:
        walked.append(plan.window)  # type: ignore[attr-defined]
        return [_ingested(records_seen=7, records_written=5)]

    _use_lane(monkeypatch, TEST_LANE)
    monkeypatch.setattr(archive_walk_module, "run_source_backfill", fake_backfill)

    async with archive_walk_context(ArchiveWalkContext(write_features=RecordingWriter(), bbox="-125,42,-111,49")):
        outcome = await archive_walk_handler(_invocation())

    # One handler call is exactly one chunk; the runtime, not this module, decides whether to call again.
    assert len(walked) == 1
    assert walked[0].start == datetime(2026, 1, 6, tzinfo=UTC)
    assert outcome.kind == "progressed"
    assert outcome.cursor is not None
    assert outcome.cursor[CURSOR_NEXT_CHUNK_START] == "2026-01-07T00:00:00+00:00"
    assert outcome.cursor[CURSOR_RECORDS_SEEN] == 7
    assert outcome.cursor[CURSOR_RECORDS_WRITTEN] == 5
    assert outcome.cursor[CURSOR_RECORDS_REJECTED] == 0
    assert outcome.progress_fraction == 0.2


async def test_a_reopened_window_restarts_at_its_first_chunk_instead_of_finishing_the_old_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The exact hole `jobs-plan-gaps` would otherwise write: the shard is back in `queued`, but its stored
    # checkpoint still names the last chunk of the pass that succeeded over nothing.
    walked: list[HistoryWindow] = []

    async def fake_backfill(_source: object, _write: object, plan: object) -> list[IngestionJobResult]:
        walked.append(plan.window)  # type: ignore[attr-defined]
        return [_ingested()]

    _use_lane(monkeypatch, TEST_LANE)
    monkeypatch.setattr(archive_walk_module, "run_source_backfill", fake_backfill)
    chunks = history_chunks(NEWEST_WINDOW, TEST_LANE.chunk)
    stale_cursor: dict[str, object] = {
        CURSOR_NEXT_CHUNK_START: chunks[-1].start.isoformat(),
        CURSOR_RECORDS_SEEN: 900,
        CURSOR_RECORDS_WRITTEN: 900,
    }

    async with archive_walk_context(ArchiveWalkContext(write_features=RecordingWriter())):
        outcome = await archive_walk_handler(_invocation(cursor=stale_cursor, payload_generation=1))

    assert walked[0].start == chunks[0].start
    assert outcome.kind == "progressed"
    assert outcome.cursor is not None
    assert outcome.cursor[CURSOR_NEXT_CHUNK_START] == chunks[1].start.isoformat()
    # The superseded pass's running totals go with it; carrying them over would report 903 records for a
    # window that has walked one chunk.
    assert outcome.cursor[CURSOR_RECORDS_SEEN] == 3
    # And the new cursor states which generation it belongs to, so the next claim can make the same call.
    assert outcome.cursor[CURSOR_WALK_GENERATION] == 1


async def test_the_handler_completes_the_window_only_after_its_last_chunk(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_backfill(_source: object, _write: object, _plan: object) -> list[IngestionJobResult]:
        return [_ingested(records_seen=2, records_written=2)]

    _use_lane(monkeypatch, TEST_LANE)
    monkeypatch.setattr(archive_walk_module, "run_source_backfill", fake_backfill)
    chunks = history_chunks(NEWEST_WINDOW, TEST_LANE.chunk)
    last_cursor: dict[str, object] = {
        CURSOR_NEXT_CHUNK_START: chunks[-1].start.isoformat(),
        CURSOR_RECORDS_SEEN: 8,
        CURSOR_RECORDS_WRITTEN: 8,
    }

    async with archive_walk_context(ArchiveWalkContext(write_features=RecordingWriter())):
        outcome = await archive_walk_handler(_invocation(cursor=last_cursor))

    assert outcome.kind == "completed"
    # The running totals survive every attempt of the window, so the closing metrics describe the whole
    # window rather than only its final chunk.
    assert outcome.metrics["window_records_written"] == 10
    assert outcome.metrics["chunks_walked"] == 5


async def test_a_chunk_that_bit_the_record_cap_fails_the_window_instead_of_advancing_past_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_backfill(_source: object, _write: object, _plan: object) -> list[IngestionJobResult]:
        return [
            IngestionJobResult(
                source=TEST_LANE.source_name,
                status="failed",
                records_seen=23_651,
                records_written=0,
                truncated=True,
                reason="Re-walk it with --chunk-days 1, or raise INGEST_MAX_SOURCE_RECORDS (ceiling 50000).",
                details={"dropped": 3_651},
            )
        ]

    _use_lane(monkeypatch, TEST_LANE)
    monkeypatch.setattr(archive_walk_module, "run_source_backfill", fake_backfill)

    async with archive_walk_context(ArchiveWalkContext(write_features=RecordingWriter())):
        outcome = await archive_walk_handler(_invocation())

    assert outcome.kind == "failed"
    assert outcome.failure_class == TRUNCATED_FAILURE_CLASS
    assert "--chunk-days 1" in (outcome.reason or "")
    assert outcome.cursor is None


async def test_a_lane_missing_its_credential_fails_the_window_by_name_rather_than_completing_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_lane(monkeypatch, KEYED_LANE)

    async with archive_walk_context(ArchiveWalkContext(write_features=RecordingWriter())):
        outcome = await archive_walk_handler(_invocation(lane_name=KEYED_LANE.name))

    assert outcome.kind == "failed"
    assert outcome.failure_class == MISSING_CREDENTIAL_FAILURE_CLASS
    assert FIRMS_API_KEY_VARIABLE in (outcome.reason or "")


async def test_a_fenced_out_worker_stops_before_fetching_rather_than_writing_under_a_dead_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_backfill(_source: object, _write: object, _plan: object) -> list[IngestionJobResult]:
        raise AssertionError("a fenced-out worker must not fetch")

    _use_lane(monkeypatch, TEST_LANE)
    monkeypatch.setattr(archive_walk_module, "run_source_backfill", fake_backfill)

    async with archive_walk_context(ArchiveWalkContext(write_features=RecordingWriter())):
        outcome = await archive_walk_handler(_invocation(fence_held=False))

    assert outcome.kind == "failed"


async def test_the_handler_refuses_to_advance_a_window_whose_chunk_rejected_every_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The end-to-end shape of the same bug: the chunk reports `ingested`, the runtime would checkpoint,
    # and on the last chunk the window would land SUCCEEDED with progress_fraction=1 and no rows behind
    # it. The handler already computed `window_records_written`; now it acts on what it computed.
    async def fake_backfill(_source: object, _write: object, _plan: object) -> list[IngestionJobResult]:
        return [
            IngestionJobResult(
                source=TEST_LANE.source_name,
                status="ingested",
                records_seen=21_000,
                records_written=0,
                truncated=False,
                details={"rejected": 21_000, "dropped": 0},
            )
        ]

    _use_lane(monkeypatch, TEST_LANE)
    monkeypatch.setattr(archive_walk_module, "run_source_backfill", fake_backfill)

    async with archive_walk_context(ArchiveWalkContext(write_features=RecordingWriter())):
        outcome = await archive_walk_handler(_invocation())

    assert outcome.kind == "failed"
    assert outcome.failure_class == TOTAL_REJECTION_FAILURE_CLASS
    assert outcome.cursor is None
    assert outcome.metrics["window_records_rejected"] == 21_000


async def test_the_handler_declines_to_start_a_chunk_this_tick_cannot_finish_and_parks_the_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # `worker.py::_drive_work_item` tests its deadline BEFORE calling the handler and a handler call is
    # uninterruptible, so a tick that tests at deadline-epsilon, passes, and starts a 690s chunk runs
    # 690s past its own budget and straight into the next */15 tick. The decision to start a chunk is the
    # only place the budget can be honoured, so the handler makes it.
    async def fake_backfill(_source: object, _write: object, _plan: object) -> list[IngestionJobResult]:
        raise AssertionError("a tick with no budget left must not start a chunk")

    _use_lane(monkeypatch, TEST_LANE)
    monkeypatch.setattr(archive_walk_module, "run_source_backfill", fake_backfill)

    async with archive_walk_context(ArchiveWalkContext(write_features=RecordingWriter())):
        outcome = await archive_walk_handler(_invocation(seconds_remaining=12.0))

    # Yielded, not failed and not deferred: nothing went wrong, and the runtime parks a yield on the same
    # `defer_work_item` primitive, which raises max_attempts alongside parking the item so the wait costs
    # none of the eight attempts the connect failures need. The kind is what separates "this tick ran out
    # of clock" from "upstream had nothing to give" in `summary.yielded` versus `summary.deferred`.
    assert outcome.kind == "yielded"
    assert BUDGET_STOP_REASON in (outcome.reason or "")


async def test_a_budget_stop_pins_no_resume_time_because_the_runtime_ends_the_slice_on_a_yield(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # This used to park past the tick's own deadline: `run_job_slice` claimed until its deadline rather
    # than until a park, so a shard parked to now() was immediately claimable by the same loop and spun
    # against its own refusal for the rest of the tick. A yield now ENDS the slice
    # (stop_reason="time_budget_exhausted"), so there is no loop left to respin it -- and
    # `JobHandlerOutcome` REFUSES a resume time on a yield, so the old arithmetic would now raise.
    async def fake_backfill(_source: object, _write: object, _plan: object) -> list[IngestionJobResult]:
        raise AssertionError("a tick with no budget left must not start a chunk")

    _use_lane(monkeypatch, TEST_LANE)
    monkeypatch.setattr(archive_walk_module, "run_source_backfill", fake_backfill)

    async with archive_walk_context(ArchiveWalkContext(write_features=RecordingWriter())):
        outcome = await archive_walk_handler(_invocation(seconds_remaining=45.0))

    assert outcome.kind == "yielded"
    assert outcome.resume_at is None


async def test_a_budget_stop_reports_what_the_window_has_already_walked_so_a_parked_shard_is_not_blank(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A window that straddles several ticks parks on the clock more often than it finishes, so an attempt
    # closed by a yield is the row an operator reads most. Reporting nothing on it would make the running
    # totals visible only on the one tick that happens to finish the window.
    async def fake_backfill(_source: object, _write: object, _plan: object) -> list[IngestionJobResult]:
        raise AssertionError("a tick with no budget left must not start a chunk")

    _use_lane(monkeypatch, TEST_LANE)
    monkeypatch.setattr(archive_walk_module, "run_source_backfill", fake_backfill)
    chunks = history_chunks(NEWEST_WINDOW, TEST_LANE.chunk)
    walked: dict[str, object] = {
        CURSOR_NEXT_CHUNK_START: chunks[2].start.isoformat(),
        CURSOR_RECORDS_SEEN: 4_100,
        CURSOR_RECORDS_WRITTEN: 3_900,
        CURSOR_RECORDS_REJECTED: 200,
        CURSOR_SLOWEST_CHUNK_SECONDS: 300,
    }

    async with archive_walk_context(ArchiveWalkContext(write_features=RecordingWriter())):
        outcome = await archive_walk_handler(_invocation(cursor=walked, seconds_remaining=45.0))

    assert outcome.kind == "yielded"
    assert outcome.metrics["chunks_walked"] == 2
    assert outcome.metrics["chunks_total"] == len(chunks)
    assert outcome.metrics["window_records_seen"] == 4_100
    assert outcome.metrics["window_records_written"] == 3_900
    assert outcome.metrics["window_records_rejected"] == 200
    # No chunk ran, so the two keys only a walked chunk can fill are absent rather than zero -- the
    # runtime merges metrics key-wise, so a zero here would clobber the last real measurement.
    assert "chunk_seconds" not in outcome.metrics


async def test_a_tick_with_room_for_a_measured_chunk_walks_it_even_though_the_worst_case_would_not_fit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The throughput half of the same rule: a window that has already walked a 20-second chunk is not
    # held to the peak-fire-season 690s worst case, or every one of FIRMS' 1,490 chunks would cost a
    # whole */15 tick and the archive would take a year.
    walked: list[HistoryWindow] = []

    async def fake_backfill(_source: object, _write: object, plan: object) -> list[IngestionJobResult]:
        walked.append(plan.window)  # type: ignore[attr-defined]
        return [_ingested()]

    _use_lane(monkeypatch, TEST_LANE)
    monkeypatch.setattr(archive_walk_module, "run_source_backfill", fake_backfill)
    chunks = history_chunks(NEWEST_WINDOW, TEST_LANE.chunk)
    measured: dict[str, object] = {
        CURSOR_NEXT_CHUNK_START: chunks[1].start.isoformat(),
        CURSOR_SLOWEST_CHUNK_SECONDS: 20,
    }

    async with archive_walk_context(ArchiveWalkContext(write_features=RecordingWriter())):
        outcome = await archive_walk_handler(_invocation(cursor=measured, seconds_remaining=60.0))

    assert len(walked) == 1
    assert outcome.kind == "progressed"


async def test_a_walked_chunk_records_its_own_wall_time_so_the_next_one_is_estimated_from_measurement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_backfill(_source: object, _write: object, _plan: object) -> list[IngestionJobResult]:
        return [_ingested()]

    _use_lane(monkeypatch, TEST_LANE)
    monkeypatch.setattr(archive_walk_module, "run_source_backfill", fake_backfill)

    async with archive_walk_context(ArchiveWalkContext(write_features=RecordingWriter())):
        outcome = await archive_walk_handler(_invocation())

    assert outcome.cursor is not None
    # The measurement lives on the cursor and not in a process variable, because a window straddles ticks
    # and every tick is a fresh one-shot container.
    assert outcome.cursor[CURSOR_SLOWEST_CHUNK_SECONDS] >= 1


async def test_a_window_keeps_the_slowest_chunk_it_ever_walked_rather_than_only_the_latest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_backfill(_source: object, _write: object, _plan: object) -> list[IngestionJobResult]:
        return [_ingested(records_seen=9, records_written=4, rejected=5)]

    _use_lane(monkeypatch, TEST_LANE)
    monkeypatch.setattr(archive_walk_module, "run_source_backfill", fake_backfill)
    chunks = history_chunks(NEWEST_WINDOW, TEST_LANE.chunk)
    measured: dict[str, object] = {
        CURSOR_NEXT_CHUNK_START: chunks[1].start.isoformat(),
        CURSOR_RECORDS_REJECTED: 11,
        CURSOR_SLOWEST_CHUNK_SECONDS: 400,
    }

    async with archive_walk_context(ArchiveWalkContext(write_features=RecordingWriter())):
        outcome = await archive_walk_handler(_invocation(cursor=measured))

    assert outcome.cursor is not None
    assert outcome.cursor[CURSOR_SLOWEST_CHUNK_SECONDS] == 400
    assert outcome.cursor[CURSOR_RECORDS_REJECTED] == 16


async def test_the_walk_runs_under_the_lanes_record_ceiling_and_hands_the_chunk_its_own_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    async def fake_backfill(_source: object, _write: object, plan: object) -> list[IngestionJobResult]:
        observed["cap"] = os.environ.get(MAX_SOURCE_RECORDS_VARIABLE)
        observed["chunk"] = plan.chunk  # type: ignore[attr-defined]
        observed["bbox"] = plan.bbox  # type: ignore[attr-defined]
        return [_ingested()]

    _use_lane(monkeypatch, TEST_LANE)
    monkeypatch.setattr(archive_walk_module, "run_source_backfill", fake_backfill)

    async with archive_walk_context(ArchiveWalkContext(write_features=RecordingWriter(), bbox="-125,42,-111,49")):
        await archive_walk_handler(_invocation())

    assert observed["cap"] == "50000"
    assert observed["chunk"] == TEST_LANE.chunk
    assert observed["bbox"] == "-125,42,-111,49"

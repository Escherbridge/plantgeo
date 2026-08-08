"""The declarative archive-lane registry: the measured walk shape, and the floor-anchored newest-first grid."""

# ruff: noqa: PLR2004

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agri_data_service.ingest.backfill import history_chunks
from agri_data_service.ingest.commands import _build_backfillable_sources
from agri_data_service.ingest.firms import FIRMS_API_KEY_VARIABLE, FIRMS_ARCHIVE_SOURCE
from agri_data_service.ingest.lanes import (
    ARCHIVE_MAX_SOURCE_RECORDS,
    BACKFILL_LANES,
    FIRMS_ARCHIVE_LANE,
    STREAMFLOW_ARCHIVE_LANE,
    BackfillLane,
    LaneSpecificationError,
    UnknownBackfillLaneError,
    lane_windows,
    resolve_lane,
    utc_day_start,
)
from agri_data_service.ingest.usgs_nwis import USGS_STREAMFLOW_ARCHIVE_SOURCE

# A synthetic lane with a shallow floor, so a whole grid fits in an assertion. The real lanes' floors are
# 2000-11-01 and 2022-08-05 and would expand to thousands of windows.
TEST_FLOOR = datetime(2026, 1, 1, tzinfo=UTC)


def _lane(*, chunk_days: int = 1, window_days: int = 5, floor: datetime = TEST_FLOOR) -> BackfillLane:
    return BackfillLane(
        name="test-archive",
        source_name=FIRMS_ARCHIVE_SOURCE,
        floor=floor,
        chunk_days=chunk_days,
        window_days=window_days,
    )


@pytest.fixture(autouse=True)
def _clear_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for variable in (FIRMS_API_KEY_VARIABLE, "INGEST_BBOX", "INGEST_MAX_SOURCE_RECORDS"):
        monkeypatch.delenv(variable, raising=False)


def test_a_lane_expands_its_newest_window_first_because_the_slider_extends_only_the_newest_cluster() -> None:
    windows = lane_windows(_lane(), datetime(2026, 1, 16, tzinfo=UTC))

    assert [window.label for window in windows] == [
        "2026-01-11..2026-01-16",
        "2026-01-06..2026-01-11",
        "2026-01-01..2026-01-06",
    ]


def test_the_newest_window_carries_the_highest_grid_index_so_priority_desc_walks_backward() -> None:
    windows = lane_windows(_lane(), datetime(2026, 1, 16, tzinfo=UTC))

    assert [window.grid_index for window in windows] == [2, 1, 0]


def test_the_oldest_window_starts_exactly_on_the_floor_and_never_below_it() -> None:
    windows = lane_windows(_lane(), datetime(2026, 1, 14, tzinfo=UTC))

    assert windows[-1].window.start == TEST_FLOOR
    assert all(window.window.start >= TEST_FLOOR for window in windows)


def test_the_trailing_days_above_the_newest_whole_window_are_not_planned_at_all() -> None:
    # 13 days above the floor at 5 days per window is two whole windows plus a 3-day remainder, and the
    # remainder is deliberately dropped. A partial window would have to end at the boundary, so its shard
    # key would move every calendar day while its start stood still -- a NEW shard alongside yesterday's
    # rather than a replacement, because DO NOTHING has nothing to match. The forward hourly cron owns
    # those days until the grid passes them.
    windows = lane_windows(_lane(), datetime(2026, 1, 14, tzinfo=UTC))

    assert [window.label for window in windows] == ["2026-01-06..2026-01-11", "2026-01-01..2026-01-06"]


def test_planning_the_same_lane_on_three_consecutive_days_never_grows_the_shard_key_set() -> None:
    # The regression this exists for: with a trailing partial window, planning on 01-12, 01-13 and 01-14
    # minted 01-11..01-12, 01-11..01-13 and 01-11..01-14 -- three shards for one period, each carrying
    # the HIGHEST grid index on the grid, and the claim's `ORDER BY priority DESC` would serve the newest
    # of them ahead of every window of the backlog on every single tick.
    lane = _lane()
    plans = [
        {window.label for window in lane_windows(lane, datetime(2026, 1, day, tzinfo=UTC))} for day in (12, 13, 14)
    ]

    assert plans[0] == plans[1] == plans[2]
    assert len(plans[0]) == 2


def test_a_replan_that_crosses_a_grid_boundary_adds_exactly_that_window_and_re_keys_nothing() -> None:
    lane = _lane()
    before = {window.label for window in lane_windows(lane, datetime(2026, 1, 15, tzinfo=UTC))}
    after = {window.label for window in lane_windows(lane, datetime(2026, 1, 16, tzinfo=UTC))}

    assert before < after
    assert after - before == {"2026-01-11..2026-01-16"}


def test_every_planned_window_ends_on_the_fixed_grid_so_no_shard_key_moves_with_the_calendar() -> None:
    lane = _lane()

    for boundary_day in (12, 13, 14, 15, 16):
        for window in lane_windows(lane, datetime(2026, 1, boundary_day, tzinfo=UTC)):
            assert (window.window.start - lane.floor) % lane.window == timedelta(0)
            assert window.window.end - window.window.start == lane.window


def test_the_grid_is_anchored_at_the_floor_so_a_later_end_leaves_every_finished_window_untouched() -> None:
    lane = _lane()
    first = {window.label for window in lane_windows(lane, datetime(2026, 1, 11, tzinfo=UTC))}
    later = {window.label for window in lane_windows(lane, datetime(2026, 1, 21, tzinfo=UTC))}

    # The whole point of the anchoring: a replan is a superset, so `ON CONFLICT (job_run_id, shard_key)
    # DO NOTHING` skips every window that already exists instead of planning the archive twice.
    assert first <= later


def test_a_floor_equal_to_the_walk_end_owes_no_windows() -> None:
    assert lane_windows(_lane(), TEST_FLOOR) == ()


def test_an_end_below_the_floor_owes_no_windows_rather_than_walking_backward_past_it() -> None:
    assert lane_windows(_lane(), TEST_FLOOR - timedelta(days=30)) == ()


def test_a_lane_owes_nothing_until_its_first_whole_window_is_complete() -> None:
    # One day above the floor is not a window. Planning it would key a shard `2026-01-01..2026-01-02`
    # that tomorrow's replan cannot correct and tomorrow's shard would sit beside rather than replace.
    assert lane_windows(_lane(), TEST_FLOOR + timedelta(days=1)) == ()
    assert lane_windows(_lane(), TEST_FLOOR + timedelta(days=4)) == ()

    first = lane_windows(_lane(), TEST_FLOOR + timedelta(days=5))
    assert [window.label for window in first] == ["2026-01-01..2026-01-06"]


def test_a_walk_boundary_is_truncated_to_its_utc_day_so_two_plans_in_one_day_are_one_grid() -> None:
    morning = lane_windows(_lane(), datetime(2026, 1, 16, 3, 14, tzinfo=UTC))
    evening = lane_windows(_lane(), datetime(2026, 1, 16, 22, 59, 59, tzinfo=UTC))

    assert morning == evening


def test_a_naive_walk_boundary_is_refused_in_typed_terms() -> None:
    with pytest.raises(LaneSpecificationError, match="timezone"):
        utc_day_start(datetime(2026, 1, 16))  # noqa: DTZ001 - the missing zone is the case under test.


def test_every_window_of_a_lane_is_walked_at_exactly_the_chunk_the_lane_declares() -> None:
    lane = _lane(chunk_days=1, window_days=5)

    for window in lane_windows(lane, datetime(2026, 1, 16, tzinfo=UTC)):
        chunks = history_chunks(window.window, lane.chunk)
        assert all(chunk.end - chunk.start <= lane.chunk for chunk in chunks)
        assert len(chunks) == 5


def test_a_lane_whose_window_is_narrower_than_its_chunk_is_refused_before_it_can_walk_one_wide_chunk() -> None:
    # `history_chunks` clamps its last chunk to the window end, so a window narrower than its chunk
    # yields ONE chunk spanning the whole window -- silently walking wider than the lane declares, which
    # is the exact overrun that made a 5-day July FIRMS chunk see 23,651 records and write zero.
    with pytest.raises(LaneSpecificationError, match="chunk wider than its window"):
        _lane(chunk_days=7, window_days=5)


def test_a_lane_that_fetches_less_than_a_day_per_chunk_is_refused() -> None:
    with pytest.raises(LaneSpecificationError, match="at least one day"):
        _lane(chunk_days=0)


def test_a_naive_lane_floor_is_refused_in_typed_terms() -> None:
    with pytest.raises(LaneSpecificationError, match="timezone"):
        _lane(floor=datetime(2026, 1, 1))  # noqa: DTZ001 - the missing zone is the case under test.


def test_a_floor_that_is_not_a_utc_midnight_is_refused_because_two_windows_would_spell_one_shard_key() -> None:
    with pytest.raises(LaneSpecificationError, match="UTC midnight"):
        _lane(floor=datetime(2026, 1, 1, 6, tzinfo=UTC))


def test_a_lane_may_not_declare_a_ceiling_above_the_one_the_ingest_policy_would_clamp_it_to() -> None:
    with pytest.raises(LaneSpecificationError, match="record ceiling"):
        BackfillLane(
            name="over-ceiling",
            source_name=FIRMS_ARCHIVE_SOURCE,
            floor=TEST_FLOOR,
            chunk_days=1,
            window_days=5,
            max_source_records=ARCHIVE_MAX_SOURCE_RECORDS + 1,
        )


def test_the_firms_lane_carries_the_measured_walk_shape_it_was_tuned_to() -> None:
    # chunk_days=1: measured 2026-08-06, a 5-day July chunk saw 23,651 records against the cap and wrote
    # ZERO, because the cap drops the OLDEST days whole rather than thinning the sample.
    # window_days=5: one 2026-07-23 peak-season chunk took 11.5 minutes.
    # floor 2000-11-01: MODIS_SP's own published min_date, the oldest any FIRMS product offers.
    assert FIRMS_ARCHIVE_LANE.source_name == FIRMS_ARCHIVE_SOURCE
    assert FIRMS_ARCHIVE_LANE.chunk_days == 1
    assert FIRMS_ARCHIVE_LANE.window_days == 5
    assert FIRMS_ARCHIVE_LANE.floor_day == "2000-11-01"
    assert FIRMS_ARCHIVE_LANE.credential_variable == FIRMS_API_KEY_VARIABLE
    assert FIRMS_ARCHIVE_LANE.max_source_records == 50_000


def test_the_streamflow_lane_carries_the_measured_walk_shape_it_was_tuned_to() -> None:
    # chunk_days=10: measured 2026-08-07, the full PNW box yields ~807 gauge-days per day, so ~8,070
    # records per chunk -- comfortably inside the 50,000 ceiling.
    assert STREAMFLOW_ARCHIVE_LANE.source_name == USGS_STREAMFLOW_ARCHIVE_SOURCE
    assert STREAMFLOW_ARCHIVE_LANE.chunk_days == 10
    assert STREAMFLOW_ARCHIVE_LANE.window_days == 30
    assert STREAMFLOW_ARCHIVE_LANE.floor_day == "2022-08-05"
    assert STREAMFLOW_ARCHIVE_LANE.credential_variable is None
    assert STREAMFLOW_ARCHIVE_LANE.max_source_records == 50_000


def test_every_lane_the_registry_declares_walks_a_source_the_backfill_cli_can_actually_resolve() -> None:
    # A lane whose token no `--source` accepts would plan thousands of windows that can never run, and
    # the drift would only show up as a dead-lettered archive months later.
    resolvable = set(_build_backfillable_sources())

    assert {lane.source_name for lane in BACKFILL_LANES.values()} <= resolvable


def test_every_lane_is_registered_under_its_own_name() -> None:
    assert all(name == lane.name for name, lane in BACKFILL_LANES.items())
    assert resolve_lane("firms-archive") is FIRMS_ARCHIVE_LANE


def test_resolving_an_unregistered_lane_names_the_registry_rather_than_returning_nothing() -> None:
    with pytest.raises(UnknownBackfillLaneError, match="firms-archive, streamflow-archive"):
        resolve_lane("ndvi-archive")


def test_a_lane_reports_its_missing_credential_by_name_and_reads_it_at_call_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert FIRMS_ARCHIVE_LANE.missing_credential() == FIRMS_API_KEY_VARIABLE

    # Call-time, not import-time: the cron container is long-lived and a variable set after import must
    # take effect without a restart. See ingest/AGENTS.md "policy.py".
    monkeypatch.setenv(FIRMS_API_KEY_VARIABLE, "a-key")
    assert FIRMS_ARCHIVE_LANE.missing_credential() is None


def test_a_whitespace_only_credential_counts_as_missing_rather_than_as_a_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A blank Railway variable is the shape this guard exists for: `railway variables | cut -d= -f2` on a
    # key that was never set yields an empty string, and the bash driver checked `-z` for the same reason.
    monkeypatch.setenv(FIRMS_API_KEY_VARIABLE, "   ")

    assert FIRMS_ARCHIVE_LANE.missing_credential() == FIRMS_API_KEY_VARIABLE


def test_a_lane_renders_its_walk_shape_as_the_parameters_an_operator_reads_off_the_definition_row() -> None:
    assert FIRMS_ARCHIVE_LANE.to_parameters() == {
        "lane": "firms-archive",
        "source": FIRMS_ARCHIVE_SOURCE,
        "floor": "2000-11-01",
        "chunk_days": 1,
        "window_days": 5,
        "max_source_records": 50_000,
        "credential_variable": FIRMS_API_KEY_VARIABLE,
    }

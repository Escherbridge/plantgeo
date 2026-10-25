"""`burn-severity-direct-forward` through real runner turns: the shipped TOML, the real provider and client.

Only the process edges are faked: the MTBS EDW layer (`mtbs_world.py`, behind `httpx.MockTransport`), the bucket
(`tests/runner/fakes.py::MemoryLaneStore`) and the clock. NEEDS DuckDB's `spatial` extension loadable, as the
legacy rows tests do: every written day repairs its polygons through it. Spec §7; CA7 in
`pipeline/lanes/burn_severity/AGENTS.md`.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

import httpx

from agri_data_service.execution.lane_catalogue import build_lane_catalogue
from agri_data_service.execution.lane_specs import LANE_SPECS
from agri_data_service.foundation.lane_config import load_lane_configs
from agri_data_service.foundation.region import load_region
from agri_data_service.ingest import mtbs as ingest_mtbs
from agri_data_service.pipeline.runner.binding import ConfigProviderClient
from agri_data_service.pipeline.runner.resolve import resolve_strategy
from tests.lane_config.builders import REAL_LANES_DIRECTORY
from tests.lanes.burn_severity.mtbs_world import QUERY_PATH, MtbsWorld, cohort
from tests.runner.fakes import NOW, ManualClock, MemoryLaneStore, ports_for, run, spec_for, unwritten_by_day

if TYPE_CHECKING:
    from collections.abc import Mapping

    import pytest

    from agri_data_service.foundation.lane_config import LaneConfig
    from agri_data_service.pipeline.runner.contract import TurnMode

LANE_ID: Final = "burn-severity-direct-forward"
STREAM: Final = "burn-severity"
#: `warehouse/mtbs_releases.py::MTBS_ANNUAL_RELEASE_DATES`: fire year -> its governed release day.
RELEASES: Final[Mapping[int, date]] = MappingProxyType(
    {
        2018: date(2020, 11, 24),
        2019: date(2021, 9, 27),
        2020: date(2022, 4, 28),
        2021: date(2023, 8, 9),
        2022: date(2024, 8, 22),
    }
)
_ENVELOPE = load_region("pnw").sub_envelopes["burn_severity"]
ENVELOPE: Final = f"{_ENVELOPE.west},{_ENVELOPE.south},{_ENVELOPE.east},{_ENVELOPE.north}"


def _shipped_lane() -> LaneConfig:
    return load_lane_configs(REAL_LANES_DIRECTORY, load_region("pnw")).lanes[LANE_ID]


def _cut_over(*, gap_fill: bool = False) -> LaneConfig:
    """The shipped lane as a cut-over would flip it: executor config, gap-fill enabled at G6 when asked."""
    lane = _shipped_lane()
    schedule = lane.schedule.model_copy(
        update={"gap_fill_enabled": gap_fill, "gap_fill_enabled_at_gate": "G6" if gap_fill else None}
    )
    return lane.model_copy(update={"executor": "config", "schedule": schedule})


async def _turn(
    lane: LaneConfig, world: MtbsWorld, store: MemoryLaneStore, *, mode: TurnMode = "forward", now: datetime = NOW
) -> tuple[int, dict[str, object]]:
    """One turn with the real `ConfigProviderClient` sending to the fake layer through the real provider file."""
    spec = spec_for(lane, mode=mode)
    clock = ManualClock(now)
    async with httpx.AsyncClient(transport=httpx.MockTransport(world.handler)) as http:
        ports = ports_for(resolve_strategy(lane), store, clock=clock)
        assert spec.provider is not None
        ports.client = ConfigProviderClient(provider=spec.provider, http=http, clock=clock)
        return await run(spec, ports)


async def test_a_gap_fill_turn_publishes_each_governed_release_day_as_its_whole_cohort() -> None:
    """Five governed days, one unit each; a 60-fire cohort pages 50 + 10; an honest zero is a governed absence."""
    sizes = {2018: 3, 2020: 2, 2021: 1, 2022: 60}
    world = MtbsWorld(fires={year: cohort(year, size) for year, size in sizes.items()})
    store = MemoryLaneStore()

    exit_code, payload = await _turn(_cut_over(gap_fill=True), world, store, mode="gap-fill")

    assert exit_code == 0, payload
    assert unwritten_by_day(payload) == {}
    assert {request.url.path for request in world.requests} == {QUERY_PATH}
    assert {request.url.params["geometry"] for request in world.requests} == {ENVELOPE}
    # Only governed release days are ever asked: one count per fire year, never a non-release day.
    counts = [request for request in world.requests if request.url.params.get("returnCountOnly") == "true"]
    assert sorted(request.url.params["where"] for request in counts) == [f"year = {year}" for year in sorted(RELEASES)]
    # The count is authoritative: 60 fires end on the second page, with no trailing empty page; zero asks no page.
    assert world.pages_for(2022) == [(0, 50), (50, 50)]
    assert world.pages_for(2019) == []
    assert store.statuses[(STREAM, RELEASES[2019])] == "absent"
    for year, fires in sizes.items():
        day = RELEASES[year]
        rows = store.tables[(STREAM, day)].to_pylist()
        assert sorted(row["fire_id"] for row in rows) == [f"F{year}{index:04d}" for index in range(fires)]
        assert {row["release_identifier"] for row in rows} == {f"mtbs-{year}-release-{day.isoformat()}"}
        assert {row["observed_day"] for row in rows} == {day}
        assert {row["data_available_at"] for row in rows} == {datetime(day.year, day.month, day.day, tzinfo=UTC)}
        assert {row["fire_year"] for row in rows} == {year}
        assert {row["allowed_client_exposure"] for row in rows} == {False}
        assert all(row["feature_id"] == f"direct:{row['fire_id']}" and row["geom"] for row in rows)
        receipt = store.receipts[(STREAM, day)]
        assert (receipt.present_units, receipt.expected_units) == (fires, fires)


async def test_a_page_the_host_refuses_as_oversized_halves_until_it_answers() -> None:
    """The host refuses a 50-row page as oversized; the window halves to 25 and the cohort completes in one turn."""
    world = MtbsWorld(fires={2018: cohort(2018, 30)}, max_page_rows=25)
    store = MemoryLaneStore()

    exit_code, payload = await _turn(_cut_over(gap_fill=True), world, store, mode="gap-fill")

    assert exit_code == 0, payload
    assert world.pages_for(2018) == [(0, 50), (0, 25), (25, 25)]
    expected_rows_after_halving = 30  # 25 + 5: the two pages the halved walk lands
    assert store.tables[(STREAM, RELEASES[2018])].num_rows == expected_rows_after_halving
    assert RELEASES[2018].isoformat() not in unwritten_by_day(payload)


async def test_an_oversized_page_halves_to_five_rows_then_hands_the_unit_to_the_runner_ladder() -> None:
    """The host refuses even a 5-row page: 50 -> 25 -> 12 -> 6 -> 5, then `upstream_unavailable` after the ladder."""
    world = MtbsWorld(fires={2018: cohort(2018, 30)}, max_page_rows=4)
    store = MemoryLaneStore()

    exit_code, payload = await _turn(_cut_over(gap_fill=True), world, store, mode="gap-fill")

    assert exit_code == 0, payload
    ladder = [(0, 50), (0, 25), (0, 12), (0, 6), (0, 5)]
    pages = world.pages_for(2018)
    # Each runner attempt walks the same halving again; a narrowed window never re-widens within one.
    assert pages[: len(ladder)] == ladder
    assert set(pages) == set(ladder)
    assert len(pages) % len(ladder) == 0
    assert len(pages) > len(ladder)
    assert unwritten_by_day(payload)[RELEASES[2018].isoformat()]["reason"] == "upstream_unavailable"
    assert (STREAM, RELEASES[2018]) not in store.statuses


async def test_a_real_page_outage_never_narrows_the_window() -> None:
    """A 503 is an outage, not an oversized page: every attempt re-asks the same 50-row page, then gives up."""
    world = MtbsWorld(fires={2018: cohort(2018, 30)}, page_outage_status=503)
    store = MemoryLaneStore()

    exit_code, payload = await _turn(_cut_over(gap_fill=True), world, store, mode="gap-fill")

    assert exit_code == 0, payload
    pages = world.pages_for(2018)
    assert pages
    assert set(pages) == {(0, 50)}
    assert unwritten_by_day(payload)[RELEASES[2018].isoformat()]["reason"] == "upstream_unavailable"


async def test_a_cohort_ending_exactly_on_a_page_asks_one_more_page_and_still_publishes() -> None:
    """Exactly 50 fires: the full page is not taken as the end; the empty page after it is."""
    world = MtbsWorld(fires={2018: cohort(2018, 50)})
    store = MemoryLaneStore()

    exit_code, payload = await _turn(_cut_over(gap_fill=True), world, store, mode="gap-fill")

    assert exit_code == 0, payload
    assert world.pages_for(2018) == [(0, 50), (50, 50)]
    exactly_one_full_page = 50  # the cohort's own count: no trailing-empty-page row loss
    assert store.tables[(STREAM, RELEASES[2018])].num_rows == exactly_one_full_page


async def test_a_fire_added_between_the_count_and_the_paging_fails_loud() -> None:
    """Counted 50, paged 51 with no truncation flag: the page after the full one exposes it; nothing is written."""
    world = MtbsWorld(fires={2018: cohort(2018, 51)}, count_overrides={2018: 50})
    store = MemoryLaneStore()

    exit_code, payload = await _turn(_cut_over(gap_fill=True), world, store, mode="gap-fill")

    assert exit_code == 0, payload
    assert world.pages_for(2018) == [(0, 50), (50, 50)]
    moved = unwritten_by_day(payload)[RELEASES[2018].isoformat()]
    assert moved["reason"] == "strategy_error"
    assert "paged 51 features but the service counts 50" in str(moved["detail"])
    assert (STREAM, RELEASES[2018]) not in store.statuses


async def test_a_capture_short_of_the_service_count_is_never_published() -> None:
    """The service counts four fires and pages three: the day stays owed, and the other releases still write."""
    world = MtbsWorld(fires={2018: cohort(2018, 3), 2020: cohort(2020, 2)}, count_overrides={2018: 4})
    store = MemoryLaneStore()

    exit_code, payload = await _turn(_cut_over(gap_fill=True), world, store, mode="gap-fill")

    assert exit_code == 0, payload
    short = unwritten_by_day(payload)[RELEASES[2018].isoformat()]
    assert short["reason"] == "strategy_error"
    assert "service counts 4" in str(short["detail"])
    assert (STREAM, RELEASES[2018]) not in store.statuses
    surviving_cohort_rows = 2  # the 2020 cohort untouched by 2018's short page
    assert store.tables[(STREAM, RELEASES[2020])].num_rows == surviving_cohort_rows


async def test_the_daily_forward_fire_owes_nothing_between_governed_releases() -> None:
    """The shipped window (lag 7, 14 days) holds no governed release day: no send, no write, nothing owed."""
    world = MtbsWorld(fires={2018: cohort(2018, 3)})
    store = MemoryLaneStore()

    exit_code, payload = await _turn(_cut_over(), world, store)

    assert exit_code == 0, payload
    assert world.requests == []
    assert store.statuses == {}
    assert payload["days_candidate"] == 0


async def test_a_release_governed_inside_the_current_snapshot_era_is_refused_before_any_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fire year dated on/after 2026-09-11 would be refused at read, so it is reported and never fetched."""
    governed = date(2026, 10, 1)
    extended = MappingProxyType({**ingest_mtbs.MTBS_ANNUAL_RELEASE_DATES, 2023: governed})
    monkeypatch.setattr(ingest_mtbs, "MTBS_ANNUAL_RELEASE_DATES", extended)
    world = MtbsWorld(fires={2023: cohort(2023, 2)})
    store = MemoryLaneStore()

    exit_code, payload = await _turn(_cut_over(), world, store, now=datetime(2026, 10, 14, 12, tzinfo=UTC))

    assert exit_code == 0, payload
    assert payload["days_candidate"] == 1
    refused = unwritten_by_day(payload)[governed.isoformat()]
    assert refused["reason"] == "strategy_error"
    assert "current snapshot" in str(refused["detail"])
    assert world.requests == []
    assert (STREAM, governed) not in store.statuses


def test_the_shipped_toml_keeps_the_legacy_daily_lane_and_its_current_capture_dispatching() -> None:
    """CQ-9 carve-out: executor stays legacy, so the command that runs the weekly current capture keeps the id."""
    configs = load_lane_configs(REAL_LANES_DIRECTORY, load_region("pnw"))

    catalogue = build_lane_catalogue(legacy_specs=LANE_SPECS, configs=configs, environment={})

    assert LANE_ID not in catalogue.quarantined
    assert LANE_ID not in catalogue.config_lanes
    legacy = catalogue.legacy_specs[LANE_ID]
    assert legacy.command is not None
    assert "--current-snapshots" in legacy.command

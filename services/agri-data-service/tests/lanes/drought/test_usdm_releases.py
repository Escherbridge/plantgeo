"""`drought-direct-forward`'s config-lane strategy through real runner turns.

The shipped TOML ships `executor = "legacy"` (dark; see `usdm_releases.py`'s module docstring for
why), so these turns are the strategy's OWN conformance proof, not a production path yet. Only the
process edge is faked (`ScriptedUpstream`, a `ProviderClient` double) and the bucket
(`tests.runner.fakes.MemoryLaneStore`); the real shipped TOML, the real provider file and the real
`resolve_strategy` resolution are exercised unchanged.

Deliberately does NOT drive the real `ConfigProviderClient`/`ingest.provider_client` URL
construction: `usdm_releases.py`'s module docstring documents why that path cannot yet express USDM's
dated-path-segment resource, so a mocked-transport integration test through it would pass on a URL
shape the real server would 404 -- false confidence this file declines to manufacture.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Final

import pyarrow as pa  # type: ignore[import-untyped]
import pytest

from agri_data_service.foundation.lane_config import load_lane_configs
from agri_data_service.foundation.lane_config.models import lane_requires_probe_edge
from agri_data_service.foundation.region import load_region
from agri_data_service.ingest.http import HTTP_NOT_FOUND, UpstreamHttpError
from agri_data_service.pipeline.lanes.drought import usdm_releases
from agri_data_service.pipeline.lanes.drought.products import release_weeks
from agri_data_service.pipeline.lanes.drought.usdm_releases import (
    RELEASE_ENDPOINT,
    STRATEGY,
    VALID_DATE_PARAMETER,
)
from agri_data_service.pipeline.runner.contract import ProviderResponse
from agri_data_service.pipeline.runner.resolve import resolve_strategy
from agri_data_service.warehouse.schemas.drought import DROUGHT_GRAIN, DROUGHT_STREAM
from tests.lane_config.builders import REAL_LANES_DIRECTORY
from tests.runner.fakes import NOW, ManualClock, MemoryLaneStore, ScriptedUpstream, ports_for, run, spec_for

if TYPE_CHECKING:
    from collections.abc import Mapping

    from agri_data_service.foundation.lane_config.models import LaneConfig
    from agri_data_service.pipeline.runner.contract import SourceRequest

LANE_ID: Final = "drought-direct-forward"
STREAM: Final = DROUGHT_STREAM
#: `tests.runner.fakes.NOW`'s date; `candidate_edge(today, lag=4) = 2026-09-16`, so the newest
#: Tuesday a forward turn may ask for (given the shipped lag) is 2026-09-15.
TODAY: Final = date(2026, 9, 20)
NEWEST_TUESDAY: Final = date(2026, 9, 15)
OLDER_TUESDAY: Final = date(2026, 9, 8)
VALID_SQUARE: Final[dict[str, object]] = {
    "type": "Polygon",
    "coordinates": [[[-120.0, 45.0], [-119.0, 45.0], [-119.0, 46.0], [-120.0, 46.0], [-120.0, 45.0]]],
}


def _shipped_lane() -> LaneConfig:
    return load_lane_configs(REAL_LANES_DIRECTORY, load_region("pnw")).lanes[LANE_ID]


def _narrowed(*, absence_recheck_days: int) -> LaneConfig:
    """The shipped lane with a smaller recheck window, so a test turn asks for exactly one Tuesday
    (absence_recheck_days still exceeds publication_lag_days=4, as the schema requires)."""
    lane = _shipped_lane()
    assert lane.days is not None
    days = lane.days.model_copy(update={"absence_recheck_days": absence_recheck_days})
    return lane.model_copy(update={"days": days})


def _feature(drought_class: int, geometry: dict[str, object] = VALID_SQUARE) -> dict[str, object]:
    return {"type": "Feature", "properties": {"DM": drought_class}, "geometry": geometry}


def _release_body(*, classes: tuple[int, ...] = (0, 1, 2, 3, 4)) -> bytes:
    """A full five-class USDM cohort, the shape one release actually publishes."""
    return json.dumps({"type": "FeatureCollection", "features": [_feature(c) for c in classes]}).encode("utf-8")


@dataclass
class ReleaseWorld:
    """USDM's archive: one dated release per published Tuesday; anything else is its documented 404."""

    releases: dict[date, bytes] = field(default_factory=dict)
    requested: list[date] = field(default_factory=list)

    def answer(self, endpoint: str, parameters: Mapping[str, str], probe: bool) -> bytes:  # noqa: ARG002 - the ScriptedUpstream shape
        assert endpoint == RELEASE_ENDPOINT
        valid_date = date.fromisoformat(parameters[VALID_DATE_PARAMETER])
        self.requested.append(valid_date)
        if valid_date not in self.releases:
            raise UpstreamHttpError(HTTP_NOT_FOUND)
        return self.releases[valid_date]


async def _turn(
    lane: LaneConfig, world: ReleaseWorld, store: MemoryLaneStore, *, now: datetime = NOW
) -> tuple[int, dict[str, object]]:
    clock = ManualClock(now)
    ports = ports_for(resolve_strategy(lane), store, upstream=ScriptedUpstream(answer=world.answer), clock=clock)
    spec = spec_for(lane)
    return await run(spec, ports)


def _rows(store: MemoryLaneStore, day: date) -> list[dict[str, object]]:
    return store.tables[(STREAM, day)].to_pylist()


def _seed_table(day: date) -> pa.Table:
    """A minimal stand-in table for a pre-seeded already-published day; its content is never read."""
    return pa.table({"valid_date": pa.array([day], pa.date32()), "dm_category": pa.array([0], pa.int32())})


def _unwritten(payload: dict[str, object]) -> list[dict[str, object]]:
    entries = payload["unwritten"]
    assert isinstance(entries, list)
    return entries


# --- release-window planning: only Tuesdays are ever owed ---------------------------------------


def test_release_days_is_every_usdm_tuesday_in_the_window_matching_the_lane_calendar() -> None:
    """The strategy's `ReleaseCalendarStrategy.release_days` is `products.py::release_weeks` verbatim."""
    first, last = date(2026, 8, 1), date(2026, 9, 30)

    assert tuple(STRATEGY.release_days(first, last)) == release_weeks(first, last)
    assert all(day.weekday() == 1 for day in STRATEGY.release_days(first, last))  # Tuesday


def test_plan_requests_refuses_a_non_tuesday_day_rather_than_silently_requesting_it() -> None:
    with pytest.raises(ValueError, match="non-Tuesday"):
        STRATEGY.plan_requests([date(2026, 9, 16)], _shipped_lane(), load_region("pnw"))


async def test_a_forward_turn_only_ever_requests_release_tuesdays_inside_its_window() -> None:
    """The shipped lane's 56-day recheck window holds exactly the 8 Tuesdays `release_weeks` names for
    it, `[window.first, window.last] = [2026-07-23, 2026-09-16]` given `tests.runner.fakes.NOW` --
    never a non-Tuesday day, and never one outside that span."""
    lane = _shipped_lane()
    world = ReleaseWorld(releases={NEWEST_TUESDAY: _release_body()})
    store = MemoryLaneStore()

    exit_code, payload = await _turn(lane, world, store)

    assert exit_code == 0, payload
    assert sorted(world.requested) == list(release_weeks(date(2026, 7, 23), date(2026, 9, 16)))


# --- the shipped TOML: dark, release_series, narrowed cron ---------------------------------------


def test_the_shipped_lane_is_dark_release_series_on_legacy_with_the_narrowed_cron() -> None:
    """spec §7 drought row + the s-drought task: migrated in place, dark, cron narrowed to the release
    window (Thu-Sat UTC, matching the legacy lane's own :45 minute)."""
    lane = _shipped_lane()

    assert (lane.nature, lane.kind, lane.executor, lane.enabled) == ("release_series", "ingest", "legacy", True)
    cron = lane.schedule.forward_expression()
    assert cron.days_of_week == frozenset({4, 5, 6})
    assert cron.minutes == frozenset({45})
    assert lane.schedule.gap_fill_enabled is False
    assert lane.schedule.gap_fill_enabled_at_gate == "G6"
    assert lane.days is not None
    assert lane.days.floor == date(2022, 8, 9)
    assert lane.days.publication_lag_days == 4
    assert lane.days.absence_recheck_days == 56
    assert lane.days.partial_day == "refuse"
    assert [stream.slug for stream in lane.streams] == [STREAM]


def test_the_shipped_lane_resolves_its_strategy_and_needs_no_probe_edge() -> None:
    """USDM is unweighted (`lanes/_providers/usdm.toml`), so S19's probe_edge rule never binds it even
    though the forward cron fires more than once a day (hourly on its three release-window days)."""
    configs = load_lane_configs(REAL_LANES_DIRECTORY, load_region("pnw"))
    lane = configs.lanes[LANE_ID]

    assert resolve_strategy(lane) is STRATEGY
    assert lane_requires_probe_edge(lane, configs.provider_of(lane)) is False


# --- the 404-unsettled classification --------------------------------------------------------------


async def test_an_unpublished_tuesday_with_no_later_release_is_unsettled_never_absent() -> None:
    """A 404 with nothing proving the cadence moved past it stays `unsettled`: retried, never governed absent."""
    lane = _narrowed(absence_recheck_days=5)  # window = [2026-09-12, 2026-09-16]: one Tuesday, 09-15.
    world = ReleaseWorld(releases={})
    store = MemoryLaneStore()

    exit_code, payload = await _turn(lane, world, store, now=datetime(2026, 9, 20, 12, tzinfo=UTC))

    assert exit_code == 0, payload
    assert world.requested == [NEWEST_TUESDAY]
    [entry] = _unwritten(payload)
    assert (entry["day"], entry["reason"]) == (NEWEST_TUESDAY.isoformat(), "unsettled")
    assert store.status(STREAM, NEWEST_TUESDAY) == "missing"


async def test_an_unpublished_tuesday_with_a_later_release_is_a_governed_absence() -> None:
    """The release_series mirrored-past pattern: a later published Tuesday proves USDM's weekly
    cadence moved past this one, so the earlier 404 is a governed absence, not unsettled."""
    lane = _narrowed(absence_recheck_days=12)  # window = [2026-09-05, 2026-09-16]: 09-08 and 09-15.
    store = MemoryLaneStore()
    store.publish(STREAM, NEWEST_TUESDAY, _seed_table(NEWEST_TUESDAY))
    world = ReleaseWorld(releases={})  # 09-08 (the only owed day) 404s; 09-15 is already published.

    exit_code, payload = await _turn(lane, world, store, now=datetime(2026, 9, 20, 12, tzinfo=UTC))

    assert exit_code == 0, payload
    assert world.requested == [OLDER_TUESDAY], "the already-published later Tuesday is never re-asked"
    assert _unwritten(payload) == []
    assert store.status(STREAM, OLDER_TUESDAY) == "absent"


async def test_a_malformed_release_body_is_a_strategy_error_never_a_partial_write() -> None:
    """`partial_day = refuse`: a body that is no FeatureCollection refuses the day, it does not write one."""
    lane = _narrowed(absence_recheck_days=5)
    world = ReleaseWorld(releases={NEWEST_TUESDAY: b'{"type": "NotAFeatureCollection"}'})
    store = MemoryLaneStore()

    exit_code, payload = await _turn(lane, world, store, now=datetime(2026, 9, 20, 12, tzinfo=UTC))

    assert exit_code == 0, payload
    [entry] = _unwritten(payload)
    assert (entry["day"], entry["reason"]) == (NEWEST_TUESDAY.isoformat(), "strategy_error")
    assert store.status(STREAM, NEWEST_TUESDAY) == "missing"


# --- cohort publication shape: one release, every class, one grain -------------------------------


async def test_a_published_tuesday_writes_the_whole_five_class_cohort_in_one_unit() -> None:
    """A release is one HTTP answer, never fetched or written in parts: all five D0-D4 classes land
    together, keyed by the schema's own (valid_date, dm_category) grain."""
    lane = _narrowed(absence_recheck_days=5)  # window = [2026-09-12, 2026-09-16]: one Tuesday, 09-15.
    world = ReleaseWorld(releases={NEWEST_TUESDAY: _release_body()})
    store = MemoryLaneStore()

    exit_code, payload = await _turn(lane, world, store, now=datetime(2026, 9, 20, 12, tzinfo=UTC))

    assert exit_code == 0, payload
    assert _unwritten(payload) == []
    rows = _rows(store, NEWEST_TUESDAY)
    assert sorted(row["dm_category"] for row in rows) == [0, 1, 2, 3, 4]
    assert {row["valid_date"] for row in rows} == {NEWEST_TUESDAY}
    assert len({(row[DROUGHT_GRAIN[0]], row[DROUGHT_GRAIN[1]]) for row in rows}) == len(rows)  # grain is unique
    assert all(row["area_id"] == f"direct:{NEWEST_TUESDAY.isoformat()}:{row['dm_category']}" for row in rows)
    assert all(isinstance(row["geom"], bytes) and len(row["geom"]) > 0 for row in rows)
    receipt = store.receipts[(STREAM, NEWEST_TUESDAY)]
    assert (receipt.present_units, receipt.expected_units) == (1, 1)


# --- digest stability (S11), and the G6 compare gap it does NOT close --------------------------


@dataclass
class _FixedBodyClient:
    """A `ProviderClient` double answering the same body at a caller-chosen retrieval instant --
    the two-fetches-at-different-times shape `_source_digest` must and does ignore."""

    body: bytes
    retrieved_at: datetime

    async def get(self, endpoint: str, parameters: Mapping[str, str], *, probe: bool = False) -> ProviderResponse:
        del endpoint, parameters, probe
        return ProviderResponse(body=self.body, request_url="https://test.invalid/", retrieved_at=self.retrieved_at)


def _release_request(day: date) -> SourceRequest:
    [request] = STRATEGY.plan_requests([day], _shipped_lane(), load_region("pnw"))
    return request


async def test_source_digest_is_stable_across_refetches_but_reacts_to_a_real_content_change() -> None:
    """The direct test the docstring's claim substitutes for: two fetches of byte-identical release
    content at different retrieval instants digest equal; a fetch of genuinely different content
    digests different. This is `_source_digest`'s own S11 scope (`usdm_releases.py`'s "A second known
    gap") -- it says nothing about `pipeline/runner/turn.py::_Turn._compare`'s separate, whole-table
    `canonical_digest`, which DOES see `ingested_at` and is NOT exercised here."""
    request = _release_request(NEWEST_TUESDAY)
    body = _release_body(classes=(0, 4))

    first = await STRATEGY.fetch(request, _FixedBodyClient(body=body, retrieved_at=datetime(2026, 9, 18, tzinfo=UTC)))
    second = await STRATEGY.fetch(request, _FixedBodyClient(body=body, retrieved_at=datetime(2026, 9, 19, tzinfo=UTC)))

    assert first.retrieved_at != second.retrieved_at
    assert usdm_releases._source_digest(first.payload) == usdm_releases._source_digest(second.payload)

    changed = await STRATEGY.fetch(
        request, _FixedBodyClient(body=_release_body(classes=(0, 1, 4)), retrieved_at=second.retrieved_at)
    )

    assert usdm_releases._source_digest(first.payload) != usdm_releases._source_digest(changed.payload)


def test_a_written_release_day_is_never_re_settled_by_a_later_forward_turn() -> None:
    """Why `_source_digest` has no OBSERVABLE rewrite path today: `refuse` with no `revision_window_days`
    means a "data" day is neither owed (`census.owed_days()`) nor an absence-recheck candidate
    (`census.absent_days()`), so no later forward turn ever re-asks it. The digest is still required
    (`Written.source_digest`'s own contract) as a safeguard against a future revision window or a
    manual rerun -- this test pins the CURRENT reachable behaviour, not that requirement away."""
    lane = _narrowed(absence_recheck_days=5)
    assert lane.days is not None
    assert lane.days.revision_window_days is None
    store = MemoryLaneStore()
    store.publish(STREAM, NEWEST_TUESDAY, _seed_table(NEWEST_TUESDAY))

    census = store.census([STREAM], date(2026, 9, 12), date(2026, 9, 16))

    assert NEWEST_TUESDAY not in census.owed_days()
    assert NEWEST_TUESDAY not in census.absent_days()

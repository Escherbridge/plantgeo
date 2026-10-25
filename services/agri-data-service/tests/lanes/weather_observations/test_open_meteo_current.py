"""`weather-observations-direct-forward` through real runner turns: the shipped TOML, the real S14 strategy.

Only the Open-Meteo upstream is faked (`CurrentConditionsWorld`, behind `PolledAtUpstream` -- this
module's own `tests.runner.fakes.ScriptedUpstream`-shaped double, needed because that shared fake
hardcodes `retrieved_at`; see `PolledAtUpstream`'s docstring); the lane TOML, the provider file and
the strategy resolver are all real (`tests/lanes/watersheds/test_nhdplus.py:85-87`'s
`lane.model_copy(update={"executor": "config"})` pattern, since this TOML ships
`executor = "legacy"` DARK per `pipeline/lanes/AGENTS.md`).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Final

import pytest

from agri_data_service.foundation.lane_config import load_lane_configs
from agri_data_service.foundation.region import load_region
from agri_data_service.ingest.identity import OPEN_METEO_PRODUCER, FeatureIdentity
from agri_data_service.ingest.open_meteo import OPEN_METEO_PROPERTY_SOURCE
from agri_data_service.pipeline.lanes.weather_observations.open_meteo_current import (
    CURRENT_PARAMETER,
    ENDPOINT,
    STRATEGY,
    PointReading,
    WeatherObservationsRowError,
    sample_points,
)
from agri_data_service.pipeline.runner.contract import DayContext, ProviderResponse, SourceRequest, SourceResponse
from agri_data_service.pipeline.runner.resolve import resolve_strategy
from tests.lane_config.builders import REAL_LANES_DIRECTORY
from tests.runner.fakes import NOW, TODAY, ManualClock, MemoryLaneStore, ports_for, run, spec_for

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from agri_data_service.foundation.lane_config.models import LaneConfig

LANE_ID: Final = "weather-observations-direct-forward"
STREAM: Final = "weather-observations"

#: 30 minutes after UTC midnight on TODAY: late enough that `utc_today`
#: (`pipeline/runner/clock.py`, what the runner computes "today" from) still reads TODAY, early
#: enough that a reading taken `YESTERDAY_AT` (shortly before midnight) is still within
#: `is_fresh_observation`'s 3-hour `MAX_OBSERVATION_AGE`. This is the only way a REAL turn -- through
#: `fetch()`'s own freshness check -- can ever write a day other than TODAY: `candidate_edge`'s O1
#: clamp (`pipeline/runner/windows.py`) never lets a `write_and_recheck` lane reach past TODAY - 1,
#: so TODAY's own bucket (what `tests.runner.fakes.NOW`, noon, would otherwise produce) is never
#: fanned out at all.
POLL_AT: Final = datetime(TODAY.year, TODAY.month, TODAY.day, 0, 30, tzinfo=UTC)
#: 40 minutes before `POLL_AT`: still fresh, but its own `observedAt` day-prefix is TODAY - 1 -- the
#: midnight-straddle case `absence_recheck_days = 2` exists to never drop
#: (`lanes/weather-observations-direct-forward.toml` [days]).
YESTERDAY_AT: Final = POLL_AT - timedelta(minutes=40)

DEFAULT_TEMPERATURE_C: Final = 15.0


@dataclass
class CurrentConditionsWorld:
    """The upstream: every sample point reports the same fresh reading, unless marked `down`."""

    observed_at_unix: int
    down: frozenset[tuple[float, float]] = field(default_factory=frozenset)
    temperature_c: float = DEFAULT_TEMPERATURE_C

    def answer(self, endpoint: str, parameters: Mapping[str, str], probe: bool) -> bytes:  # noqa: ARG002 - the ScriptedUpstream shape
        assert endpoint == ENDPOINT
        point = (float(parameters["latitude"]), float(parameters["longitude"]))
        if point in self.down:
            raise RuntimeError(f"point {point} did not answer this poll")
        return json.dumps(
            {
                "current": {
                    "time": self.observed_at_unix,
                    "temperature_2m": self.temperature_c,
                    "relative_humidity_2m": 55.0,
                    "wind_speed_10m": 3.0,
                    "wind_direction_10m": 180.0,
                    "precipitation": 0.0,
                }
            }
        ).encode("utf-8")


@dataclass
class PolledAtUpstream:
    """`tests.runner.fakes.ScriptedUpstream`-shaped, but stamps `retrieved_at` from a caller-chosen
    poll instant instead of that module's hardcoded `NOW` (noon) -- needed so a reading can bucket
    onto TODAY - 1 while still passing `is_fresh_observation` (see `POLL_AT` above).
    """

    answer: Callable[[str, dict[str, str], bool], bytes]
    retrieved_at: datetime

    async def get(self, endpoint: str, parameters: Mapping[str, str], *, probe: bool = False) -> ProviderResponse:
        body = self.answer(endpoint, dict(parameters), probe)
        return ProviderResponse(
            body=body, request_url=f"https://upstream.test/{endpoint}", retrieved_at=self.retrieved_at
        )


def _shipped_lane() -> LaneConfig:
    return load_lane_configs(REAL_LANES_DIRECTORY, load_region("pnw")).lanes[LANE_ID]


def _config_lane() -> LaneConfig:
    """The shipped lane as a future G6 gate will flip it: `executor = "config"`, so a real turn may run."""
    return _shipped_lane().model_copy(update={"executor": "config"})


async def _turn(
    lane: LaneConfig, world: CurrentConditionsWorld, store: MemoryLaneStore
) -> tuple[int, dict[str, object]]:
    spec = spec_for(lane, mode="forward")
    upstream = PolledAtUpstream(answer=world.answer, retrieved_at=POLL_AT)
    ports = ports_for(STRATEGY, store, upstream=upstream, clock=ManualClock(POLL_AT))
    return await run(spec, ports)


def _rows(store: MemoryLaneStore, day: date) -> list[dict[str, object]]:
    return store.tables[(STREAM, day)].to_pylist()


# --- shipped config shape --------------------------------------------------------------------


def test_the_shipped_lane_is_a_dark_daily_series_write_and_recheck_lane() -> None:
    lane = _shipped_lane()

    assert (lane.nature, lane.kind, lane.executor) == ("daily_series", "ingest", "legacy")
    assert lane.strategy == "weather_observations.open_meteo_current"
    assert lane.source is not None
    assert (lane.source.provider, lane.source.endpoint, lane.source.history.capability) == (
        "open-meteo",
        "forecast",
        "none",
    )
    assert lane.days is not None
    # publication_lag_days = 1, not 0: `candidate_edge`'s O1 clamp (`pipeline/runner/windows.py`)
    # pins every `write_and_recheck` lane's edge to `today - 1` regardless of this field, so lag = 1
    # states that clamped edge honestly instead of a lag = 0 the clamp would silently override. The
    # effective window is unchanged -- see the TOML's own [days] comment.
    assert (lane.days.publication_lag_days, lane.days.absence_recheck_days, lane.days.partial_day) == (
        1,
        2,
        "write_and_recheck",
    )
    assert lane.schedule.forward_cron == "30 * * * *"
    assert lane.schedule.gap_fill_enabled is False
    assert [stream.slug for stream in lane.streams] == [STREAM]
    assert lane.streams[0].volatile_columns == ("ingested_at",)


def test_the_shipped_strategy_key_resolves_to_the_real_strategy() -> None:
    assert resolve_strategy(_shipped_lane()) is STRATEGY


# --- a current-observation write --------------------------------------------------------------


async def test_a_forward_turn_writes_yesterdays_bucket_from_every_sample_point() -> None:
    """Every bounded sample point reports a midnight-straddle reading, fresh but dated TODAY - 1 --
    the only day `candidate_edge`'s O1 clamp (`pipeline/runner/windows.py`) ever lets a
    `write_and_recheck` lane reach -- so that bucket is written whole, in one turn.
    """
    region = load_region("pnw")
    points = sample_points(region)
    yesterday = TODAY - timedelta(days=1)
    world = CurrentConditionsWorld(observed_at_unix=int(YESTERDAY_AT.timestamp()))
    store = MemoryLaneStore()

    exit_code, payload = await _turn(_config_lane(), world, store)

    assert exit_code == 0, payload
    rows = _rows(store, yesterday)
    assert len(rows) == len(points)
    receipt = store.receipts[(STREAM, yesterday)]
    assert (receipt.present_units, receipt.expected_units) == (len(points), len(points))
    sample = rows[0]
    assert sample["source"] == OPEN_METEO_PROPERTY_SOURCE
    assert sample["observed_day"] == yesterday
    assert sample["ingested_at"] == POLL_AT
    assert str(sample["feature_id"]).startswith("direct:")
    assert sample["temperature_c"] == DEFAULT_TEMPERATURE_C


# --- the write_and_recheck rolling reconcile ---------------------------------------------------


async def test_a_recheck_turn_rewrites_the_day_once_a_quiet_point_starts_answering() -> None:
    """Derived from the legacy forward's own shape: an unanswered point keeps the day partial, never
    blocks it, and the next poll's fuller answer rewrites it (`pipeline/runner/AGENTS.md` "A unit
    that stays down" / "The writer" -- `more_units`), the config-lane counterpart of
    `pipeline/direct/weather_observations/adapter.py`'s incremental merge. The day under test is
    TODAY - 1, the only day O1's clamp ever lets this lane reach (see `POLL_AT`/`YESTERDAY_AT`).
    """
    region = load_region("pnw")
    points = sample_points(region)
    yesterday = TODAY - timedelta(days=1)
    quiet_point = points[0]
    world = CurrentConditionsWorld(observed_at_unix=int(YESTERDAY_AT.timestamp()), down=frozenset({quiet_point}))
    store = MemoryLaneStore()

    exit_code_1, payload_1 = await _turn(_config_lane(), world, store)
    assert exit_code_1 == 0, payload_1
    first_rows = _rows(store, yesterday)
    assert len(first_rows) == len(points) - 1
    receipt_1 = store.receipts[(STREAM, yesterday)]
    assert (receipt_1.present_units, receipt_1.expected_units) == (len(points) - 1, len(points))

    world.down = frozenset()  # the quiet point starts answering on the next poll
    exit_code_2, payload_2 = await _turn(_config_lane(), world, store)

    assert exit_code_2 == 0, payload_2
    second_rows = _rows(store, yesterday)
    assert len(second_rows) == len(points)
    receipt_2 = store.receipts[(STREAM, yesterday)]
    assert (receipt_2.present_units, receipt_2.expected_units) == (len(points), len(points))


# --- a malformed upstream day refuses whole, never a partial write ----------------------------


def _reading(latitude: float, longitude: float, *, observed_at_text: str) -> PointReading:
    return PointReading(
        latitude=latitude,
        longitude=longitude,
        observation={
            "observedAt": observed_at_text,
            "temperature": 15.0,
            "humidity": 55.0,
            "windSpeed": 3.0,
            "windDirection": 180.0,
            "precipitation": 0.0,
        },
        identity=FeatureIdentity(
            producer=OPEN_METEO_PRODUCER,
            producer_local_id=f"{latitude}:{longitude}:{observed_at_text}",
            observed_at=None,
            entity_local_id=f"{latitude}:{longitude}",
        ),
        retrieved_at=NOW,
    )


def _response(unit: str, point: PointReading) -> SourceResponse:
    request = SourceRequest(
        unit=unit,
        endpoint=ENDPOINT,
        days=(TODAY,),
        requested_parameters=frozenset({CURRENT_PARAMETER}),
    )
    return SourceResponse(
        request=request, body=b"{}", retrieved_at=NOW, complete_parameters=frozenset({CURRENT_PARAMETER}), payload=point
    )


def test_a_malformed_reading_refuses_the_whole_day_never_a_partial_write() -> None:
    """One point's `observedAt` cannot be bucketed into a day; the OTHER, well-formed point must not be written
    either -- `settle` and `rows` both raise rather than silently dropping the bad reading and keeping the good
    one, the lane's `REFUSE_WHOLE_RELEASE` policy (`pipeline/lanes/weather_observations/AGENTS.md`).
    """
    good = _response("point:good", _reading(45.0, -120.0, observed_at_text="2026-09-20T12:00:00.000Z"))
    bad = _response("point:bad", _reading(46.0, -119.0, observed_at_text="not-a-date"))
    context = DayContext(day=TODAY, states={}, published_days={}, absent_days={})

    with pytest.raises(WeatherObservationsRowError):
        STRATEGY.settle(TODAY, [good, bad], context)
    with pytest.raises(WeatherObservationsRowError):
        STRATEGY.rows(TODAY, [good, bad])

"""Conformance fixture: a point `write_and_recheck` lane on keyless NASA POWER (unweighted: capped in requests).

`PointWorld` is the upstream: one reading per (day, station); a station that has not reported is missing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import TYPE_CHECKING, Final, cast

import pyarrow as pa  # type: ignore[import-untyped]

from agri_data_service.pipeline.runner.contract import SourceRequest, SourceResponse, Unsettled, Written

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from agri_data_service.foundation.lane_config.models import LaneConfig
    from agri_data_service.foundation.region.manifest import Region
    from agri_data_service.pipeline.runner.contract import DayContext, ProviderClient, Settlement

POINT_STREAM: Final = "fixture-point-reading"
STATIONS: Final = ("s1", "s2", "s3")
ENDPOINT: Final = "daily-point"


def _days(first: date, last: date) -> tuple[date, ...]:
    return tuple(first + timedelta(days=offset) for offset in range((last - first).days + 1))


@dataclass
class PointWorld:
    """The upstream: what each station reported for each day."""

    readings: dict[tuple[date, str], float] = field(default_factory=dict)

    def answer(self, endpoint: str, parameters: Mapping[str, str], probe: bool) -> bytes:  # noqa: ARG002 - the ScriptedUpstream shape
        """`{day: reading | null}` for one station over the asked span."""
        station = parameters["station"]
        span = _days(date.fromisoformat(parameters["start"]), date.fromisoformat(parameters["end"]))
        return json.dumps({day.isoformat(): self.readings.get((day, station)) for day in span}).encode("utf-8")


@dataclass(frozen=True)
class PointRecheckStrategy:
    """One request per station over the whole asked span; a day is written with whatever stations reported."""

    def plan_requests(self, days: Sequence[date], lane: LaneConfig, region: Region) -> list[SourceRequest]:  # noqa: ARG002 - the Protocol's lane and region
        """One unit per station covering `[min(days), max(days)]`."""
        first, last = min(days), max(days)
        return [
            SourceRequest(
                unit=f"{station}:{first.isoformat()}..{last.isoformat()}",
                endpoint=ENDPOINT,
                days=_days(first, last),
                parameters=(("station", station), ("start", first.isoformat()), ("end", last.isoformat())),
                requested_parameters=frozenset({"T2M"}),
                support_digest="fixture-points",
            )
            for station in STATIONS
        ]

    async def fetch(self, request: SourceRequest, client: ProviderClient) -> SourceResponse:
        """Parse one station's `{day: reading}`."""
        response = await client.get(request.endpoint, request.query())
        payload = json.loads(response.body)
        complete = all(value is not None for value in payload.values())
        return SourceResponse(
            request=request,
            body=response.body,
            retrieved_at=response.retrieved_at,
            complete_parameters=frozenset({"T2M"}) if complete else frozenset(),
            payload=(dict(request.parameters)["station"], payload),
        )

    def settle(self, day: date, responses: Sequence[SourceResponse], context: DayContext) -> Settlement:  # noqa: ARG002 - the Protocol's context
        """Any reporting station writes the day; `write_and_recheck` rewrites it when more report later."""
        present = len(_readings(day, responses))
        if not present:
            return Unsettled("unsettled", "no station has reported")
        return Written(
            expected_units=len(STATIONS),
            present_units=present,
            expected_unit_ids=frozenset(STATIONS),
            present_unit_ids=frozenset(_readings(day, responses)),
        )

    def rows(self, day: date, responses: Sequence[SourceResponse]) -> pa.Table:
        """One row per reporting station."""
        readings = _readings(day, responses)
        return pa.table(
            {
                "station_id": pa.array(list(readings), pa.string()),
                "observed_day": pa.array([day] * len(readings), pa.date32()),
                "value": pa.array(list(readings.values()), pa.float64()),
            }
        )


def _readings(day: date, responses: Sequence[SourceResponse]) -> dict[str, float]:
    readings: dict[str, float] = {}
    for response in responses:
        station, payload = cast("tuple[str, dict[str, float | None]]", response.payload)
        value = payload.get(day.isoformat())
        if value is not None:
            readings[station] = value
    return dict(sorted(readings.items()))


STRATEGY: Final = PointRecheckStrategy()

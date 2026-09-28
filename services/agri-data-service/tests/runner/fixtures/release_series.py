"""Conformance fixture: a `release_series` with weekly Tuesday releases; a 404 is `unsettled`, never absent.

`ReleaseWorld` is the upstream: the areas each published release lists; an unpublished release is a 404.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Final

import pyarrow as pa  # type: ignore[import-untyped]

from agri_data_service.ingest.http import UpstreamHttpError
from agri_data_service.pipeline.runner.contract import SourceRequest, SourceResponse, Unsettled, Written

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from agri_data_service.foundation.lane_config.models import LaneConfig
    from agri_data_service.foundation.region.manifest import Region
    from agri_data_service.pipeline.runner.contract import DayContext, ProviderClient, Settlement

RELEASE_STREAM: Final = "fixture-release-map"
ENDPOINT: Final = "daily-point"
HTTP_NOT_FOUND: Final = 404
TUESDAY: Final = 1
#: A 404 carries no retrieval instant of its own.
NOT_PUBLISHED_AT: Final = datetime(1970, 1, 1, tzinfo=UTC)


@dataclass
class ReleaseWorld:
    """The upstream: each published release's areas."""

    releases: dict[date, list[str]] = field(default_factory=dict)

    def answer(self, endpoint: str, parameters: Mapping[str, str], probe: bool) -> bytes:  # noqa: ARG002 - the ScriptedUpstream shape
        """The release's areas, or a 404 while it is unpublished."""
        release = date.fromisoformat(parameters["release"])
        if release not in self.releases:
            raise UpstreamHttpError(HTTP_NOT_FOUND)
        return json.dumps(self.releases[release]).encode("utf-8")


@dataclass(frozen=True)
class ReleaseSeriesStrategy:
    """One unit per release day; only Tuesdays are ever owed."""

    def release_days(self, first: date, last: date) -> list[date]:
        """Every Tuesday in `[first, last]`."""
        span = (first + timedelta(days=offset) for offset in range((last - first).days + 1))
        return [day for day in span if day.weekday() == TUESDAY]

    def plan_requests(self, days: Sequence[date], lane: LaneConfig, region: Region) -> list[SourceRequest]:  # noqa: ARG002 - the Protocol's lane and region
        """One request per release."""
        return [
            SourceRequest(
                unit=f"release:{day.isoformat()}",
                endpoint=ENDPOINT,
                days=(day,),
                parameters=(("release", day.isoformat()),),
            )
            for day in days
        ]

    async def fetch(self, request: SourceRequest, client: ProviderClient) -> SourceResponse:
        """A 404 is an answer (not yet released), never a failure; everything else raises typed."""
        try:
            response = await client.get(request.endpoint, request.query())
        except UpstreamHttpError as error:
            if error.status != HTTP_NOT_FOUND:
                raise
            return SourceResponse(request=request, body=b"404", retrieved_at=NOT_PUBLISHED_AT, payload=None)
        return SourceResponse(
            request=request, body=response.body, retrieved_at=response.retrieved_at, payload=json.loads(response.body)
        )

    def settle(self, day: date, responses: Sequence[SourceResponse], context: DayContext) -> Settlement:  # noqa: ARG002 - the Protocol's context
        """An unpublished release is unsettled; a published one writes its areas."""
        areas = responses[0].payload
        if areas is None:
            return Unsettled("unsettled", "release not published yet (404 is never an absence)")
        return Written(expected_units=len(areas), present_units=len(areas))

    def rows(self, day: date, responses: Sequence[SourceResponse]) -> pa.Table:
        """One row per released area."""
        areas = list(responses[0].payload)
        return pa.table(
            {"area_id": pa.array(areas, pa.string()), "release_day": pa.array([day] * len(areas), pa.date32())}
        )


STRATEGY: Final = ReleaseSeriesStrategy()

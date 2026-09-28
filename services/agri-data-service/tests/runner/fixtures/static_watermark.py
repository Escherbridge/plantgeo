"""Conformance fixture: a `static_lookup` whose `probe_edge` reads the source watermark (the day it last changed).

`ZoneWorld` is the upstream: the current zone levels and the watermark of their last edit.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Final

import pyarrow as pa  # type: ignore[import-untyped]

from agri_data_service.pipeline.runner.contract import ProviderEdge, SourceRequest, SourceResponse, Written

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from agri_data_service.foundation.lane_config.models import LaneConfig
    from agri_data_service.foundation.region.manifest import Region
    from agri_data_service.pipeline.runner.contract import DayContext, ProbeWindow, ProviderClient, Settlement

STATIC_STREAM: Final = "fixture-static-zones"
ENDPOINT: Final = "daily-point"


@dataclass
class ZoneWorld:
    """The upstream: the zones as they stand now, and when they last changed."""

    watermark: date
    zones: dict[str, str] = field(default_factory=dict)

    def answer(self, endpoint: str, parameters: Mapping[str, str], probe: bool) -> bytes:  # noqa: ARG002 - the ScriptedUpstream shape
        """The watermark document, or the snapshot."""
        if parameters["query"] == "watermark":
            return json.dumps({"last_edit": self.watermark.isoformat()}).encode("utf-8")
        return json.dumps(self.zones, sort_keys=True).encode("utf-8")


@dataclass(frozen=True)
class StaticWatermarkStrategy:
    """One snapshot request; the watermark probe decides whether a snapshot is owed at all."""

    async def probe_edge(self, client: ProviderClient, window: ProbeWindow) -> ProviderEdge:
        """The watermark day is the one valued day: the snapshot a static lookup owes is dated there."""
        response = await client.get(ENDPOINT, {"query": "watermark"}, probe=True)
        watermark = date.fromisoformat(json.loads(response.body)["last_edit"])
        return ProviderEdge(status="ok", window=window, valued_days=frozenset({watermark}))

    def plan_requests(self, days: Sequence[date], lane: LaneConfig, region: Region) -> list[SourceRequest]:  # noqa: ARG002 - the Protocol's lane and region
        """The one snapshot, answering the watermark day."""
        return [
            SourceRequest(unit="snapshot", endpoint=ENDPOINT, days=tuple(days), parameters=(("query", "snapshot"),))
        ]

    async def fetch(self, request: SourceRequest, client: ProviderClient) -> SourceResponse:
        """Parse the zone levels."""
        response = await client.get(request.endpoint, request.query())
        return SourceResponse(
            request=request, body=response.body, retrieved_at=response.retrieved_at, payload=json.loads(response.body)
        )

    def settle(self, day: date, responses: Sequence[SourceResponse], context: DayContext) -> Settlement:  # noqa: ARG002 - the Protocol's day and context
        """Every zone the source lists is present."""
        zones = responses[0].payload
        return Written(expected_units=len(zones), present_units=len(zones))

    def rows(self, day: date, responses: Sequence[SourceResponse]) -> pa.Table:
        """One row per zone, dated at the snapshot day."""
        zones = dict(sorted(responses[0].payload.items()))
        return pa.table(
            {
                "zone_id": pa.array(list(zones), pa.string()),
                "level": pa.array(list(zones.values()), pa.string()),
                "snapshot_day": pa.array([day] * len(zones), pa.date32()),
            }
        )


STRATEGY: Final = StaticWatermarkStrategy()

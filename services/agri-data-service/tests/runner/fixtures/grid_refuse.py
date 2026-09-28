"""Conformance fixture: a settled (`refuse`) grid lane on the weighted Open-Meteo archive, with `probe_edge` (S6, S19).

`GridWorld` is the upstream: one value per (day, cell), `None` while a day is unpublished.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import TYPE_CHECKING, Final

import pyarrow as pa  # type: ignore[import-untyped]

from agri_data_service.pipeline.runner.contract import (
    Absent,
    ProviderEdge,
    SourceRequest,
    SourceResponse,
    Unsettled,
    Written,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from agri_data_service.foundation.lane_config.models import LaneConfig
    from agri_data_service.foundation.region.manifest import Region
    from agri_data_service.pipeline.runner.contract import DayContext, ProbeWindow, ProviderClient, Settlement

GRID_STREAM: Final = "fixture-grid-value"
CELLS: Final = ("c1", "c2", "c3", "c4")
#: The probe's two cells are never one fan-out chunk, as soil's inland support cells are not.
PROBE_CELLS: Final = ("c1", "c3")
VARIABLE: Final = "value_mean"
ENDPOINT: Final = "archive"


def _days(first: date, last: date) -> tuple[date, ...]:
    return tuple(first + timedelta(days=offset) for offset in range((last - first).days + 1))


def query(cells: Sequence[str], first: date, last: date) -> tuple[tuple[str, str], ...]:
    """One request's parameters: a latitude per cell (the weight's location count), the span, one variable."""
    coordinates = ",".join(f"4{index}.0" for index, _ in enumerate(cells))
    return (
        ("latitude", coordinates),
        ("longitude", coordinates),
        ("start_date", first.isoformat()),
        ("end_date", last.isoformat()),
        ("daily", VARIABLE),
        ("cells", ",".join(cells)),
    )


@dataclass
class GridWorld:
    """The upstream archive: what it answers for each (day, cell)."""

    values: dict[tuple[date, str], float | None] = field(default_factory=dict)

    def publish(self, days: Iterable[date], *, value: float = 1.0) -> None:
        """Every cell of `days` carries `value` (plus its cell index, so rows differ by cell)."""
        for day in days:
            for index, cell in enumerate(CELLS):
                self.values[(day, cell)] = value + index

    def answer(self, endpoint: str, parameters: Mapping[str, str], probe: bool) -> bytes:  # noqa: ARG002 - the ScriptedUpstream shape
        """The JSON body for one request: `{day: {cell: value | null}}` over the asked span and cells."""
        cells = parameters["cells"].split(",")
        span = _days(date.fromisoformat(parameters["start_date"]), date.fromisoformat(parameters["end_date"]))
        body = {day.isoformat(): {cell: self.values.get((day, cell)) for cell in cells} for day in span}
        return json.dumps(body, sort_keys=True).encode("utf-8")


@dataclass(frozen=True)
class GridRefuseStrategy:
    """One request per cell chunk per span; a span answers `span_days` days ending at its newest owed day."""

    chunk_size: int = 2
    span_days: int = 14

    def plan_requests(self, days: Sequence[date], lane: LaneConfig, region: Region) -> list[SourceRequest]:  # noqa: ARG002 - the Protocol's lane and region
        """Newest span first, then older owed days not yet covered; each span split into cell chunks."""
        remaining = sorted(days)
        spans: list[tuple[date, date]] = []
        while remaining:
            last = remaining[-1]
            first = last - timedelta(days=self.span_days - 1)
            spans.append((first, last))
            remaining = [day for day in remaining if day < first]
        requests: list[SourceRequest] = []
        for first, last in sorted(spans):
            for index in range(0, len(CELLS), self.chunk_size):
                chunk = CELLS[index : index + self.chunk_size]
                requests.append(
                    SourceRequest(
                        unit=f"{first.isoformat()}..{last.isoformat()}:{','.join(chunk)}",
                        endpoint=ENDPOINT,
                        days=_days(first, last),
                        parameters=query(chunk, first, last),
                        requested_parameters=frozenset({VARIABLE}),
                        support_digest="fixture-grid",
                    )
                )
        return requests

    async def fetch(self, request: SourceRequest, client: ProviderClient) -> SourceResponse:
        """Parse `{day: {cell: value}}`; the answer is checkpoint-complete only when no value is null."""
        response = await client.get(request.endpoint, request.query())
        payload = json.loads(response.body)
        complete = all(value is not None for cells in payload.values() for value in cells.values())
        return SourceResponse(
            request=request,
            body=response.body,
            retrieved_at=response.retrieved_at,
            complete_parameters=frozenset({VARIABLE}) if complete else frozenset(),
            payload=payload,
        )

    def settle(self, day: date, responses: Sequence[SourceResponse], context: DayContext) -> Settlement:
        """Values -> written (partial is the runner's to refuse); none -> absent only with a mirrored-past proof."""
        values = _values(day, responses)
        present = sum(value is not None for value in values.values())
        if present:
            return Written(expected_units=len(CELLS), present_units=present)
        later = context.later_published_day(GRID_STREAM)
        if later is not None:
            return Absent(reason="no_values", proof=f"{GRID_STREAM} publishes {later.isoformat()} with values")
        return Unsettled("unsettled", "no values yet")

    def rows(self, day: date, responses: Sequence[SourceResponse]) -> pa.Table:
        """One row per valued cell."""
        values = {cell: value for cell, value in sorted(_values(day, responses).items()) if value is not None}
        return pa.table(
            {
                "cell_id": pa.array(list(values), pa.string()),
                "observed_day": pa.array([day] * len(values), pa.date32()),
                "value": pa.array(list(values.values()), pa.float64()),
            }
        )

    async def probe_edge(self, client: ProviderClient, window: ProbeWindow) -> ProviderEdge:
        """Two cells over the probe window: a day is valued when both carry a value."""
        response = await client.get(ENDPOINT, dict(query(PROBE_CELLS, window.first, window.last)), probe=True)
        payload = json.loads(response.body)
        valued = frozenset(
            date.fromisoformat(day)
            for day, cells in payload.items()
            if all(value is not None for value in cells.values())
        )
        return ProviderEdge(status="ok", window=window, valued_days=valued)


def _values(day: date, responses: Sequence[SourceResponse]) -> dict[str, float | None]:
    values: dict[str, float | None] = {}
    for response in responses:
        payload = response.payload if isinstance(response.payload, dict) else {}
        values.update(payload.get(day.isoformat(), {}))
    return values


STRATEGY: Final = GridRefuseStrategy()

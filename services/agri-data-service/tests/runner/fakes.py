"""Test ports for runner turns: the bucket and the upstream are the process edges faked here, plus a clock.

`MemoryLaneStore` is the bucket as a turn sees it through `LaneReader` and `LaneWriter`: one status per
stream-day, the base rows, the turn receipts and the owed availability claims. The real binding over
`pipeline/parquet` is exercised separately (`test_writer.py`), against `RecordingBackend`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from functools import cache
from typing import TYPE_CHECKING, Final

import pyarrow as pa  # type: ignore[import-untyped]

from agri_data_service.foundation.lane_config.loader import load_lane_configs
from agri_data_service.foundation.lane_config.models import LaneConfig
from agri_data_service.foundation.region import load_region
from agri_data_service.pipeline.runner.census import LaneCensus
from agri_data_service.pipeline.runner.contract import ProviderResponse
from agri_data_service.pipeline.runner.digests import table_digest
from agri_data_service.pipeline.runner.receipts import DayReceipt
from agri_data_service.pipeline.runner.report import TurnLog, TurnReportBuilder
from agri_data_service.pipeline.runner.turn import TurnPorts, TurnSpec, run_turn
from agri_data_service.pipeline.runner.writer import (
    LadderRepairError,
    LaneDayContendedError,
    LaneDayCoverageRefusedError,
    WriteResult,
)
from tests.lane_config.builders import (
    REAL_LANES_DIRECTORY,
    merged,
    nasa_power_lane,
    settled_soil_lane,
    transform_lane,
)
from tests.runner.fixtures.grid_refuse import GRID_STREAM
from tests.runner.fixtures.point_recheck import POINT_STREAM
from tests.runner.fixtures.release_series import RELEASE_STREAM
from tests.runner.fixtures.static_watermark import STATIC_STREAM

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from agri_data_service.foundation.lane_config.models import ProviderConfig
    from agri_data_service.foundation.parquet.paths import PartitionDayStatus
    from agri_data_service.pipeline.runner.checkpoints import CheckpointStore
    from agri_data_service.pipeline.runner.contract import Absent, IngestStrategy, TransformStrategy, TurnMode
    from agri_data_service.pipeline.runner.cooldown import ProviderCooldowns
    from agri_data_service.pipeline.runner.fetch import FetchRetryPolicy

TODAY: Final = date(2026, 9, 20)
NOW: Final = datetime(2026, 9, 20, 12, tzinfo=UTC)
#: The package the fixture strategy keys (`fixtures.<module>`) resolve under.
FIXTURE_STRATEGY_PACKAGE: Final = "tests.runner"


def days_between(first: date, last: date) -> list[date]:
    """Every day in `[first, last]`, ascending."""
    return [first + timedelta(days=offset) for offset in range((last - first).days + 1)]


def grid_table(day: date, values: Mapping[str, float]) -> pa.Table:
    """A grid stream-day exactly as `fixtures.grid_refuse` builds its rows (cell, day, value)."""
    cells = sorted(values)
    return pa.table(
        {
            "cell_id": pa.array(cells, pa.string()),
            "observed_day": pa.array([day] * len(cells), pa.date32()),
            "value": pa.array([values[cell] for cell in cells], pa.float64()),
        }
    )


class ManualClock:
    """The turn's clock: fixed wall time; `sleep` only advances the monotonic clock and records the wait."""

    def __init__(self, now: datetime = NOW) -> None:
        self._now = now
        self._monotonic = 0.0
        self.slept: list[float] = []

    def now(self) -> datetime:
        return self._now

    def monotonic(self) -> float:
        return self._monotonic

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self._monotonic += seconds


@dataclass(frozen=True)
class Send:
    """One request the upstream received."""

    endpoint: str
    parameters: dict[str, str]
    probe: bool


@dataclass
class ScriptedUpstream:
    """The provider edge: `answer(endpoint, parameters, probe)` returns a body or raises; every send is kept."""

    answer: Callable[[str, dict[str, str], bool], bytes]
    sends: list[Send] = field(default_factory=list)

    async def get(self, endpoint: str, parameters: Mapping[str, str], *, probe: bool = False) -> ProviderResponse:
        sent = dict(parameters)
        self.sends.append(Send(endpoint=endpoint, parameters=sent, probe=probe))
        body = self.answer(endpoint, sent, probe)
        return ProviderResponse(body=body, request_url=f"https://upstream.test/{endpoint}", retrieved_at=NOW)

    @property
    def fan_out_sends(self) -> list[Send]:
        """Every send that was not the probe."""
        return [send for send in self.sends if not send.probe]


@dataclass
class MemoryLaneStore:
    """The bucket through the runner's own ports: statuses, base rows, receipts, owed claims, and a journal."""

    statuses: dict[tuple[str, date], PartitionDayStatus] = field(default_factory=dict)
    tables: dict[tuple[str, date], pa.Table] = field(default_factory=dict)
    receipts: dict[tuple[str, date], DayReceipt] = field(default_factory=dict)
    #: Stream-days whose base rung is published but whose coarse rungs are not: ladder work only.
    ladder_incomplete: set[tuple[str, date]] = field(default_factory=set)
    #: Owed `availability/pending/` claims per stream (CA20).
    owed_claims: dict[str, int] = field(default_factory=dict)
    #: Stream-days whose lane-day lock another run holds: every write to them is refused as contended (M4).
    contended: set[tuple[str, date]] = field(default_factory=set)
    #: Stream-days another run republished with coverage this answer lacks: the locked recheck refuses them.
    coverage_refused: set[tuple[str, date]] = field(default_factory=set)
    #: Stream-days whose coarse rungs cannot be derived from their base rung (H1).
    unrepairable: set[tuple[str, date]] = field(default_factory=set)
    #: Every write-side call, in order.
    journal: list[str] = field(default_factory=list)

    def publish(
        self, stream: str, day: date, table: pa.Table, *, lane: str = "legacy", with_receipt: bool = True
    ) -> None:
        """Seed one published stream-day, with the receipt a runner write would have left."""
        self.statuses[(stream, day)] = "data"
        self.tables[(stream, day)] = table
        if with_receipt:
            self.receipts[(stream, day)] = DayReceipt(
                stream=stream,
                day=day,
                lane=lane,
                outcome="written",
                source_digest=table_digest(table),
                present_units=table.num_rows,
                expected_units=table.num_rows,
            )

    def status(self, stream: str, day: date) -> PartitionDayStatus:
        """One stream-day's folded status."""
        if (stream, day) in self.ladder_incomplete:
            return "incomplete"
        return self.statuses.get((stream, day), "missing")

    # --- LaneReader ---------------------------------------------------------------------------

    def census(
        self, streams: Sequence[str], first: date, last: date, *, expected_unit_ids: frozenset[str] | None = None
    ) -> LaneCensus:
        days = days_between(first, last)
        folded = {stream: {day: self.status(stream, day) for day in days} for stream in streams}
        base = {
            stream: {day: "data" if (stream, day) in self.ladder_incomplete else folded[stream][day] for day in days}
            for stream in streams
        }
        source_owed = {}
        source_unresolved = {}
        if expected_unit_ids is not None:
            for stream in streams:
                # What `SourceCompleteness.confirm` would have recorded for each published day's receipt.
                answered = {
                    day: receipt
                    for day in days
                    if base[stream][day] == "data"
                    and (receipt := self.receipt(stream, day)) is not None
                    and receipt.publication_state == "complete"
                    and receipt.expected_unit_ids == expected_unit_ids
                    and receipt.present_unit_ids == expected_unit_ids
                }
                proven = {day for day, receipt in answered.items() if receipt.source_resolved}
                source_owed[stream] = frozenset(day for day in days if base[stream][day] == "data") - proven
                source_unresolved[stream] = frozenset(answered) - proven
        return LaneCensus(
            first=first,
            last=last,
            streams=folded,
            base=base,
            source_owed=source_owed,
            source_unresolved=source_unresolved,
        )

    def receipt(self, stream: str, day: date) -> DayReceipt | None:
        return self.receipts.get((stream, day))

    def read_published(self, stream: str, day: date) -> pa.Table | None:
        return self.tables.get((stream, day)) if self.status(stream, day) == "data" else None

    def newest_data_day(self, stream: str) -> date | None:
        return max(
            (day for (name, day), status in self.statuses.items() if name == stream and status == "data"),
            default=None,
        )

    def canonical_digest(self, stream: str, table: pa.Table) -> str:  # noqa: ARG002 - the LaneReader shape
        return table_digest(table)

    # --- LaneWriter ---------------------------------------------------------------------------

    async def retry_owed_availability(self, streams: Sequence[str]) -> int:
        self.journal.append("retry_owed_availability")
        return sum(self.owed_claims.pop(stream, 0) for stream in streams)

    def _refuse_if_contended(self, stream: str, day: date) -> None:
        if (stream, day) in self.contended:
            raise LaneDayContendedError(f"{stream} {day.isoformat()}: another run holds this lane-day")

    async def write_day(
        self, stream: str, day: date, table: pa.Table, receipt: DayReceipt, *, availability: bool
    ) -> WriteResult:
        self._refuse_if_contended(stream, day)
        if (stream, day) in self.coverage_refused:
            raise LaneDayCoverageRefusedError(f"{stream} {day.isoformat()}: published coverage changed before the lock")
        self.journal.append(f"write:{stream}:{day.isoformat()}:availability={availability}")
        self.statuses[(stream, day)] = "data"
        self.ladder_incomplete.discard((stream, day))
        self.tables[(stream, day)] = table
        self.receipts[(stream, day)] = receipt
        return WriteResult(partitions=4, rows=table.num_rows, bytes_written=table.nbytes)

    async def write_absence(self, stream: str, day: date, absence: Absent, receipt: DayReceipt) -> WriteResult:
        self._refuse_if_contended(stream, day)
        self.journal.append(f"absent:{stream}:{day.isoformat()}:{absence.reason}")
        self.statuses[(stream, day)] = "absent"
        self.tables.pop((stream, day), None)
        self.receipts[(stream, day)] = receipt
        return WriteResult(partitions=0, rows=0, bytes_written=0)

    async def write_receipt(self, receipt: DayReceipt) -> None:
        self.journal.append(f"receipt:{receipt.stream}:{receipt.day.isoformat()}")
        self.receipts[(receipt.stream, receipt.day)] = receipt

    async def prune(self, stream: str, day: date) -> WriteResult:
        self._refuse_if_contended(stream, day)
        self.journal.append(f"prune:{stream}:{day.isoformat()}")
        self.statuses.pop((stream, day), None)
        self.tables.pop((stream, day), None)
        return WriteResult(partitions=4)

    async def repair_ladder(self, stream: str, day: date) -> WriteResult:
        self._refuse_if_contended(stream, day)
        self.journal.append(f"repair:{stream}:{day.isoformat()}")
        if (stream, day) in self.unrepairable:
            raise LadderRepairError(f"{stream} {day.isoformat()}: the base rung no longer derives")
        self.ladder_incomplete.discard((stream, day))
        return WriteResult(partitions=3)

    def publication_summary(self) -> Mapping[str, int]:
        return {"availability_retry_owed": sum(self.owed_claims.values())}

    def writes(self) -> list[str]:
        """The journal's data writes only (`write:` lines)."""
        return [line for line in self.journal if line.startswith("write:")]


@cache
def providers() -> Mapping[str, ProviderConfig]:
    """The real provider files the service ships."""
    return load_lane_configs(REAL_LANES_DIRECTORY, load_region("pnw")).providers


def _lane(document: Mapping[str, object]) -> LaneConfig:
    return LaneConfig.model_validate(dict(document))


def _streams(*slugs: str) -> list[dict[str, object]]:
    return [{"slug": slug, "floor_basis": "conformance fixture"} for slug in slugs]


def grid_lane(lane_id: str = "fixture-grid-settled", stream: str = GRID_STREAM, **overrides: object) -> LaneConfig:
    """A settled grid lane on the Open-Meteo archive: window 14 days ending `TODAY - 5`."""
    base = {"executor": "config", "strategy": "fixtures.grid_refuse", "streams": _streams(stream)}
    return _lane(settled_soil_lane(lane_id, **merged(base, overrides)))


def point_lane(**overrides: object) -> LaneConfig:
    """A provisional point lane on NASA POWER: lag 0, a 3-day recheck window ending yesterday UTC."""
    base = {
        "executor": "config",
        "strategy": "fixtures.point_recheck",
        "days": {"publication_lag_days": 0, "absence_recheck_days": 3, "partial_day": "write_and_recheck"},
        "streams": _streams(POINT_STREAM),
    }
    return _lane(nasa_power_lane("fixture-point-recheck", **merged(base, overrides)))


def release_lane(**overrides: object) -> LaneConfig:
    """A weekly release series on NASA POWER's endpoint: lag 2, a 21-day window."""
    base = {
        "executor": "config",
        "nature": "release_series",
        "strategy": "fixtures.release_series",
        "days": {"publication_lag_days": 2, "absence_recheck_days": 21},
        "streams": _streams(RELEASE_STREAM),
    }
    return _lane(nasa_power_lane("fixture-release", **merged(base, overrides)))


def static_lane(**overrides: object) -> LaneConfig:
    """A static lookup read at its watermark."""
    base = {
        "executor": "config",
        "nature": "static_lookup",
        "strategy": "fixtures.static_watermark",
        "streams": _streams(STATIC_STREAM),
    }
    document = nasa_power_lane("fixture-static", **merged(base, overrides))
    document["days"] = None
    return _lane(document)


def precedence_lanes(*, pruning: bool = False) -> tuple[LaneConfig, dict[str, LaneConfig]]:
    """A `transforms.precedence` lane over a settled and a provisional grid lane, and those two inputs."""
    settled = grid_lane("fixture-era5-settled", stream="fixture-era5-value")
    provisional = grid_lane(
        "fixture-ifs-provisional",
        stream="fixture-ifs-value",
        days={"publication_lag_days": 0, "absence_recheck_days": 3, "partial_day": "write_and_recheck"},
    )
    overrides: dict[str, object] = {
        "executor": "config",
        "streams": _streams("fixture-value"),
        "days": {"publication_lag_days": 1, "absence_recheck_days": 5},
    }
    if pruning:
        overrides["pruning"] = {"enabled": True, "enabled_at_gate": "G6"}
    transform = _lane(transform_lane("fixture-precedence", inputs=[settled.id, provisional.id], **overrides))
    return transform, {settled.id: settled, provisional.id: provisional}


def spec_for(  # noqa: PLR0913 - one turn option per keyword
    lane: LaneConfig,
    *,
    mode: TurnMode = "forward",
    input_lanes: Mapping[str, LaneConfig] | None = None,
    compare: bool = False,
    republish_current: bool = False,
    weighted_budget: int | None = None,
) -> TurnSpec:
    """The turn spec a command would build for `lane`."""
    provider = None if lane.source is None else providers()[lane.source.provider]
    return TurnSpec(
        lane=lane,
        provider=provider,
        region=load_region("pnw"),
        mode=mode,
        run_id="runner-test",
        input_lanes=dict(input_lanes or {}),
        compare=compare,
        republish_current=republish_current,
        weighted_budget=weighted_budget,
    )


def ports_for(  # noqa: PLR0913 - one collaborator per keyword
    strategy: IngestStrategy | TransformStrategy,
    store: MemoryLaneStore,
    *,
    upstream: ScriptedUpstream | None = None,
    clock: ManualClock | None = None,
    compare: bool = False,
    checkpoint_store: CheckpointStore | None = None,
    fetch_policy: FetchRetryPolicy | None = None,
    cooldowns: ProviderCooldowns | None = None,
) -> TurnPorts:
    """Ports over the memory bucket; a compare turn gets no writer, as production binds it."""
    ports = TurnPorts(
        strategy=strategy,
        reader=store,
        clock=clock or ManualClock(),
        writer=None if compare else store,
        client=upstream,
        checkpoint_store=checkpoint_store,
        cooldowns=cooldowns,
    )
    if fetch_policy is not None:
        ports.fetch_policy = fetch_policy
    return ports


async def run(spec: TurnSpec, ports: TurnPorts) -> tuple[int, dict[str, object]]:
    """Run one turn and return its exit code and the S5 report payload it would print."""
    report = TurnReportBuilder(lane=spec.lane.id, mode=spec.mode, run_id=spec.run_id, compare=spec.compare)
    log = TurnLog(lane=spec.lane.id, mode=spec.mode)
    exit_code = await run_turn(spec, ports, report, log)
    return exit_code, report.to_payload(exit_code=exit_code, log=log)


def unwritten_by_day(payload: Mapping[str, object]) -> dict[str, dict[str, object]]:
    """The report's `unwritten` entries, keyed by ISO day."""
    entries = payload["unwritten"]
    assert isinstance(entries, list)
    return {str(entry["day"]): entry for entry in entries}

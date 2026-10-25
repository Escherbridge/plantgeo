"""`soil.open_meteo_era5_land`: eight ERA5-Land soil streams off the Open-Meteo archive, one day per request (S14).

Derived from the legacy `pipeline/direct/soil/**` writer, never imported. A day writes 0 or 1,470
cells per variable: anything between is `refused_partial`; all-null is `Absent` only with a
mirrored-past proof. Rationale and every deviation from legacy: `pipeline/lanes/soil/AGENTS.md`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Final

from agri_data_service.foundation.region import load_region
from agri_data_service.ingest.open_meteo import OPEN_METEO_ERA5_LAND_MODEL
from agri_data_service.pipeline.lanes._shared.digest import sha256_hex
from agri_data_service.pipeline.lanes.soil.archive import (
    ARCHIVE_ENDPOINT,
    ArchiveAnswer,
    SoilArchiveBodyError,
    archive_parameters,
    canonical_document,
    parse_answer,
    part_key,
    requested_points,
)
from agri_data_service.pipeline.lanes.soil.rows import CellValue, DayReceipt, product_day_table
from agri_data_service.pipeline.lanes.soil.support import (
    SUPPORT_CELL_COUNT,
    SoilSupportError,
    load_support_identity,
    probe_points,
    support_cells,
    support_chunks,
    support_digest,
)
from agri_data_service.pipeline.runner.contract import (
    Absent,
    ProviderEdge,
    SourceRequest,
    SourceResponse,
    Unsettled,
    Written,
)
from agri_data_service.warehouse.field_products.soil import (
    ERA5_LAND_SNAPSHOT_LAST_DAY,
    SOIL_FIELD_PRODUCTS,
    SOIL_SOURCE_PARAMETERS,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from datetime import date

    import pyarrow as pa  # type: ignore[import-untyped]

    from agri_data_service.foundation.lane_config.models import LaneConfig
    from agri_data_service.foundation.region.manifest import AnalysisLattice, Region
    from agri_data_service.pipeline.lanes.soil.support import SupportIdentity
    from agri_data_service.pipeline.runner.contract import DayContext, ProbeWindow, ProviderClient, Settlement

#: The analysis lattice this strategy is pinned to; the lane TOML's `grid` must name it.
SUPPORT_GRID: Final = "analysis-0p25"

#: Valued cells on a complete day (`direct/soil/support.py` ERA5_LAND_VALUE_CELL_COUNT); TOML `expected_value_units`.
VALUE_CELL_COUNT: Final = 1_470


class SoilSettlementError(RuntimeError):
    """A day the strategy refuses to settle at all (immutable, re-masked, mis-shaped): the day's `strategy_error`."""


@dataclass(frozen=True)
class OpenMeteoEra5LandStrategy:
    """Per-day 50-cell chunk requests; settle refuses any day short of 1,470 values for every variable."""

    #: The cells' `agri.spatial_cell` identities (`soil/AGENTS.md` "Cell identities"); read lazily, in `rows` only.
    identity: Callable[[], SupportIdentity] = load_support_identity
    #: The active region; `probe_edge` is handed no lane, so it reads the pinned lattice from here.
    region: Callable[[], Region] = load_region

    def plan_requests(self, days: Sequence[date], lane: LaneConfig, region: Region) -> list[SourceRequest]:
        """One unit per (day, 50-cell chunk), day by day: 32 units, 1,568 weighted calls a day."""
        _require_lane(lane)
        cells = support_cells(_lattice(region))
        digest = support_digest(cells)
        return [
            SourceRequest(
                unit=f"{day.isoformat()}:{chunk.key}",
                endpoint=ARCHIVE_ENDPOINT,
                days=(day,),
                parameters=archive_parameters(chunk.points, day, day),
                requested_parameters=frozenset(SOIL_SOURCE_PARAMETERS),
                support_digest=digest,
            )
            for day in sorted(set(days))
            for chunk in support_chunks(cells)
        ]

    async def fetch(self, request: SourceRequest, client: ProviderClient) -> SourceResponse:
        """Send, canonicalize, bind the body to its cells; an unbindable body is the unit's strategy_error."""
        response = await client.get(request.endpoint, request.query())
        document = canonical_document(response.body)
        answer = parse_answer(
            document,
            points=requested_points(request.parameters),
            days=request.days,
            key=part_key(response.request_url, request.parameters),
        )
        complete = frozenset(
            parameter
            for parameter, series in answer.values.items()
            if all(value is not None for values in series for value in values)
        )
        # `body` is the CANONICAL document: its digest is the legacy `response_sha256`, and a
        # checkpoint replay re-canonicalizes it to the same bytes.
        return SourceResponse(
            request=request,
            body=document,
            retrieved_at=response.retrieved_at,
            complete_parameters=complete,
            payload=answer,
        )

    def settle(self, day: date, responses: Sequence[SourceResponse], context: DayContext) -> Settlement:
        """1,470 values per variable writes; any short variable is partial; all-null is absent only when proven."""
        if day <= ERA5_LAND_SNAPSHOT_LAST_DAY:
            raise SoilSettlementError(f"{day} is immutable snapshot history (through {ERA5_LAND_SNAPSHOT_LAST_DAY})")
        answers = _day_answers(day, responses)
        counts = {
            parameter: sum(value is not None for answer in answers for value in answer.day_values(parameter, day))
            for parameter in SOIL_SOURCE_PARAMETERS
        }
        over = {parameter: count for parameter, count in counts.items() if count > VALUE_CELL_COUNT}
        if over:
            raise SoilSettlementError(f"{day} carries more than {VALUE_CELL_COUNT} values for {over}; the mask moved")
        if not any(counts.values()):
            return _all_null(day, context)
        return Written(
            expected_units=VALUE_CELL_COUNT,
            present_units=min(counts.values()),
            source_digest=_day_digest(answers),
        )

    def rows(self, day: date, responses: Sequence[SourceResponse]) -> Mapping[str, pa.Table]:
        """One table per stream, read off the same 32 answers, each in its historical row shape."""
        answers = _day_answers(day, responses)
        identity = self.identity()
        receipt = DayReceipt(
            response_sha256=_day_digest(answers),
            retrieved_at=max(response.retrieved_at for response in responses),
        )
        tables: dict[str, pa.Table] = {}
        for product in SOIL_FIELD_PRODUCTS:
            values: list[CellValue] = []
            for answer in answers:
                for point, value in zip(answer.points, answer.day_values(product.source_parameter, day), strict=True):
                    if value is None:
                        continue
                    cell = identity.resolve(point)
                    if cell is None:
                        raise SoilSupportError(f"answered point {point} has no shipped cell identity")
                    values.append(
                        CellValue(cell=cell, value=value, part_key=answer.part_key, part_sha256=answer.response_sha256)
                    )
            tables[product.stream] = product_day_table(product, day=day, values=values, receipt=receipt)
        return tables

    async def probe_edge(self, client: ProviderClient, window: ProbeWindow) -> ProviderEdge:
        """Two centre cells over the window; a day is valued when every variable is valued at both."""
        points = probe_points(_lattice(self.region()))
        days = _window_days(window)
        parameters = dict(archive_parameters(points, window.first, window.last))
        response = await client.get(ARCHIVE_ENDPOINT, parameters, probe=True)
        try:
            answer = parse_answer(canonical_document(response.body), points=points, days=days, key=response.request_url)
        except SoilArchiveBodyError as error:
            # O-R3-1: a probe that cannot answer gates its window; it never fails the turn.
            return ProviderEdge(status="unavailable", window=window, detail=f"{type(error).__name__}: {error}")
        valued = frozenset(
            day
            for day in days
            if all(
                value is not None for parameter in SOIL_SOURCE_PARAMETERS for value in answer.day_values(parameter, day)
            )
        )
        return ProviderEdge(status="ok", window=window, valued_days=valued)


def _require_lane(lane: LaneConfig) -> None:
    """The strategy is pinned to one lattice and one model; a TOML naming others is a code fault (exit 70)."""
    model = None if lane.source is None else lane.source.model
    if lane.grid != SUPPORT_GRID or model != OPEN_METEO_ERA5_LAND_MODEL:
        raise SoilSupportError(f"soil needs grid {SUPPORT_GRID!r} and model {OPEN_METEO_ERA5_LAND_MODEL!r}")


def _lattice(region: Region) -> AnalysisLattice:
    lattice = region.analysis_lattices.get(SUPPORT_GRID)
    if lattice is None:
        raise SoilSupportError(f"region {region.slug!r} declares no {SUPPORT_GRID!r} lattice")
    return lattice


def _window_days(window: ProbeWindow) -> tuple[date, ...]:
    return tuple(window.first + timedelta(days=offset) for offset in range((window.last - window.first).days + 1))


def _day_answers(day: date, responses: Sequence[SourceResponse]) -> tuple[ArchiveAnswer, ...]:
    """The day's chunk answers in unit (= chunk) order, covering the whole support exactly once."""
    ordered = sorted(responses, key=lambda response: response.request.unit)
    answers = tuple(response.payload for response in ordered if isinstance(response.payload, ArchiveAnswer))
    if len(answers) != len(ordered) or any(day not in answer.days for answer in answers):
        raise SoilSettlementError(f"{day}: a response carries no parsed answer for this day")
    covered = sum(len(answer.points) for answer in answers)
    if covered != SUPPORT_CELL_COUNT:
        raise SoilSettlementError(f"{day}: the answers cover {covered} cells, not {SUPPORT_CELL_COUNT}")
    return answers


def _day_digest(answers: Sequence[ArchiveAnswer]) -> str:
    """The legacy receipt's `response_sha256`: SHA-256 over the chunk digests, in chunk order. Content only (S11)."""
    return sha256_hex("".join(answer.response_sha256 for answer in answers).encode("utf-8"))


def _all_null(day: date, context: DayContext) -> Settlement:
    """Absent only when EVERY stream already publishes a later day with values (`forward.py::_mirrored_past_day`)."""
    later = {product.stream: context.later_published_day(product.stream) for product in SOIL_FIELD_PRODUCTS}
    unproven = sorted(stream for stream, published in later.items() if published is None)
    if unproven:
        return Unsettled(
            "unsettled",
            f"every value was null and no later day of {unproven} is published, so the mirror is not proven past {day}",
        )
    proof = "; ".join(f"{stream} is published with values for {seen}" for stream, seen in sorted(later.items()))
    return Absent(
        reason=f"the Open-Meteo ERA5-Land archive answered all {SUPPORT_CELL_COUNT} cells and every value was null",
        proof=f"{proof}, so the ERA5-Land mirror has moved past {day}",
    )


STRATEGY: Final = OpenMeteoEra5LandStrategy()

__all__ = ["STRATEGY", "SUPPORT_GRID", "VALUE_CELL_COUNT", "OpenMeteoEra5LandStrategy", "SoilSettlementError"]

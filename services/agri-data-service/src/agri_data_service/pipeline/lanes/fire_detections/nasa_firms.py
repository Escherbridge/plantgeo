"""`fire-detections-direct-forward`'s config-lane strategy: one unit per settled day, products from FIRMS availability.

See `lanes/fire_detections/AGENTS.md` for the full rationale (legacy-derived behaviour, the dated
endpoint, `{api_key}` handling, availability-aware coverage, why per-day units, the forward
re-ask window, the 10k-cap tripwire, unconditional absence, `source_digest=None`, and defect refusal).
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC
from decimal import ROUND_FLOOR, Decimal
from typing import TYPE_CHECKING, Final

import pyarrow as pa  # type: ignore[import-untyped]

from agri_data_service.ingest.firms import (
    FIRMS_HISTORY_SOURCES,
    build_fire_detection_write,
    collapse_history_records,
    parse_firms_csv,
    parse_product_availability,
    products_covering,
)
from agri_data_service.ingest.policy import resolve_bounded_bbox
from agri_data_service.pipeline.runner.contract import Absent, SourceRequest, SourceResponse, Unsettled, Written
from agri_data_service.warehouse.schemas.fire_detections import (
    FIRE_DETECTIONS_CELL_SIZE_DEGREES,
    FIRE_DETECTIONS_SCHEMA,
    FIRE_DETECTIONS_STREAM,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date, datetime

    from agri_data_service.foundation.lane_config.models import LaneConfig
    from agri_data_service.foundation.region.manifest import Region
    from agri_data_service.ingest.firms import ProductAvailability
    from agri_data_service.ingest.records import FeatureWrite
    from agri_data_service.pipeline.runner.contract import DayContext, ProviderClient, Settlement

#: Matches `lanes/_providers/nasa-firms.toml`'s `[endpoints.area-csv-dated]` declaration.
FORWARD_ENDPOINT: Final = "area-csv-dated"
#: Matches `lanes/_providers/nasa-firms.toml`'s `[endpoints.data-availability]` declaration.
AVAILABILITY_ENDPOINT: Final = "data-availability"
#: Every product a day may owe; which of them it DOES owe is read from FIRMS per turn (module docstring).
CANDIDATE_PRODUCTS: Final = FIRMS_HISTORY_SOURCES
#: A day unit's `source` path value: its budget/report key, never sent (module docstring, "Why per-day units").
DAY_UNIT_SOURCE: Final = "AVAILABILITY_COVERED"
#: Every placeholder the dated template names besides the reserved `{api_key}`.
_REQUESTED_PARAMETERS: Final = frozenset({"source", "area", "day_range", "start_date"})
#: One exact settled UTC day per request; never a rolling span (see module docstring).
_DAY_RANGE: Final = "1"
#: The real, measured upstream truncation point this repo has evidence for (module docstring,
#: "The 10,000-record cap"). Hit per single product-day response, never the legacy rolling job's
#: multi-day merged total.
FIRMS_RESPONSE_RECORD_CAP: Final = 10_000
#: A GeoJSON Point's `[longitude, latitude]`.
_POINT_COORDINATE_COUNT: Final = 2
#: `Decimal(str(...))`, never `Decimal(float)`: avoids binary-float imprecision in the floor-snap.
_CELL_SIZE: Final = Decimal(str(FIRE_DETECTIONS_CELL_SIZE_DEGREES))


@dataclass(frozen=True, slots=True)
class ProductAnswer:
    """One covering product's parsed CSV for the day."""

    product: str
    features: list[dict[str, object]]


@dataclass(frozen=True, slots=True)
class FirmsDay:
    """A day unit's payload: the products availability names as covering the day, and each one's answer."""

    covering: tuple[str, ...]
    answers: tuple[ProductAnswer, ...]


@dataclass(frozen=True, slots=True)
class _Availability:
    """One parsed `data_availability` read and when it was retrieved."""

    table: Mapping[str, ProductAvailability]
    retrieved_at: datetime


@dataclass(slots=True)
class _TurnAvailability:
    """Memoization: one `data_availability` read per turn, shared by every day unit the turn's client fetches.

    Keyed on the client's identity (the runner builds one `BudgetedClient` per turn). A failed read is
    dropped, never cached, so the runner's per-unit retry ladder re-asks it; concurrent units await one
    shielded in-flight read, so a sibling's per-unit timeout never cancels it for the others.
    """

    client: object = None
    task: asyncio.Future[_Availability] | None = None

    async def read(self, client: ProviderClient) -> _Availability:
        """The turn's availability: the in-flight or finished read for this client, else a new one."""
        task = self.task
        if task is None or self.client is not client:
            self.client = client
            task = self.task = asyncio.ensure_future(_read_availability(client))
        try:
            return await asyncio.shield(task)
        except Exception:
            if self.task is task:
                self.task = None
            raise


async def _read_availability(client: ProviderClient) -> _Availability:
    """The live per-product coverage windows (`ingest/firms.py::parse_product_availability`)."""
    response = await client.get(AVAILABILITY_ENDPOINT, {})
    return _Availability(
        table=parse_product_availability(response.body.decode("utf-8")), retrieved_at=response.retrieved_at
    )


def _firms_day(responses: Sequence[SourceResponse]) -> FirmsDay:
    """The one day unit's payload (`plan_requests` plans exactly one unit per day)."""
    days = [response.payload for response in responses if isinstance(response.payload, FirmsDay)]
    if len(days) != 1:
        raise ValueError(f"expected exactly one FIRMS day answer, got {len(days)}")
    return days[0]


def _floor_cell(value: Decimal) -> Decimal:
    """Floor-snap one ordinate to the registered 0.005-degree grid (`FIRE_DETECTIONS_CELL_SIZE_DEGREES`)."""
    return (value / _CELL_SIZE).to_integral_value(rounding=ROUND_FLOOR) * _CELL_SIZE


def _finite_decimal(value: object, *, field: str, external_id: str) -> Decimal:
    """Parse one numeric field as a finite Decimal, or refuse the whole day -- never a silently dropped ordinate."""
    try:
        parsed = Decimal(str(value))
    except Exception as error:
        raise ValueError(f"FIRMS detection {external_id!r} has an invalid {field}: {value!r}") from error
    if not parsed.is_finite():
        raise ValueError(f"FIRMS detection {external_id!r} has a non-finite {field}: {value!r}")
    return parsed


@dataclass(slots=True)
class _CellTotals:
    """Typed additive values for one 0.005-degree fire cell, mirroring the legacy direct writer's grain."""

    newest_observed_at: datetime
    detection_count: int = 0
    frp_sum: Decimal = Decimal("0")
    frp_observation_count: int = 0
    high_confidence_detection_count: int = 0


@dataclass(frozen=True, slots=True)
class NasaFirmsForwardStrategy:
    """One unit per settled day, owing exactly the products FIRMS availability names; never a partial day."""

    _availability: _TurnAvailability = field(default_factory=_TurnAvailability, compare=False, repr=False)

    def plan_requests(self, days: Sequence[date], lane: LaneConfig, region: Region) -> list[SourceRequest]:  # noqa: ARG002 - the Protocol's lane and region; bbox comes from INGEST_BBOX (matches watersheds.nhdplus)
        """One request per day; gracefully plans nothing when `INGEST_BBOX` is unset."""
        bbox = resolve_bounded_bbox(None)
        if bbox is None:
            # No raise: `plan_requests` raising aborts the WHOLE turn (exit 70, a code-fault
            # severity), the wrong weight for an operator forgetting an env var. Mirrors
            # watersheds.nhdplus's identical graceful-empty-plan choice for the same gap.
            return []
        return [
            SourceRequest(
                unit=day.isoformat(),
                endpoint=FORWARD_ENDPOINT,
                days=(day,),
                parameters=(
                    ("source", DAY_UNIT_SOURCE),
                    ("area", bbox),
                    ("day_range", _DAY_RANGE),
                    ("start_date", day.isoformat()),
                ),
                requested_parameters=_REQUESTED_PARAMETERS,
            )
            for day in days
        ]

    async def fetch(self, request: SourceRequest, client: ProviderClient) -> SourceResponse:
        """Read the turn's availability, then fetch every product covering the day; any failure fails the unit."""
        day = request.days[0]
        availability = await self._availability.read(client)
        covering = products_covering(availability.table, day, CANDIDATE_PRODUCTS)
        answers: list[ProductAnswer] = []
        bodies: list[bytes] = []
        retrieved_at = availability.retrieved_at
        # Sequential: the TOML's max_concurrency stays the turn's concurrent-send ceiling.
        for product in covering:
            response = await client.get(request.endpoint, {**request.query(), "source": product})
            features = parse_firms_csv(response.body.decode("utf-8"), product)
            answers.append(ProductAnswer(product=product, features=features))
            bodies.append(product.encode("utf-8") + b"\n" + response.body)
            retrieved_at = max(retrieved_at, response.retrieved_at)
        return SourceResponse(
            request=request,
            body=b"\n".join(bodies),
            retrieved_at=retrieved_at,
            # Never checkpoint-eligible: a replay could not re-read availability (module docstring).
            complete_parameters=frozenset(),
            payload=FirmsDay(covering=covering, answers=tuple(answers)),
        )

    def settle(self, day: date, responses: Sequence[SourceResponse], context: DayContext) -> Settlement:  # noqa: ARG002 - absence here is unconditional (module docstring); no mirrored-past proof needed
        """Expected = the day's covering products; none covering is a loud refusal, a capped one never complete."""
        if not responses:
            return Unsettled("upstream_unavailable", f"no FIRMS answer for {day.isoformat()}")
        firms_day = _firms_day(responses)
        if not firms_day.covering:
            return Unsettled(
                "unsettled",
                f"FIRMS data_availability names no product covering {day.isoformat()} (candidates: "
                f"{', '.join(CANDIDATE_PRODUCTS)}); refusing rather than certifying a fire-free day",
            )
        capped = sorted(
            answer.product for answer in firms_day.answers if len(answer.features) >= FIRMS_RESPONSE_RECORD_CAP
        )
        if capped:
            return Unsettled(
                "unsettled",
                f"{', '.join(capped)} returned >= {FIRMS_RESPONSE_RECORD_CAP} record(s) for {day.isoformat()}; a "
                "response at FIRMS' own silent per-request truncation point can never be proven complete -- see "
                'this module\'s docstring ("The 10,000-record cap") and docs/lanes/fire-detections.md section 5.1',
            )
        expected_units = len(firms_day.covering)
        present_units = len({answer.product for answer in firms_day.answers} & set(firms_day.covering))
        if present_units == expected_units and not any(answer.features for answer in firms_day.answers):
            return Absent(
                reason="every FIRMS product covering the day answered with zero detections",
                proof=f"{', '.join(firms_day.covering)} each answered {day.isoformat()} with zero rows",
            )
        # A short answer is a partial `Written`, which the runner refuses on this `refuse` lane.
        return Written(expected_units=expected_units, present_units=present_units)

    def rows(self, day: date, responses: Sequence[SourceResponse]) -> pa.Table:
        """One row per valued 0.005-degree cell; any identity or geometry defect refuses the whole day."""
        retrieved_at = max(response.retrieved_at for response in responses)
        features = [feature for answer in _firms_day(responses).answers for feature in answer.features]
        writes: dict[str, FeatureWrite] = {}
        rejected = 0
        # SP supersedes NRT on a reprocessing overlap, deterministically (ingest/firms.py precedence rule).
        for feature in collapse_history_records(features):
            write = build_fire_detection_write(feature, FIRE_DETECTIONS_STREAM, None, retrieved_at)
            if write is None:
                rejected += 1
                continue
            observed_at = write.identity.observed_at
            if observed_at is None or observed_at.astimezone(UTC).date() != day:
                raise ValueError(
                    f"FIRMS answered the exact-day request for {day.isoformat()} with a detection observed at "
                    f"{observed_at}; refusing the whole day rather than writing it under the wrong partition"
                )
            writes[write.external_id] = write
        if rejected:
            raise ValueError(
                f"FIRMS returned {rejected} unkeyable or invalid detection(s) for {day.isoformat()}; refusing the "
                'whole day rather than writing a partial one (this lane\'s partial_day = "refuse" policy)'
            )
        cells: dict[tuple[Decimal, Decimal], _CellTotals] = {}
        for write in writes.values():
            geometry = write.properties.get("geometry")
            coordinates = geometry.get("coordinates") if isinstance(geometry, Mapping) else None
            if not isinstance(coordinates, list) or len(coordinates) < _POINT_COORDINATE_COUNT:
                msg = f"FIRMS detection {write.external_id!r} has no point coordinates for {day.isoformat()}"
                raise ValueError(msg)
            longitude = _finite_decimal(coordinates[0], field="longitude", external_id=write.external_id)
            latitude = _finite_decimal(coordinates[1], field="latitude", external_id=write.external_id)
            cell = (_floor_cell(longitude), _floor_cell(latitude))
            observed_at = write.identity.observed_at
            if observed_at is None:  # pragma: no cover - already refused above when None
                raise ValueError(f"FIRMS detection {write.external_id!r} lost its observation timestamp")
            held = cells.setdefault(cell, _CellTotals(newest_observed_at=observed_at))
            held.detection_count += 1
            frp = write.properties.get("frp")
            if isinstance(frp, int | float) and not isinstance(frp, bool):
                held.frp_sum += _finite_decimal(frp, field="frp", external_id=write.external_id)
                held.frp_observation_count += 1
            if write.properties.get("confidenceNormalized") == "high":
                held.high_confidence_detection_count += 1
            held.newest_observed_at = max(held.newest_observed_at, observed_at)
        table_rows = [
            {
                "cell_longitude": float(longitude),
                "cell_latitude": float(latitude),
                "observed_day": day,
                "detection_count": totals.detection_count,
                "frp_sum": float(totals.frp_sum) if totals.frp_observation_count else None,
                "frp_observation_count": totals.frp_observation_count,
                "high_confidence_detection_count": totals.high_confidence_detection_count,
                "newest_observed_at": totals.newest_observed_at,
            }
            for (longitude, latitude), totals in sorted(cells.items())
        ]
        return pa.Table.from_pylist(table_rows, schema=FIRE_DETECTIONS_SCHEMA.arrow_schema)


#: The single instance the lane TOML's `strategy = "fire_detections.nasa_firms"` resolves to (S14).
STRATEGY: Final = NasaFirmsForwardStrategy()


__all__ = [
    "AVAILABILITY_ENDPOINT",
    "CANDIDATE_PRODUCTS",
    "DAY_UNIT_SOURCE",
    "FIRMS_RESPONSE_RECORD_CAP",
    "FORWARD_ENDPOINT",
    "STRATEGY",
    "FirmsDay",
    "NasaFirmsForwardStrategy",
    "ProductAnswer",
]

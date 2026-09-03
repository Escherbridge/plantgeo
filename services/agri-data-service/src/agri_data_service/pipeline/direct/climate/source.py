"""One bounded NASA POWER point request per support cell-day, shared by every product of that day."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from agri_data_service.execution.weather_observations.nasa_power import (
    extract_nasa_power_parameter_values,
    nasa_power_daily_point_url,
    nasa_power_observed_value,
)
from agri_data_service.ingest.http import UpstreamBounds, UpstreamError, fetch_bounded, upstream_client
from agri_data_service.pipeline.direct.climate.products import (
    CLIMATE_DIRECT_SNAPSHOT_PREFIX,
    CLIMATE_SOURCE_PARAMETERS,
)
from agri_data_service.pipeline.direct.climate.support import quantize_coordinate

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import date

    import httpx

    from agri_data_service.pipeline.direct.climate.products import ClimateFieldProduct
    from agri_data_service.pipeline.direct.climate.support import NasaPowerSupport, NasaPowerSupportCell

#: One cell-day carrying all eight parameters measured 1,189 bytes on 2026-08-20 at 46N/119W
#: (`.omc/research/nasa-power-point-response-2026-09-02.json`). The cap is two hundred times that:
#: a legitimate response never trips it and a runaway body is refused unread.
NASA_POWER_POINT_MAX_BYTES: Final = 262_144
NASA_POWER_POINT_TIMEOUT_SECONDS: Final = 30.0
NASA_POWER_POINT_BOUNDS: Final = UpstreamBounds(
    max_bytes=NASA_POWER_POINT_MAX_BYTES,
    timeout_seconds=NASA_POWER_POINT_TIMEOUT_SECONDS,
)

#: Simultaneous point requests against a public, key-free API. See `pipeline/direct/AGENTS.md`.
NASA_POWER_POINT_CONCURRENCY: Final = 4

POINT_ORDINATE_COUNT: Final = 2


class ClimateSourceError(RuntimeError):
    """Raised when POWER cannot support one complete, comparable product-day."""


class ClimateSourceUnsettledError(ClimateSourceError):
    """Raised when POWER answered but the day is not yet whole: a refusal, never a governed absence."""


class ClimateTimeBudgetExhaustedError(RuntimeError):
    """Raised when the turn's clock ran out mid-fetch; deliberately NOT a `ClimateSourceError`.

    See `pipeline/direct/AGENTS.md`, "The turn deadline bounds every wait".
    """


@dataclass(frozen=True, slots=True)
class ClimateCellDayResponse:
    """One support cell's one day, as POWER answered it, carrying every product's parameter."""

    cell: NasaPowerSupportCell
    day: date
    request_url: str
    response_sha256: str
    response_bytes: int
    retrieved_at: datetime
    parameters: Mapping[str, object]


@dataclass(slots=True)
class ClimateSourceCache:
    """One turn's cell-day responses and its request budget, so a turn pays per DAY, not per product.

    See `pipeline/direct/AGENTS.md`, "One point request per support cell-day".
    """

    request_budget: int
    responses: dict[tuple[str, date], ClimateCellDayResponse] = field(default_factory=dict)
    requests_spent: int = 0

    @property
    def remaining_requests(self) -> int:
        """How many upstream requests this turn may still issue."""
        return max(0, self.request_budget - self.requests_spent)

    def missing_cells(self, support: NasaPowerSupport, day: date) -> tuple[NasaPowerSupportCell, ...]:
        """Return the support cells this day holds no response for, in support order."""
        return tuple(cell for cell in support.cells if (cell.cell_key, day) not in self.responses)

    def can_afford(self, support: NasaPowerSupport, day: date) -> bool:
        """True when the remaining budget covers every cell this day still owes a request for."""
        return len(self.missing_cells(support, day)) <= self.remaining_requests

    def hold(self, response: ClimateCellDayResponse) -> None:
        """Record one completed cell-day; a failed request is never held, so a retry re-asks only for it."""
        self.responses[(response.cell.cell_key, response.day)] = response


@dataclass(frozen=True, slots=True)
class ClimateSourceReceipt:
    """The immutable identity of the one request per support cell a product-day was read out of."""

    request_url_sha256: str
    request_count: int
    response_sha256: str
    response_bytes: int
    retrieved_at: datetime
    cell_count: int
    fill_cell_count: int

    @property
    def snapshot_id(self) -> str:
        """Return the `source_snapshot_id` a direct row carries: a `direct:` token over the day's responses."""
        return f"{CLIMATE_DIRECT_SNAPSHOT_PREFIX}{self.response_sha256}"

    def as_event(self) -> dict[str, object]:
        """Render the receipt for a progress record and for a governed absence body."""
        return {
            "request_url_sha256": self.request_url_sha256,
            "request_count": self.request_count,
            "response_sha256": self.response_sha256,
            "response_bytes": self.response_bytes,
            "retrieved_at": self.retrieved_at.isoformat(),
            "cell_count": self.cell_count,
            "fill_cell_count": self.fill_cell_count,
        }


@dataclass(frozen=True, slots=True)
class ClimateCellValue:
    """One support cell of one product-day, and the exact request its value was read out of."""

    cell: NasaPowerSupportCell
    value: float
    response_ordinal: int
    request_url: str
    response_sha256: str


@dataclass(frozen=True, slots=True)
class ClimateDaySource:
    """The complete POWER answer for one product-day, or a complete answer that holds no values."""

    product: ClimateFieldProduct
    day: date
    receipt: ClimateSourceReceipt
    values: tuple[ClimateCellValue, ...]

    @property
    def fill_value_cells(self) -> int:
        """How many support cells answered this day with a POWER fill value rather than a reading."""
        return self.receipt.fill_cell_count

    @property
    def is_governed_absence(self) -> bool:
        """True when POWER answered for every support cell and every value was a fill value."""
        return not self.values


async def fetch_climate_day(  # noqa: PLR0913 - the product, day, support, cache and clocks are distinct
    product: ClimateFieldProduct,
    *,
    day: date,
    support: NasaPowerSupport,
    cache: ClimateSourceCache,
    now: datetime | None = None,
    deadline: float | None = None,
    concurrency: int = NASA_POWER_POINT_CONCURRENCY,
) -> ClimateDaySource:
    """Fill the cache for this day from the point API, then read one product out of it."""
    await fill_cell_day_cache(
        day=day,
        support=support,
        cache=cache,
        now=now,
        deadline=deadline,
        concurrency=concurrency,
    )
    return climate_day_from_cache(product, day=day, support=support, cache=cache)


async def fill_cell_day_cache(  # noqa: PLR0913 - the day, support, cache, clocks and cap are distinct
    *,
    day: date,
    support: NasaPowerSupport,
    cache: ClimateSourceCache,
    now: datetime | None = None,
    deadline: float | None = None,
    concurrency: int = NASA_POWER_POINT_CONCURRENCY,
) -> None:
    """Request every support cell this day still owes, bounded by budget, concurrency and the turn deadline."""
    missing = cache.missing_cells(support, day)
    if not missing:
        return
    if len(missing) > cache.remaining_requests:
        raise ClimateSourceUnsettledError(
            f"{day.isoformat()} needs {len(missing)} more POWER point request(s) and this turn has "
            f"{cache.remaining_requests} left of its budget of {cache.request_budget}"
        )
    require_time_remaining(deadline, day=day)
    gate = asyncio.Semaphore(max(1, concurrency))

    async def one(cell: NasaPowerSupportCell, client: httpx.AsyncClient) -> ClimateCellDayResponse:
        async with gate:
            require_time_remaining(deadline, day=day)
            return await _fetch_cell_day(client, cell, day=day, now=now)

    async with upstream_client(NASA_POWER_POINT_BOUNDS) as client:
        answers = await asyncio.gather(*(one(cell, client) for cell in missing), return_exceptions=True)

    failures: list[tuple[NasaPowerSupportCell, BaseException]] = []
    for cell, answer in zip(missing, answers, strict=True):
        cache.requests_spent += 1
        if isinstance(answer, BaseException):
            failures.append((cell, answer))
            continue
        cache.hold(answer)
    if any(isinstance(error, ClimateTimeBudgetExhaustedError) for _cell, error in failures):
        raise ClimateTimeBudgetExhaustedError(
            f"the turn's time budget ran out while fetching {day.isoformat()}; "
            f"{len(failures)} of {len(missing)} cell request(s) did not complete"
        )
    if failures:
        cell, error = failures[0]
        raise ClimateSourceUnsettledError(
            f"NASA POWER did not answer {len(failures)} of {len(missing)} support cell(s) for "
            f"{day.isoformat()}; a partial day is refused rather than published "
            f"(first: {cell.cell_key} -- {type(error).__name__}: {error})"
        )


def climate_day_from_cache(
    product: ClimateFieldProduct,
    *,
    day: date,
    support: NasaPowerSupport,
    cache: ClimateSourceCache,
) -> ClimateDaySource:
    """Read one product out of the day's held responses, in support order.

    The support bijection is by construction: one request per support cell, matched back by the cell
    the request was built from. Nothing here searches for a cell a returned coordinate might belong
    to. See `pipeline/direct/AGENTS.md`, "One point request per support cell-day".
    """
    responses: list[ClimateCellDayResponse] = []
    for cell in support.cells:
        held = cache.responses.get((cell.cell_key, day))
        if held is None:
            raise ClimateSourceUnsettledError(f"support cell {cell.cell_key!r} holds no response for {day.isoformat()}")
        responses.append(held)
    return build_climate_day(product, day=day, responses=tuple(responses))


def build_climate_day(
    product: ClimateFieldProduct,
    *,
    day: date,
    responses: Sequence[ClimateCellDayResponse],
) -> ClimateDaySource:
    """Bind one complete, support-ordered set of cell-day responses to one product's parameter.

    A cell reporting a fill value contributes no row and does not refuse the day; only an all-fill
    day is a governed absence. See `pipeline/direct/AGENTS.md`, "Fill cells, absence and refusal".
    """
    if not responses:
        raise ClimateSourceUnsettledError(f"{product.stream} {day.isoformat()} was asked to bind no responses at all")
    values: list[ClimateCellValue] = []
    fill_cell_count = 0
    digest = hashlib.sha256()
    urls = hashlib.sha256()
    response_bytes = 0
    retrieved_at = responses[0].retrieved_at
    for ordinal, response in enumerate(responses):
        if response.day != day:
            raise ClimateSourceUnsettledError(
                f"{product.stream} {day.isoformat()} was handed a response for {response.day.isoformat()}"
            )
        digest.update(response.response_sha256.encode("utf-8"))
        urls.update(response.request_url.encode("utf-8"))
        response_bytes += response.response_bytes
        retrieved_at = max(retrieved_at, response.retrieved_at)
        observed = _observed_value(response, product=product, day=day)
        if observed is None:
            fill_cell_count += 1
            continue
        values.append(
            ClimateCellValue(
                cell=response.cell,
                value=observed,
                response_ordinal=ordinal,
                request_url=response.request_url,
                response_sha256=response.response_sha256,
            )
        )
    receipt = ClimateSourceReceipt(
        request_url_sha256=urls.hexdigest(),
        request_count=len(responses),
        response_sha256=digest.hexdigest(),
        response_bytes=response_bytes,
        retrieved_at=retrieved_at,
        cell_count=len(responses),
        fill_cell_count=fill_cell_count,
    )
    return ClimateDaySource(product=product, day=day, receipt=receipt, values=tuple(values))


def parse_climate_point_body(
    cell: NasaPowerSupportCell,
    *,
    day: date,
    body: bytes,
    request_url: str,
    retrieved_at: datetime,
) -> ClimateCellDayResponse:
    """Narrow one untrusted point body to this cell's day, refusing a body that answers another point.

    Bound to a real captured response: `.omc/research/nasa-power-point-response-2026-09-02.json`.
    """
    decoded = _decoded(body, cell=cell, day=day)
    _require_echoed_point(decoded, cell=cell, day=day)
    try:
        parameters = extract_nasa_power_parameter_values(decoded)
    except ValueError as error:
        raise ClimateSourceUnsettledError(
            f"NASA POWER response for {cell.cell_key} {day.isoformat()} is not a daily point payload: {error}"
        ) from error
    stamp = day.strftime("%Y%m%d")
    values: dict[str, object] = {}
    for parameter in CLIMATE_SOURCE_PARAMETERS:
        series = parameters.get(parameter)
        if series is None:
            raise ClimateSourceUnsettledError(
                f"NASA POWER response for {cell.cell_key} {day.isoformat()} omits {parameter}"
            )
        if stamp not in series:
            raise ClimateSourceUnsettledError(
                f"NASA POWER response for {cell.cell_key} omits day {stamp} of {parameter}"
            )
        values[parameter] = series[stamp]
    return ClimateCellDayResponse(
        cell=cell,
        day=day,
        request_url=request_url,
        response_sha256=hashlib.sha256(body).hexdigest(),
        response_bytes=len(body),
        retrieved_at=retrieved_at,
        parameters=values,
    )


def climate_point_url(cell: NasaPowerSupportCell, *, day: date) -> str:
    """Build the one point URL a support cell-day is read out of, carrying every product's parameter."""
    return str(
        nasa_power_daily_point_url(
            latitude=cell.cell_latitude,
            longitude=cell.cell_longitude,
            parameters=CLIMATE_SOURCE_PARAMETERS,
            start_date=day,
            end_date=day,
        )
    )


def require_time_remaining(deadline: float | None, *, day: date) -> None:
    """Stop the fan-out the moment the turn's budget is gone, rather than after 397 more requests."""
    if deadline is not None and time.monotonic() >= deadline:
        raise ClimateTimeBudgetExhaustedError(f"the turn's time budget ran out before {day.isoformat()} completed")


async def _fetch_cell_day(
    client: httpx.AsyncClient,
    cell: NasaPowerSupportCell,
    *,
    day: date,
    now: datetime | None,
) -> ClimateCellDayResponse:
    """Issue one bounded point request and narrow its body, naming the cell-day on every refusal."""
    url = climate_point_url(cell, day=day)
    try:
        response = await fetch_bounded(client, url, NASA_POWER_POINT_BOUNDS)
    except UpstreamError as error:
        raise ClimateSourceUnsettledError(
            f"NASA POWER transport failed for {cell.cell_key} {day.isoformat()}: {type(error).__name__}: {error}"
        ) from error
    if not response.ok:
        raise ClimateSourceUnsettledError(
            f"NASA POWER answered {response.status} for {cell.cell_key} {day.isoformat()}"
        )
    if response.payload_error is not None:
        raise ClimateSourceUnsettledError(
            f"NASA POWER body for {cell.cell_key} {day.isoformat()} was unusable: {response.payload_error}"
        )
    return parse_climate_point_body(
        cell,
        day=day,
        body=response.text.encode("utf-8"),
        request_url=url,
        retrieved_at=receipt_clock(now),
    )


def receipt_clock(now: datetime | None) -> datetime:
    """Return the aware UTC instant a response is stamped with, refusing a naive injected clock."""
    stamped = now if now is not None else datetime.now(UTC)
    if stamped.tzinfo is None or stamped.utcoffset() is None:
        raise ClimateSourceError("the source fetch clock must include a timezone")
    return stamped.astimezone(UTC)


def _decoded(body: bytes, *, cell: NasaPowerSupportCell, day: date) -> Mapping[str, object]:
    """Decode one response body to a JSON object, naming the cell-day on every refusal."""
    try:
        decoded = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise ClimateSourceUnsettledError(
            f"NASA POWER response for {cell.cell_key} {day.isoformat()} is not UTF-8 JSON"
        ) from error
    if not isinstance(decoded, dict):
        raise ClimateSourceUnsettledError(
            f"NASA POWER response for {cell.cell_key} {day.isoformat()} is not a JSON object"
        )
    return decoded


def _require_echoed_point(
    decoded: Mapping[str, object],
    *,
    cell: NasaPowerSupportCell,
    day: date,
) -> None:
    """Refuse a body whose echoed point is not this cell's centroid, so no value binds to a guessed place.

    Every support cell sits on an integer degree, which lands exactly on POWER's 0.5-degree product
    grid, so the service snaps nothing and the echo is an equality check rather than a tolerance.
    """
    geometry = decoded.get("geometry")
    coordinates = geometry.get("coordinates") if isinstance(geometry, dict) else None
    if not isinstance(coordinates, list) or len(coordinates) < POINT_ORDINATE_COUNT:
        raise ClimateSourceUnsettledError(
            f"NASA POWER response for {cell.cell_key} {day.isoformat()} carries no point coordinates"
        )
    longitude, latitude = coordinates[0], coordinates[1]
    if isinstance(longitude, bool) or isinstance(latitude, bool):
        raise ClimateSourceUnsettledError(
            f"NASA POWER response for {cell.cell_key} {day.isoformat()} has boolean coordinates"
        )
    if not isinstance(longitude, int | float) or not isinstance(latitude, int | float):
        raise ClimateSourceUnsettledError(
            f"NASA POWER response for {cell.cell_key} {day.isoformat()} has non-numeric coordinates"
        )
    echoed = (quantize_coordinate(float(longitude)), quantize_coordinate(float(latitude)))
    expected = (quantize_coordinate(cell.cell_longitude), quantize_coordinate(cell.cell_latitude))
    if echoed != expected:
        raise ClimateSourceUnsettledError(
            f"NASA POWER answered point {echoed} for support cell {cell.cell_key!r} at {expected} on "
            f"{day.isoformat()}; a value is never bound to a point that was not asked for"
        )


def _observed_value(
    response: ClimateCellDayResponse,
    *,
    product: ClimateFieldProduct,
    day: date,
) -> float | None:
    """Read one product's parameter out of one held cell-day response, applying POWER's fill rule."""
    if product.source_parameter not in response.parameters:
        raise ClimateSourceUnsettledError(
            f"the held response for {response.cell.cell_key} {day.isoformat()} omits {product.source_parameter}"
        )
    try:
        return nasa_power_observed_value(response.parameters[product.source_parameter])
    except ValueError as error:
        raise ClimateSourceUnsettledError(
            f"NASA POWER returned an unusable {product.source_parameter} value for cell "
            f"{response.cell.cell_key!r} on {day.isoformat()}: {error}"
        ) from error


__all__ = [
    "NASA_POWER_POINT_BOUNDS",
    "NASA_POWER_POINT_CONCURRENCY",
    "NASA_POWER_POINT_MAX_BYTES",
    "NASA_POWER_POINT_TIMEOUT_SECONDS",
    "ClimateCellDayResponse",
    "ClimateCellValue",
    "ClimateDaySource",
    "ClimateSourceCache",
    "ClimateSourceError",
    "ClimateSourceReceipt",
    "ClimateSourceUnsettledError",
    "ClimateTimeBudgetExhaustedError",
    "build_climate_day",
    "climate_day_from_cache",
    "climate_point_url",
    "fetch_climate_day",
    "fill_cell_day_cache",
    "parse_climate_point_body",
    "receipt_clock",
]

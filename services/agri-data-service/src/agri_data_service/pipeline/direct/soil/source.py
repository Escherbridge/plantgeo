"""One bounded Open-Meteo archive request per support chunk-day, shared by every product of that day.

WHY THE REUSE STOPS WHERE IT DOES. The historical lane's whole parse
(`execution/weather_observations/era5_land.py::parse_open_meteo_archive_payload`) is reachable only
through a `HistoricalOpenMeteoArchivePlan`, whose `window` is a `HistoricalBackfillWindow` -- and
that contract REFUSES any span that is not exactly four calendar years
(`execution/backfill_types.py::require_exact_four_calendar_years`). A forward writer asks for one
settled day, so it cannot build that plan, and re-declaring the window rule to get around it would
be a second definition of the historical contract.

What it reuses instead is every governed guard underneath that plan, all of them public and all of
them the same objects the history was normalized through: `archive_daily_request` (the credentialed
URL) and `archive_daily_url` (the keyless one that is recorded), `fetch_archive_daily` (the byte and
rate-limit bound), `fetch_lane_capture` (the retry and 429 policy), `canonical_location_document`,
`ordered_locations`, `validated_grid_point`, `max_grid_offset_degrees`, `nearest_native_grid_point`,
`bounded_numeric_series`, and `OPEN_METEO_ARCHIVE_SIGNAL_SPECIFICATIONS` for units and acceptance
ranges. Only the three checks the historical module keeps private -- its `daily` block, its named-day
axis and its provider-unit assertion -- are restated here, each beside a comment naming its sibling.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from agri_data_service.execution.open_meteo_lane import (
    ISO_DATE_LENGTH,
    bounded_numeric_series,
    fetch_lane_capture,
    max_grid_offset_degrees,
    nearest_native_grid_point,
    ordered_locations,
    validated_grid_point,
)
from agri_data_service.execution.weather_observations.era5_land import (
    OPEN_METEO_ARCHIVE_LANE,
    OPEN_METEO_ARCHIVE_NATIVE_GRID_DEGREES,
    OPEN_METEO_ARCHIVE_SIGNAL_SPECIFICATIONS,
)
from agri_data_service.ingest.http import upstream_client
from agri_data_service.ingest.open_meteo import (
    OPEN_METEO_ARCHIVE_BOUNDS,
    OPEN_METEO_ERA5_LAND_MODEL,
    archive_daily_request,
    archive_daily_url,
    fetch_archive_daily,
)
from agri_data_service.ingest.open_meteo_endpoint import OpenMeteoProductRequest
from agri_data_service.pipeline.direct.soil.products import (
    SOIL_DIRECT_SNAPSHOT_PREFIX,
    SOIL_SOURCE_PARAMETERS,
)
from agri_data_service.pipeline.direct.soil.support import ERA5_LAND_VALUE_CELL_COUNT

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence
    from datetime import date

    import httpx

    from agri_data_service.execution.open_meteo_lane import OpenMeteoLaneCapture
    from agri_data_service.ingest.http import UpstreamError
    from agri_data_service.ingest.open_meteo import ArchiveDailyRequest
    from agri_data_service.pipeline.direct.soil.products import SoilFieldProduct
    from agri_data_service.pipeline.direct.soil.support import Era5LandSupport, Era5LandSupportCell

#: Cells per request. 50 is the `chunk_cell_count` of all three reviewed plans, so a forward chunk
#: asks the provider for exactly the shape four years of history were fetched in. The endpoint's own
#: ceiling is 200 (`ingest/open_meteo.py` MAX_ARCHIVE_LOCATIONS_PER_REQUEST) and quota is weighted by
#: locations x variables x timesteps rather than by request count, so a larger chunk would buy fewer
#: round trips at no quota saving and a four-times larger body to lose on one transport error.
ERA5_LAND_CHUNK_CELL_COUNT: Final = 50

#: Simultaneous chunk requests. Two, matching `open_meteo_lane.DEFAULT_CHUNK_CONCURRENCY`: this is a
#: keyless, quota-weighted public API, and the shared scaffold's own ceiling is four.
ERA5_LAND_CHUNK_CONCURRENCY: Final = 2

#: One settled day is one timestep per variable, so a chunk body is ~50 locations x 8 short arrays.
#: The endpoint bound (`OPEN_METEO_ARCHIVE_BOUNDS`, 64 MiB) is sized for four YEARS of daily rows and
#: is left as it is rather than narrowed here: the byte cost of a day is stated in
#: `pipeline/direct/AGENTS.md` and enforced by the same reader the history was fetched through.
ERA5_LAND_DAY_TIMESTEP_COUNT: Final = 1


class SoilSourceError(RuntimeError):
    """Raised when the archive cannot support one complete, comparable product-day."""


class SoilSourceUnsettledError(SoilSourceError):
    """Raised when the archive answered but the day is not yet whole: a refusal, never an absence."""


class SoilTimeBudgetExhaustedError(RuntimeError):
    """Raised when the turn's clock ran out mid-fetch; deliberately NOT a `SoilSourceError`.

    See `pipeline/direct/AGENTS.md`, "The turn deadline bounds every wait".
    """


@dataclass(frozen=True, slots=True)
class Era5LandChunk:
    """One bounded multi-location request, cut from the support in its own stable cell order."""

    key: str
    cells: tuple[Era5LandSupportCell, ...]


@dataclass(frozen=True, slots=True)
class SoilChunkDayResponse:
    """One chunk's one day, as the archive answered it, carrying every product's variable."""

    chunk: Era5LandChunk
    day: date
    request_url: str
    response_sha256: str
    response_bytes: int
    retrieved_at: datetime
    #: `(cell_key, source_parameter)` -> the day's value, or None where the archive modelled nothing.
    values: Mapping[tuple[str, str], float | None]


@dataclass(slots=True)
class SoilSourceCache:
    """One turn's chunk-day responses and its request budget, so a turn pays per DAY, not per product.

    See `pipeline/direct/AGENTS.md`, "One archive request per support chunk-day".
    """

    request_budget: int
    responses: dict[tuple[str, date], SoilChunkDayResponse] = field(default_factory=dict)
    requests_spent: int = 0

    @property
    def remaining_requests(self) -> int:
        """How many upstream requests this turn may still issue."""
        return max(0, self.request_budget - self.requests_spent)

    def missing_chunks(self, chunks: Sequence[Era5LandChunk], day: date) -> tuple[Era5LandChunk, ...]:
        """Return the chunks this day holds no response for, in chunk order."""
        return tuple(chunk for chunk in chunks if (chunk.key, day) not in self.responses)

    def can_afford(self, chunks: Sequence[Era5LandChunk], day: date) -> bool:
        """True when the remaining budget covers every chunk this day still owes a request for."""
        return len(self.missing_chunks(chunks, day)) <= self.remaining_requests

    def hold(self, response: SoilChunkDayResponse) -> None:
        """Record one completed chunk-day; a failed request is never held, so a retry re-asks only for it."""
        self.responses[(response.chunk.key, response.day)] = response


@dataclass(frozen=True, slots=True)
class SoilSourceReceipt:
    """The immutable identity of the requests one product-day was read out of."""

    request_url_sha256: str
    request_count: int
    response_sha256: str
    response_bytes: int
    retrieved_at: datetime
    cell_count: int
    null_cell_count: int

    @property
    def snapshot_id(self) -> str:
        """Return the `direct:` token a direct row's lineage columns are scoped by."""
        return f"{SOIL_DIRECT_SNAPSHOT_PREFIX}{self.response_sha256}"

    def as_event(self) -> dict[str, object]:
        """Render the receipt for a progress record and for a governed absence body."""
        return {
            "request_url_sha256": self.request_url_sha256,
            "request_count": self.request_count,
            "response_sha256": self.response_sha256,
            "response_bytes": self.response_bytes,
            "retrieved_at": self.retrieved_at.isoformat(),
            "cell_count": self.cell_count,
            "null_cell_count": self.null_cell_count,
        }


@dataclass(frozen=True, slots=True)
class SoilCellValue:
    """One support cell of one product-day, and the exact request its value was read out of."""

    cell: Era5LandSupportCell
    value: float
    support_ordinal: int
    request_url: str
    response_sha256: str


@dataclass(frozen=True, slots=True)
class SoilDaySource:
    """The complete archive answer for one product-day, or a complete answer that holds no values."""

    product: SoilFieldProduct
    day: date
    receipt: SoilSourceReceipt
    values: tuple[SoilCellValue, ...]

    @property
    def null_value_cells(self) -> int:
        """How many support cells answered this day with no value at all."""
        return self.receipt.null_cell_count

    @property
    def is_governed_absence(self) -> bool:
        """True when the archive answered for every support cell and every value was null."""
        return not self.values


def support_chunks(support: Era5LandSupport) -> tuple[Era5LandChunk, ...]:
    """Cut the ordered support into stable request chunks, refusing a lattice the archive cannot separate.

    The native-grid uniqueness check is the historical plan validator's
    (`HistoricalOpenMeteoArchivePlan.require_governed_lattice`), restated because that validator is
    only reachable through a four-year window. Two support cells that round to one 0.1-degree box
    would receive one another's value under `cell_selection=nearest`.
    """
    native_points = {
        nearest_native_grid_point(cell.analysis_cell, OPEN_METEO_ARCHIVE_NATIVE_GRID_DEGREES) for cell in support.cells
    }
    if len(native_points) != len(support.cells):
        raise SoilSourceError(
            f"the support's {len(support.cells)} cells occupy only {len(native_points)} distinct "
            f"{OPEN_METEO_ARCHIVE_NATIVE_GRID_DEGREES}-degree ERA5-Land boxes; two cells sharing one box "
            "cannot be told apart in the archive's answer"
        )
    size = ERA5_LAND_CHUNK_CELL_COUNT
    return tuple(
        Era5LandChunk(key=f"cells-{index // size:04d}", cells=tuple(support.cells[index : index + size]))
        for index in range(0, len(support.cells), size)
    )


async def fetch_soil_day(  # noqa: PLR0913 - the product, day, chunks, cache and clocks are distinct
    product: SoilFieldProduct,
    *,
    day: date,
    support: Era5LandSupport,
    chunks: Sequence[Era5LandChunk],
    cache: SoilSourceCache,
    now: datetime | None = None,
    deadline: float | None = None,
    concurrency: int = ERA5_LAND_CHUNK_CONCURRENCY,
) -> SoilDaySource:
    """Fill the cache for this day from the archive, then read one product out of it."""
    await fill_chunk_day_cache(
        day=day,
        chunks=chunks,
        cache=cache,
        now=now,
        deadline=deadline,
        concurrency=concurrency,
    )
    return soil_day_from_cache(product, day=day, support=support, chunks=chunks, cache=cache)


async def fill_chunk_day_cache(  # noqa: PLR0913 - the day, chunks, cache, clocks and cap are distinct
    *,
    day: date,
    chunks: Sequence[Era5LandChunk],
    cache: SoilSourceCache,
    now: datetime | None = None,
    deadline: float | None = None,
    concurrency: int = ERA5_LAND_CHUNK_CONCURRENCY,
) -> None:
    """Request every chunk this day still owes, bounded by budget, concurrency and the turn deadline."""
    missing = cache.missing_chunks(chunks, day)
    if not missing:
        return
    if len(missing) > cache.remaining_requests:
        raise SoilSourceUnsettledError(
            f"{day.isoformat()} needs {len(missing)} more archive request(s) and this turn has "
            f"{cache.remaining_requests} left of its budget of {cache.request_budget}"
        )
    require_time_remaining(deadline, day=day)
    gate = asyncio.Semaphore(max(1, concurrency))

    async def one(chunk: Era5LandChunk, client: httpx.AsyncClient) -> SoilChunkDayResponse:
        async with gate:
            require_time_remaining(deadline, day=day)
            return await _fetch_chunk_day(client, chunk, day=day, now=now, deadline=deadline)

    async with upstream_client(OPEN_METEO_ARCHIVE_BOUNDS) as client:
        answers = await asyncio.gather(*(one(chunk, client) for chunk in missing), return_exceptions=True)

    failures: list[tuple[Era5LandChunk, BaseException]] = []
    for chunk, answer in zip(missing, answers, strict=True):
        cache.requests_spent += 1
        if isinstance(answer, BaseException):
            failures.append((chunk, answer))
            continue
        cache.hold(answer)
    if any(isinstance(error, SoilTimeBudgetExhaustedError) for _chunk, error in failures):
        raise SoilTimeBudgetExhaustedError(
            f"the turn's time budget ran out while fetching {day.isoformat()}; "
            f"{len(failures)} of {len(missing)} chunk request(s) did not complete"
        )
    if failures:
        chunk, error = failures[0]
        raise SoilSourceUnsettledError(
            f"the Open-Meteo archive did not answer {len(failures)} of {len(missing)} chunk(s) for "
            f"{day.isoformat()}; a partial day is refused rather than published "
            f"(first: {chunk.key} -- {type(error).__name__}: {error})"
        )


def soil_day_from_cache(
    product: SoilFieldProduct,
    *,
    day: date,
    support: Era5LandSupport,
    chunks: Sequence[Era5LandChunk],
    cache: SoilSourceCache,
) -> SoilDaySource:
    """Read one product out of the day's held responses, in support order."""
    responses: list[SoilChunkDayResponse] = []
    for chunk in chunks:
        held = cache.responses.get((chunk.key, day))
        if held is None:
            raise SoilSourceUnsettledError(f"chunk {chunk.key!r} holds no response for {day.isoformat()}")
        responses.append(held)
    return build_soil_day(product, day=day, support=support, responses=tuple(responses))


def build_soil_day(
    product: SoilFieldProduct,
    *,
    day: date,
    support: Era5LandSupport,
    responses: Sequence[SoilChunkDayResponse],
) -> SoilDaySource:
    """Bind one complete, support-ordered set of chunk-day responses to one product's variable.

    A cell the archive modelled nothing for contributes no row and does not refuse the day; only an
    all-null day is a governed absence, and a day whose value count is neither zero nor the pinned
    `ERA5_LAND_VALUE_CELL_COUNT` is refused. See `pipeline/direct/AGENTS.md`, "Null cells, absence
    and refusal".
    """
    if not responses:
        raise SoilSourceUnsettledError(f"{product.stream} {day.isoformat()} was asked to bind no responses at all")
    held: dict[tuple[str, str], SoilChunkDayResponse] = {}
    digest = hashlib.sha256()
    urls = hashlib.sha256()
    response_bytes = 0
    retrieved_at = responses[0].retrieved_at
    for response in responses:
        if response.day != day:
            raise SoilSourceUnsettledError(
                f"{product.stream} {day.isoformat()} was handed a response for {response.day.isoformat()}"
            )
        digest.update(response.response_sha256.encode("utf-8"))
        urls.update(response.request_url.encode("utf-8"))
        response_bytes += response.response_bytes
        retrieved_at = max(retrieved_at, response.retrieved_at)
        for cell in response.chunk.cells:
            held[(cell.cell_key, product.source_parameter)] = response

    values: list[SoilCellValue] = []
    null_cell_count = 0
    for ordinal, cell in enumerate(support.cells):
        key = (cell.cell_key, product.source_parameter)
        covering_response = held.get(key)
        if covering_response is None:
            raise SoilSourceUnsettledError(
                f"{product.stream} {day.isoformat()} holds no chunk covering support cell {cell.cell_key!r}"
            )
        if key not in covering_response.values:
            raise SoilSourceUnsettledError(
                f"the held response for {covering_response.chunk.key} {day.isoformat()} omits "
                f"{product.source_parameter} at {cell.cell_key!r}"
            )
        observed = covering_response.values[key]
        if observed is None:
            null_cell_count += 1
            continue
        values.append(
            SoilCellValue(
                cell=cell,
                value=observed,
                support_ordinal=ordinal,
                request_url=covering_response.request_url,
                response_sha256=covering_response.response_sha256,
            )
        )
    if values and len(values) != ERA5_LAND_VALUE_CELL_COUNT:
        raise SoilSourceUnsettledError(
            f"{product.stream} {day.isoformat()} carries {len(values)} value cells, not the "
            f"{ERA5_LAND_VALUE_CELL_COUNT} every one of the 1,556 immutable days holds; a day on a "
            "different land-sea mask is not comparable with the history it extends, so it is refused "
            "rather than published thin"
        )
    receipt = SoilSourceReceipt(
        request_url_sha256=urls.hexdigest(),
        request_count=len(responses),
        response_sha256=digest.hexdigest(),
        response_bytes=response_bytes,
        retrieved_at=retrieved_at,
        cell_count=len(support.cells),
        null_cell_count=null_cell_count,
    )
    return SoilDaySource(product=product, day=day, receipt=receipt, values=tuple(values))


def parse_soil_chunk_body(
    chunk: Era5LandChunk,
    *,
    day: date,
    body: bytes,
    request_url: str,
    retrieved_at: datetime,
) -> SoilChunkDayResponse:
    """Narrow one canonicalized archive body to this chunk's day, refusing a body that answers elsewhere.

    `body` is the CANONICAL document (`open_meteo_lane.canonical_location_document`), not the wire
    bytes: the provider stamps every response with `generationtime_ms`, so a checksum over the wire
    body would differ between two retrievals of identical content.
    """
    try:
        locations = ordered_locations(OPEN_METEO_ARCHIVE_LANE, body, len(chunk.cells))
    except ValueError as error:
        raise SoilSourceUnsettledError(
            f"the archive response for {chunk.key} {day.isoformat()} is not one entry per requested cell: {error}"
        ) from error
    max_offset = max_grid_offset_degrees(OPEN_METEO_ARCHIVE_NATIVE_GRID_DEGREES)
    values: dict[tuple[str, str], float | None] = {}
    seen_grid_points: set[tuple[float, float]] = set()
    for cell, location in zip(chunk.cells, locations, strict=True):
        try:
            latitude, longitude = validated_grid_point(
                OPEN_METEO_ARCHIVE_LANE, cell.analysis_cell, location, max_offset
            )
        except ValueError as error:
            raise SoilSourceUnsettledError(
                f"the archive answered a point that is not support cell {cell.cell_key!r} on {day.isoformat()}: {error}"
            ) from error
        if (latitude, longitude) in seen_grid_points:
            raise SoilSourceUnsettledError(
                f"the archive returned one native grid point for two support cells in {chunk.key} on "
                f"{day.isoformat()}; a value would bind to whichever cell was read second"
            )
        seen_grid_points.add((latitude, longitude))
        daily = _daily_block(location, cell=cell, day=day)
        _require_named_day(daily, cell=cell, day=day)
        for parameter in SOIL_SOURCE_PARAMETERS:
            specification = OPEN_METEO_ARCHIVE_SIGNAL_SPECIFICATIONS[parameter]
            _require_provider_unit(location, parameter=parameter, cell=cell, day=day)
            try:
                series = bounded_numeric_series(
                    OPEN_METEO_ARCHIVE_LANE,
                    daily,
                    parameter,
                    minimum=specification.minimum,
                    maximum=specification.maximum,
                    expected_count=ERA5_LAND_DAY_TIMESTEP_COUNT,
                    subject="variable",
                )
            except ValueError as error:
                raise SoilSourceUnsettledError(
                    f"the archive returned an unusable {parameter} value for support cell "
                    f"{cell.cell_key!r} on {day.isoformat()}: {error}"
                ) from error
            values[(cell.cell_key, parameter)] = series[0]
    return SoilChunkDayResponse(
        chunk=chunk,
        day=day,
        request_url=request_url,
        response_sha256=hashlib.sha256(body).hexdigest(),
        response_bytes=len(body),
        retrieved_at=retrieved_at,
        values=values,
    )


def soil_chunk_url(chunk: Era5LandChunk, *, day: date) -> str:
    """Build the CREDENTIAL-FREE chunk URL a support chunk-day is recorded under.

    `archive_daily_url`, never `archive_daily_request().request_url`: the second one carries
    `OPEN_METEO_API_KEY` when the process holds one, and this string is written into every row's
    `selected_source_part_key`. See `execution/AGENTS.md`, "Where the key may and may not appear".
    """
    return archive_daily_url(
        [(cell.cell_latitude, cell.cell_longitude) for cell in chunk.cells],
        SOIL_SOURCE_PARAMETERS,
        day,
        day,
        model=OPEN_METEO_ERA5_LAND_MODEL,
    )


def require_time_remaining(deadline: float | None, *, day: date) -> None:
    """Stop the fan-out the moment the turn's budget is gone, rather than after 31 more chunks."""
    if deadline is not None and time.monotonic() >= deadline:
        raise SoilTimeBudgetExhaustedError(f"the turn's time budget ran out before {day.isoformat()} completed")


def receipt_clock(now: datetime | None) -> datetime:
    """Return the aware UTC instant a response is stamped with, refusing a naive injected clock."""
    stamped = now if now is not None else datetime.now(UTC)
    if stamped.tzinfo is None or stamped.utcoffset() is None:
        raise SoilSourceError("the source fetch clock must include a timezone")
    return stamped.astimezone(UTC)


def deadline_bounded_sleep(deadline: float | None, *, day: date) -> Callable[[float], Awaitable[None]]:
    """Return the waiter the shared fetch scaffold backs off through, bounded by this turn's clock.

    `fetch_lane_capture` sleeps 70 s on a minutely quota refusal and 15 s, 30 s, 45 s on transport
    errors. Left to `asyncio.sleep` those waits are unbounded by the turn, so one walled chunk could
    hold the whole budget and the executor's SIGKILL would land on a writer holding a session lock.
    """

    async def waiter(seconds: float) -> None:
        require_time_remaining(deadline, day=day)
        if deadline is not None and time.monotonic() + seconds >= deadline:
            raise SoilTimeBudgetExhaustedError(
                f"backing off {seconds:g}s for {day.isoformat()} would outlast this turn's time budget"
            )
        await asyncio.sleep(seconds)

    return waiter


async def _fetch_chunk_day(
    client: httpx.AsyncClient,
    chunk: Era5LandChunk,
    *,
    day: date,
    now: datetime | None,
    deadline: float | None,
) -> SoilChunkDayResponse:
    """Issue one bounded chunk request and narrow its body, naming the chunk-day on every refusal."""
    keyless_url = soil_chunk_url(chunk, day=day)
    request = archive_daily_request(
        [(cell.cell_latitude, cell.cell_longitude) for cell in chunk.cells],
        SOIL_SOURCE_PARAMETERS,
        day,
        day,
        model=OPEN_METEO_ERA5_LAND_MODEL,
    )
    capture: OpenMeteoLaneCapture = await _capture_chunk(
        client,
        chunk,
        request=request,
        day=day,
        now=now,
        deadline=deadline,
    )
    return parse_soil_chunk_body(
        chunk,
        day=day,
        body=capture.canonical_payload,
        request_url=keyless_url,
        retrieved_at=receipt_clock(now or capture.retrieved_at),
    )


async def _capture_chunk(  # noqa: PLR0913 - the client, chunk, request, day and clocks are distinct
    client: httpx.AsyncClient,
    chunk: Era5LandChunk,
    *,
    request: ArchiveDailyRequest,
    day: date,
    now: datetime | None,
    deadline: float | None,
) -> OpenMeteoLaneCapture:
    """Run one chunk through the shared retry/quota policy, translating its failure into a refusal."""

    def refuse(chunk_key: str, cause: UpstreamError | None, attempts: int) -> Exception:
        return SoilSourceUnsettledError(
            f"the Open-Meteo archive refused {chunk_key} for {day.isoformat()} after {attempts} attempt(s): "
            f"{type(cause).__name__ if cause is not None else 'no response'}: {cause}"
        )

    return await fetch_lane_capture(
        OPEN_METEO_ARCHIVE_LANE,
        chunk.key,
        # The archive builder predates `OpenMeteoProductRequest`; restating the two fields it already
        # resolved is the whole adaptation, exactly as `fetch_open_meteo_archive_chunk` does it.
        OpenMeteoProductRequest(base_url=request.base_url, request_url=request.request_url),
        client=client,
        fetch_text=fetch_archive_daily,
        error_factory=refuse,
        retrieved_at=now,
        sleep=deadline_bounded_sleep(deadline, day=day),
    )


def _daily_block(location: Mapping[str, object], *, cell: Era5LandSupportCell, day: date) -> dict[str, object]:
    """Read the `daily` block, refusing a location that omits a requested variable.

    Mirrors `execution/weather_observations/era5_land.py::_archive_daily_block`, which is private to
    the four-year plan path.
    """
    daily = location.get("daily")
    if not isinstance(daily, dict):
        raise SoilSourceUnsettledError(
            f"the archive location for {cell.cell_key!r} on {day.isoformat()} is missing its daily block"
        )
    missing = sorted(set(SOIL_SOURCE_PARAMETERS).difference(daily))
    if missing:
        raise SoilSourceUnsettledError(
            f"the archive location for {cell.cell_key!r} on {day.isoformat()} omits: {', '.join(missing)}"
        )
    return daily


def _require_named_day(daily: Mapping[str, object], *, cell: Era5LandSupportCell, day: date) -> None:
    """Refuse a body whose daily axis is not exactly the day that was asked for.

    THE DAY IS THE PUBLISHER'S OWN ISO PREFIX, never an instant recast into a local calendar --
    mirrors `_archive_days`, and the reason `time_zone` is pinned to GMT on every request.
    """
    raw = daily.get("time")
    if not isinstance(raw, list) or len(raw) != ERA5_LAND_DAY_TIMESTEP_COUNT:
        raise SoilSourceUnsettledError(
            f"the archive location for {cell.cell_key!r} on {day.isoformat()} does not carry exactly "
            f"{ERA5_LAND_DAY_TIMESTEP_COUNT} daily timestep(s)"
        )
    named = raw[0]
    if not isinstance(named, str) or len(named) < ISO_DATE_LENGTH or named[:ISO_DATE_LENGTH] != day.isoformat():
        raise SoilSourceUnsettledError(
            f"the archive answered day {named!r} for support cell {cell.cell_key!r} when "
            f"{day.isoformat()} was asked for"
        )


def _require_provider_unit(
    location: Mapping[str, object],
    *,
    parameter: str,
    cell: Era5LandSupportCell,
    day: date,
) -> None:
    """Reject a payload whose provider unit drifted from the one the mapping was verified against.

    Mirrors `_require_provider_unit` in the historical module, including its silence for the
    variables whose provider spelling was never confirmed live: asserting a guessed Unicode string
    would reject every valid payload. None of these eight products declares one today, so this is a
    guard that arms itself if one is ever verified and added.
    """
    expected = OPEN_METEO_ARCHIVE_SIGNAL_SPECIFICATIONS[parameter].provider_unit
    if expected is None:
        return
    daily_units = location.get("daily_units")
    if not isinstance(daily_units, dict):
        raise SoilSourceUnsettledError(
            f"the archive location for {cell.cell_key!r} on {day.isoformat()} is missing its daily_units block"
        )
    reported = daily_units.get(parameter)
    if reported != expected:
        raise SoilSourceUnsettledError(
            f"the archive reported unit {reported!r} for {parameter!r} at {cell.cell_key!r} on "
            f"{day.isoformat()}, expected {expected!r}"
        )


__all__ = [
    "ERA5_LAND_CHUNK_CELL_COUNT",
    "ERA5_LAND_CHUNK_CONCURRENCY",
    "ERA5_LAND_DAY_TIMESTEP_COUNT",
    "Era5LandChunk",
    "SoilCellValue",
    "SoilChunkDayResponse",
    "SoilDaySource",
    "SoilSourceCache",
    "SoilSourceError",
    "SoilSourceReceipt",
    "SoilSourceUnsettledError",
    "SoilTimeBudgetExhaustedError",
    "build_soil_day",
    "deadline_bounded_sleep",
    "fetch_soil_day",
    "fill_chunk_day_cache",
    "parse_soil_chunk_body",
    "receipt_clock",
    "soil_chunk_url",
    "soil_day_from_cache",
    "support_chunks",
]

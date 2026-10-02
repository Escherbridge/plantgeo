"""Gate legacy shortwave work on bounded UTC POWER probes; see AGENTS.md."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import timedelta
from http import HTTPStatus
from typing import TYPE_CHECKING, Final

from agri_data_service.execution.weather_observations.nasa_power import (
    nasa_power_daily_point_url,
    nasa_power_observed_value,
)
from agri_data_service.ingest.http import UpstreamError, fetch_bounded, upstream_client
from agri_data_service.pipeline.direct.climate.source import (
    NASA_POWER_POINT_BOUNDS,
    ClimateProviderDeferredError,
    ClimateSourceError,
    parse_climate_point_body,
    receipt_clock,
)
from agri_data_service.pipeline.runner.contract import ProbeWindow, ProviderEdge

if TYPE_CHECKING:
    from datetime import date

    from agri_data_service.pipeline.direct.climate.products import ClimateFieldProduct
    from agri_data_service.pipeline.direct.climate.source import ClimateSourceCache
    from agri_data_service.pipeline.direct.climate.support import NasaPowerSupport, NasaPowerSupportCell

SHORTWAVE_PROBE_REQUESTS: Final = 3
SHORTWAVE_PROBE_MAX_DAYS: Final = 400


async def probe_shortwave_edge(  # noqa: PLR0913 - support, window and turn bounds are independent
    product: ClimateFieldProduct,
    *,
    support: NasaPowerSupport,
    cache: ClimateSourceCache,
    first_day: date,
    last_day: date,
    deadline: float,
) -> ProviderEdge:
    """Measure solar availability without treating three points as a publication or absence proof."""
    window = ProbeWindow(first_day, last_day)
    if not 1 <= (last_day - first_day).days + 1 <= SHORTWAVE_PROBE_MAX_DAYS:
        raise ValueError("shortwave edge probe must fit the bounded backlog window")
    refusal = _probe_preflight(cache, window=window, deadline=deadline)
    if refusal is not None:
        return refusal
    cells = (support.cells[0], support.cells[len(support.cells) // 2], support.cells[-1])
    try:
        async with asyncio.timeout(max(0.0, deadline - time.monotonic())):
            return await _fetch_probe_cells(product, cells, window=window, cache=cache)
    except TimeoutError:
        return ProviderEdge("unavailable", window, detail="time_budget_exhausted")
    except UpstreamError as error:
        return ProviderEdge("unavailable", window, detail=f"{type(error).__name__}: {error}")
    except (ClimateSourceError, ValueError, ArithmeticError) as error:
        return ProviderEdge("invalid", window, detail=f"{type(error).__name__}: {error}")


def _probe_preflight(cache: ClimateSourceCache, *, window: ProbeWindow, deadline: float) -> ProviderEdge | None:
    """Report an existing quota, request-budget or deadline refusal before starting the probe."""
    if cache.deferred_refusal is not None:
        return ProviderEdge("deferred", window, detail=str(cache.deferred_refusal))
    if cache.remaining_requests < SHORTWAVE_PROBE_REQUESTS:
        return ProviderEdge("unavailable", window, detail="request_budget_exhausted")
    if time.monotonic() >= deadline:
        return ProviderEdge("unavailable", window, detail="time_budget_exhausted")
    return None


async def _fetch_probe_cells(
    product: ClimateFieldProduct,
    cells: tuple[NasaPowerSupportCell, ...],
    *,
    window: ProbeWindow,
    cache: ClimateSourceCache,
) -> ProviderEdge:
    """Charge every started probe once and stop immediately on an incomplete or throttled answer."""
    valued_days: set[date] = set()
    async with upstream_client(NASA_POWER_POINT_BOUNDS) as client:
        for cell in cells:
            url = str(
                nasa_power_daily_point_url(
                    latitude=cell.cell_latitude,
                    longitude=cell.cell_longitude,
                    parameters=(product.source_parameter,),
                    start_date=window.first,
                    end_date=window.last,
                    time_standard="UTC",
                )
            )
            cache.requests_spent += 1
            response = await fetch_bounded(client, url, NASA_POWER_POINT_BOUNDS)
            if response.status == HTTPStatus.TOO_MANY_REQUESTS:
                cache.deferred_refusal = ClimateProviderDeferredError("NASA POWER shortwave edge probe answered 429")
                return ProviderEdge("deferred", window, detail=str(cache.deferred_refusal))
            if not response.ok:
                return ProviderEdge("unavailable", window, detail=f"NASA POWER edge probe answered {response.status}")
            if response.payload_error is not None:
                return ProviderEdge("invalid", window, detail=str(response.payload_error))
            valued_days.update(
                _valued_probe_days(
                    response.text.encode("utf-8"), cell=cell, product=product, window=window, request_url=url
                )
            )
    return ProviderEdge("ok", window, frozenset(valued_days))


def _valued_probe_days(
    body: bytes,
    *,
    cell: NasaPowerSupportCell,
    product: ClimateFieldProduct,
    window: ProbeWindow,
    request_url: str,
) -> set[date]:
    """Reuse the point parser for every requested day and require the echoed UTC time standard."""
    decoded = json.loads(body)
    header = decoded.get("header") if isinstance(decoded, dict) else None
    if not isinstance(header, dict) or header.get("time_standard") != "UTC":
        raise ValueError("NASA POWER edge probe did not echo the requested UTC time standard")
    valued: set[date] = set()
    retrieved_at = receipt_clock(None)
    for offset in range((window.last - window.first).days + 1):
        day = window.first + timedelta(days=offset)
        response = parse_climate_point_body(
            cell,
            day=day,
            body=body,
            request_url=request_url,
            retrieved_at=retrieved_at,
            required_parameters=(product.source_parameter,),
        )
        if nasa_power_observed_value(response.parameters[product.source_parameter]) is not None:
            valued.add(day)
    return valued

"""Query-parameter models: every `/api/v1/ml` parameter is validated once, here, before any read.

Layer L4. Why a day travels as a strict `YYYY-MM-DD` string rather than as a parsed instant, and why
every model forbids extras, live in `AGENTS.md` in this directory.
"""

from __future__ import annotations

import math
import re
from datetime import date
from typing import Annotated, Final

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, ValidationError

#: `YYYY-MM-DD` and nothing else. A `T` or a `Z` here is how 6,279 of 16,743 water-gauge rows once
#: moved to the following calendar day, so an instant is REFUSED rather than truncated to its date.
CALENDAR_DAY_PATTERN: Final = re.compile(r"^\d{4}-\d{2}-\d{2}$")

#: A lane slug, a model kind and a cell identifier are all short, opaque tokens. The ceilings are
#: about the REQUEST, not about the vocabulary: an unbounded parameter is a listing key we would
#: otherwise assemble from whatever the caller sent.
MAX_SLUG_LENGTH: Final = 64
MAX_CELL_ID_LENGTH: Final = 128

MIN_LONGITUDE: Final = -180.0
MAX_LONGITUDE: Final = 180.0
MIN_LATITUDE: Final = -90.0
MAX_LATITUDE: Final = 90.0


def parse_calendar_day(value: object) -> object:
    """Refuse anything that is not a bare `YYYY-MM-DD` day, then parse it."""
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not CALENDAR_DAY_PATTERN.match(value.strip()):
        raise ValueError(
            "must be a YYYY-MM-DD calendar day; a day carries no time and no zone, and converting one would "
            "move rows onto the neighbouring day"
        )
    return date.fromisoformat(value.strip())


def parse_finite_coordinate(value: object) -> object:
    """Refuse a non-finite coordinate before the range check, which `nan` would silently pass."""
    if isinstance(value, str):
        try:
            value = float(value.strip())
        except ValueError as error:
            raise ValueError("must be a decimal number") from error
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("must be a finite number; nan and infinity compare false against every bound")
    return value


CalendarDay = Annotated[date, BeforeValidator(parse_calendar_day)]
Longitude = Annotated[float, BeforeValidator(parse_finite_coordinate), Field(ge=MIN_LONGITUDE, le=MAX_LONGITUDE)]
Latitude = Annotated[float, BeforeValidator(parse_finite_coordinate), Field(ge=MIN_LATITUDE, le=MAX_LATITUDE)]
Slug = Annotated[str, Field(min_length=1, max_length=MAX_SLUG_LENGTH, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")]
CellIdentifier = Annotated[str, Field(min_length=1, max_length=MAX_CELL_ID_LENGTH, pattern=r"^[A-Za-z0-9_.:-]+$")]


class _BoundedQuery(BaseModel):
    """Every query model forbids extras: an unread parameter is a caller who thinks it was honoured."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class FireRiskQuery(_BoundedQuery):
    """`GET /api/v1/ml/fire-risk?lon&lat&day`: one point, one valid day."""

    lon: Longitude
    lat: Latitude
    day: CalendarDay


class AnalogsQuery(_BoundedQuery):
    """`GET /api/v1/ml/analogs?cell_id&origin`: one signal cell, one issue day."""

    cell_id: CellIdentifier
    origin: CalendarDay


class ForecastSummaryQuery(_BoundedQuery):
    """`GET /api/v1/ml/forecast-summary?layer&lon&lat&day`: one lane, one point, one valid day."""

    layer: Slug
    lon: Longitude
    lat: Latitude
    day: CalendarDay


class ArtifactKindPath(_BoundedQuery):
    """The `<kind>` path segment of `GET /api/v1/ml/artifacts/<kind>`."""

    kind: Annotated[str, Field(min_length=1, max_length=MAX_SLUG_LENGTH, pattern=r"^[a-z0-9]+(?:[_-][a-z0-9]+)*$")]


def field_errors(error: ValidationError) -> list[dict[str, str]]:
    """Render a validation failure as one entry per offending field, naming the field."""
    return [
        {"field": ".".join(str(part) for part in entry["loc"]) or "<request>", "message": str(entry["msg"])}
        for entry in error.errors()
    ]


__all__ = [
    "CALENDAR_DAY_PATTERN",
    "MAX_CELL_ID_LENGTH",
    "MAX_SLUG_LENGTH",
    "AnalogsQuery",
    "ArtifactKindPath",
    "FireRiskQuery",
    "ForecastSummaryQuery",
    "ValidationError",
    "field_errors",
    "parse_calendar_day",
    "parse_finite_coordinate",
]

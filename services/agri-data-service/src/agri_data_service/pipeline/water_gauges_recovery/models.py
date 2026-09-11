"""Bounded request and complete response identities for NWIS daily values."""

from __future__ import annotations

from datetime import date, datetime
from typing import Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agri_data_service.ingest.usgs_nwis import tile_bbox

MAX_RESPONSE_BYTES: Final = 8 * 1024 * 1024
MAX_TOTAL_BYTES: Final = 64 * 1024 * 1024
MAX_MANIFEST_BYTES: Final = 64 * 1024
MAX_RECORDS: Final = 50_000
MAX_TILES: Final = 16
MAX_DAYS: Final = 15
MAX_CAPTURE_SECONDS: Final = 600
MAX_ABS_LONGITUDE: Final = 180
MAX_ABS_LATITUDE: Final = 90
HISTORY_FLOOR: Final = date(2022, 8, 5)


class TileResponse(BaseModel):
    """Complete JSON entity bytes for one canonical request tile."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    tile: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    byte_length: int = Field(gt=0, le=MAX_RESPONSE_BYTES)
    captured_at: datetime
    status_code: Literal[200]
    transport_complete: Literal[True]


class WaterSourceCapture(BaseModel):
    """Pinned daily-mean request scope, with inclusive start and exclusive end."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    schema_version: Literal["nwis-daily-source-capture/v1"]
    start_day: date
    end_day_exclusive: date
    bbox: tuple[float, float, float, float]
    parameter_cd: Literal["00060"]
    statistic_cd: Literal["00003"]
    site_type: Literal["ST"]
    site_status_filter: Literal["all"]
    capture_started_at: datetime
    capture_finished_at: datetime
    request_count: int = Field(ge=1, le=MAX_TILES)
    responses: tuple[TileResponse, ...] = Field(min_length=1, max_length=MAX_TILES)

    @model_validator(mode="after")
    def bounded_scope(self) -> Self:
        """Refuse incomplete tile coverage and unbounded source requests."""
        west, south, east, north = self.bbox
        if not (
            -MAX_ABS_LONGITUDE <= west < east <= MAX_ABS_LONGITUDE
            and -MAX_ABS_LATITUDE <= south < north <= MAX_ABS_LATITUDE
        ):
            raise ValueError("NWIS bbox must be finite ordered WGS84 bounds")
        if not 1 <= (self.end_day_exclusive - self.start_day).days <= MAX_DAYS or self.start_day < HISTORY_FLOOR:
            raise ValueError("NWIS capture lies outside the bounded historical window")
        clocks = (self.capture_started_at, self.capture_finished_at)
        if any(clock.tzinfo is None or clock.utcoffset() is None for clock in clocks):
            raise ValueError("NWIS capture clocks require explicit UTC offsets")
        if not 0 <= (clocks[1] - clocks[0]).total_seconds() <= MAX_CAPTURE_SECONDS:
            raise ValueError("NWIS capture exceeds the ten-minute unit bound")
        if self.end_day_exclusive > self.capture_started_at.date():
            raise ValueError("NWIS daily-values request includes an unfinished source day")
        expected = tile_bbox(",".join(str(value) for value in self.bbox))
        actual = [response.tile for response in self.responses]
        if len(set(actual)) != len(actual) or set(expected) != set(actual):
            raise ValueError("NWIS capture does not cover every canonical request tile exactly once")
        if self.request_count != len(actual) or sum(item.byte_length for item in self.responses) > MAX_TOTAL_BYTES:
            raise ValueError("NWIS capture exceeds the complete request or total byte bound")
        for response in self.responses:
            at = response.captured_at
            if at.tzinfo is None or at.utcoffset() is None or not clocks[0] <= at <= clocks[1]:
                raise ValueError("NWIS response clock is outside its captured request unit")
        return self

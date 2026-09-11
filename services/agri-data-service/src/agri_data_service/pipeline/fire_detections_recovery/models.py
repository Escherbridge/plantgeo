"""Bounded captured-response contract for exact-day FIRMS replay."""

from __future__ import annotations

from datetime import date, datetime
from typing import Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agri_data_service.ingest.firms import FIRMS_HISTORY_SOURCES

MAX_RESPONSE_BYTES: Final = 16 * 1024 * 1024
MAX_AVAILABILITY_BYTES: Final = 64 * 1024
MAX_RECORDS: Final = 50_000
MAX_CAPTURE_SECONDS: Final = 600
MAX_MANIFEST_BYTES: Final = 64 * 1024
MAX_ABS_LONGITUDE: Final = 180
MAX_ABS_LATITUDE: Final = 90
HISTORY_FLOOR: Final = date(2000, 11, 1)


class CapturedResponse(BaseModel):
    """Identity of complete response entity bytes; no credential-bearing URL."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    byte_length: int = Field(gt=0, le=MAX_RESPONSE_BYTES)
    captured_at: datetime
    status_code: Literal[200]
    transport_complete: Literal[True]


class ProductResponse(BaseModel):
    """One requested FIRMS product and its captured CSV bytes."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    product: str
    response: CapturedResponse


class FireSourceCapture(BaseModel):
    """Operator capture attestation pinned separately from response identities."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    schema_version: Literal["firms-source-capture/v1"]
    day: date
    bbox: tuple[float, float, float, float]
    capture_started_at: datetime
    capture_finished_at: datetime
    request_count: int = Field(ge=2, le=8)
    availability: CapturedResponse
    products: tuple[ProductResponse, ...] = Field(min_length=1, max_length=7)

    @model_validator(mode="after")
    def bounded_scope(self) -> Self:
        """Refuse invalid geography, clocks, products and transport scope."""
        west, south, east, north = self.bbox
        if not (
            -MAX_ABS_LONGITUDE <= west < east <= MAX_ABS_LONGITUDE
            and -MAX_ABS_LATITUDE <= south < north <= MAX_ABS_LATITUDE
        ):
            raise ValueError("FIRMS capture bbox must be finite ordered WGS84 bounds")
        clocks = (self.capture_started_at, self.capture_finished_at)
        if any(clock.tzinfo is None or clock.utcoffset() is None for clock in clocks):
            raise ValueError("FIRMS capture clocks require explicit UTC offsets")
        if not 0 <= (clocks[1] - clocks[0]).total_seconds() <= MAX_CAPTURE_SECONDS:
            raise ValueError("FIRMS capture exceeds the ten-minute unit bound")
        if not HISTORY_FLOOR <= self.day <= self.capture_started_at.date():
            raise ValueError("FIRMS day is outside captured historical support")
        names = [item.product for item in self.products]
        if len(set(names)) != len(names) or not set(names).issubset(FIRMS_HISTORY_SOURCES):
            raise ValueError("FIRMS capture has duplicate or undeclared products")
        if self.request_count != len(self.products) + 1:
            raise ValueError("FIRMS request count does not cover availability plus every product")
        if self.availability.byte_length > MAX_AVAILABILITY_BYTES:
            raise ValueError("FIRMS availability response exceeds its byte cap")
        for response in (self.availability, *(item.response for item in self.products)):
            at = response.captured_at
            if at.tzinfo is None or at.utcoffset() is None or not clocks[0] <= at <= clocks[1]:
                raise ValueError("FIRMS response clock is outside the captured request unit")
        return self

"""Bounded native soil-survey preservation input; see AGENTS.md."""

from __future__ import annotations

from datetime import date, datetime  # noqa: TC003 - Pydantic resolves these model field types at runtime.
from pathlib import PurePosixPath
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_PART_BYTES = 32 * 1024 * 1024
MAX_SOURCE_BYTES = 512 * 1024 * 1024
MAX_PARTS = 256
MAX_SOURCE_ROWS = 100_000
SHA256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class SourcePart(BaseModel):
    """One immutable, complete native Parquet part."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    path: str
    sha256: SHA256
    byte_count: Annotated[int, Field(gt=0, le=MAX_PART_BYTES)]
    row_count: Annotated[int, Field(gt=0, le=MAX_SOURCE_ROWS)]

    @model_validator(mode="after")
    def require_relative_parquet_path(self) -> SourcePart:
        path = PurePosixPath(self.path)
        if path.is_absolute() or ".." in path.parts or "\\" in self.path or ":" in self.path:
            raise ValueError("soil-survey input must use bounded relative paths")
        if path.as_posix() != self.path or path.suffix != ".parquet":
            raise ValueError("soil-survey input requires canonical Parquet paths")
        return self


class SoilSurveyPreservation(BaseModel):
    """Exact preserved holdings are distinct from complete upstream survey coverage."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    schema_version: Literal["soil-survey-preservation/v1"] = "soil-survey-preservation/v1"
    release_day: date
    preserved_at: datetime
    population_scope: Annotated[str, Field(min_length=1)]
    preservation_receipt_sha256: SHA256
    preserved_population_complete: Literal[True]
    upstream_population_complete: Literal[False] = False
    parts: Annotated[tuple[SourcePart, ...], Field(min_length=1, max_length=MAX_PARTS)]

    @model_validator(mode="after")
    def require_bounded_inventory(self) -> SoilSurveyPreservation:
        if self.preserved_at.utcoffset() is None or self.release_day > self.preserved_at.date():
            raise ValueError("soil-survey preservation cannot invent a future release or an undated capture")
        if len({part.path for part in self.parts}) != len(self.parts):
            raise ValueError("duplicate soil-survey preserved part")
        if sum(part.byte_count for part in self.parts) > MAX_SOURCE_BYTES:
            raise ValueError("soil-survey preserved source exceeds the byte budget")
        if sum(part.row_count for part in self.parts) > MAX_SOURCE_ROWS:
            raise ValueError("soil-survey preserved source exceeds the row budget")
        return self

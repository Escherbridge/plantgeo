"""SSURGO capture receipts and shared admission descriptors; see `foundation/AGENTS.md`.

Layer L0 (`foundation`), a ruled exception like `foundation/region/`: `soil_survey` is a domain
noun, not a mechanism (Admission Test criterion 4), but sits here anyway because `spec.md` l.307
(S14) forbids a future `pipeline/lanes/**` strategy from importing `pipeline/direct/**`, and
`planes/`, `interface/`, `pipeline/validation/` and the acquisition CLI all need these types while
importing nothing but stdlib and `pydantic` -- passing `LAYER_FORBIDDEN_IMPORTS["foundation"]`
unmodified. Ported from `archive/freshness-integrated-candidate-20260914`'s
`pipeline/direct/soil_survey/contracts.py`, split in two: this module keeps the CAPTURE-side
receipts (one area's own census, one page, one area's full acquisition); the CANDIDATE/release
side (`Part`, `Candidate`, `ShardRef`, `Release`) is `release.py`'s, added in a later slice.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

SHA256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
AreaSymbol = Annotated[str, Field(pattern=r"^[A-Z]{2}[0-9]{3}$")]

#: Object-store prefix every immutable SSURGO byte lives under; `Blob.key` composes onto it.
ROOT = "soil-survey/candidates"

#: A local circuit-breaker on one area's own delineation count, checked explicitly in
#: `capture.py::_summary` (P1-03) -- NOT a pydantic field bound, so a census that trips it raises a
#: `SoilSurveyError` naming the area and the count, never a raw `ValidationError`. USDA SDA's
#: 100,000-rows-per-query ceiling (`AGENTS.md`, "Source and identity") does NOT bound this number:
#: the census that produces `AreaSummary.count` is a one-row `COUNT(...)` (`ssurgo_summary.sql`),
#: not a paginated query, so a single area can legitimately report more than 100,000 delineations.
#: This value matches archive A's own per-area magnitude (`MAX_PREPARATION_ROWS = 2_000_000`),
#: pending the Go-1 pilot measurement that sets a considered number.
MAX_AREA_DELINEATIONS = 2_000_000
#: Raw decoded SDA response bytes one capture call will hold in memory before refusing (`source.py`
#: `fetch`); conservative and local, chosen 2026-09-13, well under USDA's documented row ceiling.
MAX_OBJECT_BYTES = 16 * 1024 * 1024
#: Per-invocation capture ceilings (`capture.py::capture_area`); `AGENTS.md`, "Bounds, resume and
#: missingness".
MAX_CAPTURE_PAGES = 64
MAX_CAPTURE_SECONDS = 600
MAX_PAGE_ROWS = 500
#: SQL Server `int` printed as ASCII decimal; both `mupolygonkey` and `mukey` fit comfortably.
MAX_NATIVE_KEY_DIGITS = 18
#: Shared WGS84 sanity bounds: `source.py`'s area-inventory envelope check and `release.py`'s
#: per-part bounds both need the same positive-extent rule, so it lives here once.
WGS84_MAX_LONGITUDE = 180
WGS84_MAX_LATITUDE = 90


class SoilSurveyError(RuntimeError):
    """A refused source read, capture receipt, or candidate/published SSURGO object."""


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class Blob(FrozenModel):
    """One immutable, content-addressed byte string; `key` is where it lives in the object store."""

    sha256: SHA256
    byte_count: int = Field(gt=0, le=MAX_OBJECT_BYTES)

    @property
    def key(self) -> str:
        return f"{ROOT}/objects/{self.sha256}"


class AreaSummary(FrozenModel):
    """One area's own delineation count and publication watermark, at one census call."""

    area: AreaSymbol
    #: `ge=0` only (P1-03): the upper sanity bound is enforced explicitly in `capture.py::_summary`,
    #: as a `SoilSurveyError`, not here as a pydantic `le=` -- a receipt that already exists must
    #: never fail to *parse* because a later pilot measurement raised the operational ceiling.
    count: int = Field(ge=0)
    saverest: str = Field(min_length=10)
    response: Blob
    query_sha256: SHA256
    checked_at: datetime

    @model_validator(mode="after")
    def valid_vintage(self) -> AreaSummary:
        datetime.fromisoformat(self.saverest)
        if self.checked_at.tzinfo is None or self.vintage > self.checked_at.date():
            raise ValueError("source vintage requires a real date and a timezone-aware later capture")
        return self

    @property
    def vintage(self) -> date:
        return date.fromisoformat(self.saverest[:10])


class AreaCensusEntry(FrozenModel):
    """One row of the region-wide area inventory: which survey area, at what vintage."""

    area: AreaSymbol
    saverest: str = Field(min_length=10)

    @model_validator(mode="after")
    def valid_vintage(self) -> AreaCensusEntry:
        """Same ISO check `AreaSummary` gives its own `saverest` (P1-06): a malformed vintage from
        a malformed or hand-edited census response must fail to parse, not sit unreadable until
        some later `date.fromisoformat` call blows up far from its source."""
        datetime.fromisoformat(self.saverest)
        return self


class AreaInventory(FrozenModel):
    """The region-wide scope census (`ssurgo_area_inventory.sql`): which areas capture must cover.

    New in revision 2 (F1, F9): the shard plan and every subsequent capture invocation's `--area`
    argument are meant to come from this receipt, never from a literal area list (NFR-5). `envelope`
    and `region` (P1-01/SEC-1) record exactly what this census covered, so an auditor or a later
    release step never has to guess a candidate envelope and re-hash it to find out.
    """

    response: Blob
    query_sha256: SHA256
    checked_at: datetime
    #: `min_length=1` (P1-02/P1-06): an area-inventory response where every row was rejected or the
    #: source answered zero areas is refused before it is ever saved -- it is not a governed absence
    #: (`AGENTS.md`, "Bounds, resume and missingness"), it is a scope this census failed to prove.
    areas: tuple[AreaCensusEntry, ...] = Field(min_length=1)
    #: The WGS84 (west, south, east, north) envelope this census actually ran against -- the Region
    #: envelope unless an operator explicitly overrode it (`__main__.py::_resolve_scope`).
    envelope: tuple[float, float, float, float]
    #: `foundation/region/manifest.py::Region.slug` this census was resolved against.
    region: str

    @model_validator(mode="after")
    def unique_areas(self) -> AreaInventory:
        symbols = [entry.area for entry in self.areas]
        if len(set(symbols)) != len(symbols):
            raise ValueError("area inventory contains a duplicate survey-area symbol")
        if self.checked_at.tzinfo is None:
            raise ValueError("checked_at requires a timezone-aware capture clock")
        return self


class CapturePage(FrozenModel):
    """One bounded page: its own key-inventory response, payload response, and page size.

    `page_size` is recorded per page, not per capture, so an area whose acquisition resumed under a
    different `--page-size` (e.g. after a 16 MiB response refusal) still produces a valid capture.
    """

    response: Blob
    keys_response: Blob
    keys_query_sha256: SHA256
    after_key: str
    last_key: str
    row_count: int = Field(gt=0, le=MAX_PAGE_ROWS)
    captured_at: datetime
    query_sha256: SHA256
    page_size: int = Field(ge=1, le=MAX_PAGE_ROWS)


class AreaCapture(FrozenModel):
    """One area's resumable acquisition journal: its opening census, pages, and closing census."""

    opening: AreaSummary
    pages: tuple[CapturePage, ...] = ()
    closing: AreaSummary | None = None

    @model_validator(mode="after")
    def check_chain(self) -> AreaCapture:
        previous = "0"
        count = 0
        for page in self.pages:
            if page.after_key != previous or not page.last_key.isdecimal():
                raise ValueError("capture cursor chain is broken")
            if int(page.last_key) <= int(previous):
                raise ValueError("capture keys do not advance")
            previous = page.last_key
            count += page.row_count
            if page.captured_at.tzinfo is None or page.captured_at < self.opening.checked_at:
                raise ValueError("page capture must follow its opening census")
        if count > self.opening.count:
            raise ValueError("capture exceeds its opening source census")
        if self.closing is not None:
            if (self.closing.area, self.closing.count, self.closing.saverest) != (
                self.opening.area,
                self.opening.count,
                self.opening.saverest,
            ) or count != self.opening.count:
                raise ValueError("source changed or acquisition is incomplete")
            if self.closing.checked_at < max(
                (page.captured_at for page in self.pages), default=self.opening.checked_at
            ):
                raise ValueError("closing census predates the captured pages")
        return self


def encoded(model: BaseModel) -> bytes:
    """Canonical JSON bytes for any receipt model: sorted keys, no whitespace -- stable to hash."""
    return json.dumps(model.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def require_area(value: str) -> str:
    """Validate a USDA survey-area symbol before it reaches a literal-substitution query."""
    if re.fullmatch(r"[A-Z]{2}[0-9]{3}", value) is None:
        raise SoilSurveyError("invalid USDA survey area symbol")
    return value


def verify_blob(blob: Blob, payload: bytes | None) -> bytes:
    """Read back an immutable object and refuse it if it is missing or its checksum changed."""
    if payload is None or len(payload) != blob.byte_count or digest(payload) != blob.sha256:
        raise SoilSurveyError("immutable SSURGO object is missing or its checksum changed")
    return payload


__all__ = [
    "MAX_AREA_DELINEATIONS",
    "MAX_CAPTURE_PAGES",
    "MAX_CAPTURE_SECONDS",
    "MAX_NATIVE_KEY_DIGITS",
    "MAX_OBJECT_BYTES",
    "MAX_PAGE_ROWS",
    "ROOT",
    "SHA256",
    "WGS84_MAX_LATITUDE",
    "WGS84_MAX_LONGITUDE",
    "AreaCapture",
    "AreaCensusEntry",
    "AreaInventory",
    "AreaSummary",
    "AreaSymbol",
    "Blob",
    "CapturePage",
    "FrozenModel",
    "SoilSurveyError",
    "digest",
    "encoded",
    "require_area",
    "verify_blob",
]

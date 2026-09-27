"""Resumable single-owner survey acquisition with immutable raw responses.

Ported from `archive/freshness-integrated-candidate-20260914`'s `pipeline/direct/soil_survey/
capture.py`; Freeze 1 covers this module's public names and `capture_area`'s behaviour verbatim
(`AGENTS.md`, "Freeze 1"). The only behavioural change is the default `page_size`, raised from 250
to 500 to match the new capture-page ceiling; every other line is an import-path adaptation onto
`foundation.soil_survey.receipts`. `save_checkpoint` is widened to accept any receipt model (not
just `AreaCapture`) so `__main__.py`'s `areas` census can reuse the same atomic-write path.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from typing import TYPE_CHECKING, TypedDict, Unpack

from filelock import FileLock, Timeout

from agri_data_service.foundation.soil_survey.receipts import (
    MAX_AREA_DELINEATIONS,
    MAX_CAPTURE_PAGES,
    MAX_CAPTURE_SECONDS,
    AreaCapture,
    AreaSummary,
    Blob,
    CapturePage,
    SoilSurveyError,
    digest,
    encoded,
    require_area,
    verify_blob,
)
from agri_data_service.pipeline.direct.soil_survey.source import (
    PAGE_COLUMNS,
    fetch,
    page_keys,
    page_query,
    polygon_query,
    summary_query,
    table_rows,
)

if TYPE_CHECKING:
    from pathlib import Path

    import httpx
    from pydantic import BaseModel


#: `capture_area`'s own default page size, named (P1-08, PLR2004) so callers and tests cite one
#: constant rather than repeating the literal 500.
DEFAULT_PAGE_SIZE = 500
DEFAULT_MAX_PAGES = 8
DEFAULT_TIME_BUDGET_SECONDS = 120


class CaptureLimits(TypedDict, total=False):
    max_pages: int
    page_size: int
    time_budget_seconds: float


def save_blob(root: Path, payload: bytes) -> Blob:
    """Write one immutable, content-addressed object, hard-linking a concurrent write onto itself."""
    blob = Blob(sha256=digest(payload), byte_count=len(payload))
    path = root / "objects" / blob.sha256
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_name(f".{blob.sha256}.{os.getpid()}.pending")
    if path.exists():
        verify_blob(blob, path.read_bytes())
        return blob
    try:
        with staged.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(staged, path)
        except FileExistsError:
            verify_blob(blob, path.read_bytes())
    except FileExistsError:
        raise SoilSurveyError("an unfinished local blob requires inspection before retry") from None
    finally:
        if staged.exists():
            staged.unlink()
    return blob


def read_blob(root: Path, blob: Blob) -> bytes:
    """Read one immutable object back, refusing it if its checksum no longer matches."""
    return verify_blob(blob, (root / "objects" / blob.sha256).read_bytes())


def save_checkpoint(path: Path, model: BaseModel) -> None:
    """Atomically replace one local receipt file: write to a sibling, then rename over it.

    Generic over any receipt model (`AreaCapture`'s per-area journal, `__main__.py`'s `areas`
    census) so every local checkpoint in this package shares one crash-safe write path.
    """
    staged = path.with_suffix(".pending")
    with staged.open("wb") as stream:
        stream.write(encoded(model))
        stream.flush()
        os.fsync(stream.fileno())
    staged.replace(path)


async def _summary(client: httpx.AsyncClient, root: Path, area: str) -> AreaSummary:
    query = summary_query(area)
    payload = await fetch(client, query)
    blob = save_blob(root, payload)
    rows = table_rows(payload, ("delineation_count", "saverest"))
    if len(rows) != 1 or rows[0]["saverest"] is None or rows[0]["delineation_count"] is None:
        raise SoilSurveyError(f"{area}: no source census/vintage; this is not a governed absence")
    count = int(str(rows[0]["delineation_count"]))
    if count > MAX_AREA_DELINEATIONS:
        # P1-03: checked explicitly, not left to AreaSummary's pydantic field, so this refuses with
        # a SoilSurveyError naming the area and the count instead of a raw ValidationError.
        raise SoilSurveyError(
            f"{area}: reports {count} delineations, over the {MAX_AREA_DELINEATIONS} capture sanity bound"
        )
    return AreaSummary(
        area=area,
        count=count,
        saverest=str(rows[0]["saverest"]),
        response=blob,
        checked_at=datetime.now(UTC),
        query_sha256=digest(query.encode()),
    )


def validate_page(payload: bytes, capture: AreaCapture, after: str, page_size: int) -> tuple[str, int]:
    """Check one payload page against its capture's opening census, returning its last key and count."""
    rows = table_rows(payload, PAGE_COLUMNS)
    if not rows or len(rows) > page_size:
        raise SoilSurveyError("short source coverage or page exceeds its requested bound")
    previous = int(after)
    for row in rows:
        key = row["mupolygonkey"]
        if key is None or not key.isascii() or not key.isdecimal() or int(key) <= previous:
            raise SoilSurveyError("duplicate, invalid or out-of-order native polygon identity")
        if row["areasymbol"] != capture.opening.area or row["saverest"] != capture.opening.saverest:
            raise SoilSurveyError("survey area/vintage changed during capture")
        previous = int(key)
    return str(previous), len(rows)


async def _capture_area(
    client: httpx.AsyncClient, root: Path, area: str, *, max_pages: int, page_size: int
) -> AreaCapture:
    path = root / f"{area}.json"
    fresh = await _summary(client, root, area)
    if path.exists():
        capture = AreaCapture.model_validate_json(path.read_bytes())
        if (fresh.area, fresh.count, fresh.saverest) != (
            capture.opening.area,
            capture.opening.count,
            capture.opening.saverest,
        ):
            raise SoilSurveyError("source watermark changed; retain this capture and start a new directory")
        for page in capture.pages:
            read_blob(root, page.response)
            read_blob(root, page.keys_response)
        if capture.closing is not None:
            save_blob(root, encoded(fresh))
            return capture
    else:
        capture = AreaCapture(opening=fresh)
        save_checkpoint(path, capture)
    for _ in range(max_pages):
        seen = sum(page.row_count for page in capture.pages)
        if seen == capture.opening.count:
            break
        after = capture.pages[-1].last_key if capture.pages else "0"
        query = page_query(area, after, page_size)
        keys_payload = await fetch(client, query)
        keys_blob = save_blob(root, keys_payload)
        keys = page_keys(keys_payload, after=after, page_size=page_size)
        data_query = polygon_query(area, keys)
        payload = await fetch(client, data_query)
        blob = save_blob(root, payload)
        last_key, count = validate_page(payload, capture, after, page_size)
        if tuple(row["mupolygonkey"] for row in table_rows(payload, PAGE_COLUMNS)) != keys:
            raise SoilSurveyError("source payload differs from its native-key inventory")
        page = CapturePage(
            response=blob,
            after_key=after,
            last_key=last_key,
            row_count=count,
            captured_at=datetime.now(UTC),
            query_sha256=digest(data_query.encode()),
            page_size=page_size,
            keys_query_sha256=digest(query.encode()),
            keys_response=keys_blob,
        )
        capture = AreaCapture(opening=capture.opening, pages=(*capture.pages, page))
        save_checkpoint(path, capture)
    if sum(page.row_count for page in capture.pages) == capture.opening.count:
        closing = await _summary(client, root, area)
        capture = AreaCapture(opening=capture.opening, pages=capture.pages, closing=closing)
        save_checkpoint(path, capture)
    return capture


async def capture_area(
    client: httpx.AsyncClient,
    root: Path,
    area: str,
    **limits: Unpack[CaptureLimits],
) -> AreaCapture:
    """Acquire one bounded slice of one survey area, resuming from its last durable native key."""
    if unexpected := limits.keys() - CaptureLimits.__annotations__.keys():
        raise TypeError(f"unexpected capture limits: {sorted(unexpected)}")
    max_pages = limits.get("max_pages", DEFAULT_MAX_PAGES)
    page_size = limits.get("page_size", DEFAULT_PAGE_SIZE)
    time_budget_seconds = limits.get("time_budget_seconds", DEFAULT_TIME_BUDGET_SECONDS)
    require_area(area)
    page_query(area, "0", page_size)
    if not 1 <= max_pages <= MAX_CAPTURE_PAGES or not 1 <= time_budget_seconds <= MAX_CAPTURE_SECONDS:
        raise SoilSurveyError("capture invocation exceeds the page/time budget")
    root.mkdir(parents=True, exist_ok=True)
    lock = FileLock(root / f"{area}.lock", timeout=0)
    try:
        lock.acquire()
    except Timeout as error:
        raise SoilSurveyError("another capture process holds this survey-area checkpoint") from error
    try:
        async with asyncio.timeout(time_budget_seconds):
            return await _capture_area(client, root, area, max_pages=max_pages, page_size=page_size)
    finally:
        lock.release()


__all__ = [
    "DEFAULT_MAX_PAGES",
    "DEFAULT_PAGE_SIZE",
    "DEFAULT_TIME_BUDGET_SECONDS",
    "CaptureLimits",
    "capture_area",
    "read_blob",
    "save_blob",
    "save_checkpoint",
    "validate_page",
]

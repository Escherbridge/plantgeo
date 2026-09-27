"""USDA SDA source reads: comment-stripped query bodies, bounded response bytes, keyset paging.

Ported from `archive/freshness-integrated-candidate-20260914`'s `pipeline/direct/soil_survey/
source.py`, adapted for F7 (see `AGENTS.md`, "Query bodies are frozen once a capture exists"):
every builder below returns the query with its `--` comment lines and blank lines already
stripped, so editing a `.sql` file's documentation header -- including reflowing it across lines
or adding a blank line for readability -- can never change what gets sent, hashed into a capture
receipt, or matched by a body-SHA test. Also adds `area_inventory_query`, new in revision 2 (F1,
F9): the region-wide scope census that replaces a literal area list.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from typing import TYPE_CHECKING

from agri_data_service.db.sql_queries import load_query_sql
from agri_data_service.foundation.soil_survey.receipts import (
    MAX_NATIVE_KEY_DIGITS,
    MAX_OBJECT_BYTES,
    MAX_PAGE_ROWS,
    WGS84_MAX_LATITUDE,
    WGS84_MAX_LONGITUDE,
    SoilSurveyError,
    require_area,
)

if TYPE_CHECKING:
    import httpx

    from agri_data_service.foundation.geography.bounding_box import BoundingBox

ENDPOINT = "https://sdmdataaccess.nrcs.usda.gov/Tabular/post.rest"
#: HTTP redirect status range (SEC-2(d)): `raise_for_status()` alone treats none of these as an
#: error, so `fetch()` checks the range itself before trusting the response body is real data.
_HTTP_REDIRECT_STATUS_RANGE = range(300, 400)
#: `fetch()`'s own default socket-read timeout, unchanged from the pre-census-tiling literal
#: (`AGENTS.md`, "Census tiling"): every one of `capture.py`'s three per-page/per-summary calls
#: keeps this exact value, since none of them pass `timeout=` explicitly. Only `__main__.py::_areas`
#: overrides it, per-tile, via its own `--request-timeout-seconds` flag.
DEFAULT_FETCH_TIMEOUT_SECONDS = 30


def query_body(sql: str) -> str:
    """Drop every full `--` comment line and every blank line before a query body is formatted,
    sent, or hashed (F7/P1-04).

    A documentation header can be rewritten at any time -- rephrased, dated, extended, reflowed
    across more or fewer lines, or padded with a blank separator line for readability -- without
    ever touching what SDA receives or what a capture receipt's `query_sha256` commits to, because
    only the non-blank lines below survive to `.format()`. Dropping blank lines too (not just `--`
    lines) matters because a header's own internal blank lines are not `--`-prefixed and would
    otherwise leak into the "body" verbatim; each surviving line is also `rstrip()`-ed, so trailing
    whitespace on the body's own SQL lines cannot silently change the hash either. A line-1
    dispatch marker (`sql/AGENTS.md`, "Marker protocol") is a comment too and is stripped the same
    way; nothing in this tree hashes it.
    """
    return "\n".join(line.rstrip() for line in sql.splitlines() if line.strip() and not line.lstrip().startswith("--"))


_SUMMARY = load_query_sql("pipeline/ssurgo_summary.sql")
_PAGE = load_query_sql("pipeline/ssurgo_page.sql")
_KEYS = load_query_sql("pipeline/ssurgo_keys.sql")
_AREA_INVENTORY = load_query_sql("pipeline/ssurgo_area_inventory.sql")
KEY_COLUMNS = ("mupolygonkey",)
AREA_INVENTORY_COLUMNS = ("areasymbol", "saverest")
PAGE_COLUMNS = (
    "mupolygonkey",
    "mukey",
    "muname",
    "areasymbol",
    "saverest",
    "compname",
    "drainagecl",
    "hydricrating",
    "nirrcapcl",
    "geom",
)


def table_rows(payload: bytes, columns: tuple[str, ...]) -> tuple[dict[str, str | None], ...]:
    """Decode one SDA `JSON+COLUMNNAME` table, refusing anything short of an exact projection match."""
    value: object = json.loads(payload)
    if isinstance(value, Mapping) and not value:
        # SDA's own empty-object answer, distinct from a malformed or error payload (F7): the
        # caller must not read this as "not exactly one result table" and hide that no rows exist.
        raise SoilSurveyError("SDA returned no rows")
    if not isinstance(value, Mapping) or set(value) != {"Table"}:
        raise SoilSurveyError("SDA did not return exactly one result table")
    table = value["Table"]
    if not isinstance(table, list) or not table or table[0] != list(columns):
        raise SoilSurveyError("SDA table header differs from the requested projection")
    rows: list[dict[str, str | None]] = []
    for row in table[1:]:
        if not isinstance(row, list) or len(row) != len(columns):
            raise SoilSurveyError("malformed SDA table row")
        if any(
            isinstance(cell, bool) or (cell is not None and not isinstance(cell, (str, int, float))) for cell in row
        ):
            raise SoilSurveyError("malformed SDA scalar")
        rows.append({key: None if cell is None else str(cell) for key, cell in zip(columns, row, strict=True)})
    return tuple(rows)


def summary_query(area: str) -> str:
    """One area's own delineation count and publication watermark."""
    return query_body(_SUMMARY).format(area=require_area(area))


def page_query(area: str, after_key: str, page_size: int) -> str:
    """The bounded native-key page after `after_key`, before any geometry/attribute projection."""
    if not after_key.isascii() or not after_key.isdecimal() or len(after_key) > MAX_NATIVE_KEY_DIGITS:
        raise SoilSurveyError("invalid polygon cursor")
    # SEC-2(e): reject a non-int page_size explicitly, before it can reach `.format()` -- otherwise
    # a caller passing a str/float falls through the bound check below with a raw TypeError
    # (str/int are not orderable) instead of this module's own SoilSurveyError vocabulary, and a
    # str value would land in the template unquoted and unchecked.
    if isinstance(page_size, bool) or not isinstance(page_size, int):
        raise SoilSurveyError("page_size must be an int")
    if not 1 <= page_size <= MAX_PAGE_ROWS:
        raise SoilSurveyError(f"page_size must be between 1 and {MAX_PAGE_ROWS}")
    return query_body(_KEYS).format(area=require_area(area), after_key=after_key, page_size=page_size)


def polygon_query(area: str, keys: tuple[str, ...]) -> str:
    """Full native delineations for exactly the keys a prior key-inventory page returned."""
    if not 1 <= len(keys) <= MAX_PAGE_ROWS or len(set(keys)) != len(keys):
        raise SoilSurveyError("polygon payload query requires a bounded unique key list")
    for key in keys:
        if not key.isascii() or not key.isdecimal() or len(key) > MAX_NATIVE_KEY_DIGITS:
            raise SoilSurveyError("invalid polygon payload key")
    return query_body(_PAGE).format(area=require_area(area), polygon_keys=",".join(keys))


def area_inventory_query(bounding_box: BoundingBox) -> str:
    """Every survey area whose extent intersects the region envelope, with its own vintage.

    New in revision 2 (F1, F9): the scope for a wave's shard plan comes from this census, never
    from a literal area list. Envelope floats are written with `repr()` of validated finite values
    -- the same literal-substitution safety boundary the three capture queries rest on
    (`AGENTS.md`, "Literal substitution is still the safety boundary").
    """
    west, south, east, north = bounding_box
    if not all(math.isfinite(value) for value in bounding_box):
        raise SoilSurveyError("area-inventory envelope must be finite")
    if not (-WGS84_MAX_LONGITUDE <= west < east <= WGS84_MAX_LONGITUDE) or not (
        -WGS84_MAX_LATITUDE <= south < north <= WGS84_MAX_LATITUDE
    ):
        raise SoilSurveyError("area-inventory envelope must be a positive WGS84 extent")
    return query_body(_AREA_INVENTORY).format(west=repr(west), south=repr(south), east=repr(east), north=repr(north))


def page_keys(payload: bytes, *, after: str, page_size: int) -> tuple[str, ...]:
    """The native keys a key-inventory page returned, checked strictly increasing and in budget."""
    rows = table_rows(payload, KEY_COLUMNS)
    keys = tuple(str(row["mupolygonkey"]) for row in rows)
    if not keys or any(not key.isascii() or not key.isdecimal() or len(key) > MAX_NATIVE_KEY_DIGITS for key in keys):
        raise SoilSurveyError("source inventory lacks valid polygon identities")
    if len(keys) > page_size or any(
        int(key) <= int(previous) for previous, key in zip((after, *keys[:-1]), keys, strict=True)
    ):
        raise SoilSurveyError("source key page is duplicate, unordered or over budget")
    return keys


async def fetch(client: httpx.AsyncClient, query: str, *, timeout: float = DEFAULT_FETCH_TIMEOUT_SECONDS) -> bytes:
    """POST one query body to SDA, refusing a decoded response over the capture byte budget.

    SEC-2(d): every caller builds `client` with `follow_redirects=False`, but `raise_for_status()`
    alone does NOT refuse a 3xx -- httpx only treats 4xx/5xx as an error status. Left unchecked, a
    redirect response's body (a redirect page, not `JSON+COLUMNNAME`) would fall through to
    `table_rows()` and fail as an unrelated `json.JSONDecodeError`, not this module's own
    `SoilSurveyError` vocabulary. Refuse it here, explicitly, before that can happen.

    `timeout` is a keyword-only override (`AGENTS.md`, "Census tiling"): every `capture.py` call
    site omits it and keeps the 30 s default unchanged; only the `areas` verb's per-tile census
    calls pass a caller-supplied value, since a one-time offline census over a large envelope is not
    latency-sensitive the way a resumable capture page is.
    """
    async with client.stream(
        "POST", ENDPOINT, json={"format": "JSON+COLUMNNAME", "query": query}, timeout=timeout
    ) as response:
        if response.status_code in _HTTP_REDIRECT_STATUS_RANGE:
            raise SoilSurveyError(f"SDA responded with an unexpected redirect ({response.status_code})")
        response.raise_for_status()
        payload = bytearray()
        async for chunk in response.aiter_bytes():
            payload.extend(chunk)
            if len(payload) > MAX_OBJECT_BYTES:
                raise SoilSurveyError("SDA response exceeds the 16 MiB capture budget")
    return bytes(payload)


__all__ = [
    "AREA_INVENTORY_COLUMNS",
    "DEFAULT_FETCH_TIMEOUT_SECONDS",
    "ENDPOINT",
    "KEY_COLUMNS",
    "PAGE_COLUMNS",
    "area_inventory_query",
    "fetch",
    "page_keys",
    "page_query",
    "polygon_query",
    "query_body",
    "summary_query",
    "table_rows",
]

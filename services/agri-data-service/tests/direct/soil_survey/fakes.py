"""A scripted USDA SDA transport and an in-memory object-store double, shared across this package's tests.

Ported from `archive/freshness-integrated-candidate-20260914`'s combined `test_capture_and_
serving.py`, split out as an explicit helper module rather than a `conftest.py` -- precedent
`tests/agent_fakes.py`, imported by name (`from tests.direct.soil_survey.fakes import ...`), not
autowired through fixture discovery.

`Source.response()` used to dispatch on each query's stripped-away line-1 marker comment
(`"ssurgo_keys" in query`); now that `source.py::query_body()` strips every comment line before a
query is ever sent (F7), that marker never reaches the wire. Dispatch here instead reads tokens
that only ever appear in one query body: `COUNT(p.mupolygonkey)` (summary), `STIntersects`
(area inventory), `STAsText` (page); anything else is the keys page, which carries none of those
three and is the only remaining shape.

P1-07(a): the key-inventory and data-page branches genuinely parse `TOP {page_size}`, the resume
cursor (`p.mupolygonkey > {after_key}`) and the requested key list (`p.mupolygonkey IN (...)`)
instead of hard-coding a fixed one-row answer -- a page_size of 2 against a fresh capture really
does return 2 rows in one page, and a resumed capture with a *smaller* remaining key set still
returns only what is left, proving `TOP` is an upper bound, not a fixed count.

`Storage` is authored here for slice S1 even though only a later slice's `stage.py`/`prepare.py`
tests exercise `put_immutable`/`compare_and_swap`: `fakes.py` is this package's one shared test
double, so those tests import it by name rather than re-deriving an equivalent fake.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

import httpx

from agri_data_service.foundation.soil_survey.receipts import digest
from agri_data_service.pipeline.direct.soil_survey.capture import capture_area
from agri_data_service.pipeline.direct.soil_survey.source import PAGE_COLUMNS
from agri_data_service.pipeline.parquet.availability_index import StoredAvailabilityObject

if TYPE_CHECKING:
    from pathlib import Path

VINTAGE = "2025-08-27T20:27:08.200"
AREA = "ID001"
POLYGONS = (
    "POLYGON ((0 0, 2 0, 2 2, 0 2, 0 0), (0.75 0.75, 0.75 1.25, 1.25 1.25, 1.25 0.75, 0.75 0.75))",
    "MULTIPOLYGON (((3 0, 4 0, 4 1, 3 1, 3 0)), ((5 0, 6 0, 6 1, 5 1, 5 0)))",
)

_TOP_PATTERN = re.compile(r"TOP (\d+)")
_AFTER_KEY_PATTERN = re.compile(r"p\.mupolygonkey > (\d+)")
_REQUESTED_KEYS_PATTERN = re.compile(r"p\.mupolygonkey IN \(([^)]*)\)")


def row(key: str, *, vintage: str = VINTAGE) -> list[object]:
    """One `PAGE_COLUMNS`-shaped SDA row for native key `key`, `key`'s own fixed polygon."""
    return [
        key,
        "same-map-unit",
        "Test loam",
        AREA,
        vintage,
        "Series",
        "Well drained",
        None,
        "3",
        POLYGONS[int(key) - 1],
    ]


class Source:
    """A scripted `httpx.MockTransport` handler standing in for USDA SDA's POST endpoint."""

    def __init__(self) -> None:
        self.vintage = VINTAGE
        self.page_calls = 0
        #: Total native delineations the fake's universe holds; the summary census and the
        #: key-inventory page both stay consistent with it. Defaults to `len(POLYGONS)` so every
        #: existing test's row/page-count expectations are unchanged; P1-03's boundary test raises
        #: it past `MAX_AREA_DELINEATIONS` to exercise `_summary`'s explicit refusal.
        self.count = len(POLYGONS)
        self.duplicate = False
        self.fail_page = False
        self.invalid_geometry = False
        self.empty_response = False
        #: Distinct from `empty_response` (which blanks every call, including the opening census):
        #: this blanks only the key-inventory page, so a test can prove that a `{}` arriving
        #: mid-capture -- while the census still says rows remain -- refuses cleanly rather than
        #: being silently read as completion.
        self.empty_key_page = False
        #: (area, saverest) rows the area-inventory census answers with; one area by default.
        self.inventory_areas: tuple[tuple[str, str], ...] = ((AREA, self.vintage),)

    def response(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["format"] == "JSON+COLUMNNAME"
        query = body["query"]
        if self.empty_response:
            return httpx.Response(200, json={})
        if "COUNT(p.mupolygonkey)" in query:
            return httpx.Response(
                200, json={"Table": [["delineation_count", "saverest"], [str(self.count), self.vintage]]}
            )
        if "STIntersects" in query:
            table = [["areasymbol", "saverest"], *([area, vintage] for area, vintage in self.inventory_areas)]
            return httpx.Response(200, json={"Table": table})
        if "STAsText" not in query:
            # The key-inventory page: the only query shape left once summary/area-inventory/page
            # are ruled out above.
            return self._key_page_response(query)
        self.page_calls += 1
        if self.fail_page:
            return httpx.Response(503)
        requested_match = _REQUESTED_KEYS_PATTERN.search(query)
        requested = requested_match.group(1).split(",") if requested_match else ["1"]
        rows_out: list[list[object]] = []
        for key in requested:
            values = row(key, vintage=self.vintage)
            if self.invalid_geometry:
                values[-1] = "POLYGON ((0 0, 2 2, 0 2, 2 0, 0 0))"
            rows_out.append(values)
        return httpx.Response(200, json={"Table": [list(PAGE_COLUMNS), *rows_out]})

    def _key_page_response(self, query: str) -> httpx.Response:
        """`self.duplicate` forces the SOURCE ITSELF to keep answering key "1" no matter the
        cursor -- the non-advancing-key scenario, caught by `source.py::page_keys`'s own
        strictly-increasing check, not by the data page above."""
        if self.empty_key_page:
            return httpx.Response(200, json={})
        if self.duplicate:
            return httpx.Response(200, json={"Table": [["mupolygonkey"], ["1"]]})
        after_match = _AFTER_KEY_PATTERN.search(query)
        top_match = _TOP_PATTERN.search(query)
        after = int(after_match.group(1)) if after_match else 0
        page_size = int(top_match.group(1)) if top_match else 1
        available = [key for key in range(1, self.count + 1) if key > after]
        return httpx.Response(200, json={"Table": [["mupolygonkey"], *([str(key)] for key in available[:page_size])]})


class Storage:
    """An in-memory object store standing in for `BotoAvailabilityStorage`."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.writes: list[str] = []
        self.reads: list[str] = []
        self.fail_manifest = False

    def read(self, key: str, *, max_bytes: int) -> StoredAvailabilityObject | None:
        self.reads.append(key)
        payload = self.objects.get(key)
        if payload is None:
            return None
        assert len(payload) <= max_bytes
        return StoredAvailabilityObject(payload=payload, etag=digest(payload))

    def put_immutable(self, key: str, payload: bytes, *, content_type: str) -> None:
        assert content_type
        if self.fail_manifest and "/manifests/" in key:
            raise OSError("injected marker-last failure")
        existing = self.objects.setdefault(key, payload)
        assert existing == payload
        self.writes.append(key)

    def compare_and_swap(self, key: str, payload: bytes, *, expected_etag: str | None, content_type: str) -> bool:
        raise AssertionError(
            f"candidate publication cannot advance admission: {key}, {len(payload)}, {expected_etag}, {content_type}"
        )


async def completed(root: Path) -> None:
    """Run one full two-page capture of `AREA` to completion, for a test that starts from there."""
    source = Source()
    async with httpx.AsyncClient(transport=httpx.MockTransport(source.response)) as client:
        capture = await capture_area(client, root, AREA, max_pages=2, page_size=1)
    assert capture.closing is not None


__all__ = [
    "AREA",
    "POLYGONS",
    "VINTAGE",
    "Source",
    "Storage",
    "completed",
    "row",
]

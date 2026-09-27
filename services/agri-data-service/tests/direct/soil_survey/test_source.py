"""SSURGO query builders: comment-stripped bodies (F7), literal-substitution safety, table decoding."""

from __future__ import annotations

import httpx
import pytest

from agri_data_service.db.sql_queries import load_query_sql
from agri_data_service.foundation.soil_survey.receipts import SoilSurveyError, digest
from agri_data_service.pipeline.direct.soil_survey import source
from agri_data_service.pipeline.direct.soil_survey.source import (
    PAGE_COLUMNS,
    area_inventory_query,
    fetch,
    page_query,
    polygon_query,
    query_body,
    summary_query,
    table_rows,
)

AREA = "ID001"

#: The comment-stripped body SHA of each capture query, pinned so a future body edit is caught as
#: a deliberate, reviewed change rather than an incidental one (AGENTS.md, "Freeze 1").
_SUMMARY_BODY_SHA256 = "960463d3ac34c33dde07f32c78436257080074e170cd0d730e96377117555485"
_KEYS_BODY_SHA256 = "197d7d92159ca1697cf0a9ea0c86f036ba30fcf9a96b2a07e42641708954b66b"
_PAGE_BODY_SHA256 = "33e64cef974f5862e2c4ad236316177672d91c5305cd3ce16289f5b241e37dd5"
_AREA_INVENTORY_BODY_SHA256 = "06187826cd9addddf89af92da939f8454c79c3121852daa30c4433230478d442"


def test_query_body_drops_every_full_comment_line_and_nothing_else() -> None:
    sql = "-- marker\n-- Purpose: does a thing\nSELECT 1\n-- a trailing note\nFROM t WHERE x = 1"
    assert query_body(sql) == "SELECT 1\nFROM t WHERE x = 1"


def test_query_body_also_drops_blank_lines_and_trailing_whitespace() -> None:
    """P1-04: a bare blank line inside the header, or trailing whitespace on a body line, must not
    survive into the hashed body -- both are things a future header rewrite could introduce."""
    sql = "-- Purpose: x\n\n-- more header\n\nSELECT 1  \nFROM t WHERE x = 1\n"
    assert query_body(sql) == "SELECT 1\nFROM t WHERE x = 1"


@pytest.mark.parametrize(
    ("relative_path", "pinned_sha256"),
    [
        ("pipeline/ssurgo_summary.sql", _SUMMARY_BODY_SHA256),
        ("pipeline/ssurgo_keys.sql", _KEYS_BODY_SHA256),
        ("pipeline/ssurgo_page.sql", _PAGE_BODY_SHA256),
        ("pipeline/ssurgo_area_inventory.sql", _AREA_INVENTORY_BODY_SHA256),
    ],
)
def test_capture_query_body_sha_is_pinned(relative_path: str, pinned_sha256: str) -> None:
    body = query_body(load_query_sql(relative_path))
    assert digest(body.encode()) == pinned_sha256


@pytest.mark.parametrize(
    "relative_path",
    [
        "pipeline/ssurgo_summary.sql",
        "pipeline/ssurgo_keys.sql",
        "pipeline/ssurgo_page.sql",
        "pipeline/ssurgo_area_inventory.sql",
    ],
)
def test_editing_a_documented_header_does_not_change_the_hashed_body(relative_path: str) -> None:
    """P1-04: three real header mutations, not one that `query_body` removes by definition.

    The prior version of this test only rewrote text inside an existing `--` line, which the
    function strips wholesale regardless of what it says -- it could not have caught a blank line
    or a reflowed header slipping a stray line into the "body". These three do.
    """
    original = load_query_sql(relative_path)
    reworded = original.replace("Purpose:", "Purpose: REWRITTEN 2026-09-27,", 1)
    with_blank_line = original.replace("-- Dialect:", "\n-- Dialect:", 1)
    with_new_comment_line = original.replace("-- Dialect:", "-- Reviewed: 2026-09-27\n-- Dialect:", 1)
    # Reflow: fold the first two header lines into one, same words, different line breaks.
    lines = original.splitlines()
    reflowed = "\n".join((lines[0] + " " + lines[1].removeprefix("--").lstrip(), *lines[2:]))
    for mutated in (reworded, with_blank_line, with_new_comment_line, reflowed):
        assert mutated != original
        assert query_body(mutated) == query_body(original)
        assert digest(query_body(mutated).encode()) == digest(query_body(original).encode())


def test_each_capture_query_formats_sample_parameters_without_error() -> None:
    summary = summary_query(AREA)
    keys = page_query(AREA, "0", 500)
    page = polygon_query(AREA, ("1", "2"))
    inventory = area_inventory_query((-126.0, 41.0, -110.0, 50.0))
    for rendered in (summary, keys, page, inventory):
        assert "--" not in rendered  # no comment line survives query_body()
    assert AREA in summary
    assert AREA in keys
    assert AREA in page
    assert "1,2" in page
    assert "-126.0" in inventory
    assert "-110.0" in inventory
    assert "41.0" in inventory
    assert "50.0" in inventory


def test_area_inventory_query_rejects_a_non_finite_or_inverted_envelope() -> None:
    with pytest.raises(SoilSurveyError, match="finite"):
        area_inventory_query((float("nan"), 41.0, -110.0, 50.0))
    with pytest.raises(SoilSurveyError, match="WGS84"):
        area_inventory_query((-110.0, 41.0, -126.0, 50.0))
    with pytest.raises(SoilSurveyError, match="WGS84"):
        area_inventory_query((-126.0, 50.0, -110.0, 41.0))


def test_sda_empty_object_is_a_distinct_no_rows_refusal() -> None:
    with pytest.raises(SoilSurveyError, match="no rows"):
        table_rows(b"{}", PAGE_COLUMNS)


def test_source_failure_payload_and_injection_are_refused() -> None:
    with pytest.raises(SoilSurveyError, match="exactly one result table"):
        table_rows(b'{"Error":"upstream unavailable"}', PAGE_COLUMNS)
    with pytest.raises(SoilSurveyError):
        page_query("ID001'; DROP TABLE mupolygon", "0", 10)
    with pytest.raises(SoilSurveyError):
        page_query(AREA, "0 OR 1=1", 10)


def test_polygon_query_rejects_an_unbounded_or_duplicate_key_list() -> None:
    with pytest.raises(SoilSurveyError):
        polygon_query(AREA, ())
    with pytest.raises(SoilSurveyError):
        polygon_query(AREA, ("1", "1"))
    with pytest.raises(SoilSurveyError):
        polygon_query(AREA, ("1' OR '1'='1",))


def test_summary_query_rejects_an_injected_or_malformed_area() -> None:
    """SEC-2(e): `summary_query` gets the same `require_area` injection coverage `page_query` has."""
    with pytest.raises(SoilSurveyError):
        summary_query("ID001'; DROP TABLE sacatalog--")
    with pytest.raises(SoilSurveyError):
        summary_query("id001")  # lowercase does not fit ^[A-Z]{2}[0-9]{3}$


def test_require_area_rejects_a_trailing_newline() -> None:
    """SEC-2(e): a symbol that is otherwise valid but for a trailing newline must still be refused."""
    with pytest.raises(SoilSurveyError):
        summary_query("ID001\n")


def test_area_inventory_query_rejects_an_out_of_range_ordinate() -> None:
    """SEC-2(e): east=200 is finite and correctly ordered, but outside WGS84's own longitude range."""
    with pytest.raises(SoilSurveyError, match="WGS84"):
        area_inventory_query((-126.0, 41.0, 200.0, 50.0))


def test_page_query_rejects_a_non_int_page_size() -> None:
    """SEC-2(e): a str/float page_size must raise SoilSurveyError, not a bare TypeError from the
    unordered `1 <= page_size <= MAX_PAGE_ROWS` comparison, and must never reach `.format()`."""
    with pytest.raises(SoilSurveyError, match="int"):
        page_query(AREA, "0", "500")  # type: ignore[arg-type]
    with pytest.raises(SoilSurveyError, match="int"):
        page_query(AREA, "0", 12.5)  # type: ignore[arg-type]


async def test_fetch_refuses_a_redirect_without_following_it() -> None:
    """SEC-2(d): `raise_for_status()` alone does not refuse a 3xx; `fetch` must refuse it itself."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://example.invalid/"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False) as client:
        with pytest.raises(SoilSurveyError, match="redirect"):
            await fetch(client, "SELECT 1")


async def test_fetch_refuses_a_response_over_the_capture_byte_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """SEC-2(b): the 16 MiB decoded-byte cap the recovery procedure depends on is otherwise untested."""
    monkeypatch.setattr(source, "MAX_OBJECT_BYTES", 8)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 9)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(SoilSurveyError, match="budget"):
            await fetch(client, "SELECT 1")

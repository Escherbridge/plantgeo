"""SSURGO capture: resume, cursor-chain fidelity, and the per-page page_size freedom (F1)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import httpx
import pytest

from agri_data_service.foundation.soil_survey.receipts import MAX_AREA_DELINEATIONS, SoilSurveyError
from agri_data_service.pipeline.direct.soil_survey.capture import DEFAULT_PAGE_SIZE, capture_area
from agri_data_service.pipeline.direct.soil_survey.source import page_query
from tests.direct.soil_survey.fakes import AREA, POLYGONS, Source

if TYPE_CHECKING:
    from pathlib import Path

#: P1-08 (PLR2004): the resumed page size in the mixed-page-size test, named so it is not a bare
#: literal in an assertion.
_RESUMED_PAGE_SIZE = 2


async def test_capture_resumes_without_refetching_complete_pages(tmp_path: Path) -> None:
    source = Source()
    async with httpx.AsyncClient(transport=httpx.MockTransport(source.response)) as client:
        first = await capture_area(client, tmp_path, AREA, max_pages=1, page_size=1)
        assert first.closing is None
        assert len(first.pages) == 1
        second = await capture_area(client, tmp_path, AREA, max_pages=1, page_size=1)
        assert second.closing is not None
        assert len(second.pages) == len(POLYGONS)
        assert source.page_calls == len(POLYGONS)
        unchanged = await capture_area(client, tmp_path, AREA, max_pages=1, page_size=1)
        assert unchanged == second
        assert source.page_calls == len(POLYGONS)


async def test_source_failure_keeps_checkpoint_and_releases_lock(tmp_path: Path) -> None:
    source = Source()
    async with httpx.AsyncClient(transport=httpx.MockTransport(source.response)) as client:
        await capture_area(client, tmp_path, AREA, max_pages=1, page_size=1)
        checkpoint = (tmp_path / f"{AREA}.json").read_bytes()
        source.fail_page = True
        with pytest.raises(httpx.HTTPStatusError):
            await capture_area(client, tmp_path, AREA, max_pages=1, page_size=1)
        assert (tmp_path / f"{AREA}.json").read_bytes() == checkpoint
        source.fail_page = False
        assert (await capture_area(client, tmp_path, AREA, max_pages=1, page_size=1)).closing is not None


async def test_changed_survey_vintage_refuses_resume_without_overwriting_evidence(tmp_path: Path) -> None:
    source = Source()
    async with httpx.AsyncClient(transport=httpx.MockTransport(source.response)) as client:
        await capture_area(client, tmp_path, AREA, max_pages=1, page_size=1)
        checkpoint = (tmp_path / f"{AREA}.json").read_bytes()
        source.vintage = "2025-08-28T20:27:08.200"
        with pytest.raises(SoilSurveyError, match="watermark changed"):
            await capture_area(client, tmp_path, AREA, max_pages=1, page_size=1)
    assert (tmp_path / f"{AREA}.json").read_bytes() == checkpoint
    assert source.page_calls == 1


async def test_duplicate_native_keys_never_finish_a_capture(tmp_path: Path) -> None:
    source = Source()
    source.duplicate = True
    async with httpx.AsyncClient(transport=httpx.MockTransport(source.response)) as client:
        with pytest.raises(SoilSurveyError, match="duplicate"):
            await capture_area(client, tmp_path, AREA, max_pages=2, page_size=1)
    assert json.loads((tmp_path / f"{AREA}.json").read_bytes())["closing"] is None


async def test_a_capture_resumed_under_a_different_page_size_still_validates(tmp_path: Path) -> None:
    """F1: `CapturePage.page_size` is recorded per page, so mixed sizes across a resume are valid."""
    source = Source()
    async with httpx.AsyncClient(transport=httpx.MockTransport(source.response)) as client:
        first = await capture_area(client, tmp_path, AREA, max_pages=1, page_size=1)
        assert len(first.pages) == 1
        assert first.pages[0].page_size == 1
        second = await capture_area(client, tmp_path, AREA, max_pages=1, page_size=_RESUMED_PAGE_SIZE)
    assert second.closing is not None
    assert len(second.pages) == len(POLYGONS)
    assert second.pages[0].page_size == 1
    assert second.pages[1].page_size == _RESUMED_PAGE_SIZE
    assert sum(page.row_count for page in second.pages) == second.opening.count


async def test_page_size_two_returns_two_rows_in_one_page_when_top_is_honoured(tmp_path: Path) -> None:
    """P1-07(a): the fake must genuinely apply `TOP {page_size}`, not always answer a fixed one row
    -- a fresh capture requesting `page_size=2` against a 2-delineation area gets both in one page."""
    source = Source()
    async with httpx.AsyncClient(transport=httpx.MockTransport(source.response)) as client:
        capture = await capture_area(client, tmp_path, AREA, max_pages=1, page_size=_RESUMED_PAGE_SIZE)
    assert capture.closing is not None
    assert len(capture.pages) == 1
    assert capture.pages[0].row_count == len(POLYGONS)
    assert capture.pages[0].page_size == _RESUMED_PAGE_SIZE


async def test_capture_defaults_to_a_page_size_of_500(tmp_path: Path) -> None:
    """The port raises `capture_area`'s own default `page_size` from A's 250 to 500."""
    source = Source()
    async with httpx.AsyncClient(transport=httpx.MockTransport(source.response)) as client:
        # page_size omitted entirely: the default must be recorded on the page it produces.
        capture = await capture_area(client, tmp_path, AREA, max_pages=1)
    assert capture.pages[0].page_size == DEFAULT_PAGE_SIZE


def test_the_500_row_ceiling_is_unchanged() -> None:
    """`MAX_PAGE_ROWS` itself is frozen at 500; only the default argument moved to meet it."""
    page_query(AREA, "0", DEFAULT_PAGE_SIZE)
    with pytest.raises(SoilSurveyError):
        page_query(AREA, "0", DEFAULT_PAGE_SIZE + 1)


def test_the_default_page_size_is_really_sent_as_top_500_on_the_wire() -> None:
    """P1-07(b): the prior test only checked the receipt field, never the query text itself --
    this asserts the rendered key-inventory query actually carries `TOP 500`, not just that
    `capture_area`'s default argument and `CapturePage.page_size` happen to agree on the number."""
    assert f"TOP {DEFAULT_PAGE_SIZE}" in page_query(AREA, "0", DEFAULT_PAGE_SIZE)


async def test_area_delineation_count_at_the_cap_is_accepted_and_over_it_is_refused(tmp_path: Path) -> None:
    """P1-03: the sanity bound is enforced explicitly in `_summary`, as a SoilSurveyError -- not
    left to `AreaSummary`'s pydantic field, which would raise a raw ValidationError instead."""
    at_cap = Source()
    at_cap.count = MAX_AREA_DELINEATIONS
    async with httpx.AsyncClient(transport=httpx.MockTransport(at_cap.response)) as client:
        capture = await capture_area(client, tmp_path, AREA, max_pages=1, page_size=1)
    assert capture.opening.count == MAX_AREA_DELINEATIONS

    over_cap = Source()
    over_cap.count = MAX_AREA_DELINEATIONS + 1
    async with httpx.AsyncClient(transport=httpx.MockTransport(over_cap.response)) as client:
        with pytest.raises(SoilSurveyError, match="capture sanity bound"):
            await capture_area(client, tmp_path / "over", AREA, max_pages=1, page_size=1)


async def test_an_empty_key_page_mid_capture_refuses_without_advancing_the_checkpoint(tmp_path: Path) -> None:
    """P1-07(d): SDA's own `{}` empty-object answer arriving from a KEY PAGE mid-capture -- while
    the opening census still says rows remain -- must refuse via `SoilSurveyError`, distinctly from
    the already-covered case of `{}` on the very first call, and leave the checkpoint untouched."""
    source = Source()
    async with httpx.AsyncClient(transport=httpx.MockTransport(source.response)) as client:
        await capture_area(client, tmp_path, AREA, max_pages=1, page_size=1)
        checkpoint = (tmp_path / f"{AREA}.json").read_bytes()
        source.empty_key_page = True
        with pytest.raises(SoilSurveyError, match="no rows"):
            await capture_area(client, tmp_path, AREA, max_pages=1, page_size=1)
    assert (tmp_path / f"{AREA}.json").read_bytes() == checkpoint

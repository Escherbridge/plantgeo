"""Provider intake refusals keep their identity and never become smaller-page retries."""

from __future__ import annotations

# ruff: noqa: PLR2004 - explicit HTTP status and page-size contract cases
import json
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import httpx
import pytest

from agri_data_service.ingest import mtbs
from agri_data_service.pipeline.direct.burn_severity import capture, forward, source
from agri_data_service.pipeline.parquet.availability_extension import AvailabilityExtensionTally
from agri_data_service.pipeline.parquet.lane_registry import LANE_REGISTRY
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from tests.direct.test_burn_severity_direct_adapter import DAY, SessionDouble
from tests.parquet.test_availability_extension import LaneAvailabilityStorage, LoggingBackend

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.asyncio
@pytest.mark.parametrize(("status", "attempts"), [(403, 1), (429, 4), (503, 4)])
async def test_refusal_does_not_restart_geometry_at_smaller_page(status: int, attempts: int) -> None:
    sizes = []

    def respond(request: httpx.Request) -> httpx.Response:
        sizes.append(request.url.params["resultRecordCount"])
        return httpx.Response(status, headers={"Retry-After": "0"}, request=request)

    async def no_sleep(_seconds: float) -> None:
        pass

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        expected = httpx.HTTPStatusError if status == 503 else mtbs.MtbsProviderRefusalError
        with pytest.raises(expected, match=str(status)):
            await mtbs._fetch_page_within_service_limits(
                2022,
                mtbs.PACIFIC_NORTHWEST_BBOX,
                client=client,
                window=mtbs.PageWindow(offset=0, size=50),
                sleep=no_sleep,
            )
    assert sizes == ["50"] * attempts


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [413, 500])
async def test_known_polygon_size_refusal_still_downshifts(status: int) -> None:
    sizes = []

    def respond(request: httpx.Request) -> httpx.Response:
        size = request.url.params["resultRecordCount"]
        sizes.append(size)
        return httpx.Response(status, request=request) if size == "50" else httpx.Response(200, json={"features": []})

    async def no_sleep(_seconds: float) -> None:
        pass

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        _, _, window = await mtbs._fetch_page_within_service_limits(
            2022, mtbs.PACIFIC_NORTHWEST_BBOX, client=client, window=mtbs.PageWindow(offset=0, size=50), sleep=no_sleep
        )
    assert window.size == 25
    assert sizes == ["50"] * (4 if status == 500 else 1) + ["25"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [403, 429])
async def test_provider_refusal_survives_real_finalizer_without_refetch(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    calls = 0
    refusal = mtbs.MtbsProviderRefusalError(status, "60")

    async def fail_source(*_args: object, **_kwargs: object) -> source.BurnSeverityDaySource:
        nonlocal calls
        calls += 1
        raise refusal

    monkeypatch.setattr(forward, "fetch_burn_severity_release_day", fail_source)
    log: list[str] = []
    backend = LoggingBackend(log)
    storage = LaneAvailabilityStorage(backend, log)
    config = forward.BurnSeverityForwardConfig(5, 300, 5, 1, 5, 1)
    with pytest.raises(mtbs.MtbsProviderRefusalError, match=str(status)) as caught:
        await forward._publish_locked_release_day_with_retries(
            SessionDouble(),
            ObjectStore(backend),
            LANE_REGISTRY["burn-severity"],
            DAY,
            ignition_years=(2018,),
            bounding_box=mtbs.PACIFIC_NORTHWEST_BBOX,
            run_id="refusal-test",
            config=config,
            deadline=time.monotonic() + 60,
            today=DAY,
            availability_storage=storage,
            availability=AvailabilityExtensionTally(),
        )
    assert caught.value is refusal
    assert calls == 1
    assert backend.objects == {}
    calls = 0
    with pytest.raises(mtbs.MtbsProviderRefusalError, match=str(status)):
        await source._retry_async("source", fail_source, attempts=5, base_seconds=1, max_seconds=5)
    assert calls == 1


@pytest.mark.parametrize("status", [403, 429])
def test_capture_failure_journal_keeps_safe_status_without_response_body(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, status: int
) -> None:
    original = httpx.Client
    calls = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            status,
            content=b"secret-body",
            headers={"Retry-After": "60", "Authorization": "secret-header"},
            request=request,
        )

    monkeypatch.setattr(
        capture.httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs)
    )
    with pytest.raises(mtbs.MtbsProviderRefusalError, match=str(status)):
        capture.capture_snapshot(tmp_path / "capture")
    raw = (tmp_path / "capture" / "capture-journal.json").read_text()
    journal = json.loads(raw)
    assert journal["failure_http"]["status"] == status
    assert journal["failure_http"]["retry_after"] == "60"
    assert journal["failure_http"]["role"] == "count:2018"
    assert journal["receipts"] == []
    assert calls == 1
    assert "secret" not in raw
    assert "https://" not in raw
    assert not (tmp_path / "capture" / "manifest.json").exists()


@pytest.mark.parametrize("value", ["secret token", "x" * 129, "123\nsecret", "١٢٣"])
def test_retry_after_diagnostic_rejects_nonprotocol_text(value: str) -> None:
    assert mtbs.bounded_retry_after(value) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("header", "expected_wait"),
    [
        ("120", None),
        ("Fri, 11 Sep 2026 00:02:00 GMT", None),
        ("30", 30.0),
        ("Fri, 11 Sep 2026 00:00:30 GMT", 30.0),
        ("١٢٣", 1.0),
        ("-5", 1.0),
        ("invalid", 1.0),
        (None, 1.0),
    ],
)
async def test_429_obeys_cooldown_or_defers_without_an_early_retry(
    monkeypatch: pytest.MonkeyPatch, header: str | None, expected_wait: float | None
) -> None:
    now = datetime(2026, 9, 11, tzinfo=UTC)

    class Clock(datetime):
        @classmethod
        def now(cls, tz: object = None) -> datetime:
            del tz
            return now

    monkeypatch.setattr(mtbs, "datetime", Clock)
    calls = 0
    waits: list[float] = []

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls > 1:
            return httpx.Response(200, json={"count": 0}, request=request)
        headers = [] if header is None else [(b"retry-after", header.encode("utf-8"))]
        return httpx.Response(429, headers=headers, request=request)

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        if expected_wait is None:
            with pytest.raises(mtbs.MtbsProviderRefusalError, match="429"):
                await mtbs._get_query(client, {"returnCountOnly": "true"}, sleep=sleep)
            assert calls == 1
            assert waits == []
        else:
            assert await mtbs._get_query(client, {"returnCountOnly": "true"}, sleep=sleep) == {"count": 0}
            assert calls == 2
            assert waits == [expected_wait]

"""Run-level tests for the soil settlement probe: probe cells, gating, accounting and every fault path."""

from __future__ import annotations

import json
import time
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any, Final
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from agri_data_service.execution.open_meteo_lane import MAX_FETCH_ATTEMPTS
from agri_data_service.ingest.http import MAX_REDIRECTS, TRANSPORT_RETRY_ATTEMPTS, UpstreamError
from agri_data_service.ingest.open_meteo import OpenMeteoRateLimitError
from agri_data_service.pipeline.direct.soil import forward, source
from agri_data_service.pipeline.direct.soil.adapter import DirectSoilFieldError
from agri_data_service.pipeline.direct.soil.forward import SOIL_DEFAULT_TIME_BUDGET_SECONDS, SoilForwardConfig
from agri_data_service.pipeline.direct.soil.products import SOIL_SOURCE_PARAMETERS
from agri_data_service.pipeline.direct.soil.source import (
    SOIL_EDGE_PROBE_CELL_COUNT,
    SoilSourceCache,
    SoilSourceError,
    fill_chunk_day_cache,
    probe_cells,
    probe_soil_edge,
)
from agri_data_service.pipeline.direct.soil.support import Era5LandSupport, quantize_coordinate
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from agri_data_service.pipeline.parquet.source_checkpoint import SourceResponseCheckpoints
from tests.direct.soil.conftest import chunk_body, masked_cell_keys, probe_body
from tests.direct.soil.test_adapter import SessionDouble
from tests.direct.soil.test_forward_command import _statuses, always_granted
from tests.parquet.test_availability_index import MemoryAvailabilityStorage
from tests.parquet.test_objectstore_writer import RecordingBackend

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping, Sequence

    from agri_data_service.pipeline.direct.soil.source import Era5LandChunk
    from agri_data_service.pipeline.direct.soil.support import Era5LandSupportCell

#: Most tests drive `today=CEILING + 5` (`ERA5_LAND_ARCHIVE_PUBLICATION_LAG_DAYS`) through
#: `probe_config()`, so the candidate edge (`CEILING`) and the fourteen-day recheck window are the
#: same fixed dates the brief names -- `2026-09-15` and `2026-09-02..2026-09-15`. The tests that call
#: `forward.main(...)` instead (`test_an_unavailable_probe_gates_the_window_and_the_run_still_completes`,
#: the `main([])` halves of `test_a_throttled_probe_is_deferred_and_the_run_exits_zero` and the
#: thin-day/changed-support tests) use the REAL UTC clock, since `main` has no `--today` flag; those
#: use `OLD_DAY`/`IN_WINDOW_DAY` (real-clock relative, never `CEILING`-relative) with enough margin
#: that `date.today()` (local) versus `datetime.now(UTC).date()` (what `main` actually reads) -- which
#: differ by at most one day -- can never flip which side of a boundary a day falls on.
TODAY: Final = date(2026, 9, 20)
CEILING: Final = date(2026, 9, 15)
WINDOW_FIRST: Final = date(2026, 9, 2)
CHUNKS_PER_DAY: Final = 32
#: A day safely older than every fixed-clock window above AND after `SOIL_DIRECT_WRITER_START_DAY`,
#: for the `probe_config()` (fixed `TODAY`) half of a test. Never mix this with the real-clock
#: `OLD_DAY`/`IN_WINDOW_DAY` below, which exist only for the `forward.main(...)` (real UTC clock) half.
FIXED_OLD_DAY: Final = WINDOW_FIRST - timedelta(days=5)

#: G0's request ceiling: `chunks_per_day * max_days + SOIL_EDGE_PROBE_REQUESTS` = 32*1+1.
REQUEST_CEILING: Final = 33
#: `SoilSourceCache.restore` can resume a null-free 18-cell chunk mid-turn, so the hard weighted
#: ceiling (a hostile re-ask of the 18-cell chunk as a 50-cell one) exceeds the clean-fan-out figure.
WEIGHTED_CEILING: Final = 1602
FETCH_ATTEMPT_CEILING: Final = REQUEST_CEILING * MAX_FETCH_ATTEMPTS
#: A redirect hop fires the same `request` event hook as the original send; see `soil/AGENTS.md`.
HTTP_REQUEST_CEILING: Final = FETCH_ATTEMPT_CEILING * TRANSPORT_RETRY_ATTEMPTS * (1 + MAX_REDIRECTS)
#: The probe's own weighted cost: two locations, at or under the 14-day/10-variable ceiling.
PROBE_WEIGHT: Final = 2
#: Failed sends before the probe's mock transport succeeds, proving `http_requests` counts each one
#: while `fetch_attempts` (one `fetch_lane_capture` call) does not.
PROBE_FAILED_SENDS: Final = 2
#: Failed sends before the chunk client's mock transport succeeds (same proof, one chunk request).
CHUNK_FAILED_SENDS: Final = 1
#: Redirect hops the redirect-path mock transport answers before its final 200.
REDIRECT_HOPS: Final = 1


def probe_config(**overrides: Any) -> SoilForwardConfig:
    """One valid, fully-bounded turn pinned to `TODAY`, so a test states only the field it is about."""
    base = SoilForwardConfig(
        product_id="all",
        max_days=1,
        time_budget_seconds=SOIL_DEFAULT_TIME_BUDGET_SECONDS,
        retry_attempts=4,
        retry_base_seconds=5.0,
        retry_max_seconds=60.0,
        contention_timeout_seconds=300.0,
        today=TODAY,
    )
    return replace(base, **overrides)


def install_common(monkeypatch: pytest.MonkeyPatch, *, support: Era5LandSupport) -> None:
    """Install the settings/session/store/support/lock/retry/sleep/env seams every probe test shares.

    Copies the in-tree settings/`from_settings` precedent verbatim
    (`tests/direct/climate/test_forward_command.py:727-737`): `settings` is a pydantic `BaseSettings`
    *instance*, so a method goes on its class with an explicit `_self`, and `from_settings` needs
    `classmethod(lambda _cls, _source=None: ...)`, never a bare `lambda:`.
    """
    monkeypatch.setattr(
        forward.settings.__class__,
        "require_local_source_loader_database_url",
        lambda _self: "postgresql+asyncpg://unused/never-opened",
    )

    @asynccontextmanager
    async def session(_url: str) -> AsyncIterator[SessionDouble]:
        yield SessionDouble()

    monkeypatch.setattr(forward, "local_source_loader_session", session)
    monkeypatch.setattr(
        forward.ObjectStore, "from_settings", classmethod(lambda _cls, _source=None: ObjectStore(RecordingBackend()))
    )
    monkeypatch.setattr(forward.BotoAvailabilityStorage, "from_settings", classmethod(lambda _cls, _source=None: None))
    monkeypatch.setattr(
        forward,
        "SourceResponseCheckpoints",
        lambda _storage: SourceResponseCheckpoints(MemoryAvailabilityStorage()),
    )
    monkeypatch.setattr(forward, "load_era5_land_support", AsyncMock(return_value=support))
    monkeypatch.setattr(forward, "postgres_lane_day_lock", always_granted)
    monkeypatch.setattr(forward, "_retry_owed_availability", AsyncMock(return_value=0))
    # The SAME `asyncio` module `source.py` backs off through, so `deadline_bounded_sleep` is instant too.
    monkeypatch.setattr(forward.asyncio, "sleep", AsyncMock())
    monkeypatch.delenv("OPEN_METEO_API_KEY", raising=False)


def install_census(monkeypatch: pytest.MonkeyPatch, days: dict[date, str], *, derived: str = "data") -> None:
    """Stub `_tier_status_window` with a fixed per-day census, independent of the queried window."""
    statuses = _statuses(days, derived=derived)

    def stub(_store: object, _product: object, _first_day: date, _last_day: date) -> dict[Any, dict[date, Any]]:
        return statuses

    monkeypatch.setattr(forward, "_tier_status_window", stub)


def _archive_query(url: str) -> dict[str, str]:
    """Parse one archive request URL's single-valued governed query parameters."""
    parsed = parse_qs(urlparse(url).query)
    return {key: values[0] for key, values in parsed.items()}


def _request_days(query: Mapping[str, str]) -> tuple[date, ...]:
    """Return the inclusive day range a request's `start_date`/`end_date` named."""
    start = date.fromisoformat(query["start_date"])
    end = date.fromisoformat(query["end_date"])
    days: list[date] = []
    current = start
    while current <= end:
        days.append(current)
        current += timedelta(days=1)
    return tuple(days)


def _request_cells(query: Mapping[str, str]) -> tuple[tuple[float, float], ...]:
    """Return the requested `(latitude, longitude)` pairs, in request order."""
    latitudes = query["latitude"].split(",")
    longitudes = query["longitude"].split(",")
    return tuple((float(lat), float(lon)) for lat, lon in zip(latitudes, longitudes, strict=True))


def _chunk_for(chunks: Sequence[Era5LandChunk], first_cell: tuple[float, float]) -> Era5LandChunk:
    """Identify which chunk a request answers by its first cell's exact coordinate."""
    for chunk in chunks:
        head = chunk.cells[0]
        if (head.cell_latitude, head.cell_longitude) == first_cell:
            return chunk
    raise AssertionError(f"no chunk starts at {first_cell!r}")


def _default_probe_response(
    query: Mapping[str, str],
    *,
    probe_pair: tuple[Era5LandSupportCell, Era5LandSupportCell],
    valued_days: frozenset[date] | None = None,
) -> str:
    """Answer a probe request with every requested day valued, unless told otherwise."""
    days = _request_days(query)
    valued = frozenset(days) if valued_days is None else valued_days
    return probe_body(probe_pair, days=days, valued_days=valued).decode("utf-8")


def _default_chunk_response(
    query: Mapping[str, str], *, support: Era5LandSupport, chunks: Sequence[Era5LandChunk]
) -> str:
    """Answer a chunk request with a complete, ordinary valued day (the 98-cell mask null)."""
    cells = _request_cells(query)
    chunk = _chunk_for(chunks, cells[0])
    day = _request_days(query)[0]
    return chunk_body(chunk, day=day, null_cell_keys=tuple(masked_cell_keys(support))).decode("utf-8")


def _support_missing_probe_cells(
    support: Era5LandSupport, probe_pair: tuple[Era5LandSupportCell, Era5LandSupportCell]
) -> Era5LandSupport:
    """A support whose coordinate index no longer resolves the two probe-straddling cells."""
    removed = {
        (quantize_coordinate(cell.cell_longitude), quantize_coordinate(cell.cell_latitude)) for cell in probe_pair
    }
    trimmed = {key: cell for key, cell in support.by_coordinate.items() if key not in removed}
    return Era5LandSupport(cells=support.cells, by_coordinate=trimmed)


@pytest.fixture
def probe_pair(support: Era5LandSupport) -> tuple[Era5LandSupportCell, Era5LandSupportCell]:
    """The two on-lattice cells `probe_cells` resolves for the real fixture support."""
    return probe_cells(support)


def test_the_probe_cells_are_the_two_support_cells_beside_the_extent_centre(support: Era5LandSupport) -> None:
    """`probe_cells` resolves to the pinned centre-straddling pair, both outside the null mask."""
    west, east = probe_cells(support)

    assert (west.cell_longitude, west.cell_latitude) == pytest.approx((-118.125, 45.375))
    assert (east.cell_longitude, east.cell_latitude) == pytest.approx((-117.875, 45.375))
    masked = masked_cell_keys(support)
    assert west.cell_key not in masked
    assert east.cell_key not in masked


@pytest.mark.asyncio
async def test_the_probe_is_one_two_location_request_over_the_fourteen_day_recheck_window(
    support: Era5LandSupport,
    chunks: tuple[Era5LandChunk, ...],
    probe_pair: tuple[Era5LandSupportCell, Era5LandSupportCell],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The probe fires once, before any product's walk, over the fixed candidate-edge window."""
    install_common(monkeypatch, support=support)
    install_census(monkeypatch, {})  # nothing owed, so no product ever fans out a chunk
    calls: list[dict[str, str]] = []

    async def fake(_client: object, url: str) -> str:
        query = _archive_query(url)
        calls.append(query)
        if len(_request_cells(query)) == SOIL_EDGE_PROBE_CELL_COUNT:
            return _default_probe_response(query, probe_pair=probe_pair)
        return _default_chunk_response(query, support=support, chunks=chunks)

    monkeypatch.setattr(source, "fetch_archive_daily", fake)

    report = await forward.run_soil_forward(probe_config())

    assert calls[0]["latitude"] == "45.375,45.375"
    assert calls[0]["longitude"] == "-118.125,-117.875"
    assert calls[0]["start_date"] == WINDOW_FIRST.isoformat()
    assert calls[0]["end_date"] == CEILING.isoformat()
    for parameter in SOIL_SOURCE_PARAMETERS:
        assert parameter in calls[0]["daily"].split(",")
    assert report["requests_spent"] == 1
    assert report["weighted_calls"] == pytest.approx(float(PROBE_WEIGHT))
    assert report["fetch_attempts"] == 1
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_a_probe_null_window_day_costs_no_request_and_no_slot(
    support: Era5LandSupport,
    chunks: tuple[Era5LandChunk, ...],
    probe_pair: tuple[Era5LandSupportCell, Era5LandSupportCell],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every window day the probe answered null is gated; only the one valued day is fanned out."""
    older = [CEILING - timedelta(days=offset) for offset in range(4)]  # 09-15..09-12
    valued_day = CEILING - timedelta(days=4)  # 09-11
    install_common(monkeypatch, support=support)
    # `partition_day_statuses` classifies chronologically ascending (`foundation/parquet/paths.py`);
    # the census dict must be built oldest-first for `_owed_and_recheck_days`'s `reversed(...)` walk
    # to visit these days newest-first, exactly as the real census would.
    install_census(monkeypatch, dict.fromkeys((valued_day, *reversed(older)), "missing"))
    calls: list[dict[str, str]] = []

    async def fake(_client: object, url: str) -> str:
        query = _archive_query(url)
        calls.append(query)
        if len(_request_cells(query)) == SOIL_EDGE_PROBE_CELL_COUNT:
            return _default_probe_response(query, probe_pair=probe_pair, valued_days=frozenset({valued_day}))
        return _default_chunk_response(query, support=support, chunks=chunks)

    monkeypatch.setattr(source, "fetch_archive_daily", fake)

    report = await forward.run_soil_forward(probe_config())

    for result in report["results"]:
        assert set(result["probe_gated_days"]) == {day.isoformat() for day in older}
        for entry in result["days"]:
            if entry["day"] in result["probe_gated_days"]:
                assert entry["detail"] == "newer_than_probed_edge"
    chunk_calls = [call for call in calls if len(_request_cells(call)) != SOIL_EDGE_PROBE_CELL_COUNT]
    assert len(chunk_calls) == CHUNKS_PER_DAY
    assert {call["start_date"] for call in chunk_calls} == {valued_day.isoformat()}


@pytest.mark.asyncio
async def test_a_probe_null_day_is_not_fanned_out_on_the_next_run(
    support: Era5LandSupport,
    chunks: tuple[Era5LandChunk, ...],
    probe_pair: tuple[Era5LandSupportCell, Era5LandSupportCell],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two consecutive runs with only probe-null days owed each spend exactly the one probe request."""
    seeded_data_day = CEILING - timedelta(days=1)
    install_common(monkeypatch, support=support)
    # A seeded valued `data` day keeps the probe `ok` (not `blind`), so the null CEILING day is gated
    # by the `ok` "not among valued_days" rule the review asked this test to exercise.
    install_census(monkeypatch, {seeded_data_day: "data", CEILING: "missing"})
    calls: list[dict[str, str]] = []

    async def fake(_client: object, url: str) -> str:
        query = _archive_query(url)
        calls.append(query)
        if len(_request_cells(query)) == SOIL_EDGE_PROBE_CELL_COUNT:
            return _default_probe_response(query, probe_pair=probe_pair, valued_days=frozenset({seeded_data_day}))
        return _default_chunk_response(query, support=support, chunks=chunks)

    monkeypatch.setattr(source, "fetch_archive_daily", fake)

    first = await forward.run_soil_forward(probe_config())
    assert first["probe"]["status"] == "ok"
    assert first["requests_spent"] == 1
    assert len(calls) == 1
    calls_after_first_run = len(calls)

    second = await forward.run_soil_forward(probe_config())
    assert second["probe"]["status"] == "ok"
    assert second["requests_spent"] == 1
    assert len(calls) == calls_after_first_run + 1


@pytest.mark.asyncio
async def test_an_absence_recheck_fans_out_only_when_the_probe_shows_values(
    support: Era5LandSupport,
    chunks: tuple[Era5LandChunk, ...],
    probe_pair: tuple[Era5LandSupportCell, Era5LandSupportCell],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A governed absence inside the recheck window only re-fetches once the probe shows values."""
    seeded_data_day = CEILING - timedelta(days=1)
    install_common(monkeypatch, support=support)
    # Seeding a valued `data` day keeps the probe `ok`, so the absence is gated by the `ok` rule,
    # not folded into `blind`.
    install_census(monkeypatch, {seeded_data_day: "data", CEILING: "absent"})
    calls: list[dict[str, str]] = []

    async def null_probe(_client: object, url: str) -> str:
        query = _archive_query(url)
        calls.append(query)
        if len(_request_cells(query)) == SOIL_EDGE_PROBE_CELL_COUNT:
            return _default_probe_response(query, probe_pair=probe_pair, valued_days=frozenset({seeded_data_day}))
        return _default_chunk_response(query, support=support, chunks=chunks)

    monkeypatch.setattr(source, "fetch_archive_daily", null_probe)
    null_report = await forward.run_soil_forward(probe_config())
    assert null_report["probe"]["status"] == "ok"
    assert len(calls) == 1
    assert null_report["requests_spent"] == 1
    for result in null_report["results"]:
        assert CEILING.isoformat() in result["probe_gated_days"]
        gated = next(entry for entry in result["days"] if entry["day"] == CEILING.isoformat())
        assert gated["outcome"] == "idempotent_noop"
        assert gated["detail"] == "absence_unchanged"

    calls.clear()

    async def valued_probe(_client: object, url: str) -> str:
        query = _archive_query(url)
        calls.append(query)
        if len(_request_cells(query)) == SOIL_EDGE_PROBE_CELL_COUNT:
            return _default_probe_response(
                query, probe_pair=probe_pair, valued_days=frozenset({seeded_data_day, CEILING})
            )
        return _default_chunk_response(query, support=support, chunks=chunks)

    monkeypatch.setattr(source, "fetch_archive_daily", valued_probe)
    valued_report = await forward.run_soil_forward(probe_config())
    assert valued_report["probe"]["status"] == "ok"
    assert len(calls) == 1 + CHUNKS_PER_DAY
    assert valued_report["requests_spent"] == 1 + CHUNKS_PER_DAY


@pytest.mark.asyncio
async def test_a_probe_null_on_a_published_day_marks_the_probe_invalid_and_gates_the_newer_owed_day(
    support: Era5LandSupport,
    chunks: tuple[Era5LandChunk, ...],
    probe_pair: tuple[Era5LandSupportCell, Era5LandSupportCell],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A `data` census day the probe answers null makes the probe `invalid`; the newer owed day is gated."""
    published_day = CEILING - timedelta(days=2)
    owed_day = CEILING - timedelta(days=1)
    install_common(monkeypatch, support=support)
    install_census(monkeypatch, {published_day: "data", owed_day: "missing"})
    calls: list[dict[str, str]] = []

    async def fake(_client: object, url: str) -> str:
        query = _archive_query(url)
        calls.append(query)
        if len(_request_cells(query)) == SOIL_EDGE_PROBE_CELL_COUNT:
            return _default_probe_response(query, probe_pair=probe_pair, valued_days=frozenset())
        return _default_chunk_response(query, support=support, chunks=chunks)

    monkeypatch.setattr(source, "fetch_archive_daily", fake)

    report = await forward.run_soil_forward(probe_config())

    for result in report["results"]:
        assert result["probe_status"] == "invalid"
        gated = next(entry for entry in result["days"] if entry["day"] == owed_day.isoformat())
        assert gated["outcome"] == "source_unsettled"
        assert gated["detail"] == "probe_invalid"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_an_invalid_run_still_spends_at_most_thirty_three_requests(
    support: Era5LandSupport,
    chunks: tuple[Era5LandChunk, ...],
    probe_pair: tuple[Era5LandSupportCell, Era5LandSupportCell],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The candidate day the probe cannot vouch for is gated, not fanned out into a stall (G2)."""
    newest_data = CEILING - timedelta(days=3)
    install_common(monkeypatch, support=support)
    install_census(monkeypatch, {newest_data: "data", CEILING: "missing"})
    calls: list[dict[str, str]] = []

    async def fake(_client: object, url: str) -> str:
        query = _archive_query(url)
        calls.append(query)
        if len(_request_cells(query)) == SOIL_EDGE_PROBE_CELL_COUNT:
            return _default_probe_response(query, probe_pair=probe_pair, valued_days=frozenset())
        return _default_chunk_response(query, support=support, chunks=chunks)

    monkeypatch.setattr(source, "fetch_archive_daily", fake)

    report = await forward.run_soil_forward(probe_config())

    assert report["requests_spent"] <= REQUEST_CEILING
    chunk_calls = [call for call in calls if len(_request_cells(call)) != SOIL_EDGE_PROBE_CELL_COUNT]
    assert all(call["start_date"] != CEILING.isoformat() for call in chunk_calls)
    for result in report["results"]:
        assert result["probe_status"] == "invalid"
        gated = next(entry for entry in result["days"] if entry["day"] == CEILING.isoformat())
        assert gated["outcome"] == "source_unsettled"
        assert gated["detail"] == "probe_invalid"


@pytest.mark.asyncio
async def test_an_unavailable_status_still_walks_a_day_whose_base_rung_already_reads_data(
    support: Era5LandSupport,
    chunks: tuple[Era5LandChunk, ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A day owed only for a derived rung is settled at the base and is never probe-gated."""
    day = CEILING - timedelta(days=1)
    install_common(monkeypatch, support=support)
    install_census(monkeypatch, {day: "data"}, derived="incomplete")
    calls: list[dict[str, str]] = []

    async def fake(_client: object, url: str) -> str:
        query = _archive_query(url)
        calls.append(query)
        if len(_request_cells(query)) == SOIL_EDGE_PROBE_CELL_COUNT:
            raise UpstreamError("simulated transport failure")
        return _default_chunk_response(query, support=support, chunks=chunks)

    monkeypatch.setattr(source, "fetch_archive_daily", fake)

    report = await forward.run_soil_forward(probe_config())

    assert report["probe"]["status"] == "unavailable"
    chunk_calls = [call for call in calls if len(_request_cells(call)) != SOIL_EDGE_PROBE_CELL_COUNT]
    assert any(call["start_date"] == day.isoformat() for call in chunk_calls)


@pytest.mark.asyncio
async def test_a_blind_probe_fans_out_nothing_inside_the_window(
    support: Era5LandSupport,
    chunks: tuple[Era5LandChunk, ...],
    probe_pair: tuple[Era5LandSupportCell, Era5LandSupportCell],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No valued day and no `data` day in the window is `blind`; an older owed day still walks."""
    in_window = CEILING - timedelta(days=3)
    older = WINDOW_FIRST - timedelta(days=5)
    install_common(monkeypatch, support=support)
    install_census(monkeypatch, {older: "missing", in_window: "missing"})  # oldest first, see paths.py
    calls: list[dict[str, str]] = []

    async def fake(_client: object, url: str) -> str:
        query = _archive_query(url)
        calls.append(query)
        if len(_request_cells(query)) == SOIL_EDGE_PROBE_CELL_COUNT:
            return _default_probe_response(query, probe_pair=probe_pair, valued_days=frozenset())
        return _default_chunk_response(query, support=support, chunks=chunks)

    monkeypatch.setattr(source, "fetch_archive_daily", fake)

    report = await forward.run_soil_forward(probe_config())

    for result in report["results"]:
        assert result["probe_status"] == "blind"
    chunk_calls = [call for call in calls if len(_request_cells(call)) != SOIL_EDGE_PROBE_CELL_COUNT]
    assert all(call["start_date"] != in_window.isoformat() for call in chunk_calls)
    assert any(call["start_date"] == older.isoformat() for call in chunk_calls)


UNAVAILABLE_CASES: Final = (
    "four_transport_failures",
    "bare_object_body",
    "thirteen_day_axis",
    "budget_runs_out_in_backoff",
)
#: An old day far enough back that it is always both after `SOIL_DIRECT_WRITER_START_DAY` and older
#: than the probe's real-clock window, whenever this suite actually runs -- `main([])` uses the real
#: current date, so this cannot be one of the fixed `CEILING`-relative literals the other tests use.
OLD_DAY: Final = date.today() - timedelta(days=45)  # noqa: DTZ011 - real-clock relative window, not a defect
#: `today - 10` sits inside the real probe window `[ceiling-13, ceiling]` (`ceiling = today - 5`) on
#: any real execution date, so this owed day is reliably gated regardless of when the suite runs.
IN_WINDOW_DAY: Final = date.today() - timedelta(days=10)  # noqa: DTZ011 - real-clock relative window


@pytest.mark.asyncio
@pytest.mark.parametrize("case", UNAVAILABLE_CASES)
async def test_an_unavailable_probe_gates_the_window_and_the_run_still_completes(  # noqa: PLR0913 - one per fault axis
    support: Era5LandSupport,
    chunks: tuple[Era5LandChunk, ...],
    probe_pair: tuple[Era5LandSupportCell, Era5LandSupportCell],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    case: str,
) -> None:
    """Every upstream or body fault the probe can hit becomes `unavailable`; the run still exits 0."""
    install_common(monkeypatch, support=support)
    install_census(monkeypatch, dict.fromkeys((OLD_DAY, IN_WINDOW_DAY), "missing"))
    calls: list[dict[str, str]] = []
    attempts = 0

    async def fake(_client: object, url: str) -> str:
        nonlocal attempts
        query = _archive_query(url)
        calls.append(query)
        if len(_request_cells(query)) != SOIL_EDGE_PROBE_CELL_COUNT:
            return _default_chunk_response(query, support=support, chunks=chunks)
        attempts += 1
        if case == "four_transport_failures":
            raise UpstreamError("simulated transport failure")
        if case == "budget_runs_out_in_backoff" and attempts == 1:
            raise UpstreamError("simulated transport failure")
        if case == "bare_object_body":
            return '{"latitude": 45.4}'
        if case == "thirteen_day_axis":
            days = _request_days(query)[:-1]
            return probe_body(probe_pair, days=days, valued_days=frozenset(days)).decode("utf-8")
        raise AssertionError(f"unexpected extra probe attempt for case {case!r}")

    monkeypatch.setattr(source, "fetch_archive_daily", fake)
    argv = ["--time-budget-seconds", "10"] if case == "budget_runs_out_in_backoff" else []

    exit_code = await forward.main(argv)
    report = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert report["probe"]["status"] == "unavailable"
    chunk_calls = [call for call in calls if len(_request_cells(call)) != SOIL_EDGE_PROBE_CELL_COUNT]
    assert all(call["start_date"] != IN_WINDOW_DAY.isoformat() for call in chunk_calls)
    for result in report["results"]:
        gated = next(entry for entry in result["days"] if entry["day"] == IN_WINDOW_DAY.isoformat())
        assert gated["outcome"] == "source_unsettled"
        assert gated["detail"] == "probe_unavailable"
    if case != "budget_runs_out_in_backoff":
        assert any(call["start_date"] == OLD_DAY.isoformat() for call in chunk_calls)


@pytest.mark.asyncio
async def test_a_throttled_probe_is_deferred_and_the_run_exits_zero(
    support: Era5LandSupport,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A daily-quota 429 on the probe defers the whole run; the quota circuit stops every chunk (R5)."""
    install_common(monkeypatch, support=support)
    # `probe_config()` pins `today=TODAY`, so the owed day here must be `TODAY`-relative too --
    # never the real-clock `OLD_DAY`, which is for the `main([])` half below only.
    install_census(monkeypatch, {FIXED_OLD_DAY: "missing"})
    calls: list[dict[str, str]] = []

    async def fake(_client: object, url: str) -> str:
        query = _archive_query(url)
        calls.append(query)
        if len(_request_cells(query)) == SOIL_EDGE_PROBE_CELL_COUNT:
            raise OpenMeteoRateLimitError("daily", "Daily API request limit exceeded")
        raise AssertionError("no chunk request should ever be made when the probe is throttled")

    monkeypatch.setattr(source, "fetch_archive_daily", fake)

    report = await forward.run_soil_forward(probe_config())

    assert report["probe"]["status"] == "deferred"
    assert len(calls) == 1
    for result in report["results"]:
        old_entry = next(entry for entry in result["days"] if entry["day"] == FIXED_OLD_DAY.isoformat())
        assert old_entry["outcome"] == "source_unsettled"

    calls.clear()
    install_census(monkeypatch, {OLD_DAY: "missing"})  # main() reads the real clock, so this day must too
    exit_code = await forward.main([])
    assert exit_code == 0


NO_OVERSPEND_CASES: Final = (
    "all_null_frontier",
    "valued_window_day",
    "old_gap_plus_recheck",
    "three_chunks_failing_once_each",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("case", NO_OVERSPEND_CASES)
async def test_no_run_spends_more_than_thirty_three_requests(
    support: Era5LandSupport,
    chunks: tuple[Era5LandChunk, ...],
    probe_pair: tuple[Era5LandSupportCell, Era5LandSupportCell],
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    """Every shape this probe can see still respects the request/weighted/attempt ceilings."""
    install_common(monkeypatch, support=support)
    if case == "all_null_frontier":
        seed_day = CEILING - timedelta(days=4)
        owed = dict.fromkeys((CEILING - timedelta(days=offset) for offset in range(3, -1, -1)), "missing")
        install_census(monkeypatch, {seed_day: "data", **owed})
        valued_days: frozenset[date] = frozenset({seed_day})
    elif case == "old_gap_plus_recheck":
        seed_day = CEILING - timedelta(days=2)
        install_census(monkeypatch, {WINDOW_FIRST - timedelta(days=5): "missing", seed_day: "data", CEILING: "absent"})
        valued_days = frozenset({seed_day})
    else:
        install_census(monkeypatch, {CEILING: "missing"})
        valued_days = frozenset({CEILING})

    failing_chunk_keys: set[str] = (
        {chunks[0].key, chunks[1].key, chunks[2].key} if case == "three_chunks_failing_once_each" else set()
    )
    attempt_counts: dict[str, int] = {}

    async def fake(_client: object, url: str) -> str:
        query = _archive_query(url)
        if len(_request_cells(query)) == SOIL_EDGE_PROBE_CELL_COUNT:
            return _default_probe_response(query, probe_pair=probe_pair, valued_days=valued_days)
        cells = _request_cells(query)
        chunk = _chunk_for(chunks, cells[0])
        count = attempt_counts.get(chunk.key, 0) + 1
        attempt_counts[chunk.key] = count
        if chunk.key in failing_chunk_keys and count == 1:
            raise UpstreamError("simulated transient failure")
        return _default_chunk_response(query, support=support, chunks=chunks)

    monkeypatch.setattr(source, "fetch_archive_daily", fake)

    report = await forward.run_soil_forward(probe_config())

    assert report["probe"]["status"] == "ok"
    assert report["requests_spent"] <= REQUEST_CEILING
    assert report["weighted_calls"] <= WEIGHTED_CEILING
    assert report["fetch_attempts"] <= FETCH_ATTEMPT_CEILING
    # `http_requests` is always 0 here: `fetch_archive_daily` is faked, so no real httpx send ever
    # happens. `test_http_requests_counts_every_real_send_and_fetch_attempts_does_not` and
    # `test_the_chunk_client_counts_every_real_send` own the `HTTP_REQUEST_CEILING` bound instead.


@pytest.mark.asyncio
async def test_a_thin_fanned_out_day_raises_after_at_most_thirty_three_requests(
    support: Era5LandSupport,
    chunks: tuple[Era5LandChunk, ...],
    probe_pair: tuple[Era5LandSupportCell, Era5LandSupportCell],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A day that answers one cell short of the pinned value count raises rather than publishing thin."""
    install_common(monkeypatch, support=support)
    install_census(monkeypatch, {CEILING: "missing"})
    # The mask (98 cells) plus exactly one more cell, drawn from OUTSIDE the mask, so every chunk's
    # answer is one cell short of `ERA5_LAND_VALUE_CELL_COUNT` (1,469, not 1,470).
    thin_mask = frozenset({*masked_cell_keys(support), support.cells[len(masked_cell_keys(support))].cell_key})
    calls: list[dict[str, str]] = []

    async def fake(_client: object, url: str) -> str:
        query = _archive_query(url)
        calls.append(query)
        if len(_request_cells(query)) == SOIL_EDGE_PROBE_CELL_COUNT:
            return _default_probe_response(query, probe_pair=probe_pair)
        cells = _request_cells(query)
        chunk = _chunk_for(chunks, cells[0])
        day = _request_days(query)[0]
        return chunk_body(chunk, day=day, null_cell_keys=tuple(thin_mask)).decode("utf-8")

    monkeypatch.setattr(source, "fetch_archive_daily", fake)

    with pytest.raises(DirectSoilFieldError):
        await forward.run_soil_forward(probe_config())
    assert len(calls) == 1 + CHUNKS_PER_DAY

    calls.clear()
    exit_code = await forward.main([])
    assert exit_code == 1


@pytest.mark.asyncio
async def test_weighted_calls_and_fetch_attempts_are_reported(
    support: Era5LandSupport,
    chunks: tuple[Era5LandChunk, ...],
    probe_pair: tuple[Era5LandSupportCell, Era5LandSupportCell],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A clean fan-out reports the exact 33/1,570/33 triple; one retried chunk adds to attempts alone."""
    install_common(monkeypatch, support=support)
    install_census(monkeypatch, {CEILING: "missing"})
    calls: list[dict[str, str]] = []

    async def clean(_client: object, url: str) -> str:
        query = _archive_query(url)
        calls.append(query)
        if len(_request_cells(query)) == SOIL_EDGE_PROBE_CELL_COUNT:
            return _default_probe_response(query, probe_pair=probe_pair, valued_days=frozenset({CEILING}))
        return _default_chunk_response(query, support=support, chunks=chunks)

    monkeypatch.setattr(source, "fetch_archive_daily", clean)
    report = await forward.run_soil_forward(probe_config())

    assert report["requests_spent"] == 1 + CHUNKS_PER_DAY
    assert report["weighted_calls"] == pytest.approx(1570.0)
    assert report["fetch_attempts"] == 1 + CHUNKS_PER_DAY

    calls.clear()
    attempt_counts: dict[str, int] = {}

    async def flaky(_client: object, url: str) -> str:
        query = _archive_query(url)
        calls.append(query)
        if len(_request_cells(query)) == SOIL_EDGE_PROBE_CELL_COUNT:
            return _default_probe_response(query, probe_pair=probe_pair, valued_days=frozenset({CEILING}))
        cells = _request_cells(query)
        chunk = _chunk_for(chunks, cells[0])
        count = attempt_counts.get(chunk.key, 0) + 1
        attempt_counts[chunk.key] = count
        if chunk.key == chunks[0].key and count == 1:
            raise UpstreamError("simulated transient failure")
        return _default_chunk_response(query, support=support, chunks=chunks)

    monkeypatch.setattr(source, "fetch_archive_daily", flaky)
    retried_report = await forward.run_soil_forward(probe_config())

    assert retried_report["requests_spent"] == 1 + CHUNKS_PER_DAY
    assert retried_report["weighted_calls"] == pytest.approx(report["weighted_calls"])
    assert retried_report["fetch_attempts"] == 1 + CHUNKS_PER_DAY + 1


@pytest.mark.asyncio
async def test_http_requests_counts_every_real_send_and_fetch_attempts_does_not(
    support: Era5LandSupport,
    probe_pair: tuple[Era5LandSupportCell, Era5LandSupportCell],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """G1: `http_requests` counts wire sends (transport retries); `fetch_attempts` counts scaffold calls."""
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts <= PROBE_FAILED_SENDS:
            raise httpx.ConnectError("simulated connect failure", request=request)
        query = _archive_query(str(request.url))
        body = _default_probe_response(query, probe_pair=probe_pair).encode("utf-8")
        return httpx.Response(200, content=body)

    transport = httpx.MockTransport(handler)

    @asynccontextmanager
    async def fake_upstream_client(_bounds: object) -> AsyncIterator[httpx.AsyncClient]:
        async with httpx.AsyncClient(transport=transport) as client:
            yield client

    monkeypatch.setattr(source, "upstream_client", fake_upstream_client)
    monkeypatch.setattr(source.asyncio, "sleep", AsyncMock())

    cache = SoilSourceCache(request_budget=REQUEST_CEILING)
    probe = await probe_soil_edge(
        support=support,
        window_first=WINDOW_FIRST,
        window_last=CEILING,
        cache=cache,
        deadline=time.monotonic() + 60.0,
    )

    assert probe.status == "ok"
    assert cache.fetch_attempts == 1
    assert cache.http_requests == PROBE_FAILED_SENDS + 1


@pytest.mark.asyncio
async def test_the_chunk_client_counts_every_real_send(
    chunks: tuple[Era5LandChunk, ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """G1/G3: the chunk-fetch client counts every real send too; one retry adds to `http_requests` alone."""
    one_chunk = chunks[:1]
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts <= CHUNK_FAILED_SENDS:
            raise httpx.ConnectError("simulated connect failure", request=request)
        query = _archive_query(str(request.url))
        chunk = _chunk_for(one_chunk, _request_cells(query)[0])
        body = chunk_body(chunk, day=CEILING, null_cell_keys=())
        return httpx.Response(200, content=body)

    transport = httpx.MockTransport(handler)

    @asynccontextmanager
    async def fake_upstream_client(_bounds: object) -> AsyncIterator[httpx.AsyncClient]:
        async with httpx.AsyncClient(transport=transport) as client:
            yield client

    monkeypatch.setattr(source, "upstream_client", fake_upstream_client)
    monkeypatch.setattr(source.asyncio, "sleep", AsyncMock())

    cache = SoilSourceCache(request_budget=len(one_chunk))
    await fill_chunk_day_cache(day=CEILING, chunks=one_chunk, cache=cache, concurrency=1)

    assert cache.requests_spent == 1
    assert cache.fetch_attempts == 1
    assert cache.http_requests == CHUNK_FAILED_SENDS + 1


@pytest.mark.asyncio
async def test_a_redirect_hop_counts_as_an_extra_http_request(
    support: Era5LandSupport,
    probe_pair: tuple[Era5LandSupportCell, Era5LandSupportCell],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """G2: `upstream_client`'s own redirect following fires the hook too; `fetch_attempts` stays 1."""
    redirected = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal redirected
        if not redirected:
            redirected = True
            return httpx.Response(302, headers={"location": str(request.url)})
        query = _archive_query(str(request.url))
        body = _default_probe_response(query, probe_pair=probe_pair).encode("utf-8")
        return httpx.Response(200, content=body)

    transport = httpx.MockTransport(handler)

    @asynccontextmanager
    async def fake_upstream_client(_bounds: object) -> AsyncIterator[httpx.AsyncClient]:
        # The same `follow_redirects`/`max_redirects` shape `ingest/http.py::upstream_client` builds.
        async with httpx.AsyncClient(transport=transport, follow_redirects=True, max_redirects=MAX_REDIRECTS) as client:
            yield client

    monkeypatch.setattr(source, "upstream_client", fake_upstream_client)
    monkeypatch.setattr(source.asyncio, "sleep", AsyncMock())

    cache = SoilSourceCache(request_budget=REQUEST_CEILING)
    probe = await probe_soil_edge(
        support=support,
        window_first=WINDOW_FIRST,
        window_last=CEILING,
        cache=cache,
        deadline=time.monotonic() + 60.0,
    )

    assert probe.status == "ok"
    assert cache.fetch_attempts == 1
    assert cache.http_requests == REDIRECT_HOPS + 1
    assert cache.http_requests <= HTTP_REQUEST_CEILING


@pytest.mark.asyncio
async def test_a_deadline_already_spent_before_the_probe_charges_nothing(
    support: Era5LandSupport, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R1: a spent deadline is caught before the request is charged, not discovered mid-backoff."""
    cache = SoilSourceCache(request_budget=REQUEST_CEILING)
    deadline = time.monotonic()
    monkeypatch.setattr(source.time, "monotonic", lambda: deadline + 1_000.0)

    probe = await probe_soil_edge(
        support=support,
        window_first=WINDOW_FIRST,
        window_last=CEILING,
        cache=cache,
        deadline=deadline,
    )

    assert probe.status == "unavailable"
    assert cache.requests_spent == 0
    assert cache.weighted_calls == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_a_changed_support_missing_a_probe_cell_exits_one(
    support: Era5LandSupport,
    probe_pair: tuple[Era5LandSupportCell, Era5LandSupportCell],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`probe_cells` finding the support has changed shape is the one exit-1 path the probe adds."""
    install_common(monkeypatch, support=_support_missing_probe_cells(support, probe_pair))
    install_census(monkeypatch, {})

    with pytest.raises(SoilSourceError, match="not on the pinned ERA5-Land"):
        await forward.run_soil_forward(probe_config())

    exit_code = await forward.main([])

    assert exit_code == 1


def test_a_product_with_only_absence_recheck_gates_reports_idempotent_noop() -> None:
    """CR-5: a product whose only published entries are absence-recheck gates is a no-op, not unsettled."""
    gated_day = forward._stopped_day(CEILING, outcome="idempotent_noop", detail="absence_unchanged")

    outcome = forward._product_outcome((CEILING,), [gated_day], probe_gated_days=[CEILING])

    assert outcome == "idempotent_noop"


@pytest.mark.asyncio
async def test_an_unavailable_probe_detail_names_the_error_class(
    support: Era5LandSupport, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CR-4: the probe's `unavailable` detail carries the exception class name and message."""
    monkeypatch.setattr(source, "fetch_archive_daily", AsyncMock(side_effect=UpstreamError("simulated failure")))
    monkeypatch.setattr(source.asyncio, "sleep", AsyncMock())
    cache = SoilSourceCache(request_budget=1)

    probe = await probe_soil_edge(
        support=support,
        window_first=WINDOW_FIRST,
        window_last=CEILING,
        cache=cache,
        deadline=time.monotonic() + 60.0,
    )

    assert probe.status == "unavailable"
    assert probe.detail is not None
    assert probe.detail.startswith("SoilSourceUnsettledError:")

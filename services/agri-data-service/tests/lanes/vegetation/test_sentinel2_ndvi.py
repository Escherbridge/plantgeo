"""`vegetation-sentinel2-ndvi-direct-forward` through real runner turns: the shipped TOML, the real S14 strategy.

Two process edges are faked. The STAC probe goes through the handed `ProviderClient`
(`ScriptedUpstream`, which refuses every non-probe send, proving the Wave O bypass). The day walk
(STAC paging + COG reads) is faked at `ingest/vegetation.py::collect_ndvi_grid_records` as the
strategy module binds it -- the one seam both this strategy and the legacy `source.py` call.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

from agri_data_service.execution.lane_specs import LANE_SPECS
from agri_data_service.foundation.lane_config import load_lane_configs
from agri_data_service.foundation.region import load_region
from agri_data_service.ingest.http import UpstreamPayloadError
from agri_data_service.ingest.vegetation import NDVI_GRID_NAME, GridSampleOutcome
from agri_data_service.pipeline.direct.vegetation import rows as legacy_rows
from agri_data_service.pipeline.direct.vegetation import source as legacy_source
from agri_data_service.pipeline.direct.vegetation import support as legacy_support
from agri_data_service.pipeline.lanes.vegetation import sentinel2_ndvi
from agri_data_service.pipeline.lanes.vegetation.sentinel2_ndvi import ENDPOINT, STRATEGY, Sentinel2NdviStrategy
from agri_data_service.pipeline.lanes.vegetation.support import (
    NDVI_SUPPORT_CELL_KEY_PREFIX,
    VegetationSupportCell,
    build_support,
)
from agri_data_service.pipeline.runner.reader import registered_stream_contract
from agri_data_service.pipeline.runner.resolve import resolve_strategy
from tests.lane_config.builders import REAL_LANES_DIRECTORY
from tests.runner.fakes import (
    ManualClock,
    MemoryLaneStore,
    ScriptedUpstream,
    ports_for,
    run,
    spec_for,
    unwritten_by_day,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    import pyarrow as pa  # type: ignore[import-untyped]
    import pytest

    from agri_data_service.foundation.lane_config.models import LaneConfig
    from agri_data_service.ingest.vegetation import GridSampleWindow

LANE_ID = "vegetation-sentinel2-ndvi-direct-forward"
STREAM = "vegetation"
#: today 2026-10-24, lag 7, recheck 14: the forward (and probe) window is 2026-10-04..2026-10-17.
NOW = datetime(2026, 10, 24, 12, tzinfo=UTC)
WINDOW_FIRST, WINDOW_LAST = date(2026, 10, 4), date(2026, 10, 17)
LATTICE_CELLS = load_region("pnw").analysis_lattices["analysis-0p25"].cells()
SUPPORT = build_support(
    [
        VegetationSupportCell(
            cell_id=f"cell-{index:04d}",
            cell_key=cell.cell_key,
            cell_longitude=cell.longitude,
            cell_latitude=cell.latitude,
        )
        for index, cell in enumerate(LATTICE_CELLS)
    ]
)
TEST_STRATEGY = Sentinel2NdviStrategy(support=lambda: SUPPORT)
FIRST, SECOND, THIRD = SUPPORT.cells[0], SUPPORT.cells[1], SUPPORT.cells[2]


def _window_days(first: date = WINDOW_FIRST, last: date = WINDOW_LAST) -> list[date]:
    return [first + timedelta(days=offset) for offset in range((last - first).days + 1)]


def _raw_key(cell: VegetationSupportCell) -> str:
    return cell.cell_key.removeprefix(NDVI_SUPPORT_CELL_KEY_PREFIX)


def _record(cell: VegetationSupportCell, day: date, *, ndvi: object = 0.6123, cloud: object = 4.5) -> dict[str, object]:
    """One sampled-cell record in `ingest/vegetation.py::ndvi_grid_record`'s exact shape."""
    return {
        "cellKey": _raw_key(cell),
        "gridName": NDVI_GRID_NAME,
        "centerLatitude": cell.cell_latitude,
        "centerLongitude": cell.cell_longitude,
        "ndvi": ndvi,
        "observedAt": f"{day.isoformat()}T19:05:12.345Z",
        "sceneId": f"S2B_11TNH_{day.strftime('%Y%m%d')}_0_L2A",
        "cloudCover": cloud,
        "sampleCount": 25,
    }


def _legacy_checksum(record: Mapping[str, object]) -> str:
    """The legacy `_record_sha256` algorithm, restated here so a drift in either copy goes red."""
    fields = ("cellKey", "gridName", "ndvi", "observedAt", "sceneId", "cloudCover", "sampleCount")
    payload = json.dumps({name: record.get(name) for name in fields}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _install_walk(
    monkeypatch: pytest.MonkeyPatch,
    records_by_day: Mapping[date, Sequence[dict[str, object]]],
    *,
    truncated: frozenset[date] = frozenset(),
    payload_error: frozenset[date] = frozenset(),
    module: object = sentinel2_ndvi,
) -> list[GridSampleWindow]:
    """Fake the STAC+COG walk for one module; returns every window it was asked."""
    asked: list[GridSampleWindow] = []

    async def collect(client: object, window: GridSampleWindow) -> GridSampleOutcome:  # noqa: ARG001
        asked.append(window)
        day = window.start.date()
        if day in payload_error:
            raise UpstreamPayloadError("Earth Search returned an invalid item collection")
        return GridSampleOutcome(
            records=[dict(record) for record in records_by_day.get(day, ())],
            cells_requested=len(SUPPORT.cells),
            truncated=day in truncated,
        )

    monkeypatch.setattr(module, "collect_ndvi_grid_records", collect)
    return asked


def _stac_probe(newest: date | None) -> Callable[[str, dict[str, str], bool], bytes]:
    """The probe's STAC answer: the newest matching scene, or none; any fan-out send fails the test."""

    def answer(endpoint: str, parameters: dict[str, str], probe: bool) -> bytes:  # noqa: ARG001
        if not probe:
            raise AssertionError(f"vegetation.sentinel2_ndvi fanned out through the provider client ({endpoint!r})")
        features = [] if newest is None else [{"properties": {"datetime": f"{newest.isoformat()}T19:05:12.345Z"}}]
        return json.dumps({"type": "FeatureCollection", "features": features}).encode("utf-8")

    return answer


def _shipped_lane() -> LaneConfig:
    return load_lane_configs(
        REAL_LANES_DIRECTORY, load_region("pnw"), stream_contract=registered_stream_contract
    ).lanes[LANE_ID]


def _config_lane(*, gap_fill: bool = False) -> LaneConfig:
    """The shipped lane as G6 will flip it (tests/lanes/watersheds/test_nhdplus.py pattern)."""
    lane = _shipped_lane().model_copy(update={"executor": "config"})
    if gap_fill:
        lane = lane.model_copy(update={"schedule": lane.schedule.model_copy(update={"gap_fill_enabled": True})})
    return lane


async def _turn(
    lane: LaneConfig, store: MemoryLaneStore, upstream: ScriptedUpstream, *, compare: bool = False
) -> tuple[int, dict[str, object]]:
    spec = spec_for(lane, mode="forward", compare=compare)
    ports = ports_for(TEST_STRATEGY, store, upstream=upstream, clock=ManualClock(NOW), compare=compare)
    return await run(spec, ports)


def _asked_days(windows: Sequence[GridSampleWindow]) -> list[date]:
    return sorted(window.start.date() for window in windows)


# --- the shipped config ---------------------------------------------------------------------------


def test_the_shipped_lane_loads_dark_mirrors_the_legacy_cadence_and_resolves() -> None:
    lane = _shipped_lane()
    legacy = LANE_SPECS[LANE_ID]

    assert (lane.kind, lane.nature, lane.executor, lane.enabled) == ("ingest", "daily_series", "legacy", True)
    assert resolve_strategy(lane) is STRATEGY
    assert lane.schedule.forward_cron == legacy.schedule
    assert lane.days is not None
    assert lane.days.publication_lag_days == legacy.publication_lag_days
    # The floor reaches below the legacy writer floor so the G6 gap-fill enable owns 2026-09-01..05.
    assert lane.days.floor == date(2026, 9, 1) < date.fromisoformat(str(legacy.writer_floor))
    assert (lane.schedule.gap_fill_enabled, lane.schedule.gap_fill_enabled_at_gate) == (False, "G6")
    assert [(stream.slug, stream.volatile_columns) for stream in lane.streams] == [(STREAM, ("data_available_at",))]


# --- full turns, through run_turn ---------------------------------------------------------------


async def test_a_full_ndvi_day_writes_one_governed_row_per_filled_cell(monkeypatch: pytest.MonkeyPatch) -> None:
    day = date(2026, 10, 10)
    clear, cloudy = _record(FIRST, day, ndvi=0.7, cloud=0.0), _record(FIRST, day, ndvi=0.2, cloud=12.0)
    asked = _install_walk(monkeypatch, {day: [cloudy, clear, _record(SECOND, day), _record(THIRD, day, cloud=None)]})
    upstream = ScriptedUpstream(answer=_stac_probe(WINDOW_LAST))
    store = MemoryLaneStore()

    exit_code, payload = await _turn(_config_lane(), store, upstream)

    assert exit_code == 0, payload
    assert payload["edge"] == WINDOW_LAST.isoformat()
    rows = store.tables[(STREAM, day)].to_pylist()
    assert [row["cell_id"] for row in rows] == [FIRST.cell_id, SECOND.cell_id, THIRD.cell_id]
    first = rows[0]
    # A reported 0.0 cloud cover is the CLEAREST candidate; release_count says it beat one other.
    assert (first["metric_value"], first["release_count"]) == (0.7, 2)
    assert first["observation_checksum"] == _legacy_checksum(clear)
    assert (first["grid_name"], first["metric_name"]) == (NDVI_GRID_NAME, "ndvi")
    assert first["allowed_client_exposure"] is True
    assert (first["cell_longitude"], first["cell_latitude"]) == (FIRST.cell_longitude, FIRST.cell_latitude)
    assert first["data_available_at"].tzinfo is not None
    # Every window day up to the probed edge is walked, and only through the module's own walk.
    assert _asked_days(asked) == _window_days()
    assert {window.bbox for window in asked} == {SUPPORT.bbox}
    assert upstream.fan_out_sends == []
    (probe,) = upstream.sends
    assert (probe.endpoint, probe.parameters["limit"]) == (ENDPOINT, "1")
    assert probe.parameters["sortby"] == "-properties.datetime"
    assert probe.parameters["bbox"] == SUPPORT.bbox


async def test_the_partial_day_policy_is_the_legacy_forward_s(monkeypatch: pytest.MonkeyPatch) -> None:
    """Derived from `pipeline/direct/vegetation`, never guessed.

    - ONE filled cell of 1,568 publishes the whole day: `source.py::VegetationDaySource.is_governed_absence`
      (line 126) is `len(values) == 0`, and `adapter.py::__call__`'s else-branch writes any other count.
    - ZERO cells with a LATER day published with values is a governed absence: `adapter.py` lines
      116-118 and `forward.py::_mirrored_past_day` (lines 629-632, the earliest later `data` day).
    - ZERO cells with nothing later published is refused, not governed, and retried next turn:
      `adapter.py` lines 118-127 and `forward.py` lines 454-462 (`source_unsettled`).
    """
    sparse_day, later_published = date(2026, 10, 8), date(2026, 10, 12)
    asked = _install_walk(monkeypatch, {sparse_day: [_record(FIRST, sparse_day)]})
    store = MemoryLaneStore()
    store.publish(STREAM, later_published, _legacy_table(later_published, [_record(SECOND, later_published)]))

    exit_code, payload = await _turn(_config_lane(), store, ScriptedUpstream(answer=_stac_probe(WINDOW_LAST)))

    assert exit_code == 0, payload
    assert store.tables[(STREAM, sparse_day)].num_rows == 1
    receipt = store.receipts[(STREAM, sparse_day)]
    assert (receipt.present_units, receipt.expected_units) == (1, 1)
    assert store.status(STREAM, date(2026, 10, 9)) == "absent"
    assert store.status(STREAM, date(2026, 10, 13)) == "missing"
    unwritten = unwritten_by_day(payload)
    assert unwritten["2026-10-13"]["reason"] == "unsettled"
    assert all(entry["reason"] != "refused_partial" for entry in unwritten.values())
    # The already-written day is census-complete, not an owed hole: the walk never re-asks it.
    assert later_published not in _asked_days(asked)


async def test_a_day_governed_absent_is_retracted_once_a_later_walk_finds_cells(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No separate 'un-absent' verb exists for this lane: the forward window just re-asks every day
    inside `absence_recheck_days` every fire, absent or not (AGENTS.md "Probe" accepted cost), and
    `writer.py::_decide_unverified_rewrite` already names the generic S11 rule for what happens next --
    a `Written` settlement over an `absent` status returns `RewriteDecision("retract_disproven_absence")`,
    which writes.
    """
    day, later_published = date(2026, 10, 6), date(2026, 10, 12)
    _install_walk(monkeypatch, {})
    store = MemoryLaneStore()
    store.publish(STREAM, later_published, _legacy_table(later_published, [_record(SECOND, later_published)]))
    first_exit, _ = await _turn(_config_lane(), store, ScriptedUpstream(answer=_stac_probe(WINDOW_LAST)))
    assert first_exit == 0
    assert store.status(STREAM, day) == "absent"

    _install_walk(monkeypatch, {day: [_record(FIRST, day)]})
    exit_code, payload = await _turn(_config_lane(), store, ScriptedUpstream(answer=_stac_probe(WINDOW_LAST)))

    assert exit_code == 0, payload
    assert store.status(STREAM, day) == "data"
    assert store.tables[(STREAM, day)].num_rows == 1


async def test_the_g6_gap_fill_flip_fills_the_oldest_five_days_back_to_the_floor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S12: flipping `gap_fill_enabled` is G6's whole job. The window runs from the TOML floor
    2026-09-01 up to just before the forward window, but `[budget] gap_fill_max_weighted_calls = 5`
    (the legacy `VEGETATION_MAX_DAYS` repair cap) lets only the oldest five days through one fire,
    oldest first (`windows.py::plan_gap_fill` + `select_affordable_days`); the rest wait
    `deferred_budget`. `earth-search` is unweighted, so each day costs exactly one request.
    """
    owed = [date(2026, 9, 1) + timedelta(days=offset) for offset in range(5)]
    asked = _install_walk(monkeypatch, {day: [_record(FIRST, day)] for day in owed})
    store = MemoryLaneStore()
    upstream = ScriptedUpstream(answer=_stac_probe(None))

    spec = spec_for(_config_lane(gap_fill=True), mode="gap-fill")
    ports = ports_for(TEST_STRATEGY, store, upstream=upstream, clock=ManualClock(NOW))
    exit_code, payload = await run(spec, ports)

    assert exit_code == 0, payload
    assert _asked_days(asked) == owed
    assert [store.status(STREAM, day) for day in owed] == ["data"] * 5
    # A gap-fill turn never touches the provider client for this lane: Wave O bypasses it entirely.
    assert upstream.sends == []
    assert unwritten_by_day(payload)[date(2026, 9, 6).isoformat()]["reason"] == "deferred_budget"


async def test_the_stac_probe_is_the_edge_and_newer_days_are_never_walked(monkeypatch: pytest.MonkeyPatch) -> None:
    newest_scene = date(2026, 10, 15)
    asked = _install_walk(monkeypatch, {})
    store = MemoryLaneStore()

    exit_code, payload = await _turn(_config_lane(), store, ScriptedUpstream(answer=_stac_probe(newest_scene)))

    assert exit_code == 0, payload
    assert (payload["edge"], payload["probe_status"]) == (newest_scene.isoformat(), "ok")
    # Scene-less days behind the newest scene are still walked (legacy asks them too) ...
    assert _asked_days(asked) == _window_days(WINDOW_FIRST, newest_scene)
    # ... and days after it are held as ordinary lag, not an alarm.
    newer = unwritten_by_day(payload)["2026-10-16"]
    assert (newer["behind_edge"], "newer_than_probed_edge" in newer["detail"]) == (False, True)


async def test_a_window_with_no_matching_scene_walks_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    asked = _install_walk(monkeypatch, {})

    exit_code, payload = await _turn(_config_lane(), MemoryLaneStore(), ScriptedUpstream(answer=_stac_probe(None)))

    assert exit_code == 0, payload
    assert payload["probe_status"] == "blind"
    assert asked == []


async def test_malformed_upstream_is_a_lean_per_day_strategy_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Legacy retried these, then failed the WHOLE turn (`forward.py` 446-450, 489); here each is one day's error."""
    days = {name: date(2026, 10, 10 + offset) for offset, name in enumerate(("text_ndvi", "off_lattice", "truncated"))}
    good, page = date(2026, 10, 13), date(2026, 10, 14)
    off_lattice = {**_record(FIRST, days["off_lattice"]), "cellKey": "0.1250:0.1250"}
    _install_walk(
        monkeypatch,
        {
            days["text_ndvi"]: [_record(FIRST, days["text_ndvi"], ndvi="not-a-number")],
            days["off_lattice"]: [off_lattice],
            days["truncated"]: [_record(FIRST, days["truncated"])],
            good: [_record(FIRST, good)],
        },
        truncated=frozenset({days["truncated"]}),
        payload_error=frozenset({page}),
    )
    store = MemoryLaneStore()

    exit_code, payload = await _turn(_config_lane(), store, ScriptedUpstream(answer=_stac_probe(WINDOW_LAST)))

    assert exit_code == 0, payload
    unwritten = unwritten_by_day(payload)
    assert {unwritten[day.isoformat()]["reason"] for day in (*days.values(), page)} == {"strategy_error"}
    assert store.tables[(STREAM, good)].num_rows == 1


async def test_compare_against_the_legacy_writer_is_equal_except_its_capture_instant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """G6's proof on the dark shipped lane: legacy fetch+rows vs this strategy, blind only to `data_available_at`.

    The day is the window's first, so no earlier zero-cell day settles `Absent` against it (an absence
    the bucket does not hold would count as a differing day and blur this assertion).
    """
    day = WINDOW_FIRST
    records = [_record(FIRST, day, cloud=7.0), _record(FIRST, day, cloud=1.0), _record(SECOND, day)]
    _install_walk(monkeypatch, {day: records})
    store = MemoryLaneStore()
    store.publish(STREAM, day, await _legacy_published(monkeypatch, day, records))

    exit_code, payload = await _turn(
        _shipped_lane(), store, ScriptedUpstream(answer=_stac_probe(WINDOW_LAST)), compare=True
    )

    assert exit_code == 0, payload
    compared = payload["compare_result"][STREAM]
    assert (compared["days_equal"], compared["days_differ"]) == (1, 0)


# --- legacy-side builders ------------------------------------------------------------------------


def _legacy_support() -> legacy_support.VegetationSupport:
    return legacy_support.build_support(
        [
            legacy_support.VegetationSupportCell(
                cell_id=cell.cell_id,
                cell_key=cell.cell_key,
                cell_longitude=cell.cell_longitude,
                cell_latitude=cell.cell_latitude,
            )
            for cell in SUPPORT.cells
        ]
    )


async def _legacy_published(
    monkeypatch: pytest.MonkeyPatch, day: date, records: Sequence[dict[str, object]]
) -> pa.Table:
    """The day exactly as the legacy writer publishes it, stamped an hour after midnight (an older capture)."""
    _install_walk(monkeypatch, {day: records}, module=legacy_source)
    # The walk is faked (above), so no real httpx client ever sends; `fetch_vegetation_day` only
    # needs something of the declared shape (`httpx.AsyncClient | None`), never a live connection.
    fetched = await legacy_source.fetch_vegetation_day(
        day=day, support=_legacy_support(), deadline=math.inf, client=cast("Any", object())
    )
    return legacy_rows.vegetation_day_table(
        day=day, values=fetched.values, data_available_at=datetime(day.year, day.month, day.day, 1, tzinfo=UTC)
    )


def _legacy_table(day: date, records: Sequence[dict[str, object]]) -> pa.Table:
    """A published legacy day built without a fetch, for census-only seeding."""
    support = _legacy_support()
    values: list[legacy_source.VegetationCellValue] = []
    for record in records:
        cell = support.resolve(str(record["cellKey"]))
        if cell is None:
            raise AssertionError(f"record cellKey {record['cellKey']!r} is not on the test support")
        values.append(legacy_source._select_clearest(cell, [record]))
    return legacy_rows.vegetation_day_table(day=day, values=tuple(values), data_available_at=NOW)

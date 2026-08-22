"""A continuation may only claim freshness the provider actually published, and must clone the rest.

The lanes' `HistoricalBackfillWindow` fixes the span at four calendar years, so a continuation slides
the whole window and necessarily re-persists the overlap under a new release lineage. These tests pin
the three things that keeps honest: the window arithmetic, the refusals that stop a cheap-looking
re-plan from costing a full duplicate replay, and the verbatim inheritance of everything else.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from agri_data_service.execution.backfill_types import (
    HistoricalBackfillWindow,
    four_calendar_years_before,
)
from agri_data_service.execution.historical_backfill import (
    HistoricalNasaBackfillPlan,
)
from agri_data_service.execution.historical_open_meteo import (
    OPEN_METEO_ARCHIVE_SCHEMA_VERSION,
    HistoricalOpenMeteoArchivePlan,
)
from agri_data_service.execution.plan_continuation import (
    CONTINUATION_NOTE_SENTINEL,
    CellFrontier,
    ContinuationLane,
    ContinuationRefusal,
    ParameterFrontier,
    PlanContinuationError,
    ProviderFrontier,
    continuation_window,
    decide_continuation,
    declared_frontier,
    frontier_probe_cells,
    load_continuation_source,
    plan_family,
    probe_provider_frontier,
    resolve_frontier,
    retarget_window_suffix,
    scan_plan_staleness,
    sibling_plan_candidates,
    superseding_sibling,
    write_continuation_plan,
)

PLANS_ROOT = Path(__file__).resolve().parent.parent / "plans"
NASA_WEATHER_FAST_PLAN = PLANS_ROOT / "nasa-power-western-na-weather-fast-20220806-20260806.json"
NASA_RADIATION_PLAN = PLANS_ROOT / "nasa-power-western-na-weather-radiation-20220531-20260531.json"
OPEN_METEO_VPD_PLAN = PLANS_ROOT / "open-meteo-era5-land-pnw-vpd-20220802-20260802.json"

# Every declared frontier below is a day this instant has already passed: a continuation may not claim
# days the provider has not published, so a decision's `now` sits at or after its frontier.
MEASURED_AT = datetime(2027, 4, 1, 5, 0, tzinfo=UTC)
# The day the module was built, used where the point of the test is a frontier relative to today.
TODAY = datetime(2026, 8, 9, 5, 0, tzinfo=UTC)
PROBE_CELL_COUNT = 3
NASA_PROBE_CELL_COUNT = 2


def _mark_complete(root: Path, plan_path: Path) -> None:
    locks = root / "locks"
    locks.mkdir(parents=True, exist_ok=True)
    (locks / f"{plan_path.stem}.done").write_text("2026-08-09T00:00:00Z\n", encoding="utf-8")


def _frontier(end_date: date) -> ProviderFrontier:
    return declared_frontier(end_date, measured_at=MEASURED_AT)


def _nasa_plan_on_window(destination: Path, window: HistoricalBackfillWindow) -> Path:
    """Write a real NASA plan retargeted onto another window, so an old lane can be reasoned about."""
    document = json.loads(NASA_WEATHER_FAST_PLAN.read_bytes())
    document["nasa"]["window"] = {
        "start_date": window.start_date.isoformat(),
        "end_date": window.end_date.isoformat(),
    }
    document["release_set_key"] = retarget_window_suffix(document["release_set_key"], window)
    path = destination / f"{retarget_window_suffix(NASA_WEATHER_FAST_PLAN.stem, window)}.json"
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def test_continuation_window_slides_because_the_span_is_frozen() -> None:
    window = continuation_window(date(2026, 11, 30))

    assert window.end_date == date(2026, 11, 30)
    # A tail-only window covering just the new days is structurally impossible on these lanes.
    assert window.start_date == date(2022, 11, 30)
    with pytest.raises(ValueError, match="four calendar years"):
        HistoricalBackfillWindow(start_date=date(2022, 8, 6), end_date=date(2026, 11, 30))


def test_a_leap_day_end_keeps_the_shared_calendar_arithmetic() -> None:
    # Four years before a leap day is normally another leap day...
    assert continuation_window(date(2028, 2, 29)).start_date == date(2024, 2, 29)
    # ...except across a non-leap century, where the lane contract's own fallback applies.
    assert continuation_window(date(2104, 2, 29)).start_date == date(2100, 2, 28)


def test_window_suffix_is_retargeted_where_it_exists_and_appended_where_it_does_not() -> None:
    window = HistoricalBackfillWindow(start_date=date(2022, 11, 30), end_date=date(2026, 11, 30))

    assert (
        retarget_window_suffix("nasa-power-western-na-weather-fast-20220806-20260806", window)
        == "nasa-power-western-na-weather-fast-20221130-20261130"
    )
    assert retarget_window_suffix("lane-with-no-dates", window) == "lane-with-no-dates-20221130-20261130"


def test_resolve_frontier_takes_the_slowest_parameter_and_the_best_cell() -> None:
    cells = (
        CellFrontier(
            "cell-a",
            (
                ParameterFrontier("T2M", date(2026, 8, 7)),
                ParameterFrontier("ALLSKY_SFC_SW_DWN", date(2026, 5, 31)),
            ),
        ),
        # An out-of-domain cell publishes nothing and must not be read as provider lag.
        CellFrontier("cell-b", (ParameterFrontier("T2M", None), ParameterFrontier("ALLSKY_SFC_SW_DWN", None))),
    )

    resolved, limiting = resolve_frontier(cells, ("ALLSKY_SFC_SW_DWN", "T2M"))

    assert resolved == date(2026, 5, 31)
    assert limiting == ("ALLSKY_SFC_SW_DWN",)


def test_resolve_frontier_is_unknown_when_a_parameter_is_published_nowhere() -> None:
    cells = (CellFrontier("cell-a", (ParameterFrontier("T2M", date(2026, 8, 7)), ParameterFrontier("WS2M", None))),)

    resolved, limiting = resolve_frontier(cells, ("T2M", "WS2M"))

    assert resolved is None
    assert limiting == ("WS2M",)


def test_probe_cells_are_spread_across_the_sorted_lattice(tmp_path: Path) -> None:
    source = load_continuation_source(NASA_WEATHER_FAST_PLAN, local_execution_root=tmp_path)

    probed = frontier_probe_cells(source.cells, PROBE_CELL_COUNT)

    assert len(probed) == PROBE_CELL_COUNT
    assert probed[0] == source.cells[0]
    assert probed[-1] == source.cells[-1]
    assert frontier_probe_cells(source.cells, 10_000) == source.cells


def test_an_unretired_plan_is_refused(tmp_path: Path) -> None:
    source = load_continuation_source(NASA_WEATHER_FAST_PLAN, local_execution_root=tmp_path)

    decision = decide_continuation(
        source,
        _frontier(date(2026, 11, 30)),
        output_directory=tmp_path,
        now=MEASURED_AT,
    )

    assert decision.refusal is ContinuationRefusal.NOT_MARKED_COMPLETE
    assert decision.plan_bytes is None
    with pytest.raises(PlanContinuationError, match="no plan to write"):
        write_continuation_plan(decision)


def test_a_frontier_that_has_not_moved_is_refused_not_faked(tmp_path: Path) -> None:
    _mark_complete(tmp_path, NASA_RADIATION_PLAN)
    source = load_continuation_source(NASA_RADIATION_PLAN, local_execution_root=tmp_path)

    decision = decide_continuation(
        source,
        _frontier(source.window.end_date),
        output_directory=tmp_path,
        now=MEASURED_AT,
    )

    assert decision.refusal is ContinuationRefusal.FRONTIER_NOT_ADVANCED
    assert decision.continuation_window is None


def test_an_unknown_frontier_is_refused(tmp_path: Path) -> None:
    _mark_complete(tmp_path, NASA_WEATHER_FAST_PLAN)
    source = load_continuation_source(NASA_WEATHER_FAST_PLAN, local_execution_root=tmp_path)

    decision = decide_continuation(
        source,
        ProviderFrontier(end_date=None, mode="probed", measured_at=MEASURED_AT, cells=(), limiting_parameters=("T2M",)),
        output_directory=tmp_path,
        now=MEASURED_AT,
    )

    assert decision.refusal is ContinuationRefusal.FRONTIER_UNKNOWN


def test_a_one_day_advance_is_refused_because_the_overlap_is_re_persisted(tmp_path: Path) -> None:
    _mark_complete(tmp_path, NASA_WEATHER_FAST_PLAN)
    source = load_continuation_source(NASA_WEATHER_FAST_PLAN, local_execution_root=tmp_path)

    decision = decide_continuation(
        source,
        _frontier(source.window.end_date.replace(day=source.window.end_date.day + 1)),
        output_directory=tmp_path,
        now=MEASURED_AT,
    )

    assert decision.refusal is ContinuationRefusal.ADVANCE_BELOW_MINIMUM
    assert decision.advance_days == 1


def test_a_sufficient_advance_reports_the_duplication_it_will_cause(tmp_path: Path) -> None:
    _mark_complete(tmp_path, NASA_WEATHER_FAST_PLAN)
    source = load_continuation_source(NASA_WEATHER_FAST_PLAN, local_execution_root=tmp_path)

    decision = decide_continuation(
        source,
        _frontier(date(2026, 11, 30)),
        output_directory=tmp_path,
        now=MEASURED_AT,
    )

    assert decision.refusal is None
    assert decision.advance_days == (date(2026, 11, 30) - source.window.end_date).days
    assert decision.continuation_window is not None
    overlap = (source.window.end_date - decision.continuation_window.start_date).days + 1
    assert decision.overlap_days == overlap
    series = decision.cell_count * decision.parameter_count
    assert decision.projected_duplicated_observation_rows == series * overlap
    assert decision.projected_new_observation_rows == series * decision.advance_days
    # The overlap dwarfs the gain, which is exactly why the minimum-advance guard exists.
    assert decision.projected_duplicated_observation_rows > decision.projected_new_observation_rows


def test_the_nasa_continuation_clones_everything_but_the_window_and_identity(tmp_path: Path) -> None:
    _mark_complete(tmp_path, NASA_WEATHER_FAST_PLAN)
    source = load_continuation_source(NASA_WEATHER_FAST_PLAN, local_execution_root=tmp_path)

    decision = decide_continuation(
        source,
        _frontier(date(2026, 11, 30)),
        output_directory=tmp_path,
        now=MEASURED_AT,
    )
    assert decision.plan_bytes is not None
    written = write_continuation_plan(decision)

    assert written.name == "nasa-power-western-na-weather-fast-20221130-20261130.json"
    # Re-parsed through the very contract `historical-nasa-backfill` uses, from the bytes on disk.
    plan = HistoricalNasaBackfillPlan.model_validate_json(written.read_bytes())
    original = HistoricalNasaBackfillPlan.model_validate_json(NASA_WEATHER_FAST_PLAN.read_bytes())
    assert plan.nasa.window.end_date == date(2026, 11, 30)
    assert plan.nasa.window.start_date == date(2022, 11, 30)
    assert plan.nasa.cells == original.nasa.cells
    assert plan.nasa.parameters == original.nasa.parameters
    assert plan.nasa.grid_name == original.nasa.grid_name
    assert plan.nasa.grid_resolution_m == original.nasa.grid_resolution_m
    assert plan.nasa.cell_half_span_degrees == original.nasa.cell_half_span_degrees
    # `_ensure_data_source` raises on any source-block disagreement, so it must be byte-identical.
    assert plan.source == original.source
    assert plan.transform_version == original.transform_version
    assert plan.release_set_key == "nasa-power-western-na-weather-fast-20221130-20261130"
    # release_set_as_of sits past today rather than forecasting a completion.
    assert plan.release_set_as_of > MEASURED_AT
    assert written.read_bytes().endswith(b"\n")
    assert b"\r\n" not in written.read_bytes()


def test_an_existing_artifact_is_never_rewritten(tmp_path: Path) -> None:
    _mark_complete(tmp_path, NASA_WEATHER_FAST_PLAN)
    source = load_continuation_source(NASA_WEATHER_FAST_PLAN, local_execution_root=tmp_path)
    decision = decide_continuation(source, _frontier(date(2026, 11, 30)), output_directory=tmp_path, now=MEASURED_AT)
    write_continuation_plan(decision)

    with pytest.raises(PlanContinuationError, match="never rewritten"):
        write_continuation_plan(decision)


def test_the_open_meteo_continuation_retargets_filename_and_key_independently(tmp_path: Path) -> None:
    _mark_complete(tmp_path, OPEN_METEO_VPD_PLAN)
    source = load_continuation_source(OPEN_METEO_VPD_PLAN, local_execution_root=tmp_path)
    original = HistoricalOpenMeteoArchivePlan.model_validate_json(OPEN_METEO_VPD_PLAN.read_bytes())
    assert source.lane is ContinuationLane.OPEN_METEO
    # This plan's key names a lattice its filename omits, so one may not be derived from the other.
    assert original.release_set_key != OPEN_METEO_VPD_PLAN.stem

    decision = decide_continuation(
        source,
        _frontier(date(2026, 11, 30)),
        output_directory=tmp_path,
        now=MEASURED_AT,
    )
    written = write_continuation_plan(decision)

    plan = HistoricalOpenMeteoArchivePlan.model_validate_json(written.read_bytes())
    assert written.name == "open-meteo-era5-land-pnw-vpd-20221130-20261130.json"
    assert plan.release_set_key == "open-meteo-era5-land-pnw-ndvi-lattice-vpd-20221130-20261130"
    assert plan.cells == original.cells
    assert plan.parameters == original.parameters
    assert plan.chunk_cell_count == original.chunk_cell_count
    assert plan.grid_name == original.grid_name
    assert plan.support_key == original.support_key
    assert plan.source == original.source


def test_the_continuation_note_is_replaced_rather_than_accumulated(tmp_path: Path) -> None:
    _mark_complete(tmp_path, NASA_WEATHER_FAST_PLAN)
    source = load_continuation_source(NASA_WEATHER_FAST_PLAN, local_execution_root=tmp_path)
    first = decide_continuation(source, _frontier(date(2026, 11, 30)), output_directory=tmp_path, now=MEASURED_AT)
    written = write_continuation_plan(first)

    _mark_complete(tmp_path, written)
    second_source = load_continuation_source(written, local_execution_root=tmp_path)
    second = decide_continuation(
        second_source, _frontier(date(2027, 3, 31)), output_directory=tmp_path, now=MEASURED_AT
    )

    assert second.plan_bytes is not None
    description = json.loads(second.plan_bytes)["description"]
    assert description.count(CONTINUATION_NOTE_SENTINEL) == 1
    # The hand-written rationale that predates the mechanism survives.
    assert "CONTINUATION 2026-08-08, split A" in description


def test_a_plan_a_later_sibling_already_covers_is_refused(tmp_path: Path) -> None:
    # This exact redundancy is what the skipped ERA5 PNW plan cost: a second lineage over days a
    # sibling already persisted, which the release-scoped unique index does not dedupe.
    family = tmp_path / "plans"
    family.mkdir()
    old = family / NASA_WEATHER_FAST_PLAN.name
    old.write_bytes(NASA_WEATHER_FAST_PLAN.read_bytes())
    _mark_complete(tmp_path, old)
    source = load_continuation_source(old, local_execution_root=tmp_path)
    later = decide_continuation(source, _frontier(date(2026, 11, 30)), output_directory=family, now=MEASURED_AT)
    write_continuation_plan(later)

    again = decide_continuation(source, _frontier(date(2027, 3, 31)), output_directory=family, now=MEASURED_AT)

    assert again.refusal is ContinuationRefusal.ALREADY_SUPERSEDED
    assert again.plan_bytes is None


def test_supersession_only_counts_the_same_family(tmp_path: Path) -> None:
    window = HistoricalBackfillWindow(start_date=date(2022, 8, 6), end_date=date(2026, 8, 6))
    subject = tmp_path / "nasa-power-western-na-weather-fast-20220806-20260806.json"
    same_family_later = tmp_path / "nasa-power-western-na-weather-fast-20221130-20261130.json"
    other_family_later = tmp_path / "nasa-power-western-na-soil-wetness-20221130-20261130.json"

    assert superseding_sibling(subject, window, [subject, other_family_later]) is None
    assert superseding_sibling(subject, window, [subject, other_family_later, same_family_later]) == same_family_later
    assert plan_family("nasa-power-western-na-weather-fast-20220806-20260806") == "nasa-power-western-na-weather-fast"


def test_a_plan_of_an_uncontinuable_shape_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "not-a-lane-plan-20220101-20260101.json"
    path.write_text(json.dumps({"schema_version": "something-else"}), encoding="utf-8")

    with pytest.raises(PlanContinuationError, match="not a continuable lane plan"):
        load_continuation_source(path, local_execution_root=tmp_path)


def test_supersession_sees_the_sibling_a_previous_run_wrote_to_another_directory(tmp_path: Path) -> None:
    """`--output-directory` elsewhere used to hide the plan just written from the only duplication guard.

    Three ordinary invocations authored three overlapping lineages of one plan, ~3.94 M duplicated
    observation rows each, because the search only ever globbed the source plan's own directory.
    """
    family = tmp_path / "plans"
    family.mkdir()
    elsewhere = tmp_path / "authored"
    elsewhere.mkdir()
    source_path = family / NASA_WEATHER_FAST_PLAN.name
    source_path.write_bytes(NASA_WEATHER_FAST_PLAN.read_bytes())
    _mark_complete(tmp_path, source_path)
    source = load_continuation_source(source_path, local_execution_root=tmp_path)

    first = decide_continuation(source, _frontier(date(2026, 11, 30)), output_directory=elsewhere, now=MEASURED_AT)
    written = write_continuation_plan(first)
    assert written.parent == elsewhere
    assert not (family / written.name).exists()

    again = decide_continuation(source, _frontier(date(2027, 3, 31)), output_directory=elsewhere, now=MEASURED_AT)

    assert again.refusal is ContinuationRefusal.ALREADY_SUPERSEDED
    assert again.plan_bytes is None
    assert again.output_path is None
    assert written in sibling_plan_candidates(source_path, elsewhere)
    assert source_path in sibling_plan_candidates(source_path, elsewhere)


def test_the_candidate_set_does_not_double_count_one_directory(tmp_path: Path) -> None:
    window = HistoricalBackfillWindow(start_date=date(2022, 8, 6), end_date=date(2026, 8, 6))
    source_path = _nasa_plan_on_window(tmp_path, window)
    (tmp_path / "nested").mkdir()

    assert sibling_plan_candidates(source_path, tmp_path) == (source_path,)
    # The same directory reached by a second spelling is still one directory, not two candidates.
    assert sibling_plan_candidates(source_path, tmp_path / "nested" / "..") == (source_path,)


def _foreign_lane_document() -> dict[str, object]:
    """A CAMS-shaped plan: the same top-level `cells`/`chunk_cell_count` keys, a different contract."""
    archive = json.loads(OPEN_METEO_VPD_PLAN.read_bytes())
    return {
        "schema_version": "open-meteo-cams-europe-air-quality-hourly-v1",
        "domain": "europe",
        "cells": archive["cells"][:2],
        "chunk_cell_count": 2,
        "chunk_day_count": 92,
        "parameters": ["pm2_5"],
    }


def test_a_sibling_open_meteo_lane_is_refused_rather_than_misrouted(tmp_path: Path) -> None:
    """CAMS, GloFAS and the ensemble carry the keys the old duck-typing matched; only `schema_version` differs."""
    path = tmp_path / "open-meteo-cams-europe-air-quality-20220802-20260802.json"
    path.write_text(json.dumps(_foreign_lane_document()), encoding="utf-8")

    with pytest.raises(PlanContinuationError, match="not a continuable lane plan"):
        load_continuation_source(path, local_execution_root=tmp_path)


def test_a_malformed_plan_of_a_known_lane_is_this_modules_refusal_not_a_bare_validation_error(
    tmp_path: Path,
) -> None:
    path = tmp_path / "open-meteo-era5-land-pnw-vpd-20220101-20260101.json"
    path.write_text(
        json.dumps({"schema_version": OPEN_METEO_ARCHIVE_SCHEMA_VERSION, "cells": [], "chunk_cell_count": 1}),
        encoding="utf-8",
    )

    with pytest.raises(PlanContinuationError, match="does not satisfy the open-meteo lane contract"):
        load_continuation_source(path, local_execution_root=tmp_path)


@pytest.mark.asyncio
async def test_a_staleness_sweep_skips_foreign_lanes_instead_of_aborting(tmp_path: Path) -> None:
    """One unrelated plan in the directory used to abort the whole sweep with a `pydantic.ValidationError`."""
    plans = tmp_path / "plans"
    plans.mkdir()
    subject = plans / NASA_WEATHER_FAST_PLAN.name
    subject.write_bytes(NASA_WEATHER_FAST_PLAN.read_bytes())
    _mark_complete(tmp_path, subject)
    foreign = plans / "open-meteo-cams-europe-air-quality-20220802-20260802.json"
    foreign.write_text(json.dumps(_foreign_lane_document()), encoding="utf-8")
    malformed = plans / "open-meteo-era5-land-pnw-vpd-20220101-20260101.json"
    malformed.write_text(json.dumps({"schema_version": OPEN_METEO_ARCHIVE_SCHEMA_VERSION}), encoding="utf-8")

    report = await scan_plan_staleness(
        (foreign, subject, malformed),
        local_execution_root=tmp_path,
        probe=False,
        now=MEASURED_AT,
    )

    assert [entry.path for entry in report] == [subject]
    assert report[0].lane is ContinuationLane.NASA
    assert report[0].driver_marked_complete
    # Offline: no frontier was measured, so nothing is claimed about continuability.
    assert report[0].frontier is None
    assert report[0].continuable is None
    assert report[0].days_behind_today == (MEASURED_AT.date() - date(2026, 8, 6)).days


def test_a_declared_frontier_the_provider_has_not_reached_is_refused(tmp_path: Path) -> None:
    """`--end-date 2031-01-01` authored a plan for days that do not exist yet, and would fetch them."""
    _mark_complete(tmp_path, NASA_WEATHER_FAST_PLAN)
    source = load_continuation_source(NASA_WEATHER_FAST_PLAN, local_execution_root=tmp_path)

    decision = decide_continuation(
        source,
        declared_frontier(date(2031, 1, 1), measured_at=TODAY),
        output_directory=tmp_path,
        now=TODAY,
    )

    assert decision.refusal is ContinuationRefusal.FRONTIER_IN_FUTURE
    assert decision.continuation_window is None
    assert decision.plan_bytes is None


def test_a_frontier_of_today_is_not_the_future(tmp_path: Path) -> None:
    """The bound is inclusive: the newest day the provider can possibly have published is today."""
    lagging = _nasa_plan_on_window(
        tmp_path, HistoricalBackfillWindow(start_date=date(2022, 7, 1), end_date=date(2026, 7, 1))
    )
    _mark_complete(tmp_path, lagging)
    source = load_continuation_source(lagging, local_execution_root=tmp_path)

    decision = decide_continuation(
        source,
        declared_frontier(TODAY.date(), measured_at=TODAY),
        output_directory=tmp_path,
        now=TODAY,
    )

    assert decision.refusal is None
    assert decision.continuation_window is not None
    assert decision.continuation_window.end_date == TODAY.date()


def test_a_continuation_that_would_leave_an_uncovered_gap_is_refused(tmp_path: Path) -> None:
    """The window slides, so a far enough jump moves the START past the day the source stopped."""
    stale = _nasa_plan_on_window(
        tmp_path, HistoricalBackfillWindow(start_date=date(2017, 1, 1), end_date=date(2021, 1, 1))
    )
    _mark_complete(tmp_path, stale)
    source = load_continuation_source(stale, local_execution_root=tmp_path)

    decision = decide_continuation(
        source,
        declared_frontier(date(2026, 8, 7), measured_at=TODAY),
        output_directory=tmp_path,
        now=TODAY,
    )

    # 2022-08-07 .. 2026-08-07 would leave 2021-01-02 .. 2022-08-06 covered by neither lineage.
    assert continuation_window(date(2026, 8, 7)).start_date > source.window.end_date + timedelta(days=1)
    assert decision.refusal is ContinuationRefusal.WOULD_LEAVE_COVERAGE_GAP
    assert decision.continuation_window is None
    assert decision.plan_bytes is None


def test_a_continuation_starting_the_day_after_the_source_ended_is_the_accepted_boundary(tmp_path: Path) -> None:
    """Overlap zero is allowed; a hole of one day is not. The boundary is exactly `end + 1`."""
    frontier_end = date(2026, 8, 7)
    touching = continuation_window(frontier_end).start_date - timedelta(days=1)
    stale = _nasa_plan_on_window(
        tmp_path,
        HistoricalBackfillWindow(start_date=four_calendar_years_before(touching), end_date=touching),
    )
    _mark_complete(tmp_path, stale)
    source = load_continuation_source(stale, local_execution_root=tmp_path)

    decision = decide_continuation(
        source,
        declared_frontier(frontier_end, measured_at=TODAY),
        output_directory=tmp_path,
        now=TODAY,
    )

    assert decision.refusal is None
    assert decision.continuation_window is not None
    assert decision.continuation_window.start_date == source.window.end_date + timedelta(days=1)
    assert decision.overlap_days == 0


@pytest.mark.asyncio
async def test_the_nasa_probe_reads_fill_values_as_missing_not_as_freshness(tmp_path: Path) -> None:
    source = load_continuation_source(NASA_WEATHER_FAST_PLAN, local_execution_root=tmp_path)
    series = {
        parameter: {
            "20260805": 12.5,
            "20260806": 12.5,
            # POWER's numeric fill value must never be read as a published day.
            "20260807": -999.0,
        }
        for parameter in source.parameters
    }
    series["T2M"]["20260807"] = 13.0

    def handler(request: httpx.Request) -> httpx.Response:
        assert "power.larc.nasa.gov" in str(request.url)
        assert request.url.params["community"] == "AG"
        return httpx.Response(200, json={"properties": {"parameter": series}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        frontier = await probe_provider_frontier(
            source, probe_cell_count=NASA_PROBE_CELL_COUNT, now=MEASURED_AT, client=client
        )

    assert frontier.mode == "probed"
    assert frontier.end_date == date(2026, 8, 6)
    assert "T2M" not in frontier.limiting_parameters
    assert len(frontier.cells) == NASA_PROBE_CELL_COUNT


@pytest.mark.asyncio
async def test_the_open_meteo_probe_reads_nulls_as_missing(tmp_path: Path) -> None:
    source = load_continuation_source(OPEN_METEO_VPD_PLAN, local_execution_root=tmp_path)
    daily: dict[str, object] = {"time": ["2026-08-05", "2026-08-06", "2026-08-07"]}
    for parameter in source.parameters:
        daily[parameter] = [1.2, 1.3, None]

    def handler(request: httpx.Request) -> httpx.Response:
        assert "archive-api.open-meteo.com" in str(request.url)
        return httpx.Response(200, json={"daily": daily}, headers={"content-type": "application/json"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        frontier = await probe_provider_frontier(source, probe_cell_count=1, now=MEASURED_AT, client=client)

    assert frontier.end_date == date(2026, 8, 6)

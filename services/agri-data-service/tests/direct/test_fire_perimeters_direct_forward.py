"""Config validation, CLI defaults, the memoized watermark, and the repair-audit surface of the report.

No network and no object store: every test here validates a config in isolation, exercises
`MemoizedDirectWatermark` (which by construction opens neither a socket nor a session), or renders
the terminal report / stderr event from a population built in-process. `run_fire_perimeters_forward`
itself always fetches WFIGS first -- this lane's version day is derived from the population, not from
the calendar -- so there is no before-the-floor no-op path to exercise the way
`test_drought_forward.py` has one. The two repair-audit tests NEED DuckDB's `spatial` extension: the
population they render comes from the real 2026-09-15 fixture through `rows.py`.
"""

# ruff: noqa: PLR2004 - the small literal counts and ratios ARE the assertion; naming each one hides it.

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

import pytest

from agri_data_service.foundation.parquet.lane_contract import SourceWatermark
from agri_data_service.pipeline.constants import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.direct.fire_perimeters import forward as forward_module
from agri_data_service.pipeline.direct.fire_perimeters.forward import (
    FIRE_PERIMETERS_DEFAULT_MAX_DAYS,
    FIRE_PERIMETERS_DEFAULT_RETRY_ATTEMPTS,
    FIRE_PERIMETERS_MAX_DAYS,
    FirePerimetersForwardConfig,
    FirePerimetersForwardConfigError,
    MemoizedDirectWatermark,
    _emit_geometry_repairs,
    _report,
    _validate_config,
    parse_args,
    parser,
)
from agri_data_service.pipeline.direct.fire_perimeters.products import (
    FIRE_PERIMETERS_DIRECT_ALL_TIERS,
    FIRE_PERIMETERS_DIRECT_KIND,
    fire_perimeters_lane_registration,
)
from agri_data_service.pipeline.direct.fire_perimeters.rows import fire_perimeter_population
from agri_data_service.pipeline.direct.fire_perimeters.source import FirePerimetersSource
from agri_data_service.pipeline.direct.fire_perimeters.watermark import DirectWatermarkReading, PublishedLadder
from agri_data_service.pipeline.parquet.availability_extension import AvailabilityExtensionTally
from tests.direct.test_fire_perimeters_direct_support import (
    EGYPT_INVALID_POLYGON,
    SKULL_INVALID_MULTIPOLYGON,
    VALID_SQUARE,
    WOLF_CREEK_INVALID_MULTIPOLYGON,
    wfigs_fixture_geometries,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from agri_data_service.pipeline.direct.fire_perimeters.rows import FirePerimeterPopulation

#: The three fixture perimeters the chain repairs, by `poly_SourceOID`.
REPAIRED_SOURCE_OIDS = (EGYPT_INVALID_POLYGON, SKULL_INVALID_MULTIPOLYGON, WOLF_CREEK_INVALID_MULTIPOLYGON)

WATERMARK = SourceWatermark(
    day=date(2026, 9, 6), instant=datetime(2026, 9, 6, 14, 30, tzinfo=UTC), basis="test watermark"
)


def _config(**overrides: object) -> FirePerimetersForwardConfig:
    base: dict[str, object] = {
        "max_days": 1,
        "time_budget_seconds": 60.0,
        "retry_attempts": 3,
        "retry_base_seconds": 1.0,
        "retry_max_seconds": 10.0,
        "contention_timeout_seconds": 30.0,
    }
    base.update(overrides)
    return FirePerimetersForwardConfig(**base)  # type: ignore[arg-type]


@pytest.mark.parametrize("max_days", [0, 2, 5])
def test_any_max_days_but_one_is_refused_because_a_static_lane_owes_one_version(max_days: int) -> None:
    """A turn that quietly published one version under `--max-days 5` would misreport what it did."""
    with pytest.raises(FirePerimetersForwardConfigError, match="static_lookup"):
        _validate_config(_config(max_days=max_days))


def test_the_documented_operator_command_validates() -> None:
    """`python -m agri_data_service.pipeline.direct.fire_perimeters --max-days 1`."""
    config = parse_args(["--max-days", "1"])

    assert config.max_days == FIRE_PERIMETERS_MAX_DAYS


def test_retry_attempts_outside_bounds_are_refused() -> None:
    with pytest.raises(FirePerimetersForwardConfigError, match="retry-attempts"):
        _validate_config(_config(retry_attempts=0))


def test_retry_max_below_retry_base_is_refused() -> None:
    with pytest.raises(FirePerimetersForwardConfigError, match="retry-max-seconds"):
        _validate_config(_config(retry_base_seconds=10.0, retry_max_seconds=1.0))


def test_a_non_finite_time_budget_is_refused() -> None:
    with pytest.raises(FirePerimetersForwardConfigError, match="time-budget-seconds"):
        _validate_config(_config(time_budget_seconds=float("inf")))


def test_the_parser_has_no_product_flag_because_this_lane_has_exactly_one() -> None:
    built = parser()

    with pytest.raises(SystemExit):
        built.parse_args(["--product", "fire-perimeters"])


def test_default_args_parse_to_the_documented_defaults() -> None:
    config = parse_args([])

    assert config.max_days == FIRE_PERIMETERS_DEFAULT_MAX_DAYS
    assert config.retry_attempts == FIRE_PERIMETERS_DEFAULT_RETRY_ATTEMPTS
    assert config.bbox is None


def test_the_bbox_override_is_carried_onto_the_config() -> None:
    """The extent the published version claims to cover; an unconfigured one is a refusal, not a skip.

    THE TWO ARGV TOKENS ARE THE TEST. `--bbox` and its value arrive separately, exactly as a shell
    hands them over, and the value's leading `-125` is not a pure negative number -- so argparse
    reads it as another option and raises "argument --bbox: expected one argument" unless
    `parse_args` rewrites it to `--bbox=-125,...` first. Passing one pre-joined `--bbox=...` token
    here would exercise the CLI without exercising the guard, which is how four sibling writers
    shipped the same crash.
    """
    config = parse_args(["--bbox", "-125,42,-116,49"])

    assert config.bbox == "-125,42,-116,49"


@pytest.mark.asyncio
async def test_the_memoized_watermark_answers_from_the_turns_one_capture() -> None:
    """Both sides of `_fill_static_day`'s race bracket see one immutable capture, so it never loops."""
    memo = MemoizedDirectWatermark(watermark=WATERMARK)

    before = await memo(None, None, today=date(2026, 9, 6))  # type: ignore[arg-type]
    after = await memo(None, None, today=date(2026, 9, 6))  # type: ignore[arg-type]

    assert before is WATERMARK
    assert after is WATERMARK
    assert before.instant == after.instant
    assert memo.reads == 2  # the race bracket reads once before and once after


def test_the_registered_lane_is_still_the_static_watermark_driven_one_this_writer_substitutes_into() -> None:
    """If this lane ever stopped being a static_lookup, the whole no-day-loop design would be wrong."""
    lane = fire_perimeters_lane_registration()

    assert lane.nature == "static_lookup"
    assert lane.publication_lag_days == 0
    assert lane.watermark is not None
    assert lane.writer_ceiling is None


def test_the_direct_tier_ladder_is_the_base_rung_plus_its_three_derived_rungs() -> None:
    assert FIRE_PERIMETERS_DIRECT_KIND == "observed"
    assert FIRE_PERIMETERS_DIRECT_ALL_TIERS[0] == LANE_BASE_ZOOM_TIER
    assert len(FIRE_PERIMETERS_DIRECT_ALL_TIERS) == 4  # the rung count IS the assertion


# ---------------------------------------------------------------------------------------------
# The repair-audit surface: until a schema column lands, the report and the event ARE the audit trail
# ---------------------------------------------------------------------------------------------


def _perimeter(identifier: str, geometry: Mapping[str, Any]) -> dict[str, Any]:
    """One record shaped as `ingest.wfigs.parse_perimeter_collection` emits it, carrying a real geometry."""
    return {
        "uniqueFireIdentifier": identifier,
        "irwinId": None,
        "incidentName": f"{identifier} Fire",
        "fireDiscoveryDateTime": "2026-07-16T01:07:00.000Z",
        "polygonDateTime": "2026-08-30T18:45:00.000Z",
        "gisAcres": 10.0,
        "fireCause": None,
        "incidentTypeCategory": "WF",
        "pooState": "US-OR",
        "percentContained": 30.0,
        "geometry": dict(geometry),
    }


def _live_identity(source_oid: str) -> str:
    return f"2026-WFIGS-{source_oid}"


def _live_population() -> FirePerimeterPopulation:
    """The five real 2026-09-15 perimeters -- two valid, three the chain repairs -- conformed by `rows.py`."""
    geometries = wfigs_fixture_geometries()
    return fire_perimeter_population(
        FirePerimetersSource(
            perimeters=tuple(_perimeter(_live_identity(oid), geometry) for oid, geometry in geometries.items()),
            bbox="-125,42,-111,49",
            fetched_at=WATERMARK.instant or datetime(2026, 9, 6, 14, 30, tzinfo=UTC),
            bytes_read=84_883,
        )
    )


def _all_valid_population() -> FirePerimeterPopulation:
    return fire_perimeter_population(
        FirePerimetersSource(
            perimeters=(_perimeter("OR-A", VALID_SQUARE),),
            bbox="-125,42,-111,49",
            fetched_at=datetime(2026, 9, 6, 14, 30, tzinfo=UTC),
            bytes_read=1024,
        )
    )


def _render_report(population: FirePerimeterPopulation) -> dict[str, object]:
    reading = DirectWatermarkReading(
        watermark=WATERMARK,
        fresh_digest="0" * 64,
        fresh_rows=len(population.rows),
        published=None,
        short_circuited=False,
    )
    ladder = PublishedLadder(
        newest_data_day=None, newest_data_instant=None, newest_marker_day=None, version_count=0, stranded_days=()
    )
    return _report(
        "audit-probe",
        lane=fire_perimeters_lane_registration(),
        today=date(2026, 9, 6),
        source_fetched_at=population.fetched_at,
        population=population,
        reading=reading,
        ladder=ladder,
        verdict_state="stale",
        verdict_detail="test verdict",
        availability=AvailabilityExtensionTally(),
        result=None,
    )


def test_the_terminal_report_names_every_repaired_incident_with_its_area_ratio() -> None:
    """`AGENTS.md`, "Where the repair flag lives": the report is the ONLY audit trail until a column lands."""
    report = _render_report(_live_population())

    assert report["perimeters_conformed"] == 5
    assert report["perimeters_repaired"] == 3
    repairs = report["geometry_repairs"]
    assert isinstance(repairs, list)
    assert [entry["unique_fire_identifier"] for entry in repairs] == sorted(
        _live_identity(oid) for oid in REPAIRED_SOURCE_OIDS
    )
    ratios = {entry["unique_fire_identifier"]: entry["area_change"] for entry in repairs}
    assert ratios[_live_identity(SKULL_INVALID_MULTIPOLYGON)] == pytest.approx(-0.0756, abs=0.001)
    assert ratios[_live_identity(WOLF_CREEK_INVALID_MULTIPOLYGON)] == pytest.approx(-0.0258, abs=0.001)
    assert ratios[_live_identity(EGYPT_INVALID_POLYGON)] == pytest.approx(0.0, abs=1e-9)
    json.dumps(report, sort_keys=True)  # the caller parses this off stdout; it must serialise as-is


def test_an_all_valid_population_reports_zero_repairs_and_an_empty_list() -> None:
    report = _render_report(_all_valid_population())

    assert report["perimeters_repaired"] == 0
    assert report["geometry_repairs"] == []


def test_the_repair_event_fires_once_naming_each_incident_and_never_on_an_all_valid_population(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Silent on a clean feed: an empty event every tick is noise a monitor learns to ignore."""
    emitted: list[dict[str, object]] = []
    monkeypatch.setattr(forward_module, "emit", lambda payload: emitted.append(dict(payload)))

    _emit_geometry_repairs("audit-probe", _all_valid_population())
    assert emitted == []

    _emit_geometry_repairs("audit-probe", _live_population())
    assert len(emitted) == 1
    event = emitted[0]
    assert event["event"] == "fire_perimeters_forward_geometry_repaired"
    assert event["run_id"] == "audit-probe"
    repairs = event["repairs"]
    assert isinstance(repairs, list)
    assert {entry["unique_fire_identifier"] for entry in repairs} == {
        _live_identity(oid) for oid in REPAIRED_SOURCE_OIDS
    }
    json.dumps(event, sort_keys=True)

"""The lane registry: thirteen streams, three natures, four return shapes folded into one, cited floors.

The exporters themselves are exercised by their own lane tests; what is pinned here is the
integration surface -- that every registered slug has a schema the writer can autoload, that the
divergent return shapes normalise, that every lane's declared NATURE agrees with what the lane
actually ships, and that no floor is uncited.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from agri_data_service.foundation.parquet.paths import validate_layer_slug
from agri_data_service.pipeline.parquet.gap_fill import GapFillContractError, lane_window
from agri_data_service.pipeline.parquet.lane_registry import (
    LANE_REGISTRATIONS,
    LANE_REGISTRY,
    MAX_SOIL_SURVEY_POLYGON_KEYS,
    LaneRegistration,
    LaneRegistryError,
    LaneRunResult,
    normalise_export_outcome,
    registered_lane_slugs,
    resolve_lanes,
)
from agri_data_service.pipeline.parquet.objectstore import (
    AbsenceWriteReceipt,
    EmptyPartitionError,
    ObjectStore,
    ParquetWriteReceipt,
)
from agri_data_service.warehouse.parquet.schema import get_stream_schema
from tests.parquet.test_objectstore_writer import RecordingBackend

if TYPE_CHECKING:
    from collections.abc import Sequence

EXPECTED_LANE_COUNT = 13
AUGUST_SIXTH = date(2026, 8, 6)

# RUNBOOK section 0.26.6's table -- the wave-2 join's own record of what landed -- plus `calendar`,
# the conformed date dimension, which is a registered stream with no source system.
EXPECTED_SLUGS = frozenset(
    {
        "burn-severity",
        "calendar",
        "drought",
        "evacuation-zones",
        "fire-detections",
        "fire-perimeters",
        "sensors",
        "signal",
        "soil-survey",
        "vegetation",
        "water-gauges",
        "watersheds",
        "weather-observations",
    }
)

# Reference data with a VERSION and no time axis: the partition day is a version stamp, so these
# lanes are watermark-driven rather than schedule-driven and are never forecastable.
STATIC_LOOKUP_SLUGS = frozenset({"calendar", "evacuation-zones", "soil-survey", "watersheds"})

# Discrete dated publications: each release IS a dated fact, so the partition day is its own
# valid/issue date rather than a day anyone observed.
RELEASE_SERIES_SLUGS = frozenset({"burn-severity", "drought"})

# The five lanes that ship a `method/monte_carlo/<module>.py`, mapped to that module's stem. Only
# `vegetation` diverges from its slug: `vegetation_ndvi_forecast.py` predates `layer-lanes.md` §3,
# which says bring it into conformance rather than writing a second forecaster beside it.
EXPECTED_FORECAST_MODULES = {
    "fire-detections": "fire_detections",
    "sensors": "sensors",
    "signal": "signal",
    "vegetation": "vegetation_ndvi_forecast",
    "water-gauges": "water_gauges",
}

_SOURCE_ROOT = Path(__file__).resolve().parents[2] / "src" / "agri_data_service"
_LANE_MODULE_DIRECTORY = _SOURCE_ROOT / "pipeline" / "lanes"
_MONTE_CARLO_DIRECTORY = _SOURCE_ROOT / "method" / "monte_carlo"

# Lane modules that deliberately carry NO gap-fill registration, each with the reason it is exempt.
# DEFAULT-DENY, and for the reason RUNBOOK section 0.26.7 gives: a stream added later is policed the
# day it lands, with nothing to remember to register. An unregistered lane is a stream nothing ever
# schedules, and that failure is invisible -- every test still passes and the bucket just stays empty.
#
# `drought` was the sole entry here and is now REGISTERED. Both blockers this dict recorded were
# cleared on 2026-08-22: its floor was measured against production (min(valid_date)=2022-08-09,
# 209 releases) rather than inferred, and `cadence_days` was added to the registration so a weekly
# source no longer marks six days in seven as a governed absence. The exemption mechanism stays --
# it is the default-deny gate, not a list that happened to have one member.
UNREGISTERED_LANE_MODULES: dict[str, str] = {}


class _Result:
    def __init__(self, rows: Sequence[dict[str, object]]) -> None:
        self._rows = rows

    def mappings(self) -> Sequence[dict[str, object]]:
        return self._rows


class ScriptedSession:
    """Answers each statement from a queue, recording the parameters it was bound with."""

    def __init__(self, answers: Sequence[Sequence[dict[str, object]]]) -> None:
        self.answers = list(answers)
        self.params: list[dict[str, Any] | None] = []

    async def execute(self, _statement: Any, params: dict[str, Any] | None = None) -> _Result:
        self.params.append(params)
        return _Result(self.answers.pop(0) if self.answers else [])


def receipt(*, rows: int, size: int, part_index: int = 0) -> ParquetWriteReceipt:
    """One part-file receipt, shaped as `ObjectStore.write_partition` returns it."""
    return ParquetWriteReceipt(
        key=f"layer=signal/kind=observed/year=2026/month=08/day=06/part-{part_index}.parquet",
        relative_path=f"layer=signal/kind=observed/year=2026/month=08/day=06/part-{part_index}.parquet",
        stream="signal",
        kind="observed",
        day=AUGUST_SIXTH,
        row_count=rows,
        byte_count=size,
        sha256="0" * 64,
    )


def absence_receipt(*, size: int = 128) -> AbsenceWriteReceipt:
    """One governed-absence receipt, shaped as `ObjectStore.write_absence` returns it."""
    return AbsenceWriteReceipt(
        key="layer=signal/kind=observed/year=2026/month=08/day=06/absent.json",
        relative_path="layer=signal/kind=observed/year=2026/month=08/day=06/absent.json",
        kind="observed",
        day=AUGUST_SIXTH,
        byte_count=size,
        sha256="0" * 64,
    )


def test_exactly_the_thirteen_streams_are_registered_and_interventions_is_not() -> None:
    """RUNBOOK section 0.26.1 keeps `interventions` in Postgres; an entry here would move it."""
    assert set(registered_lane_slugs()) == EXPECTED_SLUGS
    assert len(LANE_REGISTRATIONS) == EXPECTED_LANE_COUNT
    assert "interventions" not in LANE_REGISTRY


def test_every_lane_module_is_either_registered_or_explicitly_declared_unregistered() -> None:
    """A lane nothing registers is a stream nothing schedules -- and that failure is otherwise silent.

    `drought` proved the point the day it landed: it appeared in `pipeline/lanes/` from a concurrent
    agent, is a first-class stream, and would have had no gap-fill entry with nothing failing.
    """
    modules = {
        path.stem.replace("_", "-") for path in _LANE_MODULE_DIRECTORY.glob("*.py") if path.stem != "__init__"
    }
    registered = set(registered_lane_slugs())
    exempt = set(UNREGISTERED_LANE_MODULES)

    assert exempt <= modules, f"an exemption names a lane module that no longer exists: {sorted(exempt - modules)}"
    assert not (registered & exempt), "a lane cannot be both registered and exempt"
    unaccounted = sorted(modules - registered - exempt)
    assert not unaccounted, (
        f"lane module(s) {unaccounted} have no gap-fill registration and no declared exemption. Either add a "
        "LaneRegistration in pipeline/parquet/lane_registry.py -- with a CITED history floor -- or record why "
        "the lane is exempt in UNREGISTERED_LANE_MODULES above. A stream with neither is never gap-filled and "
        "nothing else will say so."
    )


def test_every_registered_slug_has_a_schema_get_stream_schema_can_autoload() -> None:
    """A slug the writer cannot resolve a schema for would fail on its first real write, not here."""
    for registration in LANE_REGISTRATIONS:
        schema = get_stream_schema(registration.slug)
        assert schema.name == registration.slug
        assert schema.sort_columns
        assert validate_layer_slug(registration.slug) == registration.slug


def test_the_registry_is_keyed_by_slug_and_ordered_deterministically() -> None:
    assert tuple(LANE_REGISTRY) == registered_lane_slugs()
    assert registered_lane_slugs() == tuple(sorted(registered_lane_slugs()))


def test_every_floor_is_cited_and_every_lag_is_declared() -> None:
    """An uncited floor is a guess that reads as a measurement; the citation is the guard."""
    for registration in LANE_REGISTRATIONS:
        assert registration.floor_basis.strip(), registration.slug
        assert registration.publication_lag_days >= 0, registration.slug
        assert registration.history_floor <= date(2026, 8, 22), registration.slug


def test_the_one_guessed_floor_says_so_out_loud() -> None:
    """RUNBOOK section 0.26.8: this lane's producer has no contract, and the basis must not pretend otherwise."""
    weather = LANE_REGISTRY["weather-observations"]

    assert "FALLBACK" in weather.floor_basis
    assert "0.26.8" in weather.floor_basis


def test_signal_uses_the_larger_of_the_two_measured_publication_lags() -> None:
    """At NASA's 5-day lag the four newest days read as missing while ERA5-Land has not published them."""
    era5_land_lag = 9

    assert LANE_REGISTRY["signal"].publication_lag_days == era5_land_lag
    assert LANE_REGISTRY["signal"].history_floor == date(2022, 4, 30)


def test_water_gauges_does_not_inherit_the_borrowed_2022_floor() -> None:
    """`USGS_DAILY_VALUES_EARLIEST` is the vegetation layer's floor, not this lane's measured record."""
    water_gauges = LANE_REGISTRY["water-gauges"]

    assert water_gauges.history_floor == date(2026, 5, 24)
    assert water_gauges.history_floor.year != LANE_REGISTRY["vegetation"].history_floor.year


def test_every_lane_declares_the_nature_its_shape_actually_has() -> None:
    """The nature says what the partition day MEANS, and getting it wrong is what caused daily churn."""
    for registration in LANE_REGISTRATIONS:
        if registration.slug in STATIC_LOOKUP_SLUGS:
            assert registration.nature == "static_lookup", registration.slug
        elif registration.slug in RELEASE_SERIES_SLUGS:
            assert registration.nature == "release_series", registration.slug
        else:
            assert registration.nature == "daily_series", registration.slug


def test_a_series_lane_gets_a_window_and_a_static_lane_is_refused_one() -> None:
    """`current_snapshot` collapsed to "the newest settled day"; that is exactly the churn being removed."""
    today = date(2026, 8, 22)

    for registration in LANE_REGISTRATIONS:
        if registration.nature == "static_lookup":
            with pytest.raises(GapFillContractError, match="has no settled window"):
                lane_window(registration, today=today)
            continue
        window = lane_window(registration, today=today)
        assert window is not None, registration.slug
        first_day, _last_day = window
        assert first_day == registration.history_floor, registration.slug


def test_every_static_lane_declares_a_source_watermark_and_no_series_lane_does() -> None:
    """A static lane with no watermark is schedule-driven again; a series lane with one has two clocks."""
    for registration in LANE_REGISTRATIONS:
        if registration.nature == "static_lookup":
            assert registration.watermark is not None, registration.slug
            assert registration.publication_lag_days == 0, registration.slug
        else:
            assert registration.watermark is None, registration.slug


def test_forecastability_agrees_with_the_forecasters_that_actually_ship() -> None:
    """`layer-lanes.md` §2 makes claiming a horizon and shipping a forecaster the SAME fact.

    A lane claiming forecastable with no module would advertise a projection nobody can produce; a
    module with no claim is dead code the driver will never reach. Both are caught here, in both
    directions, against the filesystem rather than against another declaration.
    """
    shipped = {path.stem for path in _MONTE_CARLO_DIRECTORY.glob("*.py") if path.stem != "__init__"}

    assert shipped == set(EXPECTED_FORECAST_MODULES.values()), (
        "method/monte_carlo/ no longer holds exactly the modules the registry names. Add or remove the "
        "matching `forecast_module=` on the lane's LaneRegistration in the same change."
    )
    for registration in LANE_REGISTRATIONS:
        expected = EXPECTED_FORECAST_MODULES.get(registration.slug)
        assert registration.forecast_module == expected, registration.slug
        assert registration.forecastable is (expected is not None), registration.slug
        if expected is not None:
            assert (_MONTE_CARLO_DIRECTORY / f"{expected}.py").is_file(), registration.slug


def test_no_static_lookup_lane_is_forecastable() -> None:
    """Reference data has no time axis, so there is nothing a forecast of it could mean."""
    for registration in LANE_REGISTRATIONS:
        if registration.nature == "static_lookup":
            assert not registration.forecastable, registration.slug
            assert registration.forecast_module is None, registration.slug


def test_a_lane_whose_floor_has_not_settled_yet_has_no_window() -> None:
    """A floor later than today-minus-lag is not a gap; it is a lane that has nothing settled."""
    unsettled = LaneRegistration(
        slug="signal",
        adapter=LANE_REGISTRY["signal"].adapter,
        history_floor=date(2026, 8, 20),
        publication_lag_days=9,
        nature="daily_series",
        floor_basis="test fixture",
    )

    assert lane_window(unsettled, today=date(2026, 8, 22)) is None


def test_a_registration_must_cite_its_floor_and_declare_a_non_negative_lag() -> None:
    with pytest.raises(LaneRegistryError, match="cite where its history floor"):
        LaneRegistration(
            slug="signal",
            adapter=LANE_REGISTRY["signal"].adapter,
            history_floor=date(2022, 4, 30),
            publication_lag_days=9,
            nature="daily_series",
            floor_basis="   ",
        )
    with pytest.raises(LaneRegistryError, match="negative publication lag"):
        LaneRegistration(
            slug="signal",
            adapter=LANE_REGISTRY["signal"].adapter,
            history_floor=date(2022, 4, 30),
            publication_lag_days=-1,
            nature="daily_series",
            floor_basis="test fixture",
        )


def test_a_static_lookup_may_not_claim_a_forecaster() -> None:
    """The nature is the CEILING on forecastability, enforced at construction rather than by review."""
    with pytest.raises(LaneRegistryError, match="no time axis to project along"):
        LaneRegistration(
            slug="watersheds",
            adapter=LANE_REGISTRY["watersheds"].adapter,
            history_floor=date(2026, 8, 7),
            publication_lag_days=0,
            nature="static_lookup",
            watermark=LANE_REGISTRY["watersheds"].watermark,
            forecast_module="watersheds",
            floor_basis="test fixture",
        )


def test_only_a_release_series_may_declare_a_cadence_above_one_day() -> None:
    """A daily series that skips days is either not daily or not a series."""
    with pytest.raises(LaneRegistryError, match="only a release_series has a publication rhythm"):
        LaneRegistration(
            slug="signal",
            adapter=LANE_REGISTRY["signal"].adapter,
            history_floor=date(2022, 4, 30),
            publication_lag_days=9,
            nature="daily_series",
            cadence_days=7,
            floor_basis="test fixture",
        )


def test_a_static_lookup_without_a_watermark_is_refused() -> None:
    """Without one it is schedule-driven again, re-snapshotting whatever day the cron ran on."""
    with pytest.raises(LaneRegistryError, match="declares no source watermark"):
        LaneRegistration(
            slug="watersheds",
            adapter=LANE_REGISTRY["watersheds"].adapter,
            history_floor=date(2026, 8, 7),
            publication_lag_days=0,
            nature="static_lookup",
            floor_basis="test fixture",
        )


def test_a_series_lane_carrying_a_watermark_is_refused() -> None:
    """Two clocks on one lane disagree, and the disagreement is invisible until a day goes missing."""
    with pytest.raises(LaneRegistryError, match="declares a source watermark"):
        LaneRegistration(
            slug="signal",
            adapter=LANE_REGISTRY["signal"].adapter,
            history_floor=date(2022, 4, 30),
            publication_lag_days=9,
            nature="daily_series",
            watermark=LANE_REGISTRY["watersheds"].watermark,
            floor_basis="test fixture",
        )


def test_a_static_lookup_may_not_declare_a_publication_lag() -> None:
    """A version stamp is not settled by waiting; subtracting a lag would date it before the change."""
    with pytest.raises(LaneRegistryError, match="publication lag"):
        LaneRegistration(
            slug="watersheds",
            adapter=LANE_REGISTRY["watersheds"].adapter,
            history_floor=date(2026, 8, 7),
            publication_lag_days=1,
            nature="static_lookup",
            watermark=LANE_REGISTRY["watersheds"].watermark,
            floor_basis="test fixture",
        )


def test_the_calendar_dimension_covers_every_lane_floor_and_has_no_source_system() -> None:
    """It is PURE COMPUTATION, so its floor is derived from the lanes rather than measured anywhere."""
    calendar = LANE_REGISTRY["calendar"]
    database_backed_floors = [
        registration.history_floor for registration in LANE_REGISTRATIONS if registration.slug != "calendar"
    ]

    assert calendar.nature == "static_lookup"
    assert not calendar.forecastable
    assert calendar.history_floor == min(database_backed_floors)
    assert "no source system" in calendar.floor_basis


def test_the_four_return_shapes_fold_into_one_result() -> None:
    """The eleven exporters landed concurrently with divergent signatures; this is where that ends."""
    single = normalise_export_outcome(receipt(rows=7, size=100))
    spilled = normalise_export_outcome((receipt(rows=7, size=100), receipt(rows=3, size=40, part_index=1)))
    absent = normalise_export_outcome(absence_receipt(size=128))

    assert single == LaneRunResult(part_count=1, row_count=7, byte_count=100, absence_recorded=False)
    assert spilled == LaneRunResult(part_count=2, row_count=10, byte_count=140, absence_recorded=False)
    assert absent == LaneRunResult(part_count=0, row_count=0, byte_count=128, absence_recorded=True)


def test_an_export_that_produced_no_object_is_refused_not_folded() -> None:
    """Zero parts and no absence marker is a gap; reporting it as a completed export would hide one."""
    with pytest.raises(LaneRegistryError, match="neither a part file nor an absence marker"):
        normalise_export_outcome(())


def test_resolve_lanes_returns_registry_order_and_names_what_is_unknown() -> None:
    assert resolve_lanes(["water-gauges", "signal"]) == (
        LANE_REGISTRY["signal"],
        LANE_REGISTRY["water-gauges"],
    )
    with pytest.raises(LaneRegistryError, match="interventions"):
        resolve_lanes(["signal", "interventions"])


@pytest.mark.asyncio
async def test_a_sensors_day_no_station_published_on_becomes_a_governed_absence_not_a_broken_lane() -> None:
    """The station list is day-scoped, so an empty one is an empty DAY, which belongs behind a marker."""
    session = ScriptedSession([[]])
    store = ObjectStore(RecordingBackend())

    with pytest.raises(EmptyPartitionError, match="qualifying station reading"):
        await LANE_REGISTRY["sensors"].adapter(
            session,  # type: ignore[arg-type]
            store,
            day=AUGUST_SIXTH,
            run_id="test-run",
        )

    assert session.params == [{"observed_day": AUGUST_SIXTH}]


@pytest.mark.asyncio
async def test_an_empty_analysis_grid_is_a_broken_warehouse_not_an_empty_day() -> None:
    """`agri.spatial_cell` is not day-scoped: empty means no grid at all, which must never read as absence."""
    session = ScriptedSession([[]])
    store = ObjectStore(RecordingBackend())

    with pytest.raises(LaneRegistryError, match="no analysis grid"):
        await LANE_REGISTRY["signal"].adapter(
            session,  # type: ignore[arg-type]
            store,
            day=AUGUST_SIXTH,
            run_id="test-run",
        )


@pytest.mark.asyncio
async def test_an_unresolvable_layer_id_fails_closed_rather_than_exporting_the_wrong_population() -> None:
    session = ScriptedSession([[]])
    store = ObjectStore(RecordingBackend())

    with pytest.raises(LaneRegistryError, match="holds 0 rows named 'fire-detections'"):
        await LANE_REGISTRY["fire-detections"].adapter(
            session,  # type: ignore[arg-type]
            store,
            day=AUGUST_SIXTH,
            run_id="test-run",
        )


@pytest.mark.asyncio
async def test_a_soil_survey_key_list_that_hit_the_ceiling_is_refused_not_truncated() -> None:
    """A truncated release reads back as a complete one -- the exact wrong-but-plausible output to refuse."""
    over_budget = [{"mupolygonkey": str(index)} for index in range(MAX_SOIL_SURVEY_POLYGON_KEYS + 1)]
    session = ScriptedSession([over_budget])
    store = ObjectStore(RecordingBackend())

    with pytest.raises(LaneRegistryError, match="truncated key list"):
        await LANE_REGISTRY["soil-survey"].adapter(
            session,  # type: ignore[arg-type]
            store,
            day=AUGUST_SIXTH,
            run_id="test-run",
        )

    assert session.params == [{"key_ceiling": MAX_SOIL_SURVEY_POLYGON_KEYS + 1}]


@pytest.mark.asyncio
async def test_a_watermark_query_answer_becomes_a_cited_version_day() -> None:
    """The version stamp is the source's own change time in UTC, and the basis names what produced it."""
    changed_at = datetime(2026, 8, 7, 23, 30, tzinfo=UTC)
    session = ScriptedSession(
        [
            [
                {
                    "feature_updated_at": changed_at,
                    "feature_created_at": datetime(2026, 8, 7, 9, 0, tzinfo=UTC),
                    "watermark_at": changed_at,
                    "row_count": 9396,
                }
            ]
        ]
    )
    resolver = LANE_REGISTRY["watersheds"].watermark
    assert resolver is not None

    watermark = await resolver(session, ObjectStore(RecordingBackend()), today=date(2026, 8, 22))  # type: ignore[arg-type]

    assert watermark.day == date(2026, 8, 7)
    assert "feature_updated_at=2026-08-07T23:30:00+00:00" in watermark.basis
    assert "over 9396 published rows" in watermark.basis


@pytest.mark.asyncio
async def test_a_source_with_no_published_rows_yields_a_null_watermark_not_a_fabricated_day() -> None:
    """count(*)=0 makes every max() NULL; that is an empty population, never "changed today"."""
    session = ScriptedSession(
        [[{"source_vintage_at": None, "feature_created_at": None, "watermark_at": None, "row_count": 0}]]
    )
    resolver = LANE_REGISTRY["soil-survey"].watermark
    assert resolver is not None

    watermark = await resolver(session, ObjectStore(RecordingBackend()), today=date(2026, 8, 22))  # type: ignore[arg-type]

    assert watermark.day is None
    assert "no published rows" in watermark.basis


@pytest.mark.asyncio
async def test_a_timezone_naive_watermark_is_refused_rather_than_assumed_to_be_utc() -> None:
    """Assuming a zone for a version stamp would silently shift it by up to a day."""
    session = ScriptedSession(
        [
            [
                {
                    "feature_updated_at": datetime(2026, 8, 7, 23, 30),  # noqa: DTZ001 - the defect under test
                    "feature_created_at": None,
                    "geometry_version_valid_from": None,
                    "watermark_at": datetime(2026, 8, 7, 23, 30),  # noqa: DTZ001 - the defect under test
                    "row_count": 12,
                }
            ]
        ]
    )
    resolver = LANE_REGISTRY["evacuation-zones"].watermark
    assert resolver is not None

    with pytest.raises(LaneRegistryError, match="timezone-naive"):
        await resolver(session, ObjectStore(RecordingBackend()), today=date(2026, 8, 22))  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_an_unwarmed_soil_survey_is_an_empty_day_behind_a_marker() -> None:
    """This lane is warmed lazily by viewport reads, so "nothing published yet" is honest, not broken."""
    session = ScriptedSession([[]])
    store = ObjectStore(RecordingBackend())

    with pytest.raises(EmptyPartitionError, match="published SSURGO delineation"):
        await LANE_REGISTRY["soil-survey"].adapter(
            session,  # type: ignore[arg-type]
            store,
            day=AUGUST_SIXTH,
            run_id="test-run",
        )

"""The lane registry: eleven streams, four return shapes folded into one, and floors that are cited.

The exporters themselves are exercised by their own lane tests; what is pinned here is the
integration surface -- that every registered slug has a schema the writer can autoload, that the
divergent return shapes normalise, that the three current-state lanes refuse a historical window,
and that no floor is uncited.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from agri_data_service.foundation.parquet.paths import validate_layer_slug
from agri_data_service.pipeline.parquet.gap_fill import lane_window
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

EXPECTED_LANE_COUNT = 12
AUGUST_SIXTH = date(2026, 8, 6)

# RUNBOOK section 0.26.6's table, which is the wave-2 join's own record of what landed.
EXPECTED_SLUGS = frozenset(
    {
        "burn-severity",
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

# The three whose export broadcasts the caller's day onto every row and applies no date predicate.
CURRENT_SNAPSHOT_SLUGS = frozenset({"evacuation-zones", "soil-survey", "watersheds"})

_LANE_MODULE_DIRECTORY = Path(__file__).resolve().parents[2] / "src" / "agri_data_service" / "pipeline" / "lanes"

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


def test_exactly_the_twelve_streams_are_registered_and_interventions_is_not() -> None:
    """RUNBOOK section 0.26.1 keeps `interventions` in Postgres; a twelfth entry here would move it."""
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


def test_the_current_state_lanes_refuse_a_historical_window() -> None:
    """Their export broadcasts the day onto every row, so a past day would be today's state, fabricated."""
    today = date(2026, 8, 22)

    for registration in LANE_REGISTRATIONS:
        window = lane_window(registration, today=today)
        assert window is not None, registration.slug
        first_day, last_day = window
        if registration.slug in CURRENT_SNAPSHOT_SLUGS:
            assert registration.window_kind == "current_snapshot"
            assert first_day == last_day, registration.slug
        else:
            assert registration.window_kind == "daily_series"
            assert first_day == registration.history_floor, registration.slug


def test_a_lane_whose_floor_has_not_settled_yet_has_no_window() -> None:
    """A floor later than today-minus-lag is not a gap; it is a lane that has nothing settled."""
    unsettled = LaneRegistration(
        slug="signal",
        adapter=LANE_REGISTRY["signal"].adapter,
        history_floor=date(2026, 8, 20),
        publication_lag_days=9,
        window_kind="daily_series",
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
            window_kind="daily_series",
            floor_basis="   ",
        )
    with pytest.raises(LaneRegistryError, match="negative publication lag"):
        LaneRegistration(
            slug="signal",
            adapter=LANE_REGISTRY["signal"].adapter,
            history_floor=date(2022, 4, 30),
            publication_lag_days=-1,
            window_kind="daily_series",
            floor_basis="test fixture",
        )


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

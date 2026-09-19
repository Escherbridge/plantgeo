"""The leakage gate, the stratum split, the lattice join and the mandatory seasonal features.

The warehouse fixtures here are shared with `test_fire_risk_daily.py`, which imports them from this
module: one seeded lane set, two suites, no second definition to drift.
"""

from __future__ import annotations

import struct
from datetime import UTC, date, datetime, timedelta

import duckdb
import polars as pl
import pyarrow as pa
import pytest

from plantgeo_ml_service.foundation.parquet_markers import GovernedAbsence
from plantgeo_ml_service.pipeline.duckdb_session import DuckDbSession
from plantgeo_ml_service.pipeline.fire_risk_features import (
    CLOSED_FOREST_NDVI_FLOOR,
    CLOSED_FOREST_STRATUM,
    FIRE_RISK_FEATURE_NAMES,
    OPEN_CANOPY_STRATUM,
    UNKNOWN_STRATUM,
    FireRiskFeatureError,
    binding_frontier,
    build_fire_risk_features,
    cyclical_day_of_year,
    photoperiod_seconds,
    wkb_envelope,
)
from plantgeo_ml_service.pipeline.object_store import InMemoryObjectStoreBackend, ObjectStore
from plantgeo_ml_service.pipeline.observed_reader import ObservedReader
from plantgeo_ml_service.warehouse.lanes import lane_contract
from plantgeo_ml_service.warehouse.streams import (
    BURN_SEVERITY_SCHEMA,
    DROUGHT_SCHEMA,
    FIRE_DETECTIONS_SCHEMA,
    SIGNAL_SCHEMA,
    VEGETATION_SCHEMA,
    ParquetStreamSchema,
)

ISSUED_ON = date(2026, 9, 19)
MOMENT = datetime(2026, 9, 19, 12, 0, 0, tzinfo=UTC)

# One 0.005-degree fire cell and the 0.25-degree analysis cell it bins into. The analysis lattice has
# its centres at odd multiples of 0.125 degrees, so -120.125 / 46.125 is one centre exactly.
FIRE_CELL = (-120.125, 46.125)
ANALYSIS_CELL = (-120.125, 46.125)

DRIVER_VALUES = {
    "vapor_pressure_deficit": 2.4,
    "air_temperature_mean": 28.0,
    "relative_humidity": 22.0,
    "wind_speed": 4.5,
    "precipitation": 0.0,
    "soil_water_content_layer_1": 0.11,
    "soil_water_content_layer_3": 0.19,
    "soil_temperature_level_1": 24.0,
}


class RecordingReader:
    """An `ObservedReader` stand-in that serves seeded lane frames and records every window asked for.

    The lanes hold days PAST each producer's frontier on purpose: a builder that read one would score
    better than the live lane ever can, and this is what notices.
    """

    def __init__(self, frames: dict[str, pl.DataFrame]) -> None:
        self.frames = frames
        self.windows: list[tuple[str, date, date]] = []

    def read_lane_window(
        self,
        layer: str,
        zoom: int,  # noqa: ARG002 - part of the ObservedReader protocol, unused by this fake
        first_day: date,
        last_day: date,
        *,
        as_of: date,  # noqa: ARG002 - part of the ObservedReader protocol, unused by this fake
    ) -> pl.DataFrame:
        """Return the seeded rows inside the window, after recording exactly what was requested."""
        self.windows.append((layer, first_day, last_day))
        frame = self.frames[layer]
        day_column = "valid_date" if layer == "drought" else "observed_day"
        return frame.filter(pl.col(day_column).is_between(first_day, last_day))

    def last_day_for(self, layer: str) -> date:
        """Return the newest day this reader was asked for on one lane."""
        return max(window[2] for window in self.windows if window[0] == layer)


def fire_detection_rows(days: list[date], *, cell: tuple[float, float] = FIRE_CELL) -> pl.DataFrame:
    """Return a fire-detections frame with the pinned schema and one cell-day per requested day."""
    return _framed(
        FIRE_DETECTIONS_SCHEMA,
        {
            "cell_longitude": [cell[0]] * len(days),
            "cell_latitude": [cell[1]] * len(days),
            "observed_day": days,
            "detection_count": [2] * len(days),
            "frp_sum": [11.5] * len(days),
            "frp_observation_count": [2] * len(days),
            "high_confidence_detection_count": [1] * len(days),
            "newest_observed_at": [MOMENT] * len(days),
        },
    )


def vegetation_rows(days: list[date], values: list[float]) -> pl.DataFrame:
    """Return a vegetation frame carrying one NDVI value per day on the analysis cell."""
    return _framed(
        VEGETATION_SCHEMA,
        {
            "cell_id": ["cell-1"] * len(days),
            "grid_name": ["sentinel2-ndvi-0p25deg"] * len(days),
            "metric_name": ["ndvi"] * len(days),
            "metric_unit": ["index"] * len(days),
            "observed_day": days,
            "metric_value": values,
            "observation_checksum": ["a" * 64] * len(days),
            "data_available_at": [MOMENT] * len(days),
            "release_count": [1] * len(days),
            "allowed_client_exposure": [True] * len(days),
            "cell_longitude": [ANALYSIS_CELL[0]] * len(days),
            "cell_latitude": [ANALYSIS_CELL[1]] * len(days),
        },
    )


def signal_rows(days: list[date]) -> pl.DataFrame:
    """Return a signal frame carrying every named driver on the analysis cell, once per day."""
    names = sorted(DRIVER_VALUES)
    return _framed(
        SIGNAL_SCHEMA,
        {
            "support_key": ["support"] * (len(days) * len(names)),
            "signal_name": [name for _day in days for name in names],
            "normalized_unit": ["unit"] * (len(days) * len(names)),
            "cell_id": ["cell-1"] * (len(days) * len(names)),
            "observed_day": [day for day in days for _name in names],
            "normalized_value": [DRIVER_VALUES[name] for _day in days for name in names],
            "observation_count": [1] * (len(days) * len(names)),
            "newest_observed_at": [MOMENT] * (len(days) * len(names)),
            "coverage_fraction": [1.0] * (len(days) * len(names)),
            "allowed_client_exposure": [True] * (len(days) * len(names)),
            "cell_longitude": [ANALYSIS_CELL[0]] * (len(days) * len(names)),
            "cell_latitude": [ANALYSIS_CELL[1]] * (len(days) * len(names)),
        },
    )


def drought_rows(days: list[date], categories: list[int]) -> pl.DataFrame:
    """Return a drought frame with one release row per day."""
    return _framed(
        DROUGHT_SCHEMA,
        {
            "area_id": [f"area-{index}" for index in range(len(days))],
            "valid_date": days,
            "dm_category": categories,
            "source_url": ["https://droughtmonitor.unl.edu"] * len(days),
            "ingested_at": [MOMENT] * len(days),
            "geom": [polygon_wkb(-121.0, 45.0, -119.0, 47.0)] * len(days),
        },
    )


def burn_severity_rows(days: list[date], perimeters: list[bytes]) -> pl.DataFrame:
    """Return a burn-severity frame with one perimeter per day."""
    count = len(days)
    return _framed(
        BURN_SEVERITY_SCHEMA,
        {
            "feature_id": [f"feature-{index}" for index in range(count)],
            "fire_id": [f"fire-{index}" for index in range(count)],
            "natural_key": [f"natural-{index}" for index in range(count)],
            "release_identifier": ["release-1"] * count,
            "mapping_revision": ["1"] * count,
            "fire_year": [day.year for day in days],
            "ignition_date": days,
            "observed_day": days,
            "data_available_at": [MOMENT] * count,
            "fire_name": ["Test Fire"] * count,
            "fire_type": ["Wildfire"] * count,
            "assessment_type": ["Initial"] * count,
            "acres": [1200.0] * count,
            "severity_class": [None] * count,
            "dnbr_offset": [None] * count,
            "dnbr_standard_deviation": [None] * count,
            "nodata_threshold": [None] * count,
            "greenness_threshold": [None] * count,
            "low_threshold": [None] * count,
            "moderate_threshold": [None] * count,
            "high_threshold": [None] * count,
            "allowed_client_exposure": [True] * count,
            "geom": perimeters,
        },
    )


def polygon_wkb(west: float, south: float, east: float, north: float) -> bytes:
    """Return a little-endian WKB polygon of one closed rectangular ring."""
    corners = ((west, south), (east, south), (east, north), (west, north), (west, south))
    body = struct.pack("<BII", 1, 3, 1) + struct.pack("<I", len(corners))
    for longitude, latitude in corners:
        body += struct.pack("<dd", longitude, latitude)
    return body


def seeded_reader(
    *,
    ndvi_values: list[float] | None = None,
    leak_ndvi: float | None = None,
    perimeter: bytes | None = None,
) -> RecordingReader:
    """Return a reader seeded with every lane the builder reads, plus days past three frontiers."""
    settled = {layer: lane_contract(layer).settled_through(ISSUED_ON) for layer in _LANES}
    leak_days = {layer: [day + timedelta(days=offset) for offset in (1, 2)] for layer, day in settled.items()}
    fire_days = _trailing(settled["fire-detections"], 6) + leak_days["fire-detections"]
    vegetation_days = _trailing(settled["vegetation"], 4)
    values = ndvi_values if ndvi_values is not None else [0.22, 0.24, 0.21, 0.23]
    vegetation = vegetation_rows(vegetation_days, values)
    if leak_ndvi is not None:
        vegetation = pl.concat([vegetation, vegetation_rows(leak_days["vegetation"], [leak_ndvi, leak_ndvi])])
    perimeters = [perimeter] if perimeter is not None else []
    burn_days = [settled["burn-severity"] - timedelta(days=30)] if perimeter is not None else []
    return RecordingReader(
        {
            "fire-detections": fire_detection_rows(fire_days),
            "vegetation": vegetation,
            "signal": signal_rows(_trailing(settled["signal"], 5)),
            "drought": drought_rows([settled["drought"]], [3]),
            "burn-severity": burn_severity_rows(burn_days, perimeters),
        }
    )


def absent_warehouse_reader() -> ObservedReader:
    """Return a REAL reader over an in-memory bucket whose every window day is a governed absence."""
    store = ObjectStore(backend=InMemoryObjectStoreBackend())
    for layer, span in _LANE_SPANS.items():
        last_day = lane_contract(layer).settled_through(ISSUED_ON)
        for offset in range(span):
            day = last_day - timedelta(days=offset)
            store.write_absence_marker(
                GovernedAbsence(reason="source_empty", upstream_response="HTTP 200", recorded_at=MOMENT, run_id="test"),
                layer=layer,
                kind="observed",
                zoom=13,
                day=day,
            )
    session = DuckDbSession(connection=duckdb.connect(), bucket_uri="s3://bucket")
    return ObservedReader(store=store, session=session)


def test_every_lane_window_ends_at_its_own_settled_frontier() -> None:
    """The frontier is the producer's clock, never today: a nine-day lane reads through day minus nine."""
    reader = seeded_reader()

    build_fire_risk_features(reader, run_date=ISSUED_ON, horizons=(1, 2))

    for layer in _LANES:
        assert reader.last_day_for(layer) == lane_contract(layer).settled_through(ISSUED_ON)
        assert reader.last_day_for(layer) < ISSUED_ON


def test_a_vegetation_day_past_the_frontier_never_reaches_the_stratum() -> None:
    """A leaked NDVI day would flip the stratum; the window that excludes it is what keeps it open."""
    leaked = seeded_reader(leak_ndvi=0.95)

    frame = build_fire_risk_features(leaked, run_date=ISSUED_ON, horizons=(1,)).frame

    assert frame.get_column("stratum").unique().to_list() == [OPEN_CANOPY_STRATUM]


def test_a_cell_the_vegetation_lane_does_not_cover_carries_no_stratum() -> None:
    """A cell without fuel state is refusable, not scoreable: it gets the unknown stratum, never a guess."""
    reader = seeded_reader()
    reader.frames["vegetation"] = reader.frames["vegetation"].head(0)

    frame = build_fire_risk_features(reader, run_date=ISSUED_ON, horizons=(1,)).frame

    assert frame.get_column("stratum").unique().to_list() == [UNKNOWN_STRATUM]


def test_greenness_above_the_declared_floor_is_closed_forest() -> None:
    reader = seeded_reader(ndvi_values=[CLOSED_FOREST_NDVI_FLOOR + 0.1] * 4)

    frame = build_fire_risk_features(reader, run_date=ISSUED_ON, horizons=(1,)).frame

    assert frame.get_column("stratum").unique().to_list() == [CLOSED_FOREST_STRATUM]


def test_seasonality_and_photoperiod_are_computed_for_every_valid_day() -> None:
    """Owner requirement: seasonality must influence the output, and photoperiod is computed, not read."""
    reader = seeded_reader()
    horizons = (1, 14)

    frame = build_fire_risk_features(reader, run_date=ISSUED_ON, horizons=horizons).frame

    # Horizons are counted from the BINDING FRONTIER, not from the calendar run date (M4).
    first_valid_day = binding_frontier(ISSUED_ON) + timedelta(days=1)
    first = frame.filter(pl.col("horizon_days") == 1).row(0, named=True)
    expected_sine, expected_cosine = cyclical_day_of_year(first_valid_day)
    assert first["day_of_year_sine"] == pytest.approx(expected_sine, abs=1e-9)
    assert first["day_of_year_cosine"] == pytest.approx(expected_cosine, abs=1e-9)
    assert first["photoperiod_seconds"] == pytest.approx(photoperiod_seconds(FIRE_CELL[1], first_valid_day), abs=1e-6)
    assert frame.get_column("photoperiod_seconds").n_unique() == len(horizons)


def test_horizon_one_never_precedes_the_binding_frontier() -> None:
    """M4: `issued_on` used to mean the calendar run date, which hid a full lag inside horizon 1."""
    built = build_fire_risk_features(seeded_reader(), run_date=ISSUED_ON, horizons=(1, 14))

    frontier = binding_frontier(ISSUED_ON)
    # The frontier is the SLOWEST input lane's settled day, so it is the minimum of all five.
    assert frontier == min(lane_contract(layer).settled_through(ISSUED_ON) for layer in _LANES)
    assert frontier < ISSUED_ON
    assert built.issued_on == frontier
    assert built.run_date == ISSUED_ON
    assert built.to_wire()["issued_on"] == frontier.isoformat()
    assert built.to_wire()["run_date"] == ISSUED_ON.isoformat()

    frame = built.frame
    assert set(frame.get_column("issued_on").unique().to_list()) == {frontier}
    earliest = min(frame.get_column("valid_day").to_list())
    assert earliest == frontier + timedelta(days=1)
    assert earliest > frontier


def test_the_same_inputs_produce_the_same_feature_checksums() -> None:
    """A checksum that moved without an input moving would make every scored row unattributable."""
    first = build_fire_risk_features(seeded_reader(), run_date=ISSUED_ON, horizons=(1, 2)).frame
    second = build_fire_risk_features(seeded_reader(), run_date=ISSUED_ON, horizons=(1, 2)).frame

    assert first.get_column("feature_checksum").to_list() == second.get_column("feature_checksum").to_list()
    assert first.get_column("feature_checksum").n_unique() == first.height


def test_a_horizon_at_or_before_the_issue_day_is_refused() -> None:
    with pytest.raises(FireRiskFeatureError):
        build_fire_risk_features(seeded_reader(), run_date=ISSUED_ON, horizons=(0, 1))


def test_a_repeated_horizon_is_refused() -> None:
    with pytest.raises(FireRiskFeatureError):
        build_fire_risk_features(seeded_reader(), run_date=ISSUED_ON, horizons=(1, 1))


def test_a_prior_burn_envelope_marks_the_cells_its_bounding_box_covers() -> None:
    """The envelope OVERSTATES a perimeter, so the feature is an upper bound and is named for one."""
    covering = seeded_reader(perimeter=polygon_wkb(-120.2, 46.0, -120.0, 46.2))
    distant = seeded_reader(perimeter=polygon_wkb(-110.2, 40.0, -110.0, 40.2))

    covered = build_fire_risk_features(covering, run_date=ISSUED_ON, horizons=(1,)).frame
    uncovered = build_fire_risk_features(distant, run_date=ISSUED_ON, horizons=(1,)).frame

    assert covered.get_column("prior_burn_envelope_overlap").to_list() == [1.0]
    assert uncovered.get_column("prior_burn_envelope_overlap").to_list() == [0.0]


def test_a_well_known_binary_polygon_yields_its_own_bounding_box() -> None:
    assert wkb_envelope(polygon_wkb(-120.5, 45.5, -119.5, 46.5)) == (-120.5, 45.5, -119.5, 46.5)


def test_a_governed_absent_warehouse_yields_a_typed_empty_plane() -> None:
    """Every lane day is a governed absence, so the real reader answers empty and the plane is empty."""
    frame = build_fire_risk_features(absent_warehouse_reader(), run_date=ISSUED_ON, horizons=(1,)).frame

    assert frame.height == 0
    assert set(FIRE_RISK_FEATURE_NAMES) <= set(frame.columns)


def test_the_lane_windows_are_carried_into_the_receipt() -> None:
    built = build_fire_risk_features(seeded_reader(), run_date=ISSUED_ON, horizons=(1,))

    wire = built.to_wire()
    lanes = {window["layer"]: window for window in wire["lane_windows"]}  # type: ignore[union-attr]  # a list of dicts
    assert set(lanes) == set(_LANES)
    assert lanes["signal"]["settled_through"] == lane_contract("signal").settled_through(ISSUED_ON).isoformat()


_LANES = ("fire-detections", "vegetation", "signal", "drought", "burn-severity")

#: How many days of each lane the ABSENT-warehouse fixture governs. One more than the builder's own
#: window, so a window that grew by a day fails here loudly rather than reading an ungoverned day.
_LANE_SPANS = {
    "fire-detections": 29,
    "vegetation": 41,
    "signal": 15,
    "drought": 22,
    "burn-severity": 1_096,
}


def _framed(schema: ParquetStreamSchema, columns: dict[str, object]) -> pl.DataFrame:
    """Return one lane frame carrying the stream's pinned Arrow schema, not a convenient subset."""
    table = pa.table(columns, schema=schema.arrow_schema)
    return pl.from_arrow(table)  # type: ignore[return-value]  # a Table always yields a DataFrame


def _trailing(last_day: date, count: int) -> list[date]:
    """Return `count` consecutive days ending at `last_day`."""
    return [last_day - timedelta(days=offset) for offset in reversed(range(count))]

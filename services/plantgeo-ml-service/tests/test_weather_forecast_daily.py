"""FR-12: the daily weather-forecast lane, replayed from the probe. No network, no live bucket.

The source here is a REPLAY binding over the captured probe response, so these cases exercise the
real parse, the real rung derivation and the real availability publication without HTTP.
"""

from __future__ import annotations

import io
import json
import math
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Final

import pyarrow.parquet as pq
import pytest

from plantgeo_ml_service.foundation.lattice import floor_coordinate
from plantgeo_ml_service.foundation.parquet_paths import (
    BASE_PARTITION_ZOOM,
    ZOOM_TIERS,
    availability_bootstrap_marker_key,
    availability_lane_root,
    availability_pointer_path,
    partition_path,
)
from plantgeo_ml_service.pipeline.availability_publisher import InMemoryPointerStore
from plantgeo_ml_service.pipeline.forecast_lane_bootstrap import (
    BOOTSTRAP_MARKER_SCHEMA_VERSION,
    bootstrap_marker_payload,
    read_lane_bootstrap_receipt,
)
from plantgeo_ml_service.pipeline.object_store import (
    JSON_CONTENT_TYPE,
    InMemoryObjectStoreBackend,
    ObjectStore,
    sha256_of,
)
from plantgeo_ml_service.pipeline.sources.open_meteo import (
    OPEN_METEO_FORECAST_RUN_MODEL,
    WEATHER_FORECAST_SOURCE_SLUG,
    WeatherForecastRun,
    forecast_run_url,
    parse_forecast_run,
)
from plantgeo_ml_service.pipeline.sources.protocol import GLOBAL_SOURCE_COVERAGE, SourceCoverage
from plantgeo_ml_service.pipeline.weather_forecast_daily import (
    MERGED_SUPPORT,
    NO_READINGS_REASON,
    ForecastCell,
    ForecastCellInventoryError,
    WeatherForecastDailyReceipt,
    WeatherForecastRunError,
    read_forecast_cells,
    run_weather_forecast_daily,
)
from plantgeo_ml_service.pipeline.weather_forecast_rows import _merged_support
from plantgeo_ml_service.warehouse.weather_forecast import (
    PUBLISHED_VARIABLES,
    UPSTREAM_VARIABLES,
    WEATHER_FORECAST_KIND,
    WEATHER_FORECAST_STREAM,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

FIXTURES: Final = Path(__file__).resolve().parent / "fixtures" / "open_meteo"
PAYLOAD: Final = (FIXTURES / "multi-location-run-20260918.json").read_bytes()

ISSUE_DATE: Final = date(2026, 9, 18)
RUN_INIT: Final = datetime(2026, 9, 18, tzinfo=UTC)
FETCHED_AT: Final = datetime(2026, 9, 18, 4, 0, tzinfo=UTC)
PUBLISHED_AT: Final = datetime(2026, 9, 18, 5, 0, tzinfo=UTC)

#: Where a deployment keeps the list of cells its weather-forecast lane is fetched for.
INVENTORY_KEY: Final = "ml/config/forecast-cells.json"

#: The two cells the captured response answers for, at the coordinates it was requested with.
NEIGHBOURING_CELLS: Final = (
    ForecastCell(cell_id="cell-0001", longitude=-120.25, latitude=46.25),
    ForecastCell(cell_id="cell-0002", longitude=-119.25, latitude=47.25),
)

#: Two cells inside ONE z5 and z0 lattice cell, so the coarse rungs actually merge rather than
#: passing two singletons through. `MERGED_PAYLOAD` is the captured response with both entries
#: snapped to ONE provider grid point, which is what two cells this close really receive: the probe
#: asked a degree apart, and the pairing rule tolerates the tie exactly because of this case.
MERGING_CELLS: Final = (
    ForecastCell(cell_id="cell-0001", longitude=-120.25, latitude=46.25),
    ForecastCell(cell_id="cell-0002", longitude=-120.24, latitude=46.24),
)


def _both_entries_on_one_grid_point() -> bytes:
    """Return the captured response with both locations answering from the first's grid point."""
    document = json.loads(PAYLOAD)
    document[1]["latitude"] = document[0]["latitude"]
    document[1]["longitude"] = document[0]["longitude"]
    return json.dumps(document).encode("utf-8")


MERGED_PAYLOAD: Final = _both_entries_on_one_grid_point()

HOURS_IN_FIXTURE: Final = 48

#: The rung the merge case reads back: coarse enough that both cells fall in one lattice cell.
MERGED_RUNG: Final = 5

#: A SHA-256 rendered as lowercase hex.
SHA256_HEX_LENGTH: Final = 64

DEGREES_PER_TURN: Final = 360.0


@dataclass(frozen=True, slots=True)
class ReplayForecastSource:
    """A `WeatherForecastSource` that answers one captured response, whatever it is asked for."""

    payload: bytes = PAYLOAD
    source_slug: str = WEATHER_FORECAST_SOURCE_SLUG
    coverage: SourceCoverage = GLOBAL_SOURCE_COVERAGE
    max_locations_per_request: int = 200

    async def fetch_run(
        self,
        *,
        model_init_time: datetime,
        coordinates: Sequence[tuple[float, float]],
        variables: Sequence[str] = UPSTREAM_VARIABLES,
        forecast_days: int = 2,
    ) -> WeatherForecastRun:
        """Parse the captured bytes against the coordinates the lane asked for."""
        request_url = forecast_run_url(coordinates, variables, model_init_time, forecast_days)
        return parse_forecast_run(
            self.payload,
            coordinates=coordinates,
            variables=variables,
            model=OPEN_METEO_FORECAST_RUN_MODEL,
            model_init_time=model_init_time,
            fetched_at=FETCHED_AT,
            request_url=request_url,
        )


def _store() -> ObjectStore:
    return ObjectStore(backend=InMemoryObjectStoreBackend())


def _run(
    store: ObjectStore,
    *,
    cells: Sequence[ForecastCell] = NEIGHBOURING_CELLS,
    pointers: InMemoryPointerStore | None = None,
    dry_run_prefix: str | None = None,
    payload: bytes = PAYLOAD,
) -> WeatherForecastDailyReceipt:
    return run_weather_forecast_daily(
        store,
        ISSUE_DATE,
        cells,
        dry_run_prefix=dry_run_prefix,
        source=ReplayForecastSource(payload=payload),
        pointers=pointers if pointers is not None else InMemoryPointerStore(),
        forecast_days=2,
        now=PUBLISHED_AT,
    )


def test_a_run_writes_every_rung_and_marks_each_one_complete() -> None:
    store = _store()

    receipt = _run(store)

    assert tuple(rung.zoom for rung in receipt.rungs) == ZOOM_TIERS
    assert receipt.base_row_count == len(NEIGHBOURING_CELLS) * HOURS_IN_FIXTURE * len(PUBLISHED_VARIABLES)
    for rung in receipt.rungs:
        assert rung.parts[0].relative_path == partition_path(
            WEATHER_FORECAST_STREAM, WEATHER_FORECAST_KIND, rung.zoom, ISSUE_DATE
        )
        assert store.read_object(rung.completion.relative_path) is not None


def test_the_partition_day_is_the_issue_date_under_kind_observed() -> None:
    """The release-series rule: a run's issue day is never in the future, so `observed` is honest."""
    store = _store()

    _run(store)

    keys = store.list_relative_paths(f"layer={WEATHER_FORECAST_STREAM}/")
    assert keys
    assert all(key.startswith(f"layer={WEATHER_FORECAST_STREAM}/kind=observed/") for key in keys)
    assert all("/kind=forecast/" not in key for key in keys)
    assert any("year=2026/month=09/day=18/" in key for key in keys)


def test_no_published_row_carries_a_negative_lead_or_an_unexplained_null() -> None:
    store = _store()

    receipt = _run(store)

    base = store.read_partition(WEATHER_FORECAST_STREAM, WEATHER_FORECAST_KIND, BASE_PARTITION_ZOOM, ISSUE_DATE)
    rows = base.table.to_pylist()
    assert rows
    assert min(row["lead_hours"] for row in rows) == 0
    assert max(row["lead_hours"] for row in rows) == HOURS_IN_FIXTURE - 1
    for row in rows:
        assert row["lead_hours"] >= 0
        assert row["valid_time"] >= row["model_init_time"]
        assert row["cell_id"] is not None
        assert (row["value"] is None) == (row["missing_reason"] is not None)
        assert row["run_id"] == receipt.run_id


def test_a_variable_the_provider_never_answered_becomes_a_reasoned_absence() -> None:
    """The fixture's first hour carries a null cloud_cover; it must publish a row, not vanish."""
    store = _store()

    _run(store)

    rows = store.read_partition(
        WEATHER_FORECAST_STREAM, WEATHER_FORECAST_KIND, BASE_PARTITION_ZOOM, ISSUE_DATE
    ).table.to_pylist()
    first_hour = [row for row in rows if row["valid_time"] == RUN_INIT and row["cell_id"] == "cell-0001"]
    assert {row["variable"] for row in first_hour} == set(PUBLISHED_VARIABLES)
    absent = next(row for row in first_hour if row["variable"] == "cloud_cover")
    assert absent["value"] is None
    assert absent["missing_reason"] == "not_generated"


def test_the_coarse_rungs_merge_cells_null_the_cell_id_and_rederive_the_bearing() -> None:
    store = _store()

    _run(store, cells=MERGING_CELLS, payload=MERGED_PAYLOAD)

    coarse = store.read_partition(
        WEATHER_FORECAST_STREAM, WEATHER_FORECAST_KIND, MERGED_RUNG, ISSUE_DATE
    ).table.to_pylist()
    assert coarse
    assert all(row["cell_id"] is None for row in coarse)
    assert {(row["longitude"], row["latitude"]) for row in coarse} == {
        (
            floor_coordinate(MERGING_CELLS[0].longitude, zoom=MERGED_RUNG, axis="longitude"),
            floor_coordinate(MERGING_CELLS[0].latitude, zoom=MERGED_RUNG, axis="latitude"),
        )
    }
    hour = min(row["valid_time"] for row in coarse if row["variable"] == "wind_u_10m" and row["value"] is not None)
    readings = {row["variable"]: row for row in coarse if row["valid_time"] == hour}
    east, north = readings["wind_u_10m"]["value"], readings["wind_v_10m"]["value"]
    assert readings["wind_speed_10m"]["value"] == pytest.approx(math.hypot(east, north))
    assert readings["wind_direction_10m"]["value"] == pytest.approx(
        math.degrees(math.atan2(-east, -north)) % DEGREES_PER_TURN
    )


def test_two_runs_of_one_day_write_byte_identical_partitions() -> None:
    """NFR 1: same inputs, same clock, same bytes. A dropped sort or a stray timestamp breaks it."""
    left = _run(_store())
    right = _run(_store())

    assert [rung.parts[0].sha256 for rung in left.rungs] == [rung.parts[0].sha256 for rung in right.rungs]
    assert left.publication.generation_sha256 == right.publication.generation_sha256


def test_a_written_day_is_published_with_a_pointer_and_a_source_receipt() -> None:
    store = _store()
    pointers = InMemoryPointerStore()

    receipt = _run(store, pointers=pointers)

    assert receipt.publication.outcome == "advanced"
    pointer_key = store.absolute_key(availability_pointer_path(WEATHER_FORECAST_STREAM, WEATHER_FORECAST_KIND))
    assert pointers.read_pointer(pointer_key) is not None
    assert store.read_object(receipt.publication.generation_key) is not None
    evidence = [key for key in store.list_relative_paths("layer=") if "/availability/evidence/" in key]
    assert any("/source-" in key for key in evidence)
    assert len([key for key in evidence if "/terminal-z" in key]) == len(ZOOM_TIERS)


def test_the_source_receipt_names_the_request_and_the_response_digest() -> None:
    store = _store()

    receipt = _run(store)

    assert len(receipt.source_receipts) == 1
    source = receipt.source_receipts[0]
    assert source.source_slug == WEATHER_FORECAST_SOURCE_SLUG
    assert source.request_url.startswith("https://single-runs-api.open-meteo.com/v1/forecast?")
    assert len(source.response_sha256) == SHA256_HEX_LENGTH
    assert source.location_count == len(NEIGHBOURING_CELLS)


def test_a_dry_run_writes_only_under_its_scratch_root() -> None:
    store = _store()

    _run(store, dry_run_prefix="ml/scratch/2026-09-18/")

    written = tuple(store.backend.objects)
    assert written
    assert all(key.startswith("ml/scratch/2026-09-18/") for key in written)


def test_a_run_with_no_pointer_store_is_refused_before_it_writes_anything() -> None:
    store = _store()

    with pytest.raises(WeatherForecastRunError):
        run_weather_forecast_daily(store, ISSUE_DATE, NEIGHBOURING_CELLS, source=ReplayForecastSource())

    assert not store.backend.objects


@pytest.mark.parametrize(
    "cells",
    [
        (),
        (
            ForecastCell(cell_id="cell-0001", longitude=-120.25, latitude=46.25),
            ForecastCell(cell_id="cell-0001", longitude=-119.25, latitude=47.25),
        ),
    ],
)
def test_an_inadmissible_cell_list_is_refused(cells: Sequence[ForecastCell]) -> None:
    with pytest.raises(WeatherForecastRunError):
        _run(_store(), cells=cells)


def test_a_cell_outside_the_sources_coverage_is_refused_rather_than_invented() -> None:
    regional = ReplayForecastSource(coverage=SourceCoverage(claim="pnw", bounding_box=(-125.0, 42.0, -116.0, 49.0)))

    with pytest.raises(WeatherForecastRunError):
        run_weather_forecast_daily(
            _store(),
            ISSUE_DATE,
            (ForecastCell(cell_id="cell-9999", longitude=100.0, latitude=5.0),),
            source=regional,
            pointers=InMemoryPointerStore(),
            now=PUBLISHED_AT,
        )


def test_the_bootstrap_marker_is_the_shape_the_siblings_reader_admits() -> None:
    """B1: the marker used to be an invented three-field document `put_immutable` made permanent."""
    store = _store()

    _run(store)

    lane_root = availability_lane_root(WEATHER_FORECAST_STREAM, WEATHER_FORECAST_KIND)
    marker = store.read_object(availability_bootstrap_marker_key(lane_root))
    assert marker is not None
    document = json.loads(marker)
    assert set(document) == {
        "bootstrap_receipt_key",
        "bootstrap_receipt_sha256",
        "lane_root",
        "schema_version",
    }
    assert document["schema_version"] == BOOTSTRAP_MARKER_SCHEMA_VERSION
    assert document["lane_root"] == lane_root

    # The bytes are exactly what this repo's own reader admits, and what it re-serializes.
    receipt = read_lane_bootstrap_receipt(store, lane_root=lane_root)
    assert receipt is not None
    assert marker == bootstrap_marker_payload(lane_root, receipt)
    # The marker names a receipt that EXISTS and digests to what it claims.
    body = store.read_object(receipt.key)
    assert body is not None
    assert sha256_of(body) == receipt.sha256
    assert json.loads(body)["lane_root"] == lane_root


def test_a_replayed_day_adopts_the_bootstrap_already_on_the_lane() -> None:
    """The first generation is immutable; a second run must bind it rather than begin a history."""
    store = _store()
    pointers = InMemoryPointerStore()

    first = _run(store, pointers=pointers)
    second = _run(store, pointers=pointers)

    lane_root = availability_lane_root(WEATHER_FORECAST_STREAM, WEATHER_FORECAST_KIND)
    assert read_lane_bootstrap_receipt(store, lane_root=lane_root) is not None
    assert first.publication.generation_sha256 == second.publication.generation_sha256


@pytest.mark.parametrize("prefix", ["layer=weather-forecast/", "scratch/", "ml/scratch/no-slash"])
def test_a_dry_run_prefix_outside_the_scratch_root_is_refused(prefix: str) -> None:
    """M5: the same SCRATCH_PREFIX_ROOT check fire-risk makes, through the same shared helper."""
    store = _store()

    with pytest.raises(WeatherForecastRunError, match="scratch root"):
        _run(store, dry_run_prefix=prefix)

    assert not store.backend.objects


def test_a_run_with_no_readings_publishes_the_day_as_a_governed_absence() -> None:
    """M2: a day nobody wrote must still be INDEXED, or silence reads as "never attempted"."""
    store = _store()
    pointers = InMemoryPointerStore()

    receipt = run_weather_forecast_daily(
        store,
        ISSUE_DATE,
        NEIGHBOURING_CELLS,
        source=_SamplelessSource(),
        pointers=pointers,
        forecast_days=2,
        now=PUBLISHED_AT,
    )

    assert receipt.rungs == ()
    assert receipt.base_row_count == 0
    assert receipt.publication.outcome == "advanced"
    assert partition_path(WEATHER_FORECAST_STREAM, WEATHER_FORECAST_KIND, BASE_PARTITION_ZOOM, ISSUE_DATE) not in (
        store.backend.objects
    )
    rows = _generation_rows(store, receipt)
    assert {row["rung"] for row in rows} == set(ZOOM_TIERS)
    assert {row["terminal_state"] for row in rows} == {"governed_absence"}
    assert {row["absence_reason"] for row in rows} == {NO_READINGS_REASON}


def test_a_merged_coarse_row_reports_a_merged_support_not_the_first_members() -> None:
    """A coarse cell that merged two footprints may claim neither of them verbatim."""
    store = _store()

    _run(store, cells=MERGING_CELLS, payload=_mixed_support_payload())

    coarse = store.read_partition(
        WEATHER_FORECAST_STREAM, WEATHER_FORECAST_KIND, MERGED_RUNG, ISSUE_DATE
    ).table.to_pylist()
    base = store.read_partition(
        WEATHER_FORECAST_STREAM, WEATHER_FORECAST_KIND, BASE_PARTITION_ZOOM, ISSUE_DATE
    ).table.to_pylist()

    # Every base row is a sampled point, so the unanimous merge keeps that claim...
    assert {row["support"] for row in base} == {"sampled_point"}
    assert {row["support"] for row in coarse} == {"sampled_point"}
    # ...and a group whose members disagree falls back to the weakest claim rather than rows[0].
    mixed = ({"support": "sampled_point"}, {"support": "native_grid"})
    assert _merged_support(mixed) == MERGED_SUPPORT


@dataclass(frozen=True, slots=True)
class _SamplelessSource(ReplayForecastSource):
    """A source that answers for every requested location but carries no readings at all."""

    async def fetch_run(
        self,
        *,
        model_init_time: datetime,
        coordinates: Sequence[tuple[float, float]],
        variables: Sequence[str] = UPSTREAM_VARIABLES,
        forecast_days: int = 2,
    ) -> WeatherForecastRun:
        """Parse the captured bytes, then strip every sample off every location.

        Named explicitly rather than through `super()`: a `slots=True` dataclass subclass cannot use
        the zero-argument form, because the closure cell points at the pre-slots class object.
        """
        run = await ReplayForecastSource.fetch_run(
            self,
            model_init_time=model_init_time,
            coordinates=coordinates,
            variables=variables,
            forecast_days=forecast_days,
        )
        return replace(
            run,
            locations=tuple(replace(location, samples=()) for location in run.locations),
        )


def _mixed_support_payload() -> bytes:
    """Return the merge payload unchanged; support is a parse-time constant, merged in the rung step."""
    return MERGED_PAYLOAD


def _generation_rows(store: ObjectStore, receipt: WeatherForecastDailyReceipt) -> list[dict[str, object]]:
    """Read the published availability generation back out of the bucket."""
    payload = store.read_object(receipt.publication.generation_key)
    assert payload is not None
    return pq.read_table(io.BytesIO(payload)).to_pylist()


# --- The configured cell inventory (M6) -----------------------------------------------------------


def test_the_cell_inventory_is_read_out_of_the_bucket_rather_than_declared_in_the_environment() -> None:
    """A deployment variable holding hundreds of coordinates is a configuration nobody reviews."""
    store = ObjectStore(backend=InMemoryObjectStoreBackend())
    store.backend.put(
        INVENTORY_KEY,
        json.dumps([{"cell_id": "c-1", "longitude": -120.125, "latitude": 46.375}]).encode("utf-8"),
        content_type=JSON_CONTENT_TYPE,
    )

    cells = read_forecast_cells(store.read_only(), key=INVENTORY_KEY)

    assert cells == (ForecastCell(cell_id="c-1", longitude=-120.125, latitude=46.375),)


def test_an_inventory_key_naming_no_object_refuses_by_its_own_type() -> None:
    """An unnamed inventory and a broken one are different operator actions, under different types."""
    store = ObjectStore(backend=InMemoryObjectStoreBackend())

    with pytest.raises(ForecastCellInventoryError):
        read_forecast_cells(store.read_only(), key=INVENTORY_KEY)


def test_an_inventory_record_missing_a_coordinate_is_refused_rather_than_defaulted() -> None:
    store = ObjectStore(backend=InMemoryObjectStoreBackend())
    store.backend.put(
        INVENTORY_KEY,
        json.dumps([{"cell_id": "c-1", "longitude": -120.125}]).encode("utf-8"),
        content_type=JSON_CONTENT_TYPE,
    )

    with pytest.raises(ForecastCellInventoryError):
        read_forecast_cells(store.read_only(), key=INVENTORY_KEY)

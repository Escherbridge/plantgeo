"""The P4 gates: reference parsing, exact-pixel checks, representativeness, REST deferral, internal sums, verdict.

Captured rasters are in-memory EPSG:4326 grids; no network, bucket or database. Rationale: the lane's
AGENTS.md, "Verify".
"""

from __future__ import annotations

import io
import tarfile
from typing import TYPE_CHECKING, Any, Final

import numpy as np
import pytest
from rasterio.crs import CRS  # type: ignore[import-untyped]
from rasterio.transform import Affine  # type: ignore[import-untyped]

from agri_data_service.pipeline.direct.soil_properties import verify
from agri_data_service.pipeline.direct.soil_properties.products import CELL_CENTRE_OFFSET_DEGREES, LATTICE_DEGREES
from agri_data_service.pipeline.direct.soil_properties.source import SoilPropertiesPipelineError
from agri_data_service.warehouse.schemas.soil_properties import SOIL_PROPERTIES_VALUE_COLUMNS
from tests.direct.soil_properties.lane_fixtures import lane_table

if TYPE_CHECKING:
    import pyarrow as pa  # type: ignore[import-untyped]

#: A native grid of 0.005-degree pixels aligned with the lattice, so each cell centre is inside one pixel.
GRID_WEST: Final = -117.0
GRID_NORTH: Final = 46.75
GRID_SIDE: Final = 6
PH_MAPPED: Final = 57
CELLS: Final = [
    (GRID_WEST + LATTICE_DEGREES * column, GRID_NORTH - LATTICE_DEGREES * (row + 1))
    for row in range(1, 4)
    for column in range(1, 4)
]


def _raster(values: np.ndarray | None = None) -> verify.CapturedRaster:
    grid = values if values is not None else np.full((GRID_SIDE, GRID_SIDE), PH_MAPPED, dtype=np.int32)
    return verify.CapturedRaster(
        values=grid.astype(np.int32),
        transform=Affine(LATTICE_DEGREES, 0.0, GRID_WEST, 0.0, -LATTICE_DEGREES, GRID_NORTH),
        crs=CRS.from_epsg(4326),
    )


def _lane(value: float = PH_MAPPED, **kwargs: Any) -> pa.Table:
    return lane_table(CELLS, value=value, **kwargs)


def _centre(cell: tuple[float, float]) -> tuple[float, float]:
    return cell[0] + CELL_CENTRE_OFFSET_DEGREES, cell[1] + CELL_CENTRE_OFFSET_DEGREES


# --- Units and the reference archive ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("physical", "divisor", "mapped"),
    [(5.65, 10, 57), (1.285, 100, 129), (24.3, 10, 243), (0.05, 100, 5)],
)
def test_physical_values_convert_by_decimal_round_half_up(physical: float, divisor: int, mapped: int) -> None:
    assert verify.mapped_from_physical(physical, divisor) == mapped


CSV_CACHE: Final = (
    "id,lat,lon,ph,organic_carbon,nitrogen,bulk_density,cec,cached_at\nx,46.7375,-116.9925,5.7,24.3,1.9,1.21,18.2,t\n"
)
COPY_DUMP: Final = (
    "CREATE TABLE public.soil_grid_cache (id uuid);\n"
    "COPY public.soil_grid_cache (id, lat, lon, ph, organic_carbon, nitrogen, bulk_density, cec, cached_at) "
    "FROM stdin;\n"
    "x\t46.7375\t-116.9925\t5.7\t\\N\t1.9\t1.21\t18.2\tt\n"
    "\\.\n"
)


def test_a_csv_cache_parses_into_physical_values() -> None:
    [point] = verify.read_reference_archive(CSV_CACHE.encode(), "soil_grid_cache.csv")
    assert (point.longitude, point.latitude) == (-116.9925, 46.7375)
    assert point.values["phh2o"] == pytest.approx(5.7)
    assert set(point.values) == {"phh2o", "soc", "nitrogen", "bdod", "cec"}


def test_a_plain_sql_copy_block_parses_and_keeps_nulls_out() -> None:
    [point] = verify.read_reference_archive(COPY_DUMP.encode(), "preserve.sql")
    assert "soc" not in point.values, "a \\N value is absent, never zero"
    assert point.values["bdod"] == pytest.approx(1.21)


def test_a_tarred_preserve_set_is_searched_for_the_cache_member() -> None:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, body in (("job_definition.csv", b"id\n1\n"), ("public/soil_grid_cache.csv", CSV_CACHE.encode())):
            info = tarfile.TarInfo(name)
            info.size = len(body)
            archive.addfile(info, io.BytesIO(body))
    points = verify.read_reference_archive(buffer.getvalue(), "20260909-prod-preserve-set.tar.gz")
    assert len(points) == 1


def test_a_pg_custom_dump_is_refused_with_the_extraction_instruction() -> None:
    with pytest.raises(SoilPropertiesPipelineError, match="extract soil_grid_cache to CSV"):
        verify.read_reference_archive(b"PGDMP\x01\x0e" + b"soil_grid_cache", "dump.soil_grid_cache.bin")


# --- Captured rasters -------------------------------------------------------------------------------


def test_a_point_reads_the_native_pixel_containing_it_and_its_neighbourhood() -> None:
    values = np.arange(GRID_SIDE * GRID_SIDE, dtype=np.int32).reshape(GRID_SIDE, GRID_SIDE)
    raster = _raster(values)
    longitude, latitude = _centre(CELLS[0])
    assert raster.values_at([longitude], [latitude]).tolist() == [values[1, 1]]
    assert raster.neighbourhoods([longitude], [latitude]) == [frozenset(values[0:3, 0:3].ravel().tolist())]
    assert raster.values_at([-100.0], [40.0]).tolist() == [int(verify.MISSING)]


# --- Gates -------------------------------------------------------------------------------------------


def _reference(value: float, cells: list[tuple[float, float]] = CELLS) -> list[verify.ReferencePoint]:
    return [verify.ReferencePoint(*_centre(cell), {"phh2o": value}) for cell in cells]


def test_g_v1_passes_when_the_cache_equals_the_captured_pixel() -> None:
    gate = verify.gate_reference_exact(_reference(PH_MAPPED / 10), lambda _column: _raster(), units="physical")
    assert gate["status"] == "pass"
    assert gate["properties"]["phh2o"]["exact_share"] == 1.0


def test_g_v1_fails_a_mismatch_outside_the_neighbourhood() -> None:
    gate = verify.gate_reference_exact(_reference(9.9), lambda _column: _raster(), units="physical")
    assert gate["status"] == "fail"
    assert gate["properties"]["phh2o"]["mismatches_outside_neighbourhood"] == len(CELLS)


def test_g_v2_requires_every_sampled_lane_value_to_equal_the_capture() -> None:
    assert verify.gate_lane_matches_capture(_lane(), lambda _column: _raster())["status"] == "pass"
    drifted = _lane(overrides={"phh2o_0_5cm": [PH_MAPPED + 1] * len(CELLS)})
    gate = verify.gate_lane_matches_capture(drifted, lambda _column: _raster())
    assert gate["status"] == "fail"
    assert gate["columns"]["phh2o_0_5cm"]["exact_share"] == 0.0


def test_g_v3_correlates_the_containing_cell_with_the_cache() -> None:
    ph = [50.0 + index for index in range(len(CELLS))]
    lane = _lane(overrides={"phh2o_0_5cm": ph})
    reference = [
        verify.ReferencePoint(*_centre(cell), {"phh2o": value / 10}) for cell, value in zip(CELLS, ph, strict=True)
    ]
    gate = verify.gate_representativeness(lane, reference, units="physical")
    assert gate["status"] == "pass"
    assert gate["properties"]["phh2o"]["pearson_r"] == 1.0


def test_g_v4_is_deferred_never_passed_when_rest_is_down() -> None:
    def down(_longitude: float, _latitude: float) -> dict[str, int | None]:
        raise verify.RestUnavailableError("HTTP 429")

    gate = verify.gate_rest(_lane(), lambda _column: _raster(), points=3, fetch=down, pause=lambda: None)
    assert gate["status"] == "deferred"
    assert gate["reason"] == "HTTP 429"


def test_g_v4_passes_when_rest_answers_the_lane_values() -> None:
    def answering(_longitude: float, _latitude: float) -> dict[str, int | None]:
        return dict.fromkeys(SOIL_PROPERTIES_VALUE_COLUMNS, PH_MAPPED)

    gate = verify.gate_rest(_lane(), lambda _column: _raster(), points=3, fetch=answering, pause=lambda: None)
    assert gate["status"] == "pass"


def test_the_rest_answer_parses_into_lane_columns() -> None:
    answer = {
        "properties": {
            "layers": [
                {"name": "phh2o", "depths": [{"label": "0-5cm", "values": {"mean": 57}}]},
                {"name": "cfvo", "depths": [{"label": "15-30cm", "values": {"mean": None}}]},
            ]
        }
    }
    assert verify.parse_rest_answer(answer) == {"phh2o_0_5cm": PH_MAPPED, "cfvo_15_30cm": None}


def test_g_v5_checks_texture_sums_and_plausibility() -> None:
    fractions = (("sand", 400.0), ("silt", 400.0), ("clay", 200.0))
    texture = {
        f"{code}_{depth}cm": [value] * len(CELLS) for code, value in fractions for depth in ("0_5", "5_15", "15_30")
    }
    plausible = _lane(overrides={**texture, "phh2o_0_5cm": [60.0] * len(CELLS), "bdod_0_5cm": [130.0] * len(CELLS)})
    assert verify.gate_internal(plausible)["status"] == "pass"
    acidic_beyond_nature = _lane(overrides={**texture, "phh2o_0_5cm": [20.0] * len(CELLS)})
    assert verify.gate_internal(acidic_beyond_nature)["status"] == "fail"


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        ({"G-V1": "pass", "G-V2": "pass", "G-V3": "pass", "G-V4": "deferred", "G-V5": "pass"}, "pass"),
        ({"G-V1": "pass", "G-V2": "pass", "G-V3": "pass", "G-V4": "pass", "G-V5": "pass"}, "pass"),
        ({"G-V1": "fail", "G-V2": "pass", "G-V3": "pass", "G-V4": "pass", "G-V5": "pass"}, "fail"),
        ({"G-V1": "pass", "G-V2": "pass", "G-V3": "pass", "G-V4": "fail", "G-V5": "pass"}, "fail"),
        ({"G-V1": "pass", "G-V2": "pass", "G-V3": "pass", "G-V4": "not_run", "G-V5": "pass"}, "incomplete"),
        ({"G-V1": "fail", "G-V2": "pass", "G-V3": "pass", "G-V4": "not_run", "G-V5": "pass"}, "fail"),
    ],
)
def test_the_verdict_accepts_a_deferred_rest_gate_only(statuses: dict[str, str], expected: str) -> None:
    assert verify.verdict({name: {"status": status} for name, status in statuses.items()}) == expected


def test_stratified_rows_are_reproducible_and_one_per_band() -> None:
    lane = _lane()
    first = verify.stratified_rows(lane, 3)
    assert first == verify.stratified_rows(lane, 3)
    assert len(set(first)) == len(first)


def test_the_lane_grid_finds_the_cell_containing_a_point() -> None:
    lane = _lane()
    grid = verify.lane_grid_index(lane)
    for index, cell in enumerate(CELLS):
        assert verify.containing_row(grid, *_centre(cell)) == index
    assert verify.containing_row(grid, -100.0, 40.0) is None


# --- Review m1: an answer that cannot be compared defers; zero points is incomplete, never pass -------


def test_zero_rest_points_is_not_run_and_the_verdict_is_incomplete() -> None:
    def never(_longitude: float, _latitude: float) -> dict[str, int | None]:
        raise AssertionError("no REST call with --rest-points 0")

    gate = verify.gate_rest(_lane(), lambda _column: _raster(), points=0, fetch=never, pause=lambda: None)
    assert gate["status"] == "not_run"
    statuses = {"G-V1": "pass", "G-V2": "pass", "G-V3": "pass", "G-V4": gate["status"], "G-V5": "pass"}
    assert verify.verdict({name: {"status": status} for name, status in statuses.items()}) == "incomplete"


@pytest.mark.parametrize(
    ("answer", "reason"),
    [
        pytest.param({}, "unexpected_answer_shape", id="unexpected-shape"),
        pytest.param(dict.fromkeys(SOIL_PROPERTIES_VALUE_COLUMNS), "all_null_means", id="all-null"),
    ],
)
def test_an_uncomparable_rest_answer_is_deferred_never_failed(answer: dict[str, int | None], reason: str) -> None:
    gate = verify.gate_rest(
        _lane(), lambda _column: _raster(), points=3, fetch=lambda *_: dict(answer), pause=lambda: None
    )
    assert gate["status"] == "deferred"
    assert gate["reason"] == reason


def test_the_real_parser_turns_an_unexpected_shape_into_a_deferral() -> None:
    parsed = verify.parse_rest_answer({"type": "Feature", "geometry": None})
    assert verify.rest_answers_problem([parsed]) == "unexpected_answer_shape"


def test_a_column_rest_never_answered_defers_unless_another_column_failed() -> None:
    column = SOIL_PROPERTIES_VALUE_COLUMNS[0]

    def partial(_longitude: float, _latitude: float) -> dict[str, int | None]:
        return {**dict.fromkeys(SOIL_PROPERTIES_VALUE_COLUMNS, PH_MAPPED), column: None}

    gate = verify.gate_rest(_lane(), lambda _column: _raster(), points=3, fetch=partial, pause=lambda: None)
    assert gate["status"] == "deferred"
    assert gate["reason"] == "columns_without_rest_values"
    assert gate["columns"][column]["passed"] is None

    def partial_and_wrong(_longitude: float, _latitude: float) -> dict[str, int | None]:
        return {**partial(0.0, 0.0), SOIL_PROPERTIES_VALUE_COLUMNS[1]: PH_MAPPED + 40}

    failing = verify.gate_rest(
        _lane(), lambda _column: _raster(), points=3, fetch=partial_and_wrong, pause=lambda: None
    )
    assert failing["status"] == "fail"

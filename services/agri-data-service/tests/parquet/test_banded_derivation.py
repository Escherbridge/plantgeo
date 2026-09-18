"""Latitude-band folding in `derive_and_write_day_tiers`, and what it must NOT change.

THE FIRST DUTY OF THIS FILE IS BYTE-IDENTITY. A lane that declares no banding takes the whole-day
path, and every one of the existing lanes declares none. That is pinned against the code as it stood
BEFORE banding existed: `git show d4bb3491:.../derivation.py` (the last commit without it) is loaded
beside the working tree, both derive the same base day into two in-memory buckets, and every object
-- part bytes, completion markers -- must be identical. The oracle is asserted to lack
`latitude_banding`, so the pin cannot silently become "the tree against itself".

THE SECOND DUTY IS EXACTNESS OF THE FOLD, PROVEN ON THE ENVELOPE. Band membership is ONE integer
rule -- `floor(lat / 0.2) // cells_per_band`, evaluated by Polars everywhere -- and the property test
walks the whole 24-50 N lattice for every (band height, base pitch) pair ROW-CAP-ANALYSIS section 4
recommends, asserting that every z9 and z5 cell lies in exactly one band and that banded z9/z5/z0
equal the whole-day rungs frame for frame. The reviewer's reproducers (0.4 deg over 24.0-24.4,
27.0-27.2, 27.0-27.6 with one-row parts; 0.2 deg over 26.0-26.2) are pinned as regressions.

THE THIRD DUTY IS THE READ-BACK. The writer RETURNS each part's z5 cell range with its digest; the
caller hands them to the fold, band k fetches no part whose range lies outside k, and a part whose
bytes no longer match the digest (another process rewrote the day in place) is refused by name. With
no ranges in hand the day is read whole when small and REFUSED by name when its part count says it
may exceed the cap, rather than exhausting the host. Nothing is cached in the store.

MEMBERSHIP IS EXACT INTEGER ARITHMETIC ON THE DECLARED LATTICE. Polars 1.43 evaluates `col / 0.2`
through different paths for 1-row and N-row frames (32.8 / 0.2 is 163.999... on one and 164.0 on the
other), so `floor(lat / 0.2)` was not one rule; `round(lat / base) // cells_per_z5` is, and a test
evaluates it on 1-row and N-row frames for every envelope lattice latitude at each recommended base.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import sys
import types
from pathlib import Path
from typing import TYPE_CHECKING, Final

import polars as pl
import pyarrow as pa  # type: ignore[import-untyped]
import pytest

from agri_data_service.foundation.parquet.paths import MAX_PART_INDEX, partition_path
from agri_data_service.pipeline.parquet import derivation
from agri_data_service.pipeline.parquet.derivation import (
    BAND_SAFE_AGGREGATES,
    BandedBaseWrite,
    LatitudeBanding,
    LatitudeBandingError,
    NonConstantFirstError,
    PartCellRange,
    TierWriteError,
    UnknownPartBoundsError,
    derive_and_write_day_tiers,
    latitude_banding,
    register_latitude_banding,
    write_banded_base_day,
)
from agri_data_service.pipeline.parquet.objectstore import (
    BANDED_BASE_ROWS_PER_PART,
    ObjectStore,
    ParquetWriteError,
    required_part_count,
)
from agri_data_service.warehouse.parquet.schema import (
    ParquetStreamSchema,
    observed_stream_schema,
    register_stream_schema,
)
from agri_data_service.warehouse.parquet.tiers import (
    BASE_ZOOM_TIER,
    DERIVED_ZOOM_TIERS,
    TIER_RESOLUTION_DEGREES,
    ColumnAggregation,
    GridAggregation,
    TierDerivation,
    TierPassthrough,
    derive_tier,
    floor_to_resolution,
    register_tier_derivation,
)
from tests.parquet.test_objectstore_writer import RecordingBackend

if TYPE_CHECKING:
    from collections.abc import Mapping

    from agri_data_service.foundation.parquet.zoom import ZoomTier

from collections import Counter

DAY: Final = dt.date(2026, 9, 1)
RUN_ID: Final = "banded-test-run"
FROZEN_NOW: Final = dt.datetime(2026, 9, 1, 12, tzinfo=dt.UTC)
KIND: Final = "observed"
FIRE_DETECTIONS_STREAM: Final = "fire-detections"
#: The last commit whose `derivation.py` had no banding at all: the oracle the unbanded path is pinned to,
#: vendored byte-exact (`git show d4bb3491:...derivation.py`, git blob 4b8cddfc6c09beeed0674d1ec9800af5a1bfc88f)
#: as a `.py.txt` so no linter, formatter or collector ever touches it, and pinned by digest before it is exec'd.
ORACLE_COMMIT: Final = "d4bb3491"
ORACLE_PATH: Final = Path(__file__).parent / "fixtures" / f"derivation_oracle_{ORACLE_COMMIT}.py.txt"
ORACLE_SHA256: Final = "8bac193e4e1ec5bd41f359f721c1841f7f0d54bec488223efeb3b9e56e28d7c4"

BANDED_STREAM: Final = "test-banded-evt-lane"
UNBANDED_TWIN_STREAM: Final = "test-banded-evt-lane-whole-day"
BASE_RESOLUTION: Final = 0.005
Z5_PITCH: Final = TIER_RESOLUTION_DEGREES[5]
Z9_PITCH: Final = TIER_RESOLUTION_DEGREES[9]
BANDING: Final = LatitudeBanding.of_height(1.0, BASE_RESOLUTION)
ONE_DEGREE_IN_Z5_CELLS: Final = 5

#: Fixture sizes named once, so a comparison against them reads as the intent rather than a number.
SMALL_PART_ROWS: Final = 150
EDGE_PART_ROWS: Final = 4
ENVELOPE_PART_ROWS: Final = 5_000
STRADDLING_BANDS: Final = frozenset({42, 43, 44})
BAND_42: Final = 42
BAND_43: Final = 43
BAND_43_EDGE_LATITUDE: Final = 43.0
BAND_43_CELLS: Final = (215, 219)
FIRST_CELL_OF_BAND_42: Final = 210
FIRST_CELL_OF_BAND_44: Final = 220
LARGE_PART_ROWS: Final = 1_000
#: Two origins either side of a 0.4 deg band edge: 32.8 is base cell 13120 -> z5 cell 164, which opens band 82;
#: 32.7975 is z5 cell 163, band 81. Before `FLOOR_SNAP_TOLERANCE` the platform floored 32.8 into the z9 cell "32.79".
SPLIT_CELL_LATITUDES: Final = (32.7975, 32.8)
RECOMMENDED_BASES: Final = (0.0025, 0.005, 0.01)
LEGEND_CONIFER: Final = 7011
ENVELOPE_SOUTH: Final = 24.0
ENVELOPE_NORTH: Final = 50.0

#: ROW-CAP-ANALYSIS sections 2.3 / 3.2, pinned.
ROWS_AT_0005: Final = 22_800_000
ROWS_AT_00025: Final = 65_200_000
ROWS_AT_00025_TOP_OF_RANGE: Final = 85_600_000
PARTS_AT_0005: Final = 92
PARTS_AT_00025: Final = 261
PARTS_AT_00025_TOP_OF_RANGE: Final = 343
BAND_CUTS_AT_00025: Final = 18
DERIVED_PART_ROWS: Final = 10_000
PARTS_AT_00025_IF_DERIVED_SIZE_REUSED: Final = 6_520
RECEIPT_BYTES_PER_PART: Final = 200
RECEIPT_CEILING_BYTES: Final = 1024 * 1024

# The joint-key ladder of spec FR-3: every coarser tuple is a subset of the finer one.
JOINT_LADDER: Final[Mapping[ZoomTier, tuple[str, ...]]] = {
    13: ("evt_code",),
    9: ("evt_group_code", "evt_phys", "evt_lifeform"),
    5: ("evt_phys", "evt_lifeform"),
    0: ("evt_lifeform",),
}
# A legend that does NOT nest (group 645 spans two physiognomies), as the real LF2025 legend does not.
LEGEND: Final = {
    7011: (645, "Conifer", "Tree"),
    7012: (645, "Riparian", "Tree"),
    7050: (609, "Shrubland", "Shrub"),
    -9999: (-9999, "Fill-NoData", "Fill-NoData"),
}


def _now() -> dt.datetime:
    return FROZEN_NOW


def _fire_detections_base_table(*, cells: int) -> pa.Table:
    """A fire-detections day whose cells merge at z9 and collapse to one at z0 (the drain test's shape)."""
    return pa.Table.from_pylist(
        [
            {
                "cell_longitude": -116.0 - index * 0.001,
                "cell_latitude": 43.0 + index * 0.001,
                "observed_day": DAY,
                "detection_count": index + 1,
                "frp_sum": 10.0 * (index + 1),
                "frp_observation_count": 1,
                "high_confidence_detection_count": 1,
                "newest_observed_at": FROZEN_NOW,
            }
            for index in range(cells)
        ],
        schema=observed_stream_schema(FIRE_DETECTIONS_STREAM).arrow_schema,
    )


def _vegetation_arrow_schema() -> pa.Schema:
    return pa.schema(
        [
            pa.field("cell_lon", pa.float64(), nullable=False),
            # Nullable so the "every band is empty" case can be built from unlocated rows.
            pa.field("cell_lat", pa.float64(), nullable=True),
            pa.field("class_system", pa.string(), nullable=False),
            pa.field("evt_code", pa.int32(), nullable=True),
            pa.field("evt_group_code", pa.int32(), nullable=True),
            pa.field("evt_phys", pa.string(), nullable=True),
            pa.field("evt_lifeform", pa.string(), nullable=False),
            pa.field("pixel_count", pa.int64(), nullable=False),
            pa.field("release_day", pa.date32(), nullable=False),
        ]
    )


def _vegetation_strategy() -> GridAggregation:
    return GridAggregation(
        longitude_column="cell_lon",
        latitude_column="cell_lat",
        key_columns=("evt_code",),
        key_columns_by_tier=JOINT_LADDER,
        aggregations=(
            ColumnAggregation("class_system", "first"),
            ColumnAggregation("evt_code", "null"),
            ColumnAggregation("evt_group_code", "null"),
            ColumnAggregation("evt_phys", "null"),
            ColumnAggregation("pixel_count", "sum"),
            ColumnAggregation("release_day", "first"),
        ),
    )


def _register_vegetation_like_lane(stream: str, banding: LatitudeBanding | None = None) -> str:
    register_stream_schema(
        ParquetStreamSchema(
            name=stream, arrow_schema=_vegetation_arrow_schema(), sort_columns=("cell_lon", "cell_lat", "evt_code")
        )
    )
    register_tier_derivation(TierDerivation(stream=stream, strategy=_vegetation_strategy()))
    if banding is not None:
        register_latitude_banding(stream, banding)
    return stream


@pytest.fixture(name="banded_lane")
def _banded_lane() -> str:
    """The banded lane and its whole-day twin: same schema, same strategy, only one declares a band."""
    _register_vegetation_like_lane(UNBANDED_TWIN_STREAM)
    return _register_vegetation_like_lane(BANDED_STREAM, BANDING)


def _lattice(
    *,
    latitudes: list[float],
    longitudes: tuple[float, ...] = (-116.0, -116.005, -116.2, -117.3),
) -> pl.DataFrame:
    """A class-composition lattice: two or three class rows per cell, deterministic counts."""
    rows = []
    for row_index, latitude in enumerate(latitudes):
        for column_index, longitude in enumerate(longitudes):
            seed = row_index * 7 + column_index * 3
            codes = [7011, 7050] if seed % 2 else [7011, 7012, -9999]
            for class_index, code in enumerate(codes):
                group, phys, lifeform = LEGEND[code]
                rows.append(
                    {
                        "cell_lon": longitude,
                        "cell_lat": latitude,
                        "class_system": "LANDFIRE_EVT_LF2025",
                        "evt_code": code,
                        "evt_group_code": group,
                        "evt_phys": phys,
                        "evt_lifeform": lifeform,
                        "pixel_count": 1 + (seed + class_index * 11) % 97,
                        "release_day": DAY,
                    }
                )
    frame = pl.from_arrow(pa.Table.from_pylist(rows, schema=_vegetation_arrow_schema()))
    assert isinstance(frame, pl.DataFrame)
    return frame


def _lattice_latitudes(low: float, high: float, pitch: float = BASE_RESOLUTION) -> list[float]:
    """Cell origins from `low` to `high` inclusive at `pitch`, rounded to six places as a lane would write them."""
    steps = round((high - low) / pitch)
    return [round(low + step * pitch, 6) for step in range(steps + 1)]


def _straddling_lattice() -> pl.DataFrame:
    """42.0 .. 44.0 inclusive: three 1.0 deg bands (42, 43, 44 -- the last holding one lattice row), ten z5 edges."""
    return _lattice(latitudes=_lattice_latitudes(42.0, 44.0))


def _read_rung(store: ObjectStore, stream: str, tier: ZoomTier) -> pl.DataFrame:
    frame = pl.from_arrow(store.read_partition(stream, KIND, tier, DAY))
    assert isinstance(frame, pl.DataFrame)
    return frame


def _derive_banded_day(store: ObjectStore, stream: str, written: BandedBaseWrite) -> derivation.DerivationResult:
    """The banded caller's shape: hand the fold the part ranges the writer returned."""
    return derive_and_write_day_tiers(
        store, layer=stream, kind=KIND, day=DAY, run_id=RUN_ID, now=_now, part_cell_ranges=written.part_cell_ranges
    )


def _recording_get(backend: RecordingBackend) -> list[str]:
    """Make `backend.get` record every key it is asked for; returns the live list."""
    fetched: list[str] = []
    original_get = backend.get

    def recording_get(key: str) -> bytes | None:
        fetched.append(key)
        return original_get(key)

    backend.get = recording_get  # type: ignore[method-assign]
    return fetched


def _parquet_keys(keys: list[str]) -> set[str]:
    return {key for key in keys if key.endswith(".parquet")}


def _assert_banded_equals_whole_day(store: ObjectStore, stream: str, base: pl.DataFrame) -> None:
    for tier in DERIVED_ZOOM_TIERS:
        from_bands = _read_rung(store, stream, tier)
        whole_day = derive_tier(base, stream=stream, tier=tier)
        assert from_bands.equals(whole_day), f"z{tier}: banded {from_bands.height} rows vs whole-day {whole_day.height}"


# --- Byte-identity: the unbanded path against the pre-banding oracle -----------------------------------


def _derivation_module_before_banding() -> types.ModuleType:
    """Load `derivation.py` exactly as committed at `ORACLE_COMMIT` from the vendored bytes, digest-pinned."""
    payload = ORACLE_PATH.read_bytes()
    assert hashlib.sha256(payload).hexdigest() == ORACLE_SHA256, "the vendored oracle is not the committed bytes"
    source = payload.decode("utf-8")
    module = types.ModuleType("agri_data_service_derivation_before_banding")
    # Registered first: `dataclass` resolves string annotations through `sys.modules[cls.__module__]`.
    sys.modules[module.__name__] = module
    exec(compile(source, f"<derivation.py@{ORACLE_COMMIT}>", "exec"), module.__dict__)
    return module


def test_a_lane_without_banding_writes_byte_identical_objects_to_the_pre_banding_oracle() -> None:
    """DO NOT DELETE. Every existing lane declares no band; under that default nothing may move."""
    assert latitude_banding(FIRE_DETECTIONS_STREAM) is None
    oracle = _derivation_module_before_banding()
    assert not hasattr(oracle, "latitude_banding"), "the oracle must predate banding, or this pins the tree to itself"
    buckets: dict[str, RecordingBackend] = {}
    for label, derive in (("oracle", oracle.derive_and_write_day_tiers), ("tree", derive_and_write_day_tiers)):
        backend = RecordingBackend()
        store = ObjectStore(backend)
        store.write_partition(
            _fire_detections_base_table(cells=6), layer=FIRE_DETECTIONS_STREAM, kind=KIND, zoom=BASE_ZOOM_TIER, day=DAY
        )
        result = derive(store, layer=FIRE_DETECTIONS_STREAM, kind=KIND, day=DAY, run_id=RUN_ID, now=_now)
        assert {report.tier for report in result.tiers} == set(DERIVED_ZOOM_TIERS)
        buckets[label] = backend

    assert buckets["oracle"].objects.keys() == buckets["tree"].objects.keys()
    for key, payload in buckets["oracle"].objects.items():
        assert buckets["tree"].objects[key] == payload, f"{key} differs from the pre-banding oracle"
    for tier in DERIVED_ZOOM_TIERS:
        oracle_rung = _read_rung(ObjectStore(buckets["oracle"]), FIRE_DETECTIONS_STREAM, tier)
        tree_rung = _read_rung(ObjectStore(buckets["tree"]), FIRE_DETECTIONS_STREAM, tier)
        assert oracle_rung.equals(tree_rung)


# --- Exactness of the fold ---------------------------------------------------------------------------


def test_banded_rungs_equal_the_whole_day_derivation_frame_for_frame(banded_lane: str) -> None:
    base = _straddling_lattice()
    banded_store = ObjectStore(RecordingBackend())
    written = write_banded_base_day(
        banded_store, base, layer=banded_lane, kind=KIND, day=DAY, rows_per_part=SMALL_PART_ROWS
    )
    twin_store = ObjectStore(RecordingBackend())
    twin_store.write_partition(base.to_arrow(), layer=UNBANDED_TWIN_STREAM, kind=KIND, zoom=BASE_ZOOM_TIER, day=DAY)

    banded = _derive_banded_day(banded_store, banded_lane, written)
    whole = derive_and_write_day_tiers(
        twin_store, layer=UNBANDED_TWIN_STREAM, kind=KIND, day=DAY, run_id=RUN_ID, now=_now
    )

    assert len(written.receipts) > len(STRADDLING_BANDS), "several parts per band, or the read-back proves nothing"
    assert banded.emptied == ()
    assert whole.emptied == ()
    for tier in DERIVED_ZOOM_TIERS:
        from_bands = _read_rung(banded_store, banded_lane, tier)
        from_whole_day = _read_rung(twin_store, UNBANDED_TWIN_STREAM, tier)
        assert from_bands.equals(from_whole_day), f"z{tier} differs between the banded and whole-day paths"
    _assert_banded_equals_whole_day(banded_store, banded_lane, base)
    # Nothing was lost or double-counted across the band edges: the pixel total is invariant up the ladder.
    total = base["pixel_count"].sum()
    for tier in DERIVED_ZOOM_TIERS:
        assert _read_rung(banded_store, banded_lane, tier)["pixel_count"].sum() == total


def test_a_band_edge_at_a_z5_edge_keeps_every_coarse_cell_whole(banded_lane: str) -> None:
    """Cells whose origin is exactly on a band edge (43.0) or one base pitch below it land in one band each."""
    base = _lattice(latitudes=[42.995, 43.0, 43.005, 43.995, 44.0], longitudes=(-116.0,))
    store = ObjectStore(RecordingBackend())
    written = write_banded_base_day(store, base, layer=banded_lane, kind=KIND, day=DAY, rows_per_part=EDGE_PART_ROWS)

    _derive_banded_day(store, banded_lane, written)

    _assert_banded_equals_whole_day(store, banded_lane, base)
    z5 = _read_rung(store, banded_lane, 5)
    # 42.995 floors to the 42.8 z5 cell (band 42); 43.0 and 43.005 to 43.0 (band 43); 43.995 to 43.8; 44.0 to 44.0.
    assert sorted(z5["cell_lat"].unique().to_list()) == pytest.approx([42.8, 43.0, 43.8, 44.0])


RECOMMENDED_PAIRS: Final = [(0.4, 0.0025), (0.2, 0.01), (1.0, 0.005), (2.0, 0.005)]


@pytest.mark.parametrize(("band_height", "base_pitch"), RECOMMENDED_PAIRS)
def test_every_cell_lies_in_exactly_one_band_across_the_envelope_and_the_fold_is_exact(
    band_height: float, base_pitch: float
) -> None:
    """The property behind the fold, on the full 24-50 N lattice for every pair ROW-CAP section 4 recommends.

    Band membership is exact integer arithmetic on the lattice and, since `floor_to_resolution` snaps
    lattice origins (`FLOOR_SNAP_TOLERANCE`), so is the platform's flooring of every rung -- so NO z9
    or z5 cell holds rows from two bands anywhere in the envelope, and that is asserted. Exactness is
    then the associativity of the aggregates: written band-major and derived band by band, every rung
    equals the whole-day derivation.
    """
    banding = LatitudeBanding.of_height(band_height, base_pitch)
    slug = f"h{band_height}-b{base_pitch}".replace(".", "")
    stream = _register_vegetation_like_lane(f"test-banded-envelope-{slug}", banding)
    base = _lattice(latitudes=_lattice_latitudes(ENVELOPE_SOUTH, ENVELOPE_NORTH, base_pitch), longitudes=(-116.0,))
    cells = base.with_columns(
        banding.band_index_expression("cell_lat").alias("band"),
        floor_to_resolution(pl.col("cell_lat"), Z9_PITCH).alias("z9_origin"),
        floor_to_resolution(pl.col("cell_lat"), Z5_PITCH).alias("z5_origin"),
    )
    split_cells = {
        origin: cells.group_by(origin).agg(pl.col("band").n_unique().alias("bands")).filter(pl.col("bands") > 1).height
        for origin in ("z9_origin", "z5_origin")
    }

    store = ObjectStore(RecordingBackend())
    written = write_banded_base_day(store, base, layer=stream, kind=KIND, day=DAY, rows_per_part=ENVELOPE_PART_ROWS)
    _derive_banded_day(store, stream, written)

    assert sum(receipt.row_count for receipt in written.receipts) == base.height
    _assert_banded_equals_whole_day(store, stream, base)
    assert split_cells == {"z9_origin": 0, "z5_origin": 0}, "a snapped floor puts every origin cell in one band"


@pytest.mark.parametrize("base_pitch", RECOMMENDED_BASES)
def test_membership_agrees_between_a_one_row_frame_and_a_bulk_frame_for_every_envelope_latitude(
    base_pitch: float,
) -> None:
    """Polars evaluates `col / c` by different paths for 1-row and N-row frames; the membership rule must not care.

    `floor(lat / 0.2)` disagreed on 46 envelope latitudes at 0.0025 deg (24.2, 24.4, 25.2, ...), every
    one a z5 edge, and a 1-row part holding one of them recorded a range the bulk filter then excluded.
    """
    banding = LatitudeBanding.of_height(0.4, base_pitch)
    envelope = _lattice_latitudes(ENVELOPE_SOUTH, ENVELOPE_NORTH, base_pitch)
    # The only latitudes a division path can move across a z5 cell are the ones AT a z5 edge (and the
    # origin just below it); every other origin sits a whole base cell inside its z5 cell. Those plus a
    # stride sample of the rest keep the loop honest and the file fast.
    edge_indexes = {index for index, lat in enumerate(envelope) if round(lat / base_pitch) % banding.cells_per_z5 == 0}
    below_edges = {index - 1 for index in edge_indexes if index > 0}
    chosen = sorted(edge_indexes | below_edges | set(range(0, len(envelope), 97)))
    latitudes = [envelope[index] for index in chosen]
    bulk = pl.DataFrame({"lat": latitudes}).select(banding.z5_cell_index_expression("lat"))["lat"].to_list()
    one_at_a_time = [
        pl.DataFrame({"lat": [latitude]}).select(banding.z5_cell_index_expression("lat")).item()
        for latitude in latitudes
    ]
    assert len(edge_indexes) > 100, "every z5 edge in the envelope is exercised"  # noqa: PLR2004 - ~130 edges

    disagreements = [(lat, b, o) for lat, b, o in zip(latitudes, bulk, one_at_a_time, strict=True) if b != o]
    assert disagreements == []
    # And the cell is the exact-arithmetic one: a lattice origin `k * base` is in z5 cell `k // cells_per_z5`.
    exact = [round(latitude / base_pitch) // banding.cells_per_z5 for latitude in latitudes]
    assert bulk == exact


def _two_pieces_of_one_z9_cell(*, class_system_by_piece: tuple[str, str] = ("LF2025", "LF2025")) -> pl.DataFrame:
    """Two band pieces that both derived the same z9 grain row, as `_assemble_rungs` concatenates them."""
    rows = []
    for piece, (class_system, pixels) in enumerate(zip(class_system_by_piece, (5, 7), strict=True)):
        rows.append(
            {
                "cell_lon": -116.0,
                "cell_lat": 32.79,
                "class_system": class_system,
                "evt_code": None,
                "evt_group_code": 645,
                "evt_phys": "Conifer",
                "evt_lifeform": "Tree",
                "pixel_count": pixels + piece,
                "release_day": DAY,
            }
        )
    frame = pl.from_arrow(pa.Table.from_pylist(rows, schema=_vegetation_arrow_schema()))
    assert isinstance(frame, pl.DataFrame)
    return frame


def test_a_cell_two_bands_both_derived_is_merged_exactly_by_the_assembly_defence(banded_lane: str) -> None:
    """Unreachable from an origin lattice now the floor is exact; kept as defence. The merge is the whole-day sum."""
    pieces = _two_pieces_of_one_z9_cell()

    merged = derivation._merge_split_cells(pieces, _vegetation_strategy(), tier=9, layer=banded_lane, day=DAY)

    assert merged.height == 1
    assert merged["pixel_count"].item() == pieces["pixel_count"].sum()
    assert merged.select("cell_lon", "cell_lat", *JOINT_LADDER[9]).is_duplicated().sum() == 0
    assert merged.columns == pieces.columns


def test_a_first_column_that_differs_across_a_split_cell_is_refused_at_the_merge(banded_lane: str) -> None:
    pieces = _two_pieces_of_one_z9_cell(class_system_by_piece=("LF2024", "LF2025"))

    with pytest.raises(NonConstantFirstError, match=r"was derived in two bands and column 'class_system'"):
        derivation._merge_split_cells(pieces, _vegetation_strategy(), tier=9, layer=banded_lane, day=DAY)


ONE_ROW_PART_CASES: Final = [
    (0.4, 0.0025, (27.15, 27.25), 1, (-116.0, -116.005, -116.2, -117.3), "reviewer-shape-four-longitudes"),
    (0.4, 0.0025, (27.2, 27.2), BANDED_BASE_ROWS_PER_PART, (-116.0,), "a-band-holding-one-cell-at-its-opening-edge"),
    (1.0, 0.005, (42.0, 44.0), BANDED_BASE_ROWS_PER_PART, (-116.0,), "integer-height-lone-edge-rows"),
    (0.4, 0.0025, (27.0, 27.2), 80, (-116.0,), "edge-row-lands-alone-in-a-tail-part"),
]


@pytest.mark.parametrize(
    ("band_height", "base_pitch", "span", "rows_per_part", "longitudes", "label"), ONE_ROW_PART_CASES
)
def test_the_reviewers_one_row_part_cases_are_exact(  # noqa: PLR0913 - one coordinate of the reproducer per arg
    band_height: float,
    base_pitch: float,
    span: tuple[float, float],
    rows_per_part: int,
    longitudes: tuple[float, ...],
    label: str,
) -> None:
    """Part ranges recorded one row at a time and rows banded in bulk must agree: no lone edge row may be lost."""
    banding = LatitudeBanding.of_height(band_height, base_pitch)
    stream = _register_vegetation_like_lane(f"test-banded-one-row-{label}", banding)
    latitudes = _lattice_latitudes(span[0], span[1], base_pitch) if span[0] != span[1] else [span[0]]
    base = _lattice(latitudes=latitudes, longitudes=longitudes)
    store = ObjectStore(RecordingBackend())
    written = write_banded_base_day(store, base, layer=stream, kind=KIND, day=DAY, rows_per_part=rows_per_part)

    result = _derive_banded_day(store, stream, written)

    assert result.emptied == ()
    _assert_banded_equals_whole_day(store, stream, base)


def _single_row_band_fixture() -> tuple[str, pl.DataFrame, ObjectStore, BandedBaseWrite]:
    """32.7975 and 32.8, ONE class row each and one row per part: the row at 32.8 is alone in band 82."""
    banding = LatitudeBanding.of_height(0.4, 0.0025)
    stream = _register_vegetation_like_lane("test-banded-single-row-band", banding)
    lattice = _lattice(latitudes=list(SPLIT_CELL_LATITUDES), longitudes=(-116.0,))
    base = lattice.filter(pl.col("evt_code") == LEGEND_CONIFER)
    assert base.height == len(SPLIT_CELL_LATITUDES), "one row per cell, so every part is a 1-row part"
    store = ObjectStore(RecordingBackend())
    written = write_banded_base_day(store, base, layer=stream, kind=KIND, day=DAY, rows_per_part=1)
    recorded = sorted((part.z5_cell_min, part.z5_cell_max) for part in written.part_cell_ranges.values())
    assert recorded == [(163, 163), (164, 164)], "the 1-row part holding 32.8 records z5 cell 164, not 163"
    return stream, base, store, written


def test_a_row_alone_in_its_band_is_derived_not_lost() -> None:
    """The row-loss half of the reviewer's 1-row-part reproducer: 32.8 lands in band 82 and reaches every rung."""
    stream, base, store, written = _single_row_band_fixture()

    result = _derive_banded_day(store, stream, written)

    assert result.emptied == ()
    total = base["pixel_count"].sum()
    for tier in DERIVED_ZOOM_TIERS:
        assert _read_rung(store, stream, tier)["pixel_count"].sum() == total, f"z{tier} lost pixels"
    assert _read_rung(store, stream, 9).equals(derive_tier(base, stream=stream, tier=9))
    assert _read_rung(store, stream, 0).equals(derive_tier(base, stream=stream, tier=0))


def test_a_row_alone_in_its_band_gets_the_same_z5_origin_as_the_whole_day() -> None:
    """Once `floor_to_resolution` snapped lattice origins, a 1-row band frame and the whole day agree at z5 too."""
    stream, base, store, written = _single_row_band_fixture()

    _derive_banded_day(store, stream, written)

    assert _read_rung(store, stream, 5).equals(derive_tier(base, stream=stream, tier=5))


REVIEWER_REPRODUCERS: Final = [
    (0.4, 0.0025, 24.0, 24.4, DERIVED_PART_ROWS),
    (0.4, 0.0025, 27.0, 27.2, DERIVED_PART_ROWS),
    (0.4, 0.0025, 27.0, 27.6, 1),
    (0.2, 0.005, 26.0, 26.2, DERIVED_PART_ROWS),
]


@pytest.mark.parametrize(("band_height", "base_pitch", "low", "high", "rows_per_part"), REVIEWER_REPRODUCERS)
def test_the_reviewers_row_loss_reproducers_are_exact(
    band_height: float, base_pitch: float, low: float, high: float, rows_per_part: int
) -> None:
    """Under the float rule these lost rows at the extent's top edge and at band edges; pinned as regressions."""
    slug = f"h{band_height}-l{low}-{high}-r{rows_per_part}".replace(".", "")
    stream = _register_vegetation_like_lane(
        f"test-banded-repro-{slug}", LatitudeBanding.of_height(band_height, base_pitch)
    )
    base = _lattice(latitudes=_lattice_latitudes(low, high, base_pitch), longitudes=(-116.0,))
    store = ObjectStore(RecordingBackend())
    written = write_banded_base_day(store, base, layer=stream, kind=KIND, day=DAY, rows_per_part=rows_per_part)

    _derive_banded_day(store, stream, written)

    _assert_banded_equals_whole_day(store, stream, base)


def test_z0_is_derived_from_the_z5_rung_never_from_the_base(banded_lane: str, monkeypatch: pytest.MonkeyPatch) -> None:
    base = _straddling_lattice()
    store = ObjectStore(RecordingBackend())
    written = write_banded_base_day(store, base, layer=banded_lane, kind=KIND, day=DAY, rows_per_part=SMALL_PART_ROWS)
    calls: list[tuple[ZoomTier, int]] = []

    def recording_derive_tier(table: pl.DataFrame, **kwargs: object) -> pl.DataFrame:
        calls.append((kwargs["tier"], table.height))  # type: ignore[arg-type]
        return derive_tier(table, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(derivation, "derive_tier", recording_derive_tier)

    _derive_banded_day(store, banded_lane, written)

    z0_calls = [height for tier, height in calls if tier == 0]
    z5_total = _read_rung(store, banded_lane, 5).height
    assert z0_calls == [z5_total], "z0 must be derived exactly once, from the whole written z5 rung"
    assert all(height < base.height for _, height in calls), "no derive_tier call ever held the whole base day"
    assert {tier for tier, _ in calls if tier != 0} == {9, 5}
    assert _read_rung(store, banded_lane, 0).equals(derive_tier(base, stream=banded_lane, tier=0))


def test_a_base_table_handed_in_is_banded_by_filtering_never_by_re_reading(banded_lane: str) -> None:
    base = _straddling_lattice()
    backend = RecordingBackend()
    store = ObjectStore(backend)
    write_banded_base_day(store, base, layer=banded_lane, kind=KIND, day=DAY, rows_per_part=SMALL_PART_ROWS)
    fetched = _recording_get(backend)

    result = derive_and_write_day_tiers(
        store, layer=banded_lane, kind=KIND, day=DAY, run_id=RUN_ID, now=_now, base_table=base.to_arrow()
    )

    assert not _parquet_keys(fetched), "the repair path already holds the day"
    assert {report.tier for report in result.tiers} == set(DERIVED_ZOOM_TIERS)
    _assert_banded_equals_whole_day(store, banded_lane, base)


# --- Emptiness and unlocated rows across bands -----------------------------------------------------------


def test_an_empty_middle_band_does_not_retract_a_rung_other_bands_filled(banded_lane: str) -> None:
    """Rows in band 42 and band 44 with nothing in 43: every rung is written, none is retracted."""
    base = _lattice(latitudes=[42.1, 42.105, 44.5, 44.505], longitudes=(-116.0, -116.2))
    store = ObjectStore(RecordingBackend())
    written = write_banded_base_day(store, base, layer=banded_lane, kind=KIND, day=DAY, rows_per_part=EDGE_PART_ROWS)

    result = _derive_banded_day(store, banded_lane, written)

    assert result.emptied == ()
    assert result.notes == ()
    assert {report.tier for report in result.tiers} == set(DERIVED_ZOOM_TIERS)
    _assert_banded_equals_whole_day(store, banded_lane, base)


def test_a_rung_is_retracted_only_when_every_band_left_it_empty(banded_lane: str) -> None:
    """A base day of unlocated rows only (null latitude) is in no band, so every rung empties and is retracted."""
    located = _lattice(latitudes=[42.1], longitudes=(-116.0,))
    unlocated = located.with_columns(pl.lit(None, dtype=pl.Float64).alias("cell_lat"))
    store = ObjectStore(RecordingBackend())
    written = write_banded_base_day(store, unlocated, layer=banded_lane, kind=KIND, day=DAY)
    assert all(part.z5_cell_min is None for part in written.part_cell_ranges.values())

    result = _derive_banded_day(store, banded_lane, written)

    assert set(result.emptied) == set(DERIVED_ZOOM_TIERS)
    assert result.tiers == ()


def _with_nan_latitude_at(frame: pl.DataFrame, latitude: float) -> pl.DataFrame:
    return frame.with_columns(
        pl.when(pl.col("cell_lat") == latitude).then(float("nan")).otherwise(pl.col("cell_lat")).alias("cell_lat")
    )


def test_a_nan_latitude_is_unlocated_on_the_derive_path_not_a_raw_polars_error(banded_lane: str) -> None:
    base = _with_nan_latitude_at(_lattice(latitudes=[42.1, 42.105], longitudes=(-116.0,)), 42.1)
    store = ObjectStore(RecordingBackend())
    store.write_partition(base.to_arrow(), layer=banded_lane, kind=KIND, zoom=BASE_ZOOM_TIER, day=DAY)

    result = derive_and_write_day_tiers(store, layer=banded_lane, kind=KIND, day=DAY, run_id=RUN_ID, now=_now)

    assert result.emptied == ()
    located_only = base.with_columns(pl.col("cell_lat").fill_nan(None))
    _assert_banded_equals_whole_day(store, banded_lane, located_only)


def test_a_nan_latitude_is_written_last_with_no_recorded_range_by_the_band_major_writer(banded_lane: str) -> None:
    base = _with_nan_latitude_at(_lattice(latitudes=[42.1, 42.105], longitudes=(-116.0,)), 42.1)
    store = ObjectStore(RecordingBackend())

    written = write_banded_base_day(store, base, layer=banded_lane, kind=KIND, day=DAY, rows_per_part=EDGE_PART_ROWS)

    assert sum(receipt.row_count for receipt in written.receipts) == base.height
    *located, unlocated = written.receipts
    ranges = written.part_cell_ranges
    assert all(ranges[receipt.relative_path].z5_cell_min is not None for receipt in located)
    assert (ranges[unlocated.relative_path].z5_cell_min, ranges[unlocated.relative_path].z5_cell_max) == (None, None)
    assert unlocated.row_count == base.filter(pl.col("cell_lat").is_nan()).height
    assert ranges[unlocated.relative_path].sha256 == unlocated.sha256


# --- Refusals ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("band_height", [0.3, 0.5, 0.1])
def test_a_band_height_that_is_not_a_whole_number_of_z5_cells_is_refused(band_height: float) -> None:
    with pytest.raises(LatitudeBandingError, match=r"not a multiple of 0\.2 deg.*flooring composes exactly"):
        LatitudeBanding.of_height(band_height, BASE_RESOLUTION)


def test_a_base_pitch_that_does_not_divide_the_z5_pitch_is_refused() -> None:
    with pytest.raises(LatitudeBandingError, match=r"base rung pitch 0\.007 deg does not divide the z5 pitch 0\.2"):
        LatitudeBanding(z5_cells_per_band=ONE_DEGREE_IN_Z5_CELLS, base_resolution_degrees=0.007)


@pytest.mark.parametrize("cells", [0, 0.4, 1.0])
def test_a_band_that_is_not_a_positive_whole_number_of_cells_is_refused_pointing_at_of_height(cells: float) -> None:
    with pytest.raises(LatitudeBandingError, match=r"whole number of z5 cells, at least one.*of_height"):
        LatitudeBanding(z5_cells_per_band=cells, base_resolution_degrees=BASE_RESOLUTION)  # type: ignore[arg-type]


@pytest.mark.parametrize(("band_height", "base_pitch"), RECOMMENDED_PAIRS)
def test_the_recommended_heights_are_lawful_and_edges_belong_to_the_band_they_open(
    band_height: float, base_pitch: float
) -> None:
    banding = LatitudeBanding.of_height(band_height, base_pitch)
    assert banding.band_height_degrees == pytest.approx(band_height)
    for band in (banding.band_of_cell(120), banding.band_of_cell(214), banding.band_of_cell(250)):
        first, last = banding.cell_interval(band)
        assert banding.band_of_cell(first) == band
        assert banding.band_of_cell(last) == band
        assert banding.band_of_cell(last + 1) == band + 1


@pytest.mark.parametrize("how", ["mean", "sha256-lines"])
def test_a_non_associative_aggregate_is_refused_in_banded_mode_by_column_name(how: str) -> None:
    stream = f"test-banded-{how.replace('-', '')}-lane"
    register_tier_derivation(
        TierDerivation(
            stream=stream,
            strategy=GridAggregation(
                longitude_column="cell_lon",
                latitude_column="cell_lat",
                key_columns=("signal_name",),
                aggregations=(ColumnAggregation("total", "sum"), ColumnAggregation("temperature", how)),  # type: ignore[arg-type]
            ),
        )
    )

    with pytest.raises(LatitudeBandingError, match=rf"'temperature' aggregates '{how}'.*not associative across bands"):
        register_latitude_banding(stream, BANDING)
    assert latitude_banding(stream) is None
    assert how not in BAND_SAFE_AGGREGATES


def test_a_lane_without_a_grid_strategy_cannot_be_banded() -> None:
    stream = "test-banded-passthrough-lane"
    register_tier_derivation(TierDerivation(stream=stream, strategy=TierPassthrough()))

    with pytest.raises(LatitudeBandingError, match=r"only a GridAggregation lane can be folded.*TierPassthrough"):
        register_latitude_banding(stream, BANDING)


def test_redeclaring_a_different_band_is_refused_and_the_same_band_is_a_no_op(banded_lane: str) -> None:
    assert register_latitude_banding(banded_lane, BANDING) == BANDING
    with pytest.raises(LatitudeBandingError, match=r"already declares a different latitude banding"):
        register_latitude_banding(banded_lane, LatitudeBanding.of_height(2.0, BASE_RESOLUTION))


def test_a_band_over_the_per_call_cap_is_refused_naming_the_band(
    banded_lane: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = _straddling_lattice()
    store = ObjectStore(RecordingBackend())
    written = write_banded_base_day(store, base, layer=banded_lane, kind=KIND, day=DAY, rows_per_part=SMALL_PART_ROWS)
    band_42_rows = base.filter(BANDING.band_index_expression("cell_lat") == BAND_42).height
    assert band_42_rows < base.height, "the cap must be one a whole-day read would trip but a band alone need not"
    monkeypatch.setattr(derivation, "MAX_DERIVATION_ROWS", band_42_rows - 1)

    with pytest.raises(TierWriteError, match=r"band 42 \(z5 cells 210\.\.214, latitudes \[42, 43\)\) holds .* over"):
        _derive_banded_day(store, banded_lane, written)


# --- `first` is band-safe only under the constancy contract, and the fold enforces it -----------------------


def test_a_first_column_that_varies_within_a_group_is_refused_naming_column_and_group(banded_lane: str) -> None:
    base = _straddling_lattice().with_columns(
        (pl.lit("V") + pl.int_range(pl.len()).cast(pl.String)).alias("class_system")
    )
    store = ObjectStore(RecordingBackend())
    written = write_banded_base_day(store, base, layer=banded_lane, kind=KIND, day=DAY, rows_per_part=SMALL_PART_ROWS)

    with pytest.raises(NonConstantFirstError, match=r"column 'class_system' aggregates `first` but holds \d+ distinct"):
        _derive_banded_day(store, banded_lane, written)


def test_a_first_column_constant_per_band_but_varying_across_a_z0_cell_is_refused_at_the_chain(
    banded_lane: str,
) -> None:
    """Constant within every z9 and z5 group, different between band 42 and band 43: z0 from z5 must refuse."""
    base = _straddling_lattice().with_columns(
        pl.when(pl.col("cell_lat") < BAND_43_EDGE_LATITUDE)
        .then(pl.lit("LF2024"))
        .otherwise(pl.lit("LF2025"))
        .alias("class_system")
    )
    store = ObjectStore(RecordingBackend())
    written = write_banded_base_day(store, base, layer=banded_lane, kind=KIND, day=DAY, rows_per_part=SMALL_PART_ROWS)

    with pytest.raises(NonConstantFirstError, match=r"z0 .*from the z5 rung: column 'class_system'"):
        _derive_banded_day(store, banded_lane, written)
    assert derive_tier(base, stream=banded_lane, tier=0)["class_system"].n_unique() == 1, (
        "the whole-day derivation would have silently picked one vintage for the 5 deg cell"
    )


# --- The read-back -----------------------------------------------------------------------------------------------


def test_the_band_major_base_writer_keeps_every_part_inside_one_band(banded_lane: str) -> None:
    base = _straddling_lattice()
    store = ObjectStore(RecordingBackend())

    written = write_banded_base_day(store, base, layer=banded_lane, kind=KIND, day=DAY, rows_per_part=SMALL_PART_ROWS)

    receipts = written.receipts
    assert [receipt.relative_path for receipt in receipts] == [
        partition_path(banded_lane, KIND, BASE_ZOOM_TIER, DAY, index) for index in range(len(receipts))
    ], "parts are numbered contiguously from 0, which the prune and the completion marker both assume"
    assert sum(receipt.row_count for receipt in receipts) == base.height
    assert set(written.part_cell_ranges) == {receipt.relative_path for receipt in receipts}
    bands_seen: list[int] = []
    for receipt in receipts:
        recorded = written.part_cell_ranges[receipt.relative_path]
        assert recorded.sha256 == receipt.sha256
        assert recorded.z5_cell_min is not None
        assert recorded.z5_cell_max is not None
        assert receipt.row_count <= SMALL_PART_ROWS
        low_band, high_band = BANDING.band_of_cell(recorded.z5_cell_min), BANDING.band_of_cell(recorded.z5_cell_max)
        assert low_band == high_band, f"{receipt.relative_path} straddles bands {low_band} and {high_band}"
        bands_seen.append(low_band)
    assert bands_seen == sorted(bands_seen), "band-major: a band is a contiguous part range"
    assert set(bands_seen) == STRADDLING_BANDS
    expected_parts = sum(
        required_part_count(base.filter(BANDING.band_index_expression("cell_lat") == band).height, SMALL_PART_ROWS)
        for band in STRADDLING_BANDS
    )
    assert len(receipts) == expected_parts


def test_the_read_back_for_a_band_fetches_no_part_outside_it(banded_lane: str) -> None:
    base = _straddling_lattice()
    backend = RecordingBackend()
    store = ObjectStore(backend)
    written = write_banded_base_day(store, base, layer=banded_lane, kind=KIND, day=DAY, rows_per_part=SMALL_PART_ROWS)
    fetched = _recording_get(backend)
    in_band_43 = {
        store.key_for(path)
        for path, part in written.part_cell_ranges.items()
        if part.z5_cell_min is not None and BANDING.band_of_cell(part.z5_cell_min) == BAND_43
    }
    assert BANDING.cell_interval(BAND_43) == BAND_43_CELLS

    read = store.read_partition_with_receipts(
        banded_lane, KIND, BASE_ZOOM_TIER, DAY, part_selector=BANDING.part_selector(BAND_43, written.part_cell_ranges)
    )

    assert _parquet_keys(fetched) == in_band_43, "a part outside the band was downloaded"
    assert 0 < len(in_band_43) < len(written.receipts)
    in_band_rows = base.filter(BANDING.band_index_expression("cell_lat") == BAND_43).height
    assert read.table.num_rows == in_band_rows
    assert {receipt.relative_path for receipt in read.parts} == {store.relative_key(key) for key in in_band_43}


def test_the_banded_fold_fetches_every_part_exactly_once(banded_lane: str) -> None:
    """Parts are cut at band edges, so across all bands each part is downloaded once and none twice or never."""
    base = _straddling_lattice()
    backend = RecordingBackend()
    store = ObjectStore(backend)
    written = write_banded_base_day(store, base, layer=banded_lane, kind=KIND, day=DAY, rows_per_part=SMALL_PART_ROWS)
    fetched = _recording_get(backend)

    _derive_banded_day(store, banded_lane, written)

    base_keys = {receipt.key for receipt in written.receipts}
    base_fetches = Counter(key for key in fetched if key in base_keys)
    assert set(base_fetches) == base_keys
    assert set(base_fetches.values()) == {1}


def test_a_part_with_no_recorded_range_is_selected_for_every_band() -> None:
    ranges = {"part-0.parquet": PartCellRange(sha256="a", z5_cell_min=None, z5_cell_max=None)}
    for band in (BAND_42, BAND_43, 0, -7):
        assert BANDING.part_selector(band, ranges)("part-0.parquet")
        assert BANDING.part_selector(band, ranges)("part-9.parquet"), "a part the ranges do not name is selected too"


def test_a_small_day_without_ranges_in_hand_is_read_whole_and_still_derives_exactly(banded_lane: str) -> None:
    """Fail-open: no ranges (another process wrote the parts) on a small day reads it whole and bands by filtering."""
    base = _straddling_lattice()
    backend = RecordingBackend()
    write_banded_base_day(
        ObjectStore(backend), base, layer=banded_lane, kind=KIND, day=DAY, rows_per_part=LARGE_PART_ROWS
    )
    part_keys = _parquet_keys(list(backend.objects))
    assert len(part_keys) * BANDED_BASE_ROWS_PER_PART <= derivation.MAX_DERIVATION_ROWS, "small enough to read whole"
    other_process = ObjectStore(backend)
    fetched = _recording_get(backend)

    result = derive_and_write_day_tiers(other_process, layer=banded_lane, kind=KIND, day=DAY, run_id=RUN_ID, now=_now)

    assert _parquet_keys(fetched) >= part_keys, "no ranges in hand: every base part is read"
    assert {report.tier for report in result.tiers} == set(DERIVED_ZOOM_TIERS)
    _assert_banded_equals_whole_day(other_process, banded_lane, base)


def test_a_large_day_without_ranges_in_hand_is_refused_by_name_instead_of_read_whole(banded_lane: str) -> None:
    """When the part count says the day may exceed the cap and no ranges are in hand, the fold must not try."""
    base = _straddling_lattice()
    backend = RecordingBackend()
    too_many_parts_for_the_cap = derivation.MAX_DERIVATION_ROWS // BANDED_BASE_ROWS_PER_PART + 1
    rows_per_part = base.height // too_many_parts_for_the_cap
    write_banded_base_day(
        ObjectStore(backend), base, layer=banded_lane, kind=KIND, day=DAY, rows_per_part=rows_per_part
    )
    other_process = ObjectStore(backend)
    fetched = _recording_get(backend)

    refusal = rf"{banded_lane} {DAY.isoformat()}: the day's \d+ base parts have no recorded z5 cell ranges in hand"
    with pytest.raises(UnknownPartBoundsError, match=refusal):
        derive_and_write_day_tiers(other_process, layer=banded_lane, kind=KIND, day=DAY, run_id=RUN_ID, now=_now)
    assert not _parquet_keys(fetched), "the refusal must come before any part is downloaded"
    # The same day handed in as `base_table` needs no ranges at all.
    result = derive_and_write_day_tiers(
        other_process, layer=banded_lane, kind=KIND, day=DAY, run_id=RUN_ID, now=_now, base_table=base
    )
    assert {report.tier for report in result.tiers} == set(DERIVED_ZOOM_TIERS)


def test_ranges_that_do_not_name_every_listed_part_make_the_extent_unknown(banded_lane: str) -> None:
    """A re-export grew the day: the old ranges miss the new part, so no band is enumerated from them -- the day is
    read whole, the parts the old ranges DO name are digest-checked, and the rewritten one is refused by name."""
    base = _straddling_lattice()
    backend = RecordingBackend()
    store = ObjectStore(backend)
    first = write_banded_base_day(store, base, layer=banded_lane, kind=KIND, day=DAY, rows_per_part=LARGE_PART_ROWS)
    grown = _lattice(latitudes=_lattice_latitudes(42.0, 45.0))
    second = write_banded_base_day(store, grown, layer=banded_lane, kind=KIND, day=DAY, rows_per_part=LARGE_PART_ROWS)
    assert len(second.receipts) > len(first.receipts)
    fetched = _recording_get(backend)

    with pytest.raises(TierWriteError, match=r"was rewritten since its z5 cell range was recorded"):
        derive_and_write_day_tiers(
            store,
            layer=banded_lane,
            kind=KIND,
            day=DAY,
            run_id=RUN_ID,
            now=_now,
            part_cell_ranges=first.part_cell_ranges,
        )

    assert _parquet_keys(fetched) == {receipt.key for receipt in second.receipts}, "unknown extent: read whole"
    result = _derive_banded_day(store, banded_lane, second)
    assert result.emptied == ()
    _assert_banded_equals_whole_day(store, banded_lane, grown)


def test_ranges_from_before_another_process_rewrote_the_day_in_place_are_refused_not_trusted(banded_lane: str) -> None:
    """The stale-memo hazard, now explicit: same keys, different bytes -> a digest mismatch refusal, not empty rungs."""
    backend = RecordingBackend()
    first_export = _lattice(latitudes=_lattice_latitudes(42.0, 43.995))
    second_export = _lattice(latitudes=_lattice_latitudes(44.0, 45.995))
    stale = write_banded_base_day(
        ObjectStore(backend), first_export, layer=banded_lane, kind=KIND, day=DAY, rows_per_part=LARGE_PART_ROWS
    )
    fresh = write_banded_base_day(
        ObjectStore(backend), second_export, layer=banded_lane, kind=KIND, day=DAY, rows_per_part=LARGE_PART_ROWS
    )
    assert set(stale.part_cell_ranges) == set(fresh.part_cell_ranges), "same keys: a path-keyed cache would be fooled"
    store = ObjectStore(backend)

    with pytest.raises(TierWriteError, match=r"was rewritten since its z5 cell range was recorded"):
        derive_and_write_day_tiers(
            store,
            layer=banded_lane,
            kind=KIND,
            day=DAY,
            run_id=RUN_ID,
            now=_now,
            part_cell_ranges=stale.part_cell_ranges,
        )
    for tier in DERIVED_ZOOM_TIERS:
        assert not store.partition_exists(banded_lane, KIND, tier, DAY), "nothing was written or retracted"
    result = _derive_banded_day(store, banded_lane, fresh)
    assert result.emptied == ()
    _assert_banded_equals_whole_day(store, banded_lane, second_export)


def test_stale_all_unlocated_ranges_over_a_relocated_rewrite_are_refused_not_retracted(banded_lane: str) -> None:
    """Export 1 was all unlocated (every range None); the same key was rewritten with located rows. Zero bands would
    have fetched nothing, skipped the digest check and retracted every rung; the whole read catches the rewrite."""
    backend = RecordingBackend()
    located = _lattice(latitudes=[42.1, 42.105], longitudes=(-116.0,))
    unlocated = located.with_columns(pl.lit(None, dtype=pl.Float64).alias("cell_lat"))
    stale = write_banded_base_day(ObjectStore(backend), unlocated, layer=banded_lane, kind=KIND, day=DAY)
    fresh = write_banded_base_day(ObjectStore(backend), located, layer=banded_lane, kind=KIND, day=DAY)
    assert set(stale.part_cell_ranges) == set(fresh.part_cell_ranges)
    assert all(part.z5_cell_min is None for part in stale.part_cell_ranges.values())
    store = ObjectStore(backend)

    with pytest.raises(TierWriteError, match=r"was rewritten since its z5 cell range was recorded"):
        _derive_banded_day(store, banded_lane, stale)
    for tier in DERIVED_ZOOM_TIERS:
        assert not store.partition_exists(banded_lane, KIND, tier, DAY)
        assert store.read_completion_marker(banded_lane, KIND, tier, DAY) is None, "no rung was retracted as empty"
    result = _derive_banded_day(store, banded_lane, fresh)
    assert result.emptied == ()
    _assert_banded_equals_whole_day(store, banded_lane, located)


def test_all_unlocated_ranges_that_still_match_the_bytes_read_the_day_whole_and_retract_honestly(
    banded_lane: str,
) -> None:
    backend = RecordingBackend()
    unlocated = _lattice(latitudes=[42.1], longitudes=(-116.0,)).with_columns(
        pl.lit(None, dtype=pl.Float64).alias("cell_lat")
    )
    written = write_banded_base_day(ObjectStore(backend), unlocated, layer=banded_lane, kind=KIND, day=DAY)
    store = ObjectStore(backend)
    fetched = _recording_get(backend)

    result = _derive_banded_day(store, banded_lane, written)

    assert _parquet_keys(fetched) == {receipt.key for receipt in written.receipts}, "read whole, digest-checked"
    assert set(result.emptied) == set(DERIVED_ZOOM_TIERS)


def test_the_band_major_writer_refuses_centroids_because_membership_rounds_to_an_origin(banded_lane: str) -> None:
    origins = _lattice(latitudes=[42.1, 42.105], longitudes=(-116.0,))
    centroids = origins.with_columns((pl.col("cell_lat") + BASE_RESOLUTION / 2).alias("cell_lat"))
    backend = RecordingBackend()

    with pytest.raises(LatitudeBandingError, match=r"base rows must be lattice origins, not centroids"):
        write_banded_base_day(ObjectStore(backend), centroids, layer=banded_lane, kind=KIND, day=DAY)
    assert backend.objects == {}, "refused before the first put"
    # Origins carrying only float noise pass, and so does an unlocated row.
    noisy = origins.with_columns((pl.col("cell_lat") + 1e-12).alias("cell_lat"))
    write_banded_base_day(ObjectStore(backend), noisy, layer=banded_lane, kind=KIND, day=DAY)


def test_a_centroid_base_table_handed_to_the_fold_is_refused_with_nothing_written(banded_lane: str) -> None:
    """The origins contract holds on the derive path too, not only in the band-major writer."""
    centroids = _lattice(latitudes=[42.1, 42.105], longitudes=(-116.0,)).with_columns(
        (pl.col("cell_lat") + BASE_RESOLUTION / 2).alias("cell_lat")
    )
    backend = RecordingBackend()

    with pytest.raises(LatitudeBandingError, match=r"base rows must be lattice origins, not centroids"):
        derive_and_write_day_tiers(
            ObjectStore(backend), layer=banded_lane, kind=KIND, day=DAY, run_id=RUN_ID, now=_now, base_table=centroids
        )
    assert backend.objects == {}, "refused before any rung was written or retracted"


def test_a_band_that_matches_no_part_reads_as_an_empty_table_not_a_refusal(banded_lane: str) -> None:
    base = _lattice(latitudes=[42.1, 42.105], longitudes=(-116.0,))
    store = ObjectStore(RecordingBackend())
    write_banded_base_day(store, base, layer=banded_lane, kind=KIND, day=DAY)

    nothing = store.read_partition_with_receipts(banded_lane, KIND, BASE_ZOOM_TIER, DAY, part_selector=lambda _: False)
    read = nothing

    assert read.table.num_rows == 0
    assert read.parts == ()
    assert read.table.schema.names == _vegetation_arrow_schema().names


def test_the_cell_index_expression_treats_nan_and_null_as_unlocated() -> None:
    latitudes = pl.DataFrame({"lat": [float("nan"), None, 24.4, 42.0, 49.995, -0.1]})
    cells = latitudes.select(BANDING.z5_cell_index_expression("lat").alias("cell"))["cell"].to_list()
    assert cells[:2] == [None, None]
    assert cells[2:] == [round(lat / BASE_RESOLUTION) // BANDING.cells_per_z5 for lat in (24.4, 42.0, 49.995, -0.1)]


# --- Part sizing ---------------------------------------------------------------------------------------------------


def test_part_sizing_keeps_the_estimated_bases_under_both_ceilings() -> None:
    """ROW-CAP-ANALYSIS section 2.3 / 3.2 arithmetic, pinned: 22.8M rows at 0.005 deg, 65.2M (85.6M top) at 0.0025."""
    assert required_part_count(ROWS_AT_0005, BANDED_BASE_ROWS_PER_PART) == PARTS_AT_0005
    assert required_part_count(ROWS_AT_00025, BANDED_BASE_ROWS_PER_PART) == PARTS_AT_00025
    worst = required_part_count(ROWS_AT_00025_TOP_OF_RANGE, BANDED_BASE_ROWS_PER_PART)
    assert worst == PARTS_AT_00025_TOP_OF_RANGE
    with_band_cuts = worst + BAND_CUTS_AT_00025
    assert with_band_cuts <= MAX_PART_INDEX + 1, "0.0025 deg in 0.4 deg bands: one extra cut per band edge still fits"
    assert with_band_cuts * RECEIPT_BYTES_PER_PART < RECEIPT_CEILING_BYTES
    # Why the derived rungs' 10,000-row part size may NOT be reused for the base at 0.0025 deg.
    at_derived_size = required_part_count(ROWS_AT_00025, DERIVED_PART_ROWS)
    assert at_derived_size == PARTS_AT_00025_IF_DERIVED_SIZE_REUSED
    assert at_derived_size * RECEIPT_BYTES_PER_PART > RECEIPT_CEILING_BYTES


def test_more_parts_than_the_layout_can_number_are_refused() -> None:
    numberable = MAX_PART_INDEX + 1
    assert required_part_count(numberable * DERIVED_PART_ROWS, DERIVED_PART_ROWS) == numberable
    with pytest.raises(ParquetWriteError, match=r"need 10,001 parts, but the layout numbers at most 10,000"):
        required_part_count(numberable * DERIVED_PART_ROWS + 1, DERIVED_PART_ROWS)


def test_the_band_major_writer_refuses_an_unnumberable_day_before_the_first_put(banded_lane: str) -> None:
    base = _lattice(
        latitudes=_lattice_latitudes(42.0, 46.0),
        longitudes=(-116.0, -116.005, -116.01, -116.015, -116.02, -116.025),
    )
    assert base.height > MAX_PART_INDEX + 1
    backend = RecordingBackend()

    with pytest.raises(ParquetWriteError, match=r"but the layout numbers at most 10,000"):
        write_banded_base_day(ObjectStore(backend), base, layer=banded_lane, kind=KIND, day=DAY, rows_per_part=1)
    assert backend.objects == {}


def test_the_band_major_writer_is_only_for_lanes_that_declared_a_band() -> None:
    with pytest.raises(LatitudeBandingError, match=r"declares no latitude banding"):
        write_banded_base_day(
            ObjectStore(RecordingBackend()),
            _fire_detections_base_table(cells=4),
            layer=FIRE_DETECTIONS_STREAM,
            kind=KIND,
            day=DAY,
        )

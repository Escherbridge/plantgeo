"""The resource guards every DuckDB session the tier derivation opens must carry.

WHY THIS FILE EXISTS SEPARATELY FROM `test_tier_derivation.py`. That file tests what a coarse rung
CONTAINS -- the aggregates, the flooring, the dissolve, the column coverage. This one tests what
deriving one COSTS the machine it runs on, which is a different failure and a much worse one: an
unbounded local DuckDB query consumed the host on 2026-08-24, and the query that did it has exactly
the shape `_derive_geometry_tier` builds. A wrong aggregate publishes a wrong number and a reader
catches it; an unguarded session takes down the host that would have caught it.

THE LOAD-BEARING ASSERTION IS SPILLING, NOT THE MEMORY CEILING. A capped session that may still
spill consumes the machine anyway -- it just does it on local disk and slowly, which is harder to
see and harder to stop. DuckDB's default is "90% of available disk space". With
`max_temp_directory_size` at zero the same over-budget query raises in about a second and the drain
records a failed day an operator can act on.

The memory ceiling is asserted as a RANGE rather than a string. DuckDB reports `memory_limit` back
rounded ('1600MB' comes back as '1.4 GiB'), so pinning the exact rendering would test DuckDB's
formatter rather than this module's contract.
"""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING, Final

import duckdb
import polars as pl
import pytest

from agri_data_service.warehouse.parquet.schema import observed_stream_schema
from agri_data_service.warehouse.parquet.tiers import (
    DERIVATION_MEMORY_LIMIT,
    DERIVATION_TEMP_DIRECTORY_SIZE,
    DERIVATION_THREAD_COUNT,
    TierDerivationError,
    derivation_session,
    derive_tier,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

DAY: Final = dt.date(2026, 8, 1)
NOON: Final = dt.datetime(2026, 8, 1, 12, tzinfo=dt.UTC)
# A geometry lane, because the grid lanes never open DuckDB at all -- they coarsen in Polars.
GEOMETRY_STREAM: Final = "watersheds"
GRID_STREAM: Final = "fire-detections"
# The window `DERIVATION_MEMORY_LIMIT` must land inside once DuckDB has rounded its own readback.
MINIMUM_EXPECTED_CEILING_BYTES: Final = 1_000_000_000
MAXIMUM_EXPECTED_CEILING_BYTES: Final = 2_000_000_000

_BYTE_SCALE: Final = {"bytes": 1, "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3, "TiB": 1024**4}


def _setting(connection: duckdb.DuckDBPyConnection, name: str) -> str:
    """Read one DuckDB setting back out of the session, as the session itself reports it."""
    row = connection.execute("SELECT current_setting(?)", [name]).fetchone()
    assert row is not None, f"{name} has no value at all"
    return str(row[0])


def _bytes_of(setting: str) -> float:
    """Turn DuckDB's rendered byte setting ('0 bytes', '1.4 GiB') into a number."""
    amount, _, unit = setting.partition(" ")
    return float(amount) * _BYTE_SCALE[unit.strip()]


@pytest.fixture
def unguarded() -> Iterator[duckdb.DuckDBPyConnection]:
    """A connection deliberately opened with NO caps, standing in for a careless caller's."""
    connection = duckdb.connect(database=":memory:")
    try:
        yield connection
    finally:
        connection.close()


def _watershed_frame() -> pl.DataFrame:
    """One watersheds day: two HUC12s that dissolve into one HUC8 at z5, with real WKB."""
    with derivation_session() as session:
        wkb = session.execute(
            "SELECT ST_AsWKB(ST_GeomFromText('POLYGON((-117 43, -116 43, -116 44, -117 44, -117 43))')), "
            "ST_AsWKB(ST_GeomFromText('POLYGON((-116 43, -115 43, -115 44, -116 44, -116 43))'))"
        ).fetchone()
    assert wkb is not None
    empty = pl.from_arrow(observed_stream_schema(GEOMETRY_STREAM).arrow_schema.empty_table())
    assert isinstance(empty, pl.DataFrame)
    return pl.DataFrame(
        [
            {
                "huc12": code,
                "name": f"basin {code}",
                "areasqkm": 100.0,
                "tohuc": "170501010200",
                "states": "ID",
                "hutype": "S",
                "source": "test",
                "observed_at": NOON,
                "data_available_at": NOON,
                "release_day": DAY,
                "feature_id": code,
                "geom": geometry,
            }
            for code, geometry in (("170501010101", wkb[0]), ("170501010102", wkb[1]))
        ],
        schema=empty.schema,
    )


def _fire_detections_frame() -> pl.DataFrame:
    """One fire-detections day: a grid lane, which coarsens in Polars and opens no session."""
    empty = pl.from_arrow(observed_stream_schema(GRID_STREAM).arrow_schema.empty_table())
    assert isinstance(empty, pl.DataFrame)
    return pl.DataFrame(
        [
            {
                "cell_longitude": -116.0,
                "cell_latitude": 43.0,
                "observed_day": DAY,
                "detection_count": 1,
                "frp_sum": 1.0,
                "frp_observation_count": 1,
                "high_confidence_detection_count": 1,
                "newest_observed_at": NOON,
            }
        ],
        schema=empty.schema,
    )


def test_a_session_this_module_opens_caps_its_memory() -> None:
    with derivation_session() as session:
        ceiling = _bytes_of(_setting(session, "memory_limit"))
    assert MINIMUM_EXPECTED_CEILING_BYTES < ceiling < MAXIMUM_EXPECTED_CEILING_BYTES, (
        f"a derivation session must run under {DERIVATION_MEMORY_LIMIT}, got {ceiling:,.0f} bytes"
    )


def test_a_session_this_module_opens_caps_its_threads() -> None:
    with derivation_session() as session:
        assert int(_setting(session, "threads")) == DERIVATION_THREAD_COUNT


def test_spilling_to_local_disk_is_disabled_outright(unguarded: duckdb.DuckDBPyConnection) -> None:
    """THE LOAD-BEARING ONE. A capped session that may spill still consumes the host, just slowly."""
    assert _setting(unguarded, "max_temp_directory_size") != "0 bytes", "DuckDB's default is not zero"

    with derivation_session() as session:
        assert _bytes_of(_setting(session, "max_temp_directory_size")) == 0.0, (
            f"a derivation may never spill to local disk; expected {DERIVATION_TEMP_DIRECTORY_SIZE}"
        )


def test_a_session_this_module_opens_holds_no_database_file() -> None:
    """`:memory:` and nothing else: a derivation may not leave a database behind or reopen a stale one."""
    with derivation_session() as session:
        attached = session.execute("SELECT database_name, path FROM duckdb_databases()").fetchall()
    assert "memory" in {name for name, _ in attached}, attached
    assert all(not path for _, path in attached), f"a derivation session attached a file: {attached}"


def test_the_session_is_closed_when_this_module_opened_it() -> None:
    with derivation_session() as session:
        pass

    with pytest.raises(duckdb.Error):
        session.execute("SELECT 1")


def test_a_caller_supplied_connection_is_re_pinned_rather_than_trusted(
    unguarded: duckdb.DuckDBPyConnection,
) -> None:
    """The caller who hands in an unguarded connection is exactly the caller who would eat the host."""
    assert _setting(unguarded, "max_temp_directory_size") != "0 bytes", "fixture is not actually unguarded"

    derive_tier(_watershed_frame(), stream=GEOMETRY_STREAM, tier=5, connection=unguarded)

    assert _bytes_of(_setting(unguarded, "max_temp_directory_size")) == 0.0
    assert int(_setting(unguarded, "threads")) == DERIVATION_THREAD_COUNT


def test_a_caller_supplied_connection_is_left_open_for_its_owner(
    unguarded: duckdb.DuckDBPyConnection,
) -> None:
    """Reusing one session across many lane-days is the point of the parameter; closing it would break that."""
    derive_tier(_watershed_frame(), stream=GEOMETRY_STREAM, tier=5, connection=unguarded)

    assert unguarded.execute("SELECT 1").fetchone() == (1,)


def test_a_dissolve_still_rolls_up_under_the_guards() -> None:
    """The guards must bound the session without changing what the rung contains."""
    derived = derive_tier(_watershed_frame(), stream=GEOMETRY_STREAM, tier=5)

    assert derived.height == 1
    assert derived["huc12"].to_list() == ["17050101"]


def test_a_duckdb_failure_names_the_lane_and_the_rung_rather_than_leaking_raw() -> None:
    """A bare DuckDB error says nothing about which lane, which rung or how many rows -- and the drain
    would record exactly that as the whole explanation for a failed day."""
    corrupt = _watershed_frame().with_columns(pl.lit(b"not wkb at all", dtype=pl.Binary).alias("geom"))

    with pytest.raises(TierDerivationError) as raised:
        derive_tier(corrupt, stream=GEOMETRY_STREAM, tier=5)

    message = str(raised.value)
    assert GEOMETRY_STREAM in message
    assert "z5" in message
    assert "spilling disabled" in message
    assert "2 base rows" in message


def test_the_grid_path_never_opens_duckdb_at_all(monkeypatch: pytest.MonkeyPatch) -> None:
    """A grid lane coarsens in Polars, so no session -- guarded or not -- is opened for it."""

    def refuse(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("a GridAggregation lane must not open a DuckDB session")

    monkeypatch.setattr(duckdb, "connect", refuse)

    assert derive_tier(_fire_detections_frame(), stream=GRID_STREAM, tier=0).height == 1

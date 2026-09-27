"""The `areas` CLI verb and the `--apply` dry-run gate: P1-01/SEC-1, P1-02, P1-06, P1-07(c), SEC-2(a)(c).

`fakes.py::Source.inventory_areas` and `.empty_response` exist for exactly these tests; before this
file, nothing exercised them (P1-07(c)). The default region (`PLANTGEO_REGION` unset -> `pnw.json`)
has slug `pnw` and envelope `(-126.0, 41.0, -110.0, 50.0)`, matching `test_source.py`'s own literal.
"""

from __future__ import annotations

import itertools
import json
import math
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import httpx
import pytest

from agri_data_service.foundation.soil_survey.receipts import (
    AreaCensusEntry,
    AreaInventory,
    Blob,
    SoilSurveyError,
    digest,
)
from agri_data_service.pipeline.direct.soil_survey import __main__ as soil_survey_main
from agri_data_service.pipeline.direct.soil_survey.__main__ import _areas, _axis_edges, _tile_grid, main, parser
from agri_data_service.pipeline.direct.soil_survey.source import area_inventory_query
from tests.direct.soil_survey.fakes import Source

if TYPE_CHECKING:
    from pathlib import Path

_REGION_ENVELOPE = (-126.0, 41.0, -110.0, 50.0)
_REGION_SLUG = "pnw"
#: The pnw envelope at the default 2.0 deg edge: 16 deg wide / 2.0 = 8 lon bands, ceil(9 / 2.0) = 5
#: lat bands (review finding 4: named so `ruff`'s PLR2004 does not flag these as magic literals).
_PNW_LON_BANDS_AT_2_DEG = 8
_PNW_LAT_BANDS_AT_2_DEG = 5
_PNW_TILE_COUNT_AT_2_DEG = _PNW_LON_BANDS_AT_2_DEG * _PNW_LAT_BANDS_AT_2_DEG
_SMALL_BBOX_TILE_COUNT_AT_POINT_2_DEG = 4


def _patch_transport(monkeypatch: pytest.MonkeyPatch, source: Source) -> None:
    """Route every `httpx.AsyncClient()` built anywhere in this test to `source`'s MockTransport.

    `__main__.py::_areas`/`_capture` construct their own client inline (`httpx.AsyncClient(
    follow_redirects=False)`), so there is no seam to inject a transport through the public API;
    patching the constructor itself is the least invasive way to script the network boundary.
    """
    original_init = httpx.AsyncClient.__init__

    def patched_init(self: httpx.AsyncClient, *args: object, **kwargs: object) -> None:
        kwargs.setdefault("transport", httpx.MockTransport(source.response))
        original_init(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)


async def test_areas_happy_path_writes_an_inventory_receipt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = Source()
    source.inventory_areas = (("ID001", "2025-01-01T00:00:00.000"), ("OR002", "2025-02-02T00:00:00.000"))
    _patch_transport(monkeypatch, source)
    arguments = parser().parse_args(["areas", "--root", str(tmp_path), "--apply"])

    report = await _areas(arguments)

    assert report["outcome"] == "censused"
    assert report["region"] == _REGION_SLUG
    assert report["region_envelope_override"] is False
    assert sorted(report["areas"]) == ["ID001", "OR002"]  # type: ignore[call-overload]
    inventory = AreaInventory.model_validate_json((tmp_path / "areas.json").read_bytes())
    assert inventory.envelope == _REGION_ENVELOPE
    assert inventory.region == _REGION_SLUG
    assert {entry.area for entry in inventory.areas} == {"ID001", "OR002"}


async def test_areas_refuses_a_duplicate_area_symbol(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """P1-07(c): a census with the same area symbol twice is refused as a SoilSurveyError, not a
    raw pydantic ValidationError leaking out of `AreaInventory`'s own `unique_areas` validator."""
    source = Source()
    source.inventory_areas = (("ID001", "2025-01-01T00:00:00.000"), ("ID001", "2025-06-01T00:00:00.000"))
    _patch_transport(monkeypatch, source)
    arguments = parser().parse_args(["areas", "--root", str(tmp_path), "--apply"])

    with pytest.raises(SoilSurveyError, match="duplicate"):
        await _areas(arguments)
    assert not (tmp_path / "areas.json").exists()


async def test_areas_refuses_a_null_symbol_or_vintage_row(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """P1-02: a NULL row is refused outright, never silently dropped from the saved receipt."""
    source = Source()
    source.inventory_areas = (("ID001", "2025-01-01T00:00:00.000"), (None, "2025-01-01T00:00:00.000"))  # type: ignore[assignment]
    _patch_transport(monkeypatch, source)
    arguments = parser().parse_args(["areas", "--root", str(tmp_path), "--apply"])

    with pytest.raises(SoilSurveyError, match="missing its symbol or vintage"):
        await _areas(arguments)
    assert not (tmp_path / "areas.json").exists()


async def test_areas_refuses_an_empty_census(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """P1-02/P1-06: zero areas is refused, never recorded as `outcome: censused`.

    Every tile's own `{}` (SDA's bare no-rows answer) is now caught and treated as zero rows for
    that tile (live-run defect fix, below), so this refusal fires only once every tile in the
    default grid has answered empty -- the message is the aggregate "zero areas" refusal, not the
    single-tile "no rows" message a pre-fix run would have crashed on at tile 1.
    """
    source = Source()
    source.empty_response = True
    _patch_transport(monkeypatch, source)
    arguments = parser().parse_args(["areas", "--root", str(tmp_path), "--apply"])

    with pytest.raises(SoilSurveyError, match="zero areas"):
        await _areas(arguments)
    assert not (tmp_path / "areas.json").exists()


async def test_a_single_empty_tile_contributes_zero_rows_and_the_census_continues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Live-run defect: the Go-2 re-run over the full pnw envelope crashed at tile 33/40 because
    that tile legitimately had zero intersecting survey areas and SDA answered it with a bare `{}`,
    which `table_rows` refuses as a fatal `SoilSurveyError` with no per-tile handling around it. One
    empty tile among several non-empty ones must contribute zero rows and let the other tiles'
    results still be unioned, not discard the whole census.
    """
    tiles = _tile_grid(_SMALL_BBOX, 0.2)
    assert len(tiles) == _SMALL_BBOX_TILE_COUNT_AT_POINT_2_DEG
    source = Source()
    source.inventory_areas_by_tile = {
        tiles[0]: (("ID001", "2025-01-01T00:00:00.000"),),
        tiles[1]: (),
        tiles[2]: (),
        tiles[3]: (("OR002", "2025-03-03T00:00:00.000"),),
    }
    source.no_rows_tiles = {tiles[1]}  # this tile answers SDA's own bare `{}`, not a header-only table
    _patch_transport(monkeypatch, source)
    arguments = parser().parse_args(
        ["areas", "--root", str(tmp_path), *_SMALL_BBOX_ARGV, "--census-tile-degrees", "0.2", "--apply"]
    )

    report = await _areas(arguments)

    assert report["tiles_queried"] == _SMALL_BBOX_TILE_COUNT_AT_POINT_2_DEG
    assert sorted(report["areas"]) == ["ID001", "OR002"]  # type: ignore[call-overload]


async def test_an_inherited_ingest_bbox_env_var_never_narrows_the_census(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P1-01/SEC-1: setting INGEST_BBOX in the environment (as `plantgeo-job-executor` does) must
    not silently narrow the census -- `--bbox` takes NO environment fallback at all."""
    monkeypatch.setenv("INGEST_BBOX", "-125.0,42.0,-116.0,49.0")
    source = Source()
    _patch_transport(monkeypatch, source)
    arguments = parser().parse_args(["areas", "--root", str(tmp_path), "--apply"])

    report = await _areas(arguments)

    assert report["bbox"] == list(_REGION_ENVELOPE)
    assert report["region_envelope_override"] is False


def test_an_explicit_narrower_bbox_is_refused_without_the_override_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P1-01/SEC-1: an operator-supplied --bbox that differs from the Region envelope is refused
    unless --allow-non-region-scope is also given, so the override is always explicit and visible.

    Refused even on a bare dry run (no --apply): `main()` resolves scope before the dry-run gate so
    a misconfigured --bbox is visible before any network call, and lets the domain error propagate
    uncaught, the same way every other `SoilSurveyError` in this CLI does. The value below is also a
    real negative-leading bbox (`-125.0,...`), which doubles as coverage for `main()` routing argv
    through `inline_bbox_value` -- see the regression test right below this one.
    """
    source = Source()
    _patch_transport(monkeypatch, source)
    monkeypatch.setattr(
        "sys.argv",
        [
            "soil_survey",
            "areas",
            "--root",
            str(tmp_path),
            "--bbox",
            "-125.0,42.0,-116.0,49.0",
        ],
    )
    with pytest.raises(SoilSurveyError, match="allow-non-region-scope"):
        main()


def test_a_negative_leading_bbox_survives_argv_parsing_when_the_override_is_allowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """REGRESSION PIN, same class as `test_direct_writer_contract.py::
    test_every_bbox_writer_survives_a_negative_leading_bbox`: argparse reads a bare leading `-` on
    an option's VALUE as the start of another flag, so `--bbox -125.0,...` (space-separated, exactly
    how an operator types it) failed with `argument --bbox: expected one argument` until `main()`
    routed argv through `inline_bbox_value` first. Every real bbox this deployment uses has a
    negative west ordinate, so this is not a corner case -- it is the only shape that occurs."""
    source = Source()
    _patch_transport(monkeypatch, source)
    monkeypatch.setattr(
        "sys.argv",
        [
            "soil_survey",
            "areas",
            "--root",
            str(tmp_path),
            "--bbox",
            "-125.0,42.0,-116.0,49.0",
            "--allow-non-region-scope",
        ],
    )

    main()

    report = json.loads(capsys.readouterr().out)
    assert report["bbox"] == [-125.0, 42.0, -116.0, 49.0]
    assert report["region_envelope_override"] is True


def test_a_second_census_over_a_different_scope_refuses_to_overwrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P1-06: `areas.json` from one scope is never silently replaced by a census of another."""
    previous = AreaInventory(
        response=Blob(sha256="0" * 64, byte_count=2),
        query_sha256="1" * 64,
        checked_at=datetime(2026, 1, 1, tzinfo=UTC),
        areas=(AreaCensusEntry(area="ID001", saverest="2025-01-01T00:00:00.000"),),
        envelope=(-125.0, 42.0, -116.0, 49.0),
        region="pnw",
    )
    (tmp_path / "areas.json").write_bytes(previous.model_dump_json().encode())
    source = Source()
    _patch_transport(monkeypatch, source)
    monkeypatch.setattr("sys.argv", ["soil_survey", "areas", "--root", str(tmp_path), "--apply"])
    with pytest.raises(SoilSurveyError, match="already holds a census"):
        main()


def test_dry_run_makes_no_network_call_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """SEC-2(c): a bare `areas` invocation (no --apply) must construct no client at all."""

    def _refuse_to_construct(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("dry run must never construct an httpx.AsyncClient")

    monkeypatch.setattr(httpx.AsyncClient, "__init__", _refuse_to_construct)
    monkeypatch.setattr("sys.argv", ["soil_survey", "areas", "--root", str(tmp_path)])

    main()

    report = json.loads(capsys.readouterr().out)
    assert report["outcome"] == "dry_run"
    assert report["command"] == "areas"
    assert report["region"] == _REGION_SLUG
    assert report["bbox"] == list(_REGION_ENVELOPE)
    assert list(tmp_path.iterdir()) == []


def test_capture_dry_run_makes_no_network_call_and_reports_no_scope_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--bbox`/region resolution is `areas`-only; a `capture` dry run must not touch it, or the
    network, at all."""

    def _refuse_to_construct(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("dry run must never construct an httpx.AsyncClient")

    monkeypatch.setattr(httpx.AsyncClient, "__init__", _refuse_to_construct)
    monkeypatch.setattr("sys.argv", ["soil_survey", "capture", "--root", str(tmp_path), "--area", "ID001"])

    main()

    report = json.loads(capsys.readouterr().out)
    assert report["outcome"] == "dry_run"
    assert report["command"] == "capture"
    assert "region" not in report
    assert "bbox" not in report


# --- Census tiling (Go-2 fix for the Go-1 pilot's Anomaly 1; AGENTS.md, "Census tiling") ------- #

_SMALL_BBOX = (-116.4, 43.4, -116.0, 43.8)  # 0.4 deg square, well inside the pnw region envelope
#: `--bbox=...` (one token, `=`-joined), not `--bbox`, "..." as two argv items: argparse reads a
#: bare leading `-` on a separate VALUE token as another flag (the same regression
#: `inline_bbox_value`/`main()` guards against for a real CLI invocation); tests that call
#: `parser().parse_args()` directly, bypassing `main()`, must pre-join it themselves.
_SMALL_BBOX_ARGV = ["--bbox=-116.4,43.4,-116.0,43.8", "--allow-non-region-scope"]


def test_tile_grid_partitions_the_envelope_with_no_gaps_or_overlaps() -> None:
    """The pnw region envelope is 16 deg wide, 9 deg tall; a 2 deg tile does not evenly divide 9,
    so the grid's own final row must still reconstruct the envelope exactly, clipped, not padded."""
    bbox = (-126.0, 41.0, -110.0, 50.0)
    tiles = _tile_grid(bbox, 2.0)

    assert len(tiles) == _PNW_TILE_COUNT_AT_2_DEG
    lon_bands = sorted({(tile[0], tile[2]) for tile in tiles})
    lat_bands = sorted({(tile[1], tile[3]) for tile in tiles})
    assert len(lon_bands) == _PNW_LON_BANDS_AT_2_DEG
    assert len(lat_bands) == _PNW_LAT_BANDS_AT_2_DEG
    assert lon_bands[0][0] == bbox[0]
    assert lon_bands[-1][1] == bbox[2]
    assert lat_bands[0][0] == bbox[1]
    assert lat_bands[-1][1] == bbox[3]
    # No gap and no overlap along either axis: each band's own end is the next band's own start.
    for (_, end), (start, _) in itertools.pairwise(lon_bands):
        assert end == start
    for (_, end), (start, _) in itertools.pairwise(lat_bands):
        assert end == start
    assert lat_bands[-1][1] - lat_bands[-1][0] == pytest.approx(1.0)  # the clipped final row


def test_tile_grid_returns_one_tile_when_the_tile_edge_equals_the_envelope_span() -> None:
    bbox = (-116.4, 43.4, -116.0, 43.8)
    assert _tile_grid(bbox, 0.4) == (bbox,)


async def test_areas_unions_rows_by_area_symbol_and_dedupes_a_same_vintage_repeat_across_tiles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Core tiling contract: one request per tile (`area_inventory_calls`), rows unioned by area
    symbol, and an area answered by two different tiles at the SAME vintage collapses to one entry."""
    tiles = _tile_grid(_SMALL_BBOX, 0.2)
    assert len(tiles) == _SMALL_BBOX_TILE_COUNT_AT_POINT_2_DEG
    source = Source()
    source.inventory_areas_by_tile = {
        tiles[0]: (("ID001", "2025-01-01T00:00:00.000"),),
        tiles[1]: (("ID001", "2025-01-01T00:00:00.000"), ("ID683", "2025-02-02T00:00:00.000")),
        tiles[2]: (),
        tiles[3]: (("OR002", "2025-03-03T00:00:00.000"),),
    }
    _patch_transport(monkeypatch, source)
    arguments = parser().parse_args(
        ["areas", "--root", str(tmp_path), *_SMALL_BBOX_ARGV, "--census-tile-degrees", "0.2", "--apply"]
    )

    report = await _areas(arguments)

    assert report["tiles_queried"] == _SMALL_BBOX_TILE_COUNT_AT_POINT_2_DEG
    assert source.area_inventory_calls == _SMALL_BBOX_TILE_COUNT_AT_POINT_2_DEG
    assert sorted(report["areas"]) == ["ID001", "ID683", "OR002"]  # type: ignore[call-overload]
    inventory = AreaInventory.model_validate_json((tmp_path / "areas.json").read_bytes())
    assert inventory.envelope == _SMALL_BBOX  # the whole resolved envelope, never a tile
    assert isinstance(report["max_tile_seconds"], float)
    # Review finding 3: `query_sha256` must commit to each tile's own query body digest, in tile
    # order, not merely the tile bboxes -- otherwise a changed `ssurgo_area_inventory.sql` body
    # produces the identical top-level digest as the unchanged query, defeating F7 for this receipt.
    expected_tile_digests = [digest(area_inventory_query(tile).encode()) for tile in tiles]
    expected_commitment = digest(
        json.dumps({"tile_degrees": 0.2, "tile_query_sha256": expected_tile_digests}, sort_keys=True).encode()
    )
    assert inventory.query_sha256 == expected_commitment


async def test_areas_refuses_when_an_area_disagrees_on_saverest_across_tiles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real survey area never re-publishes mid-census; two different vintages for the same area
    symbol across two tiles is refused outright, never silently resolved by picking one."""
    tiles = _tile_grid(_SMALL_BBOX, 0.2)
    source = Source()
    source.inventory_areas_by_tile = {
        tiles[0]: (("ID001", "2025-01-01T00:00:00.000"),),
        tiles[1]: (("ID001", "2025-06-01T00:00:00.000"),),
        tiles[2]: (),
        tiles[3]: (),
    }
    _patch_transport(monkeypatch, source)
    arguments = parser().parse_args(
        ["areas", "--root", str(tmp_path), *_SMALL_BBOX_ARGV, "--census-tile-degrees", "0.2", "--apply"]
    )

    with pytest.raises(SoilSurveyError, match="conflicting vintages"):
        await _areas(arguments)
    assert not (tmp_path / "areas.json").exists()


async def test_a_non_positive_census_tile_degrees_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = Source()
    _patch_transport(monkeypatch, source)
    for value in ("0", "-1"):
        arguments = parser().parse_args(["areas", "--root", str(tmp_path), "--census-tile-degrees", value, "--apply"])
        with pytest.raises(SoilSurveyError, match="census-tile-degrees"):
            await _areas(arguments)


async def test_a_census_tile_degrees_larger_than_the_envelope_span_yields_one_tile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review finding 2: there is no upper bound on `--census-tile-degrees` any more -- `_axis_edges`
    already degenerates a step larger than the envelope's own span into a single untiled band, so a
    17 deg step over the 16-degree-wide pnw envelope must succeed with exactly one tile, not raise.
    """
    source = Source()
    _patch_transport(monkeypatch, source)
    arguments = parser().parse_args(["areas", "--root", str(tmp_path), "--census-tile-degrees", "17", "--apply"])

    report = await _areas(arguments)

    assert report["tiles_queried"] == 1


async def test_a_census_tile_degrees_equal_to_the_envelope_span_is_allowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = Source()
    _patch_transport(monkeypatch, source)
    arguments = parser().parse_args(["areas", "--root", str(tmp_path), "--census-tile-degrees", "16", "--apply"])

    report = await _areas(arguments)

    assert report["tiles_queried"] == 1


async def test_the_default_census_tile_degrees_works_over_a_bbox_smaller_than_the_default_edge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review finding 2's own failure scenario: the Go-1 pilot's own ~0.4 deg diagnostic bbox, with
    no `--census-tile-degrees` override at all, used to raise under the old `<= envelope span` bound
    (0.4 < the 2.0 deg default) -- it must now succeed with exactly one tile.
    """
    source = Source()
    _patch_transport(monkeypatch, source)
    arguments = parser().parse_args(["areas", "--root", str(tmp_path), *_SMALL_BBOX_ARGV, "--apply"])

    report = await _areas(arguments)

    assert report["tiles_queried"] == 1


def test_axis_edges_never_adds_a_spurious_hairline_band_from_float_accumulation() -> None:
    """Review finding 1: the old `cursor = min(cursor + step, high)` accumulation left `cursor` a
    hair below `high` even when the division was exact, adding a ~1e-13-degree-wide extra band.
    Both steps below reproduce the pilot's own probed cases (pnw envelope, 0.1 and 0.3 deg edges).
    """
    west, south, east, north = _REGION_ENVELOPE
    for low, high, step in ((west, east, 0.1), (south, north, 0.3)):
        edges = _axis_edges(low, high, step)
        expected_count = math.ceil((high - low) / step)
        assert len(edges) == expected_count
        narrowest = min(band_high - band_low for band_low, band_high in edges)
        assert narrowest > step * 1e-6


async def test_an_out_of_range_request_timeout_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = Source()
    _patch_transport(monkeypatch, source)
    for value in ("0", "-5", "121"):
        arguments = parser().parse_args(
            ["areas", "--root", str(tmp_path), "--request-timeout-seconds", value, "--apply"]
        )
        with pytest.raises(SoilSurveyError, match="request-timeout-seconds"):
            await _areas(arguments)


async def test_the_request_timeout_flag_reaches_every_tiles_fetch_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--request-timeout-seconds` must reach `source.py::fetch` for every tile, not just parse."""
    seen_timeouts: list[float] = []

    async def _fake_fetch(client: httpx.AsyncClient, query: str, *, timeout: float = 30) -> bytes:
        del client, query
        seen_timeouts.append(timeout)
        return json.dumps({"Table": [["areasymbol", "saverest"], ["ID001", "2025-01-01T00:00:00.000"]]}).encode()

    monkeypatch.setattr(soil_survey_main, "fetch", _fake_fetch)
    arguments = parser().parse_args(
        [
            "areas",
            "--root",
            str(tmp_path),
            *_SMALL_BBOX_ARGV,
            "--census-tile-degrees",
            "0.4",  # one tile exactly the size of _SMALL_BBOX
            "--request-timeout-seconds",
            "77",
            "--apply",
        ]
    )

    report = await _areas(arguments)

    assert report["tiles_queried"] == 1
    assert seen_timeouts == [77.0]

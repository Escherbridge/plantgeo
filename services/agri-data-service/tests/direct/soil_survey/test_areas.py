"""The `areas` CLI verb and the `--apply` dry-run gate: P1-01/SEC-1, P1-02, P1-06, P1-07(c), SEC-2(a)(c).

`fakes.py::Source.inventory_areas` and `.empty_response` exist for exactly these tests; before this
file, nothing exercised them (P1-07(c)). The default region (`PLANTGEO_REGION` unset -> `pnw.json`)
has slug `pnw` and envelope `(-126.0, 41.0, -110.0, 50.0)`, matching `test_source.py`'s own literal.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import httpx
import pytest

from agri_data_service.foundation.soil_survey.receipts import AreaCensusEntry, AreaInventory, Blob, SoilSurveyError
from agri_data_service.pipeline.direct.soil_survey.__main__ import _areas, main, parser
from tests.direct.soil_survey.fakes import Source

if TYPE_CHECKING:
    from pathlib import Path

_REGION_ENVELOPE = (-126.0, 41.0, -110.0, 50.0)
_REGION_SLUG = "pnw"


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
    """P1-02/P1-06: zero areas is refused, never recorded as `outcome: censused`."""
    source = Source()
    source.empty_response = True
    _patch_transport(monkeypatch, source)
    arguments = parser().parse_args(["areas", "--root", str(tmp_path), "--apply"])

    with pytest.raises(SoilSurveyError, match="no rows"):
        await _areas(arguments)
    assert not (tmp_path / "areas.json").exists()


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

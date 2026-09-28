"""HEAD probes, drift, retries, canonical manifests and the resumable capture (CONTRACT C1, C8).

No network: every HEAD is answered by `httpx.MockTransport`. Rationale: the lane's AGENTS.md, "Capture".
"""

from __future__ import annotations

import json
from email.utils import format_datetime
from typing import TYPE_CHECKING, Any, Final

import httpx
import numpy as np
import pytest
import rasterio  # type: ignore[import-untyped]

from agri_data_service.pipeline.direct.soil_properties import capture, forward, source
from agri_data_service.pipeline.direct.soil_properties.products import (
    LATTICE_EAST,
    LATTICE_NORTH,
    LATTICE_SOUTH,
    LATTICE_WEST,
    RELEASE_ID,
    SOURCE_FILE_PINS,
)
from tests.direct.soil_properties.lane_fixtures import write_raster

if TYPE_CHECKING:
    from pathlib import Path

    from agri_data_service.pipeline.direct.soil_properties.products import SourceFilePin

PIN: Final = SOURCE_FILE_PINS[0]
NO_WAIT: Final = source.RetryPolicy(attempts=3, base_seconds=0.0, max_seconds=0.0)
OK: Final = 200
NOT_FOUND: Final = 404
UNAVAILABLE: Final = 503
EXPECTED_BACKOFF: Final = [2.0, 4.0]
MARGIN_DEGREES_LATITUDE: Final = 2_000 / 110_574


def _headers(pin: SourceFilePin, *, etag: str | None = None) -> dict[str, str]:
    return {
        "ETag": etag or pin.etag,
        "Last-Modified": format_datetime(pin.last_modified, usegmt=True),
        "Content-Length": str(pin.content_length),
    }


def _client(handler: Any) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _answering_pins(*, moved: str | None = None) -> httpx.Client:
    """A client whose HEADs answer every pin as pinned, except `moved` (a file name) with a new ETag."""
    by_url = {pin.vrt_url: pin for pin in SOURCE_FILE_PINS}

    def handler(request: httpx.Request) -> httpx.Response:
        pin = by_url[str(request.url)]
        etag = '"moved"' if pin.file_name == moved else None
        return httpx.Response(OK, headers=_headers(pin, etag=etag))

    return _client(handler)


# --- Probes and drift -----------------------------------------------------------------------------


def test_a_head_that_matches_its_pin_has_no_drift() -> None:
    with _client(lambda _request: httpx.Response(OK, headers=_headers(PIN))) as client:
        probed = source.probe_pin(client, PIN, NO_WAIT)
    assert probed.drift() == ()
    assert probed.content_length == PIN.content_length


def test_a_moved_etag_is_drift_and_capture_refuses_it() -> None:
    with _answering_pins(moved=PIN.file_name) as client:
        probes = source.probe_pins(client, NO_WAIT)
    reasons = source.drift_report(probes)
    assert len(reasons) == 1
    assert PIN.file_name in reasons[0]
    with pytest.raises(source.SoilPropertiesDriftError, match="new ISRIC release suspected"):
        source.refuse_on_drift(probes, stage="capture")


def test_a_vanished_file_is_drift() -> None:
    with _client(lambda _request: httpx.Response(NOT_FOUND)) as client:
        probed = source.probe_pin(client, PIN, NO_WAIT)
    assert probed.drift() == (f"{PIN.file_name}: HTTP 404 (the file set changed)",)


def test_a_server_error_is_retried() -> None:
    answers = iter([httpx.Response(UNAVAILABLE), httpx.Response(OK, headers=_headers(PIN))])
    with _client(lambda _request: next(answers)) as client:
        probed = source.probe_pin(client, PIN, NO_WAIT)
    assert probed.status == OK


def test_retrying_backs_off_exponentially_then_raises() -> None:
    waits: list[float] = []
    calls: list[int] = []

    def flaky() -> None:
        calls.append(1)
        raise httpx.ConnectError("down")

    policy = source.RetryPolicy(attempts=3, base_seconds=2.0, max_seconds=60.0)
    with pytest.raises(httpx.ConnectError):
        source.retrying(flaky, policy, retryable=(httpx.TransportError,), sleep=waits.append)
    assert len(calls) == policy.attempts
    assert waits == EXPECTED_BACKOFF


def test_a_machine_proj_path_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROJ_LIB", "C:/OSGeo4W/share/proj")
    with pytest.raises(source.SoilPropertiesPipelineError, match="unset PROJ_LIB"):
        source.require_clean_proj_environment("capture")


# --- Canonical JSON and the manifest (C5.5, C8) ---------------------------------------------------


def test_the_manifest_digest_ignores_its_own_field_and_nothing_else() -> None:
    body = {"release_id": RELEASE_ID, "files": [{"bytes": 1.0}]}
    digest = source.manifest_digest(body)
    assert source.manifest_digest({**body, "manifest_sha256": "x"}) == digest
    assert source.manifest_digest({**body, "release_id": "other"}) != digest
    assert source.canonical_json(body) == '{"files":[{"bytes":1}],"release_id":"soilgrids-v2.0/2020-06-02"}'


def _fake_capture(directory: Path) -> dict[str, Any]:
    """Thirty stand-in files and their closed manifest; bytes are arbitrary, digests are real."""
    entries = []
    for index, pin in enumerate(SOURCE_FILE_PINS):
        image = directory / capture.capture_file_name(pin)
        image.write_bytes(f"window-{index}".encode())
        entries.append(capture.file_entry(pin, image))
    window = {"crs": "HOMOLOSINE", "col_off": 1, "row_off": 2, "width": 3, "height": 4, "margin_m": 2000}
    manifest = capture.build_manifest(entries, window)
    (directory / capture.CAPTURE_MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")
    return manifest


def test_a_closed_manifest_round_trips_with_every_c8_field(tmp_path: Path) -> None:
    manifest = _fake_capture(tmp_path)
    read = capture.read_capture_manifest(tmp_path)
    assert read == manifest
    assert read["watermark"]["instant"] == "2020-06-02T16:14:20Z"
    assert {entry["path"] for entry in read["files"]} == {capture.capture_file_name(pin) for pin in SOURCE_FILE_PINS}
    assert read["tooling"]["proj_env_unset"] is True


def test_changed_capture_bytes_are_refused(tmp_path: Path) -> None:
    _fake_capture(tmp_path)
    (tmp_path / capture.capture_file_name(PIN)).write_bytes(b"tampered")
    with pytest.raises(source.SoilPropertiesPipelineError, match="sha256 mismatch"):
        capture.read_capture_manifest(tmp_path)


def test_an_edited_manifest_fails_its_own_digest(tmp_path: Path) -> None:
    manifest = _fake_capture(tmp_path)
    manifest["window"]["width"] = 999
    (tmp_path / capture.CAPTURE_MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(source.SoilPropertiesPipelineError, match="manifest_sha256"):
        capture.read_capture_manifest(tmp_path)


def test_a_receipt_is_reused_only_when_pin_and_bytes_both_match(tmp_path: Path) -> None:
    image = tmp_path / capture.capture_file_name(PIN)
    image.write_bytes(b"window")
    receipt = {**capture.file_entry(PIN, image), "window": {}}
    receipt_path = tmp_path / f"{image.name}.receipt.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    assert capture.reusable_entry(tmp_path, PIN) == receipt
    receipt_path.write_text(json.dumps({**receipt, "etag": '"older"'}), encoding="utf-8")
    assert capture.reusable_entry(tmp_path, PIN) is None


def test_capture_refuses_drift_before_reading_any_window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in source.PROJ_ENVIRONMENT_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    options = forward.parser().parse_args(
        ["capture", "--capture-dir", str(tmp_path), "--retry-base-seconds", "0", "--retry-max-seconds", "0"]
    )
    moved = SOURCE_FILE_PINS[-1].file_name
    with _answering_pins(moved=moved) as client, pytest.raises(source.SoilPropertiesDriftError):
        capture.capture_release(options, client=client)
    assert list(tmp_path.glob("*.tif")) == []


# --- The window -------------------------------------------------------------------------------------


def test_the_capture_bounds_widen_the_lattice_by_the_margin() -> None:
    west, south, east, north = capture.capture_bounds_degrees()
    assert west < LATTICE_WEST
    assert east > LATTICE_EAST
    assert south == pytest.approx(LATTICE_SOUTH - MARGIN_DEGREES_LATITUDE)
    assert north == pytest.approx(LATTICE_NORTH + MARGIN_DEGREES_LATITUDE)


#: West edge of the window both fixture rasters share: -130 + 19 x 0.25, exact in binary.
SHARED_WINDOW_WEST: Final = -125.25


def _capture_window(tmp_path: Path, name: str, *, west: float) -> dict[str, Any]:
    """Cut one fixed geographic window out of a source raster whose origin is `west`, as capture does."""
    grid = np.zeros((100, 203), dtype=np.int16)
    source_raster = write_raster(tmp_path / f"{name}-source.tif", grid, west=west, north=52.0, pixel=0.25)
    with rasterio.open(source_raster) as dataset:
        window = capture.native_window(dataset, (-125.05, 45.0, -115.0, 49.05))
        capture.write_window(dataset, window, tmp_path / f"{name}.tif")
        return {"path": f"{name}.tif", "col_off": int(window.col_off)}


def test_vrts_offset_by_whole_pixels_still_share_one_grid(tmp_path: Path) -> None:
    # ISRIC's bdod/soc VRTs start three pixels west of the other 24, so one window has two col_offs.
    shifted = _capture_window(tmp_path, "bdod", west=-130.75)
    aligned = _capture_window(tmp_path, "phh2o", west=-130.0)
    assert shifted["col_off"] == aligned["col_off"] + 3
    grid = capture.shared_grid(tmp_path, [shifted, aligned])
    assert grid["transform"][2] == SHARED_WINDOW_WEST
    assert (grid["width"], grid["margin_m"]) == (41, capture.CAPTURE_MARGIN_METERS)


def test_windows_on_a_different_lattice_are_refused(tmp_path: Path) -> None:
    half_pixel = _capture_window(tmp_path, "bdod", west=-130.125)
    aligned = _capture_window(tmp_path, "phh2o", west=-130.0)
    with pytest.raises(source.SoilPropertiesPipelineError, match="no longer share one grid"):
        capture.shared_grid(tmp_path, [half_pixel, aligned])


def test_the_native_window_is_rounded_outward_and_clamped(tmp_path: Path) -> None:
    grid = np.zeros((100, 200), dtype=np.int16)
    raster = write_raster(tmp_path / "grid.tif", grid, west=-130.0, north=52.0, pixel=0.1)
    with rasterio.open(raster) as dataset:
        window = capture.native_window(dataset, (-125.05, 41.95, -110.95, 49.05))
    # Offsets 49.5 and 29.5 floor; ends 190.5 and 100.5 ceil, and the row end clamps to the 100-row raster.
    assert (window.col_off, window.row_off) == (49, 29)
    assert (window.col_off + window.width, window.row_off + window.height) == (191, 100)

"""Contract tests for the Open-Meteo ERA5-Land archive lane: accounting, nulls, order, and quota failure."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from agri_data_service.execution.contracts import canonical_json_bytes, reject_sensitive_fields
from agri_data_service.execution.historical_backfill import AnalysisGridCell  # noqa: TC001
from agri_data_service.execution.historical_open_meteo import (
    OPEN_METEO_ARCHIVE_SIGNAL_SPECIFICATIONS,
    OPEN_METEO_ARCHIVE_SOIL_MOISTURE_PARAMETERS,
    OPEN_METEO_ARCHIVE_SUPPORT_KEY,
    RATE_LIMIT_BACKOFF_SECONDS,
    HistoricalOpenMeteoArchivePlan,
    HistoricalOpenMeteoRawCacheReceipt,
    OpenMeteoArchiveCapture,
    OpenMeteoArchiveChunk,
    OpenMeteoArchiveFetchError,
    _canonical_archive_document,
    cache_historical_open_meteo_result,
    fetch_open_meteo_archive_chunk,
    historical_open_meteo_plan_checksum,
    historical_open_meteo_release_manifest,
    initialize_historical_open_meteo_checkpoint,
    load_cached_historical_open_meteo_result,
    open_meteo_archive_chunk_url,
    parse_open_meteo_archive_payload,
    record_historical_open_meteo_result,
    rederive_historical_open_meteo_checkpoint_state,
    require_accounted_open_meteo_result,
)
from agri_data_service.ingest.open_meteo import (
    OPEN_METEO_API_KEY_VARIABLE,
    OPEN_METEO_ARCHIVE_BASE_URL,
    OPEN_METEO_ARCHIVE_CUSTOMER_BASE_URL,
    OpenMeteoRateLimitError,
    _rate_limit_scope,
    archive_daily_request,
    archive_daily_url,
)

if TYPE_CHECKING:
    from pathlib import Path

WINDOW_START = date(2022, 4, 30)
WINDOW_END = date(2026, 4, 30)
SHORT_WINDOW_DAYS = 3
CELL_LATITUDES = (43.125, 43.375, 43.625, 43.875)
CELL_LONGITUDE = -116.375
RETRIEVED_AT = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)


def _cell(latitude: float, longitude: float = CELL_LONGITUDE) -> dict[str, object]:
    return {
        "cell_key": f"sentinel2-ndvi-0p25deg:{latitude:.4f}:{longitude:.4f}",
        "latitude": latitude,
        "longitude": longitude,
    }


def _plan(*, chunk_cell_count: int = 2, parameters: tuple[str, ...] | None = None) -> HistoricalOpenMeteoArchivePlan:
    return HistoricalOpenMeteoArchivePlan.model_validate(
        {
            "source": {
                "key": "open-meteo-era5-land-archive",
                "name": "Open-Meteo ERA5-Land archive (redistributed ECMWF reanalysis)",
                "owner": "Open-Meteo",
                "purpose": "test",
                "base_url": "https://archive-api.open-meteo.com/v1/archive",
                "license_name": "CC-BY 4.0 (Open-Meteo) over Copernicus/ECMWF ERA5-Land",
                "license_url": "https://open-meteo.com/en/license",
                "citation": "Open-Meteo is an intermediary redistributor of ERA5-Land.",
                "retention_days": None,
                "reviewed_at": "2026-08-05T00:00:00Z",
                "reviewed_by": "test",
            },
            "window": {"start_date": WINDOW_START.isoformat(), "end_date": WINDOW_END.isoformat()},
            "grid_name": "sentinel2-ndvi-0p25deg",
            "grid_resolution_m": 27830,
            "native_grid_degrees": 0.1,
            "native_grid_resolution_m": 9000,
            "cells": [_cell(latitude) for latitude in CELL_LATITUDES],
            "chunk_cell_count": chunk_cell_count,
            "parameters": sorted(parameters or OPEN_METEO_ARCHIVE_SOIL_MOISTURE_PARAMETERS),
            "transform_version": "open-meteo-era5-land-archive-daily-mean-normalization-v1",
            "release_set_key": "open-meteo-test-release",
            "release_set_as_of": "2026-08-06T23:59:59Z",
            "description": "test plan",
        }
    )


def _window_days(plan: HistoricalOpenMeteoArchivePlan) -> list[str]:
    days: list[str] = []
    current = plan.window.start_date
    while current <= plan.window.end_date:
        days.append(current.isoformat())
        current += timedelta(days=1)
    return days


def _location(
    plan: HistoricalOpenMeteoArchivePlan,
    cell: AnalysisGridCell,
    index: int,
    *,
    values: dict[str, list[float | None]] | None = None,
) -> dict[str, object]:
    days = _window_days(plan)
    daily: dict[str, object] = {"time": days}
    for parameter in plan.parameters:
        daily[parameter] = (values or {}).get(parameter, [0.25] * len(days))
    location: dict[str, object] = {
        # The provider snaps to the nearest 0.1-degree node, always 0.025 from a `.125`/`.375` centroid.
        "latitude": round(round(cell.latitude / 0.1) * 0.1, 6),
        "longitude": round(round(cell.longitude / 0.1) * 0.1, 6),
        "generationtime_ms": 12.5,
        "utc_offset_seconds": 0,
        "timezone": "GMT",
        "timezone_abbreviation": "GMT",
        "elevation": 923.0,
        "daily": daily,
    }
    if index:
        location["location_id"] = index
    return location


def _payload(
    plan: HistoricalOpenMeteoArchivePlan,
    chunk: OpenMeteoArchiveChunk,
    *,
    values: dict[str, list[float | None]] | None = None,
) -> bytes:
    return canonical_json_bytes([_location(plan, cell, index, values=values) for index, cell in enumerate(chunk.cells)])


def _capture(payload: bytes) -> OpenMeteoArchiveCapture:
    return OpenMeteoArchiveCapture(
        retrieved_at=RETRIEVED_AT,
        wire_payload_bytes=len(payload),
        wire_payload_checksum=hashlib.sha256(payload).hexdigest(),
    )


def test_plan_cuts_stable_chunks_anchored_at_the_first_cell() -> None:
    plan = _plan(chunk_cell_count=3)
    assert [chunk.key for chunk in plan.chunks] == ["cells-0000", "cells-0001"]
    assert len(plan.chunks[0].cells) == 3  # noqa: PLR2004
    assert len(plan.chunks[1].cells) == 1
    assert plan.chunks == _plan(chunk_cell_count=3).chunks


def test_plan_rejects_two_cells_sharing_one_native_grid_point() -> None:
    """A 0.1-degree sample cannot stand for two analysis cells; the plan must refuse to try."""
    with pytest.raises(ValueError, match="must not share a native grid point"):
        HistoricalOpenMeteoArchivePlan.model_validate(
            {
                **_plan().model_dump(mode="json"),
                "cells": [_cell(43.115), _cell(43.125)],
            }
        )


def test_plan_rejects_an_unsupported_parameter() -> None:
    with pytest.raises(ValueError, match="unsupported Open-Meteo archive parameter"):
        HistoricalOpenMeteoArchivePlan.model_validate({**_plan().model_dump(mode="json"), "parameters": ["rainfall"]})


def test_moisture_layers_reuse_the_cds_signal_names_and_unit() -> None:
    """One physical quantity keeps one name; only support and provenance differ from the CDS lane."""
    layer_one = OPEN_METEO_ARCHIVE_SIGNAL_SPECIFICATIONS["soil_moisture_0_to_7cm_mean"]
    assert layer_one.signal_name == "soil_water_content_layer_1"
    assert layer_one.original_unit == layer_one.normalized_unit == "m^3/m^3"
    assert OPEN_METEO_ARCHIVE_SUPPORT_KEY == "era5-land-0.1deg"
    for parameter in OPEN_METEO_ARCHIVE_SOIL_MOISTURE_PARAMETERS:
        specification = OPEN_METEO_ARCHIVE_SIGNAL_SPECIFICATIONS[parameter]
        assert specification.signal_name.startswith("soil_water_content_layer_")
        assert specification.original_unit == specification.normalized_unit == "m^3/m^3"


def test_every_moisture_layer_is_bounded_to_the_physical_volumetric_range() -> None:
    """Volumetric water content is physically [0, 1] m^3/m^3; nothing else may be stored as one."""
    for parameter in OPEN_METEO_ARCHIVE_SOIL_MOISTURE_PARAMETERS:
        specification = OPEN_METEO_ARCHIVE_SIGNAL_SPECIFICATIONS[parameter]
        assert (specification.minimum, specification.maximum) == (0.0, 1.0)


def test_vapour_pressure_deficit_is_a_bounded_atmospheric_covariate_not_a_soil_signal() -> None:
    """VPD is atmospheric dryness, not soil state; it keeps its own name, unit, and physical range."""
    specification = OPEN_METEO_ARCHIVE_SIGNAL_SPECIFICATIONS["vapour_pressure_deficit_max"]
    assert specification.signal_name == "vapor_pressure_deficit"
    assert specification.original_unit == specification.normalized_unit == "kPa"
    assert (specification.minimum, specification.maximum) == (0.0, 15.0)


@pytest.mark.parametrize("sentinel", [-999.0, 9.969209968386869e36, 1.5, -0.0001])
def test_an_out_of_range_value_fails_the_whole_chunk(sentinel: float) -> None:
    """A provider sentinel is a provider failure, not a gap: it must never land as an accepted row.

    `-999` and the netCDF `_FillValue` 9.969e36 are both finite, so the finite-check alone lets them
    through. Downgrading them to `no_data` would assert the provider modelled nothing here, which is
    a different and unevidenced claim, so the chunk fails and keeps no receipt.
    """
    plan = _plan()
    chunk = plan.chunks[0]
    poisoned: list[float | None] = [0.25] * plan.window.day_count
    poisoned[7] = sentinel
    payload = _payload(plan, chunk, values={plan.parameters[0]: poisoned})
    with pytest.raises(ValueError, match="outside its reviewed physical range"):
        parse_open_meteo_archive_payload(plan, chunk, payload, _capture(payload))


def test_archive_url_pins_the_land_model_and_nearest_cell_selection() -> None:
    """The endpoint default is 0.25-degree `era5`; only an explicit model gives the 0.1-degree product."""
    url = archive_daily_url([(43.125, -116.375)], ["soil_moisture_0_to_7cm_mean"], WINDOW_START, WINDOW_END)
    assert "models=era5_land" in url
    assert "cell_selection=nearest" in url
    assert "timezone=GMT" in url
    assert url.startswith("https://archive-api.open-meteo.com/v1/archive?")


def test_parse_normalizes_every_reviewed_cell_signal_and_day() -> None:
    plan = _plan()
    chunk = plan.chunks[0]
    payload = _payload(plan, chunk)
    result = parse_open_meteo_archive_payload(plan, chunk, payload, _capture(payload))
    expected_rows = len(chunk.cells) * len(plan.parameters) * plan.window.day_count
    assert len(result.observations) == expected_rows
    assert len(result.coverage) == len(chunk.cells) * len(plan.parameters)
    assert {item.status for item in result.coverage} == {"complete"}
    assert all(item.normalized_unit == "m^3/m^3" for item in result.observations)


def test_parse_buckets_by_the_publisher_named_day_not_a_utc_recast() -> None:
    plan = _plan()
    chunk = plan.chunks[0]
    payload = _payload(plan, chunk)
    result = parse_open_meteo_archive_payload(plan, chunk, payload, _capture(payload))
    first = min(result.observations, key=lambda item: item.observed_at)
    assert first.observed_at == datetime.combine(WINDOW_START, datetime.min.time(), tzinfo=UTC)
    assert first.observed_at.date().isoformat() == _window_days(plan)[0]


def test_an_all_null_series_is_no_data_and_writes_no_fabricated_rows() -> None:
    """Ocean and out-of-domain cells return null, never zero; absence is recorded as absence."""
    plan = _plan()
    chunk = plan.chunks[0]
    days = _window_days(plan)
    payload = canonical_json_bytes(
        [
            _location(plan, cell, index, values={parameter: [None] * len(days) for parameter in plan.parameters})
            if index == 0
            else _location(plan, cell, index)
            for index, cell in enumerate(chunk.cells)
        ]
    )
    result = parse_open_meteo_archive_payload(plan, chunk, payload, _capture(payload))
    ocean_key = chunk.cells[0].cell_key
    assert not [item for item in result.observations if item.cell_key == ocean_key]
    ocean_coverage = [item for item in result.coverage if item.cell_key == ocean_key]
    assert len(ocean_coverage) == len(plan.parameters)
    assert {item.status for item in ocean_coverage} == {"no_data"}
    assert {item.received_observation_count for item in ocean_coverage} == {0}


def test_a_partial_series_stays_partial_with_an_explicit_missing_row() -> None:
    plan = _plan()
    chunk = plan.chunks[0]
    days = _window_days(plan)
    gapped: list[float | None] = [0.25] * len(days)
    gapped[5] = None
    payload = canonical_json_bytes(
        [
            _location(plan, cell, index, values={plan.parameters[0]: gapped})
            if index == 0
            else _location(plan, cell, index)
            for index, cell in enumerate(chunk.cells)
        ]
    )
    result = parse_open_meteo_archive_payload(plan, chunk, payload, _capture(payload))
    target = next(
        item
        for item in result.coverage
        if item.cell_key == chunk.cells[0].cell_key and item.source_parameter == plan.parameters[0]
    )
    assert target.status == "partial"
    assert target.received_observation_count == len(days) - 1
    missing = [
        item
        for item in result.observations
        if item.cell_key == chunk.cells[0].cell_key
        and item.source_parameter == plan.parameters[0]
        and not item.is_observed
    ]
    assert len(missing) == 1
    assert missing[0].normalized_value is None
    assert missing[0].quality_flag == "source_missing"


def test_parse_rejects_a_response_that_dropped_a_location() -> None:
    plan = _plan()
    chunk = plan.chunks[0]
    payload = canonical_json_bytes([_location(plan, chunk.cells[0], 0)])
    with pytest.raises(ValueError, match="one entry per requested cell"):
        parse_open_meteo_archive_payload(plan, chunk, payload, _capture(payload))


def test_parse_rejects_a_response_that_dropped_a_day() -> None:
    plan = _plan()
    chunk = plan.chunks[0]
    document = json.loads(_payload(plan, chunk))
    for location in document:
        location["daily"]["time"] = location["daily"]["time"][:-1]
        for parameter in plan.parameters:
            location["daily"][parameter] = location["daily"][parameter][:-1]
    payload = canonical_json_bytes(document)
    with pytest.raises(ValueError, match="does not match the reviewed window"):
        parse_open_meteo_archive_payload(plan, chunk, payload, _capture(payload))


def test_parse_rejects_a_grid_point_outside_the_reviewed_cell() -> None:
    plan = _plan()
    chunk = plan.chunks[0]
    document = json.loads(_payload(plan, chunk))
    document[0]["latitude"] = document[0]["latitude"] + 0.3
    payload = canonical_json_bytes(document)
    with pytest.raises(ValueError, match="outside the reviewed cell"):
        parse_open_meteo_archive_payload(plan, chunk, payload, _capture(payload))


def test_parse_rejects_out_of_order_locations() -> None:
    plan = _plan()
    chunk = plan.chunks[0]
    document = json.loads(_payload(plan, chunk))
    document[1]["location_id"] = 7
    payload = canonical_json_bytes(document)
    with pytest.raises(ValueError, match="not in the requested order"):
        parse_open_meteo_archive_payload(plan, chunk, payload, _capture(payload))


def test_parse_rejects_a_non_gmt_time_base() -> None:
    plan = _plan()
    chunk = plan.chunks[0]
    document = json.loads(_payload(plan, chunk))
    document[0]["utc_offset_seconds"] = -25200
    payload = canonical_json_bytes(document)
    with pytest.raises(ValueError, match="not on the reviewed GMT time base"):
        parse_open_meteo_archive_payload(plan, chunk, payload, _capture(payload))


def test_accounting_rejects_a_result_whose_coverage_undercounts_its_rows() -> None:
    """The guard that a chunk which dropped records cannot report success."""
    plan = _plan()
    chunk = plan.chunks[0]
    payload = _payload(plan, chunk)
    result = parse_open_meteo_archive_payload(plan, chunk, payload, _capture(payload))
    tampered = result.__class__(
        **{
            **{field: getattr(result, field) for field in result.__dataclass_fields__},
            "observations": result.observations[:-1],
        }
    )
    with pytest.raises(ValueError, match="dropped or duplicated normalized daily rows"):
        require_accounted_open_meteo_result(plan, tampered)


def test_checkpoint_validates_only_when_every_chunk_has_a_receipt() -> None:
    plan = _plan(chunk_cell_count=2)
    checkpoint = initialize_historical_open_meteo_checkpoint(plan, updated_at=RETRIEVED_AT)
    for index, chunk in enumerate(plan.chunks):
        payload = _payload(plan, chunk)
        result = parse_open_meteo_archive_payload(plan, chunk, payload, _capture(payload))
        checkpoint = record_historical_open_meteo_result(plan, checkpoint, result, updated_at=RETRIEVED_AT)
        expected_state = "validated" if index == len(plan.chunks) - 1 else "running"
        assert checkpoint.state == expected_state
    assert len(historical_open_meteo_release_manifest(plan, checkpoint)) == 64  # noqa: PLR2004


def test_release_manifest_refuses_an_incomplete_checkpoint() -> None:
    plan = _plan(chunk_cell_count=2)
    checkpoint = initialize_historical_open_meteo_checkpoint(plan, updated_at=RETRIEVED_AT)
    payload = _payload(plan, plan.chunks[0])
    result = parse_open_meteo_archive_payload(plan, plan.chunks[0], payload, _capture(payload))
    checkpoint = record_historical_open_meteo_result(plan, checkpoint, result, updated_at=RETRIEVED_AT)
    with pytest.raises(ValueError, match="complete validated checkpoint is required"):
        historical_open_meteo_release_manifest(plan, checkpoint)


def test_raw_cache_round_trips_and_refuses_conflicting_content(tmp_path: Path) -> None:
    plan = _plan()
    chunk = plan.chunks[0]
    payload = _payload(plan, chunk)
    result = parse_open_meteo_archive_payload(plan, chunk, payload, _capture(payload))
    cache_historical_open_meteo_result(tmp_path, plan, result)
    reloaded = load_cached_historical_open_meteo_result(tmp_path, plan, chunk)
    assert reloaded is not None
    assert reloaded.payload_checksum == result.payload_checksum
    assert len(reloaded.observations) == len(result.observations)

    other_payload = _payload(plan, chunk, values={plan.parameters[0]: [0.99] * plan.window.day_count})
    other = parse_open_meteo_archive_payload(plan, chunk, other_payload, _capture(other_payload))
    with pytest.raises(ValueError, match="already binds this chunk to different source content"):
        cache_historical_open_meteo_result(tmp_path, plan, other)


def test_the_canonical_document_ignores_the_provider_timing_metric() -> None:
    """`generationtime_ms` varies per request; leaving it in would make every refetch a new release."""
    plan = _plan()
    chunk = plan.chunks[0]
    document = json.loads(_payload(plan, chunk))
    first = _canonicalized(document)
    for location in document:
        location["generationtime_ms"] = 999.0
    assert _canonicalized(document) == first


def _canonicalized(document: list[dict[str, object]]) -> bytes:
    return _canonical_archive_document(json.dumps(document).encode("utf-8"))


def test_chunk_url_matches_the_url_the_fetcher_would_request() -> None:
    plan = _plan()
    chunk = plan.chunks[0]
    assert open_meteo_archive_chunk_url(plan, chunk) == archive_daily_url(
        [(cell.latitude, cell.longitude) for cell in chunk.cells],
        plan.parameters,
        plan.window.start_date,
        plan.window.end_date,
    )


# --- paid-tier access: the key is an environment fact, never a stored one --------------------
# See execution/AGENTS.md §historical_open_meteo and ingest/AGENTS.md §the paid archive host.

TEST_API_KEY = "test-key-not-real"

# Byte-for-byte what the keyless builder produced before paid-tier support existed. Already-persisted
# probe releases pin this exact string in `agri.source_release.query_parameters`; a changed parameter
# order or host would silently orphan them.
KEYLESS_CANONICAL_URL = (
    "https://archive-api.open-meteo.com/v1/archive"
    "?latitude=43.125&longitude=-116.375&start_date=2022-04-30&end_date=2026-04-30"
    "&daily=soil_moisture_0_to_7cm_mean&models=era5_land&timezone=GMT&cell_selection=nearest"
)


def test_the_keyless_canonical_url_is_byte_identical_to_the_pre_paid_tier_builder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No key means the free host and eight parameters in the original order -- nothing may shift."""
    monkeypatch.delenv(OPEN_METEO_API_KEY_VARIABLE, raising=False)
    url = archive_daily_url([(43.125, -116.375)], ["soil_moisture_0_to_7cm_mean"], WINDOW_START, WINDOW_END)
    assert url == KEYLESS_CANONICAL_URL
    request = archive_daily_request([(43.125, -116.375)], ["soil_moisture_0_to_7cm_mean"], WINDOW_START, WINDOW_END)
    assert request.base_url == OPEN_METEO_ARCHIVE_BASE_URL
    assert request.request_url == KEYLESS_CANONICAL_URL


@pytest.mark.parametrize("blank", ["", "   "])
def test_a_blank_key_is_the_free_tier_not_a_credentialed_request(monkeypatch: pytest.MonkeyPatch, blank: str) -> None:
    """An exported-but-empty variable must not send `apikey=` to a host that would reject it."""
    monkeypatch.setenv(OPEN_METEO_API_KEY_VARIABLE, blank)
    request = archive_daily_request([(43.125, -116.375)], ["soil_moisture_0_to_7cm_mean"], WINDOW_START, WINDOW_END)
    assert request.request_url == KEYLESS_CANONICAL_URL


def test_a_configured_key_moves_the_real_request_and_only_the_real_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The credential belongs in the request URL alone; the canonical URL keeps the host and drops the key."""
    monkeypatch.setenv(OPEN_METEO_API_KEY_VARIABLE, TEST_API_KEY)
    coordinates = [(43.125, -116.375)]
    request = archive_daily_request(coordinates, ["soil_moisture_0_to_7cm_mean"], WINDOW_START, WINDOW_END)
    assert request.base_url == OPEN_METEO_ARCHIVE_CUSTOMER_BASE_URL
    assert request.request_url.startswith(f"{OPEN_METEO_ARCHIVE_CUSTOMER_BASE_URL}?")
    assert request.request_url.endswith(f"&apikey={TEST_API_KEY}")
    canonical = archive_daily_url(
        coordinates, ["soil_moisture_0_to_7cm_mean"], WINDOW_START, WINDOW_END, base_url=request.base_url
    )
    assert canonical.startswith(f"{OPEN_METEO_ARCHIVE_CUSTOMER_BASE_URL}?")
    assert TEST_API_KEY not in canonical
    assert "apikey" not in canonical
    # Only the host and the appended credential differ; every governed parameter keeps its position.
    assert canonical.split("?", 1)[1] == KEYLESS_CANONICAL_URL.split("?", 1)[1]


def test_the_persisted_chunk_url_names_the_retrieval_host_without_the_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Naming the free host while the paid host answered is a provenance lie; so is the reverse."""
    monkeypatch.setenv(OPEN_METEO_API_KEY_VARIABLE, TEST_API_KEY)
    plan = _plan()
    chunk = plan.chunks[0]
    paid = open_meteo_archive_chunk_url(plan, chunk, base_url=OPEN_METEO_ARCHIVE_CUSTOMER_BASE_URL)
    assert paid.startswith(f"{OPEN_METEO_ARCHIVE_CUSTOMER_BASE_URL}?")
    assert TEST_API_KEY not in paid
    # Bytes fetched keylessly stay attributed to the free host even once a key is configured.
    cached_keyless = open_meteo_archive_chunk_url(plan, chunk, base_url=OPEN_METEO_ARCHIVE_BASE_URL)
    assert cached_keyless.startswith(f"{OPEN_METEO_ARCHIVE_BASE_URL}?")


def test_an_unreviewed_archive_host_is_refused() -> None:
    """A tampered local cache receipt must not be able to write an arbitrary URL into provenance."""
    plan = _plan()
    with pytest.raises(ValueError, match="not a reviewed endpoint"):
        open_meteo_archive_chunk_url(plan, plan.chunks[0], base_url="https://example.invalid/v1/archive")


def test_the_plan_checksum_ignores_the_key_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Access is not a governed input: keying the lane must not orphan a checkpoint or its raw cache."""
    monkeypatch.delenv(OPEN_METEO_API_KEY_VARIABLE, raising=False)
    keyless = historical_open_meteo_plan_checksum(_plan())
    monkeypatch.setenv(OPEN_METEO_API_KEY_VARIABLE, TEST_API_KEY)
    keyed_plan = _plan()
    assert historical_open_meteo_plan_checksum(keyed_plan) == keyless
    governed = keyed_plan.model_dump(mode="json")
    reject_sensitive_fields(governed)
    assert TEST_API_KEY not in canonical_json_bytes(governed).decode("utf-8")


@pytest.mark.asyncio
async def test_the_key_reaches_the_wire_and_nothing_durable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """End to end: the credential is in the request, and in no receipt, checkpoint, or release field."""
    monkeypatch.setenv(OPEN_METEO_API_KEY_VARIABLE, TEST_API_KEY)
    plan = _plan()
    chunk = plan.chunks[0]
    payload = _payload(plan, chunk)
    requested: list[str] = []

    async def answer(_client: object, url: str) -> str:
        requested.append(url)
        return payload.decode("utf-8")

    monkeypatch.setattr("agri_data_service.execution.historical_open_meteo.fetch_archive_daily", answer)
    result = await fetch_open_meteo_archive_chunk(plan, chunk, client=object(), retrieved_at=RETRIEVED_AT)

    assert len(requested) == 1
    assert f"apikey={TEST_API_KEY}" in requested[0]
    assert requested[0].startswith(OPEN_METEO_ARCHIVE_CUSTOMER_BASE_URL)
    assert result.request_base_url == OPEN_METEO_ARCHIVE_CUSTOMER_BASE_URL

    cache_historical_open_meteo_result(tmp_path, plan, result)
    checkpoint = record_historical_open_meteo_result(
        plan, initialize_historical_open_meteo_checkpoint(plan), result, updated_at=RETRIEVED_AT
    )
    written = sorted(path for path in tmp_path.rglob("*") if path.is_file())
    assert written
    for path in written:
        assert TEST_API_KEY.encode() not in path.read_bytes()
    assert TEST_API_KEY not in canonical_json_bytes(checkpoint.model_dump(mode="json")).decode("utf-8")

    # The exact dict `_ensure_open_meteo_source_release` persists, checked by the repo's own guard.
    query_parameters = {
        "request_url": open_meteo_archive_chunk_url(plan, chunk, base_url=result.request_base_url),
        "model": plan.model,
        "cell_selection": plan.cell_selection,
        "time_zone": plan.time_zone,
        "parameters": plan.parameters,
        "cell_keys": [cell.cell_key for cell in chunk.cells],
    }
    reject_sensitive_fields(query_parameters)
    assert TEST_API_KEY not in canonical_json_bytes(query_parameters).decode("utf-8")


def test_the_custody_guard_would_refuse_a_credentialed_request_url() -> None:
    """Defence in depth: if a key ever reached the URL, the write-time guard must reject the row."""
    with pytest.raises(ValueError, match="credential query parameters"):
        reject_sensitive_fields({"request_url": f"{KEYLESS_CANONICAL_URL}&apikey={TEST_API_KEY}"})


def test_cached_bytes_keep_the_host_they_were_fetched_from(tmp_path: Path) -> None:
    """Persistence replays the cache, so the receipt -- not the current environment -- names the host."""
    plan = _plan()
    chunk = plan.chunks[0]
    payload = _payload(plan, chunk)
    capture = OpenMeteoArchiveCapture(
        retrieved_at=RETRIEVED_AT,
        wire_payload_bytes=len(payload),
        wire_payload_checksum=hashlib.sha256(payload).hexdigest(),
        request_base_url=OPEN_METEO_ARCHIVE_CUSTOMER_BASE_URL,
    )
    result = parse_open_meteo_archive_payload(plan, chunk, payload, capture)
    receipt = cache_historical_open_meteo_result(tmp_path, plan, result)
    assert receipt.request_base_url == OPEN_METEO_ARCHIVE_CUSTOMER_BASE_URL
    replayed = load_cached_historical_open_meteo_result(tmp_path, plan, chunk)
    assert replayed is not None
    assert replayed.request_base_url == OPEN_METEO_ARCHIVE_CUSTOMER_BASE_URL


def test_a_receipt_written_before_the_paid_host_reads_as_the_free_host() -> None:
    """No other host was reachable when a v1 receipt was written, so the free host is derived, not assumed."""
    legacy = HistoricalOpenMeteoRawCacheReceipt.model_validate(
        {
            "schema_version": 1,
            "plan_checksum": "a" * 64,
            "chunk_key": "cells-0000",
            "payload_checksum": "b" * 64,
            "payload_bytes": 1,
            "wire_payload_checksum": "c" * 64,
            "wire_payload_bytes": 1,
            "retrieved_at": RETRIEVED_AT.isoformat(),
        }
    )
    assert legacy.request_base_url == OPEN_METEO_ARCHIVE_BASE_URL


class _RateLimitedClient:
    """A client that always refuses, so an exhausted quota is proven to surface rather than be swallowed."""

    def __init__(self, scope: str) -> None:
        self.scope = scope
        self.attempts = 0


@pytest.mark.parametrize(
    ("body", "expected_scope"),
    [
        # The exact bodies the provider returned on 2026-08-06. "Daily ... tomorrow" contains no
        # substring "day", which is precisely how a daily wall got slept through in a live run.
        ('{"reason":"Minutely API request limit exceeded. Please try again in one minute.","error":true}', "minute"),
        ('{"reason":"Hourly API request limit exceeded. Please try again in the next hour.","error":true}', "hour"),
        ('{"reason":"Daily API request limit exceeded. Please try again tomorrow.","error":true}', "day"),
        # Ambiguous: names two windows. It must resolve to the LEAST retryable one.
        ('{"reason":"Daily API request limit exceeded. Please try again in 60 minutes.","error":true}', "day"),
        ('{"reason":"something entirely new","error":true}', "unknown"),
        ("not json at all", "unknown"),
    ],
)
def test_rate_limit_scope_reads_the_providers_own_wording(body: str, expected_scope: str) -> None:
    scope, _reason = _rate_limit_scope(body)
    assert scope == expected_scope


def test_a_daily_wall_is_never_slept_through() -> None:
    """A misclassified daily wall costs 3 x 120 s per chunk of pointless waiting; assert it cannot recur."""
    scope, reason = _rate_limit_scope(
        '{"reason":"Daily API request limit exceeded. Please try again tomorrow.","error":true}'
    )
    assert scope not in RATE_LIMIT_BACKOFF_SECONDS
    assert "tomorrow" in reason


def test_only_a_minutely_refusal_is_ever_waited_out() -> None:
    """An unrecognised 429 body is more likely a reworded wall than a blip, so it fails closed."""
    assert RATE_LIMIT_BACKOFF_SECONDS == {"minute": 70.0}
    for scope in ("hour", "day", "unknown"):
        assert scope not in RATE_LIMIT_BACKOFF_SECONDS


@pytest.mark.asyncio
async def test_an_unrecognised_refusal_fails_immediately_without_sleeping(monkeypatch: pytest.MonkeyPatch) -> None:
    """Burning 4 requests and 360 s against an exhausted keyless quota is the failure mode to avoid."""
    plan = _plan()
    chunk = plan.chunks[0]
    attempts = 0
    waits: list[float] = []

    async def refuse(_client: object, _url: str) -> str:
        nonlocal attempts
        attempts += 1
        raise OpenMeteoRateLimitError("unknown", "something entirely new")

    async def record_wait(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr("agri_data_service.execution.historical_open_meteo.fetch_archive_daily", refuse)
    with pytest.raises(OpenMeteoArchiveFetchError):
        await fetch_open_meteo_archive_chunk(plan, chunk, client=_RateLimitedClient("unknown"), sleep=record_wait)
    assert attempts == 1
    assert waits == []


@pytest.mark.asyncio
async def test_an_exhausted_hourly_quota_fails_immediately_without_sleeping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sleeping through an hourly wall would turn a quota refusal into an unexplained hang."""
    plan = _plan()
    chunk = plan.chunks[0]
    attempts = 0
    waits: list[float] = []

    async def refuse(_client: object, _url: str) -> str:
        nonlocal attempts
        attempts += 1
        raise OpenMeteoRateLimitError("hour", "Hourly API request limit exceeded.")

    async def record_wait(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr("agri_data_service.execution.historical_open_meteo.fetch_archive_daily", refuse)
    with pytest.raises(OpenMeteoArchiveFetchError) as raised:
        await fetch_open_meteo_archive_chunk(plan, chunk, client=_RateLimitedClient("hour"), sleep=record_wait)
    assert raised.value.chunk_key == chunk.key
    assert "Hourly API request limit exceeded" in str(raised.value)
    assert attempts == 1
    assert waits == []
    assert "failed after 1 attempt:" in str(raised.value)


@pytest.mark.asyncio
async def test_a_minutely_quota_is_retried_then_surfaced(monkeypatch: pytest.MonkeyPatch) -> None:
    plan = _plan()
    chunk = plan.chunks[0]
    waits: list[float] = []

    async def refuse(_client: object, _url: str) -> str:
        raise OpenMeteoRateLimitError("minute", "Minutely API request limit exceeded.")

    async def record_wait(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr("agri_data_service.execution.historical_open_meteo.fetch_archive_daily", refuse)
    with pytest.raises(OpenMeteoArchiveFetchError):
        await fetch_open_meteo_archive_chunk(plan, chunk, client=_RateLimitedClient("minute"), sleep=record_wait)
    assert waits == [70.0, 70.0, 70.0]


@pytest.mark.asyncio
async def test_a_recovered_rate_limit_still_produces_an_accounted_result(monkeypatch: pytest.MonkeyPatch) -> None:
    plan = _plan()
    chunk = plan.chunks[0]
    payload = _payload(plan, chunk)
    calls = 0

    async def flaky(_client: object, _url: str) -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OpenMeteoRateLimitError("minute", "Minutely API request limit exceeded.")
        return payload.decode("utf-8")

    async def no_wait(_seconds: float) -> None:
        return None

    monkeypatch.setattr("agri_data_service.execution.historical_open_meteo.fetch_archive_daily", flaky)
    result = await fetch_open_meteo_archive_chunk(
        plan,
        chunk,
        client=_RateLimitedClient("minute"),
        retrieved_at=RETRIEVED_AT,
        sleep=no_wait,
    )
    assert calls == 2  # noqa: PLR2004
    require_accounted_open_meteo_result(plan, result)
    assert result.chunk_key == chunk.key


def test_a_blocked_checkpoint_whose_chunks_all_landed_is_recoverable() -> None:
    """Trusting a stored `blocked` strands a complete run: nothing outstanding would ever clear it."""
    plan = _plan(chunk_cell_count=2)
    checkpoint = initialize_historical_open_meteo_checkpoint(plan, updated_at=RETRIEVED_AT)
    for chunk in plan.chunks:
        payload = _payload(plan, chunk)
        result = parse_open_meteo_archive_payload(plan, chunk, payload, _capture(payload))
        checkpoint = record_historical_open_meteo_result(plan, checkpoint, result, updated_at=RETRIEVED_AT)
    stranded = checkpoint.model_copy(update={"state": "blocked", "reason": "a transient warehouse error"})
    recovered = rederive_historical_open_meteo_checkpoint_state(plan, stranded)
    assert recovered.state == "validated"
    assert recovered.reason == "a transient warehouse error"
    assert len(historical_open_meteo_release_manifest(plan, recovered)) == 64  # noqa: PLR2004


def test_a_blocked_checkpoint_with_no_receipts_rederives_to_initialized() -> None:
    plan = _plan(chunk_cell_count=2)
    checkpoint = initialize_historical_open_meteo_checkpoint(plan, updated_at=RETRIEVED_AT)
    stranded = checkpoint.model_copy(update={"state": "blocked", "reason": "a daily quota wall"})
    assert rederive_historical_open_meteo_checkpoint_state(plan, stranded).state == "initialized"


def test_state_rederivation_refuses_a_checkpoint_bound_to_another_plan() -> None:
    plan = _plan(chunk_cell_count=2)
    other = initialize_historical_open_meteo_checkpoint(_plan(chunk_cell_count=3), updated_at=RETRIEVED_AT)
    with pytest.raises(ValueError, match="does not bind the reviewed plan"):
        rederive_historical_open_meteo_checkpoint_state(plan, other)


def test_plan_checksum_changes_when_chunking_changes() -> None:
    """Chunk boundaries are part of the governed identity, so a resume can never straddle two shapes."""
    assert historical_open_meteo_plan_checksum(_plan(chunk_cell_count=2)) != historical_open_meteo_plan_checksum(
        _plan(chunk_cell_count=3)
    )

"""The soil-survey serving read (`planes`) and the source-system validator (`pipeline.validation`).

Covers: a multi-part release reading as one day, an unwritten/future/absent day answering as an
honest empty result rather than falling through, the dependency-free WKB point-in-polygon decoder
(including MultiPolygon-with-hole exclusion and refusal of unsupported geometry types), and the
validator's per-area reconciliation against a faked USDA SDA client -- count mismatch, vintage
staleness (a republish), an area missing on one side or the other, per-area failure isolation, and
the "nothing has ever been written" honest gap. No network call and no real bucket are used
anywhere: `RecordingBackend` backs every `ObjectStore`, real Parquet bytes are written to a local
temp directory and read back with Polars over plain filesystem paths, and USDA SDA is either a
hand-written fake or `httpx.MockTransport`.
"""

from __future__ import annotations

import struct
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Final

import httpx
import pyarrow as pa  # type: ignore[import-untyped]
import pytest

from agri_data_service.foundation.parquet.absence import GovernedAbsence
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from agri_data_service.pipeline.validation.soil_survey import (
    MAX_VALIDATION_AREAS,
    HttpxSoilSurveySdaClient,
    SdaSurveyAreaSummary,
    SoilSurveySdaResponseError,
    SoilSurveyValidationError,
    _parse_sda_table_response,
    _parse_survey_area_vintage,
    _validated_area_symbol,
    read_written_soil_survey_area_summaries,
    validate_soil_survey_release,
)
from agri_data_service.planes.soil_survey import (
    SoilSurveyGeometryDecodeError,
    SoilSurveyReadError,
    find_soil_survey_at_point,
    read_soil_survey_by_mupolygonkeys,
    resolve_latest_soil_survey_release,
    resolve_soil_survey_release,
    soil_survey_at_point,
    wkb_polygon_contains_point,
)
from agri_data_service.warehouse.schemas.soil_survey import SOIL_SURVEY_SCHEMA, SOIL_SURVEY_STREAM
from tests.parquet.test_objectstore_writer import RecordingBackend

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

RELEASE_DAY = date(2026, 8, 8)
GEOMETRY_ID = "11111111-1111-1111-1111-111111111111"
CONFIRMED_AT = datetime(2026, 8, 8, tzinfo=UTC)
EXPECTED_PART_COUNT: Final = 3
EXPECTED_DELINEATION_COUNT: Final = 3


# --- WKB construction helpers (test-only encoder; the module under test only decodes) ----------


def _wkb_ring(points: Sequence[tuple[float, float]]) -> bytes:
    body = struct.pack("<I", len(points))
    for x, y in points:
        body += struct.pack("<dd", x, y)
    return body


def _wkb_polygon_body(rings: Sequence[Sequence[tuple[float, float]]]) -> bytes:
    body = struct.pack("<I", len(rings))
    for ring in rings:
        body += _wkb_ring(ring)
    return body


def wkb_polygon(rings: Sequence[Sequence[tuple[float, float]]]) -> bytes:
    return struct.pack("<B", 1) + struct.pack("<I", 3) + _wkb_polygon_body(rings)


def wkb_multipolygon(polygons: Sequence[Sequence[Sequence[tuple[float, float]]]]) -> bytes:
    body = struct.pack("<I", len(polygons))
    for rings in polygons:
        body += wkb_polygon(rings)
    return struct.pack("<B", 1) + struct.pack("<I", 6) + body


def wkb_point(x: float, y: float) -> bytes:
    return struct.pack("<B", 1) + struct.pack("<I", 1) + struct.pack("<dd", x, y)


def _square(min_x: float, min_y: float, max_x: float, max_y: float) -> list[tuple[float, float]]:
    return [(min_x, min_y), (max_x, min_y), (max_x, max_y), (min_x, max_y), (min_x, min_y)]


# --- Soil-survey grain rows and a local-disk release writer ------------------------------------


def _soil_survey_row(
    *,
    mupolygonkey: str,
    geometry_wkb: bytes,
    survey_area_symbol: str = "ID001",
    survey_area_vintage: datetime = datetime(2025, 8, 26, tzinfo=UTC),
    hydric_rating: bool | None = False,
) -> dict[str, object]:
    return {
        "natural_key": f"usda-sda:{mupolygonkey}",
        "mupolygonkey": mupolygonkey,
        "mukey": "123456",
        "map_unit_name": "Test loam",
        "soil_series": "Test series",
        "drainage_class": "well_drained",
        "hydric_rating": hydric_rating,
        "land_capability_class": "3e",
        "survey_area_symbol": survey_area_symbol,
        "survey_area_vintage": survey_area_vintage,
        "geometry_id": GEOMETRY_ID,
        "last_confirmed_at": CONFIRMED_AT,
        "release_day": RELEASE_DAY,
        "geometry_wkb": geometry_wkb,
        "producer": "usda-sda",
    }


def _rows_to_table(rows: Sequence[dict[str, object]]) -> pa.Table:
    columns = {name: [row[name] for row in rows] for name in SOIL_SURVEY_SCHEMA.column_names}
    return pa.table({name: pa.array(values) for name, values in columns.items()}).cast(SOIL_SURVEY_SCHEMA.arrow_schema)


def _write_release(
    tmp_path: Path,
    *,
    day: date,
    parts: Sequence[Sequence[dict[str, object]]],
) -> tuple[ObjectStore, Callable[[str], str]]:
    """Write one release, one `pyarrow.Table` per part, to an in-memory backend mirrored to disk."""
    backend = RecordingBackend()
    store = ObjectStore(backend)
    for part_index, rows in enumerate(parts):
        store.write_partition(
            _rows_to_table(rows), layer=SOIL_SURVEY_STREAM, kind="observed", day=day, part_index=part_index
        )
    for key, payload in backend.objects.items():
        path = tmp_path / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)

    def _path_for(relative_path: str) -> str:
        return str(tmp_path / relative_path)

    return store, _path_for


# --- WKB point-in-polygon decoder ----------------------------------------------------------------


def test_a_point_inside_a_simple_polygon_is_contained() -> None:
    polygon = wkb_polygon([_square(0, 0, 1, 1)])
    assert wkb_polygon_contains_point(polygon, longitude=0.5, latitude=0.5) is True
    assert wkb_polygon_contains_point(polygon, longitude=5.0, latitude=5.0) is False


def test_a_point_inside_a_multipolygon_member_is_contained() -> None:
    multipolygon = wkb_multipolygon([[_square(0, 0, 1, 1)], [_square(10, 10, 11, 11)]])
    assert wkb_polygon_contains_point(multipolygon, longitude=10.5, latitude=10.5) is True
    assert wkb_polygon_contains_point(multipolygon, longitude=5.0, latitude=5.0) is False


def test_a_point_inside_a_hole_is_excluded() -> None:
    exterior = _square(0, 0, 4, 4)
    hole = _square(1, 1, 2, 2)
    polygon = wkb_polygon([exterior, hole])
    assert wkb_polygon_contains_point(polygon, longitude=0.5, latitude=0.5) is True
    assert wkb_polygon_contains_point(polygon, longitude=1.5, latitude=1.5) is False


def test_an_unsupported_geometry_type_is_refused_not_treated_as_no_match() -> None:
    with pytest.raises(SoilSurveyGeometryDecodeError, match="unsupported WKB geometry type 1"):
        wkb_polygon_contains_point(wkb_point(0.0, 0.0), longitude=0.0, latitude=0.0)


def test_a_truncated_payload_is_refused() -> None:
    with pytest.raises(SoilSurveyGeometryDecodeError, match="shorter than a WKB header"):
        wkb_polygon_contains_point(b"\x01\x03", longitude=0.0, latitude=0.0)


# --- planes: release resolution -----------------------------------------------------------------


def test_resolving_an_unwritten_day_is_an_honest_none_not_a_fallback(tmp_path: Path) -> None:
    one_row = [_soil_survey_row(mupolygonkey="poly-1", geometry_wkb=wkb_polygon([_square(0, 0, 1, 1)]))]
    store, _ = _write_release(tmp_path, day=RELEASE_DAY, parts=[one_row])

    assert resolve_soil_survey_release(store, date(2099, 1, 1)) is None
    latest = resolve_latest_soil_survey_release(store)
    assert latest is not None
    assert latest.day == RELEASE_DAY


def test_a_governed_absence_day_resolves_to_no_release_not_an_error() -> None:
    backend = RecordingBackend()
    store = ObjectStore(backend)
    absence_day = date(2026, 8, 9)
    store.write_absence(
        GovernedAbsence(
            reason="no published delineations",
            upstream_response="warehouse held zero rows",
            recorded_at=datetime(2026, 8, 9, tzinfo=UTC),
            run_id="run-1",
        ),
        layer=SOIL_SURVEY_STREAM,
        kind="observed",
        day=absence_day,
    )

    assert resolve_soil_survey_release(store, absence_day) is None
    assert resolve_latest_soil_survey_release(store) is None


def test_a_multi_part_release_reads_as_one_day(tmp_path: Path) -> None:
    """Three parts, one delineation each -- an mupolygonkey lookup and a point lookup must both
    see all three as ONE release, not three separate tables the caller has to union by hand."""
    parts = [
        [_soil_survey_row(mupolygonkey="poly-1", geometry_wkb=wkb_polygon([_square(0, 0, 1, 1)]))],
        [_soil_survey_row(mupolygonkey="poly-2", geometry_wkb=wkb_polygon([_square(2, 2, 3, 3)]))],
        [_soil_survey_row(mupolygonkey="poly-3", geometry_wkb=wkb_polygon([_square(10, 10, 11, 11)]))],
    ]
    store, path_for = _write_release(tmp_path, day=RELEASE_DAY, parts=parts)
    release = resolve_soil_survey_release(store, RELEASE_DAY)
    assert release is not None
    assert len(release.relative_paths) == EXPECTED_PART_COUNT

    by_key = read_soil_survey_by_mupolygonkeys(
        release, mupolygonkeys=["poly-1", "poly-3"], storage_options={}, path_for=path_for
    )
    assert sorted(by_key["mupolygonkey"].to_list()) == ["poly-1", "poly-3"]

    at_point = find_soil_survey_at_point(
        release, longitude=10.5, latitude=10.5, storage_options={}, path_for=path_for
    )
    assert at_point["mupolygonkey"].to_list() == ["poly-3"]


def test_soil_survey_at_point_resolves_the_latest_release_by_default(tmp_path: Path) -> None:
    store, path_for = _write_release(
        tmp_path,
        day=RELEASE_DAY,
        parts=[[_soil_survey_row(mupolygonkey="poly-1", geometry_wkb=wkb_polygon([_square(0, 0, 1, 1)]))]],
    )

    result = soil_survey_at_point(store, longitude=0.5, latitude=0.5, storage_options={}, path_for=path_for)

    assert result.release_day == RELEASE_DAY
    assert result.matches["mupolygonkey"].to_list() == ["poly-1"]


def test_soil_survey_at_point_on_a_never_written_day_is_an_honest_empty_answer(tmp_path: Path) -> None:
    store, path_for = _write_release(
        tmp_path,
        day=RELEASE_DAY,
        parts=[[_soil_survey_row(mupolygonkey="poly-1", geometry_wkb=wkb_polygon([_square(0, 0, 1, 1)]))]],
    )

    result = soil_survey_at_point(
        store, longitude=0.5, latitude=0.5, storage_options={}, path_for=path_for, day=date(2099, 1, 1)
    )

    assert result.release_day is None
    assert result.matches.is_empty()
    assert result.matches.schema.names() == list(SOIL_SURVEY_SCHEMA.column_names)


def test_an_empty_mupolygonkey_list_is_refused(tmp_path: Path) -> None:
    store, path_for = _write_release(
        tmp_path,
        day=RELEASE_DAY,
        parts=[[_soil_survey_row(mupolygonkey="poly-1", geometry_wkb=wkb_polygon([_square(0, 0, 1, 1)]))]],
    )
    release = resolve_soil_survey_release(store, RELEASE_DAY)
    assert release is not None

    with pytest.raises(SoilSurveyReadError, match="at least one key"):
        read_soil_survey_by_mupolygonkeys(release, mupolygonkeys=[], storage_options={}, path_for=path_for)


def test_max_matches_below_one_is_refused(tmp_path: Path) -> None:
    store, path_for = _write_release(
        tmp_path,
        day=RELEASE_DAY,
        parts=[[_soil_survey_row(mupolygonkey="poly-1", geometry_wkb=wkb_polygon([_square(0, 0, 1, 1)]))]],
    )
    release = resolve_soil_survey_release(store, RELEASE_DAY)
    assert release is not None

    with pytest.raises(SoilSurveyReadError, match="max_matches must be at least 1"):
        find_soil_survey_at_point(
            release, longitude=0.5, latitude=0.5, storage_options={}, path_for=path_for, max_matches=0
        )


# --- validation: written-side aggregation, including the hydric tri-state -----------------------


def test_hydric_tri_state_is_never_coerced_into_true_or_false(tmp_path: Path) -> None:
    rows = [
        _soil_survey_row(mupolygonkey="poly-1", geometry_wkb=wkb_polygon([_square(0, 0, 1, 1)]), hydric_rating=True),
        _soil_survey_row(mupolygonkey="poly-2", geometry_wkb=wkb_polygon([_square(1, 1, 2, 2)]), hydric_rating=False),
        _soil_survey_row(mupolygonkey="poly-3", geometry_wkb=wkb_polygon([_square(2, 2, 3, 3)]), hydric_rating=None),
    ]
    store, path_for = _write_release(tmp_path, day=RELEASE_DAY, parts=[rows])

    written = read_written_soil_survey_area_summaries(store, storage_options={}, path_for=path_for)

    area = written.areas["ID001"]
    assert area.delineation_count == EXPECTED_DELINEATION_COUNT
    assert area.hydric_true_count == 1
    assert area.hydric_false_count == 1
    assert area.hydric_unknown_count == 1


def test_no_release_ever_written_is_an_honest_gap(tmp_path: Path) -> None:
    backend = RecordingBackend()
    store = ObjectStore(backend)

    def _path_for(relative_path: str) -> str:
        return str(tmp_path / relative_path)

    written = read_written_soil_survey_area_summaries(store, storage_options={}, path_for=_path_for)

    assert written.release_day is None
    assert written.areas == {}


# --- validation: T-SQL / SDA response plumbing ---------------------------------------------------


def test_validated_area_symbol_accepts_real_shapes_and_refuses_injection_attempts() -> None:
    assert _validated_area_symbol("ID001") == "ID001"
    assert _validated_area_symbol("OR605") == "OR605"
    for bad in ("id001", "ID001'; DROP TABLE mupolygon; --", "ID", "", "ID00001234"):
        with pytest.raises(SoilSurveyValidationError, match="areasymbol"):
            _validated_area_symbol(bad)


def test_parse_survey_area_vintage_discards_the_clock_time() -> None:
    assert _parse_survey_area_vintage("8/27/2025 8:27:08 PM") == datetime(2025, 8, 27, tzinfo=UTC)


def test_parse_survey_area_vintage_refuses_non_us_locale_text() -> None:
    with pytest.raises(SoilSurveySdaResponseError, match="not US-locale date text"):
        _parse_survey_area_vintage("2025-08-27")


def test_parse_sda_table_response_maps_header_to_the_one_data_row() -> None:
    payload = {"Table": [["delineation_count", "saverest"], ["42", "8/27/2025 8:27:08 PM"]]}

    assert _parse_sda_table_response(payload) == {"delineation_count": "42", "saverest": "8/27/2025 8:27:08 PM"}


def test_parse_sda_table_response_with_no_data_row_yields_nulls() -> None:
    payload = {"Table": [["delineation_count", "saverest"]]}

    assert _parse_sda_table_response(payload) == {"delineation_count": None, "saverest": None}


async def test_httpx_sda_client_parses_a_mocked_response_without_touching_the_network() -> None:
    captured: dict[str, object] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = request.content
        body = {"Table": [["delineation_count", "saverest"], ["42", "8/27/2025 8:27:08 PM"]]}
        return httpx.Response(200, json=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(_handler)) as client:
        sda_client = HttpxSoilSurveySdaClient(client=client)
        summary = await sda_client.fetch_survey_area_summary("ID001")

    # httpx lower-cases the host on the wire; the endpoint constant's casing is cosmetic only.
    assert captured["url"] == "https://sdmdataaccess.nrcs.usda.gov/Tabular/post.rest"
    assert summary == SdaSurveyAreaSummary(
        area_symbol="ID001",
        delineation_count=42,
        saverest=datetime(2025, 8, 27, tzinfo=UTC),
        raw_response='{"delineation_count": "42", "saverest": "8/27/2025 8:27:08 PM"}',
    )


async def test_httpx_sda_client_refuses_to_embed_an_invalid_area_symbol() -> None:
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, json={"Table": []}))
    async with httpx.AsyncClient(transport=transport) as client:
        sda_client = HttpxSoilSurveySdaClient(client=client)
        with pytest.raises(SoilSurveyValidationError, match="areasymbol"):
            await sda_client.fetch_survey_area_summary("id'; DROP TABLE mupolygon; --")


# --- validation: full reconciliation against a faked SDA client ---------------------------------


class FakeSdaClient:
    """A `SoilSurveySdaClient` driven entirely by a canned table, no network involved."""

    def __init__(self, responses: dict[str, SdaSurveyAreaSummary | Exception]) -> None:
        self._responses = responses
        self.calls: list[str] = []

    async def fetch_survey_area_summary(self, area_symbol: str) -> SdaSurveyAreaSummary:
        self.calls.append(area_symbol)
        outcome = self._responses[area_symbol]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _sda(area_symbol: str, *, delineation_count: int, saverest: datetime | None) -> SdaSurveyAreaSummary:
    return SdaSurveyAreaSummary(
        area_symbol=area_symbol, delineation_count=delineation_count, saverest=saverest, raw_response="{}"
    )


_ORIGINAL_VINTAGE = datetime(2025, 8, 26, tzinfo=UTC)


def _validation_row(
    mupolygonkey: str,
    square: tuple[float, float, float, float],
    area: str,
    *,
    vintage: datetime = _ORIGINAL_VINTAGE,
) -> dict[str, object]:
    return _soil_survey_row(
        mupolygonkey=mupolygonkey,
        geometry_wkb=wkb_polygon([_square(*square)]),
        survey_area_symbol=area,
        survey_area_vintage=vintage,
    )


def _written_release_for_validation(tmp_path: Path) -> tuple[ObjectStore, Callable[[str], str]]:
    rows = [
        _validation_row("id-1", (0, 0, 1, 1), "ID001"),
        _validation_row("id-2", (1, 1, 2, 2), "ID001"),
        _validation_row("id-3", (2, 2, 3, 3), "ID001"),
        _validation_row("or-1", (3, 3, 4, 4), "OR605", vintage=datetime(2025, 9, 1, tzinfo=UTC)),
        _validation_row("nv-1", (4, 4, 5, 5), "NV003"),
        _validation_row("ut-1", (5, 5, 6, 6), "UT002"),
    ]
    return _write_release(tmp_path, day=RELEASE_DAY, parts=[rows])


async def test_a_clean_release_has_no_findings(tmp_path: Path) -> None:
    store, path_for = _written_release_for_validation(tmp_path)
    fake = FakeSdaClient(
        {
            "ID001": _sda("ID001", delineation_count=3, saverest=datetime(2025, 8, 26, tzinfo=UTC)),
            "OR605": _sda("OR605", delineation_count=1, saverest=datetime(2025, 9, 1, tzinfo=UTC)),
        }
    )

    report = await validate_soil_survey_release(
        store, fake, survey_area_symbols=["ID001", "OR605"], storage_options={}, path_for=path_for
    )

    assert report.passed
    assert report.release_day == RELEASE_DAY


async def test_reconciliation_names_every_kind_of_gap_and_isolates_a_source_failure(tmp_path: Path) -> None:
    store, path_for = _written_release_for_validation(tmp_path)
    fake = FakeSdaClient(
        {
            "ID001": _sda("ID001", delineation_count=3, saverest=datetime(2025, 8, 26, tzinfo=UTC)),  # clean
            "OR605": _sda("OR605", delineation_count=2, saverest=datetime(2025, 9, 1, tzinfo=UTC)),  # count mismatch
            "WA031": _sda("WA031", delineation_count=5, saverest=datetime(2025, 8, 1, tzinfo=UTC)),  # never written
            "CA649": _sda("CA649", delineation_count=0, saverest=None),  # neither side has it: no finding
            "NV003": _sda("NV003", delineation_count=0, saverest=None),  # written, gone at source
            "UT002": _sda("UT002", delineation_count=1, saverest=datetime(2026, 3, 19, tzinfo=UTC)),  # republished
            "MT004": RuntimeError("USDA SDA is unreachable"),  # per-area failure
        }
    )

    report = await validate_soil_survey_release(
        store,
        fake,
        survey_area_symbols=["ID001", "OR605", "WA031", "CA649", "NV003", "UT002", "MT004"],
        storage_options={},
        path_for=path_for,
    )

    findings_by_area = {finding.area_symbol: finding for finding in report.findings}
    assert set(findings_by_area) == {"OR605", "WA031", "NV003", "UT002", "MT004"}
    assert findings_by_area["OR605"].kind == "delineation_count_mismatch"
    assert findings_by_area["WA031"].kind == "area_not_written"
    assert findings_by_area["NV003"].kind == "area_not_at_source"
    assert findings_by_area["UT002"].kind == "vintage_stale"
    assert findings_by_area["MT004"].kind == "source_query_failed"
    for finding in report.findings:
        assert finding.lane == SOIL_SURVEY_STREAM
        assert finding.area_symbol in finding.detail
    # Every OTHER area was still checked -- one area's failure did not end the run.
    assert set(fake.calls) == {"ID001", "OR605", "WA031", "CA649", "NV003", "UT002", "MT004"}


async def test_nothing_written_yet_is_reported_without_ever_calling_the_source(tmp_path: Path) -> None:
    backend = RecordingBackend()
    store = ObjectStore(backend)
    fake = FakeSdaClient({})

    def _path_for(relative_path: str) -> str:
        return str(tmp_path / relative_path)

    report = await validate_soil_survey_release(
        store, fake, survey_area_symbols=["ID001", "OR605"], storage_options={}, path_for=_path_for
    )

    assert report.release_day is None
    assert {finding.kind for finding in report.findings} == {"no_release_written"}
    assert fake.calls == []


async def test_an_empty_area_list_is_refused(tmp_path: Path) -> None:
    store, path_for = _written_release_for_validation(tmp_path)
    fake = FakeSdaClient({})

    with pytest.raises(SoilSurveyValidationError, match="at least one survey area"):
        await validate_soil_survey_release(store, fake, survey_area_symbols=[], storage_options={}, path_for=path_for)


async def test_too_many_areas_in_one_run_is_refused(tmp_path: Path) -> None:
    store, path_for = _written_release_for_validation(tmp_path)
    fake = FakeSdaClient({})
    too_many = [f"ID{i:03d}" for i in range(MAX_VALIDATION_AREAS + 1)]

    with pytest.raises(SoilSurveyValidationError, match=str(MAX_VALIDATION_AREAS)):
        await validate_soil_survey_release(
            store, fake, survey_area_symbols=too_many, storage_options={}, path_for=path_for
        )

    assert fake.calls == []


async def test_an_invalid_area_symbol_is_refused_before_any_read_or_call(tmp_path: Path) -> None:
    store, path_for = _written_release_for_validation(tmp_path)
    fake = FakeSdaClient({})

    with pytest.raises(SoilSurveyValidationError, match="areasymbol"):
        await validate_soil_survey_release(
            store, fake, survey_area_symbols=["id001"], storage_options={}, path_for=path_for
        )

    assert fake.calls == []

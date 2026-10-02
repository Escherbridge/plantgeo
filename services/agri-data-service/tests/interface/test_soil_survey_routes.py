"""SSURGO admitted-release route: gate order, and every gate above it refuses without I/O.

Port slice S3. Each gate (region binding, admission pin, zoom) is proven to refuse BEFORE the next
one down the list is even evaluated, by making the next gate's own dependency fail loudly if it is
ever reached (`_refuse`, mirroring `tests/direct/soil_survey/test_stage.py`'s own `_refuse` helper).
No test here opens a real bucket or a real DuckDB connection.
"""

from __future__ import annotations

import json
from http import HTTPStatus
from types import SimpleNamespace
from typing import NoReturn

import pytest
from sanic.request.parameters import RequestParameters

from agri_data_service.foundation.soil_survey.receipts import digest
from agri_data_service.foundation.soil_survey.release import NATIVE_RUNG
from agri_data_service.interface.http.soil_survey import point_soil_survey, query_soil_survey, status_soil_survey
from agri_data_service.pipeline.parquet.availability_storage import StoredAvailabilityObject


def _refuse(*arguments: object, **keywords: object) -> NoReturn:
    raise AssertionError(f"must not be called: {arguments} {keywords}")


def _request(args: dict[str, list[str]]) -> SimpleNamespace:
    return SimpleNamespace(args=RequestParameters(args))


async def test_unbound_region_answers_without_touching_the_pin_or_storage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("agri_data_service.interface.http.soil_survey.is_layer_bound", lambda _region, _slug: False)
    monkeypatch.setattr("agri_data_service.interface.http.soil_survey.load_region", object)
    monkeypatch.setattr(
        "agri_data_service.interface.http.soil_survey.settings",
        SimpleNamespace(require_object_store=_refuse),  # no ssurgo_admitted_release_sha256 attribute at all
    )
    # A real Sanic request delivers `?bbox=0,0,1,1` as ONE value containing commas, never four
    # separate values for the same key -- the route's own repeated-parameter guard would 400 on
    # the latter shape before ever reaching the region gate this test exercises.
    response = await query_soil_survey(_request({"bbox": ["0,0,1,1"]}))
    result = json.loads(response.body)
    assert response.status == HTTPStatus.OK
    assert result["availability"] == "unavailable"
    assert result["reason"] == "no_source_bound_in_region"
    assert result["spatialCoverage"] is None


async def test_unadmitted_viewport_does_not_open_storage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("agri_data_service.interface.http.soil_survey.load_region", object)
    monkeypatch.setattr("agri_data_service.interface.http.soil_survey.is_layer_bound", lambda _region, _slug: True)
    monkeypatch.setattr(
        "agri_data_service.interface.http.soil_survey.settings",
        SimpleNamespace(ssurgo_admitted_release_sha256=None, require_object_store=_refuse),
    )
    response = await query_soil_survey(_request({"bbox": ["0,0,1,1"], "zoom": ["13"]}))
    result = json.loads(response.body)
    assert response.status == HTTPStatus.OK
    assert result["availability"] == "unavailable"
    assert result["reason"] == "soil_survey_release_not_admitted"
    assert result["temporalScope"] == {"kind": "static_reference", "selectedDaySupported": False}


@pytest.mark.parametrize("zoom", ["0", "9", "12"])
async def test_below_native_rung_answers_zoom_in_without_touching_storage(
    monkeypatch: pytest.MonkeyPatch, zoom: str
) -> None:
    monkeypatch.setattr("agri_data_service.interface.http.soil_survey.is_layer_bound", lambda _region, _slug: True)
    monkeypatch.setattr("agri_data_service.interface.http.soil_survey.load_region", object)
    monkeypatch.setattr(
        "agri_data_service.interface.http.soil_survey.settings",
        SimpleNamespace(ssurgo_admitted_release_sha256="a" * 64, require_object_store=_refuse),
    )
    response = await query_soil_survey(_request({"bbox": ["0,0,1,1"], "zoom": [zoom]}))
    result = json.loads(response.body)
    assert response.status == HTTPStatus.OK
    assert result["availability"] == "unavailable"
    assert result["reason"] == "soil_survey_zoom_in"
    assert result["servedZoom"] == NATIVE_RUNG


async def test_invalid_point_is_a_request_fault_before_any_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("agri_data_service.interface.http.soil_survey.is_layer_bound", _refuse)
    response = await point_soil_survey(_request({"lon": ["nan"], "lat": ["43"]}))
    assert response.status == HTTPStatus.BAD_REQUEST
    assert json.loads(response.body) == {"error": "soil_survey_invalid_request"}


async def test_repeated_bbox_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("agri_data_service.interface.http.soil_survey.is_layer_bound", _refuse)
    response = await query_soil_survey(_request({"bbox": ["0,0,1,1", "2,2,3,3"]}))
    assert response.status == HTTPStatus.BAD_REQUEST


async def test_unknown_query_parameter_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("agri_data_service.interface.http.soil_survey.is_layer_bound", _refuse)
    response = await query_soil_survey(_request({"bbox": ["0,0,1,1"], "day": ["2020-01-01"]}))
    assert response.status == HTTPStatus.BAD_REQUEST
    assert json.loads(response.body) == {"error": "soil_survey_invalid_request"}


async def test_an_unconfigured_object_store_answers_a_clean_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Past the region/pin/zoom gates, any read fault -- here `require_object_store` naming an
    unset credential -- is a uniform 503, never a 500 (`interface/http/AGENTS.md`)."""
    monkeypatch.setattr("agri_data_service.interface.http.soil_survey.is_layer_bound", lambda _region, _slug: True)
    monkeypatch.setattr("agri_data_service.interface.http.soil_survey.load_region", object)

    def _raise_unset_credential() -> None:
        raise ValueError("OBJECT_STORE_ACCESS_KEY_ID is not set")

    monkeypatch.setattr(
        "agri_data_service.interface.http.soil_survey.settings",
        SimpleNamespace(ssurgo_admitted_release_sha256="a" * 64, require_object_store=_raise_unset_credential),
    )
    response = await query_soil_survey(_request({"bbox": ["0,0,1,1"], "zoom": ["13"]}))
    assert response.status == HTTPStatus.SERVICE_UNAVAILABLE
    assert json.loads(response.body) == {"error": "soil_survey_read_refused"}


def _release_payload(*, region: str = "pnw") -> bytes:
    stamp = "2026-09-28T12:00:00+00:00"
    vintage = "2025-08-27"
    blob = {"sha256": "b" * 64, "byte_count": 2}
    return json.dumps(
        {
            "scope": {
                "response": blob,
                "query_sha256": "c" * 64,
                "checked_at": stamp,
                "areas": [{"area": "ID001", "saverest": vintage}, {"area": "ID002", "saverest": vintage}],
                "envelope": [0, 0, 1, 1],
                "region": region,
            },
            "shards": [
                {
                    "shard": "ID-1",
                    "manifest": blob,
                    "release_day": vintage,
                    "captured_at": stamp,
                    "areas": [
                        {
                            "area": "ID001",
                            "saverest": vintage,
                            "native_rows": 2,
                            "repaired_rows": 0,
                            "labelled_rows": 0,
                        }
                    ],
                    "bbox": [0, 0, 1, 1],
                }
            ],
            "pending_areas": ["ID002"],
            "source_evidence": "staged",
            "release_day": vintage,
            "captured_at": stamp,
        }
    ).encode()


class _StatusStorage:
    def __init__(self, payload: bytes | None, error: Exception | None = None) -> None:
        self.payload = payload
        self.error = error
        self.reads: list[str] = []

    def read(self, key: str, *, max_bytes: int) -> StoredAvailabilityObject | None:
        self.reads.append(key)
        if self.error is not None:
            raise self.error
        if self.payload is None:
            return None
        assert len(self.payload) <= max_bytes
        return StoredAvailabilityObject(payload=self.payload, etag=digest(self.payload))


def _configure_status(monkeypatch: pytest.MonkeyPatch, storage: _StatusStorage, pin: str) -> None:
    module = "agri_data_service.interface.http.soil_survey"
    monkeypatch.setattr(f"{module}.load_region", lambda: SimpleNamespace(slug="pnw"))
    monkeypatch.setattr(f"{module}.is_layer_bound", lambda _region, _slug: True)
    monkeypatch.setattr(
        f"{module}.settings",
        SimpleNamespace(ssurgo_admitted_release_sha256=pin, require_object_store=object, object_store_prefix="test"),
    )
    monkeypatch.setattr(f"{module}.BotoAvailabilityStorage.from_credentials", lambda *_args, **_kwargs: storage)
    monkeypatch.setattr(f"{module}.gather_admitted_soil_survey_viewport", _refuse)
    monkeypatch.setattr(f"{module}.run_serving_read", _refuse)


async def test_status_verifies_only_the_pinned_index_and_retains_static_partial_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _release_payload()
    storage = _StatusStorage(payload)
    pin = digest(payload)
    _configure_status(monkeypatch, storage, pin)

    response = await status_soil_survey(_request({}))
    result = json.loads(response.body)

    assert response.status == HTTPStatus.OK
    assert response.headers["Cache-Control"] == "no-store"
    assert len(storage.reads) == 1
    assert result == {
        "availability": "published",
        "reason": None,
        "regionSlug": "pnw",
        "requiredRungs": [13],
        "temporalScope": {"kind": "static_reference", "selectedDaySupported": False},
        "publication": {
            "revision": pin,
            "releaseDay": "2025-08-27",
            "capturedAt": "2026-09-28T12:00:00+00:00",
            "declaredAreaCount": 2,
            "publishedAreaCount": 1,
            "pendingAreaCount": 1,
        },
    }


@pytest.mark.parametrize("bound", [False, True])
async def test_status_unbound_or_unadmitted_never_opens_storage(
    monkeypatch: pytest.MonkeyPatch, *, bound: bool
) -> None:
    module = "agri_data_service.interface.http.soil_survey"
    monkeypatch.setattr(f"{module}.load_region", lambda: SimpleNamespace(slug="pnw"))
    monkeypatch.setattr(f"{module}.is_layer_bound", lambda _region, _slug: bound)
    monkeypatch.setattr(
        f"{module}.settings", SimpleNamespace(ssurgo_admitted_release_sha256=None, require_object_store=_refuse)
    )
    response = await status_soil_survey(_request({}))
    result = json.loads(response.body)
    assert response.status == HTTPStatus.OK
    assert result["availability"] == "unavailable"
    assert result["publication"] is None
    assert result["reason"] == ("soil_survey_release_not_admitted" if bound else "no_source_bound_in_region")


@pytest.mark.parametrize("fault", ["missing", "corrupt", "malformed", "transport", "foreign_region"])
async def test_status_index_fault_is_a_serving_refusal_not_an_absent_publication(
    monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    payload = _release_payload(region="other-region" if fault == "foreign_region" else "pnw")
    if fault == "malformed":
        payload = b"{}"
    pin = digest(payload)
    stored_payload = None if fault == "missing" else b"tampered" if fault == "corrupt" else payload
    storage = _StatusStorage(stored_payload, TimeoutError() if fault == "transport" else None)
    _configure_status(monkeypatch, storage, pin)
    response = await status_soil_survey(_request({}))
    assert response.status == HTTPStatus.SERVICE_UNAVAILABLE
    assert json.loads(response.body) == {"error": "soil_survey_read_refused"}


@pytest.mark.parametrize("parameter", ["day", "date", "zoom", "bbox"])
async def test_status_rejects_viewport_and_day_parameters_before_loading_a_region(
    monkeypatch: pytest.MonkeyPatch, parameter: str
) -> None:
    monkeypatch.setattr("agri_data_service.interface.http.soil_survey.load_region", _refuse)
    response = await status_soil_survey(_request({parameter: ["2026-09-28"]}))
    assert response.status == HTTPStatus.BAD_REQUEST

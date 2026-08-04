"""Tests for the MTBS capture: paging that is provably complete, honest release dating, no fabricated severity.

Database-free by construction -- this module never requests the ``agri_db`` fixtures, so it can
never contribute a silent skip to the sweep gate in ``conftest.pytest_sessionfinish``.
"""

# ruff: noqa: PLR2004

import asyncio
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from agri_data_service.execution.contracts import MAX_SOURCE_GEOJSON_FEATURES
from agri_data_service.execution.source_ingestion import SourceIngestionPlan
from agri_data_service.ingest.identity import MissingNativeKeyError, build_burn_severity_identity
from agri_data_service.ingest.mtbs import (
    INLINE_PUBLICATION_BYTE_LIMIT,
    MIN_PUBLICATION_AGE,
    MIN_RELEASE_LEAD,
    MTBS_ANNUAL_RELEASE_DATES,
    MTBS_CITATION,
    MTBS_LICENSE_NAME,
    MTBS_LICENSE_URL,
    PACIFIC_NORTHWEST_BBOX,
    SEVERITY_CLASS_BY_CODE,
    MtbsBurnSeverityRecord,
    MtbsDuplicateFeatureError,
    MtbsFeatureShapeError,
    MtbsReleaseNotPublishedError,
    MtbsReleaseTooLargeError,
    MtbsReleaseWindowError,
    MtbsTruncatedCaptureError,
    MtbsUnknownSeverityCodeError,
    SourceReview,
    bounding_box_token,
    build_mtbs_record,
    build_release_identifier,
    build_release_payload,
    build_source_ingestion_plan,
    capture_release,
    fetch_release_features,
    inline_bbox_value,
    parse_bounding_box,
    parse_mtbs_ignition_date,
    requires_object_storage,
    resolve_burn_severity_class,
    resolve_data_available_at,
    resolve_requested_years,
    validate_release_window,
)

# The `license_snapshot` column that `publish_source_release` copies `license_name` into.
LICENSE_SNAPSHOT_COLUMN_LENGTH = 255

# Recorded verbatim on 2026-08-03 from the live authoritative service,
# https://apps.fs.usda.gov/arcx/rest/services/EDW/EDW_MTBS_01/MapServer/63/query, with
# `where=year=2022`, the Pacific Northwest envelope, `orderByFields=fire_id` and `outFields=*`.
# Field names are lowercase and there is no `severity` member on any of them -- see
# `test_recorded_layer_publishes_no_severity_or_release_field`.
RECORDED_2022_PNW_PROPERTIES: list[dict[str, Any]] = [
    {
        "objectid": 640588,
        "fire_id": "ID4262711440320220906",
        "fire_name": "POWERLINE",
        "year": 2022,
        "startmonth": 9,
        "startday": 6,
        "fire_type": "Wildfire",
        "acres": 1100.0,
        "irwinid": "CD08EB65-786A-421F-AA98-94F00CF0A4DB",
        "map_id": 10029267,
        "map_prog": "MTBS",
        "asmnt_type": "Initial",
        "ig_date": 20220906,
        "pre_id": "804003020220904",
        "post_id": "804003020221006",
        "perim_id": None,
        "dnbr_offst": -1,
        "dnbr_stddv": 16,
        "nodata_threshold": -970,
        "greenness_threshold": -150,
        "low_threshold": 70,
        "moderate_threshold": 9999,
        "high_threshold": 9999,
        "comments": None,
        "latitude": 42.62650919,
        "longitude": -114.42850061,
    },
    {
        "objectid": 640608,
        "fire_id": "ID4262911434920220629",
        "fire_name": "SUGAR LOAF",
        "year": 2022,
        "startmonth": 6,
        "startday": 29,
        "fire_type": "Wildfire",
        "acres": 3200.0,
        "irwinid": "208BE3AD-4796-4FFF-B13B-94246D361D70",
        "map_id": 10029272,
        "map_prog": "MTBS",
        "asmnt_type": "Initial",
        "ig_date": 20220629,
        "pre_id": "904003020220624",
        "post_id": "904003020220710",
        "perim_id": None,
        "dnbr_offst": -11,
        "dnbr_stddv": 15,
        "nodata_threshold": -970,
        "greenness_threshold": -150,
        "low_threshold": 44,
        "moderate_threshold": 9999,
        "high_threshold": 9999,
        "comments": None,
        "latitude": 42.61748738,
        "longitude": -114.31309825,
    },
    {
        "objectid": 640566,
        "fire_id": "ID4265011421320220908",
        "fire_name": "EDEN 2",
        "year": 2022,
        "startmonth": 9,
        "startday": 8,
        "fire_type": "Wildfire",
        "acres": 13640.0,
        "irwinid": "ADFD2874-E130-41E3-89EE-3489558E8ECD",
        "map_id": 10029263,
        "map_prog": "MTBS",
        "asmnt_type": "Initial",
        "ig_date": 20220908,
        "pre_id": "804003020220904",
        "post_id": "804003020221006",
        "perim_id": None,
        "dnbr_offst": 13,
        "dnbr_stddv": 27,
        "nodata_threshold": -970,
        "greenness_threshold": -150,
        "low_threshold": 93,
        "moderate_threshold": 9999,
        "high_threshold": 9999,
        "comments": None,
        "latitude": 42.63081738,
        "longitude": -114.08036445,
    },
]

# The complete field list the live layer advertises at `<layer>?f=json`, recorded 2026-08-03.
# Pinned so a schema flip is a test failure rather than a silent normalisation change, and so the
# absence of both a `severity` member and any per-fire release/version member stays documented.
RECORDED_LAYER_FIELD_NAMES: tuple[str, ...] = (
    "objectid",
    "fire_id",
    "fire_name",
    "year",
    "startmonth",
    "startday",
    "fire_type",
    "acres",
    "irwinid",
    "map_id",
    "map_prog",
    "asmnt_type",
    "ig_date",
    "pre_id",
    "post_id",
    "perim_id",
    "dnbr_offst",
    "dnbr_stddv",
    "nodata_threshold",
    "greenness_threshold",
    "low_threshold",
    "moderate_threshold",
    "high_threshold",
    "comments",
    "latitude",
    "longitude",
    "shape",
    "st_area(shape)",
    "st_perimeter(shape)",
)


def _polygon(index: int) -> dict[str, Any]:
    """A small valid closed ring; the real perimeters carry thousands of vertices each."""
    west = -120.0 + index * 0.1
    south = 44.0 + index * 0.1
    return {
        "type": "Polygon",
        "coordinates": [
            [
                [west, south],
                [west + 0.05, south],
                [west + 0.05, south + 0.05],
                [west, south + 0.05],
                [west, south],
            ]
        ],
    }


def _feature(properties: dict[str, Any], index: int = 0) -> dict[str, Any]:
    """Pair a recorded property bag with a geometry small enough to inline."""
    return {"type": "Feature", "geometry": _polygon(index), "properties": dict(properties)}


def _recorded_features() -> list[dict[str, Any]]:
    return [_feature(properties, index) for index, properties in enumerate(RECORDED_2022_PNW_PROPERTIES)]


def _cohort(size: int) -> list[dict[str, Any]]:
    """Build an ordered cohort for the paging tests.

    Paging correctness depends only on identifier uniqueness and a stable ascending sort, so the
    recorded records are extended with additional ones whose ids continue that sort. Normalisation
    is exercised against the recorded records themselves elsewhere in this module.
    """
    features = _recorded_features()
    for index in range(len(features), size):
        properties = dict(RECORDED_2022_PNW_PROPERTIES[index % len(RECORDED_2022_PNW_PROPERTIES)])
        properties["fire_id"] = f"ID42{index:03d}11440320220906"
        features.append(_feature(properties, index))
    return sorted(features, key=lambda feature: str(feature["properties"]["fire_id"]))


class RecordedMtbsService:
    """A recorded MTBS feature service that serves one ordered cohort in pages."""

    def __init__(
        self,
        features: list[dict[str, Any]],
        *,
        reported_count: int | None = None,
        always_flag_truncation: bool = False,
        transient_failures: int = 0,
    ) -> None:
        self.features = features
        self.reported_count = len(features) if reported_count is None else reported_count
        self.always_flag_truncation = always_flag_truncation
        self.remaining_transient_failures = transient_failures
        self.count_requests = 0
        self.page_requests: list[dict[str, str]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        parameters = dict(request.url.params)
        if self.remaining_transient_failures > 0:
            self.remaining_transient_failures -= 1
            return httpx.Response(500, text="Internal Server Error", request=request)
        if parameters.get("returnCountOnly") == "true":
            self.count_requests += 1
            return httpx.Response(200, json={"count": self.reported_count}, request=request)
        self.page_requests.append(parameters)
        offset = int(parameters["resultOffset"])
        page_size = int(parameters["resultRecordCount"])
        page = self.features[offset : offset + page_size]
        truncated = self.always_flag_truncation or offset + page_size < len(self.features)
        return httpx.Response(
            200,
            json={
                "type": "FeatureCollection",
                "features": page,
                "properties": {"exceededTransferLimit": truncated},
            },
            request=request,
        )

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


def _page_offsets(service: RecordedMtbsService) -> list[int]:
    return [int(parameters["resultOffset"]) for parameters in service.page_requests]


def test_three_pages_reassemble_into_one_complete_cohort_at_the_right_offsets() -> None:
    features = _cohort(7)
    service = RecordedMtbsService(features)

    async def run() -> tuple[list[dict[str, Any]], int]:
        async with service.client() as client:
            return await fetch_release_features(2022, PACIFIC_NORTHWEST_BBOX, client=client, page_size=3)

    captured, authoritative_count = asyncio.run(run())

    assert authoritative_count == 7
    assert len(captured) == 7
    assert _page_offsets(service) == [0, 3, 6]
    assert service.count_requests == 1
    fire_identifiers = [feature["properties"]["fire_id"] for feature in captured]
    assert len(set(fire_identifiers)) == len(fire_identifiers)
    assert fire_identifiers == sorted(fire_identifiers)
    # `orderByFields` is the only defence against AGOL's unordered paging.
    assert {parameters["orderByFields"] for parameters in service.page_requests} == {"fire_id"}
    assert {parameters["outFields"] for parameters in service.page_requests} == {"*"}


def test_paging_that_falls_one_short_of_the_authoritative_count_raises() -> None:
    features = _cohort(7)
    service = RecordedMtbsService(features, reported_count=8)

    async def run() -> None:
        async with service.client() as client:
            await fetch_release_features(2022, PACIFIC_NORTHWEST_BBOX, client=client, page_size=3)

    with pytest.raises(MtbsTruncatedCaptureError, match="paged 7 features but the service counts 8"):
        asyncio.run(run())


def test_a_final_page_still_flagging_exceeded_transfer_limit_raises() -> None:
    # The count agrees with what was delivered, so only the service's own flag catches this.
    features = _cohort(6)
    service = RecordedMtbsService(features, always_flag_truncation=True)

    async def run() -> None:
        async with service.client() as client:
            await fetch_release_features(2022, PACIFIC_NORTHWEST_BBOX, client=client, page_size=3)

    with pytest.raises(MtbsTruncatedCaptureError, match="exceededTransferLimit"):
        asyncio.run(run())


def test_a_fire_id_repeated_across_pages_raises_because_the_order_was_unstable() -> None:
    features = _cohort(6)
    features[3] = _feature(dict(features[0]["properties"]), 3)
    service = RecordedMtbsService(features)

    async def run() -> None:
        async with service.client() as client:
            await fetch_release_features(2022, PACIFIC_NORTHWEST_BBOX, client=client, page_size=3)

    with pytest.raises(MtbsDuplicateFeatureError, match="repeated across pages"):
        asyncio.run(run())


def test_a_cohort_above_the_payload_feature_contract_raises_before_paging() -> None:
    service = RecordedMtbsService(_cohort(3), reported_count=MAX_SOURCE_GEOJSON_FEATURES + 1)

    async def run() -> None:
        async with service.client() as client:
            await fetch_release_features(2022, PACIFIC_NORTHWEST_BBOX, client=client, page_size=3)

    with pytest.raises(MtbsReleaseTooLargeError):
        asyncio.run(run())
    assert service.page_requests == []


def test_data_available_at_is_the_release_date_and_never_an_ignition_date() -> None:
    data_available_at = resolve_data_available_at(2022)

    assert data_available_at == datetime(2024, 8, 22, tzinfo=UTC)
    ignition_dates = {parse_mtbs_ignition_date(properties["ig_date"]) for properties in RECORDED_2022_PNW_PROPERTIES}
    assert data_available_at.date() not in ignition_dates
    # Every recorded ignition is in 2022; the release is nearly two years later.
    assert {ignition.year for ignition in ignition_dates} == {2022}
    assert data_available_at.date() - max(ignition_dates) > MIN_RELEASE_LEAD


@pytest.mark.parametrize("ignition_year", [1984, 2017, 2019, 2023, 2024, 2025, 2026])
def test_a_fire_year_without_an_established_release_date_raises_with_no_fallback(ignition_year: int) -> None:
    with pytest.raises(MtbsReleaseNotPublishedError, match="no established release publication date"):
        resolve_data_available_at(ignition_year)


def test_every_tabled_release_date_leads_its_own_fire_year_by_more_than_the_floor() -> None:
    assert MTBS_ANNUAL_RELEASE_DATES
    for ignition_year, release_date in MTBS_ANNUAL_RELEASE_DATES.items():
        last_possible_ignition = date(ignition_year, 12, 31)
        assert release_date > last_possible_ignition
        assert release_date - last_possible_ignition > MIN_RELEASE_LEAD
    # Fire years MTBS is still mapping must stay out of the table.
    assert 2023 not in MTBS_ANNUAL_RELEASE_DATES
    assert 2024 not in MTBS_ANNUAL_RELEASE_DATES


def test_the_release_window_tripwires_reject_ignition_shaped_and_now_shaped_dates() -> None:
    observed_to = datetime(2022, 12, 31, tzinfo=UTC)
    validate_release_window(datetime(2024, 8, 22, tzinfo=UTC), observed_to)

    with pytest.raises(MtbsReleaseWindowError, match="cannot precede the fires it maps"):
        validate_release_window(datetime(2022, 6, 1, tzinfo=UTC), observed_to)
    with pytest.raises(MtbsReleaseWindowError, match="day floor"):
        validate_release_window(observed_to + timedelta(days=30), observed_to)

    now = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)
    with pytest.raises(MtbsReleaseWindowError, match="`now\\(\\)` fallback"):
        validate_release_window(now - MIN_PUBLICATION_AGE / 2, observed_to, now=now)


def test_an_unknown_severity_code_raises_rather_than_becoming_unburned() -> None:
    for severity_code in (0, 6, 9, -1, "3", 2.5, True):
        with pytest.raises(MtbsUnknownSeverityCodeError):
            resolve_burn_severity_class({"severity": severity_code})


def test_a_published_severity_code_decodes_to_its_recorded_class() -> None:
    assert SEVERITY_CLASS_BY_CODE == {
        1: "unburned",
        2: "low",
        3: "moderate",
        4: "high",
        5: "increased_greenness",
    }
    for code, expected in SEVERITY_CLASS_BY_CODE.items():
        assert resolve_burn_severity_class({"Severity": code}) == expected


def test_an_absent_severity_attribute_stays_unavailable_and_never_becomes_unburned() -> None:
    # This is the regression against the retired `mtbs.ts:78` `?? "unburned"`: the live burned-area
    # boundary layer publishes no severity attribute at all, so that fallback fabricated a
    # legitimate-looking class for every single feature.
    for properties in RECORDED_2022_PNW_PROPERTIES:
        assert "severity" not in properties
        assert resolve_burn_severity_class(properties) is None
    assert resolve_burn_severity_class({"severity": None}) is None


def test_recorded_layer_publishes_no_severity_or_release_field() -> None:
    assert "severity" not in RECORDED_LAYER_FIELD_NAMES
    assert not [name for name in RECORDED_LAYER_FIELD_NAMES if "release" in name or "version" in name]
    # The fields the module reads must all really exist on the layer.
    for field_name in ("fire_id", "year", "ig_date", "fire_name", "fire_type", "asmnt_type", "acres"):
        assert field_name in RECORDED_LAYER_FIELD_NAMES
    # The per-fire mapping fingerprint the Type-2 chain versions on, in place of a release field.
    for field_name in ("map_id", "asmnt_type", "pre_id", "post_id", "perim_id"):
        assert field_name in RECORDED_LAYER_FIELD_NAMES


def test_a_feature_without_a_fire_id_raises_and_no_key_is_synthesised() -> None:
    for broken in ({}, {"fire_id": None}, {"fire_id": ""}, {"fire_id": "   "}):
        with pytest.raises(MissingNativeKeyError):
            build_mtbs_record(_feature(dict(broken)), 2022)


def test_the_natural_key_is_the_namespaced_fire_id_from_lane_as_builder() -> None:
    for index, properties in enumerate(RECORDED_2022_PNW_PROPERTIES):
        record = build_mtbs_record(_feature(properties, index), 2022)
        fire_identifier = properties["fire_id"]
        assert record.natural_key == f"mtbs:{fire_identifier}"
        assert record.natural_key == build_burn_severity_identity({"Fire_ID": fire_identifier}).natural_key
        assert record.producer == "mtbs"
        assert record.producer_local_id == fire_identifier


def test_a_recorded_feature_normalises_into_its_complete_warehouse_record() -> None:
    record = build_mtbs_record(_feature(RECORDED_2022_PNW_PROPERTIES[0]), 2022)

    assert isinstance(record, MtbsBurnSeverityRecord)
    assert record.natural_key == "mtbs:ID4262711440320220906"
    assert record.geom_kind == "polygon"
    assert record.geometry["type"] == "Polygon"
    assert record.ignition_date == date(2022, 9, 6)
    assert record.ignition_year == 2022
    assert record.fire_name == "POWERLINE"
    assert record.fire_type == "Wildfire"
    assert record.assessment_type == "Initial"
    assert record.acres == 1100.0
    assert record.severity_class is None
    assert record.severity_thresholds.low_threshold == 70
    assert record.severity_thresholds.dnbr_offset == -1
    assert record.release_identifier == "mtbs-2022-release-2024-08-22"
    assert record.data_available_at == datetime(2024, 8, 22, tzinfo=UTC)
    # The Type-2 signal must move when MTBS re-maps a fire, without comparing geometry floats.
    assert record.mapping_revision.startswith("mtbs-2022-release-2024-08-22|10029267|Initial|")
    assert record.data_available_at.date() != record.ignition_date


def test_a_remap_under_the_same_fire_id_changes_only_the_mapping_revision() -> None:
    original = build_mtbs_record(_feature(RECORDED_2022_PNW_PROPERTIES[0]), 2022)
    remapped_properties = dict(RECORDED_2022_PNW_PROPERTIES[0])
    remapped_properties["map_id"] = 10029999
    remapped_properties["asmnt_type"] = "Extended"
    remapped = build_mtbs_record(_feature(remapped_properties), 2022)

    assert remapped.natural_key == original.natural_key
    assert remapped.mapping_revision != original.mapping_revision


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (20220906, date(2022, 9, 6)),
        ("20220906", date(2022, 9, 6)),
        ("2022-09-06", date(2022, 9, 6)),
        ("2022-09-06T00:00:00Z", date(2022, 9, 6)),
    ],
)
def test_the_integer_ignition_date_the_service_publishes_parses(raw: object, expected: date) -> None:
    assert parse_mtbs_ignition_date(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "   ", True, 20221306, "not-a-date", 3.5])
def test_an_unparseable_ignition_date_raises(raw: object) -> None:
    with pytest.raises(MtbsFeatureShapeError):
        parse_mtbs_ignition_date(raw)


def test_a_non_polygon_geometry_raises_rather_than_being_stored() -> None:
    point = {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [-120.0, 44.0]},
        "properties": dict(RECORDED_2022_PNW_PROPERTIES[0]),
    }
    with pytest.raises(MtbsFeatureShapeError, match="geometry type"):
        build_mtbs_record(point, 2022)
    missing = {"type": "Feature", "properties": dict(RECORDED_2022_PNW_PROPERTIES[0])}
    with pytest.raises(MtbsFeatureShapeError, match="missing its geometry"):
        build_mtbs_record(missing, 2022)


def test_the_governed_license_snapshot_fits_the_column_it_is_copied_into() -> None:
    assert 0 < len(MTBS_LICENSE_NAME) <= LICENSE_SNAPSHOT_COLUMN_LENGTH
    # Both instruments must be named: the federal data licence and the separate hosting terms.
    assert "public domain" in MTBS_LICENSE_NAME
    assert "hosting terms are separate" in MTBS_LICENSE_NAME
    assert MTBS_LICENSE_URL.startswith("https://")
    assert "10.5066/P9IED7RZ" in MTBS_CITATION


def test_the_sidecar_carries_the_release_date_and_the_ignition_window_separately() -> None:
    review = SourceReview(reviewed_at=datetime(2026, 8, 3, tzinfo=UTC), reviewed_by="operator@example.test")
    plan = build_source_ingestion_plan(
        2022,
        PACIFIC_NORTHWEST_BBOX,
        review=review,
        observation_window=(datetime(2022, 6, 29, tzinfo=UTC), datetime(2022, 9, 8, tzinfo=UTC)),
    )

    assert isinstance(plan, SourceIngestionPlan)
    assert plan.source.key == "mtbs-burn-severity"
    assert plan.source.retention_days is None
    assert plan.source.license_name == MTBS_LICENSE_NAME
    assert plan.release.source_version == build_release_identifier(2022)
    assert plan.release.data_available_at == datetime(2024, 8, 22, tzinfo=UTC)
    # `observed_*` is when it happened; `data_available_at` is when we could have known.
    assert plan.release.observed_to == datetime(2022, 9, 8, tzinfo=UTC)
    assert plan.release.data_available_at > plan.release.observed_to
    assert plan.release.query_parameters["orderByFields"] == "fire_id"
    assert plan.release.query_parameters["where"] == "year = 2022"


def test_capture_writes_the_payload_and_only_writes_a_sidecar_the_operator_reviewed(tmp_path: Path) -> None:
    service = RecordedMtbsService(_cohort(5))

    async def run(review: SourceReview | None) -> Any:
        async with service.client() as client:
            return await capture_release(
                2022,
                bounding_box=PACIFIC_NORTHWEST_BBOX,
                output_root=tmp_path,
                client=client,
                review=review,
            )

    unreviewed = asyncio.run(run(None))
    assert unreviewed.authoritative_count == unreviewed.paged_feature_count == 5
    assert unreviewed.record_count == 5
    assert unreviewed.payload_path.exists()
    assert unreviewed.sidecar_path is None
    assert unreviewed.sidecar_skipped_reason is not None
    payload = json.loads(unreviewed.payload_path.read_bytes())
    assert payload["type"] == "FeatureCollection"
    assert len(payload["features"]) == 5
    assert unreviewed.data_available_at == datetime(2024, 8, 22, tzinfo=UTC)
    assert unreviewed.observed_to < unreviewed.data_available_at

    review = SourceReview(reviewed_at=datetime(2026, 8, 3, tzinfo=UTC), reviewed_by="operator@example.test")
    reviewed = asyncio.run(run(review))
    assert reviewed.sidecar_path is not None
    assert reviewed.sidecar_skipped_reason is None
    sidecar = SourceIngestionPlan.model_validate_json(reviewed.sidecar_path.read_bytes())
    assert sidecar.release.data_available_at == datetime(2024, 8, 22, tzinfo=UTC)
    assert sidecar.source.reviewed_by == "operator@example.test"


def test_two_extents_of_one_release_do_not_overwrite_each_other(tmp_path: Path) -> None:
    # Same release identifier, different bounding box: different immutable content, so neither the
    # capture path nor the release-set key may collide.
    wider = (-125.0, 24.0, -93.0, 49.0)
    service = RecordedMtbsService(_cohort(4))

    async def run(bounding_box: tuple[float, float, float, float]) -> Any:
        async with service.client() as client:
            return await capture_release(
                2022,
                bounding_box=bounding_box,
                output_root=tmp_path,
                client=client,
            )

    narrow_capture = asyncio.run(run(PACIFIC_NORTHWEST_BBOX))
    wide_capture = asyncio.run(run(wider))

    assert narrow_capture.release_identifier == wide_capture.release_identifier
    assert narrow_capture.payload_path != wide_capture.payload_path
    assert narrow_capture.payload_path.exists()
    assert wide_capture.payload_path.exists()
    assert bounding_box_token(PACIFIC_NORTHWEST_BBOX) != bounding_box_token(wider)

    review = SourceReview(reviewed_at=datetime(2026, 8, 3, tzinfo=UTC), reviewed_by="operator@example.test")
    narrow_plan = build_source_ingestion_plan(
        2022,
        PACIFIC_NORTHWEST_BBOX,
        review=review,
        observation_window=(datetime(2022, 1, 3, tzinfo=UTC), datetime(2022, 10, 18, tzinfo=UTC)),
    )
    wide_plan = build_source_ingestion_plan(
        2022,
        wider,
        review=review,
        observation_window=(datetime(2022, 1, 3, tzinfo=UTC), datetime(2022, 10, 18, tzinfo=UTC)),
    )
    assert narrow_plan.release_set_key != wide_plan.release_set_key
    # The upstream release itself is the same product regardless of how much of it we pulled.
    assert narrow_plan.release.source_version == wide_plan.release.source_version


def test_capturing_an_unreleased_fire_year_raises_before_any_network_call(tmp_path: Path) -> None:
    service = RecordedMtbsService(_cohort(3))

    async def run() -> None:
        async with service.client() as client:
            await capture_release(
                2023,
                bounding_box=PACIFIC_NORTHWEST_BBOX,
                output_root=tmp_path,
                client=client,
            )

    with pytest.raises(MtbsReleaseNotPublishedError):
        asyncio.run(run())
    assert service.count_requests == 0
    assert not list(tmp_path.rglob("*.geojson"))


def test_the_cli_reads_a_negative_bbox_as_a_value_rather_than_a_flag() -> None:
    assert inline_bbox_value(["--bbox", "-125,42,-111,49", "--release-year", "2022"]) == [
        "--bbox=-125,42,-111,49",
        "--release-year",
        "2022",
    ]
    assert parse_bounding_box("-125,42,-111,49") == PACIFIC_NORTHWEST_BBOX
    assert inline_bbox_value(["--all-releases"]) == ["--all-releases"]


def test_a_transient_server_error_is_retried_rather_than_failing_the_capture() -> None:
    # The live service answers an oversized polygon page with HTTP 500; a multi-release run must
    # survive that rather than losing every cohort after it.
    service = RecordedMtbsService(_cohort(4), transient_failures=2)
    delays: list[float] = []

    async def record_sleep(delay: float) -> None:
        delays.append(delay)

    async def run() -> tuple[list[dict[str, Any]], int]:
        async with service.client() as client:
            return await fetch_release_features(
                2022,
                PACIFIC_NORTHWEST_BBOX,
                client=client,
                page_size=4,
                sleep=record_sleep,
            )

    captured, authoritative_count = asyncio.run(run())
    assert authoritative_count == 4
    assert len(captured) == 4
    assert delays == [1.0, 2.0]


def test_a_server_error_that_never_clears_still_fails_loudly() -> None:
    service = RecordedMtbsService(_cohort(4), transient_failures=99)

    async def no_sleep(delay: float) -> None:
        del delay

    async def run() -> None:
        async with service.client() as client:
            await fetch_release_features(
                2022,
                PACIFIC_NORTHWEST_BBOX,
                client=client,
                page_size=4,
                sleep=no_sleep,
            )

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(run())


def test_a_real_sized_release_is_flagged_for_object_storage_not_inline_publication() -> None:
    # A real PNW cohort is roughly 20 MB of full-resolution polygon, four times the
    # `database_inline` artifact budget. The capture must still be written -- refusing it would
    # mean no MTBS data at all -- but it must be flagged so nobody inlines it into Postgres.
    assert requires_object_storage(b"x" * (INLINE_PUBLICATION_BYTE_LIMIT + 1)) is True
    assert requires_object_storage(b"x" * INLINE_PUBLICATION_BYTE_LIMIT) is False
    payload = build_release_payload(_recorded_features())
    assert json.loads(payload)["type"] == "FeatureCollection"
    assert len(json.loads(payload)["features"]) == len(RECORDED_2022_PNW_PROPERTIES)
    assert requires_object_storage(payload) is False


def test_all_releases_selects_exactly_the_years_with_an_established_release_date() -> None:
    assert resolve_requested_years(None, all_releases=True) == sorted(MTBS_ANNUAL_RELEASE_DATES)
    assert resolve_requested_years([2022, 2018, 2022], all_releases=False) == [2018, 2022]
    with pytest.raises(SystemExit):
        resolve_requested_years(None, all_releases=False)

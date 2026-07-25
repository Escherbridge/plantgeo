"""Tests for bounded, checkpointed USDM historical source contracts."""

import asyncio
import io
import zipfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import shapefile
from click.testing import CliRunner

from agri_data_service.cli import cli
from agri_data_service.config import settings
from agri_data_service.execution.historical_backfill import HistoricalBackfillWindow
from agri_data_service.execution.historical_usdm import (
    USDM_SHAPEFILE_SCHEMA_VERSION,
    HistoricalUsdmBackfillPlan,
    HistoricalUsdmFinalization,
    fetch_usdm_shapefile,
    historical_usdm_plan_checksum,
    historical_usdm_release_manifest,
    initialize_historical_usdm_checkpoint,
    parse_usdm_shapefile_zip,
    rebind_historical_usdm_checkpoint_for_finalization,
    record_historical_usdm_result,
)
from agri_data_service.execution.source_ingestion import SourceDefinition

EXPECTED_ISSUE_DATE_COUNT = 208
EXPECTED_POLYGON_COUNT = 2
EXPECTED_RETRY_ATTEMPTS = 2
SHA256_HEX_LENGTH = 64


def _issue_dates(window: HistoricalBackfillWindow) -> list[date]:
    first = window.start_date + timedelta(days=(1 - window.start_date.weekday()) % 7)
    dates: list[date] = []
    current = first
    while current <= window.end_date:
        dates.append(current)
        current += timedelta(days=7)
    return dates


def _plan() -> HistoricalUsdmBackfillPlan:
    window = HistoricalBackfillWindow(start_date="2022-07-20", end_date="2026-07-20")
    return HistoricalUsdmBackfillPlan(
        source=SourceDefinition(
            key="usdm-weekly",
            name="U.S. Drought Monitor weekly vector",
            owner="National Drought Mitigation Center",
            purpose="Reviewed national drought-category vector history",
            license_name="USDM attribution/permission statement",
            citation="U.S. Drought Monitor weekly medium-resolution shapefile.",
            base_url="https://droughtmonitor.unl.edu/DmData/GISData.aspx",
            license_url="https://droughtmonitor.unl.edu/About/Permission.aspx",
            reviewed_at="2026-07-20T12:00:00Z",
            reviewed_by="data-governance",
        ),
        window=window,
        issue_dates=_issue_dates(window),
        native_product_scope="reviewed USDM medium-resolution drought-category vector product",
        transform_version="usdm-shapefile-normalization-v1",
        release_set_key="usdm-national-20260720",
        release_set_as_of="2026-07-20T12:00:00Z",
    )


def _shapefile_zip(
    issue_date: date,
    *,
    projection: str = 'GEOGCS["GCS_WGS_1984"]',
    historical_basename: bool = False,
) -> bytes:
    shp = io.BytesIO()
    shx = io.BytesIO()
    dbf = io.BytesIO()
    with shapefile.Writer(shp=shp, shx=shx, dbf=dbf) as writer:
        writer.field("OBJECTID", "N", decimal=0)
        writer.field("DM", "N", decimal=0)
        writer.field("Shape_Leng", "F", decimal=4)
        writer.field("Shape_Area", "F", decimal=4)
        writer.poly([[[-105.0, 39.0], [-104.0, 39.0], [-104.0, 40.0], [-105.0, 39.0]]])
        writer.record(1, 2, 3.0, 0.5)
        writer.poly([[[-104.0, 39.0], [-103.0, 39.0], [-103.0, 40.0], [-104.0, 39.0]]])
        writer.record(2, 4, 2.0, 0.4)
    suffix = "" if historical_basename else "_M"
    base = f"USDM_{issue_date:%Y%m%d}{suffix}"
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
        output.writestr(f"{base}.shp", shp.getvalue())
        output.writestr(f"{base}.shx", shx.getvalue())
        output.writestr(f"{base}.dbf", dbf.getvalue())
        output.writestr(f"{base}.prj", projection)
        output.writestr(f"{base}.cpg", "UTF-8")
        output.writestr(f"{base}.sbn", b"")
        output.writestr(f"{base}.sbx", b"")
        output.writestr(f"{base}.shp.xml", "<metadata />")
    return archive.getvalue()


def test_parse_usdm_shapefile_retains_only_source_present_severities() -> None:
    plan = _plan()
    result = parse_usdm_shapefile_zip(
        plan,
        plan.issue_dates[0],
        _shapefile_zip(plan.issue_dates[0]),
        retrieved_at=datetime(2026, 7, 20, tzinfo=UTC),
    )

    assert len(plan.issue_dates) == EXPECTED_ISSUE_DATE_COUNT
    assert len(result.polygons) == EXPECTED_POLYGON_COUNT
    assert result.declared_feature_count == EXPECTED_POLYGON_COUNT
    assert [polygon.severity_class for polygon in result.polygons] == [2, 4]
    assert all('"type":"MultiPolygon"' in polygon.geometry_json for polygon in result.polygons)
    assert result.payload_checksum


def test_parse_usdm_shapefile_accepts_verified_legacy_archive_basename() -> None:
    plan = _plan()

    result = parse_usdm_shapefile_zip(
        plan,
        plan.issue_dates[0],
        _shapefile_zip(plan.issue_dates[0], historical_basename=True),
        retrieved_at=datetime(2026, 7, 20, tzinfo=UTC),
    )

    assert result.declared_feature_count == EXPECTED_POLYGON_COUNT


def test_parse_usdm_shapefile_rejects_unreviewed_projection_and_missing_package_entry() -> None:
    plan = _plan()
    with pytest.raises(ValueError, match="WGS84"):
        parse_usdm_shapefile_zip(
            plan,
            plan.issue_dates[0],
            _shapefile_zip(plan.issue_dates[0], projection='GEOGCS["GCS_NAD_1983"]'),
            retrieved_at=datetime(2026, 7, 20, tzinfo=UTC),
        )

    payload = _shapefile_zip(plan.issue_dates[0])
    source = zipfile.ZipFile(io.BytesIO(payload))
    stripped = io.BytesIO()
    with source, zipfile.ZipFile(stripped, "w") as output:
        for entry in source.infolist():
            if entry.filename.endswith(".sbx"):
                continue
            output.writestr(entry.filename, source.read(entry.filename))
    with pytest.raises(ValueError, match="entries"):
        parse_usdm_shapefile_zip(
            plan,
            plan.issue_dates[0],
            stripped.getvalue(),
            retrieved_at=datetime(2026, 7, 20, tzinfo=UTC),
        )

    mismatched = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(payload)) as source, zipfile.ZipFile(mismatched, "w") as output:
        for entry in source.infolist():
            content = source.read(entry.filename)
            if entry.filename.endswith(".dbf"):
                declared_count = bytearray(content)
                declared_count[4:8] = (1).to_bytes(4, byteorder="little")
                content = bytes(declared_count)
            output.writestr(entry.filename, content)
    with pytest.raises(ValueError, match="different shape and DBF record counts"):
        parse_usdm_shapefile_zip(
            plan,
            plan.issue_dates[0],
            mismatched.getvalue(),
            retrieved_at=datetime(2026, 7, 20, tzinfo=UTC),
        )


def test_usdm_checkpoint_binds_all_weekly_receipts_to_the_plan() -> None:
    plan = _plan()
    checkpoint = initialize_historical_usdm_checkpoint(plan, updated_at=datetime(2026, 7, 20, tzinfo=UTC))
    for issue_date in plan.issue_dates:
        result = parse_usdm_shapefile_zip(
            plan,
            issue_date,
            _shapefile_zip(issue_date),
            retrieved_at=datetime(2026, 7, 20, tzinfo=UTC),
        )
        checkpoint = record_historical_usdm_result(
            plan,
            checkpoint,
            result,
            updated_at=datetime(2026, 7, 20, tzinfo=UTC),
        )

    assert checkpoint.state == "validated"
    assert len(checkpoint.receipts) == EXPECTED_ISSUE_DATE_COUNT
    assert len(historical_usdm_release_manifest(plan, checkpoint)) == SHA256_HEX_LENGTH


def test_usdm_finalization_rebinds_only_the_completed_reviewed_replay() -> None:
    source_plan = _plan()
    checkpoint = initialize_historical_usdm_checkpoint(source_plan, updated_at=datetime(2026, 7, 20, tzinfo=UTC))
    for issue_date in source_plan.issue_dates:
        checkpoint = record_historical_usdm_result(
            source_plan,
            checkpoint,
            parse_usdm_shapefile_zip(
                source_plan,
                issue_date,
                _shapefile_zip(issue_date),
                retrieved_at=datetime(2026, 7, 20, tzinfo=UTC),
            ),
            updated_at=datetime(2026, 7, 20, tzinfo=UTC),
        )
    finalization = HistoricalUsdmFinalization(
        source_plan_checksum=historical_usdm_plan_checksum(source_plan),
        release_set_key="usdm-national-20260720-final",
        release_set_as_of="2026-07-20T13:00:00Z",
        description="Controlled finalization after durable weekly receipt collection.",
    )

    release_plan, release_checkpoint = rebind_historical_usdm_checkpoint_for_finalization(
        source_plan,
        finalization,
        checkpoint,
        updated_at=datetime(2026, 7, 20, 13, tzinfo=UTC),
    )

    assert release_plan.release_set_key == finalization.release_set_key
    assert release_checkpoint.state == "validated"
    assert release_checkpoint.plan_checksum == historical_usdm_plan_checksum(release_plan)
    assert release_checkpoint.plan_checksum != checkpoint.plan_checksum
    assert len(historical_usdm_release_manifest(release_plan, release_checkpoint)) == SHA256_HEX_LENGTH

    with pytest.raises(ValueError, match="does not identify"):
        rebind_historical_usdm_checkpoint_for_finalization(
            HistoricalUsdmBackfillPlan.model_validate(
                {**source_plan.model_dump(mode="json"), "transform_version": "usdm-shapefile-normalization-v2"}
            ),
            finalization,
            checkpoint,
        )
    with pytest.raises(ValueError, match="must advance"):
        rebind_historical_usdm_checkpoint_for_finalization(
            source_plan,
            HistoricalUsdmFinalization(
                source_plan_checksum=historical_usdm_plan_checksum(source_plan),
                release_set_key="usdm-national-20260720-early",
                release_set_as_of="2026-07-20T00:00:00Z",
                description="Invalid early finalization.",
            ),
            checkpoint,
        )


def test_fetch_usdm_retries_rate_limit_and_requires_zip_content_type() -> None:
    plan = _plan()
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        assert "application/x-zip-compressed" in request.headers["accept"]
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, headers={"retry-after": "0"})
        return httpx.Response(
            200,
            content=_shapefile_zip(plan.issue_dates[0]),
            headers={"content-type": "application/x-zip-compressed"},
        )

    async def no_sleep(_: float) -> None:
        return None

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await fetch_usdm_shapefile(
                plan,
                plan.issue_dates[0],
                client=client,
                retrieved_at=datetime(2026, 7, 20, tzinfo=UTC),
                sleep=no_sleep,
            )
        assert len(result.polygons) == EXPECTED_POLYGON_COUNT

    asyncio.run(run())
    assert attempts == EXPECTED_RETRY_ATTEMPTS


def test_usdm_cli_fails_closed_without_local_loader_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plan_path = tmp_path / "usdm-plan.json"
    plan_path.write_text(_plan().model_dump_json(), encoding="utf-8")
    monkeypatch.setattr(settings, "local_source_loader_database_url", None)

    result = CliRunner().invoke(cli, ["historical-usdm-backfill", "--plan", str(plan_path)])

    assert result.exit_code != 0
    assert "LOCAL_SOURCE_LOADER_DATABASE_URL" in result.output
    assert USDM_SHAPEFILE_SCHEMA_VERSION == "usdm-shapefile-v1"

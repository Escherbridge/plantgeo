"""Tests for cache-first ERA5-Land monthly source contracts."""

from __future__ import annotations

import calendar
import io
import zipfile
from asyncio import run
from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock
from uuid import uuid4

import numpy as np
import pytest
import xarray as xr
from click.testing import CliRunner
from pydantic import SecretStr

from agri_data_service.cli import cli
from agri_data_service.config import settings
from agri_data_service.execution.historical_backfill import HistoricalSignalObservation
from agri_data_service.execution.historical_era5 import (
    ERA5_LAND_VARIABLE_ALIASES,
    Era5LandMonthlyResult,
    HistoricalEra5Finalization,
    HistoricalEra5LandBackfillPlan,
    HistoricalEra5Receipt,
    _require_cds_credentials,  # the credential resolver under test; deliberately private
    cache_historical_era5_result,
    fetch_era5_land_monthly,
    historical_era5_checkpoint_path,
    historical_era5_plan_checksum,
    initialize_historical_era5_checkpoint,
    load_cached_historical_era5_result,
    load_historical_era5_checkpoint,
    parse_era5_land_monthly_payload,
    rebind_historical_era5_checkpoint_for_finalization,
    record_historical_era5_result,
    write_historical_era5_checkpoint,
    write_historical_era5_release_plan,
)
from agri_data_service.execution.historical_era5_parquet import (
    historical_era5_parquet_root,
    materialize_historical_era5_parquet,
)
from agri_data_service.execution.historical_writer import _insert_era5_observations
from agri_data_service.execution.source_ingestion import SourceDefinition

EXPECTED_PERIOD_COUNT = 49
EXPECTED_PARAMETER_COUNT = 6
MONTHS_PER_YEAR = 12
KELVIN_TEMPERATURE_TEST_VALUE = 300
ERA5_BATCH_TEST_OBSERVATION_COUNT = 1_001
EXPECTED_ERA5_BATCH_EXECUTIONS = 2

if TYPE_CHECKING:
    from pathlib import Path


def _periods() -> list[dict[str, object]]:
    start = date(2022, 4, 30)
    end = date(2026, 4, 30)
    periods: list[dict[str, object]] = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        period_start = max(start, date(year, month, 1))
        period_end = min(end, date(year, month, calendar.monthrange(year, month)[1]))
        periods.append(
            {
                "key": f"{year:04d}-{month:02d}",
                "start_date": period_start.isoformat(),
                "end_date": period_end.isoformat(),
                "year": f"{year:04d}",
                "month": f"{month:02d}",
                "days": [f"{day:02d}" for day in range(period_start.day, period_end.day + 1)],
            }
        )
        month += 1
        if month > MONTHS_PER_YEAR:
            year, month = year + 1, 1
    return periods


def _plan() -> HistoricalEra5LandBackfillPlan:
    return HistoricalEra5LandBackfillPlan(
        source=SourceDefinition(
            key="era5-land",
            name="ERA5-Land post-processed daily statistics",
            owner="Copernicus Climate Change Service",
            purpose="Reviewed North America retrospective land-state baseline.",
            base_url="https://cds.climate.copernicus.eu/datasets/derived-era5-land-daily-statistics",
            license_name="Copernicus Climate Change Service terms of use",
            license_url="https://cds.climate.copernicus.eu/terms",
            citation="ERA5-Land post-processed daily statistics.",
            reviewed_at="2026-07-21T00:00:00Z",
            reviewed_by="data-governance",
        ),
        window={"start_date": "2022-04-30", "end_date": "2026-04-30"},
        daily_statistic="daily_mean",
        frequency="1_hourly",
        time_zone="utc+00:00",
        requested_grid_degrees=1,
        requested_area={"north": 84, "west": -170, "south": 14, "east": -50},
        native_grid_resolution_m=9_000,
        cells=[{"cell_key": "na-sample:1deg:p040.00:m105.00", "latitude": 40, "longitude": -105}],
        parameters=sorted(ERA5_LAND_VARIABLE_ALIASES),
        periods=_periods(),
        nasa_lattice_plan_checksum="a" * 64,
        terms_acceptance_required=True,
        transform_version="era5-land-daily-requested-grid-normalization-v1",
        release_set_key="era5-land-test-20220430-20260430",
        release_set_as_of="2026-07-21T23:59:59Z",
        description="Test-only reviewed ERA5-Land scope.",
    )


def _archive(tmp_path: Path, plan: HistoricalEra5LandBackfillPlan, period_index: int = 0) -> bytes:
    period = plan.periods[period_index]
    stream = io.BytesIO()
    values = {
        "10m_u_component_of_wind": 1.0,
        "10m_v_component_of_wind": 2.0,
        "2m_dewpoint_temperature": 280.0,
        "2m_temperature": 300.0,
        "soil_temperature_level_1": 290.0,
        "volumetric_soil_water_layer_1": 0.25,
    }
    aliases = {
        "10m_u_component_of_wind": "u10",
        "10m_v_component_of_wind": "v10",
        "2m_dewpoint_temperature": "d2m",
        "2m_temperature": "t2m",
        "soil_temperature_level_1": "stl1",
        "volumetric_soil_water_layer_1": "swvl1",
    }
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for parameter, value in values.items():
            alias = aliases[parameter]
            dataset = xr.Dataset(
                {alias: (("time", "latitude", "longitude"), np.full((len(period.days), 1, 1), value))},
                coords={
                    "time": np.asarray([np.datetime64(f"{period.year}-{period.month}-{day}") for day in period.days]),
                    "latitude": np.asarray([40.0]),
                    "longitude": np.asarray([255.0]),
                },
            )
            path = tmp_path / f"{alias}.nc"
            dataset.to_netcdf(path, engine="h5netcdf")
            archive.writestr(path.name, path.read_bytes())
    return stream.getvalue()


def test_era5_plan_requires_exact_monthly_coverage() -> None:
    plan = _plan()

    assert len(plan.periods) == EXPECTED_PERIOD_COUNT
    assert len(plan.parameters) == EXPECTED_PARAMETER_COUNT
    assert historical_era5_plan_checksum(plan)

    with pytest.raises(ValueError, match="exactly cover"):
        HistoricalEra5LandBackfillPlan.model_validate({**plan.model_dump(mode="json"), "periods": plan.periods[:-1]})


def test_era5_parser_normalizes_complete_monthly_netcdf_zip(tmp_path: Path) -> None:
    plan = _plan()
    result = parse_era5_land_monthly_payload(
        plan,
        plan.periods[0],
        _archive(tmp_path, plan),
        retrieved_at=datetime(2026, 7, 21, tzinfo=UTC),
    )

    assert result.period_key == "2022-04"
    assert len(result.observations) == EXPECTED_PARAMETER_COUNT
    assert len(result.coverage) == EXPECTED_PARAMETER_COUNT
    temperature = next(item for item in result.observations if item.source_parameter == "2m_temperature")
    assert temperature.original_value == KELVIN_TEMPERATURE_TEST_VALUE
    assert temperature.normalized_value == pytest.approx(26.85)
    assert temperature.normalized_unit == "C"
    assert all(item.status == "complete" for item in result.coverage)


def test_era5_cache_reuses_only_checksum_bound_complete_archive(tmp_path: Path) -> None:
    plan = _plan()
    result = parse_era5_land_monthly_payload(
        plan,
        plan.periods[0],
        _archive(tmp_path, plan),
        retrieved_at=datetime(2026, 7, 21, tzinfo=UTC),
    )

    receipt = cache_historical_era5_result(tmp_path, plan, result)
    cached = load_cached_historical_era5_result(tmp_path, plan, plan.periods[0])

    assert cached == result
    assert receipt.payload_checksum == result.payload_checksum


def test_era5_checkpoint_is_resumable_and_plan_bound(tmp_path: Path) -> None:
    plan = _plan()
    result = parse_era5_land_monthly_payload(
        plan,
        plan.periods[0],
        _archive(tmp_path, plan),
        retrieved_at=datetime(2026, 7, 21, tzinfo=UTC),
    )
    checkpoint = record_historical_era5_result(
        plan,
        initialize_historical_era5_checkpoint(plan, updated_at=datetime(2026, 7, 21, tzinfo=UTC)),
        result,
        updated_at=datetime(2026, 7, 21, 1, tzinfo=UTC),
    )
    path = historical_era5_checkpoint_path(tmp_path, plan)
    write_historical_era5_checkpoint(path, checkpoint)

    assert checkpoint.state == "running"
    assert load_historical_era5_checkpoint(path) == checkpoint
    altered = Era5LandMonthlyResult(
        **{**result.__dict__, "payload_checksum": "0" * 64},
    )
    with pytest.raises(ValueError, match="checksum"):
        record_historical_era5_result(plan, checkpoint, altered)


def test_era5_finalization_writes_an_immutable_matching_release_plan(tmp_path: Path) -> None:
    source_plan = _plan()
    cached_result = parse_era5_land_monthly_payload(
        source_plan,
        source_plan.periods[0],
        _archive(tmp_path, source_plan),
        retrieved_at=datetime(2026, 7, 21, tzinfo=UTC),
    )
    cache_historical_era5_result(tmp_path, source_plan, cached_result)
    checkpoint = initialize_historical_era5_checkpoint(source_plan, updated_at=datetime(2026, 7, 21, tzinfo=UTC))
    checkpoint = checkpoint.model_copy(
        update={
            "state": "validated",
            "receipts": [
                HistoricalEra5Receipt(
                    period_key=period.key,
                    payload_checksum=f"{index:064x}",
                    payload_bytes=1,
                    observation_count=len(source_plan.cells) * len(source_plan.parameters) * len(period.days),
                    coverage_count=len(source_plan.cells) * len(source_plan.parameters),
                    retrieved_at=datetime(2026, 7, 21, tzinfo=UTC),
                )
                for index, period in enumerate(source_plan.periods, start=1)
            ],
        }
    )
    finalization = HistoricalEra5Finalization(
        source_plan_checksum=historical_era5_plan_checksum(source_plan),
        release_set_key="era5-land-test-20220430-20260430-final",
        release_set_as_of="2026-07-22T00:00:00Z",
        description="Durable finalization plan required for local ERA5 persistence.",
    )

    release_plan, release_checkpoint = rebind_historical_era5_checkpoint_for_finalization(
        source_plan,
        finalization,
        checkpoint,
    )
    path = tmp_path / "final-era5-release-plan.json"
    write_historical_era5_release_plan(path, release_plan)
    write_historical_era5_release_plan(path, release_plan)

    assert release_checkpoint.plan_checksum == historical_era5_plan_checksum(release_plan)
    assert release_checkpoint.raw_cache_plan_checksum == historical_era5_plan_checksum(source_plan)
    assert HistoricalEra5LandBackfillPlan.model_validate_json(path.read_bytes()) == release_plan
    assert (
        load_cached_historical_era5_result(
            tmp_path,
            release_plan,
            release_plan.periods[0],
            cache_plan_checksum=release_checkpoint.raw_cache_plan_checksum,
        )
        == cached_result
    )
    with pytest.raises(ValueError, match="different governed plan"):
        write_historical_era5_release_plan(
            path,
            HistoricalEra5LandBackfillPlan.model_validate(
                {**release_plan.model_dump(mode="json"), "description": "Different content."}
            ),
        )


def test_era5_parquet_materialization_creates_daily_hive_partitions(tmp_path: Path) -> None:
    plan = _plan()
    checkpoint = initialize_historical_era5_checkpoint(plan, updated_at=datetime(2026, 7, 21, tzinfo=UTC))
    for period_index, period in enumerate(plan.periods):
        result = parse_era5_land_monthly_payload(
            plan,
            period,
            _archive(tmp_path, plan, period_index),
            retrieved_at=datetime(2026, 7, 21, tzinfo=UTC),
        )
        cache_historical_era5_result(tmp_path, plan, result)
        checkpoint = record_historical_era5_result(
            plan,
            checkpoint,
            result,
            updated_at=datetime(2026, 7, 21, tzinfo=UTC),
        )

    manifest = materialize_historical_era5_parquet(tmp_path, plan, checkpoint)

    assert checkpoint.state == "validated"
    assert manifest.row_count == plan.window.day_count * EXPECTED_PARAMETER_COUNT
    assert manifest.partition_count == plan.window.day_count
    assert (historical_era5_parquet_root(tmp_path, plan) / "source=era5-land-daily").is_dir()


def _clear_cds_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Silence both resolution sources: the process environment and Settings/`.env`."""
    monkeypatch.delenv("CDSAPI_URL", raising=False)
    monkeypatch.delenv("CDSAPI_KEY", raising=False)
    monkeypatch.setattr(settings, "cdsapi_url", None)
    monkeypatch.setattr(settings, "cdsapi_key", None)


def test_era5_fetch_rejects_missing_local_cds_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    plan = _plan()
    _clear_cds_credentials(monkeypatch)

    with pytest.raises(ValueError, match="CDSAPI_URL"):
        run(fetch_era5_land_monthly(plan, plan.periods[0]))


def test_cds_credentials_resolve_from_settings_when_the_environment_is_silent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`.env` entries used to be inert: `Settings` loads env_file, never `os.environ`.

    That gap is what forced `run-backfill.sh`'s `set -a; . ./.env` dance and made a missing
    export look like a licence refusal. Settings now carries the pair, so an operator who only
    edits `.env` is served.
    """
    _clear_cds_credentials(monkeypatch)
    monkeypatch.setattr(settings, "cdsapi_url", "https://cds.example.test/api")
    monkeypatch.setattr(settings, "cdsapi_key", SecretStr("settings-key"))

    assert _require_cds_credentials() == ("https://cds.example.test/api", "settings-key")


def test_cds_credentials_prefer_the_process_environment_over_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real export still wins, so a one-off shell override does not need a `.env` edit."""
    monkeypatch.setattr(settings, "cdsapi_url", "https://cds.example.test/from-dotenv")
    monkeypatch.setattr(settings, "cdsapi_key", SecretStr("dotenv-key"))
    monkeypatch.setenv("CDSAPI_URL", "https://cds.example.test/from-environ")
    monkeypatch.setenv("CDSAPI_KEY", "environ-key")

    assert _require_cds_credentials() == ("https://cds.example.test/from-environ", "environ-key")


def test_cds_credentials_fall_back_per_variable_and_ignore_blank_exports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty or whitespace-only export is not a value, so it must not shadow `.env`."""
    monkeypatch.setattr(settings, "cdsapi_url", "https://cds.example.test/from-dotenv")
    monkeypatch.setattr(settings, "cdsapi_key", SecretStr("  dotenv-key  "))
    monkeypatch.setenv("CDSAPI_URL", "   ")
    monkeypatch.delenv("CDSAPI_KEY", raising=False)

    assert _require_cds_credentials() == ("https://cds.example.test/from-dotenv", "dotenv-key")


def test_cds_credential_refusal_names_variables_and_leaks_no_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Half-configured is still refused, and the message carries neither half."""
    _clear_cds_credentials(monkeypatch)
    monkeypatch.setattr(settings, "cdsapi_key", SecretStr("secret-key-value"))

    with pytest.raises(ValueError, match="ERA5-Land requires accepted CDS web terms") as refusal:
        _require_cds_credentials()

    message = str(refusal.value)
    assert "CDSAPI_URL" in message
    assert "CDSAPI_KEY" in message
    assert "secret-key-value" not in message


def test_era5_cli_records_a_resumable_redacted_credential_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan()
    plan_path = tmp_path / "era5-plan.json"
    plan_path.write_text(plan.model_dump_json(), encoding="utf-8")
    monkeypatch.setattr(settings, "local_execution_root", tmp_path)
    _clear_cds_credentials(monkeypatch)

    result = CliRunner().invoke(cli, ["historical-era5-backfill", "--plan", str(plan_path)])

    assert result.exit_code != 0
    assert "CDSAPI_URL" in result.output
    checkpoint = load_historical_era5_checkpoint(historical_era5_checkpoint_path(tmp_path, plan))
    assert checkpoint.state == "blocked"
    assert checkpoint.reason == (
        "ERA5-Land requires accepted CDS web terms plus CDSAPI_URL and CDSAPI_KEY in the local "
        "operator environment or services/agri-data-service/.env"
    )


def test_era5_persist_fails_closed_without_any_database_dsn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan_path = tmp_path / "era5-plan.json"
    plan_path.write_text(_plan().model_dump_json(), encoding="utf-8")
    # The loader override falls back to DATABASE_URL since 2026-08-08, so both must be absent.
    monkeypatch.setattr(settings, "local_source_loader_database_url", None)
    monkeypatch.setattr(settings, "database_url", None)

    result = CliRunner().invoke(cli, ["historical-era5-persist", "--plan", str(plan_path)])

    assert result.exit_code != 0
    assert "LOCAL_SOURCE_LOADER_DATABASE_URL" in result.output


def test_era5_cli_exposes_separate_cache_persist_and_finalization_boundaries() -> None:
    runner = CliRunner()

    backfill = runner.invoke(cli, ["historical-era5-backfill", "--help"])
    persist = runner.invoke(cli, ["historical-era5-persist", "--help"])
    finalization = runner.invoke(cli, ["historical-era5-finalize", "--help"])

    assert backfill.exit_code == 0
    assert persist.exit_code == 0
    assert finalization.exit_code == 0
    assert "--source-plan" in finalization.output
    assert "--finalization" in finalization.output
    assert "--output-plan" in finalization.output


def test_era5_observation_writer_uses_fixed_size_batches() -> None:
    observed_at = datetime(2026, 7, 21, tzinfo=UTC)
    observations = tuple(
        HistoricalSignalObservation(
            cell_key="na-sample:1deg:p040.00:m105.00",
            source_parameter="2m_temperature",
            signal_name="air_temperature_mean",
            observed_at=observed_at,
            original_value=300,
            original_unit="K",
            normalized_value=26.85,
            normalized_unit="C",
            quality_flag="accepted",
            is_observed=True,
            payload_checksum="a" * 64,
        )
        for _ in range(ERA5_BATCH_TEST_OBSERVATION_COUNT)
    )
    session = SimpleNamespace(execute=AsyncMock())
    source_release = SimpleNamespace(id=uuid4())
    spatial_cells = {"na-sample:1deg:p040.00:m105.00": SimpleNamespace(id=uuid4())}
    result = Era5LandMonthlyResult(
        period_key="2022-04",
        retrieved_at=observed_at,
        payload=b"zip",
        payload_checksum="a" * 64,
        observations=observations,
        coverage=(),
    )

    run(_insert_era5_observations(session, source_release, spatial_cells, result))

    assert session.execute.await_count == EXPECTED_ERA5_BATCH_EXECUTIONS

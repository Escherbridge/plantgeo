"""Tests for deterministic, bounded NASA POWER historical input contracts."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner

from agri_data_service.cli import cli
from agri_data_service.config import settings
from agri_data_service.execution import historical_parquet
from agri_data_service.execution.historical_backfill import (
    NASA_POWER_DAILY_SCHEMA_VERSION,
    AnalysisGridCell,
    HistoricalBackfillWindow,
    HistoricalNasaBackfillPlan,
    HistoricalNasaFinalization,
    HistoricalNasaRawCacheReceipt,
    NasaPowerDailyPlan,
    cache_historical_nasa_result,
    fetch_nasa_power_daily,
    historical_nasa_checkpoint_path,
    historical_nasa_plan_checksum,
    historical_nasa_raw_cache_paths,
    historical_nasa_release_manifest,
    initialize_historical_nasa_checkpoint,
    load_cached_historical_nasa_result,
    load_historical_nasa_checkpoint,
    nasa_power_daily_url,
    parse_nasa_power_daily_payload,
    rebind_historical_nasa_checkpoint_for_finalization,
    record_historical_nasa_result,
    write_historical_nasa_checkpoint,
    write_historical_nasa_release_plan,
)
from agri_data_service.execution.historical_parquet import (
    historical_nasa_parquet_root,
    materialize_historical_nasa_parquet,
)
from agri_data_service.execution.source_ingestion import SourceDefinition

EXPECTED_FOUR_YEAR_DAY_COUNT = 1462
EXPECTED_T2M_VALUES = 2
EXPECTED_PRECIPITATION_VALUES = 1
EXPECTED_RATE_LIMIT_ATTEMPTS = 2


def _plan(*, parameters: list[str] | None = None) -> NasaPowerDailyPlan:
    return NasaPowerDailyPlan(
        schema_version=NASA_POWER_DAILY_SCHEMA_VERSION,
        window=HistoricalBackfillWindow(start_date="2022-07-20", end_date="2026-07-20"),
        cells=[AnalysisGridCell(cell_key="conus:0.1:000001", latitude=39.7392, longitude=-104.9903)],
        parameters=parameters or ["PRECTOTCORR", "T2M"],
    )


def _payload() -> bytes:
    return json.dumps(
        {
            "properties": {
                "parameter": {
                    "PRECTOTCORR": {
                        "20220720": 3.5,
                        "20260720": -999.0,
                    },
                    "T2M": {
                        "20220720": 21.2,
                        "20260720": 22.7,
                    },
                }
            }
        },
        separators=(",", ":"),
    ).encode()


def _complete_payload(plan: NasaPowerDailyPlan) -> bytes:
    days = [plan.window.start_date + timedelta(days=offset) for offset in range(plan.window.day_count)]
    return json.dumps(
        {
            "properties": {
                "parameter": {
                    "PRECTOTCORR": {day.strftime("%Y%m%d"): 3.5 for day in days},
                    "T2M": {day.strftime("%Y%m%d"): 21.2 for day in days},
                }
            }
        },
        separators=(",", ":"),
    ).encode()


def _historical_plan() -> HistoricalNasaBackfillPlan:
    return HistoricalNasaBackfillPlan(
        source=SourceDefinition(
            key="nasa-power-daily",
            name="NASA POWER Daily",
            owner="NASA",
            purpose="Approved historical meteorology ingestion",
            base_url="https://power.larc.nasa.gov/api/temporal/daily/point",
            license_name="NASA POWER data policy",
            citation="NASA POWER daily API",
            reviewed_at="2026-07-20T00:00:00Z",
            reviewed_by="data-governance",
        ),
        nasa=_plan(),
        transform_version="nasa-power-normalization-v1",
        release_set_key="nasa-power-conus-20260720",
        release_set_as_of="2026-07-20T12:00:00Z",
    )


def test_nasa_power_plan_requires_canonical_four_year_window_and_inputs() -> None:
    with pytest.raises(ValueError, match="exactly four calendar years"):
        HistoricalBackfillWindow(start_date="2022-07-21", end_date="2026-07-20")

    with pytest.raises(ValueError, match="sorted and unique"):
        _plan(parameters=["T2M", "PRECTOTCORR"])

    with pytest.raises(ValueError, match="unsupported"):
        _plan(parameters=["NOT_A_POWER_PARAMETER"])

    plan = _plan()
    assert plan.window.day_count == EXPECTED_FOUR_YEAR_DAY_COUNT
    assert plan.window.start_date.isoformat() == "2022-07-20"


def test_nasa_power_url_is_canonical_and_credential_free() -> None:
    plan = _plan()
    url = nasa_power_daily_url(plan, plan.cells[0])

    assert str(url).startswith("https://power.larc.nasa.gov/api/temporal/daily/point?")
    assert url.params["community"] == "AG"
    assert url.params["latitude"] == "39.7392"
    assert url.params["longitude"] == "-104.9903"
    assert url.params["parameters"] == "PRECTOTCORR,T2M"
    assert url.params["start"] == "20220720"
    assert url.params["end"] == "20260720"
    assert url.params["time-standard"] == "UTC"
    assert "token" not in str(url).lower()


def test_nasa_power_parser_preserves_missingness_and_records_coverage() -> None:
    plan = _plan()
    result = parse_nasa_power_daily_payload(
        plan,
        plan.cells[0],
        _payload(),
        retrieved_at=datetime(2026, 7, 20, 12, tzinfo=UTC),
    )

    assert len(result.observations) == plan.window.day_count * 2
    assert result.payload_checksum == "bb233bd95d48ea429f6a890991a43cec898ac3b37d3a49e668018657872e9c97"

    precipitation_last_day = next(
        item
        for item in result.observations
        if item.source_parameter == "PRECTOTCORR" and item.observed_at == datetime(2026, 7, 20, tzinfo=UTC)
    )
    assert precipitation_last_day.original_value is None
    assert precipitation_last_day.is_observed is False
    assert precipitation_last_day.quality_flag == "source_missing"

    coverage_by_parameter = {item.source_parameter: item for item in result.coverage}
    t2m_coverage = coverage_by_parameter["T2M"]
    precipitation_coverage = coverage_by_parameter["PRECTOTCORR"]
    assert t2m_coverage.status == "partial"
    assert t2m_coverage.received_observation_count == EXPECTED_T2M_VALUES
    assert precipitation_coverage.status == "partial"
    assert precipitation_coverage.received_observation_count == EXPECTED_PRECIPITATION_VALUES


def test_nasa_power_parser_rejects_missing_requested_parameter() -> None:
    plan = _plan()
    payload = json.dumps({"properties": {"parameter": {"T2M": {}}}}).encode()

    with pytest.raises(ValueError, match="missing requested parameter"):
        parse_nasa_power_daily_payload(
            plan,
            plan.cells[0],
            payload,
            retrieved_at=datetime(2026, 7, 20, tzinfo=UTC),
        )


def test_nasa_power_fetch_enforces_json_response_boundary() -> None:
    plan = _plan()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == nasa_power_daily_url(plan, plan.cells[0])
        return httpx.Response(200, headers={"content-type": "application/json"}, content=_payload())

    async def run() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            result = await fetch_nasa_power_daily(
                plan,
                plan.cells[0],
                client=client,
                retrieved_at=datetime(2026, 7, 20, tzinfo=UTC),
            )
        assert result.cell_key == plan.cells[0].cell_key

    asyncio.run(run())


def test_nasa_power_fetch_retries_rate_limits_without_reissuing_a_new_plan() -> None:
    plan = _plan()
    attempts = 0
    delays: list[float] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, headers={"retry-after": "0"})
        return httpx.Response(200, headers={"content-type": "application/json"}, content=_payload())

    async def record_sleep(delay: float) -> None:
        delays.append(delay)

    async def run() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            result = await fetch_nasa_power_daily(
                plan,
                plan.cells[0],
                client=client,
                retrieved_at=datetime(2026, 7, 20, tzinfo=UTC),
                sleep=record_sleep,
            )
        assert result.payload_checksum

    asyncio.run(run())
    assert attempts == EXPECTED_RATE_LIMIT_ATTEMPTS
    assert delays == [0]


def test_historical_nasa_checkpoint_binds_a_complete_receipt_set(tmp_path: Path) -> None:
    plan = _historical_plan()
    result = parse_nasa_power_daily_payload(
        plan.nasa,
        plan.nasa.cells[0],
        _complete_payload(plan.nasa),
        retrieved_at=datetime(2026, 7, 20, tzinfo=UTC),
    )
    checkpoint = record_historical_nasa_result(
        plan,
        initialize_historical_nasa_checkpoint(plan, updated_at=datetime(2026, 7, 20, tzinfo=UTC)),
        result,
        updated_at=datetime(2026, 7, 20, 1, tzinfo=UTC),
    )
    path = historical_nasa_checkpoint_path(tmp_path, plan)

    assert checkpoint.state == "validated"
    assert len(checkpoint.receipts) == 1
    assert historical_nasa_release_manifest(plan, checkpoint) == historical_nasa_release_manifest(plan, checkpoint)

    write_historical_nasa_checkpoint(path, checkpoint)
    assert load_historical_nasa_checkpoint(path) == checkpoint


def test_historical_nasa_raw_cache_reuses_only_a_complete_bound_response(tmp_path: Path) -> None:
    plan = _historical_plan()
    cell = plan.nasa.cells[0]
    result = parse_nasa_power_daily_payload(
        plan.nasa,
        cell,
        _complete_payload(plan.nasa),
        retrieved_at=datetime(2026, 7, 20, tzinfo=UTC),
    )

    receipt = cache_historical_nasa_result(tmp_path, plan, result)
    cached = load_cached_historical_nasa_result(tmp_path, plan, cell)

    assert cached == result
    assert receipt.payload_checksum == result.payload_checksum

    payload_path, _ = historical_nasa_raw_cache_paths(tmp_path, plan, cell)
    payload_path.write_bytes(b"{}")
    with pytest.raises(ValueError, match="does not match its receipt"):
        load_cached_historical_nasa_result(tmp_path, plan, cell)


def test_historical_nasa_parquet_materialization_creates_daily_hive_partitions(tmp_path: Path) -> None:
    plan = _historical_plan()
    cell = plan.nasa.cells[0]
    result = parse_nasa_power_daily_payload(
        plan.nasa,
        cell,
        _complete_payload(plan.nasa),
        retrieved_at=datetime(2026, 7, 20, tzinfo=UTC),
    )
    cache_historical_nasa_result(tmp_path, plan, result)
    checkpoint = record_historical_nasa_result(
        plan,
        initialize_historical_nasa_checkpoint(plan, updated_at=datetime(2026, 7, 20, tzinfo=UTC)),
        result,
        updated_at=datetime(2026, 7, 20, 1, tzinfo=UTC),
    )

    manifest = materialize_historical_nasa_parquet(tmp_path, plan, checkpoint)

    assert manifest.row_count == EXPECTED_FOUR_YEAR_DAY_COUNT * 2
    assert manifest.partition_count == EXPECTED_FOUR_YEAR_DAY_COUNT
    dataset_root = historical_nasa_parquet_root(tmp_path, plan)
    assert (dataset_root / "source=nasa-power-daily").is_dir()
    assert not (dataset_root / "staging").exists()
    assert not (dataset_root / "duckdb-spill").exists()


def test_historical_nasa_parquet_resumes_validated_staging_after_interruption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _historical_plan()
    cell = plan.nasa.cells[0]
    result = parse_nasa_power_daily_payload(
        plan.nasa,
        cell,
        _complete_payload(plan.nasa),
        retrieved_at=datetime(2026, 7, 20, tzinfo=UTC),
    )
    cache_historical_nasa_result(tmp_path, plan, result)
    checkpoint = record_historical_nasa_result(
        plan,
        initialize_historical_nasa_checkpoint(plan, updated_at=datetime(2026, 7, 20, tzinfo=UTC)),
        result,
        updated_at=datetime(2026, 7, 20, 1, tzinfo=UTC),
    )
    original_write_table = historical_parquet.pq.write_table

    def write_then_interrupt(*args: object, **kwargs: object) -> None:
        original_write_table(*args, **kwargs)
        raise RuntimeError("simulated interruption after staging write")

    monkeypatch.setattr(historical_parquet.pq, "write_table", write_then_interrupt)
    with pytest.raises(RuntimeError, match="simulated interruption"):
        materialize_historical_nasa_parquet(tmp_path, plan, checkpoint)
    monkeypatch.setattr(historical_parquet.pq, "write_table", original_write_table)

    manifest = materialize_historical_nasa_parquet(tmp_path, plan, checkpoint)

    assert manifest.row_count == EXPECTED_FOUR_YEAR_DAY_COUNT * 2
    assert not list(historical_nasa_parquet_root(tmp_path, plan).parent.glob("*.building-*"))


def test_historical_nasa_finalized_plan_materializes_from_its_original_raw_cache(tmp_path: Path) -> None:
    source_plan = _historical_plan()
    cell = source_plan.nasa.cells[0]
    result = parse_nasa_power_daily_payload(
        source_plan.nasa,
        cell,
        _complete_payload(source_plan.nasa),
        retrieved_at=datetime(2026, 7, 20, 13, tzinfo=UTC),
    )
    cache_historical_nasa_result(tmp_path, source_plan, result)
    source_checkpoint = record_historical_nasa_result(
        source_plan,
        initialize_historical_nasa_checkpoint(source_plan, updated_at=datetime(2026, 7, 20, tzinfo=UTC)),
        result,
        updated_at=datetime(2026, 7, 20, 13, tzinfo=UTC),
    )
    release_plan, release_checkpoint = rebind_historical_nasa_checkpoint_for_finalization(
        source_plan,
        HistoricalNasaFinalization(
            source_plan_checksum=historical_nasa_plan_checksum(source_plan),
            release_set_key="nasa-power-conus-20260720-final-parquet",
            release_set_as_of="2026-07-20T14:00:00Z",
            description="Finalized receipt identity reuses the immutable raw cache.",
        ),
        source_checkpoint,
    )

    manifest = materialize_historical_nasa_parquet(tmp_path, release_plan, release_checkpoint)

    assert release_checkpoint.raw_cache_plan_checksum == historical_nasa_plan_checksum(source_plan)
    assert manifest.source_plan_checksum == historical_nasa_plan_checksum(release_plan)


def test_historical_nasa_parquet_rejects_a_different_valid_raw_cache_receipt(tmp_path: Path) -> None:
    plan = _historical_plan()
    cell = plan.nasa.cells[0]
    result = parse_nasa_power_daily_payload(
        plan.nasa,
        cell,
        _complete_payload(plan.nasa),
        retrieved_at=datetime(2026, 7, 20, tzinfo=UTC),
    )
    cache_historical_nasa_result(tmp_path, plan, result)
    checkpoint = record_historical_nasa_result(
        plan,
        initialize_historical_nasa_checkpoint(plan, updated_at=datetime(2026, 7, 20, tzinfo=UTC)),
        result,
        updated_at=datetime(2026, 7, 20, 1, tzinfo=UTC),
    )
    altered_payload = json.loads(_complete_payload(plan.nasa))
    altered_payload["properties"]["parameter"]["T2M"]["20220720"] = 99.9
    altered = parse_nasa_power_daily_payload(
        plan.nasa,
        cell,
        json.dumps(altered_payload, separators=(",", ":")).encode(),
        retrieved_at=datetime(2026, 7, 20, tzinfo=UTC),
    )
    payload_path, receipt_path = historical_nasa_raw_cache_paths(tmp_path, plan, cell)
    payload_path.write_bytes(altered.payload)
    receipt_path.write_text(
        HistoricalNasaRawCacheReceipt(
            plan_checksum=historical_nasa_plan_checksum(plan),
            cell_key=cell.cell_key,
            payload_checksum=altered.payload_checksum,
            payload_bytes=len(altered.payload),
            retrieved_at=altered.retrieved_at,
        ).model_dump_json(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="does not match its checkpoint receipt"):
        materialize_historical_nasa_parquet(tmp_path, plan, checkpoint)


def test_historical_nasa_checkpoint_rejects_incomplete_source_coverage() -> None:
    plan = _historical_plan()
    incomplete = parse_nasa_power_daily_payload(
        plan.nasa,
        plan.nasa.cells[0],
        _payload(),
        retrieved_at=datetime(2026, 7, 20, tzinfo=UTC),
    )

    with pytest.raises(ValueError, match="incomplete coverage"):
        record_historical_nasa_result(
            plan,
            initialize_historical_nasa_checkpoint(plan, updated_at=datetime(2026, 7, 20, tzinfo=UTC)),
            incomplete,
        )


def test_nasa_finalization_rebinds_only_a_complete_reviewed_replay() -> None:
    source_plan = _historical_plan()
    result = parse_nasa_power_daily_payload(
        source_plan.nasa,
        source_plan.nasa.cells[0],
        _complete_payload(source_plan.nasa),
        retrieved_at=datetime(2026, 7, 20, 13, tzinfo=UTC),
    )
    checkpoint = record_historical_nasa_result(
        source_plan,
        initialize_historical_nasa_checkpoint(source_plan, updated_at=datetime(2026, 7, 20, tzinfo=UTC)),
        result,
        updated_at=datetime(2026, 7, 20, 13, tzinfo=UTC),
    )
    finalization = HistoricalNasaFinalization(
        source_plan_checksum=historical_nasa_plan_checksum(source_plan),
        release_set_key="nasa-power-conus-20260720-final",
        release_set_as_of="2026-07-20T14:00:00Z",
        description="Controlled finalization after durable NASA sampling-point receipt collection.",
    )

    release_plan, release_checkpoint = rebind_historical_nasa_checkpoint_for_finalization(
        source_plan,
        finalization,
        checkpoint,
        updated_at=datetime(2026, 7, 20, 14, tzinfo=UTC),
    )

    assert release_plan.release_set_key == finalization.release_set_key
    assert release_checkpoint.state == "validated"
    assert release_checkpoint.plan_checksum == historical_nasa_plan_checksum(release_plan)
    assert release_checkpoint.plan_checksum != checkpoint.plan_checksum
    assert release_checkpoint.raw_cache_plan_checksum == historical_nasa_plan_checksum(source_plan)
    assert historical_nasa_release_manifest(release_plan, release_checkpoint)

    with pytest.raises(ValueError, match="does not identify"):
        rebind_historical_nasa_checkpoint_for_finalization(
            source_plan,
            HistoricalNasaFinalization(
                source_plan_checksum="0" * 64,
                release_set_key="nasa-power-conus-20260720-invalid",
                release_set_as_of="2026-07-20T14:00:00Z",
                description="Invalid finalization.",
            ),
            checkpoint,
        )
    with pytest.raises(ValueError, match="must advance"):
        rebind_historical_nasa_checkpoint_for_finalization(
            source_plan,
            HistoricalNasaFinalization(
                source_plan_checksum=historical_nasa_plan_checksum(source_plan),
                release_set_key="nasa-power-conus-20260720-early",
                release_set_as_of="2026-07-20T12:00:00Z",
                description="Invalid early finalization.",
            ),
            checkpoint,
        )


def test_nasa_finalization_release_plan_is_durable_and_never_overwritten(tmp_path: Path) -> None:
    source_plan = _historical_plan()
    result = parse_nasa_power_daily_payload(
        source_plan.nasa,
        source_plan.nasa.cells[0],
        _complete_payload(source_plan.nasa),
        retrieved_at=datetime(2026, 7, 20, 13, tzinfo=UTC),
    )
    checkpoint = record_historical_nasa_result(
        source_plan,
        initialize_historical_nasa_checkpoint(source_plan, updated_at=datetime(2026, 7, 20, tzinfo=UTC)),
        result,
        updated_at=datetime(2026, 7, 20, 13, tzinfo=UTC),
    )
    finalization = HistoricalNasaFinalization(
        source_plan_checksum=historical_nasa_plan_checksum(source_plan),
        release_set_key="nasa-power-conus-20260720-final-plan",
        release_set_as_of="2026-07-20T14:00:00Z",
        description="Durable finalization plan required for local Parquet materialization.",
    )
    release_plan, _ = rebind_historical_nasa_checkpoint_for_finalization(source_plan, finalization, checkpoint)
    path = tmp_path / "final-release-plan.json"

    write_historical_nasa_release_plan(path, release_plan)
    write_historical_nasa_release_plan(path, release_plan)

    assert HistoricalNasaBackfillPlan.model_validate_json(path.read_bytes()) == release_plan
    with pytest.raises(ValueError, match="different governed plan"):
        write_historical_nasa_release_plan(
            path,
            HistoricalNasaBackfillPlan.model_validate(
                {**release_plan.model_dump(mode="json"), "description": "Different content."}
            ),
        )


def test_historical_nasa_cli_fails_closed_without_the_dedicated_local_loader(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plan_path = tmp_path / "historical-nasa.json"
    plan_path.write_text(_historical_plan().model_dump_json(), encoding="utf-8")
    monkeypatch.setattr(settings, "local_source_loader_database_url", None)

    result = CliRunner().invoke(cli, ["historical-nasa-backfill", "--plan", str(plan_path)])

    assert result.exit_code != 0
    assert "LOCAL_SOURCE_LOADER_DATABASE_URL" in result.output

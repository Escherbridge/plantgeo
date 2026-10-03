"""`agri-service ops lane-audit`, driven end to end through the CLI against realistic lane states.

The two evidence edges are faked and nothing else: the ledger session (dispatching on the identity of
`usage_report`'s own statements, as `test_usage_report.py` does) and the availability coverage read.
The lane catalogue, the lane TOMLs, the provider files, the activation parser, the usage roll-up and
every flag rule run for real. The fleet below is calibrated on the 2026-10-03 production audit.
"""

from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

import pytest
from click.testing import CliRunner

from agri_data_service.execution import lane_audit, usage_report
from agri_data_service.execution.lane_specs import SOIL_CADENCE_SECONDS
from agri_data_service.interface.cli.ops import ops
from agri_data_service.parquet_ops.wire import DayRange, LaneCoverage, LaneRefreshPolicy, WarehouseCoverage

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from agri_data_service.foundation.parquet.lane_contract import LaneNature
    from agri_data_service.parquet_ops.wire import CoverageWithholding

NOW = datetime.now(UTC)
TODAY = NOW.date()
#: The production allow-list minus evacuation-zones, which stays inactive so its silence is not judged.
ACTIVE_LANES = (
    "fire-detections-direct-forward",
    "water-gauges-direct-forward",
    "climate-nasa-power-direct-forward",
    "soil-era5-land-direct-forward",
    "vegetation-sentinel2-ndvi-direct-forward",
    "weather-observations-direct-forward",
    "drought-direct-forward",
    "fire-perimeters-direct-forward",
    "sensors-direct-forward",
)
FULL_POLICY = LaneRefreshPolicy(publication_lag_days=5, source_cadence_days=1, refresh_interval_seconds=3600)
#: What most point lanes publish today: a lag but no cadence or refresh interval.
LAG_ONLY_POLICY = LaneRefreshPolicy(publication_lag_days=1, source_cadence_days=None, refresh_interval_seconds=None)


class Result:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows

    def mappings(self) -> Result:
        return self

    def all(self) -> list[dict[str, object]]:
        return self.rows

    def first(self) -> dict[str, object] | None:
        return self.rows[0] if self.rows else None


class FakeNestedTransaction:
    async def __aenter__(self) -> FakeNestedTransaction:
        return self

    async def __aexit__(self, *_: object) -> bool:
        return False


class FakeLedger:
    """The ledger as `usage_report`'s three statements see it."""

    def __init__(self) -> None:
        self.attempts: list[dict[str, object]] = []
        self.month_to_date: dict[str, dict[str, object]] = {}
        self.incidents: list[dict[str, object]] = []
        #: Statements that raise, as a statement timeout on one section would in production.
        self.failing: list[object] = []

    async def execute(self, statement: object, parameters: dict[str, object] | None = None) -> Result:
        params = parameters or {}
        if statement is usage_report._SET_STATEMENT_TIMEOUT or statement is usage_report._SET_READ_ONLY:
            return Result([])
        if any(statement is failing for failing in self.failing):
            raise TimeoutError("canceling statement due to statement timeout")
        if statement is usage_report._SELECT_PROVIDER_USAGE:
            wanted = params.get("lane_ids")
            return Result(
                [row for row in self.attempts if wanted is None or row["lane_id"] in cast("list[str]", wanted)]
            )
        if statement is usage_report._SELECT_MONTH_TO_DATE:
            row = self.month_to_date.get(str(params["pool"]))
            return Result([row] if row else [])
        if statement is usage_report._SELECT_OPEN_INCIDENTS:
            return Result(self.incidents)
        raise AssertionError(f"lane-audit issued a statement outside usage_report's loaders: {statement}")

    def begin_nested(self) -> FakeNestedTransaction:
        return FakeNestedTransaction()

    async def rollback(self) -> None:
        return None


def attempt(lane_id: str, *, ago: timedelta, **overrides: object) -> dict[str, object]:
    """One `select_provider_usage.sql` row: a settled forward turn that metered one host."""
    finished = NOW - ago
    base: dict[str, object] = {
        "attempt_id": uuid.uuid4(),
        "job_run_id": uuid.uuid4(),
        "job_work_item_id": uuid.uuid4(),
        "lane_id": lane_id,
        "definition_name": f"plantgeo.executor.{lane_id}",
        "scheduled_for": finished - timedelta(minutes=5),
        "started_at": finished - timedelta(minutes=5),
        "finished_at": finished,
        "attempt_status": "succeeded",
        "started_on": finished.date(),
        "exit_class": "ok",
        "turn_outcome": "completed",
        "probe_status": None,
        "usage_reported": True,
        "usage_complete": True,
        "unwritten_known": True,
        "elapsed_seconds": 300,
        "requests": 10,
        "rows_written": 500,
        "bytes_written": 40_000,
        "publication_debt": 0,
        "charged": 0,
        "suspect": 0,
        "host": "example.invalid",
        "provider": "example",
        "pool": None,
        "http_requests": 10,
        "http_429": 0,
        "http_5xx": 0,
        "transport_failures": 0,
        "bytes_in": 1_000_000,
        "backoff_seconds": 0,
        "weighted_calls_metered": 0,
    }
    base.update(overrides)
    return base


def coverage(  # noqa: PLR0913 - one census row is its layer plus the five facts a flag reads
    layer: str,
    *,
    latest: date | None,
    staleness: int = 0,
    behind: bool | None = False,
    gaps: tuple[DayRange, ...] = (),
    withheld: CoverageWithholding | None = None,
    policy: LaneRefreshPolicy | None = FULL_POLICY,
    nature: LaneNature = "daily_series",
) -> LaneCoverage:
    """One rung's census row, its freshness verdict already attached as `with_freshness` would."""
    return LaneCoverage(
        layer=layer,
        nature=nature,
        kind="observed",
        zoom=0,
        earliest_day=None if latest is None else date(2022, 1, 1),
        latest_day=latest,
        latest_recorded_day=latest,
        published_ranges=() if latest is None else (DayRange(date(2022, 1, 1), latest),),
        gap_ranges=gaps,
        governed_absence_ranges=(),
        coverage_authority="availability",
        source_ceiling_day=latest,
        withheld_reason=withheld,
        expected_horizon_day=None if latest is None else latest + timedelta(days=staleness),
        staleness_days=None if latest is None else staleness,
        behind_provider=behind,
        refresh_policy=policy,
    )


@pytest.fixture(autouse=True)
def activation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLANTGEO_JOB_EXECUTOR_ACTIVE_LANES", ",".join(ACTIVE_LANES))
    monkeypatch.delenv("PLANTGEO_JOB_EXECUTOR_STOPPED_LANES", raising=False)


@pytest.fixture
def ledger(monkeypatch: pytest.MonkeyPatch) -> FakeLedger:
    fake = FakeLedger()

    @asynccontextmanager
    async def fake_ingest_session() -> AsyncIterator[Any]:
        yield fake

    monkeypatch.setattr(lane_audit, "ingest_session", fake_ingest_session)
    return fake


@pytest.fixture
def census(monkeypatch: pytest.MonkeyPatch) -> list[LaneCoverage]:
    rows: list[LaneCoverage] = []

    def fake_read_parquet_coverage(*, now: datetime) -> WarehouseCoverage:
        return WarehouseCoverage(generated_at=now, evaluated_through_day=now.date(), lanes=tuple(rows))

    monkeypatch.setattr(lane_audit, "read_parquet_coverage", fake_read_parquet_coverage)
    return rows


@pytest.fixture
def live_fleet(ledger: FakeLedger, census: list[LaneCoverage]) -> FakeLedger:
    """The 2026-10-03 production picture, one lane per finding plus a healthy control."""
    recent = timedelta(minutes=20)
    ledger.attempts += [
        *(
            attempt(
                "sensors-direct-forward",
                ago=recent + timedelta(hours=hour),
                host="api.weather.gov",
                http_requests=600,
                bytes_in=183_000_000,
            )
            for hour in range(2)
        ),
        attempt(
            "weather-observations-direct-forward",
            ago=recent,
            host="api.open-meteo.com",
            provider="open-meteo",
            pool="open-meteo-free",
            http_requests=100,
            http_429=8,
            transport_failures=12,
        ),
        attempt(
            "water-gauges-direct-forward",
            ago=recent,
            host="waterservices.usgs.gov",
            http_requests=100,
            http_5xx=30,
            transport_failures=32,
            exit_class="upstream",
            turn_outcome="upstream_unavailable",
            attempt_status="failed",
        ),
        attempt("fire-perimeters-direct-forward", ago=recent),
        attempt("climate-nasa-power-direct-forward", ago=recent, host="power.larc.nasa.gov", http_requests=50),
        attempt(
            "soil-era5-land-direct-forward",
            ago=timedelta(hours=1),
            host="customer-archive-api.open-meteo.com",
            provider="open-meteo",
            pool="open-meteo-paid",
            http_requests=24,
        ),
    ]
    ledger.incidents.append(
        {
            "id": uuid.uuid4(),
            "fingerprint": "lane_incomplete:fire-perimeters-direct-forward",
            "incident_type": "lane_incomplete",
            "severity": "warning",
            "status": "open",
            "summary": "publication_debt 1 for 3 consecutive turns",
            "occurrence_count": 3,
            "first_seen_at": NOW - timedelta(days=1),
            "last_seen_at": NOW - timedelta(minutes=20),
            "cooldown_until": None,
            "owner": None,
            "acknowledged_at": None,
            "acknowledged_by": None,
            "state": None,
            "rung": None,
            "exit_class": "ok",
            "chain_first_seen_at": None,
        }
    )
    yesterday = TODAY - timedelta(days=1)
    census += [
        coverage("sensors", latest=yesterday, policy=LAG_ONLY_POLICY),
        coverage("climate-field-precipitation", latest=TODAY - timedelta(days=5)),
        coverage("climate-field-shortwave-radiation", latest=TODAY - timedelta(days=9), withheld="availability_stale"),
        coverage("soil-field-moisture-0-7cm", latest=TODAY - timedelta(days=5)),
    ]
    return ledger


def run_audit(*arguments: str) -> dict[str, Any]:
    result = CliRunner().invoke(ops, ["lane-audit", "--format", "json", *arguments])
    assert result.exit_code == 0, result.output
    payload: dict[str, Any] = json.loads(result.output.strip().splitlines()[-1])
    return payload


def lane(report: dict[str, Any], lane_id: str) -> dict[str, Any]:
    return next(entry for entry in report["lanes"] if entry["lane_id"] == lane_id)


def flags(report: dict[str, Any], lane_id: str) -> dict[str, dict[str, Any]]:
    return {flag["code"]: flag for flag in lane(report, lane_id)["flags"]}


@pytest.mark.usefixtures("live_fleet")
def test_sensors_byte_volume_is_flagged_and_its_clean_host_is_not() -> None:
    sensors = flags(run_audit(), "sensors-direct-forward")
    assert sensors["bytes_per_run_anomaly"]["severity"] == "warn"
    assert "183 MB in per run (600 requests/run over 2 run(s))" in sensors["bytes_per_run_anomaly"]["detail"]
    assert "provider_error_rate" not in sensors
    # The lag-only freshness policy most point lanes ship is named, as information, not as a fault.
    assert sensors["freshness_metadata_missing"]["severity"] == "info"
    assert "source_cadence_days, refresh_interval_seconds" in sensors["freshness_metadata_missing"]["detail"]


@pytest.mark.usefixtures("live_fleet")
def test_provider_failure_rates_are_judged_per_host_and_graded() -> None:
    report = run_audit()
    weather = flags(report, "weather-observations-direct-forward")["provider_error_rate"]
    assert weather["severity"] == "warn"
    assert weather["detail"].startswith("api.open-meteo.com: 20.0% failed (429 8.0%, transport 12.0% of 100 requests)")
    water = flags(report, "water-gauges-direct-forward")
    assert water["provider_error_rate"]["severity"] == "critical"
    assert water["provider_error_rate"]["detail"].startswith("waterservices.usgs.gov: 62.0% failed")
    assert water["last_turn_failed"]["severity"] == "warn"
    assert lane(report, "water-gauges-direct-forward")["status"] == "critical"


@pytest.mark.usefixtures("live_fleet")
def test_an_open_incident_attaches_to_the_lane_its_fingerprint_names() -> None:
    report = run_audit()
    incident = flags(report, "fire-perimeters-direct-forward")["open_incident"]
    assert incident["severity"] == "warn"
    assert "publication_debt 1 for 3 consecutive turns" in incident["detail"]
    assert report["unattributed_incidents"] == []


@pytest.mark.usefixtures("live_fleet")
def test_a_withheld_layer_flags_its_writer_lane_and_a_healthy_sibling_adds_nothing() -> None:
    climate = lane(run_audit(), "climate-nasa-power-direct-forward")
    assert [flag["code"] for flag in climate["flags"]] == ["coverage_withheld"]
    assert climate["flags"][0]["detail"].startswith(
        "climate-field-shortwave-radiation: coverage withheld (availability_stale)"
    )
    assert climate["status"] == "warn"


@pytest.mark.usefixtures("live_fleet")
def test_a_healthy_lane_reads_ok_with_its_declared_limits_and_usage() -> None:
    soil = lane(run_audit(), "soil-era5-land-direct-forward")
    assert soil["status"] == "ok"
    assert soil["flags"] == []
    assert soil["declared"]["cadence_seconds"] == SOIL_CADENCE_SECONDS
    assert soil["last_turn"]["exit_class"] == "ok"
    assert soil["usage"]["pools"] == ["open-meteo-paid"]


def test_census_lag_and_holes_flag_the_lane_by_severity(ledger: FakeLedger, census: list[LaneCoverage]) -> None:
    ledger.attempts.append(attempt("vegetation-sentinel2-ndvi-direct-forward", ago=timedelta(minutes=10)))
    hole = DayRange(TODAY - timedelta(days=60), TODAY - timedelta(days=58))
    census.append(
        coverage(
            "vegetation",
            latest=TODAY - timedelta(days=27),
            staleness=20,
            behind=True,
            gaps=(hole,),
            policy=LAG_ONLY_POLICY,
        )
    )
    vegetation = flags(run_audit(), "vegetation-sentinel2-ndvi-direct-forward")
    assert vegetation["behind_horizon"]["severity"] == "critical"
    assert "20 day(s) behind" in vegetation["behind_horizon"]["detail"]
    assert vegetation["coverage_gaps"]["severity"] == "warn"
    assert "3 unwritten day(s) in 1 range(s)" in vegetation["coverage_gaps"]["detail"]


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        (
            {"exit_class": "code", "turn_outcome": "code_error", "attempt_status": "failed"},
            ("last_turn_failed", "critical"),
        ),
        (
            {"exit_class": "upstream", "turn_outcome": "upstream_unavailable", "attempt_status": "failed"},
            ("last_turn_failed", "warn"),
        ),
        ({"turn_outcome": "incomplete"}, ("last_turn_incomplete", "warn")),
        ({"publication_debt": 3}, ("last_turn_incomplete", "warn")),
        ({}, None),
    ],
)
@pytest.mark.usefixtures("census")
def test_the_last_forward_turn_maps_to_one_flag(
    ledger: FakeLedger, overrides: dict[str, object], expected: tuple[str, str] | None
) -> None:
    ledger.attempts.append(attempt("drought-direct-forward", ago=timedelta(minutes=10), **overrides))
    turn_codes = {
        code: flag["severity"]
        for code, flag in flags(run_audit(), "drought-direct-forward").items()
        if code.startswith("last_turn")
    }
    assert turn_codes == ({} if expected is None else {expected[0]: expected[1]})


@pytest.mark.usefixtures("census")
def test_staleness_reads_the_forward_definition_and_only_for_active_lanes(ledger: FakeLedger) -> None:
    ledger.attempts += [
        attempt("fire-detections-direct-forward", ago=timedelta(hours=5)),
        attempt("evacuation-zones-direct-forward", ago=timedelta(hours=5)),
        # Soil (6 h cadence): the forward turn is 30 h old; a gap-repair turn 10 minutes ago must not hide it.
        attempt("soil-era5-land-direct-forward", ago=timedelta(hours=30)),
        attempt(
            "soil-era5-land-direct-forward",
            ago=timedelta(minutes=10),
            definition_name="plantgeo.executor.soil-era5-land-direct-forward:gap-repair",
        ),
    ]
    report = run_audit()
    assert flags(report, "fire-detections-direct-forward")["forward_turn_stale"]["severity"] == "warn"
    assert "forward_turn_stale" not in flags(report, "evacuation-zones-direct-forward")
    soil = lane(report, "soil-era5-land-direct-forward")
    assert "forward_turn_stale" in {flag["code"] for flag in soil["flags"]}
    assert soil["last_turn"]["age_seconds"] >= 30 * 3600
    # ...while the repair turn still counts toward the lane's usage over the window.
    assert soil["usage"]["attempts"] == len([row for row in ledger.attempts if row["lane_id"] == soil["lane_id"]])
    # An active lane with no turn at all in a window longer than twice its cadence is stale, too.
    assert "no settled forward turn" in flags(report, "drought-direct-forward")["forward_turn_stale"]["detail"]


def test_a_dead_ledger_still_reports_census_flags_and_claims_nothing_ok(
    monkeypatch: pytest.MonkeyPatch, census: list[LaneCoverage]
) -> None:
    @asynccontextmanager
    async def unreachable() -> AsyncIterator[Any]:
        raise ValueError("set LOCAL_SOURCE_LOADER_DATABASE_URL or DATABASE_URL")
        yield

    monkeypatch.setattr(lane_audit, "ingest_session", unreachable)
    census.append(
        coverage("climate-field-shortwave-radiation", latest=TODAY - timedelta(days=9), withheld="availability_stale")
    )
    report = run_audit()
    assert "LOCAL_SOURCE_LOADER_DATABASE_URL" in report["section_errors"]["connection"]
    assert "coverage_withheld" in flags(report, "climate-nasa-power-direct-forward")
    assert lane(report, "soil-era5-land-direct-forward")["status"] == "unknown"
    # Without the ledger an active lane's silence is unproven, so it is not called stale.
    assert "forward_turn_stale" not in flags(report, "water-gauges-daily")


def _month_to_date_row(pool: str, *, charged: float) -> dict[str, object]:
    """One `select_provider_month_to_date.sql` row for a pool that has spent `charged` this month."""
    return {
        "pool": pool,
        "epoch_at": NOW - timedelta(days=2),
        "metered_count": 40,
        "reported_count": 0,
        "suspect_basis_count": 0,
        "not_spawned_count": 0,
        "lost_count": 0,
        "charged": charged,
        "suspect": 0,
    }


@pytest.mark.parametrize(
    ("condemning", "failing_section", "soil_status", "climate_status"),
    [
        # Controls: the read answers, so the otherwise healthy soil lane is critical.
        ("lane_hold", None, "critical", "warn"),
        ("pool_past_forward_stop", None, "critical", "warn"),
        # The hold is unreadable: soil must not read ok, and no lane can be ranked below critical.
        ("lane_hold", "open_incidents", "unknown", "unknown"),
        # The pool read fails: only a lane spending a pool loses its verdict; climate spends none.
        ("pool_past_forward_stop", "month_to_date", "unknown", "warn"),
    ],
)
def test_a_ledger_section_that_fails_alone_never_lets_a_lane_it_could_condemn_read_ok(
    live_fleet: FakeLedger, condemning: str, failing_section: str | None, soil_status: str, climate_status: str
) -> None:
    if condemning == "lane_hold":
        live_fleet.incidents.append(
            {
                **live_fleet.incidents[0],
                "id": uuid.uuid4(),
                "fingerprint": "lane_hold:soil-era5-land-direct-forward",
                "incident_type": "lane_hold",
                "severity": "critical",
                "summary": "breaker held the lane after 3 failed turns",
            }
        )
    else:
        live_fleet.month_to_date["open-meteo-paid"] = _month_to_date_row("open-meteo-paid", charged=4_800_000)
    statements = {
        "open_incidents": usage_report._SELECT_OPEN_INCIDENTS,
        "month_to_date": usage_report._SELECT_MONTH_TO_DATE,
    }
    if failing_section is not None:
        live_fleet.failing.append(statements[failing_section])

    report = run_audit()

    soil = lane(report, "soil-era5-land-direct-forward")
    assert soil["status"] == soil_status
    assert lane(report, "climate-nasa-power-direct-forward")["status"] == climate_status
    # A lane whose OWN flag is already critical cannot be hidden by any missing read.
    assert lane(report, "water-gauges-direct-forward")["status"] == "critical"
    if failing_section is not None:
        assert "statement timeout" in report["section_errors"][failing_section]
        assert failing_section in soil["evidence_missing"]


@pytest.mark.usefixtures("live_fleet")
def test_with_no_lane_active_here_a_flag_free_lane_reads_unknown_not_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run off the executor (a laptop, a one-off shell): `active_lane_count == 0` over the WHOLE
    catalogue is the only thing that makes staleness missing for EVERY lane -- nothing here is active,
    so nothing is proven ok. This is deliberately distinct from one lane merely being inactive on a
    normal host (`test_an_inactive_lane_on_a_normal_host_still_reads_ok`, below)."""
    monkeypatch.setenv("PLANTGEO_JOB_EXECUTOR_ACTIVE_LANES", "")
    # The one config lane activates from its TOML, not the allow-list; the kill switch is what stops it.
    monkeypatch.setenv("PLANTGEO_JOB_EXECUTOR_STOPPED_LANES", "water-gauges-daily")

    report = run_audit()

    assert report["activation"]["active_lane_count"] == 0
    soil = lane(report, "soil-era5-land-direct-forward")
    assert soil["flags"] == []
    assert soil["status"] == "unknown"
    assert soil["evidence_missing"] == ["turn_staleness"]
    assert report["summary"]["ok"] == 0
    # A lane with a real warning still reads warn: staleness could not have made it worse than that.
    assert lane(report, "climate-nasa-power-direct-forward")["status"] == "warn"


@pytest.mark.usefixtures("live_fleet")
def test_an_inactive_lane_on_a_normal_host_still_reads_ok() -> None:
    """Review 2026-10-03: `missing_evidence` used to mark `turn_staleness` missing for ANY inactive
    lane, so all 9 deliberately-inactive prod lanes read `unknown` every audit on a perfectly healthy
    host, drowning a real unknown in the same noise. `evacuation-zones-direct-forward` is active's own
    documented holdout (`ACTIVE_LANES`'s comment) and `live_fleet` gives it no usage row, no incident
    and no coverage flag -- clean evidence, deliberately inactive, host otherwise normal."""
    assert "evacuation-zones-direct-forward" not in ACTIVE_LANES

    report = run_audit()

    assert report["activation"]["active_lane_count"] > 0
    evacuation = lane(report, "evacuation-zones-direct-forward")
    assert evacuation["active"] is False
    assert evacuation["flags"] == []
    assert "turn_staleness" not in evacuation["evidence_missing"]
    assert evacuation["status"] == "ok"


@pytest.mark.usefixtures("live_fleet")
def test_a_paid_pool_past_its_gap_fill_ceiling_flags_the_lanes_spending_it(ledger: FakeLedger) -> None:
    ledger.month_to_date["open-meteo-paid"] = _month_to_date_row("open-meteo-paid", charged=3_100_000)
    report = run_audit()
    assert report["pools"]["open-meteo-paid"]["flags"][0]["severity"] == "warn"
    assert flags(report, "soil-era5-land-direct-forward")["pool_budget_pressure"]["severity"] == "warn"
    assert "pool_budget_pressure" not in flags(report, "sensors-direct-forward")


@pytest.mark.usefixtures("live_fleet")
def test_the_lane_filter_narrows_the_audit_and_an_unknown_lane_is_refused() -> None:
    report = run_audit("--lane", "soil-era5-land-direct-forward")
    assert [entry["lane_id"] for entry in report["lanes"]] == ["soil-era5-land-direct-forward"]
    assert report["unowned_layers"] == []
    refused = CliRunner().invoke(ops, ["lane-audit", "--lane", "soil-era5-land-direct-forwrd"])
    assert refused.exit_code != 0
    assert "unknown lane(s) soil-era5-land-direct-forwrd" in refused.output


@pytest.mark.usefixtures("live_fleet")
def test_the_table_puts_each_lane_on_one_row_with_its_flag_codes() -> None:
    result = CliRunner().invoke(ops, ["lane-audit"])
    assert result.exit_code == 0, result.output
    sensors_row = next(line for line in result.output.splitlines() if line.startswith("sensors-direct-forward"))
    assert "bytes_per_run_anomaly" in sensors_row
    assert "183.0" in sensors_row
    assert "sensors-direct-forward: [warn] bytes_per_run_anomaly" in result.output

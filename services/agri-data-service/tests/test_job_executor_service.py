"""Cutover gates, lane isolation, cadence, and shadow safety for the unified executor."""

# ruff: noqa: PLR2004

from __future__ import annotations

import asyncio
import json
import subprocess
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from agri_data_service.execution import job_executor_service
from agri_data_service.execution.job_executor_service import (
    ACTIVE_LANES_VARIABLE,
    HANDOFF_ACKNOWLEDGEMENTS_VARIABLE,
    LANE_SPECS,
    LEGACY_RAILWAY_RESPONSIBILITIES,
    LEGACY_RAILWAY_SERVICE_IDS,
    ActivationConfig,
    DueLane,
    ExecutorConfigurationError,
    fair_due_order,
    next_scheduled_bucket,
    parse_activation,
    run_executor_tick,
    run_scheduled_command,
    scheduled_bucket,
)
from agri_data_service.jobs import JobDefinitionRecord, JobInvocation, RetryPolicy, ShutdownSignal
from agri_data_service.pipeline.parquet.lane_registry import LANE_REGISTRATIONS, LANE_REGISTRY

if TYPE_CHECKING:
    from collections.abc import Mapping
    from typing import Any

_DEFINITION_ID = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
_INGEST_OWNER = "plantgeo-ingest-cron"
#: Twelve database-backed lanes, eleven source-direct NASA POWER streams, eight source-direct
#: Open-Meteo ERA5-Land streams and `calendar`. Pinned so a lane added without its spec, or a spec
#: added without its lane, fails here rather than in production's scheduler.
_EXPECTED_REGISTRATION_COUNT = 32
#: The 32 generic `parquet-*` specs plus the 27 non-parquet duties (PostgreSQL ingestion, jobs
#: maintenance and the migration-input lanes, which now include two direct writers).
_EXPECTED_SPEC_COUNT = 59
_DIRECT_FIRE_OWNER = "plantgeo-fire-detections-forward"
_DIRECT_WATER_OWNER = "plantgeo-water-gauges-forward"


def _acknowledgements(*lane_ids: str) -> str:
    return ",".join(
        f"{lane_id}={acknowledgement}"
        for lane_id in lane_ids
        for acknowledgement in LANE_SPECS[lane_id].required_handoff_acknowledgements
    )


def _activation_environment(*lane_ids: str) -> dict[str, str]:
    return {
        ACTIVE_LANES_VARIABLE: ",".join(lane_ids),
        HANDOFF_ACKNOWLEDGEMENTS_VARIABLE: _acknowledgements(*lane_ids),
    }


def _owned_executable_lanes(owner: str) -> tuple[str, ...]:
    return tuple(lane_id for lane_id, spec in LANE_SPECS.items() if spec.executable and owner in spec.legacy_owners)


def _definition(
    name: str,
    *,
    identifier: uuid.UUID = _DEFINITION_ID,
    version: str = job_executor_service.EXECUTOR_DEFINITION_VERSION,
    handler: str = job_executor_service.EXECUTOR_HANDLER_TOKEN,
) -> JobDefinitionRecord:
    return JobDefinitionRecord(
        id=identifier,
        name=name,
        version=version,
        handler=handler,
        queue_name="default",
        concurrency_key=None,
        max_attempts=5,
        lease_seconds=1020,
        time_budget_seconds=930,
        retry_policy=RetryPolicy(),
        parameters=MappingProxyType({}),
    )


def _due(lane_id: str, last: datetime | None) -> DueLane:
    spec = LANE_SPECS[lane_id]
    return DueLane(
        spec=spec,
        definition=_definition(spec.definition_name),
        scheduled_for=datetime(2026, 8, 28, 18, tzinfo=UTC),
        existing_run_id=None,
        last_scheduled_for=last,
    )


def test_registry_splits_ingest_parquet_and_jobs_pulse_failure_domains() -> None:
    assert "postgres-forward" not in LANE_SPECS
    assert "durable-jobs-pulse" not in LANE_SPECS
    assert "parquet-gap-fill" not in LANE_SPECS

    assert LANE_SPECS["postgres-vegetation"].command == ("agri-service", "data", "ingest-ndvi")
    assert LANE_SPECS["vegetation-catch-up"].command == (
        "agri-service",
        "data",
        "parquet-catch-up-vegetation",
    )
    assert LANE_SPECS["vegetation-catch-up"].legacy_owners == (_INGEST_OWNER,)
    assert LANE_SPECS["parquet-vegetation"].command is not None
    assert "parquet-gap-fill" in LANE_SPECS["parquet-vegetation"].command
    assert LANE_SPECS["postgres-watersheds"].legacy_owners == ()
    assert LANE_SPECS["postgres-watersheds"].executable


def test_every_parquet_stream_has_one_bounded_registered_command() -> None:
    registrations = {registration.slug: registration for registration in LANE_REGISTRATIONS}
    parquet_specs = {
        lane_id.removeprefix("parquet-"): spec for lane_id, spec in LANE_SPECS.items() if lane_id.startswith("parquet-")
    }
    assert set(parquet_specs) == set(registrations)

    for slug, registration in registrations.items():
        spec = parquet_specs[slug]
        assert spec.command is not None
        assert spec.command[:3] == ("agri-service", "data", "parquet-gap-fill")
        assert spec.command[3:5] == ("--layer", slug)
        assert spec.command[5:7] == ("--max-days-per-lane", "1")
        assert spec.publication_lag_days == registration.publication_lag_days
        assert spec.publication_cadence_days == registration.cadence_days
        expected_ceiling = None if registration.writer_ceiling is None else registration.writer_ceiling.isoformat()
        assert spec.writer_ceiling == expected_ceiling


def test_jobs_pulse_lanes_and_maintenance_are_independent_failure_domains() -> None:
    durable_lanes = ("matview-refresh", "strategy-mv-refresh", "firms-archive", "streamflow-archive")
    for durable_lane in durable_lanes:
        spec = LANE_SPECS[f"jobs-{durable_lane}"]
        assert spec.command is not None
        assert spec.command[:3] == ("agri-service", "ops", "jobs-pulse")
        assert spec.command[3:5] == ("--lane", durable_lane)
        assert "--skip-maintenance" in spec.command
        assert spec.definition_name.endswith(f"jobs-{durable_lane}")

    assert LANE_SPECS["jobs-matview-refresh"].command_timeout_seconds >= 2400
    assert LANE_SPECS["jobs-strategy-mv-refresh"].command_timeout_seconds >= 900
    assert LANE_SPECS["jobs-firms-archive"].command_timeout_seconds >= 1080
    assert LANE_SPECS["jobs-streamflow-archive"].command_timeout_seconds >= 1080

    maintenance = {
        "maintenance-firms-archive-reconcile",
        "maintenance-firms-archive-plan-gaps",
        "maintenance-streamflow-archive-reconcile",
        "maintenance-streamflow-archive-plan-gaps",
        "maintenance-validate-streams",
    }
    assert maintenance <= LANE_SPECS.keys()
    assert len({LANE_SPECS[lane_id].definition_name for lane_id in maintenance}) == len(maintenance)
    strategy = LANE_SPECS["jobs-strategy-mv-refresh"]
    assert (strategy.cadence_seconds, strategy.phase_offset_seconds, strategy.schedule) == (
        900,
        0,
        "*/15 * * * *",
    )
    assert strategy.catch_up_policy == "coalesce_latest"


def test_source_specific_runtime_and_completed_snapshot_dispositions_are_explicit() -> None:
    assert LANE_SPECS["soilgrids-cache-warm"].migration_disposition == "source-specific"
    assert LANE_SPECS["soilgrids-cache-warm"].command == (
        "node",
        "/app/plantgeo/scripts/warm-soilgrids.mjs",
        "120",
    )
    assert LANE_SPECS["soilgrids-cache-warm"].executable
    assert LANE_SPECS["soil-moisture-parquet-backfill"].migration_disposition == "snapshot-only"
    assert LANE_SPECS["soil-moisture-parquet-backfill"].command is None
    assert not LANE_SPECS["soil-moisture-parquet-backfill"].executable


def test_every_observed_legacy_railway_writer_has_a_complete_terminal_mapping() -> None:
    expected_ids = {
        _INGEST_OWNER: "3ae3cc37-c398-43fe-b74c-83e4da130423",
        "plantgeo-cron-mtbs": "a683cc83-2b49-4276-a136-941e1b2cbe24",
        "plantgeo-cron-soilgrids": "0960aa81-4499-4cb1-9daa-3350eed4d654",
        _DIRECT_FIRE_OWNER: "f4ad61fe-e71a-4776-b9d5-0b153c9ee5b7",
        _DIRECT_WATER_OWNER: "40cb252b-e21c-4140-8d94-5db77eb2398d",
        "plantgeo-soil-moisture-parquet-load": "4a1413f1-5f96-44ea-853c-6a379c7673c4",
    }
    expected = set(expected_ids)
    assert expected_ids == LEGACY_RAILWAY_SERVICE_IDS
    assert set(LEGACY_RAILWAY_RESPONSIBILITIES) == expected

    mapped_lanes: set[str] = set()
    for owner, responsibility in LEGACY_RAILWAY_RESPONSIBILITIES.items():
        assert responsibility.service_name == owner
        assert responsibility.service_id == expected_ids[owner]
        assert responsibility.replacement_lanes
        mapped_lanes.update(responsibility.replacement_lanes)
        for lane_id in responsibility.replacement_lanes:
            assert owner in LANE_SPECS[lane_id].legacy_owners
        if responsibility.terminal_disposition is None:
            assert all(LANE_SPECS[lane_id].executable for lane_id in responsibility.replacement_lanes)
        else:
            assert responsibility.replacement_lanes == ("soil-moisture-parquet-backfill",)
            assert "never schedule" in responsibility.terminal_disposition

    owned_lanes = {spec.lane_id for spec in LANE_SPECS.values() if set(spec.legacy_owners) & expected}
    assert mapped_lanes == owned_lanes

    inventory = job_executor_service.executor_inventory(ActivationConfig(frozenset()))
    responsibility_rows = cast("list[dict[str, object]]", inventory["legacy_railway_responsibilities"])
    assert {row["service_name"] for row in responsibility_rows} == expected
    assert {row["service_name"]: row["service_id"] for row in responsibility_rows} == expected_ids
    lane_rows = cast("list[dict[str, object]]", inventory["lanes"])
    assert all(row["catch_up_policy"] in {"coalesce_latest", "replay_oldest"} for row in lane_rows)
    assert all("checkpoint" in row and "retry_policy" in row for row in lane_rows)


def test_drought_poll_runs_daily_after_each_publication_lag_day() -> None:
    drought = LANE_SPECS["postgres-drought"]
    assert drought.publication_lag_days == 4
    assert drought.cadence_seconds == 86400
    assert drought.phase_offset_seconds == 43200
    assert drought.schedule == "0 12 * * *"


def test_empty_activation_is_shadow() -> None:
    activation = parse_activation({})
    assert activation.active_lanes == frozenset()
    assert activation.handoff_acknowledgements == {}


@pytest.mark.parametrize(
    ("environment", "message"),
    [
        ({ACTIVE_LANES_VARIABLE: "not-a-lane"}, "unknown active lane"),
        ({ACTIVE_LANES_VARIABLE: "fire-detections-direct-forward"}, "missing="),
        (_activation_environment("soil-moisture-parquet-backfill"), "has no executor command"),
        (
            {
                **_activation_environment("fire-detections-direct-forward"),
                HANDOFF_ACKNOWLEDGEMENTS_VARIABLE: (
                    "fire-detections-direct-forward="
                    f"{_DIRECT_FIRE_OWNER}:disabled-and-no-run-in-flight,"
                    "fire-detections-direct-forward=unexpected-owner:disabled"
                ),
            },
            "extra=",
        ),
        (
            {
                HANDOFF_ACKNOWLEDGEMENTS_VARIABLE: (
                    f"fire-detections-direct-forward={_DIRECT_FIRE_OWNER}:disabled-and-no-run-in-flight"
                )
            },
            "inactive lane",
        ),
    ],
)
def test_activation_fails_closed_on_unknown_missing_extra_and_inactive_acknowledgements(
    environment: dict[str, str],
    message: str,
) -> None:
    with pytest.raises(ExecutorConfigurationError, match=message):
        parse_activation(environment)


def test_selected_lane_without_declared_acknowledgements_needs_only_the_allow_list() -> None:
    lane_id = "postgres-watersheds"
    activation = parse_activation(
        {
            ACTIVE_LANES_VARIABLE: lane_id,
            HANDOFF_ACKNOWLEDGEMENTS_VARIABLE: "",
        }
    )
    assert activation.active_lanes == frozenset({lane_id})
    assert activation.handoff_acknowledgements == {}


def test_direct_fire_cutover_is_accepted_and_generic_history_keeps_ceiling() -> None:
    lane_id = "fire-detections-direct-forward"
    activation = parse_activation(_activation_environment(lane_id))
    assert activation.active_lanes == frozenset({lane_id})
    ceiling = LANE_REGISTRY["fire-detections"].writer_ceiling
    assert ceiling is not None
    assert LANE_SPECS["parquet-fire-detections"].writer_ceiling == ceiling.isoformat()


def test_water_parquet_requires_only_the_ingest_owner_acknowledgement() -> None:
    lane_id = "parquet-water-gauges"
    environment = {
        ACTIVE_LANES_VARIABLE: lane_id,
        HANDOFF_ACKNOWLEDGEMENTS_VARIABLE: (f"{lane_id}={_DIRECT_WATER_OWNER}:disabled-and-no-run-in-flight"),
    }
    with pytest.raises(ExecutorConfigurationError, match=_INGEST_OWNER):
        parse_activation(environment)

    spec = LANE_SPECS[lane_id]
    assert spec.legacy_owners == (_INGEST_OWNER,)
    assert spec.required_handoff_acknowledgements == (f"{_INGEST_OWNER}:disabled-and-no-run-in-flight",)


def test_direct_water_and_generic_gap_repair_are_distinct_serialized_duties() -> None:
    lanes = ("parquet-water-gauges", "water-gauges-direct-forward")
    assert LANE_SPECS[lanes[0]].conflicts_with == ()
    assert LANE_SPECS[lanes[1]].conflicts_with == ()
    assert LANE_SPECS[lanes[1]].command == (
        "python",
        "-m",
        "agri_data_service.pipeline.parquet.water_gauges_forward",
    )
    assert LANE_SPECS[lanes[1]].executable
    assert LANE_SPECS[lanes[1]].migration_disposition == "source-specific"
    assert LANE_SPECS[lanes[1]].legacy_owners == (_DIRECT_WATER_OWNER,)
    assert LANE_SPECS[lanes[1]].phase_offset_seconds == 900
    assert LANE_SPECS[lanes[1]].schedule == "15 * * * *"
    assert LANE_SPECS[lanes[0]].writer_ceiling == "2026-09-01"
    assert LANE_SPECS[lanes[1]].writer_floor == "2026-09-02"


def test_the_climate_forward_lane_is_owned_by_nobody_and_activates_alone() -> None:
    """Nothing has EVER produced a forward NASA POWER climate day, so there is no handoff to make.

    Every other migration-input lane replaces a legacy Railway writer and must acknowledge it as
    disabled first. This one replaces a gap: the only POWER ingestion in the tree is a retired local
    backfill verb, and the Parquet history was built once from the immutable canonical snapshot.
    Giving it a legacy owner would invent a cutover dependency and pull eight refusing generic lanes
    into the ingest-cron atomic group with it.
    """
    lane_id = "climate-nasa-power-direct-forward"
    spec = LANE_SPECS[lane_id]

    assert spec.legacy_owners == ()
    assert spec.required_handoff_acknowledgements == ()
    assert spec.command == ("python", "-m", "agri_data_service.pipeline.direct.climate")
    assert spec.schedule == "40 * * * *"
    assert spec.phase_offset_seconds == 2400
    assert spec.phase_offset_seconds != LANE_SPECS["fire-detections-direct-forward"].phase_offset_seconds
    assert spec.migration_disposition == "source-specific"
    assert spec.executable

    assert parse_activation({}).is_active(lane_id) is False
    assert parse_activation({ACTIVE_LANES_VARIABLE: lane_id}).active_lanes == frozenset({lane_id})

    climate_lanes = {
        f"parquet-{registration.slug}"
        for registration in LANE_REGISTRATIONS
        if registration.slug.startswith("climate-field-")
    }
    assert len(climate_lanes) == 8
    # The three soil-wetness depths are POWER lanes too -- same point request, same lattice, same
    # lag -- so the direct writer owns eleven generic specs, not eight. Reading the count off the
    # `climate-field-` prefix alone would leave three lanes silently outside the conflict set.
    power_lanes = climate_lanes | {
        "parquet-soil-wetness-surface",
        "parquet-soil-wetness-root-zone",
        "parquet-soil-wetness-profile",
    }
    assert set(spec.conflicts_with) == power_lanes
    ingest_owned = {candidate.lane_id for candidate in LANE_SPECS.values() if _INGEST_OWNER in candidate.legacy_owners}
    assert not power_lanes & ingest_owned
    assert lane_id not in ingest_owned


def test_the_direct_climate_writer_and_its_generic_specs_refuse_to_run_together() -> None:
    """Two owners of one calendar, and the generic one's registered adapter can only ever fail.

    Declared on BOTH sides so `parse_activation` refuses whichever an operator names first, and so
    the inventory row of each generic spec states the constraint it is subject to rather than
    leaving it discoverable only from the direct lane.
    """
    direct = "climate-nasa-power-direct-forward"
    generic = "parquet-climate-field-precipitation"

    assert generic in LANE_SPECS[direct].conflicts_with
    assert LANE_SPECS[generic].conflicts_with == (direct,)
    with pytest.raises(ExecutorConfigurationError, match="conflicts with active lane"):
        parse_activation({ACTIVE_LANES_VARIABLE: f"{direct},{generic}"})
    with pytest.raises(ExecutorConfigurationError, match="conflicts with active lane"):
        parse_activation({ACTIVE_LANES_VARIABLE: f"{generic},{direct}"})
    assert parse_activation({ACTIVE_LANES_VARIABLE: generic}).is_active(generic) is True


def test_the_two_direct_writers_own_disjoint_lane_sets_and_may_run_together() -> None:
    """Two shadow writers, two upstreams, no shared calendar: nothing may refuse the pairing.

    They are separate lanes rather than one because they read different providers on different
    release schedules -- POWER at a measured 5-day lag, the Open-Meteo ERA5-Land archive at a
    measured 9 -- and one writer would have to hold to the slower of the two for every stream.
    """
    climate = LANE_SPECS["climate-nasa-power-direct-forward"]
    soil = LANE_SPECS["soil-era5-land-direct-forward"]

    assert set(climate.conflicts_with).isdisjoint(soil.conflicts_with)
    assert climate.phase_offset_seconds != soil.phase_offset_seconds
    assert climate.schedule != soil.schedule
    assert parse_activation({ACTIVE_LANES_VARIABLE: f"{climate.lane_id},{soil.lane_id}"}).active_lanes == frozenset(
        {climate.lane_id, soil.lane_id}
    )


def test_the_direct_soil_writer_and_its_generic_specs_refuse_to_run_together() -> None:
    """Declared on BOTH sides, so `parse_activation` refuses whichever an operator names first."""
    direct = "soil-era5-land-direct-forward"
    generic = "parquet-soil-field-vpd"

    assert generic in LANE_SPECS[direct].conflicts_with
    assert LANE_SPECS[generic].conflicts_with == (direct,)
    with pytest.raises(ExecutorConfigurationError, match="conflicts with active lane"):
        parse_activation({ACTIVE_LANES_VARIABLE: f"{direct},{generic}"})
    with pytest.raises(ExecutorConfigurationError, match="conflicts with active lane"):
        parse_activation({ACTIVE_LANES_VARIABLE: f"{generic},{direct}"})
    assert parse_activation({ACTIVE_LANES_VARIABLE: generic}).is_active(generic) is True


def test_every_registered_lane_has_exactly_one_generic_parquet_spec() -> None:
    """The registration table IS the parquet spec table; a drift between them is a lane nothing runs."""
    expected = {f"parquet-{registration.slug}" for registration in LANE_REGISTRATIONS}
    parquet_specs = {lane_id for lane_id in LANE_SPECS if lane_id.startswith("parquet-")}

    assert parquet_specs == expected
    assert len(LANE_REGISTRATIONS) == _EXPECTED_REGISTRATION_COUNT
    assert len(LANE_SPECS) == _EXPECTED_SPEC_COUNT


def test_complete_recurring_railway_responsibility_set_can_activate_together() -> None:
    lanes = tuple(
        dict.fromkeys(
            lane_id
            for responsibility in LEGACY_RAILWAY_RESPONSIBILITIES.values()
            if responsibility.terminal_disposition is None
            for lane_id in responsibility.replacement_lanes
        )
    )
    activation = parse_activation(_activation_environment(*lanes))
    assert activation.active_lanes == frozenset(lanes)
    assert "soil-moisture-parquet-backfill" not in activation.active_lanes


def test_one_ingest_owner_lane_activates_alone_now_that_the_legacy_cron_is_fenced() -> None:
    """Owner decision 2026-09-03: Postgres ingestion retires lane by lane, so no atomic-owner rule remains."""
    lane_id = "postgres-weather"
    activation = parse_activation(_activation_environment(lane_id))
    assert activation.active_lanes == frozenset({lane_id})


def test_complete_ingest_owner_cutover_is_still_accepted() -> None:
    lanes = _owned_executable_lanes(_INGEST_OWNER)
    activation = parse_activation(_activation_environment(*lanes))
    assert activation.active_lanes == frozenset(lanes)
    assert "postgres-watersheds" not in activation.active_lanes
    assert "parquet-water-gauges" in activation.active_lanes
    assert "vegetation-catch-up" in activation.active_lanes


def test_schedule_bucket_honours_lane_phase() -> None:
    fire = LANE_SPECS["fire-detections-direct-forward"]
    assert scheduled_bucket(fire, datetime(2026, 8, 28, 18, 14, 59, tzinfo=UTC)) == datetime(
        2026, 8, 28, 17, 15, tzinfo=UTC
    )
    assert scheduled_bucket(fire, datetime(2026, 8, 28, 18, 15, tzinfo=UTC)) == datetime(
        2026, 8, 28, 18, 15, tzinfo=UTC
    )


def test_mtbs_weekly_and_soilgrids_hourly_phases_are_exact() -> None:
    mtbs = LANE_SPECS["mtbs-forward"]
    soilgrids = LANE_SPECS["soilgrids-cache-warm"]
    assert (mtbs.cadence_seconds, mtbs.phase_offset_seconds, mtbs.schedule) == (
        604800,
        460500,
        "55 7 * * 2",
    )
    assert scheduled_bucket(mtbs, datetime(2026, 9, 2, 12, tzinfo=UTC)) == datetime(2026, 9, 1, 7, 55, tzinfo=UTC)
    assert (soilgrids.cadence_seconds, soilgrids.phase_offset_seconds, soilgrids.schedule) == (
        3600,
        1500,
        "25 * * * *",
    )
    assert scheduled_bucket(soilgrids, datetime(2026, 9, 2, 12, 24, 59, tzinfo=UTC)) == datetime(
        2026, 9, 2, 11, 25, tzinfo=UTC
    )


def test_restart_catch_up_coalesces_source_polls_and_replays_oldest_durable_bucket() -> None:
    now = datetime(2026, 8, 28, 18, 30, tzinfo=UTC)
    previous = datetime(2026, 8, 28, 12, tzinfo=UTC)
    source = LANE_SPECS["postgres-weather"]
    durable = LANE_SPECS["parquet-weather-observations"]
    assert source.catch_up_policy == "coalesce_latest"
    assert next_scheduled_bucket(source, now, previous) == datetime(2026, 8, 28, 18, tzinfo=UTC)
    assert durable.catch_up_policy == "replay_oldest"
    assert next_scheduled_bucket(durable, now, previous) == datetime(2026, 8, 28, 13, tzinfo=UTC)
    assert LANE_SPECS["jobs-firms-archive"].catch_up_policy == "replay_oldest"
    assert LANE_SPECS["maintenance-firms-archive-reconcile"].catch_up_policy == "coalesce_latest"


def test_durable_definition_records_exact_schedule_and_catch_up_metadata() -> None:
    mtbs = LANE_SPECS["mtbs-forward"]
    definition = mtbs.definition_spec()
    assert definition.version == job_executor_service.EXECUTOR_DEFINITION_VERSION
    assert definition.schedule == "55 7 * * 2"
    assert definition.parameters["cadence_seconds"] == 604800
    assert definition.parameters["phase_offset_seconds"] == 460500
    assert definition.parameters["catch_up_policy"] == "coalesce_latest"
    assert definition.parameters["work_class"] == "incremental"
    assert definition.parameters["migration_disposition"] == "consolidatable"


def test_fair_order_interleaves_incremental_and_backlog_and_rotates_oldest() -> None:
    older = datetime(2026, 8, 28, 15, tzinfo=UTC)
    newer = datetime(2026, 8, 28, 16, tzinfo=UTC)
    ordered = fair_due_order(
        (
            _due("fire-detections-direct-forward", newer),
            _due("postgres-weather", older),
            _due("parquet-water-gauges", newer),
            _due("jobs-firms-archive", older),
        )
    )
    assert [entry.spec.lane_id for entry in ordered] == [
        "postgres-weather",
        "jobs-firms-archive",
        "fire-detections-direct-forward",
        "parquet-water-gauges",
    ]


async def test_retry_backoff_lane_cannot_starve_same_class_peer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    incremental_id = "fire-detections-direct-forward"
    backoff_id = "jobs-firms-archive"
    backlog_peer_id = "jobs-streamflow-archive"
    lane_ids = (incremental_id, backoff_id, backlog_peer_id)
    specs = MappingProxyType({lane_id: LANE_SPECS[lane_id] for lane_id in lane_ids})
    identifiers = {lane_id: uuid.uuid5(uuid.NAMESPACE_URL, f"plantgeo-test:{lane_id}") for lane_id in lane_ids}
    definitions = {
        lane_id: _definition(specs[lane_id].definition_name, identifier=identifiers[lane_id]) for lane_id in lane_ids
    }
    prior_bucket = datetime(2026, 8, 27, 12, tzinfo=UTC)
    latest = {
        incremental_id: job_executor_service.LatestRun(
            run_id=uuid.uuid4(),
            scheduled_for=prior_bucket,
            status="succeeded",
            work_claimable=False,
        ),
        backoff_id: job_executor_service.LatestRun(
            run_id=uuid.uuid4(),
            scheduled_for=datetime(2026, 8, 26, 12, tzinfo=UTC),
            status="running",
            work_claimable=False,
        ),
        backlog_peer_id: job_executor_service.LatestRun(
            run_id=uuid.uuid4(),
            scheduled_for=prior_bucket,
            status="succeeded",
            work_claimable=False,
        ),
    }
    attempted: list[str] = []
    session = _ShadowSession()

    async def _noop_timeout(_session: object) -> None:
        return None

    async def _leader(_session: object) -> bool:
        return True

    async def _unlock(_session: object) -> None:
        return None

    async def _load(_session: object, spec: job_executor_service.LaneExecutionSpec) -> JobDefinitionRecord:
        return definitions[spec.lane_id]

    async def _latest(
        _session: object,
        lane_spec: job_executor_service.LaneExecutionSpec,
    ) -> job_executor_service.LatestRun:
        return latest[lane_spec.lane_id]

    async def _execute(
        _session: object,
        candidate: DueLane,
        **_kwargs: object,
    ) -> job_executor_service.LaneTickResult:
        attempted.append(candidate.spec.lane_id)
        return job_executor_service.LaneTickResult(lane_id=candidate.spec.lane_id, state="ran")

    monkeypatch.setattr(job_executor_service, "LANE_SPECS", specs)
    monkeypatch.setattr(job_executor_service, "apply_statement_timeout", _noop_timeout)
    monkeypatch.setattr(job_executor_service, "_try_leader_lock", _leader)
    monkeypatch.setattr(job_executor_service, "_release_leader_lock", _unlock)
    monkeypatch.setattr(job_executor_service, "_load_or_register_definition", _load)
    monkeypatch.setattr(job_executor_service, "read_lane_checkpoint", _latest)
    monkeypatch.setattr(job_executor_service, "_execute_due_lane", _execute)

    summary = await run_executor_tick(
        session,  # type: ignore[arg-type]
        activation=ActivationConfig(frozenset(lane_ids)),
        now=datetime(2026, 8, 28, 18, 30, tzinfo=UTC),
        max_lanes_per_tick=2,
    )

    assert attempted == [incremental_id, backlog_peer_id]
    backoff = next(lane for lane in summary.lanes if lane.lane_id == backoff_id)
    assert backoff.state == "not_due"
    assert backoff.run_status == "running"
    assert backoff.detail is not None
    assert "no currently claimable work" in backoff.detail


def test_latest_run_claimability_matches_the_worker_claim_and_reaper_contract() -> None:
    query = str(job_executor_service._SELECT_LATEST_RUN)
    assert "item.attempt_count < item.max_attempts" in query
    assert "item.available_at <= now()" in query
    assert "item.next_attempt_at IS NULL OR item.next_attempt_at <= now()" in query
    assert "item.status IN ('leased', 'running')" in query
    assert "item.lease_expires_at <= now()" in query
    fresh_arm = query.index("item.status IN ('queued', 'retry_wait', 'deferred')")
    attempt_guard = query.index("item.attempt_count < item.max_attempts")
    expired_arm = query.index("item.status IN ('leased', 'running')")
    assert fresh_arm < attempt_guard < expired_arm
    assert "attempt_count" not in query[expired_arm:]


async def test_exhausted_expired_lease_reaches_the_reaper_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lane_id = "jobs-firms-archive"
    spec = LANE_SPECS[lane_id]
    definition = _definition(spec.definition_name)
    run_id = uuid.uuid4()
    session = _ShadowSession()
    attempted: list[uuid.UUID | None] = []

    async def _noop_timeout(_session: object) -> None:
        return None

    async def _leader(_session: object) -> bool:
        return True

    async def _unlock(_session: object) -> None:
        return None

    async def _load(_session: object, _spec: object) -> JobDefinitionRecord:
        return definition

    async def _latest(_session: object, _definition_id: uuid.UUID) -> job_executor_service.LatestRun:
        return job_executor_service.LatestRun(
            run_id=run_id,
            scheduled_for=datetime(2026, 8, 28, 16, tzinfo=UTC),
            status="running",
            work_claimable=True,
        )

    async def _execute(
        _session: object,
        candidate: DueLane,
        **_kwargs: object,
    ) -> job_executor_service.LaneTickResult:
        attempted.append(candidate.existing_run_id)
        return job_executor_service.LaneTickResult(lane_id=lane_id, state="ran")

    monkeypatch.setattr(job_executor_service, "LANE_SPECS", MappingProxyType({lane_id: spec}))
    monkeypatch.setattr(job_executor_service, "apply_statement_timeout", _noop_timeout)
    monkeypatch.setattr(job_executor_service, "_try_leader_lock", _leader)
    monkeypatch.setattr(job_executor_service, "_release_leader_lock", _unlock)
    monkeypatch.setattr(job_executor_service, "_load_or_register_definition", _load)
    monkeypatch.setattr(job_executor_service, "read_lane_checkpoint", _latest)
    monkeypatch.setattr(job_executor_service, "_execute_due_lane", _execute)

    summary = await run_executor_tick(
        session,  # type: ignore[arg-type]
        activation=ActivationConfig(frozenset({lane_id})),
        now=datetime(2026, 8, 28, 18, tzinfo=UTC),
        max_lanes_per_tick=2,
    )

    assert attempted == [run_id]
    assert next(lane for lane in summary.lanes if lane.lane_id == lane_id).state == "ran"


class _ShadowSession:
    def __init__(self) -> None:
        self.rollbacks = 0

    async def rollback(self) -> None:
        self.rollbacks += 1

    async def execute(self, _statement: object) -> None:
        return None


async def test_restart_resumes_the_exact_open_logical_bucket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lane_id = "jobs-firms-archive"
    spec = LANE_SPECS[lane_id]
    definition = _definition(spec.definition_name)
    open_bucket = datetime(2026, 8, 28, 12, tzinfo=UTC)
    run_id = uuid.uuid4()
    session = _ShadowSession()

    async def _load(_session: object, _spec: object) -> JobDefinitionRecord:
        return definition

    async def _latest(_session: object, _definition_id: uuid.UUID) -> job_executor_service.LatestRun:
        return job_executor_service.LatestRun(
            run_id=run_id,
            scheduled_for=open_bucket,
            status="running",
            work_claimable=True,
        )

    monkeypatch.setattr(job_executor_service, "LANE_SPECS", MappingProxyType({lane_id: spec}))
    monkeypatch.setattr(job_executor_service, "_load_or_register_definition", _load)
    monkeypatch.setattr(job_executor_service, "read_lane_checkpoint", _latest)

    results, due = await job_executor_service._plan_active_lanes(
        session,  # type: ignore[arg-type]
        ActivationConfig(frozenset({lane_id})),
        datetime(2026, 8, 28, 18, 30, tzinfo=UTC),
    )

    assert results == []
    assert len(due) == 1
    assert due[0].scheduled_for == open_bucket
    assert due[0].existing_run_id == run_id


@pytest.mark.parametrize(
    ("lane_id", "expected_bucket"),
    [
        ("postgres-weather", datetime(2026, 8, 28, 18, tzinfo=UTC)),
        ("parquet-weather-observations", datetime(2026, 8, 28, 13, tzinfo=UTC)),
    ],
)
async def test_missed_tick_policy_is_applied_when_the_latest_run_settled(
    monkeypatch: pytest.MonkeyPatch,
    lane_id: str,
    expected_bucket: datetime,
) -> None:
    spec = LANE_SPECS[lane_id]
    definition = _definition(spec.definition_name)
    session = _ShadowSession()

    async def _load(_session: object, _spec: object) -> JobDefinitionRecord:
        return definition

    async def _latest(_session: object, _definition_id: uuid.UUID) -> job_executor_service.LatestRun:
        return job_executor_service.LatestRun(
            run_id=uuid.uuid4(),
            scheduled_for=datetime(2026, 8, 28, 12, tzinfo=UTC),
            status="succeeded",
            work_claimable=False,
        )

    monkeypatch.setattr(job_executor_service, "LANE_SPECS", MappingProxyType({lane_id: spec}))
    monkeypatch.setattr(job_executor_service, "_load_or_register_definition", _load)
    monkeypatch.setattr(job_executor_service, "read_lane_checkpoint", _latest)

    results, due = await job_executor_service._plan_active_lanes(
        session,  # type: ignore[arg-type]
        ActivationConfig(frozenset({lane_id})),
        datetime(2026, 8, 28, 18, 30, tzinfo=UTC),
    )

    assert results == []
    assert [candidate.scheduled_for for candidate in due] == [expected_bucket]


async def test_nonterminal_prior_version_run_resumes_through_its_exact_definition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lane_id = "jobs-firms-archive"
    spec = LANE_SPECS[lane_id]
    prior_definition = _definition(spec.definition_name, version="1")
    prior_run_id = uuid.uuid4()
    session = _ShadowSession()

    async def _load_current(_session: object, _spec: object) -> None:
        return None

    async def _latest(_session: object, _spec: object) -> job_executor_service.LatestRun:
        return job_executor_service.LatestRun(
            run_id=prior_run_id,
            scheduled_for=datetime(2026, 8, 28, 12, tzinfo=UTC),
            status="running",
            work_claimable=True,
            definition_id=prior_definition.id,
            definition_version="1",
            definition_enabled=True,
        )

    async def _load_prior(_session: object, name: str, *, version: str) -> JobDefinitionRecord:
        assert name == spec.definition_name
        assert version == "1"
        return prior_definition

    monkeypatch.setattr(job_executor_service, "LANE_SPECS", MappingProxyType({lane_id: spec}))
    monkeypatch.setattr(job_executor_service, "_load_or_register_definition", _load_current)
    monkeypatch.setattr(job_executor_service, "read_lane_checkpoint", _latest)
    monkeypatch.setattr(job_executor_service, "load_job_definition", _load_prior)

    results, due = await job_executor_service._plan_active_lanes(
        session,  # type: ignore[arg-type]
        ActivationConfig(frozenset({lane_id})),
        datetime(2026, 8, 28, 18, 30, tzinfo=UTC),
    )

    assert results == []
    assert session.rollbacks == 1
    assert len(due) == 1
    assert due[0].definition == prior_definition
    assert due[0].existing_run_id == prior_run_id
    assert due[0].scheduled_for == datetime(2026, 8, 28, 12, tzinfo=UTC)


async def test_prior_version_terminal_child_crash_repairs_the_parent_rollup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lane_id = "jobs-firms-archive"
    spec = LANE_SPECS[lane_id]
    prior_definition = _definition(spec.definition_name, version="1")
    prior_run_id = uuid.uuid4()
    session = _ShadowSession()
    captured: dict[str, object] = {}

    async def _load_current(_session: object, _spec: object) -> None:
        return None

    async def _latest(_session: object, _spec: object) -> job_executor_service.LatestRun:
        return job_executor_service.LatestRun(
            run_id=prior_run_id,
            scheduled_for=datetime(2026, 8, 28, 12, tzinfo=UTC),
            status="running",
            work_claimable=False,
            terminal_items_need_rollup=True,
            definition_id=prior_definition.id,
            definition_version="1",
            definition_enabled=True,
        )

    async def _load_prior(_session: object, _name: str, *, version: str) -> JobDefinitionRecord:
        assert version == "1"
        return prior_definition

    async def _run_slice(*_args: object, **kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return SimpleNamespace(
            retried=0,
            dead_lettered=0,
            abandoned=0,
            run_status="succeeded",
            stop_reason="no_claimable_work",
            to_summary=dict,
        )

    monkeypatch.setattr(job_executor_service, "LANE_SPECS", MappingProxyType({lane_id: spec}))
    monkeypatch.setattr(job_executor_service, "_load_or_register_definition", _load_current)
    monkeypatch.setattr(job_executor_service, "read_lane_checkpoint", _latest)
    monkeypatch.setattr(job_executor_service, "load_job_definition", _load_prior)
    monkeypatch.setattr(job_executor_service, "run_job_slice", _run_slice)

    results, due = await job_executor_service._plan_active_lanes(
        session,  # type: ignore[arg-type]
        ActivationConfig(frozenset({lane_id})),
        datetime(2026, 8, 28, 18, 30, tzinfo=UTC),
    )
    assert results == []
    assert len(due) == 1
    assert due[0].definition == prior_definition
    assert due[0].existing_run_id == prior_run_id

    repaired = await job_executor_service._execute_due_lane(
        session,  # type: ignore[arg-type]
        due[0],
        stop=None,
    )
    assert repaired.state == "ran"
    assert repaired.run_status == "succeeded"
    assert captured["version"] == "1"
    assert captured["job_run_id"] == prior_run_id


async def test_current_version_terminal_child_crash_is_due_for_parent_rollup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lane_id = "jobs-firms-archive"
    spec = LANE_SPECS[lane_id]
    current_definition = _definition(spec.definition_name)
    run_id = uuid.uuid4()
    session = _ShadowSession()

    async def _load_current(_session: object, _spec: object) -> JobDefinitionRecord:
        return current_definition

    async def _latest(_session: object, _spec: object) -> job_executor_service.LatestRun:
        return job_executor_service.LatestRun(
            run_id=run_id,
            scheduled_for=datetime(2026, 8, 28, 12, tzinfo=UTC),
            status="running",
            work_claimable=False,
            terminal_items_need_rollup=True,
        )

    monkeypatch.setattr(job_executor_service, "LANE_SPECS", MappingProxyType({lane_id: spec}))
    monkeypatch.setattr(job_executor_service, "_load_or_register_definition", _load_current)
    monkeypatch.setattr(job_executor_service, "read_lane_checkpoint", _latest)

    results, due = await job_executor_service._plan_active_lanes(
        session,  # type: ignore[arg-type]
        ActivationConfig(frozenset({lane_id})),
        datetime(2026, 8, 28, 18, 30, tzinfo=UTC),
    )

    assert results == []
    assert len(due) == 1
    assert due[0].definition == current_definition
    assert due[0].existing_run_id == run_id


def test_nonterminal_run_without_work_items_refuses_instead_of_waiting_forever() -> None:
    spec = LANE_SPECS["jobs-firms-archive"]
    run_id = uuid.uuid4()
    result = job_executor_service._blocked_open_run_result(
        spec,
        job_executor_service.LatestRun(
            run_id=run_id,
            scheduled_for=datetime(2026, 8, 28, 12, tzinfo=UTC),
            status="queued",
            work_claimable=False,
            has_work_items=False,
        ),
        prior_version=False,
    )

    assert result is not None
    assert result.state == "failed"
    assert result.run_id == run_id
    assert result.detail is not None
    assert "no work items" in result.detail
    assert "repair or cancel" in result.detail


async def test_live_prior_version_lease_blocks_current_version_without_overlap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lane_id = "jobs-firms-archive"
    spec = LANE_SPECS[lane_id]
    prior_run_id = uuid.uuid4()
    session = _ShadowSession()

    async def _load_current(_session: object, _spec: object) -> JobDefinitionRecord:
        return _definition(spec.definition_name)

    async def _latest(_session: object, _spec: object) -> job_executor_service.LatestRun:
        return job_executor_service.LatestRun(
            run_id=prior_run_id,
            scheduled_for=datetime(2026, 8, 28, 12, tzinfo=UTC),
            status="running",
            work_claimable=False,
            definition_version="1",
            definition_enabled=True,
        )

    async def _unexpected(*_args: object, **_kwargs: object) -> Any:
        raise AssertionError("a live prior-version lease loaded or opened current-version work")

    monkeypatch.setattr(job_executor_service, "LANE_SPECS", MappingProxyType({lane_id: spec}))
    monkeypatch.setattr(job_executor_service, "_load_or_register_definition", _load_current)
    monkeypatch.setattr(job_executor_service, "read_lane_checkpoint", _latest)
    monkeypatch.setattr(job_executor_service, "load_job_definition", _unexpected)
    monkeypatch.setattr(job_executor_service, "open_job_run", _unexpected)

    results, due = await job_executor_service._plan_active_lanes(
        session,  # type: ignore[arg-type]
        ActivationConfig(frozenset({lane_id})),
        datetime(2026, 8, 28, 18, 30, tzinfo=UTC),
    )

    assert due == []
    assert len(results) == 1
    assert results[0].state == "not_due"
    assert results[0].run_id == prior_run_id
    assert results[0].detail is not None
    assert "prior definition version '1'" in results[0].detail
    assert "live lease" in results[0].detail


async def test_disabled_prior_version_open_run_refuses_current_version_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lane_id = "jobs-firms-archive"
    spec = LANE_SPECS[lane_id]
    prior_run_id = uuid.uuid4()
    session = _ShadowSession()

    async def _load_current(_session: object, _spec: object) -> JobDefinitionRecord:
        return _definition(spec.definition_name)

    async def _latest(_session: object, _spec: object) -> job_executor_service.LatestRun:
        return job_executor_service.LatestRun(
            run_id=prior_run_id,
            scheduled_for=datetime(2026, 8, 28, 12, tzinfo=UTC),
            status="queued",
            work_claimable=True,
            definition_version="1",
            definition_enabled=False,
        )

    async def _unexpected(*_args: object, **_kwargs: object) -> Any:
        raise AssertionError("a disabled prior version loaded or opened current-version work")

    monkeypatch.setattr(job_executor_service, "LANE_SPECS", MappingProxyType({lane_id: spec}))
    monkeypatch.setattr(job_executor_service, "_load_or_register_definition", _load_current)
    monkeypatch.setattr(job_executor_service, "read_lane_checkpoint", _latest)
    monkeypatch.setattr(job_executor_service, "load_job_definition", _unexpected)
    monkeypatch.setattr(job_executor_service, "open_job_run", _unexpected)

    results, due = await job_executor_service._plan_active_lanes(
        session,  # type: ignore[arg-type]
        ActivationConfig(frozenset({lane_id})),
        datetime(2026, 8, 28, 18, 30, tzinfo=UTC),
    )

    assert due == []
    assert len(results) == 1
    assert results[0].state == "failed"
    assert results[0].run_id == prior_run_id
    assert results[0].detail is not None
    assert "disabled" in results[0].detail
    assert "resume or cancel" in results[0].detail


async def test_terminal_prior_version_run_remains_the_catch_up_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lane_id = "parquet-weather-observations"
    spec = LANE_SPECS[lane_id]
    current_definition = _definition(spec.definition_name)
    session = _ShadowSession()

    async def _load_current(_session: object, _spec: object) -> JobDefinitionRecord:
        return current_definition

    async def _latest(_session: object, _spec: object) -> job_executor_service.LatestRun:
        return job_executor_service.LatestRun(
            run_id=uuid.uuid4(),
            scheduled_for=datetime(2026, 8, 28, 12, tzinfo=UTC),
            status="succeeded",
            work_claimable=False,
            definition_version="1",
            definition_enabled=True,
        )

    monkeypatch.setattr(job_executor_service, "LANE_SPECS", MappingProxyType({lane_id: spec}))
    monkeypatch.setattr(job_executor_service, "_load_or_register_definition", _load_current)
    monkeypatch.setattr(job_executor_service, "read_lane_checkpoint", _latest)

    results, due = await job_executor_service._plan_active_lanes(
        session,  # type: ignore[arg-type]
        ActivationConfig(frozenset({lane_id})),
        datetime(2026, 8, 28, 18, 30, tzinfo=UTC),
    )

    assert results == []
    assert len(due) == 1
    assert due[0].definition == current_definition
    assert due[0].existing_run_id is None
    assert due[0].last_scheduled_for == datetime(2026, 8, 28, 12, tzinfo=UTC)
    assert due[0].scheduled_for == datetime(2026, 8, 28, 13, tzinfo=UTC)

    query = str(job_executor_service._SELECT_LATEST_RUN)
    assert "definition.name = :name" in query
    assert "WITH prior_version_open AS" in query
    assert "current_version_open AS" in query
    assert "latest_terminal_per_definition AS" in query
    assert "SELECT 0 AS selection_rank" in query
    assert "SELECT 1 AS selection_rank" in query
    assert "SELECT 2 AS selection_rank" in query
    assert query.count("LIMIT 1") == 5
    assert "row_number()" not in query.lower()
    assert "terminal_items_need_rollup" in query


async def test_prior_version_candidate_executes_with_stored_version_and_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lane_id = "jobs-firms-archive"
    spec = LANE_SPECS[lane_id]
    prior_definition = _definition(spec.definition_name, version="1")
    prior_run_id = uuid.uuid4()
    candidate = DueLane(
        spec=spec,
        definition=prior_definition,
        scheduled_for=datetime(2026, 8, 28, 12, tzinfo=UTC),
        existing_run_id=prior_run_id,
        last_scheduled_for=datetime(2026, 8, 28, 12, tzinfo=UTC),
    )
    captured: dict[str, object] = {}

    async def _unexpected_open(*_args: object, **_kwargs: object) -> Any:
        raise AssertionError("resuming a prior-version run attempted to open a new run")

    async def _run_slice(*_args: object, **kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return SimpleNamespace(
            retried=0,
            dead_lettered=0,
            abandoned=0,
            run_status="succeeded",
            stop_reason="completed",
            to_summary=dict,
        )

    monkeypatch.setattr(job_executor_service, "open_job_run", _unexpected_open)
    monkeypatch.setattr(job_executor_service, "run_job_slice", _run_slice)

    result = await job_executor_service._execute_due_lane(object(), candidate, stop=None)  # type: ignore[arg-type]

    assert result.state == "ran"
    assert captured["version"] == "1"
    assert captured["job_run_id"] == prior_run_id
    assert captured["budget_seconds"] == float(prior_definition.time_budget_seconds)


def test_lane_wide_latest_run_query_keeps_expired_prior_leases_reapable() -> None:
    query = str(job_executor_service._SELECT_LATEST_RUN)
    assert "item.status IN ('leased', 'running')" in query
    assert "item.lease_expires_at <= now()" in query
    expired_arm = query.index("item.status IN ('leased', 'running')")
    assert "attempt_count" not in query[expired_arm:]


def test_logical_bucket_is_stable_throughout_one_cadence_window() -> None:
    spec = LANE_SPECS["soilgrids-cache-warm"]
    previous = datetime(2026, 9, 2, 10, 25, tzinfo=UTC)
    expected = datetime(2026, 9, 2, 12, 25, tzinfo=UTC)
    assert next_scheduled_bucket(spec, datetime(2026, 9, 2, 12, 25, tzinfo=UTC), previous) == expected
    assert next_scheduled_bucket(spec, datetime(2026, 9, 2, 13, 24, 59, tzinfo=UTC), previous) == expected


async def test_shadow_tick_emits_due_predictions_without_ledger_or_layer_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _ShadowSession()

    async def _noop_timeout(_session: object) -> None:
        return None

    async def _leader(_session: object) -> bool:
        return True

    async def _unlock(_session: object) -> None:
        return None

    async def _unexpected(*_args: object, **_kwargs: object) -> Any:
        raise AssertionError("a shadow tick attempted a ledger or layer write")

    monkeypatch.setattr(job_executor_service, "apply_statement_timeout", _noop_timeout)
    monkeypatch.setattr(job_executor_service, "_try_leader_lock", _leader)
    monkeypatch.setattr(job_executor_service, "_release_leader_lock", _unlock)
    monkeypatch.setattr(job_executor_service, "_load_or_register_definition", _unexpected)
    monkeypatch.setattr(job_executor_service, "open_job_run", _unexpected)
    monkeypatch.setattr(job_executor_service, "run_job_slice", _unexpected)

    summary = await run_executor_tick(
        session,  # type: ignore[arg-type]
        activation=ActivationConfig(frozenset()),
        now=datetime(2026, 8, 28, 18, tzinfo=UTC),
        max_lanes_per_tick=2,
    )

    assert summary.leader
    executable = [lane for lane in summary.lanes if LANE_SPECS[lane.lane_id].executable]
    assert executable
    assert all(lane.state == "shadow" for lane in executable)
    assert all(lane.scheduled_for is not None for lane in executable)
    assert all(lane.command for lane in executable)
    assert all(lane.handoff_blockers for lane in executable)
    assert all(
        lane.due_prediction == "would_be_due_if_activated; source watermark parity not evaluated" for lane in executable
    )


async def test_scheduler_refuses_single_lane_ticks_before_touching_the_database() -> None:
    with pytest.raises(ExecutorConfigurationError, match="at least 2"):
        await run_executor_tick(
            object(),  # type: ignore[arg-type]
            activation=ActivationConfig(frozenset()),
            now=datetime(2026, 8, 28, 18, tzinfo=UTC),
            max_lanes_per_tick=1,
        )


async def test_planning_reapplies_statement_timeout_after_every_transaction_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lane_id = "postgres-watersheds"
    spec = LANE_SPECS[lane_id]
    definition = _definition(spec.definition_name)
    specs = MappingProxyType({lane_id: spec})
    current_bucket = scheduled_bucket(spec, datetime(2026, 8, 28, 18, 30, tzinfo=UTC))
    events: list[str] = []

    class _ArmedSession:
        def __init__(self) -> None:
            self.armed = False

        async def commit(self) -> None:
            assert self.armed
            events.append("commit")
            self.armed = False

        async def rollback(self) -> None:
            events.append("rollback")
            self.armed = False

    session = _ArmedSession()
    definition_state_calls = 0

    def _assert_armed(event: str) -> None:
        assert session.armed, f"{event} ran without transaction-local statement_timeout"
        events.append(event)

    async def _timeout(_session: object) -> None:
        session.armed = True
        events.append("timeout")

    async def _leader(_session: object) -> bool:
        _assert_armed("leader")
        return True

    async def _pause_state(_session: object, _name: str) -> SimpleNamespace:
        _assert_armed("pause_state")
        return SimpleNamespace(registered=False, paused=False)

    async def _definition_state(
        _session: object,
        _spec: object,
    ) -> tuple[uuid.UUID, bool] | None:
        nonlocal definition_state_calls
        _assert_armed("definition_state")
        definition_state_calls += 1
        return None if definition_state_calls == 1 else (_DEFINITION_ID, True)

    async def _insert(*_args: object, **_kwargs: object) -> Mapping[str, object]:
        _assert_armed("insert_definition")
        return {}

    async def _load_definition(*_args: object, **_kwargs: object) -> JobDefinitionRecord:
        _assert_armed("load_definition")
        return definition

    async def _latest(*_args: object, **_kwargs: object) -> job_executor_service.LatestRun:
        _assert_armed("latest_run")
        return job_executor_service.LatestRun(
            run_id=uuid.uuid4(),
            scheduled_for=current_bucket,
            status="succeeded",
            work_claimable=False,
        )

    async def _unlock(_session: object) -> None:
        _assert_armed("unlock")

    monkeypatch.setattr(job_executor_service, "LANE_SPECS", specs)
    monkeypatch.setattr(job_executor_service, "apply_statement_timeout", _timeout)
    monkeypatch.setattr(job_executor_service, "_try_leader_lock", _leader)
    monkeypatch.setattr(job_executor_service, "read_lane_pause_state", _pause_state)
    monkeypatch.setattr(job_executor_service, "_definition_state", _definition_state)
    monkeypatch.setattr(job_executor_service, "fetch_row", _insert)
    monkeypatch.setattr(job_executor_service, "load_job_definition", _load_definition)
    monkeypatch.setattr(job_executor_service, "read_lane_checkpoint", _latest)
    monkeypatch.setattr(job_executor_service, "_release_leader_lock", _unlock)

    summary = await run_executor_tick(
        session,  # type: ignore[arg-type]
        activation=ActivationConfig(frozenset({lane_id})),
        now=datetime(2026, 8, 28, 18, 30, tzinfo=UTC),
        max_lanes_per_tick=2,
    )

    assert summary.leader
    assert events.count("timeout") == 5
    assert events == [
        "timeout",
        "leader",
        "pause_state",
        "definition_state",
        "insert_definition",
        "commit",
        "timeout",
        "definition_state",
        "load_definition",
        "rollback",
        "timeout",
        "latest_run",
        "rollback",
        "timeout",
        "rollback",
        "timeout",
        "unlock",
        "rollback",
    ]


async def test_unlock_false_is_a_hard_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _unlock_false(*_args: object, **_kwargs: object) -> Mapping[str, object]:
        return {"released": False}

    monkeypatch.setattr(job_executor_service, "fetch_row", _unlock_false)
    with pytest.raises(job_executor_service.ExecutorLeaderUnlockError, match="did not release"):
        await job_executor_service._release_leader_lock(object())  # type: ignore[arg-type]


async def test_unlock_failure_does_not_mask_the_primary_tick_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class _PrimaryTickError(RuntimeError):
        pass

    session = _ShadowSession()

    async def _noop_timeout(_session: object) -> None:
        return None

    async def _leader(_session: object) -> bool:
        return True

    async def _primary(*_args: object, **_kwargs: object) -> tuple[list[object], list[object]]:
        raise _PrimaryTickError("primary")

    async def _unlock_error(_session: object) -> None:
        raise job_executor_service.ExecutorLeaderUnlockError("unlock")

    async def _invalidate(_session: object) -> None:
        return None

    monkeypatch.setattr(job_executor_service, "apply_statement_timeout", _noop_timeout)
    monkeypatch.setattr(job_executor_service, "_try_leader_lock", _leader)
    monkeypatch.setattr(job_executor_service, "_plan_active_lanes", _primary)
    monkeypatch.setattr(job_executor_service, "_release_leader_lock", _unlock_error)
    monkeypatch.setattr(job_executor_service, "_invalidate_leader_connection", _invalidate)

    with pytest.raises(_PrimaryTickError, match="primary"):
        await run_executor_tick(
            session,  # type: ignore[arg-type]
            activation=ActivationConfig(frozenset()),
            now=datetime(2026, 8, 28, 18, tzinfo=UTC),
            max_lanes_per_tick=2,
        )


async def test_unlock_failure_without_primary_error_invalidates_and_fails_tick(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _ShadowSession()
    invalidated = False

    async def _noop_timeout(_session: object) -> None:
        return None

    async def _leader(_session: object) -> bool:
        return True

    async def _unlock_error(_session: object) -> None:
        raise job_executor_service.ExecutorLeaderUnlockError("unlock")

    async def _invalidate(_session: object) -> None:
        nonlocal invalidated
        invalidated = True

    monkeypatch.setattr(job_executor_service, "apply_statement_timeout", _noop_timeout)
    monkeypatch.setattr(job_executor_service, "_try_leader_lock", _leader)
    monkeypatch.setattr(job_executor_service, "_release_leader_lock", _unlock_error)
    monkeypatch.setattr(job_executor_service, "_invalidate_leader_connection", _invalidate)

    with pytest.raises(job_executor_service.ExecutorLeaderUnlockError, match="unlock"):
        await run_executor_tick(
            session,  # type: ignore[arg-type]
            activation=ActivationConfig(frozenset()),
            now=datetime(2026, 8, 28, 18, tzinfo=UTC),
            max_lanes_per_tick=2,
        )
    assert invalidated


async def test_invalidated_pinned_connection_aborts_before_a_second_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _ShadowSession()
    session.bind = SimpleNamespace(invalidated=False)  # type: ignore[attr-defined]
    candidates = [_due("postgres-weather", None), _due("jobs-firms-archive", None)]
    attempted: list[str] = []

    async def _noop_timeout(_session: object) -> None:
        return None

    async def _leader(_session: object) -> bool:
        return True

    async def _plan(*_args: object, **_kwargs: object) -> tuple[list[object], list[DueLane]]:
        return [], candidates

    async def _execute(_session: object, candidate: DueLane, **_kwargs: object) -> object:
        attempted.append(candidate.spec.lane_id)
        session.bind.invalidated = True  # type: ignore[attr-defined]
        raise RuntimeError("backend disconnected")

    async def _unlock(_session: object) -> None:
        return None

    monkeypatch.setattr(job_executor_service, "apply_statement_timeout", _noop_timeout)
    monkeypatch.setattr(job_executor_service, "_try_leader_lock", _leader)
    monkeypatch.setattr(job_executor_service, "_plan_active_lanes", _plan)
    monkeypatch.setattr(job_executor_service, "_execute_due_lane", _execute)
    monkeypatch.setattr(job_executor_service, "_release_leader_lock", _unlock)

    with pytest.raises(RuntimeError, match="backend disconnected"):
        await run_executor_tick(
            session,  # type: ignore[arg-type]
            activation=ActivationConfig(frozenset()),
            now=datetime(2026, 8, 28, 18, tzinfo=UTC),
            max_lanes_per_tick=2,
        )

    assert attempted == ["postgres-weather"]


async def test_service_loop_binds_each_tick_session_to_one_external_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = object()

    class _ConnectionContext:
        async def __aenter__(self) -> object:
            return connection

        async def __aexit__(self, *_args: object) -> None:
            return None

    class _Pool:
        def connect(self) -> _ConnectionContext:
            return _ConnectionContext()

    class _PinnedSession:
        def __init__(self, *, bind: object, expire_on_commit: bool) -> None:
            assert not expire_on_commit
            self.bind = bind

        async def __aenter__(self) -> _PinnedSession:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

    @asynccontextmanager
    async def _pool(_database_url: str) -> Any:
        yield _Pool()

    @asynccontextmanager
    async def _stop() -> Any:
        yield SimpleNamespace(requested=False)

    async def _tick(session: object, **_kwargs: object) -> job_executor_service.ExecutorTickSummary:
        assert isinstance(session, _PinnedSession)
        assert session.bind is connection
        return job_executor_service.ExecutorTickSummary(
            observed_at=datetime(2026, 8, 28, 18, tzinfo=UTC),
            leader=True,
            lanes=(),
        )

    monkeypatch.setattr(
        job_executor_service,
        "settings",
        SimpleNamespace(require_local_source_loader_database_url=lambda: "postgresql+asyncpg://test/db"),
    )
    monkeypatch.setattr(job_executor_service, "local_source_loader_pool", _pool)
    monkeypatch.setattr(job_executor_service, "shutdown_signal", _stop)
    monkeypatch.setattr(job_executor_service, "AsyncSession", _PinnedSession)
    monkeypatch.setattr(job_executor_service, "run_executor_tick", _tick)
    monkeypatch.setattr(job_executor_service.click, "echo", lambda *_args, **_kwargs: None)

    result = await job_executor_service._service_loop(
        activation=ActivationConfig(frozenset()),
        poll_seconds=30,
        max_lanes_per_tick=2,
        once=True,
    )
    assert result == 0


class _ObservedShutdownSignal(ShutdownSignal):
    def __init__(self) -> None:
        super().__init__()
        self.wait_entered = asyncio.Event()

    async def wait_requested(self) -> None:
        self.wait_entered.set()
        await super().wait_requested()


def _install_service_loop_harness(
    monkeypatch: pytest.MonkeyPatch,
    *,
    stop: ShutdownSignal,
    tick: object,
) -> None:
    class _ConnectionContext:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *_args: object) -> None:
            return None

    class _Pool:
        def connect(self) -> _ConnectionContext:
            return _ConnectionContext()

    class _PinnedSession:
        def __init__(self, *, bind: object, expire_on_commit: bool) -> None:
            self.bind = bind
            assert not expire_on_commit

        async def __aenter__(self) -> _PinnedSession:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

    @asynccontextmanager
    async def _pool(_database_url: str) -> Any:
        yield _Pool()

    @asynccontextmanager
    async def _stop() -> Any:
        yield stop

    monkeypatch.setattr(
        job_executor_service,
        "settings",
        SimpleNamespace(require_local_source_loader_database_url=lambda: "postgresql+asyncpg://test/db"),
    )
    monkeypatch.setattr(job_executor_service, "local_source_loader_pool", _pool)
    monkeypatch.setattr(job_executor_service, "shutdown_signal", _stop)
    monkeypatch.setattr(job_executor_service, "AsyncSession", _PinnedSession)
    monkeypatch.setattr(job_executor_service, "run_executor_tick", tick)
    monkeypatch.setattr(job_executor_service.click, "echo", lambda *_args, **_kwargs: None)


async def test_sigterm_wakes_normal_poll_wait_without_second_tick(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stop = _ObservedShutdownSignal()
    attempts = 0

    async def _tick(*_args: object, **_kwargs: object) -> job_executor_service.ExecutorTickSummary:
        nonlocal attempts
        attempts += 1
        return job_executor_service.ExecutorTickSummary(
            observed_at=datetime(2026, 8, 28, 18, tzinfo=UTC),
            leader=True,
            lanes=(),
        )

    _install_service_loop_harness(monkeypatch, stop=stop, tick=_tick)
    service = asyncio.create_task(
        job_executor_service._service_loop(
            activation=ActivationConfig(frozenset()),
            poll_seconds=3600,
            max_lanes_per_tick=2,
            once=False,
        )
    )
    await asyncio.wait_for(stop.wait_entered.wait(), timeout=1)
    stop.request("container shutdown requested (SIGTERM)")

    assert await asyncio.wait_for(service, timeout=1) == 0
    assert attempts == 1


async def test_sigterm_wakes_error_backoff_without_retrying_tick(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stop = _ObservedShutdownSignal()
    attempts = 0

    async def _tick(*_args: object, **_kwargs: object) -> job_executor_service.ExecutorTickSummary:
        nonlocal attempts
        attempts += 1
        raise RuntimeError("planned tick failure")

    _install_service_loop_harness(monkeypatch, stop=stop, tick=_tick)
    service = asyncio.create_task(
        job_executor_service._service_loop(
            activation=ActivationConfig(frozenset()),
            poll_seconds=3600,
            max_lanes_per_tick=2,
            once=False,
        )
    )
    await asyncio.wait_for(stop.wait_entered.wait(), timeout=1)
    stop.request("container shutdown requested (SIGTERM)")

    assert await asyncio.wait_for(service, timeout=1) == 0
    assert attempts == 1


async def test_sigterm_between_candidates_does_not_open_second_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stop = ShutdownSignal()
    session = _ShadowSession()
    candidates = [_due("fire-detections-direct-forward", None), _due("jobs-firms-archive", None)]
    attempted: list[str] = []

    async def _noop_timeout(_session: object) -> None:
        return None

    async def _leader(_session: object) -> bool:
        return True

    async def _plan(*_args: object, **_kwargs: object) -> tuple[list[object], list[DueLane]]:
        return [], candidates

    async def _execute(
        _session: object,
        candidate: DueLane,
        **_kwargs: object,
    ) -> job_executor_service.LaneTickResult:
        attempted.append(candidate.spec.lane_id)
        stop.request("container shutdown requested (SIGTERM)")
        return job_executor_service.LaneTickResult(lane_id=candidate.spec.lane_id, state="ran")

    async def _unlock(_session: object) -> None:
        return None

    monkeypatch.setattr(job_executor_service, "apply_statement_timeout", _noop_timeout)
    monkeypatch.setattr(job_executor_service, "_try_leader_lock", _leader)
    monkeypatch.setattr(job_executor_service, "_plan_active_lanes", _plan)
    monkeypatch.setattr(job_executor_service, "_execute_due_lane", _execute)
    monkeypatch.setattr(job_executor_service, "_release_leader_lock", _unlock)

    summary = await run_executor_tick(
        session,  # type: ignore[arg-type]
        activation=ActivationConfig(frozenset()),
        now=datetime(2026, 8, 28, 18, tzinfo=UTC),
        max_lanes_per_tick=2,
        stop=stop,
    )

    assert attempted == ["fire-detections-direct-forward"]
    deferred = next(lane for lane in summary.lanes if lane.lane_id == "jobs-firms-archive")
    assert deferred.state == "deferred_shutdown"


async def test_one_executor_tick_never_overlaps_source_writer_processes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _ShadowSession()
    candidates = [_due("postgres-weather", None), _due("fire-detections-direct-forward", None)]
    active = 0
    maximum_active = 0
    order: list[str] = []

    async def _noop_timeout(_session: object) -> None:
        return None

    async def _leader(_session: object) -> bool:
        return True

    async def _plan(*_args: object, **_kwargs: object) -> tuple[list[object], list[DueLane]]:
        return [], candidates

    async def _execute(
        _session: object,
        candidate: DueLane,
        **_kwargs: object,
    ) -> job_executor_service.LaneTickResult:
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        order.append(f"start:{candidate.spec.lane_id}")
        await asyncio.sleep(0)
        order.append(f"stop:{candidate.spec.lane_id}")
        active -= 1
        return job_executor_service.LaneTickResult(lane_id=candidate.spec.lane_id, state="ran")

    async def _unlock(_session: object) -> None:
        return None

    monkeypatch.setattr(job_executor_service, "apply_statement_timeout", _noop_timeout)
    monkeypatch.setattr(job_executor_service, "_try_leader_lock", _leader)
    monkeypatch.setattr(job_executor_service, "_plan_active_lanes", _plan)
    monkeypatch.setattr(job_executor_service, "_execute_due_lane", _execute)
    monkeypatch.setattr(job_executor_service, "_release_leader_lock", _unlock)

    await run_executor_tick(
        session,  # type: ignore[arg-type]
        activation=ActivationConfig(frozenset()),
        now=datetime(2026, 8, 28, 18, tzinfo=UTC),
        max_lanes_per_tick=2,
    )

    assert maximum_active == 1
    assert order == [
        "start:fire-detections-direct-forward",
        "stop:fire-detections-direct-forward",
        "start:postgres-weather",
        "stop:postgres-weather",
    ]


async def test_existing_pause_is_not_overwritten(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _ShadowSession()
    spec = LANE_SPECS["fire-detections-direct-forward"]

    async def _noop_timeout(_session: object) -> None:
        return None

    async def _paused(_session: object, _spec: object) -> tuple[uuid.UUID, bool]:
        return _DEFINITION_ID, False

    async def _lane_pause(_session: object, _name: str) -> SimpleNamespace:
        return SimpleNamespace(registered=True, paused=True)

    async def _unexpected(*_args: object, **_kwargs: object) -> Any:
        raise AssertionError("an existing paused definition was upserted")

    monkeypatch.setattr(job_executor_service, "apply_statement_timeout", _noop_timeout)
    monkeypatch.setattr(job_executor_service, "read_lane_pause_state", _lane_pause)
    monkeypatch.setattr(job_executor_service, "_definition_state", _paused)
    monkeypatch.setattr(job_executor_service, "fetch_row", _unexpected)

    definition = await job_executor_service._load_or_register_definition(session, spec)  # type: ignore[arg-type]
    assert definition is None
    assert session.rollbacks == 1


async def test_new_definition_version_inherits_a_lane_wide_pause(monkeypatch: pytest.MonkeyPatch) -> None:
    spec = LANE_SPECS["fire-detections-direct-forward"]
    inserted: dict[str, object] = {}
    definition_state_calls = 0

    class _RegistrationSession(_ShadowSession):
        def __init__(self) -> None:
            super().__init__()
            self.commits = 0

        async def commit(self) -> None:
            self.commits += 1

    session = _RegistrationSession()

    async def _noop_timeout(_session: object) -> None:
        return None

    async def _lane_pause(_session: object, _name: str) -> SimpleNamespace:
        return SimpleNamespace(registered=True, paused=True)

    async def _definition_state(
        _session: object,
        _spec: object,
    ) -> tuple[uuid.UUID, bool] | None:
        nonlocal definition_state_calls
        definition_state_calls += 1
        return None if definition_state_calls == 1 else (_DEFINITION_ID, False)

    async def _insert(
        _session: object,
        statement: object,
        parameters: Mapping[str, object],
    ) -> Mapping[str, object]:
        assert statement is job_executor_service._INSERT_DEFINITION
        inserted.update(parameters)
        return {"id": _DEFINITION_ID}

    async def _unexpected(*_args: object, **_kwargs: object) -> Any:
        raise AssertionError("a paused newly registered version was loaded for execution")

    monkeypatch.setattr(job_executor_service, "apply_statement_timeout", _noop_timeout)
    monkeypatch.setattr(job_executor_service, "read_lane_pause_state", _lane_pause)
    monkeypatch.setattr(job_executor_service, "_definition_state", _definition_state)
    monkeypatch.setattr(job_executor_service, "fetch_row", _insert)
    monkeypatch.setattr(job_executor_service, "load_job_definition", _unexpected)

    definition = await job_executor_service._load_or_register_definition(session, spec)  # type: ignore[arg-type]

    assert definition is None
    assert inserted["enabled"] is False
    assert session.commits == 1
    assert session.rollbacks == 1


async def test_handler_refuses_when_activation_is_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _heartbeat() -> bool:
        return True

    async def _unexpected_subprocess(*_args: object, **_kwargs: object) -> Any:
        raise AssertionError("an inactive lane started a subprocess")

    monkeypatch.setattr(job_executor_service.asyncio, "create_subprocess_exec", _unexpected_subprocess)
    monkeypatch.delenv(ACTIVE_LANES_VARIABLE, raising=False)
    monkeypatch.delenv(HANDOFF_ACKNOWLEDGEMENTS_VARIABLE, raising=False)
    outcome = await run_scheduled_command(
        JobInvocation(
            shard_key="2026-08-28T18:15:00+00:00",
            kind="scheduled-command",
            payload={
                "lane_id": "fire-detections-direct-forward",
                "scheduled_for": "2026-08-28T18:15:00+00:00",
            },
            cursor=None,
            parameters={},
            attempt_number=1,
            max_attempts=5,
            progress_fraction=0,
            seconds_remaining=900,
            heartbeat=_heartbeat,
        )
    )
    assert outcome.kind == "failed"
    assert outcome.failure_class == "ownership_activation_removed"


class _FakeProcess:
    def __init__(self) -> None:
        self.returncode: int | None = None
        self.terminated = False
        self.killed = False
        self._done = asyncio.Event()

    async def wait(self) -> int:
        await self._done.wait()
        assert self.returncode is not None
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15
        self._done.set()

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9
        self._done.set()


async def test_running_subprocess_terminates_and_yields_on_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _heartbeat() -> bool:
        return True

    process = _FakeProcess()

    async def _start_process(*_args: object) -> _FakeProcess:
        return process

    lane_id = "fire-detections-direct-forward"
    environment = _activation_environment(lane_id)
    monkeypatch.setenv(ACTIVE_LANES_VARIABLE, environment[ACTIVE_LANES_VARIABLE])
    monkeypatch.setenv(
        HANDOFF_ACKNOWLEDGEMENTS_VARIABLE,
        environment[HANDOFF_ACKNOWLEDGEMENTS_VARIABLE],
    )
    monkeypatch.setattr(job_executor_service.asyncio, "create_subprocess_exec", _start_process)

    outcome = await run_scheduled_command(
        JobInvocation(
            shard_key="2026-08-28T18:15:00+00:00",
            kind="scheduled-command",
            payload={"lane_id": lane_id, "scheduled_for": "2026-08-28T18:15:00+00:00"},
            cursor={"state": "ready", "scheduled_for": "2026-08-28T18:15:00+00:00"},
            parameters={},
            attempt_number=1,
            max_attempts=5,
            progress_fraction=0.01,
            seconds_remaining=900,
            heartbeat=_heartbeat,
            shutdown_requested=lambda: True,
        )
    )

    assert outcome.kind == "yielded"
    assert process.terminated
    assert not process.killed


def _final_stage(dockerfile_text: str) -> str:
    """Return the Dockerfile text starting at its last top-level FROM line (the shipped runtime stage)."""
    lines = dockerfile_text.splitlines()
    from_line_indices = [index for index, line in enumerate(lines) if line.startswith("FROM ")]
    return "\n".join(lines[from_line_indices[-1] :])


def test_railway_service_is_continuous_and_shadow_by_default() -> None:
    service_root = Path(__file__).resolve().parents[1]
    config = json.loads((service_root / "railway.job-executor.json").read_text(encoding="utf-8"))
    assert config["build"]["dockerfilePath"] == "infra/job-executor/Dockerfile"
    deploy = config["deploy"]
    assert deploy["startCommand"] == "agri-service ops jobs-executor"
    assert deploy["restartPolicyType"] == "ON_FAILURE"
    assert "cronSchedule" not in deploy
    assert ACTIVE_LANES_VARIABLE not in json.dumps(config)
    assert HANDOFF_ACKNOWLEDGEMENTS_VARIABLE not in json.dumps(config)

    repo_root = service_root.parents[1]
    dockerfile = (repo_root / "infra" / "job-executor" / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY package.json package-lock.json ./" in dockerfile
    assert "services/agri-data-service/pyproject.toml" in dockerfile
    assert "scripts/warm-soilgrids.mjs" in dockerfile

    # The locked quality-receipt gate stage copies alembic/db as digest inputs; the shipped
    # runtime stage must still carry none of it. Scope the "no alembic" assertion to the final
    # stage only, and pin that the gate stage keeps copying the digest inputs it needs.
    final_stage = _final_stage(dockerfile)
    assert "alembic" not in final_stage.lower()
    copy_lines = [line for line in final_stage.splitlines() if line.strip().startswith("COPY")]
    assert not any("alembic" in line.lower() or "db/" in line.lower() for line in copy_lines)
    assert "services/agri-data-service/alembic/" in dockerfile


def test_tracked_railway_configs_cannot_resurrect_cron_scheduling() -> None:
    service_root = Path(__file__).resolve().parents[1]
    repo_root = service_root.parents[1]
    tracked = subprocess.run(
        ["git", "ls-files", "*railway*.json"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert tracked
    for relative in tracked:
        path = repo_root / relative
        if not path.exists():
            continue
        config = json.loads(path.read_text(encoding="utf-8"))
        assert "cronSchedule" not in config.get("deploy", {}), relative

    obsolete = (
        "infra/cron-ingest/railway.json",
        "infra/cron-ingest/Dockerfile",
        "infra/cron-mtbs/railway.json",
        "infra/cron-soilgrids/railway.json",
        "infra/cron-soilgrids/Dockerfile",
        "infra/parquet-drain/railway.json",
        "services/agri-data-service/railway.fire-detections-forward.json",
        "services/agri-data-service/railway.water-gauges-forward.json",
    )
    assert all(not (repo_root / relative).exists() for relative in obsolete)


# --- Failed checkpoints: released by the clock, held by the breaker, or superseded by an operator ---


async def _plan_from_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
    lane_id: str,
    latest: job_executor_service.LatestRun,
    *,
    now: datetime = datetime(2026, 8, 28, 18, 30, tzinfo=UTC),
) -> tuple[list[job_executor_service.LaneTickResult], list[DueLane]]:
    spec = LANE_SPECS[lane_id]
    definition = _definition(spec.definition_name)

    async def _load(_session: object, _spec: object) -> JobDefinitionRecord:
        return definition

    async def _latest(_session: object, _spec: object) -> job_executor_service.LatestRun:
        return latest

    monkeypatch.setattr(job_executor_service, "LANE_SPECS", MappingProxyType({lane_id: spec}))
    monkeypatch.setattr(job_executor_service, "_load_or_register_definition", _load)
    monkeypatch.setattr(job_executor_service, "read_lane_checkpoint", _latest)
    return await job_executor_service._plan_active_lanes(
        _ShadowSession(),  # type: ignore[arg-type]
        ActivationConfig(frozenset({lane_id})),
        now,
    )


def _settled(
    status: str,
    bucket: datetime,
    *,
    superseded: bool = False,
    streak: int = 1,
) -> job_executor_service.LatestRun:
    return job_executor_service.LatestRun(
        run_id=uuid.uuid4(),
        scheduled_for=bucket,
        status=status,
        work_claimable=False,
        superseded_by_operator=superseded,
        consecutive_failures=streak,
    )


def _due_rows(
    due: list[DueLane],
) -> list[tuple[datetime, uuid.UUID | None, datetime | None, uuid.UUID | None, str | None]]:
    return [
        (
            candidate.scheduled_for,
            candidate.existing_run_id,
            candidate.last_scheduled_for,
            candidate.superseded_run_id,
            candidate.supersession,
        )
        for candidate in due
    ]


_OLDER_BUCKET = datetime(2026, 8, 28, 12, tzinfo=UTC)
_CURRENT_BUCKET = datetime(2026, 8, 28, 18, tzinfo=UTC)
_BUCKET_AFTER_OLDER = datetime(2026, 8, 28, 13, tzinfo=UTC)
_BUCKET_AFTER_CURRENT = datetime(2026, 8, 28, 19, tzinfo=UTC)
_COALESCE = "postgres-fire-perimeters"
_REPLAY = "parquet-drought"


@pytest.mark.parametrize("run_status", ["failed", "partial"])
async def test_the_clock_releases_a_coalesce_lane_at_the_current_bucket_after_an_older_failed_run(
    monkeypatch: pytest.MonkeyPatch,
    run_status: str,
) -> None:
    latest = _settled(run_status, _OLDER_BUCKET)

    results, due = await _plan_from_checkpoint(monkeypatch, _COALESCE, latest)

    assert results == []
    assert _due_rows(due) == [(_CURRENT_BUCKET, None, _OLDER_BUCKET, latest.run_id, "clock")]


async def test_a_failed_current_bucket_holds_until_the_next_bucket_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    latest = _settled("failed", _CURRENT_BUCKET)

    results, due = await _plan_from_checkpoint(monkeypatch, _COALESCE, latest)

    assert due == []
    assert [result.state for result in results] == ["failed"]
    assert results[0].run_id == latest.run_id
    assert results[0].handoff_blockers == ()
    assert results[0].detail is not None
    assert "logical run is spent" in results[0].detail
    assert f"bucket {_BUCKET_AFTER_CURRENT.isoformat()} opens by itself" in results[0].detail


async def test_three_consecutive_failures_trip_the_breaker_on_a_coalesce_lane(monkeypatch: pytest.MonkeyPatch) -> None:
    latest = _settled("failed", _OLDER_BUCKET, streak=3)

    results, due = await _plan_from_checkpoint(monkeypatch, _COALESCE, latest)

    assert due == []
    assert [result.state for result in results] == ["failed"]
    assert results[0].handoff_blockers == (
        "operator supersession required: agri-service ops jobs-supersede-run "
        f"--lane {_COALESCE} --run-id {latest.run_id}",
    )
    assert results[0].detail is not None
    assert "3 consecutive bucket(s) settled without success" in results[0].detail


async def test_a_replay_lane_is_held_by_its_first_failure_until_an_operator_records_a_supersession(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    latest = _settled("failed", _OLDER_BUCKET)

    results, due = await _plan_from_checkpoint(monkeypatch, _REPLAY, latest)

    assert due == []
    assert [result.state for result in results] == ["failed"]
    assert results[0].handoff_blockers == (
        "operator supersession required: agri-service ops jobs-supersede-run "
        f"--lane {_REPLAY} --run-id {latest.run_id}",
    )


async def test_a_replay_lane_resumes_at_the_current_bucket_after_an_operator_superseded_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    latest = _settled("failed", _OLDER_BUCKET, superseded=True)

    results, due = await _plan_from_checkpoint(monkeypatch, _REPLAY, latest)

    # Not the six buckets the hold cost: one bucket, then ordinary fairness against its siblings.
    assert results == []
    assert _due_rows(due) == [(_CURRENT_BUCKET, None, _OLDER_BUCKET, latest.run_id, "operator")]


async def test_an_operator_supersession_never_reopens_the_failed_bucket_itself(monkeypatch: pytest.MonkeyPatch) -> None:
    latest = _settled("failed", _CURRENT_BUCKET, superseded=True)

    results, due = await _plan_from_checkpoint(monkeypatch, _REPLAY, latest)

    assert due == []
    assert [result.state for result in results] == ["failed"]
    assert results[0].handoff_blockers == ()
    assert results[0].detail is not None
    assert f"bucket {_BUCKET_AFTER_CURRENT.isoformat()} opens by itself" in results[0].detail


async def test_a_replay_lane_failed_in_the_current_bucket_names_the_supersession_it_will_need(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    latest = _settled("failed", _CURRENT_BUCKET)

    results, _due = await _plan_from_checkpoint(monkeypatch, _REPLAY, latest)

    assert len(results[0].handoff_blockers) == 1
    assert results[0].detail is not None
    assert "opens only after a recorded supersession" in results[0].detail


def test_judge_failed_checkpoint_follows_the_declared_catch_up_policy_and_the_breaker() -> None:
    now = datetime(2026, 8, 28, 18, 30, tzinfo=UTC)
    coalesce = LANE_SPECS[_COALESCE]
    replay = LANE_SPECS[_REPLAY]
    assert coalesce.catch_up_policy == "coalesce_latest"
    assert replay.catch_up_policy == "replay_oldest"
    judge = job_executor_service.judge_failed_checkpoint

    by_clock = judge(coalesce, _settled("failed", _OLDER_BUCKET, streak=2), now)
    assert by_clock == job_executor_service.CheckpointVerdict(
        next_bucket=_CURRENT_BUCKET, newer_bucket_exists=True, release="clock", released=True, consecutive_failures=2
    )
    tripped = judge(coalesce, _settled("failed", _OLDER_BUCKET, streak=3), now)
    assert (tripped.release, tripped.released) == ("operator", False)
    assert judge(coalesce, _settled("failed", _OLDER_BUCKET, streak=3, superseded=True), now).released is True

    held = judge(replay, _settled("failed", _OLDER_BUCKET), now)
    assert held == job_executor_service.CheckpointVerdict(
        next_bucket=_CURRENT_BUCKET,
        newer_bucket_exists=True,
        release="operator",
        released=False,
        consecutive_failures=1,
    )
    assert judge(replay, _settled("failed", _OLDER_BUCKET, superseded=True), now).released is True

    # A streak the query did not report still counts the failed checkpoint itself.
    assert judge(coalesce, _settled("failed", _OLDER_BUCKET, streak=0), now).consecutive_failures == 1

    # The failed bucket itself is never reopened, whatever releases it; the next one is named instead.
    current = judge(coalesce, _settled("failed", _CURRENT_BUCKET), now)
    assert (current.newer_bucket_exists, current.released, current.next_bucket) == (False, False, _BUCKET_AFTER_CURRENT)
    assert judge(replay, _settled("failed", _CURRENT_BUCKET, superseded=True), now).released is False


def test_the_streak_probe_covers_every_breaker_limit_so_the_breaker_can_trip() -> None:
    # The query caps consecutive_failures at the probe limit; a breaker above it could never fire.
    limits = job_executor_service.CLOCK_RELEASE_STREAK_LIMIT
    assert max(limits.values()) <= job_executor_service.FAILURE_STREAK_PROBE_LIMIT
    assert set(limits) == {"coalesce_latest", "replay_oldest"}


def test_bucket_after_is_one_cadence_later_and_refuses_a_lane_without_cadence() -> None:
    assert job_executor_service.bucket_after(LANE_SPECS[_REPLAY], _OLDER_BUCKET) == _BUCKET_AFTER_OLDER
    with pytest.raises(ExecutorConfigurationError):
        job_executor_service.bucket_after(LANE_SPECS["soil-moisture-parquet-backfill"], _OLDER_BUCKET)


def test_latest_run_query_reads_operator_supersession_and_the_failure_streak() -> None:
    query = str(job_executor_service._SELECT_LATEST_RUN)
    assert "FROM agri.job_incident AS incident" in query
    assert "CAST(:supersession_fingerprint_prefix AS text) || CAST(run.id AS text)" in query
    assert "incident.status" not in query
    assert "AS superseded_by_operator" in query
    assert "LIMIT CAST(:failure_streak_limit AS integer)" in query
    assert "AS consecutive_failures" in query
    # Both are short-circuited for every checkpoint that did not settle without success.
    assert query.count("run.status IN ('failed', 'partial')") == 2


async def test_read_lane_checkpoint_binds_the_probe_limits_and_reads_both_columns() -> None:
    spec = LANE_SPECS[_REPLAY]
    captured: dict[str, object] = {}
    row = {
        "id": uuid.uuid4(),
        "job_definition_id": _DEFINITION_ID,
        "definition_version": job_executor_service.EXECUTOR_DEFINITION_VERSION,
        "definition_enabled": True,
        "scheduled_for": _OLDER_BUCKET,
        "status": "failed",
        "has_work_items": True,
        "work_claimable": False,
        "terminal_items_need_rollup": False,
        "superseded_by_operator": True,
        "consecutive_failures": 2,
    }

    class _Result:
        def mappings(self) -> _Result:
            return self

        def first(self) -> dict[str, object]:
            return row

    class _Session:
        async def execute(self, _statement: object, parameters: dict[str, object]) -> _Result:
            captured.update(parameters)
            return _Result()

    latest = await job_executor_service.read_lane_checkpoint(_Session(), spec)  # type: ignore[arg-type]

    assert latest is not None
    assert latest.superseded_by_operator is True
    assert latest.consecutive_failures == 2
    assert captured == {
        "name": spec.definition_name,
        "current_version": job_executor_service.EXECUTOR_DEFINITION_VERSION,
        "supersession_fingerprint_prefix": job_executor_service.RUN_SUPERSESSION_FINGERPRINT_PREFIX,
        "failure_streak_limit": job_executor_service.FAILURE_STREAK_PROBE_LIMIT,
    }

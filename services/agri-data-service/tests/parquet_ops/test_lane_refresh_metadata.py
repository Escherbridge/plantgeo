"""Registered timing survives both proven and withheld coverage without claiming writer success."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from agri_data_service.execution.lane_ids import CLIMATE_DIRECT_LANE_ID, SOIL_DIRECT_LANE_ID
from agri_data_service.execution.lane_specs import LANE_SPECS
from agri_data_service.parquet_ops.availability_coverage import withheld_lane_coverage
from agri_data_service.parquet_ops.coverage import build_lane_coverage, census_lane_from_registration
from agri_data_service.pipeline.direct.climate.products import CLIMATE_FIELD_PRODUCTS
from agri_data_service.pipeline.direct.soil.products import SOIL_FIELD_PRODUCTS
from agri_data_service.pipeline.parquet.lane_registry import LANE_REGISTRY
from tests.contract.wire_contract import WireCoverageLane
from tests.parquet_ops.fakes import FakeListing


@pytest.mark.parametrize(
    ("stream", "writer"),
    [(product.stream, CLIMATE_DIRECT_LANE_ID) for product in CLIMATE_FIELD_PRODUCTS]
    + [(product.stream, SOIL_DIRECT_LANE_ID) for product in SOIL_FIELD_PRODUCTS],
)
def test_registered_policy_matches_writer_schedule_even_when_availability_is_withheld(stream: str, writer: str) -> None:
    registration = LANE_REGISTRY[stream]
    lane = census_lane_from_registration(registration)
    now = datetime(2026, 9, 20, tzinfo=UTC)
    rows = withheld_lane_coverage(lane=lane, reason="availability_unpublished", now=now)
    for row in rows:
        parsed = WireCoverageLane.model_validate(row.to_wire())
        assert parsed.freshness is not None
        assert parsed.freshness.publication_lag_days == registration.publication_lag_days
        assert parsed.freshness.source_cadence_days == registration.cadence_days
        assert parsed.freshness.refresh_interval_seconds == LANE_SPECS[writer].cadence_seconds
        assert (
            parsed.freshness.expected_horizon_day
            == (now.date() - timedelta(days=registration.publication_lag_days)).isoformat()
        )
        assert parsed.latest_recorded_day is None
        assert parsed.published_ranges == []
        assert parsed.withheld_reason == "availability_unpublished"


def test_census_metadata_uses_registered_clock_without_claiming_a_successful_refresh() -> None:
    lane = census_lane_from_registration(LANE_REGISTRY[CLIMATE_FIELD_PRODUCTS[0].stream])
    row = build_lane_coverage(FakeListing(), lane=lane, tier=13, today=date(2026, 9, 20))
    parsed = WireCoverageLane.model_validate(row.to_wire())
    assert parsed.freshness is not None
    assert parsed.freshness.refresh_interval_seconds == LANE_SPECS[CLIMATE_DIRECT_LANE_ID].cadence_seconds
    assert parsed.latest_recorded_day is None
    assert "behind_provider" not in row.to_wire()


@pytest.mark.parametrize("stream", ["soil-survey", "burn-severity", "vegetation"])
def test_unproven_source_cadences_do_not_claim_daily_publications(stream: str) -> None:
    policy = census_lane_from_registration(LANE_REGISTRY[stream]).refresh_policy
    assert policy is not None
    assert policy.source_cadence_days is None
    assert policy.refresh_interval_seconds is None

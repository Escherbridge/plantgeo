"""Building the base-rung Arrow table for one fetched MTBS release day.

NEEDS DuckDB's `spatial` extension loadable in the test environment --
`burn_severity_release_day_table` calls through `support.burn_severity_geometry_session` on every
row it builds.
"""

# ruff: noqa: PLR2004 - the small literal counts ARE the assertion; naming each one hides it.

from __future__ import annotations

from datetime import UTC, date, datetime

from agri_data_service.ingest.mtbs import MtbsBurnSeverityRecord, MtbsSeverityThresholds
from agri_data_service.pipeline.direct.burn_severity.rows import (
    DIRECT_FEATURE_ID_PREFIX,
    burn_severity_release_day_table,
    direct_feature_id,
)
from agri_data_service.warehouse.schemas.burn_severity import BURN_SEVERITY_SCHEMA

VALID_SQUARE = {
    "type": "Polygon",
    "coordinates": [[[-120.0, 45.0], [-119.0, 45.0], [-119.0, 46.0], [-120.0, 46.0], [-120.0, 45.0]]],
}
RELEASE_DAY = date(2020, 11, 24)
DATA_AVAILABLE_AT = datetime(2020, 11, 24, tzinfo=UTC)


def _record(*, fire_id: str = "2018_TEST_FIRE", **overrides: object) -> MtbsBurnSeverityRecord:
    base: dict[str, object] = {
        "natural_key": f"mtbs:{fire_id}",
        "producer": "mtbs",
        "producer_local_id": fire_id,
        "geometry": VALID_SQUARE,
        "release_identifier": "mtbs-2018-release-2020-11-24",
        "mapping_revision": f"mtbs-2018-release-2020-11-24|{fire_id}||pre1|post1|perim1",
        "data_available_at": DATA_AVAILABLE_AT,
        "ignition_date": date(2018, 8, 1),
        "ignition_year": 2018,
        "fire_name": "Test Fire",
        "fire_type": "Wildfire",
        "assessment_type": "Initial",
        "acres": 1234.5,
        "severity_class": None,
        "severity_thresholds": MtbsSeverityThresholds(
            dnbr_offset=10,
            dnbr_standard_deviation=20,
            nodata_threshold=None,
            greenness_threshold=None,
            low_threshold=100,
            moderate_threshold=200,
            high_threshold=300,
        ),
    }
    base.update(overrides)
    return MtbsBurnSeverityRecord(**base)


def test_the_table_conforms_to_the_registered_schema_and_carries_every_fire() -> None:
    records = (_record(fire_id="FIRE_A"), _record(fire_id="FIRE_B"))

    table = burn_severity_release_day_table(records, observed_day=RELEASE_DAY)

    assert table.schema == BURN_SEVERITY_SCHEMA.arrow_schema
    assert table.num_rows == 2
    assert set(table.column("fire_id").to_pylist()) == {"FIRE_A", "FIRE_B"}


def test_a_direct_row_carries_a_direct_namespaced_feature_id_never_a_postgres_id() -> None:
    """There is no `geo.features` row behind a direct fetch; `feature_id` must say so rather than imitate one."""
    table = burn_severity_release_day_table((_record(fire_id="FIRE_A"),), observed_day=RELEASE_DAY)

    row = table.to_pylist()[0]
    assert row["feature_id"] == direct_feature_id("FIRE_A")
    assert row["feature_id"].startswith(f"{DIRECT_FEATURE_ID_PREFIX}:")


def test_natural_key_is_carried_from_the_record_never_rebuilt() -> None:
    record = _record(fire_id="FIRE_A")

    table = burn_severity_release_day_table((record,), observed_day=RELEASE_DAY)

    assert table.to_pylist()[0]["natural_key"] == record.natural_key == "mtbs:FIRE_A"


def test_observed_day_is_the_callers_release_day_not_rederived_from_the_record() -> None:
    """The caller already proved which release day this union belongs to; the table must not re-derive it."""
    other_day = date(2021, 9, 27)

    table = burn_severity_release_day_table((_record(fire_id="FIRE_A"),), observed_day=other_day)

    assert table.to_pylist()[0]["observed_day"].isoformat() == other_day.isoformat()


def test_allowed_client_exposure_is_always_false_and_never_read_off_the_record() -> None:
    table = burn_severity_release_day_table((_record(fire_id="FIRE_A"),), observed_day=RELEASE_DAY)

    assert table.to_pylist()[0]["allowed_client_exposure"] is False


def test_severity_thresholds_are_carried_through_as_individual_columns() -> None:
    table = burn_severity_release_day_table((_record(fire_id="FIRE_A"),), observed_day=RELEASE_DAY)

    row = table.to_pylist()[0]
    assert row["dnbr_offset"] == 10
    assert row["dnbr_standard_deviation"] == 20
    assert row["nodata_threshold"] is None
    assert row["low_threshold"] == 100
    assert row["moderate_threshold"] == 200
    assert row["high_threshold"] == 300


def test_severity_class_carries_through_as_null_when_mtbs_publishes_none() -> None:
    """Null-on-every-row today is a documented fact of the source, not a defect (docs/lanes/burn-severity.md #5)."""
    table = burn_severity_release_day_table((_record(fire_id="FIRE_A"),), observed_day=RELEASE_DAY)

    assert table.to_pylist()[0]["severity_class"] is None


def test_geom_is_non_empty_repaired_wkb() -> None:
    table = burn_severity_release_day_table((_record(fire_id="FIRE_A"),), observed_day=RELEASE_DAY)

    geom = table.to_pylist()[0]["geom"]
    assert isinstance(geom, bytes)
    assert len(geom) > 0

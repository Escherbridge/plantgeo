"""The config-lane row builder and the legacy one are two copies of one contract; this pins them together.

`pipeline/lanes/burn_severity/release_rows.py` restates `pipeline/direct/burn_severity/{rows,support}.py`
because no `pipeline/lanes/**` module may import `pipeline/direct/**`. Until the 7S cleanup moves the legacy
pair here and leaves shims, nothing else stops the two drifting. NEEDS DuckDB's `spatial` extension for the
table comparison, as the legacy rows tests do.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Final

from agri_data_service.ingest.mtbs import MtbsBurnSeverityRecord, MtbsSeverityThresholds
from agri_data_service.pipeline.direct.burn_severity import rows as legacy_rows
from agri_data_service.pipeline.direct.burn_severity import support as legacy_support
from agri_data_service.pipeline.lanes.burn_severity import release_rows

PERMANENT_FIX: Final = (
    "pipeline/lanes/burn_severity/release_rows.py has drifted from pipeline/direct/burn_severity/{rows,support}.py; "
    "the permanent fix is the 7S cleanup move (the legacy pair moves here and becomes a shim), not a re-sync"
)
#: A bow-tie ring: self-intersecting, so the repair chain's MakeValid branch actually runs.
BOW_TIE: Final = {
    "type": "Polygon",
    "coordinates": [[[-120.0, 45.0], [-119.0, 46.0], [-119.0, 45.0], [-120.0, 46.0], [-120.0, 45.0]]],
}
SQUARE: Final = {
    "type": "Polygon",
    "coordinates": [[[-118.0, 45.0], [-117.0, 45.0], [-117.0, 46.0], [-118.0, 46.0], [-118.0, 45.0]]],
}


def _record(fire_id: str, geometry: dict[str, object]) -> MtbsBurnSeverityRecord:
    return MtbsBurnSeverityRecord(
        natural_key=f"mtbs:{fire_id}",
        producer="mtbs",
        producer_local_id=fire_id,
        geometry=geometry,
        release_identifier="mtbs-2018-release-2020-11-24",
        mapping_revision=f"mtbs-2018-release-2020-11-24|{fire_id}",
        data_available_at=datetime(2020, 11, 24, tzinfo=UTC),
        ignition_date=date(2018, 8, 1),
        ignition_year=2018,
        fire_name="Parity Fire",
        fire_type="Wildfire",
        assessment_type="Initial",
        acres=1234.5,
        severity_class=None,
        severity_thresholds=MtbsSeverityThresholds(
            dnbr_offset=-1,
            dnbr_standard_deviation=16,
            nodata_threshold=-970,
            greenness_threshold=-150,
            low_threshold=70,
            moderate_threshold=9999,
            high_threshold=9999,
        ),
    )


def test_the_repair_chain_and_its_duckdb_caps_are_the_legacy_ones() -> None:
    """The SQL and the three resource caps are equal string for string."""
    assert release_rows._REPAIR_SQL == legacy_support._REPAIR_SQL, PERMANENT_FIX
    assert release_rows._REPAIR_MEMORY_LIMIT == legacy_support._REPAIR_MEMORY_LIMIT, PERMANENT_FIX
    assert release_rows._REPAIR_THREAD_COUNT == legacy_support._REPAIR_THREAD_COUNT, PERMANENT_FIX
    assert release_rows._REPAIR_TEMP_DIRECTORY_SIZE == legacy_support._REPAIR_TEMP_DIRECTORY_SIZE, PERMANENT_FIX
    assert release_rows.DIRECT_FEATURE_ID_PREFIX == legacy_rows.DIRECT_FEATURE_ID_PREFIX, PERMANENT_FIX


def test_both_builders_produce_the_same_table_for_the_same_release_day() -> None:
    """A repaired bow-tie and a valid square: every column, geometry bytes included, is equal."""
    records = (_record("FIRE_A", BOW_TIE), _record("FIRE_B", SQUARE))
    day = date(2020, 11, 24)

    config = release_rows.release_day_table(records, observed_day=day)
    legacy = legacy_rows.burn_severity_release_day_table(records, observed_day=day)

    assert config.equals(legacy), PERMANENT_FIX

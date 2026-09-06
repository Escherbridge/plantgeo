"""The lane's identity and, above all, its Oregon-only coverage gate.

The gate is the reason this file exists. `products.COVERED_STATE` is a RESTATEMENT of
`planes/evacuation_zones.py:58`'s `_COVERED_STATE`, because a `pipeline` module may not import
`planes`. Two spellings of one fact drift silently unless something compares them, and the drift that
matters here is a writer that quietly publishes for a jurisdiction the serving layer still reports as
having no coverage at all.
"""

from __future__ import annotations

import pytest

from agri_data_service.ingest import evacuation_zones as ingest_module
from agri_data_service.pipeline.direct.evacuation_zones.products import (
    COVERED_STATE,
    DIRECT_PRODUCER,
    DIRECT_QUERY_URL,
    DIRECT_SOURCE_LITERAL,
    EVACUATION_ZONES_DIRECT_KIND,
    MAX_ROWS_PER_PART,
    EvacuationZonesCoverageError,
    bbox_unconfigured_reason,
    evacuation_zones_lane_registration,
    refuse_uncovered_state,
    resolve_coverage_bbox,
)
from agri_data_service.planes.evacuation_zones import classify_evacuation_zones_coverage


def test_the_writers_covered_state_is_the_same_state_the_serving_plane_calls_covered() -> None:
    """DO NOT DELETE. This is the whole jurisdictional gate, expressed as an equality."""
    assert classify_evacuation_zones_coverage(COVERED_STATE) == "covered"


@pytest.mark.parametrize("state", ["washington", "idaho", "montana", "california", ""])
def test_every_state_the_plane_reports_uncovered_is_refused_by_the_writer(state: str) -> None:
    """A writer that published for these would contradict `no_coverage` on a life-safety layer."""
    assert classify_evacuation_zones_coverage(state) == "no_coverage"
    with pytest.raises(EvacuationZonesCoverageError):
        refuse_uncovered_state(state)


@pytest.mark.parametrize("spelling", ["Oregon", "  oregon  ", "OREGON"])
def test_oregon_is_accepted_however_an_operator_spells_it(spelling: str) -> None:
    refuse_uncovered_state(spelling)


def test_an_unset_bbox_skips_rather_than_widening_the_query(monkeypatch: pytest.MonkeyPatch) -> None:
    """The gate `run_evacuation_zones_ingestion_job` applied, preserved verbatim.

    A direct writer that defaulted to an envelope of its own would query a wider population than the
    Postgres lane ever did while every consumer still believed the two-bound coverage contract.
    """
    monkeypatch.delenv("INGEST_BBOX", raising=False)
    assert resolve_coverage_bbox() is None
    assert bbox_unconfigured_reason().strip()


def test_a_configured_bbox_is_still_policy_checked(monkeypatch: pytest.MonkeyPatch) -> None:
    """Delegating to `ingest/policy.py` means the span ceilings are the SAME code, not a second copy."""
    monkeypatch.setenv("INGEST_BBOX", "-125,42,-111,49")
    assert resolve_coverage_bbox() == "-125,42,-111,49"
    with pytest.raises(Exception, match="bounded ingestion policy"):
        resolve_coverage_bbox("-179,-89,179,89")


def test_the_registration_this_writer_replaces_is_still_a_watermark_driven_static_lookup() -> None:
    """`forward.py` reads the registered floor and hands `fill_one_lane_day` a `static_lookup` lane.

    Both facts are load-bearing: `LaneRegistration.__post_init__` REFUSES a `static_lookup` with no
    watermark, which is why `forward._captured_watermark` exists at all.
    """
    lane = evacuation_zones_lane_registration()

    assert lane.nature == "static_lookup"
    assert lane.watermark is not None
    assert lane.publication_lag_days == 0
    assert lane.forecast_module is None


def test_the_identity_constants_are_the_ingest_modules_own_and_not_a_second_copy() -> None:
    """A drifted producer token would mint natural keys nothing else in the warehouse recognises."""
    assert DIRECT_PRODUCER == ingest_module.EVACUATION_ZONES_PRODUCER
    assert DIRECT_SOURCE_LITERAL == ingest_module.EVACUATION_ZONES_PROPERTY_SOURCE
    assert DIRECT_QUERY_URL == ingest_module.EVACUATION_ZONES_QUERY_URL
    assert EVACUATION_ZONES_DIRECT_KIND == "observed"


def test_the_part_row_bound_matches_the_lane_it_replaces() -> None:
    """Restated rather than imported, so this is what stops the restatement from drifting.

    SKIPS ONCE THE POSTGRES LANE IS DELETED, deliberately: the restatement exists precisely so this
    package survives that deletion, and a test that hard-imported the removed module would turn the
    successful removal into a red sweep. While both exist, the two numbers must agree.
    """
    postgres_lane = pytest.importorskip(
        "agri_data_service.pipeline.lanes.evacuation_zones",
        reason="the Postgres evacuation-zones lane has been deleted; the restated bound stands alone",
    )

    assert MAX_ROWS_PER_PART == postgres_lane.MAX_ROWS_PER_PART

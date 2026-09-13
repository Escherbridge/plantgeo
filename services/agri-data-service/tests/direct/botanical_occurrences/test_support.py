"""Cell membership, the fan-out cap, evaluation states and richness as distinct concepts."""

# ruff: noqa: PLR2004 - the small literal counts ARE the assertion; naming each one hides it.

from __future__ import annotations

from datetime import date

from agri_data_service.foundation.botanical_occurrences.event_interval import EventInterval
from agri_data_service.pipeline.direct.botanical_occurrences.normalize import NormalizedOccurrence
from agri_data_service.pipeline.direct.botanical_occurrences.support import (
    MAX_FANOUT_CELLS,
    associate_record,
    evaluate_support,
    summarise_cell_taxa,
    support_for,
)

FINE = support_for("grid-0.05")
COARSE = support_for("grid-0.25")


def _record(  # noqa: PLR0913 - a fixture builder; every field is a different axis under test
    occurrence_id: str,
    longitude: float | None = -122.31,
    latitude: float | None = 47.61,
    *,
    uncertainty: float | None = 10.0,
    spatial_class: str = "exact",
    taxon: str = "concept:lupinus",
    recorded_by: str = "A. Collector",
    event: EventInterval | None = None,
) -> NormalizedOccurrence:
    return NormalizedOccurrence(
        occurrence_id=occurrence_id,
        collection_key="test:COLL:vascular",
        release_key="release-1",
        source_record_key=occurrence_id,
        taxon_concept_id=taxon,
        taxonomy_recipe_version="source-names-v0",
        resolution_state="resolved",
        scientific_name="Lupinus argenteus",
        family="Fabaceae",
        event=event or EventInterval(date(1987, 6, 15), date(1987, 6, 15), "day"),
        longitude=longitude,
        latitude=latitude,
        coordinate_uncertainty_m=uncertainty,
        spatial_class=spatial_class,
        qc_policy_version="botanical-qc-v1",
        qc_reasons=(),
        within_envelope=True,
        geom=None,
        catalog_number=None,
        recorded_by=recorded_by,
        basis_of_record="PreservedSpecimen",
        rights_uri=None,
        attribution_text=None,
    )


def test_a_tight_exact_record_is_confirmed_in_exactly_one_cell() -> None:
    associations = associate_record(_record("a"), FINE)
    assert len(associations) == 1
    assert associations[0].membership == "confirmed"
    assert associations[0].distance_semantics == "to_record_point"


def test_a_generalized_record_is_never_confirmed_even_inside_one_cell() -> None:
    """The publisher has already said the point is not where the coordinates put it."""
    associations = associate_record(_record("b", spatial_class="generalized", uncertainty=1.0), FINE)
    assert [association.membership for association in associations] == ["possible"]


def test_an_uncertainty_crossing_cells_is_possible_in_each_one() -> None:
    associations = associate_record(_record("c", longitude=-122.2999, latitude=47.5999, uncertainty=2000.0), FINE)
    assert len(associations) > 1
    assert {association.membership for association in associations} == {"possible"}


def test_a_fan_out_past_the_cap_keeps_one_possible_cell_rather_than_spraying() -> None:
    wide = _record("d", uncertainty=100_000.0)
    associations = associate_record(wide, FINE)
    assert len(associations) == 1
    assert associations[0].membership == "possible"
    assert associations[0].distance_semantics == "to_cell_centroid"
    assert len(associate_record(wide, FINE, max_fanout=10_000)) > MAX_FANOUT_CELLS


def test_a_withheld_or_nonspatial_record_is_placed_nowhere() -> None:
    assert associate_record(_record("e", None, None, spatial_class="nonspatial"), FINE) == ()
    assert associate_record(_record("f", spatial_class="withheld"), FINE) == ()


def test_richness_counts_distinct_concepts_and_is_not_summed_from_the_finer_rung() -> None:
    records = [
        _record("a", taxon="concept:one"),
        _record("b", taxon="concept:one"),
        _record("c", taxon="concept:two", longitude=-122.36, latitude=47.66),
    ]
    fine_associations = [association for record in records for association in associate_record(record, FINE)]
    coarse_associations = [association for record in records for association in associate_record(record, COARSE)]
    fine = evaluate_support(records, fine_associations, FINE, release_set_id="set", include_evaluated_zero=False)
    coarse = evaluate_support(records, coarse_associations, COARSE, release_set_id="set", include_evaluated_zero=False)
    assert sum(cell.documented_taxa for cell in fine) == 2, "two cells, one concept each in this layout"
    documented_coarse = [cell for cell in coarse if cell.evaluation == "documented"]
    assert len(documented_coarse) == 1
    assert documented_coarse[0].documented_taxa == 2
    assert documented_coarse[0].record_count == 3


def test_a_cell_with_only_possible_records_is_not_documented() -> None:
    record = _record("g", spatial_class="generalized", uncertainty=1.0)
    associations = associate_record(record, FINE)
    (cell,) = list(evaluate_support([record], associations, FINE, release_set_id="set", include_evaluated_zero=False))
    assert cell.evaluation == "withheld_or_generalized_only"
    assert cell.record_count == 0
    assert cell.possible_only_records == 1


def test_evaluated_zero_is_written_only_inside_the_declared_envelope() -> None:
    record = _record("h")
    associations = associate_record(record, COARSE)
    evaluations = evaluate_support(
        [record], associations, COARSE, release_set_id="set", envelope=(-123.0, 47.0, -122.0, 48.0)
    )
    zero_cells = [evaluation for evaluation in evaluations if evaluation.evaluation == "evaluated_zero"]
    assert zero_cells, "an admitted-coverage cell with no record is an evaluated zero"
    assert all(evaluation.record_count == 0 for evaluation in zero_cells)
    assert not any(evaluation.evaluation == "not_evaluated" for evaluation in evaluations)
    assert not any(evaluation.evaluation == "outside_coverage" for evaluation in evaluations)


def test_the_summary_is_sparse_and_counts_events_rather_than_sheets() -> None:
    same_event = EventInterval(date(1987, 6, 15), date(1987, 6, 15), "day")
    records = [
        _record("a", event=same_event, recorded_by="A. Collector"),
        _record("b", event=same_event, recorded_by="A. Collector"),
        _record("c", event=EventInterval(date(1990, 1, 1), date(1990, 12, 31), "year"), recorded_by="B. Collector"),
    ]
    associations = [association for record in records for association in associate_record(record, FINE)]
    summaries = summarise_cell_taxa(records, associations, FINE, release_set_id="set")
    assert len(summaries) == 1, "one cell, one concept, so exactly one sparse row"
    summary = summaries[0]
    assert summary.record_count == 3
    assert summary.event_estimate == 2, "two sheets from one collecting event are one event"
    assert summary.earliest_event == date(1987, 6, 15)
    assert summary.latest_event == date(1990, 12, 31)

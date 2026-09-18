"""The admitted envelope is measured from admitted records, not declared by a constant."""

# ruff: noqa: PLR2004 - the coordinates and cell counts ARE the assertion; naming each one hides it.

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from agri_data_service.foundation.botanical_occurrences.coordinates import (
    ENVELOPE_PAD_DEGREES,
    botanical_seed_envelope,
    derive_envelope,
)
from agri_data_service.foundation.botanical_occurrences.event_interval import EventInterval
from agri_data_service.foundation.region import load_region
from agri_data_service.pipeline.direct.botanical_occurrences.forward import (
    ArchiveRequest,
    BotanicalForwardConfig,
    ReadRelease,
    build_generation_contents,
    generation_envelope,
    run_botanical_occurrences_forward,
)
from agri_data_service.pipeline.direct.botanical_occurrences.normalize import NormalizedOccurrence
from agri_data_service.pipeline.direct.botanical_occurrences.publish import LocalPublicationTarget

if TYPE_CHECKING:
    from pathlib import Path

#: The one `exact` record in the shared archive fixture; the other three are generalized, withheld
#: and nonspatial, so this coordinate alone is what an honest envelope may be measured from.
FIXTURE_EXACT_POINT = (-122.3, 47.6)


def _record(
    occurrence_id: str,
    longitude: float | None,
    latitude: float | None,
    *,
    spatial_class: str = "exact",
) -> NormalizedOccurrence:
    return NormalizedOccurrence(
        occurrence_id=occurrence_id,
        collection_key="test:COLL:vascular",
        release_key="release-1",
        source_record_key=occurrence_id,
        taxon_concept_id="concept:lupinus",
        taxonomy_recipe_version="source-names-v0",
        resolution_state="resolved",
        scientific_name="Lupinus argenteus",
        family="Fabaceae",
        event=EventInterval(date(1987, 6, 15), date(1987, 6, 15), "day"),
        longitude=longitude,
        latitude=latitude,
        coordinate_uncertainty_m=10.0,
        spatial_class=spatial_class,
        qc_policy_version="botanical-qc-v1",
        qc_reasons=(),
        within_envelope=True,
        geom=None,
        catalog_number=None,
        recorded_by="A. Collector",
        basis_of_record="PreservedSpecimen",
        rights_uri=None,
        attribution_text=None,
    )


def _release(records: tuple[NormalizedOccurrence, ...]) -> ReadRelease:
    return ReadRelease(
        release_key="release-1",
        release_row={},
        raw_rows=(),
        identification_rows=(),
        records=records,
        outcome="complete",
        reasons=(),
    )


def test_botanical_seed_envelope_is_the_region_manifest_botanical_seed_sub_envelope() -> None:
    """The seed envelope is read from the manifest per call, never snapshot at import."""
    envelope = load_region().sub_envelopes["botanical_seed"]
    assert (envelope.west, envelope.south, envelope.east, envelope.north) == botanical_seed_envelope()
    assert botanical_seed_envelope() == (-125.0, 41.0, -110.0, 50.0)


def test_the_envelope_tracks_the_actual_extent_of_the_records() -> None:
    envelope = derive_envelope([(-122.4, 47.5), (-122.0, 47.9), (-122.2, 47.7)])
    assert envelope == (
        -122.4 - ENVELOPE_PAD_DEGREES,
        47.5 - ENVELOPE_PAD_DEGREES,
        -122.0 + ENVELOPE_PAD_DEGREES,
        47.9 + ENVELOPE_PAD_DEGREES,
    )


def test_an_empty_release_falls_back_to_the_seed_rather_than_crashing() -> None:
    assert derive_envelope([]) == botanical_seed_envelope()
    assert derive_envelope(()) == botanical_seed_envelope()
    assert generation_envelope(()) == botanical_seed_envelope()
    assert generation_envelope((_record("a", None, None, spatial_class="nonspatial"),)) == botanical_seed_envelope()


def test_only_exact_records_are_measured_from() -> None:
    """A generalized point is not where the publisher says it is, so it cannot set a coverage edge."""
    records = (_record("a", -122.3, 47.6), _record("b", -110.0, 41.0, spatial_class="generalized"))
    assert generation_envelope(records) == derive_envelope([(-122.3, 47.6)])


def test_the_envelope_is_clamped_to_the_sphere() -> None:
    assert derive_envelope([(-179.95, -89.95), (179.95, 89.95)]) == (-180.0, -90.0, 180.0, 90.0)


def test_evaluated_zero_stays_near_the_records_rather_than_filling_a_placeholder_box() -> None:
    """The bug: a hand-picked envelope painted grey `evaluated_zero` cells over unsampled land."""
    records = (_record("a", -122.31, 47.61), _record("b", -122.36, 47.66))
    contents = build_generation_contents((_release(records),), release_set_id="set", supports=("grid-0.25",))
    zero_cells = [cell for cell in contents.support_cells["grid-0.25"] if cell["evaluation"] == "evaluated_zero"]
    # Two clustered points inside one 0.25-degree cell, padded by one cell on each side: a 3x3
    # neighbourhood at most, of which the occupied one is not an evaluated zero.
    assert len(zero_cells) <= 8
    assert not any(cell["cell_id"].startswith("grid-0.25:-44") for cell in zero_cells), (
        "no cell out at -110 degrees, which the old placeholder box reached"
    )


def test_a_record_outside_the_measured_envelope_is_still_published() -> None:
    far = _record("far", -100.0, 30.0, spatial_class="generalized")
    contents = build_generation_contents(
        (_release((_record("near", -122.3, 47.6), far)),), release_set_id="set", supports=("grid-0.25",)
    )
    published = {row["occurrence_id"]: row for row in contents.occurrences}
    assert published["far"]["within_envelope"] is False, "flagged, never dropped"
    assert published["near"]["within_envelope"] is True


def test_a_turn_reports_the_envelope_it_measured(valid_archive: Path, tmp_path: Path) -> None:
    report = run_botanical_occurrences_forward(
        BotanicalForwardConfig(
            archives=(ArchiveRequest(path=valid_archive, collection_key="test:COLL:vascular", source_version="1.0"),),
            root=str(tmp_path / "publication"),
            supports=("grid-0.25",),
            target=LocalPublicationTarget(tmp_path / "publication"),
        )
    )
    assert report["outcome"] == "published"
    assert report["envelope"] == list(derive_envelope([FIXTURE_EXACT_POINT]))

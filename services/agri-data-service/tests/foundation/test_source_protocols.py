"""Federation step 3: the three regional-source layers conform to their protocols and bind servably.

`federation.md` §2 and `layer-lanes.md` §1b. Three claims are proved here, and each one is the sort
that a comment alone would let rot: that every source implementation actually satisfies its layer's
`runtime_checkable` protocol, that the boot check accepts the pilot and rejects a region the pilot's
sources cannot serve, and that the deprecation shims still export exactly what they used to.
"""

from __future__ import annotations

import pytest

from agri_data_service.foundation.region import (
    Region,
    RegionBindingNotServableError,
    assert_region_bindings_are_servable,
    load_region,
    unverified_binding_slugs,
)
from agri_data_service.foundation.region.source_coverage import GLOBAL_SOURCE_COVERAGE, SourceCoverageClaim
from agri_data_service.pipeline.direct.burn_severity.mtbs import MTBS_BURN_SEVERITY_SOURCE
from agri_data_service.pipeline.direct.burn_severity.source_protocol import BurnSeveritySource
from agri_data_service.pipeline.direct.drought.source_protocol import DroughtSource
from agri_data_service.pipeline.direct.drought.usdm import USDM_DROUGHT_SOURCE
from agri_data_service.pipeline.direct.soil_survey.source_protocol import SoilSurveySource
from agri_data_service.pipeline.direct.soil_survey.ssurgo import SSURGO_SOIL_SURVEY_SOURCE
from agri_data_service.pipeline.source_bindings import declared_source_coverage_claims

#: Every implemented source, with the protocol it claims to satisfy and its manifest slug.
SOURCE_IMPLEMENTATIONS = (
    ("burn-severity", "mtbs", MTBS_BURN_SEVERITY_SOURCE, BurnSeveritySource),
    ("drought", "usdm", USDM_DROUGHT_SOURCE, DroughtSource),
    ("soil-survey", "ssurgo", SSURGO_SOIL_SURVEY_SOURCE, SoilSurveySource),
)


@pytest.mark.parametrize(("layer_slug", "source_slug", "source", "protocol"), SOURCE_IMPLEMENTATIONS)
def test_each_source_satisfies_its_layer_protocol(
    layer_slug: str,
    source_slug: str,
    source: object,
    protocol: type,
) -> None:
    """`isinstance` against the runtime-checkable protocol, plus the two attributes it cannot type."""
    assert isinstance(source, protocol), f"{layer_slug}'s {source_slug} source does not satisfy {protocol.__name__}"
    assert source.source_slug == source_slug
    assert isinstance(source.coverage, SourceCoverageClaim)


def test_each_source_is_bound_to_its_layer_by_the_pilot_manifest() -> None:
    """The manifest's binding and the implementation's own slug and coverage word have to agree."""
    bindings = {binding.layer_slug: binding for binding in load_region().enabled_layers}

    for layer_slug, source_slug, source, _protocol in SOURCE_IMPLEMENTATIONS:
        binding = bindings[layer_slug]
        assert binding.source_slug == source_slug
        assert binding.source_slug == source.source_slug
        assert binding.coverage == source.coverage.coverage


def test_the_registry_exposes_exactly_the_three_implemented_sources() -> None:
    """A registry that quietly lost an entry would make the boot check pass for the wrong reason."""
    assert set(declared_source_coverage_claims()) == {"mtbs", "usdm", "ssurgo"}


def test_the_pilot_region_is_servable_and_names_its_unverified_bindings() -> None:
    """PNW spans only `US`, which all three regional sources cover; the rest are honestly unchecked."""
    region = load_region()
    claims = declared_source_coverage_claims()

    assert_region_bindings_are_servable(region, claims)

    unverified = unverified_binding_slugs(region, claims)
    assert "mtbs" not in unverified
    assert "firms" in unverified, "a source with no protocol yet must be reported, not silently passed"


def _region_outside_the_united_states() -> Region:
    """A fabricated single-layer region in Kenya, bound to the pilot's US-only drought source.

    Built here rather than added to `foundation/region/` as a second manifest: it exists to be
    REFUSED, and a refusable manifest sitting next to `pnw.json` is an invitation to deploy it.
    """
    return Region(
        slug="fabricated-east-african-highland",
        display_name="Fabricated East African Highland",
        envelope={"west": 34.0, "south": -1.5, "east": 38.0, "north": 1.5},
        default_camera_envelope={"west": 34.0, "south": -1.5, "east": 38.0, "north": 1.5},
        crs=4326,
        lattice_pitch_degrees=0.01,
        lattice_origin_rule="floor_to_cell_origin",
        timezone="Africa/Nairobi",
        iso_country_codes=("KE",),
        admin_codes=("KE-30",),
        enabled_layers=(
            {"layer_slug": "drought", "source_slug": "usdm", "coverage": "regional"},
            {"layer_slug": "fire-detections", "source_slug": "firms", "coverage": "global"},
        ),
    )


def test_a_region_outside_the_sources_coverage_is_refused_at_boot() -> None:
    """The whole point of the check: a US-only source bound over Kenya must not reach a request."""
    region = _region_outside_the_united_states()

    with pytest.raises(RegionBindingNotServableError) as refusal:
        assert_region_bindings_are_servable(region, declared_source_coverage_claims())

    message = str(refusal.value)
    assert "usdm" in message
    assert "KE" in message


def test_a_coverage_word_that_disagrees_with_the_source_is_refused() -> None:
    """A manifest calling a regional source `global` is a mis-binding even where the codes would pass."""
    region = load_region()
    lying_claims = dict(declared_source_coverage_claims())
    lying_claims["usdm"] = GLOBAL_SOURCE_COVERAGE

    with pytest.raises(RegionBindingNotServableError, match="declares 'global'"):
        assert_region_bindings_are_servable(region, lying_claims)


def test_a_global_source_covers_every_region() -> None:
    """Coverage is the only thing a global source has to say, and it always answers yes."""
    assert GLOBAL_SOURCE_COVERAGE.covers_region(_region_outside_the_united_states())
    assert GLOBAL_SOURCE_COVERAGE.uncovered_country_codes(load_region()) == ()


def test_a_claim_must_name_the_countries_its_coverage_word_implies() -> None:
    """Neither half of a coverage claim may be left to the reader to infer."""
    with pytest.raises(ValueError, match="must name the ISO country codes"):
        SourceCoverageClaim(coverage="regional")
    with pytest.raises(ValueError, match="must not list ISO codes"):
        SourceCoverageClaim(coverage="global", iso_country_codes=("US",))


def test_the_drought_shim_re_exports_what_it_always_did() -> None:
    """`source.py` must stay a lossless door onto `usdm.py` until wave 4 repoints its importers."""
    from agri_data_service.pipeline.direct.drought import source, usdm

    assert source.DroughtDaySource is usdm.DroughtDaySource
    assert source.DroughtSourceError is usdm.DroughtSourceError
    assert source.fetch_drought_day is usdm.fetch_drought_day


def test_the_burn_severity_shim_re_exports_what_it_always_did() -> None:
    """`source.py` must stay a lossless door onto `mtbs.py` until wave 4 repoints its importers."""
    from agri_data_service.pipeline.direct.burn_severity import mtbs, source

    assert source.BurnSeverityDaySource is mtbs.BurnSeverityDaySource
    assert source.BurnSeverityFetchError is mtbs.BurnSeverityFetchError
    assert source.fetch_burn_severity_release_day is mtbs.fetch_burn_severity_release_day

"""Federation step 3: the three regional-source layers conform to their protocols and bind servably.

`federation.md` §2 and `layer-lanes.md` §1b. Three claims are proved here, and each one is the sort
that a comment alone would let rot: that every source implementation actually satisfies its layer's
`runtime_checkable` protocol, that the boot check accepts the pilot and rejects a region the pilot's
sources cannot serve, and that the deprecation shims still export exactly what they used to.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, cast

import pytest

from agri_data_service.foundation.region import (
    PLATFORM_LAYER_SLUGS,
    Region,
    RegionBindingNotServableError,
    assert_region_bindings_are_servable,
    load_region,
    unverified_binding_slugs,
)
from agri_data_service.foundation.region.source_coverage import GLOBAL_SOURCE_COVERAGE, SourceCoverageClaim
from agri_data_service.ingest.mtbs import MtbsBurnSeverityRecord
from agri_data_service.ingest.usdm import DroughtArea, DroughtRelease
from agri_data_service.pipeline.direct.burn_severity.mtbs import MTBS_BURN_SEVERITY_SOURCE
from agri_data_service.pipeline.direct.burn_severity.source_protocol import (
    BurnSeverityRecordPayload,
    BurnSeveritySource,
    BurnSeverityThresholdsPayload,
)
from agri_data_service.pipeline.direct.drought.source_protocol import (
    DroughtAreaPayload,
    DroughtReleasePayload,
    DroughtSource,
)
from agri_data_service.pipeline.direct.drought.usdm import USDM_DROUGHT_SOURCE
from agri_data_service.pipeline.direct.soil_survey.source_protocol import SoilSurveySource
from agri_data_service.pipeline.direct.soil_survey.ssurgo import SSURGO_SOIL_SURVEY_SOURCE
from agri_data_service.pipeline.source_bindings import (
    SourceRegistry,
    UnboundLayerError,
    declared_layer_source_contracts,
    declared_source_coverage_claims,
    resolve_drought_source,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

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


def test_a_source_bound_to_the_wrong_layer_is_refused_at_boot() -> None:
    """STYLE-REVIEW-W5 B2: `drought -> ssurgo` passes every coverage gate and implements nothing.

    SSURGO is `regional`/`US`, exactly like USDM, so the coverage word agrees and the ISO codes
    cover; before the conformance guard the only thing that noticed was `forward.py` calling
    `fetch_release_day` on a soil-survey source, on a scheduled turn, in the next region.
    """
    region = _region_bound_to_fake_source("ssurgo")

    with pytest.raises(RegionBindingNotServableError) as refusal:
        assert_region_bindings_are_servable(
            region, declared_source_coverage_claims(), declared_layer_source_contracts()
        )

    message = str(refusal.value)
    assert "drought" in message
    assert "ssurgo" in message
    assert "DroughtSource" in message


def test_the_pilots_own_bindings_pass_the_conformance_guard() -> None:
    """The other direction, and what keeps the test above from passing for a trivial reason."""
    assert_region_bindings_are_servable(
        load_region(), declared_source_coverage_claims(), declared_layer_source_contracts()
    )


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
        platform_layers=PLATFORM_LAYER_SLUGS,
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
    from agri_data_service.pipeline.direct.drought import source, usdm  # noqa: PLC0415

    assert source.DroughtDaySource is usdm.DroughtDaySource
    assert source.DroughtSourceError is usdm.DroughtSourceError
    assert source.fetch_drought_day is usdm.fetch_drought_day


def test_the_burn_severity_shim_re_exports_what_it_always_did() -> None:
    """`source.py` must stay a lossless door onto `mtbs.py` until wave 4 repoints its importers."""
    from agri_data_service.pipeline.direct.burn_severity import mtbs, source  # noqa: PLC0415

    assert source.BurnSeverityDaySource is mtbs.BurnSeverityDaySource
    assert source.BurnSeverityFetchError is mtbs.BurnSeverityFetchError
    assert source.fetch_burn_severity_release_day is mtbs.fetch_burn_severity_release_day


def _region_bound_to_fake_source(source_slug: str) -> Region:
    """A fabricated single-layer region whose `drought` binding names `source_slug`, not `usdm`."""
    return Region(
        slug="fabricated-fake-source-region",
        display_name="Fabricated Fake-Source Region",
        envelope={"west": -126.0, "south": 41.0, "east": -110.0, "north": 50.0},
        default_camera_envelope={"west": -125.0, "south": 42.0, "east": -111.0, "north": 49.0},
        crs=4326,
        lattice_pitch_degrees=0.01,
        lattice_origin_rule="floor_to_cell_origin",
        timezone="America/Los_Angeles",
        iso_country_codes=("US",),
        admin_codes=("US-WA",),
        platform_layers=PLATFORM_LAYER_SLUGS,
        enabled_layers=({"layer_slug": "drought", "source_slug": source_slug, "coverage": "regional"},),
    )


def test_resolve_drought_source_calls_whatever_the_regions_own_binding_names(monkeypatch: pytest.MonkeyPatch) -> None:
    """S5, W3 review: a lane resolves through the binding, not `usdm.py` by name.

    A fabricated source object -- not USDM, not even a real protocol implementation -- is what
    `resolve_drought_source` must return once the region's binding names it, proving the lookup is
    live at call time rather than a comment describing an import that never actually branches.
    """

    class FakeDroughtSource:
        source_slug = "fake-drought-source"
        coverage = None

    # A deliberate non-implementation: this test is about the LOOKUP, not about conformance, and
    # the registry is typed per layer now, so the fake needs a cast to sit in the drought map.
    fake_source = cast("DroughtSource", FakeDroughtSource())

    def fake_registry() -> SourceRegistry:
        # Registered UNDER `drought`: the registry is keyed by layer now, so a fake belongs to one.
        return SourceRegistry(drought={"fake-drought-source": fake_source}, burn_severity={}, soil_survey={})

    monkeypatch.setattr("agri_data_service.pipeline.source_bindings._source_registry", fake_registry)

    region = _region_bound_to_fake_source("fake-drought-source")
    assert resolve_drought_source(region=region) is fake_source


def test_resolve_drought_source_refuses_a_binding_with_no_registered_implementation() -> None:
    """A binding naming a source slug the registry does not know about must refuse, not guess."""
    region = _region_bound_to_fake_source("a-source-nobody-registered")

    with pytest.raises(UnboundLayerError, match="a-source-nobody-registered"):
        resolve_drought_source(region=region)


def test_resolve_drought_source_refuses_a_region_with_no_drought_binding_at_all() -> None:
    """A region that never enables the drought layer must refuse rather than fall back silently."""
    region = _region_bound_to_fake_source("usdm")
    no_drought_region = region.model_copy(update={"enabled_layers": ()})

    with pytest.raises(UnboundLayerError, match="drought"):
        resolve_drought_source(region=no_drought_region)


#: A tiny valid WGS84 ring. Geometry content is irrelevant to a structural conformance check -- these
#: fixtures are never repaired or written -- but a well-formed one keeps the fixture readable.
_WGS84_RING = {
    "type": "Polygon",
    "coordinates": [[[-120.0, 45.0], [-119.0, 45.0], [-119.0, 46.0], [-120.0, 46.0], [-120.0, 45.0]]],
}


def test_the_usdm_release_satisfies_the_drought_payload_protocols() -> None:
    """The concrete USDM dataclass must satisfy `DroughtReleasePayload` structurally, not by comment.

    This is the claim `adapter.py` and `rows.py` now type against: before wave 5 the protocol said
    `release: object | None` and the lane read `.areas` off it anyway, which only `mypy --strict`
    could catch. An `isinstance` on a real fixture is what keeps the two halves from drifting again.
    """
    release = DroughtRelease(
        valid_date="2026-09-15",
        source_url="https://droughtmonitor.unl.edu/data/json/usdm_20260915.json",
        areas=(DroughtArea(drought_intensity_class=0, geometry=_WGS84_RING),),
    )

    assert isinstance(release, DroughtReleasePayload)
    assert isinstance(release.areas[0], DroughtAreaPayload)
    # The calendar normalisation lives in the SOURCE, not in `rows.py` (federation.md §2).
    assert release.release_day == date(2026, 9, 15)


def test_a_fabricated_non_usdm_release_satisfies_the_same_drought_payload_protocol() -> None:
    """The whole point of a protocol: a second region's release type needs no USDM ancestry at all."""

    @dataclass(frozen=True)
    class FabricatedDroughtArea:
        drought_intensity_class: int
        geometry: Mapping[str, object]

    @dataclass(frozen=True)
    class FabricatedNationalDroughtRelease:
        release_day: date
        source_url: str
        areas: tuple[FabricatedDroughtArea, ...]

    release = FabricatedNationalDroughtRelease(
        release_day=date(2026, 9, 15),
        source_url="https://example.invalid/national-drought/20260915.json",
        areas=(FabricatedDroughtArea(drought_intensity_class=3, geometry=_WGS84_RING),),
    )

    assert isinstance(release, DroughtReleasePayload)
    assert isinstance(release.areas[0], DroughtAreaPayload)


def test_the_mtbs_record_satisfies_the_burn_severity_payload_protocols() -> None:
    """The concrete MTBS model must satisfy `BurnSeverityRecordPayload` and its nested thresholds."""
    record = MtbsBurnSeverityRecord(
        natural_key="mtbs:ID4212011950720220704",
        producer="mtbs",
        producer_local_id="ID4212011950720220704",
        geometry=_WGS84_RING,
        release_identifier="mtbs-annual:2022",
        mapping_revision="1",
        data_available_at=datetime(2026, 1, 2, tzinfo=UTC),
        ignition_date=date(2022, 7, 4),
        ignition_year=2022,
    )

    assert isinstance(record, BurnSeverityRecordPayload)
    assert isinstance(record.severity_thresholds, BurnSeverityThresholdsPayload)


def test_a_fabricated_non_mtbs_record_satisfies_the_same_burn_severity_payload_protocol() -> None:
    """A second region's burned-area record needs none of MTBS's `producer`/`geom_kind` members."""

    @dataclass(frozen=True)
    class FabricatedThresholds:
        dnbr_offset: int | None = None
        dnbr_standard_deviation: int | None = None
        nodata_threshold: int | None = None
        greenness_threshold: int | None = None
        low_threshold: int | None = None
        moderate_threshold: int | None = None
        high_threshold: int | None = None

    @dataclass(frozen=True)
    class FabricatedNationalBurnRecord:
        producer_local_id: str
        natural_key: str
        release_identifier: str
        mapping_revision: str
        ignition_year: int
        ignition_date: date
        data_available_at: datetime
        geometry: Mapping[str, object]
        fire_name: str | None = None
        fire_type: str | None = None
        assessment_type: str | None = None
        acres: float | None = None
        severity_class: str | None = None
        severity_thresholds: FabricatedThresholds = field(default_factory=FabricatedThresholds)

    record = FabricatedNationalBurnRecord(
        producer_local_id="NAT-2022-000123",
        natural_key="national-burn-programme:NAT-2022-000123",
        release_identifier="national-burn-programme:2022",
        mapping_revision="a",
        ignition_year=2022,
        ignition_date=date(2022, 7, 4),
        data_available_at=datetime(2026, 1, 2, tzinfo=UTC),
        geometry=_WGS84_RING,
    )

    assert isinstance(record, BurnSeverityRecordPayload)
    assert isinstance(record.severity_thresholds, BurnSeverityThresholdsPayload)

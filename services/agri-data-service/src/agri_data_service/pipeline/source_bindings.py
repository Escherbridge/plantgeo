"""Every source implementation that has declared a coverage claim, keyed by its manifest slug.

Lives at `pipeline/` root rather than inside `pipeline/direct/`: a module in `pipeline/direct/` is
itself a lane (`tests/test_layer_import_contract.py::test_lanes_do_not_import_each_other`), and a
registry by definition imports all of them, so it can only live one level up. See
`pipeline/AGENTS.md` section "Source bindings".

`resolve_drought_source()` / `resolve_burn_severity_source()` are what make the protocols in
`pipeline/direct/*/source_protocol.py` load-bearing (S5, W3 review): each calls `load_region()`
itself, per call, never at import time or module scope, and looks up the region's OWN binding for
the layer rather than importing `usdm.py`/`mtbs.py` module functions directly. A region whose
manifest binds `drought` to a different `source_slug` gets that implementation without any lane
code changing, as long as the implementation is registered below.

The registry is typed PER LAYER (`SourceRegistry`), so a resolver returns its layer's protocol
without a cast and the three `# type: ignore[return-value]`/`[attr-defined]` comments this module
used to carry are gone. That erasure to `object` was what let a manifest bind `drought` to `ssurgo`
and fail as an `AttributeError` inside a scheduled lane instead of at boot (STYLE-REVIEW-W5 B2);
`declared_layer_source_contracts()` below hands `assert_region_bindings_are_servable` the sources
registered under each layer, which is where that binding is now refused -- by REGISTRATION ("bound
to `drought`, registered under `burn-severity`"), with the runtime-checkable protocol kept only as
a second, weaker assertion that cannot see two protocols sharing member names (STYLE-REVIEW-W6 B1).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from agri_data_service.foundation.region.bindings import LayerSourceContracts

if TYPE_CHECKING:
    from collections.abc import Mapping

    from agri_data_service.foundation.region.manifest import Region
    from agri_data_service.foundation.region.source_coverage import SourceCoverageClaim
    from agri_data_service.pipeline.direct.burn_severity.source_protocol import BurnSeveritySource
    from agri_data_service.pipeline.direct.drought.source_protocol import DroughtSource
    from agri_data_service.pipeline.direct.soil_survey.source_protocol import SoilSurveySource

#: The manifest layer slugs this module resolves sources for. Spelled once so a resolver, the
#: protocol map and a failure message cannot drift apart.
DROUGHT_LAYER_SLUG: Final = "drought"
BURN_SEVERITY_LAYER_SLUG: Final = "burn-severity"
SOIL_SURVEY_LAYER_SLUG: Final = "soil-survey"


class UnboundLayerError(RuntimeError):
    """Raised when the resolved region has no enabled binding for the requested layer, or that
    binding names a source slug with no registered implementation."""


class DuplicateSourceSlugError(RuntimeError):
    """Raised when one `source_slug` is registered under more than one layer.

    A slug is the manifest's whole name for an implementation, so two layers claiming one slug make
    `coverage_claims()` ambiguous and every binding that names it unresolvable. Raised at
    CONSTRUCTION rather than at the first lookup, because a registry that has been built at all is
    one the boot check is about to trust (STYLE-REVIEW-W6 S6).
    """


@dataclass(frozen=True, slots=True)
class SourceRegistry:
    """Every registered source implementation, grouped by the LAYER whose protocol it satisfies.

    One map per layer rather than one flat `{slug: object}`: a flat registry cannot say which layer
    an implementation is for, so the resolvers could only assert conformance (three coded
    `type: ignore`s) and the boot check could not test it at all. The layer key is carried all the
    way into `LayerSourceContracts.sources_by_layer` -- registration under the bound layer IS the
    servability question the boot check asks (STYLE-REVIEW-W6 B1).
    """

    drought: Mapping[str, DroughtSource]
    burn_severity: Mapping[str, BurnSeveritySource]
    soil_survey: Mapping[str, SoilSurveySource]

    def __post_init__(self) -> None:
        """Refuse a registry where one slug is registered under two layers, before anything reads it."""
        seen: dict[str, str] = {}
        collisions: list[str] = []
        for layer_slug, layer_sources in self.sources_by_layer().items():
            for slug in layer_sources:
                first = seen.setdefault(slug, layer_slug)
                if first != layer_slug:
                    collisions.append(f"{slug!r} is registered under both {first!r} and {layer_slug!r}")
        if collisions:
            raise DuplicateSourceSlugError(
                "a source slug names exactly one implementation: " + "; ".join(sorted(collisions))
            )

    def sources_by_layer(self) -> dict[str, Mapping[str, object]]:
        """`{layer_slug: {source_slug: instance}}` -- the un-flattened map the boot check reads.

        Spelled here once so the layer keys the boot check refuses against are the same literals the
        resolvers and `layer_contracts()` use.
        """
        return {
            DROUGHT_LAYER_SLUG: self.drought,
            BURN_SEVERITY_LAYER_SLUG: self.burn_severity,
            SOIL_SURVEY_LAYER_SLUG: self.soil_survey,
        }

    def coverage_claims(self) -> dict[str, SourceCoverageClaim]:
        """`{source_slug: coverage claim}` across every layer; each protocol declares `coverage`.

        Flat on purpose and losslessly so: coverage is a property of the SOURCE, not of the layer
        it serves, and `__post_init__` has already refused a registry whose slugs collide, so no
        entry here can shadow another (STYLE-REVIEW-W6 S6).
        """
        return {
            slug: source.coverage
            for layer_sources in (self.drought, self.burn_severity, self.soil_survey)
            for slug, source in layer_sources.items()
        }

    def layer_contracts(self) -> LayerSourceContracts:
        """The protocol each layer expects, beside the sources registered UNDER that layer.

        The boot check's argument. The protocol classes are imported INSIDE so they exist at
        runtime for the `isinstance` second assertion, not merely as `TYPE_CHECKING` names.
        """
        from agri_data_service.pipeline.direct.burn_severity.source_protocol import (  # noqa: PLC0415
            BurnSeveritySource as BurnSeveritySourceProtocol,
        )
        from agri_data_service.pipeline.direct.drought.source_protocol import (  # noqa: PLC0415
            DroughtSource as DroughtSourceProtocol,
        )
        from agri_data_service.pipeline.direct.soil_survey.source_protocol import (  # noqa: PLC0415
            SoilSurveySource as SoilSurveySourceProtocol,
        )

        return LayerSourceContracts(
            protocol_by_layer={
                DROUGHT_LAYER_SLUG: DroughtSourceProtocol,
                BURN_SEVERITY_LAYER_SLUG: BurnSeveritySourceProtocol,
                SOIL_SURVEY_LAYER_SLUG: SoilSurveySourceProtocol,
            },
            sources_by_layer=self.sources_by_layer(),
        )


def _source_registry() -> SourceRegistry:
    """Every source implementation, under the layer it implements.

    The three source modules are imported INSIDE this function on purpose. Each pulls its layer's
    ingest transport (`httpx`) and lane registry, and the callers are the boot check in `app.py`
    and the two `resolve_*_source` functions below; importing them at module scope would make
    merely naming this registry pay for the whole of `pipeline/direct/` and would put extra import
    edges into `app.py`'s import graph.
    """
    from agri_data_service.pipeline.direct.burn_severity.mtbs import MTBS_BURN_SEVERITY_SOURCE  # noqa: PLC0415
    from agri_data_service.pipeline.direct.drought.usdm import USDM_DROUGHT_SOURCE  # noqa: PLC0415
    from agri_data_service.pipeline.direct.soil_survey.ssurgo import SSURGO_SOIL_SURVEY_SOURCE  # noqa: PLC0415

    return SourceRegistry(
        drought={USDM_DROUGHT_SOURCE.source_slug: USDM_DROUGHT_SOURCE},
        burn_severity={MTBS_BURN_SEVERITY_SOURCE.source_slug: MTBS_BURN_SEVERITY_SOURCE},
        soil_survey={SSURGO_SOIL_SURVEY_SOURCE.source_slug: SSURGO_SOIL_SURVEY_SOURCE},
    )


def declared_source_coverage_claims() -> dict[str, SourceCoverageClaim]:
    """Return `{source_slug: coverage claim}` for every source that implements its layer's protocol.

    Sources with no protocol yet are simply absent, which `foundation/region/bindings.py` reports
    through `unverified_binding_slugs` rather than treating as a failure — `federation.md` §5 lands
    the protocols three layers at a time.
    """
    return _source_registry().coverage_claims()


def declared_layer_source_contracts() -> LayerSourceContracts:
    """Return the per-layer protocols and the sources registered under each layer, for the boot check.

    Passed IN to `foundation/region/bindings.py` rather than imported by it, for the same reason
    `declared_source_coverage_claims()` is: `foundation` may not import `pipeline`
    (`tests/test_layer_import_contract.py`), and the protocol classes live beside their lanes.
    """
    return _source_registry().layer_contracts()


def _resolve_bound_source[BoundSource](
    layer_slug: str,
    implementations: Mapping[str, BoundSource],
    *,
    region: Region | None = None,
) -> BoundSource:
    """Return the concrete source instance the region's manifest binds `layer_slug` to.

    Uses a PEP 695 type parameter -- the protocol each layer's own map is typed with, never widened
    to `object` (S5, W3 review) -- rather than a module-level `TypeVar` (UP047, requires-python
    already pins 3.12).

    Reads `load_region()` PER CALL when `region` is not supplied -- never at import or module
    scope -- so a different process-wide region selection (`PLANTGEO_REGION`) resolves a different
    implementation without restarting anything. `region` is a test seam: a fabricated `Region`
    bound to a fabricated source lets a test prove the lane calls whatever the binding names,
    without touching the real manifest or a real transport.

    `implementations` is the LAYER'S own map, so the returned type is that layer's protocol and the
    caller needs no cast; a source registered under a different layer is simply not found here, and
    `assert_region_bindings_are_servable` has already refused that manifest at boot.
    """
    from agri_data_service.foundation.region.manifest import load_region  # noqa: PLC0415

    resolved_region = region if region is not None else load_region()
    binding = next((b for b in resolved_region.enabled_layers if b.layer_slug == layer_slug), None)
    if binding is None:
        raise UnboundLayerError(f"region {resolved_region.slug!r} has no enabled binding for layer {layer_slug!r}")
    source = implementations.get(binding.source_slug)
    if source is None:
        raise UnboundLayerError(
            f"region {resolved_region.slug!r} binds layer {layer_slug!r} to source "
            f"{binding.source_slug!r}, which has no registered implementation"
        )
    return source


def resolve_drought_source(*, region: Region | None = None) -> DroughtSource:
    """The drought layer's source, resolved from the region's OWN binding rather than `usdm.py` by name."""
    return _resolve_bound_source(DROUGHT_LAYER_SLUG, _source_registry().drought, region=region)


def resolve_burn_severity_source(*, region: Region | None = None) -> BurnSeveritySource:
    """The burn-severity layer's source, resolved from the region's OWN binding rather than `mtbs.py` by name."""
    return _resolve_bound_source(BURN_SEVERITY_LAYER_SLUG, _source_registry().burn_severity, region=region)


__all__ = [
    "BURN_SEVERITY_LAYER_SLUG",
    "DROUGHT_LAYER_SLUG",
    "SOIL_SURVEY_LAYER_SLUG",
    "DuplicateSourceSlugError",
    "SourceRegistry",
    "UnboundLayerError",
    "declared_layer_source_contracts",
    "declared_source_coverage_claims",
    "resolve_burn_severity_source",
    "resolve_drought_source",
]

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
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agri_data_service.foundation.region.manifest import Region
    from agri_data_service.foundation.region.source_coverage import SourceCoverageClaim
    from agri_data_service.pipeline.direct.burn_severity.source_protocol import BurnSeveritySource
    from agri_data_service.pipeline.direct.drought.source_protocol import DroughtSource


class UnboundLayerError(RuntimeError):
    """Raised when the resolved region has no enabled binding for the requested layer, or that
    binding names a source slug with no registered implementation."""


def _source_registry() -> dict[str, object]:
    """`{source_slug: source_instance}` for every source that implements its layer's protocol.

    The three source modules are imported INSIDE this function on purpose. Each pulls its layer's
    ingest transport (`httpx`) and lane registry, and the callers are the boot check in `app.py`
    and the two `resolve_*_source` functions below; importing them at module scope would make
    merely naming this registry pay for the whole of `pipeline/direct/` and would put extra import
    edges into `app.py`'s import graph.
    """
    from agri_data_service.pipeline.direct.burn_severity.mtbs import MTBS_BURN_SEVERITY_SOURCE  # noqa: PLC0415
    from agri_data_service.pipeline.direct.drought.usdm import USDM_DROUGHT_SOURCE  # noqa: PLC0415
    from agri_data_service.pipeline.direct.soil_survey.ssurgo import SSURGO_SOIL_SURVEY_SOURCE  # noqa: PLC0415

    sources = (MTBS_BURN_SEVERITY_SOURCE, USDM_DROUGHT_SOURCE, SSURGO_SOIL_SURVEY_SOURCE)
    return {source.source_slug: source for source in sources}


def declared_source_coverage_claims() -> dict[str, SourceCoverageClaim]:
    """Return `{source_slug: coverage claim}` for every source that implements its layer's protocol.

    Sources with no protocol yet are simply absent, which `foundation/region/bindings.py` reports
    through `unverified_binding_slugs` rather than treating as a failure — `federation.md` §5 lands
    the protocols three layers at a time.
    """
    return {slug: source.coverage for slug, source in _source_registry().items()}  # type: ignore[attr-defined]


def _resolve_bound_source(layer_slug: str, *, region: Region | None = None) -> object:
    """Return the concrete source instance the region's manifest binds `layer_slug` to.

    Reads `load_region()` PER CALL when `region` is not supplied -- never at import or module
    scope -- so a different process-wide region selection (`PLANTGEO_REGION`) resolves a different
    implementation without restarting anything. `region` is a test seam: a fabricated `Region`
    bound to a fabricated source lets a test prove the lane calls whatever the binding names,
    without touching the real manifest or a real transport.
    """
    from agri_data_service.foundation.region.manifest import load_region  # noqa: PLC0415

    resolved_region = region if region is not None else load_region()
    binding = next((b for b in resolved_region.enabled_layers if b.layer_slug == layer_slug), None)
    if binding is None:
        raise UnboundLayerError(f"region {resolved_region.slug!r} has no enabled binding for layer {layer_slug!r}")
    registry = _source_registry()
    source = registry.get(binding.source_slug)
    if source is None:
        raise UnboundLayerError(
            f"region {resolved_region.slug!r} binds layer {layer_slug!r} to source "
            f"{binding.source_slug!r}, which has no registered implementation"
        )
    return source


def resolve_drought_source(*, region: Region | None = None) -> DroughtSource:
    """The drought layer's source, resolved from the region's OWN binding rather than `usdm.py` by name."""
    return _resolve_bound_source("drought", region=region)  # type: ignore[return-value]


def resolve_burn_severity_source(*, region: Region | None = None) -> BurnSeveritySource:
    """The burn-severity layer's source, resolved from the region's OWN binding rather than `mtbs.py` by name."""
    return _resolve_bound_source("burn-severity", region=region)  # type: ignore[return-value]


__all__ = [
    "UnboundLayerError",
    "declared_source_coverage_claims",
    "resolve_burn_severity_source",
    "resolve_drought_source",
]

"""Which platform layers this region actually binds a source for, and which are a governed absence.

`federation.md` §2, "the platform must run with a layer unbound": a region with no soil source yet
gets a soil layer that reports `not available in this region` through the slider capability
catalogue, legends and agent tools. This module is the one place that question is answered; see
`AGENTS.md` in this directory, section "Layer availability, and why the catalogue is hand-spelled".
"""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Literal

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from collections.abc import Mapping

    from agri_data_service.foundation.region.manifest import Region

#: What one layer's binding is in one region. `bound_global`/`bound_regional` carry the manifest's
#: own `coverage` word for the bound source; `unbound` is the governed absence.
LayerBindingState = Literal["bound_global", "bound_regional", "unbound"]

#: The one reason an unbound layer carries today: this region's manifest names no source for it.
UNBOUND_REASON_NO_SOURCE: Final = "no_source_bound_in_region"

#: The platform's layer vocabulary -- every layer ANY region may bind a source for.
#:
#: Hand-spelled, and deliberately not derived from the pilot's `enabled_layers`: a catalogue read
#: off one manifest can never report a layer as unbound, which is the entire question this module
#: exists to answer. `interventions` is absent on purpose -- see this directory's `AGENTS.md`.
#:
#: Every manifest restates this vocabulary in its own `platform_layers` field, and
#: `tests/foundation/test_region_layer_availability.py` pins the two together; the manifest copy is
#: what the web tree compiles in (`src/lib/region/pnw.ts`), so both trees answer
#: "is this slug a federated layer at all" from the same enumeration rather than from one tree's
#: bindings (STYLE-REVIEW-W5 B1).
#:
#: `land-context` is in the vocabulary and bound by NO region today: the pilot's land-context plane
#: has no published lane, so the manifest states it `unbound` with a reason instead of leaving it
#: unsayable. A slug no manifest can name reads to a caller exactly like a layer that is merely
#: absent, and only one of those is a statement.
#:
#: `fire-risk` and `weather-forecast` joined on 2026-09-19 (track `plantgeo_ml_service_20260918`,
#: FR-5a and FR-12) and are bound by NO region, for the land-context reason exactly: they are
#: platform layers whose writer, `services/plantgeo-ml-service`, has published nothing yet. Naming
#: them unbound is the honest state -- a surface that asks gets a governed absence with a reason
#: instead of a slug this build has never heard of. They gain an `enabled_layers` binding in the
#: push that admits the first published partition, never before it.
PLATFORM_LAYER_SLUGS: Final[tuple[str, ...]] = (
    "botanical-occurrences",
    "burn-severity",
    "drought",
    "evacuation-zones",
    "fire-detections",
    "fire-perimeters",
    "fire-risk",
    "land-context",
    "sensors",
    "signal",
    "soil-survey",
    "vegetation",
    "water-gauges",
    "watersheds",
    "weather-forecast",
    "weather-observations",
)


class LayerBindingStatus(BaseModel):
    """One platform layer's binding in one region: bound to a named source, or a governed absence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    layer_slug: str
    binding: LayerBindingState
    #: The manifest's bound source, or `None` when the layer is unbound in this region.
    source_slug: str | None = None
    #: Why an unbound layer is absent, in a word a surface can caption. `None` while bound.
    reason: str | None = None


def region_layer_availability(region: Region) -> Mapping[str, LayerBindingStatus]:
    """Every platform layer's binding status in this region, keyed by layer slug.

    Covers `PLATFORM_LAYER_SLUGS` in full, not just the manifest's bindings: a layer the manifest
    never names is reported as `unbound` with a reason rather than omitted, because an omission and
    a governed absence read identically to a caller and only one of them is a statement.

    A manifest binding for a layer outside the platform vocabulary is carried through rather than
    dropped -- a region that binds a layer this build has never heard of is a real deployment, and
    silently losing it here would hide it from every surface that reads this mapping.
    """
    bound = {
        binding.layer_slug: LayerBindingStatus(
            layer_slug=binding.layer_slug,
            binding="bound_global" if binding.coverage == "global" else "bound_regional",
            source_slug=binding.source_slug,
        )
        for binding in region.enabled_layers
    }
    unbound = {
        layer_slug: LayerBindingStatus(
            layer_slug=layer_slug,
            binding="unbound",
            reason=UNBOUND_REASON_NO_SOURCE,
        )
        for layer_slug in PLATFORM_LAYER_SLUGS
        if layer_slug not in bound
    }
    return MappingProxyType(dict(sorted((bound | unbound).items())))


def is_layer_bound(region: Region, layer_slug: str) -> bool:
    """True when this region binds a source for the named layer; the cheap single-layer question."""
    return any(binding.layer_slug == layer_slug for binding in region.enabled_layers)


__all__ = [
    "PLATFORM_LAYER_SLUGS",
    "UNBOUND_REASON_NO_SOURCE",
    "LayerBindingState",
    "LayerBindingStatus",
    "is_layer_bound",
    "region_layer_availability",
]

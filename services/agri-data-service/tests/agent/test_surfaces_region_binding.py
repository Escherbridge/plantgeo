"""N9/N30 (python half): pin `SURFACE_REGION_LAYER_SLUGS` VALUES against `PLATFORM_LAYER_SLUGS`.

The TS half of this parity gap is already pinned by
`src/__tests__/region/layer-region-binding.test.tsx` (STYLE-REVIEW-W6 S1, closed by W7-A). This is
the other end: `agent/surfaces.py::SURFACE_REGION_LAYER_SLUGS` maps a surface name to a manifest
layer slug it asserts exists, and nothing before this test verified that slug is a REAL platform
layer. A one-character drift here routes a federated surface's binding lookup at a slug
`layerBindingInRegion` has never heard of; `layer_availability.layerBindingInRegion`'s own
equivalent (`layer-region-binding.ts`) refuses that case for a TS caller, but this Python table had
no such guard.
"""

from __future__ import annotations

from agri_data_service.agent.surfaces import SURFACE_REGION_LAYER_SLUGS
from agri_data_service.foundation.region.layer_availability import PLATFORM_LAYER_SLUGS


def test_every_surface_region_layer_slug_is_a_platform_layer() -> None:
    """Every VALUE `SURFACE_REGION_LAYER_SLUGS` names must be a real member of the platform vocabulary."""
    platform_layers = set(PLATFORM_LAYER_SLUGS)
    drifted = {
        surface: layer_slug
        for surface, layer_slug in SURFACE_REGION_LAYER_SLUGS.items()
        if layer_slug not in platform_layers
    }
    assert drifted == {}, f"surfaces bound to a non-platform layer slug: {drifted}"

/**
 * Which region-manifest layer each map toggle is bound through, and the one sentence an unbound
 * layer says.
 *
 * `conductor/code_styleguides/federation.md` §2's last bullet on the web side: a region with no
 * soil source yet gets a soil layer that reports "not available in this region" through the slider
 * capability catalogue and the legends, rather than an empty map that reads as an outage. Rationale
 * and the hand-spelling decision: see `src/lib/map/AGENTS.md` §layer-region-binding.
 */

import { LAYER_REGISTRY, type LayerToggleId } from "@/lib/map/layer-registry";
import { getRegion, regionIdentityVerdict } from "@/lib/region/region";
import type { SliderCapabilities } from "@/types/time-slider";

/**
 * Warehouse layer name to the region-manifest layer slug it binds through.
 *
 * Mirrors `agent/surfaces.py`'s `SURFACE_REGION_LAYER_SLUGS` for every layer that has an agent
 * surface, and is hand-spelled for the same reason that table is: the two namespaces genuinely
 * disagree where it matters. `fire-risk` and `weather-forecast` are here and NOT there, because the
 * agent has no surface for either yet -- that table maps agent surfaces, this one maps warehouse
 * layer names, and only the second is what a map toggle resolves through.
 * `drought-areas` is served by the manifest layer `drought`, and all twelve climate and soil field
 * streams are DERIVED products of the one `signal` plane -- they bind no source of their own, so
 * they inherit `signal`'s binding and go dark together when it is unbound.
 *
 * A warehouse name absent from this table has no manifest layer and can never be unbound:
 * `interventions` (Postgres-only, no source binding concept) and `strategy-recommendations` (a
 * derived surface, not an ingested layer) are the two, and the eight toggles with a null
 * `warehouseLayerName` never reach here at all.
 */
export const REGION_LAYER_SLUG_BY_WAREHOUSE_NAME: Readonly<Record<string, string>> = {
  "burn-severity": "burn-severity",
  "evacuation-zones": "evacuation-zones",
  "fire-detections": "fire-detections",
  "fire-perimeters": "fire-perimeters",
  sensors: "sensors",
  "soil-survey": "soil-survey",
  vegetation: "vegetation",
  watersheds: "watersheds",
  "water-gauges": "water-gauges",
  "weather-observations": "weather-observations",
  // Written by `services/plantgeo-ml-service`, not by agri-data-service (track
  // `plantgeo_ml_service_20260918`, FR-5a and FR-12). No region binds a source for either yet, so
  // both answer `unbound` from a manifest STATEMENT -- which is the point of naming them here: a
  // slug absent from this table would answer `not_federated`, and `not_federated` is treated as
  // available, which draws an empty map as a working layer.
  "fire-risk": "fire-risk",
  "weather-forecast": "weather-forecast",
  "drought-areas": "drought",
  "climate-field-air-temperature": "signal",
  "climate-field-dew-point": "signal",
  "climate-field-precipitation": "signal",
  "climate-field-relative-humidity": "signal",
  "climate-field-shortwave-radiation": "signal",
  "climate-field-wind-speed": "signal",
  "climate-field-soil-wetness-surface": "signal",
  "climate-field-soil-wetness-root-zone": "signal",
  "climate-field-soil-wetness-profile": "signal",
  "soil-field-moisture": "signal",
  "soil-field-temperature": "signal",
  "soil-field-vpd": "signal",
};

/**
 * Every manifest layer slug some toggle path can produce -- the third enumeration the binding rule
 * depends on.
 *
 * `PLATFORM_LAYER_SLUGS` and the two manifests are pinned to each other in both trees; this table's
 * VALUES were pinned to nothing (STYLE-REVIEW-W6 S1). A one-character drift here used to be
 * harmless, because the old helper only consulted the payload; under the one binding rule it takes
 * the manifest arm, misses `platformLayers` and returns `not_federated`, which is treated as
 * available -- a federated layer drawn as a working toggle over an empty map, caption suppressed,
 * silently and forever. `region/layer-region-binding.test.tsx` pins the set, and
 * `layerBindingInRegion` below refuses to call any member of it `not_federated` even if the test
 * is ever deleted.
 */
export const TOGGLE_REACHABLE_REGION_LAYER_SLUGS: ReadonlySet<string> = new Set([
  ...Object.values(REGION_LAYER_SLUG_BY_WAREHOUSE_NAME),
  // Toggles whose binding rides on `LayerRegistryEntry.regionLayerSlug` instead of the
  // warehouse-name table above (N40) -- see that field's doc comment in `layer-registry.ts`.
  ...Object.values(LAYER_REGISTRY)
    .map((entry) => entry.regionLayerSlug)
    .filter((slug): slug is string => slug !== undefined),
]);

/** The manifest layer one toggle binds through, or null when the toggle is not a federated layer. */
export function regionLayerSlugForToggle(layerId: LayerToggleId): string | null {
  const definition = LAYER_REGISTRY[layerId];
  if (definition.regionLayerSlug !== undefined) return definition.regionLayerSlug;
  const warehouseLayerName = definition.warehouseLayerName;
  if (warehouseLayerName === null) return null;
  return REGION_LAYER_SLUG_BY_WAREHOUSE_NAME[warehouseLayerName] ?? null;
}

/**
 * What this deployment's region says about one manifest layer slug: the ONE binding rule.
 *
 * - `bound` -- a source fills this layer here; the toggle works and the lane may fetch.
 * - `unbound` -- the layer IS in the platform vocabulary and this region binds no source for it.
 *   A governed absence with a named reason (`federation.md` §2), never an outage.
 * - `not_federated` -- the slug is not a platform layer at all, so binding is not a question that
 *   applies to it. Treated as available: `interventions`, an uploaded layer and a future slug this
 *   build has never heard of are all here, and none of them is "unavailable in this region".
 *   Reserved for slugs that arrive from OUTSIDE this build: a slug some compiled-in caller names
 *   (`TOGGLE_REACHABLE_REGION_LAYER_SLUGS`, `LAND_CONTEXT_REGION_LAYER_SLUG`) can never be answered
 *   `not_federated`, because for those "outside the vocabulary" means a typo rather than a newer
 *   deployment, and fail-open on a typo is the empty-map outage (STYLE-REVIEW-W6 S1).
 *
 * Two evidence sources, in this order, and the SAME verdict for the same evidence -- which is what
 * the two helpers this replaced did not do (STYLE-REVIEW-W5 B1):
 *
 * 1. The coverage PAYLOAD, when it states a row for the slug AND names this bundle's own region.
 *    A payload naming a different region describes another deployment's footprint, so its bindings
 *    are not evidence about this one and the manifest answers alone (STYLE-REVIEW-W8 S1); a payload
 *    naming NO region is unchanged -- silence is no claim, not a disagreement. An agreeing payload
 *    is the serving side's live answer and outranks a bundle that may be a deploy behind.
 * 2. The compiled MANIFEST otherwise. `platformLayers` is the platform's whole vocabulary and
 *    `enabledLayers` this region's bindings, so a slug in the first and absent from the second is a
 *    STATEMENT that nothing fills it -- not the silence the old toggle helper failed open on. A
 *    slug in neither is `not_federated`.
 *
 * Payload silence can therefore no longer disable a layer the manifest binds (the deploy-window
 * cost the old fail-open rule was written for), and it no longer dresses a manifest-declared
 * absence as a working toggle that draws an empty map.
 */
export type LayerRegionBinding = "bound" | "unbound" | "not_federated";

export function layerBindingInRegion(
  capabilities: SliderCapabilities | null,
  layerSlug: string
): LayerRegionBinding {
  const payloadDescribesThisRegion =
    regionIdentityVerdict(capabilities?.servedRegionSlug).kind !== "mismatch";
  const stated = payloadDescribesThisRegion
    ? capabilities?.layerBindings?.find((binding) => binding.layerSlug === layerSlug)
    : undefined;
  if (stated !== undefined) return stated.binding === "unbound" ? "unbound" : "bound";
  const region = getRegion();
  if (!region.platformLayers.includes(layerSlug)) {
    if (isCompiledInRegionLayerSlug(layerSlug)) {
      // A slug this build ASKS about is not a slug this build has never heard of. Reaching here
      // means the compiled table and the compiled manifest disagree, which is a contract error and
      // never a licence to draw an empty map as a working layer (STYLE-REVIEW-W6 S1).
      console.error(
        `[layer-region-binding] ${layerSlug} is a compiled-in region layer slug but is absent from ` +
          `region ${region.slug}'s platformLayers; reporting it unbound rather than not_federated`
      );
      return "unbound";
    }
    return "not_federated";
  }
  return region.enabledLayers.some((binding) => binding.layerSlug === layerSlug) ? "bound" : "unbound";
}

/** Whether some compiled-in caller names this slug: the toggle table's values, plus land-context. */
function isCompiledInRegionLayerSlug(layerSlug: string): boolean {
  return TOGGLE_REACHABLE_REGION_LAYER_SLUGS.has(layerSlug) || layerSlug === LAND_CONTEXT_REGION_LAYER_SLUG;
}

/** The same verdict for a map toggle; a toggle with no manifest layer is `not_federated`. */
export function toggleBindingInRegion(
  capabilities: SliderCapabilities | null,
  layerId: LayerToggleId
): LayerRegionBinding {
  const layerSlug = regionLayerSlugForToggle(layerId);
  if (layerSlug === null) return "not_federated";
  return layerBindingInRegion(capabilities, layerSlug);
}

/**
 * The caption an unbound layer carries, or null when the layer is available.
 *
 * Neutral by design: the layer is not broken, withheld, late or empty -- this deployment simply
 * covers ground no source fills it over. The sentence never names the pilot's source, never
 * suggests waiting, and never implies the record has a gap, because none of those is true.
 */
export function unboundLayerCaption(
  capabilities: SliderCapabilities | null,
  layerId: LayerToggleId
): string | null {
  if (toggleBindingInRegion(capabilities, layerId) !== "unbound") return null;
  return `${LAYER_REGISTRY[layerId].label} is not available in this region: no data source is bound for it here.`;
}

/**
 * The manifest layer slug the land-context reference plane binds through.
 *
 * Land-context is not a `LayerToggleId` -- it has its own group store and its own dock section --
 * so the toggle-keyed helper cannot answer for it. It is named here rather than inside the hook so
 * the one place that answers "is this layer bound in this region" stays one place. It is a platform
 * layer (`platformLayers`) that no region binds a source for yet, so `layerBindingInRegion` answers
 * `unbound` from a manifest STATEMENT rather than from an omission.
 */
export const LAND_CONTEXT_REGION_LAYER_SLUG = "land-context";

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
import { getRegion } from "@/lib/region/region";
import type { SliderCapabilities, SliderLayerBinding } from "@/types/time-slider";

/**
 * Warehouse layer name to the region-manifest layer slug it binds through.
 *
 * Mirrors `agent/surfaces.py`'s `SURFACE_REGION_LAYER_SLUGS` entry for entry, and is hand-spelled
 * for the same reason that table is: the two namespaces genuinely disagree where it matters.
 * `drought-areas` is served by the manifest layer `drought`, and all twelve climate and soil field
 * streams are DERIVED products of the one `signal` plane -- they bind no source of their own, so
 * they inherit `signal`'s binding and go dark together when it is unbound.
 *
 * A warehouse name absent from this table has no manifest layer and can never be unbound:
 * `interventions` (Postgres-only, no source binding concept) and `strategy-recommendations` (a
 * derived surface, not an ingested layer) are the two, and the eight toggles with a null
 * `warehouseLayerName` never reach here at all.
 */
const REGION_LAYER_SLUG_BY_WAREHOUSE_NAME: Readonly<Record<string, string>> = {
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

/** The manifest layer one toggle binds through, or null when the toggle is not a federated layer. */
export function regionLayerSlugForToggle(layerId: LayerToggleId): string | null {
  const warehouseLayerName = LAYER_REGISTRY[layerId].warehouseLayerName;
  if (warehouseLayerName === null) return null;
  return REGION_LAYER_SLUG_BY_WAREHOUSE_NAME[warehouseLayerName] ?? null;
}

/**
 * True only when the payload EXPLICITLY states this toggle's layer is unbound in this region.
 *
 * Fail-OPEN on every unknown, and deliberately so: a null payload, a serving side that states no
 * bindings, a toggle with no manifest layer and a layer the list simply does not mention all read
 * as available. The only claim strong enough to disable a control is the serving side naming the
 * layer `unbound`, because every other case is silence, and silence during a deploy window would
 * blank a layer that works.
 */
export function isLayerUnboundInRegion(
  capabilities: SliderCapabilities | null,
  layerId: LayerToggleId
): boolean {
  return regionLayerBindingForToggle(capabilities, layerId)?.binding === "unbound";
}

/** This toggle's binding entry from the payload, or null when nothing states one. */
export function regionLayerBindingForToggle(
  capabilities: SliderCapabilities | null,
  layerId: LayerToggleId
): SliderLayerBinding | null {
  const bindings = capabilities?.layerBindings;
  if (bindings === undefined || bindings.length === 0) return null;
  const layerSlug = regionLayerSlugForToggle(layerId);
  if (layerSlug === null) return null;
  return bindings.find((binding) => binding.layerSlug === layerSlug) ?? null;
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
  if (!isLayerUnboundInRegion(capabilities, layerId)) return null;
  return `${LAYER_REGISTRY[layerId].label} is not available in this region: no data source is bound for it here.`;
}

/**
 * The manifest layer slug the land-context reference plane would bind through.
 *
 * Land-context is not a `LayerToggleId` -- it has its own group store and its own dock section --
 * so the toggle-keyed helpers above cannot answer for it. It is named here rather than inside the
 * hook so the one place that answers "is this layer bound in this region" stays one place.
 */
export const LAND_CONTEXT_REGION_LAYER_SLUG = "land-context";

/**
 * Whether a bare manifest layer slug is bound to a source in THIS deployment's region.
 *
 * Fails CLOSED on manifest silence, which is the opposite of `isLayerUnboundInRegion` above, and
 * the difference is the difference between the two evidence sources. That helper reads the
 * coverage PAYLOAD, which arrives over the network and is silent for a whole deploy window, so
 * silence there must not disable a working layer. This reads `getRegion().enabledLayers`, which is
 * compiled into the bundle and states the region's COMPLETE binding set (`federation.md` §2) --
 * absence from it is a claim, not a gap, and the claim is that no source fills this layer here.
 *
 * The payload still wins when it states something: a serving side that names the layer `unbound`
 * is reporting a binding the manifest has not caught up with yet.
 */
export function isRegionLayerBoundHere(
  capabilities: SliderCapabilities | null,
  layerSlug: string
): boolean {
  const stated = capabilities?.layerBindings?.find((binding) => binding.layerSlug === layerSlug);
  if (stated !== undefined) return stated.binding !== "unbound";
  return getRegion().enabledLayers.some((binding) => binding.layerSlug === layerSlug);
}

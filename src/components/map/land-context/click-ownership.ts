import type { Map as MapLibreMap, PointLike } from "maplibre-gl";
import { useMapStore } from "@/stores/map-store";
import { HOVERABLE_LAYER_IDS, TOOLTIP_TAP_LAYER_IDS } from "@/lib/map/hover-fields";
import { isInterventionStyleLayerId } from "@/lib/map/layer-registry";
import { isScalarFieldInspectionAllowed } from "@/lib/map/scalar-field-inspection";

/**
 * "One click, one meaning" (`src/components/map/AGENTS.md` "Picking a point to query"): the
 * single predicate every bare canvas-click handler applies before claiming a click, hoisted
 * here so `MapView`'s agent popup and `LandContextLayer`'s point selection stand down for the
 * same owners rather than each keeping a private copy that drifts.
 *
 * Owners recognised today, in order:
 *  1. a panel capturing query points (`useMapQueryPoint`, the Soil section) -- `isCapturingQueryPoint`;
 *  2. a rendered intervention feature, unconditionally -- it opens the detail modal
 *     (`use-intervention-detail-clicks.ts`), even while its layers are inspection-suppressed;
 *  3. a rendered feature of a layer that answers THIS click with a surface of its own, and is
 *     not inspection-suppressed:
 *     - `DEDICATED_CLICK_LAYER_IDS` on every pointer: the six `FireLayer`/`WaterLayer` popup
 *       layers (`hover-fields.ts` `LAYER_IDS_WITH_A_DEDICATED_CLICK_POPUP`, derived here as
 *       `HOVERABLE_LAYER_IDS` minus `TOOLTIP_TAP_LAYER_IDS` because that set is not exported) plus
 *       the two GBIF occurrence layers (`GbifOccurrencesLayer.tsx` registers `map.on("click", id)`
 *       on both; they are deliberately NOT in `HOVERABLE_LAYER_IDS`, which has no GBIF hover
 *       formatter);
 *     - `TOOLTIP_TAP_LAYER_IDS` only on a coarse pointer, mirroring `HoverTooltip.handleClick`'s
 *       own `if (!isCoarsePointer()) return;` gate: on a mouse those thirteen fills (drought,
 *       watersheds, weather -- most of the PNW when on) do nothing with a click, so owning it
 *       would be a dead zone for both the popup and the land-context entry.
 *
 * Membership first, suppression second: `isScalarFieldInspectionAllowed` is only
 * `!suppressed.has(id)`, true for the basemap's unfiltered `earth`/`water` fills under every
 * land pixel, so applied to any rendered feature it would own every click.
 *
 * Not recognised: the drawing tools (`ServiceAreaDrawTool`, terra-draw) expose no store flag, so a
 * vertex click still reaches every bare handler. See `land-context/AGENTS.md` "Residual gap".
 */

/**
 * Layers that register their own `map.on("click", id, …)` but are not in `hover-fields.ts`'s
 * private dedicated six: GBIF (`GbifOccurrencesLayer.tsx:204-205`, absent from
 * `HOVERABLE_LAYER_IDS` entirely) and the three botanical point layers
 * (`BotanicalOccurrencesLayer.tsx:263-265`, hoverable, so the registry derivation would
 * otherwise file them as tap-only). The richness/collection-effort fills are hover-only and stay
 * tap-only. Proper home: `LAYER_IDS_WITH_A_DEDICATED_CLICK_POPUP` in `hover-fields.ts` (out of
 * this partition) -- see `land-context/AGENTS.md`.
 */
export const CLICK_HANDLER_IDS_OUTSIDE_THE_SIX: readonly string[] = [
  "gbif-occurrences-exact",
  "gbif-occurrences-generalized",
  "botanical-occurrences-exact",
  "botanical-occurrences-generalized",
  "botanical-occurrences-possible",
];
const CLICK_HANDLER_IDS_OUTSIDE_THE_SIX_SET: ReadonlySet<string> = new Set(CLICK_HANDLER_IDS_OUTSIDE_THE_SIX);

// Tap-only = the tooltip's tap set minus anything that turns out to have its own click handler.
const TAP_ONLY_LAYER_IDS: ReadonlySet<string> = new Set(
  TOOLTIP_TAP_LAYER_IDS.filter((id) => !CLICK_HANDLER_IDS_OUTSIDE_THE_SIX_SET.has(id))
);

export const DEDICATED_CLICK_LAYER_IDS: ReadonlySet<string> = new Set([
  ...HOVERABLE_LAYER_IDS.filter((id) => !TAP_ONLY_LAYER_IDS.has(id)),
  ...CLICK_HANDLER_IDS_OUTSIDE_THE_SIX,
]);

/** Every layer arm 3 can own on SOME pointer: the dedicated-popup set plus the tap-only set. */
export const CLICK_OWNING_LAYER_IDS: ReadonlySet<string> = new Set([
  ...DEDICATED_CLICK_LAYER_IDS,
  ...TAP_ONLY_LAYER_IDS,
]);

/** Mirror of `HoverTooltip.tsx`'s private `isCoarsePointer`: degrades to "a mouse" where jsdom has no `matchMedia`. */
function isCoarsePointer(): boolean {
  return window.matchMedia?.("(pointer: coarse)").matches === true;
}

/** True when this layer answers a click on the CURRENT pointer (before the suppression check). */
export function layerOwnsClickOnThisPointer(layerId: string): boolean {
  if (DEDICATED_CLICK_LAYER_IDS.has(layerId)) return true;
  return TAP_ONLY_LAYER_IDS.has(layerId) && isCoarsePointer();
}

export function isClickOwnedByAnotherSurface(map: MapLibreMap, point: PointLike): boolean {
  if (useMapStore.getState().isCapturingQueryPoint) return true;
  const features = map.queryRenderedFeatures(point);
  return Boolean(
    features?.some(
      (feature) =>
        isInterventionStyleLayerId(feature.layer.id) ||
        (layerOwnsClickOnThisPointer(feature.layer.id) &&
          isScalarFieldInspectionAllowed(map, feature.layer.id))
    )
  );
}

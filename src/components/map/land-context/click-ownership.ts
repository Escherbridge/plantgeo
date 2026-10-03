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
 *       `HOVERABLE_LAYER_IDS` minus `TOOLTIP_TAP_LAYER_IDS` because that set is not exported);
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

// Tap-only = the tooltip's tap set. (The GBIF and herbarium point layers that once registered
// their own click handlers outside it were retired 2026-10-03; see map/AGENTS.md §Retired layers.)
const TAP_ONLY_LAYER_IDS: ReadonlySet<string> = new Set(TOOLTIP_TAP_LAYER_IDS);

export const DEDICATED_CLICK_LAYER_IDS: ReadonlySet<string> = new Set(
  HOVERABLE_LAYER_IDS.filter((id) => !TAP_ONLY_LAYER_IDS.has(id))
);

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

"use client";

import { useLandContextStore } from "@/stores/land-context-store";
import { buildWideAreaBoxPolygon } from "@/components/map/land-context/mobile/wideAreaGeometry";

/**
 * Accessible area-selection alternative to freehand/drag polygon drawing.
 *
 * Per spec ("Accessibility and bounded delivery"): "On mobile, support tap selection, an
 * accessible area-selection alternative...". Dragging to draw a polygon on a touchscreen
 * competes with map pan/zoom gestures and has no clean switch-access/keyboard equivalent, so
 * this repo's `InterventionDrawControl` (terra-draw freehand/point/polygon) is deliberately NOT
 * reused here -- it is drag-oriented and belongs to a different feature with different
 * constraints. This is a single-purpose, single-tap/Enter action instead: once a point selection
 * exists, expand it into a fixed-radius bounding box centred on that point, using the store's
 * already-supported `mode: "area"` selection path (`LandContextSelectionInput.areaPolygon`,
 * consumed by `useLandContextQuery`'s bbox derivation).
 *
 * `WIDE_AREA_RADIUS_METERS` (~500m) is sized for parcel/utility-territory context: wide enough to
 * catch neighboring parcels or an adjacent utility/BLM boundary near a tap, small enough that the
 * bounded-delivery pagination in the detail panel (`resultMeta.hasMore`) stays meaningful rather
 * than immediately maxing out.
 *
 * This is deliberately NOT mobile-only (no media query / pointer-coarse gate): the spec's
 * accessibility section wants a non-drag alternative generally, and gating it to a viewport width
 * would also hide it from switch-access or keyboard-only desktop users who have the same
 * drag-gesture problem.
 */
const WIDE_AREA_RADIUS_METERS = 500;

export function WideAreaSelectionAction() {
  const selection = useLandContextStore((state) => state.selection);
  const setSelection = useLandContextStore((state) => state.setSelection);

  // Only offered once a point (or parcel-with-point) selection exists -- this is an "expand my
  // current selection" action, not an independent area-drawing tool, per the spec's point/
  // parcel/area selection model ("Never substitute the viewport centre").
  if (!selection || selection.mode !== "point" || !selection.point) return null;

  function expandToWiderArea() {
    if (!selection?.point) return;
    const areaPolygon = buildWideAreaBoxPolygon(selection.point, WIDE_AREA_RADIUS_METERS);
    setSelection({ mode: "area", areaPolygon });
  }

  return (
    <button
      type="button"
      onClick={expandToWiderArea}
      className="land-context-wide-area-action min-h-11 min-w-11 px-4 py-2.5 rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--background))] text-sm font-medium text-[hsl(var(--foreground))]"
      aria-label={`Show wider area, approximately ${WIDE_AREA_RADIUS_METERS} meters around the selected point`}
    >
      Show wider area
    </button>
  );
}

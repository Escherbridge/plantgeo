// The one caption for a published coarse streamflow cell, shared by the hover tooltip, the click
// popup and the legend. Pure formatting: no React, no maplibre, no DOM.
//
// The twin of `fire-cell-caption.ts`, and a leaf for the same reason: these strings had three
// readers -- `hover-fields.ts`, `WaterLayer.tsx` and `layer-legends.ts` -- and lived in the first
// of them, so the other two imported a tooltip module to caption a popup and a legend. A leaf
// module lets every reader take the words from the same place with no module importing another
// surface's formatter to do it.

/** The heading the coarse streamflow cell shows in the tooltip and in `WaterLayer`'s popup. */
export const WATER_CELL_CAPTION_TITLE = "Coarse streamflow cell";

/**
 * The sentence that keeps a filled purple square from reading as a surveyed boundary. Shared
 * with `WaterLayer`'s click popup for exactly the reason `FIRE_CELL_NOT_A_PERIMETER_NOTE` is:
 * once an event aggregate draws as a polygon, the words are the only thing distinguishing it
 * from the layers that publish real geometry, and two copies of them drift.
 */
export const WATER_CELL_AGGREGATE_NOTE =
  "Several gauges may contribute; no single gauge identity applies.";

/**
 * A declared cell's extent, as "0.25° × 0.25°". Four decimals with trailing zeros dropped, so a
 * 0.1-degree rung does not print the doubled-precision tail of its own cell size.
 *
 * Not named for water although it lives here: the three surfaces that render a streamflow cell are
 * its only callers today, and a fourth that needs the same sentence should import it from here
 * rather than write a second `toFixed(4)`.
 */
export function formatSupportCellSize(
  cellWidthDegrees: number,
  cellHeightDegrees: number
): string {
  const degrees = (value: number) => `${Number(value.toFixed(4))}°`;
  return `${degrees(cellWidthDegrees)} × ${degrees(cellHeightDegrees)}`;
}

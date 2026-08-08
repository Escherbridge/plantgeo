/**
 * Client-safe derivation of published NDVI forecast series keys.
 *
 * Mirrors the agri-data-service naming contract exactly -- see
 * `src/lib/server/AGENTS.md` §agri-forecasts for why this file is the coupling point.
 */

/** `SERIES_KEY_PREFIX:GRID_NAME` from the Python vegetation plane; series keys append the cell key. */
export const NDVI_FORECAST_SERIES_PREFIX = "ndvi-daily:sentinel2-ndvi-0p25deg";

const CELL_SPACING_DEGREES = 0.25;
const CELL_CENTRE_OFFSET = 0.5;

/** Snap one coordinate to the centre of its 0.25° cell, matching `ingest/vegetation.py`. */
function cellCentre(coordinate: number): number {
  return (Math.floor(coordinate / CELL_SPACING_DEGREES) + CELL_CENTRE_OFFSET) * CELL_SPACING_DEGREES;
}

/** The `lat:lon` cell key stamped on vegetation features; `toFixed(4)` IS the contract's rendering. */
export function ndviCellKeyForPoint(latitude: number, longitude: number): string {
  return `${cellCentre(latitude).toFixed(4)}:${cellCentre(longitude).toFixed(4)}`;
}

/** The full series key the forecast serving view is queried by. */
export function ndviForecastSeriesKeyForPoint(latitude: number, longitude: number): string {
  return `${NDVI_FORECAST_SERIES_PREFIX}:${ndviCellKeyForPoint(latitude, longitude)}`;
}

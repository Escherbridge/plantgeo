/**
 * The one zoom-tier vocabulary every viewport-aggregated layer speaks.
 *
 * Extracted from `usda-soil.ts`, which invented it for the SSURGO survey, so the
 * soil-moisture field could reuse the tiers rather than mint a second set of names for the
 * same idea. The THRESHOLDS stay per-layer -- a 0.25-degree reanalysis lattice and a
 * per-map-unit soil survey do not become unreadable at the same zoom -- but the names, and
 * therefore everything a panel says about them, are shared.
 */

/**
 * "detail" draws the producer's own geometry unchanged. The two coarser bands draw an
 * aggregate over a larger geometry, and must never be captioned as though they were
 * individual observations.
 */
export type ZoomGranularity = "detail" | "regional-average" | "coarse-average";

/** Where one layer puts its two tier boundaries. */
export interface ZoomGranularityTiers {
  /** At or above this zoom, the producer's own geometry is drawn. */
  detailMinZoom: number;
  /** At or above this zoom (but below `detailMinZoom`), the finer aggregate is drawn. */
  regionalMinZoom: number;
}

/**
 * No zoom means a caller that predates zoom-awareness -- keep the original single-shot
 * behaviour rather than guessing which tier it meant.
 */
export function resolveZoomGranularity(
  zoom: number | undefined,
  tiers: ZoomGranularityTiers
): ZoomGranularity {
  if (zoom === undefined || !Number.isFinite(zoom)) return "detail";
  if (zoom >= tiers.detailMinZoom) return "detail";
  if (zoom >= tiers.regionalMinZoom) return "regional-average";
  return "coarse-average";
}

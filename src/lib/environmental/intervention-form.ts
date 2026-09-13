import {
  LAND_INTERVENTION_TYPES,
  AIR_INTERVENTION_TYPES,
  type InterventionCategory,
  type InterventionType,
} from "@/lib/environmental/intervention";
import type { InterventionGeometry } from "@/lib/geo/intervention-geometry-schema";

/**
 * The pieces both intervention-proposal surfaces need: the workspace's store-backed
 * `InterventionProposalForm` and `InterventionSubmitModal`'s own local-state copy (the
 * community "+Recommend" button). Shared here so the two surfaces cannot drift on what a
 * category offers or on what counts as a drawable geometry -- see
 * `src/components/panels/AGENTS.md`.
 *
 * Client-safe on purpose: nothing here may reach into `src/lib/server/**`, because both
 * consumers are client components (`scripts/check-data-boundaries.mjs` enforces it).
 */

/** Mirrors InterventionType in src/lib/environmental/intervention.ts. */
export const INTERVENTION_TYPE_LABELS: Record<InterventionType, string> = {
  reforestation: "Reforestation",
  silvopasture: "Silvopasture",
  cover_cropping: "Cover Cropping",
  biochar: "Biochar",
  keyline: "Keyline Design",
  cloud_seeding: "Cloud Seeding",
};

export const TYPES_BY_CATEGORY: Record<InterventionCategory, InterventionType[]> = {
  land: LAND_INTERVENTION_TYPES,
  air: AIR_INTERVENTION_TYPES,
};

/**
 * A Polygon must close (first position repeats the last) and describe at
 * least 3 distinct vertices (4 ring positions including the closing one).
 * Points always pass; MultiPolygon parts are checked the same way per ring.
 */
export function validateDrawnGeometry(geometry: InterventionGeometry): string | null {
  const polygons =
    geometry.type === "Polygon"
      ? [geometry.coordinates]
      : geometry.type === "MultiPolygon"
        ? geometry.coordinates
        : null;
  if (polygons === null) return null;

  for (const polygon of polygons) {
    const ring = polygon[0] ?? [];
    if (ring.length < 4) {
      return "Draw at least 3 points, then close the polygon.";
    }
    const first = ring[0];
    const last = ring[ring.length - 1];
    if (first[0] !== last[0] || first[1] !== last[1]) {
      return "The drawn polygon must be closed.";
    }
  }
  return null;
}

import { polygonArea } from "@/lib/map/measurement";
import type { InterventionCategory } from "@/lib/environmental/intervention";
import {
  InterventionGeometrySchema,
  type InterventionGeometry,
} from "@/lib/geo/intervention-geometry-schema";

export { InterventionGeometrySchema, type InterventionGeometry };

/** Total-vertex ceiling for interactive submissions, tighter than the per-shape maxima above. */
export const MAX_INTERVENTION_GEOMETRY_POSITIONS = 10_000;

/** Total coordinate positions across every ring of a validated geometry. */
export function countInterventionGeometryPositions(
  geometry: InterventionGeometry
): number {
  if (geometry.type === "Point") return 1;
  const polygons =
    geometry.type === "Polygon" ? [geometry.coordinates] : geometry.coordinates;
  return polygons.reduce(
    (total, polygon) =>
      total + polygon.reduce((subtotal, ring) => subtotal + ring.length, 0),
    0
  );
}

const SQUARE_METERS_PER_ACRE = 4046.8564224;

/** Area of an intervention site in acres (Point is 0; outer rings only, holes not subtracted). */
export function computeInterventionAreaAcres(geometry: InterventionGeometry): number {
  if (geometry.type === "Point") return 0;
  const polygons =
    geometry.type === "Polygon" ? [geometry.coordinates] : geometry.coordinates;
  const totalSquareMeters = polygons.reduce((total, polygon) => {
    const outerRing = polygon[0] ?? [];
    const coords = outerRing.map(
      (position) => [position[0], position[1]] as [number, number]
    );
    return total + polygonArea(coords);
  }, 0);
  return totalSquareMeters / SQUARE_METERS_PER_ACRE;
}

/** Land intervention sites are capped at 500 acres. */
export const LAND_INTERVENTION_AREA_CAP_ACRES = 500;

/** Policy placeholder (spec.md Open Questions), not a settled number — revisit before launch. */
export const AIR_INTERVENTION_AREA_CAP_ACRES = 50_000;

/** `null` when the geometry passes its category's area cap, else a message naming cap and area. */
export function getInterventionAreaCapIssue(
  geometry: InterventionGeometry,
  category: InterventionCategory
): string | null {
  if (geometry.type === "Point") return null;
  const areaAcres = computeInterventionAreaAcres(geometry);
  const capAcres =
    category === "air" ? AIR_INTERVENTION_AREA_CAP_ACRES : LAND_INTERVENTION_AREA_CAP_ACRES;
  if (areaAcres > capAcres) {
    return `${category} intervention geometry exceeds the ${capAcres}-acre cap (computed area: ${areaAcres.toFixed(2)} acres)`;
  }
  return null;
}

/**
 * Geometry helper for the "Show wider area" accessible area-selection alternative
 * (see `WideAreaSelectionAction.tsx`). Builds a small axis-aligned bbox polygon
 * centred on a point, expressed directly in [lng, lat] degrees -- no reprojection
 * library needed for a fixed, small (~hundreds of meters) radius at this precision.
 */

/** Meters per degree of latitude, effectively constant across the globe. */
const METERS_PER_DEGREE_LAT = 111_320;

/**
 * Builds a closed-ring bounding-box polygon (GeoJSON position array, first === last)
 * of `radiusMeters` in every direction around `center`. Longitude degrees are scaled
 * by cos(latitude) so the box is roughly square on the ground, not just in degrees.
 */
export function buildWideAreaBoxPolygon(
  center: [number, number],
  radiusMeters: number
): GeoJSON.Position[] {
  const [lng, lat] = center;
  const latRadius = radiusMeters / METERS_PER_DEGREE_LAT;
  const metersPerDegreeLng = METERS_PER_DEGREE_LAT * Math.cos((lat * Math.PI) / 180);
  const lngRadius = radiusMeters / Math.max(metersPerDegreeLng, 1);

  const west = lng - lngRadius;
  const east = lng + lngRadius;
  const south = lat - latRadius;
  const north = lat + latRadius;

  return [
    [west, south],
    [east, south],
    [east, north],
    [west, north],
    [west, south],
  ];
}

const MAX_LONGITUDE = 180;
const MIN_LATITUDE = -90;
const MAX_LATITUDE = 90;

/** Converts a non-wrapping WGS84 viewport into the API's west,south,east,north contract. */
export function canonicalViewportBbox(value: string): string | null {
  const coordinates = value.split(",").map(Number);
  if (
    coordinates.length !== 4 ||
    coordinates.some((coordinate) => !Number.isFinite(coordinate))
  ) {
    return null;
  }

  let west = coordinates[0];
  const south = coordinates[1];
  let east = coordinates[2];
  const north = coordinates[3];
  if (east <= west || north <= south || south < MIN_LATITUDE || north > MAX_LATITUDE) {
    return null;
  }

  const longitudeSpan = east - west;
  if (longitudeSpan >= 360) return null;
  west = ((((west + 180) % 360) + 360) % 360) - 180;
  east = west + longitudeSpan;
  if (east > MAX_LONGITUDE) return null;

  return [west, south, east, north]
    .map((coordinate) => String(Number(coordinate.toFixed(6))))
    .join(",");
}

/** Builds one bounded, non-wrapping bbox for viewport-scoped API calls. */
export function viewportBbox(
  longitude: number,
  latitude: number,
  zoom: number,
  halfViewportWidth = 512,
  halfViewportHeight = 256
): string | null {
  if (
    !Number.isFinite(longitude) ||
    !Number.isFinite(latitude) ||
    !Number.isFinite(zoom) ||
    zoom < 0 ||
    zoom > 22 ||
    halfViewportWidth <= 0 ||
    halfViewportHeight <= 0
  ) {
    return null;
  }

  const degreesPerPixel = 360 / 2 ** (zoom + 8);
  const centerLongitude = ((((longitude + 180) % 360) + 360) % 360) - 180;
  const west = centerLongitude - degreesPerPixel * halfViewportWidth;
  const east = centerLongitude + degreesPerPixel * halfViewportWidth;
  const south = Math.max(MIN_LATITUDE, latitude - degreesPerPixel * halfViewportHeight);
  const north = Math.min(MAX_LATITUDE, latitude + degreesPerPixel * halfViewportHeight);
  return canonicalViewportBbox([west, south, east, north].join(","));
}

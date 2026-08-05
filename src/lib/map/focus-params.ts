/**
 * The query contract for deep-linking the map camera at a place.
 *
 * It lives here rather than in either caller because two unrelated modules have
 * to agree on it byte for byte: `/feed` writes the links and
 * `src/components/map/MapFocus.tsx` reads them. See src/components/map/AGENTS.md.
 *
 * Values are validated on read, not trusted: a URL is user input, and MapLibre
 * throws on a non-finite centre rather than ignoring it.
 */

export const MAP_FOCUS_LONGITUDE_PARAM = "focusLng";
export const MAP_FOCUS_LATITUDE_PARAM = "focusLat";
export const MAP_FOCUS_ZOOM_PARAM = "focusZoom";

/** Close enough to read a parcel boundary without outrunning the tile coverage. */
export const DEFAULT_MAP_FOCUS_ZOOM = 13;

const MIN_ZOOM = 0;
const MAX_ZOOM = 22;

/** Six decimal places is ~0.1 m — past the precision any of our sources carry. */
const COORDINATE_PRECISION = 6;

export type MapFocus = {
  longitude: number;
  latitude: number;
  zoom: number;
};

function isFiniteWithin(value: number, min: number, max: number): boolean {
  return Number.isFinite(value) && value >= min && value <= max;
}

/**
 * `Number("")` and `Number("  ")` are `0`, not `NaN`, so a present-but-blank
 * `focusLat=` would otherwise read as a legitimate equatorial coordinate and
 * fly the camera into the Gulf of Guinea. Blank means absent.
 */
function parseParam(raw: string | null): number {
  if (raw === null || raw.trim() === "") return Number.NaN;
  return Number(raw);
}

/** Builds a map href focused on a point, or `null` when the point is unusable. */
export function buildMapFocusHref(
  longitude: number | null | undefined,
  latitude: number | null | undefined,
  zoom: number = DEFAULT_MAP_FOCUS_ZOOM
): string | null {
  if (longitude == null || latitude == null) return null;
  if (!isFiniteWithin(longitude, -180, 180)) return null;
  if (!isFiniteWithin(latitude, -90, 90)) return null;

  const params = new URLSearchParams({
    [MAP_FOCUS_LONGITUDE_PARAM]: longitude.toFixed(COORDINATE_PRECISION),
    [MAP_FOCUS_LATITUDE_PARAM]: latitude.toFixed(COORDINATE_PRECISION),
    [MAP_FOCUS_ZOOM_PARAM]: String(
      isFiniteWithin(zoom, MIN_ZOOM, MAX_ZOOM) ? zoom : DEFAULT_MAP_FOCUS_ZOOM
    ),
  });

  return `/?${params.toString()}`;
}

/** Reads a focus target back out of a query string; `null` if absent or invalid. */
export function readMapFocus(
  params: Pick<URLSearchParams, "get">
): MapFocus | null {
  const longitude = parseParam(params.get(MAP_FOCUS_LONGITUDE_PARAM));
  const latitude = parseParam(params.get(MAP_FOCUS_LATITUDE_PARAM));
  if (!isFiniteWithin(longitude, -180, 180)) return null;
  if (!isFiniteWithin(latitude, -90, 90)) return null;

  const parsedZoom = parseParam(params.get(MAP_FOCUS_ZOOM_PARAM));
  const zoom = isFiniteWithin(parsedZoom, MIN_ZOOM, MAX_ZOOM)
    ? parsedZoom
    : DEFAULT_MAP_FOCUS_ZOOM;

  return { longitude, latitude, zoom };
}

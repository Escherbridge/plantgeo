export interface CoverageBbox {
  west: number;
  south: number;
  east: number;
  north: number;
}

interface NamedRegion {
  name: string;
  bbox: CoverageBbox;
}

/** Named regions the ingestion bbox may target, widest match wins ties. */
const NAMED_COVERAGE_REGIONS: NamedRegion[] = [
  { name: "Pacific Northwest", bbox: { west: -126, south: 41, east: -110, north: 50 } },
  { name: "California", bbox: { west: -125, south: 32, east: -114, north: 42.5 } },
  { name: "Western United States", bbox: { west: -126, south: 31, east: -102, north: 50 } },
  { name: "North America", bbox: { west: -170, south: 14, east: -52, north: 72 } },
];

/** Area in square degrees; a negative span clamps to zero rather than inverting the comparison. */
function bboxAreaSquareDegrees(bbox: CoverageBbox): number {
  return Math.max(0, bbox.east - bbox.west) * Math.max(0, bbox.north - bbox.south);
}

/** Fraction of `inner` that falls inside `outer`, 0 when they do not overlap. */
function containedFraction(inner: CoverageBbox, outer: CoverageBbox): number {
  const overlapWidth = Math.min(inner.east, outer.east) - Math.max(inner.west, outer.west);
  const overlapHeight = Math.min(inner.north, outer.north) - Math.max(inner.south, outer.south);
  if (overlapWidth <= 0 || overlapHeight <= 0) return 0;

  const innerArea = bboxAreaSquareDegrees(inner);
  if (innerArea <= 0) return 0;
  return (overlapWidth * overlapHeight) / innerArea;
}

function formatDegrees(value: number, positive: string, negative: string): string {
  return `${Math.abs(value).toFixed(1)}°${value >= 0 ? positive : negative}`;
}

/** Precise bounds, for the tooltip behind the friendly name. */
export function formatCoverageBounds(bbox: CoverageBbox): string {
  return (
    `${formatDegrees(bbox.south, "N", "S")}–${formatDegrees(bbox.north, "N", "S")}, ` +
    `${formatDegrees(bbox.west, "E", "W")}–${formatDegrees(bbox.east, "E", "W")}`
  );
}

/** A region must hold at least this much of the bbox to lend it its name. */
const REGION_MATCH_MIN_CONTAINED_FRACTION = 0.9;

/**
 * Names the region an ingestion bbox targets, falling back to bounds when it matches none.
 *
 * The TIGHTEST containing region wins, so a PNW box reads "Pacific Northwest" rather than the
 * "North America" box that also contains it.
 */
export function describeCoverageRegion(bbox: CoverageBbox): string {
  const containingRegions = NAMED_COVERAGE_REGIONS.filter(
    (region) => containedFraction(bbox, region.bbox) >= REGION_MATCH_MIN_CONTAINED_FRACTION
  );
  if (containingRegions.length === 0) return formatCoverageBounds(bbox);

  const tightestRegion = containingRegions.reduce((tightest, region) =>
    bboxAreaSquareDegrees(region.bbox) < bboxAreaSquareDegrees(tightest.bbox) ? region : tightest
  );
  return tightestRegion.name;
}

// Synchronous client-side camera support: the opening map view lands on the ingestion coverage
// bbox without waiting on getIngestionCoverage's tRPC round trip.
// See `src/lib/map/AGENTS.md` §coverage-region.

/**
 * Opening-camera fallback used only when NEXT_PUBLIC_INGEST_BBOX is unset; the env var always wins.
 *
 * A pilot-region footprint literal; see `src/lib/map/AGENTS.md` §coverage-region.
 */
export const FALLBACK_COVERAGE_BBOX: CoverageBbox = {
  west: -125,
  south: 42,
  east: -111,
  north: 49,
};

/** Parses the "west,south,east,north" format shared with the server-side INGEST_BBOX parsing in layers.ts. */
export function parseCoverageBbox(raw: string | undefined | null): CoverageBbox | null {
  if (!raw) return null;
  const parts = raw.split(",").map(Number);
  if (parts.length !== 4 || parts.some((part) => !Number.isFinite(part))) return null;
  const [west, south, east, north] = parts;
  return { west, south, east, north };
}

/**
 * The bbox available synchronously, before `getIngestionCoverage` resolves.
 *
 * See `src/lib/map/AGENTS.md` §coverage-region.
 */
export function getClientCoverageBbox(): CoverageBbox {
  return parseCoverageBbox(process.env.NEXT_PUBLIC_INGEST_BBOX) ?? FALLBACK_COVERAGE_BBOX;
}

/**
 * The camera MapLibre's `fitBounds` would land on for a bbox, canvas size and padding.
 *
 * Spherical Web Mercator (EPSG:3857) inverse fit, unfloored, so an initial camera computed here
 * and a later `fitBounds` to the same bbox agree closely enough that the second can be instant
 * instead of animated. Longitude/latitude are just the bbox centre; only zoom needs projection
 * math. See `src/lib/map/AGENTS.md` §coverage-region.
 */
export function viewportForBbox(
  bbox: CoverageBbox,
  widthPx: number,
  heightPx: number,
  paddingPx: number
): { longitude: number; latitude: number; zoom: number } {
  const WORLD_TILE_SIZE_PX = 256;
  const MAX_ZOOM = 21;

  const mercatorLatitudeRadians = (latitude: number) => {
    const sine = Math.sin((latitude * Math.PI) / 180);
    const mercatorY = Math.log((1 + sine) / (1 - sine)) / 2;
    return Math.max(Math.min(mercatorY, Math.PI), -Math.PI) / 2;
  };

  const latitudeFraction =
    (mercatorLatitudeRadians(bbox.north) - mercatorLatitudeRadians(bbox.south)) / Math.PI ||
    Number.EPSILON;
  const longitudeSpanDegrees = bbox.east - bbox.west;
  const longitudeFraction =
    (longitudeSpanDegrees < 0 ? longitudeSpanDegrees + 360 : longitudeSpanDegrees) / 360 ||
    Number.EPSILON;

  const latitudeZoom = Math.log2(
    (heightPx - paddingPx * 2) / WORLD_TILE_SIZE_PX / latitudeFraction
  );
  const longitudeZoom = Math.log2(
    (widthPx - paddingPx * 2) / WORLD_TILE_SIZE_PX / longitudeFraction
  );

  return {
    longitude: (bbox.west + bbox.east) / 2,
    latitude: (bbox.south + bbox.north) / 2,
    zoom: Math.max(0, Math.min(latitudeZoom, longitudeZoom, MAX_ZOOM)),
  };
}

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

/** Fraction of `inner` that falls inside `outer`, 0 when they do not overlap. */
function containedFraction(inner: CoverageBbox, outer: CoverageBbox): number {
  const overlapWidth = Math.min(inner.east, outer.east) - Math.max(inner.west, outer.west);
  const overlapHeight = Math.min(inner.north, outer.north) - Math.max(inner.south, outer.south);
  if (overlapWidth <= 0 || overlapHeight <= 0) return 0;

  const innerArea = (inner.east - inner.west) * (inner.north - inner.south);
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

/**
 * Names the region an ingestion bbox targets, falling back to bounds when it
 * matches no known region. A region matches when it holds nearly all of the
 * bbox; the tightest such region wins, so the PNW box reads "Pacific
 * Northwest" rather than the "North America" box that also contains it.
 */
export function describeCoverageRegion(bbox: CoverageBbox): string {
  const matches = NAMED_COVERAGE_REGIONS.filter(
    (region) => containedFraction(bbox, region.bbox) >= 0.9
  );
  if (matches.length === 0) return formatCoverageBounds(bbox);

  const tightest = matches.reduce((best, region) => {
    const area = (region.bbox.east - region.bbox.west) * (region.bbox.north - region.bbox.south);
    const bestArea = (best.bbox.east - best.bbox.west) * (best.bbox.north - best.bbox.south);
    return area < bestArea ? region : best;
  });
  return tightest.name;
}

// ---------------------------------------------------------------------------
// Synchronous client-side camera support, added so the opening map view can
// land on the ingestion coverage bbox without waiting on getIngestionCoverage's
// tRPC round trip. See src/stores/map-store.ts (DEFAULT_VIEWPORT) and
// src/components/map/ServiceAreaLayer.tsx (the first fitBounds call).
// ---------------------------------------------------------------------------

/**
 * Fallback ingestion coverage bbox, mirroring the INGEST_BBOX value verified
 * directly against production on 2026-08-16 -- every layer's feature extent
 * falls inside it (fire-detections is exactly this box). Used only to paint
 * the opening camera before getIngestionCoverage resolves, and only when
 * NEXT_PUBLIC_INGEST_BBOX is not set at build time; the env var always wins
 * when present, keeping this server-configurable (see getClientCoverageBbox).
 *
 * If a deployment truly has no ingestion coverage (server INGEST_BBOX unset,
 * getIngestionCoverage returns configured:false), this fallback still paints
 * a PNW-ish opening camera -- ServiceAreaLayer simply never masks or locks
 * bounds in that case, same as before this change, so it's a no-op default
 * rather than a wrong service-area claim.
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
 * The bbox available synchronously, before getIngestionCoverage resolves.
 * NEXT_PUBLIC_INGEST_BBOX is optional: set it to match the server's
 * INGEST_BBOX at deploy time to make the two agree exactly. When unset (e.g.
 * it hasn't been plumbed through the Docker build's ARG list yet -- see
 * Dockerfile's existing NEXT_PUBLIC_* forwarding pattern), this falls back to
 * the verified production box above, which already matches today's real
 * value, so behavior is correct out of the box either way.
 */
export function getClientCoverageBbox(): CoverageBbox {
  return parseCoverageBbox(process.env.NEXT_PUBLIC_INGEST_BBOX) ?? FALLBACK_COVERAGE_BBOX;
}

/**
 * Approximates the camera MapLibre's fitBounds would land on for a bbox
 * against a canvas size and padding (Web Mercator, unfloored), so an
 * initial camera computed here and a later fitBounds to the same bbox agree
 * closely enough that the second one can be instant instead of animated.
 * Longitude/latitude are just the bbox center; only zoom needs projection math.
 */
export function viewportForBbox(
  bbox: CoverageBbox,
  widthPx: number,
  heightPx: number,
  paddingPx: number
): { longitude: number; latitude: number; zoom: number } {
  const WORLD_PX = 256;
  const ZOOM_MAX = 21;

  const latRad = (lat: number) => {
    const sin = Math.sin((lat * Math.PI) / 180);
    const radX2 = Math.log((1 + sin) / (1 - sin)) / 2;
    return Math.max(Math.min(radX2, Math.PI), -Math.PI) / 2;
  };

  const latFraction = (latRad(bbox.north) - latRad(bbox.south)) / Math.PI || Number.EPSILON;
  const lngDiff = bbox.east - bbox.west;
  const lngFraction = (lngDiff < 0 ? lngDiff + 360 : lngDiff) / 360 || Number.EPSILON;

  const latZoom = Math.log2((heightPx - paddingPx * 2) / WORLD_PX / latFraction);
  const lngZoom = Math.log2((widthPx - paddingPx * 2) / WORLD_PX / lngFraction);

  return {
    longitude: (bbox.west + bbox.east) / 2,
    latitude: (bbox.south + bbox.north) / 2,
    zoom: Math.max(0, Math.min(latZoom, lngZoom, ZOOM_MAX)),
  };
}

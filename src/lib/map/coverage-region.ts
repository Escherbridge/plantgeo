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

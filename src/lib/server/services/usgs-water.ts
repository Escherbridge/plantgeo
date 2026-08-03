import type { GroundwaterWell, WaterGauge } from "@/lib/environmental/water";
import { fetchBoundedJson } from "@/lib/server/http/bounded-upstream";

export type { GroundwaterWell, WaterGauge } from "@/lib/environmental/water";

const MAX_RESPONSE_BYTES = 8 * 1024 * 1024;
const REQUEST_TIMEOUT_MS = 10_000;

// NWIS rejects bBox requests wider than 25 square degrees (lon range * lat range).
const MAX_TILE_DEGREES = 4;
const MAX_CONCURRENT_TILE_REQUESTS = 4;

interface NWISTimeSeries {
  sourceInfo: {
    siteName: string;
    siteCode: { value: string }[];
    geoLocation: {
      geogLocation: { latitude: number; longitude: number };
    };
  };
  values: {
    value: { value: string; dateTime: string }[];
    qualifier?: { qualifierCode: string }[];
  }[];
  variable: {
    variableCode: { value: string }[];
  };
}

interface NWISResponse {
  value: {
    timeSeries: NWISTimeSeries[];
  };
}

interface ParsedBbox {
  west: number;
  south: number;
  east: number;
  north: number;
}

/** Parse a "west,south,east,north" bbox string into numeric bounds. */
function parseBbox(bbox: string): ParsedBbox {
  const [west, south, east, north] = bbox.split(",").map(Number);
  return { west, south, east, north };
}

/** Round a coordinate to 6 decimal digits so NWIS's 7-digit limit is never exceeded. */
function formatCoordinate(value: number): number {
  return parseFloat(value.toFixed(6));
}

/**
 * Split a bbox into a grid of sub-bboxes no larger than maxTileDegrees square,
 * covering the full extent (the last row/column may be smaller).
 */
function tileBbox(bbox: string, maxTileDegrees: number = MAX_TILE_DEGREES): string[] {
  const { west, south, east, north } = parseBbox(bbox);
  const tiles: string[] = [];

  for (let tileSouth = south; tileSouth < north; tileSouth += maxTileDegrees) {
    const tileNorth = Math.min(tileSouth + maxTileDegrees, north);
    for (let tileWest = west; tileWest < east; tileWest += maxTileDegrees) {
      const tileEast = Math.min(tileWest + maxTileDegrees, east);
      tiles.push(
        [tileWest, tileSouth, tileEast, tileNorth].map(formatCoordinate).join(",")
      );
    }
  }

  return tiles;
}

/** Run async tasks with at most concurrencyLimit in flight, preserving input order. */
async function runWithBoundedConcurrency<T>(
  tasks: (() => Promise<T>)[],
  concurrencyLimit: number
): Promise<T[]> {
  const results: T[] = new Array(tasks.length);
  let nextIndex = 0;

  async function runNextTask(): Promise<void> {
    while (nextIndex < tasks.length) {
      const currentIndex = nextIndex++;
      results[currentIndex] = await tasks[currentIndex]();
    }
  }

  const workerCount = Math.min(concurrencyLimit, tasks.length);
  await Promise.all(Array.from({ length: workerCount }, runNextTask));

  return results;
}

/**
 * Fetch NWIS time series across every tile of a bbox and merge into one list,
 * deduping by site number (a boundary site can appear in adjacent tiles).
 * Any tile request failure propagates rather than yielding partial coverage.
 */
async function fetchTiledTimeSeries(
  bbox: string,
  buildTileUrl: (tileBbox: string) => string,
  fetchOptions: Parameters<typeof fetchBoundedJson>[2]
): Promise<NWISTimeSeries[]> {
  const tasks = tileBbox(bbox).map((tile) => async () => {
    const data = (await fetchBoundedJson(
      buildTileUrl(tile),
      { headers: { Accept: "application/json" } },
      fetchOptions
    )) as NWISResponse;
    return data?.value?.timeSeries ?? [];
  });

  const tileResults = await runWithBoundedConcurrency(tasks, MAX_CONCURRENT_TILE_REQUESTS);

  const seenSiteNumbers = new Set<string>();
  const mergedTimeSeries: NWISTimeSeries[] = [];
  for (const timeSeriesForTile of tileResults) {
    for (const series of timeSeriesForTile) {
      const siteNo = series.sourceInfo.siteCode[0]?.value ?? "";
      if (seenSiteNumbers.has(siteNo)) continue;
      seenSiteNumbers.add(siteNo);
      mergedTimeSeries.push(series);
    }
  }

  return mergedTimeSeries;
}

/**
 * Classify streamflow condition based on percentile.
 */
function classifyCondition(
  percentile: number | null
): WaterGauge["condition"] {
  if (percentile === null) return "unknown";
  if (percentile > 75) return "above_normal";
  if (percentile >= 25) return "normal";
  if (percentile >= 10) return "below_normal";
  if (percentile >= 5) return "low";
  return "critically_low";
}

/**
 * Infer a simple trend from qualifier codes provided by NWIS.
 * NWIS qualifier "P" = provisional, "Ice" = ice affected, etc.
 * Without historical comparison we default to stable.
 */
function inferTrend(
  qualifiers?: { qualifierCode: string }[]
): WaterGauge["trend"] {
  if (!qualifiers) return "stable";
  const codes = qualifiers.map((q) => q.qualifierCode.toLowerCase());
  if (codes.includes("e")) return "declining";
  return "stable";
}

/**
 * Fetch active USGS NWIS streamflow gauges for a bounding box.
 * @param bbox "west,south,east,north"
 */
export async function getStreamflowGauges(bbox: string): Promise<WaterGauge[]> {
  const buildTileUrl = (tileBbox: string) =>
    `https://waterservices.usgs.gov/nwis/iv/?format=json` +
    `&bBox=${tileBbox}&parameterCd=00060&siteType=ST&siteStatus=active`;

  const timeSeries = await fetchTiledTimeSeries(bbox, buildTileUrl, {
    maxBytes: MAX_RESPONSE_BYTES,
    timeoutMs: REQUEST_TIMEOUT_MS,
    revalidateSeconds: 900,
  });

  return timeSeries.map((ts): WaterGauge => {
    const siteNo = ts.sourceInfo.siteCode[0]?.value ?? "";
    const siteName = ts.sourceInfo.siteName ?? "";
    const lat = ts.sourceInfo.geoLocation.geogLocation.latitude;
    const lon = ts.sourceInfo.geoLocation.geogLocation.longitude;

    const latestValues = ts.values[0]?.value ?? [];
    const latest = latestValues[latestValues.length - 1];
    const flowCfs = latest ? parseFloat(latest.value) : null;
    const updatedAt = latest?.dateTime ?? new Date().toISOString();

    // NWIS does not return real-time percentile in the IV service;
    // percentile would require a stats service call. We approximate:
    // null means unknown until the BullMQ job enriches the DB record.
    const percentile: number | null = null;

    const qualifiers = ts.values[0]?.qualifier;

    return {
      siteNo,
      siteName,
      lat,
      lon,
      flowCfs: isNaN(flowCfs as number) ? null : flowCfs,
      percentile,
      condition: classifyCondition(percentile),
      trend: inferTrend(qualifiers),
      updatedAt,
    };
  });
}

/**
 * Fetch USGS NWIS groundwater well data for a bounding box.
 * parameterCd=72019 = depth to water level, feet below land surface
 * @param bbox "west,south,east,north"
 */
export async function getGroundwaterWells(
  bbox: string
): Promise<GroundwaterWell[]> {
  const buildTileUrl = (tileBbox: string) =>
    `https://waterservices.usgs.gov/nwis/iv/?format=json` +
    `&bBox=${tileBbox}&parameterCd=72019&siteType=GW`;

  const timeSeries = await fetchTiledTimeSeries(bbox, buildTileUrl, {
    maxBytes: MAX_RESPONSE_BYTES,
    timeoutMs: REQUEST_TIMEOUT_MS,
    revalidateSeconds: 3600,
  });

  return timeSeries.map((ts): GroundwaterWell => {
    const siteNo = ts.sourceInfo.siteCode[0]?.value ?? "";
    const siteName = ts.sourceInfo.siteName ?? "";
    const lat = ts.sourceInfo.geoLocation.geogLocation.latitude;
    const lon = ts.sourceInfo.geoLocation.geogLocation.longitude;

    const latestValues = ts.values[0]?.value ?? [];
    const latest = latestValues[latestValues.length - 1];
    const depthFt = latest ? parseFloat(latest.value) : null;
    const updatedAt = latest?.dateTime ?? new Date().toISOString();

    // Determine trend by comparing first vs last reading in the series
    let trend: GroundwaterWell["trend"] = "stable";
    if (latestValues.length >= 2) {
      const first = parseFloat(latestValues[0].value);
      const last = parseFloat(latestValues[latestValues.length - 1].value);
      if (!isNaN(first) && !isNaN(last)) {
        // Higher depth value = water table dropping (worse)
        const delta = last - first;
        if (delta > 10) trend = "critical";
        else if (delta > 5) trend = "declining";
        else if (delta < -2) trend = "rising";
      }
    }

    return {
      siteNo,
      siteName,
      lat,
      lon,
      depthFt: depthFt !== null && isNaN(depthFt) ? null : depthFt,
      trend,
      updatedAt,
    };
  });
}

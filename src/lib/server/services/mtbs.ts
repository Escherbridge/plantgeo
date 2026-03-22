import { cacheGeoJSON, getCachedGeoJSON } from "@/lib/server/redis";

export type BurnSeverityClass =
  | "unburned"
  | "low"
  | "moderate"
  | "high"
  | "increased_greenness";

export interface MTBSFireProperties {
  fireName: string;
  fireYear: number;
  acres: number;
  severityClass: BurnSeverityClass;
  ignitionDate: string;
  fireId: string;
}

const SEVERITY_MAP: Record<number, BurnSeverityClass> = {
  1: "unburned",
  2: "low",
  3: "moderate",
  4: "high",
  5: "increased_greenness",
};

const MTBS_URL =
  "https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/MTBS_Polygons_v1/FeatureServer/0/query";

export async function getMTBSPerimeters(
  bbox: string,
  yearFrom?: number,
  yearTo?: number
): Promise<GeoJSON.FeatureCollection> {
  const cacheKey = `mtbs:${bbox}:${yearFrom ?? ""}:${yearTo ?? ""}`;
  const cached = await getCachedGeoJSON<GeoJSON.FeatureCollection>(cacheKey);
  if (cached) return cached;

  const [west, south, east, north] = bbox.split(",").map(Number);

  const whereClauses: string[] = [];
  if (yearFrom) whereClauses.push(`Ig_Year >= ${yearFrom}`);
  if (yearTo) whereClauses.push(`Ig_Year <= ${yearTo}`);
  const where = whereClauses.length > 0 ? whereClauses.join(" AND ") : "1=1";

  const params = new URLSearchParams({
    where,
    geometry: `${west},${south},${east},${north}`,
    geometryType: "esriGeometryEnvelope",
    inSR: "4326",
    spatialRel: "esriSpatialRelIntersects",
    outFields: "Fire_ID,Fire_Name,Ig_Date,BurnBndAc,Severity",
    outSR: "4326",
    f: "geojson",
    resultRecordCount: "500",
  });

  const response = await fetch(`${MTBS_URL}?${params}`, {
    next: { revalidate: 86400 },
  });

  if (!response.ok) {
    throw new Error(`MTBS API error: ${response.status} ${response.statusText}`);
  }

  const raw = (await response.json()) as {
    features?: Array<{
      type: string;
      geometry: GeoJSON.Geometry;
      properties: Record<string, unknown>;
    }>;
  };

  const featureCollection: GeoJSON.FeatureCollection = {
    type: "FeatureCollection",
    features: (raw.features ?? []).map((f) => {
      const props = f.properties;
      const severityCode = typeof props.Severity === "number" ? props.Severity : 0;
      const igDate = typeof props.Ig_Date === "string" ? props.Ig_Date : "";
      const fireYear = igDate ? new Date(igDate).getFullYear() : 0;

      const mapped: MTBSFireProperties = {
        fireName: typeof props.Fire_Name === "string" ? props.Fire_Name : "Unknown",
        fireYear,
        acres: typeof props.BurnBndAc === "number" ? props.BurnBndAc : 0,
        severityClass: SEVERITY_MAP[severityCode] ?? "unburned",
        ignitionDate: igDate,
        fireId: typeof props.Fire_ID === "string" ? props.Fire_ID : "",
      };

      return {
        type: "Feature" as const,
        geometry: f.geometry,
        properties: mapped,
      };
    }),
  };

  await cacheGeoJSON(cacheKey, featureCollection, 86400);

  return featureCollection;
}

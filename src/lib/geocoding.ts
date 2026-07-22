export type GeocodingResultType =
  | "house"
  | "street"
  | "city"
  | "state"
  | "country"
  | "other";

export interface NormalizedGeocodingResult {
  id: string;
  type: GeocodingResultType;
  name: string;
  displayName: string;
  coordinates: [number, number];
  properties: Record<string, string | undefined>;
}

const RESULT_TYPES = new Set<GeocodingResultType>([
  "house",
  "street",
  "city",
  "state",
  "country",
  "other",
]);

function isNormalizedResult(value: unknown): value is NormalizedGeocodingResult {
  if (!value || typeof value !== "object") return false;
  const result = value as Partial<NormalizedGeocodingResult>;
  return (
    typeof result.id === "string" &&
    RESULT_TYPES.has(result.type as GeocodingResultType) &&
    typeof result.name === "string" &&
    typeof result.displayName === "string" &&
    Array.isArray(result.coordinates) &&
    result.coordinates.length === 2 &&
    Number.isFinite(result.coordinates[0]) &&
    result.coordinates[0] >= -180 &&
    result.coordinates[0] <= 180 &&
    Number.isFinite(result.coordinates[1]) &&
    result.coordinates[1] >= -90 &&
    result.coordinates[1] <= 90 &&
    Boolean(result.properties) &&
    typeof result.properties === "object" &&
    !Array.isArray(result.properties)
  );
}

/** Validates the browser-facing result contract returned by PlantGeo geocoding routes. */
export async function readGeocodingResults(
  response: Response
): Promise<NormalizedGeocodingResult[]> {
  if (!response.ok) {
    throw new Error(`Geocode request failed: ${response.status}`);
  }

  const payload: unknown = await response.json();
  if (
    !payload ||
    typeof payload !== "object" ||
    !Array.isArray((payload as { results?: unknown }).results)
  ) {
    throw new Error("Geocode response did not contain results");
  }

  const results = (payload as { results: unknown[] }).results;
  if (results.length > 10 || !results.every(isNormalizedResult)) {
    throw new Error("Geocode response did not match the expected result contract");
  }
  return results;
}

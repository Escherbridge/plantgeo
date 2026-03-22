import { getRedis } from "@/lib/server/redis";

/** TTL 7 days — SoilGrids data updates annually */
const SOILGRIDS_CACHE_TTL = 60 * 60 * 24 * 7;

const SOILGRIDS_BASE =
  "https://rest.isric.org/soilgrids/v2.0/properties/query";

export interface SoilProperties {
  ph: number;
  organicCarbon: number;
  nitrogen: number;
  bulkDensity: number;
  cec: number;
  ocd: number;
}

interface SoilGridsLayer {
  name: string;
  depths: Array<{
    label: string;
    values: {
      mean: number | null;
    };
  }>;
}

interface SoilGridsResponse {
  properties: {
    layers: SoilGridsLayer[];
  };
}

/**
 * Query SoilGrids v2 REST API for soil properties at a point.
 * Rate limit: ~2 req/sec — always checks Redis cache before fetching.
 * @param lat latitude
 * @param lon longitude
 */
export async function getSoilProperties(
  lat: number,
  lon: number
): Promise<SoilProperties> {
  const cacheKey = `soilgrids:${lat.toFixed(3)}:${lon.toFixed(3)}`;
  const r = getRedis();

  const cached = await r.get(cacheKey);
  if (cached) {
    return JSON.parse(cached) as SoilProperties;
  }

  const params = new URLSearchParams({
    lon: String(lon),
    lat: String(lat),
    property: "phh2o,soc,nitrogen,bdod,cec,ocd",
    depth: "0-5cm,5-15cm",
    value: "mean",
  });

  const response = await fetch(`${SOILGRIDS_BASE}?${params}`, {
    headers: { Accept: "application/json" },
  });

  if (!response.ok) {
    throw new Error(
      `SoilGrids API error: ${response.status} ${response.statusText}`
    );
  }

  const data = (await response.json()) as SoilGridsResponse;
  const layers = data.properties?.layers ?? [];

  function getMean(propertyName: string): number {
    const layer = layers.find((l) => l.name === propertyName);
    if (!layer) return 0;
    // Use 0-5cm depth value
    const depth = layer.depths.find((d) => d.label === "0-5cm");
    const raw = depth?.values?.mean ?? layer.depths[0]?.values?.mean ?? null;
    return raw ?? 0;
  }

  // SoilGrids units: phh2o in pH*10, soc in dg/kg, nitrogen in cg/kg,
  // bdod in cg/cm³, cec in mmol(c)/kg, ocd in hg/m³ — convert to common units
  const props: SoilProperties = {
    ph: getMean("phh2o") / 10,               // pH*10 → pH
    organicCarbon: getMean("soc") / 10,       // dg/kg → g/kg
    nitrogen: getMean("nitrogen") / 100,      // cg/kg → g/kg
    bulkDensity: getMean("bdod") / 100,       // cg/cm³ → g/cm³
    cec: getMean("cec") / 10,                 // mmol(c)/kg → cmol(c)/kg
    ocd: getMean("ocd") / 10,                 // hg/m³ → kg/m³
  };

  await r.setex(cacheKey, SOILGRIDS_CACHE_TTL, JSON.stringify(props));

  return props;
}
